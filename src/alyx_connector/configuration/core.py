from pathlib import Path
from typing import Optional

from rich.prompt import Prompt
from rich.text import Text

from ..utils.types import Singleton
from .dicts import ConfigIndex, ServerConfig, UserConfig
from .directories import Directory, RequestsCache


class Configuration(Directory, metaclass=Singleton):
    """A Configuration is the main class of this module.
    It is a class that manages a ConfigIndex (a nested dictionnary with a
    json representation on disc, see below) and provides usefull fonctions to easily set/get
    server / user related informations from the index, and keep track of the currentely selected user,
    for adressing api requests using the right server, user and token.
    """

    directory_name = ".alyx_connector"
    index_filename = "index.json"

    @property
    def index_path(self) -> Path:
        return self.root_path / self.index_filename

    @property
    def index(self) -> "ConfigIndex":
        if not hasattr(self, "_index"):
            self._index = ConfigIndex(self.index_path, self)
        return self._index

    def get_server(self, server_address: str) -> "ServerConfig":
        """Finds and returns a server. To get the currentely selected server, use `.selected_user.server` instead"""
        return self.index.server(server_address)

    def get_user(self, username: str, server_address: Optional[str] = None) -> "UserConfig":
        """Finds and returns an user. To get the currentely selected user, use .`selected_user` instead"""
        return self.index.server(server_address).user(username)

    def select_user(
        self,
        server_address: Optional[str] = None,
        username: Optional[str] = None,
        make_default: Optional[bool] = None,
        silent: Optional[bool] = None,
        force_prompt: bool = False,
        **user_options,
    ) -> "Configuration":

        self.silent = silent
        # we temporarily set force prompt if True
        self.force_prompt = force_prompt
        self.selected_user = self.index.server(server_address).user(username)
        self.force_prompt = False
        self.selected_user.set_as_default(make_default)
        self.selected_user.set_options(**user_options)
        return self

    def is_user_selected(self) -> bool:
        if not hasattr(self, "_selected_user"):
            return False
        return True

    def raise_no_user_selected(self):
        raise AttributeError("selected_user has not yet been set. Please use select_user to do so")

    def raise_if_no_user_selected(self):
        if not self.is_user_selected():
            self.raise_no_user_selected()

    @property
    def selected_user(self) -> "UserConfig":
        self.raise_if_no_user_selected()
        return self._selected_user

    @selected_user.setter
    def selected_user(self, user: "UserConfig"):
        self._selected_user = user

    @property
    def username(self) -> str:
        return self.selected_user.username

    @property
    def url(self) -> str:
        return self.selected_user.server.url

    @property
    def rest_cache_location(self) -> Path:
        return self.selected_user.rest_cache_location

    @rest_cache_location.setter
    def rest_cache_location(self, value):
        self.selected_user.rest_cache_location = value

    @property
    def local_data_location(self) -> str | None:
        return self.selected_user.local_data_location

    @local_data_location.setter
    def local_data_location(self, value):
        self.selected_user.local_data_location = value

    @property
    def token(self) -> str | None:
        return self.selected_user.token

    @token.setter
    def token(self, value: str | None):
        self.selected_user.token = value

    def token_exists(self) -> bool:
        return self.selected_user.token_exists()

    def ask_password(self) -> str:
        return Prompt.ask(
            Text("Enter Alyx password for ", style="blue")
            .append(f"{self.username}")
            .append(" at ")
            .append(f"{self.url}", style="turquoise2"),
            password=True,
        )

    @property
    def silent(self) -> bool | None:
        if not self.is_user_selected():
            if not hasattr(self, "_silent"):
                self._silent = False
            return self._silent
        if hasattr(self, "_silent"):
            if self._silent is None:
                self._silent = self.selected_user.silent
            elif self._silent != self.selected_user.silent:
                self.selected_user.silent = self._silent
        else:
            self._silent = self.selected_user.silent
        return self._silent

    @silent.setter
    def silent(self, value: bool | None):
        if not self.is_user_selected():
            self._silent = value
        else:
            if value is None:
                return
                raise ValueError(
                    "Cannot set silent value to None if a userconfig is selected. Must be True or False"
                )
            self._silent = value
            self.selected_user.silent = value

    def list_servers(self) -> list[str]:
        return list(self.index.servers.keys())

    def list_users(self, server_address: Optional[str] = None) -> list[str]:
        if server_address is None:
            if self.index.default_server:
                server_address = self.index.default_server
            else:
                raise ValueError(
                    "You must supply a server on wich to list users, as a default one is not set."
                )
        server = self.index.server(server_address, make_default=False)
        return list(server.users.keys())

    def delete_server(self, server_address: str):
        request_cache_default_root = RequestsCache().root_path

        server = self.get_server(server_address)
        for user in server.users.values():
            for file in user.rest_cache_location.glob("*"):
                file.unlink(missing_ok=True)
            if user.rest_cache_location.is_dir():
                user.rest_cache_location.rmdir()
        if request_cache_default_root.joinpath(server.stringified_url).is_dir():
            request_cache_default_root.joinpath(server.stringified_url).rmdir()

        # TODO : delete user in index dict too

    def delete_user(self, username: str, server_address: Optional[str] = None):

        user = self.get_user(username, server_address)
        for file in user.rest_cache_location.glob("*"):
            file.unlink(missing_ok=True)
        if user.rest_cache_location.is_dir():
            user.rest_cache_location.rmdir()

        # TODO : delete user in index dict too

    def delete_all(self):
        """Deletes the whole configuration file (index.json) and the reference to the current Index.
        (to have it resinstanciated upon next call to .index)
        """

        request_cache_default_root = RequestsCache().root_path
        for server in self.index.servers.values():
            for user in server.users.values():
                for file in user.rest_cache_location.glob("*"):
                    file.unlink(missing_ok=True)
                if user.rest_cache_location.is_dir():
                    user.rest_cache_location.rmdir()
            if request_cache_default_root.joinpath(server.stringified_url).is_dir():
                request_cache_default_root.joinpath(server.stringified_url).rmdir()
        if request_cache_default_root.is_dir():
            request_cache_default_root.rmdir()

        self.index_path.unlink(missing_ok=True)
        if hasattr(self, "_index"):
            delattr(self, "_index")
        if hasattr(self, "_selected_user"):
            delattr(self, "_selected_user")
        if hasattr(self, "_silent"):
            delattr(self, "_silent")
