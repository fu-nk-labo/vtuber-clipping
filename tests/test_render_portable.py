"""Offline portability checks; all media is generated synthetic video."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock,patch

from clipbeta import engine as e
from clipbeta import render as r

ROOT=Path(__file__).resolve().parents[1]


def short_plan():
    proposal=e.make_proposal([{'start_ms':0,'end_ms':1000,'text':'かわいい 合成テスト'}],0,1000,1000)
    proposal['items'][0]['status']='adopted'
    return e.export_plan(proposal)


class PortableRenderUnitTests(unittest.TestCase):
    def test_resolution_presets_and_default(self):
        options=r.render_options()
        self.assertEqual((options['width'],options['height']),(640,360))
        self.assertEqual(options['max_clip_seconds'],60)
        for width,height in r.RESOLUTIONS:
            self.assertEqual(r.render_options({'width':width,'height':height})['width'],width)
        for options in [{'width':1280},{'width':1920,'height':720},{'width':True},
                        {'width':'640'},{'shell':True},{'max_clip_seconds':301},
                        {'max_clip_seconds':False},{'max_clip_seconds':float('nan')},
                        {'max_clip_seconds':float('inf')},{'max_clip_seconds':0},
                        {'font_path':''},{'ffmpeg_path':['ffmpeg']}]:
            with self.subTest(options=options),self.assertRaises(e.ValidationError):
                r.render_options(options)

    def test_options_are_not_mutated_and_environment_is_supported(self):
        original={'width':1280,'height':720}
        before=deepcopy(original)
        with patch.dict(os.environ,{'CLIPBETA_FFMPEG_PATH':'some local ffmpeg.exe'}):
            self.assertEqual(r.render_options(original)['ffmpeg_path'],'some local ffmpeg.exe')
        self.assertEqual(original,before)

    def test_explicit_executable_is_one_path_not_a_shell_command(self):
        self.assertEqual(r.resolve_executable('ffmpeg',sys.executable),str(Path(sys.executable).resolve()))
        with self.assertRaises(e.ValidationError):r.resolve_executable('ffmpeg',sys.executable+'; echo unsafe')
        with tempfile.TemporaryDirectory(prefix='clipbeta executable ') as folder:
            executable=Path(folder)/Path(sys.executable).name
            shutil.copyfile(sys.executable,executable);executable.chmod(0o700)
            self.assertEqual(r.resolve_executable('ffmpeg',executable),str(executable.resolve()))
        with patch.object(r.shutil,'which',return_value='C:/tools/ffmpeg.cmd'):
            with self.assertRaisesRegex(e.ValidationError,'shell wrapper'):
                r.resolve_executable('ffmpeg','C:/tools/ffmpeg.cmd')

    def test_windows_system_and_user_font_locations(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);windows=base/'Windows';local=base/'Local AppData'
            system=windows/'Fonts';user=local/'Microsoft/Windows/Fonts'
            system.mkdir(parents=True);user.mkdir(parents=True)
            regular=system/'NotoSansJP-Regular.ttf';regular.write_bytes(b'font fixture')
            bold=user/'NotoSansJP-Bold.ttf';bold.write_bytes(b'font fixture')
            with patch.dict(os.environ,{'WINDIR':str(windows),'LOCALAPPDATA':str(local)},clear=True), \
                    patch.object(r,'FONT',base/'missing.ttf'),patch.object(r,'BOLD',base/'missing-bold.ttf'):
                roots=r.font_directories()
                self.assertIn(system,roots);self.assertIn(user,roots)
                with patch.object(r,'font_directories',return_value=[system,user]):
                    self.assertEqual(r.discover_fonts(),(regular.resolve(),bold.resolve()))

    def test_font_configuration_is_authoritative(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);regular=base/'regular.ttf';bold=base/'bold.ttf'
            regular.write_bytes(b'font fixture');bold.write_bytes(b'font fixture')
            options={'font_path':regular,'bold_font_path':bold}
            self.assertEqual(r.discover_fonts(options),(regular.resolve(),bold.resolve()))
            options['font_path']=base/'missing.ttf'
            with self.assertRaises(e.ValidationError):r.discover_fonts(options)

    def test_optional_clip_cap_is_bounded_and_default_unchanged(self):
        plan=e.read_json(ROOT/'samples/edit-plan.json')
        plan['range_end_ms']=plan['range_start_ms']+120000
        plan['clip_id']=e.stable_id('clip_',[plan['source_id'],plan['range_start_ms'],plan['range_end_ms']])
        plan['revision_id']=e.stable_id('rev_',{key:value for key,value in plan.items() if key!='revision_id'})
        with self.assertRaises(e.ValidationError):r.validate_render_plan(plan)
        self.assertEqual(r.validate_render_plan(plan,{'max_clip_seconds':120}),120)

    def test_cancel_before_run_launches_nothing(self):
        stopped=threading.Event();stopped.set()
        with patch.object(r.subprocess,'Popen') as popen,self.assertRaises(r.RenderCancelled):
            r.run([sys.executable,'-c','pass'],cancelled=stopped)
        popen.assert_not_called()

    def test_cancel_stops_a_running_silent_process(self):
        started=time.monotonic();processes=[];popen=subprocess.Popen
        def record(*args,**kwargs):
            child=popen(*args,**kwargs);processes.append(child);return child
        with patch.object(r.subprocess,'Popen',side_effect=record),self.assertRaises(r.RenderCancelled):
            r.run([sys.executable,'-c','import time; time.sleep(30)'],
                  cancelled=lambda:time.monotonic()-started>.2)
        self.assertLess(time.monotonic()-started,4)
        self.assertEqual(len(processes),1)
        self.assertIsNotNone(processes[0].poll())

    def test_timeout_stops_process_and_reports_useful_error(self):
        started=time.monotonic()
        with self.assertRaisesRegex(e.ValidationError,'timeout'):
            r.run([sys.executable,'-c','import time; time.sleep(30)'],timeout=.15)
        self.assertLess(time.monotonic()-started,4)

    def test_unresponsive_process_is_killed_after_termination_grace(self):
        process=Mock();process.poll.return_value=None
        process.wait.side_effect=[subprocess.TimeoutExpired('synthetic process',2),None]
        r._stop_process(process)
        process.terminate.assert_called_once();process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count,2)

    def test_failure_keeps_error_tail(self):
        with self.assertRaisesRegex(e.ValidationError,'synthetic failure'):
            r.run([sys.executable,'-c',"import sys; print('synthetic failure',file=sys.stderr); sys.exit(2)"])

    def test_large_stdout_and_stderr_do_not_deadlock(self):
        result=r.run([sys.executable,'-c',
                      "import sys; sys.stderr.write('e'*200000); sys.stdout.write('o'*200000)"])
        self.assertEqual(result,b'o'*200000)

    def test_progress_is_incremental_clamped_and_monotonic(self):
        values=[]
        code="import time; print('out_time_us=250000',flush=True); time.sleep(.2); print('out_time_us=100000',flush=True); print('out_time_us=9000000',flush=True)"
        output=r.run([sys.executable,'-c',code],duration=1,progress=values.append)
        self.assertEqual(values,[.25,.25,1.0])
        self.assertIn(b'out_time_us=9000000',output)

    def test_callback_failure_stops_process(self):
        started=time.monotonic()
        def reject(_):raise RuntimeError('callback stopped')
        with self.assertRaisesRegex(RuntimeError,'callback stopped'):
            r.run([sys.executable,'-c',"import time; print('out_time_us=250000',flush=True); time.sleep(30)"],
                  duration=1,progress=reject)
        self.assertLess(time.monotonic()-started,4)


def dependencies_ready():
    try:r.tools_ready();import PIL;return True
    except (e.ValidationError,ImportError):return False


@unittest.skipUnless(dependencies_ready(),'FFmpeg, Noto fonts or Pillow unavailable')
class PortableRenderIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='clipbeta portable ')
        cls.base=Path(cls.tmp.name);cls.plan=short_plan()
        cls.source=r.synthetic_source(cls.base/'synthetic source.mp4')

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_scaled_caption_layout_and_filter(self):
        small=r.caption_runs(self.plan);large=r.caption_runs(self.plan,{'width':1920,'height':1080})
        for before,after in zip(small,large):
            self.assertEqual(after['size'],before['size']*3)
            self.assertEqual(after['baseline'],before['baseline']*3)
            self.assertAlmostEqual(after['x'],before['x']*3,delta=.15)
        graph=r.video_filter(self.plan,{'width':1920,'height':1080})
        self.assertIn('scale=1920:1080',graph)
        self.assertIn('y=750:w=1920:h=330',graph)

    def test_720p_custom_font_paths_are_copied_and_progress_completes(self):
        # Apostrophes/spaces work on Windows too; colon emulates a Windows
        # drive separator only on systems where it is a legal filename.
        folder=self.base/("configured C: Noto's fonts" if os.name!='nt' else "configured Noto's fonts")
        folder.mkdir();regular,bold=r.discover_fonts()
        regular_copy=folder/'regular.ttf';bold_copy=folder/'bold.ttf'
        shutil.copyfile(regular,regular_copy);shutil.copyfile(bold,bold_copy)
        binaries=self.base/'local media tools';binaries.mkdir()
        executables={name:Path(shutil.which(name)) for name in ('ffmpeg','ffprobe')}
        if os.name!='nt':
            # Windows FFmpeg can depend on adjacent DLLs; never relocate an
            # installed binary away from those dependencies just for a test.
            for name in executables:
                binary=binaries/(name+'.exe')
                shutil.copyfile(executables[name],binary);binary.chmod(0o700)
                executables[name]=binary
        options={'width':1280,'height':720,'font_path':regular_copy,'bold_font_path':bold_copy,
                 'ffmpeg_path':executables['ffmpeg'],'ffprobe_path':executables['ffprobe']}
        progress=[]
        result=r.render(self.plan,self.source,self.base/'720p output',options,progress.append)
        self.assertTrue(result.is_file());self.assertFalse((result.parent/'preview.partial.mp4').exists())
        graph=(result.parent/'video-filter.txt').read_text()
        self.assertNotIn(str(folder),graph)
        self.assertIn("fontfile='font-regular.ttf'",graph)
        self.assertIn("fontfile='font-bold.ttf'",graph)
        self.assertTrue((result.parent/'font-regular.ttf').is_file())
        manifest=json.loads((result.parent/'manifest.json').read_text())
        self.assertEqual((manifest['width'],manifest['height']),(1280,720))
        self.assertEqual(manifest['video_sha256'],r.hash_file(result))
        video=next(s for s in r.probe(result)['streams'] if s['codec_type']=='video')
        self.assertEqual((video['width'],video['height']),(1280,720))
        self.assertEqual(progress[-1]['stage'],'complete');self.assertEqual(progress[-1]['progress'],1)
        self.assertEqual([p['progress'] for p in progress],sorted(p['progress'] for p in progress))
        command=json.loads((result.parent/'command.json').read_text())
        self.assertEqual(command[0],str(executables['ffmpeg'].resolve()))
        before=result.read_bytes()
        with self.assertRaises(FileExistsError):r.render(self.plan,self.source,result.parent,options)
        self.assertEqual(result.read_bytes(),before)

    def test_1080p_render_is_verified(self):
        result=r.render(self.plan,self.source,self.base/'1080p output',{'width':1920,'height':1080})
        video=next(s for s in r.probe(result)['streams'] if s['codec_type']=='video')
        self.assertEqual((video['width'],video['height']),(1920,1080))
        self.assertAlmostEqual(float(r.probe(result)['format']['duration']),1,places=2)

    def test_cancel_before_render_writes_nothing(self):
        output=self.base/'cancelled before start'
        with self.assertRaises(r.RenderCancelled):
            r.render(self.plan,self.source,output,cancelled=lambda:True)
        self.assertFalse(output.exists())

    def test_cancel_at_render_start_does_not_leave_a_finished_video(self):
        output=self.base/'cancelled before encoding';stopped=threading.Event()
        def update(event):
            if event['stage']=='rendering':stopped.set()
        with self.assertRaises(r.RenderCancelled):
            r.render(self.plan,self.source,output,progress=update,cancelled=stopped)
        self.assertFalse((output/'preview.mp4').exists())
        self.assertFalse((output/'preview.partial.mp4').exists())
        self.assertFalse((output/'manifest.json').exists())

    def test_cancel_during_encoding_terminates_and_removes_partial(self):
        output=self.base/'cancelled while encoding';stopped=threading.Event()
        def update(event):
            if event['stage']=='rendering' and event['progress']>.15:stopped.set()
        with self.assertRaises(r.RenderCancelled):
            r.render(self.plan,self.source,output,{'width':1920,'height':1080},update,stopped)
        self.assertFalse((output/'preview.mp4').exists())
        self.assertFalse((output/'preview.partial.mp4').exists())

    def test_invalid_dimensions_fail_before_output_creation(self):
        output=self.base/'invalid dimensions'
        with self.assertRaises(e.ValidationError):
            r.render(self.plan,self.source,output,{'width':1280,'height':1080})
        self.assertFalse(output.exists())


if __name__=='__main__':unittest.main()
