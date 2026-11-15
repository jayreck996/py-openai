ASSET LOG
INSTRUCTION FOR AI MODEL:

ALWAYS ADD NEW ASSET ENTRIES AT THE TOP, DIRECTLY BELOW THIS HEADER.

NEVER DELETE OR EDIT PREVIOUS ASSET ENTRIES.

REQUIRED FORMAT FOR EACH ASSET ENTRY:

## ASSET:{NAME OF ENVIRONMENT} {YYYY-MM-DD HH:MM} → {CONTENT}

## ASSET:py-openai 2026-11-15 18:46 → RSC inline images live — v11, fresh data each run

OpenAI blog RSS scraper fully operational with inline images extracted from Next.js RSC payload.

| Field | Value |
|---|---|
| Feed URL | `https://raw.githubusercontent.com/jayreck996/py-openai/master/feed.xml` |
| Versioned feed | `feed_v11.xml` |
| GUID suffix | `#v11` |
| Schedule | `0 17 * * *` UTC — 6:00 AM NZDT daily |
| Article window | 7 days rolling, 15 articles per run |
| Discovery | OpenAI RSS `https://openai.com/news/rss.xml` |

**Extraction pipeline:**

| Stage | Method |
|---|---|
| Render | Playwright headless Chromium, stealth config (`--disable-blink-features=AutomationControlled`, `navigator.webdriver` patched) |
| Text | RSC payload decode — `__next_f.push([1,"..."])` chunks, anchored at `"data-toc-content"`, spans via `["$","span","text-N",...]` |
| Headings | RSC `["$","h2",null,{"children":"..."}]` pattern |
| Inline images | RSC `$L` lazy ref resolution — chunks with `"altText"` key + ctfassets URL in `"file"."url"` placed at their payload position; `"$undefined"` altText filtered out |
| Fallback | DOM walk (`article` element), then trafilatura |
| Dedup | Base URL comparison (strip `?w=...` query params) to avoid double-appending body images |

**Workflow change:** `rm -rf data/` before each run — always fetches fresh content, no caching across runs.

**CMS:** Contentful — image assets stored as `mediaItem` with `altText` + `file.url` + `assetDark` fields in RSC payload.
