"""Provides functions to build the Croissant file."""
import logging
import os
import re
from pprint import pformat
from typing import Any

import mlcroissant as mlc
import pandas as pd
import zarr
from anndata.io import read_elem
from api_calls import asset_url
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec.implementations.zip import ZipFileSystem
from utils import FILESET_KEYWORDS, rel_path_to_id, safe_id

LOGGER = logging.getLogger(__name__)

ANNDATA_ZARR_RE = re.compile(r"(anndata-zarr/.*anndata.zarr)(/.zgroup)")
ANNDATA_ZARR_ZIP_RE = re.compile(r"(anndata-zarr/.*anndata.zarr.zip)")

PANDAS_TO_CROISSANT_TYPES = {
    "int64": mlc.DataType.INTEGER,
    "uint8": mlc.DataType.INTEGER,
    "float64": mlc.DataType.FLOAT,
    "bool": mlc.DataType.BOOL,
    "object": mlc.DataType.TEXT,
    "string": mlc.DataType.TEXT,
}


#
# Copied from hubmapconsortium/portal-visualization utils.py
# many thanks to Nick Akhmetov
#
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


def open_zip_zarr_store(url, token=None):
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


def anndata_zarr_fs_func(fs_info: dict) -> dict:
    """Generate a FileSet for anndata_zarr."""
    rslt = {key: fs_info[key] for key in FILESET_KEYWORDS}
    LOGGER.info(f"_anndata_zarr_fs_func returning:\n{pformat(rslt)}")
    return rslt


def anndata_zarr_rs_func(rs_info: dict) -> list[dict]:
    """Generate a RecordSet for anndata_zarr."""
    id_base = rs_info["id"]
    obs_dtypes = rs_info.get("obs_dtypes", {})
    X_shape = rs_info.get("X_shape", (-1, -1))

    # Build the row index RecordSet
    row_field = mlc.Field(
        id=safe_id(id_base, "/cells/cell-id"),
        name="cell_id",
        data_types=[mlc.DataType.TEXT],
        source=mlc.Source(
            file_set=id_base, extract=mlc.Extract(json_path="$.obs.index")
        ),
    )
    field_list = [row_field]
    row_index_rs = {
        "id": safe_id(id_base, "/cell-id-index"),
        "name": "cell-id-index",
        "description": "individual cell ids",
        "fields": field_list,
    }

    # Build the column index RecordSet
    col_field = mlc.Field(
        id=safe_id(id_base, "/cells/channel-id"),
        name="channel_id",
        data_types=[mlc.DataType.TEXT],
        source=mlc.Source(
            file_set=id_base, extract=mlc.Extract(json_path="$.var.index")
        ),
    )
    field_list = [col_field]
    col_index_rs = {
        "id": safe_id(id_base, "/channel-index"),
        "name": "cell-index",
        "description": "individual channel ids",
        "fields": field_list,
    }

    # Build the RecordSet for X, referencing the indices
    field_list = [
        {
            "id": safe_id(id_base, "/cells/expression-matrix"),
            "name": "expression-matrix",
            "data_types": [mlc.DataType.FLOAT],
            "is_array": True,
            "array_shape": f"{X_shape[0]},{X_shape[1]}",
            "source": mlc.Source(
                file_set=id_base, extract=mlc.Extract(json_path="$.X")
            ),
        },
        {
            "id": safe_id(id_base, "/cells/expression-rows"),
            "name": "expression-rows",
            "description": "expression matrix row IDs",
            "data_types": [mlc.DataType.TEXT],
            "references": row_field,
        },
        {
            "id": safe_id(id_base, "/cells/expression-columns"),
            "name": "expresion-columns",
            "description": "expression matrix column IDs",
            "data_types": [mlc.DataType.TEXT],
            "references": col_field,
        },
    ]
    field_list = [mlc.Field(**elt) for elt in field_list]  # convert to fields
    X_rs = {
        "id": safe_id(id_base, "/expression-matrix"),
        "name": "expression-matrix",
        "description": "expression data matrix",
        "fields": field_list,
    }

    # Guild the Record of fields representing the obs data
    field_list = []
    for col, dtype in obs_dtypes.items():
        if cr_dtype := PANDAS_TO_CROISSANT_TYPES.get(str(dtype)):
            field_list.append(
                {
                    "id": safe_id(id_base, f"/cells/{col}"),
                    "name": col,
                    "data_types": [cr_dtype],
                    "source": mlc.Source(
                        file_set=id_base,
                        extract=mlc.Extract(json_path=f"$.obs.'{col}'"),
                    ),
                }
            )
        else:
            raise RuntimeError(
                f"zarr obs column {col} type {dtype} cannot be mapped to croissant"
            )
    field_list.append(
        {
            "id": safe_id(id_base, "/cells/obs-rows"),
            "name": "obs-rows",
            "description": "observation matrix row IDs",
            "data_types": [mlc.DataType.TEXT],
            "references": row_field,
        },
    )
    field_list = [mlc.Field(**elt) for elt in field_list]  # convert to fields
    obs_rs = {
        "id": safe_id(id_base, "/cells-obs"),
        "name": "cells-obs",
        "description": "individual cells obs data",
        "fields": field_list,
    }

    rslt = [row_index_rs, col_index_rs, X_rs, obs_rs]
    LOGGER.info(f"_anndata_zarr_rs_func returning:\n{pformat(rslt)}")
    return rslt


