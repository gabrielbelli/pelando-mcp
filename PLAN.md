# pelando-mcp — Plan

An MCP server for **pelando.com.br**, the Brazilian community deal board. It answers the question a
price comparator structurally cannot: **did the crowd believe this discount?**

Scope is deliberately narrow: **community only**. Pelando has no product catalogue, so this server
does not pretend to be one. See [§2](#2-the-scope-decision-community-only) for why.

---

## 1. Site reconnaissance (verified 2026-08-09)

Every claim below was checked against the live site. Where something was *not* found, the negative is
recorded too — an absence proven by a 404 is worth as much as a feature.

### Access and compliance

`https://www.pelando.com.br/robots.txt` (390 bytes):

```
User-agent: Baiduspider     Disallow: /
User-agent: PetalBot        Disallow: /
User-agent: ManusBot        Disallow: /

User-agent: *
Allow: /
Disallow: /postar  /meus-alertas  /promocoes-salvas
Disallow: /configuracoes  /cashback  /atendimento  /meu-perfil

Sitemap: https://www.pelando.com.br/sitemaps/sitemap.xml
```

- `Allow: /` for the generic group. Every path this server touches — `/busca/`, `/d/`, `/c/`,
  `/cupons-de-descontos/`, the sitemaps, and the `api-web` host — is permitted.
- The Disallow list is exactly the **logged-in surface**. This server never authenticates, so it
  never has cause to go there. `/cashback` is disallowed and is therefore simply out of scope.
- The blocklist is **curated by a human** — Baiduspider, PetalBot and ManusBot were added by name.
  That is a live list someone maintains, which is the strongest possible argument for being polite.
- The Terms of Use at `/sobre/termos-de-uso` (16k chars, read in full) contain **no anti-crawler,
  anti-bot or anti-mineração clause** — only a generic IP reservation.

> This mattered enormously in source selection. Zoom, Buscapé, Bondfaro, Mercado Livre and Hardmob
> all name `ClaudeBot` / `Claude-User` / `Anthropic-AI` under `Disallow: /` — Zoom's under a heading
> that reads *"Bloqueio de Scrapers de Inteligência Artificial (Proteção do catálogo e dados de
> preço)"*. Pelando is the only Brazilian price-bearing source found that both permits us and has
> better data for the question we care about. It was picked on merit, not preference.

### The JSON API

Host `https://api-web.pelando.com.br`. NestJS, REST, **unauthenticated**, undocumented. No GraphQL
(the string appears zero times in every bundle fetched; `POST /graphql` 404s).

| Purpose | Call |
|---|---|
| Deal search | `GET /feed/search?term=&size=&page=&hideExpired=&kind=promotion&sortOption=` |
| Deal detail | `GET /deals/{uuid}` |
| Comments | `GET /deals/{uuid}/comments?limit=` |
| Store lookup | `GET /stores/search?term=` |
| Hot feed | `GET /feed/v2/hottest?limit=&hideExpired=` |
| Community feed | `GET /feed/v2/recents?limit=&hideExpired=&communitySlug=` |

Envelope: `{"data": {"deals": [...], "pageInfo": {"page": n, "total": N}}, "timestamp": ...}`.
`size` is capped at 50 (`size=100` → 400 `"size must not be greater than 50"`).

**`kind=promotion` is mandatory on every search.** Without it roughly a third of an electronics
result set is `kind=discussion` rows carrying `price: null` and `store: null`. The parameter is
undocumented and Pelando's own client never sends it, so the code also filters defensively on
`price` and `store` being non-null — the param is an optimisation, not the guard.

### The deal object

`id` (uuid) · `slug` · `title` · `temperature` · `commentCount` · `date` · `price` ·
`discountPercentage` · `discountFixed` · `freeShipping` · `status` · `createdAt` ·
`firstApprovedAt` · `lastActivityAt` · `cashback` · `sourceUrl` · `kind` · `userVote` ·
`lastComment` · `imageUrl` / `imageSrcset` · `store{id,name,slug,logo,linkingEnabled,pageEnabled}` ·
`author{id,nickname,topCreator,isCreator,profilePicture}`

