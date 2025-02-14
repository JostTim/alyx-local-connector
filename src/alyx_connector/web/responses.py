import math
import urllib, requests
from logging import getLogger
from collections.abc import Mapping

from .urls import UrlValidator

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .clients import Client


logger = getLogger("alyx_conntector.web.responses")


class ResponseManager:

    def __init__(self, client: Client):
        self.client = client

    def __getitem__(self, value):
        return getattr(self, value)

    def list(self, json_response: dict, original_url: str, **kwargs):

        if isinstance(json_response, dict) and list(json_response.keys()) == [
            "count",
            "next",
            "previous",
            "results",
        ]:
            if len(json_response["results"]) < json_response["count"]:
                cache_args = {k: v for k, v in kwargs.items() if k in ("clobber", "expires")}
                logger.debug(f"Creating a paginated response for a large Alyx request. Query : {original_url}")
                json_response = _PaginatedResponse(self, json_response, cache_args, callbacks=self.fix_url)
                logger.debug(
                    f"Paginated response total size is : {json_response.count}. "
                    f"Limit size is : {json_response.limit}. Query is : {json_response.query}"
                )
            else:
                json_response = json_response["results"]
        return self.fix_url(json_response)

    def fix_url(self, response_data, fixed_keys=["admin_url"], do_fix=False):

        if isinstance(response_data, list):
            return [self.fix_url(item, do_fix=False) for item in response_data]
        elif isinstance(response_data, dict):
            return {
                key: (self.fix_url(item, do_fix=True) if key in fixed_keys else self.fix_url(item, do_fix=False))
                for key, item in response_data.items()
            }
        elif isinstance(response_data, _PaginatedResponse):
            response_data.add_finishing_callback(self.fix_url)
            return response_data
        elif do_fix:
            baseurl = UrlValidator.urlsplit(self.client.url)
            return UrlValidator.urlsplit(response_data)._replace(scheme=baseurl.scheme, netloc=baseurl.netloc).geturl()
        else:
            return response_data


class _PaginatedResponse(Mapping):
    """
    This class allows to emulate a list from a paginated response.
    Provides cache functionality.

    Examples
    --------
    >>> r = _PaginatedResponse(client, response)
    """

    def __init__(self, alyx, rep, cache_args=None, callbacks=[]):
        """
        A paginated response cache object

        Parameters
        ----------
        alyx : AlyxClient
            An instance of an AlyxClient associated with the REST response
        rep : dict
            A paginated REST response JSON dictionary
        cache_args : dict
            A dict of kwargs to pass to _cache_response decorator upon subsequent requests
        """

        self.alyx = alyx
        self.base_url = self.alyx.base_url
        self.count = rep["count"]
        self.limit = len(rep["results"])
        self._cache_args = cache_args or {}
        # store URL without pagination query params
        self.query = rep["next"]
        # init the cache, list with None with count size
        self._cache = [None] * self.count

        if not isinstance(callbacks, list):
            callbacks = [callbacks]

        self.finishing_callbacks = callbacks

        # fill the cache with results of the query
        self.store_results(rep, 0)

    def __len__(self):
        return self.count

    def __getitem__(self, item):
        """Returns an item from cache if the indices locations are already loaded,
        or populates the cache chunk by chunk if some locations are empty.
        """
        if isinstance(item, slice):
            while None in self._cache[item]:
                # .index(None) finds the first location where the cache is none,
                # and populate stores a new chunk starting from here
                self.populate(self._cache[item].index(None))
        elif item > self.count:
            raise IndexError(
                f"The paginated response has no item at position {item} : " f"it contains only {self.count} items"
            )
        elif self._cache[item] is None:
            self.populate(item)
        return self._cache[item]

    def populate(self, idx):
        """Populate a chunk of size self.limit, from an offset query depending on the value of the index required."""
        offset = self.limit * math.floor(idx / self.limit)
        query = update_url_params(self.query, {"limit": self.limit, "offset": offset})
        res = self.alyx._generic_request(requests.get, query, **self._cache_args)
        if self.count != res["count"]:
            logger.warning(
                f"remote results for {UrlValidator.urlsplit(query).path} endpoint changed; results may be inconsistent",
                RuntimeWarning,
            )
        self.store_results(res, offset)

    def store_results(self, res, offset):
        for i, r in enumerate(res["results"][: self.count - offset]):
            self._cache[i + offset] = self.finish_result(r)

    def finish_result(self, result):
        """Method that should be overridden in child classes"""
        for callback in self.finishing_callbacks:
            result = callback(result)
        return result

    def add_finishing_callback(self, callback):
        if callback not in self.finishing_callbacks:
            self.finishing_callbacks.append(callback)

    def __iter__(self):
        for i in range(self.count):
            try:
                yield self.__getitem__(i)
            except requests.HTTPError as e:
                logger.error(
                    f"{e.response.status_code} error while trying to access " f"PaginatedResponse data at position {i}"
                )
                raise e


def update_url_params(url: str, params: dict) -> str:
    """Add/update the query parameters of a URL and make url safe

    Parameters
    ----------
    url : str
        A URL string with which to update the query parameters
    params : dict
        A dict of new parameters.  For multiple values for the same query, use a list (see example)

    Returns
    -------
    str
        A new URL with said parameters updated

    Examples
    -------
    >>> update_url_params('website.com/?q=', {'pg': 5})
    'website.com/?pg=5'

    >>> update_url_params('website.com?q=xxx', {'pg': 5, 'foo': ['bar', 'baz']})
    'website.com?q=xxx&pg=5&foo=bar&foo=baz'
    """
    # Remove percent-encoding
    url = urllib.parse.unquote(url)
    parsed_url = urllib.parse.urlsplit(url)
    # Extract URL query arguments and convert to dict
    parsed_get_args = urllib.parse.parse_qs(parsed_url.query, keep_blank_values=False)
    # Merge URL arguments dict with new params
    parsed_get_args.update(params)
    # Convert back to query string
    encoded_get_args = urllib.parse.urlencode(parsed_get_args, doseq=True)
    # Update parser and convert to full URL str
    return parsed_url._replace(query=encoded_get_args).geturl()
