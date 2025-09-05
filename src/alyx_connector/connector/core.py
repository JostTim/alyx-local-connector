from logging import getLogger
from pandas import DataFrame, Series
from sys import stdout
from tqdm import tqdm

from ..utils.types import Singleton
from ..utils.render_classes import obfuscate
from ..web.clients import WebClient
from ..files.registration.rules_config import Config

from typing import overload, Optional, NoReturn

logger = getLogger(__name__)


class Connector(metaclass=Singleton):
    """Connector class for managing a singleton web client connection.

    This class provides methods to interact with a web client, including searching for sessions, registering sessions,
    and converting results to pandas DataFrames or Series. It ensures that only one instance
    of the Connector exists at any time.

    Attributes:
        username (str): The username associated with the web client.
        url (str): The URL of the web client.

    Methods:
        setup(*args, **kwargs):
            Sets up the web client and returns a Connector instance.

        search(endpoint="sessions", details=True, raises=True, **kwargs):
            Searches for sessions or other resources at the specified endpoint.

        register(session_or_sessions):
            Registers a session or multiple sessions with the connector.

        raise_if_search_empty(search_result):
            Raises a ValueError if the search result is empty.

        results_to_pandas(request_result):
            Converts the results from a request into a pandas DataFrame or Series.

        details_aggregation(search_result, endpoint):
            Aggregates detailed results from a list of search results by retrieving additional
            information from a specified endpoint.
    """

    _singleton_no_argument_only = True

    def __init__(self, url=None, username=None, auto_authenticate=True, make_default=False):

        self.web_client = WebClient(
            url=url, username=username, auto_authenticate=auto_authenticate, make_default=make_default
        )

    @property
    def username(self):
        return self.web_client.username

    @property
    def url(self):
        return self.web_client.url

    @staticmethod
    def setup(*args, **kwargs):
        web_client = WebClient.setup_user(*args, **kwargs)
        web_client.config.current_user_config.make_all_default()
        return Connector(web_client.url, web_client.username)

    def search(self, endpoint="sessions", details=True, raises=True, **kwargs):
        search_result = self.web_client.search(endpoint=endpoint, details=details, **kwargs)
        if raises:
            search_result = self.raise_if_search_empty(search_result)
        return self.results_to_pandas(search_result)

    def register(self, session_or_sessions=DataFrame | Series):
        if isinstance(session_or_sessions, Series):
            return Config(connector=self, session=session_or_sessions)
        for _, session in session_or_sessions.iterrows():
            return Config(connector=self, session=session).registration_pipeline(session)

    # @overload
    # def search(self, endpoint=..., id: str = ..., **kwargs) -> Series: ...

    # @overload
    # def search(self, endpoint=..., id: None = ..., **kwargs) -> DataFrame: ...

    # def search(self, *, endpoint="sessions", id: Optional[str] = None, details=True, **kwargs):
    #     """Search for items in a specified endpoint.

    #     This method allows you to search for items in a given endpoint, with the option to retrieve details for a
    #     specific item if an ID is provided and the endpoint supports retrieval.
    #     If the endpoint does not support retrieval, a warning is logged when an ID is specified.

    #     Args:
    #         endpoint (str, optional): The endpoint to search in. Defaults to "sessions".
    #         id (Optional[str], optional): The ID of the item to retrieve. If provided, the method will attempt to
    #             retrieve the specific item if supported by the endpoint. Defaults to None.
    #         details (bool, optional): Whether to include detailed aggregation in the results. Defaults to True.
    #         **kwargs: Additional keyword arguments to pass to the search request.

    #     Returns:
    #         pandas.DataFrame or pandas.Series : A DataFrame or Series containing the search results.

    #     Raises:
    #         ValueError: If the search result is empty and details are requested.
    #     """
    #     action = "list"
    #     endpoint_implements_retrieve = "retrieve" in self.web_client.endpoint(endpoint).actions.keys()

    #     if id and endpoint_implements_retrieve:
    #         action = "retrieve"
    #         kwargs["id"] = id
    #     elif id and not endpoint_implements_retrieve:
    #         logger.warning(
    #             f"Endpoint {endpoint} does not implement retrieve. "
    #             "Specifying id will not work to select a specific item."
    #         )

    #     search_result = self.web_client.rest(endpoint, action, **kwargs)
    #     search_result = self.raise_if_search_empty(search_result)

    #     if details and not id and endpoint_implements_retrieve:
    #         search_result = self.details_aggregation(search_result, endpoint)  # type: ignore

    #     return self.results_to_pandas(search_result)

    def raise_if_search_empty(self, search_result: dict[str, dict | str] | list[dict[str, dict | str]] | None):
        if not search_result:
            raise ValueError("This search provided no result")
        return search_result

    @overload
    def results_to_pandas(self, request_result: None) -> NoReturn: ...

    @overload
    def results_to_pandas(self, request_result: dict[str, dict | str]) -> Series: ...

    @overload
    def results_to_pandas(self, request_result: list[dict[str, dict | str]]) -> DataFrame: ...

    def results_to_pandas(self, request_result: dict[str, dict | str] | list[dict[str, dict | str]] | None):
        """Converts the results from a request into a pandas DataFrame or Series.

        This method takes a request result, which can be a list of dictionaries or a single dictionary,
        and converts it into a pandas DataFrame or Series. If the input is a list, it returns a DataFrame.
        If the input is a dictionary, it extracts the 'id' field and returns a Series with the remaining data.
        If the input is neither a list nor a dictionary, it raises a NotImplementedError.

        Args:
            request_result (dict[str, dict | str] | list[dict[str, dict | str]] | None):
                The result from a request, which can be a list of dictionaries, a single dictionary, or None.

        Returns:
            DataFrame or Series:
                A pandas DataFrame if the input is a list, or a pandas Series if the input is a dictionary.

        Raises:
            TypeError:
                If the 'id' field in the dictionary is not a string.
            NotImplementedError:
                If the input is neither a list nor a dictionary.
        """
        if isinstance(request_result, list):
            return DataFrame(request_result)  # .set_index("id")
        elif isinstance(request_result, dict):
            id = request_result.pop("id", None)
            if not isinstance(id, str):
                raise TypeError(
                    f"ID of a recieved object must be a string, but alyx server returned a {type(id)} - {id=}"
                )
            return Series(request_result, name=id)
        else:
            raise NotImplementedError(f"Type was not list or dict : {type(request_result)}")

    def details_aggregation(
        self, search_result: list[dict[str, dict | str]], endpoint: str
    ) -> list[dict[str, dict | str]]:
        """Aggregates detailed results from a list of search results by retrieving additional information
        from a specified endpoint.

        Args:
            search_result (list[dict[str, dict | str]]): A list of dictionaries containing search results,
            where each dictionary includes an 'id' key.
            endpoint (str): The API endpoint from which to retrieve detailed information.

        Returns:
            list[dict[str, dict | str]]: A list of dictionaries containing detailed results retrieved
            from the specified endpoint.
        """
        detailed_results = []
        for result in tqdm(
            search_result, total=len(search_result), delay=2, desc=f"Loading {endpoint} details", file=stdout
        ):
            detailed_results.append(self.web_client.rest(endpoint, "retrieve", id=result["id"]))
        return detailed_results

    def __repr__(self):
        return (
            f"<{self.__class__.__name__}> - url:{self.url} - "
            f"username:{self.username} - "
            f"is_logged_in:{self.web_client.is_logged_in()} - "
            f"token:{obfuscate(self.web_client.token or "not_set")}"
        )


# if __name__ == "__main__":

#     class WebConnector(metaclass=Singleton):
#         """An API for searching and loading data through the Alyx database"""

#         def __init__(
#             self,
#             username=None,
#             password=None,
#             base_url=None,
#             cache_dir=None,
#             mode="auto",
#             data_access_mode="local",
#             wildcards=True,
#             **kwargs,
#         ):
#             """An API for searching and loading data through the Alyx database

