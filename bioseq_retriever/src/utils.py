import os
from pyfaidx import Fasta
from langchain_mistralai import ChatMistralAI, MistralAIEmbeddings

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

def setup_environment():
    """Sets up API keys and environment variables."""
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        raise ValueError("MISTRAL_API_KEY environment variable is not set.")
    return api_key

def get_llm(temperature=0):
    """Returns a configured Mistral LLM instance."""
    setup_environment()
    return ChatMistralAI(
        model="mistral-small-latest",
        temperature=temperature
    )

def get_text_embedder():
    """Returns a Mistral embeddings instance for text/context."""
    setup_environment()
    return MistralAIEmbeddings(model="mistral-embed")

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
    fasta = Fasta(fasta_path)
    first_record = fasta[0]
    return f">{first_record.name}\n{str(first_record)}"
