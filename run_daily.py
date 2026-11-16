"""
Daily discovery + fetch for openai.com blog/news/research.
Clears saved article data and fetches every discovered article on each run.
Keeps a rolling 7-day publication window and uses structured content only.
"""

import argparse
import asyncio
import json
import re
import shutil
import sys
import urllib.request
from datetime import date, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse
import html as html_module
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom.minidom import parseString

from bs4 import BeautifulSoup


RSS_URL = "https://openai.com/news/rss.xml"

DATA_DIR = Path("data")
WINDOW_DAYS = 7


def discover_from_rss() -> list[tuple[str, str]]:
    """Return list of (url, iso_date) tuples from RSS, newest-first, within WINDOW_DAYS."""
    req = urllib.request.Request(
        RSS_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; RSS reader)"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        content = resp.read()
    soup = BeautifulSoup(content, "xml")
    cutoff = date.today() - timedelta(days=WINDOW_DAYS)
    seen: set[str] = set()
    items: list[tuple[str, str]] = []
    for item in soup.find_all("item"):
        link_tag = item.find("link")
        pub_tag = item.find("pubDate")
        if not link_tag:
            continue
        url = link_tag.text.strip().rstrip("/")
        path = urlparse(url).path.rstrip("/")
        if not re.match(r"^/(index|blog|research)/[^/]+$", path):
            continue
        pub_iso = ""
        if pub_tag:
            try:
                dt = parsedate_to_datetime(pub_tag.text)
                pub_date = dt.date()
                if pub_date < cutoff:
                    break  # RSS is newest-first; nothing older will qualify
                pub_iso = dt.date().isoformat()
            except Exception:
                pass
        if url not in seen:
            seen.add(url)
            items.append((url, pub_iso))
    return items


def url_to_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return re.sub(r"[^a-zA-Z0-9_-]", "-", path)


async def main(test_version: int | None = None) -> None:
    from app.extractor import extract

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Discovering from RSS: {RSS_URL}")
    found = discover_from_rss()
    print(f"  Found {len(found)} links")
    rss_dates = {url: pub_iso for url, pub_iso in found}
    new_urls = list(dict.fromkeys(url for url, _ in found))

    if not new_urls:
        print("No articles found.")
    else:
        print(f"\nFetching {len(new_urls)} article(s):")
        today = date.today().isoformat()
        out_dir = DATA_DIR / today
        out_dir.mkdir(parents=True, exist_ok=True)

        for url in new_urls:
            print(f"  -> {url}")
            try:
                article = await extract(url, use_cache=False)
                blocked = (
                    not article.title
                    or "Verification successful" in (article.body_text or "")
                    or "Waiting for openai.com" in (article.body_text or "")
                )
                if blocked:
                    print(f"    Skipped (Cloudflare blocked)")
                    continue
                # Fall back to RSS date if extractor didn't find one
                if not article.published_at and rss_dates.get(url):
                    article.published_at = rss_dates[url]
                # Skip articles published outside the window
                if article.published_at:
                    try:
                        pub_date = date.fromisoformat(article.published_at[:10])
                        if pub_date < date.today() - timedelta(days=WINDOW_DAYS):
                            print(f"    Skipped (older than {WINDOW_DAYS} days): {pub_date}")
                            continue
                    except ValueError:
                        pass
                slug = url_to_slug(url)
                dest = out_dir / f"{slug}.json"
                dest.write_text(article.model_dump_json(indent=2), encoding="utf-8")
                print(f"    Saved: {dest}")
            except Exception as exc:
                print(f"    ERROR: {exc}", file=sys.stderr)

    _cleanup_old_data()
    _generate_feed(test_version)


def _body_to_html(text: str, cover_url: str = "", body_images: list = None) -> str:
    if not text.strip().startswith("<"):
        raise ValueError("Structured article HTML is required")
    inline_bases = {
        u.split("?")[0] for u in re.findall(r'<img[^>]+src=["\']([^"\']+)', text)
    }
    cover_html = ""
    if cover_url and cover_url.split("?")[0] not in inline_bases:
        cover_html = (
            f'<img src="{html_module.escape(cover_url, quote=True)}" '
            'style="max-width:100%;display:block;margin-bottom:1em">'
        )
    return cover_html + text


def _generate_feed(test_version: int | None = None) -> None:
    if test_version is not None and test_version < 1:
        raise ValueError("Test feed version must be a positive integer")
    articles = []
    for f in sorted(DATA_DIR.glob("**/*.json"), reverse=True):
        try:
            article = json.loads(f.read_text(encoding="utf-8"))
            body = article.get("body_text") or ""
            if not isinstance(body, str) or not body.strip().startswith("<"):
                print(f"Skipped {f}: structured article HTML is missing")
                continue
            articles.append(article)
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
    atom_link.set("href", "https://jayreck996.github.io/py-openai/feed.xml")
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    for a in articles:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = a.get("title") or "Untitled"
        SubElement(item, "link").text = a.get("url", "")
        SubElement(item, "guid", isPermaLink="false").text = a.get("url", "")
        body = a["body_text"]
        cover = (a.get("cover_image") or {}).get("url", "")
        body_images = a.get("images") or []
        SubElement(item, "description").text = a.get("summary") or ""
        if body:
            html_body = _body_to_html(body, cover, body_images)
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

    _write_feed(rss, Path("feed.xml"))
    print(f"Feed updated: {len(articles)} item(s) -> feed.xml")
    if test_version is not None:
        test_name = f"feed_v{test_version}.xml"
        atom_link.set("href", atom_link.get("href").rsplit("/", 1)[0] + "/" + test_name)
        for guid in channel.findall("item/guid"):
            guid.text += f"#v{test_version}"
        _write_feed(rss, Path(test_name))
        print(f"Test feed updated: {test_name}")


def _write_feed(rss: Element, destination: Path) -> None:
    xml_str = parseString(tostring(rss, encoding="unicode")).toprettyxml(indent="  ")
    # minidom escapes CDATA delimiters and content — fix both
    xml_str = xml_str.replace("&lt;![CDATA[", "<![CDATA[").replace("]]&gt;", "]]>")
    xml_str = re.sub(
        r"<!\[CDATA\[(.*?)]]>",
        lambda m: f"<![CDATA[{html_module.unescape(m.group(1))}]]>",
        xml_str,
        flags=re.DOTALL,
    )
    destination.write_text(xml_str, encoding="utf-8")


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update the permanent feed.xml RSS feed.")
    parser.add_argument("--feed-only", action="store_true", help="Rebuild from saved articles without fetching.")
    parser.add_argument("--test-version", type=int, metavar="N", help="Also write feed_vN.xml for testing.")
    args = parser.parse_args()
    if args.test_version is not None and args.test_version < 1:
        parser.error("--test-version must be a positive integer")
    if args.feed_only:
        _generate_feed(args.test_version)
    else:
        asyncio.run(main(args.test_version))
