from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app import extractor


class StrictExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        extractor._cache.clear()
        self.page = AsyncMock()
        self.page.content.return_value = '<main><article><h1>Title</h1><p>DOM-only content</p><img src="https://example.com/unrelated.png"></article></main>'
        self.context = AsyncMock()
        self.context.new_page.return_value = self.page
        self.browser = AsyncMock()
        self.browser.new_context.return_value = self.context
        browser_api = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=self.browser)))
        self.manager = MagicMock()
        self.manager.__aenter__ = AsyncMock(return_value=browser_api)
        self.manager.__aexit__ = AsyncMock(return_value=None)
        self.source_name = '_extract_sanity_body' if hasattr(extractor, '_extract_sanity_body') else '_extract_rsc_body'

    def source(self, value):
        if self.source_name == '_extract_sanity_body':
            return patch.object(extractor, self.source_name, new=AsyncMock(return_value=value))
        return patch.object(extractor, self.source_name, return_value=value)

    async def test_missing_structured_content_fails_even_with_readable_dom(self):
        with patch.object(extractor, 'async_playwright', return_value=self.manager), self.source(None):
            with self.assertRaisesRegex(ValueError, 'content was not found'):
                await extractor.extract('https://example.com/missing')
        self.browser.close.assert_awaited_once()
        self.assertNotIn('https://example.com/missing', extractor._cache)

    async def test_fresh_fetch_cannot_return_an_in_memory_cached_article(self):
        import time
        url = 'https://example.com/cached'
        extractor._cache[url] = (time.time(), object())
        with patch.object(extractor, 'async_playwright', return_value=self.manager), self.source(None):
            with self.assertRaisesRegex(ValueError, 'content was not found'):
                await extractor.extract(url, use_cache=False)
        self.browser.close.assert_awaited_once()

    async def test_html_and_images_come_only_from_structured_content(self):
        body = '<p>Structured paragraph.</p><figure><img src="https://example.com/inline.png" alt="Inline"></figure>'
        with patch.object(extractor, 'async_playwright', return_value=self.manager), self.source(body):
            article = await extractor.extract('https://example.com/structured')
        self.assertEqual(article.body_text, body)
        self.assertEqual(article.body_html, body)
        self.assertEqual([i.url for i in article.images], ['https://example.com/inline.png'])
        self.browser.close.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
