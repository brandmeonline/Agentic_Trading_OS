"""Pytest plugin that forbids outbound sockets — ATOS-P2-CI-001.

The requirement is that core safety tests do not depend on a real network. A
test that quietly reaches the internet passes on a developer's machine, passes
in CI while the venue is up, and fails at 3am for reasons that have nothing to
do with the change under test. Worse, a "safety" test that only proves the
system is safe *when a remote host answers* has not proved much.

Stating the rule is not enough, because the dependency creeps in by accident —
a new import that pings on construction, a fixture that fetches a symbol list.
So the rule is enforced: with this plugin loaded, creating a socket raises, and
any test that needed one fails loudly and locally.

Loopback is left alone. Some tests bind a local server on 127.0.0.1 to check
the bind policy itself, and that is not a network dependency.

Usage:

    PYTHONPATH="$PWD" python -m pytest -p no_network -m adversarial "Alpha IO/tests"
"""

from __future__ import annotations

import socket

_LOOPBACK = ("127.0.0.1", "::1", "localhost", "")


class NetworkAccessBlocked(RuntimeError):
    """A test tried to open a socket to somewhere other than loopback."""


_real_socket = socket.socket


class _GuardedSocket(_real_socket):  # type: ignore[misc,valid-type]
    def connect(self, address):  # type: ignore[override]
        _check(address)
        return super().connect(address)

    def connect_ex(self, address):  # type: ignore[override]
        _check(address)
        return super().connect_ex(address)


def _check(address) -> None:
    host = address[0] if isinstance(address, tuple) and address else address
    if isinstance(host, bytes):
        host = host.decode("utf-8", "replace")
    if str(host) not in _LOOPBACK:
        raise NetworkAccessBlocked(
            f"this test tried to reach {host!r}. Core safety tests must not "
            "depend on a real network; use a fake or a fixture instead."
        )


def pytest_configure(config) -> None:
    socket.socket = _GuardedSocket
    # getaddrinfo is where a hostname turns into an address, so blocking it
    # catches the resolution attempt before a connection is even shaped.
    real_getaddrinfo = socket.getaddrinfo

    def guarded_getaddrinfo(host, *args, **kwargs):
        if str(host) not in _LOOPBACK:
            raise NetworkAccessBlocked(
                f"this test tried to resolve {host!r}. Core safety tests must "
                "not depend on a real network."
            )
        return real_getaddrinfo(host, *args, **kwargs)

    socket.getaddrinfo = guarded_getaddrinfo
