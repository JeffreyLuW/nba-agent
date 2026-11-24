"""Test setup shared by all MCP server tests.

Concept: **mocking**. Real NBA.com calls are slow, rate limited, and do not work
in CI. So tests replace ("mock") the nba_api classes with fakes that return
fixed data. Then a test is fast and gives the same result every time.
"""

import socket

import cache
import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Safety net: any real network call in a test fails loudly."""

    def blocked(*args, **kwargs):
        raise RuntimeError("Tests must not use the network. Mock the call instead.")

    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    """Every test starts with an empty cache and no throttle delay."""
    cache.clear_all_caches()
    monkeypatch.setattr(cache, "MIN_SECONDS_BETWEEN_CALLS", 0)
    yield
    cache.clear_all_caches()
