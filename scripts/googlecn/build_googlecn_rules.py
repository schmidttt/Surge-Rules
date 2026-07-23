#!/usr/bin/env python3
"""Build a conservative, automatically reviewed GoogleCN ruleset."""

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
    SourceTree,
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


AUTO_APPROVED_SUFFIXES = frozenset(
    {
        "265.com",
        "g.cn",
        "ggpht.cn",
        "gkecnapps.cn",
        "google.cn",
        "googlecnapps.cn",
        "gstatic.cn",
        "gstaticcnapps.cn",
        "gvt1-cn.com",
        "gvt2-cn.com",
    }
)

# These are exact hosts only. A new global Google host is never approved by
# keyword similarity; it must be added here or to patches/googlecn/allow.txt.
AUTO_APPROVED_EXACT = frozenset(
    {
        "c.pki.goog",
        "cache.pack.google.com",
        "connectivitycheck.gstatic.com",
        "crl.pki.goog",
        "crls.pki.goog",
        "dl.google.com",
        "dl.l.google.com",
        "download.mlcc.google.com",
        "download.tensorflow.google.com",
        "fontfiles.googleapis.com",
        "fonts.googleapis.com",
        "fonts.gstatic.com",
        "g0.gstatic.com",
        "g1.gstatic.com",
        "g2.gstatic.com",
        "g3.gstatic.com",
        "googleapis-cn.com",
        "googleapps-cn.com",
        "gstatic-cn.com",
        "i.pki.goog",
        "o.pki.goog",
        "ocsp.pki.goog",
        "pki-goog.l.google.com",
        "ssl.gstatic.com",
        "tools.google.com",
        "www.gstatic.com",
    }
)

HARD_EXCLUDE_MARKERS: Mapping[str, Tuple[str, ...]] = {
    "advertising-or-measurement": (
        "admob",
        "ads",
        "analytics",
        "beacon",
        "2mdn",
        "clickserve",
        "clickserver",
        "destinationurl",
        "dartsearch",
        "doubleclick",
        "gonglchuang",
        "gongyichuangyi",
        "gtm.",
        "imasdk",
        "measurement",
        "oingo",
        "optimize",
        "pagead",
        "pxcc",
        "qiao-cn",
        "syndication",
        "tagmanager",
        "tagservices",
        "traveladservices",
        "urchin",
        "vads",
    ),
    "crash-or-telemetry": (
        "crashlytics",
        "csi.",
        "performanceparameters",
    ),
    "account-or-shared-application": (
        "googleflights",
        "floonet",
        "redirector.c.chat",
        "redirector.c.mail",
        "redirector.c.play",
        "redirector.c.youtube",
    ),
}

PRODUCT_LISTS: Mapping[str, str] = {
    "google-ai": "google-deepmind",
    "youtube": "youtube",
    "google-messaging": "googlefcm",
}

CORE_RULES = frozenset(
    {
        ("domain", "google.cn"),
        ("domain", "g.cn"),
        ("full", "dl.google.com"),
        ("full", "ocsp.pki.goog"),
    }
)
MINIMUM_RULES = 20
MAX_BUILD_CHANGE_RATIO = 0.50
MAX_AUTO_ADDITIONS = 5
MAX_AUTO_CHANGE_RATIO = 0.10


def identity_text(rule: Rule) -> str:
    return "{}:{}".format(rule.kind, rule.value)


