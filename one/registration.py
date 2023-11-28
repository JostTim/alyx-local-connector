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
import pandas as pd
import os

from typing import Dict, List

import requests.exceptions

from iblutil.io import hashfile

from .alf.io import next_num_folder
from .alf.files import (
    session_path_parts,
    get_session_path,
    full_path_parts,
    rel_path_parts,
)
from .alf.spec import to_full_path
from .alf.exceptions import AlyxSubjectNotFound, ALFError
from .util import ensure_list
from .webclient import no_cache
from .params import get as get_one_params


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
        self.registration_patterns = [
            dt["filename_pattern"] for dt in self.dtypes if dt["filename_pattern"]
        ]
        self.file_extensions = [
            df["file_extension"]
            for df in self.one.alyx.rest("data-formats", "list", no_cache=True)
        ]

    def create_sessions(
        self, root_data_folder, glob_pattern="**/create_me.flag", dry=False
    ):
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

    def create_new_session(
        self, subject, session_root=None, date=None, register=True, **kwargs
    ):
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
        session_root = (
            Path(session_root or self.one.alyx.cache_dir) / subject / date[:10]
        )
        session_path = session_root / next_num_folder(session_root)
        session_path.mkdir(exist_ok=True, parents=True)  # Ensure folder exists on disk
        eid = (
            UUID(self.create_session(session_path, **kwargs)["url"][-36:])
            if register
            else None
        )
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
        return (
            x
            for x in dsets
            if x.is_file() and any(x.name.endswith(y) for y in self.file_extensions)
        )

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
        details = session_path_parts(
            ses_path.as_posix(), as_dict=True, assert_valid=True
        )
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
        assert (
            start_time[:10] == details["date"]
        ), "start_time doesn't match session path"
        if kwargs.get("procedures", False):
            ses_["procedures"] = ensure_list(kwargs.pop("procedures"))
        if kwargs.get("projects", False):
            ses_["projects"] = ensure_list(kwargs.pop("projects"))
        assert ("subject", "number") not in kwargs
        if "lab" not in kwargs and details["lab"]:
            kwargs.update({"lab": details["lab"]})
        elif details["lab"] and kwargs.get("lab", details["lab"]) != details["lab"]:
            names = (kwargs["lab"], details["lab"])
            raise ValueError(
                'lab kwarg "%s" does not match lab name in path ("%s")' % names
            )
        ses_.update(kwargs)

        if not session:  # Create from scratch
            ses_["start_time"] = start_time
            session = self.one.alyx.rest("sessions", "create", data=ses_)
        else:  # Update existing
            if start_time:
                ses_["start_time"] = self.ensure_ISO8601(start_time)
            session = self.one.alyx.rest(
                "sessions", "update", id=session_id[0], data=ses_
            )

        logger.info(session["url"] + " ")
        # at this point the session has been created. If create only, exit
        if not file_list:
            return session, None
        recs = self.register_files(
            self.find_files(ses_path) if file_list is True else file_list
        )
        if recs:  # Update local session data after registering files
            session["data_dataset_session_related"] = ensure_list(recs)
        return session, recs

    def register_files_legacy(
        self,
        file_list,
        versions=None,
        default=True,
        created_by=None,
        server_only=False,
        repository=None,
        dry=False,
        max_md5_size=None,
    ):
        """
        Registers a set of files belonging to a session only on the server

        Parameters
        ----------
        file_list : list, str, pathlib.Path
            A filepath (or list thereof) of ALF datasets to register to Alyx
        created_by : str
            Name of Alyx user (defaults to whoever is logged in to ONE instance)
        repository : str
            Name of the repository in Alyx to register to
        server_only : bool
            Will only create file records in the 'online' repositories and skips local repositories
        versions : list of str
            Optional version tags
        default : bool
            Whether to set as default revision (defaults to True)
        dry : bool
            When true returns POST data for registration endpoint without submitting the data
        max_md5_size : int
            Maximum file in bytes to compute md5 sum (always compute if None)

        Returns
        -------
        list of dicts, dict
            A list of newly created Alyx dataset records or the registration data if dry
        """

        logger = getLogger("registration.register_files_legacy")

        F = defaultdict(list)  # empty map whose keys will be session paths
        V = defaultdict(list)  # empty map for versions
        if isinstance(file_list, (str, pathlib.Path)):
            file_list = [file_list]

        if versions is None or isinstance(versions, str):
            versions = itertools.repeat(versions)
        else:
            versions = itertools.cycle(versions)

        # Filter valid files and sort by session
        for fn, ver in zip(map(pathlib.Path, file_list), versions):
            session_path = get_session_path(fn)
            if fn.suffix not in self.file_extensions:
                logger.debug(f'{fn}: No matching extension "{fn.suffix}" in database')
                continue
            type_match = [
                x["name"]
                for x in self.dtypes
                if fnmatch(fn.name, x["filename_pattern"] or "")
            ]
            if len(type_match) == 0:
                logger.debug(f"{fn}: No matching dataset type in database")
                continue
            elif len(type_match) != 1:
                logger.debug(
                    f"{fn}: Multiple matching dataset types in database\n"
                    '"' + '", "'.join(type_match) + '"'
                )
                continue
            F[session_path].append(fn.relative_to(session_path))
            V[session_path].append(ver)

        # For each unique session, make a separate POST request
        records = []
        for session_path, files in F.items():
            # this is the generic relative path: subject/yyyy-mm-dd/NNN
            details = session_path_parts(
                session_path.as_posix(), as_dict=True, assert_valid=True
            )
            rel_path = PurePosixPath(
                details["subject"], details["date"], details["number"]
            )
            file_sizes = [session_path.joinpath(fn).stat().st_size for fn in files]
            # computing the md5 can be very long, so this is an option to skip if the file is
            # bigger than a certain threshold
            md5s = [
                hashfile.md5(session_path.joinpath(fn))
                if (max_md5_size is None or sz < max_md5_size)
                else None
                for fn, sz in zip(files, file_sizes)
            ]

            logger.info("Registering " + str(files))

            r_ = {
                "created_by": created_by or self.one.alyx.user,
                "path": rel_path.as_posix(),
                "filenames": [x.as_posix() for x in files],
                "hashes": md5s,
                "filesizes": file_sizes,
                "name": repository,
                "server_only": server_only,
                "default": default,
                "versions": V[session_path],
            }

            # Add optional field
            if details["lab"]:
                r_["labs"] = details["lab"]
            # If dry, store POST data, otherwise store resulting file records
            records.append(r_ if dry else self.one.alyx.post("/register-file", data=r_))
            # Log file names
            logger.info(f'ALYX REGISTERED DATA {"!DRY!" if dry else ""}: {rel_path}')
            for p in files:
                logger.info(f"ALYX REGISTERED DATA: {p}")

        return records[0] if len(F.keys()) == 1 else records

    def files(self, session, file_list, check_file_exist=False, repository_name=None):
        logger = getLogger("registration.files")

        dataset_groups = self.group_files_by_dataset(file_list)

        # name of the session pd.series is the database session id (aka primary key or pk)
        session_id = session.name

        self.assert_single_repo(file_list)
        repository_name = (
            self.find_session_repo(file_list)
            if repository_name is None
            else repository_name
        )

        for group_name, group in dataset_groups.items():
            root_path = group.iloc[0]["root"]
            files_list = group.apply(lambda row: to_full_path(**row), axis=1).tolist()

            if check_file_exist:
                if not all([os.path.isfile(file) for file in files_list]):
                    raise ValueError(
                        f"Some files don't exist on hard drive. In dataset group {group_name} there was an issue recreating file names from alf spec"
                    )

            files_list = [os.path.relpath(file, start=root_path) for file in files_list]
            # make them relative to the session as make_homogeneous_dataset requires that to process them.

            new_dataset = self.make_homogeneous_dataset(
                files_list, session, repository_name, dry=False
            )
            self.add_files_to_homogeneous_dataset(files_list, new_dataset, dry=False)

        # update the session object to contain info about the new registered data from the remote database

        new_session_data = self.one.search(
            id=session_id, no_cache=True, details=True
        ).iloc[0]

        # we touch the list object that is inside the data_dataset_session_related key of session
        # we cannot change the cell directly as session is a dataframe view.
        # first we clear
        session["data_dataset_session_related"].clear()
        # then we add new data
        session["data_dataset_session_related"].extend(
            new_session_data["data_dataset_session_related"]
        )

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

    def group_files_by_dataset(self, files_list: List[str]) -> Dict[str, pd.DataFrame]:
        """_summary_

        Args:
            files_list (List[str]): _description_

        Returns:
            Dict[str, pd.DataFrame]: The outputs datasets groups.
                Each key of the output dictionnary is the name of a dataset ("object.attribute") and each value is a group of
        """
        results = [
            full_path_parts(file, as_dict=True, assert_valid=False, absolute=True)
            for file in files_list
        ]
        fileparts_df = pd.DataFrame(results)

        groups = {}
        for collection_name, collection_selection in fileparts_df.groupby(
            "collection", dropna=False
        ):
            for file_names, files_collections in collection_selection.groupby(
                ["object", "attribute"], dropna=False
            ):
                group_name = (
                    collection_name + "/" if collection_name else ""
                ) + ".".join(file_names)
                groups[group_name] = files_collections

        return groups

    def make_homogeneous_dataset(self, files, session, repository_name, dry=True):
        logger = getLogger("registration.make_homogeneous_dataset")

        # repo_path = cnx.get_data_repository_path(repository_name)
        session_eid = session.name

        # Verify all goes well for a batch of alf file
        common_alf_type = {}
        for file in files:
            # alf_name = os.path.relpath(file, start = os.path.relpath(session_details["path"], start = repo_path))

            # new version :
            logger.debug(f"file path is : {file}")
            try:
                parts = full_path_parts(file, as_dict=True, absolute=True)
                for key in ["root", "lab", "subject", "date", "number"]:
                    parts.pop(key, None)
            except ValueError:
                parts = full_path_parts(file, as_dict=True, absolute=False)
                for key in ["root", "lab", "subject", "date", "number"]:
                    parts.pop(key, None)

            alf_name = to_full_path(**parts, dromedarize=False)

            if len(common_alf_type):
                alf_type = rel_path_parts(alf_name.replace("\\", "/"), as_dict=True)
                logger.debug(f"file parts are : {alf_type}")

                if alf_type["collection"] != common_alf_type["collection"]:
                    raise ValueError(
                        "Registering several files under a same dataset require them being under the same collection (session subdirectory)"
                    )
                if alf_type["revision"] != common_alf_type["revision"]:
                    raise NotImplementedError
                if alf_type["object"] != common_alf_type["object"]:
                    raise ValueError(
                        "Registering several files under a same dataset require them having the same object name (first name before dot)"
                    )
                if alf_type["attribute"] != common_alf_type["attribute"]:
                    raise ValueError(
                        "Registering several files under a same dataset require them having the same attribute name (second name before dot)"
                    )
                if alf_type["extension"] != common_alf_type["extension"]:
                    raise ValueError(
                        "Registering several files under a same dataset require them having the same extension"
                    )
            else:
                common_alf_type = rel_path_parts(
                    alf_name.replace("\\", "/"), as_dict=True
                )

        if common_alf_type["collection"] is None:
            common_alf_type["collection"] = ""

        d = {
            "created_by": get_one_params().ALYX_LOGIN,
            "dataset_type": common_alf_type["object"]
            + "."
            + common_alf_type["attribute"],
            "data_format": "." + common_alf_type["extension"],
            "collection": common_alf_type["collection"],
            "session_pk": session_eid,
            "data_repository": repository_name,
        }

        non_accepted_matching_keys = ["dataset_type", "collection"]
        for existing_dataset in session["data_dataset_session_related"]:
            booleans = [
                existing_dataset[key] == d[key] for key in non_accepted_matching_keys
            ]
            logger.debug("checking existing dataset : " + str(existing_dataset))
            if (
                all(booleans) is True
            ):  # all keys are matching, the dataset already exists, returning it.
                logger.info(
                    f"The dataset {session['alias']} - {d['collection']+'/' if d['collection'] else ''}{d['dataset_type']} was already existing. Using it to attach files instead of creating a new one."
                )
                return existing_dataset

        # if it doesn't exist, create it
        logger.info(f"Registering dataset : {d}")
        if not dry:
            new_dataset = self.one.alyx.rest("datasets", "create", data=d)
        else:
            new_dataset = None

        return new_dataset

    def add_files_to_homogeneous_dataset(self, files, dataset_dict, dry=True):
        logger = getLogger("registration.add_files_to_homogeneous_dataset")

        existing_files = []
        if dataset_dict is None:
            if not dry:
                raise ValueError(
                    "The dataset_dict returned by make_homogeneous_empty_dataset_from_filelist before calling add_files_to_empty_homogeneous_dataset seemed to be None, and dry run was not set to True. Cannot proceed."
                )

        else:
            logger.debug("loading files : " + str(dataset_dict))
            try:
                existing_files = self.one.alyx.rest(
                    "files", "list", dataset=dataset_dict["id"], no_cache=True
                )
            except KeyError:
                pass

        new_records = []
        for file in files:
            file = file.replace("\\", "/")

            file_already_existing = False

            if len(existing_files):
                for ex_file in existing_files:
                    logger.debug(
                        f"Comparing {file} and existing {ex_file['relative_path']}"
                    )
                    if file == ex_file["relative_path"]:
                        logger.error(
                            f"File {file} already exist in dataset, it was not added"
                        )
                        file_already_existing = True
                        break

            parts = full_path_parts(file, as_dict=True, absolute=False)

            if not file_already_existing:
                d = {
                    "dataset": dataset_dict["id"] if dataset_dict is not None else "",
                    "extra": parts["extra"] if parts["extra"] is not None else "",
                    "exists": True,
                }
                logger.info(f"Registering file : {d}")
                if not dry:
                    new_file_record = self.one.alyx.rest("files", "create", data=d)
                    new_records.append(new_file_record)
        return new_records

    def find_session_repo(self, files_list):
        logger = getLogger("registration.find_session_repo")

        root = os.path.normpath(
            session_path_parts(files_list[0], as_dict=True, absolute=True)["root"]
        )
        repository_name = None
        for repo in self.one.alyx.rest("data-repository", "list", no_cache=True):
            value = os.path.normpath(repo["data_path"])
            if value == root:
                repository_name = repo["name"]
                logger.info(
                    f"Found a common data-repository in the filepaths to use for registering : {repository_name}"
                )
                break
        if repository_name is None:
            raise ValueError(
                "No existing data repository was found for the location of the files you are trying to register. Either check their location and move them, or add a new data repository"
            )

        return repository_name

    def assert_single_repo(self, files_list):
        roots = set()
        for file in files_list:
            path_parts = session_path_parts(file, as_dict=True, absolute=True)
            roots.add(path_parts["root"])
        if len(roots) > 1:
            raise ValueError(
                "More than one data repository was found for the files given. The function cannot register files to more tha one session at once. Please change the file_list input."
            )
