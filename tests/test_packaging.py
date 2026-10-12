import unittest
from importlib.resources import files
from clipbeta.engine import load_captions

class PackageResourceTests(unittest.TestCase):
    def test_installed_synthetic_fixture_is_available(self):
        rows=load_captions(files('clipbeta').joinpath('data/synthetic.srt'))
        self.assertEqual(len(rows),5)
    def test_installed_flow_asset_is_available(self):
        self.assertIn('class Flow',files('clipbeta').joinpath('web/flow.js').read_text())
