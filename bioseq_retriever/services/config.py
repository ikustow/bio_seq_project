import os

# --- Service Addresses ---
# Unified services handle both embeddings and search
SEARCH_SERVICE_HOST = os.getenv("BIOSEQ_SEARCH_HOST", "0.0.0.0")
SEARCH_SERVICE_PORT = int(os.getenv("BIOSEQ_SEARCH_PORT", "8002"))

DNA_SEARCH_SERVICE_HOST = os.getenv("BIOSEQ_DNA_SEARCH_HOST", "0.0.0.0")
DNA_SEARCH_SERVICE_PORT = int(os.getenv("BIOSEQ_DNA_SEARCH_PORT", "8003"))

# --- Paths ---
# Protein Specific Paths
DEFAULT_H5_PATH = os.getenv("BIOSEQ_H5_PATH", "data/per-protein.h5")
DEFAULT_INDEX_PATH = os.getenv("BIOSEQ_INDEX_PATH", "data/per-protein.index")
DEFAULT_CACHE_PATH = os.getenv("BIOSEQ_ACCESSIONS_CACHE_PATH", "data/per-protein.accessions.json")

# DNA Specific Paths
DNA_H5_PATH = os.getenv("BIOSEQ_DNA_H5_PATH", "data/per-gene.h5")
DNA_INDEX_PATH = os.getenv("BIOSEQ_DNA_INDEX_PATH", "data/per-gene.index")
DNA_CACHE_PATH = os.getenv("BIOSEQ_DNA_ACCESSIONS_CACHE_PATH", "data/per-gene.accessions.json")

# --- FAISS HNSW Tuning ---
HNSW_M = 128
HNSW_EF_CONSTRUCTION = 512
HNSW_EF_SEARCH = 2048
RANDOM_SEED = 42

# --- Model Settings ---
MODEL_NAME = "Rostlab/prot_t5_xl_uniref50"
DNA_MODEL_NAME = "LongSafari/hyenadna-medium-160k-seqlen-hf"
DNA_MAX_LENGTH = 160_000

# --- Default FAISS Threads ---
DEFAULT_FAISS_THREADS = int(os.getenv("FAISS_DEFAULT_THREADS", max(1, os.cpu_count())))
