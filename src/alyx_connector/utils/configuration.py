from sys import platform
from os import environ
from pathlib import Path
import json
from json import JSONDecodeError
from enum import Enum
from rich.prompt import Prompt, Confirm
from rich.text import Text

from .types import Singleton
from ..web.urls import UrlValidator

from typing import Optional, Any


class Directory:
    directory_name: str | Path

    @property
    def root_path(self) -> Path:
        # windows
        if platform == "win32" or platform == "cygwin":
            return Path(environ["LOCALAPPDATA"]) / self.directory_name
        # linux
        else:
            return Path.home() / self.directory_name


class LocalData:
    directory = Path.home() / "Downloads" / "alyx_data"


class RequestsCache(Directory):
    directory_name = ".alyx_connector/requests_cache"


class Configuration(Directory, metaclass=Singleton):
    directory_name = ".alyx_connector"
    index_filename = "index.json"

    # def __init__(self, client: "Client"):
    #     self.client = client

    @property
    def index_path(self):
        return self.root_path / self.index_filename

    @property
    def index(self) -> "Index":
        if not hasattr(self, "_index"):
            self._index = Index(self.index_path, self)
        return self._index  # type: ignore

    def select_current_user_config(
        self,
        server_address: Optional[str] = None,
        username: Optional[str] = None,
        make_default: Optional[bool] = None,
        silent: Optional[bool] = None,
        force_prompt=False,
    ):
        self.silent = silent
        self.force_prompt = force_prompt
        self._current_user_config = self.index.server(server_address).user(username)
        self._current_user_config.set_as_default(make_default)

    def is_user_selected(self) -> bool:
        if not hasattr(self, "_current_user_config"):
            return False
        return True

    @property
    def current_user_config(self) -> "User":
        if not self.is_user_selected():
            raise ValueError("current_user_config has not yet been set. Please use select_current_user_config to do so")
        return self._current_user_config

    @property
    def username(self):
        return self.current_user_config.username

    @property
    def url(self):
        return self.current_user_config.server.url

    @property
    def rest_cache_location(self):
        return self.current_user_config.rest_cache_location

    @rest_cache_location.setter
    def rest_cache_location(self, value):
        self.current_user_config.rest_cache_location = value

    @property
    def local_data_location(self):
        return self.current_user_config.local_data_location

    @local_data_location.setter
    def local_data_location(self, value):
        self.current_user_config.local_data_location = value

    @property
    def token(self) -> str | None:
        return self.current_user_config.token

    @token.setter
    def token(self, value: str | None):
        self.current_user_config.token = value

    def token_exists(self):
        return self.current_user_config.token_exists()

    def ask_password(self):
        return Prompt.ask(
            Text("Enter Alyx password for ", style="blue")
            .append(f"{self.username}")
            .append(" at ")
            .append(f"{self.url}", style="turquoise2"),
            password=True,
        )

    @property
    def silent(self):
        if not self.is_user_selected():
            if not hasattr(self, "_silent"):
                self._silent = False
            return self._silent
        if hasattr(self, "_silent"):
            if self._silent is None:
                self._silent = self.current_user_config.silent
            elif self._silent != self.current_user_config.silent:
                self.current_user_config.silent = self._silent
        else:
            self._silent = self.current_user_config.silent
        return self._silent

    @silent.setter
    def silent(self, value: bool | None):
        if not self.is_user_selected():
            self._silent = value
        else:
            if value is None:
                return
                raise ValueError("Cannot set silent value to None if a userconfig is selected. Must be True or False")
            self._silent = value
            self.current_user_config.silent = value


class ConfigDict(dict):

    def __init__(
        self, input_dict={}, /, *, parent: "Optional[ConfigDict]" = None, parent_key: Optional[str] = None, **kwargs
    ):

        super().__init__(input_dict, **kwargs)
        self.parent = self if parent is None else parent
        self.parent_key = "INDEX" if parent_key is None else parent_key
        self._content_instanciation()

    def load(self):
        self.index.load()

    def save(self):
        self.index.save()

    @property
    def index(self) -> "Index":
        if self.parent == self:
            return self  # type: ignore
        return self.parent.index  # type: ignore

    def __getitem__(self, key) -> Any:
        if self.index.last_read_time != self.index.path.stat().st_mtime:
            # If the file has beeen modified manually, we reload the whole config (saves time durning debugging mostly)
            self.load()
        value = dict.__getitem__(self, key)
        return value

    def __setitem__(self, key, value) -> Any:
        value = self._value_instanciation(key, value)
        dict.__setitem__(self, key, value)
        self.save()

    def _content_instanciation(self, dico: Optional[dict] = None):
        dico = dict(self) if dico is None else dico
        for key, value in dico.items():
            dict.__setitem__(self, key, self._value_instanciation(key, value))

    def _value_instanciation(self, key, value):
        if isinstance(value, dict):
            cls = getattr(SpecialKeysEnum, self.parent_key, SpecialKeysEnum._DEFAULT)
            value = cls.value(value, parent=self, parent_key=key)
        return value

    @property
    def name(self):
        return self.parent_key


