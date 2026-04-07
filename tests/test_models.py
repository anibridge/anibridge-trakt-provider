"""Tests for the Trakt models."""

from datetime import date, datetime

from anibridge.providers.list.trakt.models import (
    TraktEpisode,
    TraktHistoryItem,
    TraktIds,
    TraktMovie,
    TraktRating,
    TraktSearchResult,
    TraktShow,
    TraktUser,
    TraktWatchedEpisode,
    TraktWatchedMovie,
    TraktWatchedSeason,
    TraktWatchedShow,
    TraktWatchlistItem,
)


class TestTraktIds:
    def test_all_none_by_default(self) -> None:
        ids = TraktIds()
        assert ids.trakt is None
        assert ids.slug is None
        assert ids.tvdb is None
        assert ids.imdb is None
        assert ids.tmdb is None

    def test_from_dict(self) -> None:
        ids = TraktIds.model_validate(
            {"trakt": 123, "slug": "test", "imdb": "tt1234567"}
        )
        assert ids.trakt == 123
        assert ids.slug == "test"
        assert ids.imdb == "tt1234567"


class TestTraktShow:
    def test_minimal_show(self) -> None:
        show = TraktShow(title="Test")
        assert show.title == "Test"
        assert show.year is None
        assert show.ids.trakt is None

    def test_full_show(self) -> None:
        show = TraktShow(
            title="My Show",
            year=2024,
            ids=TraktIds(trakt=1, slug="my-show"),
            aired_episodes=12,
            status="returning series",
            genres=["anime", "action"],
        )
        assert show.year == 2024
        assert show.ids.trakt == 1
        assert show.aired_episodes == 12
        assert len(show.genres) == 2

    def test_first_aired_datetime_parsing(self) -> None:
        show = TraktShow(
            title="Test", first_aired=datetime.fromisoformat("2024-01-15T20:00:00.000Z")
        )
        assert show.first_aired is not None
        assert show.first_aired.year == 2024

    def test_first_aired_none(self) -> None:
        show = TraktShow(title="Test", first_aired=None)
        assert show.first_aired is None

    def test_extra_fields_ignored(self) -> None:
        show = TraktShow(title="Test", unknown_field="value")  # type: ignore
        assert show.title == "Test"


class TestTraktMovie:
    def test_minimal_movie(self) -> None:
        movie = TraktMovie(title="Test Movie")
        assert movie.title == "Test Movie"

    def test_released_date_parsing(self) -> None:
        movie = TraktMovie(title="Test", released=date.fromisoformat("2024-06-15"))
        assert movie.released == date(2024, 6, 15)

    def test_released_none(self) -> None:
        movie = TraktMovie(title="Test", released=None)
        assert movie.released is None


class TestTraktWatchedShow:
    def test_plays_and_seasons(self) -> None:
        watched = TraktWatchedShow(
            plays=2,
            show=TraktShow(title="Test"),
            seasons=[
                TraktWatchedSeason(
                    number=1,
                    episodes=[
                        TraktWatchedEpisode(number=1, plays=2),
                        TraktWatchedEpisode(number=2, plays=1),
                    ],
                )
            ],
        )
        assert watched.plays == 2
        assert len(watched.seasons) == 1
        assert len(watched.seasons[0].episodes) == 2

    def test_datetime_parsing(self) -> None:
        watched = TraktWatchedShow(
            plays=1,
            last_watched_at=datetime.fromisoformat("2024-01-15T20:00:00.000Z"),
            show=TraktShow(title="Test"),
        )
        assert watched.last_watched_at is not None
        assert watched.last_watched_at.year == 2024


class TestTraktWatchedMovie:
    def test_plays(self) -> None:
        watched = TraktWatchedMovie(
            plays=3,
            movie=TraktMovie(title="Test Movie"),
        )
        assert watched.plays == 3


class TestTraktRating:
    def test_rating_value(self) -> None:
        rating = TraktRating(
            rating=8,
            type="show",
            show=TraktShow(title="Test"),
        )
        assert rating.rating == 8

    def test_rated_at_parsing(self) -> None:
        rating = TraktRating(
            rating=9,
            rated_at=datetime.fromisoformat("2024-03-10T12:00:00.000Z"),
        )
        assert rating.rated_at is not None


class TestTraktSearchResult:
    def test_show_result(self) -> None:
        result = TraktSearchResult(
            type="show",
            score=100.0,
            show=TraktShow(title="Found Show"),
        )
        assert result.type == "show"
        assert result.show is not None
        assert result.movie is None


class TestTraktUser:
    def test_user_fields(self) -> None:
        user = TraktUser(username="tester", name="Test User")
        assert user.username == "tester"
        assert user.name == "Test User"
        assert user.private is False


class TestTraktWatchlistItem:
    def test_show_watchlist_item(self) -> None:
        item = TraktWatchlistItem(
            type="show",
            show=TraktShow(title="Watchlisted"),
            notes="Want to watch",
        )
        assert item.show is not None
        assert item.notes == "Want to watch"


class TestTraktHistoryItem:
    def test_history_item(self) -> None:
        item = TraktHistoryItem(
            id=1,
            watched_at=datetime.fromisoformat("2024-05-20T10:00:00.000Z"),
            action="watch",
            type="episode",
            episode=TraktEpisode(season=1, number=1, title="Pilot"),
        )
        assert item.watched_at is not None
        assert item.episode is not None
        assert item.episode.title == "Pilot"
