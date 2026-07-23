#!/usr/bin/env python3
"""Build reviewable Surge YouTube, TikTok, or BiliBili rules from v2fly data.

v2fly/domain-list-community is the only formal upstream. BlackMatrix7 and
SukkaW are fetched only for aggregate comparison and are never merged into the
generated rules.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import DefaultDict, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from shared.reference_verifier import (  # noqa: E402
    ReferenceVerificationError,
    load_resolution_catalog,
    summarize_generation_decisions,
    verify_reference_sources,
)


V2FLY_REPOSITORY = "v2fly/domain-list-community"
V2FLY_DEFAULT_REF = "master"
BLACKMATRIX_REPOSITORY = "blackmatrix7/ios_rule_script"
BLACKMATRIX_DEFAULT_REF = "master"
SUKKA_REPOSITORY = "SukkaW/Surge"
SUKKA_DEFAULT_REF = "master"
SUKKA_STREAM_PATH = "Source/stream.ts"
USER_AGENT = "schmidttt-surge-rules/0.2 (+media-rules-build)"
OUTPUT_TYPES = {"domain", "full"}
TYPE_ORDER = {"full": 0, "domain": 1}
DISPLAY_TIMEZONE = timezone(timedelta(hours=8))


class BuildError(RuntimeError):
    """Raised when source data or generated output is unsafe to publish."""


@dataclass(frozen=True)
class ProductConfig:
    key: str
    display_name: str
    v2fly_list: str
    blackmatrix_path: str
    sukka_constant: str
    core_suffixes: FrozenSet[str]
    minimum_rules: int
    auto_merge_max_additions: int
    auto_merge_max_change_ratio: float
    max_build_change_ratio: float

    @property
    def output_path(self) -> Path:
        return Path("rules") / self.display_name / (self.display_name + ".list")

    @property
    def report_dir(self) -> Path:
        return Path("reports") / self.key

    @property
    def patch_dir(self) -> Path:
        return Path("patches") / self.key


PRODUCTS: Dict[str, ProductConfig] = {
    "youtube": ProductConfig(
        key="youtube",
        display_name="YouTube",
        v2fly_list="youtube",
        blackmatrix_path="rule/Surge/YouTube/YouTube.list",
        sukka_constant="YOUTUBE",
        core_suffixes=frozenset({"youtube.com", "googlevideo.com", "ytimg.com"}),
        minimum_rules=150,
        auto_merge_max_additions=5,
        auto_merge_max_change_ratio=0.03,
        max_build_change_ratio=0.15,
    ),
    "tiktok": ProductConfig(
        key="tiktok",
        display_name="TikTok",
        v2fly_list="tiktok",
        blackmatrix_path="rule/Surge/TikTok/TikTok.list",
        sukka_constant="TIKTOK",
        core_suffixes=frozenset({"tiktok.com", "tiktokv.com", "tiktokcdn.com"}),
        minimum_rules=20,
        auto_merge_max_additions=2,
        auto_merge_max_change_ratio=0.08,
        max_build_change_ratio=0.20,
    ),
    "bilibili": ProductConfig(
        key="bilibili",
        display_name="BiliBili",
        v2fly_list="bilibili",
        blackmatrix_path="rule/Surge/BiliBili/BiliBili.list",
        sukka_constant="BILIBILI_INTL",
        core_suffixes=frozenset(
            {
                "biliapi.com",
                "bilibili.com",
                "bilibili.tv",
                "biliintl.com",
                "bilivideo.com",
            }
        ),
        minimum_rules=45,
        auto_merge_max_additions=3,
        auto_merge_max_change_ratio=0.06,
        max_build_change_ratio=0.20,
    ),
}


@dataclass(frozen=True)
class Rule:
    kind: str
    value: str
    attrs: FrozenSet[str] = frozenset()

    @property
    def identity(self) -> Tuple[str, str]:
        return (self.kind, self.value)


@dataclass(frozen=True)
class Include:
    target: str
    require: FrozenSet[str] = frozenset()
    forbid: FrozenSet[str] = frozenset()


@dataclass
class SourceMetadata:
    repository: str
    requested_ref: str
    commit: str
    committed_at: Optional[str]


@dataclass
class SourceTree:
    rules: DefaultDict[str, List[Rule]]
    includes: DefaultDict[str, List[Include]]

    @classmethod
    def empty(cls) -> "SourceTree":
        return cls(defaultdict(list), defaultdict(list))


def strip_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


def normalize_domain(value: str) -> str:
    value = value.strip().rstrip(".").lower()
    if not value or len(value) > 253 or any(ch.isspace() for ch in value):
        raise BuildError("Invalid domain: {!r}".format(value))
    try:
        ascii_value = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise BuildError("Invalid IDN domain: {!r}".format(value)) from exc
    for label in ascii_value.split("."):
        if not label or len(label) > 63:
            raise BuildError("Invalid domain label in {!r}".format(value))
        if label.startswith("-") or label.endswith("-"):
            raise BuildError("Invalid hyphen placement in {!r}".format(value))
        if not re.fullmatch(r"[a-z0-9_-]+", label):
            raise BuildError("Unsupported domain characters in {!r}".format(value))
    return ascii_value


def parse_rule_token(token: str, attrs: Iterable[str] = ()) -> Rule:
    if ":" in token:
        kind, value = token.split(":", 1)
        kind = kind.lower()
    else:
        kind, value = "domain", token
    if kind in OUTPUT_TYPES:
        value = normalize_domain(value)
    elif kind in {"keyword", "regexp"}:
        value = value.strip()
        if not value:
            raise BuildError("Empty {} rule".format(kind))
    else:
        raise BuildError("Unsupported v2fly rule type: {}".format(kind))
    return Rule(kind, value, frozenset(attrs))


def parse_source_files(files: Mapping[str, Sequence[str]]) -> SourceTree:
    tree = SourceTree.empty()
    for source_name in sorted(files):
        list_name = Path(source_name).name.lower()
        for line_number, raw_line in enumerate(files[source_name], 1):
            line = strip_comment(raw_line)
            if not line:
                continue
            tokens = line.split()
            head, tail = tokens[0], tokens[1:]
            if head.startswith("include:"):
                target = head.split(":", 1)[1].strip().lower()
                require: Set[str] = set()
                forbid: Set[str] = set()
                for token in tail:
                    if token.startswith("@-"):
                        forbid.add(token[2:].lower())
                    elif token.startswith("@"):
                        require.add(token[1:].lower())
                    else:
                        raise BuildError(
                            "Unsupported include modifier {!r} in {}:{}".format(
                                token, source_name, line_number
                            )
                        )
                if not target:
                    raise BuildError("Empty include in {}:{}".format(source_name, line_number))
                tree.includes[list_name].append(
                    Include(target, frozenset(require), frozenset(forbid))
                )
                continue

            attrs = {token[1:].lower() for token in tail if token.startswith("@")}
            affiliations = {token[1:].lower() for token in tail if token.startswith("&")}
            unknown = [token for token in tail if not token.startswith(("@", "&"))]
            if unknown:
                raise BuildError(
                    "Unsupported modifiers {} in {}:{}".format(
                        unknown, source_name, line_number
                    )
                )
            rule = parse_rule_token(head, attrs)
            tree.rules[list_name].append(rule)
            for target in affiliations:
                tree.rules[target].append(rule)
    return tree


def expand_list(tree: SourceTree, name: str) -> List[Rule]:
    memo: Dict[str, List[Rule]] = {}

    def visit(current: str, stack: Tuple[str, ...]) -> List[Rule]:
        if current in memo:
            return memo[current]
        if current in stack:
            raise BuildError("Circular include: {}".format(" -> ".join(stack + (current,))))
        if current not in tree.rules and current not in tree.includes:
            raise BuildError("Missing v2fly list: {}".format(current))
        expanded = list(tree.rules.get(current, []))
        for include in tree.includes.get(current, []):
            for rule in visit(include.target, stack + (current,)):
                if include.require and not include.require.issubset(rule.attrs):
                    continue
                if include.forbid.intersection(rule.attrs):
                    continue
                expanded.append(rule)
        memo[current] = expanded
        return expanded

    return visit(name.lower(), ())


def parse_patch(path: Path) -> List[Rule]:
    if not path.exists():
        return []
    rules: List[Rule] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = strip_comment(raw_line)
        if not line:
            continue
        tokens = line.split()
        attrs = {token[1:].lower() for token in tokens[1:] if token.startswith("@")}
        unknown = [token for token in tokens[1:] if not token.startswith("@")]
        if unknown or tokens[0].startswith("include:"):
            raise BuildError("Invalid patch line in {}:{}".format(path, line_number))
        rule = parse_rule_token(tokens[0], attrs)
        if rule.kind not in OUTPUT_TYPES:
            raise BuildError("Patch supports only domain/full in {}:{}".format(path, line_number))
        rules.append(rule)
    return rules


def dedupe_rules(rules: Iterable[Rule]) -> List[Rule]:
    by_identity: Dict[Tuple[str, str], Rule] = {}
    for rule in rules:
        existing = by_identity.get(rule.identity)
        if existing is None:
            by_identity[rule.identity] = rule
        else:
            by_identity[rule.identity] = Rule(
                rule.kind, rule.value, frozenset(existing.attrs.union(rule.attrs))
            )
    return sorted(
        by_identity.values(),
        key=lambda item: (TYPE_ORDER.get(item.kind, 99), item.value),
    )


def domain_rule_covers(rule: Rule, other: Rule) -> bool:
    if rule.kind == "full":
        return other.kind == "full" and rule.value == other.value
    if rule.kind != "domain" or other.kind not in OUTPUT_TYPES:
        return False
    return other.value == rule.value or other.value.endswith("." + rule.value)


def covered_by_any(rule: Rule, covering_rules: Iterable[Rule]) -> bool:
    return any(domain_rule_covers(candidate, rule) for candidate in covering_rules)


def build_product_rules(
    tree: SourceTree,
    config: ProductConfig,
    include_patch: Sequence[Rule],
    exclude_patch: Sequence[Rule],
) -> Tuple[List[Rule], Dict[str, int], List[str]]:
    source = expand_list(tree, config.v2fly_list)
    exclude_ids = {rule.identity for rule in exclude_patch}
    output: List[Rule] = []
    omitted: List[str] = []
    counts = Counter()
    for rule in source:
        for attr in ("ads", "cn", "!cn"):
            if attr in rule.attrs:
                counts["{}_tagged_in_source".format(attr.replace("!", "not_"))] += 1
        if rule.kind not in OUTPUT_TYPES:
            counts["unsupported_{}_omitted".format(rule.kind)] += 1
            omitted.append("{}:{}".format(rule.kind, rule.value))
            continue
        output.append(rule)
    output.extend(include_patch)
    output = dedupe_rules(rule for rule in output if rule.identity not in exclude_ids)
    counts["source_expanded"] = len(source)
    counts["source_unique"] = len(dedupe_rules(source))
    counts["output"] = len(output)
    counts["include_patch"] = len(include_patch)
    counts["exclude_patch"] = len(exclude_patch)
    return output, dict(sorted(counts.items())), sorted(set(omitted))


def rule_to_surge(rule: Rule) -> str:
    if rule.kind == "full":
        return "DOMAIN,{}".format(rule.value)
    if rule.kind == "domain":
        return "DOMAIN-SUFFIX,{}".format(rule.value)
    raise BuildError("Cannot emit unsupported rule: {}".format(rule.kind))


def format_updated_at(committed_at: Optional[str]) -> str:
    if not committed_at:
        return "UNKNOWN"
    try:
        parsed = datetime.fromisoformat(committed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BuildError("Invalid source commit time: {!r}".format(committed_at)) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(DISPLAY_TIMEZONE).strftime("%Y.%m.%d %H:%M:%S")


def render_rules(config: ProductConfig, source: SourceMetadata, rules: Sequence[Rule]) -> str:
    header = [
        "# NAME: schmidttt's {} Ruleset".format(config.display_name),
        "# AUTHOR: schmidttt",
        "# REPO: https://github.com/schmidttt/surge-rules",
        "# UPDATED: {}".format(format_updated_at(source.committed_at)),
        "# TOTAL: {}".format(len(rules)),
        "#",
        "# ======== 上游同步规则 ========",
    ]
    return "\n".join(header + [rule_to_surge(rule) for rule in rules])


def parse_surge_rules(text: str) -> Tuple[List[Rule], Counter]:
    rules: List[Rule] = []
    unsupported: Counter = Counter()
    for raw_line in text.splitlines():
        line = strip_comment(raw_line)
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        rule_type = parts[0].upper()
        if len(parts) >= 2 and rule_type in {"DOMAIN", "DOMAIN-SUFFIX"}:
            kind = "full" if rule_type == "DOMAIN" else "domain"
            rules.append(Rule(kind, normalize_domain(parts[1])))
        else:
            unsupported[rule_type] += 1
    return dedupe_rules(rules), unsupported


def parse_sukka_service(text: str, constant: str) -> Tuple[List[Rule], Counter]:
    marker = re.search(
        r"(?:export\s+)?const\s+{}\s*:[^=]+=".format(re.escape(constant)), text
    )
    if marker is None:
        raise BuildError("Could not locate Sukka service {}".format(constant))
    block_end = text.find("};", marker.end())
    if block_end < 0:
        raise BuildError("Could not find end of Sukka service {}".format(constant))
    block = text[marker.end():block_end]
    rules_marker = re.search(r"rules\s*:\s*\[", block)
    if rules_marker is None:
        raise BuildError("Could not locate Sukka rules for {}".format(constant))
    rules_text = block[rules_marker.end():]
    cleaned = "\n".join(line.split("//", 1)[0] for line in rules_text.splitlines())
    values = [match[1] for match in re.findall(r"(['\"])(.*?)\1", cleaned)]
    if not values:
        raise BuildError("Sukka service {} has no rules".format(constant))
    surge_text = "\n".join(values)
    return parse_surge_rules(surge_text)


def compare_reference(generated: Sequence[Rule], text: str) -> Dict[str, object]:
    references, unsupported = parse_surge_rules(text)
    generated_ids = {rule.identity for rule in generated}
    reference_ids = {rule.identity for rule in references}
    return {
        "domain_rules": len(reference_ids),
        "exact_common_rules": len(generated_ids.intersection(reference_ids)),
        "only_reference_count": len(reference_ids.difference(generated_ids)),
        "only_generated_count": len(generated_ids.difference(reference_ids)),
        "not_covered_by_generated_count": sum(
            1 for rule in references if not covered_by_any(rule, generated)
        ),
        "non_domain_types": dict(sorted(unsupported.items())),
        "entries_persisted": False,
    }


def compare_sukka(generated: Sequence[Rule], text: str, constant: str) -> Dict[str, object]:
    references, unsupported = parse_sukka_service(text, constant)
    return {
        "domain_rules": len(references),
        "covered_by_generated": sum(
            1 for rule in references if covered_by_any(rule, generated)
        ),
        "not_covered_by_generated_count": sum(
            1 for rule in references if not covered_by_any(rule, generated)
        ),
        "non_domain_types": dict(sorted(unsupported.items())),
        "entries_persisted": False,
    }


def parse_existing_generated(path: Path) -> Set[Tuple[str, str]]:
    if not path.exists():
        return set()
    rules, _ = parse_surge_rules(path.read_text(encoding="utf-8"))
    return {rule.identity for rule in rules}


def load_existing_report(path: Path) -> Optional[Dict[str, object]]:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise BuildError("Could not read existing report: {}".format(path)) from exc
    if not isinstance(value, dict):
        raise BuildError("Existing report is not a JSON object: {}".format(path))
    return value


def get_nested_int(payload: Optional[Dict[str, object]], path: Sequence[str]) -> Optional[int]:
    value: object = payload
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value if isinstance(value, int) else None


def get_nested_strings(payload: Optional[Dict[str, object]], path: Sequence[str]) -> Optional[Set[str]]:
    value: object = payload
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return set(value)


def assess_change(
    config: ProductConfig,
    rules: Sequence[Rule],
    existing: Set[Tuple[str, str]],
    omitted: Sequence[str],
    existing_report: Optional[Dict[str, object]],
    manual_review_count: int,
    existing_audit: Optional[Dict[str, object]],
    manual_review_fingerprint: Optional[str] = None,
) -> Dict[str, object]:
    current = {rule.identity for rule in rules}
    added = current.difference(existing)
    removed = existing.difference(current)
    change_count = len(added) + len(removed)
    ratio = change_count / max(len(existing), 1)
    previous_omitted = get_nested_strings(existing_report, ("unsupported_omitted",))
    omitted_changed = previous_omitted is None or previous_omitted != set(omitted)
    previous_manual_review = get_nested_int(
        existing_audit, ("verification", "manual_review_count")
    )
    if previous_manual_review is None:
        previous_manual_review = get_nested_int(
            existing_audit,
            ("sources", "sukka", "not_covered_by_generated_count"),
        )
    previous_fingerprint: Optional[str] = None
    if existing_audit is not None:
        value = existing_audit.get("verification")
        if isinstance(value, dict) and isinstance(
            value.get("manual_review_fingerprint"), str
        ):
            previous_fingerprint = value["manual_review_fingerprint"]
    manual_review_set_changed = (
        manual_review_fingerprint is not None
        and previous_fingerprint is not None
        and manual_review_fingerprint != previous_fingerprint
    )

    reasons: List[str] = []
    if not existing:
        reasons.append("initial-baseline-requires-review")
    if removed:
        reasons.append("rules-removed")
    if len(added) > config.auto_merge_max_additions:
        reasons.append("addition-count-above-auto-merge-limit")
    if ratio > config.auto_merge_max_change_ratio:
        reasons.append("actual-change-ratio-above-auto-merge-limit")
    if omitted_changed:
        reasons.append("unsupported-rule-set-changed")
    if manual_review_set_changed:
        reasons.append("reference-manual-review-set-changed")
    elif (
        previous_manual_review is not None
        and manual_review_count > previous_manual_review
    ):
        reasons.append("reference-manual-review-increased")

    eligible = not reasons
    return {
        "schema_version": 1,
        "product": config.display_name,
        "classification": "low-risk" if eligible else "review-required",
        "auto_merge_eligible": eligible,
        "baseline_rules": len(existing),
        "generated_rules": len(current),
        "added_count": len(added),
        "removed_count": len(removed),
        "actual_change_count": change_count,
        "actual_change_ratio": round(ratio, 6),
        "unsupported_changed": omitted_changed,
        "reference_manual_review_count": manual_review_count,
        "previous_reference_manual_review_count": previous_manual_review,
        "reference_manual_review_fingerprint": manual_review_fingerprint,
        "previous_reference_manual_review_fingerprint": previous_fingerprint,
        "reference_manual_review_set_changed": manual_review_set_changed,
        "reasons": reasons,
        "added": [rule_to_surge(Rule(*identity)) for identity in sorted(added)],
        "removed": [rule_to_surge(Rule(*identity)) for identity in sorted(removed)],
        "thresholds": {
            "minimum_rules": config.minimum_rules,
            "max_auto_additions": config.auto_merge_max_additions,
            "max_auto_change_ratio": config.auto_merge_max_change_ratio,
            "max_build_change_ratio": config.max_build_change_ratio,
            "automatic_deletions_allowed": False,
        },
    }


def validate_output(
    config: ProductConfig,
    rules: Sequence[Rule],
    existing: Set[Tuple[str, str]],
    allow_large_change: bool,
) -> None:
    if len(rules) < config.minimum_rules:
        raise BuildError(
            "{}.list has {} rules, below minimum {}".format(
                config.display_name, len(rules), config.minimum_rules
            )
        )
    suffixes = {rule.value for rule in rules if rule.kind == "domain"}
    missing = sorted(config.core_suffixes.difference(suffixes))
    if missing:
        raise BuildError("Core {} suffixes disappeared: {}".format(config.display_name, missing))
    if existing and not allow_large_change:
        current = {rule.identity for rule in rules}
        ratio = len(current.symmetric_difference(existing)) / max(len(existing), 1)
        if ratio > config.max_build_change_ratio:
            raise BuildError(
                "{}.list churn is {:.1%}, above {:.1%}; review and rerun with "
                "--allow-large-change if expected".format(
                    config.display_name, ratio, config.max_build_change_ratio
                )
            )


def github_headers() -> Dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer {}".format(token)
    return headers


def http_get(url: str, accept: Optional[str] = None) -> bytes:
    headers = github_headers()
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BuildError("Download failed for {}: {}".format(url, exc)) from exc


def resolve_github_ref(repository: str, ref: str) -> SourceMetadata:
    url = "https://api.github.com/repos/{}/commits/{}".format(
        repository, urllib.parse.quote(ref, safe="")
    )
    try:
        payload = json.loads(http_get(url).decode("utf-8"))
        commit = payload["sha"]
        committed_at = payload.get("commit", {}).get("committer", {}).get("date")
    except (KeyError, ValueError, UnicodeDecodeError) as exc:
        raise BuildError("Invalid GitHub commit response for {}/{}".format(repository, ref)) from exc
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise BuildError("GitHub returned invalid commit SHA: {!r}".format(commit))
    return SourceMetadata(repository, ref, commit, committed_at)


def download_v2fly_tree(metadata: SourceMetadata) -> SourceTree:
    archive = http_get(
        "https://codeload.github.com/{}/tar.gz/{}".format(
            metadata.repository, metadata.commit
        ),
        accept="application/octet-stream",
    )
    files: DefaultDict[str, List[str]] = defaultdict(list)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile() or "/data/" not in member.name:
                    continue
                relative = member.name.split("/data/", 1)[1]
                if not relative or relative.startswith("."):
                    continue
                extracted = tar.extractfile(member)
                if extracted is not None:
                    files[relative].extend(extracted.read().decode("utf-8").splitlines())
    except (tarfile.TarError, UnicodeDecodeError) as exc:
        raise BuildError("Invalid v2fly source archive") from exc
    return parse_source_files(files)


def load_local_tree(data_dir: Path) -> SourceTree:
    if not data_dir.is_dir():
        raise BuildError("Local source directory does not exist: {}".format(data_dir))
    files: Dict[str, Sequence[str]] = {}
    for path in sorted(item for item in data_dir.rglob("*") if item.is_file()):
        files[str(path.relative_to(data_dir))] = path.read_text(encoding="utf-8").splitlines()
    return parse_source_files(files)


def download_github_text(metadata: SourceMetadata, path: str) -> str:
    url = "https://raw.githubusercontent.com/{}/{}/{}".format(
        metadata.repository, metadata.commit, path
    )
    try:
        return http_get(url, accept="text/plain").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError("Invalid UTF-8 source: {}".format(url)) from exc


def write_outputs_atomically(project_root: Path, files: Mapping[Path, str]) -> None:
    with tempfile.TemporaryDirectory(prefix="media-rules-", dir=str(project_root)) as temp_dir:
        staging = Path(temp_dir)
        staged: Dict[Path, Path] = {}
        for relative, content in files.items():
            temp_path = staging / relative.name
            temp_path.write_text(content, encoding="utf-8")
            staged[relative] = temp_path
        for relative, temp_path in staged.items():
            destination = project_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(temp_path), str(destination))


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", choices=sorted(PRODUCTS), required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true", help="Fetch pinned GitHub sources")
    source.add_argument("--source-dir", type=Path, help="Use local v2fly data fixtures")
    parser.add_argument("--blackmatrix-file", type=Path)
    parser.add_argument("--sukka-stream-file", type=Path)
    parser.add_argument("--v2fly-ref", default=V2FLY_DEFAULT_REF)
    parser.add_argument("--blackmatrix-ref", default=BLACKMATRIX_DEFAULT_REF)
    parser.add_argument("--sukka-ref", default=SUKKA_DEFAULT_REF)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--allow-large-change", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = create_parser().parse_args(argv)
    config = PRODUCTS[args.product]
    project_root = args.project_root.resolve()

    if args.fetch:
        v2fly_meta = resolve_github_ref(V2FLY_REPOSITORY, args.v2fly_ref)
        tree = download_v2fly_tree(v2fly_meta)
    else:
        v2fly_meta = SourceMetadata("local-fixture", str(args.source_dir), "local", None)
        tree = load_local_tree(args.source_dir)

    if args.blackmatrix_file:
        blackmatrix_meta = SourceMetadata(
            "local-fixture", str(args.blackmatrix_file), "local", None
        )
        blackmatrix_text = args.blackmatrix_file.read_text(encoding="utf-8")
    elif args.fetch:
        blackmatrix_meta = resolve_github_ref(BLACKMATRIX_REPOSITORY, args.blackmatrix_ref)
        blackmatrix_text = download_github_text(blackmatrix_meta, config.blackmatrix_path)
    else:
        blackmatrix_meta = SourceMetadata("not-provided", "none", "none", None)
        blackmatrix_text = ""

    if args.sukka_stream_file:
        sukka_meta = SourceMetadata("local-fixture", str(args.sukka_stream_file), "local", None)
        sukka_text = args.sukka_stream_file.read_text(encoding="utf-8")
    elif args.fetch:
        sukka_meta = resolve_github_ref(SUKKA_REPOSITORY, args.sukka_ref)
        sukka_text = download_github_text(sukka_meta, SUKKA_STREAM_PATH)
    else:
        sukka_meta = SourceMetadata("not-provided", "none", "none", None)
        sukka_text = ""

    patch_root = project_root / config.patch_dir
    include_patch = parse_patch(patch_root / "include.txt")
    exclude_patch = parse_patch(patch_root / "exclude.txt")
    rules, counts, omitted = build_product_rules(
        tree, config, include_patch, exclude_patch
    )

    output_path = project_root / config.output_path
    report_path = project_root / config.report_dir / (config.key + "-report.json")
    audit_path = project_root / config.report_dir / "reference-audit.json"
    existing = parse_existing_generated(output_path)
    existing_report = load_existing_report(report_path)
    existing_audit = load_existing_report(audit_path)
    validate_output(config, rules, existing, args.allow_large_change)

    if blackmatrix_text:
        blackmatrix_rules, _ = parse_surge_rules(blackmatrix_text)
        blackmatrix_audit = compare_reference(rules, blackmatrix_text)
    else:
        blackmatrix_rules = []
        blackmatrix_audit = {"status": "not-provided"}
    if sukka_text:
        sukka_rules, _ = parse_sukka_service(sukka_text, config.sukka_constant)
        sukka_audit = compare_sukka(rules, sukka_text, config.sukka_constant)
    else:
        sukka_rules = []
        sukka_audit = {
            "status": "not-provided",
            "not_covered_by_generated_count": 0,
        }
    try:
        resolutions = load_resolution_catalog(
            project_root / "references/verification/reference-decisions.json",
            "media.{}".format(config.key),
        )
    except ReferenceVerificationError as exc:
        raise BuildError(str(exc)) from exc
    verification = verify_reference_sources(
        {
            "blackmatrix": blackmatrix_rules,
            "sukka": sukka_rules,
        },
        {config.display_name: rules},
        resolutions,
    )

    assessment = assess_change(
        config,
        rules,
        existing,
        omitted,
        existing_report,
        int(verification["manual_review_count"]),
        existing_audit,
        str(verification["manual_review_fingerprint"]),
    )
    reference_audit = {
        "schema_version": 1,
        "product": config.display_name,
        "policy": {
            "v2fly_is_only_formal_upstream": True,
            "reference_entries_auto_merged": False,
            "third_party_reference_entries_persisted": False,
        },
        "sources": {
            "blackmatrix": {
                "repository": blackmatrix_meta.repository,
                "requested_ref": blackmatrix_meta.requested_ref,
                "commit": blackmatrix_meta.commit,
                "committed_at": blackmatrix_meta.committed_at,
                **blackmatrix_audit,
            },
            "sukka": {
                "repository": sukka_meta.repository,
                "requested_ref": sukka_meta.requested_ref,
                "commit": sukka_meta.commit,
                "committed_at": sukka_meta.committed_at,
                **sukka_audit,
            },
        },
        "verification": verification,
    }
    report = {
        "schema_version": 1,
        "product": config.display_name,
        "v2fly": {
            "repository": v2fly_meta.repository,
            "requested_ref": v2fly_meta.requested_ref,
            "commit": v2fly_meta.commit,
            "committed_at": v2fly_meta.committed_at,
            "list": config.v2fly_list,
        },
        "counts": counts,
        "unsupported_omitted": omitted,
        "verification": summarize_generation_decisions(
            {"published": len(rules)},
            [
                {
                    "identity": value,
                    "reason": "unsupported-v2fly-syntax",
                }
                for value in omitted
            ],
        ),
        "safety": {
            "formal_upstream": V2FLY_REPOSITORY,
            "blackmatrix_auto_merged": False,
            "sukka_auto_merged": False,
            "source_attributes_retained": True,
            "routing_policy_embedded": False,
            "surge_config_modified": False,
            "actual_change_ratio": assessment["actual_change_ratio"],
            "auto_merge_eligible": assessment["auto_merge_eligible"],
        },
    }

    files = {
        config.output_path: render_rules(config, v2fly_meta, rules),
        config.report_dir / (config.key + "-report.json"): json.dumps(
            report, ensure_ascii=False, indent=2, sort_keys=True
        ),
        config.report_dir / "reference-audit.json": json.dumps(
            reference_audit, ensure_ascii=False, indent=2, sort_keys=True
        ),
        config.report_dir / "change-assessment.json": json.dumps(
            assessment, ensure_ascii=False, indent=2, sort_keys=True
        ),
    }
    write_outputs_atomically(project_root, files)
    print(
        "Built {}.list={} rules from {}".format(
            config.display_name, len(rules), v2fly_meta.commit
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        raise SystemExit(2)
