import json
import logging
from os import walk
from pprint import pformat, pprint

import mlcroissant as mlc

from api_calls import fetch_entity_info, walk_ancestors

LOGGER = logging.getLogger(__name__)

HUBMAP = "https://hubmapconsortium.org/"


def _own_dag(entity: dict) -> list:
    return (entity.get("ingest_metadata") or {}).get("dag_provenance_list", [])


def is_processed(entity: dict) -> bool:
    """Raw vs processed: `creation_action` is 'Create Dataset Activity' vs 'Central Process'."""
    return "process" in (entity.get("creation_action") or "").lower()


def _protocol_dois(md: dict) -> list[str]:
    dois = []
    for k in ("preparation_protocol_doi", "reagent_prep_protocols_io_doi",
              "section_prep_protocols_io_doi"):
        v = (md.get(k) or "").strip()
        if not v:
            continue
        dois.append(f"https://dx.doi.org/{v}" if v.startswith("10.") else v)
    return dois


def _pipeline_steps(dag_list: list) -> list[dict]:
    return [{}]
"""
    steps, seen = [], set()
    for s in dag_list or []:
        repo = (s.get("origin") or "").strip().replace(".git", "")
        name = repo.rsplit("/", 1)[-1] if repo else (s.get("name") or "")
        commit = (s.get("hash") or "")[:7]
        cwl = s.get("name") or ""
        key = (name, commit, cwl)
        if not name or key in seen:
            continue
        seen.add(key)
        steps.append({"name": name, "repo": repo, "commit": commit, "cwl": cwl})
    return steps
"""

def _acquisition_activity(entity: dict, md: dict) -> dict:
    def agent(name, role=None, org=False):
        n = {"@type": "prov:Organization" if org else "prov:Person", "schema:name": name}
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
        "hubmap:instrument": " ".join(filter(None, [md.get("acquisition_instrument_vendor"),
                                                    md.get("acquisition_instrument_model")])),
        "hubmap:numberOfAntibodies": md.get("number_of_antibodies"),
        "hubmap:numberOfImagingRounds": md.get("number_of_biomarker_imaging_rounds"),
        "hubmap:numberOfChannels": md.get("number_of_channels"),
        "prov:wasAssociatedWith": assoc,
    }
    if md.get("execution_datetime"):
        act["prov:startedAtTime"] = md["execution_datetime"]
    protocols = [{"@type": ["prov:Entity", "schema:CreativeWork"], "@id": d, "prov:role": "protocol"}
                 for d in _protocol_dois(md)]
    if protocols:
        act["prov:used"] = protocols
    return {k: v for k, v in act.items() if v not in (None, "", [])}


def _specimen_chain(entity: dict, recur=0) -> dict:
    if recur > 10:
        LOGGER.warning("Recursion limit reached in _specimen_chain for entity %s", entity.get("hubmap_id"))
        return {}
    def node(anc):
        n = {"@type": "prov:Entity", "@id": HUBMAP + (anc.get("hubmap_id") or ""),
             "schema:name": anc.get("hubmap_id"), "hubmap:entityType": anc.get("entity_type"),
             "hubmap:sampleCategory": anc.get("sample_category")}
        rui = anc.get("rui_location")
        if rui:
            r = json.loads(rui) if isinstance(rui, str) else rui
            n["hubmap:ccfAnnotations"] = r.get("ccf_annotations")
            n["hubmap:dimensions"] = {"x": r.get("x_dimension"), "y": r.get("y_dimension"),
                                      "z": r.get("z_dimension"), "unit": r.get("dimension_units")}
        return {k: v for k, v in n.items() if v not in (None, "", [])}
    order = {"section": 0, "block": 1, "organ": 2}
    return None
    """ while "direct_ancestors" in entity and entity["direct_ancestors"]:
        parent_entity = entity["direct_ancestors"][0]
        ancs.append(entity)
    ancs = [a for a in context.get("ancestors", []) if a.get("entity_type") in ("Sample", "Donor")]
    ancs.sort(key=lambda a: order.get(a.get("sample_category"), 4))
    derived = None
    for anc in reversed(ancs):
        n = node(anc)
        if derived:
            n["prov:wasDerivedFrom"] = derived
        derived = n
    return derived
 """
def _pipeline_activity(dag_list: list) -> dict:
    return {}
