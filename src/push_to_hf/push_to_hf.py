#!/usr/bin/env python
"""Utilities for pushing data containers to Hugging Face."""
from __future__ import annotations

import argparse
import os

import dask.array as da
import scanpy as sc
import zarr
import logging

from anndata.experimental import read_dispatched, write_dispatched
from anndata.io import read_elem

import mlcroissant as mlc

from pprint import pformat

from jsonpath_ng import parse

logging.basicConfig(
    level=logging.INFO,
)

LOGGER = logging.getLogger(__name__)

def main() -> None:
    """Parse command line and carry out construction of the RO-Crate and Croissant files."""
    parser = argparse.ArgumentParser()
    parser.add_argument("target_crate", help="Croissant or crate zip to upload")
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()
    target_crate = args.target_crate
    debug = args.debug

    if debug:
        LOGGER.setLevel(logging.DEBUG)
        logging.getLogger("requests").setLevel(logging.DEBUG)
        logging.getLogger("urllib3").setLevel(logging.DEBUG)
        #logging.getLogger("croissant_wrapper").setLevel(logging.DEBUG)


if __name__ == "__main__":
    main()
