#!/usr/bin/env python3
"""Restore a frozen set of GitHub skill URLs with non-installable pointers."""

from __future__ import annotations

import argparse
import re
import shlex
from pathlib import Path, PurePosixPath
from urllib.parse import quote

PATH_RE = re.compile(r"skills/[^/]+/(?:[^/]+/)*SKILL\.md")


def load_paths(paths_file: Path, output_root: Path) -> list[str]:
    """Validate the complete input before creating any files."""
    paths = paths_file.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    skills_root = output_root.resolve() / "skills"
    for number, path in enumerate(paths, 1):
        parts = path.split("/")
        if (
            not PATH_RE.fullmatch(path)
            or any(part in (".", "..") for part in parts)
            or "\0" in path
        ):
            raise ValueError(f"Invalid legacy path on line {number}: {path!r}")
        key = path.casefold()
        if key in seen:
            raise ValueError(f"Duplicate or case-colliding legacy path on line {number}: {path!r}")
        seen.add(key)
        if not (output_root / path).resolve().is_relative_to(skills_root):
            raise ValueError(f"Legacy target escapes output skills directory: {path!r}")
    if not (skills_root / "README.md").resolve().is_relative_to(skills_root):
        raise ValueError("README target escapes output skills directory")
    return paths


def pointer_content(path: str, data_sha: str) -> str:
    relative = path.removeprefix("skills/")
    name = PurePosixPath(path).parent.name
    return (
        f"# {name}\n\n"
        "This page used to hold an archived copy of this skill. "
        "The archive now lives in the registry's data repository.\n\n"
        "- [Archived SKILL.md (snapshot from 2026-10-08)]"
        f"(https://github.com/majiayu000/claude-skill-registry-data/blob/{data_sha}/{quote(relative)})\n"
        "- [Find this skill in Claude Skills Registry]"
        f"(https://majiayu000.github.io/claude-skill-registry/?q={quote(name, safe='')})\n"
        "- [Request removal](https://github.com/majiayu000/claude-skill-registry-core/issues/new?template=removal-request.yml)\n\n"
        "This pointer keeps old links working. It is not an installable skill.\n"
    )


def readme_content(data_sha: str, count: int) -> str:
    return (
        "# Legacy skill URL pointers\n\n"
        f"This frozen set of {count:,} SKILL.md pointers restores GitHub URLs from the "
        "last archive mirror on 2026-10-08. These files have no YAML frontmatter "
        "and are not installable skills. New skills receive no pointers.\n\n"
        f"Source data commit: `{data_sha}`. Each pointer links to that immutable snapshot, "
        "registry search, and the removal request form. Pointer contents depend only on "
        "their legacy path and this fixed snapshot; do not rewrite them during publishes.\n\n"
        "To regenerate in a separate output directory, run from the core checkout "
        "with the original frozen path list (one skills/.../SKILL.md per line):\n\n"
        "```sh\n"
        ".venv/bin/python scripts/write_legacy_skill_pointers.py "
        f"--paths-file <legacy-paths.txt> --data-sha {data_sha} --output-root <main-checkout>\n"
        "```\n\n"
        "Add `--check` to verify every pointer and this README byte-for-byte without writing. "
        "Matching files are left untouched; mismatched existing files cause an error.\n\n"
        "For the one-time restoration, stage with `git add -f --sparse skills`, then run "
        "`git write-tree --prefix=skills/` in main. Pin that tree ID as "
        "`LEGACY_SKILL_POINTERS_TREE` in `.github/workflows/publish-from-core.yml`. "
        "Publishes exclude skills/ from checkout and preserve its index entries.\n"
    )


def write_pointers(paths_file: Path, data_sha: str, output_root: Path, *, check: bool) -> int:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", data_sha):
        raise ValueError("data-sha must be a full 40-hex commit SHA")
    output_root = output_root.resolve()
    paths = load_paths(paths_file, output_root)
    for path in [*paths, "skills/README.md"]:
        content = (
            readme_content(data_sha, len(paths))
            if path == "skills/README.md" else pointer_content(path, data_sha)
        ).encode("utf-8")
        target = output_root / path
        if target.exists():
            if target.read_bytes() != content:
                raise ValueError(f"Existing file differs from expected pointer: {path}")
        elif check:
            raise ValueError(f"Missing pointer file: {path}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    print(f"{'Checked' if check else 'Generated'} {len(paths):,} pointers and skills/README.md")
    command = f"git -C {shlex.quote(str(output_root))}"
    print(f"Tree expectation: run `{command} add -f --sparse skills`, then "
          f"`{command} write-tree --prefix=skills/`; pin the result as LEGACY_SKILL_POINTERS_TREE.")
    return len(paths)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths-file", type=Path, required=True)
    parser.add_argument("--data-sha", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Verify exact bytes without writing")
    args = parser.parse_args()
    try:
        write_pointers(args.paths_file, args.data_sha, args.output_root, check=args.check)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
