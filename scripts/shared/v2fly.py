"""Small, standard-library-only helpers for v2fly-backed Surge rules."""

from __future__ import annotations

import io
import json
import os
import re
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import DefaultDict, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


V2FLY_REPOSITORY = "v2fly/domain-list-community"
V2FLY_DEFAULT_REF = "master"
USER_AGENT = "schmidttt-surge-rules/0.2 (+reviewable-rule-build)"
DISPLAY_TIMEZONE = timezone(timedelta(hours=8))
OUTPUT_TYPES = {"domain", "full"}
TYPE_ORDER = {"full": 0, "domain": 1}


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


@dataclass(frozen=True)
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
    if not value or len(value) > 253 or any(character.isspace() for character in value):
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
                    raise BuildError(
                        "Empty include in {}:{}".format(source_name, line_number)
                    )
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
            affiliations = {
                token[1:].lower() for token in tail if token.startswith("&")
            }
            unknown = [
                token for token in tail if not token.startswith(("@", "&"))
            ]
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
            raise BuildError(
                "Circular include: {}".format(" -> ".join(stack + (current,)))
            )
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


def dedupe_rules(rules: Iterable[Rule]) -> List[Rule]:
    by_identity: Dict[Tuple[str, str], Rule] = {}
    for rule in rules:
        existing = by_identity.get(rule.identity)
        if existing is None:
            by_identity[rule.identity] = rule
        else:
            by_identity[rule.identity] = Rule(
                rule.kind,
                rule.value,
                frozenset(existing.attrs.union(rule.attrs)),
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


def parse_patch(path: Path) -> List[Rule]:
    rules: List[Rule] = []
    if not path.exists():
        return rules
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = strip_comment(raw_line)
        if not line:
            continue
        tokens = line.split()
        if len(tokens) != 1:
            raise BuildError(
                "Patch entries must contain one rule token in {}:{}".format(
                    path, line_number
                )
            )
        rule = parse_rule_token(tokens[0])
        if rule.kind not in OUTPUT_TYPES:
            raise BuildError(
                "Patch supports only domain/full in {}:{}".format(
                    path, line_number
                )
            )
        rules.append(rule)
    return dedupe_rules(rules)


def rule_to_surge(rule: Rule) -> str:
    if rule.kind == "full":
        return "DOMAIN,{}".format(rule.value)
    if rule.kind == "domain":
        return "DOMAIN-SUFFIX,{}".format(rule.value)
    raise BuildError("Cannot emit unsupported rule type: {}".format(rule.kind))


def parse_surge_rules(text: str) -> List[Rule]:
    rules: List[Rule] = []
    for raw_line in text.splitlines():
        line = strip_comment(raw_line)
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 2:
            raise BuildError("Invalid generated rule line: {!r}".format(line))
        rule_type = parts[0].upper()
        if rule_type == "DOMAIN":
            rules.append(Rule("full", normalize_domain(parts[1])))
        elif rule_type == "DOMAIN-SUFFIX":
            rules.append(Rule("domain", normalize_domain(parts[1])))
        else:
            raise BuildError("Unsupported generated rule type: {}".format(rule_type))
    return dedupe_rules(rules)


def load_existing_rules(path: Path) -> List[Rule]:
    if not path.exists():
        return []
    return parse_surge_rules(path.read_text(encoding="utf-8"))


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


def render_rules(name: str, source: SourceMetadata, rules: Sequence[Rule]) -> str:
    header = [
        "# NAME: schmidttt's {} Ruleset".format(name),
        "# AUTHOR: schmidttt",
        "# REPO: https://github.com/schmidttt/surge-rules",
        "# UPDATED: {}".format(format_updated_at(source.committed_at)),
        "# TOTAL: {}".format(len(rules)),
        "#",
        "# ======== 上游同步规则 ========",
    ]
    return "\n".join(header + [rule_to_surge(rule) for rule in rules])


def github_headers() -> Dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    }
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


def resolve_github_ref(
    repository: str = V2FLY_REPOSITORY,
    ref: str = V2FLY_DEFAULT_REF,
) -> SourceMetadata:
    encoded_ref = urllib.parse.quote(ref, safe="")
    url = "https://api.github.com/repos/{}/commits/{}".format(
        repository, encoded_ref
    )
    try:
        payload = json.loads(http_get(url).decode("utf-8"))
        commit = payload["sha"]
        committed_at = payload.get("commit", {}).get("committer", {}).get("date")
    except (KeyError, ValueError, UnicodeDecodeError) as exc:
        raise BuildError(
            "Invalid GitHub commit response for {}/{}".format(repository, ref)
        ) from exc
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
                files[relative].extend(
                    extracted.read().decode("utf-8").splitlines()
                )
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
        files[str(path.relative_to(data_dir))] = path.read_text(
            encoding="utf-8"
        ).splitlines()
    return parse_source_files(files)


def write_staged_outputs(project_root: Path, files: Mapping[Path, str]) -> None:
    """Validate all content first, then replace each destination from staging."""
    with tempfile.TemporaryDirectory(
        prefix="surge-rules-", dir=str(project_root)
    ) as temp_dir:
        staging = Path(temp_dir)
        staged: Dict[Path, Path] = {}
        for index, (relative, content) in enumerate(files.items()):
            temp_path = staging / "{}-{}".format(index, relative.name)
            temp_path.write_text(content, encoding="utf-8")
            staged[relative] = temp_path
        for relative, temp_path in staged.items():
            destination = project_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(temp_path), str(destination))
