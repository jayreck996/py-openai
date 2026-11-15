import asyncio
import sys
from urllib.parse import urlparse

from app.extractor import extract

ALLOWED = {"openai.com", "www.openai.com"}


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python fetch.py <url>", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    if urlparse(url).netloc not in ALLOWED:
        print(f"Error: only openai.com URLs are supported.", file=sys.stderr)
        sys.exit(1)

    article = await extract(url)
    print(article.model_dump_json(indent=2))


asyncio.run(main())
