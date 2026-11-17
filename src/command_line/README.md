# Usage
`build_crate_from_dataset.py` expects to find a valid token in the environment in `AUTH_TOK`.  Given the HuBMAP ID of a dataset, it accesses the HuBMAP entity, uuid,
and assets APIs to gather information and writes an RO-Crate describing the dataset to the output directory specified.
```
env AUTH_TOK='<valid token>' build_crate_from_dataset.py -o /path/to/crate/dir <HuBMAP ID>
```

`push_to_hf.py` expects to find a valid token in the environment
in `AUTH_TOK`.  Given the path to a croissant.json file or a
zipped pair of croissant.json and ro-crate-metadata.json files
like the zipped pairs produced by `build_crate_from_dataset.py` ,
and assuming the host machine is logged into HuggingFace, the
following will push records containing relevant dataset info to
a HuggingFace dataset named for the HuBMAP ID:
```
env AUTH_TOK='<valid token>' push_to_hf.py <file_path>
```
The flags `--debug` (`-d`) and `--dry-run` are supported.