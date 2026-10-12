import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from clipbeta.flow_html import render_flow
from clipbeta.engine import load_captions
from clipbeta.__main__ import main
ROOT=Path(__file__).resolve().parents[1]

class FlowHTMLTests(unittest.TestCase):
    def test_self_contained_mock(self):
        h=render_flow(load_captions(ROOT/'samples/synthetic.srt'))
        self.assertIn('MOCK',h)
        self.assertIn("default-src 'none'",h)
        self.assertNotIn('fetch(',h)
        self.assertNotIn('XMLHttpRequest',h)
        self.assertEqual(h.count('</script>'),2)
        self.assertIn('clipbeta/1',h)
        self.assertIn('元の提案JSON',h)

    def test_unsafe_text_nested_review_safe(self):
        rows=load_captions(ROOT/'samples/synthetic.srt')
        rows[0]['text']='</script><img src=x onerror=alert(1)>'
        h=render_flow(rows)
        self.assertNotIn(rows[0]['text'],h)
        self.assertEqual(h.count('</script>'),2)

    def test_cli_generation_exclusive(self):
        with tempfile.TemporaryDirectory() as d,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            p=Path(d)/'intake.html'
            self.assertEqual(main(['intake-demo','--out',str(p)]),0)
            original=p.read_bytes()
            self.assertEqual(main(['intake-demo','--out',str(p)]),2)
            self.assertEqual(p.read_bytes(),original)
