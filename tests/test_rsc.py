import json
from pathlib import Path
import unittest

from bs4 import BeautifulSoup
from app.rsc import extract_rsc_article


def element(tag, children=None, **props):
    if children is not None:
        props['children'] = children
    return ['$', tag, None, props]


def page(records):
    wire = ''.join(key + ':' + json.dumps(value) + '\n' for key, value in records)
    # A Flight record can be split between script tags, even inside a JSON string.
    split = len(wire) // 2
    return ''.join('<script>self.__next_f.push([1,' + json.dumps(chunk) + '])</script>'
                   for chunk in (wire[:split], wire[split:]))


class RscTests(unittest.TestCase):
    def test_images_follow_child_positions_not_record_arrival_or_preload_order(self):
        article = element('article', [
            element('p', 'Claim paragraph.'), '$Lb', element('p', 'Eligibility paragraph.'),
            element('h2', 'Insights'), element('p', 'Insights paragraph.'), '$La',
            element('h2', 'Agents'), element('p', 'Profile paragraph.'), '$Lc',
            element('p', 'Conversation paragraph.'), '$Ld',
        ])
        def image(name):
            return element('$Lff', image={'url': '//images.example.com/' + name + '.png'}, altText=name)
        # Definitions arrive in a different order and are also preloaded elsewhere.
        source = page([('0', {'preloads': ['$La', '$Lb', '$Lc', '$Ld'], 'children': article}),
                       ('d', image('conversation')), ('a', image('insights')),
                       ('c', image('profile')), ('b', image('claim'))])
        soup = BeautifulSoup(extract_rsc_article(source, 'https://openai.com/'), 'lxml')
        self.assertEqual([(i['alt'], i.find_previous('p').text) for i in soup.select('img')],
                         [('claim', 'Claim paragraph.'), ('insights', 'Insights paragraph.'),
                          ('profile', 'Profile paragraph.'), ('conversation', 'Conversation paragraph.')])

    def test_nested_text_and_links_keep_paragraphs_intact(self):
        body = element('article', [
            element('$Lff', text=[element('em', 'A heading')], headlineTag='h3'),
            element('p', ['A ', element('$Lff', ['linked ', element('strong', 'phrase')],
                                        variant='inline', href='/info'), ' & its ending.']),
            element('p', 'Separate paragraph.'),
        ])
        soup = BeautifulSoup(extract_rsc_article(page([('0', body)]), 'https://openai.com'), 'lxml')
        self.assertEqual(soup.h3.text, 'A heading')
        self.assertEqual(soup.select('p')[0].text, 'A linked phrase & its ending.')
        self.assertEqual(soup.a['href'], 'https://openai.com/info')
        self.assertIsNotNone(soup.select_one('a strong'))
        self.assertEqual(len(soup.select('p')), 2)

    def test_repeated_image_is_not_globally_deduplicated(self):
        body = element('article', [element('p', 'One'), '$La', element('p', 'Two'), '$La'])
        source = page([('0', body), ('a', element('img', src='https://example.com/x.png'))])
        soup = BeautifulSoup(extract_rsc_article(source), 'lxml')
        self.assertEqual([i.find_previous('p').text for i in soup.select('img')], ['One', 'Two'])

    def test_cyclic_refs_do_not_recurse_forever(self):
        source = page([('0', element('article', [element('p', 'Body'), '$La'])), ('a', '$La')])
        self.assertEqual(extract_rsc_article(source), '<p>Body</p>')

    def test_utf8_text_records_and_property_references(self):
        body = element('article', [element('p', '$a'), element('img', src='$b:url', alt='Image')])
        text = 'Café\nwith a second line'
        wire = '0:' + json.dumps(body) + '\n'
        wire += 'a:T' + format(len(text.encode('utf-8')), 'x') + ',' + text
        wire += '\nb:' + json.dumps({'url': 'https://example.com/image.png'}) + '\n'
        source = '<script>self.__next_f.push([1,' + json.dumps(wire) + '])</script>'
        soup = BeautifulSoup(extract_rsc_article(source), 'lxml')
        self.assertEqual(soup.p.text, text)
        self.assertEqual(soup.img['src'], 'https://example.com/image.png')

    def test_real_businesses_article_image_paragraphs(self):
        source = (Path(__file__).parent / 'fixtures/businesses-rsc.html').read_text(encoding='utf-8')
        body = extract_rsc_article(source, 'https://openai.com/index/introducing-businesses-chatgpt/')
        soup = BeautifulSoup(body, 'lxml')
        images = soup.select('img')
        self.assertEqual(len(images), 4)
        self.assertEqual([i.find_previous('p').get_text().strip().split('.')[0] for i in images], [
            'Any eligible business located where ChatGPT is available can claim their business in ChatGPT through Business Hub',
            'We’ve designed these insights to protect user privacy',
            'When a business appears in a model response, people can click to see the business’s ChatGPT profile',
            'Business Agents are now available to eligible ecommerce and online service businesses in the United States',
        ])
        self.assertEqual(len(soup.select('h2')), 4)
        self.assertNotIn('Internal Server Error', body)
        self.assertNotIn('Keep reading', body)

    def test_missing_article_returns_none(self):
        self.assertIsNone(extract_rsc_article(page([('0', {'preload': '//example.com/image.png'})])))


if __name__ == '__main__':
    unittest.main()