class Index(ConfigDict):

    last_read_time = 0.0

    def __init__(self, path: str | Path, config: Configuration):
        super().__init__()
        self.path = Path(path)
        self.config = config
        self.load()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as file:
            json.dump(dict(self), file, indent=4)
        self.last_read_time = self.path.stat().st_mtime

    def load(self):
        if not self.path.is_file():
            self.save()
        with open(self.path, "r") as file:
            try:
                content: dict = json.load(file)
            except JSONDecodeError as e:
                raise IOError(
                    f"Your alyx config index file located here : {self.path} has json formatting errors. "
                    "Please check it."
                ) from e
        self._content_instanciation(content)
        self.last_read_time = self.path.stat().st_mtime

    @property
    def servers(self) -> dict:
        servers_map_key = SpecialKeysEnum.SERVERS_MAP.name
        if servers_map_key not in self.keys():
            self[servers_map_key] = {}
        return self[servers_map_key]

    @property
    def default_server(self) -> str | None:
        if "DEFAULT_SERVER" not in self.keys():
            self["DEFAULT_SERVER"] = None
        return self["DEFAULT_SERVER"]

    @default_server.setter
    def default_server(self, server_address: str):
        if not self.server_exists(server_address):
            raise ValueError(f"Cannot set default_client to {server_address} because this client doesn't exist")
        self["DEFAULT_SERVER"] = server_address

    def get_server(self, server_address: str) -> "Server":
        server_address = UrlValidator.validate_url(server_address)
        if not self.server_exists(server_address):
            raise KeyError(f"Server address {server_address} does not exist in alyx config {self.path}")
        return self.servers[server_address]

    def set_server(self, server_address: str) -> "Server":
        server_address = UrlValidator.validate_url(server_address)
        if self.server_exists(server_address):
            raise KeyError(f"Server address {server_address} already exists in alyx config {self.path}")
        self.servers[server_address] = Server({}, parent=self, parent_key=server_address)
        return self.servers[server_address]

    def server(self, server_address: Optional[str] = None) -> "Server":

        if server_address is not None:
            if not self.server_exists(server_address):
                return self.set_server(server_address)
            return self.get_server(server_address)

        else:  # server is None
            if self.default_server is None or self.index.config.force_prompt:
                if self.index.config.silent:
                    raise ValueError("Cannot find the default alyx server as if has not been set")
                server_address = Prompt.ask(
                    "Please enter the server address that you use to connect to alyx.",
                    default=self.default_server or "127.0.0.1",
                )
                if not server_address:
                    raise ValueError(f"The server adress must be a valid string, you entered : {server_address}")
                server_address = UrlValidator.validate_url(server_address)
                if self.server_exists(server_address):
                    server = self.get_server(server_address)
                else:
                    server = self.set_server(server_address)
                if not server.is_default():
                    yes = Confirm.ask(f"Make {server.name} the default server for future connections ?")
                    if yes:
                        server.make_default()
                return server

            else:
                return self.get_server(self.default_server)

    def server_exists(self, server_address: str):
        if server_address not in self.servers.keys():
            return False
        return True


