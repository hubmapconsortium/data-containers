import argparse
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pprint import pformat, pprint
from typing import Any, List
from tempfile import TemporaryDirectory

# from rocrate.model.contextentity import ContextEntity
# from rocrate.model.person import Person
# from rocrate.model.dataset import Dataset
# from rocrate.model.computerlanguage import ComputerLanguage
from rocrate.model import (
    ContextEntity,
    Person,
    Dataset,
    ComputerLanguage,
    SoftwareApplication
)
from rocrate.rocrate import ROCrate

import api_calls
from croissant_wrapper import CroissantWrapper

logging.basicConfig(
    level=logging.INFO,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_OUTPUT_PATH = "/tmp/crate_test"
CROISSANT_FILENAME = "croissant.json"

# Externally defined identifiers
NIH_URI = "https://ror.org/01cwqze88"
ORCID_URI = "https://orcid.org"
OBOLIB_URI = "http://purl.obolibrary.org/obo"

###############
# Notes-
# - count_versions() is essentially untested, for lack of an example
# - croissant cite_as uses the DOI, and that only gets set for primary datasets. Do we
#   want to reference the primary dataset's DOI as the derived dataset's cite_as?
# - Some EDAM codes, e.g. 3916 (adjacency matrix), are not sufficient to specify
#   the mime type to associate with a file.  Should we use extensions instead? Some
#   specialization would be lost.
# - Writing a croissant for a file in an unpublished dataset results in an error at
#   validation time because the file block information from uuid-api has not yet been
#   set so the sha256 code is not known.
# - unpublished examples:
#   TARGET_ID = "HBM567.VCBK.562"
#   TARGET_ID = "HBM487.HJZB.546"  # primary dataset
# - published examples:
#   TARGET_ID = "HBM866.VMBK.952"
#   TARGET_ID = "HBM473.QLDT.264"  # primary dataset
###############


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


def count_versions(ds_info: dict) -> int:
    if "previous_revision_uuid" in ds_info:
        return (
            count_versions(api_calls.fetch_entity_info(ds_info["previous_revision_uuid"]))
            + 1
        )
    else:
        return 1


def build_derived_prov(ds_info: dict, crate: ROCrate) -> ContextEntity:
    prov_chain = api_calls.walk_ancestors(
        ds_info,
        lambda d: d["entity_type"] == "Dataset"
    )
    assert len(prov_chain) == 1
    assert len(prov_chain[0]) == 3 
    hubmap_id, parent_chain = prov_chain[0][0], prov_chain[0][2]
    parent_id_list = []
    for tuple in parent_chain:
        parent_id, parent_info = tuple[0:2]
        crate.add(Dataset(
            crate,
            parent_info["doi_url"],
            properties={
                "name": parent_id,
                "description": parent_info["description"]
            }
        ))
        parent_id_list.append(parent_info["doi_url"])
    hubmap_org = crate.add(ContextEntity(
        crate,
        api_calls.HUBMAP_ORG_ENTITY,
        properties={
            "@type": "Organization",
            "name": "HuBMAP Consortium",
            "url": api_calls.HUBMAP_ORG_ENTITY
        }
    ))
    agent = crate.add(SoftwareApplication(
        crate,
        "HuBMAP Process",
        properties={"parentOrganization": {"@id": hubmap_org.id}}
    ))
    python_lang = crate.add(ComputerLanguage(  # TODO this is surely wrong here
        crate,
        identifier="https://python.org",
        properties={"name": "Python", "version": "3.11", "url": "https://python.org"}
    ))
    workflow_file = crate.add_file(
        "https://github.com/hubmapconsortium/data-containers/blob/85441770eafe7da487b35d76112ec099d7b5b8f7/src/crate_builder_script/build_crate_from_dataset.py",
        properties={
            "@type": ["File", "SoftwareSourceCode", "ComputationalWorkflow"],
            "name":"build_crate_from_dataset.py",
            "description": "this should be the dag description. But how to reference CWLs?",
            "programmingLanguage": {"@id": python_lang.id}
        }
    )
    crate.mainEntity = workflow_file
    props = {
        "@id": "#some_workflow",
        "@type": "CreateAction",
        "name": "the-create-action",
        "startTime": datetime.now().isoformat(),
        "endTime": datetime.now().isoformat(),
        "agent": {"@id": agent.id},
        "instrument": {"@id": workflow_file.id},
        "object": [{"@id": this_id} for this_id in parent_id_list],
        "result": [{"@id": "./"}],  # the target dataset
        "actionStatus": "CompletedActionStatus"
    }
    return ContextEntity(crate, identifier=props["@id"], properties=props)


def build_profiles(crate: ROCrate) -> tuple:
    """
    Build ContextElements for several profiles needed to describe a workflow.
    """
    base_crate_ctx_id = f"https://w3id.org/ro/crate/{crate.version}"
    crate_profile = crate.add(ContextEntity(
        crate,
        base_crate_ctx_id,
        properties={
            "@type": ["CreativeWork", "Profile"],
            "name": "RO-Crate Profile",
            "version": crate.version
        }
    ))
    proc_profile = crate.add(ContextEntity(
        crate,
        "https://w3id.org/ro/wfrun/process/0.5",
        properties={
            "@type": ["CreativeWork", "Profile"],
            "name": "Process Run Crate Profile",
            "version": "0.5"
        }
    ))
    wf_profile = crate.add(ContextEntity(
        crate,
        "https://w3id.org/ro/wfrun/workflow/0.5",
        properties={
            "@type": ["CreativeWork", "Profile"],
            "name": "Workflow Run Crate Profile",
            "version": "0.5",
            # "isProfileOf": {"@id": proc_profile.id}
        }
    ))
    wfc_profile = crate.add(ContextEntity(
        crate,
        "https://w3id.org/workflowhub/workflow-ro-crate/1.0",
        properties={
            "@type": ["CreativeWork", "Profile"],
            "name": "Workflow Run RO-Crate",
            "version": "1.0"
        }
    ))
    return (crate_profile, proc_profile, wf_profile, wfc_profile)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "target_id", help="Build an RO-Crate for this dataset"
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
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--include_all_files",
        action="store_true",
        help="Include all files, even if some are not data product or qa resources"
    )
    args = parser.parse_args()
    target_id = args.target_id
    outdir = args.outdir
    debug = args.debug
    include_all_files = args.include_all_files

    if debug:
        LOGGER.setLevel(logging.DEBUG)
        logging.getLogger("requests").setLevel(logging.DEBUG)
        logging.getLogger("urllib3").setLevel(logging.DEBUG)
        logging.getLogger("api_calls").setLevel(logging.DEBUG)
        logging.getLogger("croissant_wrapper").setLevel(logging.DEBUG)
    ds_info = api_calls.fetch_entity_info(target_id)

    uuid_files = api_calls.fetch_uuid_files_info(target_id)
    blk_idx = {}
    for file_blk in uuid_files:
        blk_idx[file_blk["path"]] = file_blk

    # The dataset DOIs point to the Portal, which is basically a landing page, which is
    # forbidden as the direct link for a dataset under FAIR.  So we can't use the DOI
    # as the crate root dataset id.
    # The version needs to be 1.1 to avoid compatibility problems with the workflow
    # profile that seem to exist for crate profile 1.2.
    crate = ROCrate(version="1.1")

    for profile in build_profiles(crate):
        crate.root_dataset.append_to("conformsTo", {"@id": profile.id})

    crate.metadata.extra_contexts.append("https://w3id.org/ro/terms/workflow-run/context")

    wrapped_croissant = CroissantWrapper(target_id, ds_info["title"])

    crate.root_dataset["name"] = target_id
    crate.root_dataset["description"] = ds_info["title"]
    if "doi_url" in ds_info:
        doi_url = ds_info["doi_url"]
        crate.root_dataset["identifier"] = doi_url
        crate.root_dataset["sameAs"] = doi_url
        wrapped_croissant.cite_as = doi_url

    if "published_timestamp" in ds_info:
        date_published = str(
            datetime.fromtimestamp(ds_info["published_timestamp"] // 1000).astimezone(
                timezone.utc
            )
        )
        crate.root_dataset["datePublished"] = date_published
        wrapped_croissant.date_published = date_published

    license_entity = build_license_entity(crate)
    crate.root_dataset["license"] = crate.add(license_entity)
    wrapped_croissant.license = license_entity.properties()["url"]

    crate.root_dataset["funder"] = crate.add(build_funder_entity(crate))

    ds_version = count_versions(ds_info)
    crate.root_dataset["version"] = ds_version
    wrapped_croissant.version = ds_version

    if contributors := ds_info.get("contributors"):
        crate.add(build_pi_entity(crate))
        ent_l = build_contributors(crate, contributors)
        [crate.add(ent) for ent in ent_l]
        crate.root_dataset["contributor"] = ent_l

    if api_calls.is_processed(ds_info):
        crate.add(build_derived_prov(ds_info, crate))

    if "files" in ds_info:
        # This is a derived dataset- include only data products and qa_qc files
        for fl in ds_info["files"]:
            if fl["is_data_product"] or fl["is_qa_qc"] or include_all_files:
                LOGGER.debug(f"Adding {fl['rel_path']}")
                crate.add_file(
                    api_calls.asset_url(ds_info["uuid"], fl["rel_path"]),
                    validate_url=True
                )
                wrapped_croissant.add_file(ds_info["uuid"],
                                           fl, blk_idx.get(fl["rel_path"]))
            else:
                LOGGER.debug(f"{fl['rel_path']} is not a data product")
    else:
        for fl_blk in blk_idx.values():
            crate.add_file(
                api_calls.asset_url(ds_info["uuid"], fl_blk["path"]),
                validate_url=True
            )
            # We have no descriptive info for these files, so it's hard
            # to see how we could add them to the Croissant object

    tmpdir = TemporaryDirectory()

    if not os.path.isdir(outdir):
        os.makedirs(outdir, exist_ok=True)
    wrapped_croissant.write(os.path.join(tmpdir.name, CROISSANT_FILENAME))
    crate.add(ContextEntity(
        crate,
        "http://mlcommons.org/croissant/1.0",
        properties={
            "@type": ["CreativeWork", "Profile"],
            "name": "MLCommons Croissant Format Specification",
            "version": "1.0",
            "url": "https://docs.mlcommons.org/croissant/docs/crossant-spec-1.0.html"
        }
    ))
    croissant_crate_file = crate.add_file(
        os.path.join(tmpdir.name, CROISSANT_FILENAME),
        properties={
            "name": "Croissant Metadata Descriptor",
            "description": "Machine learning data-loading configurations for this dataset.",
            "encodingFormat": "application/ld+json",
            "conformsTo": {"@id": "http://mlcommons.org/croissant/1.0"}
        }
    )

    crate.write_zip(os.path.join(outdir, f"{target_id}_crate.zip"))
    tmpdir.cleanup()

if __name__ == "__main__":
    main()
