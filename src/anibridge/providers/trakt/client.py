"""Client for the Trakt API."""

import asyncio
import contextlib
import importlib.metadata
from datetime import UTC, datetime, tzinfo
from logging import Logger
from typing import Any, ClassVar

import aiohttp
import msgspec
from anibridge.utils.cache import TTLDict, ttl_cache
from anibridge.utils.limiter import Limiter

from anibridge.providers.trakt.models import (
    TraktActivities,
    TraktEpisode,
    TraktHistoryItem,
    TraktMovie,
    TraktRating,
    TraktSearchResult,
    TraktSeason,
    TraktShow,
    TraktUser,
    TraktUserSettings,
    TraktWatchedMovie,
    TraktWatchedShow,
    TraktWatchlistItem,
)

__all__ = ["TraktClient"]

# Trakt allows 1000 requests per 5 minutes.
global_trakt_limiter = Limiter(rate=1000 / 300, capacity=1)

_EXTERNAL_ID_TYPES = frozenset({"imdb", "tmdb", "tvdb"})


class TraktClient:
    """Client for the Trakt REST API."""

    API_URL: ClassVar[str] = "https://api.trakt.tv"

    def __init__(
        self,
        *,
        logger: Logger,
        client_id: str,
        client_secret: str,
        token: str,
        rate_limit: int | None = None,
    ) -> None:
        """Construct the client with the required credentials."""
        self.log = logger
        self.client_id = client_id
        self.client_secret = client_secret
        self._refresh_token = token
        self._access_token: str | None = None
        self._access_token_expiry: datetime | None = None
        self._session: aiohttp.ClientSession | None = None
        self.rate_limit = rate_limit

        if self.rate_limit is None:
            self.log.debug(
                "Using shared global Trakt rate limiter with %s requests per minute",
                global_trakt_limiter.rate * 60,
            )
            self._request_limiter = global_trakt_limiter
        else:
            self.log.debug(
                "Using local Trakt rate limiter with %s requests per minute",
                self.rate_limit,
            )
            self._request_limiter = Limiter(rate=self.rate_limit / 60, capacity=1)

        self.user: TraktUser | None = None
        self.user_timezone: tzinfo = UTC

        self._bg_task: asyncio.Task[list[TraktWatchedShow]] | None = None
        self._cache_epoch = 0
        self._list_cache: dict[int, TraktWatchedShow] = {}
        self._movie_list_cache: dict[int, TraktWatchedMovie] = {}
        self._show_cache: TTLDict[int, TraktShow] = TTLDict(ttl=43200)
        self._movie_cache: TTLDict[int, TraktMovie] = TTLDict(ttl=43200)
        self._media_cache = self._show_cache
        self._rating_cache: dict[int, TraktRating] = {}
        self._watchlist_cache: dict[int, TraktWatchlistItem] = {}

    async def _ensure_access_token(self) -> str:
        """Ensure we have a valid access token, refreshing if needed."""
        if (
            self._access_token is not None
            and self._access_token_expiry is not None
            and datetime.now(UTC) < self._access_token_expiry
        ):
            return self._access_token

        self.log.debug("Refreshing Trakt OAuth access token")
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"{self.API_URL}/oauth/token",
                json={
                    "refresh_token": self._refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                    "grant_type": "refresh_token",
                },
            ) as response,
        ):
            if response.status >= 400:
                raise aiohttp.ClientError(
                    f"Failed to refresh Trakt OAuth token ({response.status}). "
                    "Verify your refresh token and client credentials."
                )
            data = await response.json()

        self._access_token = data["access_token"]
        self._refresh_token = data["refresh_token"]
        expires_in: int = data.get("expires_in", 7776000)
        created_at: int = data.get("created_at", int(datetime.now(UTC).timestamp()))
        self._access_token_expiry = datetime.fromtimestamp(
            created_at + expires_in, tz=UTC
        )
        self.log.debug(
            "Trakt access token refreshed, expires at %s", self._access_token_expiry
        )

        # Invalidate the existing session so it picks up the new token.
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

        return self._access_token

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        access_token = await self._ensure_access_token()
        if self._session is None or self._session.closed:
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "anibridge-trakt-provider/"
                + importlib.metadata.version("anibridge-trakt-provider"),
                "trakt-api-version": "2",
                "trakt-api-key": self.client_id,
                "Authorization": f"Bearer {access_token}",
            }
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    async def close(self) -> None:
        """Close the underlying HTTP session if it is open."""
        if (task := self._bg_task) and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._session and not self._session.closed:
            await self._session.close()

    def clear_cache(self) -> None:
        """Clear in-memory caches for user list and general media lookups."""
        self._list_cache.clear()
        self._movie_list_cache.clear()
        self._show_cache.clear()
        self._movie_cache.clear()
        self._rating_cache.clear()
        self._watchlist_cache.clear()
        self._invalidate_cached_views()

    def _invalidate_cached_views(self) -> None:
        """Invalidate derived cached views after list-state changes."""
        self._cache_epoch += 1
        if (task := self._bg_task) and not task.done():
            task.cancel()
        self._bg_task = None
        with contextlib.suppress(AttributeError):
            self._fetch_watched_shows.cache_clear()
        with contextlib.suppress(AttributeError):
            self._fetch_watched_movies.cache_clear()
        with contextlib.suppress(AttributeError):
            self._search.cache_clear()

    async def initialize(self) -> None:
        """Prime the client by fetching user info and populating caches."""
        self.clear_cache()
        settings = await self.get_settings()
        if settings.user:
            self.user = settings.user
        await self._fetch_watched_shows()
        await self._fetch_watched_movies()
        await self._fetch_ratings()
        await self._fetch_watchlist()

    async def get_settings(self) -> TraktUserSettings:
        """Fetch the authenticated user's settings."""
        response = await self._make_request("GET", "/users/settings")
        return msgspec.convert(response, type=TraktUserSettings)

    async def get_show(
        self,
        trakt_id: int,
        *,
        force_refresh: bool = False,
    ) -> TraktShow:
        """Retrieve show details by Trakt ID, using cache unless forced."""
        self._schedule_list_refresh()
        if not force_refresh:
            cached = self._show_cache.get(trakt_id)
            if cached is not None:
                self.log.debug("Cache hit $${trakt_id: %s}$$", trakt_id)
                return cached
        return await self._fetch_show(trakt_id)

    async def _fetch_show(self, trakt_id: int) -> TraktShow:
        """Fetch a show from the Trakt API and populate caches."""
        self.log.debug("Pulling Trakt show data from API $${trakt_id: %s}$$", trakt_id)
        response = await self._make_request(
            "GET",
            f"/shows/{trakt_id}",
            params={"extended": "full"},
        )
        show = msgspec.convert(response, type=TraktShow)
        if show.ids.trakt is not None:
            self._show_cache[show.ids.trakt] = show
        return show

    async def get_movie(
        self,
        trakt_id: int,
        *,
        force_refresh: bool = False,
    ) -> TraktMovie:
        """Retrieve movie details by Trakt ID, using cache unless forced."""
        if not force_refresh:
            cached = self._movie_cache.get(trakt_id)
            if cached is not None:
                return cached
        return await self._fetch_movie(trakt_id)

    async def _fetch_movie(self, trakt_id: int) -> TraktMovie:
        """Fetch a movie from the Trakt API and populate caches."""
        response = await self._make_request(
            "GET",
            f"/movies/{trakt_id}",
            params={"extended": "full"},
        )
        movie = msgspec.convert(response, type=TraktMovie)
        if movie.ids.trakt is not None:
            self._movie_cache[movie.ids.trakt] = movie
        return movie

    async def search_shows(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[TraktSearchResult]:
        """Search shows by title."""
        return await self._search(query, limit=min(max(limit, 1), 100))

    async def search_movies(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[TraktSearchResult]:
        """Search movies by title."""
        return await self._search_media_type(
            "movie",
            query,
            limit=min(max(limit, 1), 100),
        )

    @ttl_cache(ttl=300)
    async def _search(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[TraktSearchResult]:
        """Cached helper for show title searches."""
        params = {
            "query": query,
            "extended": "full",
            "limit": limit,
        }
        response = await self._make_request(
            "GET",
            "/search/show",
            params=params,
        )
        results: list[TraktSearchResult] = []
        for item in response:
            result = msgspec.convert(item, type=TraktSearchResult)
            if result.show and result.show.ids.trakt is not None:
                self._show_cache[result.show.ids.trakt] = result.show
            results.append(result)
        return results

    @ttl_cache(ttl=300)
    async def _search_media_type(
        self,
        media_type: str,
        query: str,
        *,
        limit: int = 10,
    ) -> list[TraktSearchResult]:
        """Cached helper for title searches by Trakt media type."""
        params = {
            "query": query,
            "extended": "full",
            "limit": limit,
        }
        response = await self._make_request(
            "GET",
            f"/search/{media_type}",
            params=params,
        )
        results: list[TraktSearchResult] = []
        for item in response:
            result = msgspec.convert(item, type=TraktSearchResult)
            if result.show and result.show.ids.trakt is not None:
                self._show_cache[result.show.ids.trakt] = result.show
            if result.movie and result.movie.ids.trakt is not None:
                self._movie_cache[result.movie.ids.trakt] = result.movie
            results.append(result)
        return results

    async def search_by_id(
        self,
        *,
        id_type: str,
        external_id: str,
        media_type: str | None = None,
    ) -> list[TraktSearchResult]:
        """Search Trakt by an external IMDb, TMDb, or TVDb identifier."""
        normalized_id_type = id_type.lower()
        if normalized_id_type not in _EXTERNAL_ID_TYPES:
            raise ValueError(f"Unsupported Trakt external id type: {id_type}")

        normalized_media_type = media_type.lower() if media_type else None
        return await self._search_by_id(
            id_type=normalized_id_type,
            external_id=external_id,
            media_type=normalized_media_type,
        )

    @ttl_cache(ttl=300)
    async def _search_by_id(
        self,
        *,
        id_type: str,
        external_id: str,
        media_type: str | None = None,
    ) -> list[TraktSearchResult]:
        """Cached helper for Trakt external-ID lookups."""
        params: dict[str, Any] = {"extended": "full"}
        if media_type is not None:
            params["type"] = media_type

        response = await self._make_request(
            "GET",
            f"/search/{id_type}/{external_id}",
            params=params,
        )

        results: list[TraktSearchResult] = []
        for item in response:
            result = msgspec.convert(item, type=TraktSearchResult)
            if result.show and result.show.ids.trakt is not None:
                self._show_cache[result.show.ids.trakt] = result.show
            if result.movie and result.movie.ids.trakt is not None:
                self._movie_cache[result.movie.ids.trakt] = result.movie
            results.append(result)
        return results

    @ttl_cache(ttl=3600)
    async def _fetch_watched_shows(self) -> list[TraktWatchedShow]:
        """Fetch all watched shows and atomically refresh list cache."""
        if not self.user:
            raise aiohttp.ClientError("User information is required for list refresh")

        self.log.debug("Refreshing watched shows cache from Trakt API")
        refresh_epoch = self._cache_epoch

        response = await self._make_request(
            "GET",
            f"/users/{self.user.username}/watched/shows",
            params={"extended": "full"},
        )

        if refresh_epoch != self._cache_epoch:
            return []

        refreshed: dict[int, TraktWatchedShow] = {}
        results: list[TraktWatchedShow] = []
        for item in response:
            watched = msgspec.convert(item, type=TraktWatchedShow)
            results.append(watched)
            if watched.show and watched.show.ids.trakt is not None:
                trakt_id = watched.show.ids.trakt
                refreshed[trakt_id] = watched
                self._media_cache[trakt_id] = watched.show

        if refresh_epoch != self._cache_epoch:
            return results

        self._list_cache.clear()
        self._list_cache.update(refreshed)
        return results

    @ttl_cache(ttl=3600)
    async def _fetch_watched_movies(self) -> list[TraktWatchedMovie]:
        """Fetch all watched movies and refresh movie list cache."""
        if not self.user:
            raise aiohttp.ClientError("User information is required for list refresh")

        self.log.debug("Refreshing watched movies cache from Trakt API")
        refresh_epoch = self._cache_epoch

        response = await self._make_request(
            "GET",
            f"/users/{self.user.username}/watched/movies",
            params={"extended": "full"},
        )

        if refresh_epoch != self._cache_epoch:
            return []

        refreshed: dict[int, TraktWatchedMovie] = {}
        results: list[TraktWatchedMovie] = []
        for item in response:
            watched = msgspec.convert(item, type=TraktWatchedMovie)
            results.append(watched)
            if watched.movie and watched.movie.ids.trakt is not None:
                trakt_id = watched.movie.ids.trakt
                refreshed[trakt_id] = watched

        if refresh_epoch != self._cache_epoch:
            return results

        self._movie_list_cache.clear()
        self._movie_list_cache.update(refreshed)
        return results

    async def _fetch_ratings(self) -> None:
        """Fetch all user ratings and populate the rating cache."""
        if not self.user:
            return

        self.log.debug("Refreshing ratings cache from Trakt API")
        for media_type in ("shows", "movies"):
            response = await self._make_request(
                "GET",
                f"/users/{self.user.username}/ratings/{media_type}",
            )
            for item in response:
                rating = msgspec.convert(item, type=TraktRating)
                trakt_id = None
                if rating.show and rating.show.ids.trakt is not None:
                    trakt_id = rating.show.ids.trakt
                elif rating.movie and rating.movie.ids.trakt is not None:
                    trakt_id = rating.movie.ids.trakt
                if trakt_id is not None:
                    self._rating_cache[trakt_id] = rating

    async def _fetch_watchlist(self) -> None:
        """Fetch user watchlist and populate cache."""
        if not self.user:
            return

        self.log.debug("Refreshing watchlist cache from Trakt API")
        response = await self._make_request(
            "GET",
            f"/users/{self.user.username}/watchlist",
            params={"extended": "full"},
        )
        self._watchlist_cache.clear()
        for item in response:
            wl_item = msgspec.convert(item, type=TraktWatchlistItem)
            trakt_id = None
            if wl_item.show and wl_item.show.ids.trakt is not None:
                trakt_id = wl_item.show.ids.trakt
            elif wl_item.movie and wl_item.movie.ids.trakt is not None:
                trakt_id = wl_item.movie.ids.trakt
            if trakt_id is not None:
                self._watchlist_cache[trakt_id] = wl_item

    async def list_items(
        self,
    ) -> tuple[
        tuple[
            int,
            TraktShow | TraktMovie,
            str,
            TraktWatchedShow | TraktWatchedMovie | None,
            TraktRating | None,
            TraktWatchlistItem | None,
        ],
        ...,
    ]:
        """Return all cached user-state items keyed by Trakt media id."""
        await self._fetch_watched_shows()
        await self._fetch_watched_movies()
        ids = (
            set(self._list_cache)
            | set(self._movie_list_cache)
            | set(self._rating_cache)
            | set(self._watchlist_cache)
        )
        items: list[
            tuple[
                int,
                TraktShow | TraktMovie,
                str,
                TraktWatchedShow | TraktWatchedMovie | None,
                TraktRating | None,
                TraktWatchlistItem | None,
            ]
        ] = []
        for trakt_id in sorted(ids):
            watched = self._list_cache.get(trakt_id) or self._movie_list_cache.get(
                trakt_id
            )
            rating = self._rating_cache.get(trakt_id)
            watchlist_item = self._watchlist_cache.get(trakt_id)
            media = self._media_from_cached_state(watched, rating, watchlist_item)
            if media is None:
                continue
            media_type = "movie" if isinstance(media, TraktMovie) else "show"
            items.append((trakt_id, media, media_type, watched, rating, watchlist_item))
        return tuple(items)

    def _media_from_cached_state(
        self,
        watched: TraktWatchedShow | TraktWatchedMovie | None,
        rating: TraktRating | None,
        watchlist_item: TraktWatchlistItem | None,
    ) -> TraktShow | TraktMovie | None:
        """Return the media object attached to cached Trakt state."""
        if isinstance(watched, TraktWatchedShow) and watched.show is not None:
            return watched.show
        if isinstance(watched, TraktWatchedMovie) and watched.movie is not None:
            return watched.movie
        if rating is not None:
            if rating.show is not None:
                return rating.show
            if rating.movie is not None:
                return rating.movie
        if watchlist_item is not None:
            if watchlist_item.show is not None:
                return watchlist_item.show
            if watchlist_item.movie is not None:
                return watchlist_item.movie
        return None

    async def get_seasons(self, trakt_id: int) -> tuple[TraktSeason, ...]:
        """Fetch show seasons with episode metadata."""
        response = await self._make_request(
            "GET",
            f"/shows/{trakt_id}/seasons",
            params={"extended": "episodes"},
        )
        return tuple(msgspec.convert(item, type=TraktSeason) for item in response)

    async def get_episode(
        self,
        trakt_id: int,
        season: int,
        episode: int,
    ) -> TraktEpisode | None:
        """Fetch one episode by show id and coordinate."""
        response = await self._make_request(
            "GET",
            f"/shows/{trakt_id}/seasons/{season}/episodes/{episode}",
            params={"extended": "full"},
        )
        if not response:
            return None
        return msgspec.convert(response, type=TraktEpisode)

    async def get_activities(self) -> TraktActivities:
        """Fetch the authenticated user's Trakt last-activity timestamps."""
        response = await self._make_request("GET", "/sync/last_activities")
        return msgspec.convert(response, type=TraktActivities)

    def _schedule_list_refresh(self) -> None:
        """Schedule a background refresh when caches are stale."""
        if (task := self._bg_task) and not task.done():
            return

        def _on_done(t: asyncio.Task[list[TraktWatchedShow]]) -> None:
            if not t.cancelled() and (exc := t.exception()):
                self.log.warning("Watched list cache refresh failed", exc_info=exc)

        self._bg_task = task = asyncio.create_task(self._fetch_watched_shows())
        task.add_done_callback(_on_done)

    async def add_to_history(
        self,
        trakt_id: int,
        *,
        media_type: str = "show",
        watched_at: datetime | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> dict[str, Any]:
        """Add a show or movie to the user's watched history."""
        if watched_at is None:
            watched_at = datetime.now(tz=UTC)

        payload: dict[str, Any]
        if media_type == "movie":
            payload = {
                "movies": [
                    {
                        "ids": {"trakt": trakt_id},
                        "watched_at": watched_at.isoformat(),
                    }
                ]
            }
        elif episode is not None:
            payload = {
                "shows": [
                    {
                        "ids": {"trakt": trakt_id},
                        "seasons": [
                            {
                                "number": season or 1,
                                "episodes": [
                                    {
                                        "number": episode,
                                        "watched_at": watched_at.isoformat(),
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        else:
            payload = {
                "shows": [
                    {
                        "ids": {"trakt": trakt_id},
                        "watched_at": watched_at.isoformat(),
                    }
                ]
            }

        result = await self._make_request("POST", "/sync/history", json=payload)
        self._invalidate_cached_views()
        return result

    async def rate_media(
        self,
        trakt_id: int,
        rating: int,
        *,
        media_type: str = "show",
        rated_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Add a rating (1-10) for a show or movie."""
        if rated_at is None:
            rated_at = datetime.now(tz=UTC)

        entry: dict[str, Any] = {
            "ids": {"trakt": trakt_id},
            "rating": rating,
            "rated_at": rated_at.isoformat(),
        }
        key = "movies" if media_type == "movie" else "shows"
        result = await self._make_request("POST", "/sync/ratings", json={key: [entry]})
        self._rating_cache.pop(trakt_id, None)
        return result

    async def remove_rating(
        self,
        trakt_id: int,
        *,
        media_type: str = "show",
    ) -> dict[str, Any]:
        """Remove a rating for a show or movie."""
        key = "movies" if media_type == "movie" else "shows"
        result = await self._make_request(
            "POST",
            "/sync/ratings/remove",
            json={key: [{"ids": {"trakt": trakt_id}}]},
        )
        self._rating_cache.pop(trakt_id, None)
        return result

    async def add_to_watchlist(
        self,
        trakt_id: int,
        *,
        media_type: str = "show",
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Add a show or movie to the user's watchlist."""
        key = "movies" if media_type == "movie" else "shows"
        item: dict[str, Any] = {"ids": {"trakt": trakt_id}}
        if notes:
            item["notes"] = notes
        result = await self._make_request(
            "POST",
            "/sync/watchlist",
            json={key: [item]},
        )
        self._watchlist_cache.pop(trakt_id, None)
        return result

    async def remove_from_watchlist(
        self,
        trakt_id: int,
        *,
        media_type: str = "show",
    ) -> dict[str, Any]:
        """Remove a show or movie from the user's watchlist."""
        key = "movies" if media_type == "movie" else "shows"
        result = await self._make_request(
            "POST",
            "/sync/watchlist/remove",
            json={key: [{"ids": {"trakt": trakt_id}}]},
        )
        self._watchlist_cache.pop(trakt_id, None)
        return result

    async def get_history(
        self,
        trakt_id: int,
        *,
        media_type: str = "shows",
    ) -> list[TraktHistoryItem]:
        """Fetch history for a specific show or movie."""
        response = await self._make_request(
            "GET",
            f"/sync/history/{media_type}/{trakt_id}",
        )
        return [msgspec.convert(item, type=TraktHistoryItem) for item in response]

    async def _make_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: Any = None,
    ) -> Any:
        """Make a rate-limited Trakt API request with bounded retries."""
        max_attempts = 3
        session = await self._get_session()
        url = f"{self.API_URL.rstrip('/')}/{path.lstrip('/')}"
        normalized_path = f"/{path.lstrip('/')}"

        for attempt in range(1, max_attempts + 1):
            try:
                await self._request_limiter.acquire()  # ty:ignore[invalid-await]

                async with session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    data=data,
                ) as response:
                    if response.status in (401, 403):
                        raise aiohttp.ClientError(
                            f"Trakt API request unauthorized ({response.status}). "
                            "Verify your Trakt credentials."
                        )

                    if response.status == 429:
                        retry_after = response.headers.get("Retry-After", "unknown")
                        raise aiohttp.ClientError(
                            f"Trakt API rate limited (429). Retry-After: {retry_after}"
                        )

                    response.raise_for_status()

                    if response.status == 204:
                        return {}

                    return await response.json()

            except (
                aiohttp.ClientResponseError,
                aiohttp.ClientConnectionError,
                TimeoutError,
            ) as exc:
                if attempt < max_attempts:
                    self.log.error(
                        "Retrying failed request (attempt %s/%s): %s",
                        attempt,
                        max_attempts,
                        exc,
                    )
                    await asyncio.sleep(1)
                    continue

                error_message = (
                    exc.message
                    if isinstance(exc, aiohttp.ClientResponseError)
                    else str(exc)
                )

                raise aiohttp.ClientError(
                    "Trakt request failed after 3 attempts. "
                    f"error={exc.__class__.__name__}: {error_message}; "
                    f"method={method}; "
                    f"path={normalized_path}; "
                    f"params={params}; "
                    f"json={json}"
                ) from exc

        raise aiohttp.ClientError("Trakt request failed unexpectedly")
