import argparse
import numpy as np
from pathlib import Path
from sklearn.preprocessing import normalize
from sklearn.decomposition import PCA

DEFAULT_INPUT = "graph_core/output/embeddings.npy"
DEFAULT_OUTDIR = "graph_core/output"
DEFAULT_PCA_DIM = 256


def resolve_input_path(input_value: str) -> Path:
    input_path = Path(input_value)
    if input_path.is_absolute() and input_path.exists():
        return input_path

    cwd = Path.cwd()
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent

    candidates = [
        cwd / input_path,
        script_dir / input_path,
        project_root / input_path,
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    tried = "\n".join(str(p) for p in candidates)
    raise FileNotFoundError(
        f"Could not find input file '{input_value}'. Tried:\n{tried}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help="Path to embeddings.npy file",
    )
    parser.add_argument(
        "--outdir",
        default=DEFAULT_OUTDIR,
        help="Output directory (default: data/processed_human)",
    )
    parser.add_argument("--pca-dim", type=int, default=DEFAULT_PCA_DIM, help="0 = no PCA")
    args = parser.parse_args()

    input_path = resolve_input_path(args.input)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    X = np.load(input_path).astype(np.float32)
    print("Original shape:", X.shape)

    X_norm = normalize(X, norm="l2").astype(np.float32)
    np.save(outdir / "embeddings_l2.npy", X_norm)

    print("Saved L2-normalized vectors:", outdir / "embeddings_l2.npy")

    if args.pca_dim and args.pca_dim > 0:
        pca = PCA(n_components=args.pca_dim, svd_solver="randomized", random_state=42)
        X_pca = pca.fit_transform(X_norm).astype(np.float32)
        X_pca = normalize(X_pca, norm="l2").astype(np.float32)
        np.save(outdir / f"embeddings_l2_pca{args.pca_dim}.npy", X_pca)

        explained = float(pca.explained_variance_ratio_.sum())
        with open(outdir / f"pca_{args.pca_dim}_info.txt", "w", encoding="utf-8") as f:
            f.write(f"explained_variance_ratio_sum={explained}\n")

        print(f"Saved PCA vectors: {outdir / f'embeddings_l2_pca{args.pca_dim}.npy'}")
        print(f"Explained variance ratio sum: {explained:.4f}")


if __name__ == "__main__":
    main()