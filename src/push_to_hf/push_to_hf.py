#!/usr/bin/env python
"""Utilities for pushing data containers to Hugging Face."""
from __future__ import annotations

import argparse
import os

import dask.array as da
import scanpy as sc
import zarr
import logging
from zipfile import ZipFile
import json

from typing import Any

from anndata.experimental import read_dispatched, write_dispatched
from anndata.io import read_elem

import mlcroissant as mlc

from pprint import pformat, pprint

from jsonpath_ng import parse

logging.basicConfig(
    level=logging.INFO,
)

LOGGER = logging.getLogger(__name__)

def read_dask(store):
    f = zarr.open(store, mode="r", storage_options={"headers": {"Authorization": f"Bearer {os.environ['AUTH_TOK']}"}})

    def callback(func, elem_name: str, elem, iospec):
        print(f"callback {elem_name}: {iospec}")
        if iospec.encoding_type in (
            "dataframe",
            "csr_matrix",
            "csc_matrix",
            "awkward-array",
        ):
            # Preventing recursing inside of these types
            return read_elem(elem)
        elif iospec.encoding_type == "array":
            return da.from_zarr(elem)
        else:
            return func(elem)

    adata = read_dispatched(f, callback=callback)

    return adata

def adata_from_filesets(ds_json):
    rslt = {}
    for file_set in [elt for elt in ds_json["distribution"] if elt["@type"] == "cr:FileSet"]:
        pprint(file_set)
        fs_id = file_set["@id"]
        contained_in = file_set["containedIn"]["@id"]
        print(f"containedIn: {contained_in}")
        if (encoding_format := file_set.get("encodingFormat")) == "application/x-zarr":
            includes_str = file_set.get("includes")
            assert includes_str == "../**/*", f"Unexpected FileSet 'includes' value {includes_str}"
            container = [elt for elt in ds_json["distribution"] if elt["@id"] == contained_in][0]
            print(f"FileSet {file_set['@id']} zarr data contained in:")
            pprint(container)
            content_url = container["contentUrl"]
            offset = content_url.rfind("/")
            base_url = content_url[:offset]
            print(f"Reading adata from {base_url}")
            adata = read_dask(base_url)
            rslt[fs_id] = adata
            print("successfully accessed anndata from this fileset")
        else:
            print(f"Unrecognized encodingFormat {encoding_format}")
    return rslt

def get_adata_by_jsonpath(adata, path_str):
    path_segments = []
    current_expr = parse(path_str)
    while hasattr(current_expr, "left") and hasattr(current_expr, "right"):
        print(f"current_expr: {current_expr} ; path_segments: {path_segments}")
        if hasattr(current_expr.right, "fields"):
            path_segments.insert(0, current_expr.right.fields[0])
        elif hasattr(current_expr.right, "index"):
            path_segments.insert(0, current_expr.right.index)
        current_expr = current_expr.left
    print(f"exit: current_expr {current_expr} path_segments {path_segments}")

    if hasattr(current_expr, "fields"):
        path_segments.insert(0, current_expr.fields[0])
    if hasattr(current_expr, "index"):
        path_segments.insert(0, current_expr.index)

    print(f"final: current_expr {current_expr} path_segments {path_segments}")

    cur_obj = getattr(adata, path_segments[0])
    for seg in path_segments[1:]:
        cur_obj = getattr(cur_obj, seg)
    return cur_obj

def read_croissant_json(target_crate: str) -> dict[str, Any]:
    if target_crate.endswith(".json"):
        ds = mlc.Dataset(target_crate)
    elif target_crate.endswith(".zip"):
        with ZipFile(target_crate, "r") as zf:
            if "croissant.json" in zf.namelist():
                with zf.open("croissant.json") as jsonf:
                    json_blob = json.load(jsonf)
                ds = mlc.Dataset(jsonld=json_blob)
            else:
                raise RuntimeError(f"Zip file {target_crate} does not contain croissant.json")
    return ds.metadata.to_json()


def main() -> None:
    """Parse command line and carry out construction of the RO-Crate and Croissant files."""
    parser = argparse.ArgumentParser()
    parser.add_argument("target_crate", help="Croissant or crate zip to upload")
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()
    target_crate = args.target_crate
    debug = args.debug

    if debug:
        LOGGER.setLevel(logging.DEBUG)
        logging.getLogger("requests").setLevel(logging.DEBUG)
        logging.getLogger("urllib3").setLevel(logging.DEBUG)
        #logging.getLogger("croissant_wrapper").setLevel(logging.DEBUG)

    ds_json = read_croissant_json(target_crate)
    pprint(ds_json, depth=2)
    fileset_d = adata_from_filesets(ds_json)

if __name__ == "__main__":
    main()
