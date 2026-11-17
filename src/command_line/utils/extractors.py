"""Define utilities to extract relevant subsets of entity data."""

import json
import logging
from collections.abc import Callable
from pprint import pformat
from typing import Any

from api_calls import fetch_entity_info

LOGGER = logging.getLogger(__name__)

# HuBMAP two-letter organ codes -> (label, UBERON term)
ORGAN_MAP = {
    "LY": ("Lymph Node", "UBERON:0000029"),
    "SP": ("Spleen", "UBERON:0002106"),
    "TH": ("Thymus", "UBERON:0002370"),
    "BM": ("Bone Marrow", "UBERON:0002371"),
    "LK": ("Kidney (left)", "UBERON:0004538"),
    "RK": ("Kidney (right)", "UBERON:0004539"),
    "HT": ("Heart", "UBERON:0000948"),
    "LI": ("Large Intestine", "UBERON:0000059"),
    "SI": ("Small Intestine", "UBERON:0002108"),
    "LL": ("Lung (left)", "UBERON:0002168"),
    "RL": ("Lung (right)", "UBERON:0002167"),
    "LV": ("Liver", "UBERON:0002107"),
    "PA": ("Pancreas", "UBERON:0001264"),
    "BL": ("Bladder", "UBERON:0001255"),
    "SK": ("Skin", "UBERON:0002097"),
    "LF": ("Fallopian tube (left)", "UBERON:0001303"),
    "RF": ("Fallopian tube (right)", "UBERON:0001302"),
}


def walk_ancestors(
    entity: dict, continue_test: Callable[[dict], bool] = lambda ent: True
) -> list[tuple]:
    """
    Extract the chain of ancestors for a dataset.

    Given an entity dictionary, return a list of tuples.  Each tuple has
    the form:
    (hubmap_id entity_dict list-of-ancestors)
    where list-of-ancestors is None or a list of tuples of the same form.

    continue_test takes an entity dict as a parameter and returns True if
    the descent should continue to the children of that entity, False otherwise.
    """
    rslt = []
    if not continue_test(entity):
        return rslt
    e_type = entity.get("entity_type")
    e_id = entity.get("hubmap_id")
    LOGGER.debug(f"walk_ancestors {e_id} {e_type}")
    if e_type == "Dataset":
        if not entity.get("direct_ancestors"):
            entity = WrappedEntity(fetch_entity_info(e_id))
            assert entity["hubmap_id"] == e_id
            assert entity["entity_type"] == e_type
            assert entity[
                "direct_ancestors"
            ], f"{e_id} is a dataset with no direct ancestors"
        ancs = [
            walk_ancestors(anc, continue_test)
            for anc in entity.get("direct_ancestors", [])
        ]
        all_tuples = []
        for sub_list in ancs:
            assert isinstance(sub_list, list)
            all_tuples.extend(sub_list)
        rslt.append((e_id, entity, all_tuples))
    elif e_type in ("Sample", "Donor"):
        e_cat = entity.get("sample_category", "UNKNOWN SAMPLE CATEGORY")
        LOGGER.debug(f"walk_ancestors sample category is {e_cat}")
        if "direct_ancestor" not in entity:
            LOGGER.debug(f"walk_ancestors fetching dead-end sample {e_id}")
            entity = fetch_entity_info(e_id)
            LOGGER.debug("walk_ancestors fetch yielded:\n%s", pformat(entity, depth=2))
            LOGGER.debug("walk_ancestors end of walk jump result")
        new_entity = entity.get("direct_ancestor", {})
        if e_type == "Donor":
            rslt.append((e_id, entity, None))
        else:
            rslt.append((e_id, entity, walk_ancestors(new_entity, continue_test)))
    else:
        LOGGER.warning(f"walk_ancestors UNKNOWN ETYPE {e_type} for {e_id}")
    return rslt


def listify(ancestor_chain: list, omit_test: Callable[[dict], bool]) -> list:
    """Transform the output of walk_ancestors() to a list."""
    assert len(ancestor_chain) == 1, "listify must start on a 1-tuple chain"
    hubmap_id, entity_dict, ancestors = ancestor_chain[0]
    rslt = []
    if not omit_test(entity_dict):
        rslt.append(entity_dict)
    if ancestors:
        for anc in ancestors:
            rslt.extend(listify([anc], omit_test))
    return rslt


