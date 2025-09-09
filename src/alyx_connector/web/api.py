from openapi_parser.specification import Operation, Specification, Parameter, Path as OpenAPIPath
from openapi_parser import parse as parse_openapi_schema
from openapi_parser.errors import ParserError
import json, re, requests
from requests.models import Response
from urllib.parse import urlencode, quote
from logging import getLogger
from abc import ABC
from pandas import DataFrame, Series
from tqdm import tqdm
from sys import stdout
from warnings import warn
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..utils.logging import filter_message
from .urls import UrlValidator

from typing import Any, Dict, Tuple, List, Optional, Protocol, TypeAlias, NoReturn, overload, cast, TYPE_CHECKING

if TYPE_CHECKING:
    from .clients import ClientWithAuth, Client

logger = getLogger("alyx_connector.client")


ResponseDataEntry: TypeAlias = dict[str, "ResponseDataEntry | str"]
ResponseListofDataEntries = list[ResponseDataEntry]


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
        self.endpoint = self.path.endpoint

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
    def required_parameters(self) -> tuple[dict[str, Parameter]]:
        return ({param.name: param for param in self.operation.parameters if param.required},)

    def __repr__(self):
        return f"{self.path} - {self.operation}"

    def describe(self):
        return self.client.schema.paths_dict[self.path]

    def verify_required_args_present(self, raises=False, **kwargs):

        # if no required parameter is defined in the endpoint, just skip the checks
        if len(self.required_parameters[0]) == 0:
            return True

        required_params_present = {
            param_name: bool(kwargs.get(param_name, None)) for param_name in self.required_parameters[0].keys()
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
        self.client = self.operateurs[0].client
        self.endpoint = self.operateurs[0].path.endpoint

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
    def required_parameters(self) -> tuple[dict[str, Parameter], ...]:
        return tuple([op.required_parameters[0] for op in self.operateurs])

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
    response: None | Response = None

    def __init__(
        self,
        client: "Client",
        operateur: "Operateur",
        data=None,
        files=None,
        timeout=3000,
        details=False,
        unpaginate=True,
        **url_arguments,
    ):
        self.operateur = operateur
        self.client = client
        self.url_arguments = url_arguments
        self.input_data = data
        self.files = files
        self.headers = self.client.headers.copy()
        self.timeout = timeout
        self.details = details
        self.unpaginate = unpaginate

        if self.operateur.rest_operation_name in ["post", "put"] and self.input_data is None:
            raise ValueError(
                "To create (a.k.a POST) or update (a.k.a PUT) a new element, "
                "you need to supply the fields with the data argument"
            )

        # just ensure details is false if action is retrieve, to avoid recursiveness, in case of a bug somewhere
        # this should be unnecessary but also fixing details=False make
        # it more clear as details=True should have no effect in retrieve mode.
        if self.action_name == "retrieve":
            self.details = False

        self.output_data = ResponseData(self)

    def copy(
        self,
        client: "Optional[Client]" = None,
        operateur: "Optional[Operateur]" = None,
        data=None,
        files=None,
        timeout=None,
        details=None,
        unpaginate=None,
        use_original_url_arguments=True,
        **url_arguments,
    ):

        if use_original_url_arguments:
            arguments = self.url_arguments.copy()
            arguments.update(**url_arguments)
        else:
            arguments = url_arguments
        return Request(
            client=client if client is not None else self.client,
            operateur=operateur if operateur is not None else self.operateur,
            data=data if data is not None else self.input_data,
            files=files if files is not None else self.files,
            timeout=timeout if timeout is not None else self.timeout,
            details=details if details is not None else self.details,
            unpaginate=unpaginate if unpaginate is not None else self.unpaginate,
            **arguments,
        )

    @property
    def action_name(self):
        return self.operateur.action_name

    @property
    def url(self):
        return self.operateur.make_url(**self.url_arguments)

    @property
    def request_method(self):
        return self.operateur.request_method

    def get_input_data(self):
        if self.files is not None:
            return None
        if isinstance(self.input_data, (dict, list)):
            self.headers["Content-Type"] = "application/json"
            return json.dumps(self.input_data)
        return self.input_data

    def get_files(self):
        return self.files

    def get_response(self) -> Response:
        """This function uses ther Request object's arguments and variables to actually get a standard response.
        It creates the appropriate url and inputs, and sends the API request.
        The returned object is the request's response."""
        data = self.get_input_data()
        files = self.get_files()
        logger.debug(f"Sending a request with url={self.url}, headers={self.headers}")
        r = self.request_method(
            self.url, stream=True, headers=self.headers, data=data, files=files, timeout=self.timeout
        )
        return r

    def handle(self):
        if self.response:
            return self
        self.response = self.get_response()
        self.handle_response(self.response)
        return self

    def handle_response(self, response: Response):
        """This function is meant to run the wrappers before or as a consequence of the response.
        For example, ability to retry, etc"""
        action = self.client.post_request_callback(self)
        if action:
            if action == "retry" and self.trys < self.max_retries:
                self.trys += 1
                self.response = None
                return self.handle()
            elif action == "retry" and self.trys >= self.max_retries:
                raise IOError("Maximum number of retries reached.")
            else:
                raise NotImplementedError
        if response and response.status_code in (200, 201):
            return self
        elif response and response.status_code == 204:
            return self
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


class ResponseData:

    def __init__(self, request: "Request"):
        self.request = request

    @property
    def status_code(self):
        if not self.request.response:
            raise ValueError("Cannot determine status code of a request that didn't got a response.")
        return self.request.response.status_code

    @property
    def unpaginate(self):
        return self.request.unpaginate

    def is_paginated(self):
        if self.raw_json and isinstance(self.raw_json, dict) and self.raw_json.get("next", None):
            return True
        return False

    @property
    def raw_json(self) -> dict[str, Any] | None:
        if hasattr(self, "_raw_json"):
            return self._raw_json
        if not self.request.response:
            return None
        if self.status_code not in (200, 201):
            return None

        self._raw_json = json.loads(self.request.response.text)
        return self._raw_json

    def process_json(self) -> ResponseDataEntry | ResponseListofDataEntries | None:

        if self.raw_json is None:
            return None
        if not isinstance(self.raw_json, dict):
            if isinstance(self.raw_json, list):
                # Data in this section is an unpaginated list of items (list of dicts)
                return self.details_aggregation(self.raw_json)
            raise NotImplementedError(f"Type of response data is {type(self.json)} - Response data : {self.json}")

        if self.is_paginated():
            # Data in this section is a paginated list of items (list of dicts)
            if self.unpaginate:
                return self.details_aggregation(self.raw_json["results"] + self.next_pages_as_json())
            return self.details_aggregation(self.raw_json["results"])

        # In case of last page in the pagination, is paginated will be false, but the json will still be a dict
        if "results" in self.raw_json.keys():
            return self.details_aggregation(self.raw_json["results"])

        # data here is a dict or a scalar (inslge line of text, or number)
        return self.raw_json

    @property
    def json(self) -> ResponseDataEntry | ResponseListofDataEntries | None:
        if hasattr(self, "_processed_json"):
            return self._processed_json
        if not self.request.response:
            return None
        self._processed_json = self.process_json()
        return self._processed_json

    def next_page_request(self, all_pages=True):
        if not self.is_paginated():
            return None

        next_url = cast(str, self.raw_json.get("next"))  # type: ignore
        limit_offset = UrlValidator.get_limit_offset(next_url)
        # unpaginate = True if all pages = True, or we just request the next page
        next_request = self.request.copy(unpaginate=all_pages, details=False, **limit_offset)
        return next_request

    def next_page_as_json(self):
        request = self.next_page_request(all_pages=False)
        return request.handle().output_data.json if request else None

    def next_page(self):
        request = self.next_page_request(all_pages=False)
        return request.handle().output_data.table if request else None

    def next_pages_as_json(self):
        request = self.next_page_request()
        return request.handle().output_data.json if request else None

    def next_pages(self):
        request = self.next_page_request()
        return request.handle().output_data.table if request else None

    @property
    def table(self) -> Series | DataFrame | None:
        return self.entries_to_pandas(self.json)

    def details_aggregation(self, data_entries: ResponseListofDataEntries) -> ResponseListofDataEntries:
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

        # Aggregate only if the request is set up to do so.
        if not self.request.details:
            return data_entries

        # find the operateur correcsponding to the request done, but for the retrieve action
        retrieve_operateur = self.request.operateur.path.endpoint.actions["retrieve"]

        required_parameter_names = select_matching_required_parameters(data_entries, operateur=retrieve_operateur)

        if required_parameter_names is None:
            raise ValueError(
                "Cannot retrieve details if the retrieve operateur " "doesn't take at least one required argument"
            )
        detailed_results: ResponseListofDataEntries = []
        # make max_workers a setting of the web_client
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(
                    self.get_detail,
                    result,
                    required_parameter_names=required_parameter_names,
                    retrieve_operateur=retrieve_operateur,
                )
                for result in data_entries
            ]
            for f in tqdm(
                as_completed(futures),
                total=len(data_entries),
                delay=2,
                desc=f"Loading {retrieve_operateur.endpoint} details",
                file=stdout,
            ):
                detailed_results.append(f.result())
        return detailed_results

    def get_detail(
        self,
        data_entry: ResponseDataEntry,
        required_parameter_names: list[str] | None,
        retrieve_operateur: Operateur | None,
    ):
        if retrieve_operateur is None:
            retrieve_operateur = self.request.operateur.path.endpoint.actions["retrieve"]
        if required_parameter_names is None:
            required_parameter_names = select_matching_required_parameters(data_entry, operateur=retrieve_operateur)

        retrieve_params = {name: data_entry[name] for name in required_parameter_names}
        request = self.request.copy(
            operateur=retrieve_operateur, use_original_url_arguments=False, **retrieve_params  # type: ignore
        )
        recieved_data = request.handle().output_data.json
        if recieved_data is None:
            raise ValueError("Error getting details for <help code misssing>")
        if isinstance(recieved_data, list):
            raise TypeError("Retrieved data seems to be a list")
        return recieved_data

    def entries_to_pandas(self, data_entries: ResponseDataEntry | ResponseListofDataEntries | None):
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
        if data_entries is None:
            return None

        retrieve_operateur = self.request.operateur.endpoint.actions["retrieve"]

        if isinstance(data_entries, list):
            required_parameters = select_matching_required_parameters(data_entries, operateur=retrieve_operateur)
            table = recursively_pandify_items(data_entries)

            if required_parameters:
                table = table.reset_index().set_index(required_parameters)
            return table
        elif isinstance(data_entries, dict):
            return recursively_pandify_items(data_entries)
        else:
            raise NotImplementedError(f"Type was not list or dict : {type(data_entries)}")


