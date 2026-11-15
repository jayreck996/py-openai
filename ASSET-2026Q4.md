ASSET LOG
INSTRUCTION FOR AI MODEL:

ALWAYS ADD NEW ASSET ENTRIES AT THE TOP, DIRECTLY BELOW THIS HEADER.

NEVER DELETE OR EDIT PREVIOUS ASSET ENTRIES.

REQUIRED FORMAT FOR EACH ASSET ENTRY:

## ASSET:{NAME OF ENVIRONMENT} {YYYY-MM-DD HH:MM} → {CONTENT}

## ASSET:py-openai 2026-11-15 21:32 → Correct inline image positions, permanent production feed, strict fresh fetching

- React Flight tree traversal replaces payload-offset regex extraction. Lazy references resolve at their actual child positions; all four Businesses in ChatGPT screenshots were verified against the captured source.
- Production output is always `feed.xml`, with stable article-URL GUIDs. Daily workflows commit `data/` and `feed.xml` only.
- Versioned feeds are testing snapshots: `python run_daily.py --feed-only --test-version 15` also creates `feed_v15.xml` without changing production GUIDs or existing snapshots.
- Extraction uses the structured React Flight article only. DOM, Trafilatura, and plain-text content fallbacks are removed; articles without structured content are skipped.
- Every daily run clears saved article data, bypasses the in-memory extraction cache, and fetches all discovered URLs. The rolling publication window remains 7 days.
- Validation: 21 tests passed on GitHub; a live structured-only fetch of Businesses in ChatGPT succeeded. Manual fetch artifacts can include `source.html` for reproducing extraction issues.
- Implementation commits: `6239444` (verified image positions), `bb6af2f` (permanent feed), and `362d645` (strict extraction and fresh fetching).

## ASSET:py-openai 2026-11-15 20:30 → RSC headings, inline links, spacing fixed — mirrors Anthropic output

Fixed RSC extractor to produce clean semantic HTML matching Anthropic's output quality.

**Problems fixed:**
- Headings rendered as `<p>` — OpenAI uses a Contentful rich-text renderer component (`$L<id>`) with `"headlineTag":"h2"` prop, not native `["$","h2",...]` elements. Added heading component regex targeting this pattern.
- Missing spaces between words — `_unescape()` was calling `.strip()` on each span, removing the trailing/leading spaces that RSC spans use for word separation. Removed `.strip()`; assembler now does `"".join(buf).strip()` on the whole paragraph.
- Links missing — inline links are Contentful link components with `"variant":"inline","href":"URL"`. Added link regex that finds the href and associates it with the inner span, emitting `<a href>` tags.
- Inner heading/link spans now excluded from the text-span pass via position tracking to avoid duplicate text.

| Fix | Regex anchor |
|---|---|
| Headings | `["$","$L<id>",null,{"text":[..spans..],"headlineTag":"h2",...}]` |
| Links | `["$","$L<id>","key",{"variant":"inline","href":"URL",...}]` |
| Spacing | Removed `.strip()` from `_unescape()`; `"".join(buf).strip()` at paragraph level |

Verified against live RSC payload on cogent-security article — output matches actual page content exactly.

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