def is_processed(entity: dict) -> bool:
    """
    Distinguished processed datasets from raw (primary) datasets.

    Raw vs processed: `creation_action` is 'Create Dataset Activity' vs 'Central Process'.
    """
    return "process" in entity.get("creation_action", "").lower()


def _own_dag(entity: dict) -> list:
    return entity.get("ingest_metadata", {}).get("dag_provenance_list", [])


def pipeline_steps(entity: dict) -> list[dict]:
    """Return a list of dicts describing the steps by which a derived dataset is created."""
    dag_list = _own_dag(entity)
    steps, seen = [], set()
    for s in dag_list or []:
        repo = s.get("origin", "").strip().replace(".git", "")
        name = repo.rsplit("/", 1)[-1] if repo else s.get("name", "")
        commit = s.get("hash", "")[:7]
        cwl = s.get("name", "")
        key = (name, commit, cwl)
        if not name or key in seen:
            continue
        seen.add(key)
        steps.append({"name": name, "repo": repo, "commit": commit, "cwl": cwl})
    return steps


class WrappedEntity:
    """Provide a convenient wrapper for entity information."""

    def __init__(self, entity: dict):
        """Construct a WrappedEntity."""
        self._entity = entity

    def get(self, key: Any, default=None) -> Any:
        """Pass references to the get method to the internal dict."""
        return self._entity.get(key, default)

    def __getitem__(self, key: Any) -> Any:
        """Implement square brackets for the wrapper class."""
        return self._entity[key]

    def __contains__(self, key: Any) -> bool:
        """Implement 'in' for the wrapper class."""
        return key in self._entity

    def walk_ancestors(
        self, continue_test: Callable[[dict], bool] = lambda ent: True
    ) -> list[tuple]:
        """
        Walk the ancestors of the entity.

        Return a list of tuples of the form:
        (hubmap_id entity_dict list-of-ancestors)
        where list-of-ancestors is None or a list of tuples of the same form.

        continue_test takes an entity dict as a parameter and returns True if
        the descent should continue to the children of that entity, False otherwise.
        """
        return walk_ancestors(self._entity, continue_test)

    def list_ancestors(
        self,
        continue_test: Callable[[dict], bool] = lambda ent: True,
        omit_test: Callable[[dict], bool] = lambda end: False,
    ) -> list:
        """Return ancestor information in an expanded, non-recursive list."""
        return listify(self.walk_ancestors(continue_test), omit_test)

    @property
    def is_processed(self):
        """True if this a processed dataset, as opposed to a raw (primary) dataset."""
        return is_processed(self._entity)

    def pipeline_steps(self) -> list[dict]:
        """Return the pipeline steps of the wrapped entity."""
        return pipeline_steps(self._entity)

    def count_versions(self) -> int:
        """Return a version number for the wrapped entity."""
        if "previous_revision_uuid" in self:
            prev_ent = WrappedEntity(fetch_entity_info(self["previous_revision_uuid"]))
            return prev_ent.count_versions() + 1
        else:
            return 1

    def _organ(self, ancs: list[dict]) -> tuple:
        for anc in ancs:
            if anc.get("organ"):
                return ORGAN_MAP.get(anc["organ"], (anc["organ"], None))
        return None, None

    @staticmethod
    def _assay_info_kwds(ancs: list) -> list[str]:
        rslt = []
        for anc in ancs:
            md = anc.get("metadata", {})
            for v in [
                anc.get("dataset_type"),
                md.get("assay_category"),
                md.get("analyte_class"),
            ]:
                if v and v not in rslt:
                    rslt.append(v)
        return rslt

    def keywords(self) -> list[str]:
        """Return a list of keywords appropriate to the dataset."""
        kws: list[str] = []
        ancs = self.list_ancestors()
        kws.extend(self._assay_info_kwds(ancs))
        organ_label, uberon = self._organ(ancs)
        if organ_label:
            kws.append(organ_label)
            if uberon:
                kws.append(uberon)
        for anc in ancs:
            rui = anc.get("rui_location")
            if rui:
                r = json.loads(rui) if isinstance(rui, str) else rui
                for term in r.get("ccf_annotations", []) or []:
                    short = (
                        "UBERON:" + term.rsplit("_", 1)[-1]
                        if "UBERON" in term
                        else term
                    )
                    if short not in kws:
                        kws.append(short)
                break
        kws += ["Homo sapiens", "NCBITaxon:9606", "HuBMAP"]
        LOGGER.debug(f"KEYWORDS for {self['hubmap_id']}:\n{pformat(kws)}")
        return kws
