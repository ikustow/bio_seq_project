# RefSeq to Swiss-Prot CDS Mapper

This project provides a robust, functional Python script to map RefSeq mRNA coding sequences (CDS) to their corresponding UniProt/Swiss-Prot protein accessions.

## Features
- **Functional Architecture**: Modular design for clarity and maintainability.
- **NCBI History Server**: Efficiently manages large-scale data retrieval using `WebEnv` and `QueryKey` to prevent memory and network bottlenecks.
- **Robustness**: Implements exponential backoff and retry logic to gracefully handle network timeouts, HTTP errors, and NCBI rate-limiting.
- **Pandas Integration**: Leverages Pandas for high-performance data processing, length statistics, and CSV export.
- **Surgical Extraction**: Targets annotated `CDS` features within mRNA records to ensure accurate extraction of coding sequences.

## Setup
1. **Create Conda Environment**:
   To avoid dependency conflicts, create a new Conda environment and install dependencies natively:

   ```bash
   conda create -n bioprep-env -c conda-forge python=3.10 numpy pandas biopython certifi urllib3 -y
   conda activate bioprep-env
   ```

2. **Configure Email**:
   Open `refseq_to_swissprot.py` and update the `Entrez.email` field with your active email address. NCBI requires this to identify your requests.

   ```python
   Entrez.email = "your.email@example.com"
   ```

3. **(Optional) API Key**:
   For higher rate limits (10 requests/sec instead of 3), you can register for a free NCBI account. To protect your credentials, the script loads the key from an environment variable. Set it in your terminal before running:
   ```bash
   # Windows (PowerShell)
   $env:NCBI_API_KEY="your_api_key_here"
   
   # Linux/macOS
   export NCBI_API_KEY="your_api_key_here"
   ```

## Usage
Run the script from your terminal:
```bash
python refseq_to_swissprot.py
```

The script will:
1. Search NCBI for relevant RefSeq records.
2. Fetch them in batches.
3. Extract mapping information and sequences.
4. Print summary statistics (total pairs, shortest/longest/average CDS length).
5. Save the mappings to `refseq_swissprot_cds.csv`.
