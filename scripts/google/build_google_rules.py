#!/usr/bin/env python3
"""Build reviewable Surge Google rules from v2fly/domain-list-community.

The script intentionally uses only Python's standard library. BlackMatrix7 and
SukkaW are downloaded only for comparison and never merged into generated
rules.
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


V2FLY_REPOSITORY = "v2fly/domain-list-community"
V2FLY_DEFAULT_REF = "master"
BLACKMATRIX_REPOSITORY = "blackmatrix7/ios_rule_script"
BLACKMATRIX_DEFAULT_REF = "master"
BLACKMATRIX_PATH = "rule/Surge/Google/Google.list"
SUKKA_REPOSITORY = "SukkaW/Surge"
SUKKA_DEFAULT_REF = "master"
SUKKA_GLOBAL_PATH = "Source/non_ip/global.ts"
SUKKA_AI_PATH = "Source/non_ip/ai.conf"
USER_AGENT = "schmidttt-Surge-Rules/0.1 (+local-review-build)"
CORE_SUFFIXES = {"google.com", "googleapis.com", "gstatic.com"}
PRODUCT_LISTS = ("google-deepmind", "youtube")
OUTPUT_TYPES = {"domain", "full"}
TYPE_ORDER = {"full": 0, "domain": 1}
AUTO_MERGE_MAX_ADDITIONS = 20
AUTO_MERGE_MAX_CHANGE_RATIO = 0.02
DISPLAY_TIMEZONE = timezone(timedelta(hours=8))


class BuildError(RuntimeError):
    """Raised when source data or generated output is unsafe to publish."""


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
    labels = ascii_value.split(".")
    for label in labels:
        if not label or len(label) > 63:
            raise BuildError("Invalid domain label in {!r}".format(value))
        if label.startswith("-") or label.endswith("-"):
            raise BuildError("Invalid hyphen placement in {!r}".format(value))
        if not re.fullmatch(r"[a-z0-9_-]+", label):
            raise BuildError("Unsupported domain characters in {!r}".format(value))
    return ascii_value


def parse_rule_token(token: str, attrs: Iterable[str]) -> Rule:
    if ":" in token:
        kind, value = token.split(":", 1)
        kind = kind.lower()
    else:
        kind, value = "domain", token
    if kind in {"domain", "full"}:
        value = normalize_domain(value)
    elif kind in {"keyword", "regexp"}:
        value = value.strip()
        if not value:
            raise BuildError("Empty {} rule".format(kind))
    else:
        raise BuildError("Unsupported v2fly rule type: {}".format(kind))
    return Rule(kind=kind, value=value, attrs=frozenset(attrs))


def parse_source_files(files: Mapping[str, Sequence[str]]) -> SourceTree:
    """Parse all data files, merging files that share the same basename."""
    tree = SourceTree.empty()
    for source_name in sorted(files):
        list_name = Path(source_name).name
        for line_number, raw_line in enumerate(files[source_name], 1):
            line = strip_comment(raw_line)
            if not line:
                continue
            tokens = line.split()
            head, tail = tokens[0], tokens[1:]
            if head.startswith("include:"):
                target = head.split(":", 1)[1].strip().lower()
                if not target:
                    raise BuildError("Empty include in {}:{}".format(source_name, line_number))
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
                tree.includes[list_name].append(
                    Include(target, frozenset(require), frozenset(forbid))
                )
                continue

            attrs = {token[1:].lower() for token in tail if token.startswith("@")}
            affiliations = {token[1:].lower() for token in tail if token.startswith("&")}
            unknown = [token for token in tail if not token.startswith(("@", "&"))]
            if unknown:
                raise BuildError(
                    "Unsupported modifiers {} in {}:{}".format(unknown, source_name, line_number)
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
            raise BuildError("Missing included list: {}".format(current))
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
    rules: List[Rule] = []
    if not path.exists():
        return rules
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = strip_comment(raw_line)
        if not line:
            continue
        tokens = line.split()
        if tokens[0].startswith("include:"):
            raise BuildError("Patch includes are not allowed: {}:{}".format(path, line_number))
        attrs = {token[1:].lower() for token in tokens[1:] if token.startswith("@")}
        unknown = [token for token in tokens[1:] if not token.startswith("@")]
        if unknown:
            raise BuildError("Invalid patch modifiers in {}:{}".format(path, line_number))
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
    """Return whether rule's matching scope contains other's domain scope."""
    if rule.kind == "full":
        return other.kind == "full" and rule.value == other.value
    if rule.kind != "domain" or other.kind not in OUTPUT_TYPES:
        return False
    return other.value == rule.value or other.value.endswith("." + rule.value)


def covered_by_any(rule: Rule, covering_rules: Iterable[Rule]) -> bool:
    return any(domain_rule_covers(candidate, rule) for candidate in covering_rules)


def build_outputs(
    tree: SourceTree,
    include_patch: Sequence[Rule],
    exclude_patch: Sequence[Rule],
) -> Tuple[List[Rule], Dict[str, int], List[str]]:
    """Build one routing-neutral Google remainder list.

    v2fly @cn and @ads attributes are retained in Google.list. The Surge
    configuration owns DIRECT/REJECT decisions and must place those rules
    before this list. Google AI and YouTube are still removed as product
    partitions, because they use separate policy groups.
    """
    google = expand_list(tree, "google")
    products: Dict[str, List[Rule]] = {
        name: expand_list(tree, name) for name in PRODUCT_LISTS
    }
    product_rules = dedupe_rules(rule for values in products.values() for rule in values)
    product_ids = {rule.identity for rule in product_rules}
    exclude_ids = {rule.identity for rule in exclude_patch}

    main: List[Rule] = []
    unsupported_omitted: List[str] = []
    counts = Counter()
    for rule in google:
        if "ads" in rule.attrs:
            counts["ads_tagged_in_source"] += 1
        if "cn" in rule.attrs:
            counts["cn_tagged_in_source"] += 1
        if rule.identity in product_ids:
            counts["product_exact_excluded"] += 1
            continue
        if rule.kind not in OUTPUT_TYPES:
            counts["unsupported_{}_omitted".format(rule.kind)] += 1
            unsupported_omitted.append("{}:{}".format(rule.kind, rule.value))
            continue
        main.append(rule)

    main.extend(include_patch)

    main = dedupe_rules(rule for rule in main if rule.identity not in exclude_ids)

    counts["google_expanded"] = len(google)
    counts["google_unique"] = len(dedupe_rules(google))
    counts["google_deepmind_unique"] = len(dedupe_rules(products["google-deepmind"]))
    counts["youtube_unique"] = len(dedupe_rules(products["youtube"]))
    counts["main_output"] = len(main)
    counts["include_patch"] = len(include_patch)
    counts["exclude_patch"] = len(exclude_patch)
    return main, dict(sorted(counts.items())), sorted(set(unsupported_omitted))


def rule_to_surge(rule: Rule) -> str:
    if rule.kind == "full":
        return "DOMAIN,{}".format(rule.value)
    if rule.kind == "domain":
        return "DOMAIN-SUFFIX,{}".format(rule.value)
    raise BuildError("Cannot emit unsupported rule type: {}".format(rule.kind))


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


def render_rules(source: SourceMetadata, rules: Sequence[Rule]) -> str:
    header = [
        "# NAME: schmidttt's Google Ruleset",
        "# AUTHOR: schmidttt",
        "# REPO: https://github.com/schmidttt/Surge-Rules",
        "# UPDATED: {}".format(format_updated_at(source.committed_at)),
        "# TOTAL: {}".format(len(rules)),
        "#",
        "# ======== 上游同步规则 ========",
    ]
    return "\n".join(header + [rule_to_surge(rule) for rule in rules])


def parse_surge_domain_rules(text: str) -> Tuple[Set[Tuple[str, str]], Counter]:
    identities: Set[Tuple[str, str]] = set()
    unsupported: Counter = Counter()
    for raw_line in text.splitlines():
        line = strip_comment(raw_line)
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        rule_type = parts[0].upper()
        if len(parts) >= 2 and rule_type in {"DOMAIN", "DOMAIN-SUFFIX"}:
            kind = "full" if rule_type == "DOMAIN" else "domain"
            identities.add((kind, normalize_domain(parts[1])))
        else:
            unsupported[rule_type] += 1
    return identities, unsupported


def parse_sukka_global_google(text: str) -> List[Rule]:
    """Extract Sukka's GLOBAL.GOOGLE domains without treating it as a ruleset."""
    lines = text.splitlines()
    in_google = False
    in_domains = False
    rules: List[Rule] = []
    for raw_line in lines:
        code = raw_line.split("//", 1)[0].strip()
        if not in_google:
            if re.fullmatch(r"GOOGLE\s*:\s*\{", code):
                in_google = True
            continue
        if not in_domains:
            if re.fullmatch(r"domains\s*:\s*\[", code):
                in_domains = True
            continue
        if code == "]" or code == "],":
            break
        if not code:
            continue
        match = re.fullmatch(r"(['\"])([^'\"]+)\1\s*,?", code)
        if not match:
            raise BuildError("Unsupported Sukka GLOBAL.GOOGLE line: {!r}".format(raw_line))
        value = match.group(2)
        kind = "domain"
        if value.startswith("$"):
            kind, value = "full", value[1:]
        rules.append(Rule(kind, normalize_domain(value)))
    if not in_google or not in_domains or not rules:
        raise BuildError("Could not locate Sukka GLOBAL.GOOGLE domains")
    return dedupe_rules(rules)


