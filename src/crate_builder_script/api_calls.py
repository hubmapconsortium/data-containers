import logging
import os
from pprint import pformat, pprint
from typing import Any

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
