import logging
import os
from pprint import pformat
from typing import Any
from collections.abc import Callable

import requests

LOGGER = logging.getLogger(__name__)

ENTITY_API = "https://entity.api.hubmapconsortium.org"
ASSETS_API = "https://assets.hubmapconsortium.org"
UUID_API = "https://uuid.api.hubmapconsortium.org"

AUTH_TOK = os.environ["AUTH_TOK"]


def fetch_entity_info(target_id: str) -> dict[str, Any]:
    resp = requests.get(
        ENTITY_API + f"/entities/{target_id}",
        headers={"Authorization": f"Bearer {AUTH_TOK}"}
    )
    resp.raise_for_status()
    ds_info = resp.json()
    LOGGER.debug("TOP LEVEL for %s:\n%s", target_id, pformat(ds_info, depth=1))
    LOGGER.debug("INGEST METADATA:\n%s", pformat(ds_info.get("ingest_metadata", {}),
                                                 depth=2))
    LOGGER.debug("METADATA:\n%s", pformat(ds_info.get("metadata", {}), depth=2))
    LOGGER.debug("DIRECT ANCESTORS:\n%s", pformat(ds_info.get("direct_ancestors"), depth=2)) 
    LOGGER.debug("DIRECT ANCESTOR:\n%s", pformat(ds_info.get("direct_ancestor"), depth=2)) 
    if "direct_ancestors" in ds_info:
        first_ancestor = ds_info["direct_ancestors"][0]
    elif "direct_ancestor" in ds_info:
        first_ancestor = ds_info["direct_ancestor"]
    else:
        first_ancestor = {}
    LOGGER.debug("ANCESTOR INGEST MD\n%s", pformat(first_ancestor.get("ingest_metadata", {})))
    LOGGER.debug("ANCESTOR MD\n%s", pformat(first_ancestor.get("metadata", {})))
    return ds_info


def fetch_uuid_files_info(target_id: str) -> dict[str, Any]:
    resp = requests.get(
        UUID_API + f"/{target_id}/files",
        headers={"Authorization": f"Bearer {AUTH_TOK}"},
    )
    resp.raise_for_status()
    # LOGGER.debug("UUID FILES first 10:\n%s", pformat(resp.json()[:10]))
    return resp.json()


def asset_url(uuid: str, rel_path: str) -> str:
    return f"{ASSETS_API}/{uuid}/{rel_path}"


def walk_ancestors(
        entity: dict,
        continue_test: Callable[[dict], bool] = lambda ent: True
    ) -> list[tuple]:
    """
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
        ancs = [walk_ancestors(anc, continue_test)
                for anc in entity.get("direct_ancestors", [])]
        if ancs:
            all_tuples = []
            for sub_list in ancs:
                assert isinstance(sub_list, list)
                all_tuples.extend(sub_list)
            rslt.append((e_id, entity, all_tuples))
        else:
            md = entity.get("metadata", {})
            if "parent_sample_id" in md:
                # The parent is a sample
                samp_id = md["parent_sample_id"]
                samp_entity = fetch_entity_info(samp_id)
                rslt.append((e_id, entity, walk_ancestors(samp_entity, continue_test)))
    elif e_type in ("Sample", "Donor"):
        e_cat = entity.get("sample_category", "UNKNOWN SAMPLE CATEGORY")
        LOGGER.debug(f"walk_ancestors sample category is {e_cat}")
        if "direct_ancestor" not in entity:
            LOGGER.debug(f"walk_ancestors fetching dead-end sample {e_id}")
            entity = fetch_entity_info(e_id)
            LOGGER.debug("walk_ancestors fetch yielded:\n%s",
                         pformat(entity, depth=2))
            LOGGER.debug("walk_ancestors end of walk jump result")
        new_entity = entity.get("direct_ancestor", {})
        if e_type == "Donor":
            rslt.append((e_id, entity, None))
        else:
            rslt.append((e_id, entity, walk_ancestors(new_entity, continue_test)))
    else:
        LOGGER.warning(f"walk_ancestors UNKNOWN ETYPE {e_type} for {e_id}")
    return rslt
