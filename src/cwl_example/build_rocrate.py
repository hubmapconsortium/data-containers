#!/bin/env python

from pathlib import Path
from subprocess import run
import bagit
from rocrate.rocrate import ROCrate
from rocrate.model.dataset import Dataset


def main():
    run("cwltool --provenance prov1 hello_world.cwl", shell=True, check=True)
    run(
        "cwltool --provenance prov2 hello_world.cwl --message 'hola'",
        shell=True,
        check=True,
    )
    crate = ROCrate()
    crate.name = "Example ROCrate from CWLTOOL provenance"
    crate.name = "Example ROCrate from CWLTOOL provenance"

    for bag in ["prov1", "prov2"]:
        bag_path = Path(bag)
        try:
            bag = bagit.Bag(bag_path)
            bag_dataset = crate.add_dataset(
                source=bag_path,
                dest_path=bag_path.name,
                properties={
                    "name": f"Dataset Bag: {bag_path.name}",
                    "description": bag.info.get(
                        "Internal-Sender-Description", "No description available"
                    ),
                    "conformsTo": "https://i.am.unsure",
                },
            )
        except (bagit.BagError, bagit.BagValidationError) as excp:
            print(
                f"Skipping {bag_path} because it could not be"
                f"parsed as a bag: {excp}"
            )
            continue

    full_output_dir = Path("rocrate")
    print(f"Writing rocrate to {full_output_dir}")
    crate.write(full_output_dir)


if __name__ == "__main__":
    main()
