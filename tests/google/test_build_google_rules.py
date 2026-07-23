import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "google" / "build_google_rules.py"
SPEC = importlib.util.spec_from_file_location("build_google_rules", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ParserTests(unittest.TestCase):
    def test_load_published_rules_prefers_existing_product_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "GoogleAI.list"
            path.write_text("DOMAIN,g.ai", encoding="utf-8")
            rules = MODULE.load_published_rules(
                path,
                [MODULE.Rule("domain", "fallback.example")],
            )
            self.assertEqual(
                [rule.identity for rule in rules],
                [("full", "g.ai")],
            )

    def test_include_attributes_and_affiliation(self):
        tree = MODULE.load_local_tree(FIXTURE_ROOT / "v2fly-data")
        expanded = MODULE.expand_list(tree, "google")
        identities = {rule.identity for rule in expanded}
        self.assertIn(("domain", "fonts.gstatic.com"), identities)
        self.assertNotIn(("domain", "tracking.example"), identities)
        self.assertIn(("domain", "affiliated-google.example"), identities)

    def test_circular_include_fails(self):
        tree = MODULE.parse_source_files(
            {"a": ["include:b"], "b": ["include:a"]}
        )
        with self.assertRaises(MODULE.BuildError):
            MODULE.expand_list(tree, "a")

    def test_keyword_in_target_is_reported_and_omitted(self):
        tree = MODULE.parse_source_files(
            {
                "google": [
                    "include:google-deepmind",
                    "include:youtube",
                    "google.com",
                    "googleapis.com",
                    "gstatic.com",
                    "keyword:google",
                ],
                "google-deepmind": ["gemini.google.com"],
                "youtube": ["youtube.com"],
            }
        )
        main, counts, omitted = MODULE.build_outputs(tree, [], [])
        self.assertNotIn(("keyword", "google"), {rule.identity for rule in main})
        self.assertEqual(counts["unsupported_keyword_omitted"], 1)
        self.assertEqual(omitted, ["keyword:google"])

    def test_cn_regexp_is_safely_omitted(self):
        tree = MODULE.parse_source_files(
            {
                "google": [
                    "include:google-deepmind",
                    "include:youtube",
                    "google.com",
                    "googleapis.com",
                    "gstatic.com",
                    r"regexp:^edge\.google\.com$ @cn",
                ],
                "google-deepmind": ["gemini.google.com"],
                "youtube": ["youtube.com"],
            }
        )
        _, counts, omitted = MODULE.build_outputs(tree, [], [])
        self.assertEqual(counts["cn_tagged_in_source"], 1)
        self.assertEqual(counts["unsupported_regexp_omitted"], 1)
        self.assertEqual(omitted, [r"regexp:^edge\.google\.com$"])

    def test_parse_sukka_references(self):
        global_rules = MODULE.parse_sukka_global_google(
            (FIXTURE_ROOT / "Sukka-global.ts").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn(MODULE.Rule("domain", "reference-video.example"), global_rules)
        self.assertIn(MODULE.Rule("full", "reference-exact.example"), global_rules)

        ai_rules, unsupported, unsupported_lines = MODULE.parse_sukka_google_ai(
            (FIXTURE_ROOT / "Sukka-ai.conf").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn(MODULE.Rule("domain", "reference-ai.example"), ai_rules)
        self.assertEqual(unsupported["DOMAIN-KEYWORD"], 1)
        self.assertEqual(unsupported["URL-REGEX"], 1)
        self.assertEqual(len(unsupported_lines), 2)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tree = MODULE.load_local_tree(FIXTURE_ROOT / "v2fly-data")

    def test_product_partition_keeps_cn_and_ads_attributes(self):
        include = [MODULE.Rule("full", "manual.google.example")]
        exclude = [MODULE.Rule("domain", "affiliated-google.example")]
        main, counts, omitted = MODULE.build_outputs(self.tree, include, exclude)
        main_ids = {rule.identity for rule in main}

        self.assertIn(("domain", "google.com"), main_ids)
        self.assertIn(("full", "accounts.google.com"), main_ids)
        self.assertIn(("full", "manual.google.example"), main_ids)
        self.assertIn(("domain", "fonts.gstatic.com"), main_ids)
        self.assertIn(("domain", "ads.google.com"), main_ids)
        self.assertNotIn(("domain", "gemini.google.com"), main_ids)
        self.assertNotIn(("full", "gemini.google.com"), main_ids)
        self.assertNotIn(("domain", "youtube.com"), main_ids)
        self.assertNotIn(("domain", "affiliated-google.example"), main_ids)
        self.assertGreater(counts["product_exact_excluded"], 0)
        self.assertEqual(counts["ads_tagged_in_source"], 1)
        self.assertEqual(counts["cn_tagged_in_source"], 2)
        self.assertEqual(omitted, [])

    def test_render_has_no_trailing_newline(self):
        metadata = MODULE.SourceMetadata(
            "example/repo", "main", "abc", "2026-07-21T08:54:40Z"
        )
        rendered = MODULE.render_rules(metadata, [MODULE.Rule("domain", "google.com")])
        self.assertTrue(rendered.startswith("# NAME: schmidttt's Google Ruleset\n"))
        self.assertIn("# UPDATED: 2026.07.21 16:54:40", rendered)
        self.assertIn("# TOTAL: 1", rendered)
        self.assertIn("# ======== 上游同步规则 ========", rendered)
        self.assertTrue(rendered.endswith("DOMAIN-SUFFIX,google.com"))
        self.assertFalse(rendered.endswith("\n"))

    def test_large_change_guard(self):
        main = [
            MODULE.Rule("domain", "google.com"),
            MODULE.Rule("domain", "googleapis.com"),
            MODULE.Rule("domain", "gstatic.com"),
        ]
        existing = {("domain", "example{}.com".format(index)) for index in range(20)}
        with self.assertRaises(MODULE.BuildError):
            MODULE.validate_outputs(
                main, existing, 0.10, False
            )

    def test_equal_size_replacement_is_counted_as_real_churn(self):
        main = [
            MODULE.Rule("domain", "google.com"),
            MODULE.Rule("domain", "googleapis.com"),
            MODULE.Rule("domain", "gstatic.com"),
            MODULE.Rule("domain", "new.example"),
        ]
        existing = {
            ("domain", "google.com"),
            ("domain", "googleapis.com"),
            ("domain", "gstatic.com"),
            ("domain", "old.example"),
        }
        with self.assertRaises(MODULE.BuildError):
            MODULE.validate_outputs(main, existing, 0.10, False)

    def test_addition_only_small_change_is_low_risk(self):
        existing = {
            ("domain", "google.com"),
            ("domain", "googleapis.com"),
            ("domain", "gstatic.com"),
        }
        main = [MODULE.Rule(kind, value) for kind, value in existing]
        main.append(MODULE.Rule("domain", "new.example"))
        assessment = MODULE.assess_change(
            main, existing, [], set(), 20, 0.50, 0.10
        )
        self.assertTrue(assessment["auto_merge_eligible"])
        self.assertEqual(assessment["classification"], "low-risk")
        self.assertEqual(assessment["added_count"], 1)
        self.assertEqual(assessment["removed_count"], 0)

    def test_any_deletion_requires_review(self):
        existing = {
            ("domain", "google.com"),
            ("domain", "googleapis.com"),
            ("domain", "gstatic.com"),
            ("domain", "old.example"),
        }
        main = [
            MODULE.Rule(kind, value)
            for kind, value in existing
            if value != "old.example"
        ]
        assessment = MODULE.assess_change(
            main, existing, [], set(), 20, 0.50, 0.10
        )
        self.assertFalse(assessment["auto_merge_eligible"])
        self.assertIn("rules-removed", assessment["reasons"])

    def test_unsupported_rule_change_requires_review(self):
        existing = {
            ("domain", "google.com"),
            ("domain", "googleapis.com"),
            ("domain", "gstatic.com"),
        }
        main = [MODULE.Rule(kind, value) for kind, value in existing]
        assessment = MODULE.assess_change(
            main, existing, ["regexp:new"], {"regexp:old"}, 20, 0.50, 0.10
        )
        self.assertFalse(assessment["auto_merge_eligible"])
        self.assertIn("unsupported-rule-set-changed", assessment["reasons"])

    def test_increased_reference_manual_review_requires_review(self):
        existing = {
            ("domain", "google.com"),
            ("domain", "googleapis.com"),
            ("domain", "gstatic.com"),
        }
        main = [MODULE.Rule(kind, value) for kind, value in existing]
        assessment = MODULE.assess_change(
            main,
            existing,
            [],
            set(),
            20,
            0.50,
            0.10,
            {"global_google": 3, "google_ai": 1},
            {"global_google": 2, "google_ai": 1},
        )
        self.assertFalse(assessment["auto_merge_eligible"])
        self.assertIn(
            "reference-manual-review-increased",
            assessment["reasons"],
        )

    def test_changed_reference_manual_review_set_requires_review(self):
        existing = {
            ("domain", "google.com"),
            ("domain", "googleapis.com"),
            ("domain", "gstatic.com"),
        }
        main = [MODULE.Rule(kind, value) for kind, value in existing]
        assessment = MODULE.assess_change(
            main,
            existing,
            [],
            set(),
            20,
            0.50,
            0.10,
            {
                "manual_review": 2,
                "manual_review_fingerprint": "a" * 64,
            },
            {
                "manual_review": 2,
                "manual_review_fingerprint": "b" * 64,
            },
        )
        self.assertIn(
            "reference-manual-review-set-changed",
            assessment["reasons"],
        )

    def test_blackmatrix_is_comparison_only(self):
        main, _, _ = MODULE.build_outputs(self.tree, [], [])
        text = (FIXTURE_ROOT / "BlackMatrix-Google.list").read_text(
            encoding="utf-8"
        )
        report = MODULE.comparison_report(main, text)
        identities = {rule.identity for rule in main}
        self.assertNotIn(("domain", "blackmatrix-only.example"), identities)
        self.assertEqual(report["blackmatrix_non_domain_types"]["DOMAIN-KEYWORD"], 1)
        self.assertNotIn("only_blackmatrix_sample", report)
        self.assertNotIn("only_v2fly_generated_sample", report)

    def test_reference_audit_uses_product_priority(self):
        main, _, _ = MODULE.build_outputs(self.tree, [], [])
        audit = MODULE.classify_reference_rules(
            [
                MODULE.Rule("domain", "reference-ai.example"),
                MODULE.Rule("domain", "reference-video.example"),
                MODULE.Rule("domain", "reference-main.example"),
                MODULE.Rule("full", "reference-exact.example"),
                MODULE.Rule("domain", "unknown.example"),
            ],
            main,
            MODULE.expand_list(self.tree, "google-deepmind"),
            MODULE.expand_list(self.tree, "youtube"),
        )
        self.assertEqual(audit["counts"]["google_ai"], 1)
        self.assertEqual(audit["counts"]["youtube"], 1)
        self.assertEqual(audit["counts"]["google_main"], 2)
        self.assertEqual(audit["counts"]["needs_review"], 1)

    def test_missing_official_core_fails(self):
        with self.assertRaises(MODULE.BuildError):
            MODULE.validate_official_core(
                [MODULE.Rule("full", "missing.official.example")],
                [MODULE.Rule("domain", "google.com")],
                [],
                [],
            )


class IntegrationTests(unittest.TestCase):
    def test_local_build_writes_all_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "patches/google").mkdir(parents=True)
            for name in ("include.txt", "exclude.txt"):
                (root / "patches/google" / name).write_text("", encoding="utf-8")
            exit_code = MODULE.main(
                [
                    "--source-dir",
                    str(FIXTURE_ROOT / "v2fly-data"),
                    "--blackmatrix-file",
                    str(FIXTURE_ROOT / "BlackMatrix-Google.list"),
                    "--sukka-global-file",
                    str(FIXTURE_ROOT / "Sukka-global.ts"),
                    "--sukka-ai-file",
                    str(FIXTURE_ROOT / "Sukka-ai.conf"),
                    "--official-core-file",
                    str(FIXTURE_ROOT / "google-official-core.txt"),
                    "--project-root",
                    str(root),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue((root / "rules/Google/Google.list").is_file())
            self.assertFalse((root / "rules/Google/GoogleCN-Candidate.list").exists())
            self.assertTrue((root / "reports/google/google-report.json").is_file())
            self.assertTrue((root / "reports/google/reference-audit.json").is_file())
            self.assertTrue((root / "reports/google/change-assessment.json").is_file())
            audit = (root / "reports/google/reference-audit.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("reference-only.example", audit)
            self.assertNotIn("reference-unsupported.example", audit)
            self.assertIn('"entries_persisted": false', audit)
            self.assertIn('"third_party_reference_entries_persisted": false', audit)
            self.assertIn('"reference_entries_auto_merged": false', audit)
            self.assertIn('"verification"', audit)


if __name__ == "__main__":
    unittest.main()
