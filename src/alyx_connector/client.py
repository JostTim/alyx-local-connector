import re
import requests
import math
import json
from requests.models import Response
from urllib.parse import urlparse, urlunparse, urlsplit
from openapi_parser import parse as parse_openapi_schema
from openapi_parser.specification import Operation, Specification, Path as OpenAPIPath
from openapi_parser.errors import ParserError
from datetime import datetime, timedelta
from rich.prompt import Prompt
from rich.text import Text
from logging import getLogger
from pathlib import Path
from collections.abc import Mapping
from abc import ABC, abstractmethod

from .logging import temporary_filter_out
from . import params as config

from typing import Any, Dict, Tuple, List, Callable, Optional, Literal, Protocol

logger = getLogger("alyx_connector.client")


class RequestFunction(Protocol):
    def __call__(
        self,
        url: str,
        *,
        stream: Optional[bool] = True,
        headers: Optional[dict] = None,
        data: Optional[Any] = None,
        files: Optional[Any] = None,
    ) -> Response: ...


class OpenAPISpecification(Specification):

    @property
    def paths_dict(self) -> Dict[str, OpenAPIPath]:
        return {path.url: path for path in self.paths}


class UrlPath(str):

    client: "Client"
    requirements: List[str]

    def __new__(cls, path: str, client: "Client"):
        path = cls.normalize_path(path)
        obj = super(UrlPath, cls).__new__(cls, path)
        setattr(obj, "client", client)
        setattr(obj, "requirements", cls.parse_requirements(path))
        return obj

    @staticmethod
    def parse_requirements(path):
        pattern = re.compile(r"{(\w+)}")
        matches = pattern.findall(path)
        return matches

    @staticmethod
    def normalize_path(path):
        if not path.startswith("/"):
            path = "/" + path

        # Ensure the path does not end with a '/'
        if path.endswith("/"):
            path = path[:-1]

        return path

    def make_url(self, fragment="", **kwargs):
        """_summary_

        Args:
            params (str, optional): Query parameters. Defaults to "".
            query (str, optional): Query string , separated from the rest of the url by an ?
                (? wich you should not provide here). Defaults to "".
            fragment (str, optional): Section, separated from the rest of the url by an #
                (# wich you should not provide here) also called anchor. Defaults to "".

        Returns:
            _type_: _description_
        """
        query_dict, requirements_dict = self.separate_query_and_requirements(**kwargs)
        url = urlunparse(
            (
                self.client.protocol,  # http or https
                self.client.netloc,  # netloc = host+port
                self.finalized_path(**requirements_dict),  # path
                "",  # params
                self.make_query_string(query_dict),  # querystring example : ?thing=truc
                fragment,  # basically an anchor, fragment example : #title1
            )
        )
        return url

    def finalized_path(self, **requirements_dict):
        path = str(self)
        for requirement in self.requirements:
            if (requirement_value := requirements_dict.get(requirement)) is None:
                raise ValueError(f"You must provide a {requirement} keyword argument with the path {self}")
            path = path.replace(f"{{{requirement}}}", str(requirement_value))
        return path

    def separate_query_and_requirements(self, **kwargs) -> Tuple[dict, dict]:
        """Separates the query parameters (key values) and the requirements (parts of the url that are needed)
        from an unpacked dictionnary as input.

        Returns:
            Tuple[dict, dict]: dictionnary of query arguments, dictionnary of path required elements
        """
        requirements = {k: v for k, v in kwargs.items() if k in self.requirements}
        query_dict = {k: v for k, v in kwargs.items() if k not in self.requirements}
        return query_dict, requirements

    def make_query_string(self, query_dict: dict) -> str:
        query_list = []
        for key, value in query_dict.items():
            query_list.append(f"{key}={value}")
        return "&".join(query_list)

    @property
    def endpoint(self):
        return Endpoint(self, self.client)


