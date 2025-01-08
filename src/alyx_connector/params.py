"""Functions for modifying, loading and saving ONE and Alyx database parameters.

Scenarios:

    - Load ONE with a cache dir: tries to load the Web client params from the dir
    - Load ONE with http address - gets cache dir from the URL map

The ONE params comprise two files: a caches file that contains a map of Alyx db URLs to cache
directories, and a separate parameter file for each url containing the client parameters.  The
caches file also sets the default client for when no url is provided.
"""

import re
import shutil

from getpass import getpass
from pathlib import Path, PurePath
from urllib.parse import urlsplit
import unicodedata

from datetime import datetime
import collections
import sys
import os
import json
import subprocess
import logging
import time
import socket
import asyncio
from math import inf

_PAR_ID_STR = "one"
_CLIENT_ID_STR = "caches"
CACHE_DIR_DEFAULT = str(Path.home() / "Downloads" / "ONE")
"""str: The default database location"""
LOCAL_ROOT_DIR_DEFAULT = Path(CACHE_DIR_DEFAULT) / "LOCAL_DATA"
"""str: The default rawdata download location"""


def default():
    """Default Web client parameters"""
    par = {
        "ALYX_URL": "http://127.0.0.0:80",
        "ALYX_LOGIN": "guest",
        "HTTP_DATA_SERVER": "--unused",
        "HTTP_DATA_SERVER_LOGIN": "--unused",
        "HTTP_DATA_SERVER_PWD": "--unused",
        "LOCAL_ROOT": LOCAL_ROOT_DIR_DEFAULT,
    }
    return from_dict(par)


def _get_current_par(k, par_current):
    """
    Return the current parameter value or the default.

    Parameters
    ----------
    k : str
        The parameter key lookup
    par_current : IBLParams
        The current parameter set

    Returns
    -------
    any
        The current parameter value or default if None or not set
    """
    cpar = getattr(par_current, k, None)
    if cpar is None:
        cpar = getattr(default(), k, None)
    return cpar


def _key_from_url(url: str) -> str:
    """
     Convert a URL str to one valid for use as a file name or dict key.  URL Protocols are
     removed entirely.  The returned string will have characters in the set [a-zA-Z.-_].

     Parameters
     ----------
     url : str
         A URL string

     Returns
     -------
     str
         A filename-safe string

     Example
     -------
     >>> url = _key_from_url('http://test.alyx.internationalbrainlab.org/')
    'test.alyx.internationalbrainlab.org'
    """
    url = unicodedata.normalize("NFKC", url)  # Ensure ASCII
    url = re.sub("^https?://", "", url).strip("/")  # Remove protocol and trialing slashes
    url = re.sub(r"[^.\w\s-]", "_", url.lower())  # Convert non word chars to underscore
    return re.sub(r"[-\s]+", "-", url)  # Convert spaces to hyphens


