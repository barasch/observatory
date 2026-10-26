import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import discovery as d
import weekly as w


def stream(**updates):
    base={
        'id':'route','publisher':'Publisher','name':'Papers','category':'economy',
        'source_type':'Working paper','endpoint':'https://example.org/feed',
        'adapter':'feed','homepage':'https://example.org/','scope':'U.S.',
        'status':'active','date_semantics':'listed date','alternative_routes':[],
        'provenance':'test','last_check':None,
    }
    base.update(updates); return base


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.run=Path(self.tmp.name)
        w.init(self.run,'2026-10-05','2026-10-16')
    def tearDown(self): self.tmp.cleanup()
    def state(self): return json.loads((self.run/'run.json').read_text())

    def test_registry_rejects_unknown_adapter(self):
        registry={'schema_version':2,'streams':[stream(adapter='wishful_parser')]}
        with self.assertRaisesRegex(ValueError,'unknown adapter'): d.validate_registry(registry)

    def test_crossref_dates_are_provisional_and_stable(self):
        source=stream(adapter='crossref',endpoint='https://api.crossref.org/prefixes/10.3386/works')
        raw=json.dumps({'message':{'total-results':1,'items':[{
            'DOI':'10.3386/W12345','title':['Consequential labor evidence'],
            'created':{'date-parts':[[2026,10,9]]},'subject':['Labor'],
        }]}}).encode()
        result=d.parse_crossref(raw,source,'2026-10-05','2026-10-16')
        record=result.records[0]
        self.assertEqual(record['date'],'2026-10-09')
        self.assertEqual(record['date_status'],'provisional')
        self.assertEqual(record['work_id'],'doi:10.3386/w12345')
        self.assertTrue(result.window_covered)

    def test_arxiv_reports_date_bounded_truncation(self):
        source=stream(adapter='arxiv',endpoint='https://export.arxiv.org/api/query')
        raw=b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
        <opensearch:totalResults>20</opensearch:totalResults><opensearch:startIndex>0</opensearch:startIndex>
        <entry><title>Paper</title><published>2026-10-09T00:00:00Z</published>
        <link rel="alternate" href="https://arxiv.org/abs/2610.00001"/></entry></feed>'''
        result=d.parse_arxiv(raw,source,'2026-10-05','2026-10-16')
        self.assertTrue(result.truncated)
        self.assertFalse(result.complete)
        self.assertFalse(result.window_covered)

    def test_reachable_unparsed_is_not_healthy(self):
        w.record_route_health(self.run,'bad',{'reachable':True,'parsed':False,'fresh':False,
          'complete':False,'window_covered':False,'records_seen':0,'records_dated':0,
          'records_in_window':0,'truncated':False})
        state=self.state()
        self.assertFalse(state['route_health']['bad']['healthy'])
        self.assertEqual(w.audit(self.run)['routes']['reachable_but_unparsed'],1)

    def test_feed_with_wrong_content_fails_instead_of_claiming_coverage(self):
        result=d.parse_feed(b'<html><title>not a feed</title></html>',stream(),'2026-10-05','2026-10-16')
        self.assertFalse(result.recognized)
        self.assertFalse(result.complete)

    def test_referrer_records_only_outbound_links_and_no_article_text(self):
        raw=b'''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item>
        <title>Roundup</title><link>https://blog.example/post</link>
        <pubDate>Fri, 09 Oct 2026 12:00:00 GMT</pubDate>
        <content:encoded><![CDATA[Discussion <a href="https://primary.gov/paper?utm_source=blog">paper</a>
        <a href="https://blog.example/about">internal</a>]]></content:encoded>
        </item></channel></rss>'''
        ref={'id':'blog','name':'Blog','endpoint':'https://blog.example/feed','status':'active','provenance':'test'}
        referrals,result=d.parse_referrer_feed(raw,ref,'2026-10-05','2026-10-16')
        self.assertEqual(len(referrals),1)
        self.assertEqual(referrals[0]['target_url'],'https://primary.gov/paper')
        self.assertNotIn('text',referrals[0])
        self.assertFalse(result.window_covered)

    def test_referrer_skips_link_farms_and_nonprimary_news(self):
        links=''.join(f'<a href="https://news.example/{i}">n</a>' for i in range(5))
        raw=f'''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item>
        <title>Rolling tracker</title><link>https://blog.example/post</link>
        <pubDate>Fri, 09 Oct 2026 12:00:00 GMT</pubDate><content:encoded><![CDATA[{links}]]></content:encoded>
        </item></channel></rss>'''.encode()
        ref={'id':'blog','name':'Blog','endpoint':'https://blog.example/feed','status':'active',
          'provenance':'test','max_links_per_post':3}
        referrals,result=d.parse_referrer_feed(raw,ref,'2026-10-05','2026-10-16')
        self.assertEqual(referrals,[])
        self.assertTrue(any('link-heavy' in note for note in result.notes))

    def test_duplicate_candidate_tracks_second_route_once(self):
        base=dict(title='Paper',date='2026-10-10',source='Source',themes=[],
          url='https://example.org/paper',category='economy',work_id='doi:one')
        w.add_candidates(self.run,[{**base,'route':'a'},{**base,'route':'b'},{**base,'route':'b'}])
        candidate=self.state()['candidates'][0]
        self.assertEqual(candidate['also_found_by'],['b'])


if __name__=='__main__': unittest.main()
