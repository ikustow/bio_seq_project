import faiss
import numpy as np
from typing import List, Tuple

def get_prottrans_embedder(model_name: str = "Rostlab/prot_t5_xl_uniref50"):
    import torch
    from transformers import T5EncoderModel, T5Tokenizer
    
    print(f"Loading model {model_name}...")
    tokenizer = T5Tokenizer.from_pretrained(model_name, do_lower_case=False)
    model = T5EncoderModel.from_pretrained(model_name)
    
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model.to(device)
    model.eval()
    
    return model, tokenizer, device

def embed_sequence(sequence: str, model, tokenizer, device) -> np.ndarray:
    import torch
    
    processed_seq = " ".join(list(sequence.upper()))
    inputs = tokenizer(processed_seq, return_tensors="pt").to(device)
    
    with torch.no_grad():
        outputs = model(**inputs)
        residue_embeddings = outputs.last_hidden_state.squeeze(0)
        
    protein_embedding = residue_embeddings.mean(dim=0).cpu().numpy()
    return protein_embedding.astype(np.float32)

def search_top_k(
    query_sequence: str, 
    embedder_tools: Tuple, 
    index: faiss.Index, 
    accession_list: List[str], 
    k: int = 25
) -> List[Tuple[str, float]]:
    model, tokenizer, device = embedder_tools
    
    print(f"Embedding query sequence (length {len(query_sequence)})...")
    query_emb = embed_sequence(query_sequence, model, tokenizer, device)
    query_emb = query_emb.reshape(1, -1) 
    
    faiss.normalize_L2(query_emb)
    
    print(f"Searching index for top {k} matches...")
    distances, indices = index.search(query_emb, k)
    
    return [(accession_list[idx], float(distances[0][i])) for i, idx in enumerate(indices[0])]
