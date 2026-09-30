#!/usr/bin/env python3
"""Generate crawlable skill guides from the full, content-deduplicated catalog."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shlex
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

PUBLIC_SITE = "https://majiayu000.github.io/claude-skill-registry/"
STATIC_PAGE_LIMIT = 5800
FEATURED_START = "<!-- static-featured:start -->"
FEATURED_END = "<!-- static-featured:end -->"
GENERATED_MARKER = ".generated-static-skill-pages"
REMOVAL_URL = "https://github.com/majiayu000/claude-skill-registry-core/issues/new?template=removal-request.yml"


def group_skill_copies(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group exact Markdown-body copies; a shared description is not identity."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        name = str(record.get("name") or "").strip().casefold()
        fingerprint = record.get("content_fingerprint")
        identity = f"{name}|{fingerprint}" if fingerprint else f"{record.get('install')}|{record.get('branch', 'main')}"
        groups[identity].append(record)
    grouped = []
    for identity, copies in sorted(groups.items()):
        # Choose a documented, scanned copy. This ranking does not establish
        # original authorship; every matching source remains attributed.
        representative = max(copies, key=lambda item: (
            item.get("security_status") == "passed",
            item.get("install_status") == "known_good",
            bool(item.get("license") and item.get("license") != "NOASSERTION"),
            int(item.get("quality_score", 0) or 0),
            int(item.get("stars", 0) or 0),
            str(item.get("install") or ""),
        ))
        record = dict(representative)
        record["copies"] = sorted(
            [{key: copy.get(key, "") for key in ("repo", "install", "path", "branch", "name", "archive_path")} for copy in copies],
            key=lambda item: (item["repo"], item["install"], item["branch"]),
        )
        record["repository_count"] = len({copy.get("repo") for copy in copies if copy.get("repo")})
        record["page_identity"] = identity
        record["page_slug"] = skill_page_slug(record)
        grouped.append(record)
    return grouped


def select_featured_skills(
    records: Iterable[dict[str, Any]], limit: int = STATIC_PAGE_LIMIT
) -> list[dict[str, Any]]:
    """Select first-wave guides with scan evidence and substantive descriptions."""
    eligible = [record for record in records if (
        record.get("security_status") == "passed"
        and record.get("install_status") == "known_good"
        and "/" in str(record.get("repo") or "")
        and record.get("name")
        and len(str(record.get("description") or "").strip()) >= 30
        and int(record.get("quality_score", 0) or 0) >= 70
    )]
    return sorted(eligible, key=lambda record: (
        -int(record.get("repository_count", 1) or 1),
        -int(record.get("quality_score", 0) or 0),
        -int(record.get("stars", 0) or 0),
        skill_page_slug(record),
    ))[:limit]


def skill_page_slug(record: dict[str, Any]) -> str:
    """Build a readable slug that stays stable when a copy's rank changes."""
    base = re.sub(r"[^a-z0-9]+", "-", str(record.get("name") or "").casefold()).strip("-") or "skill"
    identity = str(record.get("page_identity") or record.get("id") or f"{record.get('install', '')}|{record.get('branch', 'main')}")
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"{base[:90]}-{suffix}"


def _source_url(record: dict[str, Any]) -> str:
    repo = quote(str(record.get("repo") or ""), safe="/")
    branch = quote(str(record.get("branch") or "main"), safe="")
    path = quote(str(record.get("path") or "").strip("/"), safe="/")
    return f"https://github.com/{repo}/blob/{branch}/{path + '/' if path else ''}SKILL.md"


