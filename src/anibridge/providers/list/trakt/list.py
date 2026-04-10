"""Trakt list provider for AniBridge."""

import json
from collections.abc import Sequence
from datetime import datetime
from json import JSONDecodeError
from typing import Any, cast

from anibridge.list import (
    ListEntry,
    ListMedia,
    ListMediaType,
    ListProvider,
    ListStatus,
    ListTarget,
    ListUser,
)
from anibridge.utils.types import ProviderLogger

from anibridge.providers.list.trakt.client import TraktClient
from anibridge.providers.list.trakt.config import TraktListProviderConfig
from anibridge.providers.list.trakt.models import (
    TraktMovie,
    TraktRating,
    TraktShow,
    TraktWatchedMovie,
    TraktWatchedShow,
    TraktWatchlistItem,
)

__all__ = ["TraktListProvider"]

_EXTERNAL_ID_PROVIDERS: dict[str, tuple[str, str]] = {
    "imdb_movie": ("imdb", "movie"),
    "imdb_show": ("imdb", "show"),
    "tmdb_movie": ("tmdb", "movie"),
    "tmdb_show": ("tmdb", "show"),
    "tvdb_movie": ("tvdb", "movie"),
    "tvdb_show": ("tvdb", "show"),
}


class TraktListMedia(ListMedia["TraktListProvider"]):
    """AniBridge media wrapper for Trakt show/movie resources."""

    def __init__(
        self,
        provider: TraktListProvider,
        *,
        show: TraktShow | None = None,
        movie: TraktMovie | None = None,
    ) -> None:
        """Initialize the Trakt media wrapper."""
        self._provider = provider
        self._show = show
        self._movie = movie

        if show is not None:
            self._key = str(show.ids.trakt)
            self._title = show.title
        elif movie is not None:
            self._key = str(movie.ids.trakt)
            self._title = movie.title
        else:
            raise ValueError("Either show or movie must be provided")

    @property
    def external_url(self) -> str | None:
        if self._show is not None and self._show.ids.slug:
            return f"https://trakt.tv/shows/{self._show.ids.slug}"
        if self._movie is not None and self._movie.ids.slug:
            return f"https://trakt.tv/movies/{self._movie.ids.slug}"
        return None

    @property
    def labels(self) -> Sequence[str]:
        labels: list[str] = []
        if self._show is not None:
            if self._show.year:
                labels.append(str(self._show.year))
            if self._show.status:
                labels.append(self._show.status.replace("_", " ").title())
            if self._show.network:
                labels.append(self._show.network)
        elif self._movie is not None:
            if self._movie.year:
                labels.append(str(self._movie.year))
            if self._movie.status:
                labels.append(self._movie.status.replace("_", " ").title())
        return labels

    @property
    def media_type(self) -> ListMediaType:
        if self._movie is not None:
            return ListMediaType.MOVIE
        return ListMediaType.TV

    @property
    def total_units(self) -> int | None:
        if self._show is not None:
            return self._show.aired_episodes
        if self._movie is not None:
            return 1
        return None

    @property
    def poster_image(self) -> str | None:
        return None

    def provider(self) -> TraktListProvider:
        return self._provider


