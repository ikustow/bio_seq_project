from typing import List, Tuple

def get_similarity_score(similarity_val: float) -> float:
    """
    Normalizes a cosine similarity score (typically -1 to 1) 
    to a 0-1 scale. Assuming we are dealing with positive similarities 
    in a biological embedding space.
    """
    # Simply clip to 0-1 range
    return float(max(0.0, min(1.0, similarity_val)))

def rank_sequences(matches: List[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """
    Ranks the retrieved records by similarity.
    """
    return sorted(matches, key=lambda x: x[1], reverse=True)
