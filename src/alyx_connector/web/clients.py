# import requests
from requests import HTTPError

# from datetime import timedelta
from logging import getLogger

# from abc import ABC, abstractmethod
# from rich.prompt import Prompt
from pandas import DataFrame, Series

from ..configuration import Configuration
from .urls import UrlValidator
from .api import EndpointUrl, Endpoint, Request
from .schema import OpenAPISpecification
from .authentification import Authenticator

from typing import (
    Optional,
    Literal,
    List,
)  # Type, TypeVar, Generic, overload, TYPE_CHECKING

# if TYPE_CHECKING:
#     from ..connector.core import Connector

# Specification = TypeVar("Specification", bound=APISpecification)

logger = getLogger("alyx_connector.web.client")


class WebClient:
    """
    Client class for managing connections.

    Attributes:
        protocol (str): Protocol used by the client, e.g., 'http' or 'https'.
        host (str): Host address, e.g., '127.0.0.1'.
        port (str): Port number, e.g., '80'.
        SCHEMA_ENDPOINT (str): Endpoint for the API schema.
    """

    SCHEMA_ENDPOINT = "/api/schema"

    def __init__(
        self,
        url: Optional[str] = None,
        username: Optional[str] = None,
        *,
        auto_authenticate: bool = True,
        password: Optional[str] = None,
        make_default: Optional[bool] = False,
        silent: Optional[bool] = False,
        force_prompt: bool = False,
        use_default=True,
        config: Optional[Configuration] = None,
        **user_options,
    ):

        if config is not None:
            self.config = config

        if use_default:
            self.select_user(
                url=url,
                username=username,
                auto_authenticate=auto_authenticate,
                password=password,
                make_default=make_default,
                silent=silent,
                force_prompt=force_prompt,
                **user_options,
            )

    @property
    def authenticator(self) -> Authenticator:
        if not hasattr(self, "_authenticator"):
            self._authenticator = Authenticator(self.config)
        return self._authenticator

    @property
    def config(self) -> Configuration:
        if not hasattr(self, "_config"):
            self._conf = Configuration()
        return self._conf

    @config.setter
    def config(self, conf: Configuration):
        # ensures the authenticator variable has always the
        # right config object reference
        self._conf = conf
        self._authenticator = Authenticator(conf)

    def _manage_config_selection_change(self):
        self.config.raise_if_no_user_selected()
        if not hasattr(self, "_config_selection_hash"):
            self._config_selection_hash = hash(self.config.selected_user)
        elif self._config_selection_hash == hash(self.config.selected_user):
            return  # we do not update values
        # if we arrive here, it means the selected_user changed, we need to update
        # url, host, protocol, and netloc properties
        protocol, host, port, url = UrlValidator.validate_url_components(self.config.url)
        self.protocol = protocol
        self.host = host
        self.port = port
        self.url = url

    @property
    def url(self) -> str:
        """The url of the remote server currntely selected.
        It includes the port, but not the relative path to the api root.
        Example : https://127.0.0.1:80"""
        self._manage_config_selection_change()
        return self._url

    @url.setter
    def url(self, url: str):
        self._url = url

    @property
    def port(self) -> str:

        self._manage_config_selection_change()
        return self._port

    @port.setter
    def port(self, port: str):
        self._port = port

    @property
    def host(self) -> str:
        self._manage_config_selection_change()
        return self._host

    @host.setter
    def host(self, host: str):
        self._host = host

    @property
    def protocol(self) -> str:
        self._manage_config_selection_change()
        return self._protocol

    @protocol.setter
    def protocol(self, protocol: str):
        self._protocol = protocol

    @property
    def netloc(self) -> str:
        "example : 127.0.0.1:80"
        return f"{self.host}:{self.port}"

    @property
    def username(self) -> str:
        return self.config.username

    @property
    def token(self) -> str | None:
        return self.authenticator.token

    @property
    def headers(self) -> dict[str, str]:
        if not hasattr(self, "_supplementay_headers"):
            self._supplementay_headers = {"Accept": "application/json"}

        if self.config.is_user_selected() and self.authenticator.is_authenticated():
            authorization_header = {"Authorization": f"Token {self.token}"}
        else:
            authorization_header = {}
        authorization_header.update(self._supplementay_headers)

        return authorization_header

    @headers.setter
    def headers(self, headers: dict[str, str]):
        self._supplementay_headers = {}

    @property
    def schema(self) -> OpenAPISpecification:
        if not hasattr(self, "_schema"):
            self._schema = OpenAPISpecification.from_url(self.url + self.SCHEMA_ENDPOINT)
        return self._schema

    def get_initial_headers(self):
        return {**{}, "Accept": "application/json"}

    def list_endpoints(self) -> List[str]:
        return sorted(self.schema.paths_dict.keys())

    def path(self, path: str) -> EndpointUrl:
        return EndpointUrl(path, self)  # ty:ignore[invalid-argument-type]

    def endpoint(self, path: str) -> Endpoint:
        return self.path(path).endpoint

    def endpoint_exists(self, path: str) -> bool:
        return self.endpoint(path).exists()

    def rest(
        self,
        endpoint_name: str,
        action: Literal["list", "retrieve", "update", "create", "destroy", "partial_update"],
        timeout: int = 1000,
        handles_internally: Literal[True, False] = True,
        **kwargs,
    ) -> Series | DataFrame | None | Request:
        operateur = self.path(endpoint_name).endpoint.assert_exists().actions[action]
        operateur.verify_required_args_present(**kwargs, raises=True)
        request = Request(self, operateur, timeout=timeout, **kwargs)  # ty:ignore[invalid-argument-type]
        return request.handle().output_data.table if handles_internally else request

    def search(
        self,
        endpoint: str = "sessions",
        *,
        details: bool = True,
        raises: bool = True,
        name: Optional[str] = None,
        id: Optional[str] = None,
        handles_internally: bool = True,
        **kwargs,
    ) -> Series | DataFrame | Request | None:
        """Search for items in a specified endpoint.

        This method allows you to search for items in a given endpoint, with the option to retrieve details for a
        specific item if an ID is provided and the endpoint supports retrieval.
        If the endpoint does not support retrieval, a warning is logged when an ID is specified.

        Args:
            endpoint (str, optional): The endpoint to search in. Defaults to "sessions".
            id (Optional[str], optional): The ID of the item to retrieve. If provided, the method will attempt to
                retrieve the specific item if supported by the endpoint. Defaults to None.
            details (bool, optional): Whether to include detailed aggregation in the results. Defaults to True.
            **kwargs: Additional keyword arguments to pass to the search request.

        Returns:
            pandas.DataFrame or pandas.Series : A DataFrame or Series containing the search results.

        Raises:
            ValueError: If the search result is empty and details are requested.
        """

        if name is not None:
            kwargs["name"] = name
        if id is not None:
            kwargs["id"] = id
        if handles_internally is not None:
            kwargs["handles_internally"] = handles_internally

        # if no argument for the retrieve action is present (name or id), we assume the user wanted to list instead.
        if (self.endpoint(endpoint).implements_retrieve) and (
            self.endpoint(endpoint).action("retrieve").verify_required_args_present(**kwargs)
        ):
            # if the required arguments for retrieve are present, we do the retrieve here.
            search_result = self.retrieve(endpoint=endpoint, details=details, **kwargs)
            if raises:
                self.raise_if_search_empty(search_result)
            return search_result

        # else, we assume the user wanted to list instead
        search_result = self.list(endpoint=endpoint, details=details, **kwargs)
        if raises:
            self.raise_if_search_empty(search_result)
        return search_result

    def raise_if_search_empty(self, search_result: DataFrame | Series | None):
        if not isinstance(search_result, Request) and (
            search_result is None or not len(search_result)
        ):
            raise ValueError("This search provided no result")
        return search_result

    def list(self, endpoint: str, *, details=True, **kwargs) -> DataFrame | None:
        results = self.rest(endpoint, "list", details=details, **kwargs)
        return results  # ty:ignore[invalid-return-type]

    def create(self, endpoint: str, *, data: dict, **kwargs) -> DataFrame | Series | None:
        result = self.rest(endpoint, "create", data=data, **kwargs)
        return result  # ty:ignore[invalid-return-type]

    def retrieve(self, endpoint: str, **kwargs) -> Series | None:
        result = self.rest(endpoint, "retrieve", **kwargs)
        return result  # ty:ignore[invalid-return-type]

    def update(self, endpoint: str, *, data: dict, **kwargs) -> DataFrame | Series | None:
        result = self.rest(endpoint, "update", data=data, **kwargs)
        return result  # ty:ignore[invalid-return-type]

    def destroy(self, endpoint: str, **kwargs) -> DataFrame | Series | None:
        result = self.rest(endpoint, "destroy", **kwargs)
        return result  # ty:ignore[invalid-return-type]

    def partial_update(self, *, endpoint: str, data: dict, **kwargs) -> DataFrame | Series | None:
        result = self.rest(endpoint, "partial_update", data=data, **kwargs)
        return result  # ty:ignore[invalid-return-type]

    def describe(self, endpoint_name: str):
        # TODO make a proper description tool
        endpoint = self.path(endpoint_name).endpoint.assert_exists()
        return endpoint

    def pre_request_callback(self, request: "Request") -> None:
        self.authenticator.raise_if_not_authenticated()

    def post_request_callback(self, request: "Request") -> Literal["retry"] | None:
        if (
            request.response
            and request.response.status_code == 403
            and '"Invalid token."' in request.response.text
        ):
            self.authenticator.authenticate(cache_token=True, force=True)
            return "retry"
        return None

    def clear_rest_cache(self):
        """Clear all REST response cache files for the base url"""
        self.config.raise_if_no_user_selected()

        for file in self.config.rest_cache_location.glob("*"):
            file.unlink()

    def server_is_reachable(self) -> bool:
        try:
            return bool(self.rest("server-info", "retrieve"))
        except ConnectionError:
            return False
        except HTTPError as error:
            if error.response.status_code in ["502", "504"]:
                return False
            raise NotImplementedError(
                f"Error catching code : {error.response.status_code} with {error.response}"
            )

    def select_user(
        self,
        url: Optional[str] = None,
        username: Optional[str] = None,
        *,
        auto_authenticate: bool = True,
        password: Optional[str] = None,
        make_default: Optional[bool] = False,
        silent: Optional[bool] = False,
        force_prompt: bool = False,
        **user_options,
    ) -> "WebClient":
        # if url is None:
        #     if self.config and self.config.is_user_selected():
        #         url = self.config.url
        # else we leave url as None, for a prompt to enter it to triger in
        # select_user
        # else:
        #     url = UrlValidator.validate_url(url)
        self.config.select_user(
            server_address=url,
            username=username,
            make_default=make_default,
            silent=silent,
            force_prompt=force_prompt,
        )
        self.url = self.config.url
        # self.config.selected_user.set_options(**user_options)
        if auto_authenticate or password:
            # authenticate using config. If password is not set, it is prefereable to set it with setup_user
            self.authenticator.authenticate(password=password, force=True if password else False)
        self.config_hash = hash(self.config.selected_user)
        return self


