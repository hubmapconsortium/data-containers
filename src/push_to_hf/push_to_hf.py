#!/usr/bin/env python
"""Utilities for pushing data containers to Hugging Face."""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
from pprint import pformat
from typing import Any
from zipfile import ZipFile

import dask.array as da
import mlcroissant as mlc
import yaml
import zarr
from anndata import AnnData
from anndata.experimental import StorageType, read_dispatched
from anndata.io import read_elem
from dataframe_factories import (
    ExpressionMatrixDataFrameFactory,
    SeriesListDataFrameFactory,
)
from datasets import Dataset
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec.implementations.zip import ZipFileSystem
from huggingface_hub import HfApi
from jsonpath_ng import parse

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


def create_zip_zarr_store(url: str, token: str = None) -> StorageType:
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
    """Produce an AnnData from a Zarr store with Dask."""

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


def adata_from_filesets(ds_json: dict[str, Any]) -> dict[str, AnnData]:
    """Build AnnData objects for all filesets."""
    rslt = {}
    for file_set in [
        elt for elt in ds_json["schema:distribution"] if elt["@type"] == "cr:FileSet"
    ]:
        fs_id = file_set["@id"]
        contained_in = file_set["schema:containedIn"]["@id"]
        if (
            encoding_format := file_set.get("schema:encodingFormat")
        ) == "application/x-zarr":
            container = [
                elt
                for elt in ds_json["schema:distribution"]
                if elt["@id"] == contained_in
            ][0]
            content_url = container["schema:contentUrl"]
            includes_str = file_set.get("includes")
            if content_url.endswith(".zip"):
                assert (
                    includes_str == "*/*"
                ), f"Unexpected FileSet 'includes' value {includes_str}"
                store = zarr.open(
                    create_zip_zarr_store(content_url, token=os.environ["AUTH_TOK"]),
                    mode="r",
                )
                adata = read_dask(store)
            else:
                assert (
                    includes_str == "../**/*"
                ), f"Unexpected FileSet 'includes' value {includes_str}"
                offset = content_url.rfind("/")
                base_url = content_url[:offset]
                store = zarr.open(
                    base_url,
                    mode="r",
                    storage_options={
                        "headers": {"Authorization": f"Bearer {os.environ['AUTH_TOK']}"}
                    },
                )
                adata = read_dask(store)
            rslt[fs_id] = adata
            LOGGER.info(f"successfully accessed anndata from fileset {fs_id}")
        else:
            LOGGER.error(
                f"Unrecognized encodingFormat {encoding_format} for fileset {fs_id}"
            )
    return rslt


def _get_part(obj: AnnData | dict, target: str) -> AnnData | dict:
    """Works for both an AnnData object and a dict."""
    if hasattr(obj, target):
        return getattr(obj, target)
    else:
        return obj[target]


def get_adata_by_jsonpath(adata: AnnData, path_str: str) -> Any:
    """Given an AnnData, get a sub-part by JSON path."""
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
    """Read the JSON from croissant.json, even if it is zipped."""
    if target_crate.endswith(".json"):
        ds = mlc.Dataset(target_crate)
    elif target_crate.endswith(".zip"):
        with ZipFile(target_crate, "r") as zf:
            if "croissant.json" in zf.namelist():
                with zf.open("croissant.json") as jsonf:
                    json_blob = json.load(jsonf)
                ds = mlc.Dataset(jsonld=json_blob)
            else:
                raise RuntimeError(
                    f"Zip file {target_crate} does not contain croissant.json"
                )
    return ds.metadata.to_json()


