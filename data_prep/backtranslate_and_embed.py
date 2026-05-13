#!/usr/bin/env python3
import os
import random
import logging
import h5py
import numpy as np
import polars as pl
import torch
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModel, AutoTokenizer
from multiprocessing import Pool, cpu_count
from typing import List, Tuple, Dict, Generator

# Import configuration
import config

# =============================================================================
# LOGGING SETUP
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("BioSeqPipeline")

# =============================================================================
# NON-NEGOTIABLE CONSTANTS
# =============================================================================
MODEL_NAME = "LongSafari/hyenadna-medium-160k-seqlen-hf"

# Set PyTorch threads based on config (typically N_CORES / N_DATALOADER_WORKERS)
torch.set_num_threads(config.THREAD_COUNT // (config.THREAD_COUNT // 2)) # Allocate half to main process for inference

INVERSE_CODON_TABLE = {
    'A': ['GCT', 'GCC', 'GCA', 'GCG'], 'R': ['CGT', 'CGC', 'CGA', 'CGG', 'AGA', 'AGG'],
    'N': ['AAT', 'AAC'], 'D': ['GAT', 'GAC'], 'C': ['TGT', 'TGC'], 'E': ['GAA', 'GAG'],
    'Q': ['CAA', 'CAG'], 'G': ['GGT', 'GGC', 'GGA', 'GGG'], 'H': ['CAT', 'CAC'],
    'I': ['ATT', 'ATC', 'ATA'], 'L': ['CTT', 'CTC', 'CTA', 'CTG', 'TTA', 'TTG'],
    'K': ['AAA', 'AAG'], 'M': ['ATG'], 'F': ['TTT', 'TTC'], 'P': ['CCT', 'CCC', 'CCA', 'CCG'],
    'S': ['TCT', 'TCC', 'TCA', 'TCG', 'AGT', 'AGC'], 'T': ['ACT', 'ACC', 'ACA', 'ACG'],
    'W': ['TGG'], 'V': ['GTT', 'GTC', 'GTA', 'GTG'], 'Y': ['TAT', 'TAC'], '*': ['TAA', 'TAG', 'TGA']
}

# =============================================================================
# FUNCTIONAL COMPONENTS
# =============================================================================

def get_deterministic_dna(aa_sequence: str, seed: int) -> str:
    """
    Responsibility: Deterministic back-translation using a seed.
    Ensures reproducibility if the pipeline is interrupted and resumed.
    """
    random.seed(seed)
    dna_seq = []
    for aa in aa_sequence.upper():
        codons = INVERSE_CODON_TABLE.get(aa, [])
        if codons:
            dna_seq.append(random.choice(codons))
    return "".join(dna_seq)

class BioDataset(Dataset):
    """
    Responsibility: Provide batched access to generated sequences, using
    DataLoader workers for parallel back-translation.
    """
    def __init__(self, proteins: pl.DataFrame):
        self.proteins = proteins

    def __len__(self):
        return len(self.proteins)

    def __getitem__(self, idx):
        row = self.proteins.row(idx, named=True)
        accession = row["Entry"]
        aa_seq = row["Sequence"]
        # Seed based on index for deterministic DNA generation
        dna_sequence = get_deterministic_dna(aa_seq, idx)
        return accession, dna_sequence

def initialize_stack() -> Tuple[AutoModel, AutoTokenizer, torch.device]:
    """Responsibility: Hardware-aware model initialization."""
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Initializing HyenaDNA on {device} (trust_remote_code=True)")
    
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
    model = AutoModel.from_pretrained(MODEL_NAME, trust_remote_code=True).to(device)
    model.eval()
    
    return model, tokenizer, device

def compute_batch_embeddings(batch_dna: List[str], model: AutoModel, tokenizer: AutoTokenizer, device: torch.device) -> np.ndarray:
    """
    Responsibility: High-throughput batched inference with mean pooling.
    Uses inference_mode for maximum CPU performance.
    """
    inputs = tokenizer(
        batch_dna,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=config.EMBEDDING_MAX_LENGTH
    ).to(device)
    
    with torch.inference_mode():
        outputs = model(**inputs)
        # mean pooling across sequence dimension [B, L, D] -> [B, D]
        embeddings = outputs.last_hidden_state.mean(dim=1).squeeze()
        # Handle single-entry batches
        if embeddings.ndim == 1:
            embeddings = embeddings.unsqueeze(0)
            
    return embeddings.cpu().numpy()

def save_to_h5(h5_path: str, accessions: List[str], embeddings: np.ndarray):
    """
    Responsibility: Safe incremental HDF5 storage.
    Uses 'libver=latest' for metadata efficiency with 600k+ datasets.
    Stores as float32 to match search_service requirement.
    """
    with h5py.File(h5_path, "a", libver='latest') as f:
        for acc, emb in zip(accessions, embeddings):
            if acc in f:
                del f[acc]
            # Compression 4 is a balance between speed and disk usage
            f.create_dataset(acc, data=emb.astype(np.float32), compression="gzip", compression_opts=4)
        f.flush()

def run_pipeline():
    """
    Responsibility: Pipeline orchestration.
    Coordinates parallel preprocessing, batched inference, and robust storage.
    """
    try:
        # 1. Load Data
        df = pl.read_csv(config.SWISSPROT_TSV, separator="	")
        total = len(df)
        logger.info(f"Loaded {total} sequences from {config.SWISSPROT_TSV}")

        # 2. Setup DataLoader for Parallel Back-translation
        # num_workers: Half of total cores for data loading/back-translation
        num_dataloader_workers = config.THREAD_COUNT // 2
        
        dataset = BioDataset(df)
        dataloader = DataLoader(
            dataset,
            batch_size=config.BATCH_SIZE,
            shuffle=False,
            num_workers=num_dataloader_workers,
            pin_memory=True # Speeds up data transfer if using CUDA, also helps CPU
        )

        # 3. Model Prep (Initialized in Main Process)
        model, tokenizer, device = initialize_stack()

        # 4. Batched Inference Loop
        logger.info(f"Starting high-throughput inference loop with Batch Size: {config.BATCH_SIZE}, DataLoader workers: {num_dataloader_workers}...")
        
        for i, (batch_acc, batch_dna) in enumerate(dataloader):
            try:
                embeddings = compute_batch_embeddings(batch_dna, model, tokenizer, device)
                save_to_h5(config.EMBEDDING_OUTPUT_FILE, batch_acc, embeddings)
                
                # Progress logging
                processed_count = (i + 1) * config.BATCH_SIZE
                if i % 10 == 0 or processed_count >= total: # Log every 10 batches or at end
                    logger.info(f"Progress: {min(processed_count, total)}/{total} ({(min(processed_count, total)/total)*100:.1f}%)")
            except Exception as e:
                logger.error(f"Failed to process batch {i}: {e}")
                continue

        logger.info(f"Pipeline complete. All embeddings saved to {config.EMBEDDING_OUTPUT_FILE}")

    except Exception as e:
        logger.critical(f"Pipeline execution failed: {e}")

if __name__ == "__main__":
    run_pipeline()
