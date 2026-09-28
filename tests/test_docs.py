"""Documentation stays in step with the code (standing rule: every change
updates the docs).  These tests fail when a generated section is stale; the
message says which command regenerates it."""

import importlib.util
import os
import unittest

import helpers
from nfe import cam_rules
from nfe.cam_defaults import DEFAULT_PROFILES

DOCS = os.path.join(helpers.ROOT, "docs")


def _load_generator(name="gen_test_catalogue"):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(helpers.ROOT, "scripts", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class GeneratedDocs(unittest.TestCase):
    def test_testing_md_catalogue_is_current(self):
        """docs/TESTING.md lists every test with its docstring; regenerate with
        `py -3 scripts/gen_test_catalogue.py` after adding or changing tests."""
        gen = _load_generator()
        with open(os.path.join(DOCS, "TESTING.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(gen.current_block(text), gen.render(gen.collect()),
                         "docs/TESTING.md test catalogue is out of date: "
                         "run py -3 scripts/gen_test_catalogue.py")

    def test_toolpath_tables_doc_matches_defaults(self):
        """docs/tables/TOOLPATH_SELECTION.md shows the built-in toolpath tables;
        regenerate with `py -3 -m nfe.cam_rules ../../docs/tables/TOOLPATH_SELECTION.md`
        (from addin/NewFusionExtension)."""
        path = os.path.join(DOCS, "tables", "TOOLPATH_SELECTION.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(cam_rules.render_markdown(DEFAULT_PROFILES).strip(), text)

    def test_svg_diagrams_are_current(self):
        """docs/workflow.svg and docs/infrastructure.svg match what
        scripts/gen_diagrams.py draws; edit the script and rerun
        `py -3 scripts/gen_diagrams.py` when the flow changes."""
        self.assertEqual(_load_generator("gen_diagrams").stale(), [],
                         "SVG diagrams out of date: run py -3 scripts/gen_diagrams.py")

    def test_docs_mention_every_module(self):
        """ARCHITECTURE.md describes every module in the nfe package."""
        nfe_dir = os.path.join(helpers.ADDIN, "nfe")
        with open(os.path.join(DOCS, "ARCHITECTURE.md"), encoding="utf-8") as fh:
            arch = fh.read()
        missing = [f for f in sorted(os.listdir(nfe_dir))
                   if f.endswith(".py") and f != "__init__.py" and "nfe/%s" % f not in arch]
        self.assertEqual(missing, [], "ARCHITECTURE.md does not describe: %s" % ", ".join(missing))


if __name__ == "__main__":
    unittest.main()
