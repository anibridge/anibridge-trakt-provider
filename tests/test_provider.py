"""Tests for the Trakt provider contract."""

import logging
from datetime import UTC, datetime

from anibridge.provider.base import (
    AppendEvent,
    EventKind,
    Rating,
    RecordField,
    RecordKind,
    Ref,
    State,
    Status,
    UpsertRecord,
    WriteOp,
)

from anibridge.providers.trakt.models import (
    TraktIds,
    TraktMovie,
    TraktRating,
    TraktWatchlistItem,
)
from anibridge.providers.trakt.provider import TraktProvider


def _provider() -> TraktProvider:
    return TraktProvider(logger=logging.getLogger("test"), config={"token": "token"})


def test_capabilities_use_progress_records_and_scrobble_events() -> None:
    provider = _provider()
    capabilities = provider.capabilities()

    assert [record.kind.semantic for record in capabilities.records] == [
        RecordKind.PROGRESS
    ]
    record = capabilities.records[0]
    assert record.kind.native == "progress"
    assert record.write_ops == frozenset({WriteOp.UPSERT_RECORD, WriteOp.DELETE_RECORD})
    assert record.fields[RecordField.STATUS].writable is True
    assert record.fields[RecordField.RATING].writable is True
    assert RecordField.PROGRESS not in record.fields
    assert [event.kind.semantic for event in capabilities.events] == [
        EventKind.SCROBBLE
    ]


def test_progress_record_combines_watchlist_and_rating_fields() -> None:
    provider = _provider()
    listed_at = datetime(2026, 1, 1, tzinfo=UTC)
    rated_at = datetime(2026, 1, 2, tzinfo=UTC)
    movie = TraktMovie(title="Movie", ids=TraktIds(trakt=123, slug="movie"))
    records = provider._records_from_state(
        movie,
        "movie",
        TraktRating(rating=8, rated_at=rated_at),
        TraktWatchlistItem(listed_at=listed_at, notes="later"),
        frozenset({"progress"}),
        frozenset(),
    )

    assert len(records) == 1
    record = records[0]
    assert record.kind == "progress"
    assert record.values[RecordField.STATUS] == State(
        native="planned", status=Status.PLANNED
    )
    assert record.values[RecordField.STARTED_AT] == listed_at
    assert record.values[RecordField.NOTES] == "later"
    assert record.values[RecordField.RATING] == Rating(8.0, (1, 10, 1))
    assert record.values[RecordField.LAST_ACTIVITY_AT] == rated_at


async def test_progress_record_writes_watchlist_and_rating(monkeypatch) -> None:
    provider = _provider()
    calls: list[tuple[str, object]] = []

    async def add_to_watchlist(trakt_id, *, media_type, notes):
        calls.append(("watchlist", (trakt_id, media_type, notes)))

    async def rate_media(trakt_id, rating, *, media_type):
        calls.append(("rating", (trakt_id, media_type, rating)))

    monkeypatch.setattr(provider._client, "add_to_watchlist", add_to_watchlist)
    monkeypatch.setattr(provider._client, "rate_media", rate_media)

    result = await provider._upsert_record(
        UpsertRecord(
            ref=Ref.anchor("movie:123"),
            kind="progress",
            set={
                RecordField.STATUS: State(status=Status.PLANNED),
                RecordField.NOTES: "later",
                RecordField.RATING: Rating(8.0, (1, 10, 1)),
            },
        )
    )

    assert result.ok is True
    assert calls == [
        ("watchlist", (123, "movie", "later")),
        ("rating", (123, "movie", 8)),
    ]


async def test_progress_record_status_clear_does_not_recreate_watchlist(
    monkeypatch,
) -> None:
    provider = _provider()
    calls: list[tuple[str, object]] = []

    async def add_to_watchlist(trakt_id, *, media_type, notes):
        calls.append(("watchlist", (trakt_id, media_type, notes)))

    async def remove_from_watchlist(trakt_id, *, media_type):
        calls.append(("remove_watchlist", (trakt_id, media_type)))

    monkeypatch.setattr(provider._client, "add_to_watchlist", add_to_watchlist)
    monkeypatch.setattr(
        provider._client,
        "remove_from_watchlist",
        remove_from_watchlist,
    )

    result = await provider._upsert_record(
        UpsertRecord(
            ref=Ref.anchor("movie:123"),
            kind="progress",
            clear=frozenset({RecordField.STATUS, RecordField.NOTES}),
        )
    )

    assert result.ok is True
    assert calls == [("remove_watchlist", (123, "movie"))]


async def test_scrobble_writes_stay_on_event_channel(monkeypatch) -> None:
    provider = _provider()
    calls: list[tuple[object, ...]] = []
    watched_at = datetime(2026, 1, 3, tzinfo=UTC)

    async def add_to_history(trakt_id, *, media_type, watched_at, season, episode):
        calls.append((trakt_id, media_type, watched_at, season, episode))

    monkeypatch.setattr(provider._client, "add_to_history", add_to_history)

    result = await provider._append_event(
        AppendEvent(
            ref=Ref.at("show:456", ("season", 1), ("episode", 2)),
            kind="scrobble",
            at=watched_at,
        )
    )

    assert result.ok is True
    assert calls == [(456, "show", watched_at, 1, 2)]