class Server(ConfigDict):

    @property
    def default_user(self) -> str | None:
        if "DEFAULT_USER" not in self.keys():
            self["DEFAULT_USER"] = None
        return self["DEFAULT_USER"]

    @default_user.setter
    def default_user(self, value: str):
        self["DEFAULT_USER"] = value

    @property
    def users(self):
        users_map_key = SpecialKeysEnum.USERS_MAP.name
        if users_map_key not in self.keys():
            self[users_map_key] = {}
        return self[users_map_key]

    def get_user(self, username: str) -> "User":
        if not self.user_exists(username):
            raise KeyError(f"User {username} does not exist for server {self.name} in alyx config {self.index.path}")
        return self.users[username]

    def set_user(self, username: str) -> "User":
        if self.user_exists(username):
            raise KeyError(f"User {username} already exists for server {self.name} in alyx config {self.index.path}")
        self.users[username] = User({}, parent=self, parent_key=username)
        return self.users[username]

    def user(self, username: Optional[str] = None) -> "User":
        """Main interface for getting user using automatic resolution, and setting with prompt interface.
        To set the user with code interface, use set_user.
        Retrieve or set the user for the current session.

        This method allows you to specify a username to retrieve or create a user.
        If no username is provided, it will prompt the user for input if a default user is
        not set or if forced by configuration.
        The user can also be marked as the default for future connections.

        Args:
            username (Optional[str]): The username to retrieve or set.
                If None, the method will handle prompting for a username.

        Returns:
            User: The User object associated with the specified or prompted username.

        Raises:
            ValueError: If the default user is not set and the method is in silent mode,
                or if the entered username is invalid.
        """
        if username is not None:
            if not self.user_exists(username):
                return self.set_user(username)
            return self.get_user(username)

        else:  # user is None

            if self.default_user is None or self.index.config.force_prompt:
                if self.index.config.silent:
                    raise ValueError(
                        "Cannot find the default alyx user for " f"the server {self.name} as if has not been set"
                    )
                username = Prompt.ask(
                    f"Please enter the username that you use to connect to {self.name}", default=self.default_user
                )
                if not username:
                    raise ValueError(f"Username must be a valid string, you entered : {username}")
                if self.user_exists(username):
                    user = self.get_user(username)
                else:
                    user = self.set_user(username)
                if not user.is_default():
                    yes = Confirm.ask(
                        f"Make {user.name} the default user for next connections to the server {self.name}?"
                    )
                    if yes:
                        user.make_default()
                return user
            else:
                return self.get_user(self.default_user)

    def user_exists(self, username: str):
        if username not in self.users.keys():
            return False
        return True

    def make_default(self):
        """Make current server in server list as default in config file"""
        self.index.default_server = self.name

    def is_default(self):
        if self.index.default_server == self.name:
            return True
        return False

    @property
    def url(self):
        return self.name

    @property
    def stringified_url(self):
        return self.url.replace(":", "_").replace("/", "_")


class User(ConfigDict):

    @property
    def rest_cache_location(self) -> Path:
        if "REST_CACHE_LOCATION" not in self.keys():
            path = RequestsCache().root_path / f"{self.server.stringified_url}" / f"{self.username}"
            path.mkdir(parents=True, exist_ok=True)
            self["REST_CACHE_LOCATION"] = str(path)
        return Path(self["REST_CACHE_LOCATION"])

    @rest_cache_location.setter
    def rest_cache_location(self, path: str | Path):
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        self["REST_CACHE_LOCATION"] = str(path)

    @property
    def silent(self):
        if "SILENT" not in self.keys():
            self["SILENT"] = False
        return self["SILENT"]

    @silent.setter
    def silent(self, value: bool):
        self["SILENT"] = value

    @property
    def local_data_location(self) -> str | None:
        if "LOCAL_DATA_FOLDER" not in self.keys():
            self["LOCAL_DATA_FOLDER"] = None
        return self["LOCAL_DATA_FOLDER"]

    @local_data_location.setter
    def local_data_location(self, value: str):
        self["LOCAL_DATA_FOLDER"] = value

    @property
    def token(self) -> str | None:
        if "TOKEN" not in self.keys():
            self["TOKEN"] = None
        return self["TOKEN"]

    @token.setter
    def token(self, value: str | None):
        self["TOKEN"] = value

    def token_exists(self):
        if self.token:
            return True
        return False

    @property
    def server(self) -> "Server":
        return self.parent.parent  # type: ignore

    def make_default(self):
        """Make current user in server as default in config file"""
        self.server.default_user = self.name

    def make_all_default(self):
        """Make both current user in server, and current server in list of servers, as defaults, in config file"""
        self.make_default()
        self.server.make_default()

    def set_as_default(self, yes: Optional[bool]):
        """Make all default if yes, ask if None and not silent, else do nothing"""
        if self.is_all_default():
            return
        if yes is None:
            if self.index.config.silent:
                return
            yes = Confirm.ask("Make this the default for next connections ?")
        if yes:
            self.make_all_default()

    def is_default(self):
        if self.server.default_user == self.name:
            return True
        return False

    def is_all_default(self):
        return all([self.is_default(), self.server.is_default()])

    @property
    def username(self):
        return self.name

    def set_options(self, **options):
        for key, value in options:
            setter = getattr(getattr(self, key), "fset")
            setter(value)


class SpecialKeysEnum(Enum):
    SERVERS_MAP = Server
    USERS_MAP = User
    _DEFAULT = ConfigDict