# class ClientWithAuth(Client):

#     token: str
#     username: str

#     def raise_if_not_authenticated(self):
#         if "Authorization" not in self.headers:
#             raise ConnectionError("Please authenticate your connection with .authenticate()")

#     def authenticate(self, password: Optional[str] = None, cache_token=True, force=False) -> str | None:
#         """
#         Gets a security token from the Alyx REST API to create requests headers.
#         Credentials are loaded via one.params

#         Parameters
#         ----------
#         username : str
#             Alyx username.  If None, token not cached and not silent, user is prompted.
#         password : str
#             Alyx password.  If None, token not cached and not silent, user is prompted.
#         cache_token : bool
#             If true, the token is cached for subsequent auto-logins
#         force : bool
#             If true, any cached token is ignored
#         """

#         # Check if token cached
#         if not force and self.token:
#             self._headers = {
#                 "Authorization": f"Token {self.token}",
#                 "Accept": "application/json",
#             }
#             return self.token

#         # Else, if force or token does not exists : use or get password
#         if password is None:
#             if self.silent:
#                 raise ValueError("Cannot ask for password if the client is in silent mode")
#             password = self.ask_password()

#         try:
#             rep = requests.post(self.url + "/api/auth-token", data={"username": self.username, "password": password})
#         except requests.exceptions.ConnectionError:
#             raise ConnectionError(
#                 f"Can't connect to {self.url}.\n" + "Check your internet connections and Alyx database firewall"
#             )
#         # Assign token or raise exception on auth error
#         if rep.ok:
#             token = rep.json().get("token")
#         else:
#             if rep.status_code == 400:  # Auth error; re-raise with details
#                 redacted = "*" * len(password) if password else None
#                 message = (
#                     "Alyx authentication failed with credentials: " f"user = {self.username}, password = {redacted}"
#                 )
#                 raise requests.HTTPError(rep.status_code, rep.url, message, response=rep)
#             else:
#                 rep.raise_for_status()
#             raise RuntimeError("Unidentified error while trying to authenticate")

