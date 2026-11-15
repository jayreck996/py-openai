"""
Daily discovery + fetch for openai.com blog/news/research.
Maintains a rolling 7-day window: fetches new articles, skips already-saved ones,
and removes data directories older than WINDOW_DAYS at the end of each run.
"""

import asyncio
import json
import re
import shutil
import sys
from datetime import date, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom.minidom import parseString

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from app.extractor import extract

LISTING_PAGES = [
    "https://openai.com/news/",
]

DATA_DIR = Path("data")
WINDOW_DAYS = 7


async def discover_listing(listing_url: str) -> list[str]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                )
            )
            page = await ctx.new_page()
            await page.goto(listing_url, wait_until="networkidle", timeout=30_000)
            try:
                await page.wait_for_selector("a[href]", timeout=8_000)
            except Exception:
                pass
            html = await page.content()
        finally:
            await browser.close()

    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        # OpenAI article paths: /index/<slug>, /blog/<slug>, /research/<slug>
        if re.match(r"^/(index|blog|research)/[^/]+/?$", href):
            full = urljoin("https://openai.com", href).rstrip("/")
            if full not in seen:
                seen.add(full)
                urls.append(full)
    return urls


def already_fetched_urls() -> set[str]:
    fetched: set[str] = set()
    for f in DATA_DIR.glob("**/*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if url := data.get("url"):
                fetched.add(url)
        except Exception:
            continue
    return fetched


def url_to_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return re.sub(r"[^a-zA-Z0-9_-]", "-", path)


async def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    fetched = already_fetched_urls()

    new_urls: list[str] = []
    for listing in LISTING_PAGES:
        print(f"Discovering: {listing}")
        found = await discover_listing(listing)
        print(f"  Found {len(found)} links")
        for url in found:
            if url not in fetched and url not in new_urls:
                new_urls.append(url)

    if not new_urls:
        print("No new articles found.")
        return

    print(f"\nFetching {len(new_urls)} new article(s):")
    today = date.today().isoformat()
    out_dir = DATA_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)

    for url in new_urls:
        print(f"  → {url}")
        try:
            article = await extract(url)
            # Skip articles published outside the window (when date is known)
            if article.published_at:
                try:
                    pub_date = date.fromisoformat(article.published_at[:10])
                    if pub_date < date.today() - timedelta(days=WINDOW_DAYS):
                        print(f"    Skipped (older than {WINDOW_DAYS} days): {pub_date}")
                        continue
                except ValueError:
                    pass  # unparseable date — save it anyway
            slug = url_to_slug(url)
            dest = out_dir / f"{slug}.json"
            dest.write_text(article.model_dump_json(indent=2), encoding="utf-8")
            print(f"    Saved: {dest}")
        except Exception as exc:
            print(f"    ERROR: {exc}", file=sys.stderr)

    _cleanup_old_data()
    _generate_feed()


def _generate_feed() -> None:
    articles = []
    for f in sorted(DATA_DIR.glob("**/*.json"), reverse=True):
        try:
            articles.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue

    rss = Element("rss", version="2.0")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")
    rss.set("xmlns:content", "http://purl.org/rss/1.0/modules/content/")
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = "OpenAI News"
    SubElement(channel, "link").text = "https://openai.com/news/"
    SubElement(channel, "description").text = "Latest articles from OpenAI"
    SubElement(channel, "language").text = "en"
    atom_link = SubElement(channel, "atom:link")
    atom_link.set("href", "https://raw.githubusercontent.com/jayreck996/py-openai/master/feed.xml")
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    for a in articles:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = a.get("title") or "Untitled"
        SubElement(item, "link").text = a.get("url", "")
        SubElement(item, "guid", isPermaLink="true").text = a.get("url", "")
        SubElement(item, "description").text = a.get("summary") or ""
        # Full body as content:encoded — Feedly renders this as the article body
        body = a.get("body_text") or a.get("body_html") or ""
        if body:
            cover = (a.get("cover_image") or {}).get("url", "")
            html_body = (
                f'<img src="{cover}" style="max-width:100%"><br><br>' if cover else ""
            ) + body.replace("\n", "<br>")
            encoded = SubElement(item, "content:encoded")
            encoded.text = f"<![CDATA[{html_body}]]>"
        if a.get("published_at"):
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(a["published_at"].replace("Z", "+00:00"))
                SubElement(item, "pubDate").text = format_datetime(dt)
            except Exception:
                pass
        if a.get("cover_image") and a["cover_image"].get("url"):
            enc = SubElement(item, "enclosure")
            enc.set("url", a["cover_image"]["url"])
            enc.set("type", "image/jpeg")
            enc.set("length", "0")

    xml_str = parseString(tostring(rss, encoding="unicode")).toprettyxml(indent="  ")
    # Fix CDATA blocks mangled by minidom
    xml_str = xml_str.replace("&lt;![CDATA[", "<![CDATA[").replace("]]&gt;", "]]>")
    Path("feed.xml").write_text(xml_str, encoding="utf-8")
    print(f"Feed updated: {len(articles)} item(s) → feed.xml")


def _cleanup_old_data() -> None:
    cutoff = date.today() - timedelta(days=WINDOW_DAYS)
    for day_dir in sorted(DATA_DIR.iterdir()):
        if not day_dir.is_dir():
            continue
        try:
            if date.fromisoformat(day_dir.name) < cutoff:
                shutil.rmtree(day_dir)
                print(f"Removed expired data: {day_dir}")
        except ValueError:
            pass  # skip non-date directories


asyncio.run(main())
