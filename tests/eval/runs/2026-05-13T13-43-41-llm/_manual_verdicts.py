"""Manual judging of 2026-05-13T13-43-41-llm L2 run.

OpenRouter judge hit daily-quota 429 on every rubric item, so every CSV row
contained `passed=0` and `judge_explanation = [judge failed] HTTPError: 429`.
Gemini answers (in llm_raw/*.txt) are all valid — I (Claude, acting as the
fallback judge) re-judged each of the 56 rubric items against the same
contract used by JUDGE_SYSTEM_PROMPT (must_cover / must_not, no partial
credit, single-sentence explanation in English) and rewrite the CSV here.

Run once:  python tests/eval/runs/2026-05-13T13-43-41-llm/_manual_verdicts.py
"""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

RUN_DIR = Path(__file__).resolve().parent
CSV_PATH = RUN_DIR / "llm_results.csv"
BACKUP_PATH = RUN_DIR / "llm_results.before_manual_judge.csv"
MARKER = "[manual:claude]"

# (passed, one-sentence explanation).
# Keyed by f"{scenario_id}_{rubric_item}".
VERDICTS: dict[str, tuple[int, str]] = {
    # ===== A1 — Insulin function =====
    "A1_a": (1, "Answer states insulin 'decreases blood glucose concentration', the main function from function_text."),
    "A1_b": (1, "Answer references glucose metabolism (glycolysis, glycogen synthesis, glucose-permeability changes), matching the `Glucose metabolism` / `Carbohydrate metabolism` keywords."),
    "A1_c": (1, "All functions named (glucose decrease, glycolysis, pentose phosphate cycle, glycogen synthesis) are taken verbatim from function_text."),

    # ===== A2 — UNC5C location =====
    "A2_a": (1, "Answer explicitly names 'cell membrane' and 'cell surface' from subcellular_locations."),
    "A2_b": (1, "Answer lists axons, dendrites, growth cones, lamellipodia, filopodia — all cell-projection locations from the context."),
    "A2_c": (1, "All locations enumerated are present in subcellular_locations; no novel ones introduced."),

    # ===== A3 — UNC5C domains =====
    "A3_a": (1, "Answer names Ig-like, Ig-like C2-type, TSP type-1, and Transmembrane — multiple distinct domains from the context list."),
    "A3_b": (1, "Domain boundaries are given for every listed domain (e.g., Ig-like 62-159, TSP type-1 260-314)."),
    "A3_c": (1, "All named domains are present in the context's domain list; ZU5/Death are omitted but not invented."),

    # ===== A4 — Insulin length / MW =====
    "A4_a": (1, "Answer reports length as 110 aa, matching the context exactly."),
    "A4_b": (1, "Answer reports molecular weight as 11,981 Da, matching the context exactly."),
    "A4_c": (1, "Only the context numbers (110 aa, 11,981 Da) and the context accession P01308 are mentioned."),

    # ===== A5 — Adenovirus identification =====
    "A5_a": (1, "Answer identifies the organism as 'Human adenovirus D serotype 8 (HAdV-8)' from the context."),
    "A5_b": (1, "Answer names the L5 gene from the context."),
    "A5_c": (1, "Answer clearly frames L5 as belonging to the adenovirus organism, not as a human gene."),

    # ===== A6 — UNC5C diseases =====
    "A6_a": (1, "Answer mentions Alzheimer disease (AD) from the disease field."),
    "A6_b": (1, "Only Alzheimer disease and terms drawn from disease.description (amyloid plaques, neurofibrillary tangles, dementia) appear; nothing invented."),

    # ===== A7 — Insulin Gorilla articles =====
    "A7_a": (1, "Answer states that the database information for Q6YK33 does not include any specific articles or publications, honestly flagging the gap."),
    "A7_b": (1, "No PubMed IDs, author names, or article titles are fabricated."),
    "A7_c": (0, "Answer does not suggest any productive next step (e.g., consulting UniProt or PubMed) — it only states the absence."),

    # ===== A8 — UNC5C interaction partners =====
    "A8_a": (1, "Answer lists DCC, PTPN11, FLRT3, DSCAM, DAPK1, TUBB3 — all from the context interactions list."),
    "A8_b": (1, "No partners outside the context interactions list are introduced."),

    # ===== B9 — No new DB-search claim =====
    "B9_a": (1, "Answer explicitly states it cannot perform a new search and disclaims the capability to conduct new database searches."),
    "B9_b": (1, "Answer says the information provided is specific to the already-selected protein (P01308 Insulin)."),

    # ===== B10 — Berlin weather (off-topic) =====
    "B10_a": (1, "No weather data is invented; the answer refuses to provide any."),
    "B10_b": (1, "Answer says its knowledge base is limited to data about Insulin, conveying that the weather question is out of scope."),
    "B10_c": (0, "Answer does not offer to return to discussing the protein; it stops at refusal."),

    # ===== B11 — Unknown accession =====
    "B11_a": (1, "Answer explicitly says it has no data for UNIPROT-XYZ12345 in the provided context."),
    "B11_b": (1, "No facts about the unknown accession are fabricated."),

    # ===== B12 — GC content (out-of-scope tooling) =====
    "B12_a": (1, "No GC% value is fabricated for the spike protein."),
    "B12_b": (1, "Answer explicitly states GC content is a DNA/RNA-level metric and the current entry is a protein sequence."),
    "B12_c": (1, "Answer redirects the user to calculate GC for the gene (DNA/RNA) that codes for the protein, staying constructive."),

    # ===== B13 — Phylogenetic tree =====
    "B13_a": (1, "Answer does not output a tree or pretend to build one; it explicitly says a single sequence is insufficient."),
    "B13_b": (1, "Answer explains a tree requires comparing homologous sequences from multiple species, signaling it is a separate, larger analysis outside this chat."),
    "B13_c": (1, "Answer suggests including this insulin sequence with insulin sequences from other organisms — context-relevant homolog guidance."),

    # ===== B14 — Conserved regions =====
    "B14_a": (1, "Answer references domain ranges that are present in the context (Ig-like 62-159 / 161-256, TSP type-1 260-314 / 316-368, Transmembrane 381-401); no alignment results invented."),
    "B14_b": (1, "Answer explicitly tells the user to perform Multiple Sequence Alignment with orthologs and use external domain databases (Pfam, SMART, CDD)."),
    "B14_c": (1, "Answer names the Ig-like, TSP type-1, and Transmembrane domains from the context as functionally constrained starting points."),

    # ===== C15 — UNC5C disease + mechanism =====
    "C15_a": (1, "Answer mentions Alzheimer disease (AD) from the disease field."),
    "C15_b": (1, "Answer states the Thr835Met variant increases neuronal cell death, matching the mechanism in disease.description."),
    "C15_c": (1, "Only AD and the Thr835Met/neuronal-cell-death mechanism (both in disease.description) are discussed; nothing introduced."),

    # ===== C16 — Adeno fiber receptor binding =====
    "C16_a": (1, "Answer identifies the Knob domain as the receptor-binding region of the fiber."),
    "C16_b": (1, "Answer states the knob interacts with host sialic acid, taken from the context."),
    "C16_c": (1, "Answer mentions only sialic acid — no off-context receptors such as ACE2 or CAR are introduced."),

    # ===== C17 — Spike surface-exposed parts =====
    "C17_a": (1, "Answer identifies S1 (and ACE2-binding via S1) as the surface-exposed receptor-engaging part."),
    "C17_b": (0, "Answer cites the extracellular topological domain range (14-1213) but does not explicitly contrast it with the transmembrane or cytoplasmic-tail regions named in the context."),
    "C17_c": (1, "All topology details mentioned (S1, S2, extracellular topological domain, ACE2 binding) are present in the context; nothing fabricated."),

    # ===== C18 — Adeno repeated motifs =====
    "C18_a": (1, "Answer attributes the repeated motifs to the Shaft region, matching the context's domain description."),
    "C18_b": (1, "Answer connects the repeats to the shaft's elongated structure and the resulting protrusion of the spike from the viral capsid (i.e., presenting the knob)."),
    "C18_c": (1, "Answer cites only the Shaft range 45-180 from the context; no specific repeat count is invented."),

    # ===== C19 — UNC5C mutation hotspots =====
    "C19_a": (1, "Answer names the Thr835Met variant at position 835, present in disease.description."),
    "C19_b": (0, "Answer says the variant is 'linked to Alzheimer disease susceptibility' but does not state the mechanistic effect of increased neuronal cell death from disease.description."),
    "C19_c": (1, "Only Thr835Met is mentioned; no additional invented variant positions."),

    # ===== C20 — Insulin match-score interpretation =====
    "C20_a": (1, "Answer reports the confidence as 94.0%, matching match_score exactly."),
    "C20_b": (0, "Answer states the score but does not interpret it qualitatively (e.g., 'high'), which the rubric requires."),
    "C20_c": (1, "Only the value 94.0 appears; no alternative or rounded confidence number is introduced."),
}


