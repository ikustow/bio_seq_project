import h5py
import faiss
import numpy as np
import torch
from transformers import T5EncoderModel, T5Tokenizer
import requests
from typing import List, Tuple, Dict, Any

def load_embeddings_and_build_index(h5_path: str) -> Tuple[faiss.IndexHNSWFlat, List[str]]:
    """
    Loads protein embeddings from an HDF5 file and inserts them into a FAISS HNSW index.
    Uses Manhattan distance (L1) as the metric.
    
    :param h5_path: Path to the .h5 file containing embeddings.
    :return: A tuple of (FAISS index, list of accession numbers).
    """
    print(f"Loading embeddings from {h5_path}...")
    with h5py.File(h5_path, 'r') as f:
        accessions = list(f.keys())
        if not accessions:
            raise ValueError("The HDF5 file is empty.")
            
        # Get dimension from the first entry
        dim = f[accessions[0]].shape[0]
        print(f"Detected embedding dimension: {dim}")
        
        # Initialize HNSW index with Manhattan distance (METRIC_L1)
        # M=32 is a reasonable default for the number of links per node
        index = faiss.IndexHNSWFlat(dim, 32, faiss.METRIC_L1)
        
        # Load embeddings into a single numpy array for batch addition to FAISS
        # This is more efficient than adding one by one
        print(f"Reading {len(accessions)} embeddings into memory...")
        all_embeddings = np.zeros((len(accessions), dim), dtype=np.float32)
        for i, acc in enumerate(accessions):
            all_embeddings[i] = f[acc][:]
            
        print("Adding embeddings to HNSW index...")
        index.add(all_embeddings)
        
    return index, accessions

def get_prottrans_embedder(model_name: str = "Rostlab/prot_t5_xl_uniref50"):
    """
    Initializes the ProtT5 model and tokenizer.
    Note: This model is very large (~11GB) and requires significant RAM/VRAM.
    """
    print(f"Loading model {model_name}...")
    tokenizer = T5Tokenizer.from_pretrained(model_name, do_lower_case=False)
    model = T5EncoderModel.from_pretrained(model_name)
    
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model.to(device)
    model.eval()
    
    return model, tokenizer, device

def embed_sequence(sequence: str, model, tokenizer, device) -> np.ndarray:
    """
    Generates a per-protein embedding for a given sequence using ProtT5.
    Standard approach: space-separated residues, mean pooling of residue embeddings.
    """
    # ProtT5 expects amino acids separated by spaces
    # Example: "M E T A"
    processed_seq = " ".join(list(sequence.upper()))
    
    inputs = tokenizer(processed_seq, return_tensors="pt").to(device)
    
    with torch.no_grad():
        outputs = model(**inputs)
        # last_hidden_state shape: [batch_size, sequence_length, embedding_dim]
        residue_embeddings = outputs.last_hidden_state.squeeze(0)
        
    # Mean pooling across the residue dimension (excluding special tokens if necessary)
    # Most bio-embeddings implementations mean-pool the entire output sequence
    protein_embedding = residue_embeddings.mean(dim=0).cpu().numpy()
    
    return protein_embedding.astype(np.float32)

def search_top_k(
    query_sequence: str, 
    embedder_tools: Tuple, 
    index: faiss.Index, 
    accession_list: List[str], 
    k: int = 5
) -> List[str]:
    """
    Embeds the query sequence and searches the index for the top k closest matches.
    """
    model, tokenizer, device = embedder_tools
    
    print(f"Embedding query sequence (length {len(query_sequence)})...")
    query_emb = embed_sequence(query_sequence, model, tokenizer, device)
    query_emb = query_emb.reshape(1, -1) # FAISS expects [1, dim]
    
    print(f"Searching index for top {k} matches...")
    distances, indices = index.search(query_emb, k)
    
    # Map index IDs back to accession numbers
    match_accessions = [accession_list[idx] for idx in indices[0]]
    return match_accessions

def get_uniprot_records(accessions: List[str]) -> List[Dict[str, Any]]:
    """
    Retrieves UniProt records for the given accessions using the REST API.
    """
    if not accessions:
        return []
        
    print(f"Fetching UniProt records for: {', '.join(accessions)}")
    
    # We use the UniProt search endpoint with accessions
    ids_query = " OR ".join([f"accession:{acc}" for acc in accessions])
    url = "https://rest.uniprot.org/uniprotkb/search"
    params = {
        "query": ids_query,
        "format": "json",
        "size": len(accessions)
    }
    
    response = requests.get(url, params=params)
    if response.status_code == 200:
        return response.json().get('results', [])
    else:
        response.raise_for_status()

if __name__ == "__main__":
    # Example execution flow
    H5_PATH = "data/per-protein.h5"
    
    try:
        # 1. Load data and build index
        hnsw_index, acc_map = load_embeddings_and_build_index(H5_PATH)
        
        # 2. Setup embedder (This might fail on 16GB RAM if not careful)
        # Suggestion: If this OOMs, consider using a smaller model or 
        # loading in 8-bit/4-bit if transformers supports it here.
        embedder = get_prottrans_embedder()
        
        # 3. Search
        # Test sequence (example: Insulin)
        test_seq = "MALWMRLLPLLALLALWGPDPAAAFVNQHLCGSHLVEALYLVCGERGFFYTPKTRREAEDLQVGQVELGGGPGAGSLQPLALEGSLQKRGIVEQCCTSICSLYQLENYCN"
        top_accessions = search_top_k(test_seq, embedder, hnsw_index, acc_map, k=5)
        
        print("\nTop Matches found:")
        for i, acc in enumerate(top_accessions):
            print(f"{i+1}. {acc}")
            
        # 4. Fetch details
        records = get_uniprot_records(top_accessions)
        print(f"\nRetrieved {len(records)} records from UniProt.")
        for record in records:
            primary_acc = record.get('primaryAccession')
            gene = record.get('genes', [{}])[0].get('geneName', {}).get('value', 'N/A')
            organism = record.get('organism', {}).get('scientificName', 'N/A')
            print(f"[{primary_acc}] Gene: {gene}, Organism: {organism}")
            
    except Exception as e:
        print(f"An error occurred: {e}")
