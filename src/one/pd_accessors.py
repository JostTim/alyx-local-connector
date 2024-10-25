import pandas as pd, numpy as np
from .api import ONE
from .alf.spec import to_full_path
from pathlib import Path


@pd.api.extensions.register_dataframe_accessor("alyx")
class AlyxDataframeAcessorsRegistry:
    def __init__(self, pandas_obj) -> None:
        self.pandas_obj = pandas_obj

    @property
    def datasets(self):
        return DatasetsDataframeAcessor(self.pandas_obj)

    @property
    def sessions(self):
        return SessionsDataframeAccessor(self.pandas_obj)


@pd.api.extensions.register_series_accessor("alyx")
class AlyxSeriesAcessorsRegistry:
    def __init__(self, pandas_obj) -> None:
        self.pandas_obj = pandas_obj

    @property
    def dataset(self):
        return DatasetsSeriesAcessor(self.pandas_obj)

    @property
    def plots(self):
        return PlotSeriesAcessor(self.pandas_obj)

    @property
    def files(self):
        return FilesSeriesAccessor(self.pandas_obj)

    @property
    def session(self):
        return SessionSeriesAccessor(self.pandas_obj)


class PlotSeriesAcessor:
    def __init__(self, pandas_obj) -> None:
        self.pandas_obj = pandas_obj


class SessionsDataframeAccessor:

    def __init__(self, pandas_obj) -> None:
        self._obj: pd.DataFrame = pandas_obj

    def local_mode(self):
        return self._obj.assign(path=self._obj["local_path"])

    def remote_mode(self):
        return self._obj.assign(path=self._obj["remote_path"])


class SessionSeriesAccessor:

    def __init__(self, pandas_obj) -> None:
        self._obj: pd.Series = pandas_obj

    def local_mode(self):
        series = self._obj.copy()
        series["path"] = series["local_path"]
        return series

    def remote_mode(self):
        series = self._obj.copy()
        series["path"] = series["remote_path"]
        return series


class FilesSeriesAccessor:

    def __init__(self, pandas_obj) -> None:
        self.pandas_obj = pandas_obj

    def search_figure(self, criterias=[], extension=".png"):
        if not isinstance(criterias, list):
            criterias = [criterias]

        criterias.append(extension)
        criterias.append("fig.")

        files = []
        for r, d, f in (Path(self.pandas_obj.path) / "figures").walk():
            files.extend([r / file for file in f if all([crit in file for crit in criterias])])
        return sorted(files)


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