#             Parameters
#             ----------
#             mode : str
#                 Query mode, options include 'auto', 'local' (offline) and 'remote' (online only).  Most
#                 methods have a `query_type` parameter that can override the class mode.
#             wildcards : bool
#                 If true, methods allow unix shell style pattern matching, otherwise regular
#                 expressions are supported
#             cache_dir : str, Path
#                 Path to the data files.  If Alyx parameters have been set up for this location,
#                 an OneAlyx instance is returned.  If data_dir and base_url are None, the default
#                 location is used.
#             base_url : str
#                 An Alyx database URL.  The URL must start with 'http'.
#             username : str
#                 An Alyx database login username.
#             password : str
#                 An Alyx database password.
#             cache_rest : str
#                 If not in 'local' mode, this determines which http request types to cache.  Default is
#                 'GET'.  Use None to deactivate cache (not recommended).
#             """

#             # Load Alyx Web client
#             self.web_client = AlyxClient(
#                 username=username,
#                 password=password,
#                 base_url=base_url,
#                 cache_dir=cache_dir,
#                 **kwargs,
#             )

#             self._registration_client = RegistrationClient(self)

#             self.data_access_mode = data_access_mode

#             self._search_endpoint = "sessions"

#             # get parameters override if inputs provided
#             super().__init__(mode=mode, wildcards=wildcards, cache_dir=cache_dir)

#         def set_data_access_mode(self, mode):
#             available_modes = [
#                 "local",
#                 "remote",
#             ]  # TODO : add ability to go auto mode later. (if possible, still have to think about it)
#             if mode in available_modes:
#                 self.data_access_mode = mode
#                 self.alyx.delete_cache()  # delete rest cache in case there is local / remote path stored inside
#             else:
#                 raise ValueError(f"data_access_mode must be one of : {available_modes}")

#         def __repr__(self):
#             return f'One ({"off" if self.offline else "on"}line, {self.alyx.base_url})'

#         def load_cache(self, cache_dir=None, clobber=False, tag=None):
#             """
#             Load parquet cache files.  If the local cache is sufficiently old, this method will query
#             the database for the location and creation date of the remote cache.  If newer, it will be
#             download and loaded.

#             Note: Unlike refresh_cache, this will always reload the local files at least once.

#             Parameters
#             ----------
#             cache_dir : str, pathlib.Path
#                 An optional directory location of the parquet files, defaults to One.cache_dir.
#             clobber : bool
#                 If True, query Alyx for a newer cache even if current (local) cache is recent.
#             tag : str
#                 An optional Alyx dataset tag for loading cache tables containing a subset of datasets.
#             """
#             _logger = logging.getLogger("load_cache")
#             cache_meta = self._cache.get("_meta", {})
#             cache_dir = cache_dir or self.cache_dir
#             # If user provides tag that doesn't match current cache's tag, always download.
#             # NB: In the future 'database_tags' may become a list.
#             current_tags = [x.get("database_tags") for x in cache_meta.get("raw", {}).values() or [{}]]
#             tag = tag or current_tags[0]  # For refreshes take the current tag as default
#             different_tag = any(x != tag for x in current_tags)
#             if not clobber or different_tag:
#                 super().load_cache(cache_dir)  # Load any present cache
#                 cache_meta = self._cache.get("_meta", {})  # TODO Make walrus when we drop 3.7 support
#                 expired = self._cache and cache_meta["expired"]
#                 if not expired or self.mode in ("local", "remote"):
#                     return

#             # Warn user if expired
#             if cache_meta["expired"] and cache_meta.get("created_time", False) and not self.alyx.silent:
#                 age = datetime.now() - cache_meta["created_time"]
#                 t_str = f"{age.days} day(s)" if age.days >= 1 else f"{np.floor(age.seconds / (60 * 2))} hour(s)"
#                 _logger.info(f"cache over {t_str} old")

#             try:
#                 # Determine whether a newer cache is available
#                 cache_info = self.alyx.get(f'cache/info/{tag or ""}'.strip("/"), expires=True)
#                 assert tag == cache_info.get("database_tags")

#                 # Check version compatibility
#                 min_version = packaging.version.parse(cache_info.get("min_api_version", "0.0.0"))
#                 if packaging.version.parse(alyx_connector.__version__) < min_version:
#                     warnings.warn(f"Newer cache tables require ONE version {min_version} or greater")
#                     return

#                 # Check whether remote cache more recent
#                 remote_created = datetime.fromisoformat(cache_info["date_created"])
#                 local_created = cache_meta.get("created_time", None)
#                 if local_created and (remote_created - local_created) < timedelta(minutes=1):
#                     _logger.info("No newer cache available")
#                     return

#                 # Download the remote cache files
#                 _logger.info("Downloading remote caches...")
#                 files = self.alyx.download_cache_tables(cache_info.get("location"), cache_dir)
#                 assert any(files)
#                 super().load_cache(cache_dir)  # Reload cache after download
#             except (requests.exceptions.HTTPError, HTTPError) as ex:
#                 _logger.debug(ex)
#                 ## REMOVED THIS WARNING FOR NOW AS I CAN'T FIND IF IT IS ACTUALLY USEFULL TO HAVE A REMOTE CACHE WHEN
#                 # SETUPING A LOCAL USE OF ALYX LIKE WE DO
#                 # _logger.error('Failed to load the remote cache file')
#                 self.mode = "remote"
#             except (ConnectionError, requests.exceptions.ConnectionError) as ex:
#                 _logger.debug(ex)
#                 _logger.error("Failed to connect to Alyx")
#                 self.mode = "local"

#         @property
#         def alyx(self):
#             """one.webclient.AlyxClient: The Alyx Web client"""
#             return self.web_client

#         @property
#         def register(self):
#             return self._registration_client

#         @property
#         def cache_dir(self):
#             """pathlib.Path: The location of the downloaded file cache"""
#             return self.web_client.cache_dir

#         @util.refresh
#         def search_terms(self, query_type=None):
#             """
#             Returns a list of search terms to be passed as kwargs to the search method

#             Parameters
#             ----------
#             query_type : str
#                 If 'remote', the search terms are largely determined by the REST endpoint used

#             Returns
#             -------
#             tuple
#                 Tuple of search strings
#             """
#             if (query_type or self.mode) != "remote":
#                 # Return search terms from REST schema
#                 return self._search_terms
#             fields = self.alyx.rest_schemes[self._search_endpoint]["list"]["fields"]
#             # 'laboratory' already in search terms
#             excl = ("lab",)
#             return tuple({*self._search_terms, *(x["name"] for x in fields if x["name"] not in excl)})

#         def describe_dataset(self, dataset_type=None):
#             """Print a dataset type description.

#             NB: This requires an Alyx database connection.

#             Parameters
#             ----------
#             dataset_type : str
#                 A dataset type or dataset name

#             Returns
#             -------
#             dict
#                 The Alyx dataset type record
#             """
#             _logger = logging.getLogger("describe_dataset")
#             assert self.mode != "local" and not self.offline, "Unable to connect to Alyx in local mode"
#             if not dataset_type:
#                 return self.alyx.rest("dataset-types", "list")
#             try:
#                 assert isinstance(dataset_type, str) and not is_uuid_string(dataset_type)
#                 _logger.disabled = True
#                 out = self.alyx.rest("dataset-types", "read", dataset_type)
#             except (AssertionError, requests.exceptions.HTTPError):
#                 # Try to get dataset type from dataset name
#                 out = self.alyx.rest("dataset-types", "read", self.dataset2type(dataset_type))
#             finally:
#                 _logger.disabled = False
#             print(out["description"])
#             return out

#         def change_session_data_mode(self, session_details, mode):
#             session_details["full_path"] = session_details[mode + "_full_path"]
#             return session_details

#         #### LIST DATASETS
#         @util.refresh
#         def list_datasets(
#             self,
#             eid=None,
#             details=False,
#             query_type=None,
#             as_mode=None,
#             no_cache=False,
#             **filters,
#         ) -> np.ndarray | pd.DataFrame | List[str]:
#             """_summary_

