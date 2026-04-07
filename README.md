# anibridge-trakt-provider

An [AniBridge](https://github.com/anibridge/anibridge) provider for [Trakt](https://trakt.tv/).

_This provider comes built-in with AniBridge, so you don't need to install it separately._

## Configuration

```yaml
list_provider_config:
  trakt:
    token: ...
    # client_id: "fab91d3719c4206245850c46022ba5a571677ee62a886cfd8da8fc93db4e9f7c"
    # rate_limit: null
```

### `token`

`str` (required)

Your Trakt OAuth refresh token. You can generate one [here](https://anibridge.eliasbenb.dev?generate_token=trakt).

### `client_id`

`str` (optional, default: `"fab91d3719c4206245850c46022ba5a571677ee62a886cfd8da8fc93db4e9f7c"`)

Your Trakt API client ID. The default value is AniBridge's official Trakt application ID. You can create your own at [trakt.tv/oauth/applications](https://trakt.tv/oauth/applications).

### `rate_limit`

`int | None` (optional, default: `null`)

The maximum number of API requests per minute.

If unset or set to `null`, the provider will use a default global rate limit of 1000 requests per 5 minutes (matching Trakt's official rate limit). This global limit is shared across all Trakt provider instances. If you override the rate limit, a local per-instance limiter is created instead.
