# Usage
`build_crate_from_dataset.py` expects to find a valid token in the environment in `AUTH_TOK`.  Given the HuBMAP ID of a dataset, it accesses the HuBMAP entity, uuid,
and assets APIs to gather information and writes an RO-Crate describing the dataset to the output directory specified.
```
env AUTH_TOK='<valid token>' python build_crate_from_dataset.py -o /path/to/crate/dir <HuBMAP ID>
```
