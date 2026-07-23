#!/usr/bin/env python3
"""Build modular v2fly-backed Game and conservative GameCN rulesets."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from shared.v2fly import (  # noqa: E402
    BuildError,
    Rule,
    SourceMetadata,
    covered_by_any,
    dedupe_rules,
    download_v2fly_tree,
    expand_list,
    load_existing_rules,
    load_local_tree,
    parse_patch,
    render_rules,
    resolve_github_ref,
    rule_to_surge,
    write_staged_outputs,
)
from shared.reference_verifier import summarize_generation_decisions  # noqa: E402


PLATFORMS = ("epicgames", "playstation", "steam", "nintendo")
GAME_CORE = frozenset(
    {
        ("domain", "epicgames.com"),
        ("domain", "playstation.com"),
        ("domain", "steampowered.com"),
        ("domain", "nintendo.com"),
    }
)
GAME_CN_CORE = frozenset(
    {
        ("domain", "steamchina.com"),
        ("domain", "nintendoswitch.cn"),
    }
)
CN_NON_DNS_SUFFIX_ROOTS = frozenset({"steamchina.com", "wmsjsteam.com"})
MINIMUM_GAME_RULES = 150
MINIMUM_GAME_CN_RULES = 20
MAX_BUILD_CHANGE_RATIO = 0.30
MAX_AUTO_ADDITIONS = 10
MAX_AUTO_CHANGE_RATIO = 0.05


def is_registrable_cn_root(value: str) -> bool:
    labels = value.split(".")
    if value.endswith(".com.cn"):
        return len(labels) == 3
    if value.endswith(".cn"):
        return len(labels) == 2
    return value in CN_NON_DNS_SUFFIX_ROOTS


def game_cn_rule(rule: Rule) -> Optional[Rule]:
    if rule.kind == "full":
        return Rule("full", rule.value, rule.attrs)
    if rule.kind != "domain":
        return None
    kind = "domain" if is_registrable_cn_root(rule.value) else "full"
    return Rule(kind, rule.value, rule.attrs)


def apply_patches(
    rules: Sequence[Rule],
    include_patch: Sequence[Rule],
    exclude_patch: Sequence[Rule],
) -> List[Rule]:
    exclude_ids = {rule.identity for rule in exclude_patch}
    return dedupe_rules(
        rule
        for rule in list(rules) + list(include_patch)
        if rule.identity not in exclude_ids
    )


def build_rules(
    tree,
    game_include: Sequence[Rule],
    game_exclude: Sequence[Rule],
    cn_include: Sequence[Rule],
    cn_exclude: Sequence[Rule],
) -> Tuple[List[Rule], List[Rule], Dict[str, object]]:
    game_rules: List[Rule] = []
    cn_rules: List[Rule] = []
    unsupported: List[Dict[str, str]] = []
    module_counts: Dict[str, Dict[str, int]] = {}

    for platform in PLATFORMS:
        expanded = dedupe_rules(expand_list(tree, platform))
        counts: Counter = Counter()
        for rule in expanded:
            counts["expanded"] += 1
            if "cn" in rule.attrs:
                counts["cn_tagged"] += 1
                converted = game_cn_rule(rule)
                if converted is None:
                    counts["unsupported_cn"] += 1
                    unsupported.append(
                        {
                            "platform": platform,
                            "identity": "{}:{}".format(rule.kind, rule.value),
                            "reason": "unsupported-cn-{}".format(rule.kind),
                        }
                    )
                else:
                    cn_rules.append(converted)
                continue
            if rule.kind not in {"domain", "full"}:
                counts["unsupported_global"] += 1
                unsupported.append(
                    {
                        "platform": platform,
                        "identity": "{}:{}".format(rule.kind, rule.value),
                        "reason": "unsupported-global-{}".format(rule.kind),
                    }
                )
                continue
            if "." not in rule.value:
                counts["single_label_omitted"] += 1
                unsupported.append(
                    {
                        "platform": platform,
                        "identity": "{}:{}".format(rule.kind, rule.value),
                        "reason": "single-label-domain-omitted",
                    }
                )
                continue
            game_rules.append(rule)
        module_counts[platform] = dict(sorted(counts.items()))

    game_rules = apply_patches(game_rules, game_include, game_exclude)
    cn_rules = apply_patches(cn_rules, cn_include, cn_exclude)
    covered_cn = [
        rule_to_surge(rule)
        for rule in cn_rules
        if covered_by_any(rule, game_rules)
    ]
    report = {
        "modules": module_counts,
        "unsupported_omitted": sorted(
            unsupported, key=lambda item: (item["platform"], item["identity"])
        ),
        "counts": {
            "game_output": len(game_rules),
            "gamecn_output": len(cn_rules),
            "game_include_patch": len(game_include),
            "game_exclude_patch": len(game_exclude),
            "gamecn_include_patch": len(cn_include),
            "gamecn_exclude_patch": len(cn_exclude),
            "gamecn_covered_by_game_parent_count": len(covered_cn),
        },
        "gamecn_covered_by_game_parent": covered_cn,
    }
    return game_rules, cn_rules, report


def validate_outputs(
    game_rules: Sequence[Rule],
    cn_rules: Sequence[Rule],
    existing_game: Sequence[Rule],
    existing_cn: Sequence[Rule],
    allow_large_change: bool,
) -> None:
    game_ids = {rule.identity for rule in game_rules}
    cn_ids = {rule.identity for rule in cn_rules}
    if len(game_rules) < MINIMUM_GAME_RULES:
        raise BuildError("Game.list would contain too few rules")
    if len(cn_rules) < MINIMUM_GAME_CN_RULES:
        raise BuildError("GameCN.list would contain too few rules")
    missing_game = sorted(GAME_CORE.difference(game_ids))
    missing_cn = sorted(GAME_CN_CORE.difference(cn_ids))
    if missing_game:
        raise BuildError("Game core rules disappeared: {}".format(missing_game))
    if missing_cn:
        raise BuildError("GameCN core rules disappeared: {}".format(missing_cn))
    if game_ids.intersection(cn_ids):
        raise BuildError("Game and GameCN contain exact duplicate rules")
    if allow_large_change:
        return
    for name, rules, existing in (
        ("Game.list", game_rules, existing_game),
        ("GameCN.list", cn_rules, existing_cn),
    ):
        if not existing:
            continue
        new = {rule.identity for rule in rules}
        old = {rule.identity for rule in existing}
        ratio = len(new.symmetric_difference(old)) / max(len(old), 1)
        if ratio > MAX_BUILD_CHANGE_RATIO:
            raise BuildError(
                "{} churn is {:.1%}, above {:.1%}".format(
                    name, ratio, MAX_BUILD_CHANGE_RATIO
                )
            )


def load_previous_unsupported(path: Path) -> Optional[Set[str]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload["unsupported_omitted"]
        identities = {
            "{}|{}|{}".format(
                item["platform"], item["identity"], item["reason"]
            )
            for item in values
        }
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BuildError("Could not read existing Game report: {}".format(path)) from exc
    return identities


def assess_change(
    game_rules: Sequence[Rule],
    cn_rules: Sequence[Rule],
    existing_game: Sequence[Rule],
    existing_cn: Sequence[Rule],
    unsupported: Sequence[Mapping[str, str]],
    previous_unsupported: Optional[Set[str]],
) -> Dict[str, object]:
    new = {
        ("Game",) + rule.identity for rule in game_rules
    }.union({("GameCN",) + rule.identity for rule in cn_rules})
    old = {
        ("Game",) + rule.identity for rule in existing_game
    }.union({("GameCN",) + rule.identity for rule in existing_cn})
    added = new.difference(old)
    removed = old.difference(new)
    ratio = (len(added) + len(removed)) / max(len(old), 1)
    unsupported_set = {
        "{}|{}|{}".format(
            item["platform"], item["identity"], item["reason"]
        )
        for item in unsupported
    }
    unsupported_changed = (
        previous_unsupported is None
        or unsupported_set != previous_unsupported
    )

    reasons: List[str] = []
    if not old:
        reasons.append("initial-baseline-requires-review")
    if removed:
        reasons.append("rules-removed")
    if len(added) > MAX_AUTO_ADDITIONS:
        reasons.append("addition-count-above-auto-merge-limit")
    if ratio > MAX_AUTO_CHANGE_RATIO:
        reasons.append("actual-change-ratio-above-auto-merge-limit")
    if unsupported_changed:
        reasons.append("unsupported-rule-set-changed")
    eligible = not reasons

    def render(identity: Tuple[str, str, str]) -> str:
        output, kind, value = identity
        return "{}:{}".format(output, rule_to_surge(Rule(kind, value)))

    return {
        "schema_version": 1,
        "classification": "low-risk" if eligible else "review-required",
        "auto_merge_eligible": eligible,
        "baseline_rules": len(old),
        "generated_rules": len(new),
        "added_count": len(added),
        "removed_count": len(removed),
        "actual_change_ratio": round(ratio, 6),
        "unsupported_changed": unsupported_changed,
        "reasons": reasons,
        "added": [render(identity) for identity in sorted(added)],
        "removed": [render(identity) for identity in sorted(removed)],
        "thresholds": {
            "max_auto_additions": MAX_AUTO_ADDITIONS,
            "max_auto_change_ratio": MAX_AUTO_CHANGE_RATIO,
            "max_build_change_ratio": MAX_BUILD_CHANGE_RATIO,
            "automatic_deletions_allowed": False,
        },
    }


def render_review_markdown(
    metadata: SourceMetadata,
    report: Mapping[str, object],
    assessment: Mapping[str, object],
) -> str:
    unsupported = report["unsupported_omitted"]
    lines = [
        "# Game / GameCN 自动审查报告",
        "",
        "- v2fly 提交：`{}`".format(metadata.commit),
        "- 自动结论：`{}`".format(assessment["classification"]),
        "- `Game.list`：{} 条".format(report["counts"]["game_output"]),
        "- `GameCN.list`：{} 条".format(report["counts"]["gamecn_output"]),
        "- 无法安全转换：{} 条".format(len(unsupported)),
        "",
        "## 规则目标",
        "",
        "- `Game.list` 仅承载 Epic、PlayStation、Steam、Nintendo 的非中国大陆条目，交给 `🎲 Gamer`。",
        "- `GameCN.list` 仅承载带 `@cn` 的大陆入口；平台根域名保留后缀匹配，CDN/下载主机收紧为精确 `DOMAIN`。",
        "- `GameCN.list` 必须放在 `Game.list` 之前；海外游戏下载继续跟随 `🎲 Gamer`。",
        "",
        "## 无法安全转换的上游规则",
        "",
    ]
    if unsupported:
        lines.extend(["| 平台 | 上游条目 | 原因 |", "|---|---|---|"])
        for item in unsupported:
            lines.append(
                "| `{}` | `{}` | `{}` |".format(
                    item["platform"], item["identity"], item["reason"]
                )
            )
    else:
        lines.append("无。")
    return "\n".join(lines)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true")
    source.add_argument("--source-dir", type=Path)
    parser.add_argument("--v2fly-ref", default="master")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--allow-large-change", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = create_parser().parse_args(argv)
    project_root = args.project_root.resolve()
    if args.fetch:
        metadata = resolve_github_ref(ref=args.v2fly_ref)
        tree = download_v2fly_tree(metadata)
    else:
        metadata = SourceMetadata(
            "local-fixture", str(args.source_dir), "local", None
        )
        tree = load_local_tree(args.source_dir)

    game_patch = project_root / "patches/game"
    cn_patch = project_root / "patches/gamecn"
    game_include = parse_patch(game_patch / "include.txt")
    game_exclude = parse_patch(game_patch / "exclude.txt")
    cn_include = parse_patch(cn_patch / "include.txt")
    cn_exclude = parse_patch(cn_patch / "exclude.txt")
    game_rules, cn_rules, report_details = build_rules(
        tree,
        game_include,
        game_exclude,
        cn_include,
        cn_exclude,
    )

    game_path = project_root / "rules/Game/Game.list"
    cn_path = project_root / "rules/GameCN/GameCN.list"
    report_path = project_root / "reports/game/game-report.json"
    existing_game = load_existing_rules(game_path)
    existing_cn = load_existing_rules(cn_path)
    previous_unsupported = load_previous_unsupported(report_path)
    validate_outputs(
        game_rules,
        cn_rules,
        existing_game,
        existing_cn,
        args.allow_large_change,
    )
    assessment = assess_change(
        game_rules,
        cn_rules,
        existing_game,
        existing_cn,
        report_details["unsupported_omitted"],
        previous_unsupported,
    )
    verification = summarize_generation_decisions(
        {
            "published-game": len(game_rules),
            "published-gamecn": len(cn_rules),
            "resolved-by-rule-order": report_details["counts"][
                "gamecn_covered_by_game_parent_count"
            ],
        },
        report_details["unsupported_omitted"],
    )
    report = {
        "schema_version": 1,
        "source": {
            "repository": metadata.repository,
            "requested_ref": metadata.requested_ref,
            "commit": metadata.commit,
            "committed_at": metadata.committed_at,
        },
        "policy": {
            "v2fly_is_only_generation_source": True,
            "platforms_are_internal_modules": list(PLATFORMS),
            "final_outputs_follow_policy_targets": ["Game", "GameCN"],
            "gamecn_uses_exact_hosts_for_non_root_entries": True,
            "game_download_output_created": False,
            "gamecn_must_precede_game": True,
        },
        "verification": verification,
        **report_details,
    }
    files = {
        Path("rules/Game/Game.list"): render_rules(
            "Game", metadata, game_rules
        ),
        Path("rules/GameCN/GameCN.list"): render_rules(
            "GameCN", metadata, cn_rules
        ),
        Path("reports/game/game-report.json"): json.dumps(
            report, ensure_ascii=False, indent=2, sort_keys=True
        ),
        Path("reports/game/change-assessment.json"): json.dumps(
            assessment, ensure_ascii=False, indent=2, sort_keys=True
        ),
        Path("reports/game/review.md"): render_review_markdown(
            metadata, report, assessment
        ),
    }
    write_staged_outputs(project_root, files)
    print(
        "Built Game.list={}, GameCN.list={}, source={}".format(
            len(game_rules), len(cn_rules), metadata.commit
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        raise SystemExit(2)
