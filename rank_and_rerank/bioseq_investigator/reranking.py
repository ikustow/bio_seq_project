import numpy as np
from typing import List, Dict, Any, Tuple
import faiss

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
    def __init__(self, model_name: str = 'all-MiniLM-L6-v2'):
        from sentence_transformers import SentenceTransformer
        print(f"Loading SentenceTransformer model: {model_name} for local reranking...")
        self.model = SentenceTransformer(model_name)
        self.model.eval() # Set model to evaluation mode

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """
        Generates embeddings for a list of texts.
        """
        embeddings = self.model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        return embeddings.astype(np.float32)

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
        # Using FAISS for efficient search, similar to initial protein embedding search
        dim = passage_embeddings.shape[1]
        index = faiss.IndexFlatIP(dim) # Inner Product for cosine similarity with normalized vectors
        index.add(passage_embeddings)

        # Query FAISS index
        distances, indices = index.search(query_embedding, len(contexts)) # Search all passages
        
        # 4. Sort and return top_n records
        # Distances are cosine similarities; higher is better
        reranked_results = []
        for i in range(len(indices[0])):
            original_index = indices[0][i]
            score = distances[0][i]
            reranked_results.append((records[original_index], float(score)))
        
        # Sort by score in descending order
        reranked_results.sort(key=lambda x: x[1], reverse=True)
        
        return [item[0] for item in reranked_results[:top_n]]

if __name__ == "__main__":
    # Demo Usage for local reranking
    reranker = LocalReranker()

    proverbs = [
        "Actions speak louder than words.",
        "Better late than never.",
        "Cleanliness is next to godliness.",
        "Don't judge a book by its cover.",
        "Every cloud has a silver lining.",
        "Haste makes waste.",
        "It's no use crying over spilled milk.",
        "Knowledge is power.",
        "Laughter is the best medicine.",
        "Practice makes perfect."
    ]

    # Simulate UniProt-like records for the proverbs
    simulated_records = [
        {"primaryAccession": f"PROV{i+1}", "proteinDescription": {"recommendedName": {"fullName": {"value": p}}}}
        for i, p in enumerate(proverbs)
    ]

    query = "Being on time is better than not arriving at all."
    print(f"\nQuery: {query}")

    print("\n--- Local Reranking Results (Top 5) ---")
    reranked_top_5 = reranker.rerank_by_context(simulated_records, query, top_n=5)
    
    # To show scores, we need to re-run the process and capture scores explicitly
    # For demo purposes, we will just print the records that were reranked.
    # In a real scenario, rerank_by_context would return records with scores.
    # Let's modify rerank_by_context to return scores for the demo.
    
    # Re-running logic to get scores for printing
    passages = [reranker._format_record_for_reranking(rec) for rec in simulated_records]
    query_embedding = reranker.embed_texts([query])
    passage_embeddings = reranker.embed_texts(passages)

    dim = passage_embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(passage_embeddings)
    distances, indices = index.search(query_embedding, len(passages))

    reranked_scored_results = []
    for i in range(len(indices[0])):
        original_index = indices[0][i]
        score = distances[0][i]
        reranked_scored_results.append((simulated_records[original_index], float(score)))
    reranked_scored_results.sort(key=lambda x: x[1], reverse=True)

    for i, (record, score) in enumerate(reranked_scored_results[:5]):
        print(f"{i+1}. Score: {score:.4f}, Record: {record.get('proteinDescription', {}).get('recommendedName', {}).get('fullName', {}).get('value', 'N/A')}")