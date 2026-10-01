import re
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_static_skill_pages import (  # noqa: E402
    PUBLIC_SITE,
    build_static_skill_pages,
    group_skill_copies,
    select_featured_skills,
    skill_page_slug,
)


def skill(name: str, **overrides):
    record = {
        "id": f"stable-{name}",
        "name": name,
        "description": f"Use {name} to complete a focused workflow.",
        "repo": "acme/skills",
        "path": f"skills/{name}",
        "branch": "main",
        "category": "development",
        "tags": ["testing", "agent"],
        "stars": 10,
        "install": f"acme/skills/skills/{name}",
        "quality_grade": "A",
        "quality_score": 75,
        "security_status": "passed",
        "install_status": "known_good",
    }
    record.update(overrides)
    return record


def homepage_template() -> str:
    return """<!doctype html>
<html><body>
<!-- static-featured:start -->
<p>Generated during the index build.</p>
<!-- static-featured:end -->
</body></html>
"""


def test_selection_is_bounded_and_excludes_unsafe_or_uninstallable_skills():
    records = [skill(f"safe-{index}", stars=index) for index in range(25)]
    records.extend(
        [
            skill("unsafe", security_status="failed", stars=1000),
            skill("unknown", security_status="unknown", stars=999),
            skill("broken", install_status="broken", stars=998),
            skill("no-repo", repo="", stars=997),
        ]
    )

    selected = select_featured_skills(records)

    assert len(selected) == 25
    assert [item["name"] for item in selected] == [
        f"safe-{index}" for index in range(24, -1, -1)
    ]


def test_slug_is_stable_readable_and_collision_safe():
    first = skill("C++ Review", id="stable-one")
    second = skill("C++ Review", id="stable-two")

    assert skill_page_slug(first).startswith("c-review-")
    assert skill_page_slug(first) == skill_page_slug(dict(first))
    assert skill_page_slug(second) != skill_page_slug(first)


def test_generator_escapes_metadata_and_writes_homepage_links_and_sitemap(tmp_path):
    output_dir = tmp_path / "docs"
    output_dir.mkdir()
    (output_dir / "index.html").write_text(homepage_template(), encoding="utf-8")
    record = skill(
        "unsafe-looking <name>",
        id="escape-id",
        description='A <script>alert("x")</script> description.',
        tags=["<tag>", "safe"],
    )

    summary = build_static_skill_pages([record], output_dir)

    slug = skill_page_slug(record)
    detail_path = output_dir / "skills" / slug / "index.html"
    detail = detail_path.read_text(encoding="utf-8")
    homepage = (output_dir / "index.html").read_text(encoding="utf-8")
    sitemap = ElementTree.parse(output_dir / "sitemap.xml")
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locations = [
        node.text for node in sitemap.findall("sm:url/sm:loc", namespace)
    ]

    assert summary == {"generated_count": 1, "slugs": [slug]}
    assert "<script>" not in detail
    assert "&lt;script&gt;" in detail
    assert "&lt;tag&gt;" in detail
    assert f'href="skills/{slug}/"' in homepage
    assert locations == [PUBLIC_SITE, f"{PUBLIC_SITE}choose-a-skill.html", f"{PUBLIC_SITE}skills/", f"{PUBLIC_SITE}skills/{slug}/"]
    assert re.search(r'<link rel="canonical" href="[^\"]+/">', detail)


def test_generator_removes_only_stale_generated_skill_pages(tmp_path):
    output_dir = tmp_path / "docs"
    stale = output_dir / "skills" / "old-skill" / "index.html"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale", encoding="utf-8")
    (output_dir / "skills" / ".generated-static-skill-pages").write_text(
        "generated\n", encoding="utf-8"
    )
    keep = output_dir / "manual.txt"
    keep.write_text("keep", encoding="utf-8")

    build_static_skill_pages([skill("current")], output_dir)

    assert not stale.exists()
    assert keep.read_text(encoding="utf-8") == "keep"


def test_generator_includes_code_review_guide_in_regenerated_sitemap(tmp_path):
    output_dir = tmp_path / "docs"
    guide_path = output_dir / "guides" / "code-review.html"
    guide_path.parent.mkdir(parents=True)
    guide = (ROOT / "docs" / "guides" / "code-review.html").read_bytes()
    guide_path.write_bytes(guide)
    guide_url = f"{PUBLIC_SITE}guides/code-review.html"
    (output_dir / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{guide_url}</loc></url></urlset>",
        encoding="utf-8",
    )
    record = skill("current")

    for _ in range(2):
        build_static_skill_pages([record], output_dir)

        sitemap = ElementTree.parse(output_dir / "sitemap.xml")
        namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locations = [node.text for node in sitemap.findall("sm:url/sm:loc", namespace)]
        assert locations == [
            PUBLIC_SITE,
            f"{PUBLIC_SITE}choose-a-skill.html",
            f"{PUBLIC_SITE}skills/",
            f"{PUBLIC_SITE}skills/{skill_page_slug(record)}/",
            guide_url,
        ]
        assert guide_path.read_bytes() == guide


