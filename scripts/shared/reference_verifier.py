"""Deterministic reference verification shared by every ruleset builder."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


Identity = Tuple[str, str]
ALLOWED_ACTIONS = {
    "accept-exact-host",
    "exclude-shared-service",
    "exclude-reference-scope",
}
ALLOWED_SCOPES = {
    "ai",
    "game",
    "google",
    "googlecn",
    "media.bilibili",
    "media.tiktok",
    "media.youtube",
}


class ReferenceVerificationError(ValueError):
    """Raised when a persisted verification decision is malformed."""


def identity(rule) -> Identity:
    return (rule.kind, rule.value)


def identity_text(value: Identity) -> str:
    return "{}:{}".format(value[0], value[1])


def covers(left, right) -> bool:
    if left.kind == "full":
        return right.kind == "full" and left.value == right.value
    if left.kind != "domain" or right.kind not in {"domain", "full"}:
        return False
    return right.value == left.value or right.value.endswith("." + left.value)


def load_resolution_catalog(
    path: Path,
    scope: str,
) -> Dict[Identity, Dict[str, object]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["schema_version"] != 1 or not isinstance(
            payload["decisions"], list
        ):
            raise ValueError("unsupported schema")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReferenceVerificationError(
            "Invalid reference resolution catalog: {}".format(path)
        ) from exc

    selected: Dict[Identity, Dict[str, object]] = {}
    for item in payload["decisions"]:
        try:
            scopes = item["scopes"]
            kind = item["kind"]
            value = item["value"]
            action = item["action"]
            reason = item["reason"]
            evidence = item["evidence"]
        except (KeyError, TypeError) as exc:
            raise ReferenceVerificationError(
                "Incomplete reference resolution: {!r}".format(item)
            ) from exc
        if not isinstance(scopes, list) or not all(
            isinstance(entry, str) for entry in scopes
        ):
            raise ReferenceVerificationError(
                "Resolution scopes must be strings: {!r}".format(item)
            )
        unknown_scopes = sorted(set(scopes).difference(ALLOWED_SCOPES))
        if unknown_scopes:
            raise ReferenceVerificationError(
                "Unknown resolution scopes {}: {!r}".format(
                    unknown_scopes, item
                )
            )
        if kind not in {"domain", "full"} or not isinstance(value, str):
            raise ReferenceVerificationError(
                "Invalid resolution identity: {!r}".format(item)
            )
        if action not in ALLOWED_ACTIONS:
            raise ReferenceVerificationError(
                "Invalid resolution action: {!r}".format(item)
            )
        if not isinstance(reason, str) or not reason:
            raise ReferenceVerificationError(
                "Resolution reason is required: {!r}".format(item)
            )
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(url, str) and url.startswith("https://") for url in evidence
        ):
            raise ReferenceVerificationError(
                "Resolution evidence must use HTTPS: {!r}".format(item)
            )
        target = item.get("target")
        if target is not None and not isinstance(target, str):
            raise ReferenceVerificationError(
                "Resolution target must be a string: {!r}".format(item)
            )
        if scope not in scopes:
            continue
        key = (kind, value)
        if key in selected:
            raise ReferenceVerificationError(
                "Duplicate resolution for {} in {}".format(identity_text(key), scope)
            )
        selected[key] = {
            "action": action,
            "reason": reason,
            "target": target,
            "evidence": evidence,
        }
    return selected


def verify_reference_sources(
    sources: Mapping[str, Sequence[object]],
    outputs: Mapping[str, Sequence[object]],
    resolutions: Optional[Mapping[Identity, Mapping[str, object]]] = None,
    corroboration_threshold: int = 2,
) -> Dict[str, object]:
    """Resolve reference differences and persist only exceptional decisions."""
    resolutions = resolutions or {}
    references: Dict[Identity, object] = {}
    identity_sources: Dict[Identity, set[str]] = defaultdict(set)
    for source_name, rules in sources.items():
        for rule in rules:
            key = identity(rule)
            references.setdefault(key, rule)
            identity_sources[key].add(source_name)

    output_rules = {
        target: list({identity(rule): rule for rule in rules}.values())
        for target, rules in outputs.items()
    }
    counts: Counter = Counter()
    by_target: Counter = Counter()
    manual: List[Dict[str, object]] = []
    catalog_decisions: List[Dict[str, object]] = []

    for key in sorted(references):
        rule = references[key]
        source_names = sorted(identity_sources[key])
        covered_target = next(
            (
                target
                for target, rules in output_rules.items()
                if any(covers(candidate, rule) for candidate in rules)
            ),
            None,
        )
        if covered_target is not None:
            counts["covered"] += 1
            by_target[covered_target] += 1
            continue

        resolution = resolutions.get(key)
        if resolution is not None:
            action = str(resolution["action"])
            target = resolution.get("target")
            satisfied = True
            if action == "accept-exact-host":
                satisfied = bool(target) and any(
                    candidate.kind == "full" and candidate.value == rule.value
                    for candidate in output_rules.get(str(target), [])
                )
            if satisfied:
                counts[action] += 1
                catalog_decisions.append(
                    {
                        "identity": identity_text(key),
                        "sources": source_names,
                        "action": action,
                        "target": target,
                        "reason": resolution["reason"],
                        "evidence": list(resolution["evidence"]),
                    }
                )
                continue
            manual.append(
                {
                    "identity": identity_text(key),
                    "sources": source_names,
                    "reason": "catalog-resolution-not-satisfied",
                }
            )
            counts["catalog-resolution-not-satisfied"] += 1
            continue

        if len(source_names) < corroboration_threshold:
            counts["single-reference-only"] += 1
            continue
        counts["corroborated-needs-review"] += 1
        manual.append(
            {
                "identity": identity_text(key),
                "sources": source_names,
                "reason": "corroborated-reference-gap",
            }
        )

    manual_identities = sorted(item["identity"] for item in manual)
    fingerprint = hashlib.sha256(
        "\n".join(manual_identities).encode("utf-8")
    ).hexdigest()
    total = len(references)
    manual_count = len(manual)
    return {
        "schema_version": 1,
        "policy": {
            "formal_source_output_is_not_modified": True,
            "single_reference_only_is_auto_resolved": True,
            "corroboration_threshold": corroboration_threshold,
            "network_observations_are_not_a_hard_gate": True,
        },
        "reference_rule_count": total,
        "auto_resolved_count": total - manual_count,
        "manual_review_count": manual_count,
        "manual_review_fingerprint": fingerprint,
        "decision_counts": dict(sorted(counts.items())),
        "covered_by_output": dict(sorted(by_target.items())),
        "catalog_decisions": catalog_decisions,
        "manual_review": manual,
        "source_rule_counts": {
            name: len({identity(rule) for rule in rules})
            for name, rules in sorted(sources.items())
        },
        "persisted_reference_entries": "catalog-and-manual-exceptions-only",
    }


def summarize_generation_decisions(
    automatic: Mapping[str, int],
    manual_entries: Sequence[Mapping[str, object]],
) -> Dict[str, object]:
    manual = [dict(item) for item in manual_entries]
    manual_identities = sorted(str(item["identity"]) for item in manual)
    fingerprint = hashlib.sha256(
        "\n".join(manual_identities).encode("utf-8")
    ).hexdigest()
    automatic_count = sum(int(value) for value in automatic.values())
    return {
        "schema_version": 1,
        "automatic_decision_counts": {
            key: int(value) for key, value in sorted(automatic.items())
        },
        "auto_resolved_count": automatic_count,
        "manual_review_count": len(manual),
        "manual_review_fingerprint": fingerprint,
        "manual_review": manual,
        "policy": {
            "only_unresolved_candidates_require_manual_review": True,
        },
    }