#             Args:
#                 eid (str, optional): Session id (alias or pk). Does not need to be supplied only if
#                     using session_details. Defaults to None.
#                 details (bool, optional): If False, will only return a list of absolute file paths
#                     (obtained from the column full_path of the dataset dataframe).
#                     Otherwise will return a full dataframe of all datasets found for this session
#                     Defaults to False.
#                 query_type (str or None, optional): Wether to perform the metadata fetch
#                     (only if using eid) from local cache or on alyx. If None, uses the current mode of
#                     the connector instance. Defaults to None.
#                 as_mode (str or None, optional): Wether to perform the full_path construction using
#                     default current (None), 'local' or 'remote' mode. Defaults to None.
#                 session_details (pd.Series, optional): The pandas series containing
#                     data_dataset_session_related information.
#                     If not supplied, the function must be supplied a session id (eid, first argument)
#                     to get this session_details variable internally. Defaults to None.

#                 Other filtering keys are accepted :
#                     (the value on wich you search must match perfectly. Key must also match any of the
#                     below. key and values are case sensitive)
#                     - extra
#                     - object
#                     - attribute
#                     - subject
#                     - date
#                     - number
#                     - revision
#                     - collection
#                     - extension

#                     - dataset_type
#                     - name (from dataset, ex trials.eventTimelines)
#                     - file_name

#                     - relative_path
#                     - exists
#                     - created_by
#                     - created_datetime
#                     - data_repository
#                     - hash
#                     - file_size
#                     - tags

#             Raises:
#                 NotImplementedError: For now, using mode (metadata) local will not work as the cache system to retrive data
#                     has not been tested for.

#             Returns:
#                 Union[list, pd.DataFrame]: A list of matching files full_paths (if details = False) or a dataframe of all
#                     matching file records components and metadata.
#             """

#             import natsort

#             _logger = logging.getLogger("list_datasets")

#             def flatten_pathlist(x):
#                 if isinstance(x, (list, tuple)):
#                     return natsort.natsorted([a for i in x for a in flatten(i)])
#                 else:
#                     return natsort.natsorted([x])

#             # OBTAINING A SESSION_DETAILS (IN WHICH RESIDES FILES INFO)

#             data_access_mode = as_mode or self.data_access_mode
#             if hasattr(eid, "keys") and "data_dataset_session_related" in eid.keys():
#                 session_details = (
#                     eid.copy()
#                 )  # either a pd.series of dict, copy and key getters/setters works both cases
#                 try:
#                     eid = session_details["id"]
#                 except KeyError:
#                     eid = session_details.name
#                 session_details["id"] = eid
#             else:
#                 _eid = eid
#                 # Ensure we have a UUID str
#                 eid = self.to_eid(eid)
#                 if eid is None:
#                     raise ValueError(
#                         f"The session id {_eid} supplied seem to not be existing. Check that you are in 'remote' mode "
#                         "(not data_access_mode) and check that the session exists on a webpage."
#                     )
#                 if (query_type or self.mode) != "remote":
#                     raise NotImplementedError
#                     ## TODO : GET BACK THE DATA FROM THE CACHE AFTER CHANGE IN DATA MANAGEMENT METHODOLOGY
#                     # return super().list_datasets(eid, details=details, query_type=query_type, **filters)

#                 session_details = self.to_session_details(
#                     self.alyx.rest("sessions", "read", id=eid, query_type=query_type, no_cache=no_cache)
#                 )
#             # session, datasets = util.ses2records(self.alyx.rest('sessions', 'read', id=eid))
#             # self._update_cache_from_records(sessions=session, datasets=datasets.copy()
#             # if datasets is not None else datasets)
#             # Add to cache tables # TODO : DO ADD THAT FUNCTIONNALITY AGAIN

#             datasets = copy.deepcopy(session_details["data_dataset_session_related"])
#             # copy to not change the session_details in case they are suplied by user as input

#             file_records = []
#             for dataset in datasets:
#                 files = dataset.pop("file_records")
#                 dataset["session#"] = dataset.pop("session")
#                 dataset["dataset#"] = dataset.pop("id")
#                 dataset["dataset_url"] = dataset.pop("url")
#                 dataset["dataset_admin_url"] = dataset.pop("admin_url")
#                 dataset["local_root"] = alyx_connector.params.get().LOCAL_ROOT
#                 for file in files:
#                     # file["file_url"] = file.pop("url")
#                     # file["file_admin_url"] = file.pop("admin_url")
#                     file["file#"] = file.pop("id")
#                     file.update(
#                         {
#                             "remote_full_path": os.path.normpath(
#                                 os.path.join(dataset["remote_root"], file["relative_path"])
#                             ),
#                             "local_full_path": os.path.normpath(
#                                 os.path.join(dataset["local_root"], file["relative_path"])
#                             ),
#                         }
#                     )
#                     # remote or local depending on current mode
#                     file["full_path"] = file[data_access_mode + "_full_path"]
#                     file.update(dataset)
#                     file_records.append(file)

#             dataframe = pd.DataFrame(file_records)
#             if dataframe.empty:
#                 _logger.warning(
#                     "No dataset found for this session. Are you sure you ran the file registration routine ?"
#                 )
#                 return dataframe if details else []

#             dataframe.set_index(["session#", "dataset#", "file#"])

#             # FILTERING THE ROWS BASED ON USER INPUT
#             # query_string = ' & '.join([f'{k} == {repr(v)}' for k, v in filters.items()])
#             if filters:
#                 if "filename" in filters.keys():
#                     # allowing filename for retrocompatibility
#                     filters["file_name"] = filters.pop("filename")
#                 queries = []
#                 for key, value in filters.items():
#                     # if the columns correspunding to filter is object or string, we try to match using wildcard type
#                     # syntax as input
#                     # to do that we convert * wildcards to .* in regex and add a ^ and $ at start and end of pattern
#                     # to force a complete string length match.

#                     if dataframe[key].dtype is npdtype("O") or dataframe[key].dtype is npdtype(str):
#                         queries.append(f"{key}.str.match('^' + {repr(value).replace('*', '.*')} + '$') ")  #
#                     # if the columns is not a sting, we match it directly.
#                     else:
#                         queries.append(f"{key} == {value}")

#                 # example of a query string : query_string = "exists == True & subject.str.match('wm.*$')"
#                 query_string = " & ".join(queries)

#                 try:
#                     dataframe = dataframe.query(query_string, engine="python")
#                 except (KeyError, pd.errors.UndefinedVariableError) as e:
#                     raise KeyError(f"Cannot use the key {str(e)} to filter for datasets")

#             return dataframe if details else list(dataframe["full_path"])

#         @util.refresh
#         def pid2eid(self, pid: str, query_type=None) -> Tuple[str, str]:
#             """
#             Given an Alyx probe UUID string, returns the session id string and the probe label
#             (i.e. the ALF collection).

#             NB: Requires a connection to the Alyx database.

#             Parameters
#             ----------
#             pid : str, uuid.UUID
#                 A probe UUID
#             query_type : str
#                 Query mode - options include 'remote', and 'refresh'

#             Returns
#             -------
#             str
#                 Experiment ID (eid)
#             str
#                 Probe label
#             """
#             query_type = query_type or self.mode
#             if query_type != "remote":
#                 self.refresh_cache(query_type)
#             if query_type == "local" and "insertions" not in self._cache.keys():
#                 raise NotImplementedError("Converting probe IDs required remote connection")
#             rec = self.alyx.rest("insertions", "read", id=str(pid))
#             return rec["session"], rec["name"]

#         #### SEARCH
#         def search(
#             self,
#             id=None,
#             *,
#             details=False,
#             query_type=None,
#             as_mode=None,
#             no_cache=False,
#             **kwargs,
#         ):
#             """
#             Searches sessions matching the given criteria and returns a list of matching eids

#             For a list of search terms, use the method

#                 one.search_terms(query_type='remote')

