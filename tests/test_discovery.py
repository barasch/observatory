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

    def test_signal_policy_requires_positive_threshold(self):
        registry={'schema_version':2,'streams':[stream(admission={'mode':'signals','min_score':0})]}
        with self.assertRaisesRegex(ValueError,'positive min_score'): d.validate_registry(registry)

    def test_x_referrer_requires_named_account(self):
        registry={'schema_version':2,'streams':[stream()],'referrers':[{
          'id':'x','name':'X','endpoint':'https://x.com/example','adapter':'x_account',
          'status':'experimental','provenance':'test'}]}
        with self.assertRaisesRegex(ValueError,'needs a handle'): d.validate_registry(registry)

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

    def test_bls_calendar_keeps_only_major_in_window_releases(self):
        source=stream(adapter='bls_calendar',endpoint='https://www.bls.gov/schedule/news_release/bls.ics')
        raw=b'''BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20261030T083000
SUMMARY:Employment Cost Index
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20261029T100000
SUMMARY:Consumer Expenditures
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20261106T083000
SUMMARY:Employment Situation
END:VEVENT
END:VCALENDAR'''
        result=d.parse_bls_calendar(raw,source,'2026-10-25','2026-11-01')
        self.assertEqual([r['title'] for r in result.records],['Employment Cost Index'])
        self.assertEqual(result.records[0]['date'],'2026-10-30')
        self.assertEqual(result.records[0]['date_status'],'provisional')
        self.assertTrue(result.complete)

    def test_crossref_extracts_structure_without_storing_abstract(self):
        source=stream(adapter='crossref',endpoint='https://api.crossref.org/journals/1/works')
        raw=json.dumps({'message':{'total-results':1,'items':[{
          'DOI':'10.1234/example','title':['Research'], 'created':{'date-parts':[[2026,10,9]]},
          'abstract':'<p>body text</p>','reference':[{}]*5,'is-referenced-by-count':1,
        }]}}).encode()
        record=d.parse_crossref(raw,source,'2026-10-05','2026-10-16').records[0]
        self.assertEqual(record['admission_signals'],['early-citation','research-metadata'])
        self.assertNotIn('abstract',record)

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

    def test_arxiv_journal_link_is_structural_signal(self):
        source=stream(adapter='arxiv',endpoint='https://export.arxiv.org/api/query')
        raw=b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom">
        <opensearch:totalResults>1</opensearch:totalResults><opensearch:startIndex>0</opensearch:startIndex>
        <entry><id>http://arxiv.org/abs/2610.00001v1</id><title>Paper</title><published>2026-10-09T00:00:00Z</published>
        <link rel="alternate" href="https://arxiv.org/abs/2610.00001v1"/><arxiv:journal_ref>Journal 1 (2026)</arxiv:journal_ref></entry></feed>'''
        record=d.parse_arxiv(raw,source,'2026-10-05','2026-10-16').records[0]
        self.assertEqual(record['work_id'],'arxiv:2610.00001')
        self.assertEqual(record['admission_signals'],['journal-linked'])

    def test_published_preprint_route_keeps_only_in_window_first_releases(self):
        source=stream(adapter='biorxiv_pubs',endpoint='https://api.biorxiv.org/pubs/biorxiv/x')
        raw=json.dumps({'messages':[{'total':'2'}],'collection':[
          {'preprint_doi':'10.1101/new','preprint_title':'New preprint','preprint_date':'2026-10-09',
           'published_date':'2026-10-12','preprint_category':'biology','preprint_abstract':'not retained'},
          {'preprint_doi':'10.1101/old','preprint_title':'Old preprint','preprint_date':'2025-10-09',
           'published_date':'2026-10-12','preprint_category':'biology'},
        ]}).encode()
        result=d.parse_biorxiv_pubs(raw,source,'2026-10-05','2026-10-16')
        self.assertEqual(len(result.records),1)
        self.assertEqual(result.records[0]['admission_signals'],['journal-linked'])
        self.assertNotIn('abstract',result.records[0])

    def test_biorxiv_status_label_is_not_journal_linkage(self):
        source=stream(adapter='biorxiv',endpoint='https://api.biorxiv.org/details/biorxiv/x')
        raw=json.dumps({'messages':[{'total':'1'}],'collection':[{
          'doi':'10.1101/example','title':'Preprint','date':'2026-10-09','version':'1',
          'published':'PUBLISHAHEADOFPRINT','category':'biology',
        }]}).encode()
        record=d.parse_biorxiv(raw,source,'2026-10-05','2026-10-16').records[0]
        self.assertEqual(record['admission_signals'],[])

    def test_source_declared_pagination_urls(self):
        bio=json.dumps({'messages':[{'total':'205'}],'collection':[{}]*100}).encode()
        self.assertEqual(
          d.next_page_endpoint('biorxiv_pubs',bio,'https://api.biorxiv.org/pubs/biorxiv/a/b/0'),
          'https://api.biorxiv.org/pubs/biorxiv/a/b/100')
        trials=json.dumps({'nextPageToken':'next token'}).encode()
        next_trials=d.next_page_endpoint('clinicaltrials',trials,'https://clinicaltrials.gov/api/v2/studies?pageSize=100')
        self.assertIn('pageToken=next+token',next_trials)
        arxiv=b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
        <opensearch:totalResults>3</opensearch:totalResults><opensearch:startIndex>0</opensearch:startIndex>
        <entry/><entry/></feed>'''
        next_arxiv=d.next_page_endpoint('arxiv',arxiv,'https://export.arxiv.org/api/query?start=0&max_results=2')
        self.assertIn('start=2',next_arxiv)

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

    def test_pith_referrer_keeps_ranked_arxiv_pointer_without_review_text(self):
        raw=b'''<html><article class="feed-entry"><a href="/paper/2610.12345">Useful result</a>
        <p>Model-written review prose that must not be retained.</p><span>Pith reviewed 2026-10-10</span></article>
        <article class="feed-row"><a href="/paper/2609.00001">Old review</a>
        <span>reviewed 2026-09-30</span></article></html>'''
        ref={'id':'pith','name':'Pith','endpoint':'https://pith.science/?sort=impact',
          'adapter':'pith','status':'provisional','provenance':'test'}
        referrals,result=d.parse_pith_referrer(raw,ref,'2026-10-05','2026-10-16')
        self.assertEqual(len(referrals),1)
        self.assertEqual(referrals[0]['target_url'],'https://arxiv.org/abs/2610.12345')
        self.assertNotIn('text',referrals[0])
        self.assertEqual(result.seen,2)

    def test_referred_arxiv_metadata_is_compact_and_signal_gated(self):
        raw=b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <id>http://arxiv.org/abs/2610.12345v1</id><title>Referred paper</title>
        <published>2026-10-10T00:00:00Z</published><category term="quant-ph"/>
        <summary>full abstract must not survive</summary></entry></feed>'''
        records=d.parse_arxiv_referral_metadata(raw,{'2610.12345':{'pith'}},'https://export.arxiv.org/api/query')
        self.assertEqual(records[0]['category'],'science')
        self.assertEqual(records[0]['admission_signals'],['external-referral'])
        self.assertNotIn('abstract',records[0])
        registry={'schema_version':2,'streams':[stream()], 'referrers':[
          {'id':'pith','name':'Pith','endpoint':'https://pith.science/','adapter':'pith',
           'status':'provisional','provenance':'test'}]}
        w.add_inventory(self.run,records); d.admit_inventory(self.run,registry)
        self.assertEqual([c['title'] for c in self.state()['candidates']],['Referred paper'])

    def test_referred_ny_opinion_metadata_retains_no_opinion_text(self):
        raw=b'''<html><head><title>Case</title></head><body>
        <div class="case-info"><h1>Example v Respondent</h1>
        <p>2026 NY Slip Op 06394</p><p>October 29, 2026</p>
        <p>Appellate Division, First Department</p></div>
        <p>Substantive opinion text must not survive.</p></body></html>'''
        records=d.parse_ny_opinion_referral_metadata(raw,{'digest'},
          'https://www.nycourts.gov/reporter/current/3dseries/2026/2026_06394.shtml')
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['date'],'2026-10-29')
        self.assertEqual(records[0]['admission_signals'],['external-referral'])
        self.assertNotIn('text',records[0])

    def test_duplicate_candidate_tracks_second_route_once(self):
        base=dict(title='Paper',date='2026-10-10',source='Source',themes=[],
          url='https://example.org/paper',category='economy',work_id='doi:one')
        w.add_candidates(self.run,[{**base,'route':'a'},{**base,'route':'b'},{**base,'route':'b'}])
        candidate=self.state()['candidates'][0]
        self.assertEqual(candidate['also_found_by'],['b'])

    def test_inventory_admission_uses_signals_not_route_order(self):
        all_route=stream(id='low')
        gated=stream(id='broad',admission={'mode':'signals','min_score':2})
        registry={'schema_version':2,'streams':[gated,all_route]}
        records=[
          dict(title='Unsignalled',date='2026-10-10',source='Source',themes=[],url='https://example.org/u',route='broad',category='economy'),
          dict(title='Referred',date='2026-10-10',source='Source',themes=[],url='https://doi.org/10.1234/referred',work_id='doi:10.1234/referred',route='broad',category='economy'),
          dict(title='Baseline',date='2026-10-10',source='Source',themes=[],url='https://example.org/b',route='low',category='economy'),
        ]
        w.add_inventory(self.run,records)
        w.add_referrals(self.run,[{'referrer_id':'blog','post_title':'Post','post_date':'2026-10-10',
          'post_url':'https://blog.example/post','target_url':'https://publisher.org/doi/10.1234/referred'}])
        d.apply_discovery_signals(self.run); d.admit_inventory(self.run,registry)
        state=self.state()
        self.assertEqual({c['title'] for c in state['candidates']},{'Referred','Baseline'})
        referred=next(c for c in state['candidates'] if c['title']=='Referred')
        self.assertIn('external-referral',referred['admission_signals'])
        self.assertEqual(state['admission']['status'],'complete')

    def test_admission_overflow_fails_closed_without_truncating(self):
        registry={'schema_version':2,'streams':[stream(id='low')]}
        records=[dict(title=f'Paper {i}',date='2026-10-10',source='Source',themes=[],
          url=f'https://example.org/{i}',route='low',category='economy') for i in range(1001)]
        w.add_inventory(self.run,records); d.admit_inventory(self.run,registry)
        state=self.state()
        self.assertEqual(state['candidates'],[])
        self.assertEqual(state['admission']['status'],'overflow')
        self.assertEqual(state['coverage']['admission_overflow'],1001)


if __name__=='__main__': unittest.main()
