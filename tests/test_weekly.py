import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import weekly as w
from editions import validate_edition, render
from test_editions import EXAMPLE

class WeeklyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.run=Path(self.tmp.name)
        w.init(self.run,'2026-10-05','2026-10-16')
    def tearDown(self): self.tmp.cleanup()
    def candidate(self, **kw):
        return dict(title='A study', date='2026-10-10', source='Research series', themes=['employment'],
                    url='https://example.org/paper?utm_source=test',route='series',category='economy',**kw)
    def selected(self):
        w.add_candidates(self.run,[self.candidate()]);w.transition(self.run,'screen')
        ident=self.state()['candidates'][0]['id']
        w.screen(self.run,{ident:{'decision':'advance','reason':'Employment evidence'}})
        w.transition(self.run,'assess');return ident
    def state(self): return json.loads((self.run/'run.json').read_text())
    def test_no_public_run_data(self):
        with self.assertRaises(ValueError): w.init(w.ROOT/'private','2026-10-05','2026-10-16')
    def test_failures_and_pending_calls_consume_budget_on_resume(self):
        for _ in range(40):
            t=w.reserve(self.run,'registry','https://example.org');w.finish(self.run,t,'failed')
        with self.assertRaises(ValueError): w.reserve(self.run,'registry','https://example.org')
        self.assertEqual(w.audit(self.run)['actions']['registry'],40)
        with self.assertRaises(ValueError): w.init(self.run,'2026-10-05','2026-10-16')
    def test_stage_gates_and_reserved_verification_allowance(self):
        with self.assertRaises(ValueError): w.reserve(self.run,'assess','https://example.org','x')
        self.selected()
        with self.assertRaises(ValueError):w.reserve(self.run,'search','more searches')
        t=w.reserve(self.run,'checks','private prerequisites');w.finish(self.run,t,'ok')
    def test_metadata_whitelist_dedup_and_date_boundaries(self):
        a=self.candidate();a['abstract']='secret long abstract '*100
        b={**a,'url':'https://example.org/paper?utm_source=other'}
        c={**a,'url':'https://example.org/outside','date':'2026-10-16'}
        w.add_candidates(self.run,[a,b,c]);s=self.state()
        self.assertEqual(len(s['candidates']),1)
        self.assertNotIn('abstract',s['candidates'][0]);self.assertLessEqual(w.words(s['candidates'][0]['screen_text']),50)
        self.assertEqual(s['excluded'][0]['reason'],'outside-window')
    def test_text_caps_include_repeated_exposure(self):
        ident=self.selected();t=w.reserve(self.run,'assess','https://example.org',ident);w.finish(self.run,t,'ok')
        self.assertEqual(w.words(w.expose(self.run,'assess',ident,'word '*1000,'page 1')),600)
        with self.assertRaises(ValueError):w.expose(self.run,'assess',ident,'more','page 1')
        with self.assertRaises(ValueError):w.transition(self.run,'verify')
    def test_uninspected_claim_cannot_advance(self):
        ident=self.selected()
        with self.assertRaises(ValueError):w.assessment(self.run,ident,dict(status='assessed',evidence='data',scope='US',finding='x',limitations='y',source_characteristics='z',decision='select'))
    def test_pending_call_prevents_checkpoint_and_double_finish(self):
        w.add_candidates(self.run,[self.candidate()]); t=w.reserve(self.run,'registry','url')
        with self.assertRaises(ValueError):w.transition(self.run,'screen')
        w.finish(self.run,t,'failed')
        with self.assertRaises(ValueError):w.finish(self.run,t,'ok')
        w.transition(self.run,'screen')
    def test_atom_and_rss_metadata(self):
        source={'id':'feed','publisher':'Institute','endpoint':'https://example.org/feed','category':'science','source_type':'Research'}
        raw=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Study</title><published>2026-10-10T00:00:00Z</published><link href="https://example.org/study"/><summary>Not for screening</summary><category term="physics"/></entry></feed>'
        records=w.feed_records(raw,source)
        self.assertEqual(records[0]['date'],'2026-10-10');self.assertEqual(records[0]['themes'],['physics']);self.assertNotIn('summary',records[0])
    def test_feed_date_can_come_from_description(self):
        source={'id':'court','publisher':'Court','endpoint':'https://example.gov/feed','category':'law','source_type':'Court opinion'}
        raw=b'<rss><channel><item><title>People v Example</title><link>https://example.gov/opinion/1</link><description>&lt;table&gt;&lt;td&gt;October 15, 2026&lt;/td&gt;&lt;/table&gt;</description></item></channel></rss>'
        records=w.feed_records(raw,source)
        self.assertEqual(records[0]['date'],'2026-10-15')
    def test_schema_two_twenty_items_window_and_old_compatibility(self):
        validate_edition(copy.deepcopy(EXAMPLE))
        e=copy.deepcopy(EXAMPLE);e.update(schema_version=2,window={'start':'2026-09-10','end':'2026-09-17'})
        e['items']=[{**e['items'][0],'id':f'item-{i}','document_url':f'https://example.org/{i}'} for i in range(20)]
        validate_edition(e);self.assertIn('Coverage: 2026-09-10',render(e))
        e['items'][0]['published']='2026-09-17'
        with self.assertRaises(ValueError):validate_edition(e)
    def test_revision_fields_are_bounded_and_escaped(self):
        e=copy.deepcopy(EXAMPLE);e.update(schema_version=2,window={'start':'2026-09-10','end':'2026-09-17'})
        e['items'][0]['revision_of']={'item_id':'original-item','edition_date':'2026-09-01','changes':'New estimate <script>unsafe</script>'}
        validate_edition(e);self.assertNotIn('<script>unsafe',render(e))
        e['items'][0]['revision_of']['private_reason']='personal feedback'
        with self.assertRaises(ValueError):validate_edition(e)
    def test_publication_gate_holds_technical_coverage_even_outside_pilot(self):
        ident=self.selected()
        t=w.reserve(self.run,'assess','https://example.org/paper',ident);w.finish(self.run,t,'ok')
        w.expose(self.run,'assess',ident,'Evidence about employment.','source passage')
        w.assessment(self.run,ident,dict(status='assessed',evidence='records',scope='US',
            finding='finding',limitations='limits',source_characteristics='primary',decision='advance-to-verify'))
        w.transition(self.run,'verify')
        t=w.reserve(self.run,'verify','https://example.org/paper',ident);w.finish(self.run,t,'ok')
        w.expose(self.run,'verify',ident,'Verified claim and release date.','exact passage')
        w.assessment(self.run,ident,dict(status='verified',passages=['exact passage'],
            release_date='2026-10-10',first_release_date='2026-10-10',
            document_url='https://example.org/paper',claims_checked=['claim']),verify=True)
        with w.transaction(self.run) as state:
            state['coverage']={'complete':True,'shortfall':'editorial'}
        w.transition(self.run,'ready')
        edition=copy.deepcopy(EXAMPLE)
        edition.update(schema_version=2,date='2026-10-16',prepared_at='2026-10-16T17:00:00Z',
                       window={'start':'2026-10-05','end':'2026-10-16'})
        edition['items'][0].update(published='2026-10-10',source_url='https://example.org/paper',
                                  document_url='https://example.org/paper')
        first=w.publication_gate(self.run,edition,{})
        self.assertEqual(first['hold_reasons'],['pilot'])
        with w.transaction(self.run) as state:
            state['pilot']=False
            state['coverage']={'complete':False,'shortfall':'technical'}
        second=w.publication_gate(self.run,edition,{})
        self.assertFalse(second['may_publish'])
        self.assertEqual(second['hold_reasons'],['technical-coverage'])

if __name__=='__main__':unittest.main()
