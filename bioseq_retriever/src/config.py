import os

# --- Default Paths ---
DEFAULT_H5_PATH = os.getenv("BIOSEQ_H5_PATH", "data/per-protein.h5")

# Derive other paths from H5_PATH by default if not explicitly set
_h5_base = os.path.splitext(DEFAULT_H5_PATH)[0]

DEFAULT_INDEX_PATH = os.getenv("BIOSEQ_INDEX_PATH", f"{_h5_base}.index")
DEFAULT_CACHE_PATH = os.getenv("BIOSEQ_ACCESSIONS_CACHE_PATH", f"{_h5_base}.accessions.json")

# --- Security ---
ALLOWED_DATA_DIR = os.getenv("BIOSEQ_ALLOWED_DATA_DIR", "data")

# --- Fetcher ---
FETCH_TIMEOUT = float(os.getenv("BIOSEQ_FETCH_TIMEOUT", "10.0"))
