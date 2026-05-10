# Research Note: WorldMonitor Free-Source Architecture
*Date: 2026-04-03*  
*Scope: What the public WorldMonitor project suggests about organizing many free data/news sources for a monitoring system, with a focus on lessons for `industry_signal_radar`.*

## 1. Overview
WorldMonitor (koala73/worldmonitor) is an open-source global intelligence dashboard that aggregates **435+ RSS feeds**, **65+ external data sources**, and **45 map data layers** into a single browser-based interface. It is explicitly designed around the idea that most of the stack can run on free tiers and public APIs, with minimal backend infrastructure.

## 2. Source Layering & Feed Tiering
### 2.1 Credibility Tiers
WorldMonitor assigns every RSS feed a **4-tier reliability score**:
- **Tier 1**: Wire services (Reuters, AP) — highest weight in AI scoring.
- **Tier 2**: Major outlets (CNN, NYT, Bloomberg, FT).
- **Tier 3**: Specialized/defense publications (Military Times, USNI News, Oryx OSINT).
- **Tier 4**: Aggregators, blogs, regional sources.

State-affiliated sources (e.g., RT, Xinhua, IRNA) are included but visually tagged with a propaganda-risk flag. The focal-point / threat-classification algorithm weights alerts by tier: a Tier 1 breaking alert carries more signal weight than a Tier 4 blog post.

### 2.2 Category Layering
Feeds are grouped into **15 categories** (geopolitics, MENA, Africa, tech/AI, finance, energy, cyber, defense/intel, think tanks, etc.). The UI loads categories with bounded concurrency, so startup does not waterfall 435+ requests.

### 2.3 Data-Layer Separation
The project separates sources into **static layers** (GeoJSON/CSV shipped with the app: pipelines, cables, military bases, AI datacenters) and **live layers** (API-driven: USGS earthquakes, NASA EONET, AIS vessel tracking, ADS-B flights, Cloudflare Radar outages). This lets the dashboard render instantly even when live APIs are slow or down.

## 3. Free-Source Categories
### 3.1 Works Out-of-the-Box (No API Key)
- **USGS Earthquakes** (M4.5+)
- **NASA EONET** (storms, wildfires, volcanoes, floods)
- **NWS Severe Weather Alerts** (US)
- **FAA Airport Delays / Ground Stops**
- **Public RSS feeds** (70–435+ depending on variant)
- **Yahoo Finance** (backup quotes)
- **CoinGecko** (crypto prices)
- **GDELT** (limited public Doc/Geo APIs)

### 3.2 Unlocked by Free API Keys
- **FRED** (Federal Reserve economic data)
- **Finnhub** (stock quotes)
- **EIA** (US energy prices/inventory)
- **NASA FIRMS** (satellite fire detection)
- **AISStream** (terrestrial AIS vessel positions)
- **OpenSky Network** (ADS-B flight data; rate-limited free tier)
- **ACLED** (conflict/unrest events; free for researchers but API-restricted)

### 3.3 Paid / Hard-to-Access Sources
- **Cloudflare Radar** (internet outages — requires paid Radar access)
- **Satellite AIS** (global maritime coverage — commercial providers like Spire/Kpler)
- **Wingbits enrichment** (military aircraft classification — provided via partnership)

**Lesson for us**: A clear "no-key / free-key / paid-key" taxonomy makes graceful degradation possible. WorldMonitor ships a functional dashboard with zero env vars; keys only unlock richer panels.

## 4. Caching Strategy
WorldMonitor uses a **3-tier cache**:
1. **Redis (Upstash)** — shared server-side cache for AI summaries, risk scores, theater posture, and bootstrap payloads. TTLs are source-aware (5–10 min for live APIs, up to 1 hour for stale fallbacks).
2. **CDN / Edge Cache** — Cloudflare edge caching with `Cache-Control` headers aligned to upstream TTLs. In v2.5.20, 52 endpoints were migrated from POST to GET specifically to make edge caching viable.
3. **Service Worker / Browser Cache** — PWA-style asset caching and negative-result caching (`cachedFetchJson`) to avoid repeated failed requests.

