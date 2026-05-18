# DNA Back-Translation and Embedding Pipeline

This project provides a high-throughput, production-grade pipeline to generate DNA gene sequences from protein amino acid sequences and compute embeddings using genomic language models.

## Workflow

1.  **Protein Data Input**: Reads protein UniProt accessions and sequences from `swissprot.tsv`.
2.  **Prioritized Lookup**: Checks `refseq_swissprot_cds.csv` for existing DNA sequences.
3.  **Taxonomy-Informed Synthesis**: For proteins without RefSeq mappings, the pipeline uses **ProGen2** to infer taxonomic origin and **CodonTransformer** to generate plausible synthetic DNA sequences based on host-specific codon bias.
4.  **Genomic Embedding**: Sequences are embedded using the **HyenaDNA** model for high-throughput inference.
5.  **HDF5 Output**: Embeddings are stored in a high-performance HDF5 file (`per-gene.h5`), ensuring strict compatibility with downstream FAISS retrieval systems.

## Prerequisites

-   Python 3.10+
-   [PyTorch](https://pytorch.org/)
-   [Hugging Face Transformers](https://huggingface.co/docs/transformers/index)
-   [h5py](https://www.h5py.org/)
-   [Polars](https://pola.rs/)
-   [CodonTransformer](https://github.com/adibvafa/CodonTransformer)
-   [Multimolecule](https://github.com/salesforce/multimolecule)

## Setup

1.  **Create Conda Environment**:
    ```bash
    conda create -n bioprep-env -c conda-forge python=3.10 numpy pytorch transformers polars h5py -y
    conda activate bioprep-env
    pip install CodonTransformer multimolecule
    ```

2.  **Configuration**:
    Adjust pipeline parameters (e.g., input file paths) in `config.py`.

3.  **Input Data**:
    Ensure `swissprot.tsv` (protein data) and `refseq_swissprot_cds.csv` (optional RefSeq cache) are present in the project root directory.

## Usage

Run the production-grade script:

```bash
python backtranslate_and_embed.py
```

The script will process the input file, prioritize existing sequences, generate synthetic variants for unknowns, compute batch embeddings, and incrementally save results to `per-gene.h5`.
