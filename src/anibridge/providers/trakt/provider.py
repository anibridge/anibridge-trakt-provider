"""AniBridge provider implementation for Trakt."""

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from json import JSONDecodeError
from typing import Any, cast

import aiohttp
import msgspec
from anibridge.provider.base import (
    Account,
    AppendEvent,
    BackupArtifact,
    Capabilities,
    Change,
    ChangeKind,
    ChangeQuery,
    DeleteRecord,
    Descriptor,
    Event,
    EventChange,
    EventKind,
    EventQuery,
    EventSpec,
    EventWrite,
    ExternalId,
    Facet,
    FacetName,
    FieldSpec,
    Identifiers,
    Match,
    Metadata,
    MetaValue,
    Node,
    NodeChange,
    NodeFlag,
    NodeKind,
    NodeQuery,
    NodeSpec,
    NumericConstraint,
    Page,
    Part,
    Provider,
    Rating,
    Record,
    RecordChange,
    RecordField,
    RecordQuery,
    RecordSpec,
    RecordWrite,
    Ref,
    Role,
    ScanItem,
    ScanQuery,
    State,
    Status,
    Step,
    Structure,
    SupportsBackupExports,
    SupportsBackupImports,
    SupportsChangeFeed,
    SupportsEventReads,
    SupportsEventWrites,
    SupportsMapping,
    SupportsNodeReads,
    SupportsNodeSearch,
    SupportsRecordReads,
    SupportsRecordWrites,
    SupportsScan,
    TemporalConstraint,
    TemporalPrecision,
    TextConstraint,
    Titles,
    UpsertRecord,
    Value,
    WriteError,
    WriteOp,
    WriteResult,
)

from anibridge.providers.trakt.client import TraktClient
from anibridge.providers.trakt.config import TraktProviderConfig
from anibridge.providers.trakt.models import (
    TraktEpisode,
    TraktMovie,
    TraktRating,
    TraktShow,
    TraktWatchlistItem,
)

__all__ = ["TraktProvider"]

_USER_STATE_SURFACE = "user_state"
_SCROBBLE = "scrobble"
_ALL_RECORD_FIELDS = frozenset(RecordField)

_ID_PROVIDERS: dict[str, tuple[str, str]] = {
    "imdb_movie": ("imdb", "movie"),
    "imdb_show": ("imdb", "show"),
    "tmdb_movie": ("tmdb", "movie"),
    "tmdb_show": ("tmdb", "show"),
    "tvdb_movie": ("tvdb", "movie"),
    "tvdb_show": ("tvdb", "show"),
}