def load_previous_review(path: Path) -> Optional[Set[str]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload["decisions"]["review"]
        identities = {
            item["identity"] if isinstance(item, dict) else item for item in values
        }
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BuildError("Could not read existing GoogleCN review: {}".format(path)) from exc
    if not all(isinstance(value, str) for value in identities):
        raise BuildError("Invalid existing GoogleCN review: {}".format(path))
    return identities


def hard_exclusion_reason(
    rule: Rule,
    product_rules: Mapping[str, Sequence[Rule]],
) -> Optional[str]:
    if "ads" in rule.attrs:
        return "v2fly-ads-attribute"
    if "!cn" in rule.attrs:
        return "v2fly-not-cn-attribute"
    if rule.kind not in {"domain", "full"}:
        return "unsupported-{}".format(rule.kind)
    for product, rules in product_rules.items():
        if covered_by_any(rule, rules):
            return "conflicts-with-{}".format(product)
    for reason, markers in HARD_EXCLUDE_MARKERS.items():
        if any(marker in rule.value for marker in markers):
            return reason
    return None


def classify_candidates(
    tree: SourceTree,
    allow_patch: Sequence[Rule],
    deny_patch: Sequence[Rule],
) -> Tuple[List[Rule], Dict[str, List[Dict[str, str]]], Dict[str, int]]:
    candidates = dedupe_rules(
        rule for rule in expand_list(tree, "google") if "cn" in rule.attrs
    )
    product_rules = {
        product: dedupe_rules(expand_list(tree, source))
        for product, source in PRODUCT_LISTS.items()
    }
    candidate_ids = {rule.identity for rule in candidates}
    allow_ids = {rule.identity for rule in allow_patch}
    deny_ids = {rule.identity for rule in deny_patch}
    stale_allow = sorted(allow_ids.difference(candidate_ids))
    stale_deny = sorted(deny_ids.difference(candidate_ids))
    if stale_allow:
        raise BuildError(
            "GoogleCN allow entries are no longer v2fly @cn candidates: {}".format(
                stale_allow
            )
        )

    decisions: Dict[str, List[Dict[str, str]]] = {
        "approved": [],
        "excluded": [],
        "review": [],
    }
    approved: List[Rule] = []
    exclusion_counts: Counter = Counter()
    blocked_allow: List[str] = []

    for rule in candidates:
        reason = hard_exclusion_reason(rule, product_rules)
        if reason:
            if rule.identity in allow_ids:
                blocked_allow.append(identity_text(rule))
            decisions["excluded"].append(
                {
                    "identity": identity_text(rule),
                    "rule": (
                        rule_to_surge(rule)
                        if rule.kind in {"domain", "full"}
                        else identity_text(rule)
                    ),
                    "reason": reason,
                }
            )
            exclusion_counts[reason] += 1
            continue
        if rule.identity in deny_ids:
            decisions["excluded"].append(
                {
                    "identity": identity_text(rule),
                    "rule": rule_to_surge(rule),
                    "reason": "manual-deny-policy",
                }
            )
            exclusion_counts["manual-deny-policy"] += 1
            continue

        approval_reason: Optional[str] = None
        if rule.identity in allow_ids:
            approval_reason = "manual-reviewed-allow"
        elif rule.kind == "domain" and rule.value in AUTO_APPROVED_SUFFIXES:
            approval_reason = "china-specific-approved-suffix"
        elif rule.kind == "full" and rule.value in AUTO_APPROVED_EXACT:
            approval_reason = "approved-exact-technical-host"

        if approval_reason:
            approved.append(rule)
            decisions["approved"].append(
                {
                    "identity": identity_text(rule),
                    "rule": rule_to_surge(rule),
                    "reason": approval_reason,
                }
            )
        else:
            decisions["review"].append(
                {
                    "identity": identity_text(rule),
                    "rule": rule_to_surge(rule),
                    "reason": "new-or-ambiguous-cn-host",
                }
            )

    if blocked_allow:
        raise BuildError(
            "GoogleCN allow entries violate hard safety policy: {}".format(
                blocked_allow
            )
        )

    counts = {
        "v2fly_cn_candidates": len(candidates),
        "approved": len(decisions["approved"]),
        "excluded": len(decisions["excluded"]),
        "review": len(decisions["review"]),
        "allow_patch": len(allow_patch),
        "deny_patch": len(deny_patch),
        "stale_deny_patch": len(stale_deny),
    }
    counts.update(
        {
            "excluded_{}".format(key.replace("-", "_")): value
            for key, value in sorted(exclusion_counts.items())
        }
    )
    return dedupe_rules(approved), decisions, counts


def validate_output(
    rules: Sequence[Rule],
    existing_rules: Sequence[Rule],
    allow_large_change: bool,
) -> None:
    identities = {rule.identity for rule in rules}
    if len(rules) < MINIMUM_RULES:
        raise BuildError(
            "GoogleCN.list would contain only {} rules".format(len(rules))
        )
    missing_core = sorted(CORE_RULES.difference(identities))
    if missing_core:
        raise BuildError("GoogleCN core rules disappeared: {}".format(missing_core))
    if allow_large_change or not existing_rules:
        return
    old = {rule.identity for rule in existing_rules}
    ratio = len(identities.symmetric_difference(old)) / max(len(old), 1)
    if ratio > MAX_BUILD_CHANGE_RATIO:
        raise BuildError(
            "GoogleCN.list churn is {:.1%}, above {:.1%}".format(
                ratio, MAX_BUILD_CHANGE_RATIO
            )
        )


def assess_change(
    rules: Sequence[Rule],
    existing_rules: Sequence[Rule],
    decisions: Mapping[str, Sequence[Mapping[str, str]]],
    previous_review: Optional[Set[str]],
) -> Dict[str, object]:
    new = {rule.identity for rule in rules}
    old = {rule.identity for rule in existing_rules}
    added = new.difference(old)
    removed = old.difference(new)
    ratio = (len(added) + len(removed)) / max(len(old), 1)
    current_review = {item["identity"] for item in decisions["review"]}
    newly_unresolved = (
        current_review
        if previous_review is None
        else current_review.difference(previous_review)
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
    if newly_unresolved:
        reasons.append("new-unresolved-candidates")

    eligible = not reasons
    return {
        "schema_version": 1,
        "classification": "low-risk" if eligible else "review-required",
        "auto_merge_eligible": eligible,
        "baseline_rules": len(old),
        "generated_rules": len(new),
        "added_count": len(added),
        "removed_count": len(removed),
        "actual_change_ratio": round(ratio, 6),
        "unresolved_candidate_count": len(current_review),
        "new_unresolved_candidate_count": len(newly_unresolved),
        "reasons": reasons,
        "added": [
            rule_to_surge(Rule(kind, value)) for kind, value in sorted(added)
        ],
        "removed": [
            rule_to_surge(Rule(kind, value)) for kind, value in sorted(removed)
        ],
        "new_unresolved_candidates": sorted(newly_unresolved),
        "thresholds": {
            "max_auto_additions": MAX_AUTO_ADDITIONS,
            "max_auto_change_ratio": MAX_AUTO_CHANGE_RATIO,
            "max_build_change_ratio": MAX_BUILD_CHANGE_RATIO,
            "automatic_deletions_allowed": False,
        },
    }


def render_review_markdown(
    metadata: SourceMetadata,
    counts: Mapping[str, int],
    decisions: Mapping[str, Sequence[Mapping[str, str]]],
    assessment: Mapping[str, object],
) -> str:
    lines = [
        "# GoogleCN 自动审查报告",
        "",
        "- v2fly 提交：`{}`".format(metadata.commit),
        "- 自动结论：`{}`".format(assessment["classification"]),
        "- 已批准：{} 条".format(counts["approved"]),
        "- 自动排除：{} 条".format(counts["excluded"]),
        "- 保留待确认：{} 条".format(counts["review"]),
        "- 本次新增待确认：{} 条".format(
            assessment["new_unresolved_candidate_count"]
        ),
        "",
        "待确认条目不会进入 `GoogleCN.list`，也不会影响已发布规则。",
        "",
        "## 本次新增待确认",
        "",
    ]
    new_review = set(assessment["new_unresolved_candidates"])
    review_rows = [
        item for item in decisions["review"] if item["identity"] in new_review
    ]
    if review_rows:
        lines.extend(["| 规则 | 原因 |", "|---|---|"])
        for item in review_rows:
            lines.append("| `{}` | `{}` |".format(item["rule"], item["reason"]))
    else:
        lines.append("无。")
    lines.extend(
        [
            "",
            "## 审查原则",
            "",
            "- 只处理 v2fly `google` 集合中明确带 `@cn` 的条目。",
            "- 广告、统计、FCM/消息、AI、YouTube 和高风险共享服务自动排除。",
            "- 中国专属根域名可以使用 `DOMAIN-SUFFIX`；全球共享主机只允许精确 `DOMAIN`。",
            "- GitHub Actions 的海外网络探测不作为“中国大陆可直连”的证据。",
            "- 新的模糊条目默认隔离，不会自动发布。",
        ]
    )
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

    patch_root = project_root / "patches/googlecn"
    allow_patch = parse_patch(patch_root / "allow.txt")
    deny_patch = parse_patch(patch_root / "deny.txt")
    rules, decisions, counts = classify_candidates(
        tree, allow_patch, deny_patch
    )

    output_path = project_root / "rules/GoogleCN/GoogleCN.list"
    report_path = project_root / "reports/googlecn/googlecn-report.json"
    existing_rules = load_existing_rules(output_path)
    previous_review = load_previous_review(report_path)
    validate_output(rules, existing_rules, args.allow_large_change)
    assessment = assess_change(
        rules, existing_rules, decisions, previous_review
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
            "v2fly_cn_is_only_candidate_source": True,
            "output_uses_conservative_exact_hosts": True,
            "ambiguous_candidates_published": False,
            "ads_ai_youtube_and_messaging_excluded": True,
            "network_probe_is_authoritative": False,
        },
        "counts": counts,
        "decisions": decisions,
    }
    files = {
        Path("rules/GoogleCN/GoogleCN.list"): render_rules(
            "GoogleCN", metadata, rules
        ),
        Path("reports/googlecn/googlecn-report.json"): json.dumps(
            report, ensure_ascii=False, indent=2, sort_keys=True
        ),
        Path("reports/googlecn/change-assessment.json"): json.dumps(
            assessment, ensure_ascii=False, indent=2, sort_keys=True
        ),
        Path("reports/googlecn/review.md"): render_review_markdown(
            metadata, counts, decisions, assessment
        ),
    }
    write_staged_outputs(project_root, files)
    print(
        "Built GoogleCN.list={} approved, {} review, source={}".format(
            len(rules), counts["review"], metadata.commit
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        raise SystemExit(2)
