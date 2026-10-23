"""Provides functions to build the Croissant file."""
import copy
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from pprint import pformat

import mlcroissant as mlc
import zarr
from api_calls import asset_url, entity_iri, fetch_entity_info
from extractors import WrappedEntity
from utils import get_sha256_from_url, FILESET_KEYWORDS, RECORDSET_KEYWORDS

LOGGER = logging.getLogger(__name__)

ANNDATA_ZARR_RE = re.compile(r"(anndata-zarr/.*anndata.zarr)(/.zgroup)")

def anndata_zarr_fs_func(fs_info: dict) -> dict:
    rslt = {key: fs_info[key] for key in FILESET_KEYWORDS}
    LOGGER.info(f"_anndata_zarr_fs_func returning:\n{pformat(rslt)}")
    return rslt

def anndata_zarr_rs_func(rs_info: dict) -> list[dict]:
    id_base = rs_info["id"]
    obs_cols = rs_info.get("obs_columns")
    print(f"POINT 3: obs_cols {obs_cols}")
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
            "source": mlc.Source(
                file_set=id_base,
                extract=mlc.Extract(json_path="$.X")
            )
        },
        {
            "id": f"{id_base}/cells/cell-k-means-mean-expression",
            "name": "cell-k-means-mean-expression",
            "data_types": [mlc.DataType.FLOAT],
            "source": mlc.Source(
                file_set=id_base,
                extract=mlc.Extract(json_path="$.obs.'Cell K-Means [Mean] Expression'")
            )
        },
    ]
    field_list = [mlc.Field(**elt) for elt in field_list]  # convert to fields
    rslt = {
        "id": f"{id_base}/cells-recordset",
        "name": "cells-recordset",
        "description": "individual cells data",
        "fields": field_list
    }
    LOGGER.info(f"_anndata_zarr_rs_func returning:\n{pformat(rslt)}")
    return [rslt]


def anndata_zarr_detector(ds_uuid: str, rel_path:str) -> dict | None
    """If rel_path is an anndata_zarr root, return appropriate RecordSet info"""
    if re_match := ANNDATA_ZARR_RE.match(rel_path):
        # Information which directly describes the FileSet and can
        # be used to derive the RecordSets
        anchor_id = rel_path_to_id(re_match.group(1))
        zarr_url = asset_url(ds_uuid, anchor_id)
        print(f"POINT 1: zarr_url = {zarr_url}")
        import os
        from anndata.io import read_elem
        auth_tok = os.environ["AUTH_TOK"]
        zarr_obs_store = zarr.open(zarr_url, mode="r", use_consolidated=False,
                               zarr_format=2, path="obs",
                               storage_options={"headers":{"Authorization": f"Bearer {auth_tok}"}})
        zarr_elem = read_elem(zarr_obs_store)
        print(f"POINT 1b: {zarr_elem.columns}")
        print(f"POINT 1c: {list(zarr_elem.columns)}"),
        obs_cols = list(zarr_elem.columns)
        print(f"POINT 2: cols {obs_cols}")
        return {
            "id": f"anndata-zarr-dir-{anchor_id}",
            "name": f"anndata_zarr_directory-{anchor_id}",
            "contained_in": [rel_path_to_id(rel_path)],
            "includes": "../**/*", 
            "encoding_formats": ["application/x-zarr"],
            "fileset_func": anndata_zarr_fs_func,
            "recordset_func": anndata_zarr_rs_func,
            "obs_columns": obs_cols
        }
    else:
        return None


