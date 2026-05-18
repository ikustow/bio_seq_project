"""End-to-end evaluation harness for the bioseq retrieval pipeline.

Origin: drafted by a collaborator with Gemini's help, adapted here so it
matches the actual `pipeline_interface` API in this repo. Lives under
`scripts/eval/` so it can be removed with a single `rm -rf scripts/eval`
when no longer needed.

Run from the repo root:
    python scripts/eval/e2e_eval.py
"""

from __future__ import annotations

import os
import sys
import json
import time
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any

# ---------------------------------------------------------------------------
# Make `pipeline_interface` importable without changing project layout.
# pipeline_interface itself does `from src.pipeline import ...`, so its own
# directory must be on sys.path.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = REPO_ROOT / "app" / "backend" / "bioseq_retriever"
sys.path.insert(0, str(PIPELINE_DIR))

# Pipeline reads API keys from os.environ — load .env first.
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)
except ImportError:
    pass

from pipeline_interface import run_pipeline_interface  # noqa: E402

# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class TestCase:
    id: int
    name: str
    prompt: str
    expected_type: str
    expected_accessions: List[str] = field(default_factory=list)
    constraints: Dict[str, Any] = field(default_factory=dict)
    is_negative: bool = False

# =============================================================================
# HELPER FUNCTIONS (METRICS & EVALUATION)
# =============================================================================

def calculate_rr(matches: List[Dict[str, Any]], expected_accessions: List[str]) -> float:
    """Calculates Reciprocal Rank (RR)."""
    if not expected_accessions:
        return 0.0
    for i, match in enumerate(matches):
        if match.get("primaryAccession") in expected_accessions:
            return 1.0 / (i + 1)
    return 0.0

def calculate_recall_at_k(matches: List[Dict[str, Any]], expected_accessions: List[str], k: int) -> float:
    """Calculates Recall@K (Binary: 1 if any expected found in top K, else 0)."""
    if not expected_accessions:
        return 0.0
    top_k_accs = [m.get("primaryAccession") for m in matches[:k]]
    for acc in expected_accessions:
        if acc in top_k_accs:
            return 1.0
    return 0.0

def calculate_ndcg(matches: List[Dict[str, Any]], expected_accessions: List[str], k: int) -> float:
    """Calculates simplified nDCG@K (Binary relevance: 1 for match, 0 otherwise)."""
    if not expected_accessions:
        return 0.0

    dcg = 0.0
    for i, match in enumerate(matches[:k]):
        rel = 1.0 if match.get("primaryAccession") in expected_accessions else 0.0
        dcg += rel / math.log2(i + 2)

    idcg = 1.0
    return dcg / idcg

def evaluate_constraints(matches: List[Dict[str, Any]], constraints: Dict[str, Any]) -> float:
    """Evaluates structured biological constraints against the top match."""
    if not constraints or not matches:
        return 1.0

    total_constraints = len(constraints)
    satisfied_count = 0
    top_match = matches[0]
    record_text = json.dumps(top_match).lower()

    for c_type, c_values in constraints.items():
        is_satisfied = False

        if c_type == "include_taxa":
            lineage = top_match.get("organism", {}).get("lineage", [])
            org_name = top_match.get("organism", {}).get("scientificName", "").lower()
            if any(v.lower() in org_name or any(v.lower() in l.lower() for l in lineage) for v in c_values):
                is_satisfied = True

        elif c_type == "exclude_taxa":
            lineage = top_match.get("organism", {}).get("lineage", [])
            org_name = top_match.get("organism", {}).get("scientificName", "").lower()
            if not any(v.lower() in org_name or any(v.lower() in l.lower() for l in lineage) for v in c_values):
                is_satisfied = True

        elif c_type == "subcellular_location":
            locations = []
            for comment in top_match.get("comments", []):
                if comment.get("commentType") == "SUBCELLULAR_LOCATION":
                    for loc in comment.get("subcellularLocations", []):
                        val = loc.get("location", {}).get("value", "").lower()
                        if val:
                            locations.append(val)
            if any(v.lower() in " ".join(locations) for v in c_values):
                is_satisfied = True

        elif c_type == "ec_numbers":
            ec_nums = []
            for xref in top_match.get("uniProtKBCrossReferences", []):
                if xref.get("database") == "EC":
                    ec_nums.append(xref.get("id", ""))
            if any(v in ec_nums for v in c_values):
                is_satisfied = True

        elif c_type == "interaction_partners":
            if any(v.lower() in record_text for v in c_values):
                is_satisfied = True

        elif c_type == "functional_terms":
            if any(v.lower() in record_text for v in c_values):
                is_satisfied = True

        if is_satisfied:
            satisfied_count += 1

    return satisfied_count / total_constraints

