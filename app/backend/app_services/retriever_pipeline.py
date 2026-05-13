from __future__ import annotations

import asyncio
import importlib
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from backend.app_contracts import BioSeqInputExtraction, BioSeqPipelineSnapshot, CandidateView

from .protein_view_mapper import uniprot_record_to_candidate


STANDARD_CODON_TABLE = {
    "TTT": "F", "TCT": "S", "TAT": "Y", "TGT": "C",
    "TTC": "F", "TCC": "S", "TAC": "Y", "TGC": "C",
    "TTA": "L", "TCA": "S", "TAA": "*", "TGA": "*",
    "TTG": "L", "TCG": "S", "TAG": "*", "TGG": "W",
    "CTT": "L", "CCT": "P", "CAT": "H", "CGT": "R",
    "CTC": "L", "CCC": "P", "CAC": "H", "CGC": "R",
    "CTA": "L", "CCA": "P", "CAA": "Q", "CGA": "R",
    "CTG": "L", "CCG": "P", "CAG": "Q", "CGG": "R",
    "ATT": "I", "ACT": "T", "AAT": "N", "AGT": "S",
    "ATC": "I", "ACC": "T", "AAC": "N", "AGC": "S",
    "ATA": "I", "ACA": "T", "AAA": "K", "AGA": "R",
    "ATG": "M", "ACG": "T", "AAG": "K", "AGG": "R",
    "GTT": "V", "GCT": "A", "GAT": "D", "GGT": "G",
    "GTC": "V", "GCC": "A", "GAC": "D", "GGC": "G",
    "GTA": "V", "GCA": "A", "GAA": "E", "GGA": "G",
    "GTG": "V", "GCG": "A", "GAG": "E", "GGG": "G",
}

DNA_IUPAC = set("ACGTUNRYKMSWBDHV")
PROTEIN_ALPHABET = set("ACDEFGHIKLMNPQRSTVWY")
PROTEIN_ONLY = PROTEIN_ALPHABET - DNA_IUPAC
SEQUENCE_TOKEN_RE = re.compile(r"[A-Za-z*\-]{10,}")
PATH_RE = re.compile(r"(?P<path>(?:[\w./~:-]+)?[\w.-]+\.(?:fasta|faa|fna|nuc|pep|pro|fa))", re.IGNORECASE)


EXTRACTION_SYSTEM_PROMPT = (
    "You are an expert bioinformatics analyst specializing in sequence identification and data extraction. "
    "Your mission is to parse user input to extract biological data and determine its molecular nature with high precision. "
    "You must follow this multi-step Chain-of-Thought process:\n\n"
    "### 1. EXTRACTION STRATEGY\n"
    "Your first priority is to separate the core data from the surrounding metadata.\n"
    "- Biological Sequence: Look for strings composed of single-letter codes. They may appear as raw text or within a FASTA format (starting with a '>' header line). DNA sequences usually consist of A, T, G, C, while Proteins use a wider 20-letter alphabet (M, A, L, etc.).\n"
    "- File Path: Identify strings that resemble filesystem paths (e.g., 'data/sample.fasta', 'C:\\Sequences\\test.faa', './output.txt'). These point to external files containing sequences.\n"
    "- Contextual Information: Everything else is context. This includes user instructions (e.g., 'Find the closest matches'), biological metadata ('This is from a mouse'), or specific queries.\n"
    "*Rule*: If both a sequence and a path are present, prioritize the sequence for the 'sequence_or_path' field and note the path in the reasoning.\n\n"
    "### 2. CLASSIFICATION REASONING\n"
    "This is the most complex task. You must classify the sequence as either DNA or PROTEIN by weighing multiple evidence tiers:\n\n"
    "- Tier A: Explicit Hints & Metadata: Search the prompt and FASTA headers for keywords.\n"
    "  * DNA/RNA Indicators: 'DNA', 'RNA', 'nucleotide', 'transcript', 'gene', 'genome'. RefSeq prefixes: 'NM_', 'NR_', 'XM_', 'XR_'.\n"
    "  * Protein Indicators: 'protein', 'peptide', 'amino acid', 'ORF', 'proteome'. RefSeq prefixes: 'NP_', 'XP_', 'YP_'.\n"
    "- Tier B: Structural Cues: Look at the file extension if a path is provided.\n"
    "  * DNA: .fna, .fasta, .fa, .nuc.\n"
    "  * Protein: .faa, .pro, .pep.\n"
    "- Tier C: Character Composition Analysis:\n"
    "  * DNA: Strictly limited to {A, C, G, T, U} and IUPAC ambiguity codes {N, R, Y, K, M, S, W, B, D, H, V}. If the sequence contains characters like {L, I, V, F, W, Y, P, Q, E, K, H, R}, it is almost certainly a PROTEIN.\n"
    "  * Protein: Uses a much broader character set. The presence of 'L' (Leucine), 'F' (Phenylalanine), or 'W' (Tryptophan) is a strong indicator of protein, as these are common in proteins but never found in DNA (except as errors).\n"
    "  * Ambiguity Check: A sequence like 'ACGT' is ambiguous but defaults to DNA. A sequence like 'MAPLR' is unambiguously PROTEIN.\n"
    "- Tier D: Length and Pattern: Consider the length. Very short sequences might require more weight on Tier A.\n\n"
    "### 3. CONFIDENCE ASSESSMENT\n"
    "Evaluate the internal consistency of the evidence.\n"
    "- High Confidence (is_confident = True): Evidence across Tiers A, B, and C is consistent (e.g., user says 'protein' and the sequence contains 'L' and 'W').\n"
    "- Low Confidence (is_confident = False): Evidence is contradictory (e.g., user says 'DNA' but the sequence contains 'P' and 'Q'), or the sequence is too short and lacks any Tier A/B markers (e.g., a raw 'AAAAAA' with no context).\n\n"
    "Deliver your findings in the requested structured format, ensuring the 'reasoning' field reflects this multi-tier analysis."
)