class Client(ABC):
    """
    Client class for managing connections.

    Attributes:
        protocol (str): Protocol used by the client, e.g., 'http' or 'https'.
        host (str): Host address, e.g., '127.0.0.1'.
        port (str): Port number, e.g., '80'.
        schema_endpoint (str): Endpoint for the API schema.
        default_port (str): Default port if none is specified.
    """

    protocol: str
    host: str
    port: str

    schema_endpoint = "/api/schema"
    default_port = "80"

    _schema = None
    _par = None
    _headers = None

    def __init__(self, url: str, username=Optional[str], *, silent=True):

        self.protocol, self.host, self.port, validated_url = self._validate_url(url)
        self.username = username
        self.silent = silent
        self._obj_id = id(self)

    def _validate_url(self, input_url: str):
        """Validate and correct a given URL.

        This method checks if the input URL starts with 'http:' or 'https:'.
        If not, it attempts to correct the URL by prepending 'http://' and
        assumes the protocol is HTTP. It also ensures that a port is specified;
        if missing, it defaults to port 80.

        Args:
            input_url (str): The URL to be validated and corrected.

        Returns:
            tuple: A tuple containing:
                - protocol (str): The protocol of the validated URL.
                - host (str): The host of the validated URL.
                - port (str): The port of the validated URL.
                - validated_url (str): The corrected and validated URL.
        """

        if not input_url.startswith(("http:", "https:")):
            scheme_and_netloc = input_url.split("//")
            if len(scheme_and_netloc) == 1:
                url = "http://" + scheme_and_netloc[0]
            else:
                url = "http://" + scheme_and_netloc[1]
            print(f"corrected invalid url {input_url} into {url} asuming http protocol")
        else:
            url = input_url

        parsed_url = urlparse(url)
        protocol = parsed_url.scheme
        host_and_port = parsed_url.netloc.split(":")
        if len(host_and_port) == 2:
            port = host_and_port[1]
            host = host_and_port[0]
        else:
            port = "80"
            host = host_and_port[0]
            print(f"corrected url {input_url} missing port info into port 80 asuming http protocol is used")

        validated_url = self.urlunparse(protocol, host, port, original_url=input_url)

        return protocol, host, port, validated_url

    @property
    def netloc(self):
        "example : 127.0.0.1:80"
        return f"{self.host}:{self.port}"

    @property
    def url(self):
        "example : http://127.0.0.1:80"
        return self.urlunparse(self.protocol, self.host, self.port)

    base_url = url

    @property
    def params(self):
        if self._par is None:
            self._par = self.get_params()
        return self._par

    @property
    def headers(self) -> dict:
        if self._headers is None:
            self._headers = self.get_initial_headers()
        return self._headers

    @property
    def schema(self) -> OpenAPISpecification:
        if self._schema:
            return self._schema
        self._schema = self.get_initial_schema()
        return self._schema

    def urlunparse(self, protocol, host, port, original_url: Optional[str] = None):
        url = urlunparse((protocol, f"{host}:{port}", "", "", "", ""))
        if url == "":
            raise ValueError(f"Cound not parse the url {original_url}. Verify it is correct")
        return url

    def get_initial_schema(self) -> OpenAPISpecification:
        logger = getLogger()
        try:
            with temporary_filter_out(logger, "Implicit type assignment: schema does not contain 'type' property"):
                schema = parse_openapi_schema(self.url + self.schema_endpoint)
            schema.paths_dict = {path.url: path for path in schema.paths}  # type: ignore
            return schema  # type: ignore
        except ParserError:
            raise ConnectionError(
                f"Can't connect to {self.url}.\n" + "Check your internet connections and Alyx database firewall"
            )

    def get_initial_headers(self):
        return {**{}, "Accept": "application/json"}

    def get_params(self, url=None, username=None, silent=None):
        return config.get(
            client=url or getattr(self, "url", None),
            username=username or getattr(self, "username", None),
            silent=silent if silent is not None else getattr(self, "silent", False),
        )

    def list_endpoints(self):
        return sorted(self.schema.paths_dict.keys())

    def path(self, path: str) -> UrlPath:
        return UrlPath(path, self)

    def endpoint(self, path: str) -> "Endpoint":
        return self.path(path).endpoint

    def endpoint_exists(self, path: str) -> bool:
        return self.endpoint(path).exists()

    def rest(self, endpoint_name: str, action: str, **kwargs):
        endpoint = self.path(endpoint_name).endpoint.assert_exists()
        return endpoint.actions[action].request(**kwargs)

    def search(self, endpoint_name: str, **kwargs):
        if kwargs.get("id"):
            return self.rest(endpoint_name, "retrieve", **kwargs)
        return self.rest(endpoint_name, "list", **kwargs)

    def describe(self, endpoint_name: str):
        endpoint = self.path(endpoint_name).endpoint.assert_exists()
        return endpoint

    @abstractmethod
    def authenticate(self, *args, **kwargs):
        return NotImplementedError

    @abstractmethod
    def ensure_authenticated(self, *args, **kwargs):
        return NotImplementedError

    def logout(self, *args, **kwargs):
        return NotImplementedError


class Endpoint:

    def __init__(self, path: UrlPath, client: Client):

        self.path = path
        self.client = client

    def exists(self):
        return True if self.routes else False

    @property
    def routes(self):
        search_path = self.path.replace("/", r"\/")
        pattern = re.compile(rf"^{search_path}(?:(?=\/{{).*)?$")
        return [UrlPath(key, self.client) for key in self.client.schema.paths_dict.keys() if pattern.match(key)]

    @property
    def actions(self):
        return {
            operation.operation_id.split("_")[-1]: Operateur(route, self.client, operation)
            for route in self.routes
            for operation in self.client.schema.paths_dict[route].operations
            if operation.operation_id is not None
        }

    def assert_exists(self):
        if not self.exists():
            self.raise_not_existing()
        return self

    def raise_not_existing(self):
        raise ValueError(f"Endpoint {self.path} do not exist in the schema")


