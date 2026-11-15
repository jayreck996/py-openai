import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup
from app.rendering import clean_article_html
from run_daily import _body_to_html, _generate_feed


class RenderingTests(unittest.TestCase):
    def test_nested_inline_markup_and_block_boundaries(self):
        source = '''<article><div data-section-header><h1>Hero</h1></div>
        <div><h2>Heading <em>two</em></h2><p>More than<a href="/news/"> 1.2 billion
        <strong>people</strong></a> use ChatGPT.</p><p>Next paragraph.</p>
        <ul><li>First<ul><li>Nested</li></ul></li><li>Second</li></ul>
        <blockquote><p>A quote.</p></blockquote><pre><code>a &lt; b\n  c</code></pre>
        <figure><img src="/chart.png" alt='A &amp; B'><figcaption>Chart</figcaption></figure>
        <table><tr><th colspan="2">Results</th></tr><tr><td>1</td><td>2</td></tr></table>
        <script>bad()</script><section><h2>Keep reading</h2><p>Related</p></section></div></article>'''
        body = clean_article_html(source, 'https://openai.com/index/example/')
        soup = BeautifulSoup(body, 'lxml')
        self.assertEqual([p.get_text() for p in soup.select('p')],
                         ['More than 1.2 billion\n        people use ChatGPT.', 'Next paragraph.', 'A quote.'])
        self.assertEqual(soup.a['href'], 'https://openai.com/news/')
        self.assertIsNotNone(soup.select_one('a strong'))
        self.assertEqual(soup.select_one('ul > li > ul > li').text, 'Nested')
        self.assertEqual(soup.img['src'], 'https://openai.com/chart.png')
        self.assertEqual(soup.th['colspan'], '2')
        self.assertEqual(soup.code.text, 'a < b\n  c')
        self.assertNotIn('Hero', body)
        self.assertNotIn('Related', body)
        self.assertNotIn('script', body)

    def test_unsafe_urls_and_escaping(self):
        body = clean_article_html('<article><p>&lt;unsafe&gt;<a href="javascript:alert(1)">link</a>'
                                  '<img src="data:text/html,bad"></p></article>')
        self.assertEqual(body, '<p>&lt;unsafe&gt;link</p>')

    def test_challenge_does_not_replace_extracted_body(self):
        self.assertIsNone(clean_article_html('<article><p>Verification successful</p></article>'))

    def test_feed_keeps_inline_image_order_without_extra_images(self):
        body = '<p>First</p><img src="https://example.com/a.png"><p>Last</p>'
        result = _body_to_html(body, 'https://example.com/a.png',
                               [{'url': 'https://example.com/unrelated.png'}])
        self.assertEqual(result, body)

    def test_saved_article_paragraphs_and_feed_xml(self):
        fixture = Path(__file__).parent / 'fixtures/aws-marketplace.html'
        article = {
            'url': 'https://openai.com/index/chatgpt-enterprise-aws-marketplace/',
            'body_html': fixture.read_text(encoding='utf-8'),
            'body_text': '<p>Old flattened extraction</p>',
        }
        body = clean_article_html(article['body_html'], article['url'])
        soup = BeautifulSoup(body, 'lxml')
        paragraphs = [p.get_text() for p in soup.select('p')]
        self.assertTrue(any(p.startswith('More than 1.2 billion people') for p in paragraphs))
        self.assertTrue(any(p.startswith('Contact your OpenAI') for p in paragraphs))
        self.assertTrue(any(p.startswith('Eligible customers') and 'Contact your' not in p for p in paragraphs))
        self.assertEqual(len(soup.select('blockquote')), 3)
        self.assertNotIn('Keep reading', body)
        self.assertNotIn('View all', body)
        self.assertEqual(len(soup.select('img')), 0)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            (dest / 'article.json').write_text(json.dumps(article))
            with patch('run_daily.DATA_DIR', dest), patch('run_daily.Path', side_effect=lambda s: dest / s):
                _generate_feed()
            feed = ET.parse(dest / 'feed.xml')
            encoded = feed.find('.//{http://purl.org/rss/1.0/modules/content/}encoded').text
            self.assertIn(body, encoded)
            self.assertNotIn('<![CDATA[', encoded)


if __name__ == '__main__':
    unittest.main()
