import argparse
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pprint import pformat, pprint
from typing import List
from tempfile import TemporaryDirectory

from rocrate.model import (
    ContextEntity,
    Person,
    Dataset,
    ComputerLanguage,
    SoftwareApplication
)
from rocrate.rocrate import ROCrate

from api_calls import (
    fetch_entity_info,
    fetch_uuid_files_info,
    asset_url,
    HUBMAP_ORG_ENTITY
)
from extractors import WrappedEntity
from croissant_wrapper import CroissantWrapper

logging.basicConfig(
    level=logging.INFO,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_OUTPUT_PATH = "/tmp/crate_test"
CROISSANT_FILENAME = "croissant.json"

APACHE_ORG_ENTITY = "https://apache.org"

# Externally defined identifiers
NIH_URI = "https://ror.org/01cwqze88"
ORCID_URI = "https://orcid.org"
OBOLIB_URI = "http://purl.obolibrary.org/obo"

AIRFLOW_VERSION = "2.11.0"
CWL_VERSION = "v1.1"

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
# - We need the Airflow version to write a valid provenance block, but
#   AFAIK it is not maintained in the entity information. Likewise, some
#   datasets were produced with python versions before 3.11.  Likewise, I've
#   hard-coded the CWL version, but ours is actually modified- the CWL language
#   definition entity should point at ours rather than at default CWL.
# - the workflow_instance lacks start and end dates
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


def build_hubmap_org_entity(crate: ROCrate) -> ContextEntity:
    hubmap_org = crate.add(ContextEntity(
        crate,
        HUBMAP_ORG_ENTITY,
        properties={
            "@type": "Organization",
            "name": "HuBMAP Consortium",
            "url": HUBMAP_ORG_ENTITY
        }
    ))
    return hubmap_org


def build_primary_prov(ds_entity: WrappedEntity, crate: ROCrate) -> ContextEntity:
    """
    This is a dummy routine for now.  It needs enough structure to define
    crate.mainEntity.
    """

    hubmap_org = build_hubmap_org_entity(crate)
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
        "object": [],
        "result": [{"@id": "./"}],  # the target dataset
        "actionStatus": "CompletedActionStatus"
    }
    return ContextEntity(crate, identifier=props["@id"], properties=props)


def build_cwl_entity(crate: ROCrate) -> ContextEntity:
    props = {
        "@id": "https://w3id.org/workflowhub/workflow-ro-crate#cwl",
        "@type": "ComputerLanguage",
        "name": "Common Workflow Language",
        "alternateName": "CWL",
        "identifier": { "@id": f"https://w3id.org/cwl/{CWL_VERSION}/" },
        "url": "https://www.commonwl.org/"
    }
    return crate.add(ContextEntity(
        crate, identifier=props["@id"], properties=props
    ))


def build_step_entity(step: dict, idx: int, cwl_entity: ContextEntity,
                      crate: ROCrate) -> ContextEntity:
    pos = idx + 1
    id_str = f"#step_{pos}"
    hash = step["commit"]
    return crate.add(ContextEntity(
        crate,
        id_str,
        properties={
            "position": pos,
            "@type": "HowToStep",
            "name": step["cwl"],
            "description": step["name"],
            "version": hash,
            "url": step["repo"],
            "codeRepository": step["repo"],
            "programmingLanguage": cwl_entity.id
        }   
    ))


