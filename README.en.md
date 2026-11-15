# vnstock-oapi (Vnstock Open API)

[Tiếng Việt](README.md) · English

A Python library and command-line tool that runs on your machine to manage credentials, maintain sessions, and call official brokerage and financial Open APIs using your own account (Bring Your Own Key — BYOK).

Part of the [Vnstock Generation 6](https://vnstocks.com/the-he-6) ecosystem.

![License: Custom (Personal Use)](https://img.shields.io/badge/License-Custom%20(Personal%20Use)-lightgrey)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)

## Highlights

- **Direct BYOK source connectivity:** runs locally on your machine using the account and API credentials you registered with each source; requests go directly from your machine to the selected source without passing through any Vnstock relay server.
- **Unified authentication and AI-safe credential handling:** standardises diverse authentication flows (static API keys, key + secret token exchange and refresh, per-request `HMAC-SHA256` signing, manual Smart OTP sessions shared across local processes, and key-expiry tracking). No function accepts `api_key=...` as an argument; credentials load from environment variables → local `.env` → OS `keyring` and are wrapped in `Secret` so values are masked in `repr`, logs, f-strings, and `pickle`.
- **Inspect sources from the terminal before writing code:** `status`, `check`, `ops`, `--preview`, `dump`, and `terms` let you verify keys, preview redacted requests, and export response field structures (`shapes.md`, with zero raw values) safe to share with coding agents.
- **Verbatim responses wrapped in `Envelope`:** every call preserves the source's raw payload alongside request context and a `SHA-256` digest, stripped of credentials — ready to save as `.jsonl` or pass to `vnstock` Generation 6 (`Connector`) for normalisation into a `pandas.DataFrame`.
- **Strict read-only boundary:** connects only over verified `HTTPS` to each source's allowlisted hosts and performs read-only market and reference queries; order-signing keys (`PRIVATE_KEY`), order placement/cancellation, and personal account/portfolio endpoints are refused by design.

> Read the full design story and how `vnstock-oapi` fits into Vnstock Generation 6 (in Vietnamese): **[Vnstock Open API: Connect to Open APIs with Your Own Keys](https://vnstocks.com/blog/vnstock-oapi-ket-noi-open-api-chung-khoan)**.

## Scope and Boundaries

| What `vnstock-oapi` does | What `vnstock-oapi` does not do |
| :--- | :--- |
| Runs on your machine to connect to official Open APIs using your own account and keys | Not a data provider or data redistributor; operates no proxy or relay server for market data |
| Manages keys via OS keyring, `.env`, or environment variables and shares active sessions across local processes | Never accepts keys as function arguments; never sends source keys or usage telemetry to `vnstocks.com` |
| Executes read-only operations for supported market data, instrument directories, trading calendars, and corporate events | Never places, modifies, or cancels orders and never reads personal account balances or holdings |
| Preserves the source's verbatim response inside an `Envelope` with provenance metadata and a `SHA-256` hash | Does not rename columns, convert units, or flatten payloads (handled downstream by `vnstock` Generation 6) |
| Publishes source code under a custom licence for inspection, study, and personal research | Not open-source software; does not grant rights over third-party source data |

## Supported Sources

| Source | Identifier (`source`) | Authentication | Environment Variables |
| :--- | :--- | :--- | :--- |
| [SSI FastConnect API](https://developers.ssi.com.vn/) | `ssi` | Key + secret exchanged for a bearer token; automatic refresh | `SSI_API_KEY`, `SSI_API_SECRET` |
| [DNSE Lightspeed API](https://developers.dnse.com.vn/docs/guide/intro/register_guide/) | `dnse` | Key + secret signing every request with `HMAC-SHA256` (stateless) | `DNSE_API_KEY`, `DNSE_API_SECRET` |
| [TCBS iFlash Open API](https://developers.tcbs.com.vn/) | `tcbs` | API key plus manual Smart OTP entry when opening a session | `TCBS_API_KEY` |
| [FHSC Open API](https://developers.fhsc.com.vn/introduction) | `fhsc` | Single API key sent per request (tracks 30-day key rotation) | `FHSC_API_KEY` |
| [Financial Modeling Prep](https://intelligence.financialmodelingprep.com/pricing-plans?couponCode=thinhvu&utm_source=vnstock-official-site&utm_medium=vnstock-oapi&utm_campaign=thinhvu) | `fmp` | Single API key sent per request (international markets) | `FMP_API_KEY` |
| [Binance Spot Public API](https://developers.binance.com/docs/binance-spot-api-docs/rest-api) | `binance` | No key required (dedicated public market-data endpoint only) | None |

> The Financial Modeling Prep (FMP) sign-up link above includes a Vnstock referral code (20% discount on paid FMP plans; Vnstock may receive an affiliate commission). FMP's no-cost Basic plan does not require this link.

## Installation and Quickstart

Requires Python 3.10 or newer. Install from the Vnstock package index or directly from GitHub:

```bash
pip install -U --extra-index-url https://vnstocks.com/api/packages "vnstock-oapi>=0.1.0,<1"
# Or install directly from GitHub:
pip install git+https://github.com/vnstock-hq/vnstock-open-api.git
vnstock-oapi --version
```

### 1. Command Line (CLI)

```bash
vnstock-oapi keys set <source>               # store keys in OS keyring (hidden input); supports --profile
vnstock-oapi auth <source>                   # open a session (prompts for Smart OTP if required)
vnstock-oapi status                          # check key origins, session state, and key rotation offline
vnstock-oapi check <source>                  # run the lightest read call to verify credentials
vnstock-oapi ops <source>                    # list common verbs, source extras, and raw operations
vnstock-oapi ohlc <source> FPT --preview     # preview the outgoing request without sending it (keys masked)
vnstock-oapi ohlc <source> FPT --start 2026-10-01 -o fpt.jsonl
vnstock-oapi dump <source> --symbol FPT      # run sample calls, write JSONL + shapes.md (field paths & types only)
vnstock-oapi terms <source>                  # view technical summary and link to the source's official terms
```

`dump` writes `samples/<timestamp>/shapes.md` containing only field paths and data types — **never raw values** — so you can hand response schemas to an AI coding assistant without exposing data from your account.

### 2. Python API

Importing `vnstock_oapi` makes no network calls, reads no keys, and writes no files:

```python
import vnstock_oapi as oapi

src = oapi.source("<source>")
bars = src.ohlc("FPT", start="2026-10-01", end="2026-10-30", interval="1D")
bars[0].body                        # verbatim payload returned by the source
bars.save("fpt.jsonl")              # save as JSONL (file permissions restricted to your user account)

src.preview("ohlc", "FPT")          # inspect the request that would be sent (secrets masked)
src.raw("<operation>")              # call any declared native endpoint by name
print(src.terms)                    # technical summary and link to the source's terms
```

Combine with `vnstock` Generation 6 to normalise envelopes into a `pandas.DataFrame` (or wrap payloads fetched with your own code via `oapi.wrap`):

```python
from vnstock import Connector

df = Connector("<source>").equity("FPT").ohlcv("fpt.jsonl")
env = oapi.wrap("<source>", "<operation>", body=my_json, request={"symbol": "FPT"})
```

### 3. Intraday WebSocket Streams

Supported for `ssi`, `dnse`, `tcbs`, and `binance` across the market-data channels declared in `streams.toml`:

```bash
pip install "vnstock-oapi[stream]"
vnstock-oapi stream <source>    # list allowed market-data channels for the source
```

### 4. Registering Your Own Source (`oapi.register`)

Use `vnstock_oapi.ext` and `oapi.register(...)` to declare an additional Open API source in your own package without modifying this repository. Registered sources inherit keyring lookup, `Transport` safety controls, and `Envelope` output (stamped with your package's `producer`). See [docs/viet-nguon-cua-ban.md](docs/viet-nguon-cua-ban.md).

## Common Verbs and `Envelope` Format

8 common verbs share the same name across supported sources (raising `UnsupportedError` or `InputError` explicitly when a verb or interval is not supported by a source):

| Verb | Description |
| :--- | :--- |
| `ohlc` | Historical candlestick bars (`1m`, `3m`, `5m`, `15m`, `30m`, `1H`, `4H`, `1D`, `1W`, `1M`) |
| `trades` | Intraday tick-by-tick matched trades |
| `quote` | Latest bid/ask quotes and order book depth |
| `index` | Market index values and history |
| `foreign` | Foreign investor trading activity |
| `symbols` | Instrument directory and listings |
| `calendar` | Market trading dates |
| `events` | Corporate actions and event calendars |

Each response page is wrapped in an `Envelope` record (JSON Schemas: [envelope.v1.json](vnstock_oapi/spec/envelope.v1.json), stream frames: [frame.v1.json](vnstock_oapi/spec/frame.v1.json)):

| Field | Meaning |
| :--- | :--- |
| `envelope_version` | Schema version (`"1"`) |
| `source`, `operation`, `api_version` | Source identifier, operation/endpoint called, and API version |
| `request` | Query parameters sent (stripped of keys, signatures, tokens, and auth headers) |
| `fetched_at`, `http_status` | Retrieval timestamp (RFC 3339) and HTTP status code |
| `body`, `body_sha256` | Verbatim response payload (or `body_base64`) and its `SHA-256` digest |
| `producer`, `transport` | Producer metadata plus target host, protocol, latency, and request count |

## Licence and Source Terms

- **Software licence ([LICENSE](LICENSE)):** `vnstock-oapi` is released under Vnstock's custom software licence, not an open-source licence. The source code is published so you can inspect how credentials and network requests are handled and use the software for personal, educational, and research purposes. You may publish and share your own extension package that depends on `vnstock-oapi` at no charge provided it meets all conditions in Section 1.1 of the licence ([docs/viet-nguon-cua-ban.md](docs/viet-nguon-cua-ban.md)). Redistributing the software, commercial use, or building a product whose primary value is helping third parties retrieve data requires separate written permission from Vnstock.
- **Source data terms:** data retrieved using your account and keys is governed by the terms you agreed to with that source. Run `vnstock-oapi terms <source>` for a quick technical summary and a direct link to the source's official terms (provided for convenience only, not legal advice).

## Documentation and Development

- Overview blog post: [Vnstock Open API: Connect to Open APIs with Your Own Keys](https://vnstocks.com/blog/vnstock-oapi-ket-noi-open-api-chung-khoan)
- Official documentation: [Introduction](https://vnstocks.com/docs/vnstock-oapi/gioi-thieu) · [Installation & Keys](https://vnstocks.com/docs/vnstock-oapi/cai-dat) · [CLI Reference](https://vnstocks.com/docs/vnstock-oapi/dong-lenh) · [Python Guide](https://vnstocks.com/docs/vnstock-oapi/python)
- Writing your own source extension: [docs/viet-nguon-cua-ban.md](docs/viet-nguon-cua-ban.md)

```bash
pip install -e ".[dev]"
pytest -q && ruff check . && mypy vnstock_oapi
```

## About Vnstock

Vnstock is a personal-automation ecosystem for investment research: Python tools running on your machine for data connectivity, quantitative calculation with transparent formulas, and workflow automation, paired with standardised AI assistant documentation and a practitioner community.

- Website & Documentation: [vnstocks.com](https://vnstocks.com)
- GitHub: [github.com/vnstock-hq](https://github.com/vnstock-hq)

Vnstock licenses software and does not sell data; data availability and accuracy depend on each source. All software and documentation are provided as-is for research and reference, not investment advice.
