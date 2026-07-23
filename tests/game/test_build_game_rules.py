import importlib.util
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts/game/build_game_rules.py"
SPEC = importlib.util.spec_from_file_location("build_game_rules", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

from shared.v2fly import Rule, parse_source_files


def make_tree():
    return parse_source_files(
        {
            "epicgames": [
                "epicgames.com",
                r"regexp:^epic-download\.file\.myqcloud\.com$ @cn",
            ],
            "playstation": [
                "playstation",
                "playstation.com",
            ],
            "steam": [
                "steampowered.com",
                "steamchina.com @cn",
                "dl.steam.example.com @cn",
            ],
            "nintendo": [
                "nintendo.com",
                "nintendoswitch.cn @cn",
            ],
        }
    )


class BuildTests(unittest.TestCase):
    def test_platform_modules_split_by_policy_target(self):
        game, game_cn, report = MODULE.build_rules(
            make_tree(), [], [], [], []
        )
        game_ids = {rule.identity for rule in game}
        cn_ids = {rule.identity for rule in game_cn}
        self.assertIn(("domain", "steampowered.com"), game_ids)
        self.assertNotIn(("domain", "steamchina.com"), game_ids)
        self.assertIn(("domain", "steamchina.com"), cn_ids)
        self.assertIn(("domain", "nintendoswitch.cn"), cn_ids)
        self.assertIn(("full", "dl.steam.example.com"), cn_ids)
        self.assertEqual(len(report["unsupported_omitted"]), 2)

    def test_single_label_and_regex_are_not_emitted(self):
        game, game_cn, report = MODULE.build_rules(
            make_tree(), [], [], [], []
        )
        values = {rule.value for rule in game + game_cn}
        self.assertNotIn("playstation", values)
        reasons = {
            item["reason"] for item in report["unsupported_omitted"]
        }
        self.assertIn("single-label-domain-omitted", reasons)
        self.assertIn("unsupported-cn-regexp", reasons)

    def test_patches_are_exact(self):
        game, _, _ = MODULE.build_rules(
            make_tree(),
            [Rule("full", "manual.game.example")],
            [Rule("domain", "epicgames.com")],
            [],
            [],
        )
        identities = {rule.identity for rule in game}
        self.assertIn(("full", "manual.game.example"), identities)
        self.assertNotIn(("domain", "epicgames.com"), identities)


class SafetyTests(unittest.TestCase):
    def test_unchanged_unsupported_baseline_can_be_low_risk(self):
        game, game_cn, report = MODULE.build_rules(
            make_tree(), [], [], [], []
        )
        unsupported = {
            "{}|{}|{}".format(
                item["platform"], item["identity"], item["reason"]
            )
            for item in report["unsupported_omitted"]
        }
        assessment = MODULE.assess_change(
            game,
            game_cn,
            game,
            game_cn,
            report["unsupported_omitted"],
            unsupported,
        )
        self.assertTrue(assessment["auto_merge_eligible"])

    def test_deletion_requires_review(self):
        game, game_cn, report = MODULE.build_rules(
            make_tree(), [], [], [], []
        )
        assessment = MODULE.assess_change(
            game[:-1],
            game_cn,
            game,
            game_cn,
            report["unsupported_omitted"],
            {
                "{}|{}|{}".format(
                    item["platform"], item["identity"], item["reason"]
                )
                for item in report["unsupported_omitted"]
            },
        )
        self.assertFalse(assessment["auto_merge_eligible"])
        self.assertIn("rules-removed", assessment["reasons"])


if __name__ == "__main__":
    unittest.main()