class TraktListEntry(ListEntry["TraktListProvider"]):
    """AniBridge list entry backed by Trakt data."""

    def __init__(
        self,
        provider: TraktListProvider,
        *,
        show: TraktShow | None = None,
        movie: TraktMovie | None = None,
        watched: TraktWatchedShow | None = None,
        watched_movie: TraktWatchedMovie | None = None,
        rating: TraktRating | None = None,
        watchlist_item: TraktWatchlistItem | None = None,
    ) -> None:
        """Initialize the Trakt list entry."""
        self._provider = provider
        self._show = show
        self._movie = movie
        self._watched = watched
        self._watched_movie = watched_movie
        self._rating = rating
        self._watchlist_item = watchlist_item
        self._media = TraktListMedia(provider, show=show, movie=movie)

        self._key = self._media.key
        self._title = self._media.title

        # Mutable state for pending changes.
        self._pending_status: ListStatus | None | object = _UNSET
        self._pending_progress: int | None | object = _UNSET
        self._pending_rating: int | None | object = _UNSET
        self._pending_review: str | None | object = _UNSET
        self._pending_started_at: datetime | None | object = _UNSET
        self._pending_finished_at: datetime | None | object = _UNSET
        self._pending_repeats: int | None | object = _UNSET

    @property
    def status(self) -> ListStatus | None:
        if self._pending_status is not _UNSET:
            return cast(ListStatus | None, self._pending_status)

        if self._watchlist_item is not None and not self._is_watched:
            return ListStatus.PLANNING

        if self._is_watched:
            total = self._media.total_units
            progress = self._compute_progress()
            if total is not None and progress >= total:
                return ListStatus.COMPLETED
            return ListStatus.CURRENT

        return None

    @status.setter
    def status(self, value: ListStatus | None) -> None:
        self._pending_status = value

    @property
    def _is_watched(self) -> bool:
        return self._watched is not None or self._watched_movie is not None

    def _compute_progress(self) -> int:
        if self._watched is not None:
            total = 0
            for season in self._watched.seasons:
                total += len(season.episodes)
            return total
        if self._watched_movie is not None:
            return self._watched_movie.plays or 0
        return 0

    @property
    def progress(self) -> int:
        if self._pending_progress is not _UNSET:
            return cast(int, self._pending_progress) or 0
        return self._compute_progress()

    @progress.setter
    def progress(self, value: int | None) -> None:
        if value is not None and value < 0:
            raise ValueError("Progress cannot be negative.")
        self._pending_progress = value

    @property
    def repeats(self) -> int:
        if self._pending_repeats is not _UNSET:
            return cast(int, self._pending_repeats) or 0
        if self._watched is not None:
            plays = self._watched.plays or 0
            return max(plays - 1, 0)
        if self._watched_movie is not None:
            plays = self._watched_movie.plays or 0
            return max(plays - 1, 0)
        return 0

    @repeats.setter
    def repeats(self, value: int | None) -> None:
        if value is not None and value < 0:
            raise ValueError("Repeat count cannot be negative.")
        self._pending_repeats = value

    @property
    def review(self) -> str | None:
        if self._pending_review is not _UNSET:
            return cast(str | None, self._pending_review)
        if self._watchlist_item is not None:
            return self._watchlist_item.notes
        return None

    @review.setter
    def review(self, value: str | None) -> None:
        self._pending_review = value

    @property
    def user_rating(self) -> int | None:
        if self._pending_rating is not _UNSET:
            return cast(int | None, self._pending_rating)
        if self._rating is not None and self._rating.rating is not None:
            return self._rating.rating * 10
        return None

    @user_rating.setter
    def user_rating(self, value: int | None) -> None:
        if value is not None and (value < 0 or value > 100):
            raise ValueError("Ratings must be between 0 and 100.")
        self._pending_rating = value

    @property
    def started_at(self) -> datetime | None:
        if self._pending_started_at is not _UNSET:
            return cast(datetime | None, self._pending_started_at)
        if self._watchlist_item is not None:
            return self._watchlist_item.listed_at
        if self._watched is not None:
            return self._watched.last_watched_at
        if self._watched_movie is not None:
            return self._watched_movie.last_watched_at
        return None

    @started_at.setter
    def started_at(self, value: datetime | None) -> None:
        self._pending_started_at = value

    @property
    def finished_at(self) -> datetime | None:
        if self._pending_finished_at is not _UNSET:
            return cast(datetime | None, self._pending_finished_at)
        if self.status is ListStatus.COMPLETED:
            if self._watched is not None:
                return self._watched.last_watched_at
            if self._watched_movie is not None:
                return self._watched_movie.last_watched_at
        return None

    @finished_at.setter
    def finished_at(self, value: datetime | None) -> None:
        self._pending_finished_at = value

    @property
    def total_units(self) -> int | None:
        return self._media.total_units

    def media(self) -> TraktListMedia:
        return self._media

    def provider(self) -> TraktListProvider:
        return self._provider


# Sentinel for unset pending values.
_UNSET = object()


def _resolve_media_type(entry: TraktListEntry) -> str:
    """Determine the Trakt media type string for API calls."""
    if entry._movie is not None:
        return "movie"
    return "show"


def _result_media_key(result: Any) -> str | None:
    """Extract a Trakt media key from a Trakt search result."""
    if result.show is not None and result.show.ids.trakt is not None:
        return str(result.show.ids.trakt)
    if result.movie is not None and result.movie.ids.trakt is not None:
        return str(result.movie.ids.trakt)
    return None


