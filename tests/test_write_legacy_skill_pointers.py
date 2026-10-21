import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from write_legacy_skill_pointers import main, pointer_content, write_pointers  # noqa: E402

SHA = "a21980a5b576646393ed574f158d7dd92fae4c3f"


def test_pointer_exact_content():
    assert pointer_content("skills/design/example/SKILL.md", SHA) == f"""# example

This page used to hold an archived copy of this skill. The archive now lives in the registry's data repository.

- [Archived SKILL.md (snapshot from 2026-10-08)](https://github.com/majiayu000/claude-skill-registry-data/blob/{SHA}/design/example/SKILL.md)
- [Find this skill in Claude Skills Registry](https://majiayu000.github.io/claude-skill-registry/?q=example)
- [Request removal](https://github.com/majiayu000/claude-skill-registry-core/issues/new?template=removal-request.yml)

This pointer keeps old links working. It is not an installable skill.
"""


def test_pointer_encodes_url_parts():
    content = pointer_content("skills/odd category/nested/空 格#?/SKILL.md", SHA)
    assert content.startswith("# 空 格#?\n\n")
    assert f"/blob/{SHA}/odd%20category/nested/%E7%A9%BA%20%E6%A0%BC%23%3F/SKILL.md)" in content
    assert "?q=%E7%A9%BA%20%E6%A0%BC%23%3F)" in content
    assert not content.startswith("---")


@pytest.mark.parametrize("paths", [
    "skills/design/../SKILL.md\n",
    "skills/../outside/SKILL.md\n",
    "/skills/design/example/SKILL.md\n",
    "skills/design/./example/SKILL.md\n",
    "skills/design/example/skill.md\n",
    "skills/design/example/SKILL.md.bak\n",
    "skills//example/SKILL.md\n",
    "skills/design/example/SKILL.md\nskills/design/example/SKILL.md\n",
    "skills/design/example/SKILL.md\nskills/DESIGN/EXAMPLE/SKILL.md\n",
    "skills/design/example/SKILL.md\n\n",
    "skills/design/nu\0l/SKILL.md\n",
])
def test_invalid_paths_fail_before_writing(tmp_path, paths):
    paths_file = tmp_path / "paths.txt"
    paths_file.write_text("skills/design/valid/SKILL.md\n" + paths, encoding="utf-8")
    output = tmp_path / "main"
    with pytest.raises(ValueError, match="Invalid|Duplicate"):
        write_pointers(paths_file, SHA, output, check=False)
    assert not output.exists()


@pytest.mark.parametrize("symlink", ["skills", "skills/design", "skills/README.md"])
def test_symlink_escape_fails(tmp_path, symlink):
    paths_file = tmp_path / "paths.txt"
    paths_file.write_text("skills/design/example/SKILL.md\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    output = tmp_path / "main"
    target = output / symlink
    target.parent.mkdir(parents=True)
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        write_pointers(paths_file, SHA, output, check=False)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("sha", ["abc", "g" * 40, "a" * 41, "a" * 40 + "\n"])
def test_invalid_sha(tmp_path, sha):
    with pytest.raises(ValueError, match="40-hex"):
        write_pointers(tmp_path / "unused", sha, tmp_path / "main", check=False)


def run_generator(paths_file, output, *extra):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/write_legacy_skill_pointers.py"),
         "--paths-file", str(paths_file), "--data-sha", SHA,
         "--output-root", str(output), *extra],
        capture_output=True, text=True, check=False,
    )


def test_cli_check_and_idempotence(tmp_path):
    paths_file = tmp_path / "paths.txt"
    paths_file.write_text("skills/design/example/SKILL.md\n", encoding="utf-8")
    output = tmp_path / "main"
    missing = run_generator(paths_file, output, "--check")
    assert missing.returncode == 1
    assert "Missing pointer file" in missing.stderr
    assert not output.exists()

    generated = run_generator(paths_file, output)
    assert generated.returncode == 0, generated.stderr
    assert "LEGACY_SKILL_POINTERS_TREE" in generated.stdout
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in output.rglob("*.md")}
    assert len(before) == 2
    readme = (output / "skills/README.md").read_text()
    assert SHA in readme and "1 SKILL.md pointers" in readme and "--check" in readme
    assert run_generator(paths_file, output).returncode == 0
    assert run_generator(paths_file, output, "--check").returncode == 0
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}

    pointer = output / "skills/design/example/SKILL.md"
    pointer.write_text("changed\n")
    for extra in [(), ("--check",)]:
        mismatch = run_generator(paths_file, output, *extra)
        assert mismatch.returncode == 1
        assert "differs" in mismatch.stderr
        assert pointer.read_text() == "changed\n"


def test_check_includes_readme(tmp_path):
    paths_file = tmp_path / "paths.txt"
    paths_file.write_text("skills/design/example/SKILL.md\n", encoding="utf-8")
    output = tmp_path / "main"
    write_pointers(paths_file, SHA, output, check=False)
    (output / "skills/README.md").unlink()
    with pytest.raises(ValueError, match="Missing pointer file: skills/README.md"):
        write_pointers(paths_file, SHA, output, check=True)


def test_main_reports_success_and_mismatch_in_process(tmp_path, monkeypatch, capsys):
    paths_file = tmp_path / "paths.txt"
    paths_file.write_text("skills/design/example/SKILL.md\n", encoding="utf-8")
    output = tmp_path / "main"
    argv = ["write_legacy_skill_pointers.py", "--paths-file", str(paths_file), "--data-sha", SHA, "--output-root", str(output)]
    monkeypatch.setattr(sys, "argv", argv)
    assert main() == 0
    assert "Generated 1 pointers" in capsys.readouterr().out

    (output / "skills/design/example/SKILL.md").write_text("changed\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [*argv, "--check"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "Existing file differs from expected pointer: skills/design/example/SKILL.md" in capsys.readouterr().err