#         self._headers = {
#             "Authorization": f"Token {token}",
#             "Accept": "application/json",
#         }

#         if cache_token:
#             self.token = token

#         logger.warning(f"Connected to {self.url} as {self.username}")

#         return token

#     login = authenticate

#     def logout(self, *args, **kwargs):
#         self._headers = self.get_initial_headers()
#         self.token = ""

#     def is_authenticated(self) -> bool:
#         return True if self.token else False

#     def ask_password(self):
#         return Prompt.ask("Enter password :", password=True)


# class ClientWithConfig(ClientWithAuth):
#     default_expiry = timedelta(days=1)
#     cache_mode = "GET"
#     _token: str = ""

#     def __init__(
#         self,
#         url: Optional[str] = None,
#         username: Optional[str] = None,
#         *,
#         auto_authenticate=True,
#         make_default=False,
#         use_default=True,
#         config: Optional[Configuration] = None,
#     ):
#         if config is None:
#             config = Configuration()

#         if use_default:
#             self.select_user(
#                 url=url,
#                 username=username,
#                 auto_authenticate=auto_authenticate,
#                 make_default=make_default,
#             )

#     @property
#     def username(self) -> str:
#         return (
#             self.config.username
#             if self.config.is_user_selected()
#             else self.config.raise_no_user_selected()
#         )