def setup(client=None, silent=False, make_default=None, username=None):
    """
    Set up ONE parameters.  If a client (i.e. Alyx database URL) is provided, settings for
    that instance will be set.  If silent, the user will be prompted to input each parameter
    value.  Pressing return will use either current parameter or the default.

    Parameters
    ----------
    client : str
        An Alyx database URL. If None, the user will be prompted to input one.
    silent : bool
        If True, user is not prompted for any input.
    make_default : bool
        If True, client is set as the default and will be returned when calling `get` with no
        arguments.
    username : str, optional
        If present,

    Returns
    -------
    IBLParams
        An updated cache map.
    """
    # First get default parameters
    par_default = default()
    client_key = _key_from_url(client or par_default.ALYX_URL)

    # If a client URL has been provided, set it as the default URL
    par_default = par_default.set("ALYX_URL", client or par_default.ALYX_URL)
    par_current = read(f"{_PAR_ID_STR}/{client_key}", par_default)
    if username:
        par_current = par_current.set("ALYX_LOGIN", username)

    # Load the db URL map
    cache_map = read(f"{_PAR_ID_STR}/{_CLIENT_ID_STR}", {"CLIENT_MAP": dict()})
    cache_dir = cache_map.CLIENT_MAP.get(client_key, Path(CACHE_DIR_DEFAULT, client_key))

    if not silent:
        par = as_dict(par_default)
        for k in par.keys():
            cpar = _get_current_par(k, par_current)
            # Prompt for database URL; skip if client url already provided

            # We do not use an HTTP data server but smb file exchange. All HTTP_DATA_SERVER params are set to --unused
            if "HTTP_DATA_SERVER" in k:
                par[k] = "--unused"
                continue

            if k == "ALYX_URL":
                if not client:
                    par[k] = input(f'Param {k}, current value is ["{str(cpar)}"]:') or cpar
                    if "://" not in par[k]:
                        par[k] = "https://" + par[k]
                    url_parsed = urlsplit(par[k])
                    if not (url_parsed.netloc and re.match("https?", url_parsed.scheme)):
                        raise ValueError(f"{k} must be valid HTTP URL")
                    client = par[k]
            # Iterate through other non-password pars
            elif "PWD" not in k:
                par[k] = input(f'Param {k}, current value is ["{str(cpar)}"]:') or cpar

        # REMOVED : we do not use an HTTP data server but smb file exchange.
        # cpar = _get_current_par('HTTP_DATA_SERVER_PWD', par_current)
        # prompt = f'Enter the FlatIron HTTP password for {par["HTTP_DATA_SERVER_LOGIN"]} '\
        #         '(leave empty to keep current): '
        # par['HTTP_DATA_SERVER_PWD'] = getpass(prompt) or cpar
        prompt = ""
        if "ALYX_PWD" in par_current.as_dict():
            # Only store plain text password if user manually added it to params JSON file
            cpar = _get_current_par("ALYX_PWD", par_current)
            prompt = f'Enter the Alyx password for {par["ALYX_LOGIN"]} (leave empty to keep current):'
            par["ALYX_PWD"] = getpass(prompt) or cpar

        # create the LOCAL_ROOT directory if it does not exist
        Path(par["LOCAL_ROOT"]).mkdir(exist_ok=True, parents=True)

        par = from_dict(par)

        # Prompt for cache directory
        client_key = _key_from_url(par.ALYX_URL)
        cache_dir = Path(CACHE_DIR_DEFAULT, client_key)
        answer = input("Would you like to keep the default database cache location ? [Y/n]")
        if (answer or "y")[0].lower() == "n":
            prompt = f'Enter the location of the database cache, current value is ["{cache_dir}"]:'
            cache_dir = input(prompt) or cache_dir

        # Check if directory already used by another instance
        in_use = [v for k, v in cache_map.CLIENT_MAP.items() if k != client_key]
        while str(cache_dir) in in_use:
            answer = input(
                "Warning: the directory provided is already a cache for another URL.  "
                "This may cause conflicts.  Would you like to change the cache location? [Y/n]"
            )
            if answer and answer[0].lower() == "n":
                break
            cache_dir = input(prompt) or cache_dir  # Prompt for another directory

        if make_default is None:
            answer = input("Would you like to set this URL as the default one? [Y/n]")
            make_default = (answer or "y")[0].lower() == "y"

        # Verify setup pars
        answer = input("Are the above settings correct? [Y/n]")
        if answer and answer.lower()[0] == "n":
            print("SETUP ABANDONED.  Please re-run.")
            return par_current
    else:
        par = par_current

    # Update and save parameters
    Path(cache_dir).mkdir(exist_ok=True, parents=True)
    rest_dir = Path(cache_dir).joinpath(".rest")
    rest_dir.mkdir(exist_ok=True, parents=True)
    from iblutil.io.params import set_hidden

    set_hidden(rest_dir, True)

    cache_map.CLIENT_MAP[client_key] = str(cache_dir)
    if make_default or "DEFAULT" not in cache_map.as_dict():
        cache_map = cache_map.set("DEFAULT", client_key)

    write(f"{_PAR_ID_STR}/{client_key}", par)  # Client params
    write(f"{_PAR_ID_STR}/{_CLIENT_ID_STR}", cache_map)

    if not silent:
        print("ONE Parameter files location: " + getfile(_PAR_ID_STR))

    return cache_map


