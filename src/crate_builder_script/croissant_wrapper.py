"""Provides functions to build the Croissant file."""
import json
import logging
from pprint import pformat

import mlcroissant as mlc
from api_calls import HUBMAP, asset_url, fetch_entity_info
from extractors import WrappedEntity

LOGGER = logging.getLogger(__name__)

EDAM_INFO = {
    "EDAM_1.24.format_3727": {"desc": "tiff", "mime": "image/tiff"},
    "EDAM_1.24.format_3464": {"desc": "json", "mime": "application/json"},
    "EDAM_1.24.format_3508": {"desc": "pdf", "mime": "application/pdf"},
    "EDAM_1.24.format_3590": {"desc": "hdf5", "mime": "application/x-hdf5"},
    "EDAM_1.24.format_3987": {"desc": "zip", "mime": "application/zip"},
    "EDAM_1.24.format_3752": {"desc": "csv", "mime": "text/csv"},
    "EDAM_1.24.format_3755": {"desc": "tsv", "mime": "text/tab-separated-values"},
    "EDAM_1.24.format_3790": {"desc": "h5ad (anndata)", "mime": "application/x-hdf5"},
    "EDAM_1.24.format_3915": {"desc": "zarr", "mime": "application/vnd.zarr"},
    "EDAM_1.24.format_4006": {
        "desc": "zarr (spatialdata)",
        "mime": "application/vnd.zarr",
    },
    "EDAM_1.24.data_3671": {"desc": "plain text", "mime": "text/plain"},
    "EDAM_1.24.format_3916": {"desc": "adjacency matrix", "mime": "text/plain"},
}


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
            "@id": HUBMAP + (anc.get("hubmap_id") or ""),
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
        assert len(ancestors) == 1, f"Dataset {hubmap_id} has too many ancestors"
        raw_entity = WrappedEntity(ancestors[0][1])
        raw_md = raw_entity.get("metadata")
        raw_node = {
            "@type": "prov:Entity",
            "@id": HUBMAP + (raw_entity.get("hubmap_id") or ""),
            "schema:name": raw_entity.get("hubmap_id"),
            "hubmap:datasetType": raw_entity.get("dataset_type"),
            "prov:wasGeneratedBy": _acquisition_activity(raw_entity, raw_md or {}),
            "prov:wasDerivedFrom": _specimen_chain(raw_entity),
        }
        return {
            "prov:wasGeneratedBy": _pipeline_activity(entity),
            "prov:wasDerivedFrom": raw_node,
        }
    provo = {"prov:wasGeneratedBy": _acquisition_activity(entity, md)}
    if chain := _specimen_chain(entity):
        provo["prov:wasDerivedFrom"] = chain
    if descendants:
        provo["hubmap:hasProcessedDataset"] = [
            {
                "@type": "prov:Entity",
                "@id": HUBMAP + (d.get("hubmap_id") or ""),
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
        self.cite_as = None
        self.date_published = None
        self.license = None
        self.version = None

    def add_file(self, ds_uuid: str, file_info: dict, file_blk: dict | None) -> None:
        """Define and describe a single file in the dataset."""
        args = {
            "id": file_info["rel_path"],
            "name": file_info["rel_path"],
            "description": file_info["description"],
            "content_url": asset_url(ds_uuid, file_info["rel_path"]),
        }
        if edam := file_info.get("edam_term"):
            if edam in EDAM_INFO:
                args["encoding_formats"] = [EDAM_INFO[edam]["mime"]]
            else:
                LOGGER.warning(f"Unknown EDAM format {edam} for {pformat(file_info)}")
                args["encoding_formats"] = ["application/octet-stream"]
        else:
            args["encoding_formats"] = ["application/octet-stream"]
        if file_blk:
            args["sha256"] = file_blk["sha256_checksum"]
        self.file_objects.append(mlc.FileObject(**args))

    # def add_record_set(self, record_set: mlc.RecordSet):
    #     self.record_sets.append(record_set)

    def write(self, croissant_filename: str):
        """Write the Croissant file."""
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
        if self.cite_as:
            args["cite_as"] = self.cite_as
        croissant_meta = mlc.Metadata(**args).to_json()
        croissant_meta["@context"].update(
            {
                "hubmap": "https://hubmapconsortium.org/",
                "mlc": "https://mlcommons.org/",
                "prov": "http://www.w3.org/ns/prov#",
                "schema": "http://schema.org/",
            }
        )
        entity = WrappedEntity(fetch_entity_info(self.name))
        croissant_meta.update(
            build_embedded_provenance(
                entity,
                md=entity.get("metadata"),  # md
                descendants=entity.get("direct_descendants", []),
            )
        )
        with open(croissant_filename, "w", encoding="utf-8") as f:
            json.dump(croissant_meta, f, indent=2)