class TraktListProvider(ListProvider):
    """List provider backed by the Trakt API."""

    NAMESPACE = "trakt"
    MAPPING_PROVIDERS = frozenset({NAMESPACE, *_EXTERNAL_ID_PROVIDERS})

    def __init__(self, *, logger: ProviderLogger, config: dict | None = None) -> None:
        """Create the Trakt list provider with required credentials."""
        super().__init__(logger=logger, config=config)
        self.parsed_config = TraktListProviderConfig.model_validate(config or {})
        self._client = TraktClient(
            logger=self.log,
            client_id=self.parsed_config.client_id,
            client_secret=self.parsed_config.client_secret,
            token=self.parsed_config.token,
            rate_limit=self.parsed_config.rate_limit,
        )
        self._user: ListUser | None = None

    async def initialize(self) -> None:
        """Fetch Trakt user info and prepare caches."""
        self.log.debug("Initializing Trakt provider client")
        await self._client.initialize()
        if self._client.user is not None:
            self._user = ListUser(
                key=self._client.user.username,
                title=self._client.user.name or self._client.user.username,
            )
            self.log.debug("Trakt provider initialized for user %s", self._user.key)
        else:
            raise RuntimeError("Trakt provider initialized without a resolved user")

    async def backup_list(self) -> str:
        """Return a JSON backup of the user's Trakt watched/rated data."""
        self.log.debug("Starting Trakt list backup")
        entries: list[dict[str, Any]] = []

        for trakt_id, watched in self._client._list_cache.items():
            entry: dict[str, Any] = {
                "trakt_id": trakt_id,
                "type": "show",
                "plays": watched.plays,
            }
            if trakt_id in self._client._rating_cache:
                entry["rating"] = self._client._rating_cache[trakt_id].rating
            entries.append(entry)

        for trakt_id, watched_movie in self._client._movie_list_cache.items():
            entry = {
                "trakt_id": trakt_id,
                "type": "movie",
                "plays": watched_movie.plays,
            }
            if trakt_id in self._client._rating_cache:
                entry["rating"] = self._client._rating_cache[trakt_id].rating
            entries.append(entry)

        self.log.debug("Completed Trakt list backup with %s entries", len(entries))
        return json.dumps(entries, separators=(",", ":"))

    async def delete_entry(self, key: str) -> None:
        """Delete a list entry by Trakt ID."""
        trakt_id = int(key)
        media_type = "show"
        if trakt_id in self._client._movie_list_cache:
            media_type = "movie"

        await self._client.remove_from_history(trakt_id, media_type=media_type)
        await self._client.remove_from_watchlist(trakt_id, media_type=media_type)
        self.log.debug("Deleted Trakt entry for id %s", key)

    async def get_entry(self, key: str) -> TraktListEntry | None:
        """Fetch a single entry by building it from cached data."""
        trakt_id = int(key)
        return self._build_entry(trakt_id)

    def _build_entry(self, trakt_id: int) -> TraktListEntry | None:
        """Build a TraktListEntry from cached data for the given Trakt ID."""
        watched = self._client._list_cache.get(trakt_id)
        watched_movie = self._client._movie_list_cache.get(trakt_id)
        rating = self._client._rating_cache.get(trakt_id)
        watchlist_item = self._client._watchlist_cache.get(trakt_id)

        show: TraktShow | None = None
        movie: TraktMovie | None = None

        if watched is not None and watched.show is not None:
            show = watched.show
        elif watched_movie is not None and watched_movie.movie is not None:
            movie = watched_movie.movie
        elif watchlist_item is not None:
            show = watchlist_item.show
            movie = watchlist_item.movie
        else:
            cached_show = self._client._show_cache.get(trakt_id)
            if cached_show is not None:
                show = cached_show
            else:
                movie = self._client._movie_cache.get(trakt_id)
                if movie is None:
                    return None

        if show is None and movie is None:
            return None

        return TraktListEntry(
            self,
            show=show,
            movie=movie,
            watched=watched,
            watched_movie=watched_movie,
            rating=rating,
            watchlist_item=watchlist_item,
        )

    async def derive_keys(
        self, descriptors: Sequence[tuple[str, str, str | None]]
    ) -> set[str]:
        """Resolve mapping descriptors into Trakt media keys."""
        return {
            target.media_key
            for target in await self.resolve_mapping_descriptors(descriptors)
        }

    async def resolve_mapping_descriptors(
        self, descriptors: Sequence[tuple[str, str, str | None]]
    ) -> Sequence[ListTarget]:
        """Resolve mapping descriptors into Trakt media keys."""
        resolved: list[ListTarget] = []

        for provider, entry_id, scope in descriptors:
            if not entry_id:
                continue

            descriptor = (provider, entry_id, scope)
            if provider == self.NAMESPACE:
                resolved.append(ListTarget(descriptor=descriptor, media_key=entry_id))
                continue

            external_mapping = _EXTERNAL_ID_PROVIDERS.get(provider)
            if external_mapping is None:
                continue

            id_type, media_type = external_mapping
            results = await self._client.search_by_id(
                id_type=id_type,
                external_id=entry_id,
                media_type=media_type,
            )
            for result in results:
                if media_key := _result_media_key(result):
                    resolved.append(
                        ListTarget(descriptor=descriptor, media_key=media_key)
                    )

        return resolved

    async def restore_list(self, backup: str) -> None:
        """Restore list entries from a JSON backup string."""
        try:
            data = json.loads(backup)
        except JSONDecodeError:
            self.log.exception("Failed to decode Trakt backup JSON")
            raise
        self.log.debug("Restoring Trakt backup containing %s entries", len(data))
        for item in data:
            trakt_id = int(item["trakt_id"])
            media_type = item.get("type", "show")
            await self._client.add_to_history(trakt_id, media_type=media_type)
            if "rating" in item and item["rating"] is not None:
                await self._client.rate_media(
                    trakt_id,
                    item["rating"],
                    media_type=media_type,
                )
        self.log.debug("Finished restoring Trakt backup entries")

    async def search(self, query: str) -> Sequence[TraktListEntry]:
        """Search Trakt and return entries with minimal metadata."""
        results = await self._client.search_shows(query, limit=10)
        self.log.debug("Trakt search query=%r yielded %s entries", query, len(results))
        entries: list[TraktListEntry] = []
        for result in results:
            if result.show is not None:
                entry = TraktListEntry(self, show=result.show)
                entries.append(entry)
            elif result.movie is not None:
                entry = TraktListEntry(self, movie=result.movie)
                entries.append(entry)
        return tuple(entries)

    async def update_entry(self, key: str, entry: ListEntry) -> None:
        """Update a Trakt list entry."""
        trakt_entry = cast(TraktListEntry, entry)
        trakt_id = int(key)
        media_type = _resolve_media_type(trakt_entry)

        # Handle status changes.
        if trakt_entry._pending_status is not _UNSET:
            status = cast(ListStatus | None, trakt_entry._pending_status)
            if status is ListStatus.PLANNING:
                await self._client.add_to_watchlist(trakt_id, media_type=media_type)
            elif status in (
                ListStatus.CURRENT,
                ListStatus.COMPLETED,
                ListStatus.REPEATING,
            ):
                watched_at = (
                    cast(datetime | None, trakt_entry._pending_started_at)
                    if trakt_entry._pending_started_at is not _UNSET
                    else None
                )
                await self._client.add_to_history(
                    trakt_id, media_type=media_type, watched_at=watched_at
                )
            elif status is None:
                await self._client.remove_from_history(trakt_id, media_type=media_type)
                await self._client.remove_from_watchlist(
                    trakt_id, media_type=media_type
                )

        # Handle rating changes.
        if trakt_entry._pending_rating is not _UNSET:
            rating_value = cast(int | None, trakt_entry._pending_rating)
            if rating_value is not None:
                trakt_rating = max(1, min(10, round(rating_value / 10)))
                await self._client.rate_media(
                    trakt_id, trakt_rating, media_type=media_type
                )
            else:
                await self._client.remove_rating(trakt_id, media_type=media_type)

        self.log.debug("Updated Trakt entry for id %s", key)

    async def clear_cache(self) -> None:
        """Clear cached user/list data."""
        self._client.clear_cache()
        self.log.debug("Cleared Trakt provider cache")

    async def close(self) -> None:
        """Close the underlying Trakt client session."""
        await self._client.close()
        self.log.debug("Closed Trakt provider client")

    async def update_entries_batch(
        self, entries: Sequence[ListEntry]
    ) -> Sequence[TraktListEntry | None]:
        """Batch update list entries sequentially."""
        self.log.debug("Starting Trakt batch update for %s entries", len(entries))
        updated: list[TraktListEntry | None] = []
        for entry in entries:
            await self.update_entry(entry.media().key, entry)
            updated.append(cast(TraktListEntry, entry))
        self.log.debug("Completed Trakt batch update for %s entries", len(updated))
        return updated

    async def get_entries_batch(
        self, keys: Sequence[str]
    ) -> Sequence[TraktListEntry | None]:
        """Batch fetch list entries, returning None when missing."""
        results: list[TraktListEntry | None] = []
        for key in keys:
            results.append(await self.get_entry(key))
        self.log.debug("Completed Trakt batch get for %s keys", len(keys))
        return results

    def user(self) -> ListUser | None:
        """Return cached Trakt user info if initialized."""
        return self._user
