import json
import time
from typing import Optional
from urllib.parse import urljoin

import trafilatura
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from .models import Article, Image

_cache: dict[str, tuple[float, Article]] = {}
CACHE_TTL = 3600


async def extract(url: str) -> Article:
    if url in _cache:
        ts, article = _cache[url]
        if time.time() - ts < CACHE_TTL:
            return article

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                },
            )
            await ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = await ctx.new_page()
            await page.goto(url, wait_until="networkidle", timeout=60_000)
            try:
                await page.wait_for_selector("article, [class*='content'], main", timeout=10_000)
            except Exception:
                pass
            html = await page.content()

            # Try DOM walk extraction first (Contentful content is pre-rendered)
            body_text = await _extract_dom_body(page)
            if not body_text:
                body_text = trafilatura.extract(
                    html,
                    include_images=True,
                    include_links=True,
                    no_fallback=False,
                    favor_precision=True,
                )
        finally:
            await browser.close()

    soup = BeautifulSoup(html, "lxml")

    title = (
        _meta(soup, "og:title")
        or _meta(soup, "twitter:title")
        or _jsonld_field(soup, "headline")
        or (soup.h1.get_text(strip=True) if soup.h1 else None)
    )
    summary = (
        _meta(soup, "og:description")
        or _meta(soup, "description")
        or _jsonld_field(soup, "description")
    )
    author = _jsonld_author(soup) or _meta(soup, "author")
    published_at = (
        _meta(soup, "article:published_time")
        or _jsonld_field(soup, "datePublished")
        or _jsonld_field(soup, "dateCreated")
    )
    og_image = _meta(soup, "og:image") or _meta(soup, "twitter:image")
    cover_image = Image(url=og_image) if og_image else None
    body_html = _body_html(soup)
    images = _body_images(soup, url)
    tags = _extract_tags(soup)

    article = Article(
        url=url,
        title=title,
        author=author,
        published_at=published_at,
        summary=summary,
        cover_image=cover_image,
        body_html=body_html,
        body_text=body_text,
        images=images,
        tags=tags,
    )
    _cache[url] = (time.time(), article)
    return article


async def _extract_dom_body(page) -> Optional[str]:
    """Walk the rendered article DOM to extract clean HTML with correct heading levels."""
    result = await page.evaluate("""
    () => {
        const article = document.querySelector('article');
        if (!article) return null;
        const clone = article.cloneNode(true);
        clone.querySelectorAll(
            'script,style,nav,button,footer,sup,aside,[role="navigation"],[data-testid="share"]'
        ).forEach(e => e.remove());

        const skipHeadings = new Set(['Author', 'Acknowledgments', 'Footnotes', 'References']);
        const skipText = new Set(['Release', 'Research', 'Product', 'Company', 'Featured',
                                  'Listen to article', 'Read aloud']);

        function extractHtml(node) {
            const parts = [];
            for (const child of node.childNodes) {
                if (child.nodeType === 3) {
                    const t = child.textContent
                        .replace(/\\(opens in a new window\\)/g, '')
                        .replace(/\\n+/g, ' ')
                        .trim();
                    if (t && t.length > 2 && !skipText.has(t) && !/^\\d+:\\d+$/.test(t))
                        parts.push(t);
                    continue;
                }
                if (child.nodeType !== 1) continue;
                const tag = child.tagName.toLowerCase();
                if (['script','style','button','nav','sup','aside'].includes(tag)) continue;

                if (['h2','h3','h4'].includes(tag)) {
                    const text = child.innerText.trim();
                    if (text && !skipHeadings.has(text))
                        parts.push(`<${tag}>${text}</${tag}>`);
                } else if (tag === 'p') {
                    let html = '';
                    for (const n of child.childNodes) {
                        if (n.nodeType === 3) {
                            html += n.textContent.replace(/\\(opens in a new window\\)/g, '');
                        } else if (n.tagName === 'A') {
                            const text = n.innerText.replace(/\\(opens in a new window\\)/g, '').trim();
                            html += `<a href="${n.href}">${text}</a>`;
                        } else if (['STRONG','B'].includes(n.tagName)) {
                            html += `<strong>${n.innerText}</strong>`;
                        } else if (['EM','I'].includes(n.tagName)) {
                            html += `<em>${n.innerText}</em>`;
                        } else if (n.tagName === 'CODE') {
                            html += `<code>${n.innerText}</code>`;
                        } else {
                            html += n.innerText || '';
                        }
                    }
                    html = html.replace(/\\(opens in a new window\\)/g, '').trim();
                    if (html && html.length > 5) parts.push(`<p>${html}</p>`);
                } else if (tag === 'img' && child.src && !child.src.startsWith('data:')) {
                    parts.push(
                        `<figure style="margin:1.5em 0">` +
                        `<img src="${child.src}" alt="${child.alt}" style="max-width:100%;display:block">` +
                        `</figure>`
                    );
                } else if (tag === 'ul' || tag === 'ol') {
                    const items = [...child.querySelectorAll('li')]
                        .map(li => `<li>${li.innerText.trim()}</li>`).join('');
                    if (items) parts.push(`<${tag}>${items}</${tag}>`);
                } else {
                    parts.push(...extractHtml(child));
                }
            }
            return parts;
        }
        const html = extractHtml(clone).join('');
        return html.length > 100 ? html : null;
    }
    """)
    return result or None


