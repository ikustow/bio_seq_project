#!/usr/bin/env python3
import argparse
import os
import random
import logging
import time
import torch
import h5py
import polars as pl
import numpy as np
from transformers import AutoModel, AutoTokenizer
from pathlib import Path
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

def select_device(requested_device: str) -> torch.device:
    """Selects the best available torch device for this local machine."""
    if requested_device != "auto":
        return torch.device(requested_device)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def initialize_ai_stack(model_name: str, requested_device: str) -> Tuple[AutoModel, AutoTokenizer, torch.device]:
    """Sets up the HyenaDNA model and tokenizer on the available device."""
    device = select_device(requested_device)
    logger.info(f"Initializing model {model_name} on {device}")
    
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    model = AutoModel.from_pretrained(model_name, trust_remote_code=True).to(device)
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


def get_existing_accessions(output_path: str) -> set[str]:
    if not os.path.exists(output_path):
        return set()
    with h5py.File(output_path, "r") as h5_f:
        return set(h5_f.keys())


def parse_args():
    parser = argparse.ArgumentParser(description="Generate synthetic DNA embeddings from Swiss-Prot protein sequences.")
    parser.add_argument("--input", default=config.SWISSPROT_TSV, help="Input Swiss-Prot TSV with Entry and Sequence columns.")
    parser.add_argument("--output", default=config.EMBEDDING_OUTPUT_FILE, help="Output HDF5 path for DNA embeddings.")
    parser.add_argument("--model-name", default=MODEL_NAME, help="HyenaDNA model name.")
    parser.add_argument("--device", default="auto", help="Torch device: auto, cpu, mps, or cuda.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for deterministic back-translation.")
    parser.add_argument("--log-every", type=int, default=500, help="Log progress every N newly embedded records.")
    parser.add_argument("--flush-every", type=int, default=500, help="Flush HDF5 writes every N newly embedded records.")
    parser.add_argument("--max-records", type=int, default=0, help="Optional cap for smoke tests. 0 means all records.")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True, help="Skip accessions already present in output.")
    return parser.parse_args()


def run_pipeline(args=None):
    """Orchestrates the back-translation and embedding process."""
    if args is None:
        args = parse_args()

    try:
        random.seed(args.seed)
        df = load_data(args.input)
        model, tokenizer, device = initialize_ai_stack(args.model_name, args.device)
        
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        existing_accessions = get_existing_accessions(str(output_path)) if args.resume else set()
        
        logger.info(
            "Starting pipeline: %s proteins, %s already present, output=%s",
            len(df),
            len(existing_accessions),
            output_path,
        )
        
        embedded_count = 0
        skipped_count = 0
        error_count = 0
        started_at = time.monotonic()

        with h5py.File(output_path, "a") as h5_f:
            for row_num, row in enumerate(df.iter_rows(named=True), start=1):
                if args.max_records and row_num > args.max_records:
                    logger.info("Reached --max-records=%s; stopping.", args.max_records)
                    break

                acc = row["Entry"]
                aa_seq = row["Sequence"]
                
                if not aa_seq:
                    continue

                if args.resume and acc in existing_accessions:
                    skipped_count += 1
                    continue
                
                try:
                    dna_variant = back_translate(aa_seq)
                    emb = compute_mean_embedding(dna_variant, model, tokenizer, device)
                    save_to_h5(h5_f, acc, emb)
                    embedded_count += 1

                    if args.flush_every > 0 and embedded_count % args.flush_every == 0:
                        h5_f.flush()

                    if args.log_every > 0 and embedded_count % args.log_every == 0:
                        elapsed = max(time.monotonic() - started_at, 1e-9)
                        rate = embedded_count / elapsed
                        logger.info(
                            "Progress: row=%s/%s, embedded=%s, skipped=%s, errors=%s, rate=%.2f seq/s",
                            row_num,
                            len(df),
                            embedded_count,
                            skipped_count,
                            error_count,
                            rate,
                        )
                except Exception as e:
                    error_count += 1
                    logger.error(f"Error embedding {acc}: {e}")
        
        logger.info(
            "Pipeline complete. embedded=%s, skipped=%s, errors=%s. Embeddings saved to %s",
            embedded_count,
            skipped_count,
            error_count,
            output_path,
        )

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")

if __name__ == "__main__":
    run_pipeline()