def _render_detail_page(record: dict[str, Any], slug: str, related: list[dict[str, Any]]) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value), quote=True)

    name = esc(record["name"])
    description = esc(record.get("description") or "No description provided.")
    canonical = f"{PUBLIC_SITE}skills/{slug}/"
    meta_description = esc(" ".join(str(record.get("description") or "").split())[:160])
    repo = esc(record.get("repo") or "")
    tags = "".join(f'<span class="tag">{esc(tag)}</span>' for tag in record.get("tags", []))
    copies = record.get("copies", [record])
    copy_links = "".join(f'<li><a href="{esc(_source_url(copy))}">{esc(copy.get("repo"))}</a></li>' for copy in copies[:10])
    similar = "".join(f'<a class="related" href="../{skill_page_slug(item)}/"><strong>{esc(item["name"])}</strong><span>{esc(item.get("description", ""))[:160]}</span></a>' for item in related)
    license_name = record.get("license") or "Not declared in registry metadata"
    license_notice = "Check the original repository for permission and license terms before reuse. Registry metadata does not grant a license."
    install = str(record.get("install") or record.get("repo") or "")
    command = f"sk install {shlex.quote(install)} --branch {shlex.quote(str(record.get('branch') or 'main'))}"
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{name} — Claude Skills Registry</title>
  <meta name="description" content="{meta_description}">
  <link rel="canonical" href="{canonical}">
  <meta property="og:type" content="article">
  <meta property="og:title" content="{name} — Claude Skills Registry">
  <meta property="og:description" content="{meta_description}">
  <meta property="og:url" content="{canonical}">
  <meta name="twitter:card" content="summary">
  <link rel="stylesheet" href="../../css/skill-detail.css">
</head>
<body>
<header><a class="brand" href="{PUBLIC_SITE}">◈ Skill Registry</a><a href="{PUBLIC_SITE}">Explore skills</a></header>
<main>
  <a class="back" href="{PUBLIC_SITE}">← Back to search</a>
  <div class="layout">
    <aside>
      <div class="terminal">&gt;_</div>
      <h2>Signals</h2>
      <dl><dt>Quality score</dt><dd>{esc(record.get('quality_grade', 'unknown'))} · {esc(record.get('quality_score', 0))}/100</dd>
      <dt>Registry scan</dt><dd>{esc(record.get('security_status', 'unknown'))}</dd>
      <dt>Source path</dt><dd>{esc(record.get('install_status', 'unknown'))}</dd>
      <dt>Trust score</dt><dd>{esc(record.get('trust_score', 0))}/100</dd></dl>
      <h2>Metadata</h2>
      <dl><dt>Category</dt><dd>{esc(record.get('category', 'other'))}</dd>
      <dt>Repository stars</dt><dd>{esc(record.get('stars', 0))}</dd>
      <dt>Author</dt><dd>{esc(record.get('author') or 'See original repository')}</dd>
      <dt>License</dt><dd>{esc(license_name)}</dd></dl>
    </aside>
    <article>
      <div class="kicker">{esc(record.get('category', 'other'))} · Found in {esc(record.get('repository_count', 1))} repositories</div>
      <h1>{name}</h1><p class="intro">{description}</p>
      <div class="tags">{tags}</div>
      <a class="source" href="{esc(_source_url(record))}">View source: {repo} ↗</a>
      <section><h2>Install from source</h2><p>Install using <a href="https://github.com/majiayu000/caude-skill-manager">Skill Manager</a>:</p>
      <code>{esc(command)}</code><p class="note">Source-path status is inferred from metadata; it does not verify a live download. Scan and quality scores describe registry checks and are not a guarantee of safety.</p></section>
      <section><h2>Attribution and permissions</h2><p>{esc(license_notice)}</p><p>This guide links to the author’s instructions and includes metadata only.</p>
      <a href="{REMOVAL_URL}">Request removal or correct attribution</a></section>
      <section><h2>Copies with matching content</h2><p>Exact Markdown body copies across {esc(record.get('repository_count', 1))} repositories. This count does not identify the original author.</p><ul>{copy_links}</ul></section>
      <section><h2>More in {esc(record.get('category', 'other'))}</h2><div class="related-grid">{similar}</div></section>
    </article>
  </div>
</main>
<footer><a href="{PUBLIC_SITE}">Claude Skills Registry</a> · <a href="{REMOVAL_URL}">Removal requests</a></footer>
</body>
</html>
'''


def _render_homepage_links(records: list[dict[str, Any]]) -> str:
    items = "\n".join(f'      <li><a href="skills/{skill_page_slug(record)}/">{html.escape(str(record["name"]))}</a></li>' for record in records[:20])
    return f'''{FEATURED_START}
<section class="featured-static" aria-labelledby="featured-static-heading">
  <h2 id="featured-static-heading">Skill guides</h2>
  <p>{len(records):,} content-deduplicated guides with source links, quality signals, and installation instructions. <a href="skills/">Browse all guides</a>.</p>
  <ul>
{items}
  </ul>
