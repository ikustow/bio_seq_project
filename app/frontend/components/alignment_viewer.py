"""Embedded protein alignment viewer for the protein card.

This module intentionally contains the alignment-specific logic so the main
protein card only needs to pass two amino-acid sequences and render the result.
"""

from __future__ import annotations

import html
import json
import re
import warnings
from dataclasses import dataclass

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

warnings.filterwarnings("ignore", message="Bio.pairwise2 has been deprecated*")

try:
    from Bio import BiopythonDeprecationWarning, pairwise2
    from Bio.Align import substitution_matrices
except Exception:  # pragma: no cover - optional dependency
    pairwise2 = None
    substitution_matrices = None
    BiopythonDeprecationWarning = None

if BiopythonDeprecationWarning is not None:
    warnings.filterwarnings("ignore", category=BiopythonDeprecationWarning)


VALID_AA = set("ACDEFGHIKLMNPQRSTVWYBXZ")
BLOSUM62_TEXT = """
   A  R  N  D  C  Q  E  G  H  I  L  K  M  F  P  S  T  W  Y  V  B  Z  X
A  4 -1 -2 -2  0 -1 -1  0 -2 -1 -1 -1 -1 -2 -1  1  0 -3 -2  0 -2 -1  0
R -1  5  0 -2 -3  1  0 -2  0 -3 -2  2 -1 -3 -2 -1 -1 -3 -2 -3 -1  0 -1
N -2  0  6  1 -3  0  0  0  1 -3 -3  0 -2 -3 -2  1  0 -4 -2 -3  3  0 -1
D -2 -2  1  6 -3  0  2 -1 -1 -3 -4 -1 -3 -3 -1  0 -1 -4 -3 -3  4  1 -1
C  0 -3 -3 -3  9 -3 -4 -3 -3 -1 -1 -3 -1 -2 -3 -1 -1 -2 -2 -1 -3 -3 -2
Q -1  1  0  0 -3  5  2 -2  0 -3 -2  1  0 -3 -1  0 -1 -2 -1 -2  0  3 -1
E -1  0  0  2 -4  2  5 -2  0 -3 -3  1 -2 -3 -1  0 -1 -3 -2 -2  1  4 -1
G  0 -2  0 -1 -3 -2 -2  6 -2 -4 -4 -2 -3 -3 -2  0 -2 -2 -3 -3 -1 -2 -1
H -2  0  1 -1 -3  0  0 -2  8 -3 -3 -1 -2 -1 -2 -1 -2 -2  2 -3  0  0 -1
I -1 -3 -3 -3 -1 -3 -3 -4 -3  4  2 -3  1  0 -3 -2 -1 -3 -1  3 -3 -3 -1
L -1 -2 -3 -4 -1 -2 -3 -4 -3  2  4 -2  2  0 -3 -2 -1 -2 -1  1 -4 -3 -1
K -1  2  0 -1 -3  1  1 -2 -1 -3 -2  5 -1 -3 -1  0 -1 -3 -2 -2  0  1 -1
M -1 -1 -2 -3 -1  0 -2 -3 -2  1  2 -1  5  0 -2 -1 -1 -1 -1  1 -3 -1 -1
F -2 -3 -3 -3 -2 -3 -3 -3 -1  0  0 -3  0  6 -4 -2 -2  1  3 -1 -3 -3 -1
P -1 -2 -2 -1 -3 -1 -1 -2 -2 -3 -3 -1 -2 -4  7 -1 -1 -4 -3 -2 -2 -1 -2
S  1 -1  1  0 -1  0  0  0 -1 -2 -2  0 -1 -2 -1  4  1 -3 -2 -2  0  0  0
T  0 -1  0 -1 -1 -1 -1 -2 -2 -1 -1 -1 -1 -2 -1  1  5 -2 -2  0 -1 -1  0
W -3 -3 -4 -4 -2 -2 -3 -2 -2 -3 -2 -3 -1  1 -4 -3 -2 11  2 -3 -4 -3 -2
Y -2 -2 -2 -3 -2 -1 -2 -3  2 -1 -1 -2 -1  3 -3 -2 -2  2  7 -1 -3 -2 -1
V  0 -3 -3 -3 -1 -2 -2 -3 -3  3  1 -2  1 -1 -2 -2  0 -3 -1  4 -3 -2 -1
B -2 -1  3  4 -3  0  1 -1  0 -3 -4  0 -3 -3 -2  0 -1 -4 -3 -3  4  1 -1
Z -1  0  0  1 -3  3  4 -2  0 -3 -3  1 -1 -3 -1  0 -1 -3 -2 -2  1  4 -1
X  0 -1 -1 -1 -2 -1 -1 -1 -1 -1 -1 -1 -1 -1 -2  0  0 -2 -1 -1 -1 -1 -1
"""