def main() -> None:
    if not CSV_PATH.exists():
        raise SystemExit(f"CSV not found: {CSV_PATH}")

    # Backup once.
    if not BACKUP_PATH.exists():
        shutil.copy2(CSV_PATH, BACKUP_PATH)

    with CSV_PATH.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    fieldnames = list(rows[0].keys())

    n_updated = 0
    missing: list[str] = []
    for r in rows:
        key = f"{r['scenario_id']}_{r['rubric_item']}"
        if key not in VERDICTS:
            missing.append(key)
            continue
        passed, expl = VERDICTS[key]
        r["passed"] = str(passed)
        r["judge_explanation"] = f"{MARKER} {expl}"
        n_updated += 1

    if missing:
        raise SystemExit(f"VERDICTS missing for {len(missing)} rows: {missing[:5]} ...")

    with CSV_PATH.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    total = len(rows)
    passed = sum(int(r["passed"]) for r in rows)
    by_sc: dict[str, list[dict]] = {}
    for r in rows:
        by_sc.setdefault(r["scenario_id"], []).append(r)
    cov = [sum(int(x["passed"]) for x in v) / len(v) for v in by_sc.values()]
    mean_cov = sum(cov) / len(cov)
    b_pass = []
    for sc_rows in by_sc.values():
        if sc_rows[0].get("class") != "B":
            continue
        mustnots = [x for x in sc_rows if x["rubric_type"] == "must_not"]
        b_pass.append(int(all(int(x["passed"]) == 1 for x in mustnots)) if mustnots else 1)
    behavior = sum(b_pass) / len(b_pass) if b_pass else None

    print(f"Updated {n_updated}/{total} rows.")
    print(f"Passed: {passed}/{total} ({passed/total:.1%})")
    print(f"Mean coverage (per-scenario): {mean_cov:.3f}")
    print(f"Behavior pass rate (class B must_not): {behavior}")
    print(f"Backup: {BACKUP_PATH.name}")


if __name__ == "__main__":
    main()
