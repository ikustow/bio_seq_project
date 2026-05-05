import h5py
import os
import sys
from pathlib import Path

DEFAULT_MAX_ITEMS = 25


def make_h5_printer(max_items):
    seen = {"count": 0, "suppressed": 0}

    def print_h5(name, obj):
        if seen["count"] >= max_items:
            seen["suppressed"] += 1
            return
        obj_type = type(obj).__name__
        shape = getattr(obj, "shape", None)
        dtype = getattr(obj, "dtype", None)
        print(f"{name} | type={obj_type} | shape={shape} | dtype={dtype}")
        seen["count"] += 1

    print_h5.seen = seen
    return print_h5

if __name__ == "__main__":
    default_path = Path(__file__).resolve().parent.parent / "data" / "per-protein.h5"
    if len(sys.argv) == 1:
        path = default_path
    elif len(sys.argv) == 2:
        path = sys.argv[1]
    else:
        print("Usage: python inspect_h5.py [path_to_h5]")
        sys.exit(1)
    max_items = int(os.getenv("BIOSEQ_INSPECT_MAX_ITEMS", str(DEFAULT_MAX_ITEMS)))
    with h5py.File(path, "r") as f:
        top_level_keys = list(f.keys())
        print("Top-level key count:", len(top_level_keys))
        print("Top-level key sample:", top_level_keys[:max_items])
        printer = make_h5_printer(max_items)
        f.visititems(printer)
        if printer.seen["suppressed"]:
            print(f"... suppressed {printer.seen['suppressed']} additional HDF5 items")
