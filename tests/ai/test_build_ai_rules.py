import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts/ai/build_ai_rules.py"
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
SPEC = importlib.util.spec_from_file_location("build_ai_rules", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

from shared.v2fly import Rule, load_local_tree, render_rules


def make_tree():
    return load_local_tree(FIXTURE_ROOT / "v2fly-data")


class BuildTests(unittest.TestCase):
    def test_google_and_non_google_ai_are_partitioned(self):
        google_ai, ai, _, domestic, report = MODULE.build_rules(
            make_tree(), [], [], [], []
        )
        google_ids = {rule.identity for rule in google_ai}
        ai_ids = {rule.identity for rule in ai}
        self.assertIn(("domain", "gemini.google.com"), google_ids)
        self.assertIn(("domain", "generativelanguage.googleapis.com"), google_ids)
        self.assertNotIn(("domain", "gemini.google.com"), ai_ids)
        self.assertIn(("domain", "openai.com"), ai_ids)
        self.assertIn(("domain", "claude.ai"), ai_ids)
        self.assertNotIn(("domain", "deepseek.com"), ai_ids)
        self.assertIn(("domain", "deepseek.com"), {rule.identity for rule in domestic})
        self.assertEqual(report["counts"]["google_partition_excluded"], 3)

    def test_unsupported_rule_is_report_only(self):
        _, ai, _, _, report = MODULE.build_rules(
            make_tree(), [], [], [], []
        )
        self.assertNotIn("unsupported-ai-keyword", {rule.value for rule in ai})
        self.assertEqual(len(report["unsupported_omitted"]), 1)
        self.assertEqual(
            report["unsupported_omitted"][0]["reason"],
            "unsupported-overseas-ai-keyword",
        )

    def test_patches_cannot_hide_google_overlap(self):
        google_ai, ai, google, domestic, _ = MODULE.build_rules(
            make_tree(),
            [],
            [],
            [Rule("domain", "google.com")],
            [],
        )
        with self.assertRaises(MODULE.BuildError):
            MODULE.validate_outputs(
                google_ai * 20,
                ai * 30,
                google,
                domestic,
                [],
                [],
                True,
            )

    def test_patches_cannot_add_domestic_ai(self):
        google_ai, ai, google, domestic, _ = MODULE.build_rules(
            make_tree(),
            [],
            [],
            [Rule("domain", "deepseek.com")],
            [],
        )
        with self.assertRaises(MODULE.BuildError):
            MODULE.validate_outputs(
                google_ai * 20,
                ai * 30,
                google,
                domestic,
                [],
                [],
                True,
            )

    def test_rendered_rule_file_has_no_trailing_newline(self):
        google_ai, _, _, _, _ = MODULE.build_rules(
            make_tree(), [], [], [], []
        )
        text = render_rules(
            "GoogleAI",
            MODULE.SourceMetadata("fixture", "local", "local", None),
            google_ai,
        )
        self.assertFalse(text.endswith("\n"))


class AuditTests(unittest.TestCase):
    def test_sukka_is_comparison_only(self):
        google_ai, ai, _, _, _ = MODULE.build_rules(
            make_tree(), [], [], [], []
        )
        audit = MODULE.audit_sukka(
            (FIXTURE_ROOT / "Sukka-ai.conf").read_text(encoding="utf-8"),
            google_ai,
            ai,
        )
        self.assertEqual(audit["domain_rules"], 5)
        self.assertEqual(audit["covered_by_google_ai"], 2)
        self.assertEqual(audit["covered_by_ai"], 2)
        self.assertEqual(audit["uncovered"], 1)
        self.assertEqual(audit["unsupported_types"], {"URL-REGEX": 1})

    def test_unchanged_baseline_can_be_low_risk(self):
        google_ai, ai, _, _, report = MODULE.build_rules(
            make_tree(), [], [], [], []
        )
        unsupported = {
            "{}|{}|{}".format(
                item["source"], item["identity"], item["reason"]
            )
            for item in report["unsupported_omitted"]
        }
        assessment = MODULE.assess_change(
            google_ai,
            ai,
            google_ai,
            ai,
            report["unsupported_omitted"],
            unsupported,
            {
                "available": True,
                "domain_rules": 5,
                "uncovered": 1,
                "unsupported_types": {"URL-REGEX": 1},
            },
            {
                "available": True,
                "domain_rules": 5,
                "uncovered": 1,
                "unsupported_types": {"URL-REGEX": 1},
            },
        )
        self.assertTrue(assessment["auto_merge_eligible"])

    def test_increased_sukka_gap_requires_review(self):
        google_ai, ai, _, _, report = MODULE.build_rules(
            make_tree(), [], [], [], []
        )
        unsupported = {
            "{}|{}|{}".format(
                item["source"], item["identity"], item["reason"]
            )
            for item in report["unsupported_omitted"]
        }
        assessment = MODULE.assess_change(
            google_ai,
            ai,
            google_ai,
            ai,
            report["unsupported_omitted"],
            unsupported,
            {
                "available": True,
                "domain_rules": 6,
                "uncovered": 2,
                "unsupported_types": {"URL-REGEX": 1},
            },
            {
                "available": True,
                "domain_rules": 5,
                "uncovered": 1,
                "unsupported_types": {"URL-REGEX": 1},
            },
        )
        self.assertFalse(assessment["auto_merge_eligible"])
        self.assertIn("sukka-uncovered-count-increased", assessment["reasons"])


class IntegrationTests(unittest.TestCase):
    def test_local_build_writes_all_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(MODULE, "MINIMUM_GOOGLE_AI_RULES", 1), mock.patch.object(
                MODULE, "MINIMUM_AI_RULES", 1
            ):
                result = MODULE.main(
                    [
                        "--source-dir",
                        str(FIXTURE_ROOT / "v2fly-data"),
                        "--sukka-file",
                        str(FIXTURE_ROOT / "Sukka-ai.conf"),
                        "--project-root",
                        str(root),
                        "--allow-large-change",
                    ]
                )
            self.assertEqual(result, 0)
            expected = [
                root / "rules/GoogleAI/GoogleAI.list",
                root / "rules/AI/AI.list",
                root / "reports/ai/ai-report.json",
                root / "reports/ai/change-assessment.json",
                root / "reports/ai/review.md",
            ]
            for path in expected:
                self.assertTrue(path.is_file(), path)
                self.assertFalse(path.read_bytes().endswith(b"\n"), path)
            report = json.loads(
                (root / "reports/ai/ai-report.json").read_text(encoding="utf-8")
            )
            self.assertTrue(report["policy"]["v2fly_is_only_generation_source"])
            self.assertTrue(report["policy"]["sukka_is_audit_only"])


if __name__ == "__main__":
    unittest.main()
