#!/usr/bin/env python3
"""Validate generated lists, repository casing, and published raw paths."""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


EXPECTED_LISTS = {
    "rules/Google/Google.list": 500,
    "rules/GoogleCN/GoogleCN.list": 20,
    "rules/YouTube/YouTube.list": 100,
    "rules/TikTok/TikTok.list": 20,
    "rules/BiliBili/BiliBili.list": 30,
    "rules/Game/Game.list": 150,
    "rules/GameCN/GameCN.list": 20,
}
TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".yml",
    ".yaml",
    ".list",
    ".txt",
    ".json",
}
RAW_PREFIX = "https://raw.githubusercontent.com/schmidttt/surge-rules/"
LEGACY_REPO_NAME = "Surge" + "-Rules"


class ValidationError(RuntimeError):
    pass


def validate_list(path: Path, minimum: int) -> Tuple[int, List[str]]:
    data = path.read_bytes()
    if data.endswith((b"\n", b"\r")):
        raise ValidationError("{} has a trailing newline".format(path))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("{} is not UTF-8".format(path)) from exc
    lines = text.splitlines()
    rules = [line for line in lines if line and not line.startswith("#")]
    if len(rules) < minimum:
        raise ValidationError(
            "{} contains {} rules, below {}".format(path, len(rules), minimum)
        )
    total_lines = [line for line in lines if line.startswith("# TOTAL: ")]
    if total_lines != ["# TOTAL: {}".format(len(rules))]:
        raise ValidationError("{} has an invalid TOTAL header".format(path))
    if len(rules) != len(set(rules)):
        raise ValidationError("{} contains duplicate rules".format(path))
    for line in rules:
        parts = line.split(",")
        if len(parts) != 2 or parts[0] not in {"DOMAIN", "DOMAIN-SUFFIX"}:
            raise ValidationError("{} has invalid rule {!r}".format(path, line))
        if not parts[1] or parts[1] != parts[1].lower():
            raise ValidationError("{} has invalid domain {!r}".format(path, line))
    return len(rules), rules


def validate_lowercase_repository_name(project_root: Path) -> None:
    findings: List[str] = []
    for path in sorted(project_root.rglob("*")):
        if (
            not path.is_file()
            or ".git" in path.parts
            or path.suffix not in TEXT_SUFFIXES
        ):
            continue
        text = path.read_text(encoding="utf-8")
        if LEGACY_REPO_NAME in text:
            findings.append(str(path.relative_to(project_root)))
    if findings:
        raise ValidationError(
            "Legacy repository casing remains in: {}".format(findings)
        )


def validate_raw_links(project_root: Path) -> None:
    readme = (project_root / "README.md").read_text(encoding="utf-8")
    paths = set(
        re.findall(
            re.escape(RAW_PREFIX) + r"main/(rules/[A-Za-z0-9/_-]+\.list)",
            readme,
        )
    )
    expected = set(EXPECTED_LISTS)
    missing_links = sorted(expected.difference(paths))
    invalid_paths = sorted(path for path in paths if not (project_root / path).is_file())
    if missing_links:
        raise ValidationError(
            "README is missing raw links for: {}".format(missing_links)
        )
    if invalid_paths:
        raise ValidationError(
            "README raw links do not map to local files: {}".format(invalid_paths)
        )


def validate_cross_outputs(all_rules: Dict[str, List[str]]) -> None:
    game = set(all_rules["rules/Game/Game.list"])
    game_cn = set(all_rules["rules/GameCN/GameCN.list"])
    duplicates = sorted(game.intersection(game_cn))
    if duplicates:
        raise ValidationError(
            "Game and GameCN contain exact duplicates: {}".format(duplicates)
        )
    google_cn = all_rules["rules/GoogleCN/GoogleCN.list"]
    forbidden = [
        rule
        for rule in google_cn
        if any(
            marker in rule
            for marker in (
                "analytics",
                "doubleclick",
                "mtalk",
                "tagmanager",
                "tagservices",
            )
        )
    ]
    if forbidden:
        raise ValidationError(
            "GoogleCN contains a hard-excluded service: {}".format(forbidden)
        )


def check_remote_ref(project_root: Path, ref: str) -> None:
    for relative in EXPECTED_LISTS:
        url = "{}{}/{}".format(RAW_PREFIX, ref, relative)
        request = urllib.request.Request(
            url, headers={"User-Agent": "schmidttt-surge-rules-validator/0.2"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                remote = response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ValidationError("Could not fetch {}: {}".format(url, exc)) from exc
        local = (project_root / relative).read_bytes()
        if remote != local:
            raise ValidationError("Remote raw content differs: {}".format(relative))


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--check-remote-ref",
        help="Fetch every raw list at this branch or commit and compare bytes",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = create_parser().parse_args(argv)
    project_root = args.project_root.resolve()
    all_rules: Dict[str, List[str]] = {}
    counts: Dict[str, int] = {}
    for relative, minimum in EXPECTED_LISTS.items():
        path = project_root / relative
        if not path.is_file():
            raise ValidationError("Missing generated list: {}".format(relative))
        count, rules = validate_list(path, minimum)
        counts[relative] = count
        all_rules[relative] = rules
    validate_lowercase_repository_name(project_root)
    validate_raw_links(project_root)
    validate_cross_outputs(all_rules)
    if args.check_remote_ref:
        check_remote_ref(project_root, args.check_remote_ref)
    print(
        "Repository validation passed: {}".format(
            ", ".join(
                "{}={}".format(Path(path).name, count)
                for path, count in counts.items()
            )
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        raise SystemExit(2)