AA_NAMES = {
    "A": "Alanine",
    "R": "Arginine",
    "N": "Asparagine",
    "D": "Aspartic acid",
    "C": "Cysteine",
    "Q": "Glutamine",
    "E": "Glutamic acid",
    "G": "Glycine",
    "H": "Histidine",
    "I": "Isoleucine",
    "L": "Leucine",
    "K": "Lysine",
    "M": "Methionine",
    "F": "Phenylalanine",
    "P": "Proline",
    "S": "Serine",
    "T": "Threonine",
    "W": "Tryptophan",
    "Y": "Tyrosine",
    "V": "Valine",
    "B": "Asparagine or aspartic acid",
    "Z": "Glutamine or glutamic acid",
    "X": "Unknown amino acid",
    "-": "Gap",
}


@dataclass(frozen=True)
class AlignmentResult:
    algorithm: str
    seq1_name: str
    seq2_name: str
    raw_seq1: str
    raw_seq2: str
    aligned_seq1: str
    aligned_seq2: str
    score: float
    start: int
    end: int
    gap_open: float
    gap_extend: float


@dataclass(frozen=True)
class AlignmentMetrics:
    aligned_length: int
    paired_residues: int
    exact_matches: int
    positive_matches: int
    weak_matches: int
    mismatches: int
    gap_columns: int
    seq1_covered: int
    seq2_covered: int
    identity_alignment: float
    identity_paired: float
    similarity_alignment: float
    gap_percent: float
    seq1_coverage: float
    seq2_coverage: float


def sequence_match_percent(metrics: AlignmentMetrics) -> float:
    """Return a single full-sequence match percent for candidate ranking.

    Identity is the clearest "is this the same protein?" signal, while coverage
    prevents a short conserved fragment from looking like a full-protein match.
    """
    coverage_factor = min(metrics.seq1_coverage, metrics.seq2_coverage) / 100
    return metrics.identity_alignment * coverage_factor


@st.cache_data(show_spinner=False)
def alignment_match_percent(query_sequence: str, candidate_sequence: str) -> float | None:
    """Calculate the compact alignment score shown in the top-5 selector."""
    query, _query_warnings = normalize_protein_sequence(query_sequence)
    candidate, _candidate_warnings = normalize_protein_sequence(candidate_sequence)
    if not query or not candidate:
        return None
    try:
        algorithm = choose_alignment_algorithm(query, candidate)
        result = run_alignment(
            "Query sequence",
            "Candidate sequence",
            query,
            candidate,
            algorithm,
        )
        return sequence_match_percent(analyze_alignment(result))
    except Exception:
        return None


def normalize_protein_sequence(sequence: str) -> tuple[str, list[str]]:
    warnings_out: list[str] = []
    cleaned = re.sub(r"[^A-Za-z*.-]", "", sequence).upper()
    cleaned = cleaned.replace("-", "").replace(".", "").replace("*", "")
    replacements = {"U": "X", "O": "X", "J": "X"}
    normalized: list[str] = []
    replaced: list[str] = []
    dropped: list[str] = []

    for char in cleaned:
        if char in replacements:
            normalized.append(replacements[char])
            replaced.append(char)
        elif char in VALID_AA:
            normalized.append(char)
        else:
            dropped.append(char)

    if replaced:
        warnings_out.append("Residues U/O/J were converted to X for BLOSUM62 alignment.")
    if dropped:
        warnings_out.append("Unexpected symbols were removed: " + ", ".join(sorted(set(dropped))))
    return "".join(normalized), warnings_out


@st.cache_resource(show_spinner=False)
def load_blosum62():
    if substitution_matrices is None:
        return parse_blosum62_text()
    return substitution_matrices.load("BLOSUM62")


@st.cache_resource(show_spinner=False)
def parse_blosum62_text() -> dict[tuple[str, str], int]:
    lines = [line.split() for line in BLOSUM62_TEXT.strip().splitlines()]
    header = lines[0]
    matrix: dict[tuple[str, str], int] = {}
    for row in lines[1:]:
        aa = row[0]
        for other, value in zip(header, row[1:]):
            matrix[(aa, other)] = int(value)
    return matrix


def matrix_score(matrix, aa1: str, aa2: str) -> float:
    if aa1 == "-" or aa2 == "-":
        return 0
    if isinstance(matrix, dict):
        return float(matrix.get((aa1, aa2), matrix.get((aa2, aa1), 0)))
    try:
        return float(matrix[aa1, aa2])
    except Exception:
        return 0


def choose_alignment_algorithm(seq1: str, seq2: str) -> str:
    """Choose a sensible algorithm without exposing alignment settings in the UI."""
    # Global alignment is clearer when both proteins are similar in length: it
    # answers whether the full candidate sequence matches the full query.
    # Local alignment is better when one sequence looks like a fragment/domain:
    # it finds the strongest shared region instead of penalizing the missing
    # ends heavily. The threshold is intentionally conservative for teaching.
    shorter = min(len(seq1), len(seq2))
    longer = max(len(seq1), len(seq2))
    if shorter == 0:
        return "Needleman-Wunsch"
    if shorter / longer < 0.75:
        return "Smith-Waterman"
    return "Needleman-Wunsch"


