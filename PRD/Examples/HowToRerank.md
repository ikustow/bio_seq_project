# How-To: NVIDIA Re-rank API Integration

This guide demonstrates how to use the NVIDIA NIM (NVIDIA Inference Microservice) Re-rank API to compute semantic relevance between queries and documents. Re-ranking is a crucial step in RAG (Retrieval-Augmented Generation) pipelines to refine search results.

## Prerequisites

1.  **NVIDIA API Key**: Obtain your key from the [NVIDIA API Catalog](https://build.nvidia.com/explore/discover).
2.  **Environment Variables**: Create a `.env` file in your project root:
    ```env
    NVIDIA_API_KEY=your_api_key_here
    ```
3.  **Dependencies**: Install the required Python libraries:
    ```bash
    pip install requests python-dotenv
    ```

## Implementation Example

The following script (`nvidia_rerank_demo.py`) generates 10 English proverbs and computes a cross-relevance matrix using the `nvidia/rerank-qa-mistral-4b` model.

```python
import os
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
API_KEY = os.getenv("NVIDIA_API_KEY")
API_URL = "https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking"

def get_rerank_scores(query, passages):
    """
    Fetches relevance scores from NVIDIA API.
    Returns a list of logits mapped to the original index of passages.
    """
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    
    payload = {
        "model": "nvidia/rerank-qa-mistral-4b",
        "query": {"text": query},
        "passages": [{"text": p} for p in passages],
        "truncate": "NONE"
    }

    response = requests.post(API_URL, headers=headers, json=payload)
    if response.status_code != 200:
        raise Exception(f"API Error {response.status_code}: {response.text}")
    
    # Sort rankings by index to maintain consistency with the input list
    rankings = response.json().get("rankings", [])
    sorted_rankings = sorted(rankings, key=lambda x: x["index"])
    return [round(item["logit"], 2) for item in sorted_rankings]

def main():
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

    print("### Proverbs List")
    for i, p in enumerate(proverbs, 1):
        print(f"{i}. {p}")
    
    print("\n### Relevance Matrix (Logits)")
    print("Rows = Queries | Columns = Passages")
    
    header = "      " + "".join([f"{i:^8}" for i in range(1, 11)])
    print(header)
    print("-" * len(header))

    for i, query in enumerate(proverbs):
        scores = get_rerank_scores(query, proverbs)
        row = f"{i+1:<5} |" + "".join([f"{s:^8.2f}" for s in scores])
        print(row)

if __name__ == "__main__":
    main()
```

## Example Output

When running the script, you will see a matrix where the diagonal (the proverb compared to itself) shows the highest scores.

```text
### Proverbs List
1. Actions speak louder than words.
2. Better late than never.
3. Cleanliness is next to godliness.
4. Don't judge a book by its cover.
5. Every cloud has a silver lining.
6. Haste makes waste.
7. It's no use crying over spilled milk.
8. Knowledge is power.
9. Laughter is the best medicine.
10. Practice makes perfect.

### Relevance Matrix (Logits)
Rows = Queries | Columns = Passages
         1       2       3       4       5       6       7       8       9       10   
--------------------------------------------------------------------------------------
1     |  3.94   -15.24  -15.86  -14.26  -15.96  -15.06  -15.55  -15.39  -17.17  -15.05 
2     | -14.91   2.89   -15.98  -16.02  -13.48  -12.56  -13.15  -16.55  -16.33  -15.06 
3     | -15.55  -15.02   4.72   -16.23  -15.27  -14.31  -16.30  -14.39  -14.92  -15.35 
4     | -13.56  -14.48  -15.62   4.54   -14.94  -14.70  -14.58  -15.26  -16.38  -15.19 
5     | -15.63  -13.98  -14.80  -15.23   3.46   -14.48  -14.17  -14.80  -14.93  -14.55 
6     | -14.27  -12.68  -12.91  -14.29  -13.90   4.91   -13.56  -13.60  -14.23  -12.20 
7     | -14.31  -13.23  -15.20  -14.58  -12.74  -13.27   5.96   -14.80  -14.27  -14.34 
8     | -14.48  -15.80  -13.47  -15.91  -15.66  -14.23  -16.59   3.06   -14.94  -14.12 
9     | -15.42  -15.02  -13.12  -16.47  -14.01  -14.90  -15.28  -14.05   4.24   -14.73 
10    | -14.41  -13.85  -14.25  -15.52  -14.65  -12.27  -15.04  -13.96  -15.10   3.13  
```

## Key Technical Details

*   **Model**: `nvidia/rerank-qa-mistral-4b` is a high-performance ranker optimized for Q&A and retrieval tasks.
*   **Logits**: The scores returned are typically logits. Higher values indicate greater semantic relevance.
*   **Batching**: The API allows multiple passages to be ranked against a single query in a single call, which is much more efficient than multiple individual requests.
*   **Endpoint**: Use `https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking`.
