"""Smoke tests for protein card Markdown export.

Run:
    python tests/scripts/test_protein_markdown_export.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "app" / "frontend"))

from components.protein_markdown_export import build_protein_markdown, markdown_filename
from mock.protein_loader import load_candidates


def test_build_protein_markdown_contains_visible_card_sections() -> None:
    candidates = load_candidates(
        PROJECT_ROOT / "app" / "frontend" / "test_data_from_database",
        [("O95185", 98.7), ("Q761X5", 92.4)],
    )
    selected = candidates[0]
    markdown = build_protein_markdown(
        selected=selected,
        candidates=candidates,
        selected_index=0,
        revealed={
            "header",
            "keyfacts",
            "function",
            "domains",
            "pathways",
            "references",
            "structure",
        },
        query_sequence=None,
    )

    assert markdown.startswith("# Netrin receptor UNC5C")
    assert "## Top Matches" in markdown
    assert "| Rank | UniProt | Gene | Protein | Match |" in markdown
    assert "[O95185](https://www.uniprot.org/uniprotkb/O95185)" in markdown
    assert "## Key Facts" in markdown
    assert "## Domain Architecture" in markdown
    assert "## References & External Links" in markdown
    assert "https://pubmed.ncbi.nlm.nih.gov/" in markdown


def test_markdown_filename_is_safe_and_descriptive() -> None:
    candidates = load_candidates(
        PROJECT_ROOT / "app" / "frontend" / "test_data_from_database",
        [("O95185", 98.7)],
    )

    assert markdown_filename(candidates[0]["protein"]) == "O95185_UNC5C_protein_card.md"


def test_build_protein_markdown_uses_selected_candidate() -> None:
    candidates = load_candidates(
        PROJECT_ROOT / "app" / "frontend" / "test_data_from_database",
        [("O95185", 98.7), ("Q761X5", 92.4)],
    )
    markdown = build_protein_markdown(
        selected=candidates[1],
        candidates=candidates,
        selected_index=1,
        revealed={"header", "keyfacts", "references"},
        query_sequence=None,
    )

    assert markdown.startswith("# Netrin receptor UNC5C")
    assert "**UniProt:** [Q761X5](https://www.uniprot.org/uniprotkb/Q761X5)" in markdown
    assert "**Rank:** #2 of 2" in markdown
    assert "**[Q761X5](https://www.uniprot.org/uniprotkb/Q761X5)**" in markdown
    assert markdown_filename(candidates[1]["protein"]) == "Q761X5_Unc5c_protein_card.md"


def main() -> int:
    tests = [
        test_build_protein_markdown_contains_visible_card_sections,
        test_markdown_filename_is_safe_and_descriptive,
        test_build_protein_markdown_uses_selected_candidate,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  [ok] {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  [FAIL] {fn.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  [ERR ] {fn.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