</section>
{FEATURED_END}'''


def _update_homepage(output_dir: Path, records: list[dict[str, Any]]) -> None:
    homepage_path = output_dir / "index.html"
    if not homepage_path.exists():
        return
    homepage = homepage_path.read_text(encoding="utf-8")
    start, end = homepage.find(FEATURED_START), homepage.find(FEATURED_END)
    if start < 0 or end < 0 or end < start:
        raise ValueError("docs/index.html is missing a valid static featured block")
    homepage_path.write_text(homepage[:start] + _render_homepage_links(records) + homepage[end + len(FEATURED_END):], encoding="utf-8")


def _write_sitemap(output_dir: Path, slugs: list[str]) -> None:
    urls = [PUBLIC_SITE, f"{PUBLIC_SITE}skills/", *(f"{PUBLIC_SITE}skills/{slug}/" for slug in slugs)]
    rows = "\n".join(f"  <url><loc>{html.escape(url)}</loc></url>" for url in urls)
    (output_dir / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + rows + '\n</urlset>\n', encoding="utf-8")


def _prepare_generated_root(output_dir: Path) -> Path:
    generated_root = output_dir / "skills"
    if generated_root.exists():
        if any(generated_root.iterdir()) and not (generated_root / GENERATED_MARKER).is_file():
            raise ValueError(f"Refusing to replace {generated_root}: directory is not marked as generated")
        shutil.rmtree(generated_root)
    generated_root.mkdir()
    (generated_root / GENERATED_MARKER).write_text("generated\n", encoding="utf-8")
    return generated_root


def build_static_skill_pages(catalog_records: Iterable[dict[str, Any]], output_dir: Path, archive_dir: Path | None = None) -> dict[str, Any]:
    """Write guides, browse links, category archive entrypoints, and sitemap."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    records = select_featured_skills(catalog_records)
    slugs = [skill_page_slug(record) for record in records]
    if len(slugs) != len(set(slugs)):
        raise ValueError("Duplicate generated skill page slug")
    generated_root = _prepare_generated_root(output_dir)
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        categories[str(record.get("category") or "other")].append(record)
    for record, slug in zip(records, slugs, strict=True):
        detail_dir = generated_root / slug
        detail_dir.mkdir()
        related = [item for item in categories[str(record.get("category") or "other")] if item is not record][:4]
        (detail_dir / "index.html").write_text(_render_detail_page(record, slug, related), encoding="utf-8")
    browse = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Skill guides — Claude Skills Registry</title><link rel="canonical" href="' + PUBLIC_SITE + 'skills/"><link rel="stylesheet" href="../css/skill-detail.css"></head><body><header><a href="' + PUBLIC_SITE + '">← Search skills</a></header><main><h1>Browse skill guides</h1>'
    for category, items in sorted(categories.items()):
        browse += f'<section id="{html.escape(category, quote=True)}"><h2>{html.escape(category)}</h2><ul>'
        browse += ''.join(f'<li><a href="{skill_page_slug(item)}/">{html.escape(str(item["name"]))}</a></li>' for item in items) + '</ul></section>'
    (generated_root / "index.html").write_text(browse + '</main></body></html>', encoding="utf-8")
    if archive_dir is not None:
        for directory in sorted(Path(archive_dir).iterdir()):
            if directory.is_dir() and not directory.name.startswith('.'):
                (directory / "README.md").write_text(f'# {directory.name}\n\nBrowse [skill guides and installation instructions]({PUBLIC_SITE}skills/#{quote(directory.name)}) or [search the full registry]({PUBLIC_SITE}).\n\nArchived files retain their original ownership and license terms. [Request removal]({REMOVAL_URL}).\n', encoding='utf-8')
    _update_homepage(output_dir, records)
    _write_sitemap(output_dir, slugs)
    return {"generated_count": len(records), "slugs": slugs}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    records = payload.get("skills") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise ValueError(f"Catalog artifact has no valid skills list: {args.catalog}")
    summary = build_static_skill_pages(records, args.output, args.archive)
    print(f"Generated {summary['generated_count']} static skill pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
