# Writing Your Own Source on Top of vnstock-oapi

> **Language / Ngôn ngữ:** [Tiếng Việt](viet-nguon-cua-ban.md) | English  
> **Official Docs / Tài liệu chính thức:** [vnstocks.com/docs/vnstock-oapi](https://vnstocks.com/docs/vnstock-oapi)

`vnstock-oapi` handles the hard, repetitive parts across every source: reading credentials from environment variables,
`.env`, or the OS keyring; signing requests or keeping tokens in a machine-wide shared session store; full TLS
verification; a closed host allowlist; timeouts; honoring `429` rate limits; redacting secrets in logs and errors; and
returning the verbatim response body wrapped in an envelope with provenance metadata. Your package only declares what
is specific to the source and registers it with `oapi.register(...)`.

## Installation

```console
pip install git+https://github.com/vnstock-hq/vnstock-open-api.git
# or via the shared package index of the Vnstock ecosystem
pip install -U --extra-index-url https://vnstocks.com/api/packages vnstock-oapi
```

## Minimal Example

```python
# vnstock_oapi_demo/__init__.py
import vnstock_oapi as oapi
from vnstock_oapi import ext

assert ext.EXT_API == 1, "This package targets extension API version 1"

def _bars(symbol, *, start=None, end=None, interval="1D", limit=None, asset="equity"):
    return ext.Plan("bars", {"symbol": ext.symbol("demo", symbol), "from": start, "to": end})

SPEC = ext.SourceSpec(
    name="demo",
    title="Demo API",
    base_url="https://api.example.com/v1",
    hosts=frozenset({"api.example.com"}),
    credentials=(ext.CredentialField("api_key", "API_KEY"),),   # reads from DEMO_API_KEY
    auth=lambda source: ext.ApiKeyHeaderAuth(source, header="X-Api-Key"),
    ops={"bars": ext.Op("bars", "/bars/{symbol}", doc="https://example.com/docs/bars")},
    verbs={"ohlc": _bars},
)

TERMS = ext.Terms(
    source="demo",
    title="Demo API",
    terms_url="https://example.com/terms",
    docs_url="https://example.com/docs",
    checked_at="2026-11-01",
    summary=("Personal use only.", "No data redistribution."),
    note="",
)

oapi.register(SPEC, terms=TERMS, producer=("vnstock-oapi-demo", "0.1.0"))
```

```python
import vnstock_oapi_demo          # registration happens here, and only here
import vnstock_oapi as oapi

bars = oapi.demo.ohlc("FPT", start="2026-10-01")
bars[0].producer                  # {"name": "vnstock-oapi-demo", "version": "0.1.0"}
oapi.demo.raw("bars", symbol="FPT")
oapi.demo.preview("ohlc", "FPT")  # inspect the outgoing request with secrets redacted
```

## What You Can Use

Only the names listed in `vnstock_oapi.ext.__all__` form the public extension API. They change only when `EXT_API` is
incremented. Everything else under `vnstock_oapi.core` may change in any release.

| Group | Names |
| --- | --- |
| Declaration | `SourceSpec`, `Op`, `Plan`, `Builder`, `BodyError`, `Samples`, `VERBS`, `Terms`, `CredentialField` |
| Authentication | `PublicAuth`, `ApiKeyHeaderAuth`, `SignedAuth` (+ `SignHeaders`), `TokenAuth`, `OtpTokenAuth`, `TokenSet`, `Credentials`, `Auth`, `jwt_expiry`, `fallback_expiry`, `to_epoch_seconds` |
| Shared vocabulary | `canonical`, `native`, `symbol` (reads the `mapping` you pass to `register`) |
| Output | `Source`, `Envelope`, `Envelopes`, `redact` |

`verbs` accepts only the eight shared operations (`ohlc trades quote index foreign symbols calendar events`).
Source-specific functions go into `extras` and are callable via `oapi.<source>.<name>(...)`.

## The Safety Floor Applied to Your Source

A registered source runs through the exact same `Transport` as built-in sources: `https` only, declared hosts only, no
redirects, `GET` only (except token endpoints), 10/30-second timeouts, a single neutral User-Agent
(`Python/<version>`) that imitates no browser and does not name the library or project, and `429` waiting once for
`Retry-After`. In addition, `register` rejects declarations when:

| Rejected Condition | Why |
| --- | --- |
| Name collides with a built-in source or package attribute | `oapi.ssi` is always the built-in SSI connector of `vnstock-oapi` |
| Host belongs to a built-in source | One key has one active session. A second connector on the same host would log in separately and invalidate the existing session (e.g., SSI). If an operation is missing, contribute it to the built-in source |
| Any path segment is `order(s)`, `account(s)`, `position(s)`, `portfolio(s)`, `balance(s)`, or contains a trading token or account parameter (`{accountNo}`), including `login_path` and `refresh_path` | `vnstock-oapi` carries market data only |
| A credential field reads a private key | Private keys are only used to sign trading orders |
| `producer` is `vnstock-oapi` or empty | Envelopes must accurately identify the package that produced them |
| Missing `terms` with an `https` `terms_url` | Users of your package need a direct link to the upstream source's terms |
| `mapping` uses non-canonical values on the canonical side (`1h` instead of `1H`) | One shared vocabulary across the entire ecosystem |

The path rule checks **whole path segments**, so `order-book`, `orderbook`, `/trading/foreign`, and
`/trading/market/session` pass normally. If a genuine market-data path happens to match a restricted segment—such as
market-wide order statistics—declare that exact path with its reason:

```python
oapi.register(
    SPEC, terms=TERMS, producer=("vnstock-oapi-demo", "0.1.0"),
    market_paths={"/stocks/{symbol}/trading/orders": "Market-wide order statistics, not user orders"},
)
```

`market_paths` only accepts exact paths of declared `Op` entries (never prefixes) and requires a reason. Trading tokens
and account path parameters are never exempted. The reason is logged to `vnstock_oapi.registry` upon registration.

Registration takes effect only inside the Python process that called `register`. The `vnstock-oapi` CLI does not load
external registered sources.

## Responsibilities

- Source code declared via `register` is your responsibility. Envelopes carry your package's `producer`, not `vnstock-oapi`.
- You are responsible for reading and complying with the upstream source's terms of service, including rules on data
  storage, display, and redistribution.
- `vnstock-oapi` is licensed under the repository license ([LICENSE](../LICENSE)). The source code is publicly readable
  for inspection, auditing, and learning, but it is not an open-source license.
- Section 1.1 of the license permits publishing and sharing your extension package for free—including declaring
  `vnstock-oapi` as a dependency—without separate written permission, provided your package:
  - does not copy or bundle `vnstock-oapi` code (users install `vnstock-oapi` themselves);
  - is provided free of charge;
  - lets each end user connect directly with their own account and credentials, without running a proxy service that
    fetches data on behalf of others or redistributes data;
  - does not submit trading orders or access account information;
  - does not modify, disable, or circumvent the safety controls of `vnstock-oapi`;
  - clearly states that it uses `vnstock-oapi`, links to the upstream repository and license, and does not imply
    endorsement or official authorship by Vnstock.
- Commercial use, or any use outside the conditions above, requires separate written permission (Section 2).

## Testing Your Package

Use `httpx.MockTransport` so your test suite never makes live network calls:

```python
import httpx
from vnstock_oapi.core.transport import Transport   # for testing only

source = oapi.demo
source._transport = Transport(
    "demo", source.spec.hosts,
    client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"c": [1]}))),
)
```