#             For all of the search parameters, a single value or list may be provided.  For dataset,
#             the sessions returned will contain all listed datasets.  For the other parameters,
#             the session must contain at least one of the entries. NB: Wildcards are not permitted,
#             however if wildcards property is False, regular expressions may be used for all but
#             number and date_range.

#             Parameters
#             ----------
#             dataset : str, list
#                 List of dataset names. Returns sessions containing all these datasets.
#                 A dataset matches if it contains the search string e.g. 'wheel.position' matches
#                 '_ibl_wheel.position.npy'
#             date_range : str, list, datetime.datetime, datetime.date, pandas.timestamp
#                 A single date to search or a list of 2 dates that define the range (inclusive).  To
#                 define only the upper or lower date bound, set the other element to None.
#             lab : str, list
#                 A str or list of lab names, returns sessions from any of these labs
#             number : str, int
#                 Number of session to be returned, i.e. number in sequence for a given date
#             subject : str, list
#                 A list of subject nicknames, returns sessions for any of these subjects
#             task_protocol : str, list
#                 The task protocol name (can be partial, i.e. any task protocol containing that str
#                 will be found)
#             project(s) : str, list
#                 The project name (can be partial, i.e. any task protocol containing that str
#                 will be found)
#             performance_lte / performance_gte : float
#                 Search only for sessions whose performance is less equal or greater equal than a
#                 pre-defined threshold as a percentage (0-100)
#             users : str, list
#                 A list of users
#             location : str, list
#                 A str or list of lab location (as per Alyx definition) name
#                 Note: this corresponds to the specific rig, not the lab geographical location per se
#             json : dict
#                 example :
#                     ```python
#                         json = {
#                             "whisker_stims": {
#                                 "amplitudes": {
#                                     "abstract__contains": ['10-0','10-10','10_90-0']
#                                     }
#                                 }
#                             }
#                     ```
#                 allowed filter looups are listed here :
#                 https://docs.djangoproject.com/en/5.0/ref/models/querysets/#field-lookups


#             dataset_types : str, list
#                 One or more of dataset_types
#             details : bool
#                 If true also returns a dict of dataset details
#             query_type : str, None
#                 Query cache ('local') or Alyx database ('remote')
#             limit : int
#                 The number of results to fetch in one go (if pagination enabled on server)


#             Returns
#             -------
#             list
#                 List of eids
#             (list of dicts)
#                 If details is True, also returns a list of dictionaries, each entry corresponding to a
#                 matching session
#             """
#             _logger = logging.getLogger("search")
#             query_type = query_type or self.mode  # we set query_type = self.mode if query_type is None
#             if query_type != "remote":
#                 return super().search(details=details, query_type=query_type, **kwargs)

#             # loop over input arguments and build the url
#             search_terms = self.search_terms(query_type=query_type)
#             params = {}  # {"django": kwargs.pop("django", "")}

#             if isinstance(id, (list, tuple)):
#                 sessions = []
#                 for i in id:
#                     sessions.append(
#                         self.search(
#                             id=i, details=details, as_mode=as_mode, no_cache=no_cache, query_type=query_type, **kwargs
#                         )
#                     )
#                 return pd.DataFrame(sessions)

#             if id is not None:
#                 params["id"] = self.to_eid(id)
#                 if params["id"] is None:  # this means the id we supplied is not a valid eid
#                     raise ValueError(f"{id} is not a valid identifier, could not convert it to session uuid.")
#             _logger.debug(f"kwargs : {kwargs}")
#             for key, value in sorted(kwargs.items()):
#                 field = util.autocomplete(key, search_terms)  # Validate and get full name
#                 _logger.debug(f"field : {field}")
#                 # check that the input matches one of the defined filters
#                 if field == "date_range":
#                     params[field] = [x.date().isoformat() for x in util.validate_date_range(value)]
#                 elif field == "laboratory":
#                     params["lab"] = value
#                 else:
#                     params[field] = value
#             _logger.debug(f"params : {params}")
#             # Make GET request
#             ses = self.alyx.rest(
#                 self._search_endpoint,
#                 "list",
#                 no_cache=no_cache,
#                 query_type=query_type,
#                 **params,
#             )
#             if len(ses) > 1:
#                 _logger.info(f"Found {len(ses)} sessions.")

#             if not details:
#                 return [item["id"] for item in ses]
#                 # list(util.LazyId(ses))

#             sess_df = []
#             for session_short_info in tqdm(ses, total=len(ses), delay=2, desc="Loading session details", file=stdout):
#                 session_detailed_info = self.alyx.rest(
#                     "sessions",
#                     "read",
#                     id=session_short_info["id"],
#                     no_cache=no_cache,
#                     query_type=query_type,
#                 )
#                 formated_session = self.to_session_details(session_detailed_info, as_mode=as_mode)
#                 sess_df.append(formated_session)

#             try:
#                 sess_df = pd.DataFrame(sess_df)
#                 sess_df.index = sess_df.index.set_names("id")
#                 sess_df["start_datetime"] = pd.to_datetime(sess_df["start_datetime"], utc=True)
#             except (ValueError, KeyError):
#                 warnings.warn("search result contained no entry")
#                 pass  # could not create a dataframe form session details. Returning dict instead
#             if id is not None:
#                 return sess_df.iloc[0]
#             return sess_df

#         def to_session_details(self, session_dict, as_mode=None):
#             data_access_mode = (
#                 as_mode or self.data_access_mode
#             )  # we set data_access_mode = self.data_access_mode if as_mode is None

#             session_dict["date"] = str(datetime.fromisoformat(session_dict["start_time"]).date())
#             session_dict["extended_qc"] = session_dict["extended_qc"] if session_dict["extended_qc"] is not None else {}
#             session_dict["local_path"] = os.path.normpath(
#                 os.path.join(alyx_connector.params.get().LOCAL_ROOT, session_dict["rel_path"])
#             )
#             session_dict["remote_path"] = os.path.normpath(
#                 session_dict["path"]
#             )  # path is the remote path initially (out from the database)
#             # we set path depending on the data_access_mode
#             session_dict["path"] = session_dict[data_access_mode + "_path"]
#             session_dict["rel_path"] = Path(session_dict["rel_path"])

#             session_dict["start_datetime"] = datetime.strptime(session_dict["start_time"], "%Y-%m-%dT%H:%M:%S%z")

#             id = session_dict.pop("id")

#             session_details = pd.Series(session_dict, name=id)
#             return session_details

#         def _download_datasets(self, dsets, **kwargs) -> List[Path]:
#             """
#             Download a single or multitude of datasets if stored on AWS, otherwise calls
#             OneAlyx._download_dataset.

#             NB: This will not skip files that are already present.  Use check_filesystem instead.

#             Parameters
#             ----------
#             dset : dict, str, pd.Series
#                 A single or multitude of dataset dictionaries

#             Returns
#             -------
#             pathlib.Path
#                 A local file path or list of paths
#             """
#             # If all datasets exist on AWS, download from there.
#             _logger = logging.getLogger("_download_datasets")
#             try:
#                 if "exists_aws" in dsets and np.all(np.equal(dsets["exists_aws"].values, True)):
#                     _logger.info("Downloading from AWS")
#                     return self._download_aws(map(lambda x: x[1], dsets.iterrows()), **kwargs)
#             except Exception as ex:
#                 _logger.debug(ex)
#             return self._download_dataset(dsets, **kwargs)

#         def _dset2url(self, dset, update_cache=True):
#             """
#             Converts a dataset into a remote HTTP server URL.  The dataset may be one or more of the
#             following: a dict from returned by the sessions endpoint or dataset endpoint, a record
#             from the datasets cache table, or a file path.  Unlike record2url, this method can convert
#             dicts and paths to URLs.

#             Parameters
#             ----------
#             dset : dict, str, pd.Series, pd.DataFrame, list
#                 A single or multitude of dataset dictionary from an Alyx REST query OR URL string
#             update_cache : bool
#                 If True (default) and the dataset is from Alyx and cannot be converted to a URL,
#                 'exists' will be set to False in the corresponding entry in the cache table.

