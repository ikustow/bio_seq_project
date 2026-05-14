import os

# --- Service Addresses ---
# The unified gateway handles all biological retrieval and reranking
SEARCH_SERVICE_HOST = os.getenv("BIOSEQ_SEARCH_HOST", "0.0.0.0")
SEARCH_SERVICE_PORT = int(os.getenv("BIOSEQ_SEARCH_PORT", "8002"))

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
PROTEIN_MODEL_NAME = "Rostlab/prot_t5_xl_uniref50"
DNA_MODEL_NAME = "LongSafari/hyenadna-medium-160k-seqlen-hf"
RERANK_MODEL_NAME = "intfloat/e5-large-v2"

# Sequence length limits
DNA_MAX_LENGTH = 160_000

# --- Default FAISS Threads ---
DEFAULT_FAISS_THREADS = int(os.getenv("FAISS_DEFAULT_THREADS", max(1, os.cpu_count())))

# --- HDF5 Loading Settings ---
# Batch size for reading embeddings from H5 files during index construction
H5_BATCH_SIZE = int(os.getenv("BIOSEQ_H5_BATCH_SIZE", "1000"))
