import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
SCRIPTS_DIR = ROOT / "scripts"

STEP_SCRIPTS = [
    "inspect_h5.py",
    "extract_embeddings.py",
    "prepare_vectors.py",
    "build_knn_graph.py",
    "analyze_graph.py",
    "fetch_uniprot_annotations.py",
    "fetch_disease_annotations.py",
    "export_for_neo4j.py",
]


def clean_output_directory():
    if OUTPUT_DIR.exists():
        print(f"Cleaning output directory: {OUTPUT_DIR}")
        for child in OUTPUT_DIR.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    else:
        OUTPUT_DIR.mkdir(parents=True)
        print(f"Created output directory: {OUTPUT_DIR}")


def run_script(script_name, *args):
    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")

    command = [sys.executable, str(script_path), *args]
    print("\nRunning:", " ".join(command))
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Step failed: {script_name} (exit code {result.returncode})")


def main():
    clean_output_directory()

    for script_name in STEP_SCRIPTS:
        run_script(script_name)

    print("\nPipeline completed successfully.")


if __name__ == "__main__":
    main()
