import os

# --- Service Addresses ---
# The unified search service handles both embeddings and search
SEARCH_SERVICE_HOST = os.getenv("BIOSEQ_SEARCH_HOST", "0.0.0.0")
SEARCH_SERVICE_PORT = int(os.getenv("BIOSEQ_SEARCH_PORT", "8002"))

# --- Paths ---
# These paths are used by the service to load data
DEFAULT_H5_PATH = os.getenv("BIOSEQ_H5_PATH", "data/per-protein.h5")
DEFAULT_INDEX_PATH = os.getenv("BIOSEQ_INDEX_PATH", "data/per-protein.index")
DEFAULT_CACHE_PATH = os.getenv("BIOSEQ_ACCESSIONS_CACHE_PATH", "data/per-protein.accessions.json")

# --- FAISS HNSW Tuning ---
HNSW_M = 128
HNSW_EF_CONSTRUCTION = 512
HNSW_EF_SEARCH = 2048
RANDOM_SEED = 42

# --- Model Settings ---
MODEL_NAME = "Rostlab/prot_t5_xl_uniref50"