#             Returns
#             -------
#             str
#                 The remote URL of the dataset
#             """
#             _logger = logging.getLogger("_dset2url")
#             did = None
#             if isinstance(dset, str) and dset.startswith("http"):
#                 url = dset
#             elif isinstance(dset, (str, Path)):
#                 url = self.path2url(dset)
#                 if not url:
#                     _logger.warning(f"Dataset {dset} not found in cache")
#                     return
#             elif isinstance(dset, (list, tuple)):
#                 dset2url = partial(self._dset2url, update_cache=update_cache)
#                 return list(flatten(map(dset2url, dset)))
#             else:
#                 # check if dset is dataframe, iterate over rows
#                 if hasattr(dset, "iterrows"):
#                     dset2url = partial(self._dset2url, update_cache=update_cache)
#                     url = list(map(lambda x: dset2url(x[1]), dset.iterrows()))
#                 elif "data_url" in dset:  # data_dataset_session_related dict
#                     url = dset["data_url"]
#                     did = dset["id"]
#                 elif "file_records" not in dset:  # Convert dataset Series to alyx dataset dict
#                     url = self.record2url(dset)  # NB: URL will always be returned but may not exist
#                     is_int = all(isinstance(x, (int, np.int64)) for x in util.ensure_list(dset.name))
#                     did = np.array(dset.name)[-2:] if is_int else util.ensure_list(dset.name)[-1]
#                 else:  # from datasets endpoint
#                     repo = getattr(getattr(self.web_client, "_par", None), "HTTP_DATA_SERVER", None)
#                     url = next(
#                         (
#                             fr["data_url"]
#                             for fr in dset["file_records"]
#                             if fr["data_url"] and fr["exists"] and fr["data_url"].startswith(repo or fr["data_url"])
#                         ),
#                         None,
#                     )
#                     did = dset["url"][-36:]

#             # Update cache if url not found
#             if did is not None and not url and update_cache:
#                 _logger.debug("Updating cache")
#                 if isinstance(did, str) and self._index_type("datasets") is int:
#                     (did,) = parquet.str2np(did).tolist()
#                 elif self._index_type("datasets") is str and not isinstance(did, str):
#                     did = parquet.np2str(did)
#                 # NB: This will be considerably easier when IndexSlice supports Ellipsis
#                 idx = [slice(None)] * int(self._cache["datasets"].index.nlevels / 2)
#                 self._cache["datasets"].loc[(*idx, *util.ensure_list(did)), "exists"] = False
#                 self._cache["_meta"]["modified_time"] = datetime.now()

#             return url

#         def _download_dataset(self, dset, cache_dir=None, update_cache=True, **kwargs) -> List[Path | None]:
#             """
#             Download a single or multitude of dataset from an Alyx REST dictionary.

#             NB: This will not skip files that are already present.  Use check_filesystem instead.

#             Parameters
#             ----------
#             dset : dict, str, pd.Series, pd.DataFrame, list
#                 A single or multitude of dataset dictionary from an Alyx REST query OR URL string
#             cache_dir : str, pathlib.Path
#                 The root directory to save the data to (default taken from ONE parameters)
#             update_cache : bool
#                 If true, the cache is updated when filesystem discrepancies are encountered

#             Returns
#             -------
#             pathlib.Path, list
#                 A local file path or list of paths
#             """
#             cache_dir = cache_dir or self.cache_dir
#             url = self._dset2url(dset, update_cache=update_cache)
#             if not url:
#                 return
#             if isinstance(url, str):
#                 target_dir = str(Path(cache_dir, get_alf_path(url)).parent)
#                 return self._download_file(url, target_dir, **kwargs)
#             # must be list of URLs
#             valid_urls = list(filter(None, url))
#             if not valid_urls:
#                 return [None] * len(url)
#             target_dir = [str(Path(cache_dir, get_alf_path(x)).parent) for x in valid_urls]
#             files = self._download_file(valid_urls, target_dir, **kwargs)
#             # Return list of file paths or None if we failed to extract URL from dataset
#             return [None if not x else files.pop(0) for x in url]

#         def _tag_mismatched_file_record(self, url):
#             fr = self.alyx.rest(
#                 "files",
#                 "list",
#                 django=f'dataset,{Path(url).name.split(".")[-2]},data_repository__globus_is_personal,False',
#                 no_cache=True,
#             )
#             if len(fr) > 0:
#                 json_field = fr[0]["json"]
#                 if json_field is None:
#                     json_field = {"mismatch_hash": True}
#                 else:
#                     json_field.update({"mismatch_hash": True})
#                 try:
#                     self.alyx.rest(
#                         "files",
#                         "partial_update",
#                         id=fr[0]["url"][-36:],
#                         data={"json": json_field},
#                     )
#                 except requests.exceptions.HTTPError as ex:
#                     warnings.warn(
#                         f"Failed to tag remote file record mismatch: {ex}\nPlease contact the database administrator."
#                     )

#         def _download_file(self, url, target_dir, keep_uuid=False, file_size=None, hash=None) -> Path | List[Path]:
#             """
#             Downloads a single file or multitude of files from an HTTP webserver.
#             The webserver in question is set by the AlyxClient object.

#             Parameters
#             ----------
#             url : str, list
#                 An absolute or relative URL for a remote dataset
#             target_dir : str, list
#                 Absolute path of directory to download file to (including alf path)
#             keep_uuid : bool
#                 If true, the UUID is not removed from the file name (default is False)
#             file_size : int, list
#                 The expected file size or list of file sizes to compare with downloaded file
#             hash : str, list
#                 The expected file hash or list of file hashes to compare with downloaded file

#             Returns
#             -------
#             pathlib.Path
#                 The file path of the downloaded file or files.

#             Example
#             -------
#             >>> file_path = OneAlyx._download_file(
#             ...    'https://example.com/data.file.npy', '/home/Downloads/subj/1900-01-01/001/alf')
#             """
#             assert not self.offline
#             # Ensure all target directories exist
#             [Path(x).mkdir(parents=True, exist_ok=True) for x in set(util.ensure_list(target_dir))]

#             # download file(s) from url(s), returns file path(s) with UUID
#             local_path, md5 = self.alyx.download_file(url, target_dir=target_dir, return_md5=True)

#             # check if url, hash, and file_size are lists
#             if isinstance(url, (tuple, list)):
#                 assert (file_size is None) or len(file_size) == len(url)
#                 assert (hash is None) or len(hash) == len(url)
#             for args in zip(*map(util.ensure_list, (file_size, md5, hash, local_path, url))):
#                 self._check_hash_and_file_size_mismatch(*args)

#             # check if we are keeping the uuid on the list of file names
#             if keep_uuid:
#                 return local_path

#             # remove uuids from list of file names
#             if isinstance(local_path, (list, tuple)):
#                 return [alfio.remove_uuid_file(x) for x in local_path]
#             else:
#                 return alfio.remove_uuid_file(local_path)

#         def _check_hash_and_file_size_mismatch(self, file_size, hash, expected_hash, local_path, url):
#             """
#             Check to ensure the hash and file size of a downloaded file matches what is on disk

#             Parameters
#             ----------
#             file_size : int
#                 The expected file size to compare with downloaded file
#             hash : str
#                 The expected file hash to compare with downloaded file
#             local_path: str
#                 The path of the downloaded file
#             url : str
#                 An absolute or relative URL for a remote dataset
#             """
#             _logger = logging.getLogger("_check_hash_and_file_size_mismatch")
#             # verify hash size
#             hash = hash or hashfile.md5(local_path)
#             hash_mismatch = hash and hash != expected_hash
#             # verify file size
#             file_size_mismatch = file_size and Path(local_path).stat().st_size != file_size
#             # check if there is a mismatch in hash or file_size
#             if hash_mismatch or file_size_mismatch:
#                 # post download, if there is a mismatch between Alyx and the newly downloaded file size
#                 # or hash, flag the offending file record in Alyx for database for maintenance
#                 hash_mismatch = expected_hash and expected_hash != hash
#                 file_size_mismatch = file_size and Path(local_path).stat().st_size != file_size
#                 if hash_mismatch or file_size_mismatch:
#                     url = url or self.path2url(local_path)
#                     _logger.debug(f"Tagging mismatch for {url}")
#                     # tag the mismatched file records
#                     self._tag_mismatched_file_record(url)

