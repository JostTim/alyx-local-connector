from typing import Literal, Optional, List, overload, TYPE_CHECKING

if TYPE_CHECKING:
    from pandas import DataFrame, Series
    from ..configuration import Configuration
    from .schema import OpenAPISpecification
    from .api import Request, Endpoint
    from .authentification import Authenticator

class WebClient:
    def __init__(
        self,
        url: Optional[str] = None,
        username: Optional[str] = None,
        *,
        auto_authenticate=True,
        make_default=False,
        use_default=True,
        config: "Optional[Configuration]" = None,
    ): ...
    def endpoint(self, path: str) -> "Endpoint": ...
    def list_endpoints(self) -> List[str]: ...
    @overload
    def rest(
        self,
        endpoint_name: str,
        action: Literal["list"] = "list",
        timeout: int = 1000,
        handles_internally: Literal[True] = True,
        **kwargs,
    ) -> "DataFrame" | None: ...
    @overload
    def rest(
        self,
        endpoint_name: str,
        action: Literal["retrieve"] = "retrieve",
        timeout: int = 1000,
        handles_internally: Literal[True] = True,
        **kwargs,
    ) -> "Series" | None: ...
    @overload
    def rest(
        self,
        endpoint_name: str,
        action: Literal["update"] = "update",
        timeout: int = 1000,
        handles_internally: Literal[True] = True,
        **kwargs,
    ) -> "Series" | None: ...
    @overload
    def rest(
        self,
        endpoint_name: str,
        action: Literal["create"] = "create",
        timeout: int = 1000,
        handles_internally: Literal[True] = True,
        **kwargs,
    ) -> "Series" | None: ...
    @overload
    def rest(
        self,
        endpoint_name: str,
        action: Literal["destroy"] = "destroy",
        timeout: int = 1000,
        handles_internally: Literal[True] = True,
        **kwargs,
    ) -> None: ...
    @overload
    def rest(
        self,
        endpoint_name: str,
        action: Literal["partial_update"] = "partial_update",
        timeout: int = 1000,
        handles_internally: Literal[True] = True,
        **kwargs,
    ) -> "Series" | None: ...
    # @overload
    # def rest(
    #     self,
    #     endpoint_name: str,
    #     action: Literal[
    #         "list", "retrieve", "update", "create", "destroy", "partial_update"
    #     ],
    #     timeout: int = 1000,
    #     handles_internally: Literal[True] = True,
    #     **kwargs,
    # ) -> Series | DataFrame | None: ...
    @overload
    def rest(
        self,
        endpoint_name: str,
        action: Literal["list", "retrieve", "update", "create", "destroy", "partial_update"],
        timeout: int = 1000,
        handles_internally: Literal[False] = False,
        **kwargs,
    ) -> "Request": ...

    # when handles_internally is False, it returns a Request
    @overload
    def search(
        self,
        endpoint: str,
        *,
        details: bool = True,
        raises: bool = True,
        name: Optional[str] = None,
        id: Optional[str] = None,
        handles_internally: Literal[False],
        **kwargs,
    ) -> "Request": ...

    # when handles_internally is True, it returns a Series if id is str
    @overload
    def search(
        self,
        endpoint: str,
        *,
        details: bool = True,
        raises: Literal[True] = True,
        name: Optional[str] = None,
        id: str,
        handles_internally: Literal[True] = True,
        **kwargs,
    ) -> "Series": ...

    # when handles_internally is True, if not raise, it returns a Series or None if id is str
    @overload
    def search(
        self,
        endpoint: str,
        *,
        details: bool = True,
        raises: Literal[False] = False,
        name: Optional[str] = None,
        id: str,
        handles_internally: Literal[True] = True,
        **kwargs,
    ) -> "Series" | None: ...

    # when handles_internally is True, it returns a Series if id is str
    @overload
    def search(
        self,
        endpoint: str,
        *,
        details: bool = True,
        raises: Literal[True] = True,
        name: str,
        id: Optional[str] = None,
        handles_internally: Literal[True] = True,
        **kwargs,
    ) -> "Series": ...

    # when handles_internally is True, if not raise, it returns a Series or None if id is str
    @overload
    def search(
        self,
        endpoint: str,
        *,
        details: bool = True,
        raises: Literal[False] = False,
        name: str,
        id: Optional[str] = None,
        handles_internally: Literal[True] = True,
        **kwargs,
    ) -> "Series" | None: ...

    # when handles_internally is True and raises is False, it can return None on top of pandas objs
    @overload
    def search(
        self,
        endpoint: str,
        *,
        details: bool = True,
        raises: Literal[True] = True,
        name: None = None,
        id: None = None,
        handles_internally: Literal[True] = True,
        **kwargs,
    ) -> "DataFrame": ...
    # when handles_internally is True and raises is False, it can return None on top of pandas objs
    @overload
    def search(
        self,
        endpoint: str,
        *,
        details: bool = True,
        raises: Literal[False] = False,
        name: None = None,
        id: None = None,
        handles_internally: Literal[True] = True,
        **kwargs,
    ) -> "DataFrame" | None: ...
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
    ) -> "WebClient": ...
    def pre_request_callback(self, request: "Request") -> None: ...
    def post_request_callback(self, request: "Request") -> Literal["retry"] | None: ...
    @property
    def username(self) -> str: ...
    @property
    def url(self) -> str: ...
    @property
    def token(self) -> str | None: ...
    @property
    def port(self) -> str: ...
    @property
    def host(self) -> str: ...
    @property
    def protocol(self) -> str: ...
    @property
    def netloc(self) -> str: ...
    @property
    def schema(self) -> "OpenAPISpecification": ...
    @property
    def authenticator(self) -> "Authenticator": ...
    @property
    def config(self) -> "Configuration": ...
    @config.setter
    def config(self, conf: Configuration): ...
    @property
    def headers(self) -> dict[str, str]: ...
