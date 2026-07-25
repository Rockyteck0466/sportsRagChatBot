import ipaddress
import socket
from urllib.parse import urlparse, urlunparse


class UnsafeUrl(ValueError):
    pass


def normalize_approved_url(raw_url: str, allowed_hosts: frozenset[str]) -> str:
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or host not in allowed_hosts:
        raise UnsafeUrl("Only HTTPS pages on the approved NBA.com host are allowed.")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise UnsafeUrl("Credentials and custom ports are not allowed.")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise UnsafeUrl("The approved host could not be resolved.") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise UnsafeUrl("The URL resolved to a non-public network.")
    clean_path = parsed.path or "/"
    return urlunparse(("https", host, clean_path, "", parsed.query, ""))