class Operateur:

    def __init__(self, path: UrlPath, client: Client, operation: Operation):
        self.path = path
        self.client = client
        self.operation = operation

    @property
    def action_name(self):
        operation_id = self.operation.operation_id
        action_name = operation_id.split("_")[-1] if operation_id is not None else ""
        return action_name

    def request(self, data=None, files=None, **kwargs):
        request_method: RequestFunction = getattr(requests, self.operation.method.value)
        try:
            url = self.path.make_url(**kwargs)
        except ValueError as e:
            raise ValueError(f"For the {self.action_name} action, " + str(e)) from e
        return self.get_response(request_method, url, data=data, files=files)

    def get_response(self, request_method: RequestFunction, url: str, data=None, files=None):
        self.client.ensure_authenticated()

        headers = self.client.headers.copy()
        logger.info(f"Sending a request with url={url}, headers={headers}")
        if files is None:
            data = json.dumps(data) if isinstance(data, dict) or isinstance(data, list) else data
            headers["Content-Type"] = "application/json"
        r = request_method(
            url,
            stream=True,
            headers=headers,
            data=data,
            files=files,
        )
        if r and r.status_code in (200, 201):
            return json.loads(r.text)
        elif r and r.status_code == 204:
            return
        if r.status_code == 403 and '"Invalid token."' in r.text:
            logger.debug("Token invalid; Attempting to re-authenticate...")
            # Log out in order to flush stale token.  At this point we no longer have the password
            # but if the user re-instantiates with a password arg it will request a new token.
            if self.client.silent:  # no need to log out otherwise; user will be prompted for password
                self.client.logout()
            self.client.authenticate(username=self.client.username, force=True)
            return self.get_response(request_method, url, data=data, files=files)
        else:
            logger.debug("Response text: " + r.text)
            try:
                message = json.loads(r.text)
                message.pop("status_code", None)  # Get status code from response object instead
                message = message.get("detail") or message  # Get details if available
            except json.decoder.JSONDecodeError:
                message = r.text
            raise requests.HTTPError(r.status_code, url, message, response=r)

    def __repr__(self):
        return f"{self.path} - {self.operation}"

    def describe(self):
        return self.client.schema.paths_dict[self.path]


class TokenizedClient(Client):

    default_expiry = timedelta(days=1)
    cache_mode = "GET"
    _token: str = ""

    def __init__(
        self,
        url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        *,
        silent: Optional[bool] = False,
        # cache_dir: Optional[bool] = None,
    ):
        if url is not None:
            _, _, _, url = self._validate_url(url)

        _params = self.get_params(url, username, silent)

        if username is None:
            username = _params.ALYX_LOGIN

        if url is None:
            url = _params.ALYX_URL

        super().__init__(url=url, username=username, silent=silent)  # type: ignore
        self.authenticate(username, password, cache_token=True)

    def clear_rest_cache(self):
        """Clear all REST response cache files for the base url"""
        for file in self.cache_dir.joinpath(".rest").glob("*"):
            file.unlink()

    @property
    def cache_dir(self):
        """pathlib.Path: The location of the downloaded file cache"""
        return Path(self.params.CACHE_DIR)

    # def delete_cache(self):
    #     """Delete all cached files in the .rest directory of your ONE installation "
    #     "(usually located in ONE inside downloads)"""
    #     cache_dir = self.cache_dir.joinpath(".rest")
    #     for item in cache_dir.iterdir():
    #         item.unlink() if item.is_file() else None

    @property
    def token(self) -> str:
        if not self._token:
            self.authenticate()
        return self._token

    def ensure_authenticated(self):
        if not self.token or "Authorization" not in self.headers:
            raise ConnectionError("Cannot authenticate")

    def authenticate(self, username=None, password=None, cache_token=True, force=False) -> str | None:
        """
        Gets a security token from the Alyx REST API to create requests headers.
        Credentials are loaded via one.params

        Parameters
        ----------
        username : str
            Alyx username.  If None, token not cached and not silent, user is prompted.
        password : str
            Alyx password.  If None, token not cached and not silent, user is prompted.
        cache_token : bool
            If true, the token is cached for subsequent auto-logins
        force : bool
            If true, any cached token is ignored
        """
        # Get username
        if username is None:
            username = getattr(self._par, "ALYX_LOGIN", self.username)
        if username is None and not self.silent:
            username = input("Enter Alyx username:")

        # Check if token cached
        if not force and getattr(self.params, "TOKEN", False) and username in self.params.TOKEN:
            self._token = self.params.TOKEN[username]["token"]
            self._headers = {
                "Authorization": f"Token {self._token}",
                "Accept": "application/json",
            }
            self.username = username
            return self._token

        # Get password
        if password is None:
            password = getattr(self._par, "ALYX_PWD", None)
        if password is None and not self.silent:
            password = Prompt.ask(
                Text("Enter Alyx password for ", style="blue").append(f"{username}", style="turquoise2"), password=True
            )

        try:
            credentials = {"username": username, "password": password}
            rep = requests.post(self.base_url + "/auth-token", data=credentials)
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Can't connect to {self.url}.\n" + "Check your internet connections and Alyx database firewall"
            )
        # Assign token or raise exception on auth error
        if rep.ok:
            self._token = rep.json().get("token")
        else:
            if rep.status_code == 400:  # Auth error; re-raise with details
                redacted = "*" * len(credentials["password"]) if credentials["password"] else None
                message = (
                    "Alyx authentication failed with credentials: "
                    f"user = {credentials['username']}, password = {redacted}"
                )
                raise requests.HTTPError(rep.status_code, rep.url, message, response=rep)
            else:
                rep.raise_for_status()
            return self._token

        self._headers = {
            "Authorization": f"Token {self.token}",
            "Accept": "application/json",
        }
        self.username = username

        if cache_token:
            token_struct = {username: {"token": self._token}}
            config.save(self.params.set("TOKEN", token_struct), self.url)

        if not self.silent:
            print(f"Connected to {self.url} as {self.username}")

        return self._token


