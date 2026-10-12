import copy
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from clipbeta.standalone import AppState,LocalServer,make_handler,strict_json
from clipbeta.engine import ValidationError


def fake_analyze(media,out,config,progress,cancelled):
    progress({'stage':'rank','progress':.6,'message':'test adapter'})
    return {'transcript':[{'start_ms':0,'end_ms':1000,'text':'この子かわいいね <script>alert(1)</script>'}],
            'candidates':[{'id':'candidate1','title':'<img src=x onerror=alert(1)>','start_ms':0,'end_ms':2000,'evidence':[{'cue_id':'1','quote':'かわいい'}]}], 'duration_ms':3000}

def fake_render(plan,media,out,**kw):
    out.mkdir();p=out/'preview.mp4';p.write_bytes(b'0123456789');return p

class StandaloneTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.state=AppState(self.tmp.name,1000,fake_analyze,fake_render)
        self.server=LocalServer(('127.0.0.1',0),make_handler(self.state));self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.tmp.cleanup()
    def request(self,method,path,body=None,headers=None,auth=True):
        h={'X-Clipbeta-Token':self.state.token} if auth else {}
        if isinstance(body,dict):body=json.dumps(body);h['Content-Type']='application/json'
        h.update(headers or {});c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        c.request(method,path,body,h);r=c.getresponse();status=r.status;hdr=dict(r.getheaders());data=r.read();c.close();return status,hdr,data
    def video(self):return self.state.import_stream(io.BytesIO(b'video'),'../private.mp4','video',5)['id']
    def finish(self,job):
        until=time.monotonic()+3
        while job.state in {'running','cancelling'} and time.monotonic()<until:time.sleep(.01)
        self.assertNotIn(job.state,{'running','cancelling'});return job
    def analyze(self):
        j=self.finish(self.state.analyze({'media_id':self.video(),'config':{}}));self.assertEqual(j.state,'complete',j.error);return j
    def review(self,j):
        _,p=self.state.proposal(j.id,'candidate1')
        return {'schema':p['schema'],'clip_id':p['clip_id'],'decisions':[{'id':x['id'],'status':'adopted','text':x['text'],'style':copy.deepcopy(x['style'])} for x in p['items']]}
    def test_full_http_flow_and_ranges(self):
        status,_,data=self.request('POST','/api/import?kind=video&name=video.mp4',b'video');self.assertEqual(status,201);asset=json.loads(data)
        status,_,data=self.request('POST','/api/analyze',{'media_id':asset['id'],'config':{}});self.assertEqual(status,202)
        j=self.finish(self.state.get_job(json.loads(data)['id']));self.assertEqual(j.state,'complete')
        status,_,html=self.request('GET','/api/review?job='+j.id+'&candidate=candidate1');self.assertEqual(status,200);self.assertIn(b'clipbeta-render',html);self.assertNotIn(b'<script>alert(1)</script>',html)
        status,_,data=self.request('POST','/api/render',{'analysis_job_id':j.id,'candidate_id':'candidate1','review':self.review(j),'options':{}});self.assertEqual(status,202)
        r=self.finish(self.state.get_job(json.loads(data)['id']));self.assertEqual(r.state,'complete',r.error)
        path='/media?id='+r.result['output_id'];status,h,data=self.request('GET',path,headers={'Range':'bytes=2-5'});self.assertEqual((status,data),(206,b'2345'));self.assertEqual(h['Content-Range'],'bytes 2-5/10')
        self.assertEqual(self.request('GET',path,headers={'Range':'bytes=99-'})[0],416)
    def test_host_origin_token_guards(self):
        self.assertEqual(self.request('GET','/',auth=False)[0],400)
        self.assertEqual(self.request('GET','/',headers={'Origin':'https://evil.example'})[0],400)
        self.assertEqual(self.request('GET','/',headers={'Host':'evil.example'})[0],400)
        status,_,data=self.request('GET','/');self.assertEqual(status,200);self.assertIn(self.state.token.encode(),data)
    def test_body_and_unknown_routes(self):
        self.assertEqual(self.request('POST','/api/analyze','{"config":{},"config":{}}',{'Content-Type':'application/json'})[0],400)
        self.assertEqual(self.request('POST','/api/analyze',b'abc')[0],400)
        self.assertEqual(self.request('GET','/../../etc/passwd')[0],404)
        self.assertEqual(self.request('GET','/media?id=../../etc/passwd')[0],400)
    def test_import_bounds_extensions_and_cleanup(self):
        for name,length in [('x.exe',2),('x.mp4',1001),('x.mp4',0)]:
            with self.assertRaises(ValidationError):self.state.import_stream(io.BytesIO(b'xx'),name,'video',length)
        before=list(Path(self.tmp.name).iterdir())
        with self.assertRaises(ValidationError):self.state.import_stream(io.BytesIO(b'xx'),'x.mp4','video',3)
        self.assertEqual(before,list(Path(self.tmp.name).iterdir()))
        key=self.video();self.assertTrue(self.state.asset(key)['path'].is_relative_to(self.tmp.name))
    def test_cancel_serial_and_stale_result(self):
        entered=threading.Event();release=threading.Event()
        def work(job):entered.set();release.wait(2);return {'stale':True}
        j=self.state.new_job('analysis',work);entered.wait(1)
        with self.assertRaises(ValidationError):self.state.new_job('analysis',work)
        status,_,data=self.request('GET','/api/current');self.assertEqual(status,200);self.assertEqual(json.loads(data)['job']['id'],j.id)
        self.state.cancel(j.id);self.state.cancel(j.id)
        with self.assertRaises(ValidationError):self.state.new_job('analysis',work)
        release.set();self.finish(j);self.assertEqual(j.state,'cancelled');self.assertIsNone(j.result)
        self.assertEqual(self.state.cancel(j.id)['state'],'cancelled')
        self.assertIsNone(json.loads(self.request('GET','/api/current')[2])['job'])
    def test_pending_review_and_wrong_candidate_rejected(self):
        j=self.analyze();r=self.review(j);r['decisions'][0]['status']='pending'
        with self.assertRaises(ValidationError):self.state.render({'analysis_job_id':j.id,'candidate_id':'candidate1','review':r})
        with self.assertRaises(ValidationError):self.state.proposal(j.id,'unknown')
    def test_duplicate_candidates_fail_closed(self):
        def bad(*args):r=fake_analyze(*args);r['candidates']*=2;return r
        self.state.analyzer=bad;j=self.finish(self.state.analyze({'media_id':self.video()}));self.assertEqual(j.state,'error');self.assertIn('duplicate',j.error)
    def test_manual_range_rebuild_and_stale_review(self):
        j=self.analyze();old=self.review(j)
        _,changed=self.state.proposal(j.id,'candidate1',{'start_ms':100,'end_ms':1500})
        self.assertEqual(changed['range_start_ms'],100)
        self.assertNotEqual(changed['clip_id'],old['clip_id'])
        with self.assertRaises(ValidationError):self.state.render({'analysis_job_id':j.id,'candidate_id':'candidate1','range':{'start_ms':100,'end_ms':1500},'review':old})
        for bounds in [{'start_ms':-1,'end_ms':1000},{'start_ms':0,'end_ms':60001},{'start_ms':0.5,'end_ms':1000},{'start_ms':0,'end_ms':4000}]:
            with self.assertRaises(ValidationError):self.state.proposal(j.id,'candidate1',bounds)
        status,_,data=self.request('GET','/api/review?job='+j.id+'&candidate=candidate1&start_ms=100&end_ms=1500')
        self.assertEqual(status,200);self.assertIn(changed['clip_id'].encode(),data)

    def test_twitch_mock_transport_orchestration(self):
        class FakeDownloader:
            @staticmethod
            def list_archives(url,**kw):return {'target':{'kind':'channel'},'archives':[{'id':'123','url':'https://www.twitch.tv/videos/123','title':'synthetic','duration_ms':3000}]}
            @staticmethod
            def acquire(url,out,**kw):out.mkdir();p=out/'source.mp4';p.write_bytes(b'video');return {'path':p,'metadata':{}}
        self.state.downloader=FakeDownloader
        j=self.finish(self.state.twitch_list({'url':'https://twitch.tv/example'}));self.assertEqual(j.state,'complete');self.assertEqual(len(j.result['archives']),1)
        d=self.finish(self.state.twitch_acquire({'url':j.result['archives'][0]['url']}));self.assertEqual(d.state,'complete')
        a=self.finish(self.state.analyze({'media_id':d.result['id']}));self.assertEqual(a.state,'complete')
        with self.assertRaises(ValidationError):self.state.twitch_acquire({'url':'https://127.0.0.1/x'})
        with self.assertRaises(ValidationError):self.state.twitch_acquire({'url':'https://twitch.tv/example'})

    def test_unknown_settings_and_nonfinite(self):
        with self.assertRaises(ValidationError):self.state.analyze({'media_id':self.video(),'config':{'remote_url':'https://evil.example'}})
        for s in ['{"x":NaN}','{"x":1,"x":2}']:
            with self.assertRaises(ValidationError):strict_json(s)
    def test_renderer_output_must_be_its_new_directory(self):
        j=self.analyze();outside=Path(self.tmp.name)/'unrelated.mp4';outside.write_bytes(b'old')
        self.state.renderer=lambda *a,**kw:outside
        r=self.finish(self.state.render({'analysis_job_id':j.id,'candidate_id':'candidate1','review':self.review(j)}));self.assertEqual(r.state,'error');self.assertEqual(outside.read_bytes(),b'old')

if __name__=='__main__':unittest.main()