class TraktProvider(
    Provider,
    SupportsMapping,
    SupportsNodeReads,
    SupportsNodeSearch,
    SupportsScan,
    SupportsRecordReads,
    SupportsRecordWrites,
    SupportsEventReads,
    SupportsEventWrites,
    SupportsChangeFeed,
    SupportsBackupExports,
    SupportsBackupImports,
):
    """Trakt provider for the AniBridge provider contract."""

    DISPLAY_NAME = "Trakt"
    NAMESPACE = "trakt"

    def __init__(
        self,
        *,
        logger,
        config: Mapping[str, object] | None = None,
    ) -> None:
        """Parse configuration and prepare the Trakt client."""
        super().__init__(logger=logger, config=config)
        self.parsed_config = msgspec.convert(config or {}, type=TraktProviderConfig)
        self._client = TraktClient(
            logger=self.log,
            client_id=self.parsed_config.client_id,
            client_secret=self.parsed_config.client_secret,
            token=self.parsed_config.token,
            rate_limit=self.parsed_config.rate_limit,
        )
        self._account: Account | None = None

    async def initialize(self) -> None:
        """Initialize the Trakt API session and user caches."""
        self.log.debug("Initializing Trakt provider client")
        await self._client.initialize()
        if self._client.user is None:
            raise RuntimeError("Trakt provider initialized without a resolved user")
        self._account = Account(
            key=self._client.user.username,
            title=self._client.user.name or self._client.user.username,
            url=f"https://trakt.tv/users/{self._client.user.username}",
        )
        self.log.debug("Trakt provider initialized for user %s", self._account.key)

    def account(self) -> Account | None:
        """Return the connected Trakt account."""
        return self._account

    def capabilities(self) -> Capabilities:
        """Advertise Trakt source and target capabilities."""
        return Capabilities(
            roles=frozenset({Role.SOURCE, Role.TARGET}),
            facets=frozenset(
                {
                    FacetName.TITLES,
                    FacetName.IDS,
                    FacetName.STRUCTURE,
                    FacetName.METADATA,
                }
            ),
            nodes=(
                NodeSpec(Descriptor("movie", NodeKind.FILM)),
                NodeSpec(
                    Descriptor("show", NodeKind.SERIES),
                    coordinate_axes=("season", "episode"),
                ),
                NodeSpec(Descriptor("episode", NodeKind.EPISODE)),
            ),
            records=(
                RecordSpec(
                    surface=_USER_STATE_SURFACE,
                    fields={
                        RecordField.STATUS: FieldSpec(
                            RecordField.STATUS,
                            readable=True,
                            writable=True,
                            values=(Descriptor("planned", Status.PLANNED),),
                        ),
                        RecordField.STARTED_AT: FieldSpec(
                            RecordField.STARTED_AT,
                            readable=True,
                            constraints=(
                                TemporalConstraint(
                                    precision=TemporalPrecision.DATETIME
                                ),
                            ),
                        ),
                        RecordField.NOTES: FieldSpec(
                            RecordField.NOTES,
                            readable=True,
                            writable=True,
                            constraints=(TextConstraint(max_length=500),),
                        ),
                        RecordField.RATING: FieldSpec(
                            RecordField.RATING,
                            readable=True,
                            writable=True,
                            constraints=(NumericConstraint(1, 10, 1),),
                        ),
                        RecordField.LAST_ACTIVITY_AT: FieldSpec(
                            RecordField.LAST_ACTIVITY_AT,
                            readable=True,
                            constraints=(
                                TemporalConstraint(
                                    precision=TemporalPrecision.DATETIME
                                ),
                            ),
                        ),
                    },
                    write_ops=frozenset({WriteOp.UPSERT_RECORD, WriteOp.DELETE_RECORD}),
                ),
            ),
            events=(
                EventSpec(
                    Descriptor(_SCROBBLE, EventKind.SCROBBLE),
                    write_ops=frozenset({WriteOp.APPEND_EVENT}),
                ),
            ),
            change_kinds=frozenset(
                {ChangeKind.NODE, ChangeKind.RECORD, ChangeKind.EVENT}
            ),
            external_authorities=frozenset({self.NAMESPACE, *_ID_PROVIDERS}),
        )

    async def close(self) -> None:
        """Close the Trakt API session."""
        await self._client.close()

    async def clear_cache(self) -> None:
        """Clear Trakt provider caches."""
        self._client.clear_cache()

    async def export_backup(self) -> BackupArtifact | None:
        """Export Trakt user state as a provider-managed backup artifact."""
        entries: list[dict[str, Any]] = []
        for item in await self._client.list_items():
            trakt_id, media, media_type, watched, rating, watchlist_item = item
            entry: dict[str, Any] = {
                "trakt_id": trakt_id,
                "type": media_type,
                "media": msgspec.to_builtins(media),
            }
            if watched is not None:
                entry["watched"] = msgspec.to_builtins(watched)
            if rating is not None:
                entry["rating"] = msgspec.to_builtins(rating)
            if watchlist_item is not None:
                entry["watchlist"] = msgspec.to_builtins(watchlist_item)
            entries.append(entry)
        return BackupArtifact(
            content=json.dumps(entries, separators=(",", ":")).encode(),
            file_extension=".json",
            media_type="application/json",
        )

    async def import_backup(self, payload: bytes) -> None:
        """Restore a backup produced by this provider."""
        try:
            data = json.loads(payload.decode())
        except JSONDecodeError:
            self.log.exception("Failed to decode Trakt backup JSON")
            raise
        for item in data:
            trakt_id = int(item["trakt_id"])
            media_type = str(item.get("type") or "show")
            if item.get("watched") is not None:
                await self._client.add_to_history(trakt_id, media_type=media_type)
            rating = item.get("rating")
            if isinstance(rating, dict) and rating.get("rating") is not None:
                await self._client.rate_media(
                    trakt_id,
                    int(rating["rating"]),
                    media_type=media_type,
                )
            watchlist = item.get("watchlist")
            if watchlist is not None:
                notes = watchlist.get("notes") if isinstance(watchlist, dict) else None
                await self._client.add_to_watchlist(
                    trakt_id,
                    media_type=media_type,
                    notes=notes,
                )

    async def resolve(self, ids: Sequence[ExternalId]) -> Sequence[Match]:
        """Resolve external IDs to Trakt refs."""
        matches: list[Match] = []
        for external_id in ids:
            if not external_id.value:
                continue
            if external_id.authority == self.NAMESPACE:
                scopes = (
                    (external_id.scope,)
                    if external_id.scope in {"movie", "show"}
                    else ("show", "movie")
                )
                matches.extend(
                    Match(
                        external_id=external_id,
                        ref=Ref.anchor(self._ref_key(media_type, external_id.value)),
                        confidence=1.0 if external_id.scope else None,
                    )
                    for media_type in scopes
                )
                continue

            external_mapping = _ID_PROVIDERS.get(external_id.authority)
            if external_mapping is None:
                continue
            id_type, media_type = external_mapping
            results = await self._client.search_by_id(
                id_type=id_type,
                external_id=external_id.value,
                media_type=media_type,
            )
            for result in results:
                if result.show is not None and result.show.ids.trakt is not None:
                    matches.append(
                        Match(
                            external_id=external_id,
                            ref=Ref.anchor(
                                self._ref_key("show", result.show.ids.trakt)
                            ),
                        )
                    )
                if result.movie is not None and result.movie.ids.trakt is not None:
                    matches.append(
                        Match(
                            external_id=external_id,
                            ref=Ref.anchor(
                                self._ref_key("movie", result.movie.ids.trakt)
                            ),
                        )
                    )
        return tuple(matches)

    async def fetch_nodes(self, query: NodeQuery) -> Page[Node]:
        """Fetch Trakt media metadata for targeted refs."""
        nodes: list[Node] = []
        for ref in query.refs:
            if query.limit is not None and len(nodes) >= query.limit:
                break
            media_type, _trakt_id = self._parse_ref_key(ref.key)
            media = await self._media_for_ref(ref)
            if media is None:
                continue
            if ref.path and isinstance(media, TraktShow):
                node = await self._episode_node(ref, media, query.facets)
            else:
                node = await self._node_from_media(media, media_type, query.facets)
            if node is None:
                continue
            if query.native_node_kinds and node.kind not in query.native_node_kinds:
                continue
            if query.flags and not query.flags.issubset(node.flags):
                continue
            nodes.append(node)
        return Page(items=tuple(nodes))

    async def search_nodes(
        self,
        query: str,
        *,
        limit: int = 10,
        facets: frozenset[FacetName] = frozenset(),
    ) -> Page[Node]:
        """Search Trakt shows and movies by title."""
        text = query.strip()
        if not text:
            return Page(items=())
        per_type = max(1, limit)
        show_results = await self._client.search_shows(text, limit=per_type)
        movie_results = await self._client.search_movies(text, limit=per_type)
        nodes: list[Node] = []
        seen: set[str] = set()
        for result in (*show_results, *movie_results):
            if result.show is not None:
                key = self._ref_key("show", result.show.ids.trakt)
                if result.show.ids.trakt is not None and key not in seen:
                    seen.add(key)
                    node = await self._node_from_media(result.show, "show", facets)
                    if node is not None:
                        nodes.append(node)
            if result.movie is not None:
                key = self._ref_key("movie", result.movie.ids.trakt)
                if result.movie.ids.trakt is not None and key not in seen:
                    seen.add(key)
                    node = await self._node_from_media(result.movie, "movie", facets)
                    if node is not None:
                        nodes.append(node)
            if len(nodes) >= limit:
                break
        return Page(items=tuple(node for node in nodes if node is not None))

    async def scan(self, query: ScanQuery) -> Page[ScanItem]:
        """Scan user-state-bearing Trakt items."""
        items = await self._client.list_items()
        offset = int(query.cursor or 0)
        limit = query.limit if query.limit is not None else 100
        scanned: list[ScanItem] = []
        next_offset: int | None = None
        for index, item in enumerate(items[offset:], offset):
            if len(scanned) >= limit:
                next_offset = index
                break
            _trakt_id, media, media_type, _watched, rating, watchlist_item = item
            node = await self._node_from_media(media, media_type, query.facets)
            if node is None:
                continue
            if query.native_node_kinds and node.kind not in query.native_node_kinds:
                continue
            if query.flags and not query.flags.issubset(node.flags):
                continue
            records = ()
            if query.include_records:
                records = tuple(
                    self._records_from_state(
                        media,
                        media_type,
                        rating,
                        watchlist_item,
                        query.record_surfaces,
                        query.record_fields,
                    )
                )
            scanned.append(ScanItem(node=node, records=records))
        return Page(
            items=tuple(scanned),
            cursor=str(next_offset) if next_offset is not None else None,
            total=len(items),
        )

    async def fetch_records(self, query: RecordQuery) -> Page[Record]:
        """Fetch Trakt records by ref or record key."""
        refs = tuple(query.refs)
        if not refs and query.keys:
            refs = tuple(
                Ref.anchor(key.split(":", 1)[1] if ":" in key else key)
                for key in query.keys
            )
        if not refs:
            refs = tuple(
                Ref.anchor(self._ref_key(item[2], item[0]))
                for item in await self._client.list_items()
            )

        records: list[Record] = []
        for ref in refs:
            if query.limit is not None and len(records) >= query.limit:
                break
            media_type, trakt_id = self._parse_ref_key(ref.key)
            rating = self._client._rating_cache.get(trakt_id)
            watchlist_item = self._client._watchlist_cache.get(trakt_id)
            media = await self._media_for_ref(ref)
            if media is None:
                continue
            records.extend(
                self._records_from_state(
                    media,
                    media_type,
                    rating,
                    watchlist_item,
                    frozenset(query.record_surfaces),
                    query.fields,
                )
            )
        return Page(items=tuple(records[: query.limit]))

    async def write_records(
        self,
        writes: Sequence[RecordWrite],
    ) -> Sequence[WriteResult]:
        """Apply Trakt record writes."""
        results: list[WriteResult] = []
        for write in writes:
            try:
                if isinstance(write, UpsertRecord):
                    result = await self._upsert_record(write)
                else:
                    result = await self._delete_record(write)
            except Exception as exc:
                op = (
                    WriteOp.DELETE_RECORD
                    if isinstance(write, DeleteRecord)
                    else WriteOp.UPSERT_RECORD
                )
                result = WriteResult(
                    ok=False,
                    op=op,
                    token=write.token,
                    ref=write.ref,
                    code=self._write_error_for_exception(exc),
                    error=str(exc),
                )
            results.append(result)
        return tuple(results)

    async def fetch_events(self, query: EventQuery) -> Page[Event]:
        """Fetch Trakt watched-history events."""
        if query.native_event_kinds and _SCROBBLE not in query.native_event_kinds:
            return Page(items=())
        refs = await self._event_refs(query.refs)
        events: list[Event] = []
        for ref in refs:
            if query.limit is not None and len(events) >= query.limit:
                break
            events.extend(await self._events_for_ref(ref, query))
        return Page(items=tuple(events[: query.limit]))

    async def write_events(
        self,
        writes: Sequence[EventWrite],
    ) -> Sequence[WriteResult]:
        """Apply Trakt event writes."""
        results: list[WriteResult] = []
        for write in writes:
            try:
                result = await self._append_event(write)
            except Exception as exc:
                result = WriteResult(
                    ok=False,
                    op=WriteOp.APPEND_EVENT,
                    token=write.token,
                    ref=write.ref,
                    code=self._write_error_for_exception(exc),
                    error=str(exc),
                )
            results.append(result)
        return tuple(results)

    async def poll_changes(self, query: ChangeQuery) -> Page[Change]:
        """Poll Trakt activity timestamps and return broad changes."""
        activities = await self._client.get_activities()
        cursor = self._parse_cursor(query.cursor)
        changes: list[Change] = []
        for group_name, group in (
            ("movies", activities.movies),
            ("shows", activities.shows),
            ("episodes", activities.episodes),
            ("seasons", activities.seasons),
        ):
            if group is None:
                continue
            if self._changed_after(group.watched_at, cursor):
                changes.append(
                    EventChange(
                        key=group_name,
                        kind=_SCROBBLE,
                        at=self._utc(group.watched_at),
                    )
                )
            if self._changed_after(group.rated_at, cursor):
                changes.append(
                    RecordChange(
                        key=group_name,
                        surface=_USER_STATE_SURFACE,
                        at=self._utc(group.rated_at),
                    )
                )
            if self._changed_after(group.watchlisted_at, cursor):
                changes.append(
                    RecordChange(
                        key=group_name,
                        surface=_USER_STATE_SURFACE,
                        at=self._utc(group.watchlisted_at),
                    )
                )
            if self._changed_after(group.watched_at, cursor):
                changes.append(
                    NodeChange(key=group_name, at=self._utc(group.watched_at))
                )
            if query.limit is not None and len(changes) >= query.limit:
                changes = changes[: query.limit]
                break
        if (
            activities.watchlist is not None
            and self._changed_after(activities.watchlist.updated_at, cursor)
            and (query.limit is None or len(changes) < query.limit)
        ):
            changes.append(
                RecordChange(
                    key="watchlist",
                    surface=_USER_STATE_SURFACE,
                    at=self._utc(activities.watchlist.updated_at),
                )
            )
        return Page(items=tuple(changes), cursor=self._format_cursor(activities.all))

    async def _upsert_record(self, write: UpsertRecord) -> WriteResult:
        media_type, trakt_id = self._parse_ref_key(write.ref.key)
        surface = write.surface
        if surface != _USER_STATE_SURFACE:
            raise ValueError(f"Unsupported Trakt record surface {surface!r}")

        changed = False
        status_value = write.set.get(RecordField.STATUS)
        if RecordField.STATUS in write.clear:
            await self._client.remove_from_watchlist(trakt_id, media_type=media_type)
            changed = True
        elif RecordField.STATUS in write.set or RecordField.NOTES in write.set:
            if (
                status_value is not None
                and self._status_value(status_value) != Status.PLANNED
            ):
                raise ValueError("Trakt only supports planned status records")
            note_value = write.set.get(RecordField.NOTES)
            notes = None if note_value is None else str(note_value)
            await self._client.add_to_watchlist(
                trakt_id,
                media_type=media_type,
                notes=notes,
            )
            changed = True

        if RecordField.NOTES in write.clear and RecordField.STATUS not in write.clear:
            await self._client.add_to_watchlist(
                trakt_id,
                media_type=media_type,
                notes=None,
            )
            changed = True
        if RecordField.RATING in write.clear:
            await self._client.remove_rating(trakt_id, media_type=media_type)
            changed = True
        elif RecordField.RATING in write.set:
            await self._client.rate_media(
                trakt_id,
                self._rating_value(write.set[RecordField.RATING]),
                media_type=media_type,
            )
            changed = True
        if not changed:
            raise ValueError(
                "Trakt user-state record requires status, notes, or rating"
            )
        return WriteResult(
            ok=True,
            op=WriteOp.UPSERT_RECORD,
            token=write.token,
            ref=write.ref,
            key=self._record_key(surface, write.ref),
        )

    async def _delete_record(self, write: DeleteRecord) -> WriteResult:
        ref = write.ref
        if ref is None:
            return WriteResult(
                ok=False,
                op=WriteOp.DELETE_RECORD,
                token=write.token,
                code=WriteError.INVALID,
                error="Trakt delete requires a ref",
            )
        media_type, trakt_id = self._parse_ref_key(ref.key)
        surface = write.surface
        if surface != _USER_STATE_SURFACE:
            raise ValueError(f"Unsupported Trakt record surface {surface!r}")
        await self._client.remove_from_watchlist(trakt_id, media_type=media_type)
        await self._client.remove_rating(trakt_id, media_type=media_type)
        return WriteResult(
            ok=True,
            op=WriteOp.DELETE_RECORD,
            token=write.token,
            ref=ref,
            key=self._record_key(surface, ref),
        )

    async def _append_event(self, write: AppendEvent) -> WriteResult:
        if write.kind and write.kind != _SCROBBLE:
            return self._unsupported_event(write.token, write.ref, WriteOp.APPEND_EVENT)
        media_type, trakt_id = self._parse_ref_key(write.ref.key)
        season, episode = self._episode_coordinate(write.ref)
        await self._client.add_to_history(
            trakt_id,
            media_type=media_type,
            watched_at=self._aware_utc(write.at),
            season=season,
            episode=episode,
        )
        return WriteResult(
            ok=True,
            op=WriteOp.APPEND_EVENT,
            token=write.token,
            ref=write.ref,
            key=self._event_key(write.ref, write.at),
        )

    def _records_from_state(
        self,
        media: TraktShow | TraktMovie,
        media_type: str,
        rating: TraktRating | None,
        watchlist_item: TraktWatchlistItem | None,
        surfaces: frozenset[str],
        fields: frozenset[RecordField],
    ) -> list[Record]:
        records: list[Record] = []
        if (not surfaces or _USER_STATE_SURFACE in surfaces) and (
            rating is not None or watchlist_item is not None
        ):
            records.append(
                self._progress_record(media, media_type, rating, watchlist_item, fields)
            )
        return records

    def _progress_record(
        self,
        media: TraktShow | TraktMovie,
        media_type: str,
        rating: TraktRating | None,
        watchlist_item: TraktWatchlistItem | None,
        fields: frozenset[RecordField],
    ) -> Record:
        requested = fields or _ALL_RECORD_FIELDS
        values: dict[RecordField, Value] = {}
        if RecordField.STATUS in requested and watchlist_item is not None:
            values[RecordField.STATUS] = State(native="planned", status=Status.PLANNED)
        if (
            RecordField.STARTED_AT in requested
            and watchlist_item is not None
            and self._utc(watchlist_item.listed_at)
        ):
            values[RecordField.STARTED_AT] = cast(
                datetime,
                self._utc(watchlist_item.listed_at),
            )
        if (
            RecordField.NOTES in requested
            and watchlist_item is not None
            and watchlist_item.notes
        ):
            values[RecordField.NOTES] = watchlist_item.notes
        if (
            RecordField.RATING in requested
            and rating is not None
            and rating.rating is not None
        ):
            values[RecordField.RATING] = Rating(float(rating.rating), (1, 10, 1))
        if (
            RecordField.LAST_ACTIVITY_AT in requested
            and rating is not None
            and self._utc(rating.rated_at)
        ):
            values[RecordField.LAST_ACTIVITY_AT] = cast(
                datetime,
                self._utc(rating.rated_at),
            )
        return self._record(media, media_type, _USER_STATE_SURFACE, values)

    def _record(
        self,
        media: TraktShow | TraktMovie,
        media_type: str,
        surface: str,
        values: Mapping[RecordField, Value],
    ) -> Record:
        ref = Ref.anchor(self._ref_key(media_type, media.ids.trakt))
        return Record(
            ref=ref,
            surface=surface,
            key=self._record_key(surface, ref),
            url=self._url_for_media(media, media_type),
            ids=self._ids_from_media(media, media_type),
            values=values,
            metadata={"media_type": media_type},
        )

    async def _node_from_media(
        self,
        media: TraktShow | TraktMovie,
        media_type: str,
        facets: frozenset[FacetName],
    ) -> Node | None:
        if media.ids.trakt is None:
            return None
        hydrated: dict[FacetName, Facet] = {}
        if FacetName.TITLES in facets:
            hydrated[FacetName.TITLES] = Titles(primary=media.title)
        if FacetName.IDS in facets:
            hydrated[FacetName.IDS] = Identifiers(
                self._ids_from_media(media, media_type)
            )
        if FacetName.STRUCTURE in facets and isinstance(media, TraktShow):
            hydrated[FacetName.STRUCTURE] = await self._structure_for_show(media)
        if FacetName.METADATA in facets:
            hydrated[FacetName.METADATA] = Metadata(self._metadata_for_media(media))
        flags = (
            frozenset({NodeFlag.ANCHOR, NodeFlag.CONSUMABLE, NodeFlag.TRACKABLE})
            if media_type == "movie"
            else frozenset(
                {
                    NodeFlag.ANCHOR,
                    NodeFlag.CONTAINER,
                    NodeFlag.TRACKABLE,
                    NodeFlag.ORDERED_PARTS,
                }
            )
        )
        return Node(
            ref=Ref.anchor(self._ref_key(media_type, media.ids.trakt)),
            kind=media_type,
            title=media.title,
            url=self._url_for_media(media, media_type),
            labels=self._labels_for_media(media),
            flags=flags,
            facets=hydrated,
        )

    async def _episode_node(
        self,
        ref: Ref,
        show: TraktShow,
        facets: frozenset[FacetName],
    ) -> Node | None:
        season_number, episode_number = self._episode_coordinate(ref)
        if season_number is None or episode_number is None:
            return None
        episode = await self._client.get_episode(
            cast(int, show.ids.trakt),
            season_number,
            episode_number,
        )
        hydrated: dict[FacetName, Facet] = {}
        if FacetName.IDS in facets and episode is not None:
            hydrated[FacetName.IDS] = Identifiers(
                self._ids_from_episode(episode, scope="episode")
            )
        if FacetName.METADATA in facets and episode is not None:
            hydrated[FacetName.METADATA] = Metadata(
                {
                    "season": episode.season,
                    "episode": episode.number,
                    "first_aired": self._format_cursor(episode.first_aired),
                    "runtime": episode.runtime,
                }
            )
        return Node(
            ref=ref,
            kind="episode",
            title=episode.title if episode is not None else None,
            flags=frozenset({NodeFlag.CONSUMABLE, NodeFlag.TRACKABLE}),
            facets=hydrated,
        )

    async def _structure_for_show(self, show: TraktShow) -> Structure:
        if show.ids.trakt is None:
            return Structure()
        parts: list[Part] = []
        for season in await self._client.get_seasons(show.ids.trakt):
            season_number = season.number
            if season_number is None:
                continue
            for episode in season.episodes:
                if episode.number is None:
                    continue
                parts.append(
                    Part(
                        position=(
                            Step("season", season_number),
                            Step("episode", episode.number),
                        ),
                        title=episode.title,
                        key=(
                            str(episode.ids.trakt)
                            if episode.ids.trakt is not None
                            else None
                        ),
                    )
                )
        return Structure(axes=("season", "episode"), parts=tuple(parts))

    async def _media_for_ref(self, ref: Ref) -> TraktShow | TraktMovie | None:
        media_type, trakt_id = self._parse_ref_key(ref.key)
        if media_type == "movie":
            cached = self._client._movie_cache.get(trakt_id)
            if cached is not None:
                return cached
            watched = self._client._movie_list_cache.get(trakt_id)
            if watched and watched.movie:
                return watched.movie
            return await self._client.get_movie(trakt_id)
        cached = self._client._show_cache.get(
            trakt_id
        ) or self._client._media_cache.get(trakt_id)
        if cached is not None:
            return cached
        watched_show = self._client._list_cache.get(trakt_id)
        if watched_show and watched_show.show:
            return watched_show.show
        return await self._client.get_show(trakt_id)

    async def _event_refs(self, refs: tuple[Ref, ...]) -> tuple[Ref, ...]:
        if refs:
            return refs
        return tuple(
            Ref.anchor(self._ref_key(item[2], item[0]))
            for item in await self._client.list_items()
        )

    async def _events_for_ref(self, ref: Ref, query: EventQuery) -> list[Event]:
        media_type, trakt_id = self._parse_ref_key(ref.key)
        season, episode = self._episode_coordinate(ref)
        if media_type == "movie":
            history = await self._client.get_history(trakt_id, media_type="movies")
        else:
            history = await self._client.get_history(trakt_id, media_type="shows")
        events: list[Event] = []
        for item in history:
            if item.watched_at is None:
                continue
            watched_at = self._aware_utc(item.watched_at)
            if query.start_at is not None and watched_at < query.start_at:
                continue
            if query.end_at is not None and watched_at >= query.end_at:
                continue
            item_ref = ref
            if item.episode is not None:
                if item.episode.season is None or item.episode.number is None:
                    continue
                if season is not None and item.episode.season != season:
                    continue
                if episode is not None and item.episode.number != episode:
                    continue
                item_ref = Ref.at(
                    ref.key,
                    ("season", item.episode.season),
                    ("episode", item.episode.number),
                )
            elif ref.path:
                continue
            events.append(
                Event(
                    ref=item_ref,
                    kind=_SCROBBLE,
                    at=watched_at,
                    key=str(item.id) if item.id is not None else None,
                    dedupe_key=self._event_key(item_ref, watched_at),
                    metadata={"action": item.action, "type": item.type}
                    if query.with_metadata
                    else {},
                )
            )
        return events

    def _ids_from_media(
        self,
        media: TraktShow | TraktMovie,
        media_type: str,
    ) -> tuple[ExternalId, ...]:
        ids: list[ExternalId] = []
        if media.ids.trakt is not None:
            ids.append(ExternalId(self.NAMESPACE, str(media.ids.trakt), media_type))
        if media.ids.imdb:
            authority = "imdb_movie" if media_type == "movie" else "imdb_show"
            ids.append(ExternalId(authority, media.ids.imdb))
        if media.ids.tmdb is not None:
            authority = "tmdb_movie" if media_type == "movie" else "tmdb_show"
            ids.append(ExternalId(authority, str(media.ids.tmdb)))
        if media.ids.tvdb is not None:
            authority = "tvdb_movie" if media_type == "movie" else "tvdb_show"
            ids.append(ExternalId(authority, str(media.ids.tvdb)))
        return tuple(ids)

    def _ids_from_episode(
        self,
        episode: TraktEpisode,
        *,
        scope: str,
    ) -> tuple[ExternalId, ...]:
        ids: list[ExternalId] = []
        if episode.ids.trakt is not None:
            ids.append(ExternalId(self.NAMESPACE, str(episode.ids.trakt), scope))
        if episode.ids.imdb:
            ids.append(ExternalId("imdb_show", episode.ids.imdb, scope))
        if episode.ids.tmdb is not None:
            ids.append(ExternalId("tmdb_show", str(episode.ids.tmdb), scope))
        if episode.ids.tvdb is not None:
            ids.append(ExternalId("tvdb_show", str(episode.ids.tvdb), scope))
        return tuple(ids)

    @staticmethod
    def _metadata_for_media(media: TraktShow | TraktMovie) -> Mapping[str, MetaValue]:
        return {
            "year": media.year,
            "status": media.status,
            "runtime": media.runtime,
            "language": media.language,
            "genres": tuple(media.genres),
        }

    @staticmethod
    def _labels_for_media(media: TraktShow | TraktMovie) -> tuple[str, ...]:
        labels: list[str] = []
        if media.year is not None:
            labels.append(str(media.year))
        if media.status:
            labels.append(media.status.replace("_", " ").title())
        if isinstance(media, TraktShow) and media.network:
            labels.append(media.network)
        return tuple(labels)

    @staticmethod
    def _url_for_media(media: TraktShow | TraktMovie, media_type: str) -> str | None:
        if not media.ids.slug:
            return None
        path = "movies" if media_type == "movie" else "shows"
        return f"https://trakt.tv/{path}/{media.ids.slug}"

    @staticmethod
    def _record_key(kind: str, ref: Ref) -> str:
        return f"{kind}:{ref!r}"

    @staticmethod
    def _ref_key(media_type: str | None, trakt_id: int | str | None) -> str:
        if media_type not in {"movie", "show"}:
            raise ValueError(f"Unsupported Trakt media type {media_type!r}")
        if trakt_id is None:
            raise ValueError("Trakt media is missing a trakt id")
        return f"{media_type}:{trakt_id}"

    @staticmethod
    def _parse_ref_key(key: str) -> tuple[str, int]:
        if ":" not in key:
            raise ValueError("Trakt refs must be typed as 'movie:<id>' or 'show:<id>'")
        media_type, raw_id = key.split(":", 1)
        if media_type not in {"movie", "show"}:
            raise ValueError(f"Unsupported Trakt media type {media_type!r}")
        return media_type, int(raw_id)

    def _episode_coordinate(self, ref: Ref) -> tuple[int | None, int | None]:
        season: int | None = None
        episode: int | None = None
        for step in ref.path:
            if step.axis == "season":
                season = self._int_step(step)
            elif step.axis == "episode":
                episode = self._int_step(step)
        return season, episode

    @staticmethod
    def _int_step(step: Step) -> int:
        try:
            return int(step.value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{step.axis} step must be numeric") from exc

    @staticmethod
    def _rating_value(value: object) -> int:
        raw_value = value.value if isinstance(value, Rating) else value
        if not isinstance(raw_value, int | float) or isinstance(raw_value, bool):
            raise ValueError("rating must be numeric")
        return min(max(round(raw_value), 1), 10)

    @staticmethod
    def _status_value(value: object) -> Status | None:
        if isinstance(value, State):
            return value.status
        if isinstance(value, Status):
            return value
        return None

    @staticmethod
    def _event_key(ref: Ref, at: datetime) -> str:
        return f"{ref!r}|{_SCROBBLE}|{at.isoformat()}"

    @staticmethod
    def _unsupported_event(
        token: str | None,
        ref: Ref | None,
        op: WriteOp,
    ) -> WriteResult:
        return WriteResult(
            ok=False,
            op=op,
            token=token,
            ref=ref,
            code=WriteError.UNSUPPORTED,
            error="Trakt only supports scrobble events",
        )

    @staticmethod
    def _utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _aware_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("datetime values must be timezone-aware")
        return value.astimezone(UTC)

    def _aware_utc_value(self, field: RecordField, value: object) -> datetime:
        if not isinstance(value, datetime):
            raise ValueError(f"{field.value} must be datetime")
        return self._aware_utc(value)

    @staticmethod
    def _changed_after(value: datetime | None, cursor: datetime | None) -> bool:
        if value is None:
            return False
        if cursor is None:
            return True
        return cast(datetime, TraktProvider._utc(value)) > cursor

    @staticmethod
    def _parse_cursor(cursor: str | None) -> datetime | None:
        if cursor is None:
            return None
        try:
            parsed = datetime.fromisoformat(cursor)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _format_cursor(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _write_error_for_exception(exc: Exception) -> WriteError:
        if isinstance(exc, ValueError):
            return WriteError.INVALID
        if isinstance(exc, aiohttp.ClientResponseError):
            if exc.status in {401, 403}:
                return WriteError.AUTH
            if exc.status == 404:
                return WriteError.NOT_FOUND
            if exc.status in {420, 429}:
                return WriteError.RATE_LIMITED
        if isinstance(exc, aiohttp.ClientError):
            return WriteError.TRANSIENT
        return WriteError.INTERNAL
