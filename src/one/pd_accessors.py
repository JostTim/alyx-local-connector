import pandas as pd, numpy as np
from .api import ONE
from .alf.spec import to_full_path


@pd.api.extensions.register_dataframe_accessor("datasets")
class DatasetsDataframeAcessor:
    def __init__(self, pandas_obj) -> None:
        self._validate(pandas_obj)
        self._obj = pandas_obj
        self.connector = ONE()

    @staticmethod
    def _validate(obj):
        required_fields = [
            "object",
            "attribute",
            "subject",
            "date",
            "number",
            "collection",
            "extra",
            "remote_root",
            "local_root",
            "extension",
        ]
        missing_fields = []
        for req_field in required_fields:
            if req_field not in obj.columns:
                missing_fields.append(req_field)
        if len(missing_fields):
            raise AttributeError(
                "The dataframe must have some columns to use datasets acessor. This object is missing columns :"
                f" {','.join(missing_fields)}"
            )

    def make_fullpaths(self, mode="remote"):
        root_key = "remote_root" if mode == "remote" else "local_root"

        def components_to_path(series):
            nonlocal root_key
            components_labels = [
                "object",
                "attribute",
                "subject",
                "date",
                "number",
                "collection",
                "extra",
                "root",
                "extension",
                "revision",
            ]
            components = {}
            for label, value in series.items():
                if label in components_labels:
                    components[label] = value
                elif label == root_key:
                    components["root"] = value
            return to_full_path(**components)

        return self._obj.apply(components_to_path, axis="columns")


@pd.api.extensions.register_series_accessor("dataset")
class DatasetsSeriesAcessor:
    def __init__(self, pandas_obj) -> None:
        self._validate(pandas_obj)
        self._obj = pandas_obj
        self.connector = ONE()

    @staticmethod
    def _validate(obj):
        required_fields = [
            "object",
            "attribute",
            "subject",
            "date",
            "number",
            "collection",
            "extra",
            "remote_root",
            "local_root",
            "extension",
        ]
        missing_fields = []
        for req_field in required_fields:
            if req_field not in obj.index:
                missing_fields.append(req_field)
        if len(missing_fields):
            raise AttributeError(
                "The series must have some columns to use datasets acessor. This object is missing columns :"
                f" {','.join(missing_fields)}"
            )

    def make_fullpath(self, mode="remote"):
        root_key = "remote_root" if mode == "remote" else "local_root"

        components_labels = [
            "object",
            "attribute",
            "subject",
            "date",
            "number",
            "collection",
            "extra",
            "root",
            "extension",
            "revision",
        ]
        components = {}
        for label, value in self._obj.items():
            if label in components_labels:
                components[label] = value
            elif label == root_key:
                components["root"] = value
        return to_full_path(**components)
