# DNA Back-Translation and Embedding Pipeline

This project provides a high-throughput, production-grade pipeline to generate synthetic DNA gene sequences from protein amino acid sequences and compute embeddings using the HyenaDNA model.

## Data Acquisition
To obtain the `swissprot.tsv` file:
1.  Go to the [UniProt website](https://www.uniprot.org/).
2.  Search for `reviewed:true` to filter by Swiss-Prot entries.
3.  Select the **Entry** and **Sequence** fields in the download options.
4.  Download the results as a TSV file and save it as `swissprot.tsv` in the project root directory.

## Workflow

1.  **Protein Data Input**: Reads protein UniProt accessions and amino acid sequences from `swissprot.tsv`.
2.  **Deterministic DNA Synthesis**: For each protein, it generates a single synthetic DNA gene sequence by randomly selecting codons from an inverse codon table. Seeding ensures full reproducibility and crash recovery.
3.  **Genomic Embedding**: Sequences are embedded in batches using the HyenaDNA model. The pipeline is heavily optimized for multi-core CPU inference, leveraging parallel preprocessing and efficient batching.
4.  **HDF5 Output**: Embeddings are stored in a high-performance HDF5 file (`per-gene.h5`). The HDF5 structure is designed for robustness (crash-safe, metadata-efficient for millions of entries) and direct compatibility with downstream FAISS retrieval systems.

## Prerequisites

-   Python 3.10+
-   [PyTorch](https://pytorch.org/)
-   [Hugging Face Transformers](https://huggingface.co/docs/transformers/index)
-   [h5py](https://www.h5py.org/)
-   [Polars](https://pola.rs/)
-   [NumPy](https://numpy.org/)

## Setup

1.  **Create Conda Environment**:
    It is highly recommended to use a clean Conda environment to manage dependencies:

    ```bash
    conda create -n bioprep-env -c conda-forge python=3.10 numpy pytorch transformers polars h5py -y
    conda activate bioprep-env
    ```

2.  **Configuration**:
    Adjust pipeline parameters in `config.py`. This includes:
    *   `THREAD_COUNT`: Automatically set to your CPU's logical core count, used to balance PyTorch threads and DataLoader workers.
    *   `BATCH_SIZE`: Optimized for CPU throughput (default 256).
    *   `EMBEDDING_MODEL_NAME`: Specifies the HyenaDNA model to use.
    *   `EMBEDDING_MAX_LENGTH`: Maximum sequence length the model can handle.
    *   `EMBEDDING_OUTPUT_FILE`: The name of the resulting HDF5 file.
    *   `SWISSPROT_TSV`: Path to your input data.

3.  **Input Data**:
    Ensure the `swissprot.tsv` file is present in the project root directory.

## Usage

Run the production-grade script:

```bash
python backtranslate_and_embed.py
```

The script will process the `swissprot.tsv` file, generate DNA sequences using parallelized workers, compute batch embeddings, and incrementally save results to `per-gene.h5`.