#         @staticmethod
#         def setup(base_url=None, **kwargs):
#             """
#             Set up OneAlyx for a given database

#             Parameters
#             ----------
#             base_url : str
#                 An Alyx database URL.  If None, the current default database is used.
#             **kwargs
#                 Optional arguments to pass to one.params.setup.

#             Returns
#             -------
#             OneAlyx
#                 An instance of OneAlyx for the newly set up database URL
#             """
#             base_url = base_url or alyx_connector.params.get_default_client()
#             cache_map = alyx_connector.params.setup(client=base_url, **kwargs)
#             return OneAlyx(base_url=base_url or alyx_connector.params.get(cache_map.DEFAULT).ALYX_URL)

#         @util.refresh
#         @util.parse_id
#         def eid2path(self, eid, query_type=None) -> util.Listable(Path):
#             """
#             From an experiment ID gets the local session path

#             Parameters
#             ----------
#             eid : str, UUID, pathlib.Path, dict, list
#                 Experiment session identifier; may be a UUID, URL, experiment reference string
#                 details dict or Path.
#             query_type : str
#                 If set to 'remote', will force database connection

#             Returns
#             -------
#             pathlib.Path, list
#                 A session path or list of session paths
#             """

#             # first try avoid hitting the database
#             mode = query_type or self.mode
#             if mode != "remote":
#                 cache_path = super().eid2path(eid)
#                 if cache_path or mode == "local":
#                     return cache_path

#             # If eid is a list recurse through it and return a list
#             if isinstance(eid, list):
#                 unwrapped = unwrap(self.path2eid)
#                 return [unwrapped(self, e, query_type="remote") for e in eid]

#             # if it wasn't successful, query Alyx
#             ses = self.alyx.rest("sessions", "list", django=f"pk,{eid}")

#             if len(ses) == 0:
#                 return None

#             data_repository = ses[0]["default_data_repository"]
#             if data_repository is None:
#                 data_repository = ""

#             return os.path.join(
#                 data_repository,
#                 ses[0]["subject"],
#                 ses[0]["start_time"][:10],
#                 str(ses[0]["number"]).zfill(3),
#             )

#         @util.refresh
#         def path2eid(self, path_obj: Union[str, Path], query_type=None) -> util.Listable(Path):
#             import re

#             """
#             From a local path, gets the experiment ID

#             Parameters
#             ----------
#             path_obj : str, pathlib.Path, list
#                 Local path or list of local paths
#             query_type : str
#                 If set to 'remote', will force database connection

#             Returns
#             -------
#             str, list
#                 An eid or list of eids
#             """
#             # If path_obj is a list recurse through it and return a list
#             if isinstance(path_obj, list):
#                 path_obj = [Path(x) for x in path_obj]
#                 eid_list = []
#                 unwrapped = unwrap(self.path2eid)
#                 for p in path_obj:
#                     eid_list.append(unwrapped(self, p))
#                 return eid_list
#             # else ensure the path ends with mouse,date, number
#             try:
#                 path_obj = re.findall(r"(\w+(?:\\|\/)\d{4}-\d{2}-\d{2}(?:\\|\/)\d+)", path_obj)[0]
#             except IndexError:  # could not match anything
#                 pass

#             path_obj = Path(path_obj)

#             # try the cached info to possibly avoid hitting database
#             mode = query_type or self.mode
#             if mode != "remote":
#                 cache_eid = super().path2eid(path_obj)
#                 if cache_eid or mode == "local":
#                     return cache_eid

#             session_path = get_session_path(path_obj)
#             # if path does not have a date and a number return None
#             if session_path is None:
#                 return None

#             # if not search for subj, date, number XXX: hits the DB
#             search = unwrap(self.search)
#             uuid = search(
#                 subject=session_path.parts[-3],
#                 date_range=session_path.parts[-2],
#                 number=session_path.parts[-1],
#                 query_type="remote",
#             )

#             # Return the uuid if any
#             return uuid[0] if uuid else None

#         @util.refresh
#         def path2url(self, filepath, query_type=None) -> str:
#             """
#             Given a local file path, returns the URL of the remote file.

#             Parameters
#             ----------
#             filepath : str, pathlib.Path
#                 A local file path
#             query_type : str
#                 If set to 'remote', will force database connection

#             Returns
#             -------
#             str
#                 A URL string
#             """
#             query_type = query_type or self.mode
#             if query_type != "remote":
#                 return super().path2url(filepath)
#             eid = self.path2eid(filepath)
#             try:
#                 (dataset,) = self.alyx.rest("datasets", "list", session=eid, name=Path(filepath).name)
#                 return next(r["data_url"] for r in dataset["file_records"] if r["data_url"] and r["exists"])
#             except (ValueError, StopIteration):
#                 raise alferr.ALFObjectNotFound(f"File record for {filepath} not found on Alyx")

#         @util.parse_id
#         def type2datasets(self, eid, dataset_type, details=False):
#             """
#             Get list of datasets belonging to a given dataset type for a given session

#             Parameters
#             ----------
#             eid : str, UUID, pathlib.Path, dict
#                 Experiment session identifier; may be a UUID, URL, experiment reference string
#                 details dict or Path.
#             dataset_type : str, list
#                 An Alyx dataset type, e.g. camera.times or a list of dtypes
#             details : bool
#                 If True, a datasets DataFrame is returned

#             Returns
#             -------
#             np.ndarray, dict
#                 A numpy array of data, or DataFrame if details is true
#             """
#             assert self.mode != "local" and not self.offline, "Unable to connect to Alyx in local mode"
#             if isinstance(dataset_type, str):
#                 restriction = f"session__id,{eid},dataset_type__name,{dataset_type}"
#             elif isinstance(dataset_type, collections.abc.Sequence):
#                 restriction = f"session__id,{eid},dataset_type__name__in,{dataset_type}"
#             else:
#                 raise TypeError("dataset_type must be a str or str list")
#             datasets = util.datasets2records(self.alyx.rest("datasets", "list", django=restriction))
#             return datasets if details else datasets["rel_path"].sort_values().values

#         def dataset2type(self, dset) -> str:
#             """Return dataset type from dataset.

#             NB: Requires an Alyx database connection

#             Parameters
#             ----------
#             dset : str, np.ndarray, tuple
#                 A dataset name, dataset uuid or dataset integer id

#             Returns
#             -------
#             str
#                 The dataset type
#             """
#             assert self.mode != "local" and not self.offline, "Unable to connect to Alyx in local mode"
#             # Ensure dset is a str uuid
#             if isinstance(dset, str) and not is_uuid_string(dset):
#                 dset = self._dataset_name2id(dset)
#             if isinstance(dset, np.ndarray):
#                 dset = parquet.np2str(dset)[0]
#             if isinstance(dset, tuple) and all(isinstance(x, int) for x in dset):
#                 dset = parquet.np2str(np.array(dset))
#             if not is_uuid_string(dset):
#                 raise ValueError("Unrecognized name or UUID")
#             return self.alyx.rest("datasets", "read", id=dset)["dataset_type"]

#         def describe_revision(self, revision, full=False):
#             """Print description of a revision

#             Parameters
#             ----------
#             revision : str
#                 The name of the revision (without '#')
#             full : bool
#                 If true, returns the matching record

#             Returns
#             -------
#             None, dict
#                 None if full is false or no record found, otherwise returns record as dict
#             """
#             assert self.mode != "local" and not self.offline, "Unable to connect to Alyx in local mode"
#             try:
#                 rec = self.alyx.rest("revisions", "read", id=revision)
#                 print(rec["description"])
#                 if full:
#                     return rec
#             except requests.exceptions.HTTPError as ex:
#                 if ex.response.status_code != 404:
#                     raise ex
#                 print(f'revision "{revision}" not found')

