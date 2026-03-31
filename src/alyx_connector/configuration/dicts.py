import json
from enum import Enum
from json import JSONDecodeError
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from rich.prompt import Confirm, Prompt

from ..web.urls import UrlValidator
from .directories import RequestsCache

if TYPE_CHECKING:
    from .core import Configuration

## TODO
## silent should be separated from noinput
## silent is a config for verbosity of remote / local operations,
## that can be kept in config value per user
## noinput instead is for keeping track of wether None values can be asked
## via terminal user input, or if the code should raise instead if a value is None
## (for users to be able to debug their code, if they intend for CLI / automated runs,
## and forgot to programatically set some values)


class ConfigDict(dict):
    """A config dict-class is supposed to be inherited, and be part of a set of nested dictionnaries, implemented as
    ConfigDicts, for wich a ConfigIndex is the root of them. They work together and recursively pass upwards to the index,
    events such as necessity to save or load the "root" wich holds a representation to a json file on disc, of the
    whole nested dictionnaries stack.
    It automatically instanciates "sub" dictionnaries of itself, using SpecialDictKeysEnum, wich maps dictionnary keys, to
    child classes of ConfigDict. Every key who's name is not a name in the SpecialDictKeysEnum will be by default a simple
    ConfigDict, as specified in SpecialDictKeysEnum.-DEFAULT value.

    How it works to create the whole stack of parent to child instances :
    - on __init__ -> _content_instanciation,
    - for each key : -> value obtined from _value_instanciation (using a ConfigDict if value is a dict),
    - As a result, if the child itself is a ConfigDict, it performs instanciation, starting with __init__
    - finally, sets that value to the key with standard dict.__setitem__

    To be sure to not erase previously stored value, the ConfigIndex only (the root of the dict tree) performs
    a global .load() from the .json file, unpon __init__. (Wich triggers the first _content_instanciation
    run (then recursively on the whole stack).

    To be sure to not perform many .save() write operations on ConfigIndex recursive _content_instanciation,
    after a .load(), the .save() method is only attached to the child derived __setitem__ method, and
    when instanciating,  the _content_instanciation uses the original dictionnary class .__setitem__
    method, wich attached to the dict without triggering a .save().

    An example Index ConfigDict as a json object, looks like this :

    ```json
    {
        "SERVERS_MAP": {
            "http://127.0.0.1:80": {
                "DEFAULT_USER": "admin",
                "USERS_MAP": {
                    "admin": {
                        "SILENT": false,
                        "TOKEN": "1234_abcd_etc..."
                    },
                    "user": {
                        "TOKEN": null,
                        "SILENT": false
                    },
                }
            }
        },
        "DEFAULT_SERVER": "http://127.0.0.1:80"
    }
    ```

    """

    def __init__(
        self,
        input_dict={},
        /,
        *,
        parent: "Optional[ConfigDict]" = None,
        parent_key: Optional[str] = None,
        **kwargs,
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
    def index(self) -> "ConfigIndex":
        if self.parent == self:
            return self  # type: ignore
        return self.parent.index

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
            cls = getattr(SpecialDictKeysEnum, self.parent_key, SpecialDictKeysEnum._DEFAULT)
            value = cls.value(value, parent=self, parent_key=key)
        return value

    @property
    def name(self):
        return self.parent_key


class ConfigIndex(ConfigDict):
    """The index class is a dictionnay, and a class that manages the json file that stores that dictionnary.
    It represents the "root" of that dictionary, and is meant to be used as the gateway to any user
    configuration information stored for the use of alyx-local-connector.
    """

    last_read_time = 0.0

    def __init__(self, path: str | Path, config: "Configuration"):
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
    def servers(self) -> "dict[str, ServerConfig]":
        servers_map_key = SpecialDictKeysEnum.SERVERS_MAP.name
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
            raise ValueError(
                f"Cannot set default_client to {server_address} because this client doesn't exist"
            )
        self["DEFAULT_SERVER"] = server_address

    def get_server(self, server_address: str) -> "ServerConfig":
        server_address = UrlValidator.validate_url(server_address)
        if not self.server_exists(server_address):
            raise KeyError(
                f"Server address {server_address} does not exist in alyx config {self.path}"
            )
        return self.servers[server_address]

    def set_server(self, server_address: str) -> "ServerConfig":
        server_address = UrlValidator.validate_url(server_address)
        if self.server_exists(server_address):
            raise KeyError(
                f"Server address {server_address} already exists in alyx config {self.path}"
            )
        self.servers[server_address] = ServerConfig({}, parent=self, parent_key=server_address)
        return self.servers[server_address]

    def server(
        self, server_address: Optional[str] = None, make_default: Optional[bool] = False
    ) -> "ServerConfig":

        if server_address is not None:
            if not self.server_exists(server_address):
                return self.set_server(server_address)
            return self.get_server(server_address)

        else:  # server is None, we use the default one
            if self.default_server is None or self.index.config.force_prompt:
                if self.index.config.silent:
                    raise ValueError("Cannot find the default alyx server as if has not been set")
                server_address = Prompt.ask(
                    "Please enter the server address that you use to connect to alyx.",
                    default=self.default_server or "127.0.0.1",
                )
                if not server_address:
                    raise ValueError(
                        f"The server adress must be a valid string, you entered : {server_address}"
                    )
                server_address = UrlValidator.validate_url(server_address)
                if self.server_exists(server_address):
                    server = self.get_server(server_address)
                else:
                    server = self.set_server(server_address)
                if not server.is_default() and make_default is None:
                    make_default = Confirm.ask(
                        f"Make {server.name} the default server for future connections ?"
                    )
                if make_default:
                    server.make_default()
                return server

            else:
                return self.get_server(self.default_server)

    def server_exists(self, server_address: str):
        if server_address not in self.servers.keys():
            return False
        return True


class ServerConfig(ConfigDict):
    """This class defines how items found in the SpecialDictKeysEnum.SERVERS_MAP should be implemented,
    the helper functions shey should provide, and how they should behave.
    They define helper function to find the users already setup with the connector on that computer,
    for the specific server they link to.
    """

    @property
    def default_user(self) -> str | None:
        if "DEFAULT_USER" not in self.keys():
            self["DEFAULT_USER"] = None
        return self["DEFAULT_USER"]

    @default_user.setter
    def default_user(self, value: str):
        self["DEFAULT_USER"] = value

    @property
    def users(self) -> "dict[str, UserConfig]":
        users_map_key = SpecialDictKeysEnum.USERS_MAP.name
        if users_map_key not in self.keys():
            self[users_map_key] = {}
        return self[users_map_key]

    def get_user(self, username: str) -> "UserConfig":
        if not self.user_exists(username):
            raise KeyError(
                f"User {username} does not exist for server {self.name} in alyx config {self.index.path}"
            )
        return self.users[username]

    def set_user(self, username: str) -> "UserConfig":
        if self.user_exists(username):
            raise KeyError(
                f"User {username} already exists for server {self.name} in alyx config {self.index.path}"
            )
        self.users[username] = UserConfig({}, parent=self, parent_key=username)
        return self.users[username]

    def user(
        self, username: Optional[str] = None, make_default: Optional[bool] = None
    ) -> "UserConfig":
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
            UserConfig: The UserConfig object associated with the specified or prompted username.

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
                        "Cannot find the default alyx user for "
                        f"the server {self.name} as if has not been set"
                    )
                username = Prompt.ask(
                    f"Please enter the username that you use to connect to {self.name}",
                    default=self.default_user,
                )
                if not username:
                    raise ValueError(f"Username must be a valid string, you entered : {username}")
                if self.user_exists(username):
                    user = self.get_user(username)
                else:
                    user = self.set_user(username)

                if not user.is_default() and make_default is None:
                    make_default = Confirm.ask(
                        f"Make {user.name} the default user for next connections to the server {self.name}?"
                    )
                if make_default:
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

    def is_default(self) -> bool:
        if self.index.default_server == self.name:
            return True
        return False

    @property
    def url(self) -> str:
        return self.name

    @property
    def stringified_url(self) -> str:
        return self.url.replace(":", "_").replace("/", "_")


