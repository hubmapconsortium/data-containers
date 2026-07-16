import requests
from pprint import pprint, pformat
from pathlib import Path
import json
import os
import rocrate
from rocrate.rocrate import ROCrate
from rocrate.model.contextentity import ContextEntity
import bagit
from datetime import datetime, timezone

ENTITY_API = "https://entity.api.hubmapconsortium.org"
ASSETS_API = "https://assets.hubmapconsortium.org"
UUID_API = "https://uuid.api.hubmapconsortium.org"

#TARGET_ID = "HBM567.VCBK.562"
TARGET_ID = "HBM487.HJZB.546"

AUTH_TOK = os.environ["AUTH_TOK"]

resp = requests.get(ENTITY_API + f"/entities/{TARGET_ID}?exclude=direct_ancestors")

resp.raise_for_status()
ds_info = resp.json()

pprint(ds_info, depth=1)

headers = {"Authorization": f"Bearer {AUTH_TOK}"}
resp = requests.get(UUID_API + f"/{TARGET_ID}/files", headers=headers)

resp.raise_for_status()
pprint(resp.json()[:10])
blk_idx = {}
for file_blk in resp.json():
    blk_idx[file_blk["path"]] = file_blk

# for fl in ds_info["files"]:
#     if fl["is_data_product"]:
#         if fl["rel_path"] in blk_idx:
#             pprint(fl)
#             print(" -> ")
#             pprint(blk_idx[fl["rel_path"]])
#         else:
#             print(f"{fl['rel_path']} has no match")

if "doi_url" in ds_info:
    doi_url = ds_info["doi_url"]
    crate = ROCrate(root_dataset_id=doi_url)
    crate.root_dataset["identifier"] = doi_url
    crate.root_dataset["sameAs"] = doi_url
else:
    crate = ROCrate()
crate.root_dataset["name"] = TARGET_ID
crate.root_dataset["description"] = ds_info["title"]
crate.root_dataset["datePublished"] = str(datetime.fromtimestamp(ds_info["published_timestamp"]//1000)
                                          .astimezone(timezone.utc))
license_props = {
    "@type": "CreativeWork",
    "name": "Creative Commons Atribution 4.0 International",
    "description": ("The Creative Commons Atribution 4.0 International"
                    " license allows for reuse, remixing, and"
                    " redistribution as long as attribution is"
                    " provided to the creator."),
    "url": "https://spdx.org/licenses/CC-BY-4.0"
}
license_entity = ContextEntity(crate, identifier=license_props["url"],
                               properties=license_props)
crate.add(license_entity)
crate.root_dataset["license"] = license_entity

if "files" in ds_info:
    # Include only data products and qa_qc files
    for fl in ds_info["files"]:
        if fl["is_data_product"] or fl["is_qa_qc"]:
            url = f"https://assets.hubmapconsortium.org/{ds_info['uuid']}/{fl['rel_path']}"
            crate.add_file(url, validate_url=True)
        else:
            print(f"{fl['rel_path']} is not a data product")
else:
    for fl_blk in blk_idx.values():
        url = f"https://assets.hubmapconsortium.org/{ds_info['uuid']}/{fl_blk['path']}"
        crate.add_file(url, validate_url=True)
crate.write("/tmp/crate_test")
