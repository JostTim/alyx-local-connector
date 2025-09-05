from openapi_parser.specification import Operation, Specification, Path as OpenAPIPath
from openapi_parser import parse as parse_openapi_schema
from openapi_parser.errors import ParserError
import json, re, requests
from requests.models import Response
from urllib.parse import urlencode, quote
from logging import getLogger
from abc import ABC

from warnings import warn

from ..utils.logging import filter_message
from .urls import UrlValidator

from typing import Any, Dict, Tuple, List, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .clients import ClientWithAuth, Client

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
        logger.propagate = True
        try:
            with filter_message(logger, "Implicit type assignment: schema does not contain 'type' property"):
                specification = parse_openapi_schema(url)
            specification.paths_dict = {path.url: path for path in specification.paths}  # type: ignore
            return specification  # type: ignore
        except ParserError as e:
            raise ConnectionError(
                f"Can't connect to {url}.\n" + f"Check your internet connections and Alyx database firewall. Error {e}"
            )


class RequestFunction(Protocol):
    def __call__(
        self,
        url: str,
        *,
        timeout: Optional[int] = None,
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
            query_string=urlencode(query_dict),  # querystring example : ?thing=truc
            fragment=fragment,  # basically an anchor, fragment example : #title1
        )
        return url

    def finalized_path(self, **requirements_dict):
        path = str(self)
        for requirement in self.requirements:
            if (requirement_value := requirements_dict.get(requirement)) is None:
                raise ValueError(f"You must provide a {requirement} keyword argument with the path {self}")
            path = path.replace(f"{{{requirement}}}", quote(str(requirement_value), safe=""))
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

    # def make_query_string(self, query_dict: dict) -> str:
    #     query_list = []
    #     for key, value in query_dict.items():
    #         query_list.append(f"{key}={value}")
    #     return "&".join(query_list)

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
    def actions(self) -> Dict[str, "Operateur"]:

        actions_dict: dict[str, list] = {}
        for route in self.routes:
            for operation in self.client.schema.paths_dict[route].operations:
                operateur = Operateur(route, self.client, operation)

                operations_list: list = actions_dict.setdefault(operateur.action_name, list())
                operations_list.append(operateur)

        return {
            operation_name: operations_list[0] if len(operations_list) == 1 else MultiOperateur(operations_list)
            for operation_name, operations_list in actions_dict.items()
        }

        return {
            "_".join(operation.operation_id.split("_")[1:]): Operateur(route, self.client, operation)
            for route in self.routes
            for operation in self.client.schema.paths_dict[route].operations
            if operation.operation_id is not None
        }

    @property
    def implements_retrieve(self):
        if "retrieve" in self.actions.keys():
            return True
        return False

    def action(self, action_name: str):
        return self.actions[action_name]

    def assert_exists(self):
        if not self.exists():
            self.raise_not_existing()
        return self

    def raise_not_existing(self):
        raise ValueError(f"Endpoint {self.path} do not exist in the schema")


class Operateur:

    def __init__(self, path: EndpointUrl, client: "Client", operation: Operation):
        self.path = path
        self.client = client
        self.operation = operation

    @property
    def action_name(self):
        operation_id = self.operation.operation_id
        if operation_id is None:
            return ""
        # remove first word with [1:], because it is the endpoint name (redundant)
        operation_id_words: list[str] = operation_id.split("_")[1:]
        if len(operation_id_words) >= 3 and operation_id_words[-2] == "by":
            # if by : several routes for same action
            operation_id_words = operation_id_words[:-2]
        elif len(operation_id_words) >= 2 and operation_id_words[-1].isnumeric():
            # if operation is having a numerical suffix, because of collision
            operation_id_words = operation_id_words[:-1]

        operation_name = "_".join(operation_id_words)
        return operation_name

    @property
    def rest_operation_name(self):
        return self.operation.method.value

    @property
    def request_method(self) -> RequestFunction:
        return getattr(requests, self.rest_operation_name)

    def make_url(self, **kwargs):
        try:
            return self.path.make_url(**kwargs)
        except ValueError as e:
            raise ValueError(f"For the {self.action_name} action, " + str(e)) from e

    @property
    def parameters(self):
        return {param.name: param for param in self.operation.parameters}

    @property
    def required_parameters(self):
        return {param.name: param for param in self.operation.parameters if param.required}

    def __repr__(self):
        return f"{self.path} - {self.operation}"

    def describe(self):
        return self.client.schema.paths_dict[self.path]

    def verify_required_args_present(self, raises=False, **kwargs):

        required_params_present = {
            param_name: bool(kwargs.get(param_name, None)) for param_name in self.required_parameters.keys()
        }

        if not any(required_params_present.values()):
            # no single required argument is present, we return False if raise is False, else we raise
            if not raises:
                return False
            missing_params = ", ".join([param_name for param_name in required_params_present.keys()])
            raise ValueError(
                f"Required arguments {missing_params} are required for {self.action_name} "
                f"on {self.path} and are missing"
            )
        elif not all(required_params_present.values()):
            # some required arguments for the action are present, but not all the required ones,
            # so we raise to inform the user that the action request cannot be done and that she/he should correct
            missing_params = ", ".join(
                [param_name for param_name, present in required_params_present.items() if not present]
            )
            raise ValueError(
                f"Arguments {missing_params} are required for {self.action_name} on {self.path} " "and are are missing."
            )

        return True


