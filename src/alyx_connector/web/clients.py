import requests, json
from requests.models import Response
from requests import HTTPError
from datetime import timedelta
from logging import getLogger
from abc import ABC, abstractmethod
from rich.prompt import Prompt
from sys import stdout
from tqdm import tqdm
from pandas import DataFrame, Series

from ..utils.configuration import Configuration
from .urls import UrlValidator
from .api import EndpointUrl, Endpoint, APISpecification, OpenAPISpecification, Request, Operateur

from typing import Optional, Type, TypeVar, Generic, Literal, List, Dict, Any, cast, overload

Specification = TypeVar("Specification", bound=APISpecification)

logger = getLogger("alyx_connector.web.client")


class Client(ABC, Generic[Specification]):
    """
    Client class for managing connections.

    Attributes:
        protocol (str): Protocol used by the client, e.g., 'http' or 'https'.
        host (str): Host address, e.g., '127.0.0.1'.
        port (str): Port number, e.g., '80'.
        schema_endpoint (str): Endpoint for the API schema.
    """

    protocol: str
    host: str
    port: str

    silent = False

    schema_endpoint = "/api/schema"

    specification_class: Type[Specification]

    @property
    def netloc(self):
        "example : 127.0.0.1:80"
        return f"{self.host}:{self.port}"

    @property
    def url(self):
        "example : http://127.0.0.1:80"
        return UrlValidator.build_url(self.protocol, self.host, self.port)

    @url.setter
    def url(self, url: str):
        self.protocol, self.host, self.port, validated_url = UrlValidator.validate_url_components(url)

    @property
    @abstractmethod
    def username(self):
        pass

    base_url = url

    @property
    def headers(self) -> dict:
        if not hasattr(self, "_headers"):
            self._headers = self.get_initial_headers()
        return self._headers

    @property
    def schema(self) -> Specification:
        if not hasattr(self, "_schema"):
            self._schema = self.specification_class.from_url(self.url + self.schema_endpoint)
        return self._schema

    def get_initial_headers(self):
        return {**{}, "Accept": "application/json"}

    def list_endpoints(self):
        return sorted(self.schema.paths_dict.keys())

    def path(self, path: str) -> EndpointUrl:
        return EndpointUrl(path, self)

    def endpoint(self, path: str) -> Endpoint:
        return self.path(path).endpoint

    def endpoint_exists(self, path: str) -> bool:
        return self.endpoint(path).exists()

    @overload
    def rest(
        self,
        endpoint_name: str,
        action: Literal["list", "retrieve", "update", "create", "destroy", "partial_update"],
        timeout: int = 1000,
        handles_internally: Literal[True] = True,
        **kwargs,
    ) -> Series | DataFrame | None: ...

    @overload
    def rest(
        self,
        endpoint_name: str,
        action: Literal["list", "retrieve", "update", "create", "destroy", "partial_update"],
        timeout: int = 1000,
        handles_internally: Literal[False] = False,
        **kwargs,
    ) -> Request: ...

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
        request = Request(self, operateur, timeout=timeout, **kwargs)
        return request.handle().output_data.table if handles_internally else request

        # return endpoint.actions[action].request(**kwargs)

    # def search(self, endpoint_name: str, **kwargs):
    #     if kwargs.get("id"):
    #         return self.rest(endpoint_name, "retrieve", **kwargs)
    #     return self.rest(endpoint_name, "list", **kwargs)

    def search(self, *, endpoint: str, details=True, **kwargs):
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

        # no argument for the given action is present, we assume the user wanted to list instead.

        if self.endpoint(endpoint).implements_retrieve and self.endpoint(endpoint).action(
            "retrieve"
        ).verify_required_args_present(**kwargs):
            # if the required arguments for retrieve are present, we do the retrieve here.
            return self.retrieve(endpoint=endpoint, details=details, **kwargs)

        # else, we assume the user wanted to list instead, after here
        return self.list(endpoint=endpoint, details=details, **kwargs)

    def list(self, *, endpoint: str, details=True, **kwargs) -> DataFrame | Series | None:
        results = self.rest(endpoint, "list", details=details, **kwargs)
        return results

    def create(self, *, endpoint: str, data: dict, **kwargs) -> DataFrame | Series | None:
        result = self.rest(endpoint, "create", data=data, **kwargs)
        return result

    def retrieve(self, *, endpoint: str, **kwargs) -> Series | None:
        result = self.rest(endpoint, "retrieve", **kwargs)
        return result

    def update(self, *, endpoint: str, data: dict, **kwargs) -> DataFrame | Series | None:
        result = self.rest(endpoint, "update", data=data, **kwargs)
        return result

    def destroy(self, *, endpoint: str, **kwargs) -> DataFrame | Series | None:
        result = self.rest(endpoint, "destroy", **kwargs)
        return result

    def partial_update(self, *, endpoint: str, data: dict, **kwargs) -> DataFrame | Series | None:
        result = self.rest(endpoint, "partial_update", data=data, **kwargs)
        return result

    def describe(self, endpoint_name: str):
        # TODO make a proper description tool
        endpoint = self.path(endpoint_name).endpoint.assert_exists()
        return endpoint

    def post_request_callback(self, request: "Request"):
        pass

    def pre_request_callback(self, request: "Request"):
        pass

    def is_up(self) -> bool:
        try:
            return bool(self.rest("server-info", "retrieve"))
        except ConnectionError:
            return False
        except HTTPError as error:
            if error.response.status_code in ["502", "504"]:
                return False
            raise NotImplementedError(f"Error catching code : {error.response.status_code} with {error.response}")


