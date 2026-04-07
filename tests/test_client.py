"""Tests for the Trakt API client."""

from logging import getLogger
from types import SimpleNamespace
from typing import Any, cast

import aiohttp
import pytest
from anibridge.utils.types import ProviderLogger

from anibridge.providers.list.trakt.client import TraktClient


class _StubResponse:
    """Minimal aiohttp-like response for testing ``_make_request``."""

    def __init__(
        self,
        *,
        status: int,
        payload: dict[str, Any] | list | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Create a stub response with the given status and optional payload."""
        self.status = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status >= 400 and self.status not in (401, 403, 429):
            raise aiohttp.ClientResponseError(
                request_info=cast(Any, SimpleNamespace(real_url="https://trakt.example")),
                history=(),
                status=self.status,
                message="error",
            )


class _StubSession:
    """Session stub that serves predefined responses in order."""

    def __init__(self, responses: list[Any]) -> None:
        """Queue a list of responses to serve in order."""
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: Any):
        """Pop the next queued response and return it."""
        self.calls.append({"method": method, "url": url, **kwargs})
        next_item = self._responses.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item

    async def close(self) -> None:
        """Mark the session as closed."""
        self.closed = True


@pytest.fixture()
def trakt_client() -> TraktClient:
    return TraktClient(
        logger=cast(ProviderLogger, getLogger("tests.trakt.client")),
        client_id="test-client-id",
        token="test-token",
    )


def test_default_rate_limiter_is_shared_across_clients() -> None:
    first = TraktClient(
        logger=cast(ProviderLogger, getLogger("tests.trakt.client")),
        client_id="client-id",
        token="token",
    )
    second = TraktClient(
        logger=cast(ProviderLogger, getLogger("tests.trakt.client")),
        client_id="client-id",
        token="token",
    )

    assert first.rate_limit is None
    assert second.rate_limit is None
    assert first._request_limiter is second._request_limiter


def test_custom_rate_limiter_is_local_per_client() -> None:
    first = TraktClient(
        logger=cast(ProviderLogger, getLogger("tests.trakt.client")),
        client_id="client-id",
        token="token",
        rate_limit=120,
    )
    second = TraktClient(
        logger=cast(ProviderLogger, getLogger("tests.trakt.client")),
        client_id="client-id",
        token="token",
        rate_limit=120,
    )

    assert first._request_limiter is not second._request_limiter
    assert first._request_limiter.rate == pytest.approx(2.0)
    assert second._request_limiter.rate == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_make_request_handles_204(trakt_client: TraktClient) -> None:
    session = _StubSession(responses=[_StubResponse(status=204)])
    trakt_client._session = cast(aiohttp.ClientSession, session)

    result = await trakt_client._make_request("GET", "/shows/1")

    assert result == {}
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_make_request_fails_fast_on_rate_limit(
    trakt_client: TraktClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _StubSession(
        responses=[
            _StubResponse(status=429, headers={"Retry-After": "0"}),
            _StubResponse(status=200, payload={"ok": True}),
        ]
    )
    trakt_client._session = cast(aiohttp.ClientSession, session)

    sleep_calls = 0

    async def fake_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1

    monkeypatch.setattr(
        "anibridge.providers.list.trakt.client.asyncio.sleep", fake_sleep
    )

    with pytest.raises(aiohttp.ClientError, match="rate limited"):
        await trakt_client._make_request("GET", "/shows/1")

    assert sleep_calls == 0
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_make_request_retries_bad_gateway(
    trakt_client: TraktClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _StubSession(
        responses=[
            _StubResponse(status=502),
            _StubResponse(status=200, payload={"ok": True}),
        ]
    )
    trakt_client._session = cast(aiohttp.ClientSession, session)

    async def fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "anibridge.providers.list.trakt.client.asyncio.sleep", fast_sleep
    )

    result = await trakt_client._make_request("GET", "/shows/1")

    assert result == {"ok": True}
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_make_request_fails_on_unauthorized(
    trakt_client: TraktClient,
) -> None:
    session = _StubSession(
        responses=[_StubResponse(status=401)]
    )
    trakt_client._session = cast(aiohttp.ClientSession, session)

    with pytest.raises(aiohttp.ClientError, match="unauthorized"):
        await trakt_client._make_request("GET", "/shows/1")


@pytest.mark.asyncio
async def test_search_shows_caches_results(
    trakt_client: TraktClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_make_request(
        method: str, path: str, **kwargs: Any
    ) -> Any:
        assert method == "GET"
        assert path == "/search/show"
        return [
            {
                "type": "show",
                "score": 100.0,
                "show": {
                    "title": "Test Show",
                    "year": 2024,
                    "ids": {"trakt": 1, "slug": "test-show"},
                    "aired_episodes": 12,
                },
            }
        ]

    monkeypatch.setattr(trakt_client, "_make_request", fake_make_request)

    results = await trakt_client.search_shows("test", limit=10)

    assert len(results) == 1
    assert results[0].show is not None
    assert results[0].show.title == "Test Show"
    assert 1 in trakt_client._media_cache


@pytest.mark.asyncio
async def test_search_by_id_caches_movie_results(
    trakt_client: TraktClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_make_request(method: str, path: str, **kwargs: Any) -> Any:
        assert method == "GET"
        assert path == "/search/tmdb/321"
        assert kwargs["params"] == {"extended": "full", "type": "movie"}
        return [
            {
                "type": "movie",
                "score": 100.0,
                "movie": {
                    "title": "Test Movie",
                    "year": 2024,
                    "ids": {"trakt": 555, "slug": "test-movie"},
                },
            }
        ]

    monkeypatch.setattr(trakt_client, "_make_request", fake_make_request)

    results = await trakt_client.search_by_id(
        id_type="tmdb",
        external_id="321",
        media_type="movie",
    )

    assert len(results) == 1
    assert results[0].movie is not None
    assert results[0].movie.title == "Test Movie"
    assert 555 in trakt_client._movie_cache


@pytest.mark.asyncio
async def test_initialize_sets_user_and_primes_cache(
    trakt_client: TraktClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_get_settings():
        calls.append("settings")
        from anibridge.providers.list.trakt.models import TraktUser, TraktUserSettings
        return TraktUserSettings(
            user=TraktUser(username="tester", name="Test User")
        )

    async def fake_fetch_watched_shows():
        calls.append("watched_shows")
        return []

    async def fake_fetch_watched_movies():
        calls.append("watched_movies")
        return []

    async def fake_fetch_ratings():
        calls.append("ratings")

    async def fake_fetch_watchlist():
        calls.append("watchlist")

    monkeypatch.setattr(trakt_client, "get_settings", fake_get_settings)
    monkeypatch.setattr(trakt_client, "_fetch_watched_shows", fake_fetch_watched_shows)
    monkeypatch.setattr(
        trakt_client, "_fetch_watched_movies", fake_fetch_watched_movies
    )
    monkeypatch.setattr(trakt_client, "_fetch_ratings", fake_fetch_ratings)
    monkeypatch.setattr(trakt_client, "_fetch_watchlist", fake_fetch_watchlist)

    await trakt_client.initialize()

    assert calls == [
        "settings", "watched_shows", "watched_movies", "ratings", "watchlist"
    ]
    assert trakt_client.user is not None
    assert trakt_client.user.username == "tester"