def translate_dna_to_protein(dna_sequence: str) -> str:
    normalized = "".join(dna_sequence.upper().split()).replace("U", "T")
    if len(normalized) % 3 != 0:
        raise ValueError(
            "The coding sequence (CDS) length must be divisible by 3, "
            "as each codon is 3 nucleotides long"
        )

    protein: list[str] = []
    ambiguous_bases = {"N", "R", "Y", "K", "M", "S", "W", "B", "D", "H", "V"}
    for index in range(0, len(normalized), 3):
        codon = normalized[index : index + 3]
        amino_acid = "X" if any(base in ambiguous_bases for base in codon) else STANDARD_CODON_TABLE.get(codon, "X")
        if amino_acid == "*":
            break
        protein.append(amino_acid)
    return "".join(protein)


def normalize_protein_sequence(sequence: str) -> str:
    lines = sequence.strip().splitlines()
    if lines and lines[0].startswith(">"):
        lines = [line for line in lines if not line.startswith(">")]
    return "".join("".join(lines).upper().split())


class BioSeqRetrieverPipeline:
    def __init__(
        self,
        llm_factory: Callable[[], object] | None = None,
        allow_runtime_filepaths: bool = False,
        enable_runtime_retriever: bool | None = None,
    ) -> None:
        self._llm_factory = llm_factory
        self._allow_runtime_filepaths = allow_runtime_filepaths
        self._enable_runtime_retriever = (
            _env_flag("BIOSEQ_ENABLE_RUNTIME_RETRIEVER", default=True)
            if enable_runtime_retriever is None
            else enable_runtime_retriever
        )

    def run(self, prompt: str, limit: int = 5) -> tuple[BioSeqPipelineSnapshot, list[CandidateView]]:
        state = BioSeqPipelineSnapshot(prompt=prompt)
        candidates: list[CandidateView] = []

        extraction = self.extract_and_classify(prompt)
        state.sequence_or_path = extraction.sequence_or_path
        state.input_type = extraction.input_type
        state.context = extraction.context
        state.sequence_type = extraction.sequence_type
        state.is_confident = extraction.is_confident
        if extraction.reasoning:
            state.warnings.append(f"Input classification: {extraction.reasoning}")

        if state.input_type == "TEXT":
            return state, candidates

        if state.input_type == "FILEPATH":
            if not self._allow_runtime_filepaths:
                state.controlled_miss = True
                state.error = "Runtime file path resolution is disabled."
                state.warnings.append("Send the raw FASTA/sequence or enable runtime file path resolution.")
                return state, candidates
            state.controlled_miss = True
            state.error = "Runtime file path resolution is not implemented for this service."
            return state, candidates

        state.sequence = use_raw_sequence(extraction.sequence_or_path or "")
        if not state.sequence:
            state.controlled_miss = True
            state.error = "No biological sequence could be extracted from the prompt."
            return state, candidates
        if state.sequence_type == "UNKNOWN":
            state.controlled_miss = True
            state.error = "Sequence type could not be classified as DNA or protein."
            return state, candidates

        try:
            state.protein_sequence = (
                translate_dna_to_protein(state.sequence)
                if state.sequence_type == "DNA"
                else normalize_protein_sequence(state.sequence)
            )
        except Exception as exc:
            state.controlled_miss = True
            state.error = f"Translation failed: {exc}"
            return state, candidates

        if self._enable_runtime_retriever:
            runtime_state, runtime_candidates = self._run_runtime_retriever(prompt, state, limit=limit)
            if runtime_candidates:
                return runtime_state, runtime_candidates
            return runtime_state, runtime_candidates

        state.controlled_miss = True
        state.error = "bioseq_retriever runtime is disabled."
        return state, candidates

    def _run_runtime_retriever(
        self,
        prompt: str,
        state: BioSeqPipelineSnapshot,
        limit: int,
    ) -> tuple[BioSeqPipelineSnapshot, list[CandidateView]]:
        try:
            result = _run_backend_bioseq_pipeline(prompt)
        except Exception as exc:
            state.controlled_miss = True
            state.error = f"bioseq_retriever runtime failed: {exc}"
            return state, []

        _merge_runtime_state(state, result)
        if state.error:
            state.controlled_miss = True
            return state, []

        records = result.get("final_results") or []
        candidates = [
            uniprot_record_to_candidate(record, rank=index)
            for index, record in enumerate(records[:limit])
            if isinstance(record, dict)
        ]
        if candidates:
            state.active_accession = candidates[0].protein.accession
            state.controlled_miss = False
            state.error = None
            return state, candidates

        state.controlled_miss = True
        state.error = "bioseq_retriever runtime completed without final matches."
        return state, []

    def extract_and_classify(self, prompt: str) -> BioSeqInputExtraction:
        if self._llm_factory and os.getenv("BIOSEQ_INPUT_EXTRACTOR", "deterministic").lower() == "llm":
            try:
                return self._extract_with_llm(prompt)
            except Exception as exc:
                fallback = deterministic_extract_and_classify(prompt)
                fallback.reasoning = f"LLM extraction failed ({exc}); used deterministic fallback. {fallback.reasoning}"
                fallback.is_confident = False
                return fallback
        return deterministic_extract_and_classify(prompt)

    def _extract_with_llm(self, prompt: str) -> BioSeqInputExtraction:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = self._llm_factory()
        structured_llm = llm.with_structured_output(BioSeqInputExtraction)
        result = structured_llm.invoke([SystemMessage(content=EXTRACTION_SYSTEM_PROMPT), HumanMessage(content=prompt)])
        return BioSeqInputExtraction.model_validate(result)


