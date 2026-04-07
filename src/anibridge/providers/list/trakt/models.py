"""Models for the Trakt API."""

import contextlib
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TraktBaseModel(BaseModel):
    """Base model for Trakt responses."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class TraktIds(TraktBaseModel):
    """External IDs for a Trakt media item."""

    trakt: int | None = None
    slug: str | None = None
    tvdb: int | None = None
    imdb: str | None = None
    tmdb: int | None = None


class TraktListStatus(StrEnum):
    """Watch status values used by Trakt watchlist/history tracking."""

    WATCHING = "watching"
    COMPLETED = "completed"
    PAUSED = "paused"
    DROPPED = "dropped"
    PLAN_TO_WATCH = "plan_to_watch"


class TraktMediaType(StrEnum):
    """Media type values returned by Trakt."""

    MOVIE = "movie"
    SHOW = "show"


class TraktShowStatus(StrEnum):
    """Airing status of a Trakt show."""

    RETURNING_SERIES = "returning series"
    IN_PRODUCTION = "in production"
    PLANNED = "planned"
    CANCELED = "canceled"
    ENDED = "ended"


class TraktShow(TraktBaseModel):
    """Show resource as returned by Trakt."""

    title: str
    year: int | None = None
    ids: TraktIds = Field(default_factory=TraktIds)
    overview: str | None = None
    first_aired: datetime | None = None
    runtime: int | None = None
    certification: str | None = None
    network: str | None = None
    country: str | None = None
    trailer: str | None = None
    homepage: str | None = None
    status: str | None = None
    aired_episodes: int | None = None
    genres: list[str] = Field(default_factory=list)
    language: str | None = None
    languages: list[str] = Field(default_factory=list)

    @field_validator("first_aired", mode="before")
    @classmethod
    def _parse_datetime(cls, value: Any) -> datetime | Any:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(str(value))
        return value


class TraktMovie(TraktBaseModel):
    """Movie resource as returned by Trakt."""

    title: str
    year: int | None = None
    ids: TraktIds = Field(default_factory=TraktIds)
    overview: str | None = None
    released: date | None = None
    runtime: int | None = None
    certification: str | None = None
    trailer: str | None = None
    homepage: str | None = None
    status: str | None = None
    genres: list[str] = Field(default_factory=list)
    language: str | None = None
    languages: list[str] = Field(default_factory=list)

    @field_validator("released", mode="before")
    @classmethod
    def _parse_date(cls, value: Any) -> date | None | Any:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if not isinstance(value, str):
            return value
        with contextlib.suppress(ValueError):
            return date.fromisoformat(str(value))
        return None


class TraktWatchlistItem(TraktBaseModel):
    """An item in a user's watchlist."""

    rank: int | None = None
    id: int | None = None
    listed_at: datetime | None = None
    notes: str | None = None
    type: str | None = None
    show: TraktShow | None = None
    movie: TraktMovie | None = None

    @field_validator("listed_at", mode="before")
    @classmethod
    def _parse_datetime(cls, value: Any) -> datetime | Any:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(str(value))
        return value


class TraktRating(TraktBaseModel):
    """A user's rating for a media item."""

    rated_at: datetime | None = None
    rating: int | None = None
    type: str | None = None
    show: TraktShow | None = None
    movie: TraktMovie | None = None

    @field_validator("rated_at", mode="before")
    @classmethod
    def _parse_datetime(cls, value: Any) -> datetime | Any:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(str(value))
        return value


class TraktWatchedShow(TraktBaseModel):
    """A watched show entry from the user's history."""

    plays: int | None = None
    last_watched_at: datetime | None = None
    last_updated_at: datetime | None = None
    reset_at: datetime | None = None
    show: TraktShow | None = None
    seasons: list[TraktWatchedSeason] = Field(default_factory=list)

    @field_validator(
        "last_watched_at", "last_updated_at", "reset_at", mode="before"
    )
    @classmethod
    def _parse_datetime(cls, value: Any) -> datetime | Any:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(str(value))
        return value


class TraktWatchedSeason(TraktBaseModel):
    """A watched season within a watched show."""

    number: int | None = None
    episodes: list[TraktWatchedEpisode] = Field(default_factory=list)


class TraktWatchedEpisode(TraktBaseModel):
    """A watched episode within a watched season."""

    number: int | None = None
    plays: int | None = None
    last_watched_at: datetime | None = None

    @field_validator("last_watched_at", mode="before")
    @classmethod
    def _parse_datetime(cls, value: Any) -> datetime | Any:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(str(value))
        return value


class TraktWatchedMovie(TraktBaseModel):
    """A watched movie entry from the user's history."""

    plays: int | None = None
    last_watched_at: datetime | None = None
    last_updated_at: datetime | None = None
    movie: TraktMovie | None = None

    @field_validator("last_watched_at", "last_updated_at", mode="before")
    @classmethod
    def _parse_datetime(cls, value: Any) -> datetime | Any:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(str(value))
        return value


class TraktSearchResult(TraktBaseModel):
    """A search result from Trakt."""

    type: str | None = None
    score: float | None = None
    show: TraktShow | None = None
    movie: TraktMovie | None = None


class TraktUserSettings(TraktBaseModel):
    """User settings from Trakt."""

    user: TraktUser | None = None


class TraktUser(TraktBaseModel):
    """User resource returned by Trakt."""

    username: str
    private: bool = False
    name: str | None = None
    vip: bool = False
    vip_ep: bool = False
    ids: TraktIds = Field(default_factory=TraktIds)


class TraktHistoryItem(TraktBaseModel):
    """A single history entry from Trakt."""

    id: int | None = None
    watched_at: datetime | None = None
    action: str | None = None
    type: str | None = None
    show: TraktShow | None = None
    movie: TraktMovie | None = None
    episode: TraktEpisode | None = None

    @field_validator("watched_at", mode="before")
    @classmethod
    def _parse_datetime(cls, value: Any) -> datetime | Any:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(str(value))
        return value


class TraktEpisode(TraktBaseModel):
    """Episode resource returned by Trakt."""

    season: int | None = None
    number: int | None = None
    title: str | None = None
    ids: TraktIds = Field(default_factory=TraktIds)
