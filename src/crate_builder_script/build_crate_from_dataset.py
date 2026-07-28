import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from pprint import pformat, pprint
from typing import Any, List

import bagit
import requests
import rocrate
from rocrate.model.contextentity import ContextEntity
from rocrate.model.person import Person
from rocrate.rocrate import ROCrate

ENTITY_API = "https://entity.api.hubmapconsortium.org"
ASSETS_API = "https://assets.hubmapconsortium.org"
UUID_API = "https://uuid.api.hubmapconsortium.org"

DEFAULT_OUTPUT_PATH = "/tmp/crate_test"

# Externally defined identifiers
NIH_URI = "https://ror.org/01cwqze88"
ORCID_URI = "https://orcid.org"
OBOLIB_URI = "http://purl.obolibrary.org/obo"

# TARGET_ID = "HBM567.VCBK.562"
# TARGET_ID = "HBM487.HJZB.546"

AUTH_TOK = os.environ["AUTH_TOK"]


def fetch_entity_info(target_id: str) -> dict[str, Any]:
    # resp = requests.get(ENTITY_API + f"/entities/{target_id}?exclude=direct_ancestors")
    resp = requests.get(ENTITY_API + f"/entities/{target_id}")
    resp.raise_for_status()
    ds_info = resp.json()
    print("TOP LEVEL")
    pprint(ds_info, depth=1)
    print("INGEST METADATA")
    pprint(ds_info.get("ingest_metadata", {}))
    print("DIRECT ANCESTORS")
    pprint(ds_info["direct_ancestors"], depth=2)
    return ds_info


def fetch_uuid_files_info(target_id: str) -> dict[str, Any]:
    resp = requests.get(
        UUID_API + f"/{target_id}/files",
        headers={"Authorization": f"Bearer {AUTH_TOK}"},
    )
    resp.raise_for_status()
    # pprint(resp.json()[:10])
    return resp.json()


def asset_url(uuid: str, rel_path: str) -> str:
    return f"{ASSETS_API}/{uuid}/{rel_path}"


def build_funder_entity(crate: ROCrate) -> ContextEntity:
    funder_props = {
        "@id": NIH_URI,
        "@type": "Organization",
        "name": "US National Institutes of Health",
        "identifier": NIH_URI,
    }
    return ContextEntity(crate, identifier=NIH_URI, properties=funder_props)


def build_license_entity(crate: ROCrate) -> ContextEntity:
    license_props = {
        "@type": "CreativeWork",
        "name": "Creative Commons Atribution 4.0 International",
        "description": (
            "The Creative Commons Atribution 4.0 International"
            " license allows for reuse, remixing, and"
            " redistribution as long as attribution is"
            " provided to the creator."
        ),
        "url": "https://spdx.org/licenses/CC-BY-4.0",
    }
    return ContextEntity(
        crate, identifier=license_props["url"], properties=license_props
    )


def build_pi_entity(crate: ROCrate) -> ContextEntity:
    props = {
        "@id": "#role-principal-investigator",
        "@type": "Role",
        "roleName": "Principal Investigator",
        "description": "Responsible for overall scientific direction and oversight.",
        "url": f"{OBOLIB_URI}/OBI_0000103",
    }
    return ContextEntity(crate, identifier=props["@id"], properties=props)


def build_contact_entity(crate: ROCrate) -> ContextEntity:
    props = {
        "@id": "#role-contact",
        "@type": "Role",
        "roleName": "ContactRepresentative",
        "description": (
            "A role inhering in a person who represents an institution,"
            " organization, or service provider and realized when"
            " communication is directed at them about the entity they"
            " represent."
        ),
        "url": f"{OBOLIB_URI}/OBI_0001687",
    }
    return ContextEntity(crate, identifier=props["@id"], properties=props)


def build_ia_entity(crate: ROCrate) -> ContextEntity:
    props = {
        "@id": "#role-investigative-agent",
        "@type": "Role",
        "roleName": "InvestigativeAgent",
        "description": (
            "A role borne by an entity and that is realized"
            " in a process that is part of an investigation"
            " in which an objective is achieved. These processes"
            " include, among others: planning, overseeing,"
            " funding, reviewing."
        ),
        "url": f"{OBOLIB_URI}/OBI_0000202",
    }
    return ContextEntity(crate, identifier=props["@id"], properties=props)


def build_contributors(crate: ROCrate, contributors: List[dict]) -> List[ContextEntity]:
    ent_l = []
    role_d = {}
    role_list_d = defaultdict(list)
    for contrib in contributors:
        props = {
            "@type": "Person",
            "@id": f"{ORCID_URI}/{contrib['orcid']}",
            "name": contrib["display_name"],
        }
        person = Person(crate, identifier=props["@id"], properties=props)
        add_to_entities = True
        for match_key, builder in [
            ("is_principal_investigator", build_pi_entity),
            ("is_contact", build_contact_entity),
            ("is_operator", build_ia_entity),
        ]:
            if contrib[match_key].lower() == "yes":
                entity = role_d.get(match_key) or builder(crate)
                role_d.setdefault(match_key, entity)
                role_list_d[match_key].append(crate.add(person))
                add_to_entities = False
        if add_to_entities:
            ent_l.append(person)
    for match_key in role_list_d:
        role_d[match_key]["contributor"] = role_list_d[match_key]
        ent_l.append(role_d[match_key])
    return ent_l


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "target_id", help="Build an RO-Crate for this published dataset"
    )
    parser.add_argument(
        "--outdir",
        "-o",
        help=(
            "Path for the output crate directory. The default is"
            f" {DEFAULT_OUTPUT_PATH}"
        ),
        default=DEFAULT_OUTPUT_PATH,
    )
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
    crate.root_dataset["datePublished"] = str(
        datetime.fromtimestamp(ds_info["published_timestamp"] // 1000).astimezone(
            timezone.utc
        )
    )

    crate.root_dataset["license"] = crate.add(build_license_entity(crate))
    crate.root_dataset["funder"] = crate.add(build_funder_entity(crate))
    if contributors := ds_info.get("contributors"):
        crate.add(build_pi_entity(crate))
        ent_l = build_contributors(crate, contributors)
        [crate.add(ent) for ent in ent_l]
        crate.root_dataset["contributor"] = ent_l

    if "files" in ds_info:
        # This is a derived dataset- include only data products and qa_qc files
        for fl in ds_info["files"]:
            if fl["is_data_product"] or fl["is_qa_qc"]:
                print(f"Adding {fl['rel_path']}")
                crate.add_file(
                    asset_url(ds_info["uuid"], fl["rel_path"]), validate_url=True
                )
            else:
                # print(f"{fl['rel_path']} is not a data product")
                pass
    else:
        for fl_blk in blk_idx.values():
            crate.add_file(
                asset_url(ds_info["uuid"], fl_blk["path"]), validate_url=True
            )
    crate.write(outdir)


if __name__ == "__main__":
    main()