def parse_sukka_google_ai(text: str) -> Tuple[List[Rule], Counter, List[str]]:
    """Extract only the Google section from Sukka's mixed AI ruleset."""
    in_google = False
    rules: List[Rule] = []
    unsupported: Counter = Counter()
    unsupported_lines: List[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped == "# >> Google":
            in_google = True
            continue
        if in_google and stripped.startswith("# >> "):
            break
        if not in_google:
            continue
        line = strip_comment(raw_line)
        if not line:
            continue
        parts = [part.strip() for part in line.split(",", 1)]
        rule_type = parts[0].upper()
        if len(parts) == 2 and rule_type in {"DOMAIN", "DOMAIN-SUFFIX"}:
            kind = "full" if rule_type == "DOMAIN" else "domain"
            rules.append(Rule(kind, normalize_domain(parts[1])))
        else:
            unsupported[rule_type] += 1
            unsupported_lines.append(line)
    if not in_google or not rules:
        raise BuildError("Could not locate Sukka Google AI rules")
    return dedupe_rules(rules), unsupported, unsupported_lines


def parse_existing_generated(path: Path) -> Set[Tuple[str, str]]:
    if not path.exists():
        return set()
    rules, _ = parse_surge_domain_rules(path.read_text(encoding="utf-8"))
    return rules


def load_existing_unsupported(path: Path) -> Optional[Set[str]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload["unsupported_omitted"]
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BuildError("Could not read existing unsupported baseline: {}".format(path)) from exc
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise BuildError("Invalid unsupported baseline: {}".format(path))
    return set(values)


def identity_to_surge(identity: Tuple[str, str]) -> str:
    return rule_to_surge(Rule(identity[0], identity[1]))


def assess_change(
    main: Sequence[Rule],
    existing_main: Set[Tuple[str, str]],
    unsupported_omitted: Sequence[str],
    existing_unsupported: Optional[Set[str]],
    max_auto_additions: int,
    max_auto_change_ratio: float,
    max_build_change_ratio: float,
) -> Dict[str, object]:
    new = {rule.identity for rule in main}
    added = new.difference(existing_main)
    removed = existing_main.difference(new)
    baseline_count = len(existing_main)
    change_count = len(added) + len(removed)
    change_ratio = change_count / max(baseline_count, 1)
    current_unsupported = set(unsupported_omitted)
    unsupported_changed = (
        existing_unsupported is None or current_unsupported != existing_unsupported
    )

    reasons: List[str] = []
    if not existing_main:
        reasons.append("initial-baseline-requires-review")
    if removed:
        reasons.append("rules-removed")
    if len(added) > max_auto_additions:
        reasons.append("addition-count-above-auto-merge-limit")
    if change_ratio > max_auto_change_ratio:
        reasons.append("actual-change-ratio-above-auto-merge-limit")
    if unsupported_changed:
        reasons.append("unsupported-rule-set-changed")

    auto_merge_eligible = not reasons
    return {
        "schema_version": 1,
        "classification": "low-risk" if auto_merge_eligible else "review-required",
        "auto_merge_eligible": auto_merge_eligible,
        "baseline_rules": baseline_count,
        "generated_rules": len(new),
        "added_count": len(added),
        "removed_count": len(removed),
        "actual_change_count": change_count,
        "actual_change_ratio": round(change_ratio, 6),
        "unsupported_changed": unsupported_changed,
        "reasons": reasons,
        "added": [identity_to_surge(identity) for identity in sorted(added)],
        "removed": [identity_to_surge(identity) for identity in sorted(removed)],
        "thresholds": {
            "max_auto_additions": max_auto_additions,
            "max_auto_change_ratio": max_auto_change_ratio,
            "max_build_change_ratio": max_build_change_ratio,
            "automatic_deletions_allowed": False,
        },
    }


def validate_outputs(
    main: Sequence[Rule],
    existing_main: Set[Tuple[str, str]],
    max_change_ratio: float,
    allow_large_change: bool,
) -> None:
    if not main:
        raise BuildError("Google.list would be empty")
    main_suffixes = {rule.value for rule in main if rule.kind == "domain"}
    missing_core = sorted(CORE_SUFFIXES.difference(main_suffixes))
    if missing_core:
        raise BuildError("Core Google suffixes disappeared: {}".format(missing_core))
    if allow_large_change:
        return
    if existing_main:
        new = {rule.identity for rule in main}
        ratio = len(new.symmetric_difference(existing_main)) / max(len(existing_main), 1)
        if ratio > max_change_ratio:
            raise BuildError(
                "Google.list actual rule churn is {:.1%}, above {:.1%}; review and rerun "
                "with --allow-large-change if expected".format(
                    ratio, max_change_ratio
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
    encoded_ref = urllib.parse.quote(ref, safe="")
    url = "https://api.github.com/repos/{}/commits/{}".format(repository, encoded_ref)
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
    url = "https://codeload.github.com/{}/tar.gz/{}".format(
        metadata.repository, metadata.commit
    )
    archive = http_get(url, accept="application/octet-stream")
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
                if extracted is None:
                    continue
                text = extracted.read().decode("utf-8")
                files[relative].extend(text.splitlines())
    except (tarfile.TarError, UnicodeDecodeError) as exc:
        raise BuildError("Invalid v2fly source archive") from exc
    if "google" not in {Path(name).name for name in files}:
        raise BuildError("v2fly archive does not contain data/google")
    return parse_source_files(files)


def load_local_tree(data_dir: Path) -> SourceTree:
    if not data_dir.is_dir():
        raise BuildError("Local source directory does not exist: {}".format(data_dir))
    files: Dict[str, Sequence[str]] = {}
    for path in sorted(item for item in data_dir.rglob("*") if item.is_file()):
        relative = str(path.relative_to(data_dir))
        files[relative] = path.read_text(encoding="utf-8").splitlines()
    return parse_source_files(files)


def download_blackmatrix(metadata: SourceMetadata) -> str:
    url = "https://raw.githubusercontent.com/{}/{}/{}".format(
        metadata.repository, metadata.commit, BLACKMATRIX_PATH
    )
    return http_get(url, accept="text/plain").decode("utf-8")


def download_github_text(metadata: SourceMetadata, path: str) -> str:
    url = "https://raw.githubusercontent.com/{}/{}/{}".format(
        metadata.repository, metadata.commit, path
    )
    try:
        return http_get(url, accept="text/plain").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError("Invalid UTF-8 source: {}".format(url)) from exc


def comparison_report(
    main: Sequence[Rule], blackmatrix_text: str
) -> Dict[str, object]:
    ours = {rule.identity for rule in main}
    theirs, unsupported = parse_surge_domain_rules(blackmatrix_text)
    only_theirs = sorted(theirs.difference(ours))
    only_ours = sorted(ours.difference(theirs))
    return {
        "blackmatrix_domain_rules": len(theirs),
        "common_domain_rules": len(ours.intersection(theirs)),
        "only_blackmatrix_count": len(only_theirs),
        "only_v2fly_generated_count": len(only_ours),
        "blackmatrix_non_domain_types": dict(sorted(unsupported.items())),
        "note": (
            "Aggregate comparison only; BlackMatrix7 entries are neither "
            "merged nor persisted in this repository."
        ),
    }


def classify_reference_rule_details(
    reference_rules: Sequence[Rule],
    main: Sequence[Rule],
    google_ai: Sequence[Rule],
    youtube: Sequence[Rule],
) -> Dict[str, object]:
    """Classify references internally without defining the public report shape."""
    groups = (
        ("google_ai", google_ai),
        ("youtube", youtube),
        ("google_main", main),
    )
    classified: Dict[str, List[str]] = {name: [] for name, _ in groups}
    classified["needs_review"] = []
    for rule in dedupe_rules(reference_rules):
        rendered = "{}:{}".format(rule.kind, rule.value)
        for name, covering_rules in groups:
            if covered_by_any(rule, covering_rules):
                classified[name].append(rendered)
                break
        else:
            classified["needs_review"].append(rendered)
    return {
        "total": len(dedupe_rules(reference_rules)),
        "counts": {name: len(values) for name, values in classified.items()},
        "entries": classified,
    }


def classify_reference_rules(
    reference_rules: Sequence[Rule],
    main: Sequence[Rule],
    google_ai: Sequence[Rule],
    youtube: Sequence[Rule],
) -> Dict[str, object]:
    """Return aggregate classifications without persisting reference entries."""
    details = classify_reference_rule_details(
        reference_rules, main, google_ai, youtube
    )
    return {
        "total": details["total"],
        "counts": details["counts"],
        "entries_persisted": False,
    }


def validate_official_core(
    official_rules: Sequence[Rule],
    main: Sequence[Rule],
    google_ai: Sequence[Rule],
    youtube: Sequence[Rule],
) -> Dict[str, object]:
    details = classify_reference_rule_details(
        official_rules, main, google_ai, youtube
    )
    missing = details["entries"]["needs_review"]
    if missing:
        raise BuildError(
            "Google official core assertions are not covered: {}".format(missing)
        )
    return {
        "total": details["total"],
        "counts": details["counts"],
        "entries_persisted": False,
        "note": (
            "Curated product-specific assertions from Google documentation; "
            "not a complete Google ecosystem list."
        ),
    }


def write_outputs_atomically(project_root: Path, files: Mapping[Path, str]) -> None:
    with tempfile.TemporaryDirectory(prefix="google-rules-", dir=str(project_root)) as temp_dir:
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
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true", help="Fetch pinned GitHub sources")
    source.add_argument("--source-dir", type=Path, help="Use a local v2fly data directory")
    parser.add_argument("--blackmatrix-file", type=Path, help="Local BlackMatrix7 Google.list")
    parser.add_argument("--sukka-global-file", type=Path, help="Local Sukka global.ts")
    parser.add_argument("--sukka-ai-file", type=Path, help="Local Sukka ai.conf")
    parser.add_argument(
        "--official-core-file",
        type=Path,
        help="Curated Google-official core assertions (defaults under project root)",
    )
    parser.add_argument("--v2fly-ref", default=V2FLY_DEFAULT_REF)
    parser.add_argument("--blackmatrix-ref", default=BLACKMATRIX_DEFAULT_REF)
    parser.add_argument("--sukka-ref", default=SUKKA_DEFAULT_REF)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--max-change-ratio", type=float, default=0.10)
    parser.add_argument(
        "--auto-merge-max-additions", type=int, default=AUTO_MERGE_MAX_ADDITIONS
    )
    parser.add_argument(
        "--auto-merge-max-change-ratio",
        type=float,
        default=AUTO_MERGE_MAX_CHANGE_RATIO,
    )
    parser.add_argument("--allow-large-change", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = create_parser().parse_args(argv)
    project_root = args.project_root.resolve()
    patches = project_root / "patches" / "google"

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
        blackmatrix_meta = resolve_github_ref(
            BLACKMATRIX_REPOSITORY, args.blackmatrix_ref
        )
        blackmatrix_text = download_blackmatrix(blackmatrix_meta)
    else:
        blackmatrix_meta = SourceMetadata("not-provided", "none", "none", None)
        blackmatrix_text = ""

    if bool(args.sukka_global_file) != bool(args.sukka_ai_file):
        raise BuildError(
            "Provide both --sukka-global-file and --sukka-ai-file, or neither"
        )
    if args.sukka_global_file and args.sukka_ai_file:
        sukka_meta = SourceMetadata(
            "local-fixture", str(args.sukka_global_file), "local", None
        )
        sukka_global_text = args.sukka_global_file.read_text(encoding="utf-8")
        sukka_ai_text = args.sukka_ai_file.read_text(encoding="utf-8")
    elif args.fetch:
        sukka_meta = resolve_github_ref(SUKKA_REPOSITORY, args.sukka_ref)
        sukka_global_text = download_github_text(sukka_meta, SUKKA_GLOBAL_PATH)
        sukka_ai_text = download_github_text(sukka_meta, SUKKA_AI_PATH)
    else:
        sukka_meta = SourceMetadata("not-provided", "none", "none", None)
        sukka_global_text = ""
        sukka_ai_text = ""

    include_patch = parse_patch(patches / "include.txt")
    exclude_patch = parse_patch(patches / "exclude.txt")
    main_rules, counts, unsupported_omitted = build_outputs(
        tree, include_patch, exclude_patch
    )
    google_ai_rules = dedupe_rules(expand_list(tree, "google-deepmind"))
    youtube_rules = dedupe_rules(expand_list(tree, "youtube"))

    official_core_path = args.official_core_file or (
        project_root / "references/google/official-core.txt"
    )
    if not official_core_path.is_file():
        raise BuildError(
            "Google official core assertions are missing: {}".format(
                official_core_path
            )
        )
    official_core_rules = parse_patch(official_core_path)
    if not official_core_rules:
        raise BuildError("Google official core assertions are empty")

    existing_main = parse_existing_generated(project_root / "rules/Google/Google.list")
    existing_unsupported = load_existing_unsupported(
        project_root / "reports/google/google-report.json"
    )
    validate_outputs(
        main_rules,
        existing_main,
        args.max_change_ratio,
        args.allow_large_change,
    )
    change_assessment = assess_change(
        main_rules,
        existing_main,
        unsupported_omitted,
        existing_unsupported,
        args.auto_merge_max_additions,
        args.auto_merge_max_change_ratio,
        args.max_change_ratio,
    )

    official_core_audit = validate_official_core(
        official_core_rules,
        main_rules,
        google_ai_rules,
        youtube_rules,
    )

    if sukka_global_text and sukka_ai_text:
        sukka_global_rules = parse_sukka_global_google(sukka_global_text)
        sukka_ai_rules, sukka_ai_unsupported, sukka_ai_unsupported_lines = (
            parse_sukka_google_ai(sukka_ai_text)
        )
        sukka_global_audit = classify_reference_rules(
            sukka_global_rules,
            main_rules,
            google_ai_rules,
            youtube_rules,
        )
        sukka_ai_audit = classify_reference_rules(
            sukka_ai_rules,
            main_rules,
            google_ai_rules,
            youtube_rules,
        )
        sukka_ai_audit["unsupported_types"] = dict(
            sorted(sukka_ai_unsupported.items())
        )
        sukka_ai_audit["unsupported_line_count"] = len(
            sukka_ai_unsupported_lines
        )
    else:
        sukka_global_audit = {"status": "not-provided"}
        sukka_ai_audit = {"status": "not-provided"}

    reference_audit = {
        "schema_version": 2,
        "policy": {
            "v2fly_is_only_generation_source": True,
            "reference_entries_auto_merged": False,
            "third_party_reference_entries_persisted": False,
            "needs_review_is_blocking_for_manual_merge": True,
        },
        "sukka": {
            "repository": sukka_meta.repository,
            "requested_ref": sukka_meta.requested_ref,
            "commit": sukka_meta.commit,
            "committed_at": sukka_meta.committed_at,
            "global_google": sukka_global_audit,
            "google_ai": sukka_ai_audit,
        },
        "google_official_core": official_core_audit,
    }

    report = {
        "schema_version": 2,
        "v2fly": {
            "repository": v2fly_meta.repository,
            "requested_ref": v2fly_meta.requested_ref,
            "commit": v2fly_meta.commit,
            "committed_at": v2fly_meta.committed_at,
        },
        "blackmatrix": {
            "repository": blackmatrix_meta.repository,
            "requested_ref": blackmatrix_meta.requested_ref,
            "commit": blackmatrix_meta.commit,
            "committed_at": blackmatrix_meta.committed_at,
        },
        "sukka": {
            "repository": sukka_meta.repository,
            "requested_ref": sukka_meta.requested_ref,
            "commit": sukka_meta.commit,
            "committed_at": sukka_meta.committed_at,
        },
        "counts": counts,
        "unsupported_omitted": unsupported_omitted,
        "comparison": comparison_report(main_rules, blackmatrix_text),
        "safety": {
            "blackmatrix_auto_merged": False,
            "sukka_auto_merged": False,
            "official_core_assertions_passed": True,
            "routing_policy_embedded": False,
            "source_cn_and_ads_attributes_retained": True,
            "surge_config_modified": False,
            "max_change_ratio": args.max_change_ratio,
            "actual_change_ratio": change_assessment["actual_change_ratio"],
            "auto_merge_eligible": change_assessment["auto_merge_eligible"],
        },
    }

    files = {
        Path("rules/Google/Google.list"): render_rules(v2fly_meta, main_rules),
        Path("reports/google/google-report.json"): json.dumps(
            report, ensure_ascii=False, indent=2, sort_keys=True
        ),
        Path("reports/google/reference-audit.json"): json.dumps(
            reference_audit, ensure_ascii=False, indent=2, sort_keys=True
        ),
        Path("reports/google/change-assessment.json"): json.dumps(
            change_assessment, ensure_ascii=False, indent=2, sort_keys=True
        ),
    }
    write_outputs_atomically(project_root, files)
    print(
        "Built Google.list={} rules from {}".format(
            len(main_rules), v2fly_meta.commit
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        raise SystemExit(2)
