"""Provides file type information based on edam code and extension."""
EDAM_INFO = {
    ("EDAM_1.24.format_3727", None): {"desc": "tiff", "mime": "image/tiff"},
    ("EDAM_1.24.format_3464", None): {"desc": "json", "mime": "application/json"},
    ("EDAM_1.24.format_3508", None): {"desc": "pdf", "mime": "application/pdf"},
    ("EDAM_1.24.format_3590", None): {"desc": "hdf5", "mime": "application/x-hdf5"},
    ("EDAM_1.24.format_3987", None): {"desc": "zip", "mime": "application/zip"},
    ("EDAM_1.24.format_3752", None): {"desc": "csv", "mime": "text/csv"},
    ("EDAM_1.24.format_3755", None): {
        "desc": "tsv",
        "mime": "text/tab-separated-values",
    },
    ("EDAM_1.24.format_3790", None): {
        "desc": "h5ad (anndata)",
        "mime": "application/x-hdf5",
    },
    ("EDAM_1.24.format_3915", None): {"desc": "zarr", "mime": "application/vnd.zarr"},
    ("EDAM_1.24.format_4006", None): {
        "desc": "zarr (spatialdata)",
        "mime": "application/vnd.zarr",
    },
    ("EDAM_1.24.data_3671", None): {"desc": "plain text", "mime": "text/plain"},
    ("EDAM_1.24.format_3916", ".mtx"): {
        "desc": "adjacency matrix",
        "mime": "text/plain",
    },
    (None, ".html"): {"desc": "unknown but html encoded", "mime": "text/html"},
    (None, ".tsv"): {
        "desc": "unknown but tsv encoded",
        "mime": "text/tab-separated-values",
    },
    (None, ".csv"): {"desc": "unknown but csv encoded", "mime": "text/csv"},
    ("EDAM_1.24.format_2333", ""): {
        # this entry covers the .zgroup entry at the top of a Zarr dir
        "desc": "Zarr root file",
        "mime": "application/json",
    },
    ("EDAM_1.24.format_2333", None): {
        "desc": "binary format",
        "mime": "application/octet-stream",
    },
    ("EDAM_1.24.format_3003", None): {
        "desc": "BED (Browser Extensible Data)",
        "mime": "text/x-bed",
    },
}