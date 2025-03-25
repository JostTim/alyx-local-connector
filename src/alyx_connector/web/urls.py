from urllib.parse import urlparse, urlunparse, urlsplit, parse_qs
from typing import Optional

from logging import getLogger

logger = getLogger("alyx_connector.urls")


class UrlValidator:

    @staticmethod
    def urlunparse(
        protocol="http",
        netloc="127.0.0.1:80",
        path="",
        params="",
        query_string="",
        fragment="",
    ):
        return urlunparse(
            (
                protocol,  # example : "http" or "https"
                netloc,  # netloc = host+port, example : "127.0.0.1:80"
                path,  # path to your endoint, example : "/api/sessions"
                params,  # params
                query_string,  # querystring example : "?thing=truc"
                fragment,  # basically an anchor, fragment example : "#title1"
            )
        )

    @staticmethod
    def build_url(protocol, host, port, original_url: Optional[str] = None):
        url = UrlValidator.urlunparse(*(protocol, f"{host}:{port}", "", "", "", ""))
        if url == "":
            raise ValueError(f"Cound not parse the url {original_url}. Verify it is correct")
        return url

    @staticmethod
    def validate_url_components(input_url: str):
        """Validate and correct a given URL.

        This method checks if the input URL starts with 'http:' or 'https:'.
        If not, it attempts to correct the URL by prepending 'http://' and
        assumes the protocol is HTTP. It also ensures that a port is specified;
        if missing, it defaults to port 80.

        Args:
            input_url (str): The URL to be validated and corrected.

        Returns:
            tuple: A tuple containing:
                - protocol (str): The protocol of the validated URL.
                - host (str): The host of the validated URL.
                - port (str): The port of the validated URL.
                - validated_url (str): The corrected and validated URL.
        """

        if not input_url.startswith(("http:", "https:")):
            scheme_and_netloc = input_url.split("//")
            if len(scheme_and_netloc) == 1:
                url = "http://" + scheme_and_netloc[0]
            else:
                url = "http://" + scheme_and_netloc[1]
            logger.debug(f"corrected invalid url {input_url} into {url} asuming http protocol")
        else:
            url = input_url

        parsed_url = urlparse(url)
        protocol = parsed_url.scheme
        host_and_port = parsed_url.netloc.split(":")
        if len(host_and_port) == 2:
            port = host_and_port[1]
            host = host_and_port[0]
        else:
            port = "80"
            host = host_and_port[0]
            logger.debug(f"corrected url {input_url} missing port info into port 80 asuming http protocol is used")

        validated_url = UrlValidator.build_url(protocol, host, port, original_url=input_url)

        return protocol, host, port, validated_url

    @staticmethod
    def validate_url(input_url: str):
        return UrlValidator.validate_url_components(input_url)[3]

    @staticmethod
    def urlsplit(url: str):
        return urlsplit(url)

    @staticmethod
    def get_limit_offset(url: str) -> dict:
        parsed_url = urlparse(url)
        params = {k: v[0] for k, v in parse_qs(parsed_url.query).items() if k in ("limit", "offset")}
        return params
