# =============================================================================
# CONFIGURATION
# =============================================================================

from multiprocessing import cpu_count

THREAD_COUNT = cpu_count()

# Swissprot data source
SWISSPROT_TSV = "swissprot.tsv"

# Pipeline parameters
BATCH_SIZE = 256 # Optimal batch size for high-core CPU inference

# =============================================================================
# EMBEDDING CONFIGURATION
# =============================================================================
EMBEDDING_OUTPUT_FILE = "per-gene.h5"
EMBEDDING_MAX_LENGTH = 160_000

