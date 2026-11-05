#!/usr/bin/env python
"""Utilities for pushing data containers to Hugging Face."""
from __future__ import annotations

import argparse
import os
from collections import defaultdict

import dask.array as da
import dask.dataframe as dd
import pandas as pd
import numpy as np
import scanpy as sc
import zarr
import logging
from zipfile import ZipFile
import json

from typing import Any

from anndata import AnnData
from anndata.experimental import read_dispatched, write_dispatched, StorageType
from anndata.io import read_elem

from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec.implementations.zip import ZipFileSystem

import mlcroissant as mlc

from jsonpath_ng import parse

logging.basicConfig(
    level=logging.INFO,
)

LOGGER = logging.getLogger(__name__)


class BaseDataFrameFactory:
    """Provide a base class for a set of classes to handle multiple field configurations."""
    def __init__(self, field_list: list[mlc.Field], field_value_dict: dict[str, Any]):
        self.field_list = field_list
        self.field_value_dict = field_value_dict

    def test(self) -> bool:
        """Test if this class is suitable for the given list of fields."""
        return False

    def __iter__(self) -> Generator(None, None, None):
        yield None

    def count_field_types(self) -> defaultdict[type, int]:
        fld_val_tp_cnts = defaultdict(int)
        for fld in self.field_list:
            fld_id = fld["@id"]
            fld_value = self.field_value_dict[fld_id]
            fld_val_tp_cnts[type(fld_value)] += 1
        return fld_val_tp_cnts


class ExpressionMatrixDataFrameFactory(BaseDataFrameFactory):
    def test(self) -> bool:
        """Test if this class is suitable for the given list of fields."""
        tp_cts = self.count_field_types()
        if (
            tp_cts[pd.core.indexes.base.Index] == 2
            and (tp_cts[da.core.Array] == 1 or tp_cts[np.ndarray] == 1)
        ):
            return True
        return False

    def __iter__(self) -> Generator(None, None, None):
        array_field = [
            fld for fld in self.field_list
            if isinstance(self.field_value_dict[fld["@id"]],
                          (np.ndarray, da.core.Array))
        ][0]
        array_field_id = array_field["@id"]
        array_value = self.field_value_dict[array_field_id]
        nrows, ncols = array_value.shape
        row_field_id = None
        col_field_id = None
        for fld in self.field_list:
            fld_id = fld["@id"]
            field_value = self.field_value_dict[fld_id]
            if isinstance(field_value, pd.core.indexes.base.Index):
                if len(field_value) == nrows:
                    row_field_id = fld_id
                elif len(field_value) == ncols:
                    col_field_id = fld_id
                else:
                    raise RuntimeError(f"One of the indices of the field {array_field_id}"
                                       " does not match its dimensions")
        assert row_field_id and col_field_id, f"The field {array_field_id} is missing an index"
        yield pd.DataFrame(
            array_value,
            columns=self.field_value_dict[col_field_id],
            index=self.field_value_dict[row_field_id]
        )