def _run_backend_bioseq_pipeline(prompt: str) -> dict[str, Any]:
    _ensure_backend_bioseq_import_path()
    pipeline_module = importlib.import_module("src.pipeline")
    run_pipeline = getattr(pipeline_module, "run_bioseq_pipeline")
    result = _run_coro_sync(run_pipeline(prompt))
    return dict(result or {})


def _ensure_backend_bioseq_import_path() -> None:
    backend_retriever_root = Path(__file__).resolve().parents[1] / "bioseq_retriever"
    root_text = str(backend_retriever_root)
    if root_text in sys.path:
        sys.path.remove(root_text)
    sys.path.insert(0, root_text)

    cached_pipeline = sys.modules.get("src.pipeline")
    cached_file = Path(getattr(cached_pipeline, "__file__", "") or "") if cached_pipeline else None
    if cached_file and not _is_relative_to(cached_file.resolve(), backend_retriever_root.resolve()):
        for name in list(sys.modules):
            if name == "src" or name.startswith("src."):
                del sys.modules[name]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _run_coro_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}
    error: list[BaseException] = []

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result.get("value")


def _merge_runtime_state(state: BioSeqPipelineSnapshot, result: dict[str, Any]) -> None:
    state.sequence_or_path = result.get("sequence_or_path") or state.sequence_or_path
    state.input_type = result.get("input_type") or state.input_type
    state.context = result.get("context") or state.context
    state.sequence = result.get("sequence") or state.sequence
    state.sequence_type = result.get("sequence_type") or state.sequence_type
    state.protein_sequence = result.get("protein_sequence") or state.protein_sequence
    if result.get("is_confident") is not None:
        state.is_confident = bool(result.get("is_confident"))
    if result.get("error"):
        state.error = str(result["error"])


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def deterministic_extract_and_classify(prompt: str) -> BioSeqInputExtraction:
    fasta = _extract_fasta(prompt)
    path = _extract_path(prompt)
    sequence = fasta or _extract_sequence_token(prompt)
    context_source = sequence or path
    context = prompt.replace(context_source, " ").strip() if context_source else prompt.strip()

    if sequence:
        sequence_type, confident, reason = _classify_sequence(sequence, prompt)
        return BioSeqInputExtraction(
            sequence_or_path=sequence,
            input_type="SEQUENCE",
            context=" ".join(context.split()),
            sequence_type=sequence_type,
            is_confident=confident,
            reasoning=reason,
        )

    if path:
        sequence_type, confident, reason = _classify_path(path, prompt)
        return BioSeqInputExtraction(
            sequence_or_path=path,
            input_type="FILEPATH",
            context=" ".join(context.split()),
            sequence_type=sequence_type,
            is_confident=confident,
            reasoning=reason,
        )

    return BioSeqInputExtraction(context=prompt.strip(), reasoning="No raw sequence or FASTA/file path was detected.")


