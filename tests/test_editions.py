import copy
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'scripts'))
from editions import editorial_index, validate_edition, tile
from feedback_auth import validate_auth

EXAMPLE = {'schema_version':1,'date':'2026-09-17','prepared_at':'2026-09-17T06:15:00Z','items':[{
    'id':'example-report','category':'economy','title':'Example report','publisher':'Example agency','published':'2026-09-16','geography':'United States',
    'summary':' '.join(['Evidence']*65),'detail':'Details <script>bad()</script>',
    'source_url':'https://example.gov/report','document_url':'https://example.gov/report.pdf','source_type':'Statistical release','tags':['prices']}]}

class EditionTests(unittest.TestCase):
    def test_observatory_header_stays_visible_while_scrolling(self):
        css=(Path(__file__).resolve().parents[1] / 'site_src' / 'edition.css').read_text()
        self.assertIn('.site-header{position:sticky;',css)
        self.assertIn('top:0;',css)
        self.assertIn('background:var(--paper)',css)

    def test_accepts_plain_public_edition(self):
        self.assertEqual(validate_edition(copy.deepcopy(EXAMPLE)),EXAMPLE)
    def test_private_fields_rejected_at_both_levels(self):
        for level in ('edition','item'):
            value=copy.deepcopy(EXAMPLE)
            target=value if level=='edition' else value['items'][0]
            target['preference_reason']='Private signal'
            with self.assertRaises(ValueError):validate_edition(value)
    def test_links_and_rendered_text_cannot_inject_code(self):
        value=copy.deepcopy(EXAMPLE)
        value['items'][0]['document_url']='javascript:alert(1)'
        with self.assertRaises(ValueError):validate_edition(value)
        value['items'][0]['document_url']='https://example.gov/report.pdf'
        value['items'][0]['title']='</script><img onerror=evil()>'
        rendered=tile(validate_edition(value)['items'][0])
        self.assertNotIn('<img onerror',rendered)
        self.assertNotIn('<script>bad()',rendered)
    def test_rejects_duplicates_future_sources_and_wrong_local_date(self):
        duplicate=copy.deepcopy(EXAMPLE);duplicate['items']*=2
        with self.assertRaises(ValueError):validate_edition(duplicate)
        future=copy.deepcopy(EXAMPLE);future['items'][0]['published']='2026-09-18'
        with self.assertRaises(ValueError):validate_edition(future)
        wrong=copy.deepcopy(EXAMPLE);wrong['prepared_at']='2026-09-17T01:00:00Z'
        with self.assertRaises(ValueError):validate_edition(wrong)
    def test_rejects_more_than_fifteen_items_and_long_detail(self):
        crowded=copy.deepcopy(EXAMPLE)
        crowded['items']=[{**crowded['items'][0],'id':f'item-{number}','document_url':f'https://example.gov/{number}.pdf'} for number in range(16)]
        with self.assertRaises(ValueError):validate_edition(crowded)
        verbose=copy.deepcopy(EXAMPLE);verbose['items'][0]['detail']='word '*121
        with self.assertRaises(ValueError):validate_edition(verbose)
    def test_editorial_index_is_compact_and_contains_no_summaries(self):
        index=editorial_index([copy.deepcopy(EXAMPLE)])
        self.assertEqual(index['category_counts']['economy'],1)
        self.assertEqual(index['items'][0]['id'],'example-report')
        self.assertNotIn('summary',json.dumps(index))
    def test_auth_envelope_rejects_plaintext_secret_fields(self):
        with self.assertRaises(ValueError):validate_auth({'token':'never publish this'})

if __name__=='__main__':unittest.main()
