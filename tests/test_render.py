import hashlib
import json
import shutil
import struct
import tempfile
import unittest
import wave
from copy import deepcopy
from pathlib import Path
from clipbeta import render as r
from clipbeta.engine import read_json,ValidationError,stable_id
ROOT=Path(__file__).resolve().parents[1]

class RenderUnitTests(unittest.TestCase):
    def setUp(self):self.plan=read_json(ROOT/'samples/edit-plan.json')
    def digest(self,p):p['revision_id']=stable_id('rev_',{k:v for k,v in p.items() if k!='revision_id'});return p
    def test_wrap_and_limit(self):
        self.assertEqual(r.wrap_caption('あ'*21),'あ'*20+'\nあ')
        with self.assertRaises(ValidationError):r.wrap_caption('あ'*61)
    def test_reject_remote_and_playlist(self):
        for p in ['https://twitch.tv/videos/123','https://example.com/video.mp4','a.m3u8']:
            with self.assertRaises(ValidationError):r.local_media(p)
    def test_reject_long_or_overlapping_plan(self):
        p=deepcopy(self.plan);p['operations'][0]['end_ms']=3000;self.digest(p)
        with self.assertRaises(ValidationError):r.validate_render_plan(p)
    def test_text_never_interpolated_in_filter(self):
        p=deepcopy(self.plan);p['operations'][0]['text']="%{metadata:secret}';movie=https://evil";self.digest(p)
        graph=r.video_filter(p)
        self.assertNotIn('evil',graph);self.assertIn('expansion=none',graph)
    def test_noop_has_no_zoom_or_se(self):
        p=deepcopy(self.plan)
        for op in p['operations']:op['style']['zoom']='none';op['style']['se']='none'
        self.digest(p);self.assertIn("zoompan=z='1'",r.video_filter(p))
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'se.wav';r.synth_se(p,path)
            with wave.open(str(path)) as w:self.assertEqual(set(w.readframes(w.getnframes())),{0})
    def test_se_duration_and_bounds(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'se.wav';r.synth_se(self.plan,path)
            with wave.open(str(path)) as w:
                self.assertEqual(w.getnframes(),11*48000);raw=w.readframes(w.getnframes())
                values=struct.unpack('<'+'h'*(len(raw)//2),raw)
            self.assertLess(max(values),7000);self.assertGreater(max(values),5000)
            self.assertEqual(max(abs(v) for v in values[:48000]),0)

@unittest.skipUnless(shutil.which('ffmpeg') and r.FONT.exists(),'FFmpeg/fonts unavailable')
class RenderIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.base=Path(cls.tmp.name)
        cls.source=r.synthetic_source(cls.base/'source.mp4')
        cls.plan=read_json(ROOT/'samples/edit-plan.json')
        cls.video=r.render(cls.plan,cls.source,cls.base/'first')
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_duration_streams(self):
        p=r.probe(self.video);self.assertAlmostEqual(float(p['format']['duration']),11,places=2)
        self.assertEqual({x['codec_type'] for x in p['streams']},{'audio','video'})
        v=next(x for x in p['streams'] if x['codec_type']=='video');self.assertEqual(v['nb_frames'],'330')
    def test_repeat_render_identical_bytes(self):
        second=r.render(self.plan,self.source,self.base/'second')
        self.assertEqual(hashlib.sha256(self.video.read_bytes()).hexdigest(),hashlib.sha256(second.read_bytes()).hexdigest())
    def test_output_refuses_existing_directory(self):
        before=self.video.read_bytes()
        with self.assertRaises(FileExistsError):r.render(self.plan,self.source,self.base/'first')
        self.assertEqual(self.video.read_bytes(),before)
    def test_invalid_actual_duration_rejected_before_writes(self):
        p=deepcopy(self.plan);p['range_start_ms']+=15000;p['range_end_ms']+=15000
        p['clip_id']=stable_id('clip_',[p['source_id'],p['range_start_ms'],p['range_end_ms']])
        p['revision_id']=stable_id('rev_',{k:v for k,v in p.items() if k!='revision_id'})
        with self.assertRaises(ValidationError):r.render(p,self.source,self.base/'invalid')
        self.assertFalse((self.base/'invalid').exists())
    def test_decoded_audio_and_safe_caption(self):
        raw=r.run(['ffmpeg','-v','error','-i',str(self.video),'-f','s16le','-ac','1','-ar','48000','-'])
        samples=struct.unpack('<'+'h'*(len(raw)//2),raw)
        self.assertGreater(max(samples),1000)
        self.assertLess(max(abs(v) for v in samples[:40000]),10)

    def test_silent_video_input_fallback(self):
        source=self.base/'no-audio.mp4'
        r.run(['ffmpeg','-v','error','-n','-i',str(self.source),'-c:v','copy','-an',str(source)])
        result=r.render(self.plan,source,self.base/'no-audio-result')
        self.assertIn('audio',{x['codec_type'] for x in r.probe(result)['streams']})
    def test_unsafe_caption_renders_as_plain_text(self):
        p=deepcopy(self.plan)
        p['operations'][0]['text']="%{metadata:key}';[x]"
        p['revision_id']=stable_id('rev_',{k:v for k,v in p.items() if k!='revision_id'})
        result=r.render(p,self.source,self.base/'plain-text')
        self.assertTrue(result.is_file())
        self.assertEqual((result.parent/'caption-0.txt').read_text(),p['operations'][0]['text'])
    def test_word_colors_and_target_zoom_render_pixels(self):
        p=read_json(ROOT/'samples/word-styles/edit-plan.json')
        result=r.render(p,self.source,self.base/'word-colors')
        def frame(path,t):return r.run(['ffmpeg','-v','error','-ss',str(t),'-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
        raw=frame(result,1);pixels=list(zip(raw[0::3],raw[1::3],raw[2::3]))[640*250:]
        self.assertGreater(sum(red>180 and 75<green<190 and blue>150 for red,green,blue in pixels),100)
        self.assertGreater(sum(min(red,green,blue)>200 for red,green,blue in pixels),100)
        raw=frame(result,4);pixels=list(zip(raw[0::3],raw[1::3],raw[2::3]))[640*250:]
        self.assertGreater(sum(red<150 and green>180 and blue>180 for red,green,blue in pixels),100)
        original=frame(result,4)[:640*200*3]
        for op in p['operations']:op['style']['zoom_target']={'x':.5,'y':.5}
        p['revision_id']=stable_id('rev_',{k:v for k,v in p.items() if k!='revision_id'})
        centered=r.render(p,self.source,self.base/'word-centered')
        self.assertNotEqual(original,frame(centered,4)[:640*200*3])
