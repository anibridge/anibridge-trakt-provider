"""Tests for the Trakt list provider."""

from datetime import UTC, datetime

import pytest
from anibridge.list import ListMediaType, ListStatus

from anibridge.providers.list.trakt.list import TraktListEntry, TraktListProvider
from anibridge.providers.list.trakt.models import (
    TraktRating,
    TraktWatchlistItem,
)
from anibridge.providers.list.trakt.testing import (
    FakeTraktClient,
    make_movie,
    make_show,
    make_watched_movie,
    make_watched_show,
)


class TestTraktListMedia:
    def test_show_media_type_is_tv(self, trakt_provider: TraktListProvider) -> None:
        show = make_show(trakt_id=1)
        entry = TraktListEntry(trakt_provider, show=show)
        assert entry.media().media_type is ListMediaType.TV

    def test_movie_media_type_is_movie(self, trakt_provider: TraktListProvider) -> None:
        movie = make_movie(trakt_id=100)
        entry = TraktListEntry(trakt_provider, movie=movie)
        assert entry.media().media_type is ListMediaType.MOVIE

    def test_show_total_units(self, trakt_provider: TraktListProvider) -> None:
        show = make_show(trakt_id=1, aired_episodes=24)
        entry = TraktListEntry(trakt_provider, show=show)
        assert entry.media().total_units == 24

    def test_movie_total_units_is_1(self, trakt_provider: TraktListProvider) -> None:
        movie = make_movie(trakt_id=100)
        entry = TraktListEntry(trakt_provider, movie=movie)
        assert entry.media().total_units == 1

    def test_external_url_show(self, trakt_provider: TraktListProvider) -> None:
        show = make_show(trakt_id=1, slug="my-show")
        entry = TraktListEntry(trakt_provider, show=show)
        assert entry.media().external_url == "https://trakt.tv/shows/my-show"

    def test_external_url_movie(self, trakt_provider: TraktListProvider) -> None:
        movie = make_movie(trakt_id=100, slug="my-movie")
        entry = TraktListEntry(trakt_provider, movie=movie)
        assert entry.media().external_url == "https://trakt.tv/movies/my-movie"

    def test_labels_include_year_and_status(
        self, trakt_provider: TraktListProvider
    ) -> None:
        show = make_show(trakt_id=1, year=2024, status="returning series")
        entry = TraktListEntry(trakt_provider, show=show)
        labels = entry.media().labels
        assert "2024" in labels
        assert "Returning Series" in labels