### Key Patterns
- **Bootstrap Hydration Engine**: On init, a single `api/bootstrap.js` request pipelines multiple Redis reads so the UI does not trigger an API waterfall.
- **Circuit Breakers**: Every external service has independent failure tracking. After 2 consecutive failures, the breaker opens for 5 minutes; the UI falls back to cached data and shows a "temporarily unavailable" badge.
- **Per-Feed Circuit Breakers**: RSS feeds fail independently — one blocked source does not break the news panel.
- **Negative-Result Caching**: 403/404 responses from hostile sources are cached briefly to avoid hammering blocked endpoints.

## 5. Sidecar / API Separation
### 5.1 Web Deployment
- **Vercel Edge Functions (60+)** act as lightweight CORS proxies, key gatekeepers, and cache layers.
- **Railway Relay** handles sources that block cloud-provider IPs (some RSS feeds, OpenSky, Telegram MTProto). The relay runs on residential-like IP ranges and proxies WebSocket AIS streams.

### 5.2 Desktop Deployment
- **Tauri 2 (Rust)** wraps the frontend.
- **Node.js Sidecar** runs a local gateway so the desktop app can hit local endpoints (`/api/local-env-update`, RSS proxy) without relying on Vercel.
- Secrets are stored in a **consolidated OS-keychain vault** (`secrets-vault`) so macOS prompts drop from 20+ to exactly 1 on startup.

### 5.3 Protocol-First Contracts
WorldMonitor uses **Protocol Buffers** (92 protos, 22 services) with auto-generated TypeScript clients and OpenAPI docs. This keeps the web, edge, and sidecar APIs in sync without manual drift.

## 6. What to Borrow for `industry_signal_radar`
| WorldMonitor Pattern | How We Could Adapt It |
|----------------------|------------------------|
| **4-tier source credibility** | Apply to news/announcement sources (official exchanges, Tier-1 media, industry newsletters, forums). |
| **Bounded-concurrency category loading** | Load sector feeds in batches rather than all-at-once on startup. |
| **Static base layer + live overlay** | Ship static industry mappings (supply chains, key facilities) and overlay live price/volume/event APIs. |
| **3-tier cache (Redis → CDN → SW)** | Use a local/Redis cache for computed signals, with stale-fallback TTLs. |
| **Per-feed circuit breakers** | Prevent one broken data source from stalling the entire radar. |
| **No-key baseline + BYOK enrichment** | Make the radar functional without API keys; optional keys unlock premium data. |
| **Proto-first / typed API contracts** | If we add a sidecar or desktop wrapper, typed contracts reduce web/desktop drift. |

## 7. What NOT to Copy
1. **AGPL-3.0 License Trap** — WorldMonitor is AGPL-3.0. Any code derived from it must be open-sourced under the same license, including network use. We should treat it as a reference, not a copy-paste source.
2. **Browser-First Heavy Compute** — WorldMonitor pushes clustering, ML inference (Transformers.js), and threat scoring into the browser to avoid backend costs. For a quant research tool, Python-side compute is usually preferable for reproducibility and batch processing.
3. **Five UI Variants from One Codebase** — The project maintains world, tech, finance, commodity, and "happy" variants. The variant-switching complexity is overhead we do not need unless we genuinely plan multiple frontends.
4. **Propaganda Flagging & Geopolitical Bias Management** — Tagging state-affiliated media is necessary for geopolitical OSINT but adds editorial complexity that is largely irrelevant for industry/finance signal monitoring.
5. **125k Data-Point WebGL Rendering** — The decision to drop React for Vanilla TypeScript + WebGL was driven by rendering 125,000 map markers at 60fps. Unless our radar needs a 3D globe with dense asset layers, standard visualization libraries are fine.
6. **Relay Infrastructure for Scraping Evasion** — Running a Railway relay to evade RSS paywalls is clever but operationally fragile. For a sustainable research tool, we should prefer official APIs and free data portals over scraper relays.

## 8. Bottom Line
WorldMonitor demonstrates that a **free-source monitoring stack** can be organized through:
- strict source tiering,
- source-aware caching with circuit breakers,
- a clear separation between static base data and live API overlays, and
- lightweight sidecars for desktop/offline use.

For `industry_signal_radar`, the most transferable ideas are the **tiered credibility model**, **per-source fault tolerance**, and **no-key baseline + optional BYOK enrichment**. We should avoid the project's browser-heavy compute model, AGPL-licensed code, and geopolitical-specific editorial layers.
