import os
import requests
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()
api_key = os.getenv("NVIDIA_API_KEY")

def get_rerank_scores(query, documents):
    """
    Calls NVIDIA NIM Re-rank API to get scores for a query against multiple documents.
    """
    url = "https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    
    # Use the model name found in the error message
    payload = {
        "model": "nvidia/rerank-qa-mistral-4b",
        "query": {"text": query},
        "passages": [{"text": doc} for doc in documents],
        "truncate": "NONE"
    }

    response = requests.post(url, headers=headers, json=payload)
    if response.status_code != 200:
        raise Exception(f"API Error: {response.status_code} - {response.text}")
    
    data = response.json()
    results = data.get("rankings", [])
    
    # Sort by index to maintain original document order
    results_sorted = sorted(results, key=lambda x: x["index"])
    return [round(item["logit"], 2) for item in results_sorted]

def main():
    if not api_key:
        print("Error: NVIDIA_API_KEY not found in .env file.")
        return

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

    print("--- Numbered List of Proverbs ---")
    for i, p in enumerate(proverbs, 1):
        print(f"{i}. {p}")
    
    print("\n--- Re-rank Score Matrix (Rows: Query, Columns: Documents) ---")
    print("Scores represent the relevance logit of the column proverb to the row proverb.")
    
    # Table header
    header = "      " + "".join([f"{i:^8}" for i in range(1, 11)])
    print(header)
    print("-" * len(header))

    for i, query in enumerate(proverbs):
        try:
            scores = get_rerank_scores(query, proverbs)
            # Print row
            row_str = f"{i+1:<5} |" + "".join([f"{score:^8.2f}" for score in scores])
            print(row_str)
        except Exception as e:
            print(f"Error processing row {i+1}: {e}")
            break

if __name__ == "__main__":
    main()