class AlyxClient(TokenizedClient):

    def logout(self):
        """Log out from Alyx
        Deletes the cached authentication token for the currently logged-in user
        """
        raise NotImplementedError("Deprecated")
        if not self.is_logged_in:
            return
        par = alyx_connector.params.get(client=self.base_url, silent=True)
        username = self.user
        # Remove token from cache
        if getattr(par, "TOKEN", False) and username in par.TOKEN:
            del par.TOKEN[username]
            alyx_connector.params.save(par, self.base_url)
        # Remove token from local pars
        if getattr(self._par, "TOKEN", False) and username in self._par.TOKEN:
            del self._par.TOKEN[username]
        # Remove token from object
        self.user = None
        self._token = None
        if self._headers and "Authorization" in self._headers:
            del self._headers["Authorization"]
        self.clear_rest_cache()
        if not self.silent:
            print(f"{username} logged out from {self.base_url}")

    def delete(self, json_response):
        """
        Sends a DELETE request to the Alyx server. Will raise an exception on any status_code
        other than 200, 201.

        Parameters
        ----------
        rest_query : str
            A REST query string either as a relative URL path complete URL

        Returns
        -------
        JSON interpreted dictionary from response

        Examples
        --------
        >>> AlyxClient.delete('/weighings/c617562d-c107-432e-a8ee-682c17f9e698')
        >>> AlyxClient.delete(
        ...     'https://alyx.example.com/endpoint/c617562d-c107-432e-a8ee-682c17f9e698')
        """
        return self._generic_request(requests.delete, rest_query)

    # def download_file(self, url, **kwargs):
    #     """
    #     Downloads a single file or list of files on the Alyx server from a
    #     file record REST field URL

    #     Parameters
    #     ----------
    #     url : str, list
    #         Full url(s) of the file(s)
    #     kwargs : Any
    #         WebClient.http_download_file parameters

    #     Returns
    #     -------
    #     Local path(s) of downloaded file(s)
    #     """
    #     if isinstance(url, str):
    #         url = self._validate_file_url(url)
    #         download_fcn = http_download_file
    #     else:
    #         url = (self._validate_file_url(x) for x in url)
    #         download_fcn = http_download_file_list
    #     pars = dict(
    #         silent=kwargs.pop("silent", self.silent),
    #         target_dir=kwargs.pop("target_dir", self._par.CACHE_DIR),
    #         username=self._par.HTTP_DATA_SERVER_LOGIN,
    #         password=self._par.HTTP_DATA_SERVER_PWD,
    #         **kwargs,
    #     )
    #     try:
    #         files = download_fcn(url, **pars)
    #     except HTTPError as ex:
    #         if ex.code == 401:
    #             ex.msg += (
    #                 " - please check your HTTP_DATA_SERVER_LOGIN and "
    #                 "HTTP_DATA_SERVER_PWD ONE params, or username/password kwargs"
    #             )
    #         raise ex
    #     return files

    # def download_cache_tables(self, source=None, destination=None):
    #     """Downloads the Alyx cache tables to the local data cache directory

    #     Parameters
    #     ----------
    #     source : str, pathlib.Path
    #         The remote HTTP directory of the cache table (excluding the filename).
    #         Default: AlyxClient.base_url.
    #     destination : str, pathlib.Path
    #         The target directory into to which the tables will be downloaded.

    #     Returns
    #     -------
    #         List of parquet table file paths.
    #     """
    #     # query the database for the latest cache; expires=None overrides cached response
    #     self.cache_dir.mkdir(exist_ok=True)
    #     if not self.is_logged_in:
    #         self.authenticate()
    #     source = str(source or f"{self.base_url}/cache.zip")
    #     destination = destination or self.cache_dir

    #     headers = self._headers if source.startswith(self.base_url) else None
    #     with tempfile.TemporaryDirectory(dir=destination) as tmp:
    #         file = http_download_file(
    #             source,
    #             headers=headers,
    #             silent=self.silent,
    #             target_dir=tmp,
    #             clobber=True,
    #         )
    #         with zipfile.ZipFile(file, "r") as zipped:
    #             files = zipped.namelist()
    #             zipped.extractall(destination)
    #     return [Path(destination, table) for table in files]

    def _validate_file_url(self, url):
        """Asserts that URL matches HTTP_DATA_SERVER parameter.
        Currently only one remote HTTP server is supported for a given AlyxClient instance.  If
        the URL contains only the relative path part, the full URL is returned.

        Parameters
        ----------
        url : str
            The full or partial URL to validate

        Returns
        -------
            The complete URL

        Examples
        --------
        >>> url = self._validate_file_url('https://webserver.net/path/to/file')
        'https://webserver.net/path/to/file'
        >>> url = self._validate_file_url('path/to/file')
        'https://webserver.net/path/to/file'
        """
        # (timothé) : We don't use Web based file transfert, so i commented this part to avoid assertion errors with
        # admin urls and such
        # if url.startswith('http'):  # A full URL
        #     assert url.startswith(self._par.HTTP_DATA_SERVER), \
        #         ('remote protocol and/or hostname does not match HTTP_DATA_SERVER parameter:\n' +
        #          f'"{url[:40]}..." should start with "{self._par.HTTP_DATA_SERVER}"')
        # elif not url.startswith(self._par.HTTP_DATA_SERVER):
        #     url = self.rel_path2url(url)
        return url

    def rel_path2url(self, path):
        """Given a relative file path, return the remote HTTP server URL.
        It is expected that the remote HTTP server has the same file tree as the local system.

        Parameters
        ----------
        path : str, pathlib.Path
            A relative ALF path (subject/date/number/etc.)

        Returns
        -------
            A URL string
        """
        path = str(path).strip("/")
        assert not path.startswith("http")
        return f"{self._par.HTTP_DATA_SERVER}/{path}"

    # def rel_path2admin_url(self, path):
    #     path = str(path).strip("/")
    #     if path.startswith("http"):
    #         return path
    #     return f"{self._par.ALYX_URL}/{path}"

    # def urlify_dict(self, l_result):
    #     keys_to_update = []
    #     for key, value in l_result.items():
    #         if "admin_url" in key:
    #             keys_to_update.append(key)
    #         if isinstance(value, dict):
    #             l_result[key] = self.urlify_dict(l_result[key])
    #         elif isinstance(value, list):
    #             l_result[key] = self.urlify_list(l_result[key])
    #     for key in keys_to_update:
    #         l_result[key] = self.rel_path2admin_url(l_result[key])

    #     return l_result

    # def urlify_list(self, l_result):
    #     for index, value in enumerate(l_result):
    #         if isinstance(value, dict):
    #             l_result[index] = self.urlify_dict(l_result[index])
    #     return l_result

    # def urlify_paginated_response(self, l_result):
    #     for item in l_result:
    #         if isinstance(item, list):
    #             yield self.urlify_list(item)
    #         elif isinstance(item, dict):
    #             yield self.urlify_dict(item)
    #         else:
    #             raise TypeError

    def fix_url(self, data, fixed_keys=["admin_url"], do_fix=False):

        if isinstance(data, list):
            return [self.fix_url(item, do_fix=False) for item in data]
        elif isinstance(data, dict):
            return {
                key: (self.fix_url(item, do_fix=True) if key in fixed_keys else self.fix_url(item, do_fix=False))
                for key, item in data.items()
            }
        elif isinstance(data, _PaginatedResponse):
            data.add_finishing_callback(self.fix_url)
            return data
        elif do_fix:
            baseurl = urllib.parse.urlsplit(self.base_url)
            return urllib.parse.urlsplit(data)._replace(scheme=baseurl.scheme, netloc=baseurl.netloc).geturl()
        else:
            return data

    # def urlify_result(self, result):
    #     if isinstance(result, _PaginatedResponse):
    #         return self.urlify_paginated_response(result)
    #     elif isinstance(result, dict):
    #         return self.urlify_dict(result)
    #     elif isinstance(result, list):
    #         return self.urlify_list(result)
    #     else:
    #         raise TypeError(f"HTTP Request result was not a dict nor a _PaginatedResponse but type : {type(result)}")

    def patch(self, rest_query, data=None, files=None):
        """
        Sends a PATCH request to the Alyx server.
        For the dictionary contents, refer to:
        https://openalyx.internationalbrainlab.org/docs

        Parameters
        ----------
        rest_query : str
            The endpoint as full or relative URL
        data : dict, str
            JSON encoded string or dictionary (c.f. requests)
        files : dict, tuple
            Files to attach (c.f. requests)

        Returns
        -------
        Response object
        """
        rep = self._generic_request(requests.patch, rest_query, data=data, files=files)
        return self.fix_url(rep)

    def post(self, rest_query, data=None, files=None):
        """
        Sends a POST request to the Alyx server.
        For the dictionary contents, refer to:
        https://openalyx.internationalbrainlab.org/docs

        Parameters
        ----------
        rest_query : str
            The endpoint as full or relative URL
        data : dict, str
            JSON encoded string or dictionary (c.f. requests)
        files : dict, tuple
            Files to attach (c.f. requests)

        Returns
        -------
        Response object
        """
        rep = self._generic_request(requests.post, rest_query, data=data, files=files)
        return self.fix_url(rep)

    def put(self, rest_query, data=None, files=None):
        """
        Sends a PUT request to the Alyx server.
        For the dictionary contents, refer to:
        https://openalyx.internationalbrainlab.org/docs

        Parameters
        ----------
        rest_query : str
            The endpoint as full or relative URL
        data : dict, str
            JSON encoded string or dictionary (c.f. requests)
        files : dict, tuple
            Files to attach (c.f. requests)

        Returns
        -------
        requests.Response
            Response object
        """
        rep = self._generic_request(requests.put, rest_query, data=data, files=files)
        return self.fix_url(rep)

    def rest(
        self,
        url=None,
        action=None,
        id=None,
        data=None,
        files=None,
        no_cache=False,
        **kwargs,
    ):
        """
        alyx_client.rest(): lists endpoints
        alyx_client.rest(endpoint): lists actions for endpoint
        alyx_client.rest(endpoint, action): lists fields and URL

        Example REST endpoint with all actions:

        >>> client = AlyxClient()
        >>> client.rest('subjects', 'list')
        >>> client.rest('subjects', 'list', field_filter1='filterval')
        >>> client.rest('subjects', 'create', data=sub_dict)
        >>> client.rest('subjects', 'read', id='nickname')
        >>> client.rest('subjects', 'update', id='nickname', data=sub_dict)
        >>> client.rest('subjects', 'partial_update', id='nickname', data=sub_dict)
        >>> client.rest('subjects', 'delete', id='nickname')
        >>> client.rest('notes', 'create', data=nd, files={'image': open(image_file, 'rb')})

        Parameters
        ----------
        url : str
            Endpoint name
        action : str
            One of 'list', 'create', 'read', 'update', 'partial_update', 'delete'
        id : str
            Lookup string for actions 'read', 'update', 'partial_update', and 'delete'
        data : dict
            Data dictionary for actions 'update', 'partial_update' and 'create'
        files : dict, tuple
            Option file(s) to upload
        no_cache : bool
            If true the `list` and `read` actions are performed without returning the cache
        kwargs
            Filters as per the Alyx REST documentation
            cf. https://openalyx.internationalbrainlab.org/docs/

        Returns
        -------
        list, dict
            List of queried dicts ('list') or dict (other actions)
        """

        def get_values_key_chain(dictionary, keys=[]):
            """
            Recursively retrieves all values from a nested dictionary along with their key paths.

            Args:
                dictionary (dict): The dictionary to extract values and key paths from.
                keys (list): The list of keys that leads to the current value. Defaults to an empty list.

            Returns:
                list of tuples: Each tuple contains a value from the dictionary and the list of keys
                                that leads to that value.

            Example:
                Input: dictionary = {'a': 1, 'b': {'c': 2, 'd': {'e': 3}}}
                Output: [(1, ['a']), (2, ['b', 'c']), (3, ['b', 'd', 'e'])]
            """

            # Initialize a list to store value-key path pairs
            pairs = []

            # Iterate over key-value pairs in the current dictionary
            for key, value in dictionary.items():
                # If the value is a dictionary, recursively call the function to handle the nested dict
                if isinstance(value, dict):
                    pairs.extend(get_values_key_chain(value, keys + [key]))
                else:
                    # If the value is not a dictionary, append the value and its key path to the pairs list
                    pairs.append((value, keys + [key]))

            # Return the list of value-key path pairs
            return pairs

        # if endpoint is None, list available endpoints
        if not url:
            pprint(self.list_endpoints())
            return
        # remove beginning slash if any
        if url.startswith("/"):
            url = url[1:]
        # and split to the next slash or question mark
        endpoint = re.findall("^/*[^?/]*", url)[0].replace("/", "")
        # make sure the queried endpoint exists, if not throw an informative error
        self._check_inputs(endpoint)
        endpoint_scheme = self.rest_schemes.resolve_path(endpoint)
        # on a filter request, override the default action parameter
        if "?" in url:
            action = "list"
        # if action is None, list available actions for the required endpoint
        if not action:
            pprint(list(endpoint_scheme.keys()))
            return

        # make sure the the desired action exists, if not throw an informative error
        if action not in endpoint_scheme.operations.keys():
            raise ValueError(
                'Action "'
                + action
                + '" for REST endpoint "'
                + endpoint
                + '" does '
                + "not exist. Available actions are: "
                + "\n       "
                + "\n       ".join(endpoint_scheme.operations.keys())
            )
        # the actions below require an id in the URL, warn and help the user
        if action in ["read", "update", "partial_update", "delete"] and not id:
            _logger.warning(
                'REST action "' + action + '" requires an ID in the URL: ' + endpoint_scheme.operations[action]["url"]
            )
            return
        # the actions below require a data dictionary, warn and help the user with fields list
        if action in ["create", "update", "partial_update"] and not data:
            pprint(endpoint_scheme[action]["fields"])
            for act in endpoint_scheme[action]["fields"]:
                print("'" + act["name"] + "': ...,")
            _logger.warning('REST action "' + action + '" requires a data dict with above keys')
            return

        # clobber=True means remote request always made, expires=True means response is not cached
        cache_args = {
            "clobber": no_cache,
            "expires": kwargs.pop("expires", False) or no_cache,
        }
        if action == "list":
            # list doesn't require id nor
            assert endpoint_scheme[action]["action"] == "get"
            # add to url data if it is a string
            if id:
                # this is a special case of the list where we query a uuid. Usually read is better
                if "django" in kwargs.keys() and kwargs["django"] != "":
                    kwargs["django"] = kwargs["django"] + ","
                else:
                    kwargs["django"] = ""
                # kwargs["django"] = f"{kwargs['django']}pk,{id}"
                # we remove all other filters from kwargs, as selecting by id is already all or none
                if len(excedent_keys := [key for key in kwargs.keys() if key != "django" and key != "query_type"]):
                    _logger.warning(
                        f"Some fields, {excedent_keys} have been supplied by the user, but an id is present in the list"
                        " search. These fields have been discarded."
                    )
                    # if there is any other filter, we send a warning
                kwargs = {"django": f"{kwargs['django']}pk,{id}"}
            # otherwise, look for a dictionary of filter terms
            if kwargs:
                query_params = []
                for key, value in kwargs.items():
                    # if searched value is a dict, it must be a json compatible field, so we proceed like it.
                    # if not, the backend django server will throw an error anyways
                    if isinstance(value, dict):
                        values_keys = get_values_key_chain(value)
                        values = []
                        for json_value, json_chain_keys in values_keys:

                            # if there is a __lookup_str at the end of the last dict key :
                            lookup_key = "exact"
                            if "__" in json_chain_keys[-1]:
                                lookup_keys = json_chain_keys[-1].split("__")
                                if "not" in lookup_keys:
                                    lookup_keys.pop(lookup_keys.index("not"))

                                if len(lookup_keys) == 1:
                                    lookup_key = "exact"
                                elif len(lookup_keys) == 2:
                                    lookup_key = lookup_keys[1]
                                elif len(lookup_keys) == 0:
                                    raise ValueError(f"A json key cannot be __not. It was : {json_chain_keys[-1]}")
                                else:
                                    raise ValueError(
                                        "Found several lookup parameters, but only one lookup param + an optionnal "
                                        f"'__not' are allowed. The problematic syntax was : {json_chain_keys[-1]}"
                                    )
                                _logger.debug(f"Filtering on lookup json field : {lookup_key}")

                            # we use lookup_key just to know how to compose the query string, in the subsequent if check
                            # but we do not inject it in there, as it should already be present if we find it,
                            # and if not, "exact" will be used by the django backend by default

                            # if lookup key is contains or icontains, and we require several values (list or tuple)
                            # we must make a separate query filter string for each required value in json_value
                            if "contains" in lookup_key and isinstance(json_value, (list, tuple)):
                                for item in json_value:
                                    json_query = f"{'__'.join(json_chain_keys)},{item}"
                                    values.append(json_query)
                            else:
                                json_query = f"{'__'.join(json_chain_keys)},{json_value}"
                                values.append(json_query)

                        # multiple json queries are grouped with a ; They will be splitted in with the same char in the
                        # backend, so the dict keys and values must not contain ; or it will break
                        value = ";".join(values)

                    query_params.append((key, ",".join(map(str, ensure_list(value)))))

                # the ",".join(map(str system allows to convert all lists in query params to comma separated string
                # list if value contains multiple elements

                url = update_url_listparams(url, query_params)
            return self.get("/" + url, **cache_args)
        if not isinstance(id, str) and id is not None:
            id = str(id)  # e.g. may be uuid.UUID
        if action == "read":
            assert endpoint_scheme[action]["action"] == "get"
            return self.get("/" + endpoint + "/" + id.split("/")[-1], **cache_args)
        elif action == "create":
            assert endpoint_scheme[action]["action"] == "post"
            return self.post("/" + endpoint, data=data, files=files)
        elif action == "delete":
            assert endpoint_scheme[action]["action"] == "delete"
            return self.delete("/" + endpoint + "/" + id.split("/")[-1])
        elif action == "partial_update":
            assert endpoint_scheme[action]["action"] == "patch"
            return self.patch("/" + endpoint + "/" + id.split("/")[-1], data=data, files=files)
        elif action == "update":
            assert endpoint_scheme[action]["action"] == "put"
            return self.put("/" + endpoint + "/" + id.split("/")[-1], data=data, files=files)

    # JSON field interface convenience methods
    def _check_inputs(self, endpoint: str) -> None:
        # make sure the queried endpoint exists, if not throw an informative error
        if not self.endpoint_exists(endpoint):
            av = self.list_endpoints()
            raise ValueError(
                'REST endpoint "'
                + endpoint
                + '" does not exist. Available '
                + "endpoints are \n       "
                + "\n       ".join(av)
            )

    def json_field_write(
        self,
        endpoint: str = None,
        uuid: str = None,
        field_name: str = None,
        data: dict = None,
    ) -> dict:
        """
        Write data to JSON field.  WILL NOT CHECK IF DATA EXISTS
        NOTE: Destructive write!

        Parameters
        ----------
        endpoint : str, None
            Valid alyx endpoint, defaults to None
        uuid : str, uuid.UUID, None
            UUID or lookup name for endpoint
        field_name : str, None
            Valid json field name, defaults to None
        data : dict, None
            Data to write to json field, defaults to None

        Returns
        -------
        dict
            Written data dict
        """
        self._check_inputs(endpoint)
        # Prepare data to patch
        patch_dict = {field_name: data}
        # Upload new extended_qc to session
        ret = self.rest(endpoint, "partial_update", id=uuid, data=patch_dict)
        return ret[field_name]

    def json_field_update(
        self,
        endpoint: str = None,
        uuid: str = None,
        field_name: str = "json",
        data: dict = None,
    ) -> dict:
        """
        Non-destructive update of JSON field of endpoint for object
        Will update the field_name of the object with pk = uuid of given endpoint
        If data has keys with the same name of existing keys it will squash the old
        values (uses the dict.update() method).

        Parameters
        ----------
        endpoint : str
            Alyx REST endpoint to hit
        uuid : str, uuid.UUID
            UUID or lookup name of object
        field_name : str
            Name of the json field
        data : dict
            A dictionary with fields to be updated

        Returns
        -------
        dict
            New patched json field contents as dict

        Examples
        --------
        >>> client = AlyxClient()
        >>> client.json_field_update("sessions", "eid_str", "extended_qc", {"key": "value"})
        """
        self._check_inputs(endpoint)
        # Load current json field contents
        current = self.rest(endpoint, "read", id=uuid)[field_name]
        if current is None:
            current = {}

        if not isinstance(current, dict):
            _logger.warning(f"Current json field {field_name} does not contains a dict, aborting update")
            return current

        # Patch current dict with new data
        current.update(data)
        # Prepare data to patch
        patch_dict = {field_name: current}
        # Upload new extended_qc to session
        ret = self.rest(endpoint, "partial_update", id=uuid, data=patch_dict)
        return ret[field_name]

    def json_field_remove_key(
        self,
        endpoint: str = None,
        uuid: str = None,
        field_name: str = "json",
        key: str = None,
    ) -> Optional[dict]:
        """
        Remove inputted key from JSON field dict and re-upload it to Alyx.
        Needs endpoint, uuid and json field name

        Parameters
        ----------
        endpoint : str
            Endpoint to hit, defaults to None
        uuid : str
            UUID or lookup name for endpoint
        field_name : str
            JSON field name of object, defaults to None
        key : str
            Key name of dictionary inside object, defaults to None

        Returns
        -------
        dict
            New content of json field
        """
        self._check_inputs(endpoint)
        current = self.rest(endpoint, "read", id=uuid)[field_name]
        # If no contents, cannot remove key, return
        if current is None:
            return current
        # if contents are not dict, cannot remove key, return contents
        if isinstance(current, str):
            _logger.warning(f"Cannot remove key {key} content of json field is of type str")
            return None
        # If key not present in contents of json field cannot remove key, return contents
        if current.get(key, None) is None:
            _logger.warning(f"{key}: Key not found in endpoint {endpoint} field {field_name}")
            return current
        _logger.info(f"Removing key from dict: '{key}'")
        current.pop(key)
        # Re-write contents without removed key
        written = self.json_field_write(endpoint=endpoint, uuid=uuid, field_name=field_name, data=current)
        return written

    def json_field_delete(self, endpoint: str = None, uuid: str = None, field_name: str = None) -> None:
        self._check_inputs(endpoint)
        _ = self.rest(endpoint, "partial_update", id=uuid, data={field_name: None})
        return _[field_name]

    def clear_rest_cache(self):
        """Clear all REST response cache files for the base url"""
        for file in self.cache_dir.joinpath(".rest").glob("*"):
            file.unlink()