def build_derived_prov(ds_entity: WrappedEntity, crate: ROCrate) -> ContextEntity:
    prov_chain = ds_entity.walk_ancestors(lambda d: d["entity_type"] == "Dataset")
    assert len(prov_chain) == 1
    assert len(prov_chain[0]) == 3 
    parent_chain = prov_chain[0][2]
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
    hubmap_org = build_hubmap_org_entity(crate)
    apache_org = crate.add(ContextEntity(
        crate,
        APACHE_ORG_ENTITY,
        properties={
            "@type": "Organization",
            "name": "Apache Software Foundation",
            "url": APACHE_ORG_ENTITY
        }
    ))
    airflow = crate.add(SoftwareApplication(
        crate,
        "Apache Airflow",
        properties={
            "version": AIRFLOW_VERSION,
            "description": ("Apache Airflow - A platform to programmatically author,"
                            " schedule, and monitor workflows"),
            "publisher": {"@id": apache_org.id}
        }
    ))
    python_lang = crate.add(ComputerLanguage(  # TODO this is surely wrong here
        crate,
        identifier="https://python.org",
        properties={"name": "Python", "version": "3.11", "url": "https://python.org"}
    ))
    cwl_entity = build_cwl_entity(crate)
    all_steps = ds_entity.pipeline_steps()
    assert all_steps[0]["name"] == "ingest-pipeline" and not all_steps[0]["cwl"]
    ingest_pipeline_info = all_steps[0]
    hash = ingest_pipeline_info["commit"]
    cwl_steps = all_steps[1:]
    assert all(step["cwl"] for step in cwl_steps), "Found a step which is not CWL?"
    step_list = [build_step_entity(step, idx, cwl_entity, crate)
                 for idx, step in enumerate(cwl_steps)]
    workflow = crate.add(ContextEntity(
        crate,
        "#workflow",
        properties={
            "@type": ["ComputationalWorkflow", "SoftwareApplication"],
            "name":"ingest-pipeline dag workflow",
            "description": "Processing steps implemeneted by an ingest-pipeline DAG",
            "steps": step_list,
            "version": hash,
            "url": ingest_pipeline_info["repo"],
            "codeRepository": ingest_pipeline_info["repo"],
            "downloadUrl": f"{ingest_pipeline_info['repo']}/archive/{hash}.zip",
            "publisher": {"@id": hubmap_org.id},
            "programmingLanguage": {"@id": python_lang.id},
            "softwareRequirements": {"@id": airflow.id}
        }
    ))
    #crate.mainEntity = workflow
    props = {
        "@id": "#workflow_instance",
        "@type": "CreateAction",
        "name": "the-create-action",
        "startTime": datetime.now().isoformat(),  # TODO: do I have these values?
        "endTime": datetime.now().isoformat(),
        "agent": {"@id": workflow.id},
        "instrument": {"@id": workflow.id},
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
    # return (crate_profile, proc_profile, wf_profile, wfc_profile)
    return (crate_profile, proc_profile)


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
    ds_entity = WrappedEntity(fetch_entity_info(target_id))

    uuid_files = fetch_uuid_files_info(target_id)
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

    wrapped_croissant = CroissantWrapper(target_id, ds_entity["title"])

    crate.root_dataset["name"] = target_id
    crate.root_dataset["description"] = ds_entity["title"]
    if "doi_url" in ds_entity:
        doi_url = ds_entity["doi_url"]
        crate.root_dataset["identifier"] = doi_url
        crate.root_dataset["sameAs"] = doi_url
        wrapped_croissant.cite_as = doi_url

    if "published_timestamp" in ds_entity:
        date_published = str(
            datetime.fromtimestamp(ds_entity["published_timestamp"] // 1000).astimezone(
                timezone.utc
            )
        )
        crate.root_dataset["datePublished"] = date_published
        wrapped_croissant.date_published = date_published

    license_entity = build_license_entity(crate)
    crate.root_dataset["license"] = crate.add(license_entity)
    wrapped_croissant.license = license_entity.properties()["url"]

    crate.root_dataset["funder"] = crate.add(build_funder_entity(crate))

    ds_version = ds_entity.count_versions()
    crate.root_dataset["version"] = ds_version
    wrapped_croissant.version = ds_version

    if contributors := ds_entity.get("contributors"):
        crate.add(build_pi_entity(crate))
        ent_l = build_contributors(crate, contributors)
        [crate.add(ent) for ent in ent_l]
        crate.root_dataset["contributor"] = ent_l

    if ds_entity.is_processed:
        crate.add(build_derived_prov(ds_entity, crate))
    else:
        crate.add(build_primary_prov(ds_entity, crate))

    if "files" in ds_entity:
        # This is a derived dataset- include only data products and qa_qc files
        for fl in ds_entity["files"]:
            if fl["is_data_product"] or fl["is_qa_qc"] or include_all_files:
                LOGGER.debug(f"Adding {fl['rel_path']}")
                crate.add_file(
                    asset_url(ds_entity["uuid"], fl["rel_path"]),
                    validate_url=True
                )
                wrapped_croissant.add_file(ds_entity["uuid"],
                                           fl, blk_idx.get(fl["rel_path"]))
            else:
                LOGGER.debug(f"{fl['rel_path']} is not a data product")
    else:
        for fl_blk in blk_idx.values():
            crate.add_file(
                asset_url(ds_entity["uuid"], fl_blk["path"]),
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