No `productId`, `sku`, `gtin`, `ean`, `brand`, `model` or `variant`. Exactly **one store per deal**.

### The comment object

`{totalComments, comments[{id, content, user{isAdmin,isCreator,specialty},
reactions{likeCount, hahaCount, usefulCount}, replyToId, replies[], sensitive, status, deletedAt,
createdAt}]}`

There is no report or flag field. `hahaCount > likeCount` is the crowd laughing at a deal, and
paired with a negative temperature it is the usable fake-discount heuristic.

### SSR surface (fallback + the bits with no API)

Astro with Preact islands. **No `__NEXT_DATA__`, no `/_next/data/` route.**

| Path | Contents |
|---|---|
| `/busca/{term}` | Search results. Term is a **path segment**, not a query param (`/busca?q=` 404s); spaces become `+`. |
| `/d/{slug}-{hash4}` | Deal page. Price is in `<title>`: `Por R$ 6.637: …`. JSON-LD is `DiscussionForumPosting`. |
| `/c/{slug}` | Community feed. |
| `/r/{slug}-{hash4}` | Pelando's own creator **video** reviews. No rating, no price, no offers. |
| `/cupons-de-descontos/{store}` | Coupon hub with literal codes — **5 partner stores only**. |
| `/busca/{term}/lojas` | Store cards with `couponCount` / `promotionCount` and top coupons. |

The site's own search contract, read out of `SearchFeedContent.*.js`:
`{query, page, size, hideExpired, sortOption}` → `{deals[], pageInfo{page,total}}`.
**One filter exists (`hideExpired`) and three sorts.** Everything else must be done client-side.

Two things limit the SSR route badly enough to keep it a *degraded* fallback rather than a peer:

- Deal-card CSS classes are build-hashed module names (`_deal-card-stamp_15l5n_25`) and the Astro
  scoped attributes rotate with them. The stable channel is the JSON in `<astro-island props="…">`,
  not selectors.
- **Only 5 deals are server-rendered per search**, and the HTML carries no page-2 URL and no
  cursor. The rest arrive by client-side fetch. So the fallback has a hard ceiling of 5 results
  with no pagination handle — enough to keep the server answering during an API outage, not enough
  to be an equivalent path.

### Sitemaps — the authoritative entity map

`/sitemaps/sitemap.xml` (269 entries, 10 distinct shard names):

```
131 × promotions_YYYY_MM.xml   (2020-08 → 2026-07)
130 × coupons_YYYY_MM.xml
      promotions_recent.xml  coupons_recent.xml  feed.xml
      communities.xml  discussions.xml  reviews.xml  creators.xml  stores.xml
```

The 11 canonical communities: `tech-lover` · `mundo-gamer` · `para-meu-lar` · `tudo-gratis` ·
`para-elas` · `para-eles` · `cultura` · `achadinhos-importados` · `e-meme-ou-promo` ·
`esporte-e-vida` · `para-minha-familia`. The electronics ones are **`tech-lover`** and
**`mundo-gamer`**. There is no finer taxonomy; `deal.categoryId` is a bare number with no label
anywhere in the payload.

### User-Agent behaviour

Cloudflare maintains a **bot-UA string blocklist**: `curl/8.x` and `python-requests/2.31` hard-403
with the *"Sorry, you have been blocked"* interstitial. An empty UA returns 200 — so it is string
matching, not fingerprinting, and `python-httpx/x.y` must be assumed to be on the list.

An honest, self-identifying UA returns 200 on both hosts:

```
pelando-mcp/0.1 (+https://github.com/gabrielbelli/pelando-mcp)
```

**We identify ourselves truthfully rather than impersonating Chrome.** That is what separates a
defensible integration from a covert one, and it is the reason this source was chosen. The UA is set
in the client constructor, never per call.

---