class TestTraktListEntry:
    def test_status_none_when_not_watched(
        self, trakt_provider: TraktListProvider
    ) -> None:
        show = make_show(trakt_id=1)
        entry = TraktListEntry(trakt_provider, show=show)
        assert entry.status is None

    def test_status_planning_from_watchlist(
        self, trakt_provider: TraktListProvider
    ) -> None:
        show = make_show(trakt_id=1)
        wl = TraktWatchlistItem(show=show)
        entry = TraktListEntry(trakt_provider, show=show, watchlist_item=wl)
        assert entry.status is ListStatus.PLANNING

    def test_status_current_when_partially_watched(
        self, trakt_provider: TraktListProvider
    ) -> None:
        watched = make_watched_show(trakt_id=1, episode_count=6, aired_episodes=12)
        entry = TraktListEntry(trakt_provider, show=watched.show, watched=watched)
        assert entry.status is ListStatus.CURRENT

    def test_status_completed_when_all_watched(
        self, trakt_provider: TraktListProvider
    ) -> None:
        watched = make_watched_show(trakt_id=1, episode_count=12, aired_episodes=12)
        entry = TraktListEntry(trakt_provider, show=watched.show, watched=watched)
        assert entry.status is ListStatus.COMPLETED

    def test_status_setter(self, trakt_provider: TraktListProvider) -> None:
        show = make_show(trakt_id=1)
        entry = TraktListEntry(trakt_provider, show=show)
        entry.status = ListStatus.CURRENT
        assert entry.status is ListStatus.CURRENT

    def test_progress_from_watched_episodes(
        self, trakt_provider: TraktListProvider
    ) -> None:
        watched = make_watched_show(trakt_id=1, episode_count=6)
        entry = TraktListEntry(trakt_provider, show=watched.show, watched=watched)
        assert entry.progress == 6

    def test_progress_setter(self, trakt_provider: TraktListProvider) -> None:
        show = make_show(trakt_id=1)
        entry = TraktListEntry(trakt_provider, show=show)
        entry.progress = 5
        assert entry.progress == 5

    def test_progress_rejects_negative(self, trakt_provider: TraktListProvider) -> None:
        show = make_show(trakt_id=1)
        entry = TraktListEntry(trakt_provider, show=show)
        with pytest.raises(ValueError, match="negative"):
            entry.progress = -1

    def test_repeats_from_plays(self, trakt_provider: TraktListProvider) -> None:
        watched = make_watched_show(trakt_id=1, plays=3, episode_count=12)
        entry = TraktListEntry(trakt_provider, show=watched.show, watched=watched)
        assert entry.repeats == 2

    def test_repeats_setter(self, trakt_provider: TraktListProvider) -> None:
        show = make_show(trakt_id=1)
        entry = TraktListEntry(trakt_provider, show=show)
        entry.repeats = 2
        assert entry.repeats == 2

    def test_repeats_rejects_negative(self, trakt_provider: TraktListProvider) -> None:
        show = make_show(trakt_id=1)
        entry = TraktListEntry(trakt_provider, show=show)
        with pytest.raises(ValueError, match="negative"):
            entry.repeats = -1

    def test_user_rating_from_trakt_rating(
        self, trakt_provider: TraktListProvider
    ) -> None:
        show = make_show(trakt_id=1)
        rating = TraktRating(rating=8, show=show)
        entry = TraktListEntry(trakt_provider, show=show, rating=rating)
        assert entry.user_rating == 80

    def test_user_rating_setter(self, trakt_provider: TraktListProvider) -> None:
        show = make_show(trakt_id=1)
        entry = TraktListEntry(trakt_provider, show=show)
        entry.user_rating = 75
        assert entry.user_rating == 75

    def test_user_rating_rejects_out_of_range(
        self, trakt_provider: TraktListProvider
    ) -> None:
        show = make_show(trakt_id=1)
        entry = TraktListEntry(trakt_provider, show=show)
        with pytest.raises(ValueError, match="between 0 and 100"):
            entry.user_rating = 101

    def test_review_from_watchlist_notes(
        self, trakt_provider: TraktListProvider
    ) -> None:
        show = make_show(trakt_id=1)
        wl = TraktWatchlistItem(show=show, notes="Great show")
        entry = TraktListEntry(trakt_provider, show=show, watchlist_item=wl)
        assert entry.review == "Great show"

    def test_review_setter(self, trakt_provider: TraktListProvider) -> None:
        show = make_show(trakt_id=1)
        entry = TraktListEntry(trakt_provider, show=show)
        entry.review = "Updated review"
        assert entry.review == "Updated review"

    def test_started_at_from_watchlist(self, trakt_provider: TraktListProvider) -> None:
        now = datetime.now(tz=UTC)
        show = make_show(trakt_id=1)
        wl = TraktWatchlistItem(show=show, listed_at=now)
        entry = TraktListEntry(trakt_provider, show=show, watchlist_item=wl)
        assert entry.started_at == now

    def test_movie_entry_progress(self, trakt_provider: TraktListProvider) -> None:
        watched_movie = make_watched_movie(trakt_id=100, plays=1)
        entry = TraktListEntry(
            trakt_provider, movie=watched_movie.movie, watched_movie=watched_movie
        )
        assert entry.progress == 1
        assert entry.media().media_type is ListMediaType.MOVIE