def fallback_pairwise_alignment(
    seq1: str,
    seq2: str,
    algorithm: str,
    gap_open: float,
    gap_extend: float,
) -> tuple[str, str, float, int, int]:
    matrix = load_blosum62()
    n = len(seq1)
    m = len(seq2)
    neg_inf = -10**12
    states = ("M", "X", "Y")
    score = {state: [[neg_inf] * (m + 1) for _ in range(n + 1)] for state in states}
    pointer: dict[str, list[list[tuple[str, int, int] | None]]] = {
        state: [[None] * (m + 1) for _ in range(n + 1)] for state in states
    }

    score["M"][0][0] = 0
    if algorithm == "Needleman-Wunsch":
        for i in range(1, n + 1):
            score["X"][i][0] = gap_open + (i - 1) * gap_extend
            pointer["X"][i][0] = ("X", i - 1, 0) if i > 1 else ("M", 0, 0)
        for j in range(1, m + 1):
            score["Y"][0][j] = gap_open + (j - 1) * gap_extend
            pointer["Y"][0][j] = ("Y", 0, j - 1) if j > 1 else ("M", 0, j - 1)
    else:
        for state in states:
            for i in range(n + 1):
                score[state][i][0] = 0
            for j in range(m + 1):
                score[state][0][j] = 0

    best_state = "M"
    best_i = n
    best_j = m
    best_score = neg_inf
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            subst = matrix_score(matrix, seq1[i - 1], seq2[j - 1])
            best_m, state_m = max(
                (
                    (score["M"][i - 1][j - 1] + subst, "M"),
                    (score["X"][i - 1][j - 1] + subst, "X"),
                    (score["Y"][i - 1][j - 1] + subst, "Y"),
                ),
                key=lambda item: item[0],
            )
            score["M"][i][j] = best_m
            pointer["M"][i][j] = (state_m, i - 1, j - 1)

            best_x, state_x = max(
                (
                    (score["M"][i - 1][j] + gap_open, "M"),
                    (score["X"][i - 1][j] + gap_extend, "X"),
                    (score["Y"][i - 1][j] + gap_open, "Y"),
                ),
                key=lambda item: item[0],
            )
            score["X"][i][j] = best_x
            pointer["X"][i][j] = (state_x, i - 1, j)

            best_y, state_y = max(
                (
                    (score["M"][i][j - 1] + gap_open, "M"),
                    (score["X"][i][j - 1] + gap_open, "X"),
                    (score["Y"][i][j - 1] + gap_extend, "Y"),
                ),
                key=lambda item: item[0],
            )
            score["Y"][i][j] = best_y
            pointer["Y"][i][j] = (state_y, i, j - 1)

            if algorithm == "Smith-Waterman":
                for state in states:
                    if score[state][i][j] < 0:
                        score[state][i][j] = 0
                        pointer[state][i][j] = None
                    if score[state][i][j] > best_score:
                        best_score = score[state][i][j]
                        best_state = state
                        best_i = i
                        best_j = j

    if algorithm == "Needleman-Wunsch":
        best_score, best_state = max(((score[state][n][m], state) for state in states), key=lambda item: item[0])
        best_i = n
        best_j = m

    aligned_1: list[str] = []
    aligned_2: list[str] = []
    state = best_state
    i = best_i
    j = best_j
    end_column = 0
    while i > 0 or j > 0:
        if algorithm == "Smith-Waterman" and score[state][i][j] <= 0:
            break
        previous = pointer[state][i][j]
        if previous is None:
            break
        prev_state, prev_i, prev_j = previous
        if prev_i == i - 1 and prev_j == j - 1:
            aligned_1.append(seq1[i - 1])
            aligned_2.append(seq2[j - 1])
        elif prev_i == i - 1 and prev_j == j:
            aligned_1.append(seq1[i - 1])
            aligned_2.append("-")
        else:
            aligned_1.append("-")
            aligned_2.append(seq2[j - 1])
        state = prev_state
        i = prev_i
        j = prev_j
        end_column += 1

    aligned_1.reverse()
    aligned_2.reverse()
    return "".join(aligned_1), "".join(aligned_2), float(best_score), 0, end_column


