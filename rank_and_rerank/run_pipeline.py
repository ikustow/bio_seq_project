import os
import json
from bioseq_investigator.pipeline import run_bioseq_pipeline

def main():
    # Example prompt: Mix of DNA/Protein, file paths or sequences
    user_prompt = (
        "I have a sequence in 'data/per-protein.faa' (just kidding, it's actually here: "
        "MALWMRLLPLLALLALWGPDPAAAFVNQHLCGSHLVEALYLVCGERGFFYTPKTRREAEDLQVGQVELGGGPGAGSLQPLALEGSLQKRGIVEQCCTSICSLYQLENYCN). "
        "I am looking for sequences involved in glucose metabolism or structurally related to human insulin."
    )
    
    print("--- BioSeq Investigator: Advanced LangGraph Pipeline ---")
    print(f"User Prompt: {user_prompt}\n")

    # API key is handled by the utils.setup_environment called within nodes
    if not os.getenv("MISTRAL_API_KEY"):
        print("Error: MISTRAL_API_KEY environment variable not set.")
        return

    try:
        print("Executing pipeline...")
        result = run_bioseq_pipeline(user_prompt)
        
        if result.get("error"):
            print(f"\nPipeline Error: {result['error']}")
            return

        print("\n--- Pipeline Summary ---")
        print(f"Detected Type: {result.get('sequence_type')}")
        print(f"Classification Confidence: {result.get('is_confident')}")
        print(f"Protein Sequence Length: {len(result.get('protein_sequence', ''))}")
        
        print("\n--- Top 5 Context-Aware Matches (UniProt JSON) ---")
        final_results = result.get("final_results", [])
        
        # Output results with the confidence flag as requested
        output = {
            "classification_confident": result.get("is_confident"),
            "top_matches": final_results
        }
        
        print(json.dumps(output, indent=2))
        
        print("\n--- Quick View ---")
        for i, record in enumerate(final_results, 1):
            acc = record.get('primaryAccession')
            name = record.get('proteinDescription', {}).get('recommendedName', {}).get('fullName', {}).get('value', 'N/A')
            print(f"{i}. [{acc}] {name}")

    except Exception as e:
        print(f"Critical Failure: {e}")

if __name__ == "__main__":
    main()
