#!/usr/bin/env python
"""Utilities for pushing data containers to Hugging Face."""
from __future__ import annotations

import argparse
import os
from collections import defaultdict

import dask.array as da
import dask.dataframe as dd
import pandas as pd
import numpy as np
import scanpy as sc
import zarr
import logging
from zipfile import ZipFile
import json

from typing import Any

from anndata import AnnData
from anndata.experimental import read_dispatched, StorageType
from anndata.io import read_elem

from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec.implementations.zip import ZipFileSystem

import mlcroissant as mlc

from pprint import pformat, pprint

from jsonpath_ng import parse

from dataframe_factories import (
    BaseDataFrameFactory,
    ExpressionMatrixDataFrameFactory,
)

logging.basicConfig(
    level=logging.INFO,
)

LOGGER = logging.getLogger(__name__)


# Temporarily copied from anndata_zarr_tools in the phenocycler-to-hf branch
# until that branch gets merged
class SafeZipFileSystem(ZipFileSystem):
    """
    Define ZipFileSystem with better FileNoteFound behavior.

    zarr 3 treats FileNotFoundError as "key absent"; ZipFileSystem raises KeyError,
    which breaks zarr's probes for optional metadata files.
    """

    def _open(self, path, mode="rb", *args, **kwargs):
        try:
            return super()._open(path, mode, *args, **kwargs)
        except KeyError as e:
            raise FileNotFoundError(path) from e


def create_zip_zarr_store(url: str, token: str=None) -> StorageType:
    """Open a remote zipped zarr file via URL."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    fs = SafeZipFileSystem(
        fo=url,
        target_protocol="https",  # NOT remote_protocol/remote_options: silently ignored
        target_options={"client_kwargs": {"headers": headers}},
    )
    # zarr 3's FsspecStore needs an async filesystem; ZipFileSystem is sync.
    store = zarr.storage.FsspecStore(
        AsyncFileSystemWrapper(fs, asynchronous=True), read_only=True
    )
    return store



def read_dask(store: StorageType) -> AnnData:

    def callback(func, elem_name: str, elem, iospec):
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

    adata = read_dispatched(store, callback=callback)

    return adata

def adata_from_filesets(ds_json: dict[str, Any]):
    rslt = {}
    for file_set in [elt for elt in ds_json["schema:distribution"] if elt["@type"] == "cr:FileSet"]:
        fs_id = file_set["@id"]
        contained_in = file_set["schema:containedIn"]["@id"]
        if (encoding_format := file_set.get("schema:encodingFormat")) == "application/x-zarr":
            container = [elt for elt in ds_json["schema:distribution"] if elt["@id"] == contained_in][0]
            content_url = container["schema:contentUrl"]
            includes_str = file_set.get("includes")
            if content_url.endswith(".zip"):
                assert includes_str == "*/*", f"Unexpected FileSet 'includes' value {includes_str}"
                store = zarr.open(
                    create_zip_zarr_store(content_url, token=os.environ["AUTH_TOK"]),
                    mode="r",
                )
                adata = read_dask(store)
            else:
                assert includes_str == "../**/*", f"Unexpected FileSet 'includes' value {includes_str}"
                offset = content_url.rfind("/")
                base_url = content_url[:offset]
                store = zarr.open(
                        base_url, mode="r",
                        storage_options={"headers": {"Authorization": f"Bearer {os.environ['AUTH_TOK']}"}}
                        )
                adata = read_dask(store)
            rslt[fs_id] = adata
            LOGGER.info(f"successfully accessed anndata from fileset {fs_id}")
        else:
            LOGGER.error(f"Unrecognized encodingFormat {encoding_format} for fileset {fs_id}")
    return rslt


def _get_part(obj: AnnData | dict, target: str) -> AnnData | dict:
    """Works for both an AnnData object and a dict"""
    if hasattr(obj, target):
        return getattr(obj, target)
    else:
        return obj[target]


def get_adata_by_jsonpath(adata: AnnData, path_str: str) -> Any:
    path_segments = []
    current_expr = parse(path_str)
    while hasattr(current_expr, "left") and hasattr(current_expr, "right"):
        if hasattr(current_expr.right, "fields"):
            path_segments.insert(0, current_expr.right.fields[0])
        elif hasattr(current_expr.right, "index"):
            path_segments.insert(0, current_expr.right.index)
        current_expr = current_expr.left

    if hasattr(current_expr, "fields"):
        path_segments.insert(0, current_expr.fields[0])
    if hasattr(current_expr, "index"):
        path_segments.insert(0, current_expr.index)

    cur_obj = _get_part(adata, path_segments[0])
    for seg in path_segments[1:]:
        cur_obj = _get_part(cur_obj, seg)
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
    all_fields_d = {}
    all_field_values_d = {}
    for record_set in ds_json.get("recordSet", []):
        print(f"Handling record set {record_set['schema:name']} {record_set['@id']}:")
        for fld in record_set["field"]:
            assert fld["@id"] not in all_fields_d, f"redundant field {fld['@id']}"
            all_fields_d[fld["@id"]] = fld
    for fld_id in all_fields_d:
        fld = all_fields_d[fld_id]
        if "references" in fld:
            refs = fld["references"]
            for key in refs:
                if key == "field":
                    ref_field_d = refs["field"]
                    for keykey in ref_field_d:
                        if keykey == "@id":
                            assert ref_field_d["@id"] in all_fields_d
                        else:
                            raise RuntimeError(f"Unexpected key {keykey} in references[field] for Field {fld_id}")
                else:
                    raise RuntimeError(f"Unexpected key {key} in references for Field {fld_id}")
        else:
            try:
                assert "source" in fld
                assert "fileSet" in fld["source"] and "@id" in fld["source"]["fileSet"]
                assert "extract" in fld["source"] and "jsonPath" in fld["source"]["extract"]
                print(f"OK ref to {fld_id}")
            except AssertionError as excp:
                pprint(fld)
                raise excp
            value = get_adata_by_jsonpath(
                fileset_d[fld["source"]["fileSet"]["@id"]],
                fld["source"]["extract"]["jsonPath"]
            )
            print(f"{fld_id} yielded {type(value)}")
            all_field_values_d[fld_id] = value

    for fld_id in [elt for elt in all_fields_d if "references" in all_fields_d[elt]]:
        fld = all_fields_d[fld_id]
        ref_field_d = fld["references"]
        all_field_values_d[fld_id] = all_field_values_d[ref_field_d["field"]["@id"]]

    for record_set in ds_json.get("recordSet", []):
        print(f"Handling record set {record_set['schema:name']} {record_set['@id']}:")
        for factory_type in [
            BaseDataFrameFactory,
            ExpressionMatrixDataFrameFactory,
        ]:
            factory = factory_type(record_set["field"], all_field_values_d)
            if factory.test():
                print(f"yes to {factory_type}")
                for df in factory:
                    print(f"{df}")
            else:
                print(f"no to {factory_type}")



if __name__ == "__main__":
    main()
