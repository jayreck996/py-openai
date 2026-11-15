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
import urllib.request
from datetime import date, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse
import html as html_module
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom.minidom import parseString

from bs4 import BeautifulSoup

from app.rendering import clean_article_html

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
    from app.extractor import extract

    DATA_DIR.mkdir(exist_ok=True)
    fetched = already_fetched_urls()

    print(f"Discovering from RSS: {RSS_URL}")
    found = discover_from_rss()
    print(f"  Found {len(found)} links")
    rss_dates = {url: pub_iso for url, pub_iso in found}
    new_urls = [url for url, _ in found if url not in fetched]

    if not new_urls:
        print("No new articles found.")
    else:
        print(f"\nFetching {len(new_urls)} new article(s):")
        today = date.today().isoformat()
        out_dir = DATA_DIR / today
        out_dir.mkdir(parents=True, exist_ok=True)

        for url in new_urls:
            print(f"  -> {url}")
            try:
                article = await extract(url)
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
    _generate_feed()


def _body_to_html(text: str, cover_url: str = "", body_images: list = None) -> str:
    cover_html = (
        f'<img src="{cover_url}" style="max-width:100%;display:block;margin-bottom:1em">'
        if cover_url else ""
    )
    # Structured HTML already contains images in their original positions.
    if text.strip().startswith("<"):
        inline_bases = {
            u.split("?")[0] for u in re.findall(r'<img[^>]+src=["\']([^"\']+)', text)
        }
        if cover_url.split("?")[0] in inline_bases:
            cover_html = ""
        return cover_html + text

    # Legacy: trafilatura plain text path
    text = re.sub(
        r'!\[([^\]]*)\]\(([^)]+)\)',
        r'<figure style="margin:1.5em 0"><img src="\2" alt="\1" style="max-width:100%;display:block"></figure>',
        text,
    )
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    parts = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("<figure") or line.startswith("<img"):
            parts.append(line)
        elif len(line) < 70 and not line[-1] in ".!?,:" and not line[0] in "-*•[":
            parts.append(f"<h2>{line}</h2>")
        else:
            parts.append(f"<p>{line}</p>")
    inline_urls = set(re.findall(r'<img src="([^"]+)"', "".join(parts)))
    for img in (body_images or []):
        url = img.get("url", "") if isinstance(img, dict) else str(img)
        alt = (img.get("alt") or "") if isinstance(img, dict) else ""
        if not url or url in inline_urls:
            continue
        cap = f'<figcaption style="font-size:0.85em;color:#666;margin-top:0.4em">{alt}</figcaption>' if alt else ""
        parts.append(f'<figure style="margin:1.5em 0"><img src="{url}" alt="{alt}" style="max-width:100%;display:block">{cap}</figure>')
    return cover_html + "".join(parts)


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
        SubElement(item, "guid", isPermaLink="false").text = a.get("url", "") + "#v13-layout2"
        # Re-render saved DOM too, so existing articles benefit without a refetch.
        body = (
            clean_article_html(a.get("body_html") or "", a.get("url") or "")
            or a.get("body_text") or ""
        )
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

    xml_str = parseString(tostring(rss, encoding="unicode")).toprettyxml(indent="  ")
    # minidom escapes CDATA delimiters and content — fix both
    xml_str = xml_str.replace("&lt;![CDATA[", "<![CDATA[").replace("]]&gt;", "]]>")
    xml_str = re.sub(
        r"<!\[CDATA\[(.*?)]]>",
        lambda m: f"<![CDATA[{html_module.unescape(m.group(1))}]]>",
        xml_str,
        flags=re.DOTALL,
    )
    Path("feed.xml").write_text(xml_str, encoding="utf-8")
    Path("feed_v13.xml").write_text(xml_str, encoding="utf-8")
    print(f"Feed updated: {len(articles)} item(s) -> feed.xml + feed_v13.xml")


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
    asyncio.run(main())
