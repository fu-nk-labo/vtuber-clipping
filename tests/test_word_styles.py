import math
import unittest
from copy import deepcopy
from clipbeta import engine as e
from clipbeta.render import video_filter,caption_runs

class WordStyleTests(unittest.TestCase):
    def test_asr_kanji_spelling_gets_literal_word_style(self):
        from clipbeta.engine import RuleBackend
        text="この子は可愛いですね。"
        style,_=RuleBackend().suggest(text)
        self.assertEqual(style['color'],'white')
        self.assertEqual(style['spans'],[{'start':4,'end':7,'color':'pink','emphasis':True}])

    def setUp(self):
        self.rows=[{'start_ms':0,'end_ms':3000,'text':'😀この子かわいいね'}]
        self.p=e.make_proposal(self.rows,0,3000,3000)
        self.style=self.p['items'][0]['style']
    def test_literal_keyword_codepoint_offsets(self):
        s=self.style['spans'][0]
        self.assertEqual((s['start'],s['end']),(4,8))
        self.assertEqual(self.rows[0]['text'][s['start']:s['end']],'かわいい')
        self.assertEqual(self.style['color'],'white');self.assertEqual(s['color'],'pink')
    def test_review_roundtrip_preserves_word_styles_and_target(self):
        self.style['zoom']='punch_in';self.style['zoom_target']={'x':.8,'y':.2}
        i=self.p['items'][0]
        review={'schema':e.VERSION,'clip_id':self.p['clip_id'],'decisions':[{k:deepcopy(i[k]) for k in ['id','text','style']}|{'status':'adopted'}]}
        p=e.export_plan(e.apply_review(self.p,review))
        self.assertEqual(p['operations'][0]['style'],self.style)
        self.assertEqual(e.reconcile(p,p)['create'],[])
    def test_invalid_span_bounds_order_and_overlap(self):
        for spans in [[{'start':0,'end':999,'color':'pink','emphasis':True}],
                      [{'start':True,'end':3,'color':'pink','emphasis':True}],
                      [{'start':2,'end':2,'color':'pink','emphasis':True}],
                      [{'start':2,'end':4,'color':'pink','emphasis':True},{'start':3,'end':5,'color':'red','emphasis':True}]]:
            s=deepcopy(self.style);s['spans']=spans
            with self.assertRaises(e.ValidationError):e.validate_style(s,self.rows[0]['text'])
    def test_invalid_coordinates(self):
        for target in [{'x':-1,'y':.5},{'x':.5,'y':1.1},{'x':float('nan'),'y':.5},{'x':True,'y':.5},{'x':.5},{'x':.5,'y':'0.5'}]:
            s=deepcopy(self.style);s['zoom_target']=target
            with self.assertRaises(e.ValidationError):e.validate_style(s,self.rows[0]['text'])
    def test_combining_and_zwj_split_rejected(self):
        for text,a,b in [('e\u0301',0,1),('👩\u200d💻',0,1),('☀\ufe0f',0,1)]:
            s={'emphasis':False,'color':'white','se':'none','zoom':'none','spans':[{'start':a,'end':b,'color':'pink','emphasis':True}]}
            with self.assertRaises(e.ValidationError):e.validate_style(s,text)
    def test_old_style_and_explicit_noop(self):
        s={'emphasis':False,'color':'white','se':'none','zoom':'none'};e.validate_style(s,'hello')
        self.p['items'][0]['style']=s;self.p['items'][0]['status']='adopted';plan=e.export_plan(self.p)
        old=video_filter(plan);plan['operations'][0]['style']['zoom_target']={'x':0,'y':1}
        self.assertEqual(old,video_filter(plan))
    def test_out_of_date_span_rejected_after_text_edit(self):
        self.p['items'][0]['text']='短い'
        with self.assertRaises(e.ValidationError):e.validate_proposal(self.p)
    def test_empty_span_array_and_extreme_valid_coordinates(self):
        s=deepcopy(self.style);s['spans']=[];s['zoom_target']={'x':0,'y':1};e.validate_style(s,'x')
    def test_literal_suggestion_is_not_semantic_inference(self):
        style,reason=e.RuleBackend().suggest('それは残念だったね')
        self.assertEqual(style['spans'][0]['color'],'cyan')
        self.assertIn('Literal',reason)
        neutral,_=e.RuleBackend().suggest('愛らしいですね')
        self.assertNotIn('spans',neutral)
