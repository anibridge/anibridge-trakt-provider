"""Pytest fixtures shared across the Trakt provider test-suite."""

from collections.abc import AsyncGenerator, Generator
from datetime import UTC
from logging import getLogger
from typing import cast

import pytest
import pytest_asyncio
from anibridge.utils.limiter import Limiter
from anibridge.utils.types import ProviderLogger

from anibridge.providers.list.trakt.list import TraktListProvider
from anibridge.providers.list.trakt.models import (
    TraktIds,
    TraktMovie,
    TraktRating,
    TraktSearchResult,
    TraktShow,
    TraktUser,
    TraktUserSettings,
    TraktWatchedEpisode,
    TraktWatchedMovie,
    TraktWatchedSeason,
    TraktWatchedShow,
    TraktWatchlistItem,
)


class FakeTraktClient:
    """Lightweight Trakt client stub used by tests."""

    def __init__(self) -> None:
        """Set up default user, empty caches, and call trackers."""
        self.user = TraktUser(username="tester", name="Test User")
        self.user_timezone = UTC
        self._list_cache: dict[int, TraktWatchedShow] = {}
        self._movie_list_cache: dict[int, TraktWatchedMovie] = {}
        self._show_cache: dict[int, TraktShow] = {}
        self._movie_cache: dict[int, TraktMovie] = {}
        self._media_cache: dict[int, TraktShow] = {}
        self._rating_cache: dict[int, TraktRating] = {}
        self._watchlist_cache: dict[int, TraktWatchlistItem] = {}
        self.history_calls: list[dict] = []
        self.rating_calls: list[dict] = []
        self.watchlist_calls: list[dict] = []
        self.search_by_id_calls: list[dict] = []
        self.removed_ids: list[int] = []

    async def initialize(self) -> None:
        """No-op initialize for tests."""
        pass

    async def close(self) -> None:
        """No-op close for tests."""
        pass

    def clear_cache(self) -> None:
        """Reset all caches and call trackers."""
        self._list_cache.clear()
        self._movie_list_cache.clear()
        self._show_cache.clear()
        self._movie_cache.clear()
        self._media_cache.clear()
        self._rating_cache.clear()
        self._watchlist_cache.clear()
        self.history_calls.clear()
        self.rating_calls.clear()
        self.watchlist_calls.clear()
        self.search_by_id_calls.clear()
        self.removed_ids.clear()

    async def get_settings(self) -> TraktUserSettings:
        """Return stub user settings."""
        return TraktUserSettings(user=self.user)

    async def get_show(self, trakt_id: int, *, force_refresh: bool = False):
        """Return a cached show by Trakt ID or ``None``."""
        return self._media_cache.get(trakt_id)

    async def search_shows(self, query: str, *, limit: int = 10):
        """Return search results from cached media."""
        results = []
        for show in list(self._media_cache.values())[:limit]:
            results.append(TraktSearchResult(type="show", show=show))
        return results

    async def search_by_id(
        self,
        *,
        id_type: str,
        external_id: str,
        media_type: str | None = None,
    ) -> list[TraktSearchResult]:
        """Return search results for an external ID lookup."""
        self.search_by_id_calls.append(
            {
                "id_type": id_type,
                "external_id": external_id,
                "media_type": media_type,
            }
        )

        if media_type == "movie":
            movie = make_movie(trakt_id=int(external_id), title="Resolved Movie")
            self._movie_cache[movie.ids.trakt or 0] = movie
            return [TraktSearchResult(type="movie", movie=movie)]

        show = make_show(trakt_id=int(external_id), title="Resolved Show")
        self._show_cache[show.ids.trakt or 0] = show
        self._media_cache[show.ids.trakt or 0] = show
        return [TraktSearchResult(type="show", show=show)]

    async def add_to_history(self, trakt_id, *, media_type="show", watched_at=None):
        """Record an add-to-history call."""
        self.history_calls.append({
            "trakt_id": trakt_id,
            "media_type": media_type,
            "watched_at": watched_at,
            "action": "add",
        })
        return {"added": {"shows": 1}}

    async def remove_from_history(self, trakt_id, *, media_type="show"):
        """Record a remove-from-history call."""
        self.removed_ids.append(trakt_id)
        self._list_cache.pop(trakt_id, None)
        self._movie_list_cache.pop(trakt_id, None)
        return {"deleted": {"shows": 1}}

    async def rate_media(
        self, trakt_id, rating, *, media_type="show", rated_at=None
    ):
        """Record a rate-media call."""
        self.rating_calls.append({
            "trakt_id": trakt_id,
            "rating": rating,
            "media_type": media_type,
        })
        return {"added": {"shows": 1}}

    async def remove_rating(self, trakt_id, *, media_type="show"):
        """Remove rating from cache."""
        self._rating_cache.pop(trakt_id, None)
        return {"deleted": {"shows": 1}}

    async def add_to_watchlist(self, trakt_id, *, media_type="show"):
        """Record an add-to-watchlist call."""
        self.watchlist_calls.append({
            "trakt_id": trakt_id,
            "media_type": media_type,
            "action": "add",
        })
        return {"added": {"shows": 1}}

    async def remove_from_watchlist(self, trakt_id, *, media_type="show"):
        """Remove an item from the watchlist cache."""
        self._watchlist_cache.pop(trakt_id, None)
        return {"deleted": {"shows": 1}}