def _meta(soup: BeautifulSoup, name: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"property": name}) or soup.find(
        "meta", attrs={"name": name}
    )
    return tag.get("content", "").strip() or None if tag else None


def _jsonld_field(soup: BeautifulSoup, field: str) -> Optional[str]:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
            for item in data.get("@graph", [data]):
                if isinstance(item, dict) and field in item:
                    val = item[field]
                    if isinstance(val, str):
                        return val.strip() or None
                    if isinstance(val, dict):
                        return (val.get("url") or val.get("name") or "").strip() or None
        except (json.JSONDecodeError, AttributeError):
            continue
    return None


def _jsonld_author(soup: BeautifulSoup) -> Optional[str]:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
            for item in data.get("@graph", [data]):
                if not isinstance(item, dict):
                    continue
                author = item.get("author")
                if isinstance(author, dict):
                    return author.get("name", "").strip() or None
                if isinstance(author, list):
                    names = [a.get("name", "") for a in author if isinstance(a, dict)]
                    return ", ".join(filter(None, names)) or None
        except (json.JSONDecodeError, AttributeError):
            continue
    return None


def _body_html(soup: BeautifulSoup) -> Optional[str]:
    candidates = [
        soup.select_one("main article"),
        soup.select_one("[class*='ArticleBody']"),
        soup.select_one("[class*='PostContent']"),
        soup.select_one("[class*='article-body']"),
        soup.select_one("article"),
        soup.select_one("main"),
    ]
    el = next((c for c in candidates if c), None)
    if not el:
        return None
    for dead in el.select("nav, header, footer, [role='navigation'], [aria-label='breadcrumb']"):
        dead.decompose()
    return str(el)


def _body_images(soup: BeautifulSoup, base_url: str) -> list[Image]:
    images: list[Image] = []
    seen: set[str] = set()
    container = (
        soup.select_one("article")
        or soup.select_one("main article")
        or soup.select_one("main")
    )
    if not container:
        return images
    for img in container.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src or src in seen:
            continue
        seen.add(src)
        full = urljoin(base_url, src) if not src.startswith("http") else src
        try:
            w = int(img.get("width", 0)) or None
            h = int(img.get("height", 0)) or None
        except (ValueError, TypeError):
            w = h = None
        images.append(Image(url=full, alt=img.get("alt"), width=w, height=h))
    return images


def _extract_tags(soup: BeautifulSoup) -> list[str]:
    tags: list[str] = []
    for el in soup.select(
        "[class*='tag' i], [class*='category' i], [class*='topic' i], "
        "[rel='tag'], [data-category]"
    ):
        text = el.get_text(strip=True)
        if text and len(text) < 60 and text not in tags:
            tags.append(text)
    return tags
