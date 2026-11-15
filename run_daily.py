"""
Daily discovery + fetch for openai.com blog/news/research.
Skips articles already saved under data/. Saves new ones to data/YYYY-MM-DD/slug.json.
"""

import asyncio
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from app.extractor import extract

LISTING_PAGES = [
    "https://openai.com/news/",
]

DATA_DIR = Path("data")


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
            slug = url_to_slug(url)
            dest = out_dir / f"{slug}.json"
            dest.write_text(article.model_dump_json(indent=2), encoding="utf-8")
            print(f"    Saved: {dest}")
        except Exception as exc:
            print(f"    ERROR: {exc}", file=sys.stderr)


asyncio.run(main())
