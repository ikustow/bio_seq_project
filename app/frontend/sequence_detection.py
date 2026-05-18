"""Sequence and UniProt-ID detection / normalization for the chat composer.

Pure Python — no Streamlit imports. Backend uses the same primitives where
applicable so the UI's fast preview matches the source-of-truth validation
on the server side.

NOTE: ``app/frontend/components/chat.py`` carries a 1:1 JavaScript port of
this module's helpers (``normalize``, ``classify``, ``parse_fasta``,
``_split_inline_fasta_header``, ``detect_from_text``) for the live preview
above the chat input. When you change detection here, mirror the change
there — the function names match on purpose.

Public entry points:

- ``detect_from_text(text)``   — analyze a pasted/typed chat message,
                                   returning the cleaned display text plus
                                   a list of detected items (sequences and
                                   UniProt accessions/IDs).
- ``detect_uniprot_id(token)`` — return ``(accession, uniprot_id)`` if the
                                   token looks like a UniProt accession or
                                   mnemonic ID, else ``(None, None)``.
- ``parse_fasta(text)``        — multi-entry FASTA parser.
- ``parse_uploaded_file(...)`` — parse a Streamlit ``UploadedFile`` as
                                   FASTA or a single raw sequence.
- ``classify_sequence(seq)``   — DNA / RNA / PROTEIN / UNKNOWN heuristic.
- ``normalize_sequence(seq)``  — strip whitespace, headers, formatting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

SequenceType = Literal["DNA", "RNA", "PROTEIN", "UNKNOWN"]


# Alphabets ----------------------------------------------------------------

DNA_STRICT = set("ACGT")
RNA_STRICT = set("ACGU")
NUCLEOTIDE_AMBIGUOUS = set("ACGTUNRYSWKMBDHV")
PROTEIN_STANDARD = set("ACDEFGHIKLMNPQRSTVWY")
PROTEIN_EXTENDED = set("ACDEFGHIKLMNPQRSTVWYXBZJUO")
PROTEIN_ONLY_LETTERS = PROTEIN_STANDARD - NUCLEOTIDE_AMBIGUOUS  # EFILPQ + others

# Minimum length of a raw (header-less) block to auto-classify as a sequence.
MIN_RAW_SEQUENCE_LENGTH = 30

# Characters we strip during normalization (formatting noise).
_FORMATTING_NOISE_RE = re.compile(r"[\s\-.0-9]")


# UniProt regular expressions ----------------------------------------------
# Accessions (Swiss-Prot/TrEMBL):
#   [OPQ][0-9][A-Z0-9]{3}[0-9]
#   [A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}
_UNIPROT_ACCESSION_RE = re.compile(
    r"^(?:[OPQ][0-9][A-Z0-9]{3}[0-9]"
    r"|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})$"
)
# Mnemonic identifier: 1-5 alnum chars + "_" + 1-5 alnum chars (uppercase).
_UNIPROT_MNEMONIC_RE = re.compile(r"^[A-Z0-9]{1,10}_[A-Z0-9]{1,5}$")


@dataclass
class DetectedSequence:
    """A single sequence extracted from the composer."""

    label_hint: str | None  # filled later by session_objects (Seq_A...)
    raw_text_span: tuple[int, int] | None
    fasta_header: str | None
    raw_sequence: str
    normalized_sequence: str
    sequence_type: SequenceType
    confidence: float
    reason: str
    warnings: list[str] = field(default_factory=list)
    invalid_chars: list[str] = field(default_factory=list)
    length: int = 0
    uniprot_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label_hint": self.label_hint,
            "raw_text_span": list(self.raw_text_span) if self.raw_text_span else None,
            "fasta_header": self.fasta_header,
            "raw_sequence": self.raw_sequence,
            "normalized_sequence": self.normalized_sequence,
            "sequence_type": self.sequence_type,
            "confidence": self.confidence,
            "reason": self.reason,
            "warnings": list(self.warnings),
            "invalid_chars": list(self.invalid_chars),
            "length": self.length,
            "uniprot_hint": self.uniprot_hint,
        }


@dataclass
class DetectedUniProt:
    token: str
    accession: str | None
    uniprot_id: str | None
    raw_text_span: tuple[int, int] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "accession": self.accession,
            "uniprot_id": self.uniprot_id,
            "raw_text_span": list(self.raw_text_span) if self.raw_text_span else None,
        }


@dataclass
class DetectionResult:
    """Output of ``detect_from_text``."""

    display_text: str
    sequences: list[DetectedSequence] = field(default_factory=list)
    uniprot_refs: list[DetectedUniProt] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_text": self.display_text,
            "sequences": [s.to_dict() for s in self.sequences],
            "uniprot_refs": [u.to_dict() for u in self.uniprot_refs],
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_sequence(raw: str) -> tuple[str, list[str], list[str]]:
    """Strip formatting noise and uppercase a sequence body.

    Returns ``(normalized, invalid_chars, warnings)`` — ``invalid_chars`` are
    leftover non-alphabet characters after cleanup.
    """
    if not raw:
        return "", [], []
    # If a FASTA header sneaks in, strip its line.
    lines = [ln for ln in raw.splitlines() if not ln.startswith(">")]
    body = "\n".join(lines)
    body = _FORMATTING_NOISE_RE.sub("", body).upper()

    warnings: list[str] = []
    invalid: list[str] = []
    cleaned_chars: list[str] = []
    allowed = PROTEIN_EXTENDED | NUCLEOTIDE_AMBIGUOUS | {"*"}
    for ch in body:
        if ch in allowed:
            cleaned_chars.append(ch)
        else:
            if ch not in invalid:
                invalid.append(ch)
    if invalid:
        warnings.append(
            f"Stripped {len(invalid)} kind(s) of invalid character(s): {''.join(invalid)}"
        )
    return "".join(cleaned_chars), invalid, warnings


# ---------------------------------------------------------------------------
# UniProt id / accession detection
# ---------------------------------------------------------------------------


def detect_uniprot_id(token: str) -> tuple[str | None, str | None]:
    """Return ``(accession, uniprot_id)`` for a single token.

    Either side may be ``None``: an accession-only match returns
    ``(token, None)``; a mnemonic match returns ``(None, token)``.
    """
    candidate = (token or "").strip().upper()
    if not candidate:
        return None, None
    if _UNIPROT_ACCESSION_RE.match(candidate):
        return candidate, None
    if _UNIPROT_MNEMONIC_RE.match(candidate):
        return None, candidate
    return None, None


def find_uniprot_refs(text: str) -> list[DetectedUniProt]:
    """Scan free-form text for UniProt accessions / mnemonics."""
    found: list[DetectedUniProt] = []
    if not text:
        return found
    # Match @-prefixed mentions as well as bare tokens. ``\b`` word boundaries
    # keep us from matching inside larger alphanumeric runs (e.g. sequence
    # bodies).
    token_re = re.compile(r"@?([A-Za-z0-9_]{4,15})")
    for match in token_re.finditer(text):
        token = match.group(1)
        accession, uniprot_id = detect_uniprot_id(token)
        if accession or uniprot_id:
            found.append(
                DetectedUniProt(
                    token=token,
                    accession=accession,
                    uniprot_id=uniprot_id,
                    raw_text_span=(match.start(), match.end()),
                )
            )
    return found


# ---------------------------------------------------------------------------
# Sequence classification
# ---------------------------------------------------------------------------


def classify_sequence(
    seq: str,
    fasta_header: str | None = None,
) -> dict[str, Any]:
    """Heuristically classify a normalized sequence as DNA/RNA/PROTEIN.

    Returns a dict mirroring the ``SequenceClassification`` TypedDict from
    the spec.
    """
    normalized, invalid, warnings = normalize_sequence(seq)
    length = len(normalized)
    letters = set(normalized)
    header_hint: str | None = None

    if fasta_header:
        lowered = fasta_header.lower()
        if any(
            tok in lowered
            for tok in ("dna", "rna", "nucleotide", "transcript", "gene", "genome", "nm_", "nr_", "xm_", "xr_")
        ):
            header_hint = "DNA"
        elif any(
            tok in lowered
            for tok in ("protein", "peptide", "amino acid", "proteome", "np_", "xp_", "yp_", "sp|", "tr|")
        ):
            header_hint = "PROTEIN"

    if not letters:
        return _classification(
            "UNKNOWN", 0.0, normalized, length, invalid,
            warnings + ["Sequence is empty after normalization."],
            "No usable characters in the sequence body.",
        )

    # Protein-only letters resolve cleanly to PROTEIN.
    if letters & PROTEIN_ONLY_LETTERS:
        return _classification(
            "PROTEIN", 0.95, normalized, length, invalid, warnings,
            "Contains amino-acid-only letters outside the nucleotide alphabet.",
        )

    # Nucleotide-pure cases.
    if letters <= DNA_STRICT:
        if length < MIN_RAW_SEQUENCE_LENGTH and header_hint is None:
            return _classification(
                "UNKNOWN", 0.3, normalized, length, invalid,
                warnings + [f"Sequence is too short ({length} chars) to classify with confidence."],
                "Only A/C/G/T letters but length is below the auto-detect threshold.",
            )
        if header_hint == "PROTEIN":
            return _classification(
                "PROTEIN", 0.55, normalized, length, invalid,
                warnings + ["FASTA header suggests protein but body is A/C/G/T only."],
                "FASTA header overrides nucleotide-pure body.",
            )
        return _classification(
            "DNA", 0.8, normalized, length, invalid, warnings,
            "Sequence is limited to canonical DNA letters (A/C/G/T).",
        )
    if letters <= RNA_STRICT or ("U" in letters and "T" not in letters):
        return _classification(
            "RNA", 0.8, normalized, length, invalid, warnings,
            "Sequence contains U without T — likely RNA.",
        )
    if letters <= NUCLEOTIDE_AMBIGUOUS:
        return _classification(
            "DNA", 0.65, normalized, length, invalid, warnings,
            "Letters fall within the DNA IUPAC ambiguity alphabet.",
        )
    if letters <= PROTEIN_EXTENDED:
        confidence = 0.85 if length >= MIN_RAW_SEQUENCE_LENGTH else 0.5
        return _classification(
            "PROTEIN", confidence, normalized, length, invalid, warnings,
            "Sequence is compatible with the extended protein alphabet.",
        )
    return _classification(
        "UNKNOWN", 0.2, normalized, length, invalid,
        warnings + ["Sequence contains characters outside known biological alphabets."],
        "Contains symbols outside DNA, RNA, and protein alphabets.",
    )


def _classification(
    sequence_type: SequenceType,
    confidence: float,
    normalized: str,
    length: int,
    invalid: list[str],
    warnings: list[str],
    reason: str,
) -> dict[str, Any]:
    return {
        "sequence_type": sequence_type,
        "confidence": confidence,
        "normalized_sequence": normalized,
        "length": length,
        "invalid_chars": list(invalid),
        "warnings": list(warnings),
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# FASTA parsing
# ---------------------------------------------------------------------------


def _split_inline_fasta_header(line: str) -> tuple[str, str]:
    """Split a single ``>...`` line that bundles the header and body.

    UniProt-style FASTA always puts the body on its own line, but copy/paste
    flows often collapse the newline so everything arrives as one long line
    like ``>sp|P02100|HBE_HUMAN ... SV=2 MVHFTAEEKAA...AHKYH``. Without this
    split ``parse_fasta`` would drop the entry (empty body) and the raw
    detection would mine the header text for protein letters — turning
    ``HUMAN Hemoglobin subunit epsilon OS`` into a fake 31 aa "sequence".

    Returns ``(header, body)``. ``body`` is empty when no clean split is
    possible — the caller treats that as the original header-only line.
    """
    if not line.startswith(">"):
        return line, ""
    # Walk back while the trailing run is uppercase protein letters or
    # whitespace — that's how a wrapped FASTA body looks. We stop at the
    # first character that can't appear inside an aa run (digit, ``=``,
    # ``|``, lowercase prose, etc.), which is necessarily inside the
    # header portion.
    i = len(line)
    while i > 0:
        ch = line[i - 1]
        if ch.isspace() or (ch.isupper() and ch in PROTEIN_EXTENDED):
            i -= 1
            continue
        break
    if i >= len(line):
        return line, ""
    body = line[i:].strip()
    if not body:
        return line, ""
    normalized = re.sub(r"\s", "", body)
    if len(normalized) < MIN_RAW_SEQUENCE_LENGTH:
        return line, ""
    header = line[:i].rstrip()
    return header, body


def parse_fasta(text: str) -> list[dict[str, Any]]:
    """Parse a (possibly multi-entry) FASTA blob.

    Returns a list of ``{"header": str, "raw_sequence": str}`` dicts (one
    per ``>...`` block). Returns an empty list if ``text`` has no headers.
    """
    if not text or ">" not in text:
        return []
    entries: list[dict[str, Any]] = []
    current_header: str | None = None
    body_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith(">"):
            if current_header is not None:
                entries.append(
                    {"header": current_header, "raw_sequence": "\n".join(body_lines).strip()}
                )
            header_part, inline_body = _split_inline_fasta_header(line)
            current_header = header_part.strip()
            body_lines = [inline_body] if inline_body else []
        else:
            if current_header is not None:
                body_lines.append(line)
    if current_header is not None:
        entries.append(
            {"header": current_header, "raw_sequence": "\n".join(body_lines).strip()}
        )
    return [entry for entry in entries if entry["raw_sequence"]]


def header_uniprot_hint(header: str | None) -> str | None:
    """Extract a UniProt accession from a FASTA header if present.

    Recognises ``>sp|ACC|NAME ...`` and ``>tr|ACC|NAME ...`` styles.
    """
    if not header:
        return None
    parts = header.lstrip(">").split("|")
    if len(parts) >= 3 and parts[0].lower() in {"sp", "tr"}:
        accession, _ = detect_uniprot_id(parts[1])
        if accession:
            return accession
    return None


# ---------------------------------------------------------------------------
# Composer-level detection
# ---------------------------------------------------------------------------


# Match runs of bio-like letters at least MIN_RAW_SEQUENCE_LENGTH long.
# Case-insensitive — lowercase is a legitimate FASTA convention for
# soft-masked / low-complexity regions. The only permitted interior noise
# is whitespace; punctuation, digits, parens, lowercase prose words ending
# in non-amino letters etc. all break the run.
_SEQUENCE_RUN_RE = re.compile(
    r"(?P<header>(?:^|\n)>[^\n]*\n)?"  # optional FASTA header line
    r"(?P<body>(?:[ACDEFGHIKLMNPQRSTVWYBJUOZX*\s]{%d,}))" % MIN_RAW_SEQUENCE_LENGTH,
    re.IGNORECASE,
)

# --- Prose-detection thresholds -------------------------------------------
# Five independent signals catch English text that happens to land in the
# amino acid alphabet. Tuned so that natural-distribution proteins
# (averaged over SwissProt) sit far from every threshold and English prose
# trips multiple at once.
_MIN_LONGEST_RUN = 10
_MAX_WHITESPACE_RATIO = 0.15
# Pyrrolysine (O) and selenocysteine (U) appear in <0.01% of natural protein
# residues. They are vowels in English ("you", "of", "or", "to", "out",
# "our", "look") so their density is a near-binary classifier.
_MAX_RARE_AMINO_RATIO = 0.05
# Mean vowel fraction across SwissProt is ~22%, but E-rich / A-rich
# domains (histones, p53 TAD, signal peptides, helical bundles) can hit
# the high 30s on short windows. 45% leaves room for those while still
# catching English which averages ~38-42% and routinely spikes higher.
_MAX_VOWEL_RATIO = 0.45
_MAX_ENGLISH_BIGRAM_SHARE = 0.20
_VOWELS = frozenset("AEIOU")
_RARE_AMINOS = frozenset("OU")

# Bigrams that are frequent in English but exceptionally rare in proteins.
# The O- and U-bearing ones are essentially impossible in real sequences;
# TH/HE/WH/CH/SH appear in English function words far more often than
# random amino composition would predict.
_ENGLISH_TELL_BIGRAMS = frozenset({
    "TH", "HE", "WH", "CH", "SH",
    "OU", "OF", "TO", "ON", "OR", "OT", "OW", "OM", "OL",
    "NO", "DO", "GO", "SO", "YO",
    "UR", "UN", "US", "UP", "UT", "UM",
})


def _english_bigram_share(body: str) -> float:
    total = len(body) - 1
    if total <= 0:
        return 0.0
    hits = sum(1 for i in range(total) if body[i:i + 2] in _ENGLISH_TELL_BIGRAMS)
    return hits / total


def _looks_like_prose(fragment: str) -> bool:
    """Multi-signal English-prose detector for a sequence-shaped substring.

    Any one of five independent checks rejects the fragment. Tuned for
    natural protein statistics — see threshold constants above for the
    biology behind each cut.
    """
    if not fragment:
        return True
    tokens = [t for t in re.split(r"\s+", fragment) if t]
    if not tokens:
        return True

    # 1. Short-token density. Prose has multiple 1-3 char tokens
    #    (`I`, `am`, `is`, `of`, `to`, ...); real sequence pastes are one
    #    long token or uniform 10/60/80-char blocks.
    if sum(1 for t in tokens if len(t) <= 3) >= 2:
        return True

    # 2. Longest contiguous letter run.
    if max(len(t) for t in tokens) < _MIN_LONGEST_RUN:
        return True

    # 3. Whitespace ratio.
    whitespace = sum(1 for ch in fragment if ch.isspace())
    if whitespace / len(fragment) > _MAX_WHITESPACE_RATIO:
        return True

    body = re.sub(r"\s+", "", fragment).upper()
    body = "".join(ch for ch in body if ch.isalpha())
    if not body:
        return True

    # 4. Pyrrolysine / selenocysteine density. Near-zero in real proteins.
    if sum(1 for ch in body if ch in _RARE_AMINOS) / len(body) > _MAX_RARE_AMINO_RATIO:
        return True

    # 5. Vowel-letter density. Proteins ~22%, English ~38%+.
    if sum(1 for ch in body if ch in _VOWELS) / len(body) > _MAX_VOWEL_RATIO:
        return True

    # 6. English-typical bigram density.
    if _english_bigram_share(body) > _MAX_ENGLISH_BIGRAM_SHARE:
        return True

    return False


def _extract_sequence_core(fragment: str) -> str:
    """Narrow a regex match to just the sequence token when prose surrounds it.

    `Tell me about MALWMR...EEK please find similar` matches the regex as
    one big span because every character is in the amino alphabet. The
    real sequence is the single long token; everything else is prose
    giveaway. We only narrow when both shapes are visible (≥1 token at
    least MIN_RAW_SEQUENCE_LENGTH long AND ≥1 token of ≤3 chars) so that
    NCBI 10-char wraps and multi-line FASTA bodies (no short tokens) keep
    their full bodies and reach the merge step downstream.
    """
    if not fragment:
        return fragment
    tokens = [t for t in re.split(r"\s+", fragment) if t]
    if not tokens:
        return fragment
    long_tokens = [t for t in tokens if len(t) >= MIN_RAW_SEQUENCE_LENGTH]
    short_tokens = [t for t in tokens if len(t) <= 3]
    if long_tokens and short_tokens:
        return max(long_tokens, key=len)
    return fragment


def detect_from_text(text: str) -> DetectionResult:
    """Analyze a user message and return cleaned text + detected items.

    The intent of this function is twofold:

    1. Provide a list of ``DetectedSequence`` / ``DetectedUniProt`` items
       so the caller (chat composer / pipeline) can upsert them into the
       session registry.
    2. Produce ``display_text`` where long raw sequences have been
       replaced with ``@Seq_?`` placeholders — the caller substitutes the
       final ``Seq_A`` / ``Seq_B`` labels after upserting.
    """
    if not text:
        return DetectionResult(display_text="")

    sequences: list[DetectedSequence] = []
    warnings: list[str] = []
    spans_to_redact: list[tuple[int, int, str]] = []

    # --- FASTA blocks (highest priority) ----------------------------------
    fasta_entries = parse_fasta(text)
    if fasta_entries:
        # Find the span of the FASTA block in the original text.
        first_header_index = text.find(">")
        if first_header_index != -1:
            end_index = len(text)
            spans_to_redact.append((first_header_index, end_index, "FASTA_BLOCK"))
        for entry in fasta_entries:
            normalized, invalid, norm_warnings = normalize_sequence(entry["raw_sequence"])
            classification = classify_sequence(normalized, entry["header"])
            detected = DetectedSequence(
                label_hint=None,
                raw_text_span=None,
                fasta_header=entry["header"],
                raw_sequence=entry["raw_sequence"],
                normalized_sequence=classification["normalized_sequence"],
                sequence_type=classification["sequence_type"],
                confidence=classification["confidence"],
                reason=classification["reason"],
                warnings=list(norm_warnings) + list(classification["warnings"]),
                invalid_chars=classification["invalid_chars"],
                length=classification["length"],
                uniprot_hint=header_uniprot_hint(entry["header"]),
            )
            sequences.append(detected)
    else:
        # --- Raw sequence runs without a FASTA header ----------------------
        # Split on blank lines first so a sequence run can't bleed into the
        # user's question above or below it. Within a blank-line-bounded
        # segment we still allow single ``\n`` so multi-line pastes work.
        amino_letters = set("ACDEFGHIKLMNPQRSTVWYBJUOZXacdefghiklmnpqrstvwybjuozx*")
        segments: list[tuple[int, int]] = []
        cursor = 0
        for blank in re.finditer(r"\n[ \t]*\n", text):
            segments.append((cursor, blank.start()))
            cursor = blank.end()
        segments.append((cursor, len(text)))

        raw_detections: list[DetectedSequence] = []
        for seg_start, seg_end in segments:
            segment_text = text[seg_start:seg_end]
            if not segment_text.strip():
                continue
            for match in _SEQUENCE_RUN_RE.finditer(segment_text):
                body = match.group("body")
                if not body:
                    continue
                local_start = match.start("body")
                local_end = match.end("body")
                while local_start < local_end and segment_text[local_start] not in amino_letters:
                    local_start += 1
                while local_end > local_start and segment_text[local_end - 1] not in amino_letters:
                    local_end -= 1
                if local_end - local_start < MIN_RAW_SEQUENCE_LENGTH // 2:
                    continue
                tight_body = segment_text[local_start:local_end]
                # If a single token in the span is long enough on its own,
                # narrow to just that token so the prose detector judges
                # the sequence alone and the redaction span doesn't swallow
                # the user's surrounding question.
                core = _extract_sequence_core(tight_body)
                if core is not tight_body and core != tight_body:
                    token_local_start = tight_body.find(core)
                    if token_local_start >= 0:
                        local_start = local_start + token_local_start
                        local_end = local_start + len(core)
                        tight_body = core
                if _looks_like_prose(tight_body):
                    continue
                normalized, invalid, norm_warnings = normalize_sequence(tight_body)
                if len(normalized) < MIN_RAW_SEQUENCE_LENGTH:
                    continue
                classification = classify_sequence(normalized, None)
                if classification["sequence_type"] == "UNKNOWN" and classification["confidence"] < 0.4:
                    continue
                global_start = seg_start + local_start
                global_end = seg_start + local_end
                raw_detections.append(
                    DetectedSequence(
                        label_hint=None,
                        raw_text_span=(global_start, global_end),
                        fasta_header=None,
                        raw_sequence=tight_body,
                        normalized_sequence=classification["normalized_sequence"],
                        sequence_type=classification["sequence_type"],
                        confidence=classification["confidence"],
                        reason=classification["reason"],
                        warnings=list(norm_warnings) + list(classification["warnings"]),
                        invalid_chars=classification["invalid_chars"],
                        length=classification["length"],
                    )
                )

        # Merge adjacent detections whose separator in the original text is
        # whitespace only — FASTA-style wraps and blank lines inside one
        # sequence should not be read as two distinct proteins. A real
        # boundary requires a non-whitespace character (punctuation, lowercase
        # prose, Cyrillic, etc.) between the runs.
        for det in raw_detections:
            if (
                sequences
                and det.raw_text_span
                and sequences[-1].raw_text_span
                and sequences[-1].fasta_header is None
                and text[sequences[-1].raw_text_span[1] : det.raw_text_span[0]].strip() == ""
            ):
                prev = sequences[-1]
                new_start = prev.raw_text_span[0]
                new_end = det.raw_text_span[1]
                combined_raw = text[new_start:new_end]
                normalized, _invalid, norm_warnings = normalize_sequence(combined_raw)
                classification = classify_sequence(normalized, None)
                sequences[-1] = DetectedSequence(
                    label_hint=None,
                    raw_text_span=(new_start, new_end),
                    fasta_header=None,
                    raw_sequence=combined_raw,
                    normalized_sequence=classification["normalized_sequence"],
                    sequence_type=classification["sequence_type"],
                    confidence=classification["confidence"],
                    reason=classification["reason"],
                    warnings=list(norm_warnings) + list(classification["warnings"]),
                    invalid_chars=classification["invalid_chars"],
                    length=classification["length"],
                )
                spans_to_redact[-1] = (new_start, new_end, "RAW_SEQUENCE")
            else:
                sequences.append(det)
                if det.raw_text_span:
                    spans_to_redact.append(
                        (det.raw_text_span[0], det.raw_text_span[1], "RAW_SEQUENCE")
                    )

    # --- UniProt accessions / mnemonics (only outside redacted spans) -----
    uniprot_refs: list[DetectedUniProt] = []
    occupied = _coalesce_spans([(s, e) for s, e, _ in spans_to_redact])
    for ref in find_uniprot_refs(text):
        if not ref.raw_text_span:
            uniprot_refs.append(ref)
            continue
        if _span_overlaps_any(ref.raw_text_span, occupied):
            continue
        uniprot_refs.append(ref)

    display_text = _redact_text(text, spans_to_redact)
    return DetectionResult(
        display_text=display_text,
        sequences=sequences,
        uniprot_refs=uniprot_refs,
        warnings=warnings,
    )


def _redact_text(text: str, spans: list[tuple[int, int, str]]) -> str:
    """Replace each detected span with a numbered ``@SEQ_PLACEHOLDER_N``.

    Callers substitute these placeholders with real ``Seq_A`` / ``Seq_B``
    labels once the registry has assigned them.
    """
    if not spans:
        return text
    ordered = sorted(spans, key=lambda item: item[0])
    out: list[str] = []
    cursor = 0
    counter = 0
    for start, end, _ in ordered:
        if start > cursor:
            out.append(text[cursor:start])
        out.append(f"@SEQ_PLACEHOLDER_{counter}")
        counter += 1
        cursor = end
    if cursor < len(text):
        out.append(text[cursor:])
    # Preserve interior whitespace: ``strip()`` here used to swallow the
    # blank line the user typed between a question and a sequence.
    return "".join(out)


def _coalesce_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []
    ordered = sorted(spans)
    merged: list[tuple[int, int]] = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _span_overlaps_any(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    s, e = span
    for ps, pe in spans:
        if not (e <= ps or s >= pe):
            return True
    return False


# ---------------------------------------------------------------------------
# File parsing
# ---------------------------------------------------------------------------


def parse_uploaded_file(name: str, raw_bytes: bytes) -> dict[str, Any]:
    """Parse an uploaded file as multi-entry FASTA or a single raw sequence.

    Returns::

        {
            "file_name": <str>,
            "sequences": [DetectedSequence as dict, ...],
            "warnings": [<str>, ...]
        }
    """
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1", errors="ignore")

    entries = parse_fasta(text)
    sequences: list[DetectedSequence] = []
    warnings: list[str] = []

    if entries:
        for entry in entries:
            normalized, invalid, norm_warnings = normalize_sequence(entry["raw_sequence"])
            classification = classify_sequence(normalized, entry["header"])
            sequences.append(
                DetectedSequence(
                    label_hint=None,
                    raw_text_span=None,
                    fasta_header=entry["header"],
                    raw_sequence=entry["raw_sequence"],
                    normalized_sequence=classification["normalized_sequence"],
                    sequence_type=classification["sequence_type"],
                    confidence=classification["confidence"],
                    reason=classification["reason"],
                    warnings=list(norm_warnings) + list(classification["warnings"]),
                    invalid_chars=classification["invalid_chars"],
                    length=classification["length"],
                    uniprot_hint=header_uniprot_hint(entry["header"]),
                )
            )
    else:
        normalized, invalid, norm_warnings = normalize_sequence(text)
        if len(normalized) >= MIN_RAW_SEQUENCE_LENGTH:
            classification = classify_sequence(normalized, None)
            sequences.append(
                DetectedSequence(
                    label_hint=None,
                    raw_text_span=None,
                    fasta_header=None,
                    raw_sequence=text,
                    normalized_sequence=classification["normalized_sequence"],
                    sequence_type=classification["sequence_type"],
                    confidence=classification["confidence"],
                    reason=classification["reason"],
                    warnings=list(norm_warnings) + list(classification["warnings"]),
                    invalid_chars=classification["invalid_chars"],
                    length=classification["length"],
                )
            )
        else:
            warnings.append(
                f"File '{name}' contains no FASTA entries and no sequence "
                f"of length >= {MIN_RAW_SEQUENCE_LENGTH}."
            )

    return {
        "file_name": name,
        "sequences": [s.to_dict() for s in sequences],
        "raw_objects": sequences,
        "warnings": warnings,
    }