def get(client=None, silent=False, username=None):
    """Returns the AlyxClient parameters

    Parameters
    ----------
    silent : bool
        If true, defaults are chosen if no parameters found.
    client : str
        The database URL to retrieve parameters for.  If None, the default is loaded.
    username : str
        The username to use.  If None, the default is loaded.

    Returns
    -------
    IBLParams
        A Params object for the AlyxClient.
    """
    client_key = _key_from_url(client) if client else None
    cache_map = read(f"{_PAR_ID_STR}/{_CLIENT_ID_STR}", {})
    # If there are no params for this client, run setup routine
    if not cache_map or (client_key and client_key not in cache_map.CLIENT_MAP):
        cache_map = setup(client=client, silent=silent, username=username)
    cache = cache_map.CLIENT_MAP[client_key or cache_map.DEFAULT]
    pars = read(f"{_PAR_ID_STR}/{client_key or cache_map.DEFAULT}").set("CACHE_DIR", cache)
    if username:
        pars = pars.set("ALYX_LOGIN", username)
    return _patch_params(pars)


def get_default_client(include_schema=True) -> str:
    """Returns the default AlyxClient URL, or None if no default is set

    Parameters
    ----------
    include_schema : bool
        When True, the URL schema is included (i.e. http(s)://).  Set to False to return the URL
        as a client key.

    Returns
    -------
    str
        The default database URL with or without the schema, or None if no default is set
    """
    cache_map = as_dict(read(f"{_PAR_ID_STR}/{_CLIENT_ID_STR}", {})) or {}
    client_key = cache_map.get("DEFAULT", None)
    if not client_key or include_schema is False:
        return client_key
    return get(client_key).ALYX_URL


def save(par, client):
    """
    Save a set of parameters for a given client.

    Parameters
    ----------
    par : dict, IBLParams
        A set of Web client parameters to save
    client : str
        The Alyx URL that corresponds to these parameters
    """
    # Remove cache dir variable before saving
    par = {k: v for k, v in as_dict(par).items() if "CACHE_DIR" not in k}
    write(f"{_PAR_ID_STR}/{_key_from_url(client)}", par)


def get_cache_dir(client=None) -> Path:
    """Return the download directory for a given client.

    If no client is set up, the default download location is returned.

    Parameters
    ----------
    client : str
        The client to return cache dir from.  If None, the default client is used.

    Returns
    -------
    pathlib.Path
        The download cache path
    """
    cache_map = read(f"{_PAR_ID_STR}/{_CLIENT_ID_STR}", {})
    client = _key_from_url(client) if client else cache_map.DEFAULT
    cache_dir = Path(cache_map.CLIENT_MAP[client] if cache_map else CACHE_DIR_DEFAULT)
    cache_dir.mkdir(exist_ok=True, parents=True)
    # cache_dir.joinpath(".rest").mkdir(exist_ok=True, parents=True)

    return cache_dir


def get_params_dir() -> Path:
    """Return the path to the root ONE parameters directory

    Returns
    -------
    pathlib.Path
        The root ONE parameters directory
    """
    return Path(getfile(_PAR_ID_STR))


def check_cache_conflict(cache_dir):
    """Asserts that a given directory is not currently used as a cache directory.
    This function checks whether a given directory is used as a cache directory for an Alyx
    Web client.  This function is called by the ONE factory to determine whether to return an
    OneAlyx object or not.  It is also used when setting up params for a new client.

    Parameters
    ----------
    cache_dir : str, pathlib.Path
        A directory to check.

    Raises
    ------
    AssertionError
        The directory is set as a cache for a Web client
    """
    cache_map = getattr(read(f"{_PAR_ID_STR}/{_CLIENT_ID_STR}", {}), "CLIENT_MAP", None)
    if cache_map:
        assert not any(x == str(cache_dir) for x in cache_map.values())


def _patch_params(par):
    """
    Patch previous version of parameters, if required.

    Parameters
    ----------
    par : IBLParams
        The old parameters object

    Returns
    -------
    IBLParams
        New parameters object containing the previous parameters

    """
    # Patch the URL of data server, if database is OpenAlyx.
    # The data location is in /public, however this path is no longer in the cache table
    if "openalyx" in par.ALYX_URL and "public" not in par.HTTP_DATA_SERVER:
        par = par.set("HTTP_DATA_SERVER", default().HTTP_DATA_SERVER)
        save(par, par.ALYX_URL)

    # Move old REST data
    rest_dir = get_params_dir() / ".rest"
    scheme, loc, *_ = urlsplit(par.ALYX_URL)
    rest_dir /= Path(loc.replace(":", "_"), scheme)
    new_rest_dir = Path(par.CACHE_DIR, ".rest")

    if rest_dir.exists() and any(x for x in rest_dir.glob("*") if x.is_file()):
        if not new_rest_dir.exists():
            shutil.move(str(rest_dir), str(new_rest_dir))
            from iblutil.io.params import set_hidden

            set_hidden(new_rest_dir, True)
        shutil.rmtree(rest_dir.parent)
        if not any(get_params_dir().joinpath(".rest").glob("*")):
            get_params_dir().joinpath(".rest").rmdir()

    return par


