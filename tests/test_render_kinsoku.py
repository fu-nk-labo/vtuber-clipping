"""Bounded Japanese automatic wrapping; all fixtures are synthetic text/media."""
import shutil
import tempfile
import unittest
import unicodedata
from copy import deepcopy
from pathlib import Path

from clipbeta import engine as e
from clipbeta import render as r

FIXTURE='かわいい。宝物は金貨じゃなくて、この子だったんですね。'
WRAPPED='かわいい。宝物は金貨じゃなくて、この子\nだったんですね。'


def plan_for(text):
    proposal=e.make_proposal([{'start_ms':0,'end_ms':1000,'text':text}],0,1000,1000)
    proposal['items'][0]['status']='adopted'
    return e.export_plan(proposal)


def fonts_ready():
    try:
        from PIL import ImageFont
        r.discover_fonts();return True
    except (ImportError,e.ValidationError):return False


class KinsokuWrappingTests(unittest.TestCase):
    def test_small_tsu_fixture_moves_preceding_character(self):
        self.assertEqual(r.wrap_caption(FIXTURE),WRAPPED)
        self.assertEqual([pair for line in r.caption_lines(FIXTURE) for pair in line],list(enumerate(FIXTURE)))

    def test_japanese_closing_punctuation_and_small_kana_never_start_automatic_lines(self):
        for char in r.JAPANESE_NO_START|set(')]}.,!?'):
            with self.subTest(char=char):
                text='あ'*19+'だ'+char+'続き'
                lines=r.wrap_caption(text).split('\n')
                self.assertEqual(lines,['あ'*19,'だ'+char+'続き'])
                self.assertTrue(all(sum(not unicodedata.combining(c) for c in line)<=20 for line in lines))

    def test_combining_mark_moves_with_its_base_character(self):
        text='あ'*19+'か\u3099っです'
        self.assertEqual(r.wrap_caption(text),'あ'*19+'\nか\u3099っです')
        self.assertEqual([pair for line in r.caption_lines(text) for pair in line],list(enumerate(text)))

    def test_marks_and_joined_sequences_are_not_cut_by_automatic_wrap(self):
        text='あ'*19+'👩\u200d💻です'
        self.assertEqual(r.wrap_caption(text),'あ'*19+'\n👩\u200d💻です')
        text='あ'*19+'☀\ufe0fです'
        self.assertEqual(r.wrap_caption(text),'あ'*19+'\n☀\ufe0fです')

    def test_explicit_newlines_and_blank_lines_are_preserved(self):
        for text in ['あ'*20+'\nっ。','最初\n\n最後','最初\n\u3099最後']:
            with self.subTest(text=text):self.assertEqual(r.wrap_caption(text),text)

    def test_english_keeps_existing_twenty_character_wrapping(self):
        self.assertEqual(r.wrap_caption('0123456789abcdefghij! rest'),'0123456789abcdefghij\n! rest')
        self.assertEqual(r.wrap_caption('a'*60),'\n'.join(['a'*20]*3))
        self.assertEqual(r.wrap_caption('hello\tworld'),'hello world')

    def test_three_line_cap_remains_strict_when_kinsoku_needs_an_extra_line(self):
        self.assertEqual(len(r.wrap_caption('あ'*60).split('\n')),3)
        with self.assertRaisesRegex(e.ValidationError,'at most 3 wrapped caption lines'):
            r.wrap_caption('あ'*19+'だっ'+'あ'*39)
        with self.assertRaises(e.ValidationError):r.wrap_caption('っ'*21)


@unittest.skipUnless(fonts_ready(),'Noto fonts or Pillow unavailable')
class KinsokuStyleTests(unittest.TestCase):
    def test_fixture_keeps_only_the_original_keyword_pink(self):
        plan=plan_for(FIXTURE);before=deepcopy(plan)
        runs=r.caption_runs(plan)
        self.assertEqual([run['text'] for run in runs if run['color']=='pink'],['かわいい'])
        by_line={}
        for run in runs:by_line.setdefault(run['baseline'],[]).append(run['text'])
        self.assertEqual([''.join(parts) for _,parts in sorted(by_line.items())],WRAPPED.split('\n'))
        self.assertTrue(all(run['x']>=12 for run in runs))
        self.assertEqual(plan,before)

    def test_shifted_line_preserves_keyword_codepoint_offsets(self):
        for text in ['あ'*19+'だっかわいい','あ'*19+'だっ\n\tかわいい']:
            with self.subTest(text=text):
                plan=plan_for(text);span=plan['operations'][0]['style']['spans'][0]
                self.assertEqual(span['start'],text.index('かわいい'))
                runs=r.caption_runs(plan)
                self.assertEqual(''.join(run['text'] for run in runs if run['color']=='pink'),'かわいい')
                self.assertEqual(''.join(run['text'] for run in runs),text.replace('\t',' ').replace('\n',''))


@unittest.skipUnless(fonts_ready() and shutil.which('ffmpeg') and shutil.which('ffprobe'),
                     'FFmpeg, Noto fonts or Pillow unavailable')
class KinsokuRenderTests(unittest.TestCase):
    def test_fixture_renders_literal_wrapping_and_visible_word_color(self):
        with tempfile.TemporaryDirectory(prefix='clipbeta-kinsoku-') as directory:
            base=Path(directory);source=r.synthetic_source(base/'synthetic.mp4')
            result=r.render(plan_for(FIXTURE),source,base/'render')
            self.assertEqual((result.parent/'caption-0.txt').read_text(encoding='utf-8'),WRAPPED)
            self.assertEqual((result.parent/'run-0-0-0.txt').read_text(encoding='utf-8'),'かわいい')
            self.assertEqual((result.parent/'run-0-1-0.txt').read_text(encoding='utf-8'),'だったんですね。')
            raw=r.run([shutil.which('ffmpeg'),'-v','error','-ss','0.4','-i',str(result),
                       '-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
            band=raw[640*250*3:]
            pixels=list(zip(band[0::3],band[1::3],band[2::3]))
            self.assertGreater(sum(red>180 and 75<green<190 and blue>150 for red,green,blue in pixels),100)
            self.assertGreater(sum(min(red,green,blue)>200 for red,green,blue in pixels),100)


if __name__=='__main__':unittest.main()