def test_zarr_element_not_empty(key: str, elem: Any) -> None:
    """Raise a warning/error if a zarr element is unexpectedly not empty."""
    if isinstance(elem, pd.DataFrame):
        if len(elem):
            LOGGER.warning(
                f"AnnData Zarr element {key} (a Dataframe)"
                " was detected but not translated"
            )
    elif isinstance(elem, dict):
        if elem:
            LOGGER.warning(
                f"AnnData Zarr element {key} (a dict)"
                " was detected but not translated"
            )
    else:
        LOGGER.error(
            f"The AnnData Zarr element {key} is unexpectedly" f" of type {type(elem)}"
        )


def anndata_zarr_detector(ds_uuid: str, rel_path: str) -> dict | None:
    """If rel_path is an anndata_zarr root, return appropriate RecordSet info."""
    re_match_fs = ANNDATA_ZARR_RE.match(rel_path)
    re_match_zip = ANNDATA_ZARR_ZIP_RE.match(rel_path)
    re_match_any = re_match_fs or re_match_zip
    if re_match_any:
        # Information which directly describes the FileSet and can
        # be used to derive the RecordSets
        anchor_id = rel_path_to_id(re_match_any.group(1))
        zarr_url = asset_url(ds_uuid, anchor_id)
        auth_tok = os.environ["AUTH_TOK"]
        if re_match_fs:
            zarr_obs_store = zarr.open(
                zarr_url,
                mode="r",
                use_consolidated=False,
                path="obs",
                storage_options={"headers": {"Authorization": f"Bearer {auth_tok}"}},
            )
            zarr_obs_elem = read_elem(zarr_obs_store)
            zarr_var_store = zarr.open(
                zarr_url,
                mode="r",
                use_consolidated=False,
                path="var",
                storage_options={"headers": {"Authorization": f"Bearer {auth_tok}"}},
            )
            zarr_var_elem = read_elem(zarr_var_store)
            include_str = "../**/*"
            # We have no examples of the following, but they may be info we
            # ultimately want to capture
            for elt in ["varm", "varp", "obsm", "obsp", "uns"]:
                store = zarr.open(
                    zarr_url,
                    mode="r",
                    use_consolidated=False,
                    path=elt,
                    storage_options={
                        "headers": {"Authorization": f"Bearer {auth_tok}"}
                    },
                )
                elem = read_elem(store)
                test_zarr_element_not_empty(elt, elem)
        elif re_match_zip:
            zarr_store = open_zip_zarr_store(zarr_url, token=auth_tok)
            zarr_group = zarr.open_group(zarr_store, mode="r", use_consolidated=False)
            zarr_obs_elem = read_elem(zarr_group["obs"])
            zarr_var_elem = read_elem(zarr_group["var"])
            include_str = "*/*"
            # We have no examples of the following, but they may be info we
            # ultimately want to capture
            for elt in ["varm", "varp", "obsm", "obsp", "uns"]:
                if elt in zarr_group:
                    elem = read_elem(zarr_group[elt])
                    test_zarr_element_not_empty(elt, elem)
        else:
            raise RuntimeError("Internal Error opening remote zarr")

        obs_dtypes = zarr_obs_elem.dtypes
        var_index = zarr_var_elem.index
        return {
            "id": safe_id(f"anndata-zarr-dir-{anchor_id}"),
            "name": f"anndata_zarr_directory-{anchor_id}",
            "contained_in": [rel_path_to_id(rel_path)],
            "includes": include_str,
            "encoding_formats": ["application/x-zarr"],
            "fileset_func": anndata_zarr_fs_func,
            "recordset_func": anndata_zarr_rs_func,
            "obs_dtypes": obs_dtypes,
            "var_index": var_index,
            "X_shape": (zarr_obs_elem.shape[0], zarr_var_elem.shape[0]),
        }
    else:
        return None