## 2. The scope decision: community only

Pelando has **no product entity**, and this was established four independent ways:

1. The sitemap index enumerates the site's entire entity vocabulary — promotion, coupon, discussion,
   review, creator, store, community. There is no `products` shard. A site with a catalogue would
   shard it.
2. The deal object has **no product foreign key**. Two postings of the same phone share no id, no
   slug fragment, nothing to join on.
3. `GET /products/search` returns the NestJS router's own 404 (`"Cannot GET /products/search"`) —
   the namespace does not exist. Contrast with `/feed/search?kind=nonsense`, which returns a
   well-formed empty result: the API answers questions it understands and 404s ones it does not.
4. `kind=product` is byte-identical to `kind=nonsense` — an empty deals array.

So the "Buscapé half" could only ever be **synthesised by us**: infer a product key from free-text
titles, accumulate our own price observations in sqlite, and present the aggregate. That was designed
and then **cut**. The reasons:

- It would be the bulk of the engineering for the smaller part of the value.
- Every archived posting is `status: expired`. The honest phrasing of any such output is *"the
  cheapest price a Pelando user was once seen to post"* — which is close enough to *"the cheapest
  price"* that a calling model will blur the two, and the user gets a confidently wrong number.
- The one thing no other MCP server in existence has is the **crowd's verdict**. Diluting that into
  a mediocre comparator trades a unique product for a worse copy of one that already exists.