def run_alignment(
    seq1_name: str,
    seq2_name: str,
    seq1: str,
    seq2: str,
    algorithm: str,
    gap_open: float = -10.0,
    gap_extend: float = -0.5,
) -> AlignmentResult:
    matrix = load_blosum62()
    if pairwise2 is None:
        aligned_seq1, aligned_seq2, score, start, end = fallback_pairwise_alignment(
            seq1,
            seq2,
            algorithm,
            gap_open,
            gap_extend,
        )
    else:
        aligner = pairwise2.align.localds if algorithm == "Smith-Waterman" else pairwise2.align.globalds
        alignments = aligner(seq1, seq2, matrix, gap_open, gap_extend, one_alignment_only=True)
        if not alignments:
            raise RuntimeError("No alignment was produced for these sequences.")
        best = alignments[0]
        aligned_seq1 = best.seqA
        aligned_seq2 = best.seqB
        score = float(best.score)
        start = int(best.start)
        end = int(best.end)
        if algorithm == "Smith-Waterman":
            aligned_seq1 = aligned_seq1[start:end]
            aligned_seq2 = aligned_seq2[start:end]

    return AlignmentResult(
        algorithm=algorithm,
        seq1_name=seq1_name,
        seq2_name=seq2_name,
        raw_seq1=seq1,
        raw_seq2=seq2,
        aligned_seq1=aligned_seq1,
        aligned_seq2=aligned_seq2,
        score=score,
        start=start,
        end=end,
        gap_open=gap_open,
        gap_extend=gap_extend,
    )


def analyze_alignment(result: AlignmentResult) -> AlignmentMetrics:
    matrix = load_blosum62()
    aligned_length = len(result.aligned_seq1)
    paired = exact = positive = weak = mismatches = gaps = 0
    seq1_covered = seq2_covered = 0
    for aa1, aa2 in zip(result.aligned_seq1, result.aligned_seq2):
        if aa1 != "-":
            seq1_covered += 1
        if aa2 != "-":
            seq2_covered += 1
        if aa1 == "-" or aa2 == "-":
            gaps += 1
            continue
        paired += 1
        score = matrix_score(matrix, aa1, aa2)
        if aa1 == aa2:
            exact += 1
            positive += 1
        elif score > 0:
            positive += 1
        elif score == 0:
            weak += 1
        else:
            mismatches += 1

    safe_aligned = max(aligned_length, 1)
    safe_paired = max(paired, 1)
    return AlignmentMetrics(
        aligned_length=aligned_length,
        paired_residues=paired,
        exact_matches=exact,
        positive_matches=positive,
        weak_matches=weak,
        mismatches=mismatches,
        gap_columns=gaps,
        seq1_covered=seq1_covered,
        seq2_covered=seq2_covered,
        identity_alignment=exact / safe_aligned * 100,
        identity_paired=exact / safe_paired * 100,
        similarity_alignment=positive / safe_aligned * 100,
        gap_percent=gaps / safe_aligned * 100,
        seq1_coverage=seq1_covered / max(len(result.raw_seq1), 1) * 100,
        seq2_coverage=seq2_covered / max(len(result.raw_seq2), 1) * 100,
    )


def connector_symbol(matrix, aa1: str, aa2: str) -> str:
    if aa1 == "-" or aa2 == "-":
        return ""
    if aa1 == aa2:
        return "|"
    score = matrix_score(matrix, aa1, aa2)
    if score > 0:
        return ":"
    if score == 0:
        return "."
    return ""


def connector_explanation(matrix, aa1: str, aa2: str) -> tuple[str, str]:
    if aa1 == "-" or aa2 == "-":
        return "Gap", "One sequence has an insertion/deletion here."
    if aa1 == aa2:
        return "Exact match", "The same amino acid appears in both sequences."
    score = matrix_score(matrix, aa1, aa2)
    if score > 0:
        return "Conservative substitution", "BLOSUM62 scores this replacement as favorable."
    if score == 0:
        return "Weak similarity", "BLOSUM62 scores this pair as neutral or weakly similar."
    return "Mismatch", "BLOSUM62 scores this substitution as unfavorable."


def residue_class(aa: str, other: str, matrix) -> str:
    if aa == "-":
        return "gap"
    if other == "-":
        return "gap-neighbor"
    if aa == other:
        return "exact"
    score = matrix_score(matrix, aa, other)
    if score > 0:
        return "positive-match"
    if score == 0:
        return "weak-match"
    return "mismatch"


