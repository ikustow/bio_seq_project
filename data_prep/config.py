# =============================================================================
# CONFIGURATION
# =============================================================================

# REQUIRED by NCBI: Set this to your active email address
EMAIL = "your.email@example.com"

# Number of records to fetch per request
BATCH_SIZE = 400

# Retry logic parameters
MAX_RETRIES = 5
BASE_DELAY = 2

# Output file name
OUTPUT_FILE = "refseq_swissprot_cds.csv"

# =============================================================================
# EMBEDDING CONFIGURATION
# =============================================================================
EMBEDDING_MODEL_NAME = "long_context_models/hyenadna-medium-160k-seqlen-hf"
EMBEDDING_OUTPUT_FILE = "per-gene.h5"
EMBEDDING_MAX_LENGTH = 160_000