class ClientWithAuth(Client):

    token: str
    username: str

    def ensure_authenticated(self):
        if "Authorization" not in self.headers:
            raise ConnectionError("Please authenticate your connection with .authenticate()")

    def authenticate(self, password: Optional[str] = None, cache_token=True, force=False) -> str | None:
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

        # Check if token cached
        if not force and self.token:
            self._headers = {
                "Authorization": f"Token {self.token}",
                "Accept": "application/json",
            }
            return self.token

        # Else, if force or token does not exists : use or get password
        if password is None:
            if self.silent:
                raise ValueError("Cannot ask for password if the client is in silent mode")
            password = self.ask_password()

        try:
            rep = requests.post(self.url + "/auth-token", data={"username": self.username, "password": password})
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Can't connect to {self.url}.\n" + "Check your internet connections and Alyx database firewall"
            )
        # Assign token or raise exception on auth error
        if rep.ok:
            token = rep.json().get("token")
        else:
            if rep.status_code == 400:  # Auth error; re-raise with details
                redacted = "*" * len(password) if password else None
                message = (
                    "Alyx authentication failed with credentials: " f"user = {self.username}, password = {redacted}"
                )
                raise requests.HTTPError(rep.status_code, rep.url, message, response=rep)
            else:
                rep.raise_for_status()
            raise RuntimeError("Unidentified error while trying to authenticate")

        self._headers = {
            "Authorization": f"Token {token}",
            "Accept": "application/json",
        }

        if cache_token:
            self.token = token

        logger.warning(f"Connected to {self.url} as {self.username}")

        return token

    login = authenticate

    def logout(self, *args, **kwargs):
        self._headers = self.get_initial_headers()
        self.token = ""

    def is_logged_in(self) -> bool:
        return True if self.token else False

    def ask_password(self):
        return Prompt.ask("Enter password :", password=True)


class ClientWithConfig(ClientWithAuth):

    default_expiry = timedelta(days=1)
    cache_mode = "GET"
    _token: str = ""

    def __init__(
        self,
        url: Optional[str] = None,
        username: Optional[str] = None,
        *,
        auto_authenticate=True,
        make_default=False,
    ):
        self.select_user(url=url, username=username, auto_authenticate=auto_authenticate, make_default=make_default)

    @property
    def username(self):
        return self.config.username if self.config.is_user_selected() else self.raise_config_not_setup()

    @property
    def silent(self):
        return self.config.silent if self.config.is_user_selected() else self.raise_config_not_setup()

    @property
    def token(self):
        if not self.config.is_user_selected():
            self.raise_config_not_setup()
        return self.config.token

    @token.setter
    def token(self, value):
        if not self.config.is_user_selected():
            self.raise_config_not_setup()
        self.config.token = value

    def ask_password(self):
        return self.config.ask_password()

    @property
    def config(self) -> Configuration:
        if not hasattr(self, "_config"):
            self._conf = Configuration()
            return self._conf
        return self._conf

    @config.setter
    def config(self, conf: Configuration):
        self._conf = conf

    def raise_config_not_setup(self):
        raise AttributeError("Config has not been setup")

    @staticmethod
    def setup_user(
        url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        *,
        make_default: Optional[bool] = None,
        silent: Optional[bool] = False,
        force_prompt=True,
        **user_options,
    ):
        url = UrlValidator.validate_url(url) if url is not None else url
        config = Configuration()
        config.select_current_user_config(
            server_address=url, username=username, silent=silent, force_prompt=force_prompt, make_default=make_default
        )
        client = ClientWithConfig.from_config(config, auto_authenticate=False)
        client.authenticate(password=password)
        client.config.current_user_config.set_options(**user_options)
        return client

    @staticmethod
    def from_config(config: Configuration, *, auto_authenticate=True):
        client = ClientWithConfig(url=config.url, username=config.username, auto_authenticate=auto_authenticate)
        return client

    def select_user(
        self, url: Optional[str] = None, username: Optional[str] = None, *, auto_authenticate=True, make_default=False
    ):
        if url is None:
            if self.config and self.config.is_user_selected():
                url = self.config.url
        else:
            url = UrlValidator.validate_url(url)
        self.config.select_current_user_config(server_address=url, username=username, make_default=make_default)
        self.url = self.config.url
        if auto_authenticate:
            # authenticate using config. If password is not set, it is prefereable to set it with setup_user
            self.authenticate()

    def clear_rest_cache(self):
        """Clear all REST response cache files for the base url"""
        if not self.config.is_user_selected():
            self.raise_config_not_setup()

        for file in self.config.rest_cache_location.glob("*"):
            file.unlink()

    delete_cache = clear_rest_cache

    def pre_request_callback(self, request: "Request"):
        self.ensure_authenticated()

    def post_request_callback(self, request: "Request"):
        if request.response and request.response.status_code == 403 and '"Invalid token."' in request.response.text:
            self.authenticate(cache_token=True, force=True)
            return "retry"
        return None


class WebClient(ClientWithConfig):
    specification_class = OpenAPISpecification
