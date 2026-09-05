"""Outbound request guard — ATOS-P2-CI-001.

Invariant:

    Every outbound HTTP request this system makes goes to an https URL, and
    a URL that is not https does not get opened.

``urllib.request.urlopen`` will happily open ``file:///etc/passwd`` and
``ftp://``. That is fine when every URL in the process is a literal in the
source, and stops being fine the moment one comes from configuration, an RSS
feed's redirect, or a webhook address an operator pasted in — all three of
which exist here. The cost of checking is a string comparison; the cost of not
checking is a file read or a plaintext request carrying an API key.

Kept deliberately small. It is a scheme check, not a URL allowlist: an
allowlist that has to be edited every time a feed is added gets disabled.
"""

from __future__ import annotations

import urllib.request
from typing import Union

#: The only scheme this system opens. http is excluded rather than warned
#: about: a plaintext request carrying an API key is not a lesser problem.
PERMITTED_SCHEMES = ("https://",)


class BlockedRequest(ValueError):
    """A request to a scheme this system does not open."""


def request_url(target: Union[str, urllib.request.Request]) -> str:
    """The URL of a string or a prepared Request."""
    if isinstance(target, urllib.request.Request):
        return target.full_url
    return str(target)


def assert_permitted(target: Union[str, urllib.request.Request]) -> str:
    """Raise unless this URL is one we are willing to open."""
    url = request_url(target)
    if not url.lower().startswith(PERMITTED_SCHEMES):
        raise BlockedRequest(
            f"refusing to open {url.split('://', 1)[0]!r} URL; only https is "
            f"permitted for outbound requests"
        )
    return url
