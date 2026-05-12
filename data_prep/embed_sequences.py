#!/usr/bin/env python3
import torch
import h5py
import pandas as pd
import numpy as np
from transformers import AutoModel, AutoTokenizer
from typing import Tuple

# Import configuration
import config

# =============================================================================
# LOCAL CONFIGURATION ALIASES
# =============================================================================
MODEL_NAME = config.EMBEDDING_MODEL_NAME
INPUT_CSV = config.OUTPUT_FILE
OUTPUT_H5 = config.EMBEDDING_OUTPUT_FILE
MAX_LENGTH = config.EMBEDDING_MAX_LENGTH

# =============================================================================
# FUNCTIONAL COMPONENTS
# =============================================================================

def initialize_model(model_name: str) -> Tuple[AutoModel, AutoTokenizer, torch.device]:
    """Initializes the model, tokenizer and detects the best available device."""
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Loading model {model_name} on {device}...")
    
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    model = AutoModel.from_pretrained(model_name, trust_remote_code=True).to(device)
    model.eval()
    
    return model, tokenizer, device

def compute_mean_embedding(sequence: str, model: AutoModel, tokenizer: AutoTokenizer, device: torch.device) -> np.ndarray:
    """Computes the mean-pooled embedding for a single DNA sequence."""
    # Tokenize
    inputs = tokenizer(
        sequence, 
        return_tensors="pt", 
        truncation=True, 
        max_length=MAX_LENGTH,
        padding=False
    ).to(device)
    
    with torch.no_grad():
        outputs = model(**inputs)
        # Get hidden states from the last layer [batch, seq_len, hidden_dim]
        hidden_states = outputs.last_hidden_state
        
        # Mean pooling across the sequence length to get a single vector per gene
        mean_embedding = torch.mean(hidden_states, dim=1).squeeze().cpu().numpy()
        
    return mean_embedding

def store_embedding_in_h5(h5_file: h5py.File, accession: str, embedding: np.ndarray):
    """Writes a single embedding to the HDF5 file using the accession as the key."""
    if accession in h5_file:
        del h5_file[accession] # Overwrite if exists
    h5_file.create_dataset(accession, data=embedding, compression="gzip")

def process_pipeline(csv_path: str, h5_path: str, model_name: str):
    """Orchestrates the loading, embedding, and storage process."""
    if not os.path.exists(csv_path):
        print(f"Error: Input file {csv_path} not found.")
        return

    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df)} sequences for embedding.")

    model, tokenizer, device = initialize_model(model_name)

    with h5py.File(h5_path, "a") as h5_f:
        for i, row in df.iterrows():
            accession = str(row["swissprot_id"])
            sequence = str(row["cds_sequence"])
            
            print(f"\rProcessing [{i+1}/{len(df)}] {accession} (len: {len(sequence)} nt)...", end="", flush=True)
            
            try:
                embedding = compute_mean_embedding(sequence, model, tokenizer, device)
                store_embedding_in_h5(h5_f, accession, embedding)
            except Exception as e:
                print(f"\nFailed to embed {accession}: {e}")
                continue

    print(f"\nEmbeddings successfully saved to {h5_path}")

# =============================================================================
# MAIN EXECUTION
# =============================================================================

def main():
    try:
        process_pipeline(INPUT_CSV, OUTPUT_H5, MODEL_NAME)
    except KeyboardInterrupt:
        print("\nEmbedding process interrupted by user.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")

if __name__ == "__main__":
    import os
    main()
