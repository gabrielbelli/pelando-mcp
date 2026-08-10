# pelando-mcp

[![CI](https://github.com/gabrielbelli/pelando-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/gabrielbelli/pelando-mcp/actions/workflows/ci.yml)
[![License: BSD-2-Clause](https://img.shields.io/badge/license-BSD--2--Clause-blue.svg)](./LICENSE)

MCP server for **pelando.com.br**, the Brazilian community deal board. It answers the question a
price comparator structurally cannot: **did the crowd believe this discount?**

Pelando's temperature score **goes negative** when users judge a deal fake or the price inflated
before the "promotion". No other Brazilian price source exposes that, and — as of August 2026 — no
other MCP server exposes Pelando at all.

See [`PLAN.md`](./PLAN.md) for the full design, the reconnaissance behind it, and the source-selection
decision.

## What it is, and what it is not

This is a **community deal reader**, not a price comparator. Pelando has no product catalogue —
verified four ways, see [`PLAN.md` §2](./PLAN.md#2-the-scope-decision-community-only) — so the
server does not pretend otherwise:

| It can tell you | It cannot tell you |
|---|---|
| What users posted, at what price, from which store | What a product costs on a shelf right now |
| Whether the crowd upvoted or **downvoted** it | Every store selling product X, with prices |
| What the comment thread says is wrong with it | A 30/90-day price curve |
| Coupon codes and which store has active deals | Product specs, or anything about a product nobody posted |

Coverage is exactly what the community posts. Even mainstream terms legitimately return nothing:
`iphone 16 pro` currently has **zero** live promotions against ~99 archived ones. That is a real
answer, not a failure.

## Tools

| Tool | Purpose |
|------|---------|
| `search_deals` | Free-text deal search. Weak matches are demoted to `related`, not hidden. |
| `browse_feed` | Pelando's own feeds, optionally scoped to a community (`tech-lover` for electronics). |
| `get_deal` | One deal by UUID **or slug** — a pasted `/d/<slug>` URL works directly. |
| `get_deal_comments` | The comment thread, where a bad posting gets corrected. |
| `assess_deal_quality` | **The point of the project.** The crowd's verdict, with its evidence. |
| `search_stores` | Merchant lookup: live promotion counts and top coupons. |
| `get_store_coupons` | Literal redeemable codes, with prose masquerading as codes filtered out. |
| `list_communities` | The 11 communities — the site's only taxonomy. |
| `ping` | Liveness. |

### `assess_deal_quality`

Every signal is already in payloads we fetch, so it costs at most one extra request. Real output,
against a live deal:

```json
{
  "title": "[REEMBALADO] GeForce RTX 5070 OC 12GB",
  "verdict": "crowd_rejected",
  "temperature": -229,
  "price": 4129.0,
  "store_name": "Terabyte",
  "warnings": [
    "Negative temperature (-229). On Pelando that usually means users judged the discount fake,
     the price inflated beforehand, or the listing misleading.",
    "The title declares this as 'reembalado'. Do not compare it against new-unit prices."
  ]
}
```

It returns a verdict **with its evidence attached**, never a bare score. A score invites the calling
model to quote it as fact; evidence invites it to explain, and lets you disagree.

## Quick start (local, venv)

Requires Python ≥ 3.12.

```bash
python3 -m venv venv
./venv/bin/pip install -e ".[dev]"
./venv/bin/pytest -m "not live"     # offline, runs against checked-in fixtures
./venv/bin/pelando-mcp              # runs the MCP server over stdio
```

End-to-end check against the live site:

```bash
./venv/bin/python scripts/verify_all_tools.py     # writes to data/verify/
```

## Docker

Multi-arch images (linux/amd64, linux/arm64) are published on every push to `main`:

```bash
docker pull ghcr.io/gabrielbelli/pelando-mcp:latest
docker run --rm -i -v pelando-mcp-data:/data ghcr.io/gabrielbelli/pelando-mcp:latest
```

Or build locally:

```bash
docker compose build
docker compose run --rm pelando-mcp
```

A named volume holds the sqlite cache at `/data/cache.sqlite`.

> **Run this from home, not from a cloud host.** Cloudflare fronts Pelando and blocks **datacentre
> IP ranges outright** — a 403 interstitial on every request, `robots.txt` included, no matter how
> honest the User-Agent is. Measured: the identical client returns 200 from a residential connection
> and 403 from a GitHub Actions runner. So the image is fine on a laptop, home server or NAS, and
> will not work on a VPS. Treat that as a constraint to respect rather than a puzzle to route around
> with a proxy — see [Politeness](#politeness-and-why-pelando).

## Wiring into Claude

### Docker (recommended)

```json
{
  "mcpServers": {
    "pelando": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "-v", "pelando-mcp-data:/data", "ghcr.io/gabrielbelli/pelando-mcp:latest"]
    }
  }
}
```

### Local venv

```json
{
  "mcpServers": {
    "pelando": {
      "command": "/absolute/path/to/pelando-mcp/venv/bin/pelando-mcp"
    }
  }
}
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `PELANDO_USER_AGENT` | `pelando-mcp/0.1 (+repo url)` | Sent on every request. See below. |
| `PELANDO_RATE_LIMIT_RPS` | `1.0` | Max requests/sec. |
| `PELANDO_RATE_LIMIT_BURST` | `3` | Token-bucket burst. |
| `PELANDO_CACHE_PATH` | `/data/cache.sqlite` (Docker) / `data/cache.sqlite` (local) | sqlite cache. |
| `PELANDO_LOG_LEVEL` | `INFO` | structlog level. |

## Politeness, and why Pelando

Pelando was chosen on compliance grounds as much as data grounds. Zoom, Buscapé, Bondfaro, Mercado
Livre and Hardmob all name `ClaudeBot` / `Claude-User` / `Anthropic-AI` under `Disallow: /` —
Zoom's under a heading reading *"Bloqueio de Scrapers de Inteligência Artificial (Proteção do
catálogo e dados de preço)"*. Pelando's `robots.txt` is `Allow: /`, with disallows confined to
logged-in pages, and its Terms of Use carry no anti-crawler clause.

Practically, that means:

- **The User-Agent identifies us truthfully** rather than impersonating Chrome. Cloudflare blocks
  bot UA strings — including `python-httpx`, our own library — but an honest self-identifying UA
  returns 200. If this server ever becomes a nuisance, the operator can email us instead of
  blocking us. Don't replace it with a browser string.
- **Cloudflare also blocks by IP range, not only by UA.** Datacentre ranges get a 403 interstitial
  however honest the UA is, which is why this runs from a home connection and why the live contract
  tests are not in CI. A residential proxy would defeat that block; we don't ship one.
- **1 req/s, concurrency 1**, with aggressive sqlite caching. Measured headroom is much larger;
  that is not a reason to use it.
- `robots.txt` is fetched at startup and paths are **actually evaluated** against it.
- The **merchant's own link** is surfaced, never Pelando's affiliate redirect. A tool that presents
  itself as neutral should not silently monetise your click for a third party.
- No login, no writes, no background polling, no bulk crawling.

## Layout

```
src/pelando_mcp/
├── server.py        # MCPServer entry, registers tools
├── client.py        # httpx async, honest UA, token bucket, retries, robots, cache
├── api.py           # typed calls; validates params the API would silently ignore
├── models.py        # pydantic v2 — nullability mirrors observed live payloads
├── normalise.py     # condition detection + relevance filtering
├── quality.py       # the crowd-verdict heuristic
└── tools/           # MCP tool registrations
tests/
└── fixtures/        # real captured JSON + HTML for offline parser tests
scripts/
└── verify_all_tools.py
```

## Notes

- Data comes from an **undocumented internal JSON API**. It has no contract and no deprecation
  policy, so `pytest -m live` hits the real endpoints and fails on schema drift. A scraper does not
  break loudly — it starts returning "no deals found" and lies to you. Run it by hand every so
  often, from home; if the edge blocks the network it **skips rather than fails**, because a 403
  says nothing about the schema.
- Deal `status` is maintained by users and moderators, **not verified against the merchant**. An
  "active" deal can be long dead at the shop.
- Titles are free text. `[REEMBALADO]`, `usado` and `open box` are detected and flagged; a silent
  title is reported as `unknown` condition, never as new.
- Prices are BRL, exclude frete unless `free_shipping` is true, and `0` is a real price (free games).

## Licence

BSD 2-Clause — see [`LICENSE`](./LICENSE).