def recursively_pandify_items(data_entries: ResponseDataEntry | ResponseListofDataEntries):
    if isinstance(data_entries, list):
        table = DataFrame(data_entries)
        for column in table.columns:
            if not isinstance(table[column].iloc[0], list):
                continue

            are_list_of_dict = any(
                [
                    all([isinstance(item, dict) for item in table_item])
                    for table_item in table[column]
                    if len(table_item)
                ]
            )
            if not are_list_of_dict:
                continue

            try:
                pandified_column = table[column].apply(recursively_pandify_items)  # type: ignore
            except Exception as e:
                logger.debug(f"Error pandifying column {column}. Skipping. {type(e)} - {e}")
                continue
            table[column] = pandified_column
        if len(table) and "id" in table.columns:
            table = table.set_index("id")
        return table
    elif isinstance(data_entries, dict):
        id = data_entries.pop("id", None)
        if not isinstance(id, str):
            raise TypeError(f"ID of a recieved object must be a string, but alyx server returned a {type(id)} - {id=}")
        return Series(data_entries, name=id)
    else:
        raise NotImplementedError(f"Type was not list or dict : {type(data_entries)}")


def select_matching_required_parameters(
    data_entries: ResponseDataEntry | ResponseListofDataEntries, operateur: Operateur
):

    if isinstance(data_entries, list):
        if len(data_entries):
            data_entry = data_entries[0]
        else:
            return None
    elif isinstance(data_entries, dict):
        if not len(data_entries):
            return None
    else:
        raise TypeError("Expected list or dict")

    data_entry = data_entries[0] if isinstance(data_entries, list) else data_entries

    for parameter_combination in operateur.required_parameters:
        if all([parameter_name in data_entry.keys() for parameter_name in parameter_combination.keys()]):
            return list(parameter_combination.keys())
    # TODO : in case where several possibilities are ok, try to match first according to priority list :
    # name first, id otherwise, and something else, if something else exists

    # no parameter combination necessary was found in the data_entry_based
    # on the selected operateur required parameters
    return None
