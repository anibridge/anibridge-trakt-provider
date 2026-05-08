"""Pytest fixtures shared across the Trakt provider test-suite."""

from collections.abc import AsyncGenerator, Generator
from logging import getLogger
from typing import cast

import pytest
import pytest_asyncio
from anibridge.utils.limiter import Limiter
from anibridge.utils.types import ProviderLogger

from anibridge.providers.list.trakt.client import TraktClient
from anibridge.providers.list.trakt.list import TraktListProvider
from anibridge.providers.list.trakt.testing import FakeTraktClient


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
            "client_secret": "test-client-secret",
            "token": "test-token",
        },
        logger=cast(ProviderLogger, getLogger("anibridge.providers.list.trakt")),
    )
    provider._client = cast(TraktClient, fake_client)
    await provider.initialize()
    yield provider
    await provider.close()


@pytest.fixture(autouse=True)
def disable_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    previous = Limiter.DISABLED
    Limiter.DISABLED = True
    yield
    Limiter.DISABLED = previous
