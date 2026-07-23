#!/usr/bin/env python3
"""Build disjoint GoogleAI and non-Google overseas AI Surge rulesets."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from shared.v2fly import (  # noqa: E402
    BuildError,
    Rule,
    SourceMetadata,
    covered_by_any,
    dedupe_rules,
    domain_rule_covers,
    download_v2fly_tree,
    expand_list,
    http_get,
    load_existing_rules,
    load_local_tree,
    normalize_domain,
    parse_patch,
    render_rules,
    resolve_github_ref,
    rule_to_surge,
    write_staged_outputs,
)


GOOGLE_AI_SOURCE = "google-deepmind"
OVERSEAS_AI_SOURCE = "category-ai-!cn"
DOMESTIC_AI_SOURCE = "category-ai-cn"
SUKKA_REPOSITORY = "SukkaW/Surge"
SUKKA_DEFAULT_REF = "master"
SUKKA_AI_PATH = "Source/non_ip/ai.conf"

GOOGLE_AI_CORE = frozenset(
    {
        ("domain", "gemini.google.com"),
        ("domain", "generativelanguage.googleapis.com"),
        ("domain", "deepmind.com"),
    }
)
AI_CORE = frozenset(
    {
        ("domain", "openai.com"),
        ("domain", "chatgpt.com"),
        ("domain", "anthropic.com"),
        ("domain", "claude.ai"),
        ("domain", "perplexity.ai"),
    }
)
MINIMUM_GOOGLE_AI_RULES = 30
MINIMUM_AI_RULES = 100
MAX_BUILD_CHANGE_RATIO = 0.30
MAX_AUTO_ADDITIONS = 10
MAX_AUTO_CHANGE_RATIO = 0.05


def rules_overlap(left: Rule, right: Rule) -> bool:
    return domain_rule_covers(left, right) or domain_rule_covers(right, left)


def overlaps_any(rule: Rule, candidates: Iterable[Rule]) -> bool:
    return any(rules_overlap(rule, candidate) for candidate in candidates)


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


def render_unsupported(source: str, rule: Rule, reason: str) -> Dict[str, str]:
    return {
        "source": source,
        "identity": "{}:{}".format(rule.kind, rule.value),
        "reason": reason,
    }


def build_rules(
    tree,
    google_ai_include: Sequence[Rule],
    google_ai_exclude: Sequence[Rule],
    ai_include: Sequence[Rule],
    ai_exclude: Sequence[Rule],
) -> Tuple[List[Rule], List[Rule], List[Rule], List[Rule], Dict[str, object]]:
    google_universe = dedupe_rules(expand_list(tree, "google"))
    google_candidates = dedupe_rules(expand_list(tree, GOOGLE_AI_SOURCE))
    overseas_candidates = dedupe_rules(expand_list(tree, OVERSEAS_AI_SOURCE))
    domestic_candidates = dedupe_rules(expand_list(tree, DOMESTIC_AI_SOURCE))

    google_ai: List[Rule] = []
    ai: List[Rule] = []
    unsupported: List[Dict[str, str]] = []
    counts: Counter = Counter()

    for rule in google_candidates:
        counts["google_ai_expanded"] += 1
        if rule.kind not in {"domain", "full"}:
            counts["google_ai_unsupported"] += 1
            unsupported.append(
                render_unsupported(
                    GOOGLE_AI_SOURCE,
                    rule,
                    "unsupported-google-ai-{}".format(rule.kind),
                )
            )
            continue
        google_ai.append(rule)

    google_ai = apply_patches(
        google_ai,
        google_ai_include,
        google_ai_exclude,
    )

    for rule in overseas_candidates:
        counts["overseas_ai_expanded"] += 1
        if overlaps_any(rule, google_candidates):
            counts["google_partition_excluded"] += 1
            continue
        if rule.kind not in {"domain", "full"}:
            counts["overseas_ai_unsupported"] += 1
            unsupported.append(
                render_unsupported(
                    OVERSEAS_AI_SOURCE,
                    rule,
                    "unsupported-overseas-ai-{}".format(rule.kind),
                )
            )
            continue
        if overlaps_any(rule, domestic_candidates):
            counts["domestic_partition_excluded"] += 1
            continue
        if overlaps_any(rule, google_universe):
            counts["unclassified_google_ownership_quarantined"] += 1
            unsupported.append(
                render_unsupported(
                    OVERSEAS_AI_SOURCE,
                    rule,
                    "google-owned-but-not-in-google-deepmind",
                )
            )
            continue
        ai.append(rule)

    ai = apply_patches(ai, ai_include, ai_exclude)
    report = {
        "counts": {
            **dict(sorted(counts.items())),
            "google_ai_output": len(google_ai),
            "ai_output": len(ai),
            "domestic_ai_reference": len(domestic_candidates),
            "google_ai_include_patch": len(google_ai_include),
            "google_ai_exclude_patch": len(google_ai_exclude),
            "ai_include_patch": len(ai_include),
            "ai_exclude_patch": len(ai_exclude),
        },
        "unsupported_omitted": sorted(
            unsupported,
            key=lambda item: (item["source"], item["identity"], item["reason"]),
        ),
    }
    return google_ai, ai, google_universe, domestic_candidates, report


def validate_outputs(
    google_ai: Sequence[Rule],
    ai: Sequence[Rule],
    google_universe: Sequence[Rule],
    domestic_candidates: Sequence[Rule],
    existing_google_ai: Sequence[Rule],
    existing_ai: Sequence[Rule],
    allow_large_change: bool,
) -> None:
    google_ids = {rule.identity for rule in google_ai}
    ai_ids = {rule.identity for rule in ai}
    if len(google_ai) < MINIMUM_GOOGLE_AI_RULES:
        raise BuildError("GoogleAI.list would contain too few rules")
    if len(ai) < MINIMUM_AI_RULES:
        raise BuildError("AI.list would contain too few rules")

    missing_google = sorted(GOOGLE_AI_CORE.difference(google_ids))
    missing_ai = sorted(AI_CORE.difference(ai_ids))
    if missing_google:
        raise BuildError("Google AI core rules disappeared: {}".format(missing_google))
    if missing_ai:
        raise BuildError("AI core rules disappeared: {}".format(missing_ai))

    cross = sorted(
        "{} <> {}".format(rule_to_surge(left), rule_to_surge(right))
        for left in google_ai
        for right in ai
        if rules_overlap(left, right)
    )
    if cross:
        raise BuildError("GoogleAI and AI overlap: {}".format(cross[:10]))

    google_leaks = sorted(
        rule_to_surge(rule)
        for rule in ai
        if overlaps_any(rule, google_universe)
    )
    if google_leaks:
        raise BuildError("AI contains Google-owned coverage: {}".format(google_leaks))

    domestic_leaks = sorted(
        rule_to_surge(rule)
        for rule in ai
        if overlaps_any(rule, domestic_candidates)
    )
    if domestic_leaks:
        raise BuildError("AI contains domestic AI coverage: {}".format(domestic_leaks))

    if allow_large_change:
        return
    for name, rules, existing in (
        ("GoogleAI.list", google_ai, existing_google_ai),
        ("AI.list", ai, existing_ai),
    ):
        if not existing:
            continue
        new = {rule.identity for rule in rules}
        old = {rule.identity for rule in existing}
        ratio = len(new.symmetric_difference(old)) / max(len(old), 1)
        if ratio > MAX_BUILD_CHANGE_RATIO:
            raise BuildError(
                "{} churn is {:.1%}, above {:.1%}".format(
                    name,
                    ratio,
                    MAX_BUILD_CHANGE_RATIO,
                )
            )


def parse_sukka_ai(text: str) -> Tuple[List[Rule], Counter]:
    rules: List[Rule] = []
    unsupported: Counter = Counter()
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",", 1)]
        rule_type = parts[0].upper()
        if len(parts) == 2 and rule_type in {"DOMAIN", "DOMAIN-SUFFIX"}:
            kind = "full" if rule_type == "DOMAIN" else "domain"
            rules.append(Rule(kind, normalize_domain(parts[1])))
        else:
            unsupported[rule_type] += 1
    return dedupe_rules(rules), unsupported


def audit_sukka(
    text: str,
    google_ai: Sequence[Rule],
    ai: Sequence[Rule],
) -> Dict[str, object]:
    if not text:
        return {
            "available": False,
            "domain_rules": 0,
            "covered_by_google_ai": 0,
            "covered_by_ai": 0,
            "uncovered": 0,
            "unsupported_types": {},
        }
    rules, unsupported = parse_sukka_ai(text)
    google_covered = sum(covered_by_any(rule, google_ai) for rule in rules)
    ai_covered = sum(covered_by_any(rule, ai) for rule in rules)
    uncovered = sum(
        not covered_by_any(rule, google_ai) and not covered_by_any(rule, ai)
        for rule in rules
    )
    return {
        "available": True,
        "domain_rules": len(rules),
        "covered_by_google_ai": google_covered,
        "covered_by_ai": ai_covered,
        "uncovered": uncovered,
        "unsupported_types": dict(sorted(unsupported.items())),
    }


def load_previous_unsupported(path: Path) -> Optional[Set[str]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload["unsupported_omitted"]
        return {
            "{}|{}|{}".format(
                item["source"],
                item["identity"],
                item["reason"],
            )
            for item in values
        }
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BuildError("Could not read existing AI report: {}".format(path)) from exc


def load_previous_sukka_audit(path: Path) -> Optional[Dict[str, object]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        audit = payload["sukka_audit"]
        return {
            "available": bool(audit["available"]),
            "domain_rules": int(audit["domain_rules"]),
            "uncovered": int(audit["uncovered"]),
            "unsupported_types": dict(audit["unsupported_types"]),
        }
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BuildError("Could not read existing Sukka audit: {}".format(path)) from exc


def assess_change(
    google_ai: Sequence[Rule],
    ai: Sequence[Rule],
    existing_google_ai: Sequence[Rule],
    existing_ai: Sequence[Rule],
    unsupported: Sequence[Mapping[str, str]],
    previous_unsupported: Optional[Set[str]],
    sukka_audit: Mapping[str, object],
    previous_sukka_audit: Optional[Mapping[str, object]],
) -> Dict[str, object]:
    new = {
        ("GoogleAI",) + rule.identity for rule in google_ai
    }.union({("AI",) + rule.identity for rule in ai})
    old = {
        ("GoogleAI",) + rule.identity for rule in existing_google_ai
    }.union({("AI",) + rule.identity for rule in existing_ai})
    added = new.difference(old)
    removed = old.difference(new)
    ratio = (len(added) + len(removed)) / max(len(old), 1)
    unsupported_set = {
        "{}|{}|{}".format(
            item["source"],
            item["identity"],
            item["reason"],
        )
        for item in unsupported
    }
    unsupported_changed = (
        previous_unsupported is None or unsupported_set != previous_unsupported
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
    if not sukka_audit["available"]:
        reasons.append("sukka-audit-unavailable")
    elif (
        previous_sukka_audit is not None
        and previous_sukka_audit["available"]
        and sukka_audit["uncovered"] > previous_sukka_audit["uncovered"]
    ):
        reasons.append("sukka-uncovered-count-increased")
    if (
        previous_sukka_audit is not None
        and previous_sukka_audit["available"]
        and sukka_audit["unsupported_types"]
        != previous_sukka_audit["unsupported_types"]
    ):
        reasons.append("sukka-unsupported-types-changed")
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
        "sukka_uncovered": sukka_audit["uncovered"],
        "previous_sukka_uncovered": (
            previous_sukka_audit["uncovered"]
            if previous_sukka_audit is not None
            and previous_sukka_audit["available"]
            else None
        ),
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
    v2fly: SourceMetadata,
    sukka: SourceMetadata,
    report: Mapping[str, object],
    assessment: Mapping[str, object],
) -> str:
    counts = report["counts"]
    audit = report["sukka_audit"]
    unsupported = report["unsupported_omitted"]
    lines = [
        "# GoogleAI / AI 自动审查报告",
        "",
        "- v2fly 提交：`{}`".format(v2fly.commit),
        "- Sukka 对照提交：`{}`".format(sukka.commit),
        "- 自动结论：`{}`".format(assessment["classification"]),
        "- `GoogleAI.list`：{} 条".format(counts["google_ai_output"]),
        "- `AI.list`：{} 条".format(counts["ai_output"]),
        "- 国内 AI 参考集合：{} 条（不发布）".format(
            counts["domestic_ai_reference"]
        ),
        "- 无法安全转换或需要隔离：{} 条".format(len(unsupported)),
        "",
        "## 路由目标",
        "",
        "- `GoogleAI.list` 包含 Google DeepMind、Gemini、AI Studio、NotebookLM、Jules 等 Google 自有 AI 域名，固定指向 `🔍 Google`。",
        "- `AI.list` 仅包含非 Google 的海外 AI 服务，指向 `🤖 Intelligence`。",
        "- `category-ai-cn` 仅作为排除与审计边界，不进入海外 AI 表。",
        "- `GoogleAI.list` 必须位于 `AI.list` 之前；两份产物不得有父子域覆盖。",
        "",
        "## Sukka 设计对照",
        "",
        "- Sukka 的 `ai.conf` 是人工维护的混合 AI 表，本项目只用它检查覆盖情况，不直接合并条目。",
        "- 对照域名规则：{} 条；GoogleAI 覆盖：{}；AI 覆盖：{}；未覆盖：{}。".format(
            audit["domain_rules"],
            audit["covered_by_google_ai"],
            audit["covered_by_ai"],
            audit["uncovered"],
        ),
        "- Sukka 非域名类型：`{}`。".format(audit["unsupported_types"]),
        "",
        "## 隔离条目",
        "",
    ]
    if unsupported:
        lines.extend(["| 来源 | 条目 | 原因 |", "|---|---|---|"])
        for item in unsupported:
            lines.append(
                "| `{}` | `{}` | `{}` |".format(
                    item["source"],
                    item["identity"],
                    item["reason"],
                )
            )
    else:
        lines.append("无。")
    return "\n".join(lines)


def fetch_sukka(metadata: SourceMetadata) -> str:
    url = "https://raw.githubusercontent.com/{}/{}/{}".format(
        metadata.repository,
        metadata.commit,
        SUKKA_AI_PATH,
    )
    return http_get(url, accept="text/plain").decode("utf-8")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true")
    source.add_argument("--source-dir", type=Path)
    parser.add_argument("--v2fly-ref", default="master")
    parser.add_argument("--sukka-ref", default="master")
    parser.add_argument("--sukka-file", type=Path)
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
        v2fly = resolve_github_ref(ref=args.v2fly_ref)
        tree = download_v2fly_tree(v2fly)
    else:
        v2fly = SourceMetadata("local-fixture", str(args.source_dir), "local", None)
        tree = load_local_tree(args.source_dir)

    if args.sukka_file:
        sukka = SourceMetadata(
            "local-fixture",
            str(args.sukka_file),
            "local",
            None,
        )
        sukka_text = args.sukka_file.read_text(encoding="utf-8")
    elif args.fetch:
        sukka = resolve_github_ref(SUKKA_REPOSITORY, args.sukka_ref)
        sukka_text = fetch_sukka(sukka)
    else:
        sukka = SourceMetadata("not-provided", "none", "none", None)
        sukka_text = ""

    google_patch = project_root / "patches/googleai"
    ai_patch = project_root / "patches/ai"
    google_include = parse_patch(google_patch / "include.txt")
    google_exclude = parse_patch(google_patch / "exclude.txt")
    ai_include = parse_patch(ai_patch / "include.txt")
    ai_exclude = parse_patch(ai_patch / "exclude.txt")
    google_ai, ai, google_universe, domestic, report_details = build_rules(
        tree,
        google_include,
        google_exclude,
        ai_include,
        ai_exclude,
    )

    google_path = project_root / "rules/GoogleAI/GoogleAI.list"
    ai_path = project_root / "rules/AI/AI.list"
    report_path = project_root / "reports/ai/ai-report.json"
    existing_google = load_existing_rules(google_path)
    existing_ai = load_existing_rules(ai_path)
    previous_unsupported = load_previous_unsupported(report_path)
    previous_sukka_audit = load_previous_sukka_audit(report_path)
    validate_outputs(
        google_ai,
        ai,
        google_universe,
        domestic,
        existing_google,
        existing_ai,
        args.allow_large_change,
    )
    sukka_audit = audit_sukka(sukka_text, google_ai, ai)
    assessment = assess_change(
        google_ai,
        ai,
        existing_google,
        existing_ai,
        report_details["unsupported_omitted"],
        previous_unsupported,
        sukka_audit,
        previous_sukka_audit,
    )
    report = {
        "schema_version": 1,
        "source": {
            "v2fly": {
                "repository": v2fly.repository,
                "requested_ref": v2fly.requested_ref,
                "commit": v2fly.commit,
                "committed_at": v2fly.committed_at,
            },
            "sukka_audit": {
                "repository": sukka.repository,
                "requested_ref": sukka.requested_ref,
                "commit": sukka.commit,
                "committed_at": sukka.committed_at,
            },
        },
        "policy": {
            "v2fly_is_only_generation_source": True,
            "sukka_is_audit_only": True,
            "google_ai_target": "Google",
            "non_google_overseas_ai_target": "Intelligence",
            "domestic_ai_is_not_published": True,
            "google_ai_must_precede_ai": True,
            "unsupported_rules_are_report_only": True,
        },
        **report_details,
        "sukka_audit": sukka_audit,
    }
    files = {
        Path("rules/GoogleAI/GoogleAI.list"): render_rules(
            "GoogleAI",
            v2fly,
            google_ai,
        ),
        Path("rules/AI/AI.list"): render_rules("AI", v2fly, ai),
        Path("reports/ai/ai-report.json"): json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        Path("reports/ai/change-assessment.json"): json.dumps(
            assessment,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        Path("reports/ai/review.md"): render_review_markdown(
            v2fly,
            sukka,
            report,
            assessment,
        ),
    }
    write_staged_outputs(project_root, files)
    print(
        "Built GoogleAI.list={}, AI.list={}, source={}".format(
            len(google_ai),
            len(ai),
            v2fly.commit,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        raise SystemExit(2)
