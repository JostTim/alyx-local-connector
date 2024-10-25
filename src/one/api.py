"""Classes for searching, listing and (down)loading ALyx Files."""

import collections.abc
import warnings
import logging
import packaging.version
from datetime import datetime, timedelta
from functools import lru_cache, partial, wraps
from inspect import unwrap
from pathlib import Path, PurePosixPath
from enum import Enum
import os
import copy
import shutil
from typing import Any, Union, Optional, List, Tuple
from uuid import UUID
import time
import threading

import pandas as pd
import numpy as np
from numpy import dtype as npdtype
import requests.exceptions

from iblutil.io import parquet, hashfile
from iblutil.util import Bunch, flatten
from tqdm import tqdm
from sys import stdout
import one.params
from one.webclient import HTTPError, AlyxClient
import one.alf.io as alfio
import one.alf.exceptions as alferr
from .alf.cache import make_parquet_db
from .alf.files import rel_path_parts, get_session_path, get_alf_path, add_uuid_string
from .alf.spec import is_uuid_string
from .registration import RegistrationClient
from one.converters import ConversionMixin
import one.util as util

from .files import FileTransferManager

# _logger = logging.getLogger(__name__)

"""int: The number of download threads"""
N_THREADS = 4


def singleton(cls):
    instances = {}

    @wraps(cls)
    def getinstance(*args, **kwargs):
        if cls not in instances or kwargs.get("regen", False) is True:
            kwargs.pop("regen", None)
            instances[cls] = cls(*args, **kwargs)
        return instances[cls]

    return getinstance