class ResponseManager:

    def __init__(self, client: Client):
        self.client = client

    def __getitem__(self, value):
        return getattr(self, value)

    def list(self, json_response: dict, original_url: str, **kwargs):

        if isinstance(json_response, dict) and list(json_response.keys()) == [
            "count",
            "next",
            "previous",
            "results",
        ]:
            if len(json_response["results"]) < json_response["count"]:
                cache_args = {k: v for k, v in kwargs.items() if k in ("clobber", "expires")}
                logger.debug(f"Creating a paginated response for a large Alyx request. Query : {original_url}")
                json_response = _PaginatedResponse(self, json_response, cache_args, callbacks=self.fix_url)
                logger.debug(
                    f"Paginated response total size is : {json_response.count}. "
                    f"Limit size is : {json_response.limit}. Query is : {json_response.query}"
                )
            else:
                json_response = json_response["results"]
        return self.fix_url(json_response)

    def fix_url(self, response_data, fixed_keys=["admin_url"], do_fix=False):

        if isinstance(response_data, list):
            return [self.fix_url(item, do_fix=False) for item in response_data]
        elif isinstance(response_data, dict):
            return {
                key: (self.fix_url(item, do_fix=True) if key in fixed_keys else self.fix_url(item, do_fix=False))
                for key, item in response_data.items()
            }
        elif isinstance(response_data, _PaginatedResponse):
            response_data.add_finishing_callback(self.fix_url)
            return response_data
        elif do_fix:
            baseurl = urlsplit(self.client.url)
            return urlsplit(response_data)._replace(scheme=baseurl.scheme, netloc=baseurl.netloc).geturl()
        else:
            return response_data


