"""Session creation and datasets registration

The RegistrationClient provides an high-level API for creating experimentation sessions on Alyx
and registering associated datasets.

Summary of methods
------------------
create_new_session - Create a new local session folder and optionally create session record on Alyx
create_session - Create session record on Alyx from local path, without registering files
create_sessions - Create sessions and register files for folder containing a given flag file
register_session - Create a session on Alyx from local path and register any ALF datasets present
register_files - Register a list of files to their respective sessions on Alyx
"""

import pathlib
import uuid
from pathlib import Path, PurePosixPath
import datetime
from logging import getLogger
from uuid import UUID
import itertools
from collections import defaultdict
from fnmatch import fnmatch
import os
from typing import Dict, List
from requests.exceptions import HTTPError
import requests.exceptions
from tqdm import tqdm
from sys import stdout
import pandas as pd
from iblutil.io import hashfile

from .alf.io import next_num_folder
from .alf.files import (
    session_path_parts,
    get_session_path,
    full_path_parts,
    rel_path_parts,
)
from .alf.spec import to_full_path, is_uuid_string
from .alf.exceptions import AlyxSubjectNotFound, ALFError
from .util import ensure_list
from .webclient import no_cache
from .params import get as get_one_params
from . import models


class RegistrationClient:
    """
    Object that keeps the ONE instance and provides method to create sessions and register data.
    """

    def __init__(self, one=None):
        self.one = one
        if not one:
            from .api import ONE

            self.one = ONE(cache_rest=None)
        self.dtypes = self.one.alyx.rest("dataset-types", "list")
        self.registration_patterns = [dt["filename_pattern"] for dt in self.dtypes if dt["filename_pattern"]]
        self.file_extensions = [
            df["file_extension"] for df in self.one.alyx.rest("data-formats", "list", no_cache=True)
        ]

    def create_sessions(self, root_data_folder, glob_pattern="**/create_me.flag", dry=False):
        """
        Create sessions looking recursively for flag files

        Parameters
        ----------
        root_data_folder : str, pathlib.Path
            Folder to look for sessions
        glob_pattern : str
            Register valid sessions that contain this pattern
        dry : bool
            If true returns list of sessions without creating them on Alyx

        Returns
        -------
        list of pathlib.Paths
            Newly created session paths
        list of dicts
            Alyx session records
        """
        logger = getLogger("registration.create_sessions")
        flag_files = list(Path(root_data_folder).glob(glob_pattern))
        records = []
        for flag_file in flag_files:
            if dry:
                records.append(print(flag_file))
                continue
            logger.info("creating session for " + str(flag_file.parent))
            # providing a false flag stops the registration after session creation
            records.append(self.create_session(flag_file.parent))
            flag_file.unlink()
        return [ff.parent for ff in flag_files], records

    def create_session(self, session_path, **kwargs) -> dict:
        """Create a remote session on Alyx from a local session path, without registering files

        Parameters
        ----------
        session_path : str, pathlib.Path
            The path ending with subject/date/number.
        **kwargs
            Optional arguments for RegistrationClient.register_session.

        Returns
        -------
        dict
            Newly created session record.
        """
        return self.register_session(session_path, file_list=False, **kwargs)[0]

    def create_new_session(self, subject, session_root=None, date=None, register=True, **kwargs):
        """Create a new local session folder and optionally create session record on Alyx

        Parameters
        ----------
        subject : str
            The subject name.  Must exist on Alyx.
        session_root : str, pathlib.Path
            The root folder in which to create the subject/date/number folder.  Defaults to ONE
            cache directory.
        date : datetime.datetime, datetime.date, str
            An optional date for the session.  If None the current time is used.
        register : bool
            If true, create session record on Alyx database
        **kwargs
            Optional arguments for RegistrationClient.register_session.

        Returns
        -------
        pathlib.Path
            New local session path
        uuid.UUID
            The experiment UUID if register is True

        Examples
        --------
        Create a local session only

        >>> session_path, _ = RegistrationClient().create_new_session('Ian', register=False)

        Register a session on Alyx in a specific location

        >>> session_path, eid = RegistrationClient().create_new_session('Sy', '/data/lab/Subjects')

        Create a session for a given date

        >>> session_path, eid = RegistrationClient().create_new_session('Ian', date='2020-01-01')
        """
        assert not self.one.offline, "ONE must be in online mode"
        date = self.ensure_ISO8601(date)  # Format, validate
        # Ensure subject exists on Alyx
        self.assert_exists(subject, "subjects")
        session_root = Path(session_root or self.one.alyx.cache_dir) / subject / date[:10]
        session_path = session_root / next_num_folder(session_root)
        session_path.mkdir(exist_ok=True, parents=True)  # Ensure folder exists on disk
        eid = UUID(self.create_session(session_path, **kwargs)["url"][-36:]) if register else None
        return session_path, eid

    def find_files(self, session_path):
        """
        Returns an generator of file names that match one of the dataset type patterns in Alyx

        Parameters
        ----------
        session_path : str, pathlib.Path
            The session path to search

        Returns
        -------
        generator
            Iterable of file paths that match the dataset type patterns in Alyx
        """
        session_path = Path(session_path)
        types = (x["filename_pattern"] for x in self.dtypes if x["filename_pattern"])
        dsets = itertools.chain.from_iterable(session_path.rglob(x) for x in types)
        return (x for x in dsets if x.is_file() and any(x.name.endswith(y) for y in self.file_extensions))

    def assert_exists(self, member, endpoint):
        """Raise an error if a given member doesn't exist on Alyx database

        Parameters
        ----------
        member : str, uuid.UUID, list
            The member ID(s) to verify
        endpoint: str
            The endpoint at which to look it up

        Examples
        --------
        >>> client.assert_exists('ALK_036', 'subjects')
        >>> client.assert_exists('user_45', 'users')
        >>> client.assert_exists('local_server', 'repositories')

        Raises
        -------
        one.alf.exceptions.AlyxSubjectNotFound
            Subject does not exist on Alyx
        one.alf.exceptions.ALFError
            Member does not exist on Alyx
        requests.exceptions.HTTPError
            Failed to connect to Alyx database or endpoint not found
        """
        if isinstance(member, (str, uuid.UUID)):
            try:
                self.one.alyx.rest(endpoint, "read", id=str(member), no_cache=True)
            except requests.exceptions.HTTPError as ex:
                if ex.response.status_code != 404:
                    raise ex
                elif endpoint == "subjects":
                    raise AlyxSubjectNotFound(member)
                else:
                    raise ALFError(f'Member "{member}" doesn\'t exist in Alyx')
        else:
            for x in member:
                self.assert_exists(x, endpoint)

    @staticmethod
    def ensure_ISO8601(date) -> str:
        """Ensure provided date is ISO 8601 compliant

        Parameters
        ----------
        date : str, None, datetime.date, datetime.datetime
            An optional date to convert to ISO string.  If None, the current datetime is used.

        Returns
        -------
        str
            The datetime as an ISO 8601 string
        """
        date = date or datetime.datetime.now()  # If None get current time
        if isinstance(date, str):
            date = datetime.datetime.fromisoformat(date)  # Validate by parsing
        elif type(date) is datetime.date:
            date = datetime.datetime.fromordinal(date.toordinal())
        return datetime.datetime.isoformat(date)

    def register_session(self, ses_path, users=None, file_list=True, **kwargs):
        """
        Register session in Alyx

        NB: If providing a lab or start_time kwarg, they must match the lab (if there is one)
        and date of the session path.

        Parameters
        ----------
        ses_path : str, pathlib.Path
            The local session path
        users : str, list
            The user(s) to attribute to the session
        file_list : bool, list
            An optional list of file paths to register.  If True, all valid files within the
            session folder are registered.  If False, no files are registered
        location : str
            The optional location within the lab where the experiment takes place
        procedures : str, list
            An optional list of procedures, e.g. 'Behavior training/tasks'
        n_correct_trials : int
            The number of correct trials (optional)
        n_trials : int
            The total number of completed trials (optional)
        json : dict, str
            Optional JSON data
        projects: str, list
            The project(s) to which the experiment belongs (optional)
        type : str
            The experiment type, e.g. 'Experiment', 'Base'
        task_protocol : str
            The task protocol (optional)
        lab : str
            The name of the lab where the session took place.  If None the lab name will be
            taken from the path.  If no lab name is found in the path (i.e. no <lab>/Subjects)
            the default lab on Alyx will be used.
        start_time : str, datetime.datetime
            The precise start time of the session.  The date must match the date in the session
            path.
        end_time : str, datetime.datetime
            The precise end time of the session.

        Returns
        -------
        dict
            An Alyx session record
        list, None
            Alyx file records (or None if file_list is False)

        Raises
        ------
        AssertionError
            Subject does not exist on Alyx or provided start_time does not match date in
            session path.
        ValueError
            The provided lab name does not match the one found in the session path or
            start_time/end_time is not a valid ISO date time.
        requests.HTTPError
            A 400 status code means the submitted data was incorrect (e.g. task_protocol was an
            int instead of a str); A 500 status code means there was a server error.
        ConnectionError
            Failed to connect to Alyx, most likely due to a bad internet connection.
        """

        logger = getLogger("registration.register_session")

        if isinstance(ses_path, str):
            ses_path = Path(ses_path)
        details = session_path_parts(ses_path.as_posix(), as_dict=True, assert_valid=True)
        # query alyx endpoints for subject, error if not found
        self.assert_exists(details["subject"], "subjects")

        # look for a session from the same subject, same number on the same day
        with no_cache(self.one.alyx):
            session_id, session = self.one.search(
                subject=details["subject"],
                date_range=details["date"],
                number=details["number"],
                details=True,
                query_type="remote",
            )
        users = ensure_list(users or self.one.alyx.user)
        self.assert_exists(users, "users")

        # if nothing found create a new session in Alyx
        ses_ = {
            "subject": details["subject"],
            "users": users,
            "type": "Experiment",
            "number": details["number"],
        }
        if kwargs.get("end_time", False):
            ses_["end_time"] = self.ensure_ISO8601(kwargs.pop("end_time"))
        start_time = self.ensure_ISO8601(kwargs.pop("start_time", details["date"]))
        assert start_time[:10] == details["date"], "start_time doesn't match session path"
        if kwargs.get("procedures", False):
            ses_["procedures"] = ensure_list(kwargs.pop("procedures"))
        if kwargs.get("projects", False):
            ses_["projects"] = ensure_list(kwargs.pop("projects"))
        assert ("subject", "number") not in kwargs
        if "lab" not in kwargs and details["lab"]:
            kwargs.update({"lab": details["lab"]})
        elif details["lab"] and kwargs.get("lab", details["lab"]) != details["lab"]:
            names = (kwargs["lab"], details["lab"])
            raise ValueError('lab kwarg "%s" does not match lab name in path ("%s")' % names)
        ses_.update(kwargs)

        if not session:  # Create from scratch
            ses_["start_time"] = start_time
            session = self.one.alyx.rest("sessions", "create", data=ses_)
        else:  # Update existing
            if start_time:
                ses_["start_time"] = self.ensure_ISO8601(start_time)
            session = self.one.alyx.rest("sessions", "update", id=session_id[0], data=ses_)

        logger.info(session["url"] + " ")
        # at this point the session has been created. If create only, exit
        if not file_list:
            return session, None
        recs = self.register_files(self.find_files(ses_path) if file_list is True else file_list)
        if recs:  # Update local session data after registering files
            session["data_dataset_session_related"] = ensure_list(recs)
        return session, recs

    def register_water_administration(self, subject, volume, **kwargs):
        """
        Register a water administration to Alyx for a given subject

        Parameters
        ----------
        subject : str
            A subject nickname that exists on Alyx
        volume : float
            The total volume administrated in ml
        date_time : str, datetime.datetime, datetime.date
            The time of administration.  If None, the current time is used.
        water_type : str
            A water type that exists in Alyx; default is 'Water'
        user : str
            The user who administrated the water.  Currently logged-in user is the default.
        session : str, UUID, pathlib.Path, dict
            An optional experiment ID to associate
        adlib : bool
            If true, indicates that the subject was given water ad libitum

        Returns
        -------
        dict
            A water administration record

        Raises
        ------
        one.alf.exceptions.AlyxSubjectNotFound
            Subject does not exist on Alyx
        one.alf.exceptions.ALFError
            User does not exist on Alyx
        ValueError
            date_time is not a valid ISO date time or session ID is not valid
        requests.exceptions.HTTPError
            Failed to connect to database, or submitted data not valid (500)
        """
        # Ensure subject exists
        self.assert_exists(subject, "subjects")
        # Ensure user(s) exist
        user = ensure_list(kwargs.pop("user", [])) or self.one.alyx.user
        self.assert_exists(user, "users")
        # Ensure volume not zero
        if volume == 0:
            raise ValueError("Water volume must be greater than zero")
        # Post water admin
        wa_ = {
            "subject": subject,
            "date_time": self.ensure_ISO8601(kwargs.pop("date_time", None)),
            "water_administered": float(f"{volume:.4g}"),  # Round to 4 s.f.
            "water_type": kwargs.pop("water_type", "Water"),
            "user": user,
            "adlib": kwargs.pop("adlib", False),
        }
        # Ensure session is valid; convert to eid
        if kwargs.get("session", False):
            wa_["session"] = self.one.to_eid(kwargs.pop("session"))
            if not wa_["session"]:
                raise ValueError("Failed to parse session ID")

        return self.one.alyx.rest("water-administrations", "create", data=wa_)

    def register_weight(self, subject, weight, date_time=None, user=None):
        """
        Register a subject weight to Alyx

        Parameters
        ----------
        subject : str
            A subject nickname that exists on Alyx
        weight : float
            The subject weight in grams
        date_time : str, datetime.datetime, datetime.date
            The time of weighing.  If None, the current time is used.
        user : str
            The user who performed the weighing.  Currently logged-in user is the default.

        Returns
        -------
        dict
            An Alyx weight record

        Raises
        ------
        one.alf.exceptions.AlyxSubjectNotFound
            Subject does not exist on Alyx
        one.alf.exceptions.ALFError
            User does not exist on Alyx
        ValueError
            date_time is not a valid ISO date time or weight < 1e-4
        requests.exceptions.HTTPError
            Failed to connect to database, or submitted data not valid (500)
        """
        # Ensure subject exists
        self.assert_exists(subject, "subjects")
        # Ensure user(s) exist
        user = user or self.one.alyx.user
        self.assert_exists(user, "users")
        # Ensure weight not zero
        if weight == 0:
            raise ValueError("Water volume must be greater than 0")

        # Post water admin
        wei_ = {
            "subject": subject,
            "date_time": self.ensure_ISO8601(date_time),
            "weight": float(f"{weight:.4g}"),  # Round to 4 s.f.
            "user": user,
        }
        return self.one.alyx.rest("weighings", "create", data=wei_)

    def files(self, session, file_list, repository_name=None):
        """
        This method checks for the existence of files, groups them by dataset,
        validates their compliance, checks their session match, and adds their records.

        Args:
            session (object): An object that represents the session to which the files belong.
            file_list (list): A list of files to be checked and processed.
            repository_name (str, optional): The name of the repository where the files are stored. Defaults to None.

        Raises:
            It may raise exceptions if the files do not match the provided session. The exact exceptions depend on the
            implementation of 'assert_files_match_session'.
        """
        logger = getLogger("registration.files")

        if len(file_list) == 0:
            return

        files_df = self.group_files_by_dataset(file_list)

        not_compliant_files = files_df[~files_df.alf_compliant]
        if len(not_compliant_files):
            logger.warning(
                "Some files are not alf compliant, and are skipped for registration : "
                f"{not_compliant_files.full_path.to_list()}"
            )
            # we only keep the compliant files for the next steps
            files_df = files_df[files_df.alf_compliant]

        # checks that all files belong to the session supplied
        self.assert_files_match_session(session, files_df)

        for dataset_name, file_group in files_df.groupby("dataset_name"):
            logger.info(f"Dataset {dataset_name}")
            dataset = self._make_dataset(file_group, session, repository_name)
            if dataset is None:  # registration of a new dataset failed
                continue
            self._add_file_records(file_group, session, dataset)

        self.refresh_session_files(session)

    def refresh_session_files(self, session):
        # update the session object to contain info about the new registered data from the remote database

        # name attribute of the session pd.series is the database session id (aka primary key or pk)

        new_session_data = self.one.search(id=session.name, no_cache=True, details=True)

        # we touch the list object that is inside the data_dataset_session_related key of session
        # we cannot change the cell directly as session is a dataframe view.

        # first we clear
        session["data_dataset_session_related"].clear()
        # then we add new data
        session["data_dataset_session_related"].extend(new_session_data["data_dataset_session_related"])

    def group_files_by_dataset(self, files_list: List[str]) -> Dict[str, pd.DataFrame]:
        def make_dataset_name(row):
            if not row.alf_compliant:
                return ""
            collection_name = row.collection + "/" if row.collection else ""
            dataset_name = collection_name + ".".join([row.object, row.attribute, row.extension])
            return dataset_name

        def make_path(row):
            try:
                return to_full_path(**row)
            except Exception as e:  # if we cannot make the alf path from parts, it's not alf compliant
                return ""

        def make_session_alias(row):
            if not row.alf_compliant:
                return ""
            return rf"{row.subject}/{row.date}/{str(row.number).zfill(3)}"

        def make_alf_compliant_flag(row):
            if row.relative_path == "":
                return False
            return True

        results = [
            {"full_path": file, **full_path_parts(file, as_dict=True, assert_valid=False, absolute=True)}
            for file in files_list
        ]
        files_df = pd.DataFrame(results)

        # 1:-1 to remove elements root, and full_path for creating relative path from alf parts
        files_df["relative_path"] = files_df.iloc[:, 2:].apply(make_path, axis="columns")
        files_df["alf_compliant"] = files_df.apply(make_alf_compliant_flag, axis="columns")
        files_df["dataset_name"] = files_df.apply(make_dataset_name, axis="columns")
        files_df["session"] = files_df.apply(make_session_alias, axis="columns")

        return files_df

    def _make_dataset(self, files_df, session, repository_name=None, dry=False):
        logger = getLogger("registration.make_dataset")
        self.assert_single_repository(files_df)

        if repository_name is None:
            repository_name = self.find_session_repo(files_df.iloc[0]["root"])["name"]
        else:
            if repository_name == "default_repo_path":
                repository_name = session.default_data_repository_name
            else:
                # here find_session_repo act mostly a validator for the existance of that repo name
                # (raising if not found, returning the same otherwise)
                repository_name = self.find_session_repo(repository_name)["name"]

        for unique_item in ["collection", "extension", "object", "attribute"]:
            if len(files_df[unique_item].unique()) != 1:
                raise ValueError(
                    f"Registering several files under a same dataset require them being under the same {unique_item}, "
                    f"but these were found : {files_df[unique_item].unique()}"
                )

        dataset_name = files_df.dataset_name.iloc[0]

        object = files_df["object"].unique()[0]
        attribute = files_df["attribute"].unique()[0]
        dataset_type = object + "." + attribute
        collection = files_df["collection"].unique()[0]
        collection.replace("\\", "/")  # in case there is several folders, hence slashes, they should be unix typed
        extension = "." + files_df["extension"].unique()[0]
        session_eid = session.name

        new_dataset = {
            "created_by": get_one_params().ALYX_LOGIN,
            "dataset_type": dataset_type,
            "data_format": extension,
            "collection": collection,
            "session_pk": session_eid,
            "data_repository": repository_name,
        }

        unique_dataset_keys = ["dataset_type", "collection", "data_format"]
        for existing_dataset in session["data_dataset_session_related"]:
            booleans = [existing_dataset[key] == new_dataset[key] for key in unique_dataset_keys]

            # if all keys are matching, returning the existing dataset
            if all(booleans) is True:
                logger.info(
                    f"The dataset {dataset_name} for session {session.alias} "
                    "was already existing. "
                    "Using it to attach files instead of creating a new one."
                )
                return existing_dataset

        if dry:
            new_dataset.update({"id": None, "file_records": []})

        else:
            # if it doesn't exist, create it
            try:
                models.Dataset.assert_valid.dictionnary(new_dataset)
                new_dataset = self.one.alyx.rest("datasets", "create", data=new_dataset)
                logger.info(f"Registered the new dataset : {dataset_name} for session {session.alias}")
            except HTTPError as e:
                logger.info(
                    f"{type(e).__name__} {e} occured during registration of the new dataset : {dataset_name} "
                    f"for session {session.alias}."
                )
                return None

        return new_dataset

    def _add_file_records(self, files_df, session, dataset):
        logger = getLogger("registration.add_file_records")

        existing_files = [os.path.normpath(item["relative_path"]) for item in dataset["file_records"]]

        files_df["file_exists"] = files_df.relative_path.isin(existing_files)

        dataset_name = files_df.dataset_name.iloc[0]

        alread_registered_files = files_df[files_df.file_exists]
        not_yet_registered_files = files_df[~files_df.file_exists]
        if len(alread_registered_files):
            logger.info(
                f"Found {len(alread_registered_files)} "
                f"files already registered for the dataset {dataset_name}. Skipping them"
            )

        if not len(not_yet_registered_files):
            # no file to register, return
            return []

        logger.info(
            f"Starting registration of {len(not_yet_registered_files)} "
            f"new files to the dataset {dataset_name} for the session {session.alias}."
        )

        new_records = []

        for _, file in tqdm(
            not_yet_registered_files.iterrows(),
            total=len(not_yet_registered_files),
            delay=3,
            desc=f"Registering {len(not_yet_registered_files)} files",
            file=stdout,
        ):
            extra = file.extra

            d = {
                "dataset": dataset["id"],
                "extra": extra,
                "exists": True,
            }
            try:
                models.File.assert_valid.dictionnary(d)
                new_file_record = self.one.alyx.rest("files", "create", data=d)
                new_records.append(new_file_record)
            except HTTPError as e:
                logger.error(
                    f"A {type(e).__name__} {e} occured while trying to register file "
                    f"{file.full_path} to dataset {dataset_name}. Skipping"
                )

        return new_records

    def find_session_repo(self, repository_identifier: str):
        if is_uuid_string(repository_identifier):
            try:
                dataset = self.one.alyx.rest(
                    "data-repository",
                    "list",
                    no_cache=True,
                    id="05baa7e4-5eb5-4214-a008-c9e5331004b0",
                )[0]
            except IndexError:
                raise ValueError(f"No dataset id corresponds to the identifier {repository_identifier}")
            return dataset

        try:
            dataset = self.one.alyx.rest(
                "data-repository",
                "list",
                no_cache=True,
                name=repository_identifier,
            )[0]
            return dataset
        except IndexError:
            pass

        try:
            dataset = self.one.alyx.rest(
                "data-repository",
                "list",
                no_cache=True,
                data_path=os.path.normpath(repository_identifier).replace("\\", "/"),  # make the path unix compliant
            )[0]
            return dataset
        except IndexError:
            pass

        raise ValueError(
            f"No existing data repository was found for the identifier {repository_identifier} "
            " That you supplied. Either check their location and move them, or add this root as a new DataRepository"
        )

    def assert_files_match_session(self, session, files_df):
        sessions = files_df.session.unique()
        if len(sessions) != 1:
            raise ValueError(f"More than one session has been found in the file list : {list(sessions)}")
        if session.alias != sessions[0]:
            raise ValueError(
                f"A single session : {sessions[0]} has been found in the file list, "
                f"but no corresponding to the session {session.alias} supplied."
            )

    def assert_single_repository(self, files_df):
        if len(files_df.root.unique()) != 1:
            raise ValueError(
                "More than one data repository (e.g. the root of the files) has been found in the file list"
            )

    def change_session(self, session, **kwargs):
        models.Session.assert_valid.dictionnary(kwargs)

        self.one.alyx.rest("sessions", "partial_update", id=session.name, data=kwargs)

    def change_file(self, session, file_pk, **kwargs):
        file_pk = self.assert_file_in_session(session, file_pk)

        models.File.assert_valid.dictionnary(kwargs)

        self.one.alyx.rest("files", "partial_update", id=file_pk, data=kwargs)
        self.refresh_session_files(session)

    def change_dataset(self, session, dataset_pk, **kwargs):
        dataset_pk = self.assert_dataset_in_session(session, dataset_pk)

        models.Dataset.assert_valid.dictionnary(kwargs)

        self.one.alyx.rest("datasets", "partial_update", id=dataset_pk, data=kwargs)
        self.refresh_session_files(session)

    def delete_file(self, session, file_pk):
        file_pk = self.assert_file_in_session(session, file_pk)
        self.one.alyx.rest("files", "delete", id=file_pk)
        self.refresh_session_files(session)

    def delete_dataset(self, session, dataset_pk):
        dataset_pk = self.assert_dataset_in_session(session, dataset_pk)
        self.one.alyx.rest("datasets", "delete", id=dataset_pk)
        self.refresh_session_files(session)

    def assert_file_in_session(self, session, file_pk):
        if not any(self.one.list_datasets(session, details=True)["file#"] == file_pk):
            raise ValueError(f"The file {file_pk} is not part of the session {session.alias} registered files")
        return file_pk

    def assert_dataset_in_session(self, session, dataset_pk):
        if not any(self.one.list_datasets(session, details=True)["dataset#"] == dataset_pk):
            raise ValueError(f"The dataset {dataset_pk} is not part of the session {session.alias} registered datasets")
        return dataset_pk
