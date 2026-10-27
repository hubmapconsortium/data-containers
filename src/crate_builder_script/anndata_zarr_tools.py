"""Provides functions to build the Croissant file."""
import logging
import re
from pprint import pformat
import os

from anndata.io import read_elem
import mlcroissant as mlc
import zarr
import pandas as pd
from api_calls import asset_url
from utils import (
    rel_path_to_id,
    FILESET_KEYWORDS
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

    # Build the row index RecordSet
    row_field = mlc.Field(
        id=f"{id_base}/cells/cell-id",
        name="cell_id",
        data_types=[mlc.DataType.TEXT],
        source=mlc.Source(
            file_set=id_base,
            extract=mlc.Extract(json_path="$.obs.index")
        )
    )
    field_list = [row_field]
    row_index_rs = {
        "id": f"{id_base}/cell-id-index",
        "name": "cell-id-index",
        "description": "individual cell ids",
        "fields": field_list
    }

    # Build the column index RecordSet
    col_field = mlc.Field(
        id=f"{id_base}/cells/channel-id",
        name="channel_id",
        data_types=[mlc.DataType.TEXT],
        source=mlc.Source(
            file_set=id_base,
            extract=mlc.Extract(json_path="$.var.index")
        )
    )
    field_list = [col_field]
    col_index_rs = {
        "id": f"{id_base}/channel-index",
        "name": "cell-index",
        "description": "individual channel ids",
        "fields": field_list
    }

    # Build the RecordSet for X, referencing the indices
    field_list = [
        {
            "id": f"{id_base}/cells/expression-matrix",
            "name": "expression-matrix",
            "data_types": [mlc.DataType.FLOAT],
            "is_array": True,
            "array_shape": f"{X_shape[0]},{X_shape[1]}",
            "source": mlc.Source(
                file_set=id_base,
                extract=mlc.Extract(json_path="$.X")
            )
        },
        {
            "id": f"{id_base}/cells/expression-rows",
            "name": "expression-rows",
            "description": "expression matrix row IDs",
            "data_types": [mlc.DataType.TEXT],
            "references": row_field
        },
        {
            "id": f"{id_base}/cells/expression-columns",
            "name": "expresion-columns",
            "description": "expression matrix column IDs",
            "data_types": [mlc.DataType.TEXT],
            "references": col_field
        }
    ]
    field_list = [mlc.Field(**elt) for elt in field_list]  # convert to fields
    X_rs = {
        "id": f"{id_base}/expression-matrix",
        "name": "expression-matrix",
        "description": "expression data matrix",
        "fields": field_list
    }

    # Guild the Record of fields representing the obs data
    field_list = []
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
    field_list.append(
        {
            "id": f"{id_base}/cells/obs-rows",
            "name": "obs-rows",
            "description": "observation matrix row IDs",
            "data_types": [mlc.DataType.TEXT],
            "references": row_field
        },
    )
    field_list = [mlc.Field(**elt) for elt in field_list]  # convert to fields
    obs_rs = {
        "id": f"{id_base}/cells-obs",
        "name": "cells-obs",
        "description": "individual cells obs data",
        "fields": field_list
    }

    rslt = [row_index_rs, col_index_rs, X_rs, obs_rs]
    LOGGER.info(f"_anndata_zarr_rs_func returning:\n{pformat(rslt)}")
    return rslt


def anndata_zarr_detector(ds_uuid: str, rel_path:str) -> dict | None:
    """If rel_path is an anndata_zarr root, return appropriate RecordSet info"""
    if re_match := ANNDATA_ZARR_RE.match(rel_path):
        # Information which directly describes the FileSet and can
        # be used to derive the RecordSets
        anchor_id = rel_path_to_id(re_match.group(1))
        zarr_url = asset_url(ds_uuid, anchor_id)
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
                if len(elem):
                    LOGGER.warning(f"AnnData Zarr element {elt} (a Dataframe)"
                                   " was detected but not translated")
            elif isinstance(elem, dict):
                if elem:
                    LOGGER.warning(f"AnnData Zarr element {elt} (a dict)"
                                   " was detected but not translated")
            else:
                LOGGER.error(f"The AnnData Zarr element {elt} is unexpectedly"
                             f" of type {type(elem)}")

        obs_dtypes = zarr_obs_elem.dtypes
        var_index = zarr_var_elem.index
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