class _PaginatedResponse(Mapping):
    """
    This class allows to emulate a list from a paginated response.
    Provides cache functionality.

    Examples
    --------
    >>> r = _PaginatedResponse(client, response)
    """

    def __init__(self, alyx, rep, cache_args=None, callbacks=[]):
        """
        A paginated response cache object

        Parameters
        ----------
        alyx : AlyxClient
            An instance of an AlyxClient associated with the REST response
        rep : dict
            A paginated REST response JSON dictionary
        cache_args : dict
            A dict of kwargs to pass to _cache_response decorator upon subsequent requests
        """

        self.alyx = alyx
        self.base_url = self.alyx.base_url
        self.count = rep["count"]
        self.limit = len(rep["results"])
        self._cache_args = cache_args or {}
        # store URL without pagination query params
        self.query = rep["next"]
        # init the cache, list with None with count size
        self._cache = [None] * self.count

        if not isinstance(callbacks, list):
            callbacks = [callbacks]

        self.finishing_callbacks = callbacks

        # fill the cache with results of the query
        self.store_results(rep, 0)

    def __len__(self):
        return self.count

    def __getitem__(self, item):
        """Returns an item from cache if the indices locations are already loaded,
        or populates the cache chunk by chunk if some locations are empty.
        """
        if isinstance(item, slice):
            while None in self._cache[item]:
                # .index(None) finds the first location where the cache is none,
                # and populate stores a new chunk starting from here
                self.populate(self._cache[item].index(None))
        elif item > self.count:
            raise IndexError(
                f"The paginated response has no item at position {item} : " f"it contains only {self.count} items"
            )
        elif self._cache[item] is None:
            self.populate(item)
        return self._cache[item]

    def populate(self, idx):
        """Populate a chunk of size self.limit, from an offset query depending on the value of the index required."""
        offset = self.limit * math.floor(idx / self.limit)
        query = update_url_params(self.query, {"limit": self.limit, "offset": offset})
        res = self.alyx._generic_request(requests.get, query, **self._cache_args)
        if self.count != res["count"]:
            logger.warning(
                f"remote results for {urlsplit(query).path} endpoint changed; results may be inconsistent",
                RuntimeWarning,
            )
        self.store_results(res, offset)

    def store_results(self, res, offset):
        for i, r in enumerate(res["results"][: self.count - offset]):
            self._cache[i + offset] = self.finish_result(r)

    def finish_result(self, result):
        """Method that should be overridden in child classes"""
        for callback in self.finishing_callbacks:
            result = callback(result)
        return result

    def add_finishing_callback(self, callback):
        if callback not in self.finishing_callbacks:
            self.finishing_callbacks.append(callback)

    def __iter__(self):
        for i in range(self.count):
            try:
                yield self.__getitem__(i)
            except requests.HTTPError as e:
                logger.error(
                    f"{e.response.status_code} error while trying to access " f"PaginatedResponse data at position {i}"
                )
                raise e
