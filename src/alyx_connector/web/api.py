from openapi_parser.specification import Operation, Specification, Path as OpenAPIPath
from openapi_parser import parse as parse_openapi_schema
from openapi_parser.errors import ParserError
import requests
from requests.models import Response
import json
import re
from logging import getLogger
from abc import ABC

from ..logging import filter_message
from .urls import UrlValidator

from typing import Any, Dict, Tuple, List, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .clients import ClientWithAuth

logger = getLogger("alyx_connector.client")


class APISpecification(ABC):

    @staticmethod
    def from_url(url):
        raise NotImplementedError

    @property
    def paths_dict(self) -> Dict:
        return {}


class OpenAPISpecification(APISpecification, Specification):

    @property
    def paths_dict(self) -> Dict[str, OpenAPIPath]:
        return {path.url: path for path in self.paths}

    @staticmethod
    def from_url(url) -> "OpenAPISpecification":
        logger = getLogger()
        try:
            with filter_message(logger, "Implicit type assignment: schema does not contain 'type' property"):
                specification = parse_openapi_schema(url)
            specification.paths_dict = {path.url: path for path in specification.paths}  # type: ignore
            return specification  # type: ignore
        except ParserError:
            raise ConnectionError(
                f"Can't connect to {url}.\n" + "Check your internet connections and Alyx database firewall"
            )


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


class EndpointUrl(str):

    client: "Client"
    requirements: List[str]

    def __new__(cls, path: str, client: "Client"):
        path = cls.normalize_path(path)
        obj = super(EndpointUrl, cls).__new__(cls, path)
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
        url = UrlValidator.urlunparse(
            protocol=self.client.protocol,  # http or https
            netloc=self.client.netloc,  # netloc = host+port
            path=self.finalized_path(**requirements_dict),  # path
            query_string=self.make_query_string(query_dict),  # querystring example : ?thing=truc
            fragment=fragment,  # basically an anchor, fragment example : #title1
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


class Endpoint:

    def __init__(self, path: EndpointUrl, client: "Client"):

        self.path = path
        self.client = client

    def exists(self):
        return True if self.routes else False

    @property
    def routes(self):
        search_path = self.path.replace("/", r"\/")
        pattern = re.compile(rf"^{search_path}(?:(?=\/{{).*)?$")
        return [EndpointUrl(key, self.client) for key in self.client.schema.paths_dict.keys() if pattern.match(key)]

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

    def __init__(self, path: EndpointUrl, client: "ClientWithAuth", operation: Operation):
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
            self.client.authenticate(cache_token=True, force=True)
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