class MultiOperateur(Operateur):

    def __init__(self, operateurs: list[Operateur]):
        self.operateurs = operateurs

    @property
    def action_name(self):
        return self.operateurs[0].action_name

    @property
    def rest_operation_name(self):
        return self.operateurs[0].operation.method.value

    @property
    def path(self):
        return tuple([op.path for op in self.operateurs])

    @property
    def parameters(self):
        return tuple([op.parameters for op in self.operateurs])

    @property
    def required_parameters(self):
        return tuple([op.required_parameters for op in self.operateurs])

    def verify_required_args_present(self, raises=False, **kwargs):
        selected_operateur, valid = self.get_selected_operator_from_kwargs(**kwargs)
        number_valid = len([v for v in valid if v])
        if selected_operateur is None:
            if not raises:
                return False
            raise ValueError(
                "Requires at least one of these required parameters "
                f"{', or '.join([str(p_list) for p_list in self.required_parameters])} for "
                f"{self.action_name} action on {self.path}"
            )
        if number_valid > 1:
            matching_operateurs = [op for v, op in zip(valid, self.operateurs) if v]
            warn(
                f"Several required parameters needed by the operators are present, for {self.action_name} "
                f"the first matching, {selected_operateur.path} will be used. Found matching : "
                f"{', or '.join([op.path for op in matching_operateurs])}"
            )
        return True

    def get_selected_operator_from_kwargs(self, **kwargs):
        valid = [op.verify_required_args_present(raises=False, **kwargs) for op in self.operateurs]
        if not any(valid):
            return None, valid
        return self.operateurs[valid.index(True)], valid

    def make_url(self, **kwargs):
        operator, _ = self.get_selected_operator_from_kwargs(**kwargs)
        if operator is None:
            self.verify_required_args_present(raises=True, **kwargs)
            raise ValueError("This code should be impossible to reach")
        return operator.make_url(**kwargs)

    def __repr__(self):
        return (
            f"{', '.join([op.path for op in self.operateurs])} - "
            f"{', '.join([str(op.operation) for op in self.operateurs])}"
        )

    def describe(self):
        return f"{", ".join([self.client.schema.paths_dict[op.path] for op in self.operateurs])}"


class Request:

    client: "Client"
    operateur: "Operateur"
    trys = 0
    max_retries = 2

    def __init__(self, client: "Client", operateur: "Operateur", data=None, files=None, timeout=3000, **url_arguments):
        self.operateur = operateur
        self.client = client
        self.url_arguments = url_arguments
        self.data = data
        self.files = files
        self.headers = self.client.headers.copy()
        self.timeout = timeout

        if self.operateur.rest_operation_name in ["post", "put"] and self.data is None:
            raise ValueError(
                "To create (a.k.a POST) or update (a.k.a PUT) a new element, "
                "you need to supply the fields with the data argument"
            )

    @property
    def url(self):
        return self.operateur.make_url(**self.url_arguments)

    @property
    def request_method(self):
        return self.operateur.request_method

    def get_data(self):
        if self.files is not None:
            return None
        if isinstance(self.data, (dict, list)):
            self.headers["Content-Type"] = "application/json"
            return json.dumps(self.data)
        return self.data

    def get_files(self):
        return self.files

    def get_response(self) -> Response:
        data = self.get_data()
        files = self.get_files()
        logger.debug(f"Sending a request with url={self.url}, headers={self.headers}")
        r = self.request_method(
            self.url, stream=True, headers=self.headers, data=data, files=files, timeout=self.timeout
        )
        return r

    def handle(self):
        return self.handle_response(self.get_response())

    def handle_success(self, response: Response) -> dict[str, dict | str] | list[dict[str, dict | str]]:
        response_data = json.loads(response.text)
        if not isinstance(response_data, dict):
            if not isinstance(response_data, list):
                raise NotImplementedError(
                    f"Type of response data is {type(response_data)} - Response data : {response_data}"
                )
            return response_data
        next_url = response_data.get("next", None)
        if next_url:
            limit_offset = UrlValidator.get_limit_offset(next_url)
            next_request = Request(self.client, self.operateur, self.data, self.files, self.timeout, **limit_offset)
            return response_data["results"] + next_request.handle()

        if "results" in response_data.keys():
            return response_data["results"]
        return response_data

    def handle_response(self, response: Response) -> dict[str, dict | str] | list[dict[str, dict | str]] | None:
        action = self.client.post_request_callback(response)
        if action:
            if action == "retry":
                self.trys += 1
                return self.handle()
            else:
                raise NotImplementedError
        if response and response.status_code in (200, 201):
            return self.handle_success(response)
        elif response and response.status_code == 204:
            return None
        else:
            self.raise_from_response(response)

    def raise_from_response(self, response: Response):
        # response.raise_for_status()

        try:
            message = json.loads(response.text)
            message.pop("status_code", None)  # Get status code from response object instead
            message = message.get("detail") or message  # Get details if available
        except json.decoder.JSONDecodeError:
            message = response.text
        raise requests.HTTPError(response.status_code, self.url, message, response=response)