"""
    agents = []
    for st in _pipeline_steps(dag_list):
        a = {"@type": ["prov:SoftwareAgent", "schema:SoftwareApplication"],
             "schema:name": st["name"] + (f" [{st['cwl']}]" if st["cwl"] else ""),
             "schema:codeRepository": st["repo"], "hubmap:commit": st["commit"]}
        agents.append({k: v for k, v in a.items() if v})
    act = {"@type": "prov:Activity", "schema:name": "HuBMAP uniform processing pipeline"}
    if agents:
        act["prov:wasAssociatedWith"] = agents
    return act
"""

def build_embedded_provenance(entity, context, md, descendants=None, raw_entity=None, raw_md=None) -> dict:
    """
    PROCESSED subject: wasGeneratedBy its own pipeline; wasDerivedFrom the raw parent
    (which carries the acquisition activity + specimen chain). RAW subject: wasGeneratedBy
    acquisition; wasDerivedFrom specimen chain; + a light forward pointer to processed versions.
    """
    if is_processed(entity) and raw_entity is not None:
        raw_node = {
            "@type": "prov:Entity", "@id": HUBMAP + (raw_entity.get("hubmap_id") or ""),
            "schema:name": raw_entity.get("hubmap_id"),
            "hubmap:datasetType": raw_entity.get("dataset_type"),
            "prov:wasGeneratedBy": _acquisition_activity(raw_entity, raw_md or {}),
            "prov:wasDerivedFrom": _specimen_chain(raw_entity)
        }
        #raw_node = {k: v for k, v in raw_node.items() if v}
        return {"prov:wasGeneratedBy": _pipeline_activity(_own_dag(entity)),
                "prov:wasDerivedFrom": raw_node}
    provo = {"prov:wasGeneratedBy": _acquisition_activity(entity, md)}
    if chain := _specimen_chain(entity):
        provo["prov:wasDerivedFrom"] = chain
    if descendants:
        provo["hubmap:hasProcessedDataset"] = [
            {"@type": "prov:Entity", "@id": HUBMAP + (d.get("hubmap_id") or ""),
             "schema:name": d.get("hubmap_id"), "hubmap:datasetType": d.get("dataset_type")}
            for d in descendants]
    return provo


class CroissantWrapper():
    @classmethod
    def test(cls, entity_dict: dict) -> None:
        def walk_datasets_only(d: dict) -> bool:
            return (d["entity_type"] == "Dataset")
        def anc_summary_str(anc_chain: list) -> str:
            for id, ent, sub_list in anc_chain:
                if sub_list is None:
                    return f"{id} {ent['entity_type']} none"
                else:
                    return f"{id} {ent['entity_type']} {{...}} {'[' + ' '.join(anc_summary_str([elt]) for elt in sub_list) + ']'}"
        test_chain = walk_ancestors(entity_dict, walk_datasets_only)
        LOGGER.info("walk_ancestors for %s: [%s]",
                    entity_dict.get("hubmap_id"),
                    anc_summary_str(test_chain))
        ancestors = entity_dict.get("direct_ancestors", [])
        parent_dict = ancestors[0] if len(ancestors) == 1 else None
        LOGGER.info("Testing CroissantWrapper:\n%s", 
                    pformat(
                        build_embedded_provenance(
                            entity_dict,
                            {}, # context
                            entity_dict.get("metadata"), # md
                            descendants=entity_dict.get("direct_descendants", []),
                            raw_entity=parent_dict,
                            raw_md=parent_dict.get("metadata") if parent_dict else None
                        )
                    ))
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.file_objects = []
        self.record_sets = []

    def add_file(self, file_obj: mlc.FileObject):
        self.file_objects.append(file_obj)

    def add_record_set(self, record_set: mlc.RecordSet):
        self.record_sets.append(record_set)

    def write(self, croissant_filename: str):
        croissant_meta = mlc.Metadata(
            id="croissant-spec",
            name=self.name,
            description=self.description,
            distribution=self.file_objects,
            record_sets=self.record_sets,
            ctx=mlc.Context(
                is_live_dataset=False
            )
        ).to_json()
        croissant_meta["@context"].update({
                "hubmap": "https://hubmapconsortium.org/",
                "mlc": "https://mlcommons.org/",
                "prov": "http://www.w3.org/ns/prov#",
                "schema": "http://schema.org/"
            }),
        with open(croissant_filename, "w", encoding="utf-8") as f:
            json.dump(croissant_meta, f, indent=2)

