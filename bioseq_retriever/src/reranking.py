import numpy as np
from typing import List, Dict, Any, Tuple
import faiss
from src.utils import get_text_embedder

def _format_record_for_reranking(record: Dict[str, Any]) -> str:
    """
    Summarizes a UniProt record into a descriptive string for semantic reranking.
    Excludes identifiers like accession and the sequence itself.
    """
    organism = record.get('organism', {}).get('scientificName', 'N/A')
    gene = record.get('genes', [{}])[0].get('geneName', {}).get('value', 'N/A')
    protein_name = record.get('proteinDescription', {}).get('recommendedName', {}).get('fullName', {}).get('value', 'N/A')
    
    # Extract functional comments
    comments_list = []
    for c in record.get('comments', []):
        if c.get('commentType') == 'FUNCTION':
            for text_obj in c.get('note', {}).get('texts', []):
                val = text_obj.get('value', '')
                if val:
                    comments_list.append(val)
    
    comments = " ".join(comments_list)
    
    return f"Gene: {gene}; Organism: {organism}; Protein: {protein_name}; Description: {comments}"

class LocalReranker:
    def __init__(self):
        print("Using Mistral AI Embeddings for local reranking...")
        self.embedder = get_text_embedder()

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """
        Generates embeddings for a list of texts using Mistral API.
        """
        embeddings = self.embedder.embed_documents(texts)
        return np.array(embeddings, dtype=np.float32)

    def rerank_by_context(
        self, 
        records: List[Dict[str, Any]], 
        context_query: str,
        top_n: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Reranks records locally based on user context using semantic similarity.
        """
        if not records:
            return []

        # 1. Format records into passages for embedding
        contexts = [_format_record_for_reranking(rec) for rec in records]

        # 2. Generate embeddings for query and passages
        query_embedding = self.embed_texts([context_query])
        passage_embeddings = self.embed_texts(contexts)

        # 3. Perform similarity search using FAISS (cosine similarity)
        dim = passage_embeddings.shape[1]
        index = faiss.IndexFlatIP(dim) # Inner Product for cosine similarity with normalized vectors
        
        # Normalize for cosine similarity if not already normalized by API
        faiss.normalize_L2(query_embedding)
        faiss.normalize_L2(passage_embeddings)
        
        index.add(passage_embeddings)

        # Query FAISS index
        distances, indices = index.search(query_embedding, len(contexts))
        
        # 4. Sort and return top_n records
        reranked_results = []
        for i in range(len(indices[0])):
            original_index = indices[0][i]
            score = distances[0][i]
            reranked_results.append((records[original_index], float(score)))
        
        # Sort by score in descending order
        reranked_results.sort(key=lambda x: x[1], reverse=True)
        
        return [item[0] for item in reranked_results[:top_n]]