def build_interactive_alignment_html(result: AlignmentResult) -> str:
    matrix = load_blosum62()
    seq1_position = 0
    seq2_position = 0
    columns: list[dict[str, str | int]] = []
    for idx, (aa1, aa2) in enumerate(zip(result.aligned_seq1, result.aligned_seq2), start=1):
        if aa1 != "-":
            seq1_position += 1
        if aa2 != "-":
            seq2_position += 1
        symbol = connector_symbol(matrix, aa1, aa2)
        relation_title, relation_detail = connector_explanation(matrix, aa1, aa2)
        columns.append({
            "column": idx,
            "aa1": aa1,
            "aa2": aa2,
            "aa1Name": AA_NAMES.get(aa1, "Unknown residue"),
            "aa2Name": AA_NAMES.get(aa2, "Unknown residue"),
            "relationTitle": relation_title,
            "relationDetail": relation_detail,
            "seq1Pos": seq1_position if aa1 != "-" else "",
            "seq2Pos": seq2_position if aa2 != "-" else "",
            "symbol": symbol,
            "overviewClass": residue_class(aa1, aa2, matrix),
            "class1": residue_class(aa1, aa2, matrix),
            "class2": residue_class(aa2, aa1, matrix),
        })

    payload = json.dumps({"seq1Name": result.seq1_name, "seq2Name": result.seq2_name, "columns": columns})
    return f"""
    <style>
      body {{
        margin: 0;
        font-family: Inter, Segoe UI, Arial, sans-serif;
        background: transparent;
        color: #172033;
      }}
      .alignment-browser {{
        border: 1px solid #d9dee8;
        border-radius: 8px;
        padding: 14px;
        background: #ffffff;
        box-sizing: border-box;
        user-select: none;
      }}
      .browser-head {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 10px;
        color: #687289;
        font-size: 12px;
        font-weight: 700;
      }}
      .overview {{
        position: relative;
        height: 68px;
        padding: 0 0 14px;
      }}
      .scale-line {{
        position: absolute;
        left: 0;
        right: 0;
        top: 10px;
        height: 1px;
        background: #adb5bd;
      }}
      .tick {{
        position: absolute;
        top: 10px;
        width: 1px;
        height: 10px;
        background: #adb5bd;
      }}
      .tick span {{
        position: absolute;
        top: 12px;
        left: 50%;
        transform: translateX(-50%);
        font-size: 10px;
        color: #495057;
        white-space: nowrap;
      }}
      .track {{
        position: absolute;
        left: 0;
        right: 0;
        bottom: 0;
        height: 28px;
        display: grid;
        grid-template-columns: repeat(var(--cols), minmax(1px, 1fr));
        overflow: hidden;
        background: #e9ecef;
        border: 1px solid #ced4da;
        border-radius: 3px;
      }}
      .overview-cell {{ min-width: 1px; height: 100%; }}
      .viewport {{
        position: absolute;
        top: 10px;
        bottom: 0;
        background: rgba(107, 142, 191, 0.1);
        border: 1px solid rgba(107, 142, 191, 0.4);
        cursor: grab;
        box-sizing: border-box;
        z-index: 4;
      }}
      .handle {{
        position: absolute;
        top: -2px;
        bottom: -2px;
        width: 8px;
        background: rgba(255, 255, 255, 0.9);
        border: 1px solid rgba(107, 142, 191, 0.4);
        box-sizing: border-box;
        cursor: ew-resize;
      }}
      .handle.left {{ left: -5px; }}
      .handle.right {{ right: -5px; }}
      .range-readout {{
        margin: 10px 0 8px;
        color: #687289;
        font-size: 12px;
        font-weight: 700;
      }}
      .detail {{
        border-top: 1px solid #e9ecef;
        padding-top: 10px;
      }}
      .lane {{
        position: relative;
        overflow: hidden;
        width: 100%;
        box-sizing: border-box;
      }}
      .lane-inner {{
        display: grid;
        grid-template-columns: repeat(var(--cols), var(--tile-width));
        gap: var(--tile-gap);
        width: max-content;
        transform: translateX(var(--offset-x));
        will-change: transform;
      }}
      .detail-scale {{ height: 16px; margin-bottom: 3px; }}
      .detail-pos {{
        text-align: center;
        color: #868e96;
        font-size: 9px;
        font-family: Consolas, Menlo, monospace;
        overflow: hidden;
        white-space: nowrap;
      }}
      .tile {{
        position: relative;
        height: 28px;
        width: var(--tile-width);
        min-width: var(--tile-width);
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 4px;
        border: 1px solid rgba(23, 32, 51, 0.09);
        box-sizing: border-box;
        font-family: Consolas, Menlo, monospace;
        font-size: var(--tile-font-size);
        font-weight: 800;
        overflow: hidden;
        white-space: nowrap;
      }}
      .tile.is-hovered {{
        outline: 2px solid #f8fafc;
        outline-offset: -2px;
        box-shadow: 0 0 0 2px #6b8ebf, 0 0 18px rgba(88, 166, 255, 0.35);
        z-index: 3;
      }}
      .tile.is-paired {{
        outline: 2px solid #4b6c9b;
        outline-offset: -2px;
        box-shadow: 0 0 0 2px rgba(210, 153, 34, 0.55);
        z-index: 2;
      }}
      .connector-row {{ height: 22px; align-items: center; }}
      .connector {{
        min-width: 0;
        height: 22px;
        display: flex;
        align-items: center;
        justify-content: center;
        overflow: hidden;
      }}
      .connector.is-paired {{ background: rgba(250, 204, 21, 0.12); }}
      .connector-mark {{ display: block; flex: 0 0 auto; }}
      .connector-mark.exact-line {{
        width: 2px;
        height: 18px;
        border-radius: 999px;
        background: #adb5bd;
      }}
      .connector-mark.positive-dots {{
        position: relative;
        width: 6px;
        height: 18px;
      }}
      .connector-mark.positive-dots::before,
      .connector-mark.positive-dots::after {{
        content: "";
        position: absolute;
        left: 50%;
        width: 4px;
        height: 4px;
        border-radius: 999px;
        background: #adb5bd;
        transform: translateX(-50%);
      }}
      .connector-mark.positive-dots::before {{ top: 4px; }}
      .connector-mark.positive-dots::after {{ bottom: 4px; }}
      .connector-mark.weak-dot {{
        width: 4px;
        height: 4px;
        border-radius: 999px;
        background: #adb5bd;
      }}
      .compact .tile span {{ transform: scale(0.78); }}
      .hide-residue-letters .tile span {{ opacity: 0; }}
      .micro .tile {{
        height: 22px;
        border-radius: 0;
        border-left: 0;
        border-right: 0;
      }}
      .name-row {{
        display: grid;
        grid-template-columns: 120px 1fr;
        gap: 8px;
        align-items: center;
        margin: 3px 0;
      }}
      .seq-name {{
        color: #687289;
        font-size: 12px;
        font-weight: 800;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }}
      .tooltip {{
        position: fixed;
        z-index: 30;
        display: none;
        max-width: 260px;
        padding: 9px 10px;
        border-radius: 7px;
        background: rgba(17, 24, 39, 0.96);
        color: #ffffff;
        box-shadow: 0 8px 24px rgba(15, 23, 42, 0.22);
        font-size: 12px;
        line-height: 1.35;
        pointer-events: none;
      }}
      .tooltip strong {{ display: block; margin-bottom: 2px; font-size: 13px; }}
      .tooltip span {{ color: #cbd5e1; }}
      .exact {{ background: #40c057; color: #ffffff; }}
      .positive-match {{ background: #82c91e; color: #ffffff; }}
      .weak-match {{ background: #fd7e14; color: #ffffff; }}
      .mismatch {{ background: #fa5252; color: #ffffff; }}
      .gap, .gap-neighbor {{ background: #ced4da; color: #495057; }}
    </style>

    <div class="alignment-browser" id="browser">
      <div class="browser-head">
        <span id="leftLabel"></span>
        <span id="rightLabel"></span>
      </div>
      <div class="overview" id="overview">
        <div class="scale-line"></div>
        <div id="ticks"></div>
        <div class="track" id="track"></div>
        <div class="viewport" id="viewport">
          <span class="handle left" data-mode="left"></span>
          <span class="handle right" data-mode="right"></span>
        </div>
      </div>
      <div class="range-readout" id="readout"></div>
      <div class="detail" id="detail">
        <div class="name-row">
          <div class="seq-name"></div>
          <div class="lane detail-scale"><div class="lane-inner" id="detailScale"></div></div>
        </div>
        <div class="name-row">
          <div class="seq-name" id="seq1Name"></div>
          <div class="lane tile-row"><div class="lane-inner" id="row1"></div></div>
        </div>
        <div class="name-row">
          <div class="seq-name"></div>
          <div class="lane connector-row"><div class="lane-inner" id="connectors"></div></div>
        </div>
        <div class="name-row">
          <div class="seq-name" id="seq2Name"></div>
          <div class="lane tile-row"><div class="lane-inner" id="row2"></div></div>
        </div>
      </div>
      <div class="tooltip" id="tooltip"></div>
    </div>

    <script>
      const payload = {payload};
      const columns = payload.columns;
      const total = columns.length;
      const browser = document.getElementById("browser");
      const overview = document.getElementById("overview");
      const track = document.getElementById("track");
      const viewport = document.getElementById("viewport");
      const ticks = document.getElementById("ticks");
      const readout = document.getElementById("readout");
      const detail = document.getElementById("detail");
      const detailScale = document.getElementById("detailScale");
      const row1 = document.getElementById("row1");
      const row2 = document.getElementById("row2");
      const connectors = document.getElementById("connectors");
      const seq1Name = document.getElementById("seq1Name");
      const seq2Name = document.getElementById("seq2Name");
      const leftLabel = document.getElementById("leftLabel");
      const rightLabel = document.getElementById("rightLabel");
      const tooltip = document.getElementById("tooltip");

      seq1Name.textContent = payload.seq1Name;
      seq2Name.textContent = payload.seq2Name;
      leftLabel.textContent = "1";
      rightLabel.textContent = String(total);
      const initialSize = total <= 160 ? total : Math.min(120, total);
      let start = 0.0;
      let end = initialSize;
      let drag = null;
      const minCols = Math.min(8, total);

      function escapeHtml(value) {{
        return String(value)
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;")
          .replaceAll('"', "&quot;");
      }}
      function chooseTickStep(n) {{
        if (n <= 120) return 10;
        if (n <= 400) return 25;
        if (n <= 1000) return 50;
        if (n <= 3000) return 100;
        return 500;
      }}
      function columnFromClientX(clientX) {{
        const rect = overview.getBoundingClientRect();
        const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
        return ratio * total;
      }}
      function clampRange() {{
        start = Math.max(0, Math.min(start, total - minCols));
        end = Math.max(start + minCols, Math.min(end, total));
      }}
      function updateViewport() {{
        clampRange();
        viewport.style.left = (start / total * 100) + "%";
        viewport.style.width = ((end - start) / total * 100) + "%";
        readout.textContent = `Columns ${{Math.floor(start) + 1}}-${{Math.ceil(end)}} of ${{total}}`;
      }}
      function tileHtml(col, aaKey, classKey, posKey, otherKey) {{
        const aa = col[aaKey];
        const other = col[otherKey];
        const nameKey = aaKey === "aa1" ? "aa1Name" : "aa2Name";
        const otherNameKey = aaKey === "aa1" ? "aa2Name" : "aa1Name";
        return `<div class="tile ${{col[classKey]}}"
          data-index="${{col.column - 1}}"
          data-aa="${{escapeHtml(aa)}}"
          data-name="${{escapeHtml(col[nameKey])}}"
          data-other-name="${{escapeHtml(col[otherNameKey])}}"
          data-symbol="${{escapeHtml(col.symbol || "none")}}"
          data-relation-title="${{escapeHtml(col.relationTitle)}}"
          data-relation-detail="${{escapeHtml(col.relationDetail)}}"
          data-position="${{escapeHtml(col[posKey] || "gap")}}"
          data-column="${{col.column}}"
          data-other="${{escapeHtml(other)}}"><span>${{escapeHtml(aa)}}</span></div>`;
      }}
      function connectorHtml(col) {{
        let mark = "";
        if (col.symbol === "|") mark = '<span class="connector-mark exact-line"></span>';
        else if (col.symbol === ":") mark = '<span class="connector-mark positive-dots"></span>';
        else if (col.symbol === ".") mark = '<span class="connector-mark weak-dot"></span>';
        return `<div class="connector" data-index="${{col.column - 1}}">${{mark}}</div>`;
      }}
      function renderDetail() {{
        const visibleSpan = Math.max(minCols, end - start);
        const laneWidth = Math.max(1, row1.parentElement.getBoundingClientRect().width);
        let roughTileWidth = laneWidth / visibleSpan;
        let gap = roughTileWidth < 8 ? 0 : 2;
        let tileWidth = (laneWidth - gap * Math.max(0, visibleSpan - 1)) / visibleSpan;
        if (tileWidth < 1) {{
          gap = 0;
          tileWidth = laneWidth / visibleSpan;
        }}
        const offset = -(start * (tileWidth + gap));
        detail.style.setProperty("--cols", total);
        detail.style.setProperty("--tile-width", tileWidth + "px");
        detail.style.setProperty("--tile-gap", gap + "px");
        detail.style.setProperty("--offset-x", offset + "px");
        detail.style.setProperty("--tile-font-size", Math.max(7, Math.min(13, tileWidth * 0.82)) + "px");
        detail.classList.toggle("compact", tileWidth < 10);
        detail.classList.toggle("hide-residue-letters", tileWidth < 6);
        detail.classList.toggle("micro", tileWidth < 5);
        detailScale.innerHTML = columns.map((col) => {{
          const step = tileWidth >= 18 ? 5 : tileWidth >= 9 ? 10 : tileWidth >= 4 ? 20 : 50;
          const show = col.column === 1 || col.column === total || col.column % step === 0 || visibleSpan <= 35;
          return `<div class="detail-pos">${{show ? col.column : ""}}</div>`;
        }}).join("");
        row1.innerHTML = columns.map((col) => tileHtml(col, "aa1", "class1", "seq1Pos", "aa2")).join("");
        connectors.innerHTML = columns.map((col) => connectorHtml(col)).join("");
        row2.innerHTML = columns.map((col) => tileHtml(col, "aa2", "class2", "seq2Pos", "aa1")).join("");
      }}
      function renderOverview() {{
        track.style.setProperty("--cols", total);
        track.innerHTML = columns.map((col) => `<span class="overview-cell ${{col.overviewClass}}"></span>`).join("");
        const step = chooseTickStep(total);
        const tickValues = [1];
        for (let value = step; value < total; value += step) tickValues.push(value);
        if (!tickValues.includes(total)) tickValues.push(total);
        ticks.innerHTML = tickValues.map((value) => {{
          const left = total === 1 ? 0 : ((value - 1) / (total - 1) * 100);
          return `<div class="tick" style="left:${{left}}%"><span>${{value}}</span></div>`;
        }}).join("");
      }}
      function rerender() {{
        updateViewport();
        renderDetail();
      }}
      viewport.addEventListener("pointerdown", (event) => {{
        event.preventDefault();
        viewport.setPointerCapture(event.pointerId);
        const mode = event.target.dataset.mode || "move";
        drag = {{ mode, x: event.clientX, start, end }};
      }});
      viewport.addEventListener("pointermove", (event) => {{
        if (!drag) return;
        const deltaCols = columnFromClientX(event.clientX) - columnFromClientX(drag.x);
        if (drag.mode === "left") {{
          start = Math.min(drag.end - minCols, Math.max(0, drag.start + deltaCols));
          end = drag.end;
        }} else if (drag.mode === "right") {{
          start = drag.start;
          end = Math.max(drag.start + minCols, Math.min(total, drag.end + deltaCols));
        }} else {{
          const width = drag.end - drag.start;
          start = Math.max(0, Math.min(total - width, drag.start + deltaCols));
          end = start + width;
        }}
        rerender();
      }});
      viewport.addEventListener("pointerup", () => {{ drag = null; }});
      overview.addEventListener("pointerdown", (event) => {{
        if (event.target === viewport || event.target.classList.contains("handle")) return;
        const width = end - start;
        const center = columnFromClientX(event.clientX);
        start = Math.max(0, Math.min(total - width, center - width / 2));
        end = start + width;
        rerender();
      }});
      window.addEventListener("resize", renderDetail);
      function clearHighlight() {{
        detail.querySelectorAll(".is-hovered, .is-paired").forEach((node) => {{
          node.classList.remove("is-hovered", "is-paired");
        }});
      }}
      function highlightColumn(tile) {{
        clearHighlight();
        const index = Number(tile.dataset.index);
        const first = row1.children[index];
        const second = row2.children[index];
        const link = connectors.children[index];
        tile.classList.add("is-hovered");
        if (first && first !== tile) first.classList.add("is-paired");
        if (second && second !== tile) second.classList.add("is-paired");
        if (link) link.classList.add("is-paired");
      }}
      detail.addEventListener("pointerover", (event) => {{
        const tile = event.target.closest(".tile");
        if (!tile) return;
        highlightColumn(tile);
        tooltip.innerHTML = `<strong>${{tile.dataset.aa}} - ${{tile.dataset.name}}</strong>
          <span>Position ${{tile.dataset.position}} - column ${{tile.dataset.column}}</span><br>
          Compared with: ${{tile.dataset.other}} - ${{tile.dataset.otherName}}<br>
          Symbol: ${{tile.dataset.symbol}} - ${{tile.dataset.relationTitle}}<br>
          <span>${{tile.dataset.relationDetail}}</span>`;
        tooltip.style.display = "block";
      }});
      detail.addEventListener("pointermove", (event) => {{
        if (tooltip.style.display !== "block") return;
        const padding = 12;
        const rect = tooltip.getBoundingClientRect();
        let left = event.clientX - rect.width / 2;
        let top = event.clientY - rect.height - 14;
        left = Math.max(padding, Math.min(window.innerWidth - rect.width - padding, left));
        if (top < padding) top = event.clientY + 16;
        tooltip.style.left = left + "px";
        tooltip.style.top = top + "px";
      }});
      detail.addEventListener("pointerleave", () => {{
        clearHighlight();
        tooltip.style.display = "none";
      }});
      renderOverview();
      rerender();
    </script>
    """


