#!/usr/bin/env python3
"""Validate the structure and basic contracts of this skill repository."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DIRS = ["agents", "assets", "references", "evals", "scripts"]
TECHNIQUES = [
    "acceptance-criteria-to-test-cases",
    "boundary-value-analysis",
    "classification-tree-nwise",
    "decision-table",
    "equivalence-partitioning",
    "state-transition",
    "use-case-testing",
]
NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
LOCAL_REF_PATTERN = re.compile(r"(?<![\w/])\.\.?/[A-Za-z0-9._/\-]+")
ROOT_VERSION_PATTERN = re.compile(r"\*\*Version:\*\*\s*(\d+\.\d+\.\d+)")
CHANGELOG_VERSION_PATTERN = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)
README_BADGE_PATTERN = re.compile(r"badge/version-(\d+\.\d+\.\d+)-")
EXPECTED_AUTHOR = "jovd83"
FRONTMATTER_CHAR_LIMIT = 1000


def parse_frontmatter(path: Path, errors: list[str]) -> tuple[dict, str, str] | None:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        errors.append(f"{path}: missing or invalid YAML frontmatter")
        return None
    try:
        fields = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        errors.append(f"{path}: invalid YAML frontmatter: {exc}")
        return None
    if not isinstance(fields, dict):
        errors.append(f"{path}: frontmatter must be a YAML object")
        return None
    return fields, match.group(1), text


def check_name_and_description(
    path: Path, fields: dict, expected_name: str, errors: list[str]
) -> None:
    unexpected = set(fields) - {"name", "description", "metadata"}
    if unexpected:
        errors.append(f"{path}: unexpected frontmatter keys {sorted(unexpected)}")
    name = fields.get("name", "")
    description = fields.get("description", "")
    if not NAME_PATTERN.fullmatch(str(name)):
        errors.append(f"{path}: invalid skill name {name!r}")
    if name != expected_name:
        errors.append(f"{path}: name {name!r} must match folder {expected_name!r}")
    if not description or len(description) > 1024:
        errors.append(f"{path}: description must be between 1 and 1024 characters")
    if not isinstance(fields.get("metadata"), dict):
        errors.append(f"{path}: metadata block is required")


def check_local_references(path: Path, text: str, errors: list[str]) -> None:
    """Every ./ or ../ reference must resolve relative to the file that contains it."""
    for raw_ref in LOCAL_REF_PATTERN.findall(text):
        ref = raw_ref.rstrip("`.,)")
        if not (path.parent / ref).resolve().exists():
            errors.append(f"{path}: missing referenced path {ref}")


def root_version(errors: list[str]) -> str | None:
    text = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
    match = ROOT_VERSION_PATTERN.search(text)
    if not match:
        errors.append("SKILL.md: body must state '**Version:** X.Y.Z'")
        return None
    version = match.group(1)

    changelog = CHANGELOG_VERSION_PATTERN.search(
        (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    )
    if not changelog or changelog.group(1) != version:
        errors.append(f"CHANGELOG.md: latest entry must be [{version}]")
    badge = README_BADGE_PATTERN.search(
        (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    )
    if not badge or badge.group(1) != version:
        errors.append(f"README.md: version badge must show {version}")
    return version


def validate_root(errors: list[str]) -> None:
    path = REPO_ROOT / "SKILL.md"
    parsed = parse_frontmatter(path, errors)
    if parsed is None:
        return
    fields, raw_frontmatter, text = parsed
    check_name_and_description(path, fields, REPO_ROOT.name, errors)
    if len(raw_frontmatter) > FRONTMATTER_CHAR_LIMIT:
        errors.append(
            f"{path}: frontmatter is {len(raw_frontmatter)} chars (limit {FRONTMATTER_CHAR_LIMIT})"
        )
    for technique in TECHNIQUES:
        if f"./{technique}/SKILL.md" not in text:
            errors.append(
                f"{path}: routing table does not reference ./{technique}/SKILL.md"
            )
    check_local_references(path, text, errors)


def validate_technique(technique: str, version: str | None, errors: list[str]) -> None:
    folder = REPO_ROOT / technique
    path = folder / "SKILL.md"
    if not path.exists():
        errors.append(f"{path}: missing")
        return
    parsed = parse_frontmatter(path, errors)
    if parsed is None:
        return
    fields, _, text = parsed
    check_name_and_description(path, fields, technique, errors)
    metadata = fields.get("metadata") or {}
    if metadata.get("author") != EXPECTED_AUTHOR:
        errors.append(f"{path}: metadata.author must be {EXPECTED_AUTHOR!r}")
    if version and str(metadata.get("version")) != version:
        errors.append(f"{path}: metadata.version must be {version!r} (root version)")
    if not (folder / "example.md").exists():
        errors.append(f"{folder}: example.md is required")
    reference = folder / "references" / f"{technique}.md"
    if not reference.exists():
        errors.append(f"{reference}: technique reference is required")
    elif f"./references/{technique}.md" not in text:
        errors.append(f"{path}: must point to ./references/{technique}.md")
    check_local_references(path, text, errors)
    for doc in [folder / "example.md", reference]:
        if doc.exists():
            check_local_references(doc, doc.read_text(encoding="utf-8"), errors)


def validate_openai_yaml(errors: list[str]) -> None:
    path = REPO_ROOT / "agents" / "openai.yaml"
    if not path.exists():
        errors.append(f"{path}: missing")
        return
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        errors.append(f"{path}: invalid YAML: {exc}")
        return
    if not isinstance(data, dict) or not isinstance(data.get("interface"), dict):
        errors.append(f"{path}: interface block is required")
        return
    interface = data["interface"]
    for required in [
        "display_name",
        "short_description",
        "default_prompt",
        "icon_small",
        "icon_large",
        "brand_color",
    ]:
        if required not in interface:
            errors.append(f"{path}: missing interface.{required}")
    for icon_key in ["icon_small", "icon_large"]:
        icon_path = interface.get(icon_key)
        if (
            isinstance(icon_path, str)
            and icon_path.startswith("./")
            and not (REPO_ROOT / icon_path[2:]).exists()
        ):
            errors.append(f"{path}: missing referenced icon {icon_path}")


def validate_json_fixture(
    path: Path, required_keys: set[str], errors: list[str]
) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: unreadable JSON: {exc}")
        return
    if not isinstance(data, list) or not data:
        errors.append(f"{path}: fixture must be a non-empty JSON list")
        return
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            errors.append(f"{path}: item {index} must be an object")
            continue
        missing = required_keys - set(item)
        if missing:
            errors.append(f"{path}: item {index} missing keys {sorted(missing)}")


def main() -> int:
    errors: list[str] = []
    for relative_dir in REQUIRED_DIRS:
        if not (REPO_ROOT / relative_dir).is_dir():
            errors.append(f"{REPO_ROOT / relative_dir}: required directory is missing")

    version = root_version(errors)
    validate_root(errors)
    for technique in TECHNIQUES:
        validate_technique(technique, version, errors)

    validate_openai_yaml(errors)
    validate_json_fixture(
        REPO_ROOT / "evals" / "trigger-queries.json",
        {"query", "should_trigger"},
        errors,
    )
    validate_json_fixture(
        REPO_ROOT / "evals" / "technique-selection-cases.json",
        {"id", "scenario", "expected_primary"},
        errors,
    )

    if errors:
        print("Repository validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Repository validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
