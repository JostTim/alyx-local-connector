# from abc import ABC
from logging import getLogger
from openapi_parser.specification import Specification, Path as OpenAPIPath
from openapi_parser import parse as parse_openapi_schema
from openapi_parser.errors import ParserError

from ..utils.logging import filter_message

from typing import Dict

logger = getLogger("alyx_connector.schema")

# class APISpecification(ABC):

#     @staticmethod
#     def from_url(url):
#         raise NotImplementedError

#     @property
#     def paths_dict(self) -> Dict:
#         return {}


class OpenAPISpecification(Specification):

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