class UserConfig(ConfigDict):
    """This class defines how items found in the SpecialDictKeysEnum.USERS_MAP should be implemented,
    the helper functions shey should provide, and how they should behave.
    They provide information for a specific user, on a specific server (they have a ServerConfig class in their .parent stack)
    They also provide a way to know the location of the rest cache location for that specific user, on disk.
    """

    @property
    def rest_cache_location(self) -> Path:
        if "REST_CACHE_LOCATION" not in self.keys():
            path = (
                RequestsCache().root_path / f"{self.server.stringified_url}" / f"{self.username}"
            )
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
    def server(self) -> "ServerConfig":
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
            defaulting_obj = "server / user couple"
            if self.server.is_default():
                defaulting_obj = "user"
            else:
                if self.is_default():
                    defaulting_obj = "server"
                # else we keep "server / user couple", if neither server not user are defaults already
            yes = Confirm.ask(f"Make this {defaulting_obj} the default for next connexions ?")
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
        for key, value in options.items():
            # get the setter function of the method, like token, silent, etc, with fset
            setter = getattr(getattr(self, key), "fset")
            setter(value)

    def __hash__(self) -> int:
        return hash((self.server.url, self.username, self.token))


class SpecialDictKeysEnum(Enum):
    """Provides a relationship between a dictionnary's key, and the class that items
    from that dictionnnary Key should be instanciated with, for automatic ConfigDict
    load / save on edit machinery.
    """

    SERVERS_MAP = ServerConfig
    USERS_MAP = UserConfig
    _DEFAULT = ConfigDict
