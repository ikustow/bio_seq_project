# =============================================================================
# CONFIGURATION
# =============================================================================

# Swissprot data source
SWISSPROT_TSV = "swissprot.tsv"

# =============================================================================
# EMBEDDING CONFIGURATION
# =============================================================================
EMBEDDING_MODEL_NAME = "long_context_models/hyenadna-medium-160k-seqlen-hf"
EMBEDDING_OUTPUT_FILE = "per-gene.h5"
EMBEDDING_MAX_LENGTH = 160_000

