#!/usr/bin/env python3
import os
import random
import logging
import torch
import h5py
import polars as pl
import numpy as np
from transformers import AutoModel, AutoTokenizer
from typing import List, Dict, Tuple

# Import configuration
import config

# =============================================================================
# LOGGING SETUP
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# =============================================================================
# NON-NEGOTIABLE CONSTANTS
# =============================================================================
MODEL_NAME = "LongSafari/hyenadna-medium-160k-seqlen-hf"

INVERSE_CODON_TABLE = {
    'A': ['GCT', 'GCC', 'GCA', 'GCG'],
    'R': ['CGT', 'CGC', 'CGA', 'CGG', 'AGA', 'AGG'],
    'N': ['AAT', 'AAC'],
    'D': ['GAT', 'GAC'],
    'C': ['TGT', 'TGC'],
    'E': ['GAA', 'GAG'],
    'Q': ['CAA', 'CAG'],
    'G': ['GGT', 'GGC', 'GGA', 'GGG'],
    'H': ['CAT', 'CAC'],
    'I': ['ATT', 'ATC', 'ATA'],
    'L': ['CTT', 'CTC', 'CTA', 'CTG', 'TTA', 'TTG'],
    'K': ['AAA', 'AAG'],
    'M': ['ATG'],
    'F': ['TTT', 'TTC'],
    'P': ['CCT', 'CCC', 'CCA', 'CCG'],
    'S': ['TCT', 'TCC', 'TCA', 'TCG', 'AGT', 'AGC'],
    'T': ['ACT', 'ACC', 'ACA', 'ACG'],
    'W': ['TGG'],
    'V': ['GTT', 'GTC', 'GTA', 'GTG'],
    'Y': ['TAT', 'TAC'],
    '*': ['TAA', 'TAG', 'TGA']
}

# =============================================================================
# FUNCTIONAL COMPONENTS
# =============================================================================

def load_data(path: str) -> pl.DataFrame:
    """Loads protein data from a tab-separated file."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Source file not found: {path}")
    logger.info(f"Loading data from {path}")
    return pl.read_csv(path, separator="\t")

def back_translate(aa_sequence: str) -> str:
    """Generates a random DNA sequence from an amino acid sequence."""
    dna_seq = []
    for aa in aa_sequence.upper():
        if aa in INVERSE_CODON_TABLE:
            dna_seq.append(random.choice(INVERSE_CODON_TABLE[aa]))
        else:
            # Handle unknown residues (e.g., 'X', 'U', 'Z') by ignoring or logging
            # For standard codon table, we'll just skip them to maintain sequence integrity
            continue
    return "".join(dna_seq)

def initialize_ai_stack() -> Tuple[AutoModel, AutoTokenizer, torch.device]:
    """Sets up the HyenaDNA model and tokenizer on the available device."""
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Initializing model {MODEL_NAME} on {device}")
    
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
    model = AutoModel.from_pretrained(MODEL_NAME, trust_remote_code=True).to(device)
    model.eval()
    
    return model, tokenizer, device

def compute_mean_embedding(dna_sequence: str, model: AutoModel, tokenizer: AutoTokenizer, device: torch.device) -> np.ndarray:
    """Generates a fixed-size mean-pooled embedding for a DNA sequence."""
    inputs = tokenizer(
        dna_sequence,
        return_tensors="pt",
        truncation=True,
        max_length=config.EMBEDDING_MAX_LENGTH,
        padding=False
    ).to(device)
    
    with torch.no_grad():
        outputs = model(**inputs)
        # Hidden states: [batch, seq_len, hidden_dim]
        # Mean pooling across the sequence dimension (dim 1)
        mean_vec = torch.mean(outputs.last_hidden_state, dim=1).squeeze()
        return mean_vec.cpu().numpy()

def save_to_h5(h5_file: h5py.File, accession: str, embedding: np.ndarray):
    """Stores a single embedding for a single accession."""
    if accession in h5_file:
        del h5_file[accession]
    h5_file.create_dataset(accession, data=embedding, compression="gzip")

def run_pipeline():
    """Orchestrates the back-translation and embedding process."""
    try:
        df = load_data(config.SWISSPROT_TSV)
        model, tokenizer, device = initialize_ai_stack()
        
        output_path = config.EMBEDDING_OUTPUT_FILE
        
        logger.info(f"Starting pipeline: {len(df)} proteins, 1 gene variant each.")
        
        with h5py.File(output_path, "a") as h5_f:
            for row in df.iter_rows(named=True):
                acc = row["Entry"]
                aa_seq = row["Sequence"]
                
                if not aa_seq:
                    continue
                
                logger.info(f"Processing {acc} (len: {len(aa_seq)} aa)")
                
                try:
                    dna_variant = back_translate(aa_seq)
                    emb = compute_mean_embedding(dna_variant, model, tokenizer, device)
                    save_to_h5(h5_f, acc, emb)
                except Exception as e:
                    logger.error(f"Error embedding {acc}: {e}")
        
        logger.info(f"Pipeline complete. Embeddings saved to {output_path}")

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")

if __name__ == "__main__":
    run_pipeline()
