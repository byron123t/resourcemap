"""pipeline/live/view_for.py must agree with viewFor() in site/index.html (same cases as tests/view_for.test.mjs).
Run: python3 -I -m unittest discover -s tests -p "test_*.py"
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pipeline", "live"))
import view_for as vf  # noqa: E402


class ViewFor(unittest.TestCase):
    def test_cases(self):
        with open(os.path.join(HERE, "view_for_cases.json"), encoding="utf-8") as f:
            cases = json.load(f)
        for c in cases:
            with self.subTest(c["ctx"]):
                self.assertEqual(vf.view_for(**c["ctx"]), c["view"])

    def test_views_exist(self):
        v = vf.views()
        self.assertGreater(len(v), 30)
        for r in vf.rules():
            self.assertIn(r["view"], v)


if __name__ == "__main__":
    unittest.main()
