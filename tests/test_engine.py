import contextlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from clipbeta import engine as e
from clipbeta.__main__ import main
from clipbeta.review_html import render_review

ROOT = Path(__file__).resolve().parents[1]


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.rows = e.load_captions(ROOT/'samples/synthetic.srt')
        self.p = e.make_proposal(self.rows, 1000, 12000, 15000)

    def review(self, p=None, status='adopted'):
        p = p or self.p
        return {'schema': e.VERSION, 'clip_id': p['clip_id'], 'decisions': [
            {k: deepcopy(i[k]) for k in ['id', 'text', 'style']} | {'status': status} for i in p['items']]}

    def plan(self):
        return e.export_plan(e.apply_review(self.p, self.review()))

    def test_json_srt_equivalent(self):
        self.assertEqual(self.rows, e.load_captions(ROOT/'samples/synthetic.json'))

    def test_partial_cues_clipped_and_relative(self):
        self.assertEqual((self.p['items'][0]['start_ms'], self.p['items'][0]['end_ms']), (0,1000))
        self.assertEqual(self.p['items'][-1]['end_ms'], 11000)

    def test_stable_ids_and_determinism(self):
        self.assertEqual(self.p, e.make_proposal(self.rows,1000,12000,15000))
        self.assertEqual(self.plan(), self.plan())

    def test_backend_presets_and_noop(self):
        items = self.p['items']
        self.assertEqual(items[0]['style']['zoom'], 'none')
        self.assertEqual(items[1]['style']['se'], 'surprise')
        self.assertEqual(items[2]['style']['zoom'], 'slow_in')
        self.assertEqual(items[3]['style']['se'], 'laugh')

    def test_time_precision(self):
        for x, expected in [('1.001',1001),('01:02:03.004',3723004),('00:00:01,010',1010)]:
            self.assertEqual(e.parse_time(x),expected)

    def test_malformed_times(self):
        for x in ['-1','NaN','1e3','1.0001','00:60:00.000','00:00:99,000','1:2:3','']:
            with self.subTest(x=x), self.assertRaises(e.ValidationError): e.parse_time(x)

    def test_invalid_clip_bounds(self):
        for bounds in [(0,0,15000),(-1,1000,15000),(1,16000,15000),(0,1000,10000),(False,1000,15000)]:
            with self.subTest(bounds=bounds), self.assertRaises(e.ValidationError): e.make_proposal(self.rows,*bounds)

    def test_no_overlap(self):
        with self.assertRaises(e.ValidationError): e.make_proposal(self.rows,14001,15000,15000)

    def test_malformed_srt(self):
        for s in ['', '1\nno timestamp\nx','1\n00:00:03,000 --> 00:00:02,000\nx', '1\n00:00:00,000 --> 00:00:02,000\n']:
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'bad.srt'; p.write_text(s)
                with self.subTest(s=s), self.assertRaises(e.ValidationError): e.load_captions(p)

    def test_bad_json_captions(self):
        bads = [[], {}, self.rows+[self.rows[0]], [{'start_ms':True,'end_ms':1000,'text':'x'}],
                [{'start_ms':0,'end_ms':1000.5,'text':'x'}], [{'start_ms':0,'end_ms':1000,'text':''}],
                [{'start_ms':0,'end_ms':1000,'text':'x','unknown':0}]]
        for rows in bads:
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'bad.json'; p.write_text(json.dumps(rows))
                with self.subTest(rows=rows), self.assertRaises(e.ValidationError): e.load_captions(p)

    def test_duplicate_json_keys_and_nan(self):
        for raw in ['{"a":1,"a":2}','[NaN]','[Infinity]','{']:
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'bad.json'; p.write_text(raw)
                with self.assertRaises(e.ValidationError): e.read_json(p)

    def test_pending_blocks_export(self):
        with self.assertRaises(e.ValidationError): e.export_plan(self.p)

    def test_review_edit_adopt_reject(self):
        r=self.review(); r['decisions'][0]['text']='Edited caption'
        r['decisions'][1]['status']='rejected'
        p=e.apply_review(self.p,r); plan=e.export_plan(p)
        self.assertEqual(len(plan['operations']),4)
        self.assertEqual(plan['operations'][0]['text'],'Edited caption')
        self.assertNotEqual(self.p['items'][0]['text'],'Edited caption')
        self.assertEqual(p['items'][0]['id'],self.p['items'][0]['id'])

    def test_all_rejected_valid_empty_plan(self):
        self.assertEqual(e.export_plan(e.apply_review(self.p,self.review(status='rejected')))['operations'],[])

    def test_review_wrong_clip(self):
        r=self.review();r['clip_id']='other'
        with self.assertRaises(e.ValidationError): e.apply_review(self.p,r)

    def test_duplicate_and_unknown_decisions(self):
        for mode in ['duplicate','unknown']:
            r=self.review()
            if mode=='duplicate':r['decisions'].append(r['decisions'][0])
            else:r['decisions'][0]['id']='missing'
            with self.assertRaises(e.ValidationError):e.apply_review(self.p,r)

    def test_invalid_review_text_and_presets(self):
        for field,value in [('text',''),('text','x\x00'),('style',{'emphasis':False,'color':'url(evil)','se':'none','zoom':'none'}),('status',[])]:
            r=self.review();r['decisions'][0][field]=value
            with self.assertRaises(e.ValidationError): e.apply_review(self.p,r)

    def test_proposal_tampering(self):
        for field,value in [('start_ms',-1),('end_ms',999999),('id','bad'),('source_start_ms',999)]:
            p=deepcopy(self.p);p['items'][0][field]=value
            with self.assertRaises(e.ValidationError): e.validate_proposal(p)

    def test_duplicate_cue_rejected(self):
        p=deepcopy(self.p);p['items'].append(p['items'][0])
        with self.assertRaises(e.ValidationError): e.validate_proposal(p)

    def test_plan_digest_detects_changes(self):
        p=self.plan();p['operations'][0]['text']='tampered'
        with self.assertRaises(e.ValidationError): e.validate_plan(p)

    def test_duplicate_plan_ops_rejected(self):
        p=self.plan();p['operations'].append(p['operations'][0])
        with self.assertRaises(e.ValidationError):e.validate_plan(p)

    def test_reapply_deduplicated(self):
        plan=self.plan();r=e.reconcile(plan,plan)
        self.assertFalse(r['create']);self.assertFalse(r['update']);self.assertFalse(r['remove_managed'])
        self.assertEqual(len(r['unchanged']),len(plan['operations']))

    def test_edited_reapply_updates_same_id(self):
        old=self.plan();r=self.review();r['decisions'][0]['text']='Updated'
        new=e.export_plan(e.apply_review(self.p,r));diff=e.reconcile(new,old)
        self.assertEqual(diff['update'],[old['operations'][0]['id']]);self.assertFalse(diff['create'])
        self.assertNotEqual(old['revision_id'],new['revision_id'])

    def test_rejected_reapply_removes_managed_only(self):
        old=self.plan();r=self.review();r['decisions'][0]['status']='rejected'
        new=e.export_plan(e.apply_review(self.p,r))
        self.assertEqual(e.reconcile(new,old)['remove_managed'],[old['operations'][0]['id']])

    def test_reconcile_other_clip_rejected(self):
        p=e.make_proposal(self.rows,0,12000,15000)
        other=e.export_plan(e.apply_review(p,self.review(p)))
        with self.assertRaises(e.ValidationError):e.reconcile(self.plan(),other)

    def test_html_injection_escaped(self):
        attack='</script><script>alert("x")</script><img src=x onerror=alert(1)>'
        r=self.review();r['decisions'][0]['text']=attack
        result=render_review(e.apply_review(self.p,r))
        self.assertNotIn(attack,result);self.assertIn('\\u003c/script\\u003e',result)
        self.assertEqual(result.count('</script>'),1)
        self.assertNotIn('innerHTML',result)

    def test_non_destructive_output(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'out';e.write_new(p,'original')
            with self.assertRaises(FileExistsError):e.write_new(p,'replace')
            self.assertEqual(p.read_text(),'original')

    def test_cli_end_to_end(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            d=Path(d);proposal=d/'p.json';review=d/'review.json';reviewed=d/'reviewed.json';plan=d/'plan.json'
            self.assertEqual(main(['suggest',str(ROOT/'samples/synthetic.srt'),'--start','1','--end','12','--duration','15','--out',str(proposal)]),0)
            e.write_new(review,e.json_text(self.review(e.read_json(proposal))))
            self.assertEqual(main(['html',str(proposal),'--out',str(d/'review.html')]),0)
            self.assertEqual(main(['review',str(proposal),str(review),'--out',str(reviewed)]),0)
            self.assertEqual(main(['export',str(reviewed),'--out',str(plan)]),0)
            self.assertEqual(main(['validate',str(plan)]),0)
            self.assertEqual(main(['reconcile',str(plan),'--existing',str(plan),'--out',str(d/'diff.json')]),0)
            self.assertEqual(main(['export',str(reviewed),'--out',str(plan)]),2)
            self.assertEqual(main(['validate',str(d/'missing.json')]),2)

    def test_backend_contract_rejects_bad_provider(self):
        class Bad:
            name='stub'
            def suggest(self,text):return {'invalid':True},'bad'
        with self.assertRaises(e.ValidationError):e.make_proposal(self.rows,0,12000,15000,Bad())

if __name__=='__main__': unittest.main()
