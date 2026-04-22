"""URL validation to prevent SSRF attacks.

Single source of truth for blocked networks and hostnames used by both
property list resolution and webhook URL validation.

Operator allowlist
------------------
Two env vars carve narrow exceptions to the private-IP block without
weakening the blocked-hostname / scheme checks:

``SSRF_ALLOWED_HOSTS``
    Comma-separated exact hostnames (case-insensitive). Whitespace-only
    entries are ignored.

``SSRF_ALLOWED_HOST_SUFFIXES``
    Comma-separated DNS suffixes (case-insensitive). Include the leading
    dot to avoid sibling-domain escapes — ``.seller.local`` matches
    ``a.seller.local`` but not ``evilseller.local``.

Both default to empty. A host on the allowlist skips the
BLOCKED_NETWORKS / non-routable-IP check but still goes through scheme
validation and the BLOCKED_HOSTNAMES literal check — cloud-metadata IPs
like ``169.254.169.254`` and literal ``localhost`` cannot be allowlisted
this way.
"""

import ipaddress
import os
import socket
from urllib.parse import urlparse

# Blocked IP ranges (RFC 1918 private networks, loopback, link-local)
BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# Blocked hostnames (cloud metadata services, localhost aliases, Docker-internal hostnames)
BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "169.254.169.254",
    "metadata",
    "instance-data",
    # Docker-internal hostnames that resolve to private/loopback IPs and
    # are not guaranteed to be caught by DNS resolution in all environments
    "host.docker.internal",
    "gateway.docker.internal",
    "docker.host.internal",
}

# Env vars for the operator allowlist (see module docstring).
_ENV_ALLOWED_HOSTS = "SSRF_ALLOWED_HOSTS"
_ENV_ALLOWED_HOST_SUFFIXES = "SSRF_ALLOWED_HOST_SUFFIXES"


def _load_allowlist(env_var: str) -> tuple[str, ...]:
    """Return lowercased, whitespace-stripped, non-empty entries from an env var."""
    raw = os.getenv(env_var, "")
    return tuple(entry.strip().lower() for entry in raw.split(",") if entry.strip())


def _hostname_is_allowlisted(hostname: str) -> bool:
    """True when ``hostname`` matches SSRF_ALLOWED_HOSTS / _HOST_SUFFIXES.

    Env vars are read on every call so operators can adjust the allowlist
    without restarting. SSRF validation is not a hot path.
    """
    host = hostname.lower()
    if host in _load_allowlist(_ENV_ALLOWED_HOSTS):
        return True
    for suffix in _load_allowlist(_ENV_ALLOWED_HOST_SUFFIXES):
        if host.endswith(suffix):
            return True
    return False


def check_url_ssrf(url: str, *, require_https: bool = False) -> tuple[bool, str]:
    """Check a URL for SSRF safety.

    Validates that the URL does not target private/internal networks
    or cloud metadata services.

    Args:
        url: The URL to validate.
        require_https: If True, reject non-HTTPS schemes. If False,
            allow both HTTP and HTTPS.

    Returns:
        (is_safe, error_message) -- is_safe is True if the URL is safe,
        error_message describes the problem if not.
    """
    try:
        parsed = urlparse(url)

        if require_https:
            if parsed.scheme != "https":
                return False, f"URL must use HTTPS scheme, got '{parsed.scheme}'"
        elif parsed.scheme not in ("http", "https"):
            return False, "URL must use http or https protocol"

        hostname = parsed.hostname
        if not hostname:
            return False, "URL must have a valid hostname"

        if hostname.lower() in BLOCKED_HOSTNAMES:
            return False, f"URL hostname '{hostname}' is blocked (internal/private)"

        # Operator allowlist: hosts explicitly whitelisted via env var skip
        # the private-network IP check. The BLOCKED_HOSTNAMES check above
        # still wins, so literal metadata addresses and 'localhost' cannot
        # be allowlisted through here.
        if _hostname_is_allowlisted(hostname):
            return True, ""

        # Resolve EVERY address family the hostname returns (IPv4 + IPv6).
        # `gethostbyname` returns only a single IPv4 address, which would let
        # a hostname with a public IPv4 and a private IPv6 bypass the check.
        try:
            infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return False, f"Cannot resolve hostname: {hostname}"

        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue

            for network in BLOCKED_NETWORKS:
                if ip in network:
                    return False, f"URL resolves to blocked IP range {network} (private/internal network)"

            if (
                ip.is_loopback
                or ip.is_link_local
                or ip.is_private
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                return False, f"URL resolves to non-routable IP address: {ip}"

        return True, ""

    except Exception as e:
        return False, f"Invalid URL: {e}"