#         def _dataset_name2id(self, dset_name, eid=None):
#             # TODO finish function
#             datasets = self.list_datasets(eid) if eid else self._cache["datasets"]
#             # Get ID of fist matching dset
#             for idx, rel_path in datasets["rel_path"].items():
#                 if rel_path.endswith(dset_name):
#                     return idx[-1]  # (eid, did)
#             raise ValueError(f"Dataset {dset_name} not found in cache")

#         @util.refresh
#         @util.parse_id
#         def get_details(self, eid: str, full: bool = False, query_type=None):
#             """Return session details for a given session

#             Parameters
#             ----------
#             eid : str, UUID, pathlib.Path, dict, list
#                 Experiment session identifier; may be a UUID, URL, experiment reference string
#                 details dict or Path.
#             full : bool
#                 If True, returns a DataFrame of session and dataset info
#             query_type : {'local', 'refresh', 'auto', 'remote'}
#                 The query mode - if 'local' the details are taken from the cache tables; if 'remote'
#                 the details are returned from the sessions REST endpoint; if 'auto' uses whichever
#                 mode ONE is in; if 'refresh' reloads the cache before querying.

#             Returns
#             -------
#             pd.Series, pd.DataFrame, dict
#                 in local mode - a session record or full DataFrame with dataset information if full is
#                 True; in remote mode - a full or partial session dict

#             Raises
#             ------
#             ValueError
#                 Invalid experiment ID (failed to parse into eid string)
#             requests.exceptions.HTTPError
#                 [Errno 404] Remote session not found on Alyx
#             """
#             if (query_type or self.mode) == "local":
#                 return super().get_details(eid, full=full)
#             # If eid is a list of eIDs recurse through list and return the results
#             if isinstance(eid, list):
#                 details_list = []
#                 for p in eid:
#                     details_list.append(self.get_details(p, full=full))
#                 return details_list
#             # load all details
#             dets = self.alyx.rest("sessions", "read", eid)
#             if full:
#                 return dets
#             # If it's not full return the normal output like from a one.search
#             det_fields = [
#                 "subject",
#                 "start_time",
#                 "number",
#                 "lab",
#                 "projects",
#                 "url",
#                 "task_protocol",
#                 "local_path",
#             ]
#             out = {k: v for k, v in dets.items() if k in det_fields}
#             out["projects"] = ",".join(out["projects"])
#             out.update(
#                 {
#                     "local_path": self.eid2path(eid),
#                     "date": datetime.fromisoformat(out["start_time"]).date(),
#                 }
#             )
#             return out

#         ### METHODS ADDED BY TIMOTHE TO EXTEND THE USE OF THE API

#         def get_data_repository_path(self, repository_name):
#             repo_data = self.alyx.rest("data-repository", "read", repository_name)
#             repo_path = r"\\" + os.path.join(repo_data["hostname"], repo_data["globus_path"].lstrip("/"))
#             return os.path.normpath(repo_path)

#         def read_sql(self, query):
#             raise NotImplementedError

#         def create_session(self, data_dict):
#             import re

#             data_dict = data_dict.copy()
#             data_dict["number"] = str(int(data_dict["number"]))  # remove leading zeros if any
#             try:  # a session have been found with same 3 parameters, don't allow to process to registering.
#                 _searched_session = self.search(
#                     subject=data_dict["subject"],
#                     number=data_dict["number"],
#                     date_range=data_dict["start_time"][:10],
#                     users=data_dict["users"],
#                     details=True,
#                 )
#                 url = re.sub(r"\/sessions", r"/actions/session", _searched_session["url"].item())
#                 raise ValueError(
#                     f"This session path is already registered ! See here : {url}\n You probably need to change the number "
#                     "of the session for this animald/date combo"
#                 )
#             except KeyError:  # no session have benn found, all is well, can proceed
#                 pass

#             self.alyx.rest("sessions", "create", data=data_dict)

#         def default_session_data(self):
#             return {"lab": "HaissLab", "task_protocol": ""}

#         @staticmethod
#         def explorer(path):
#             import subprocess

#             path = os.path.normpath(path)
#             if not os.path.exists(path):
#                 raise IOError(f"Path {path} does not exist")
#             if os.path.isfile(path):
#                 path = os.path.dirname(path)

#             pre_cmd = f"explorer.exe {path}"
#             subprocess.call(pre_cmd)
#             return
#             # this part is to open in atab instead of window. Buggy for now.
#             if path[:2] == r"\\":
#                 pre_cmd = f"pushd {path}; "
#                 # path = os.path.join(*[item for item in selected_folder.split(os.sep) if item != ''][1:])
#                 # #this removes the first object of the path
#             else:
#                 pre_cmd = 'cd "{pateh}; "'
#             subprocess.call(pre_cmd + r"start .")
#             # subprocess.Popen(r'explorer '+f'{os.path.dirname(path)},select,{os.path.basename(path)}')

#         def collection_path(self, eid, collections):
#             return os.path.join(self.current_remote_repository_path, self.eid2path(eid), collections)

#         def set_current_remote_repository(self, repo_name):
#             self.current_remote_repository_path = self.get_data_repository_path(repo_name)

#         def get_parts_from_path(self, input_path):
#             import re

#             subject, date, number = re.findall(r"(\w+)(?:\\|\/)(\d{4}-\d{2}-\d{2})(?:\\|\/)(\d+)", input_path)[0]
#             return {"subject": subject, "date": date, "number": number}

#         def get_json_params(self, eid):
#             return self.alyx.rest("sessions", "read", eid)["json"]

#         def get_extended_qc(self, eid):
#             return self.alyx.rest("sessions", "read", eid)["extended_qc"]

#         def display_session_info(self, session_details):
#             from IPython.display import Markdown, display

#             session_data_link = f"[Data pathes](file:{os.path.normpath(session_details.path)}) pointing in {self.data_access_mode} mode."
#             metadatas_link = f"[Metadatas]({session_details.url}) obtained in {self.mode} mode."
#             uuid = f"(uuid is '{session_details.name}')"
#             display(Markdown(f"Session {session_details.rel_path}. {session_data_link} {metadatas_link} {uuid}"))

#         @wraps(MultiSessionPlaceholder)
#         def multisession(*args, **kwargs):
#             return MultiSessionPlaceholder(*args, **kwargs)

#         def update_session_info(self, session_details, json={}, extended_qc={}, **kwargs):
#             _logger = logging.getLogger("update_session_info")

#             data = {}
#             if len(json):
#                 base_json = session_details.json
#                 if base_json is None:
#                     base_json = {}
#                 base_json.update(json)
#                 data["json"] = base_json

#             if len(extended_qc):
#                 base_ext_qc = session_details.extended_qc
#                 if base_ext_qc is None:
#                     base_ext_qc = {}
#                 base_ext_qc.update(extended_qc)
#                 data["extended_qc"] = base_ext_qc

#             data.update(kwargs)

#             _logger.info(f"Updating session {session_details.rel_path}")
#             ans = input(f"Data OK ? (OK or Cancel) : {data}")
#             if ans != "OK":
#                 _logger.info("Aborting")
#                 return
#             _logger.info("Applying changes")
#             self.alyx.rest("sessions", "partial_update", id=session_details.name, data=data)
#             self.alyx.delete_cache()

#         def push_files(self, file_list, *, session_details, relative=False, overwrite_policy="raise"):
#             """
#             The function push_processed_files copies a list of files to a remote session directory,
#             and handles various overwrite policies.

#             Args:

