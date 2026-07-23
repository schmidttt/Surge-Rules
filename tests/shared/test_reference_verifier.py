import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from shared.reference_verifier import (  # noqa: E402
    ReferenceVerificationError,
    load_resolution_catalog,
    summarize_generation_decisions,
    verify_reference_sources,
)


@dataclass(frozen=True)
class Rule:
    kind: str
    value: str


class ReferenceVerificationTests(unittest.TestCase):
    def test_invalid_catalog_raises_specific_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            path.write_text(
                '{"schema_version": 2, "decisions": []}',
                encoding="utf-8",
            )
            with self.assertRaises(ReferenceVerificationError):
                load_resolution_catalog(path, "ai")

    def test_unknown_catalog_scope_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "decisions": [
                            {
                                "scopes": ["unknown"],
                                "kind": "domain",
                                "value": "example.com",
                                "action": "exclude-reference-scope",
                                "target": None,
                                "reason": "fixture",
                                "evidence": ["https://example.com/"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ReferenceVerificationError):
                load_resolution_catalog(path, "ai")

    def test_coverage_and_single_source_are_automatic(self):
        report = verify_reference_sources(
            {
                "first": [
                    Rule("domain", "covered.example"),
                    Rule("domain", "single.example"),
                ]
            },
            {"Output": [Rule("domain", "covered.example")]},
        )
        self.assertEqual(report["auto_resolved_count"], 2)
        self.assertEqual(report["manual_review_count"], 0)
        self.assertEqual(report["decision_counts"]["single-reference-only"], 1)

    def test_corroborated_gap_requires_review(self):
        gap = Rule("domain", "gap.example")
        report = verify_reference_sources(
            {"first": [gap], "second": [gap]},
            {"Output": []},
        )
        self.assertEqual(report["manual_review_count"], 1)
        self.assertEqual(
            report["manual_review"][0]["reason"],
            "corroborated-reference-gap",
        )

    def test_exact_host_scope_requires_catalog_resolution(self):
        reference = Rule("domain", "endpoint.example")
        output = Rule("full", "endpoint.example")
        unresolved = verify_reference_sources(
            {"first": [reference], "second": [reference]},
            {"Output": [output]},
        )
        self.assertEqual(unresolved["manual_review_count"], 1)
        resolved = verify_reference_sources(
            {"first": [reference], "second": [reference]},
            {"Output": [output]},
            {
                ("domain", "endpoint.example"): {
                    "action": "accept-exact-host",
                    "target": "Output",
                    "reason": "official-exact-endpoint",
                    "evidence": ["https://example.com/docs"],
                }
            },
        )
        self.assertEqual(resolved["manual_review_count"], 0)
        self.assertEqual(
            resolved["decision_counts"]["accept-exact-host"],
            1,
        )

    def test_catalog_validation_and_scope_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "decisions.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "decisions": [
                            {
                                "scopes": ["ai"],
                                "kind": "full",
                                "value": "api.example",
                                "action": "exclude-shared-service",
                                "target": None,
                                "reason": "shared",
                                "evidence": ["https://example.com/docs"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            selected = load_resolution_catalog(path, "ai")
            self.assertIn(("full", "api.example"), selected)
            self.assertEqual(load_resolution_catalog(path, "media"), {})

    def test_generation_summary_tracks_only_unresolved(self):
        report = summarize_generation_decisions(
            {"approved": 3, "excluded": 2},
            [{"identity": "regexp:example", "reason": "unsupported"}],
        )
        self.assertEqual(report["auto_resolved_count"], 5)
        self.assertEqual(report["manual_review_count"], 1)


if __name__ == "__main__":
    unittest.main()
