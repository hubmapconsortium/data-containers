import json
import logging
from pprint import pformat

import mlcroissant as mlc
import requests

LOGGER = logging.getLogger(__name__)

class CroissantWrapper():
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
            record_sets=self.record_sets
        )
        with open(croissant_filename, "w", encoding="utf-8") as f:
            json.dump(croissant_meta.to_json(), f, indent=2)

