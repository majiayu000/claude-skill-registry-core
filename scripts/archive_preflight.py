"""Fail-closed canonical archive path preflight shared by pipeline phases."""

from __future__ import annotations

import os
from pathlib import Path

from portable_paths import is_safe_portable_relative_path


def iter_canonical_archive_paths(root: str | Path):
    """Yield portable <category>/<skill> paths with an exact regular SKILL.md."""
    root_path = Path(root)
    if root_path.is_symlink():
        raise ValueError(f"archive root must not be a symbolic link: {root}")
    archive_root = root_path.resolve()

    def raise_walk_error(error: OSError) -> None:
        raise ValueError(f"unable to inspect archive tree {root}: {error}") from error

    for dirpath, dirnames, filenames in os.walk(root, onerror=raise_walk_error):
        for dirname in dirnames:
            candidate = Path(dirpath, dirname)
            if candidate.is_symlink():
                relative = candidate.relative_to(root_path).as_posix()
                raise ValueError(
                    "symbolic link is not allowed in archive tree; "
                    f"canonical archive path cannot be a symlink: {relative}"
                )
        if ".git" in dirnames:
            dirnames.remove(".git")
        try:
            relative = Path(dirpath).resolve().relative_to(archive_root)
        except ValueError:
            continue
        if len(relative.parts) != 2:
            continue
        skill_variants = [name for name in filenames if name.casefold() == "skill.md"]
        if len(skill_variants) > 1:
            rendered = ", ".join(sorted(skill_variants))
            raise ValueError(
                f"canonical archive contains case-conflicting SKILL.md files: "
                f"{relative} ({rendered})"
            )
        if skill_variants and skill_variants[0] != "SKILL.md":
            raise ValueError(
                f"canonical SKILL.md has invalid casing: {relative / skill_variants[0]}"
            )
        if "SKILL.md" not in filenames:
            continue
        skill_path = Path(dirpath, "SKILL.md")
        if skill_path.is_symlink() or not skill_path.is_file():
            raise ValueError(
                "canonical SKILL.md must be a regular file "
                f"(regular non-symlink file required): {relative / 'SKILL.md'}"
            )
        metadata_variants = [
            name for name in filenames if name.casefold() == "metadata.json"
        ]
        if len(metadata_variants) > 1:
            raise ValueError(
                f"canonical archive contains case-conflicting metadata.json files: {relative}"
            )
        if metadata_variants and metadata_variants[0] != "metadata.json":
            raise ValueError(
                f"canonical metadata.json has invalid casing: "
                f"{relative / metadata_variants[0]}"
            )
        relative_path = relative.as_posix()
        if not is_safe_portable_relative_path(relative_path):
            raise ValueError(f"non-portable canonical archive path: {relative_path}")
        yield relative_path
