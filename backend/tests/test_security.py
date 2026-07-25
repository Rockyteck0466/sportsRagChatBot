import socket

import pytest

from app.security import UnsafeUrl, normalize_approved_url


def test_rejects_unapproved_host() -> None:
    with pytest.raises(UnsafeUrl):
        normalize_approved_url("https://example.com/", frozenset({"www.nba.com"}))


def test_rejects_http() -> None:
    with pytest.raises(UnsafeUrl):
        normalize_approved_url("http://www.nba.com/", frozenset({"www.nba.com"}))


def test_accepts_approved_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("151.101.1.55", 443))])
    assert normalize_approved_url(
        "https://www.nba.com/standings#ignored", frozenset({"www.nba.com"})
    ) == "https://www.nba.com/standings"