def collect_fields(ds_json: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Collect Field structures from all recordSets."""
    all_fields_d = {}
    for record_set in ds_json.get("recordSet", []):
        for fld in record_set["field"]:
            assert fld["@id"] not in all_fields_d, f"redundant field {fld['@id']}"
            all_fields_d[fld["@id"]] = fld
    return all_fields_d


def collect_field_values(
    all_fields_d: dict[str, dict[str, Any]], fileset_d: dict[str, AnnData]
) -> dict[str, Any]:
    """
    Calculate and collect the values of known fields.

    Fields that get their values from reference are left unresolved.  Some syntax
    checking is applied to make sure the field json implements the expected
    relationships.
    """
    all_field_values_d = {}
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
                            raise RuntimeError(
                                f"Unexpected key {keykey} in references[field] for Field {fld_id}"
                            )
                else:
                    raise RuntimeError(
                        f"Unexpected key {key} in references for Field {fld_id}"
                    )
        else:
            try:
                assert "source" in fld
                assert "fileSet" in fld["source"] and "@id" in fld["source"]["fileSet"]
                assert (
                    "extract" in fld["source"]
                    and "jsonPath" in fld["source"]["extract"]
                )
            except AssertionError as excp:
                LOGGER.error(
                    "Failed assertion on the following field:\n" f"{pformat(fld)}"
                )
                raise excp
            value = get_adata_by_jsonpath(
                fileset_d[fld["source"]["fileSet"]["@id"]],
                fld["source"]["extract"]["jsonPath"],
            )
            LOGGER.debug(f"{fld_id} yielded {type(value)}")
            all_field_values_d[fld_id] = value
    return all_field_values_d


def resolve_references(
    all_fields_d: dict[str, dict[str, Any]],
    all_field_values_d: dict[str, Any],
) -> None:
    """Associate values with reference fields."""
    for fld_id in [elt for elt in all_fields_d if "references" in all_fields_d[elt]]:
        fld = all_fields_d[fld_id]
        ref_field_d = fld["references"]
        all_field_values_d[fld_id] = all_field_values_d[ref_field_d["field"]["@id"]]


def license_to_hf_license(license: str) -> str:
    """
    Attempt to map spdx.org license name to HuggingFace name.

    See https://huggingface.co/docs/hub/repositories-licenses for a full
    list of supported HF license strings.  If this function returns an
    invalid string the call to upload the README information will fail.
    """
    suffix = license[license.rfind("/") + 1 :]
    return suffix.lower()


def build_metadata(ds_json: dict[str, Any], config_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Construct the metadata that goes in the YAML block of the readme.md file."""
    return {
        "description": ds_json["schema:description"],
        "citation": ds_json["citeAs"],
        "license": license_to_hf_license(ds_json["schema:license"]),
        "tags": ds_json["schema:keywords"],
        "configs": config_list
    }


def build_readme(ds_json: dict[str, Any], config_list: list[dict[str, Any]]) -> str:
    """Build a HF-specific README.md from the dataset's croissant JSON."""
    md_info = build_metadata(ds_json, config_list)
    LOGGER.debug(f"Metadata:\n{pformat(md_info)}")

    descr = ds_json["schema:description"].replace("[", r"\[").replace("]", r"\]")
    with io.StringIO() as readme_fo:
        readme_fo.write("---\n")
        yaml.dump(md_info, readme_fo)
        readme_fo.write("---\n")
        readme_fo.write(f"# {ds_json['schema:name']}\n")
        readme_fo.write(descr)
        readme_str = readme_fo.getvalue()
    return readme_str


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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build internal data structures but do not actually upload anything to HF",
    )
    args = parser.parse_args()
    target_crate = args.target_crate
    debug = args.debug
    dry_run = args.dry_run

    if debug:
        LOGGER.setLevel(logging.DEBUG)
        logging.getLogger("requests").setLevel(logging.DEBUG)
        logging.getLogger("urllib3").setLevel(logging.DEBUG)
        # logging.getLogger("datafame_factories").setLevel(logging.DEBUG)

    ds_json = read_croissant_json(target_crate)

    base_name = ds_json["schema:name"]
    repo_id = f"HuBMAP/{base_name}"

    fileset_d = adata_from_filesets(ds_json)
    all_fields_d = collect_fields(ds_json)
    all_field_values_d = collect_field_values(all_fields_d, fileset_d)
    resolve_references(all_fields_d, all_field_values_d)
    config_list = []
    for record_set in ds_json.get("recordSet", []):
        rs_name = record_set["schema:name"]
        rs_id = record_set["@id"]
        rs_transcribed = False
        rs_ds_count = 0
        for factory_type in [
            ExpressionMatrixDataFrameFactory,
            SeriesListDataFrameFactory,
        ]:
            factory = factory_type(record_set["field"], all_field_values_d)
            if factory.test():
                for df in factory:
                    rs_transcribed = True
                    dataset = Dataset.from_pandas(df)
                    count_str = f"- {int(rs_ds_count) + 1}" if rs_ds_count else ""
                    config_name = f"{rs_name}{count_str}"
                    config_list.append({
                        "config_name": config_name,
                        "data_files": [{
                            "split": "train",
                            "path": f"{config_name}/train-*"
                        }]
                    })
                    if dry_run:
                        LOGGER.info(
                            f"Dry run ; skipping push of {repo_id} {config_name}"
                        )
                    else:
                        dataset.push_to_hub(repo_id=repo_id, config_name=config_name)
                        LOGGER.info(
                            f"Pushed {repo_id} {config_name} to HuggingFace hub"
                        )
                    rs_ds_count += 1
        if rs_transcribed:
            LOGGER.info(
                f"{rs_ds_count} datasets were transcribed from RecordSet {rs_name} {rs_id}"
            )
        else:
            LOGGER.info(f"RecordSet {rs_name} {rs_id} was not transcribed")

    readme_str = build_readme(ds_json, config_list)
    if dry_run:
        LOGGER.info("Dry run; not installing the following readme.md:")
        LOGGER.info(f"{readme_str}")
    else:
        api = HfApi()
        api.upload_file(
            path_or_fileobj=io.BytesIO(readme_str.encode("utf-8")),
            path_in_repo="README.md",
            repo_id=repo_id,
            repo_type="dataset",
        )


if __name__ == "__main__":
    main()
