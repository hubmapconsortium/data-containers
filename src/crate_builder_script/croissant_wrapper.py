"""Provides functions to build the Croissant file."""
import copy
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from pprint import pformat

import mlcroissant as mlc
from api_calls import asset_url, entity_iri, fetch_entity_info
from extractors import WrappedEntity
from utils import get_sha256_from_url

LOGGER = logging.getLogger(__name__)

ANNDATA_ZARR_RE = re.compile(r"(anndata-zarr/.*anndata.zarr)(/.zgroup)")

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

FILESET_KEYWORDS = ["id", "name", "contained_in", "includes", "encoding_formats"]

RECORDSET_KEYWORDS = ["id", "name", "description", "fields"]

def _anndata_zarr_fs_func(fs_info: dict) -> dict:
    rslt = {key: fs_info[key] for key in FILESET_KEYWORDS}
    LOGGER.info(f"_anndata_zarr_fs_func returning:\n{pformat(rslt)}")
    return rslt

def _anndata_zarr_rs_func(rs_info: dict) -> list[dict]:
    id_base = rs_info["id"]
    field_list = [
        {
            "id": f"{id_base}/cells/cell-id",
            "name": "cell_id",
            "data_types": [mlc.DataType.TEXT],
            "source": mlc.Source(
                file_set=id_base,
                extract=mlc.Extract(json_path="$.obs.ID")
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


def detect_recordset_root(rel_path: str) -> dict | None:
    """If rel_path is a fileset root, return appropriate RecordSet info"""
    if re_match := ANNDATA_ZARR_RE.match(rel_path):
        # Information which directly describes the FileSet and can
        # be used to derive the RecordSets
        anchor_id = rel_path_to_id(re_match.group(1))
        return {
            "id": f"anndata-zarr-dir-{anchor_id}",
            "name": f"anndata_zarr_directory-{anchor_id}",
            "contained_in": [rel_path_to_id(rel_path)],
            "includes": "../**/*", 
            "encoding_formats": ["application/x-zarr"],
            "fileset_func": _anndata_zarr_fs_func,
            "recordset_func": _anndata_zarr_rs_func
        }
    return None


def rel_path_to_id(rel_path: str) -> str:
    return rel_path


def _ts_to_iso(ms):
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()
        if ms
        else None
    )


def _creators(entity: dict) -> list[dict]:
    out = []
    for c in entity.get("contributors", []):
        name = (
            c.get("name")
            or f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
        )
        if not name:
            continue
        person = {"name": name}
        orcid = (c.get("orcid_id") or c.get("orcid") or "").strip()
        if orcid:
            person["url"] = (
                orcid if orcid.startswith("http") else f"https://orcid.org/{orcid}"
            )
        out.append(person)
    return out


def _build_citation(entity: WrappedEntity, doi) -> str:
    authors = "; ".join(c["name"] for c in _creators(entity)) or "HuBMAP Consortium"
    year = (_ts_to_iso(entity.get("published_timestamp")) or "")[:4]
    title = entity.get("title") or entity.get("hubmap_id", "")
    keystr = f"HUBMAP_{entity['hubmap_id']}"
    short_doi = (
        doi.replace("doi.org/", "").replace("http://", "").replace("https://", "")
    )
    return (
        "@data{"
        f"{keystr}, "
        f"author={{{authors}}}, "
        f"title={{{title}}}, "
        f"year={{{year}}}, "
        f"version={{{entity.count_versions()}}}, "
        f"doi={short_doi}, "
        f"url={{{doi}}}, "
        "publisher={HuBMAP}}"
    )


def _protocol_dois(md: dict) -> list[str]:
    dois = []
    for k in (
        "preparation_protocol_doi",
        "reagent_prep_protocols_io_doi",
        "section_prep_protocols_io_doi",
    ):
        v = (md.get(k) or "").strip()
        if not v:
            continue
        dois.append(f"https://dx.doi.org/{v}" if v.startswith("10.") else v)
    return dois


def _acquisition_activity(entity: WrappedEntity, md: dict) -> dict:
    def agent(name, role=None, org=False):
        n = {
            "@type": "prov:Organization" if org else "prov:Person",
            "schema:name": name,
        }
        if role:
            n["prov:role"] = role
        return n

    assoc = []
    if md.get("pi"):
        assoc.append(agent(md["pi"], role="principal investigator"))
    if md.get("operator"):
        assoc.append(agent(md["operator"], role="operator"))
    if entity.get("group_name"):
        assoc.append(agent(entity["group_name"], org=True))
    act = {
        "@type": "prov:Activity",
        "schema:name": f"{entity.get('dataset_type', 'assay')} acquisition",
        "hubmap:instrument": " ".join(
            filter(
                None,
                [
                    md.get("acquisition_instrument_vendor"),
                    md.get("acquisition_instrument_model"),
                ],
            )
        ),
        "hubmap:numberOfAntibodies": md.get("number_of_antibodies"),
        "hubmap:numberOfImagingRounds": md.get("number_of_biomarker_imaging_rounds"),
        "hubmap:numberOfChannels": md.get("number_of_channels"),
        "prov:wasAssociatedWith": assoc,
    }
    if md.get("execution_datetime"):
        act["prov:startedAtTime"] = md["execution_datetime"]
    protocols = [
        {
            "@type": ["prov:Entity", "schema:CreativeWork"],
            "@id": d,
            "prov:role": "protocol",
        }
        for d in _protocol_dois(md)
    ]
    if protocols:
        act["prov:used"] = protocols
    return {k: v for k, v in act.items() if v not in (None, "", [])}


def _specimen_chain(entity: WrappedEntity) -> dict:
    def node(anc):
        n = {
            "@type": "prov:Entity",
            "@id": entity_iri(anc),
            "schema:name": anc.get("hubmap_id"),
            "hubmap:entityType": anc.get("entity_type"),
            "hubmap:sampleCategory": anc.get("sample_category"),
        }
        rui = anc.get("rui_location")
        if rui:
            r = json.loads(rui) if isinstance(rui, str) else rui
            n["hubmap:ccfAnnotations"] = r.get("ccf_annotations")
            n["hubmap:dimensions"] = {
                "x": r.get("x_dimension"),
                "y": r.get("y_dimension"),
                "z": r.get("z_dimension"),
                "unit": r.get("dimension_units"),
            }
        return {k: v for k, v in n.items() if v not in (None, "", [])}

    ancs = entity.list_ancestors(omit_test=lambda dct: dct["entity_type"] == "Dataset")
    order = {"section": 0, "block": 1, "organ": 2}
    ancs.sort(key=lambda a: order.get(a.get("sample_category"), 4))
    derived = None
    for anc in reversed(ancs):
        n = node(anc)
        if derived:
            n["prov:wasDerivedFrom"] = derived
        derived = n
    return derived


def _pipeline_activity(entity: WrappedEntity) -> dict:
    agents = []
    for st in entity.pipeline_steps():
        a = {
            "@type": ["prov:SoftwareAgent", "schema:SoftwareApplication"],
            "schema:name": st["name"] + (f" [{st['cwl']}]" if st["cwl"] else ""),
            "schema:codeRepository": st["repo"],
            "hubmap:commit": st["commit"],
        }
        agents.append({k: v for k, v in a.items() if v})
    act = {
        "@type": "prov:Activity",
        "schema:name": "HuBMAP uniform processing pipeline",
    }
    if agents:
        act["prov:wasAssociatedWith"] = agents
    return act


def _build_raw_node(raw_entity: WrappedEntity, raw_md: dict | None) -> dict:
    return {
        "@type": "prov:Entity",
        "@id": entity_iri(raw_entity),
        "schema:name": raw_entity.get("hubmap_id"),
        "hubmap:datasetType": raw_entity.get("dataset_type"),
        "prov:wasGeneratedBy": _acquisition_activity(raw_entity, raw_md or {}),
        "prov:wasDerivedFrom": _specimen_chain(raw_entity),
    }


def build_embedded_provenance(
    entity: WrappedEntity, md: dict | None, descendants: list | None = None
) -> dict:
    """
    Build embedded provenance for the given entity.

    PROCESSED subject: wasGeneratedBy its own pipeline; wasDerivedFrom the raw parent
    (which carries the acquisition activity + specimen chain). RAW subject: wasGeneratedBy
    acquisition; wasDerivedFrom specimen chain; + a light forward pointer to processed versions.
    """
    if entity.is_processed:
        hubmap_id = entity["hubmap_id"]
        ancestor_chain = entity.walk_ancestors(lambda d: d["entity_type"] == "Dataset")
        assert len(ancestor_chain) == 1, "internal error walking ancestors"
        check_id, ignored_entity, ancestors = ancestor_chain[0]
        assert check_id == hubmap_id
        raw_node_l = []
        for anc in ancestors:
            anc_entity = WrappedEntity(anc[1])  # each ancestor is a 3-tuple
            anc_md = anc_entity.get("metadata")
            raw_node_l.append(_build_raw_node(anc_entity, anc_md))
        return {
            "prov:wasGeneratedBy": _pipeline_activity(entity),
            "prov:wasDerivedFrom": (
                raw_node_l[0] if len(raw_node_l) == 1 else raw_node_l
            ),
        }

    provo = {"prov:wasGeneratedBy": _acquisition_activity(entity, md)}
    if chain := _specimen_chain(entity):
        provo["prov:wasDerivedFrom"] = chain
    if descendants:
        provo["hubmap:hasProcessedDataset"] = [
            {
                "@type": "prov:Entity",
                "@id": entity_iri(d),
                "schema:name": d.get("hubmap_id"),
                "hubmap:datasetType": d.get("dataset_type"),
            }
            for d in descendants
        ]
    return provo


class CroissantWrapper:
    """Wraps Croissant generation functionality."""

    @classmethod
    def test(cls, entity_dict: dict) -> None:
        """Provide a test of utility methods."""
        LOGGER.info(
            "Testing CroissantWrapper:\n%s",
            pformat(
                build_embedded_provenance(
                    entity_dict,
                    md=entity_dict.get("metadata"),
                    descendants=entity_dict.get("direct_descendants", []),
                )
            ),
        )

    def __init__(self, name: str, description: str):
        """Construct a CroissantWrapper instance."""
        self.name = name
        self.description = description
        self.file_objects = []
        self.record_sets = []
        self.cite_as_doi = None
        self.date_published = None
        self.license = None
        self.version = None
        self.keywords = None

    def add_file(self, ds_uuid: str, file_info: dict, file_blk: dict | None) -> None:
        """
        Define and describe a single file in the dataset.
        """
        args = {
            "id": rel_path_to_id(file_info["rel_path"]),
            "name": file_info["rel_path"],
            "description": file_info["description"],
            "content_url": asset_url(ds_uuid, file_info["rel_path"]),
        }

        path_ext = Path(file_info["rel_path"]).suffix
        LOGGER.info(f"EDAM lookup {file_info.get('edam_term')} <{path_ext}>")
        if edam := file_info.get("edam_term"):
            if (edam, path_ext) in EDAM_INFO:
                args["encoding_formats"] = [EDAM_INFO[(edam, path_ext)]["mime"]]
            elif (edam, None) in EDAM_INFO:
                args["encoding_formats"] = [EDAM_INFO[(edam, None)]["mime"]]
            elif (None, path_ext) in EDAM_INFO:
                args["encoding_formats"] = [EDAM_INFO[(None, path_ext)]["mime"]]
            else:
                LOGGER.warning(f"Unknown EDAM format {edam} for {pformat(file_info)}")
                args["encoding_formats"] = ["application/octet-stream"]
        else:
            args["encoding_formats"] = ["application/octet-stream"]
        if file_blk:
            args["sha256"] = file_blk["sha256_checksum"]
        else:
            args["sha256"] = get_sha256_from_url(args["content_url"])
        LOGGER.info(f"HELLO {file_info['rel_path']} info:\n{pformat(args)}")
        LOGGER.info(f"file_info is:\n{pformat(file_info)}")
        self.file_objects.append(mlc.FileObject(**args))

    def add_recordset_from_root(self, rs_info: dict) -> None:
        """
        Add one or more recordsets, and a fileset if necessary.
        
        rs_info is a dict returned by detect_recordset_root().
        """
        if fileset_func := rs_info.get("fileset_func"):
            self._add_fileset(fileset_func(rs_info))
        if recordset_func := rs_info.get("recordset_func"):
            rs_info_list = recordset_func(rs_info)
            for rs_info in rs_info_list:
                self._add_recordset(rs_info)

    def _add_fileset(self, fs_info: dict) -> None:
        """Add a FileSet using info returned by detect_recordset_root()."""
        self.file_objects.append(mlc.FileSet(**fs_info))

    def _add_recordset(self, rs_info: dict) -> None:
        """Add a RecordSet."""
        self.record_sets.append(mlc.RecordSet(**rs_info))

    def write(self, croissant_filename: str):
        """Write the Croissant file."""
        entity = WrappedEntity(fetch_entity_info(self.name))
        args = {
            "id": "croissant-spec",
            "name": self.name,
            "description": self.description,
            "distribution": self.file_objects,
            "record_sets": self.record_sets,
            "ctx": mlc.Context(is_live_dataset=False),
        }
        if self.date_published:
            args["date_published"] = self.date_published
        if self.license:
            args["license"] = self.license
        if self.version:
            args["version"] = self.version
        if self.cite_as_doi:
            args["cite_as"] = _build_citation(entity, self.cite_as_doi)
        if self.keywords:
            args["keywords"] = self.keywords
        croissant_meta = mlc.Metadata(**args).to_json()
        croissant_meta["@context"].update(
            {
                "hubmap": "https://hubmapconsortium.org/",
                "mlc": "https://mlcommons.org/",
                "prov": "http://www.w3.org/ns/prov#",
                "schema": "http://schema.org/",
            }
        )
        croissant_meta.update(
            build_embedded_provenance(
                entity,
                md=entity.get("metadata"),  # md
                descendants=entity.get("direct_descendants", []),
            )
        )
        with open(croissant_filename, "w", encoding="utf-8") as f:
            json.dump(croissant_meta, f, indent=2)