def test_generator_refuses_to_replace_an_unowned_skills_directory(tmp_path):
    output_dir = tmp_path / "docs"
    manual = output_dir / "skills" / "manual" / "index.html"
    manual.parent.mkdir(parents=True)
    manual.write_text("manual", encoding="utf-8")

    with pytest.raises(ValueError, match="not marked as generated"):
        build_static_skill_pages([skill("current")], output_dir)

    assert manual.read_text(encoding="utf-8") == "manual"


def test_copy_identity_uses_content_not_shared_description():
    first = skill("review", content_fingerprint="a" * 64, repo="acme/one", install="acme/one")
    copy = skill("review", content_fingerprint="a" * 64, repo="acme/two", install="acme/two", stars=200)
    other = skill("review", content_fingerprint="b" * 64, repo="acme/three", install="acme/three")
    groups = group_skill_copies([first, copy, other])
    assert len(groups) == 2
    copied = next(group for group in groups if group["repository_count"] == 2)
    assert {item["repo"] for item in copied["copies"]} == {"acme/one", "acme/two"}
    changed = group_skill_copies([dict(first, stars=1000), copy])[0]
    assert changed["page_slug"] == copied["page_slug"]
    assert group_skill_copies([copy, first, other]) == groups


def test_selection_quality_and_first_wave_limit():
    records = [skill(f"safe-{i}", id=f"id-{i}", quality_score=80) for i in range(5810)]
    records += [skill("thin", description="Short"), skill("poor", quality_score=60)]
    selected = select_featured_skills(records)
    assert len(selected) == 5800
    assert not {"thin", "poor"}.intersection(record["name"] for record in selected)


def test_page_attribution_related_guides_and_archive_entrypoints(tmp_path):
    docs = tmp_path / "docs"
    archive = tmp_path / "archive"
    (archive / "development").mkdir(parents=True)
    records = group_skill_copies([skill("one", license="MIT", author="Acme"), skill("two")])
    build_static_skill_pages(records, docs, archive)
    detail = (docs / "skills" / records[0]["page_slug"] / "index.html").read_text()
    assert "MIT" in detail and "Acme" in detail
    assert "Trust score" in detail
    assert "Copies with matching content" in detail
    assert "removal-request.yml" in detail
    assert "sk install" in detail
    assert 'class="related"' in detail
    assert (docs / "skills" / "index.html").is_file()
    assert PUBLIC_SITE in (archive / "development" / "README.md").read_text()


def test_duplicate_slugs_do_not_destroy_previous_output(tmp_path):
    docs = tmp_path / "docs"
    build_static_skill_pages([skill("keep")], docs)
    old = docs / "skills" / skill_page_slug(skill("keep")) / "index.html"
    with pytest.raises(ValueError, match="Duplicate"):
        build_static_skill_pages([skill("bad"), skill("bad")], docs)
    assert old.is_file()


def test_empty_catalog_and_invalid_homepage(tmp_path):
    docs = tmp_path / "docs"
    assert build_static_skill_pages([], docs)["generated_count"] == 0
    assert "Browse skill guides" in (docs / "skills" / "index.html").read_text()
    (docs / "index.html").write_text("manual homepage")
    with pytest.raises(ValueError, match="static featured block"):
        build_static_skill_pages([skill("one")], docs)


def test_cli_uses_catalog_and_rejects_invalid_payload(tmp_path, monkeypatch, capsys):
    import json

    from build_static_skill_pages import main

    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"skills": group_skill_copies([skill("cli")])}))
    monkeypatch.setattr(sys, "argv", ["build-pages", "--catalog", str(catalog), "--output", str(tmp_path / "docs")])
    assert main() == 0
    assert "Generated 1" in capsys.readouterr().out
    catalog.write_text('{"skills":null}')
    with pytest.raises(ValueError, match="Catalog artifact"):
        main()


def test_install_command_quotes_source_arguments(tmp_path):
    import html
    import shlex
    record = skill("quoted", path="skills/a space; touch /tmp/unsafe", branch="feat/space $(id)")
    build_static_skill_pages([record], tmp_path)
    detail = (tmp_path / "skills" / skill_page_slug(record) / "index.html").read_text()
    command = html.unescape(re.search(r"<code>(.*?)</code>", detail).group(1))
    args = shlex.split(command)
    assert args[:2] == ["sk", "install"]
    assert len(args) == 3
    assert args[2] == "https://github.com/acme/skills/tree/feat%2Fspace%20%24%28id%29/skills/a%20space%3B%20touch%20/tmp/unsafe"
    assert "--branch" not in command


def test_unknown_license_is_explained_and_copy_paths_are_distinct(tmp_path):
    first = skill("copies", repo="acme/repo", install="acme/repo/skills/copies", content_fingerprint="x", license="NOASSERTION")
    second = skill("copies", repo="acme/repo", install="acme/repo/.claude/skills/copies", content_fingerprint="x", license="NOASSERTION")
    records = group_skill_copies([first, second])
    build_static_skill_pages(records, tmp_path)
    detail = (tmp_path / "skills" / records[0]["page_slug"] / "index.html").read_text()
    assert "NOASSERTION" not in detail
    assert "Not declared in registry metadata" in detail
    assert "acme/repo/skills/copies" in detail
    assert "acme/repo/.claude/skills/copies" in detail
