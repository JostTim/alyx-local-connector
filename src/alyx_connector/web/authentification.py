import requests
from logging import getLogger

from ..styling import obfuscate

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..configuration import Configuration

logger = getLogger("alyx_connector.web.auth")


class Authenticator:
    config: "Configuration"

    def __init__(self, config: "Configuration"):
        self.config = config

    @property
    def token(self) -> str | None:
        return self.config.token

    @token.setter
    def token(self, value: str | None):
        self.config.token = value

    def authenticate(
        self, password: Optional[str] = None, cache_token=True, force=False
    ) -> str | None:
        """
        Gets a security token from the Alyx REST API.
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

        self.config.raise_if_no_user_selected()

        silent = self.config.silent

        # Check if token cached
        if not force and self.token:
            return self.token

        # Else, if force or token does not exists : use or get password
        if password is None:
            if silent:
                raise ValueError(
                    "Cannot ask for password if the client is in silent mode"
                )
            password = self.config.ask_password()

        try:
            rep = requests.post(
                self.config.url + "/api/auth-token",
                data={"username": self.config.username, "password": password},
            )
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Can't connect to {self.config.url}\n"
                "Check your internet connections and Alyx database firewall"
            )
        # Assign token or raise exception on auth error
        if rep.ok:
            token = rep.json().get("token")
        else:
            if rep.status_code == 400:  # Auth error raised with details
                obfuscated = obfuscate(password)
                message = (
                    "Alyx authentication failed with credentials: "
                    f"user = {self.config.username}, password = {obfuscated}"
                )
                raise requests.HTTPError(
                    rep.status_code, rep.url, message, response=rep
                )
            else:
                rep.raise_for_status()
            raise RuntimeError("Unidentified error while trying to authenticate")

        if cache_token:
            self.token = token

        logger.info(f"Connected to {self.config.url} as {self.config.username}")

        return token

    def is_authenticated(self) -> bool:
        return True if self.token else False

    def raise_if_not_authenticated(self):
        if not self.is_authenticated():
            raise ConnectionError(
                "Please authenticate your connection with .authenticate()"
            )

    def logout(self, *args, **kwargs):
        # set the config and the authenticator's token to None via
        # @token.setter for token
        self.token = None
