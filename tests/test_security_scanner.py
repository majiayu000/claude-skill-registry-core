import importlib.util
from pathlib import Path


def load_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "security_scanner.py"
    spec = importlib.util.spec_from_file_location("security_scanner_module", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_scanner_checks_reference_implementations(tmp_path):
    module = load_module()
    skill_dir = tmp_path / "demo"
    references_dir = skill_dir / "references"
    references_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: demo
description: Demo skill used to verify bundled reference scanning.
---

# Demo
""",
        encoding="utf-8",
    )
    (references_dir / "helper.py").write_text(
        "import subprocess\nsubprocess.run('echo unsafe', shell=True)\n",
        encoding="utf-8",
    )

    scanner = module.SecurityScanner()
    is_safe, issues = scanner.scan_file(skill_dir / "SKILL.md")

    assert is_safe is False
    assert any(
        issue.get("type") == "dangerous_pattern"
        and issue.get("pattern") == "shell=True"
        and "references/helper.py" in issue.get("file", "")
        for issue in issues
    )


def test_scanner_checks_all_archived_support_dirs(tmp_path):
    module = load_module()
    skill_dir = tmp_path / "demo"
    examples_dir = skill_dir / "examples"
    templates_dir = skill_dir / "templates"
    assets_dir = skill_dir / "assets"
    examples_dir.mkdir(parents=True)
    templates_dir.mkdir()
    assets_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: demo
description: Demo skill used to verify bundled support dir scanning.
---

# Demo
""",
        encoding="utf-8",
    )
    (examples_dir / "install.sh").write_text(
        "python -c \"eval('unsafe')\"\n",
        encoding="utf-8",
    )
    (templates_dir / "postinstall.js").write_text(
        "const child_process = require('child_process');\n",
        encoding="utf-8",
    )
    (assets_dir / "payload.svg").write_text(
        "<svg><script>eval('unsafe')</script></svg>\n",
        encoding="utf-8",
    )

    scanner = module.SecurityScanner()
    is_safe, issues = scanner.scan_file(skill_dir / "SKILL.md")

    assert is_safe is False
    issue_files = {issue.get("file", "") for issue in issues}
    assert any("examples/install.sh" in file for file in issue_files)
    assert any("templates/postinstall.js" in file for file in issue_files)
    assert any("assets/payload.svg" in file for file in issue_files)


def test_scanner_checks_root_package_manifest(tmp_path):
    module = load_module()
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: demo
description: Demo skill used to verify bundled package scanning.
---

# Demo
""",
        encoding="utf-8",
    )
    (skill_dir / "package.json").write_text(
        '{"scripts":{"postinstall":"node -e \\"eval(process.env.PAYLOAD)\\""}}',
        encoding="utf-8",
    )

    scanner = module.SecurityScanner()
    is_safe, issues = scanner.scan_file(skill_dir / "SKILL.md")

    assert is_safe is False
    assert any(
        issue.get("type") == "dangerous_pattern"
        and issue.get("pattern") == "eval"
        and "package.json" in issue.get("file", "")
        for issue in issues
    )