def use_raw_sequence(sequence_or_path: str) -> str:
    if sequence_or_path.startswith(">"):
        lines = sequence_or_path.splitlines()
        return "".join(line.strip() for line in lines[1:] if line.strip())
    return normalize_protein_sequence(sequence_or_path).replace("-", "").replace("*", "")


def _extract_fasta(prompt: str) -> str | None:
    lines = prompt.strip().splitlines()
    for index, line in enumerate(lines):
        if line.startswith(">"):
            body = [line]
            for candidate in lines[index + 1 :]:
                compact = "".join(candidate.split())
                if compact and set(compact.upper()) <= (DNA_IUPAC | PROTEIN_ALPHABET | {"*", "-"}):
                    body.append(candidate)
                elif body:
                    break
            return "\n".join(body) if len(body) > 1 else None
    return None


def _extract_path(prompt: str) -> str | None:
    match = PATH_RE.search(prompt)
    return match.group("path") if match else None


def _extract_sequence_token(prompt: str) -> str | None:
    best = ""
    for match in SEQUENCE_TOKEN_RE.finditer(prompt):
        token = normalize_protein_sequence(match.group(0)).replace("-", "").replace("*", "")
        if len(token) >= 10 and set(token.upper()) <= (DNA_IUPAC | PROTEIN_ALPHABET):
            if len(token) > len(best):
                best = token
    return best or None


def _classify_path(path: str, prompt: str) -> tuple[str, bool, str]:
    lowered = f"{path} {prompt}".lower()
    if path.lower().endswith((".faa", ".pro", ".pep")):
        return "PROTEIN", True, "Protein file extension detected."
    if path.lower().endswith((".fna", ".nuc")):
        return "DNA", True, "DNA file extension detected."
    if any(word in lowered for word in ("protein", "peptide", "amino acid", "proteome")):
        return "PROTEIN", True, "Protein hint detected in prompt."
    if any(word in lowered for word in ("dna", "rna", "nucleotide", "transcript", "gene", "genome")):
        return "DNA", True, "DNA/RNA hint detected in prompt."
    return "UNKNOWN", False, "Path extension is ambiguous."


def _classify_sequence(sequence: str, prompt: str) -> tuple[str, bool, str]:
    normalized = use_raw_sequence(sequence).upper().replace("U", "T")
    letters = set(normalized)
    lowered = prompt.lower()
    protein_hint = any(word in lowered for word in ("protein", "peptide", "amino acid", "proteome", "np_", "xp_", "yp_"))
    dna_hint = any(word in lowered for word in ("dna", "rna", "nucleotide", "transcript", "gene", "genome", "nm_", "nr_", "xm_", "xr_"))

    if letters & PROTEIN_ONLY:
        return "PROTEIN", not dna_hint, "Protein-only amino-acid letters detected."
    if letters <= set("ACGTU"):
        if protein_hint and not dna_hint:
            return "PROTEIN", False, "Only A/C/G/T/U letters found, but prompt contains protein hints."
        return "DNA", not protein_hint, "Sequence is limited to canonical nucleotide letters."
    if letters <= DNA_IUPAC:
        if protein_hint and not dna_hint:
            return "PROTEIN", False, "IUPAC-compatible letters found, but prompt contains protein hints."
        return "DNA", not protein_hint, "Sequence is compatible with DNA/RNA IUPAC ambiguity codes."
    if letters <= PROTEIN_ALPHABET:
        return "PROTEIN", not dna_hint, "Sequence is compatible with the protein alphabet."
    return "UNKNOWN", False, "Sequence contains symbols outside supported DNA/protein alphabets."
