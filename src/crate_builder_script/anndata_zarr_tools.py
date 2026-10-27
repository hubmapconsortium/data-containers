"""Provides functions to build the Croissant file."""
import copy
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from pprint import pformat
import os

from anndata.io import read_elem
import mlcroissant as mlc
import zarr
import pandas as pd
import numpy as np
from api_calls import asset_url, entity_iri, fetch_entity_info
from extractors import WrappedEntity
from utils import (
    get_sha256_from_url,
    rel_path_to_id,
    FILESET_KEYWORDS, RECORDSET_KEYWORDS
)

LOGGER = logging.getLogger(__name__)

ANNDATA_ZARR_RE = re.compile(r"(anndata-zarr/.*anndata.zarr)(/.zgroup)")

PANDAS_TO_CROISSANT_TYPES = {
    "int64": mlc.DataType.INTEGER,
    "float64": mlc.DataType.FLOAT,
    "bool": mlc.DataType.BOOL,
    "object": mlc.DataType.TEXT,
    "string": mlc.DataType.TEXT,
}

def anndata_zarr_fs_func(fs_info: dict) -> dict:
    rslt = {key: fs_info[key] for key in FILESET_KEYWORDS}
    LOGGER.info(f"_anndata_zarr_fs_func returning:\n{pformat(rslt)}")
    return rslt

def anndata_zarr_rs_func(rs_info: dict) -> list[dict]:
    id_base = rs_info["id"]
    obs_dtypes = rs_info.get("obs_dtypes", {})
    var_index = rs_info.get("var_index", {})
    X_shape = rs_info.get("X_shape", (-1, -1))
    print(f"TEST2a: {obs_dtypes}")
    print(f"TEST2b: {var_index}")
    print(f"TEST2c: {X_shape}")

    # These first two fields are always present
    field_list = [
        {
            "id": f"{id_base}/cells/cell-id",
            "name": "cell_id",
            "data_types": [mlc.DataType.TEXT],
            "source": mlc.Source(
                file_set=id_base,
                extract=mlc.Extract(json_path="$.obs.index")
            )
        },
        {
            "id": f"{id_base}/cells/expression-vector",
            "name": "expression",
            "data_types": [mlc.DataType.FLOAT],
            "is_array": True,
            "array_shape": f"{X_shape[0]},{X_shape[1]}",
            "source": mlc.Source(
                file_set=id_base,
                extract=mlc.Extract(json_path="$.X")
            )
        },
    ]

    # Add a field for each column in obs
    for col, dtype in obs_dtypes.items():
        if cr_dtype := PANDAS_TO_CROISSANT_TYPES.get(str(dtype)):
            field_list.append({
                "id": f"{id_base}/cells/{col}",
                "name": col,
                "data_types": [cr_dtype],
                "source": mlc.Source(
                    file_set=id_base,
                    extract=mlc.Extract(json_path=f"$.obs.'{col}'")
                )
            })
        else:
            raise RuntimeError(f"zarr obs column {col} type {dtype} cannot be mapped to croissant")

    field_list = [mlc.Field(**elt) for elt in field_list]  # convert to fields
    rslt = {
        "id": f"{id_base}/cells-recordset",
        "name": "cells-recordset",
        "description": "individual cells data",
        "fields": field_list
    }
    LOGGER.info(f"_anndata_zarr_rs_func returning:\n{pformat(rslt)}")
    return [rslt]


def anndata_zarr_detector(ds_uuid: str, rel_path:str) -> dict | None:
    """If rel_path is an anndata_zarr root, return appropriate RecordSet info"""
    if re_match := ANNDATA_ZARR_RE.match(rel_path):
        # Information which directly describes the FileSet and can
        # be used to derive the RecordSets
        anchor_id = rel_path_to_id(re_match.group(1))
        zarr_url = asset_url(ds_uuid, anchor_id)
        print(f"POINT 1: zarr_url = {zarr_url}")
        auth_tok = os.environ["AUTH_TOK"]
        # We can't actually open the X store because it's too large
        # to work with here.  But we need its row and column counts
        # to specify the X array.  We will need to get them from the
        # obs and var stores.
        zarr_obs_store = zarr.open(zarr_url, mode="r", use_consolidated=False,
                               path="obs",
                               storage_options={"headers":{"Authorization": f"Bearer {auth_tok}"}})
        zarr_obs_elem = read_elem(zarr_obs_store)
        zarr_var_store = zarr.open(zarr_url, mode="r", use_consolidated=False,
                               path="var",
                               storage_options={"headers":{"Authorization": f"Bearer {auth_tok}"}})
        zarr_var_elem = read_elem(zarr_var_store)

        # We have no examples of these, but they may be info we
        # ultimately want to capture
        for elt in ["varm", "varp", "obsm", "obsp", "uns"]:
            store = zarr.open(zarr_url, mode="r", use_consolidated=False,
                              path=elt,
                              storage_options={"headers":{"Authorization": f"Bearer {auth_tok}"}})
            elem = read_elem(store)
            if isinstance(elem, pd.DataFrame):
                print(f"TEST {elt} {type(elem)} {len(elem)}")
                if len(elem):
                    LOGGER.warning(f"AnnData Zarr element {elt} (a Dataframe)"
                                   " was detected but not translated")
                    for idx, row in elem.iterrows():
                        print(f"{idx} {type(row)} {row}")
            elif isinstance(elem, dict):
                if elem:
                    LOGGER.warning(f"AnnData Zarr element {elt} (a dict)"
                                   " was detected but not translated")
            else:
                LOGGER.error(f"The AnnData Zarr element {elt} is unexpectedly"
                             f" of type {type(elem)}")

        obs_dtypes = zarr_obs_elem.dtypes
        var_index = zarr_var_elem.index
        print(f"TESTTEST: {zarr_obs_elem.shape[0]} {zarr_var_elem.shape[0]}")
        return {
            "id": f"anndata-zarr-dir-{anchor_id}",
            "name": f"anndata_zarr_directory-{anchor_id}",
            "contained_in": [rel_path_to_id(rel_path)],
            "includes": "../**/*", 
            "encoding_formats": ["application/x-zarr"],
            "fileset_func": anndata_zarr_fs_func,
            "recordset_func": anndata_zarr_rs_func,
            "obs_dtypes": obs_dtypes,
            "var_index": var_index,
            "X_shape": (zarr_obs_elem.shape[0], zarr_var_elem.shape[0])
        }
    else:
        return None


