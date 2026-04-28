import os
import json
from src.pipeline import run_bioseq_pipeline

def run_pipeline_interface(user_prompt: str):
    """
    Interface to run the bioseq pipeline with a custom prompt.
    Returns the results dictionary.
    """
    if not os.getenv("MISTRAL_API_KEY"):
        raise ValueError("MISTRAL_API_KEY environment variable not set.")
    
    print(f"Executing pipeline for prompt: {user_prompt[:50]}...")
    result = run_bioseq_pipeline(user_prompt)
    
    if result.get("error"):
        print(f"Pipeline Error: {result['error']}")
    
    return result

def main():
    # Example prompt: Mix of DNA/Protein, file paths or sequences
    user_prompt = (
        "I have a sequence in 'data/per-protein.faa' (just kidding, it's actually here: "
        "MALWMRLLPLLALLALWGPDPAAAFVNQHLCGSHLVEALYLVCGERGFFYTPKTRREAEDLQVGQVELGGGPGAGSLQPLALEGSLQKRGIVEQCCTSICSLYQLENYCN). "
        "I am looking for sequences involved in glucose metabolism or structurally related to human insulin."
    )

    print("--- BioSeq Investigator: Advanced LangGraph Pipeline ---")
    print(f"User Prompt: {user_prompt}\n")

    try:
        result = run_pipeline_interface(user_prompt)

        if result.get("error"):
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