def make_show(
    trakt_id: int = 1,
    title: str = "Test Show",
    year: int | None = 2024,
    aired_episodes: int | None = 12,
    slug: str | None = None,
    **kwargs,
) -> TraktShow:
    """Create a ``TraktShow`` with sensible defaults for testing."""
    return TraktShow(
        title=title,
        year=year,
        ids=TraktIds(trakt=trakt_id, slug=slug or f"test-show-{trakt_id}"),
        aired_episodes=aired_episodes,
        **kwargs,
    )


def make_movie(
    trakt_id: int = 100,
    title: str = "Test Movie",
    year: int | None = 2024,
    slug: str | None = None,
    **kwargs,
) -> TraktMovie:
    """Create a ``TraktMovie`` with sensible defaults for testing."""
    return TraktMovie(
        title=title,
        year=year,
        ids=TraktIds(trakt=trakt_id, slug=slug or f"test-movie-{trakt_id}"),
        **kwargs,
    )


def make_watched_show(
    trakt_id: int = 1,
    title: str = "Test Show",
    plays: int = 1,
    episode_count: int = 6,
    **kwargs,
) -> TraktWatchedShow:
    """Create a ``TraktWatchedShow`` with a single season for testing."""
    show = make_show(trakt_id=trakt_id, title=title, **kwargs)
    episodes = [
        TraktWatchedEpisode(number=i + 1, plays=1) for i in range(episode_count)
    ]
    seasons = [TraktWatchedSeason(number=1, episodes=episodes)]
    return TraktWatchedShow(plays=plays, show=show, seasons=seasons)


def make_watched_movie(
    trakt_id: int = 100,
    title: str = "Test Movie",
    plays: int = 1,
    **kwargs,
) -> TraktWatchedMovie:
    """Create a ``TraktWatchedMovie`` with sensible defaults for testing."""
    movie = make_movie(trakt_id=trakt_id, title=title, **kwargs)
    return TraktWatchedMovie(plays=plays, movie=movie)


@pytest.fixture()
def fake_client() -> FakeTraktClient:
    """Return a fresh ``FakeTraktClient`` instance."""
    return FakeTraktClient()


@pytest_asyncio.fixture()
async def trakt_provider(
    fake_client: FakeTraktClient,
) -> AsyncGenerator[TraktListProvider]:
    provider = TraktListProvider(
        config={
            "client_id": "test-client-id",
            "token": "test-token",
        },
        logger=cast(ProviderLogger, getLogger("anibridge.providers.list.trakt")),
    )
    provider._client = fake_client
    await provider.initialize()
    yield provider
    await provider.close()


@pytest.fixture(autouse=True)
def disable_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    previous = Limiter.DISABLED
    Limiter.DISABLED = True
    yield
    Limiter.DISABLED = previous
