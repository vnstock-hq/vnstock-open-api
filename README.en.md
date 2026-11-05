# vnstock-oapi

> **Language / Ngôn ngữ:** [Tiếng Việt](README.md) | English  
> **Official Documentation / Tài liệu chính thức:** [vnstocks.com/docs/vnstock-oapi](https://vnstocks.com/docs/vnstock-oapi)

A unified client connector to authenticate and call official financial Open APIs using your own account credentials:
test an API in minutes, build your own research workflows on top of it, or feed raw records into Vnstock's automated
processing pipelines.

Part of the Vnstock ecosystem.

![License: Custom (Personal Use)](https://img.shields.io/badge/License-Custom%20(Personal%20Use)-lightgrey)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)

## What vnstock-oapi Is For

**1. Stop rewriting boilerplate connection code for every source.** Storing keys, logging in, sharing active sessions,
refreshing expired tokens, paginating responses, waiting on rate limits, and parsing error payloads are already
implemented and tested across every supported source. You reuse one client layer instead of maintaining separate scripts
whenever an upstream API changes.

**2. Evaluate an API in minutes before committing to it.** As soon as you have an API key, you can check whether the
key works, preview the exact outgoing request, inspect the fields returned by the upstream gateway, and see which
endpoints your account tier can access—without reading hundreds of pages of documentation or scaffolding a throwaway
project.

**3. Produce clean, provenance-tracked raw records for downstream processing.** Every API call returns the same envelope
structure containing the verbatim upstream payload alongside provenance metadata. You can write your own analysis
directly on top of it, or pass the envelopes into downstream libraries in the Vnstock ecosystem for normalization and
automated quantitative workflows.

## Why a Unified Connector Layer Is Needed

More brokerage firms and financial sources now offer official Open APIs to their customers, but each gateway works
differently:

- Some provide only REST endpoints and documentation; others ship custom Python SDKs.
- Some require a single API key; some exchange a key and secret for a short-lived bearer token; some require HMAC
  signatures on every request; and some prompt for a manual Smart OTP on login.
- Pagination, error codes, rate limits, and session lifetimes vary across every source.

When every developer writes bespoke connection code, the community duplicates the same plumbing and produces
incompatible data shapes. `vnstock-oapi` consolidates that transport and authentication layer into one place: adding a
source only requires a declarative specification (base URL, auth type, endpoints, and parameter mappings), while the
security floor and session management are shared.

## Direction & Architecture

The goal of `vnstock-oapi` is to serve as the standard client connector for Open APIs that a user is authorized to
access, acting as the entry point for downstream processing in the Vnstock ecosystem. This standard consists of three
parts included in this repository:

| Component | Location | Purpose |
| --- | --- | --- |
| Authentication patterns | `vnstock_oapi/core/auth.py` | Consolidates common login flows (API key, key + secret token exchange, request signing, OTP session) into reusable classes |
| Source specifications | `vnstock_oapi/sources/<source>.py`, `vnstock_oapi/spec/sources/<source>/` | Endpoints, parameters, symbol/interval vocabulary mappings, and terms of service summaries |
| Output envelope format | `vnstock_oapi/spec/envelope.v1.json` | Every API call yields a uniform record: the verbatim upstream payload plus provenance metadata describing where and when it was fetched |

```text
 source A: single API key         ─┐                                     ┌─▶ your own analysis code
 source B: key + secret → token   ─┼─▶ vnstock-oapi ─▶ result envelope ──┤
 source C: manual OTP per session ─┘                   (verbatim body)   └─▶ normalization & automation in Vnstock
                                                                             (in development)
```

The result envelope is the contract between data collection and data processing. In the Vnstock roadmap, the `vnstock`
library reads these envelopes directly to normalize them into tabular datasets, which downstream quantitative and
scheduling libraries consume automatically. Adding a source to `vnstock-oapi` makes it available to the entire
downstream pipeline without extra connection code.

This package focuses strictly on the connection layer: upstream payloads are preserved verbatim without renaming
columns or converting them into DataFrames.

## What vnstock-oapi Is — and What It Is Not

| What It Is | What It Is Not |
| --- | --- |
| A Python library and CLI tool running locally on your machine | Not a data vendor. Market data belongs to the upstream source |
| A connector that helps you fetch data via official Open APIs using your own account and keys | Not an intermediary server. Requests go directly from your machine to the source you choose |
| Strictly read-only: market data, instrument lists, corporate events | Never touches trading orders or account holdings. No function for order placement or account queries exists |
| Returns the verbatim upstream response with provenance metadata | Not a schema normalizer. Normalization belongs to downstream `vnstock` packages |
| Research software provided "as is" | Not investment advice |

The library never contacts `vnstocks.com` at runtime and sends zero telemetry or usage analytics. Locally, it stores
session tokens in your operating system's keyring (or a user-restricted local file) and writes output files only when
you explicitly call `save()` or run `dump`.

## Installation

Requires Python 3.10 or newer. Install from GitHub or via the Vnstock package index:

```console
pip install git+https://github.com/vnstock-hq/vnstock-open-api.git
pip install -U --extra-index-url https://vnstocks.com/api/packages vnstock-oapi
vnstock-oapi --version
```

If you cloned the repository locally, run `pip install .` from the repository root.

## License

`vnstock-oapi` is distributed under Vnstock's custom license, not an open-source license. The source code is published
here so you can inspect and verify how the library handles your API keys, login sessions, and data on your machine.

- Permitted for personal, educational, and research use.
- You may publish and share your own free extension packages that depend on `vnstock-oapi` under Section 1.1 of the
  license: do not bundle `vnstock-oapi` code, do not charge fees, require each user to connect with their own keys,
  never submit orders or fetch account data, and clearly attribute the project. See
  [docs/write-your-own-source.md](docs/write-your-own-source.md) ([Tiếng Việt](docs/viet-nguon-cua-ban.md)).
- Redistributing this software, using it commercially, or building a product whose primary value is fetching data on
  behalf of others requires separate written permission from Vnstock.
- Permission to use this software does not grant rights to the upstream data. Any data you fetch is governed by the
  terms you agreed to with the upstream source.

Full license text: [LICENSE](LICENSE).

## Quick Start

Once you have an API key, you can test a source immediately from the terminal without writing code. Replace
`<source>` with the source you picked from [Built-in Sources](#built-in-sources):

```console
vnstock-oapi keys set <source>               # store key in OS keyring (hidden input)
vnstock-oapi check <source>                  # verify credentials with a lightweight test call
vnstock-oapi ops <source>                    # list supported operations and raw endpoints
vnstock-oapi ohlc <source> FPT --preview     # preview outgoing request without sending; secrets redacted
vnstock-oapi ohlc <source> FPT --start 2026-10-01 -o fpt.jsonl
vnstock-oapi dump <source> --symbol FPT      # probe sample endpoints and generate a capability report
```

`dump` writes output under `samples/<timestamp>/`:

- `<source>/<name>.jsonl`: raw envelope records for each endpoint.
- `shapes.md`: field names and data types of every response **without values**—useful for designing downstream parsers
  without exposing raw data.
- `manifest.json`: summary of which endpoints succeeded, HTTP status codes, and any errors.

`.jsonl` files contain live data fetched with your personal account: keep them on your machine and never commit or
redistribute them.

In Python:

```python
import vnstock_oapi as oapi

src = oapi.source("<source>")     # the source you picked
bars = src.ohlc("FPT", start="2026-10-01", end="2026-10-30", interval="1D")
bars[0].body              # verbatim payload returned by the upstream source
bars[0].operation         # endpoint operation name
bars.save("fpt.jsonl")    # save to JSONL (one envelope per line)

src.preview("ohlc", "FPT")        # preview outgoing request (not sent), secrets redacted
src.raw("<operation>")            # call a documented upstream endpoint by name; list them with src.ops
print(src.terms)                  # summary of upstream terms with a link to the official text
```

Importing `vnstock_oapi` has zero side effects: it makes no network calls, reads no keys, and writes no files at import
time.

## Built-in Sources

| Source | Library Name | Authentication | Environment Variables |
| --- | --- | --- | --- |
| SSI FastConnect API | `ssi` | Key + secret exchanged for bearer token; auto-refreshed on expiry | `SSI_API_KEY`, `SSI_API_SECRET` |
| DNSE Lightspeed API | `dnse` | Key + secret; HMAC-signed per request, stateless | `DNSE_API_KEY`, `DNSE_API_SECRET` |
| TCBS iFlash Open API | `tcbs` | API key + manual Smart OTP entered on login; session up to 24 hours | `TCBS_API_KEY` |
| FHSC Open API | `fhsc` | Single API key sent per request | `FHSC_API_KEY` |
| Financial Modeling Prep | `fmp` | Single API key sent per request | `FMP_API_KEY` |
| Binance (public market data) | `binance` | No key required | None |

FMP covers international market data and does not include Vietnamese equities. Binance connects strictly to the public
market-data gateway published by Binance for unauthenticated access. To inspect what a source supports, run
`vnstock-oapi ops <source>`.

A source that needs an OTP (see the "Authentication" column) opens a session once, and all tools on your machine
share it until it expires:

```console
vnstock-oapi auth <source>      # prompts for the OTP (hidden input)
```

OTP codes are never read from environment variables or files.

The library resolves credentials in order: environment variables, a `.env` file in the current working directory, and
the OS keyring. No function accepts raw keys as call arguments. Credentials issued by a source for non-data purposes
(such as order-signing private keys) are never read.

## Shared Operations Across Sources

The same function names work across every source that supports the operation. If a source does not provide an
operation, the library raises `UnsupportedError` rather than returning empty results.

| Function | Description |
| --- | --- |
| `ohlc` | Candlestick / bar data (open, high, low, close, volume) |
| `trades` | Tick-by-tick matched trades |
| `quote` | Latest quote and order-book snapshot |
| `index` | Market index data |
| `foreign` | Foreign investor trading activity |
| `symbols` | Instrument / ticker directory |
| `calendar` | Trading calendar |
| `events` | Corporate events |

Common parameters: `symbol`, `start`, `end` (naive datetimes are interpreted as Vietnam time, `Asia/Ho_Chi_Minh`),
`interval` (`1m 3m 5m 15m 30m 1H 4H 1D 1W 1M`, default `1D`), `limit`, and `asset`. If a source does not support the
requested timeframe, `InputError` is raised; the library never resamples bars on its own. When an upstream endpoint
paginates responses, all pages are fetched automatically, with one envelope per page.

Beyond shared operations, you can call any documented upstream endpoint via `raw()`, or invoke source-specific helper
functions via `vnstock-oapi call <source> <function>`.

## Intraday Market Data via WebSocket

For DNSE, SSI, TCBS and Binance, the library can subscribe to intraday market data over the official WebSocket
endpoints published by each source, delivering frames at the upstream gateway's native latency. Only read-only market data channels are
permitted; order and account channels are blocked inside the client.

```console
pip install ".[stream]"
vnstock-oapi stream <source>    # list allowed market-data channels
```

## Output Envelope Format

Every REST call returns one or more `Envelope` records: the upstream payload is kept verbatim alongside provenance
metadata so you can audit where and when the record was fetched.

| Field | Description |
| --- | --- |
| `envelope_version` | Schema version, currently `"1"` |
| `source`, `operation`, `api_version` | Source identifier, operation called, and upstream API version |
| `request` | Request parameters sent. Never contains keys, signatures, tokens, or auth headers |
| `fetched_at`, `http_status` | Fetch timestamp and HTTP status code |
| `body` | Verbatim JSON payload from the source (or `body_base64` for non-JSON payloads) |
| `body_sha256` | SHA-256 digest of the raw response bytes for integrity verification |
| `producer` | Package name and version that created the envelope |
| `transport` | Hostname, protocol, latency, and request count |

If you fetched data using an upstream vendor's official SDK or your own HTTP client, you can wrap the payload into the
same envelope format:

```python
env = oapi.wrap("<source>", "<operation>", body=my_json, request={"symbol": "FPT"})
```

Full JSON Schema specification: [`vnstock_oapi/spec/envelope.v1.json`](vnstock_oapi/spec/envelope.v1.json).

## Security

- Credentials live in environment variables, `.env`, or the OS keyring; they are masked when printing objects,
  formatting strings, or pickling.
- All HTTP traffic flows through a single transport layer: full TLS verification, strict per-source host allowlists,
  read-only requests, and bounded timeouts.
- Processes on the same machine share one login session store so interactive notebooks and scheduled jobs do not
  invalidate each other's tokens.
- The library has no path that accepts an account password. A source that only signs in with the account password
  is not supported, because that password also opens the assets in the account.
- Never paste API keys into notebooks, source code, or AI chat prompts.

## Upstream Terms of Service

Always review a source's terms of service before use. The library includes a brief summary with section references and
links to the official documents:

```console
vnstock-oapi terms <source>
```

These summaries are provided for quick reference only; they do not replace the official terms and do not constitute
legal advice.

## Adding a Source

A built-in source consists of `vnstock_oapi/sources/<source>.py`, a `vnstock_oapi/spec/sources/<source>/` directory
containing `mapping.toml` and `terms.toml`, and one of the shared authentication classes. Every source is verified
against mock servers in the test suite.

To declare a custom source in your own package without modifying this repository, use `vnstock_oapi.ext` and call
`oapi.register(...)`. Your source runs under the same safety floor (`https` only, declared hosts only, read-only market
data), and resulting envelopes carry your package's producer metadata. See
[docs/write-your-own-source.md](docs/write-your-own-source.md) ([Tiếng Việt](docs/viet-nguon-cua-ban.md)).

To propose a new source or report a connection issue, open a GitHub issue with the endpoint name, parameters, and error
message—never include API keys, tokens, or real data fetched from your account.

## Development

```console
pip install -e ".[dev]"
pytest -q && ruff check . && mypy vnstock_oapi
```

View CLI commands with `vnstock-oapi --help`. Inspect a source's operations with `vnstock-oapi ops <source>`.

## About Vnstock

Vnstock is a personal automation ecosystem for investment research: Python toolkits running on your machine to connect
data sources, perform quantitative analysis with transparent formulas, and automate research workflows, paired with AI
assistants designed to write accurate code on the first run.

- Official Documentation: [vnstocks.com/docs/vnstock-oapi](https://vnstocks.com/docs/vnstock-oapi)
- Website: [vnstocks.com](https://vnstocks.com)
- GitHub: [github.com/vnstock-hq](https://github.com/vnstock-hq)

Vnstock is research software provided "as is" and does not constitute investment advice.
