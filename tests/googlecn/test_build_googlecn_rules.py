import importlib.util
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts/googlecn/build_googlecn_rules.py"
SPEC = importlib.util.spec_from_file_location("build_googlecn_rules", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

from shared.v2fly import Rule, SourceMetadata, parse_source_files, render_rules


def make_tree():
    return parse_source_files(
        {
            "google": [
                "include:google-deepmind",
                "include:youtube",
                "include:googlefcm",
                "google.cn @cn",
                "full:dl.google.com @cn",
                "full:ocsp.pki.goog @cn",
                "full:fonts.gstatic.com @cn",
                "full:adservice.google.com @cn @ads",
                "full:mtalk.google.com @cn",
                "full:ambiguous.google.com @cn",
            ],
            "google-deepmind": ["gemini.google.com"],
            "youtube": ["youtube.com"],
            "googlefcm": ["mtalk.google.com"],
        }
    )


class ClassificationTests(unittest.TestCase):
    def test_conservative_rules_are_approved_and_ambiguous_is_held(self):
        rules, decisions, counts = MODULE.classify_candidates(make_tree(), [], [])
        identities = {rule.identity for rule in rules}
        self.assertIn(("domain", "google.cn"), identities)
        self.assertIn(("full", "dl.google.com"), identities)
        self.assertIn(("full", "fonts.gstatic.com"), identities)
        self.assertNotIn(("full", "ambiguous.google.com"), identities)
        self.assertEqual(counts["review"], 1)
        self.assertEqual(
            decisions["review"][0]["identity"],
            "full:ambiguous.google.com",
        )

    def test_ads_and_messaging_are_hard_excluded(self):
        _, decisions, _ = MODULE.classify_candidates(make_tree(), [], [])
        reasons = {
            item["identity"]: item["reason"] for item in decisions["excluded"]
        }
        self.assertEqual(
            reasons["full:adservice.google.com"],
            "v2fly-ads-attribute",
        )
        self.assertEqual(
            reasons["full:mtalk.google.com"],
            "conflicts-with-google-messaging",
        )

    def test_allow_patch_cannot_override_hard_exclusion(self):
        with self.assertRaises(MODULE.BuildError):
            MODULE.classify_candidates(
                make_tree(), [Rule("full", "mtalk.google.com")], []
            )


class SafetyTests(unittest.TestCase):
    def test_unchanged_unresolved_candidates_do_not_block_future_refresh(self):
        rules, decisions, _ = MODULE.classify_candidates(make_tree(), [], [])
        previous = {item["identity"] for item in decisions["review"]}
        assessment = MODULE.assess_change(
            rules, rules, decisions, previous
        )
        self.assertTrue(assessment["auto_merge_eligible"])
        self.assertEqual(assessment["new_unresolved_candidate_count"], 0)

    def test_new_unresolved_candidate_requires_review(self):
        rules, decisions, _ = MODULE.classify_candidates(make_tree(), [], [])
        assessment = MODULE.assess_change(rules, rules, decisions, set())
        self.assertFalse(assessment["auto_merge_eligible"])
        self.assertIn("new-unresolved-candidates", assessment["reasons"])

    def test_render_has_lowercase_repo_and_no_trailing_newline(self):
        metadata = SourceMetadata(
            "example/repo", "main", "abc", "2026-07-23T00:00:00Z"
        )
        rendered = render_rules(
            "GoogleCN", metadata, [Rule("full", "dl.google.com")]
        )
        self.assertIn("schmidttt/surge-rules", rendered)
        self.assertTrue(rendered.endswith("DOMAIN,dl.google.com"))
        self.assertFalse(rendered.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