#     @property
#     def silent(self) -> bool | None:
#         return (
#             self.config.silent
#             if self.config.is_user_selected()
#             else self.config.raise_no_user_selected()
#         )

#     @property
#     def token(self) -> str | None:
#         return (
#             self.config.token
#             if self.config.is_user_selected()
#             else self.config.raise_no_user_selected()
#         )

#     @token.setter
#     def token(self, value):
#         if not self.config.is_user_selected():
#             self.raise_config_not_setup()
#         self.config.token = value

#     def ask_password(self):
#         return self.config.ask_password()

#     def raise_config_not_setup(self):
#         raise AttributeError("Config has not been setup")

#     def select_user(
#         self,
#         url: Optional[str] = None,
#         username: Optional[str] = None,
#         *,
#         auto_authenticate: bool = True,
#         password: Optional[str] = None,
#         make_default: Optional[bool] = False,
#         silent: Optional[bool] = False,
#         force_prompt: bool = False,
#         **user_options,
#     ) -> "ClientWithConfig":
#         if url is None:
#             if self.config and self.config.is_user_selected():
#                 url = self.config.url
#             # else we leave url as None, for a prompt to enter it to triger in
#             # select_user
#         else:
#             url = UrlValidator.validate_url(url)
#         self.config.select_user(
#             server_address=url,
#             username=username,
#             make_default=make_default,
#             silent=silent,
#             force_prompt=force_prompt,
#         )
#         self.url = self.config.url
#         self.config.selected_user.set_options(**user_options)
#         if auto_authenticate or password:
#             # authenticate using config. If password is not set, it is prefereable to set it with setup_user
#             self.authenticate(password=password, force=True if password else False)
#         return self

#     def clear_rest_cache(self):
#         """Clear all REST response cache files for the base url"""
#         if not self.config.is_user_selected():
#             self.raise_config_not_setup()

#         for file in self.config.rest_cache_location.glob("*"):
#             file.unlink()

#     delete_cache = clear_rest_cache


# class WebClient(ClientWithConfig):
#     specification_class = OpenAPISpecification
