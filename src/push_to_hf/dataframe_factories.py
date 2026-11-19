#!/usr/bin/env python
"""Utilities for pushing data containers to Hugging Face."""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Generator
from typing import Any

import dask.array as da
import mlcroissant as mlc
import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
)

LOGGER = logging.getLogger(__name__)


class BaseDataFrameFactory:
    """Provide a base class for a set of classes to handle multiple field configurations."""

    def __init__(self, field_list: list[mlc.Field], field_value_dict: dict[str, Any]):
        """Initialize the base class."""
        self.field_list = field_list
        self.field_value_dict = field_value_dict

    def test(self) -> bool:
        """Test if this class is suitable for the given list of fields."""
        return False

    def __iter__(self) -> Generator[None, None, None]:
        """Yield no Generators for the base class."""
        yield None

    def count_field_types(self) -> defaultdict[type, int]:
        """Produce a dict mapping field types to counts of fields of each type."""
        fld_val_tp_cnts = defaultdict(int)
        for fld in self.field_list:
            fld_id = fld["@id"]
            fld_value = self.field_value_dict[fld_id]
            fld_val_tp_cnts[type(fld_value)] += 1
        return fld_val_tp_cnts


class ExpressionMatrixDataFrameFactory(BaseDataFrameFactory):
    """Provide a class which produces a dataframe from an array of elements."""

    def test(self) -> bool:
        """Test if the provided fields include an array and two indices."""
        tp_cts = self.count_field_types()
        if tp_cts[pd.core.indexes.base.Index] == 2 and (
            tp_cts[da.core.Array] == 1 or tp_cts[np.ndarray] == 1
        ):
            return True
        return False

    def __iter__(self) -> Generator[None, None, None]:
        """Yield one generator for the single DataFrame produced by the array field."""
        array_field = [
            fld
            for fld in self.field_list
            if isinstance(
                self.field_value_dict[fld["@id"]], (np.ndarray, da.core.Array)
            )
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
                    raise RuntimeError(
                        f"One of the indices of the field {array_field_id}"
                        " does not match its dimensions"
                    )
        assert (
            row_field_id and col_field_id
        ), f"The field {array_field_id} is missing an index"
        yield pd.DataFrame(
            array_value,
            columns=self.field_value_dict[col_field_id],
            index=self.field_value_dict[row_field_id],
        )


class SeriesListDataFrameFactory(BaseDataFrameFactory):
    """Provide a class which produces a dataframe from a list of series."""

    def test(self) -> bool:
        """Test if the provided fields include an index and one or more series."""
        tp_cts = self.count_field_types()
        if tp_cts[pd.core.indexes.base.Index] == 1 and (
            tp_cts[pd.core.series.Series] >= 1
        ):
            return True
        return False

    def __iter__(self) -> Generator[None, None, None]:
        """Yield one generator for the single DataFrame produced by the obs elements."""
        index_field = [
            fld
            for fld in self.field_list
            if isinstance(self.field_value_dict[fld["@id"]], pd.core.indexes.base.Index)
        ][0]
        index_field_id = index_field["@id"]
        index_field_value = self.field_value_dict[index_field_id]
        col_fields = [
            fld
            for fld in self.field_list
            if isinstance(self.field_value_dict[fld["@id"]], pd.core.series.Series)
        ]
        col_values = []
        col_names = []
        for fld in col_fields:
            fld_id = fld["@id"]
            col_values.append(self.field_value_dict[fld_id])
            col_names.append(fld["schema:name"])
        df = pd.concat(col_values, axis=1, keys=col_names)
        assert df.index.equals(index_field_value), "index does not match columns"
        yield df