class MultiSessionPlaceholder(pd.core.series.Series):
    @staticmethod
    def _get_connector():
        return one.ONE()

    def __init__(
        self,
        *args,
        project="Adaptation",
        analysis_group="default",
        remote_path="",
        data_repository=None,
        mode="remote",
        date="2000-01-01",
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if data_repository is not None:
            if data_repository == "local":
                remote_path = one.params.get().LOCAL_ROOT
            else:
                remote_path = self._get_connector().alyx.rest("data-repository", "read", data_repository)["data_path"]

        if remote_path == "":
            raise ValueError(
                "Data path cannot be empty string. Must either be obtained by supplying data_repository argument, "
                "or data_path directly"
            )

        remote_path = os.path.normpath(remote_path)

        self["rel_path"] = os.path.join("multisession", analysis_group)
        self["local_path"] = os.path.join(os.path.normpath(one.params.get().LOCAL_ROOT), self["rel_path"])
        self["remote_path"] = os.path.join(remote_path, self["rel_path"])
        self["path"] = self["remote_path"] if mode == "remote" else self["local_path"]
        self["alias"] = analysis_group
        self["u_alias"] = analysis_group
        self["projects"] = [project]
        self["subject"] = analysis_group
        self["number"] = 1
        self["date"] = "2000-01-01"
        self["json"] = {}

        if not os.path.isdir(self["path"]):
            os.makedirs(self["path"], exist_ok=True)

        self.name = f"<placeholder_{analysis_group}>"


class One(ConversionMixin):
    """An API for searching and loading data on a local filesystem"""

    _search_terms = (
        "dataset",
        "date_range",
        "laboratory",
        "number",
        "projects",
        "subject",
        "task_protocol",
        "object",
        "attribute",
        "data_repository",
        "default_data_repository",
    )

    def __init__(
        self,
        cache_dir=None,
        mode="auto",
        wildcards=True,
        data_access_mode="unapplicable",
    ):
        """An API for searching and loading data on a local filesystem

        Parameters
        ----------
        cache_dir : str, Path
            Path to the data files.  If Alyx parameters have been set up for this location,
            an OneAlyx instance is returned.  If data_dir and base_url are None, the default
            location is used.
        mode : str
            Query mode, options include 'auto' (reload cache daily), 'local' (offline) and
            'refresh' (always reload cache tables).  Most methods have a `query_type` parameter
            that can override the class mode.
        """
        # get parameters override if inputs provided
        super().__init__()
        if not getattr(self, "cache_dir", None):  # May already be set by subclass
            self.cache_dir = cache_dir or one.params.get_cache_dir()
        self.cache_expiry = timedelta(hours=24)
        self.mode = mode
        self.wildcards = wildcards  # Flag indicating whether to use regex or wildcards
        self.record_loaded = False
        # init the cache file
        self._reset_cache()
        self.load_cache()

    def __repr__(self):
        return f'One ({"off" if self.offline else "on"}line, {self.cache_dir})'

    @property
    def offline(self):
        """bool: True if mode is local or no Web client set"""
        return self.mode == "local" or not getattr(self, "_web_client", False)

    @util.refresh
    def search_terms(self, query_type=None) -> tuple:
        """List the search term keyword args for use in the search method"""
        return self._search_terms

    def _reset_cache(self):
        """Replace the cache object with a Bunch that contains the right fields"""
        self._cache = Bunch(
            {
                "_meta": {
                    "expired": False,
                    "created_time": None,
                    "loaded_time": None,
                    "modified_time": None,
                    "saved_time": None,
                    "raw": {},  # map of original table metadata
                }
            }
        )

    def load_cache(self, cache_dir=None, **kwargs):
        """
        Load parquet cache files from a local directory.

        Parameters
        ----------
        cache_dir : str, pathlib.Path
            An optional directory location of the parquet files, defaults to One.cache_dir.
        """
        _logger = logging.getLogger("load_cache")

        self._reset_cache()
        meta = self._cache["_meta"]
        INDEX_KEY = ".?id"
        for cache_file in Path(cache_dir or self.cache_dir).glob("*.pqt"):
            table = cache_file.stem
            # we need to keep this part fast enough for transient objects
            cache, meta["raw"][table] = parquet.load(cache_file)
            if "date_created" not in meta["raw"][table]:
                _logger.warning(f"{cache_file} does not appear to be a valid table. Skipping")
                continue
            meta["loaded_time"] = datetime.now()

            # Patch older tables
            cache = util.patch_cache(cache, meta["raw"][table].get("min_api_version"))

            # Set the appropriate index if none already set
            if isinstance(cache.index, pd.RangeIndex):
                idx_columns = cache.filter(regex=INDEX_KEY).columns.tolist()
                if len(idx_columns) == 0:
                    raise KeyError("Failed to set index")
                cache.set_index(idx_columns, inplace=True)

            # Check sorted
            # Sorting makes MultiIndex indexing O(N) -> O(1)
            if not cache.index.is_monotonic_increasing:
                cache.sort_index(inplace=True)

            self._cache[table] = cache

        if len(self._cache) == 1:
            # No tables present
            meta["expired"] = True
            meta["raw"] = {}
            self._cache.update({"datasets": pd.DataFrame(), "sessions": pd.DataFrame()})
        created = [datetime.fromisoformat(x["date_created"]) for x in meta["raw"].values() if "date_created" in x]
        if created:
            meta["created_time"] = min(created)
            meta["expired"] |= datetime.now() - meta["created_time"] > self.cache_expiry
        self._cache["_meta"] = meta
        return self._cache["_meta"]["loaded_time"]

    def save_cache(self, save_dir=None, force=False):
        """Save One._cache attribute into parquet tables if recently modified.

        Parameters
        ----------
        save_dir : str, pathlib.Path
            The directory path into which the tables are saved.  Defaults to cache directory.
        force : bool
            If True, the cache is saved regardless of modification time.
        """
        threading.Thread(target=lambda: self._save_cache(save_dir=save_dir, force=force)).start()

    def _save_cache(self, save_dir=None, force=False):
        """
        Checks if another process is writing to file, if so waits before saving.

        Parameters
        ----------
        save_dir : str, pathlib.Path
            The directory path into which the tables are saved.  Defaults to cache directory.
        force : bool
            If True, the cache is saved regardless of modification time.
        """
        _logger = logging.getLogger("_save_cache")
        TIMEOUT = 30  # Delete lock file this many seconds after creation/modification or waiting
        lock_file = Path(self.cache_dir).joinpath(".cache.lock")
        save_dir = Path(save_dir or self.cache_dir)
        meta = self._cache["_meta"]
        modified = meta.get("modified_time") or datetime.min
        update_time = max(meta.get(x) or datetime.min for x in ("loaded_time", "saved_time"))
        if modified < update_time and not force:
            return  # Not recently modified; return

        # Check if in use by another process
        while lock_file.exists():
            if time.time() - lock_file.stat().st_ctime > TIMEOUT:
                lock_file.unlink(missing_ok=True)
            else:
                time.sleep(0.1)

        _logger.info("Saving cache tables...")
        lock_file.touch()
        try:
            for table in filter(lambda x: not x[0] == "_", self._cache.keys()):
                metadata = meta["raw"][table]
                metadata["date_modified"] = modified.isoformat(sep=" ", timespec="minutes")
                filename = save_dir.joinpath(f"{table}.pqt")
                parquet.save(filename, self._cache[table], metadata)
                _logger.debug(f"Saved {filename}")
            meta["saved_time"] = datetime.now()
        finally:
            lock_file.unlink()

    def refresh_cache(self, mode="auto"):
        """Check and reload cache tables

        Parameters
        ----------
        mode : {'local', 'refresh', 'auto', 'remote'}
            Options are 'local' (don't reload); 'refresh' (reload); 'auto' (reload if expired);
            'remote' (don't reload)

        Returns
        -------
        datetime.datetime
            Loaded timestamp
        """
        # NB: Currently modified table will be lost if called with 'refresh';
        # May be instances where modified cache is saved then immediately replaced with a new
        # remote cache. Also it's too slow :(
        # self.save_cache()  # Save cache if modified
        _logger = logging.getLogger("refresh_cache")
        if mode in ("local", "remote"):
            pass
        elif mode == "auto":
            if datetime.now() - self._cache["_meta"]["loaded_time"] >= self.cache_expiry:
                _logger.info("Cache expired, refreshing")
                self.load_cache()
        elif mode == "refresh":
            _logger.debug("Forcing reload of cache")
            self.load_cache(clobber=True)
        else:
            raise ValueError(f'Unknown refresh type "{mode}"')
        return self._cache["_meta"]["loaded_time"]

    def _update_cache_from_records(self, strict=False, **kwargs):
        """
        Update the cache tables with new records.

        Parameters
        ----------
        strict : bool
            If not True, the columns don't need to match.  Extra columns in input tables are
            dropped and missing columns are added and filled with np.nan.
        **kwargs
            pandas.DataFrame or pandas.Series to insert/update for each table

        Returns
        -------
        datetime.datetime:
            A timestamp of when the cache was updated

        Example
        -------
        >>> session, datasets = util.ses2records(self.get_details(eid, full=True))
        ... self._update_cache_from_records(sessions=session, datasets=datasets)

        Raises
        ------
        AssertionError
            When strict is True the input columns must exactly match those oo the cache table,
            including the order.
        KeyError
            One or more of the keyword arguments does not match a table in One._cache
        """
        _logger = logging.getLogger("_update_cache_from_records")
        updated = None
        for table, records in kwargs.items():
            if records is None or records.empty:
                continue
            if table not in self._cache:
                raise KeyError(f'Table "{table}" not in cache')
            if isinstance(records, pd.Series):
                records = pd.DataFrame([records])
            if not isinstance(records, pd.DataFrame):
                raise TypeError(f"records must be a dataframe but was : {type(records)}")
            try:
                if not strict:
                    # Deal with case where there are extra columns in the cache
                    extra_columns = set(self._cache[table].columns) - set(records.columns)
                    for col in extra_columns:
                        n = list(self._cache[table].columns).index(col)
                        records.insert(n, col, np.nan)
                    # Drop any extra columns in the records that aren't in cache table
                    if len(self._cache[table].columns):
                        to_drop = list(set(records.columns) - set(self._cache[table].columns))
                        records.drop(to_drop, axis=1, inplace=True)
                    records = records.reindex(columns=self._cache[table].columns)
                assert all(self._cache[table].columns == records.columns)
                # Update existing rows
                to_update = records.index.isin(self._cache[table].index)
                if not records[to_update].empty:
                    self._cache[table] = self._cache[table].join(records[to_update])
                # Assign new rows
                to_assign = records[~to_update]
                if (
                    isinstance(self._cache[table].index, pd.MultiIndex) or isinstance(to_assign.index, pd.MultiIndex)
                ) and not to_assign.empty:
                    # Concatenate and sort (no other way for non-unique index within MultiIndex)
                    self._cache[table] = pd.concat([self._cache[table], to_assign]).sort_index()
                else:
                    for index, record in to_assign.iterrows():
                        self._cache[table].loc[index, :] = record[self._cache[table].columns].values
            except KeyError as e:
                _logger.debug(
                    f"Local cache could not be updated : {type(e).__name__} : {e} for cachefield {table} with values \n"
                    "{self._cache[table]} and new values \n{records}"
                )
            updated = datetime.now()
        self._cache["_meta"]["modified_time"] = updated
        return updated

    def save_loaded_ids(self, sessions_only=False, clear_list=True):
        """
        Save list of UUIDs corresponding to datasets or sessions where datasets were loaded.

        Parameters
        ----------
        sessions_only : bool
            If true, save list of experiment IDs, otherwise the full list of dataset IDs.
        clear_list : bool
            If true, clear the current list of loaded dataset IDs after saving.

        Returns
        -------
        list of str
            List of UUIDs.
        pathlib.Path
            The file path of the saved list.
        """
        if "_loaded_datasets" not in self._cache or self._cache["_loaded_datasets"].size == 0:
            warnings.warn('No datasets loaded; check "record_datasets" attribute is True')
            return [], None
        if sessions_only:
            name = "session_uuid"
            if self._cache["_loaded_datasets"].dtype == "int64" or self._index_type() is int:
                # We're unlikely to return to int IDs and all caches should be cast to str on load
                raise NotImplementedError("Saving integer session IDs not supported")
            else:
                idx = self._cache["datasets"].index.isin(self._cache["_loaded_datasets"], "id")
                ids = self._cache["datasets"][idx].index.unique("eid").values
        else:
            name = "dataset_uuid"
            ids = self._cache["_loaded_datasets"]
            if ids.dtype != "<U36":
                ids = parquet.np2str(ids)

        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S%z")
        filename = Path(self.cache_dir) / f"{timestamp}_loaded_{name}s.csv"
        pd.DataFrame(ids, columns=[name]).to_csv(filename, index=False)
        if clear_list:
            self._cache["_loaded_datasets"] = np.array([])
        return ids, filename

    def search(self, details=False, query_type=None, **kwargs):
        """
        Searches sessions matching the given criteria and returns a list of matching eids

        For a list of search terms, use the method

            one.search_terms()

        For all of the search parameters, a single value or list may be provided.  For dataset,
        the sessions returned will contain all listed datasets.  For the other parameters,
        the session must contain at least one of the entries. NB: Wildcards are not permitted,
        however if wildcards property is False, regular expressions may be used for all but
        number and date_range.

        Parameters
        ----------
        dataset : str, list
            list of dataset names. Returns sessions containing all these datasets.
            A dataset matches if it contains the search string e.g. 'wheel.position' matches
            '_ibl_wheel.position.npy'
        date_range : str, list, datetime.datetime, datetime.date, pandas.timestamp
            A single date to search or a list of 2 dates that define the range (inclusive).  To
            define only the upper or lower date bound, set the other element to None.
        lab : str
            A str or list of lab names, returns sessions from any of these labs
        number : str, int
            Number of session to be returned, i.e. number in sequence for a given date
        subject : str, list
            A list of subject nicknames, returns sessions for any of these subjects
        task_protocol : str
            The task protocol name (can be partial, i.e. any task protocol containing that str
            will be found)
        projects : str, list
            The project name(s) (can be partial, i.e. any project containing that str
            will be found)
        details : bool
            If true also returns a dict of dataset details
        query_type : str, None
            Query cache ('local') or Alyx database ('remote')

        Returns
        -------
        list
            A list of eids
        (list)
            (If details is True) a list of dictionaries, each entry corresponding to a matching
            session
        """

        def all_present(x, dsets, exists=True):
            """Returns true if all datasets present in Series"""
            return all(any(x.str.contains(y, regex=self.wildcards) & exists) for y in dsets)

        # Iterate over search filters, reducing the sessions table
        sessions = self._cache["sessions"]

        # Ensure sessions filtered in a particular order, with datasets last
        search_order = ("date_range", "number", "dataset")

        def sort_fcn(itm):
            return -1 if itm[0] not in search_order else search_order.index(itm[0])

        # Validate and get full name for queries
        search_terms = self.search_terms(query_type="local")
        queries = {util.autocomplete(k, search_terms): v for k, v in kwargs.items()}
        for key, value in sorted(queries.items(), key=sort_fcn):
            # key = util.autocomplete(key)  # Validate and get full name
            # No matches; short circuit
            if sessions.size == 0:
                return ([], None) if details else []
            # String fields
            elif key in ("subject", "task_protocol", "laboratory", "projects"):
                query = "|".join(util.ensure_list(value))
                key = "lab" if key == "laboratory" else key
                mask = sessions[key].str.contains(query, regex=self.wildcards)
                sessions = sessions[mask.astype(bool, copy=False)]
            elif key == "date_range":
                start, end = util.validate_date_range(value)
                session_date = pd.to_datetime(sessions["date"])
                sessions = sessions[(session_date >= start) & (session_date <= end)]
            elif key == "number":
                query = util.ensure_list(value)
                sessions = sessions[sessions[key].isin(map(int, query))]
            # Dataset check is biggest so this should be done last
            elif key == "dataset":
                index = ["eid_0", "eid_1"] if self._index_type("datasets") is int else "eid"
                query = util.ensure_list(value)
                datasets = self._cache["datasets"]
                if self._index_type() is int:
                    idx = np.array(sessions.index.tolist())
                    datasets = datasets.loc[
                        (
                            idx[:, 0],
                            idx[:, 1],
                        ),
                    ]
                else:
                    datasets = datasets.loc[(sessions.index.values,), :]
                # For each session check any dataset both contains query and exists
                mask = datasets.groupby(index, sort=False).apply(
                    lambda x: all_present(x["rel_path"], query, x["exists"])
                )
                # eids of matching dataset records
                idx = mask[mask].index

                # Reduce sessions table by datasets mask
                sessions = sessions.loc[idx]

        # Return results
        if sessions.size == 0:
            return ([], None) if details else []
        sessions = sessions.sort_values(["date", "subject", "number"], ascending=False)
        eids = sessions.index.to_list()
        if self._index_type() is int:
            eids = parquet.np2str(np.array(eids))

        if details:
            return eids, sessions.reset_index(drop=True).to_dict("records", Bunch)
        else:
            return eids

    def _check_filesystem(self, datasets, offline=None, update_exists=True):
        """Update the local filesystem for the given datasets.

        Given a set of datasets, check whether records correctly reflect the filesystem.
        Called by load methods, this returns a list of file paths to load and return.
        TODO This needs changing; overload for downloading?
         This changes datasets frame, calls _update_cache(sessions=None, datasets=None) to
         update and save tables.  Download_datasets can also call this function.

        Parameters
        ----------
        datasets : pandas.Series, pandas.DataFrame, list of dicts
            A list or DataFrame of dataset records
        offline : bool, None
            If false and Web client present, downloads the missing datasets from a remote
            repository
        update_exists : bool
            If true, the cache is updated to reflect the filesystem

        Returns
        -------
        A list of file paths for the datasets (None elements for non-existent datasets)
        """
        _logger = logging.getLogger("_check_filesystem")
        if isinstance(datasets, pd.Series):
            datasets = pd.DataFrame([datasets])
        elif not isinstance(datasets, pd.DataFrame):
            # Cast set of dicts (i.e. from REST datasets endpoint)
            datasets = util.datasets2records(list(datasets))
        indices_to_download = []  # indices of datasets that need (re)downloading
        files = []  # file path list to return
        # First go through datasets and check if file exists and hash matches
        for i, rec in datasets.iterrows():
            file = Path(self.cache_dir, *rec[["session_path", "rel_path"]])
            if file.exists():
                # Check if there's a hash mismatch
                # If so, add this index to list of datasets that need downloading
                if rec["hash"] is not None:
                    if hashfile.md5(file) != rec["hash"]:
                        _logger.warning(
                            "local md5 mismatch on dataset: %s",
                            PurePosixPath(rec.session_path, rec.rel_path),
                        )
                        indices_to_download.append(i)
                elif rec["file_size"] and file.stat().st_size != rec["file_size"]:
                    _logger.warning(
                        "local file size mismatch on dataset: %s",
                        PurePosixPath(rec.session_path, rec.rel_path),
                    )
                    indices_to_download.append(i)
                files.append(file)  # File exists so add to file list
            else:
                # File doesn't exist so add None to output file list
                files.append(None)
                # Add this index to list of datasets that need downloading
                indices_to_download.append(i)
            if rec["exists"] != file.exists():
                with warnings.catch_warnings():
                    # Suppress future warning: exist column should always be present
                    msg = ".*indexing on a MultiIndex with a nested sequence of labels.*"
                    warnings.filterwarnings("ignore", message=msg)
                    datasets.at[i, "exists"] = not rec["exists"]
                    if update_exists:
                        _logger.debug("Updating exists field")
                        self._cache["datasets"].loc[(slice(None), i), "exists"] = not rec["exists"]
                        self._cache["_meta"]["modified_time"] = datetime.now()

        # If online and we have datasets to download, call download_datasets with these datasets
        if not (offline or self.offline) and indices_to_download:
            dsets_to_download = datasets.loc[indices_to_download]
            # Returns list of local file paths and set to variable
            new_files = self._download_datasets(dsets_to_download, update_cache=update_exists)
            # Add each downloaded file to the output list of files
            for i, file in zip(indices_to_download, new_files):
                files[datasets.index.get_loc(i)] = file

        if self.record_loaded:
            loaded = np.fromiter(map(bool, files), bool)
            loaded_ids = np.array(datasets.index.to_list())[loaded]
            if "_loaded_datasets" not in self._cache:
                self._cache["_loaded_datasets"] = np.unique(loaded_ids)
            else:
                loaded_set = np.hstack([self._cache["_loaded_datasets"], loaded_ids])
                self._cache["_loaded_datasets"] = np.unique(loaded_set, axis=0)

        # Return full list of file paths
        return files

    def _index_type(self, table="sessions"):
        """For a given table return the index type.

        Parameters
        ----------
        table : str, pd.DataFrame
            The cache table to check

        Returns
        -------
        type
            The type of the table index, either str or int

        Raises
        ------
        IndexError
            Unable to determine the index type of the cache table
        """
        table = self._cache[table] if isinstance(table, str) else table
        idx_0 = table.index.values[0]
        if len(table.index.names) % 2 == 0 and all(isinstance(x, int) for x in idx_0):
            return int
        elif all(isinstance(x, str) for x in util.ensure_list(idx_0)):
            return str
        else:
            raise IndexError

    @util.refresh
    @util.parse_id
    def get_details(self, eid: Union[str, Path, UUID], full: bool = False):
        """Return session details for a given session ID

        Parameters
        ----------
        eid : str, UUID, pathlib.Path, dict
            Experiment session identifier; may be a UUID, URL, experiment reference string
            details dict or Path.
        full : bool
            If True, returns a DataFrame of session and dataset info

        Returns
        -------
        pd.Series, pd.DataFrame
            A session record or full DataFrame with dataset information if full is True
        """
        # Int ids return DataFrame, making str eid a list ensures Series not returned
        int_ids = self._index_type() is int
        idx = parquet.str2np(eid).tolist() if int_ids else [eid]
        try:
            det = self._cache["sessions"].loc[idx]
            assert len(det) == 1
        except KeyError:
            raise alferr.ALFObjectNotFound(eid)
        except AssertionError:
            raise alferr.ALFMultipleObjectsFound(f"Multiple sessions in cache for eid {eid}")
        if not full:
            return det.iloc[0]
        # to_drop = 'eid' if int_ids else ['eid_0', 'eid_1']  # .reset_index(to_drop, drop=True)
        column = ["eid_0", "eid_1"] if int_ids else "eid"
        return self._cache["datasets"].join(det, on=column, how="right")

    @util.refresh
    def list_subjects(self) -> List[str]:
        """
        List all subjects in database

        Returns
        -------
        list
            Sorted list of subject names
        """
        return self._cache["sessions"]["subject"].sort_values().unique().tolist()

    @util.refresh
    def list_datasets(
        self,
        eid=None,
        *,
        # filename=None,
        # collection=None,
        # revision=None,
        details=False,
        query_type=None,
        object=None,
        as_mode=None,
        **filters,
    ) -> Union[np.ndarray, pd.DataFrame]:
        """
        Given an eid, return the datasets for those sessions.  If no eid is provided,
        a list of all datasets is returned.  When details is false, a sorted array of unique
        datasets is returned (their relative paths).

        Parameters
        ----------
        eid : str, UUID, pathlib.Path, dict
            Experiment session identifier; may be a UUID, URL, experiment reference string
            details dict or Path.
        filename : str, dict, list
            Filters datasets and returns only the ones matching the filename
            Supports lists asterisks as wildcards.  May be a dict of ALF parts.
        collection : str, list
            The collection to which the object belongs, e.g. 'alf/probe01'.
            This is the relative path of the file from the session root.
            Supports asterisks as wildcards.
        revision : str
            Filters datasets and returns only the ones matching the revision
            Supports asterisks as wildcards
        details : bool
            When true, a pandas DataFrame is returned, otherwise a numpy array of
            relative paths (collection/revision/filename) - see one.alf.spec.describe for details.
        query_type : str
            Query cache ('local') or Alyx database ('remote')

        Returns
        -------
        np.ndarray, pd.DataFrame
            Slice of datasets table or numpy array if details is False

        Examples
        --------
        List all unique datasets in ONE cache

        >>> datasets = one.list_datasets()

        List all datasets for a given experiment

        >>> datasets = one.list_datasets(eid)

        List all datasets for an experiment that match a collection name

        >>> probe_datasets = one.list_datasets(eid, collection='*probe*')

        List datasets for an experiment that have 'wheel' in the filename

        >>> datasets = one.list_datasets(eid, filename='*wheel*')

        List datasets for an experiment that are part of a 'wheel' or 'trial(s)' object

        >>> datasets = one.list_datasets(eid, {'object': ['wheel', 'trial?']})
        """

        raise NotImplementedError("This function has been temporarily deprectaded as long as a rework is ncesessary")

        datasets = self._cache["datasets"]
        filter_args = dict(
            collection=collection,
            filename=filename,
            wildcards=self.wildcards,
            revision=revision,
            revision_last_before=False,
            assert_unique=False,
        )
        if not eid:
            datasets = util.filter_datasets(datasets, **filter_args)
            return datasets.copy() if details else datasets["rel_path"].unique().tolist()
        eid = self.to_eid(eid)  # Ensure we have a UUID str list
        if not eid:
            return datasets.iloc[0:0]  # Return empty
        try:
            if self._index_type() is int:
                (eid_num,) = parquet.str2np(eid)
                datasets = datasets.loc[pd.IndexSlice[eid_num,], :]
            else:
                datasets = datasets.loc[(eid,), :]
        except KeyError:
            return datasets.iloc[0:0]  # Return empty

        datasets = util.filter_datasets(datasets, **filter_args)
        # Return only the relative path
        return datasets if details else datasets["rel_path"].sort_values().values.tolist()

    @util.refresh
    def list_collections(
        self,
        eid=None,
        filename=None,
        collection=None,
        revision=None,
        details=False,
        query_type=None,
    ) -> Union[np.ndarray, dict]:
        """
        List the collections for a given experiment.  If no experiment ID is given,
        all collections are returned.

        Parameters
        ----------
        eid : [str, UUID, Path, dict]
            Experiment session identifier; may be a UUID, URL, experiment reference string
            details dict or Path
        filename : str, dict, list
            Filters datasets and returns only the collections containing matching datasets.
            Supports lists asterisks as wildcards.  May be a dict of ALF parts.
        collection : str, list
            Filter by a given pattern. Supports asterisks as wildcards.
        revision : str
            Filters collections and returns only the ones with the matching revision.
            Supports asterisks as wildcards
        details : bool
            If true a dict of pandas datasets tables is returned with collections as keys,
            otherwise a numpy array of unique collections
        query_type : str
            Query cache ('local') or Alyx database ('remote')

        Returns
        -------
        list, dict
            A list of unique collections or dict of datasets tables

        Examples
        --------
        List all unique collections in ONE cache

        >>> collections = one.list_collections()

        List all collections for a given experiment

        >>> collections = one.list_collections(eid)

        List all collections for a given experiment and revision

        >>> revised = one.list_collections(eid, revision='2020-01-01')

        List all collections that have 'probe' in the name.

        >>> collections = one.list_collections(eid, collection='*probe*')

        List collections for an experiment that have datasets with 'wheel' in the name

        >>> collections = one.list_collections(eid, filename='*wheel*')

        List collections for an experiment that contain numpy datasets

        >>> collections = one.list_collections(eid, {'extension': 'npy'})
        """
        filter_kwargs = dict(
            eid=eid,
            collection=collection,
            filename=filename,
            revision=revision,
            query_type=query_type,
        )
        datasets = self.list_datasets(details=True, **filter_kwargs).copy()

        datasets["collection"] = datasets.rel_path.apply(lambda x: rel_path_parts(x, assert_valid=False)[0] or "")
        if details:
            return {k: table.drop("collection", axis=1) for k, table in datasets.groupby("collection")}
        else:
            return datasets["collection"].unique().tolist()

    @util.refresh
    def list_revisions(
        self,
        eid=None,
        filename=None,
        collection=None,
        revision=None,
        details=False,
        query_type=None,
    ):
        """
        List the revisions for a given experiment.  If no experiment id is given,
        all collections are returned.

        Parameters
        ----------
        eid : str, UUID, Path, dict
            Experiment session identifier; may be a UUID, URL, experiment reference string
            details dict or Path
        filename : str, dict, list
            Filters datasets and returns only the revisions containing matching datasets.
            Supports lists asterisks as wildcards.  May be a dict of ALF parts.
        collection : str, list
            Filter by a given collection. Supports asterisks as wildcards.
        revision : str, list
            Filter by a given pattern. Supports asterisks as wildcards.
        details : bool
            If true a dict of pandas datasets tables is returned with collections as keys,
            otherwise a numpy array of unique collections
        query_type : str
            Query cache ('local') or Alyx database ('remote')

        Returns
        -------
        list, dict
            A list of unique collections or dict of datasets tables

        Examples
        --------
        List all revisions in ONE cache

        >>> revisions = one.list_revisions()

        List all revisions for a given experiment

        >>> revisions = one.list_revisions(eid)

        List all revisions for a given experiment that contain the trials object

        >>> revisions = one.list_revisions(eid, filename={'object': 'trials'})

        List all revisions for a given experiment that start with 2020 or 2021

        >>> revisions = one.list_revisions(eid, revision=['202[01]*'])

        """
        datasets = self.list_datasets(eid=eid, details=True, query_type=query_type).copy()

        # Call filter util ourselves with the revision_last_before set to False
        kwargs = dict(
            collection=collection,
            filename=filename,
            revision=revision,
            revision_last_before=False,
            wildcards=self.wildcards,
            assert_unique=False,
        )
        datasets = util.filter_datasets(datasets, **kwargs)
        datasets["revision"] = datasets.rel_path.apply(
            lambda x: (rel_path_parts(x, assert_valid=False)[1] or "").strip("#")
        )
        if details:
            return {k: table.drop("revision", axis=1) for k, table in datasets.groupby("revision")}
        else:
            return datasets["revision"].unique().tolist()

    @staticmethod
    def setup(cache_dir=None, silent=False, **kwargs):
        """Set up One cache tables for a given data directory.

        Parameters
        ----------
        cache_dir : pathlib.Path, str
            A path to the ALF data directory
        silent : (False) bool
            when True will prompt for cache_dir if cache_dir is None, and overwrite cache if any
            when False will use cwd for cache_dir if cache_dir is None and use existing cache
        **kwargs
            Optional arguments to pass to one.alf.cache.make_parquet_db.

        Returns
        -------
        One
            An instance of One for the provided cache directory
        """
        if not cache_dir:
            if not silent:
                cache_dir = input(f"Select a directory from which to build cache ({Path.cwd()})")
            cache_dir = cache_dir or Path.cwd()
        cache_dir = Path(cache_dir)
        assert cache_dir.exists(), f"{cache_dir} does not exist"

        # Check if cache already exists
        if next(cache_dir.glob("sessions.pqt"), False):
            generate_cache = False
            if not silent:
                answer = input(f"Cache tables exist for {cache_dir}, overwrite? [y/N]")
                generate_cache = True if answer == "y" else False
            if not generate_cache:
                return One(cache_dir, mode="local")

        # Build cache tables
        make_parquet_db(cache_dir, **kwargs)
        return One(cache_dir, mode="local")

    @property
    def files_manager(self):
        return FileTransferManager


@lru_cache(maxsize=1)
def ONE(*, mode="auto", data_access_mode="remote", wildcards=True, **kwargs):
    """ONE API factory
    Determine which class to instantiate depending on parameters passed.

    Parameters
    ----------
    mode : str
        Query mode, options include 'auto', 'local' (offline) and 'remote' (online only).  Most
        methods have a `query_type` parameter that can override the class mode.
    data_access_mode : str
        Priority mode to find files related to the sessions.
        Can be either :
        - 'local', in wich case files will be searched for locally, in the folder you entered as LOCAL_DATA_FOLDER
            during first alyx setup to your computer.
        - 'remote' in wich case files will be searched for based on their server location specified in alyx.
    wildcards : bool
        If true all mathods use unix shell style pattern matching, otherwise regular expressions
        are used.
    cache_dir : str, Path
        Path to the data files.  If Alyx parameters have been set up for this location,
        an OneAlyx instance is returned.  If data_dir and base_url are None, the default
        location is used.
    base_url : str
        An Alyx database URL.  The URL must start with 'http'.
    username : str
        An Alyx database login username.
    password : str
        An Alyx database password.
    cache_rest : str
        If not in 'local' mode, this determines which http request types to cache.  Default is
        'GET'.  Use None to deactivate cache (not recommended).

    Returns
    -------
    One, OneAlyx
        An One instance if mode is 'local', otherwise an OneAlyx instance.
    """
    _logger = logging.getLogger("ONE")

    if (
        any(x in kwargs for x in ("base_url", "username", "password"))
        or not kwargs.get("cache_dir", False)
        or mode == "local"
    ):
        return OneAlyx(mode=mode, data_access_mode=data_access_mode, wildcards=wildcards, **kwargs)
    print("ENFOIRAX")
    # If cache dir was provided and corresponds to one configured with an Alyx client, use OneAlyx
    try:
        one.params.check_cache_conflict(kwargs.get("cache_dir"))
        return One(mode="local", wildcards=wildcards, **kwargs)
    except AssertionError:
        # Cache dir corresponds to a Alyx repo, call OneAlyx
        return OneAlyx(mode=mode, data_access_mode=data_access_mode, wildcards=wildcards, **kwargs)


@singleton
class OneAlyx(One):
    """An API for searching and loading data through the Alyx database"""

    def __init__(
        self,
        username=None,
        password=None,
        base_url=None,
        cache_dir=None,
        mode="auto",
        data_access_mode="local",
        wildcards=True,
        **kwargs,
    ):
        """An API for searching and loading data through the Alyx database

        Parameters
        ----------
        mode : str
            Query mode, options include 'auto', 'local' (offline) and 'remote' (online only).  Most
            methods have a `query_type` parameter that can override the class mode.
        wildcards : bool
            If true, methods allow unix shell style pattern matching, otherwise regular
            expressions are supported
        cache_dir : str, Path
            Path to the data files.  If Alyx parameters have been set up for this location,
            an OneAlyx instance is returned.  If data_dir and base_url are None, the default
            location is used.
        base_url : str
            An Alyx database URL.  The URL must start with 'http'.
        username : str
            An Alyx database login username.
        password : str
            An Alyx database password.
        cache_rest : str
            If not in 'local' mode, this determines which http request types to cache.  Default is
            'GET'.  Use None to deactivate cache (not recommended).
        """

        # Load Alyx Web client
        self._web_client = AlyxClient(
            username=username,
            password=password,
            base_url=base_url,
            cache_dir=cache_dir,
            **kwargs,
        )

        self._registration_client = RegistrationClient(self)

        self.data_access_mode = data_access_mode

        self._search_endpoint = "sessions"

        # get parameters override if inputs provided
        super().__init__(mode=mode, wildcards=wildcards, cache_dir=cache_dir)

    def set_data_access_mode(self, mode):
        available_modes = [
            "local",
            "remote",
        ]  # TODO : add ability to go auto mode later. (if possible, still have to think about it)
        if mode in available_modes:
            self.data_access_mode = mode
            self.alyx.delete_cache()  # delete rest cache in case there is local / remote path stored inside
        else:
            raise ValueError(f"data_access_mode must be one of : {available_modes}")

    def __repr__(self):
        return f'One ({"off" if self.offline else "on"}line, {self.alyx.base_url})'

    def load_cache(self, cache_dir=None, clobber=False, tag=None):
        """
        Load parquet cache files.  If the local cache is sufficiently old, this method will query
        the database for the location and creation date of the remote cache.  If newer, it will be
        download and loaded.

        Note: Unlike refresh_cache, this will always reload the local files at least once.

        Parameters
        ----------
        cache_dir : str, pathlib.Path
            An optional directory location of the parquet files, defaults to One.cache_dir.
        clobber : bool
            If True, query Alyx for a newer cache even if current (local) cache is recent.
        tag : str
            An optional Alyx dataset tag for loading cache tables containing a subset of datasets.
        """
        _logger = logging.getLogger("load_cache")
        cache_meta = self._cache.get("_meta", {})
        cache_dir = cache_dir or self.cache_dir
        # If user provides tag that doesn't match current cache's tag, always download.
        # NB: In the future 'database_tags' may become a list.
        current_tags = [x.get("database_tags") for x in cache_meta.get("raw", {}).values() or [{}]]
        tag = tag or current_tags[0]  # For refreshes take the current tag as default
        different_tag = any(x != tag for x in current_tags)
        if not clobber or different_tag:
            super().load_cache(cache_dir)  # Load any present cache
            cache_meta = self._cache.get("_meta", {})  # TODO Make walrus when we drop 3.7 support
            expired = self._cache and cache_meta["expired"]
            if not expired or self.mode in ("local", "remote"):
                return

        # Warn user if expired
        if cache_meta["expired"] and cache_meta.get("created_time", False) and not self.alyx.silent:
            age = datetime.now() - cache_meta["created_time"]
            t_str = f"{age.days} day(s)" if age.days >= 1 else f"{np.floor(age.seconds / (60 * 2))} hour(s)"
            _logger.info(f"cache over {t_str} old")

        try:
            # Determine whether a newer cache is available
            cache_info = self.alyx.get(f'cache/info/{tag or ""}'.strip("/"), expires=True)
            assert tag == cache_info.get("database_tags")

            # Check version compatibility
            min_version = packaging.version.parse(cache_info.get("min_api_version", "0.0.0"))
            if packaging.version.parse(one.__version__) < min_version:
                warnings.warn(f"Newer cache tables require ONE version {min_version} or greater")
                return

            # Check whether remote cache more recent
            remote_created = datetime.fromisoformat(cache_info["date_created"])
            local_created = cache_meta.get("created_time", None)
            if local_created and (remote_created - local_created) < timedelta(minutes=1):
                _logger.info("No newer cache available")
                return

            # Download the remote cache files
            _logger.info("Downloading remote caches...")
            files = self.alyx.download_cache_tables(cache_info.get("location"), cache_dir)
            assert any(files)
            super().load_cache(cache_dir)  # Reload cache after download
        except (requests.exceptions.HTTPError, HTTPError) as ex:
            _logger.debug(ex)
            ## REMOVED THIS WARNING FOR NOW AS I CAN'T FIND IF IT IS ACTUALLY USEFULL TO HAVE A REMOTE CACHE WHEN
            # SETUPING A LOCAL USE OF ALYX LIKE WE DO
            # _logger.error('Failed to load the remote cache file')
            self.mode = "remote"
        except (ConnectionError, requests.exceptions.ConnectionError) as ex:
            _logger.debug(ex)
            _logger.error("Failed to connect to Alyx")
            self.mode = "local"

    @property
    def alyx(self):
        """one.webclient.AlyxClient: The Alyx Web client"""
        return self._web_client

    @property
    def register(self):
        return self._registration_client

    @property
    def cache_dir(self):
        """pathlib.Path: The location of the downloaded file cache"""
        return self._web_client.cache_dir

    @util.refresh
    def search_terms(self, query_type=None):
        """
        Returns a list of search terms to be passed as kwargs to the search method

        Parameters
        ----------
        query_type : str
            If 'remote', the search terms are largely determined by the REST endpoint used

        Returns
        -------
        tuple
            Tuple of search strings
        """
        if (query_type or self.mode) != "remote":
            # Return search terms from REST schema
            return self._search_terms
        fields = self.alyx.rest_schemes[self._search_endpoint]["list"]["fields"]
        # 'laboratory' already in search terms
        excl = ("lab",)
        return tuple({*self._search_terms, *(x["name"] for x in fields if x["name"] not in excl)})

    def describe_dataset(self, dataset_type=None):
        """Print a dataset type description.

        NB: This requires an Alyx database connection.

        Parameters
        ----------
        dataset_type : str
            A dataset type or dataset name

        Returns
        -------
        dict
            The Alyx dataset type record
        """
        _logger = logging.getLogger("describe_dataset")
        assert self.mode != "local" and not self.offline, "Unable to connect to Alyx in local mode"
        if not dataset_type:
            return self.alyx.rest("dataset-types", "list")
        try:
            assert isinstance(dataset_type, str) and not is_uuid_string(dataset_type)
            _logger.disabled = True
            out = self.alyx.rest("dataset-types", "read", dataset_type)
        except (AssertionError, requests.exceptions.HTTPError):
            # Try to get dataset type from dataset name
            out = self.alyx.rest("dataset-types", "read", self.dataset2type(dataset_type))
        finally:
            _logger.disabled = False
        print(out["description"])
        return out

    def change_session_data_mode(self, session_details, mode):
        session_details["full_path"] = session_details[mode + "_full_path"]
        return session_details

    #### LIST DATASETS
    @util.refresh
    def list_datasets(
        self,
        eid=None,
        details=False,
        query_type=None,
        as_mode=None,
        no_cache=False,
        **filters,
    ) -> np.ndarray | pd.DataFrame | List[str]:
        """_summary_

        Args:
            eid (str, optional): Session id (alias or pk). Does not need to be supplied only if
                using session_details. Defaults to None.
            details (bool, optional): If False, will only return a list of absolute file paths
                (obtained from the column full_path of the dataset dataframe).
                Otherwise will return a full dataframe of all datasets found for this session
                Defaults to False.
            query_type (str or None, optional): Wether to perform the metadata fetch
                (only if using eid) from local cache or on alyx. If None, uses the current mode of
                the connector instance. Defaults to None.
            as_mode (str or None, optional): Wether to perform the full_path construction using
                default current (None), 'local' or 'remote' mode. Defaults to None.
            session_details (pd.Series, optional): The pandas series containing
                data_dataset_session_related information.
                If not supplied, the function must be supplied a session id (eid, first argument)
                to get this session_details variable internally. Defaults to None.

            Other filtering keys are accepted :
                (the value on wich you search must match perfectly. Key must also match any of the
                below. key and values are case sensitive)
                - extra
                - object
                - attribute
                - subject
                - date
                - number
                - revision
                - collection
                - extension

                - dataset_type
                - name (from dataset, ex trials.eventTimelines)
                - file_name

                - relative_path
                - exists
                - created_by
                - created_datetime
                - data_repository
                - hash
                - file_size
                - tags

        Raises:
            NotImplementedError: For now, using mode (metadata) local will not work as the cache system to retrive data
                has not been tested for.

        Returns:
            Union[list, pd.DataFrame]: A list of matching files full_paths (if details = False) or a dataframe of all
                matching file records components and metadata.
        """

        import natsort

        _logger = logging.getLogger("list_datasets")

        def flatten_pathlist(x):
            if isinstance(x, (list, tuple)):
                return natsort.natsorted([a for i in x for a in flatten(i)])
            else:
                return natsort.natsorted([x])

        # OBTAINING A SESSION_DETAILS (IN WHICH RESIDES FILES INFO)

        data_access_mode = as_mode or self.data_access_mode
        if hasattr(eid, "keys") and "data_dataset_session_related" in eid.keys():
            session_details = eid.copy()  # either a pd.series of dict, copy and key getters/setters works both cases
            try:
                eid = session_details["id"]
            except KeyError:
                eid = session_details.name
            session_details["id"] = eid
        else:
            _eid = eid
            # Ensure we have a UUID str
            eid = self.to_eid(eid)
            if eid is None:
                raise ValueError(
                    f"The session id {_eid} supplied seem to not be existing. Check that you are in 'remote' mode "
                    "(not data_access_mode) and check that the session exists on a webpage."
                )
            if (query_type or self.mode) != "remote":
                raise NotImplementedError
                ## TODO : GET BACK THE DATA FROM THE CACHE AFTER CHANGE IN DATA MANAGEMENT METHODOLOGY
                # return super().list_datasets(eid, details=details, query_type=query_type, **filters)

            session_details = self.to_session_details(
                self.alyx.rest("sessions", "read", id=eid, query_type=query_type, no_cache=no_cache)
            )
        # session, datasets = util.ses2records(self.alyx.rest('sessions', 'read', id=eid))
        # self._update_cache_from_records(sessions=session, datasets=datasets.copy()
        # if datasets is not None else datasets)
        # Add to cache tables # TODO : DO ADD THAT FUNCTIONNALITY AGAIN

        datasets = copy.deepcopy(session_details["data_dataset_session_related"])
        # copy to not change the session_details in case they are suplied by user as input

        file_records = []
        for dataset in datasets:
            files = dataset.pop("file_records")
            dataset["session#"] = dataset.pop("session")
            dataset["dataset#"] = dataset.pop("id")
            dataset["dataset_url"] = dataset.pop("url")
            dataset["dataset_admin_url"] = dataset.pop("admin_url")
            dataset["local_root"] = one.params.get().LOCAL_ROOT
            for file in files:
                # file["file_url"] = file.pop("url")
                # file["file_admin_url"] = file.pop("admin_url")
                file["file#"] = file.pop("id")
                file.update(
                    {
                        "remote_full_path": os.path.normpath(
                            os.path.join(dataset["remote_root"], file["relative_path"])
                        ),
                        "local_full_path": os.path.normpath(os.path.join(dataset["local_root"], file["relative_path"])),
                    }
                )
                # remote or local depending on current mode
                file["full_path"] = file[data_access_mode + "_full_path"]
                file.update(dataset)
                file_records.append(file)

        dataframe = pd.DataFrame(file_records)
        if dataframe.empty:
            _logger.warning("No dataset found for this session. Are you sure you ran the file registration routine ?")
            return dataframe if details else []

        dataframe.set_index(["session#", "dataset#", "file#"])

        # FILTERING THE ROWS BASED ON USER INPUT
        # query_string = ' & '.join([f'{k} == {repr(v)}' for k, v in filters.items()])
        if filters:
            if "filename" in filters.keys():
                # allowing filename for retrocompatibility
                filters["file_name"] = filters.pop("filename")
            queries = []
            for key, value in filters.items():
                # if the columns correspunding to filter is object or string, we try to match using wildcard type
                # syntax as input
                # to do that we convert * wildcards to .* in regex and add a ^ and $ at start and end of pattern
                # to force a complete string length match.

                if dataframe[key].dtype is npdtype("O") or dataframe[key].dtype is npdtype(str):
                    queries.append(f"{key}.str.match('^' + {repr(value).replace('*', '.*')} + '$') ")  #
                # if the columns is not a sting, we match it directly.
                else:
                    queries.append(f"{key} == {value}")

            # example of a query string : query_string = "exists == True & subject.str.match('wm.*$')"
            query_string = " & ".join(queries)

            try:
                dataframe = dataframe.query(query_string, engine="python")
            except (KeyError, pd.errors.UndefinedVariableError) as e:
                raise KeyError(f"Cannot use the key {str(e)} to filter for datasets")

        return dataframe if details else list(dataframe["full_path"])

    @util.refresh
    def pid2eid(self, pid: str, query_type=None) -> Tuple[str, str]:
        """
        Given an Alyx probe UUID string, returns the session id string and the probe label
        (i.e. the ALF collection).

        NB: Requires a connection to the Alyx database.

        Parameters
        ----------
        pid : str, uuid.UUID
            A probe UUID
        query_type : str
            Query mode - options include 'remote', and 'refresh'

        Returns
        -------
        str
            Experiment ID (eid)
        str
            Probe label
        """
        query_type = query_type or self.mode
        if query_type != "remote":
            self.refresh_cache(query_type)
        if query_type == "local" and "insertions" not in self._cache.keys():
            raise NotImplementedError("Converting probe IDs required remote connection")
        rec = self.alyx.rest("insertions", "read", id=str(pid))
        return rec["session"], rec["name"]

    #### SEARCH
    def search(
        self,
        id=None,
        *,
        details=False,
        query_type=None,
        as_mode=None,
        no_cache=False,
        **kwargs,
    ):
        """
        Searches sessions matching the given criteria and returns a list of matching eids

        For a list of search terms, use the method

            one.search_terms(query_type='remote')

        For all of the search parameters, a single value or list may be provided.  For dataset,
        the sessions returned will contain all listed datasets.  For the other parameters,
        the session must contain at least one of the entries. NB: Wildcards are not permitted,
        however if wildcards property is False, regular expressions may be used for all but
        number and date_range.

        Parameters
        ----------
        dataset : str, list
            List of dataset names. Returns sessions containing all these datasets.
            A dataset matches if it contains the search string e.g. 'wheel.position' matches
            '_ibl_wheel.position.npy'
        date_range : str, list, datetime.datetime, datetime.date, pandas.timestamp
            A single date to search or a list of 2 dates that define the range (inclusive).  To
            define only the upper or lower date bound, set the other element to None.
        lab : str, list
            A str or list of lab names, returns sessions from any of these labs
        number : str, int
            Number of session to be returned, i.e. number in sequence for a given date
        subject : str, list
            A list of subject nicknames, returns sessions for any of these subjects
        task_protocol : str, list
            The task protocol name (can be partial, i.e. any task protocol containing that str
            will be found)
        project(s) : str, list
            The project name (can be partial, i.e. any task protocol containing that str
            will be found)
        performance_lte / performance_gte : float
            Search only for sessions whose performance is less equal or greater equal than a
            pre-defined threshold as a percentage (0-100)
        users : str, list
            A list of users
        location : str, list
            A str or list of lab location (as per Alyx definition) name
            Note: this corresponds to the specific rig, not the lab geographical location per se
        json : dict
            example :
                ```python
                    json = {
                        "whisker_stims": {
                            "amplitudes": {
                                "abstract__contains": ['10-0','10-10','10_90-0']
                                }
                            }
                        }
                ```
            allowed filter looups are listed here :
            https://docs.djangoproject.com/en/5.0/ref/models/querysets/#field-lookups


        dataset_types : str, list
            One or more of dataset_types
        details : bool
            If true also returns a dict of dataset details
        query_type : str, None
            Query cache ('local') or Alyx database ('remote')
        limit : int
            The number of results to fetch in one go (if pagination enabled on server)




        Returns
        -------
        list
            List of eids
        (list of dicts)
            If details is True, also returns a list of dictionaries, each entry corresponding to a
            matching session
        """
        _logger = logging.getLogger("search")
        query_type = query_type or self.mode  # we set query_type = self.mode if query_type is None
        if query_type != "remote":
            return super().search(details=details, query_type=query_type, **kwargs)

        # loop over input arguments and build the url
        search_terms = self.search_terms(query_type=query_type)
        params = {}  # {"django": kwargs.pop("django", "")}

        if isinstance(id, (list, tuple)):
            sessions = []
            for i in id:
                sessions.append(
                    self.search(
                        id=i, details=details, as_mode=as_mode, no_cache=no_cache, query_type=query_type, **kwargs
                    )
                )
            return pd.DataFrame(sessions)

        if id is not None:
            params["id"] = self.to_eid(id)
            if params["id"] is None:  # this means the id we supplied is not a valid eid
                raise ValueError(f"{id} is not a valid identifier, could not convert it to session uuid.")
        _logger.debug(f"kwargs : {kwargs}")
        for key, value in sorted(kwargs.items()):
            field = util.autocomplete(key, search_terms)  # Validate and get full name
            _logger.debug(f"field : {field}")
            # check that the input matches one of the defined filters
            if field == "date_range":
                params[field] = [x.date().isoformat() for x in util.validate_date_range(value)]
            elif field == "laboratory":
                params["lab"] = value
            else:
                params[field] = value
        _logger.debug(f"params : {params}")
        # Make GET request
        ses = self.alyx.rest(
            self._search_endpoint,
            "list",
            no_cache=no_cache,
            query_type=query_type,
            **params,
        )
        if len(ses) > 1:
            _logger.info(f"Found {len(ses)} sessions.")

        if not details:
            return [item["id"] for item in ses]
            # list(util.LazyId(ses))

        sess_df = []
        for session_short_info in tqdm(ses, total=len(ses), delay=2, desc="Loading session details", file=stdout):
            session_detailed_info = self.alyx.rest(
                "sessions",
                "read",
                id=session_short_info["id"],
                no_cache=no_cache,
                query_type=query_type,
            )
            formated_session = self.to_session_details(session_detailed_info, as_mode=as_mode)
            sess_df.append(formated_session)

        try:
            sess_df = pd.DataFrame(sess_df)
            sess_df.index = sess_df.index.set_names("id")
            sess_df["start_datetime"] = pd.to_datetime(sess_df["start_datetime"], utc=True)
        except (ValueError, KeyError):
            warnings.warn("search result contained no entry")
            pass  # could not create a dataframe form session details. Returning dict instead
        if id is not None:
            return sess_df.iloc[0]
        return sess_df

    def to_session_details(self, session_dict, as_mode=None):
        data_access_mode = (
            as_mode or self.data_access_mode
        )  # we set data_access_mode = self.data_access_mode if as_mode is None

        session_dict["date"] = str(datetime.fromisoformat(session_dict["start_time"]).date())
        session_dict["extended_qc"] = session_dict["extended_qc"] if session_dict["extended_qc"] is not None else {}
        session_dict["local_path"] = os.path.normpath(
            os.path.join(one.params.get().LOCAL_ROOT, session_dict["rel_path"])
        )
        session_dict["remote_path"] = os.path.normpath(
            session_dict["path"]
        )  # path is the remote path initially (out from the database)
        # we set path depending on the data_access_mode
        session_dict["path"] = session_dict[data_access_mode + "_path"]
        session_dict["rel_path"] = Path(session_dict["rel_path"])

        session_dict["start_datetime"] = datetime.strptime(session_dict["start_time"], "%Y-%m-%dT%H:%M:%S%z")

        id = session_dict.pop("id")

        session_details = pd.Series(session_dict, name=id)
        return session_details

    def _download_datasets(self, dsets, **kwargs) -> List[Path]:
        """
        Download a single or multitude of datasets if stored on AWS, otherwise calls
        OneAlyx._download_dataset.

        NB: This will not skip files that are already present.  Use check_filesystem instead.

        Parameters
        ----------
        dset : dict, str, pd.Series
            A single or multitude of dataset dictionaries

        Returns
        -------
        pathlib.Path
            A local file path or list of paths
        """
        # If all datasets exist on AWS, download from there.
        _logger = logging.getLogger("_download_datasets")
        try:
            if "exists_aws" in dsets and np.all(np.equal(dsets["exists_aws"].values, True)):
                _logger.info("Downloading from AWS")
                return self._download_aws(map(lambda x: x[1], dsets.iterrows()), **kwargs)
        except Exception as ex:
            _logger.debug(ex)
        return self._download_dataset(dsets, **kwargs)

    def _dset2url(self, dset, update_cache=True):
        """
        Converts a dataset into a remote HTTP server URL.  The dataset may be one or more of the
        following: a dict from returned by the sessions endpoint or dataset endpoint, a record
        from the datasets cache table, or a file path.  Unlike record2url, this method can convert
        dicts and paths to URLs.

        Parameters
        ----------
        dset : dict, str, pd.Series, pd.DataFrame, list
            A single or multitude of dataset dictionary from an Alyx REST query OR URL string
        update_cache : bool
            If True (default) and the dataset is from Alyx and cannot be converted to a URL,
            'exists' will be set to False in the corresponding entry in the cache table.

        Returns
        -------
        str
            The remote URL of the dataset
        """
        _logger = logging.getLogger("_dset2url")
        did = None
        if isinstance(dset, str) and dset.startswith("http"):
            url = dset
        elif isinstance(dset, (str, Path)):
            url = self.path2url(dset)
            if not url:
                _logger.warning(f"Dataset {dset} not found in cache")
                return
        elif isinstance(dset, (list, tuple)):
            dset2url = partial(self._dset2url, update_cache=update_cache)
            return list(flatten(map(dset2url, dset)))
        else:
            # check if dset is dataframe, iterate over rows
            if hasattr(dset, "iterrows"):
                dset2url = partial(self._dset2url, update_cache=update_cache)
                url = list(map(lambda x: dset2url(x[1]), dset.iterrows()))
            elif "data_url" in dset:  # data_dataset_session_related dict
                url = dset["data_url"]
                did = dset["id"]
            elif "file_records" not in dset:  # Convert dataset Series to alyx dataset dict
                url = self.record2url(dset)  # NB: URL will always be returned but may not exist
                is_int = all(isinstance(x, (int, np.int64)) for x in util.ensure_list(dset.name))
                did = np.array(dset.name)[-2:] if is_int else util.ensure_list(dset.name)[-1]
            else:  # from datasets endpoint
                repo = getattr(getattr(self._web_client, "_par", None), "HTTP_DATA_SERVER", None)
                url = next(
                    (
                        fr["data_url"]
                        for fr in dset["file_records"]
                        if fr["data_url"] and fr["exists"] and fr["data_url"].startswith(repo or fr["data_url"])
                    ),
                    None,
                )
                did = dset["url"][-36:]

        # Update cache if url not found
        if did is not None and not url and update_cache:
            _logger.debug("Updating cache")
            if isinstance(did, str) and self._index_type("datasets") is int:
                (did,) = parquet.str2np(did).tolist()
            elif self._index_type("datasets") is str and not isinstance(did, str):
                did = parquet.np2str(did)
            # NB: This will be considerably easier when IndexSlice supports Ellipsis
            idx = [slice(None)] * int(self._cache["datasets"].index.nlevels / 2)
            self._cache["datasets"].loc[(*idx, *util.ensure_list(did)), "exists"] = False
            self._cache["_meta"]["modified_time"] = datetime.now()

        return url

    def _download_dataset(self, dset, cache_dir=None, update_cache=True, **kwargs) -> List[Path | None]:
        """
        Download a single or multitude of dataset from an Alyx REST dictionary.

        NB: This will not skip files that are already present.  Use check_filesystem instead.

        Parameters
        ----------
        dset : dict, str, pd.Series, pd.DataFrame, list
            A single or multitude of dataset dictionary from an Alyx REST query OR URL string
        cache_dir : str, pathlib.Path
            The root directory to save the data to (default taken from ONE parameters)
        update_cache : bool
            If true, the cache is updated when filesystem discrepancies are encountered

        Returns
        -------
        pathlib.Path, list
            A local file path or list of paths
        """
        cache_dir = cache_dir or self.cache_dir
        url = self._dset2url(dset, update_cache=update_cache)
        if not url:
            return
        if isinstance(url, str):
            target_dir = str(Path(cache_dir, get_alf_path(url)).parent)
            return self._download_file(url, target_dir, **kwargs)
        # must be list of URLs
        valid_urls = list(filter(None, url))
        if not valid_urls:
            return [None] * len(url)
        target_dir = [str(Path(cache_dir, get_alf_path(x)).parent) for x in valid_urls]
        files = self._download_file(valid_urls, target_dir, **kwargs)
        # Return list of file paths or None if we failed to extract URL from dataset
        return [None if not x else files.pop(0) for x in url]

    def _tag_mismatched_file_record(self, url):
        fr = self.alyx.rest(
            "files",
            "list",
            django=f'dataset,{Path(url).name.split(".")[-2]},data_repository__globus_is_personal,False',
            no_cache=True,
        )
        if len(fr) > 0:
            json_field = fr[0]["json"]
            if json_field is None:
                json_field = {"mismatch_hash": True}
            else:
                json_field.update({"mismatch_hash": True})
            try:
                self.alyx.rest(
                    "files",
                    "partial_update",
                    id=fr[0]["url"][-36:],
                    data={"json": json_field},
                )
            except requests.exceptions.HTTPError as ex:
                warnings.warn(
                    f"Failed to tag remote file record mismatch: {ex}\nPlease contact the database administrator."
                )

    def _download_file(self, url, target_dir, keep_uuid=False, file_size=None, hash=None) -> Path | List[Path]:
        """
        Downloads a single file or multitude of files from an HTTP webserver.
        The webserver in question is set by the AlyxClient object.

        Parameters
        ----------
        url : str, list
            An absolute or relative URL for a remote dataset
        target_dir : str, list
            Absolute path of directory to download file to (including alf path)
        keep_uuid : bool
            If true, the UUID is not removed from the file name (default is False)
        file_size : int, list
            The expected file size or list of file sizes to compare with downloaded file
        hash : str, list
            The expected file hash or list of file hashes to compare with downloaded file

        Returns
        -------
        pathlib.Path
            The file path of the downloaded file or files.

        Example
        -------
        >>> file_path = OneAlyx._download_file(
        ...    'https://example.com/data.file.npy', '/home/Downloads/subj/1900-01-01/001/alf')
        """
        assert not self.offline
        # Ensure all target directories exist
        [Path(x).mkdir(parents=True, exist_ok=True) for x in set(util.ensure_list(target_dir))]

        # download file(s) from url(s), returns file path(s) with UUID
        local_path, md5 = self.alyx.download_file(url, target_dir=target_dir, return_md5=True)

        # check if url, hash, and file_size are lists
        if isinstance(url, (tuple, list)):
            assert (file_size is None) or len(file_size) == len(url)
            assert (hash is None) or len(hash) == len(url)
        for args in zip(*map(util.ensure_list, (file_size, md5, hash, local_path, url))):
            self._check_hash_and_file_size_mismatch(*args)

        # check if we are keeping the uuid on the list of file names
        if keep_uuid:
            return local_path

        # remove uuids from list of file names
        if isinstance(local_path, (list, tuple)):
            return [alfio.remove_uuid_file(x) for x in local_path]
        else:
            return alfio.remove_uuid_file(local_path)

    def _check_hash_and_file_size_mismatch(self, file_size, hash, expected_hash, local_path, url):
        """
        Check to ensure the hash and file size of a downloaded file matches what is on disk

        Parameters
        ----------
        file_size : int
            The expected file size to compare with downloaded file
        hash : str
            The expected file hash to compare with downloaded file
        local_path: str
            The path of the downloaded file
        url : str
            An absolute or relative URL for a remote dataset
        """
        _logger = logging.getLogger("_check_hash_and_file_size_mismatch")
        # verify hash size
        hash = hash or hashfile.md5(local_path)
        hash_mismatch = hash and hash != expected_hash
        # verify file size
        file_size_mismatch = file_size and Path(local_path).stat().st_size != file_size
        # check if there is a mismatch in hash or file_size
        if hash_mismatch or file_size_mismatch:
            # post download, if there is a mismatch between Alyx and the newly downloaded file size
            # or hash, flag the offending file record in Alyx for database for maintenance
            hash_mismatch = expected_hash and expected_hash != hash
            file_size_mismatch = file_size and Path(local_path).stat().st_size != file_size
            if hash_mismatch or file_size_mismatch:
                url = url or self.path2url(local_path)
                _logger.debug(f"Tagging mismatch for {url}")
                # tag the mismatched file records
                self._tag_mismatched_file_record(url)

    @staticmethod
    def setup(base_url=None, **kwargs):
        """
        Set up OneAlyx for a given database

        Parameters
        ----------
        base_url : str
            An Alyx database URL.  If None, the current default database is used.
        **kwargs
            Optional arguments to pass to one.params.setup.

        Returns
        -------
        OneAlyx
            An instance of OneAlyx for the newly set up database URL
        """
        base_url = base_url or one.params.get_default_client()
        cache_map = one.params.setup(client=base_url, **kwargs)
        return OneAlyx(base_url=base_url or one.params.get(cache_map.DEFAULT).ALYX_URL)

    @util.refresh
    @util.parse_id
    def eid2path(self, eid, query_type=None) -> util.Listable(Path):
        """
        From an experiment ID gets the local session path

        Parameters
        ----------
        eid : str, UUID, pathlib.Path, dict, list
            Experiment session identifier; may be a UUID, URL, experiment reference string
            details dict or Path.
        query_type : str
            If set to 'remote', will force database connection

        Returns
        -------
        pathlib.Path, list
            A session path or list of session paths
        """

        # first try avoid hitting the database
        mode = query_type or self.mode
        if mode != "remote":
            cache_path = super().eid2path(eid)
            if cache_path or mode == "local":
                return cache_path

        # If eid is a list recurse through it and return a list
        if isinstance(eid, list):
            unwrapped = unwrap(self.path2eid)
            return [unwrapped(self, e, query_type="remote") for e in eid]

        # if it wasn't successful, query Alyx
        ses = self.alyx.rest("sessions", "list", django=f"pk,{eid}")

        if len(ses) == 0:
            return None

        data_repository = ses[0]["default_data_repository"]
        if data_repository is None:
            data_repository = ""

        return os.path.join(
            data_repository,
            ses[0]["subject"],
            ses[0]["start_time"][:10],
            str(ses[0]["number"]).zfill(3),
        )

    @util.refresh
    def path2eid(self, path_obj: Union[str, Path], query_type=None) -> util.Listable(Path):
        import re

        """
        From a local path, gets the experiment ID

        Parameters
        ----------
        path_obj : str, pathlib.Path, list
            Local path or list of local paths
        query_type : str
            If set to 'remote', will force database connection

        Returns
        -------
        str, list
            An eid or list of eids
        """
        # If path_obj is a list recurse through it and return a list
        if isinstance(path_obj, list):
            path_obj = [Path(x) for x in path_obj]
            eid_list = []
            unwrapped = unwrap(self.path2eid)
            for p in path_obj:
                eid_list.append(unwrapped(self, p))
            return eid_list
        # else ensure the path ends with mouse,date, number
        try:
            path_obj = re.findall(r"(\w+(?:\\|\/)\d{4}-\d{2}-\d{2}(?:\\|\/)\d+)", path_obj)[0]
        except IndexError:  # could not match anything
            pass

        path_obj = Path(path_obj)

        # try the cached info to possibly avoid hitting database
        mode = query_type or self.mode
        if mode != "remote":
            cache_eid = super().path2eid(path_obj)
            if cache_eid or mode == "local":
                return cache_eid

        session_path = get_session_path(path_obj)
        # if path does not have a date and a number return None
        if session_path is None:
            return None

        # if not search for subj, date, number XXX: hits the DB
        search = unwrap(self.search)
        uuid = search(
            subject=session_path.parts[-3],
            date_range=session_path.parts[-2],
            number=session_path.parts[-1],
            query_type="remote",
        )

        # Return the uuid if any
        return uuid[0] if uuid else None

    @util.refresh
    def path2url(self, filepath, query_type=None) -> str:
        """
        Given a local file path, returns the URL of the remote file.

        Parameters
        ----------
        filepath : str, pathlib.Path
            A local file path
        query_type : str
            If set to 'remote', will force database connection

        Returns
        -------
        str
            A URL string
        """
        query_type = query_type or self.mode
        if query_type != "remote":
            return super().path2url(filepath)
        eid = self.path2eid(filepath)
        try:
            (dataset,) = self.alyx.rest("datasets", "list", session=eid, name=Path(filepath).name)
            return next(r["data_url"] for r in dataset["file_records"] if r["data_url"] and r["exists"])
        except (ValueError, StopIteration):
            raise alferr.ALFObjectNotFound(f"File record for {filepath} not found on Alyx")

    @util.parse_id
    def type2datasets(self, eid, dataset_type, details=False):
        """
        Get list of datasets belonging to a given dataset type for a given session

        Parameters
        ----------
        eid : str, UUID, pathlib.Path, dict
            Experiment session identifier; may be a UUID, URL, experiment reference string
            details dict or Path.
        dataset_type : str, list
            An Alyx dataset type, e.g. camera.times or a list of dtypes
        details : bool
            If True, a datasets DataFrame is returned

        Returns
        -------
        np.ndarray, dict
            A numpy array of data, or DataFrame if details is true
        """
        assert self.mode != "local" and not self.offline, "Unable to connect to Alyx in local mode"
        if isinstance(dataset_type, str):
            restriction = f"session__id,{eid},dataset_type__name,{dataset_type}"
        elif isinstance(dataset_type, collections.abc.Sequence):
            restriction = f"session__id,{eid},dataset_type__name__in,{dataset_type}"
        else:
            raise TypeError("dataset_type must be a str or str list")
        datasets = util.datasets2records(self.alyx.rest("datasets", "list", django=restriction))
        return datasets if details else datasets["rel_path"].sort_values().values

    def dataset2type(self, dset) -> str:
        """Return dataset type from dataset.

        NB: Requires an Alyx database connection

        Parameters
        ----------
        dset : str, np.ndarray, tuple
            A dataset name, dataset uuid or dataset integer id

        Returns
        -------
        str
            The dataset type
        """
        assert self.mode != "local" and not self.offline, "Unable to connect to Alyx in local mode"
        # Ensure dset is a str uuid
        if isinstance(dset, str) and not is_uuid_string(dset):
            dset = self._dataset_name2id(dset)
        if isinstance(dset, np.ndarray):
            dset = parquet.np2str(dset)[0]
        if isinstance(dset, tuple) and all(isinstance(x, int) for x in dset):
            dset = parquet.np2str(np.array(dset))
        if not is_uuid_string(dset):
            raise ValueError("Unrecognized name or UUID")
        return self.alyx.rest("datasets", "read", id=dset)["dataset_type"]

    def describe_revision(self, revision, full=False):
        """Print description of a revision

        Parameters
        ----------
        revision : str
            The name of the revision (without '#')
        full : bool
            If true, returns the matching record

        Returns
        -------
        None, dict
            None if full is false or no record found, otherwise returns record as dict
        """
        assert self.mode != "local" and not self.offline, "Unable to connect to Alyx in local mode"
        try:
            rec = self.alyx.rest("revisions", "read", id=revision)
            print(rec["description"])
            if full:
                return rec
        except requests.exceptions.HTTPError as ex:
            if ex.response.status_code != 404:
                raise ex
            print(f'revision "{revision}" not found')

    def _dataset_name2id(self, dset_name, eid=None):
        # TODO finish function
        datasets = self.list_datasets(eid) if eid else self._cache["datasets"]
        # Get ID of fist matching dset
        for idx, rel_path in datasets["rel_path"].items():
            if rel_path.endswith(dset_name):
                return idx[-1]  # (eid, did)
        raise ValueError(f"Dataset {dset_name} not found in cache")

    @util.refresh
    @util.parse_id
    def get_details(self, eid: str, full: bool = False, query_type=None):
        """Return session details for a given session

        Parameters
        ----------
        eid : str, UUID, pathlib.Path, dict, list
            Experiment session identifier; may be a UUID, URL, experiment reference string
            details dict or Path.
        full : bool
            If True, returns a DataFrame of session and dataset info
        query_type : {'local', 'refresh', 'auto', 'remote'}
            The query mode - if 'local' the details are taken from the cache tables; if 'remote'
            the details are returned from the sessions REST endpoint; if 'auto' uses whichever
            mode ONE is in; if 'refresh' reloads the cache before querying.

        Returns
        -------
        pd.Series, pd.DataFrame, dict
            in local mode - a session record or full DataFrame with dataset information if full is
            True; in remote mode - a full or partial session dict

        Raises
        ------
        ValueError
            Invalid experiment ID (failed to parse into eid string)
        requests.exceptions.HTTPError
            [Errno 404] Remote session not found on Alyx
        """
        if (query_type or self.mode) == "local":
            return super().get_details(eid, full=full)
        # If eid is a list of eIDs recurse through list and return the results
        if isinstance(eid, list):
            details_list = []
            for p in eid:
                details_list.append(self.get_details(p, full=full))
            return details_list
        # load all details
        dets = self.alyx.rest("sessions", "read", eid)
        if full:
            return dets
        # If it's not full return the normal output like from a one.search
        det_fields = [
            "subject",
            "start_time",
            "number",
            "lab",
            "projects",
            "url",
            "task_protocol",
            "local_path",
        ]
        out = {k: v for k, v in dets.items() if k in det_fields}
        out["projects"] = ",".join(out["projects"])
        out.update(
            {
                "local_path": self.eid2path(eid),
                "date": datetime.fromisoformat(out["start_time"]).date(),
            }
        )
        return out

    ### METHODS ADDED BY TIMOTHE TO EXTEND THE USE OF THE API

    def get_data_repository_path(self, repository_name):
        repo_data = self.alyx.rest("data-repository", "read", repository_name)
        repo_path = r"\\" + os.path.join(repo_data["hostname"], repo_data["globus_path"].lstrip("/"))
        return os.path.normpath(repo_path)

    def read_sql(self, query):
        raise NotImplementedError

    def create_session(self, data_dict):
        import re

        data_dict = data_dict.copy()
        data_dict["number"] = str(int(data_dict["number"]))  # remove leading zeros if any
        try:  # a session have been found with same 3 parameters, don't allow to process to registering.
            _searched_session = self.search(
                subject=data_dict["subject"],
                number=data_dict["number"],
                date_range=data_dict["start_time"][:10],
                users=data_dict["users"],
                details=True,
            )
            url = re.sub(r"\/sessions", r"/actions/session", _searched_session["url"].item())
            raise ValueError(
                f"This session path is already registered ! See here : {url}\n You probably need to change the number "
                "of the session for this animald/date combo"
            )
        except KeyError:  # no session have benn found, all is well, can proceed
            pass

        self.alyx.rest("sessions", "create", data=data_dict)

    def default_session_data(self):
        return {"lab": "HaissLab", "task_protocol": ""}

    @staticmethod
    def explorer(path):
        import subprocess

        path = os.path.normpath(path)
        if not os.path.exists(path):
            raise IOError(f"Path {path} does not exist")
        if os.path.isfile(path):
            path = os.path.dirname(path)

        pre_cmd = f"explorer.exe {path}"
        subprocess.call(pre_cmd)
        return
        # this part is to open in atab instead of window. Buggy for now.
        if path[:2] == r"\\":
            pre_cmd = f"pushd {path}; "
            # path = os.path.join(*[item for item in selected_folder.split(os.sep) if item != ''][1:])
            # #this removes the first object of the path
        else:
            pre_cmd = 'cd "{pateh}; "'
        subprocess.call(pre_cmd + r"start .")
        # subprocess.Popen(r'explorer '+f'{os.path.dirname(path)},select,{os.path.basename(path)}')

    def collection_path(self, eid, collections):
        return os.path.join(self.current_remote_repository_path, self.eid2path(eid), collections)

    def set_current_remote_repository(self, repo_name):
        self.current_remote_repository_path = self.get_data_repository_path(repo_name)

    def get_parts_from_path(self, input_path):
        import re

        subject, date, number = re.findall(r"(\w+)(?:\\|\/)(\d{4}-\d{2}-\d{2})(?:\\|\/)(\d+)", input_path)[0]
        return {"subject": subject, "date": date, "number": number}

    def get_json_params(self, eid):
        return self.alyx.rest("sessions", "read", eid)["json"]

    def get_extended_qc(self, eid):
        return self.alyx.rest("sessions", "read", eid)["extended_qc"]

    def display_session_info(self, session_details):
        from IPython.display import Markdown, display

        session_data_link = (
            f"[Data pathes](file:{os.path.normpath(session_details.path)}) pointing in {self.data_access_mode} mode."
        )
        metadatas_link = f"[Metadatas]({session_details.url}) obtained in {self.mode} mode."
        uuid = f"(uuid is '{session_details.name}')"
        display(Markdown(f"Session {session_details.rel_path}. {session_data_link} {metadatas_link} {uuid}"))

    @wraps(MultiSessionPlaceholder)
    def multisession(*args, **kwargs):
        return MultiSessionPlaceholder(*args, **kwargs)

    def update_session_info(self, session_details, json={}, extended_qc={}, **kwargs):
        _logger = logging.getLogger("update_session_info")

        data = {}
        if len(json):
            base_json = session_details.json
            if base_json is None:
                base_json = {}
            base_json.update(json)
            data["json"] = base_json

        if len(extended_qc):
            base_ext_qc = session_details.extended_qc
            if base_ext_qc is None:
                base_ext_qc = {}
            base_ext_qc.update(extended_qc)
            data["extended_qc"] = base_ext_qc

        data.update(kwargs)

        _logger.info(f"Updating session {session_details.rel_path}")
        ans = input(f"Data OK ? (OK or Cancel) : {data}")
        if ans != "OK":
            _logger.info("Aborting")
            return
        _logger.info("Applying changes")
        self.alyx.rest("sessions", "partial_update", id=session_details.name, data=data)
        self.alyx.delete_cache()

    def push_files(self, file_list, *, session_details, relative=False, overwrite_policy="raise"):
        """
        The function push_processed_files copies a list of files to a remote session directory,
        and handles various overwrite policies.

        Args:

            - file_list (list of str): a list of local file paths to be copied to the remote session directory.
            - session_details (SessionDetails): an object containing the session details.
            - relative: If the string representing the path of the files are absolute or relative from
                INSIDE the session folder (ex : 'D:\\LOCAL_DATA\\wm25\\2022-08-05\\001\\a_folder\\test.file'
                is an absolute path and 'a_folder\test.file' is a relative path)
            - overwrite_policy (str, optional): the overwrite policy. Possibilities are:
              - "raise" (raise an exception if a file with the same name already exists in the remote directory),
              - "skip" (skip copying the file if a file with the same name already exists in the remote directory),
              - "overwrite" (overwrite the file with the same name in the remote directory),
              - "most_recent" (copy the file only if it is more recent than the file
                 with the same name in the remote directory),
              - "erase" (erase the existing file in the remote directory if a file with the same name already exists,
                then copy a new one in place.) Prefer choosing overwrite in most situations).

            Default is "raise".

        Returns:

            a dictionary with two keys:
              - "copied" : a list of file paths that have been copied to the remote session directory.
              - "ignored" : a list of file paths that could not be copied (either because they were not found or
                because they were excluded by the overwrite policy).
        """

        import shutil

        def get_relative_path(absolute_path, common_root_path):
            """Compare an input path and a path with a common root with the input path, and returns only the part of
            the input path that is not shared with the _common_root_path.

            Args:
                input_path (TYPE): DESCRIPTION.
                common_root_base (TYPE): DESCRIPTION.

            Returns:
                TYPE: DESCRIPTION.

            """
            absolute_path = os.path.normpath(absolute_path)
            common_root_path = os.path.normpath(common_root_path)

            commonprefix = os.path.commonprefix([common_root_path, absolute_path])
            if commonprefix == "":
                raise IOError(f"These two pathes have no common root path : {absolute_path} and {common_root_path}")
            return os.path.relpath(absolute_path, start=commonprefix)

        overwrite_policies = ["raise", "skip", "erase", "most_recent", "overwrite"]
        if overwrite_policy not in overwrite_policies:
            raise NotImplementedError(
                f"Value {overwrite_policy} for overwrite_policy is not supported. "
                f"Possibilities are : {overwrite_policies}"
            )

        session_local_root = session_details["local_path"]
        session_remote_root = session_details["remote_path"]

        if not isinstance(file_list, (list, tuple, np.ndarray)):
            file_list = [file_list]

        if relative:
            file_list = [os.path.join(session_local_root, session_details.rel_path, file) for file in file_list]

        source_files = []  # file paths that have been copied
        # files paths of the copies of the one in the list above (ordered similarly)
        dest_files = []

        overwrite_raise_list = (
            []
        )  # stores files that already exist in remote folder in case we have 'raise' overwrite_policy, to print to user
        file_not_found_warning = []

        # MAKE THE LIST OF FILES THAT WILL BE COPIED
        for local_path in file_list:
            if not os.path.isfile(local_path):
                file_not_found_warning.append(local_path)
                continue

            try:
                relative_path = get_relative_path(local_path, session_local_root)
            except IOError:
                file_not_found_warning.append(local_path)
                continue

            remote_path = os.path.join(session_remote_root, relative_path)

            if os.path.isfile(remote_path):
                if overwrite_policy == "raise":
                    overwrite_raise_list.append(local_path)
                elif overwrite_policy == "skip":
                    logging.getLogger().info(f"Skipping file {remote_path}")
                    continue
                elif overwrite_policy == "erase":
                    os.remove(remote_path)
                elif overwrite_policy == "most_recent":
                    if (
                        os.stat(remote_path).st_mtime > os.stat(local_path).st_mtime
                    ):  # remote_path is more recent (higher time value)
                        continue
                elif overwrite_policy == "overwrite":
                    pass
                    # else if overwrite, we just do nothing and files will be overwritten during shutil.copy operation

            source_files.append(local_path)
            dest_files.append(remote_path)

        if len(file_not_found_warning):
            logging.getLogger().warning(
                "These files were not found or are not inside the session local folder, "
                f"and therefore cannot be copied :\nFiles:\n{file_not_found_warning}"
            )

        if len(overwrite_raise_list):
            raise IOError(
                f"The files listed below already exist in the destination : {session_remote_root}\n"
                "No file have been copied to avoid mistakes. "
                "You can check the content of the destination folder manually, "
                "or change the 'overwrite_policy' argument of this function (beware of data losses !).\n"
                f"Files :\n {overwrite_raise_list}"
            )

        # APPLY THE COPY
        for local_path, remote_path in zip(source_files, dest_files):
            container_dir = os.path.dirname(remote_path)
            # make destination dir if it doesn't exist already
            os.makedirs(container_dir, exist_ok=True)
            shutil.copy(local_path, remote_path)

        return {
            "copied": source_files,
            "ignored": list(set(file_list).difference(set(source_files))),
        }

    def copy_files(self, source_file_list, source_root, destination_root, overwrite_policy="raise"):
        def get_relative_path(absolute_path, common_root_path):
            """
            Compare an input path and a path with a common root with the input path, and returns only the part of the
            input path that is not shared with the _common_root_path.

            Args:
                input_path (TYPE): DESCRIPTION.
                common_root_base (TYPE): DESCRIPTION.

            Returns:
                TYPE: DESCRIPTION.

            """
            absolute_path = os.path.normpath(absolute_path)
            common_root_path = os.path.normpath(common_root_path)

            commonprefix = os.path.commonprefix([common_root_path, absolute_path])
            if commonprefix == "":
                raise IOError(f"These two pathes have no common root path : {absolute_path} and {common_root_path}")
            return os.path.relpath(absolute_path, start=commonprefix)

        # Check if the overwrite_policy is supported
        overwrite_policies = ["raise", "skip", "erase", "most_recent", "overwrite"]
        if overwrite_policy not in overwrite_policies:
            raise ValueError(
                f"Unsupported value for overwrite_policy: {overwrite_policy}. Possibilities are: {overwrite_policies}"
            )

        # Iterate through the list of source files and copy them to the destination
        copied_files = []
        ignored_files = []

        for source_file_path in source_file_list:
            # Get the relative path of the source file with respect to the source root
            relative_path = get_relative_path(source_file_path, source_root)

            # Create the destination path by joining the destination root and the relative path
            destination_path = os.path.join(destination_root, relative_path)

            # Check if the file exists at the source location and is a file
            if not os.path.isfile(source_file_path):
                ignored_files.append(source_file_path)
                continue

            # Check if the file exists at the destination location and apply the overwrite policy accordingly
            if os.path.isfile(destination_path):
                if overwrite_policy == "raise":
                    raise ValueError(f"File already exists at the destination: {destination_path}")
                elif overwrite_policy == "skip":
                    ignored_files.append(source_file_path)
                    continue
                elif overwrite_policy == "erase":
                    os.remove(destination_path)
                elif overwrite_policy == "most_recent":
                    if os.stat(destination_path).st_mtime > os.stat(source_file_path).st_mtime:
                        ignored_files.append(source_file_path)
                        continue
                elif overwrite_policy == "overwrite":
                    pass

            # Create the directory structure at the destination if it does not exist already
            os.makedirs(os.path.dirname(destination_path), exist_ok=True)

            # Copy the file from source to destination
            shutil.copy(source_file_path, destination_path)

            # Append the copied file to the list of copied files
            copied_files.append(source_file_path)

        # Return a dictionary with the list of copied and ignored files
        return {"copied": copied_files, "ignored": ignored_files}

    def pull_files(self, file_list, *, session_details, relative=False, overwrite_policy="raise"):
        pass

    #     import json

    #     def _recurse_find_alyx_repo_json(_input_path):
    #         found_file = None
    #         for item in os.listdir(_input_path):
    #             if item == "__alyx_repository__.json" :
    #                 found_file = os.path.join(_input_path,item)
    #         if found_file is None :
    #             if os.path.dirname(_input_path) == _input_path :
    #                 raise IOError("input_path is not inside an alyx data-repository containing an
    #               '__alyx_repository__.json' file")
    #             return _recurse_find_alyx_repo_json(os.path.dirname(_input_path))
    #         return found_file

    #     json_path = _recurse_find_alyx_repo_json(input_path)
    #     repo_name = json.load( open(json_path,"r") )["name"]


# overwrite_policies = ["raise","skip","erase","most_recent","overwrite"]
#       if not overwrite_policy in overwrite_policies :
#           raise NotImplementedError(f"Value {overwrite_policy} for overwrite_policy is not supported.
#           Possibilities are : {overwrite_policies}")


class Session(pd.Series):
    def __new__(
        cls,
        series=None,
        *,
        subject=None,
        date=None,
        number=None,
        path=None,
        auto_path=False,
        date_format=None,
        zfill=3,
        separator="_",
    ):
        if series is None:
            series = pd.Series()

        if subject is not None:
            series["subject"] = subject
        if date is not None:
            series["date"] = date
        if number is not None or "number" not in series.index:
            series["number"] = number
        if path is not None:
            series["path"] = path

        series.pipeline  # verify the series complies with pipeline acessor

        if auto_path:
            series["path"] = os.path.normpath(
                os.path.join(
                    series["path"],
                    series.pipeline.subject(),
                    series.pipeline.date(date_format),
                    series.pipeline.number(zfill),
                )
            )

        if series.name is None:
            series.name = series.pipeline.alias(separator=separator, zfill=zfill, date_format=date_format)

        if "alias" not in series.index:
            series["alias"] = series.pipeline.alias(separator=separator, zfill=zfill, date_format=date_format)

        return series
