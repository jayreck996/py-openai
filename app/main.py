from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .extractor import extract
from .models import Article

app = FastAPI(
    title="py-openai",
    description="Fetch full article content (title, body, images, metadata) from openai.com",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

ALLOWED_DOMAINS = {"openai.com", "www.openai.com"}


@app.get("/fetch", response_model=Article, summary="Fetch a full OpenAI article")
async def fetch_article(
    url: str = Query(..., description="Full URL of an openai.com article")
):
    """
    Returns title, author, published_at, summary, cover_image,
    body_html, body_text, images[], and tags[] for any openai.com article.
    Results are cached in-memory for 1 hour.
    """
    parsed = urlparse(url)
    if parsed.netloc.lstrip("www.") not in {"openai.com"}:
        raise HTTPException(
            status_code=400,
            detail=f"Only openai.com URLs are supported. Got: '{parsed.netloc}'",
        )
    try:
        return await extract(url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}
