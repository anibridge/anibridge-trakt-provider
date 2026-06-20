"""Pytest fixtures shared across the Trakt provider test-suite."""

from collections.abc import Generator

import pytest
from anibridge.utils.limiter import Limiter


@pytest.fixture(autouse=True)
def disable_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    previous = Limiter.DISABLED
    Limiter.DISABLED = True
    yield
    Limiter.DISABLED = previous
