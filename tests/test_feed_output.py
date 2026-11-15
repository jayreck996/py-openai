import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
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
