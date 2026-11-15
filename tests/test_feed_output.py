import asyncio
import json
from datetime import date
from types import ModuleType, SimpleNamespace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET

import run_daily


class FeedOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.url = 'https://example.com/article'
        (self.data / 'article.json').write_text(json.dumps({
            'url': self.url, 'title': 'Article', 'body_text': '<p>Body</p>',
        }), encoding='utf-8')

    def generate(self, version=None):
        with patch.object(run_daily, 'DATA_DIR', self.data), patch.object(
            run_daily, 'Path', side_effect=lambda name: self.root / name
        ):
            run_daily._generate_feed(version)

    def test_default_writes_only_permanent_feed_with_stable_id(self):
        self.generate()
        feed = ET.parse(self.root / 'feed.xml')
        self.assertEqual(feed.findtext('./channel/item/guid'), self.url)
        link = feed.find('./channel/{http://www.w3.org/2005/Atom}link')
        self.assertTrue(link.get('href').endswith('/feed.xml'))
        self.assertEqual(list(self.root.glob('feed_v*.xml')), [])

    def test_test_versions_cannot_change_permanent_feed_or_old_test_snapshot(self):
        self.generate()
        permanent = (self.root / 'feed.xml').read_bytes()
        self.generate(14)
        old_snapshot = (self.root / 'feed_v14.xml').read_bytes()
        self.generate(15)
        self.assertEqual((self.root / 'feed.xml').read_bytes(), permanent)
        self.assertEqual((self.root / 'feed_v14.xml').read_bytes(), old_snapshot)
        test_feed = ET.parse(self.root / 'feed_v15.xml')
        self.assertEqual(test_feed.findtext('./channel/item/guid'), self.url + '#v15')
        link = test_feed.find('./channel/{http://www.w3.org/2005/Atom}link')
        self.assertTrue(link.get('href').endswith('/feed_v15.xml'))

    def test_missing_structured_body_is_skipped_without_using_raw_html(self):
        for body in (None, 'Plain text that must not be guessed into paragraphs'):
            (self.data / 'article.json').write_text(json.dumps({
                'url': self.url, 'body_text': body,
                'body_html': '<article><p>Raw DOM must not be used.</p></article>',
            }), encoding='utf-8')
            self.generate()
            self.assertEqual(ET.parse(self.root / 'feed.xml').findall('./channel/item'), [])

    def test_daily_run_discards_cache_and_fetches_existing_url_again(self):
        stale = self.data / 'stale.json'
        stale.write_text('{"url":"https://example.com/stale"}')
        fake_module = ModuleType('app.extractor')
        async def fresh_extract(url, *, use_cache):
            self.assertFalse(use_cache)
            self.assertFalse(stale.exists())
            self.assertFalse((self.data / 'article.json').exists())
            return SimpleNamespace(
                title='Fresh', published_at=date.today().isoformat(), body_text='<p>Fresh</p>',
                model_dump_json=lambda **kwargs: json.dumps({'url': url, 'body_text': '<p>Fresh</p>'}),
            )
        fake_module.extract = AsyncMock(side_effect=fresh_extract)
        if hasattr(run_daily, 'discover_listing'):
            discovery = patch.object(run_daily, 'discover_listing', new=AsyncMock(return_value=[self.url]))
        else:
            discovery = patch.object(run_daily, 'discover_from_rss', return_value=[(self.url, date.today().isoformat())])
        with patch.dict('sys.modules', {'app.extractor': fake_module}), discovery, \
                patch.object(run_daily, 'DATA_DIR', self.data), patch.object(run_daily, '_generate_feed') as generate:
            asyncio.run(run_daily.main())
        fake_module.extract.assert_awaited_once_with(self.url, use_cache=False)
        generate.assert_called_once_with(None)
        files = list(self.data.glob('**/*.json'))
        self.assertEqual(len(files), 1)
        self.assertIn('<p>Fresh</p>', files[0].read_text())

    def test_invalid_version_does_not_write_any_feed(self):
        with self.assertRaises(ValueError):
            self.generate(0)
        self.assertEqual(list(self.root.glob('feed*.xml')), [])

    def test_feed_only_cli_uses_saved_data_without_browser_dependencies(self):
        script = Path(run_daily.__file__).resolve()
        result = subprocess.run(
            [sys.executable, str(script), '--feed-only', '--test-version', '99'],
            cwd=self.root, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ET.parse(self.root / 'feed.xml').findtext('./channel/item/guid'), self.url)
        self.assertTrue((self.root / 'feed_v99.xml').exists())


if __name__ == '__main__':
    unittest.main()
