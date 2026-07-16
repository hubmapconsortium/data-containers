import argparse
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

DEFAULT_OUTPUT_PATH = "/tmp/crate_test"

#TARGET_ID = "HBM567.VCBK.562"
#TARGET_ID = "HBM487.HJZB.546"

AUTH_TOK = os.environ["AUTH_TOK"]


def fetch_entity_info(target_id):
    resp = requests.get(ENTITY_API + f"/entities/{target_id}?exclude=direct_ancestors")
    resp.raise_for_status()
    ds_info = resp.json()
    #pprint(ds_info, depth=1)
    return ds_info


def fetch_uuid_files_info(target_id):
    resp = requests.get(UUID_API + f"/{target_id}/files",
                        headers={"Authorization": f"Bearer {AUTH_TOK}"})
    resp.raise_for_status()
    #pprint(resp.json()[:10])
    return resp.json()


def asset_url(uuid, rel_path):
    return f"{ASSETS_API}/{uuid}/{rel_path}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target_id",
                        help="Build an RO-Crate for this published dataset")
    parser.add_argument("--outdir", "-o",
                        help=("Path for the output crate directory. The default is"
                              f" {DEFAULT_OUTPUT_PATH}"),
                        default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    pprint(args)
    target_id = args.target_id
    outdir = args.outdir

    ds_info = fetch_entity_info(target_id)

    uuid_files = fetch_uuid_files_info(target_id)
    blk_idx = {}
    for file_blk in uuid_files:
        blk_idx[file_blk["path"]] = file_blk


    # The dataset DOIs point to the Portal, which is basically a landing page, which is
    # forbidden as the direct link for a dataset under FAIR.  So we can't use the DOI
    # as the crate root dataset id.
    crate = ROCrate()

    if "doi_url" in ds_info:
        doi_url = ds_info["doi_url"]
        crate.root_dataset["identifier"] = doi_url
        crate.root_dataset["sameAs"] = doi_url

    crate.root_dataset["name"] = target_id
    crate.root_dataset["description"] = ds_info["title"]
    crate.root_dataset["datePublished"] = str(datetime
                                              .fromtimestamp(ds_info["published_timestamp"]//1000)
                                              .astimezone(timezone.utc)
                                              )

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
        # This is a derived dataset- include only data products and qa_qc files
        for fl in ds_info["files"]:
            if fl["is_data_product"] or fl["is_qa_qc"]:
                print(f"Adding {fl['rel_path']}")
                crate.add_file(asset_url(ds_info['uuid'], fl['rel_path']),
                               validate_url=True)
            else:
                print(f"{fl['rel_path']} is not a data product")
    else:
        for fl_blk in blk_idx.values():
            crate.add_file(asset_url(ds_info['uuid'], fl_blk['path']),
                           validate_url=True)
    crate.write(outdir)

if __name__ == "__main__":
    main()
