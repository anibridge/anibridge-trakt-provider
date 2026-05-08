"""Trakt provider configuration."""

from typing import Annotated

import msgspec


class TraktListProviderConfig(msgspec.Struct, kw_only=True):
    """Configuration for the Trakt list provider."""

    token: Annotated[
        str,
        msgspec.Meta(description="Trakt refresh token for authentication."),
    ]
    client_id: Annotated[
        str,
        msgspec.Meta(description="Trakt API client ID for authentication."),
    ] = "fab91d3719c4206245850c46022ba5a571677ee62a886cfd8da8fc93db4e9f7c"
    client_secret: Annotated[
        str,
        msgspec.Meta(description="Trakt API client secret for authentication."),
    ] = "d58b8bfcc63f8e372ff932f78c3ff5ebad0a2c99910a2cce380bf313808e2bbd"
    rate_limit: (
        Annotated[
            int,
            msgspec.Meta(
                ge=1,
                description=(
                    "Maximum number of API requests per minute. "
                    "Use null to rely on the shared global default limit."
                ),
            ),
        ]
        | None
    ) = None
