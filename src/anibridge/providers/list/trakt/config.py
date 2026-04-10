"""Trakt provider configuration."""

from pydantic import BaseModel, Field


class TraktListProviderConfig(BaseModel):
    """Configuration for the Trakt list provider."""

    token: str = Field(
        default=...,
        description="Trakt refresh token for authentication.",
    )
    client_id: str = Field(
        default="fab91d3719c4206245850c46022ba5a571677ee62a886cfd8da8fc93db4e9f7c",
        description="Trakt API client ID for authentication.",
    )
    client_secret: str = Field(
        default="d58b8bfcc63f8e372ff932f78c3ff5ebad0a2c99910a2cce380bf313808e2bbd",
        description="Trakt API client secret for authentication.",
    )
    rate_limit: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Maximum number of API requests per minute. "
            "Use null to rely on the shared global default limit."
        ),
    )