def as_dict(par) -> dict:
    if not par or isinstance(par, dict):
        return par
    else:
        return dict(par._asdict())


def from_dict(par_dict):
    if not par_dict:
        return None
    par = collections.namedtuple("Params", par_dict.keys())

    class IBLParams(par):
        __slots__ = ()

        def set(self, field, value):
            d = as_dict(self)
            d[field] = value
            return from_dict(d)

        def as_dict(self):
            return as_dict(self)

    return IBLParams(**par_dict)


def getfile(str_params):
    """
    Returns full path of the param file per system convention:
     linux/mac: ~/.str_params, Windows: APPDATA folder

    :param str_params: string that identifies parm file
    :return: string of full path
    """
    # strips already existing dot if any
    parts = ["." + p if not p.startswith(".") else p for p in Path(str_params).parts]
    if sys.platform == "win32" or sys.platform == "cygwin":
        pfile = str(PurePath(os.environ["APPDATA"], *parts))
    else:
        pfile = str(Path.home().joinpath(*parts))
    return pfile


def set_hidden(path, hide: bool) -> Path:
    """
    Set a given file or folder path to be hidden.  On macOS and Windows a specific flag is set,
    while on other systems the file or folder is simply renamed to start with a dot.  On macOS the
    folder may only be hidden in Explorer.

    Parameters
    ----------
    path : str, pathlib.Path
        The path of the file or folder to (un)hide.
    hide : bool
        If True the path is set to hidden, otherwise it is unhidden.

    Returns
    -------
    pathlib.Path
        The path of the file or folder, which may have been renamed.
    """
    path = Path(path)
    assert path.exists()
    if sys.platform == "win32" or sys.platform == "cygwin":
        flag = ("+" if hide else "-") + "H"
        subprocess.run(["attrib", flag, str(path)]).check_returncode()
    elif sys.platform == "darwin":
        flag = ("" if hide else "no") + "hidden"
        subprocess.run(["chflags", flag, str(path)]).check_returncode()
    elif hide and not path.name.startswith("."):
        path = path.rename(path.parent.joinpath("." + path.name))
    elif not hide and path.name.startswith("."):
        path = path.rename(path.parent.joinpath(path.name[1:]))
    return path


def read(str_params, default=None):
    """
    Reads in and parse Json parameter file into dictionary.  If the parameter file doesn't
    exist and no defaults are provided, a FileNotFound error is raised, otherwise any extra
    default parameters will be written into the file.

    Examples:
        # Load parameters, raise error if file not found
        par = read('globus/admin')

        # Load with defaults
        par = read('globus/admin', {'local_endpoint': None, 'remote_endpoint': None})

        # Return empty dict if file not found (i.e. touch new param file)
        par = read('new_pars', {})

    :param str_params: path to text json file
    :param default: default values for missing parameters
    :return: named tuple containing parameters
    """
    pfile = getfile(str_params)
    par_dict = as_dict(default) or {}
    if Path(pfile).exists():
        with open(pfile) as fil:
            file_pars = json.loads(fil.read())
        par_dict.update(file_pars)
    elif default is None:  # No defaults provided
        raise FileNotFoundError(f"Parameter file {pfile} not found")

    if not Path(pfile).exists() or par_dict.keys() > file_pars.keys():
        # write the new parameter file with the extra param
        write(str_params, par_dict)
    return from_dict(par_dict)


def write(str_params, par):
    """
    Write a parameter file in Json format

    :param str_params: path to text json file
    :param par: dictionary containing parameters values
    :return: None
    """
    pfile = Path(getfile(str_params))
    if not pfile.parent.exists():
        pfile.parent.mkdir()
    dpar = as_dict(par)
    for k in dpar:
        if isinstance(dpar[k], Path):
            dpar[k] = str(dpar[k])
    with open(pfile, "w") as fil:
        json.dump(as_dict(par), fil, sort_keys=False, indent=4)


