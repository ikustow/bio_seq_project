import os
from bioseq_investigator.embeddings import load_embeddings_and_build_index
from bioseq_investigator.search import get_prottrans_embedder, search_top_k
from bioseq_investigator.data_fetcher import get_uniprot_records
from bioseq_investigator.scoring import rank_sequences
from bioseq_investigator.reranking import LocalReranker

def main():
    # 1. Setup
    H5_PATH = "data/per-protein.h5"
    if not os.path.exists(H5_PATH):
        print(f"Error: Data file {H5_PATH} not found.")
        return

    # Mock Input
    user_sequence = "MALWMRLLPLLALLALWGPDPAAAFVNQHLCGSHLVEALYLVCGERGFFYTPKTRREAEDLQVGQVELGGGPGAGSLQPLALEGSLQKRGIVEQCCTSICSLYQLENYCN"
    user_context = "Which of these sequences are known to be involved in glucose metabolism or are structurally related to human insulin?"
    
    print(f"--- BioSeq Investigator Pipeline ---")
    print(f"Query Sequence: {user_sequence[:50]}...")
    print(f"Query Context: {user_context}\n")

    try:
        # 2. Search
        print("Loading embeddings and building index...")
        hnsw_index, acc_map = load_embeddings_and_build_index(H5_PATH)
        
        print("Loading ProtT5 embedder...")
        embedder = get_prottrans_embedder()
        
        print(f"Searching for top 25 sequences...")
        matches = search_top_k(user_sequence, embedder, hnsw_index, acc_map, k=25)
        
        # 3. Rank by Sequence Similarity
        ranked_matches = rank_sequences(matches)
        top_25_accessions = [match[0] for match in ranked_matches]
        
        # 4. Fetch Details
        records = get_uniprot_records(top_25_accessions)
        
        # 5. Contextual Reranking
        print("Reranking by context (NVIDIA NIM alternative)...")
        reranker = LocalReranker()
        top_5_records = reranker.rerank_by_context(records, user_context, top_n=5)
        
        # 6. Show results
        print("\n--- Top 5 Closest Records (Context-Aware) ---")
        for i, record in enumerate(top_5_records, 1):
            acc = record.get('primaryAccession')
            name = record.get('proteinDescription', {}).get('recommendedName', {}).get('fullName', {}).get('value', 'N/A')
            gene = record.get('genes', [{}])[0].get('geneName', {}).get('value', 'N/A')
            print(f"{i}. [{acc}] Protein: {name}, Gene: {gene}")
            
    except Exception as e:
        print(f"Pipeline failed: {e}")

if __name__ == "__main__":
    main()