def render_alignment(
    query_sequence: str,
    candidate_sequence: str,
    *,
    query_name: str = "Query",
    candidate_name: str = "Selected candidate",
) -> None:
    query, query_warnings = normalize_protein_sequence(query_sequence)
    candidate, candidate_warnings = normalize_protein_sequence(candidate_sequence)
    if not query or not candidate:
        st.info("Alignment is unavailable because one of the protein sequences is empty.")
        return

    algorithm = choose_alignment_algorithm(query, candidate)
    try:
        result = run_alignment(query_name, candidate_name, query, candidate, algorithm)
    except Exception as exc:
        st.warning(f"Alignment could not be calculated: {exc}")
        return

    metrics = analyze_alignment(result)
    metric_cols = st.columns(6)
    metric_cols[0].metric("Sequence match", f"{sequence_match_percent(metrics):.1f}%")
    metric_cols[1].metric("Identity", f"{metrics.identity_paired:.1f}%")
    metric_cols[2].metric("Similarity", f"{metrics.similarity_alignment:.1f}%")
    metric_cols[3].metric("Gaps", f"{metrics.gap_percent:.1f}%")
    metric_cols[4].metric("Query coverage", f"{metrics.seq1_coverage:.1f}%")
    metric_cols[5].metric("Candidate coverage", f"{metrics.seq2_coverage:.1f}%")

    details = pd.DataFrame(
        [
            ("Algorithm", result.algorithm),
            ("Score", f"{result.score:.2f}"),
            ("Aligned columns", str(metrics.aligned_length)),
            ("Exact matches", str(metrics.exact_matches)),
            ("Conservative matches", str(max(metrics.positive_matches - metrics.exact_matches, 0))),
        ],
        columns=["Field", "Value"],
    )
    st.dataframe(details, hide_index=True, width="stretch")
    for message in [*query_warnings, *candidate_warnings]:
        st.caption(message)

    height = 280 if metrics.aligned_length <= 180 else 330
    components.html(build_interactive_alignment_html(result), height=height, scrolling=False)