What remains is the archive as a **plain read**: `include_expired=True` on search returns past
postings with their prices and dates, unaggregated. "Por quanto é que já postaram isto" is a
community question, and answering it needs no synthesis engine — just the parameter.

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ MCP server (FastMCP)                                         │
│                                                              │
│  Judgement tool    assess_deal_quality                       │
│      │                                                       │
│  Read tools        search / feeds / deal / comments /        │
│                    stores / coupons                          │
│      │                                                       │
│  Client            httpx async · honest UA · token bucket    │
│      │             · retries · sqlite cache                  │
│      │                                                       │
│  Adapters          api-web JSON (primary) · SSR HTML (fallback)
└──────────────────────────────────────────────────────────────┘
```

Both adapters normalise into the **same** pydantic models, so a caller cannot tell which one served
a request. The SSR adapter is tested in CI, not aspirational — an untested fallback is a fiction.

### Module layout

```
src/pelando_mcp/
├── server.py          # FastMCP entry, registers tools
├── client.py          # httpx async, token bucket, retries, cache
├── cache.py           # sqlite k/v with per-resource TTLs
├── models.py          # pydantic v2: Deal, Store, Author, Comment, Coupon, Community
├── normalise.py       # condition detection + relevance filtering
├── quality.py         # the deal-quality heuristic
├── parsers/           # SSR HTML parsers (selectolax)
└── tools/             # MCP tool registrations
tests/
└── fixtures/          # real captured JSON + HTML, checked in
```

### Stack

Python 3.12 · httpx · pydantic v2 · selectolax · sqlite · structlog · mcp (FastMCP) ·
pytest + respx. Identical to the sibling `cp-mcp` project, deliberately.

---

## 4. Tool surface (v1)

| Tool | Purpose |
|---|---|
| `search_deals(query, kind="promotion", include_expired=False, sort="hot", page=1, size=20, store=None, community=None, min_temperature=None, max_price=None, free_shipping=None)` | Free-text deal search. Everything past `size` is filtered client-side — the API offers only `hideExpired` and three sorts. |
| `browse_feed(feed="hottest", community=None, size=20)` | The site's own sorted feeds, optionally scoped to one of the 11 communities. `tech-lover` is the electronics firehose. |
| `get_deal(id_or_slug)` | One deal's full record: price, store, coupon code, status, timestamps, author. |
| `get_deal_comments(id_or_slug, limit=50, include_replies=True)` | The comment thread — the crowd's verdict, and the main source of correction on a bad deal. |
| `assess_deal_quality(id_or_slug)` | **The reason this server exists.** Rolls the scepticism signals into one structured verdict. |
| `search_stores(query, include_coupons=True)` | Merchant lookup: live `promotionCount` / `couponCount` and top coupon codes. |
| `get_store_coupons(store, include_expired=False)` | Literal redeemable codes. Hub pages for 5 partner stores; `coupons_recent.xml` backs every other store. |
| `list_communities()` | The 11 slugs. Hardcoded enum, refreshed from `communities.xml`. |
| `ping()` | Liveness + runtime config. |

### `assess_deal_quality` — the differentiator

Every input is already in payloads we fetch, so it costs at most one extra request:

| Signal | Reading |
|---|---|
| `temperature` **< 0** | The crowd actively voting a listing down. Live examples pulled during recon: RTX 5070 at **−229** and **−357 graus**. Hard-flagged, never quietly listed. |
| `hahaCount > likeCount` | They are laughing at it. |
| `usefulCount` | Someone posted a genuine correction worth reading. |
| `author.topCreator` / `isCreator` | Poster reputation. A new account posting a too-good price reads differently from a top creator. |
| `status` / `expiredAt` / `firstApprovedAt` | Age decays confidence. A 3-hour-old deal is a different claim from a 3-day-old one. |
| Title condition tokens | `REEMBALADO` at a suspiciously low price explains itself. |

Output is a structured verdict with the evidence attached — never a bare score. The user should be
able to see *why*, and disagree.

---

## 5. The normaliser (deliberately small)

With the comparator cut, this shrinks from an engine to a cleaning function. Two jobs:

1. **`detect_condition(title)`** — `REEMBALADO`, `usado`, `recondicionado`, `open box`, `vitrine`,
   `seminovo`. Accent- and case-insensitive. Defaults to **`unknown`, never `new`** — the server must
   never report an open-box price as a new-unit price because a title was silent.
2. **`is_relevant(query, title)`** — roughly a quarter of raw results for a phone query are cases,
   cables and chargers. Requires model and capacity tokens to agree before calling something a match;
   everything else is returned demoted as *related*, not silently dropped.

Titles are free-text community input and look like this:

```
[ PRIME ] Apple iPhone 16 (512 GB) – Preto
[REEMBALADO] GeForce RTX 5070 OC 12GB
(MELI 8667,00) Apple iPhone 16 Pro Max
[Moedas R$22] Capa protetora
```

Leading bracket/paren blocks are stripped into a `tags` list rather than parsed as part of the name.

---

## 6. Caching, rate limiting, politeness

| Resource | TTL |
|---|---|
| Search / feeds | 15 min |
| Deal detail | 1 h |
| Comments | 30 min |
| Stores, coupons | 24 h |
| Communities, robots.txt | 7 d |

- **1 req/s token bucket, concurrency 1.** Measured headroom is far larger (30 serial requests at
  3.5 req/s all returned 200), but ~20 concurrent calls trip a 429 plus a `Just a moment` challenge.
  Multi-term lookups are serialised, never fanned out.
- Any response body containing `Just a moment` is treated as a **block, not content**: exponential
  backoff from 5s, 3 retries, then fall through to the SSR adapter.
- `robots.txt` is fetched at startup (24h TTL) and paths are **actually evaluated** against it. It
  costs nothing and it is the difference between respecting robots and assuming.
- `sourceUrl` (the direct merchant link) is surfaced, **never** `redirectUrl` — the latter is a
  `dpl.pelando.com.br/r/<JWT>` affiliate hop. A tool presenting itself as neutral should not silently
  monetise the user's click for a third party.
- No bulk sitemap crawl. No background polling. Request volume stays proportional to actual queries.

---

## 7. Docker

Multi-stage build on `python:3.12-slim`, non-root user, stdio transport, sqlite cache on a named
volume at `/data/cache.sqlite`. Multi-arch (amd64 + arm64) published to GHCR by CI on every push to
`main`. No system libraries needed — there is no PDF renderer here, so the image is smaller than
`cp-mcp`'s.

---

## 8. Test plan

Parser tests run **offline against real captured fixtures** in `tests/fixtures/` — `search_rtx5070.json`,
`search_mixed_kinds.json`, `search_empty.json`, `deal_detail.json`,
`deal_detail_negative_temp.json`, `deal_comments.json`, `archive_iphone16pro.json`, `stores_*.json`,
`feed_hottest.json`, `busca_rtx5070.html`, `deal_page.html`, `robots.txt`.

Cases that must pass before v1 ships:

| Case | Expected |
|---|---|
| `search_mixed_kinds.json` | Every `price: null` / `store: null` row is dropped, and the filtered count is strictly lower than the raw count. |
| `deal_detail_negative_temp.json` | Negative temperature produces a hard warning, not a quiet listing. |
| `search_empty.json` | Zero results is a clean empty answer, not an exception. |
| `[REEMBALADO]` title | `condition != new`, and it is excluded from any "new" framing. |
| Phone query with accessories | Cases and cables are demoted to *related*, not returned as matches. |
| `busca_rtx5070.html` | The SSR adapter yields the same `Deal` model as the JSON adapter. |

A **weekly live contract test** in CI hits three known terms and fails on schema drift. Without a
canary, a scraper does not break loudly — it starts returning "no deals found" and lies to the user.

---

## 9. Roadmap

- **Phase 0 — scaffolding.** Repo, venv, Dockerfile, CI, `ping`.
- **Phase 1 — read tools.** `search_deals`, `browse_feed`, `get_deal`, `get_deal_comments` +
  fixture-based parser tests.
- **Phase 2 — judgement.** `assess_deal_quality`, condition detection, relevance filtering.
- **Phase 3 — merchants.** `search_stores`, `get_store_coupons`, `list_communities`.
- **Phase 4 — resilience.** SSR fallback adapter (parsing `<astro-island props>`, capped at the 5
  server-rendered cards), contract canary, packaging.
- **Later.** Creator video reviews (`/r/`); a `meupc.net` adapter for PC-part price history (that
  site permits us — `Allow: /`, only `/link/` disallowed — and exposes 180 days of daily
  per-merchant history, which is the natural way to *verify* a Pelando claim).

---

## 10. What this server will not do

Stated here so it can be stated in the README, and in the tool descriptions themselves. A tool that
oversells its scope makes the calling model lie to the user on its behalf.

- **It cannot read a live shelf price.** It only ever sees the number a Pelando user typed when they
  posted. Every archived row is already expired.
- **There is no Buscapé grid.** No catalogue, no multi-store price table for a SKU. The only stores
  that appear are those where someone happened to post.
- **"Lowest price ever" is unanswerable.** Only "the lowest anyone posted, since 2020-08". A cheaper
  price nobody posted is invisible.
- **No price curve.** No upstream history exists in any form — no array, no historical-low field, no
  chart on any page.
- **No specs, no product database.** Deals carry a free-text title and nothing else.
- **Coverage is what the crowd posts.** Even mainstream terms return nothing: `iphone 16 pro`
  currently has **zero** live promotions, against ~99 archived ones.
- **No listing a store's promotions.** `search_stores` reports that KaBuM! has 177 active, but no
  store deal-feed endpoint exists anywhere.
- **No cashback rates.** `/cashback` is robots-disallowed, so the server will not fetch it.
- **No notifications.** MCP servers cannot initiate contact, and alert creation is auth-gated.

---

## 11. Open questions

1. **Personal use, or will the image be shared?** Distribution changes the politeness calculus and
   would be the moment to open a conversation with Pelando rather than hope nobody notices.
2. **Contact string in the User-Agent.** Currently the repo URL. A reachable email is stronger — if
   we ever become a nuisance, they can email us instead of blocking us.
3. **Should `assess_deal_quality` return a numeric score at all,** or only the structured evidence?
   A score invites the calling model to quote it as fact; evidence invites it to explain.
