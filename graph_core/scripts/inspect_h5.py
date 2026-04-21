import h5py
import sys

def print_h5(name, obj):
    obj_type = type(obj).__name__
    shape = getattr(obj, "shape", None)
    dtype = getattr(obj, "dtype", None)
    print(f"{name} | type={obj_type} | shape={shape} | dtype={dtype}")

if __name__ == "__main__":
    default_path = "graph_core/data/per-protein.h5"
    if len(sys.argv) == 1:
        path = default_path
    elif len(sys.argv) == 2:
        path = sys.argv[1]
    else:
        print("Usage: python inspect_h5.py [path_to_h5]")
        sys.exit(1)
    with h5py.File(path, "r") as f:
        print("Top-level keys:", list(f.keys()))
        f.visititems(print_h5)