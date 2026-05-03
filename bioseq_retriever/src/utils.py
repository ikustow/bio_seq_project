import os

standard_codon_table = {
    'TTT': 'F', 'TCT': 'S', 'TAT': 'Y', 'TGT': 'C',
    'TTC': 'F', 'TCC': 'S', 'TAC': 'Y', 'TGC': 'C',
    'TTA': 'L', 'TCA': 'S', 'TAA': '*', 'TGA': '*',
    'TTG': 'L', 'TCG': 'S', 'TAG': '*', 'TGG': 'W',

    'CTT': 'L', 'CCT': 'P', 'CAT': 'H', 'CGT': 'R',
    'CTC': 'L', 'CCC': 'P', 'CAC': 'H', 'CGC': 'R',
    'CTA': 'L', 'CCA': 'P', 'CAA': 'Q', 'CGA': 'R',
    'CTG': 'L', 'CCG': 'P', 'CAG': 'Q', 'CGG': 'R',

    'ATT': 'I', 'ACT': 'T', 'AAT': 'N', 'AGT': 'S',
    'ATC': 'I', 'ACC': 'T', 'AAC': 'N', 'AGC': 'S',
    'ATA': 'I', 'ACA': 'T', 'AAA': 'K', 'AGA': 'R',
    'ATG': 'M', 'ACG': 'T', 'AAG': 'K', 'AGG': 'R',

    'GTT': 'V', 'GCT': 'A', 'GAT': 'D', 'GGT': 'G',
    'GTC': 'V', 'GCC': 'A', 'GAC': 'D', 'GGC': 'G',
    'GTA': 'V', 'GCA': 'A', 'GAA': 'E', 'GGA': 'G',
    'GTG': 'V', 'GCG': 'A', 'GAG': 'E', 'GGG': 'G'
}

def _select_provider(provider: str | None = None) -> str:
    requested = (provider or os.getenv("BIOSEQ_LLM_PROVIDER") or "").strip().lower()
    if requested:
        if requested not in {"mistral", "openai"}:
            raise ValueError("BIOSEQ_LLM_PROVIDER must be either 'mistral' or 'openai'.")
        return requested
    if os.getenv("MISTRAL_API_KEY"):
        return "mistral"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "mistral"


def setup_environment(provider: str | None = None):
    """Sets up API keys and environment variables."""
    selected_provider = _select_provider(provider)
    env_key = "OPENAI_API_KEY" if selected_provider == "openai" else "MISTRAL_API_KEY"
    api_key = os.getenv(env_key)
    if not api_key:
        raise ValueError("Set MISTRAL_API_KEY or OPENAI_API_KEY before running the pipeline.")
    return api_key

def get_llm(temperature=0):
    """Returns a configured chat LLM instance."""
    provider = _select_provider()
    setup_environment(provider)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-nano"),
            temperature=temperature
        )

    from langchain_mistralai import ChatMistralAI

    return ChatMistralAI(
        model=os.getenv("MISTRAL_MODEL", "mistral-small-latest"),
        temperature=temperature
    )

def get_text_embedder():
    """Returns a configured embeddings instance for text/context."""
    provider = _select_provider(os.getenv("BIOSEQ_EMBEDDINGS_PROVIDER"))
    setup_environment(provider)
    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=os.getenv("OPENAI_EMBEDDINGS_MODEL", "text-embedding-3-small"))

    from langchain_mistralai import MistralAIEmbeddings

    return MistralAIEmbeddings(model=os.getenv("MISTRAL_EMBEDDINGS_MODEL", "mistral-embed"))

def translate_dna_to_protein(dna_sequence):
    if len(dna_sequence) % 3 != 0:
        raise Exception("The coding sequence (CDS) length must be divisible by 3, "
                        "as each codon is 3 nucleotides long")
    
    def codons(sequence):
        return (sequence[i:i+3] for i in range(0, len(sequence), 3))

    def translate_codon(codon):
        ambiguous_bases = {'N', 'R', 'Y', 'K', 'M', 'S', 'W', 'B', 'D', 'H', 'V'}
        if any(base in ambiguous_bases for base in codon):
            return 'X'
        return standard_codon_table.get(codon, 'X')

    def build_protein(codons_iter):
        protein = []
        for codon in codons_iter:
            amino_acid = translate_codon(codon)
            if amino_acid == '*':
                break
            protein.append(amino_acid)
        return ''.join(protein)

    upper_dna = dna_sequence.upper()
    return build_protein(codons(upper_dna))

def get_first_fasta_entry(fasta_path: str) -> str:
    """
    Extracts the first header and sequence from a FASTA file using pyfaidx.
    """
    from pyfaidx import Fasta

    fasta = Fasta(fasta_path)
    first_record = fasta[0]
    return f">{first_record.name}\n{str(first_record)}"
