# DNA Back-Translation and Embedding Pipeline

This project provides a script to generate synthetic DNA gene sequences from protein amino acid sequences and then compute embeddings using a specialized genomic language model.

## Data Acquisition
To obtain the `swissprot.tsv` file:
1.  Go to the [UniProt website](https://www.uniprot.org/).
2.  Search for `reviewed:true` to filter by Swiss-Prot entries.
3.  Select the **Entry** and **Sequence** fields in the download options.
4.  Download the results as a TSV file and save it as `swissprot.tsv` in the `data_prep` directory.

## Workflow

1.  **Protein Data Input**: Reads protein UniProt accessions and amino acid sequences from `swissprot.tsv`.
2.  **DNA Gene Synthesis**: For each protein, it generates a single synthetic DNA gene sequence by randomly selecting codons from an inverse codon table for each amino acid.
3.  **Genomic Embedding**: Each synthetic DNA gene sequence is then embedded using the HyenaDNA model, producing a fixed-size vector representation.
4.  **Output**: The resulting embeddings are stored in a HDF5 file (`per-gene.h5`), with each UniProt accession serving as a key to a matrix of its corresponding gene variant embeddings.

## Setup

1.  **Create Conda Environment**:
    It is highly recommended to use a clean Conda environment to manage dependencies:

    ```bash
    conda create -n bioprep-env -c conda-forge python=3.10 numpy pytorch transformers polars h5py -y
    conda activate bioprep-env
    ```

2.  **Configuration**:
    Adjust pipeline parameters (e.g., number of gene variants per protein) in `config.py`. The script will automatically load `swissprot.tsv` and use the specified HyenaDNA model.

3.  **Input Data**:
    Ensure the `swissprot.tsv` file is present in the project root directory.

## Usage

Run the main script:

```bash
python backtranslate_and_embed.py
```

The script will process the `swissprot.tsv` file, generate DNA sequences, compute their embeddings, and save the results to `per-gene.h5`.