class TestTraktListProvider:
    @pytest.mark.asyncio
    async def test_user_returns_cached_user(
        self, trakt_provider: TraktListProvider
    ) -> None:
        user = trakt_provider.user()
        assert user is not None
        assert user.key == "tester"
        assert user.title == "Test User"

    @pytest.mark.asyncio
    async def test_get_entry_builds_from_cache(
        self,
        trakt_provider: TraktListProvider,
        fake_client: FakeTraktClient,
    ) -> None:
        show = make_show(trakt_id=42, title="Cached Show")
        watched = make_watched_show(trakt_id=42, title="Cached Show", episode_count=6)
        fake_client._list_cache[42] = watched
        fake_client._media_cache[42] = show

        entry = await trakt_provider.get_entry("42")
        assert entry is not None
        assert entry.title == "Cached Show"
        assert entry.progress == 6

    @pytest.mark.asyncio
    async def test_get_entry_returns_none_when_missing(
        self, trakt_provider: TraktListProvider
    ) -> None:
        entry = await trakt_provider.get_entry("9999")
        assert entry is None

    @pytest.mark.asyncio
    async def test_search_returns_entries(
        self,
        trakt_provider: TraktListProvider,
        fake_client: FakeTraktClient,
    ) -> None:
        show = make_show(trakt_id=1, title="Search Result")
        fake_client._media_cache[1] = show

        results = await trakt_provider.search("test")
        assert len(results) == 1
        assert results[0].title == "Search Result"

    @pytest.mark.asyncio
    async def test_delete_entry_removes_from_history(
        self,
        trakt_provider: TraktListProvider,
        fake_client: FakeTraktClient,
    ) -> None:
        watched = make_watched_show(trakt_id=1)
        fake_client._list_cache[1] = watched

        await trakt_provider.delete_entry("1")
        assert 1 in fake_client.removed_ids

    @pytest.mark.asyncio
    async def test_update_entry_adds_to_watchlist_for_planning(
        self,
        trakt_provider: TraktListProvider,
        fake_client: FakeTraktClient,
    ) -> None:
        show = make_show(trakt_id=1)
        fake_client._media_cache[1] = show
        entry = TraktListEntry(trakt_provider, show=show)
        entry.status = ListStatus.PLANNING

        await trakt_provider.update_entry("1", entry)
        assert len(fake_client.watchlist_calls) == 1
        assert fake_client.watchlist_calls[0]["trakt_id"] == 1

    @pytest.mark.asyncio
    async def test_update_entry_adds_to_history_for_current(
        self,
        trakt_provider: TraktListProvider,
        fake_client: FakeTraktClient,
    ) -> None:
        show = make_show(trakt_id=1)
        fake_client._media_cache[1] = show
        entry = TraktListEntry(trakt_provider, show=show)
        entry.status = ListStatus.CURRENT

        await trakt_provider.update_entry("1", entry)
        assert len(fake_client.history_calls) == 1
        assert fake_client.history_calls[0]["action"] == "add"

    @pytest.mark.asyncio
    async def test_update_entry_rates_media(
        self,
        trakt_provider: TraktListProvider,
        fake_client: FakeTraktClient,
    ) -> None:
        show = make_show(trakt_id=1)
        fake_client._media_cache[1] = show
        entry = TraktListEntry(trakt_provider, show=show)
        entry.user_rating = 80

        await trakt_provider.update_entry("1", entry)
        assert len(fake_client.rating_calls) == 1
        assert fake_client.rating_calls[0]["rating"] == 8

    @pytest.mark.asyncio
    async def test_backup_and_restore(
        self,
        trakt_provider: TraktListProvider,
        fake_client: FakeTraktClient,
    ) -> None:
        watched = make_watched_show(trakt_id=1, plays=2)
        fake_client._list_cache[1] = watched
        fake_client._rating_cache[1] = TraktRating(rating=9, show=watched.show)

        backup = await trakt_provider.backup_list()
        import json

        data = json.loads(backup)
        assert len(data) == 1
        assert data[0]["trakt_id"] == 1
        assert data[0]["rating"] == 9

        fake_client.clear_cache()
        await trakt_provider.restore_list(backup)
        assert len(fake_client.history_calls) == 1
        assert len(fake_client.rating_calls) == 1

    @pytest.mark.asyncio
    async def test_batch_get_entries(
        self,
        trakt_provider: TraktListProvider,
        fake_client: FakeTraktClient,
    ) -> None:
        watched = make_watched_show(trakt_id=1, title="Show 1", episode_count=6)
        fake_client._list_cache[1] = watched
        assert watched.show is not None
        fake_client._media_cache[1] = watched.show

        results = await trakt_provider.get_entries_batch(["1", "999"])
        assert len(results) == 2
        assert results[0] is not None
        assert results[0].title == "Show 1"
        assert results[1] is None

    @pytest.mark.asyncio
    async def test_resolve_mapping_descriptors(
        self, trakt_provider: TraktListProvider
    ) -> None:
        descriptors = [
            ("trakt", "123", None),
            ("mal", "456", None),
            ("trakt", "789", "scope"),
        ]
        targets = await trakt_provider.resolve_mapping_descriptors(descriptors)
        assert len(targets) == 2
        assert targets[0].media_key == "123"
        assert targets[1].media_key == "789"

    @pytest.mark.asyncio
    async def test_resolve_mapping_descriptors_uses_external_id_search(
        self,
        trakt_provider: TraktListProvider,
        fake_client: FakeTraktClient,
    ) -> None:
        targets = await trakt_provider.resolve_mapping_descriptors(
            [
                ("tmdb_movie", "321", None),
                ("tvdb_show", "654", None),
                ("imdb_movie", "987", None),
            ]
        )

        assert [target.media_key for target in targets] == ["321", "654", "987"]
        assert fake_client.search_by_id_calls == [
            {"id_type": "tmdb", "external_id": "321", "media_type": "movie"},
            {"id_type": "tvdb", "external_id": "654", "media_type": "show"},
            {"id_type": "imdb", "external_id": "987", "media_type": "movie"},
        ]

    @pytest.mark.asyncio
    async def test_derive_keys_resolves_external_ids(
        self,
        trakt_provider: TraktListProvider,
    ) -> None:
        keys = await trakt_provider.derive_keys(
            [("tmdb_movie", "321", None), ("trakt", "123", None)]
        )

        assert keys == {"123", "321"}