#                 - file_list (list of str): a list of local file paths to be copied to the remote session directory.
#                 - session_details (SessionDetails): an object containing the session details.
#                 - relative: If the string representing the path of the files are absolute or relative from
#                     INSIDE the session folder (ex : 'D:\\LOCAL_DATA\\wm25\\2022-08-05\\001\\a_folder\\test.file'
#                     is an absolute path and 'a_folder\test.file' is a relative path)
#                 - overwrite_policy (str, optional): the overwrite policy. Possibilities are:
#                 - "raise" (raise an exception if a file with the same name already exists in the remote directory),
#                 - "skip" (skip copying the file if a file with the same name already exists in the remote directory),
#                 - "overwrite" (overwrite the file with the same name in the remote directory),
#                 - "most_recent" (copy the file only if it is more recent than the file
#                     with the same name in the remote directory),
#                 - "erase" (erase the existing file in the remote directory if a file with the same name already exists,
#                     then copy a new one in place.) Prefer choosing overwrite in most situations).

#                 Default is "raise".

#             Returns:

#                 a dictionary with two keys:
#                 - "copied" : a list of file paths that have been copied to the remote session directory.
#                 - "ignored" : a list of file paths that could not be copied (either because they were not found or
#                     because they were excluded by the overwrite policy).
#             """

#             import shutil

#             def get_relative_path(absolute_path, common_root_path):
#                 """Compare an input path and a path with a common root with the input path, and returns only the part of
#                 the input path that is not shared with the _common_root_path.

#                 Args:
#                     input_path (TYPE): DESCRIPTION.
#                     common_root_base (TYPE): DESCRIPTION.

#                 Returns:
#                     TYPE: DESCRIPTION.

#                 """
#                 absolute_path = os.path.normpath(absolute_path)
#                 common_root_path = os.path.normpath(common_root_path)

#                 commonprefix = os.path.commonprefix([common_root_path, absolute_path])
#                 if commonprefix == "":
#                     raise IOError(f"These two pathes have no common root path : {absolute_path} and {common_root_path}")
#                 return os.path.relpath(absolute_path, start=commonprefix)

#             overwrite_policies = ["raise", "skip", "erase", "most_recent", "overwrite"]
#             if overwrite_policy not in overwrite_policies:
#                 raise NotImplementedError(
#                     f"Value {overwrite_policy} for overwrite_policy is not supported. "
#                     f"Possibilities are : {overwrite_policies}"
#                 )

#             session_local_root = session_details["local_path"]
#             session_remote_root = session_details["remote_path"]

#             if not isinstance(file_list, (list, tuple, np.ndarray)):
#                 file_list = [file_list]

#             if relative:
#                 file_list = [os.path.join(session_local_root, session_details.rel_path, file) for file in file_list]

#             source_files = []  # file paths that have been copied
#             # files paths of the copies of the one in the list above (ordered similarly)
#             dest_files = []

#             overwrite_raise_list = (
#                 []
#             )  # stores files that already exist in remote folder in case we have 'raise' overwrite_policy, to print to user
#             file_not_found_warning = []

#             # MAKE THE LIST OF FILES THAT WILL BE COPIED
#             for local_path in file_list:
#                 if not os.path.isfile(local_path):
#                     file_not_found_warning.append(local_path)
#                     continue

#                 try:
#                     relative_path = get_relative_path(local_path, session_local_root)
#                 except IOError:
#                     file_not_found_warning.append(local_path)
#                     continue

#                 remote_path = os.path.join(session_remote_root, relative_path)

#                 if os.path.isfile(remote_path):
#                     if overwrite_policy == "raise":
#                         overwrite_raise_list.append(local_path)
#                     elif overwrite_policy == "skip":
#                         logging.getLogger().info(f"Skipping file {remote_path}")
#                         continue
#                     elif overwrite_policy == "erase":
#                         os.remove(remote_path)
#                     elif overwrite_policy == "most_recent":
#                         if (
#                             os.stat(remote_path).st_mtime > os.stat(local_path).st_mtime
#                         ):  # remote_path is more recent (higher time value)
#                             continue
#                     elif overwrite_policy == "overwrite":
#                         pass
#                         # else if overwrite, we just do nothing and files will be overwritten during shutil.copy operation

#                 source_files.append(local_path)
#                 dest_files.append(remote_path)

#             if len(file_not_found_warning):
#                 logging.getLogger().warning(
#                     "These files were not found or are not inside the session local folder, "
#                     f"and therefore cannot be copied :\nFiles:\n{file_not_found_warning}"
#                 )

#             if len(overwrite_raise_list):
#                 raise IOError(
#                     f"The files listed below already exist in the destination : {session_remote_root}\n"
#                     "No file have been copied to avoid mistakes. "
#                     "You can check the content of the destination folder manually, "
#                     "or change the 'overwrite_policy' argument of this function (beware of data losses !).\n"
#                     f"Files :\n {overwrite_raise_list}"
#                 )

#             # APPLY THE COPY
#             for local_path, remote_path in zip(source_files, dest_files):
#                 container_dir = os.path.dirname(remote_path)
#                 # make destination dir if it doesn't exist already
#                 os.makedirs(container_dir, exist_ok=True)
#                 shutil.copy(local_path, remote_path)

#             return {
#                 "copied": source_files,
#                 "ignored": list(set(file_list).difference(set(source_files))),
#             }

#         def copy_files(self, source_file_list, source_root, destination_root, overwrite_policy="raise"):
#             def get_relative_path(absolute_path, common_root_path):
#                 """
#                 Compare an input path and a path with a common root with the input path, and returns only the part of the
#                 input path that is not shared with the _common_root_path.

#                 Args:
#                     input_path (TYPE): DESCRIPTION.
#                     common_root_base (TYPE): DESCRIPTION.

#                 Returns:
#                     TYPE: DESCRIPTION.

#                 """
#                 absolute_path = os.path.normpath(absolute_path)
#                 common_root_path = os.path.normpath(common_root_path)

#                 commonprefix = os.path.commonprefix([common_root_path, absolute_path])
#                 if commonprefix == "":
#                     raise IOError(f"These two pathes have no common root path : {absolute_path} and {common_root_path}")
#                 return os.path.relpath(absolute_path, start=commonprefix)

#             # Check if the overwrite_policy is supported
#             overwrite_policies = ["raise", "skip", "erase", "most_recent", "overwrite"]
#             if overwrite_policy not in overwrite_policies:
#                 raise ValueError(
#                     f"Unsupported value for overwrite_policy: {overwrite_policy}. Possibilities are: {overwrite_policies}"
#                 )

#             # Iterate through the list of source files and copy them to the destination
#             copied_files = []
#             ignored_files = []

#             for source_file_path in source_file_list:
#                 # Get the relative path of the source file with respect to the source root
#                 relative_path = get_relative_path(source_file_path, source_root)

#                 # Create the destination path by joining the destination root and the relative path
#                 destination_path = os.path.join(destination_root, relative_path)

#                 # Check if the file exists at the source location and is a file
#                 if not os.path.isfile(source_file_path):
#                     ignored_files.append(source_file_path)
#                     continue

#                 # Check if the file exists at the destination location and apply the overwrite policy accordingly
#                 if os.path.isfile(destination_path):
#                     if overwrite_policy == "raise":
#                         raise ValueError(f"File already exists at the destination: {destination_path}")
#                     elif overwrite_policy == "skip":
#                         ignored_files.append(source_file_path)
#                         continue
#                     elif overwrite_policy == "erase":
#                         os.remove(destination_path)
#                     elif overwrite_policy == "most_recent":
#                         if os.stat(destination_path).st_mtime > os.stat(source_file_path).st_mtime:
#                             ignored_files.append(source_file_path)
#                             continue
#                     elif overwrite_policy == "overwrite":
#                         pass

#                 # Create the directory structure at the destination if it does not exist already
#                 os.makedirs(os.path.dirname(destination_path), exist_ok=True)

#                 # Copy the file from source to destination
#                 shutil.copy(source_file_path, destination_path)

#                 # Append the copied file to the list of copied files
#                 copied_files.append(source_file_path)

#             # Return a dictionary with the list of copied and ignored files
#             return {"copied": copied_files, "ignored": ignored_files}

#         def pull_files(self, file_list, *, session_details, relative=False, overwrite_policy="raise"):
#             pass
#