# =============================================================================
# EVALUATOR ENGINE
# =============================================================================

class BioSeqEvaluator:
    def __init__(self):
        self.test_cases = [
            TestCase(1, "Direct Protein Identification",
                     "Identify this sequence: MALWMRLLPLLALLALWGPDPAAAFVNQHLCGSHLVEALYLVCGERGFFYTPKTRREAEDLQVGQVELGGGPGAGSLQPLALEGSLQKRGIVEQCCTSICSLYQLENYCN",
                     "PROTEIN", ["P01308"]),

            TestCase(2, "Taxonomic Exclusion",
                     "Find sequences similar to human insulin (MALWMRLL...) but I am interested in any species except Human.",
                     "PROTEIN", ["P01315", "P01317"], {"exclude_taxa": ["Homo sapiens"]}),

            TestCase(3, "Functional context (Glucose)",
                     "I have this protein [MALWMRLL...]. Is it involved in glucose metabolism?",
                     "PROTEIN", ["P01308"], {"functional_terms": ["glucose metabolism"]}),

            TestCase(4, "DNA Sequence Routing",
                     "Consider this gene sequence: ATGACAAAGAGACTCCGGGTGGAAGATGACTTCAACCCCGTCTACCCCTATGGCTACGCGCGGAATCAAAATATTCCCTTCCTCACTCCCCCCTTTGTCTCCTCCAATGGATTTCAAAACTTCCCCCCTGGGGTCCTGTCACTTAAACTGGCTGACCCAATCACCATTAACAATCAAAATGTATCACTCAAGGTTGGAGGGGGGCTAACTTTGCAAGAAGAAACTGGAAAATTAACAGTTAATACTGAACCACCTTTGCATCTTACAAATAACAAATTAGGGATAGCTTTAGACGCTCCATTTGATGTTATAGACAATAAGCTTACACTATTAGCAGGCCATGGCTTGTCTATTATAACAAAAGAAACATCAACACTGCCTGGCTTGGTTAATACTCTTGTAGTATTAACTGGAAAGGGTATTGGAACAGATTTATCAAATAATGGTGGAAATATATGTGTTAGAGTTGGAGAAGGCGGCGGCTTATCATTTAATGACAATGGAGACTTGGTAGCATTTAATAAAAAAGAAGACAAACGCACCCTATGGACAACTCCAGACACATCTCCAAATTGCAGAATTGATCAGGATAAGGACTCTAAGCTAACTTTGGTCCTTACAAAGTGTGGAAGTCAAATATTAGCCAATGTGTCATTAATTGTTGTAGCTGGAAGGTACAAAATTATCAATAACAATACTAATCCAGCTCTTAAAGGATTTACCATTAAATTGTTGTTTGATAAAAATGGAGTCCTTATGGAATCTTCAAATCTTGGTAAATCATATTGGAACTTTCGAAATCAAAATTCAATTATGTCAACAGCTTATGAAAAAGCTATTGGTTTTATGCCTAATTTGGTAGCCTATCCAAAACCTACCACTGGCTCTAAAAAATATGCAAGAGATATAGTTTATGGAAACATCTACCTTGGCGGAAAGCCACATCAACCAGTAACCATTAAAACTACCTTTAACCAGGAAACTGGATGTGAATACTCTATTACATTTGATTTTAGTTGGGCCAAAACTTATGTAAATGTTGAATTTGAAACTACCTCTTTTACCTTTTCCTATATTGCCCAAGAATAA. What is it?",
                     "DNA", ["P53110"]),

            TestCase(5, "GPCR Example (Rhodopsin)",
                     "Find matches for this G protein-coupled receptor involved in visual phototransduction: MNGTEGPNFYVPFSNKTGVVRSPFEAPQYYLAEPWQFSMLAAYMFLLIMLGFPINFLTLYVTVQHKKLRTPLNYILLNLAVADLFMVFGGFTTTLYTSLHGYFVFGPTGCNLEGFFATLGGEIALWSLVVLAIERYVVVCKPMSNFRFGENHAIMGVAFTWVMALACAAPPLVGWSRYIPEGMQCSCGIDYYTPHEETNNESFVIYMFVVHFIIPLIVIFFCYGQLVFTVKEAAAQQQESATTQKAEKEVTRMVIIMVIAFLICWLPYAGVAFYIFTHQGSDFGPIFMTIPAFFAKTSAVYNPVIYIMMNKQFRNCMVTTLCCGKNPLGDDEASTTVSKTETSQVAPA",
                     "PROTEIN", ["P08100"], {"functional_terms": ["phototransduction", "GPCR"]}),

            TestCase(6, "Kinase Example (ABL1)",
                     "Search for this tyrosine-protein kinase: MLEICLKLVGCKSKKGLSSSSSCYLEEALQRPVASDFEPQGLSEAARWNSKENLLAGPSENDPNLFVALYDFVASGDNTLSITKGEKLRVLGYNHNGEWCEAQTKNGQGWVPSNYITPVNSLEKHSWYHGPVSRNAAEYLLSSGINGSFLVRESESSPGQRSISLRYEGRVYHYRINTASDGKLYVSSESRFNTLAELVHHHSTVADGLITTLHYPAPKRNKPTVYGVSPNYDKWEMERTDITMKHKLGGGQYGEVYEGVWKKYSLTVAVKTLKEDTMEVEEFLKEAAVMKEIKHPNLVQLLGVCTREPPFYIITEFMTYGNLLDYLRECNRQEVNAVVLLYMATQISSAMEYLEKKNFIHRDLAARNCLVGENHLVKVADFGLSRLMTGDTYTAHAGAKFPIKWTAPESLAYNKFSIKSDVWAFGVLLWEIATYGMSPYPGIDLSQVYELLEKDYRMERPEGCPEKVYELMRACWQWNPSDRPSFAEIHQAFETMFQESSISDEVEKELGKQGVRGAVSTLLQAPELPTKTRTSRRAAEHRDTTDVPEMPHSKGQGESDPLDHEPAVSPLLPRKERGPPEGGLNEDERLLPKDKKTNLFSALIKKKKKTAPTPPKRSSSFREMDGQPERRGAGEEEGRDISNGALAFTPLDTADPAKSPKPSNGAGVPNGALRESGGSGFRSPHLWKKSSTLTSSRLATGEEEGGGSSSKRFLRSCSASCVPHGAKDTEWRSVTLPRDLQSTGRQFDSSTFGGHKSEKPALPRKRAGENRSDQVTRGTVTPPPRLVKKNEEAADEVFKDIMESSPGSSPPNLTPKPLRRQVTVAPASGLPHKEEAGKGSALGTPAAAEPVTPTSKAGSGAPGGTSKGPAEESRVRRHKHSSESPGRDKGRLAKLKPAPPPPPAAASAGKAGGKPSQSPSQEAAGEAVLGAKTKATSLVDAVNSDAAKPSQPGEGLKKPVLPATPKPQSAKPSGTPISPAPVPSTLPSASSALAGDQPSSTAFIPLISTRVSLRKTRQPPERIASGAITKGVVLDSTEALCLAISRNSEQMASHSAVLEAGKNLYTFCVSYVDSIQQMRNKFAFREAINKLENNLRELQICPATAGSGPAATQDFSKLLSSVKEISDIVQR",
                     "PROTEIN", ["P00519"], {"ec_numbers": ["2.7.10.2"]}),

            TestCase(7, "Membrane Protein (UNC5C)",
                     "I have a sequence for a netrin receptor involved in axon guidance. Is it UNC5C? Seq: MARAGSGAAGGRAGGAGRAAWPGLRALLGLLLPGVTAAAMNGVPTAEEVSPKPDLTVALNREVARSLSCTVTGHPKPVVSWQKDERPLDNGHYLVRNSHGLNILRIQNARPGDNGIYVCSASNPVGRQSTSTRLRVQEIDTPLPQEVEIKEVEEAYVPCVATHPQPQITWQKNGRPFADKGYYVTESNRLLVLELSNAKPDDMGLYVCSANNPIGEQSSTSRLRVQEVDSPDPKLSYKVVDEGRPVPCVAGHPVPDVTWQKNGVPFSDKGYLVLENSHGLRILELSRANPGDMGHYVCSANNPVGEQSSTSRLRVQEVDTPLPQEVEVKEVEEAAVPCVATHPQPQITWQKNGRPFADKGYYVTESNRLLVLELSNAKPDDMGLYVCSANNPIGEQSSTSRLRVQEVDSPDPKLSYKVVDEGRPVPCVAGHPVPDVTWQKNGVPFSDKGYLVLENSHGLRILELSRANPGDMGHYVCSANNPVGEQSSTSRLRVQEVDTPVPKVDVKEVEEAAVPCVATHPQPQITWQKNGRPFADKGYYVTESNRLLVLELSNAKPDDMGLYVCSANNPIGEQSSTSRLRVQEVDSPDPKLSYKVVDEGRPVPCVAGHPVPDVTWQKNGVPFSDKGYLVLENSHGLRILELSRANPGDMGHYVCSANNPVGEQSSTSRLRVQEVDTPVPKVDVKEVEEAAVPCVATHPQPQITWQKNGRPFADKGYYVTESNRLLVLELSNAKPDDMGLYVCSANNPIGEQSSTSRLRVQEVDSPDPKLSYKVVDEGRPVPCVAGHPVPDVTWQKNGVPFSDKGYLVLENSHGLRILELSRANPGDMGHYVCSANNPVGEQSSTSRLRVQEVD",
                     "PROTEIN", ["O95185"], {"subcellular_location": ["membrane"], "functional_terms": ["axon guidance"]}),

            TestCase(8, "Bacterial Enzyme (Beta-lactamase)",
                     "Search for this bacterial enzyme sequence: MSIQHFRVALIPFFAAFCLPVFAHPETLVKVKDAEDQLGARVGYIELDLNSGKILESFRPEERFPMMSTFKVLLCGAVLSRVDAGQEQLGRRIHYSQNDLVEYSPVTEKHLTDGMTVRELCSAAITMSDNTAANLLLTTIGGPKELTAFLHNMGDHVTRLDRWEPELNEAIPNDERDTTMPAAMATTLRKLLTGELLTLASRQQLIDWMEADKVAGPLLRSALPAGWFIADKSGAGERGSRGIIAALGPDGKPSRIVVIYTTGSQATMDERNRQIAEIGASLIKHW",
                     "PROTEIN", ["P62593"], {"include_taxa": ["Bacteria"], "ec_numbers": ["3.5.2.6"]}),

            TestCase(9, "Viral Protein (SARS-CoV-2 Spike S1)",
                     "Identify this viral protein fragment from SARS-CoV-2: MFVFLVLLPLVSSQCVNLTTRTQLPPAYTNSFTRGVYYPDKVFRSSVLHSTQDLFLPFFSNVTWFHAIHVSGTNGTKRFDNPVLPFNDGVYFASTEKSNIIRGWIFGTTLDSKTQSLLIVNNATNVVIKVCEFQFCNDPFLGVYYHKNNKSWMESEFRVYSSANNCTFEYVSQPFLMDLEGKQGNFKNLREFVFKNIDGYFKIYSKHTPINLVRDLPQGFSALEPLVDLPIGINITRFQTLLALHRSYLTPGDSSSGWTAGAAAYYVGYLQPRTFLLKYNENGTITDAVDCALDPLSETKCTLKSFTVEKGIYQTSNFRVQPTESIVRFPNITNLCPFGEVFNATRFASVYAWNRKRISNCVADYSVLYNSASFSTFKCYGVSPTKLNDLCFTNVYADSFVIRGDEVRQIAPGQTGKIADYNYKLPDDFTGCVIAWNSNNLDSKVGGNYNYLYRLFRKSNLKPFERDISTEIYQAGSTPCNGVEGFNCYFPLQSYGFQPTNGVGYQPYRVVVLSFELLHAPATVCGPKKSTNLVKNKCVNFNFNGLTGTGVLTESNKKFLPFQQFGRDIADTTDAVRDPQTLEILDITPCSFGGVSVITPGTNTSNQVAVLYQDVNCTEVPVAIHADQLTPTWRVYSTGSNVFQTRAGCLIGAEHVNNSYECDIPIGAGICASYQTQTNSPRRARS",
                     "PROTEIN", ["P0DTC2"], {"include_taxa": ["Viruses"]}),

            TestCase(10, "Short Peptide (Oxytocin)",
                     "Sequence: CYIQNCPLG",
                     "PROTEIN", ["P01178"]),

            TestCase(11, "Fragmented Sequence",
                     "What is this partial sequence: MALT...GIVEQCCTSICSLYQLENYCN",
                     "PROTEIN", ["P01308"], {"functional_terms": ["insulin"]}),

            TestCase(12, "Logical Firewall (Negative)",
                     "How do I bake a chocolate cake?",
                     "ERROR", [], {}, True),

            TestCase(13, "Viral DNA (Adenovirus Fiber)",
                     "Analyze this DNA sequence from a human adenovirus: ATGCAGCGCGCGGCGATGTATGAGGAAGGTCCTCCTCCCTCCTACGAGAGCGTGGTGAGCGCGGCGCCAGTGGCGGCGGCGCTGGGTTCCCCCTTCGATGCTCCCCTGGACCCGCCGTTTGTGCCTCCGCGGTACCTGCGGCCTACCGGGGGGAGAAACAGCATCCGTTACTCTGAGTTGGCACCCCTATTCGACACCACCCGTGTGTACCTTGTGGACAACAAGTCAACGGATGTGGCATCCCTGAACTACCAGAACGACCACAGCAACTTTCTAACCACGGTCATTCAAAACAATGACTACAGCCCGGGGGAGGCAAGCACACAGACCATCAATCTTGACGACCGTTCGCACTGGGGCGGCGACCTGAAAACCATCCTGCATACCAACATGCCAAATGTGAACGAGTTCATGTTTACCAATAAGTTTAAGGCGCGGGTGATGGTGTCGCGCTCGCTTACTAAGGACAAACAGGTGGAGCTGAAATATGAGTGGGTGGAGTTCACGCTGCCCGAGGGCAACTACTCCGAGACCATGACCATAGACCTTATGAACAACGCGATCGTGGAGCACTACTTGAAAGTGGGCAGGCAGAACGGGGTTCTGGAAAGCGACATCGGGGTAAAGTTTGACACCCGCAACTTCAGACTGGGGTTTGACCCAGTCACTGGTCTTGTCATGCCTGGGGTATATACAAACGAAGCCTTCCATCCAGACATCATTTTGCTGCCAGGATGCGGGGTGGACTTCACCCACAGCCGCCTGAGCAACTTGTTGGGCATCCGCAAGCGGCAACCCTTCCAGGAGGGCTTTAGGATCACCTACGATGACCTGGAGGGTGGTAACATTCCCGCACTGTTGGATGTGGACGCCTACCAGGCAAGCTTAAAAGATGACACCGAACAGGGCGGGGATGGCGCAGGCGGCGGCAACAACAGTGGCAGCGGCGCGGAAGAGAACTCCAACGCGGCAGCCGCGGCAATGCAGCCGGTGGAGGACATGAACGATCATGCCATTCGCGGCGACACCTTTGCCACACGGGCGGAGGAGAAGCGCGCTGAGGCCGAGGCAGCGGCAGAAGCTGCCGCCCCCGCTGCGCAACCCGAGGTCGAGAAGCCTCAGAAGAAACCGGTGATCAAACCCCTGACAGAGGACAGCAAGAAACGCAGTTACAACCTAATAAGCAATGACAGCACCTTCACCCAGTACCGCAGCTGGTACCTTGCATACAACTACGGCGACCCTCAGACCGGGATCCGCTCATGGACCCTCCTTTGCACTCCTGACGTAACCTGCGGCTCGGAGCAGGTCTACTGGTCGTTGCCAGACATGATGCAAGACCCCGTGACCTTCCGCTCCACGAGCCAGATCAGCAACTTTCCGGTGGTGGGCGCCGAGCTGTTGCCCGTGCACTCCAAGAGCTTCTACAACGACCAGGCCGTCTACTCCCAGCTCATCCGCCAGTTTACCTCTCTGACCCACGTGTTCAATCGCTTTCCCGAGAACCAGATTTTGGCGCGCCCGCCAGCCCCCACCATCACCACCGTCAGTGAAAACGTTCCTGCTCTCACAGATCACGGGACGCTACCGCTGCGCAACAGCATCGGAGGAGTCCAGCGAGTGACCATTACTGACGCCAGACGCCGCACCTGCCCCTACGTTTACAAGGCCCTGGGCATAGTCTCGCCGCGCGTCCTATCGAGCCGCACTTTTTGA Is it involved in cell attachment?",
                     "DNA", ["P03276"], {"include_taxa": ["Adenoviridae"], "functional_terms": ["attachment"]}),

            TestCase(14, "Bacterial Gene (recA)",
                     "Consider this bacterial DNA sequence: ATGGCTATCGACGAAAACAAACAGAAAGCGTTGGCGGCAGCACTGGGCCAGATTGAGAAACAATTTGGTAAAGGCTCCATCATGCGCCTGGGTGAAGACCGTTCCATGGATGTGGAAACCATCTCTACCGGTTCGCTTTCACTGGATATCGCGCTTGGGGCAGGTGGTCTGCCGATGGGCCGTATCGTCGAAATCTACGGACCGGAATCTTCCGGTAAAACCACGCTGACGCTGCAGGTGATCGCCGCAGCGCAGCGTGAAGGTAAAACCTGTGCGTTTATCGATGCTGAACACGCGCTGGACCCAATCTACGCACGTAAACTGGGCGTCGATATCGACAACCTGCTGTGCTCCCAGCCGGACACCGGCGAGCAGGCACTGGAAATCTGTGACGCCCTGGCGCGTTCTGGCGCAGTAGACGTTATCGTCGTTGACTCCGTGGCGGCACTGACGCCGAAAGCGGAAATCGAAGGCGAAATCGGCGACTCTCACATGGGCCTTGCGGCACGTATGATGAGCCAGGCGATGCGTAAGCTGGCGGGTAACCTGAAGCAGTCCAACACGCTGCTGATCTTCATCAACCAGATCCGTATGAAAATTGGTGTGATGTTCGGTAACCCGGAAACCACTACCGGTGGTAACGCGCTGAAATTCTACGCCTCTGTTCGTCTCGACATCCGTCGTATCGGCGCGGTGAAAGAGGGCGAAAACGTGGTGGGTAGCGAAACCCGCGTGAAAGTGGTGAAGAACAAAATCGCTGCGCCGTTTAAACAGGCTGAATTCCAGATCCTCTACGGCGAAGGTATCAACTTCTACGGCGAACTGGTTGACCTGGGCGTAAAAGAGAAGCTGATCGAGAAAGCAGGCGCGTGGTACTACTCCAAGGGTGAAAAAATCGGTCAGGGTAAAGCGAATGCGACTGCCTGGCTGAAAGATAACCCGGAAACCGCGAAAGAGATCGAGAAGAAAGTACGTGAGTTGCTGCTGAGCAACCCGAACTCAACGCCGGATTTCTCTGTAGATGATAGCGAAGGCGTAGCAGAAACTAACGAAGATTTTTAA Does it belong to RecA?",
                     "DNA", ["P0A7G6"], {"include_taxa": ["Bacteria"], "functional_terms": ["RecA", "DNA repair"]}),

            TestCase(15, "Mitochondrial DNA (cytochrome b)",
                     "What is this mitochondrial gene sequence: ATGACTCCACAAAGAAACACCAACCCCTTGAAGATGCTCATCAACCATTCTTTCATTGACCTCCCACCTTCCAACATCTCTGCCTGACTCCCTACCCCATCCAACATCTCCGCATTCATGAAACTTCGGCTCACTCCTTGGCGCCTAGCATCATCATCATTCTACTGACTGGCCTAGCCCTCATCACCATCGCTAACCCCATGGCCATCATGTTCCTTCTACTCATCGGCGTGCTACTAACATGATCGTCACCCTCGTCTGTTTGCTAGGTGTGTGACTGCTCCTCCTAGCCATCGCTATCATTACCGGCTTCATCCTAGCCCTAGCCCTACTACTAATCGCAATGGCCTTCCGCCCTATC?",
                     "DNA", ["P00156"], {"subcellular_location": ["Mitochondrion"], "functional_terms": ["cytochrome b"]})
        ]
        self.results = []
        self.metrics = {}

    def _evaluate_results(self, test: TestCase, output: Dict[str, Any]) -> Dict[str, Any]:
        res = {
            "routing_ok": False,
            "ground_truth_found": False,
            "rr": 0.0,
            "recall_at_1": 0.0,
            "recall_at_5": 0.0,
            "ndcg_at_5": 0.0,
            "constraint_score": 0.0,
            "failure_type": "UNKNOWN_FAILURE",
            "confidences": {},
            "status": "FAILED"
        }

        res["confidences"] = {
            "routing": output.get("routing_confidence"),
            "retrieval": output.get("retrieval_confidence"),
            "rejection": output.get("rejection_confidence")
        }

        if test.is_negative:
            res["routing_ok"] = "error" in output and output.get("error") is not None
            if not res["routing_ok"]:
                res["failure_type"] = "ROUTING_FAILURE"
        else:
            res["routing_ok"] = output.get("sequence_type") == test.expected_type
            if not res["routing_ok"]:
                res["failure_type"] = "ROUTING_FAILURE"

        # NB: pipeline returns matches under `final_results`, not `results`.
        raw_matches = output.get("final_results") or []
        matches = sorted(raw_matches, key=lambda x: x.get("_search_score") or 0, reverse=True)

        if not test.is_negative and matches:
            res["rr"] = calculate_rr(matches, test.expected_accessions)
            res["recall_at_1"] = calculate_recall_at_k(matches, test.expected_accessions, 1)
            res["recall_at_5"] = calculate_recall_at_k(matches, test.expected_accessions, 5)
            res["ndcg_at_5"] = calculate_ndcg(matches, test.expected_accessions, 5)
            res["ground_truth_found"] = res["recall_at_5"] > 0

            if not res["ground_truth_found"] and test.expected_accessions:
                res["failure_type"] = "RETRIEVAL_FAILURE"

        if not test.is_negative and matches:
            res["constraint_score"] = evaluate_constraints(matches, test.constraints)
            if res["constraint_score"] < 1.0 and test.constraints:
                if res["failure_type"] == "UNKNOWN_FAILURE":
                    res["failure_type"] = "CONSTRAINT_FAILURE"

        if output.get("error") and not test.is_negative:
            res["failure_type"] = "PIPELINE_EXCEPTION"

        if res["routing_ok"]:
            if test.is_negative:
                res["status"] = "PASSED"
                res["failure_type"] = None
            elif res["ground_truth_found"] and res["constraint_score"] >= 0.5:
                res["status"] = "PASSED"
                res["failure_type"] = None

        return res

    def run_evaluation(self):
        print(f"\n{'='*70}")
        print(f"STARTING E2E EVALUATION ({len(self.test_cases)} tests)")
        print(f"{'='*70}\n")

        for test in self.test_cases:
            print(f"Test {test.id}: {test.name}...", end=" ", flush=True)
            start_time = time.time()

            try:
                output = run_pipeline_interface(test.prompt)
                duration = time.time() - start_time

                eval_metrics = self._evaluate_results(test, output)
                self.results.append({
                    "test": test,
                    "metrics": eval_metrics,
                    "duration": duration,
                    "error": output.get("error")
                })

                print(f"[{eval_metrics['status']}] ({duration:.2f}s)")
            except Exception as e:
                duration = time.time() - start_time
                print(f"[EXCEPTION] {str(e)}")
                self.results.append({
                    "test": test,
                    "metrics": {"status": "ERROR", "failure_type": "PIPELINE_EXCEPTION"},
                    "duration": duration,
                    "error": str(e)
                })

        self.print_report()

    def print_report(self):
        print(f"\n{'='*70}")
        print(f"{' '*25}E2E EVALUATION REPORT")
        print(f"{'='*70}\n")

        total = len(self.test_cases)
        passed = sum(1 for r in self.results if r["metrics"].get("status") == "PASSED")

        mrr = sum(r["metrics"].get("rr", 0) for r in self.results) / total
        r1 = sum(r["metrics"].get("recall_at_1", 0) for r in self.results) / total
        r5 = sum(r["metrics"].get("recall_at_5", 0) for r in self.results) / total
        avg_cons = sum(r["metrics"].get("constraint_score", 0) for r in self.results) / total

        tax = {}
        for r in self.results:
            ft = r["metrics"].get("failure_type")
            if ft:
                tax[ft] = tax.get(ft, 0) + 1

        conf_stats = {"routing": [], "retrieval": []}
        overconfident_failures = 0
        for r in self.results:
            c = r["metrics"].get("confidences", {}) or {}
            if c.get("routing") is not None:
                conf_stats["routing"].append(c["routing"])
            if c.get("retrieval") is not None:
                conf_stats["retrieval"].append(c["retrieval"])
            max_conf = max([v for v in c.values() if v is not None] or [0])
            if max_conf > 0.8 and r["metrics"].get("status") == "FAILED":
                overconfident_failures += 1

        lengths = [len(t.prompt) for t in self.test_cases]
        durations = [r["duration"] for r in self.results]

        print(f"OVERALL SCORE: {passed}/{total} ({passed/total:.1%})")
        if durations:
            print(f"Latency: Avg {sum(durations)/total:.2f}s | Range {min(durations):.2f}-{max(durations):.2f}s")
        print(f"Query Length: Avg {sum(lengths)/total:.0f} | Min {min(lengths)} | Max {max(lengths)}")

        print(f"\n[RANKING METRICS]")
        print(f"MRR:         {mrr:.3f}")
        print(f"Recall@1:    {r1:.3f}")
        print(f"Recall@5:    {r5:.3f}")

        print(f"\n[CONSTRAINT ANALYSIS]")
        print(f"Avg Constraint Score: {avg_cons:.2%}")

        print(f"\n[FAILURE TAXONOMY]")
        if not tax:
            print("No failures recorded.")
        for k, v in tax.items():
            print(f"{k:<20}: {v}")

        print(f"\n[CONFIDENCE ANALYSIS]")
        avg_route = sum(conf_stats["routing"])/len(conf_stats["routing"]) if conf_stats["routing"] else 0
        avg_retr = sum(conf_stats["retrieval"])/len(conf_stats["retrieval"]) if conf_stats["retrieval"] else 0
        print(f"Avg Routing Conf:   {avg_route:.2%}")
        print(f"Avg Retrieval Conf: {avg_retr:.2%}")
        print(f"Overconfident Failures: {overconfident_failures}")

        print(f"\n{'ID':<4} | {'Test Name':<35} | {'Status':<8} | {'RR':<5} | {'Cons'}")
        print("-" * 70)
        for res in self.results:
            t = res["test"]
            m = res["metrics"]
            print(f"{t.id:<4} | {t.name:<35} | {m.get('status', 'ERROR'):<8} | {m.get('rr', 0):.2f} | {m.get('constraint_score', 0):.2f}")

        print(f"\n{'='*70}")


if __name__ == "__main__":
    evaluator = BioSeqEvaluator()
    evaluator.run_evaluation()
