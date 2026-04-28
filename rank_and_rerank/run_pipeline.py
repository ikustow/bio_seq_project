import os
import json
from bioseq_investigator.pipeline import run_bioseq_pipeline

def main():
    # Example prompt that includes a sequence and context
    # This sequence is human insulin (P01308)
    user_prompt = (
        "I have this protein sequence: MALWMRLLPLLALLALWGPDPAAAFVNQHLCGSHLVEALYLVCGERGFFYTPKTRREAEDLQVGQVELGGGPGAGSLQPLALEGSLQKRGIVEQCCTSICSLYQLENYCN. "
        "I am looking for sequences involved in glucose metabolism or structurally related to human insulin."
    )
    
    # Another example with a path (uncomment to test)
    # user_prompt = "Find matches for the sequence in data/my_seq.faa and tell me which are related to cancer research."

    print("--- BioSeq Investigator: LangGraph Pipeline ---")
    print(f"User Prompt: {user_prompt}\n")

    if not os.getenv("MISTRAL_API_KEY"):
        print("Warning: MISTRAL_API_KEY environment variable not set. The LLM nodes will fail.")
        # return

    try:
        print("Starting pipeline execution...")
        result = run_bioseq_pipeline(user_prompt)
        
        if result.get("error"):
            print(f"\nPipeline Error: {result['error']}")
            return

        print("\n--- Pipeline Execution Summary ---")
        print(f"Extracted Input Type: {result.get('input_type')}")
        print(f"Detected Sequence Type: {result.get('sequence_type')}")
        print(f"Processed Protein Sequence (length): {len(result.get('protein_sequence', ''))}")
        
        print("\n--- Top 5 Reranked Results (JSON) ---")
        final_results = result.get("final_results", [])
        
        # Format the output as JSON records
        print(json.dumps(final_results, indent=2))
        
        print("\n--- Summary View ---")
        for i, record in enumerate(final_results, 1):
            acc = record.get('primaryAccession')
            name = record.get('proteinDescription', {}).get('recommendedName', {}).get('fullName', {}).get('value', 'N/A')
            gene = record.get('genes', [{}])[0].get('geneName', {}).get('value', 'N/A')
            print(f"{i}. [{acc}] {name} (Gene: {gene})")

    except Exception as e:
        print(f"Critical Failure: {e}")

if __name__ == "__main__":
    main()