class FileLock:
    def __init__(self, filename, log=None, timeout=10, timeout_action="delete"):
        """
        A context manager to ensure a file is not written to.

        This context manager checks whether a lock file already exists, indicating that the
        filename is currently being written to by another process, and waits until it is free
        before entering.  If the lock file is not removed within the timeout period, it is either
        forcebly removed (assumes other process hanging or killed), or raises an exception.

        Before entering, a new lock file is created, containing the hostname, datetime and pid,
        then subsequenctly removed upon exit.

        Parameters
        ----------
        filename : pathlib.Path, str
            A filepath to 'lock'.
        log : logging.Logger
            A logger instance to use.
        timeout : float
            How long to wait before either raising an exception or deleting the previous lock file.
        timeout_action : {'delete', 'raise'} str
            Action to take if previous lock file remains throughout timeout period. Either delete
            the old lock file or raise an exception.

        Examples
        --------
        Ensure a file is not being written to by another process before writing

        >>> with FileLock(filename, timeout_action='delete'):
        >>>     with open(filename, 'w') as fp:
        >>>         fp.write(r'{"foo": "bar"}')

        Asychronous implementation example with raise behaviour

        >>> try:
        >>>     async with FileLock(filename, timeout_action='raise'):
        >>>         with open(filename, 'w') as fp:
        >>>             fp.write(r'{"foo": "bar"}')
        >>> except asyncio.TimeoutError:
        >>>     print(f'failed to write to {filename}')
        """
        self.filename = Path(filename)
        self._logger = log or __name__
        if not isinstance(log, logging.Logger):
            self._logger = logging.getLogger(self._logger)

        self.timeout = timeout
        self.timeout_action = timeout_action
        if self.timeout_action not in ("delete", "raise"):
            raise ValueError(f"Invalid timeout action: {self.timeout_action}")
        self._async_poll_freq = 0.2  # how long to sleep between lock file checks in async mode

    @property
    def lockfile(self):
        """pathlib.Path: the lock filepath."""
        return self.filename.with_suffix(".lock")

    async def _lock_check_async(self):
        while self.lockfile.exists():
            assert self._async_poll_freq > 0
            await asyncio.sleep(self._async_poll_freq)

    def __enter__(self):
        # if a lock file exists retries n times to see if it exists
        attempts = 0
        n_attempts = 5 if self.timeout else inf
        timeout = (self.timeout / n_attempts) if self.timeout else self._poll_freq

        while self.lockfile.exists() and attempts < n_attempts:
            self._logger.info("file lock found, waiting %.2f seconds %s", timeout, self.lockfile)
            time.sleep(timeout)
            attempts += 1

        # if the file still exists after 5 attempts, remove it as it's a job that went wrong
        if self.lockfile.exists():
            with open(self.lockfile, "r") as fp:
                _contents = json.load(fp) if self.lockfile.stat().st_size else "<empty>"
                self._logger.debug("file lock contents: %s", _contents)
            if self.timeout_action == "delete":
                self._logger.info("stale file lock found, deleting %s", self.lockfile)
                self.lockfile.unlink()
            else:
                raise TimeoutError(f"{self.lockfile} file lock timed out")

        # add in the lock file, add some metadata to ease debugging if one gets stuck
        with open(self.lockfile, "w") as fp:
            json.dump(dict(datetime=datetime.utcnow().isoformat(), hostname=str(socket.gethostname)), fp)

    async def __aenter__(self):
        # if a lock file exists wait until timeout before removing
        try:
            await asyncio.wait_for(self._lock_check_async(), timeout=self.timeout)  # py3.11 use with asyncio.timeout
        except asyncio.TimeoutError as e:
            with open(self.lockfile, "r") as fp:
                _contents = json.load(fp) if self.lockfile.stat().st_size else "<empty>"
                self._logger.debug("file lock contents: %s", _contents)
            if self.timeout_action == "raise":
                raise e
            self._logger.info("stale file lock found, deleting %s", self.lockfile)
            self.lockfile.unlink()

        # add in the lock file, add some metadata to ease debugging if one gets stuck
        with open(self.lockfile, "w") as fp:
            info = dict(datetime=datetime.utcnow().isoformat(), hostname=str(socket.gethostname), pid=os.getpid())
            json.dump(info, fp)

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.lockfile.unlink()

    async def __aexit__(self, exc_type, exc_value, exc_tb):
        self.lockfile.unlink()
