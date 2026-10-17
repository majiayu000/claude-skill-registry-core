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

from security_rules import SECURITY_CHECK_GROUPS

PUBLIC_SITE = "https://majiayu000.github.io/claude-skill-registry/"
STATIC_PAGE_LIMIT = 5800
FEATURED_START = "<!-- static-featured:start -->"
FEATURED_END = "<!-- static-featured:end -->"
GENERATED_MARKER = ".generated-static-skill-pages"
REMOVAL_URL = "https://github.com/majiayu000/claude-skill-registry-core/issues/new?template=removal-request.yml"
SKILLS_CLI_URL = "https://github.com/vercel-labs/skills"
BADGE_URL = f"{PUBLIC_SITE}assets/listed-badge.svg"
# Global install targets documented by the skills CLI (agent flag, install path).
RUNTIME_TARGETS = (
    ("Claude Code", "claude-code", "~/.claude/skills/"),
    ("Codex", "codex", "~/.agents/skills/"),
    ("Cursor", "cursor", "~/.cursor/skills/"),
)
# Agent Skills well-known discovery index (agentskills.io, discovery schema 0.2.0).
DISCOVERY_SCHEMA = "https://schemas.agentskills.io/discovery/0.2.0/schema.json"
WELL_KNOWN_DIR = Path(".well-known") / "agent-skills"


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
            str(item.get("branch") or "main"),
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


def attach_security_scans(
    records: list[dict[str, Any]], security_records_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Copy each guide's scan evidence summary from its representative decision."""
    for record in records:
        decision = security_records_by_id.get(record["id"], {}).get("security_decision")
        if not decision:
            continue
        record["security_scan"] = {
            "scanner_version": decision["scanner"]["version"],
            "ruleset_sha256": decision["scanner"]["ruleset_sha256"],
            "scanned_at": decision["provenance"]["scanned_at"],
            "content_sha256": decision["provenance"]["content_sha256"],
            **{key: decision[key] for key in ("checks", "finding_types") if key in decision},
        }
    return records


def _render_security_scan(record: dict[str, Any], slug: str) -> str:
    """Explain which scanner checks ran and what they flagged, from recorded evidence."""
    scan = record.get("security_scan")
    required = ("scanner_version", "ruleset_sha256", "scanned_at", "content_sha256", "checks", "finding_types")
    if not isinstance(scan, dict) or any(key not in scan for key in required):
        raise ValueError(f"Skill guide {slug} is missing security scan evidence")
    unknown = [check for check in scan["checks"] if check not in SECURITY_CHECK_GROUPS]
    if unknown:
        raise ValueError(f"Skill guide {slug} has unknown security checks: {unknown}")

    def esc(value: Any) -> str:
        return html.escape(str(value), quote=True)

    checks = "".join(f"<li>{esc(SECURITY_CHECK_GROUPS[check])}</li>" for check in scan["checks"])
    totals: dict[str, int] = defaultdict(int)
    details: dict[str, list[str]] = defaultdict(list)
    for finding, count in sorted(scan["finding_types"].items()):
        severity, _, issue_type = finding.partition(":")
        totals[severity] += int(count)
        details[severity].append(f"{issue_type.replace('_', ' ')} ({count})")
    findings = " ".join(
        f"{total} {severity}{'' if total == 1 else 's'}: {', '.join(details[severity])}."
        for severity, total in sorted(totals.items())
    ) or "Nothing was flagged."
    return f'''<section class="scan-details"><h2>What the registry checked</h2>
      <p>Scanned {esc(str(scan["scanned_at"])[:10])} by registry scanner {esc(scan["scanner_version"])} (ruleset <code>{esc(str(scan["ruleset_sha256"])[:12])}</code>, content <code>{esc(str(scan["content_sha256"])[:12])}</code>).</p>
      <ul>{checks}</ul>
      <p><strong>Findings:</strong> {esc(findings)}</p>
      <p class="note">Checks are pattern-based and cover the archived copy; they do not guarantee the skill is safe.</p></section>'''


def skill_page_slug(record: dict[str, Any]) -> str:
    """Build a readable slug that stays stable when a copy's rank changes."""
    base = re.sub(r"[^a-z0-9]+", "-", str(record.get("name") or "").casefold()).strip("-") or "skill"
    identity = str(record.get("page_identity") or record.get("id") or f"{record.get('install', '')}|{record.get('branch', 'main')}")
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"{base[:90]}-{suffix}"


def _source_url(record: dict[str, Any], *, directory: bool = False) -> str:
    repo = quote(str(record.get("repo") or ""), safe="/")
    branch = quote(str(record.get("branch") or "main"), safe="")
    source_path = str(record.get("path") or "").strip("/")
    if source_path.casefold() == "skill.md":
        source_path = ""
    elif source_path.casefold().endswith("/skill.md"):
        source_path = source_path.rsplit("/", 1)[0]
    path = quote(source_path, safe="/")
    base = f"https://github.com/{repo}/{'tree' if directory else 'blob'}/{branch}"
    if directory:
        return f"{base}/{path}" if path else base
    return f"{base}/{path + '/' if path else ''}SKILL.md"


def _install_source(record: dict[str, Any]) -> str:
    repo = str(record.get("repo") or "")
    path = str(record.get("path") or "").strip("/")
    # These delimiters cannot be represented as subpaths by the skills CLI.
    if not repo or repo.startswith("local/") or any(char in path for char in "#:"):
        return ""
    branch = quote(str(record.get("branch") or "main"), safe="")
    return f"{repo}{'/' + path if path else ''}#{branch}"


def _pinned_skill_url(pin: dict[str, Any]) -> str:
    return f"https://raw.githubusercontent.com/{quote(pin['repo'], safe='/')}/{pin['commit']}/{quote(pin['path'], safe='/')}"


def _current_install_pin(record, pin, archive_source):
    """Publish a cached pin only for the current archived bytes behind this guide."""
    archive_path = record.get("archive_path")
    if not pin or pin.get("status") != "pinned" or archive_source is None or not archive_path:
        return None
    if pin.get("archive_path") != archive_path or pin.get("repo") != record.get("repo"):
        return None
    root = Path(archive_source).resolve()
    source = (root / archive_path).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        return None
    digest = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    return pin if pin.get("digest") == digest else None


def _discovery_index(pin: dict[str, Any]) -> dict[str, Any]:
    """One-skill index pointing at the author's file at the commit that matched the scan."""
    return {"$schema": DISCOVERY_SCHEMA, "skills": [{
        "name": pin["name"], "type": "skill-md", "description": pin["description"],
        "url": _pinned_skill_url(pin), "digest": pin["digest"],
    }]}


def _render_detail_page(record: dict[str, Any], slug: str, related: list[dict[str, Any]], pin: dict[str, Any] | None = None) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value), quote=True)

    name = esc(record["name"])
    description = esc(record.get("description") or "No description provided.")
    canonical = f"{PUBLIC_SITE}skills/{slug}/"
    meta_description = esc(" ".join(str(record.get("description") or "").split())[:160])
    repo = esc(record.get("repo") or "")
    tags = "".join(f'<span class="tag">{esc(tag)}</span>' for tag in record.get("tags", []))
    copies = record.get("copies", [record])
    copy_links = "".join(f'<li><a href="{esc(_source_url(copy))}">{esc(copy.get("install") or copy.get("repo"))}</a> ({esc(copy.get("branch") or "main")})</li>' for copy in copies)
    similar = "".join(f'<a class="related" href="../{skill_page_slug(item)}/"><strong>{esc(item["name"])}</strong><span>{esc(item.get("description", ""))[:160]}</span></a>' for item in related)
    license_name = record.get("license") or "Not declared in registry metadata"
    if license_name == "NOASSERTION":
        license_name = "Not declared in registry metadata"
    license_notice = "Check the original repository for permission and license terms before reuse. Registry metadata does not grant a license."
    scan_details = _render_security_scan(record, slug)
    install_source = _install_source(record)
    source = shlex.quote(install_source)
    command = f"npx skills add {source}"
    runtime_commands = "\n".join(
        f"# {label} → {path}\nnpx skills add {source} -g -a {agent}" for label, agent, path in RUNTIME_TARGETS
    )
    install_details = (
        f'<p>Install with the <a href="{SKILLS_CLI_URL}">skills CLI</a>. It asks which coding agents to install into:</p>'
        f'<code>{esc(command)}</code><p>Or install globally for one agent:</p><code>{esc(runtime_commands)}</code>'
        if install_source else
        '<p>Open the source above and copy the folder containing SKILL.md into your agent’s skills directory.</p>'
    )
    badge_markdown = f"[![Listed on Claude Skills Registry]({BADGE_URL})]({PUBLIC_SITE})"
    if pin:
        install_details = (
            f'<p>Verified install: the <a href="{SKILLS_CLI_URL}">skills CLI</a> reads this guide\'s discovery index and installs SKILL.md '
            f'from <a href="{esc(_pinned_skill_url(pin))}">{esc(pin["repo"])} at commit {esc(pin["commit"][:12])}</a>, '
            f'checking its SHA-256 against the version the registry scanned:</p><code>{esc("npx skills add " + canonical)}</code>'
            '<p>Or install the latest version from source:</p>' + install_details
        )
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
  <meta property="og:image" content="{PUBLIC_SITE}assets/registry-social-preview.png">
  <meta property="og:image:alt" content="Claude Skills Registry — source-linked skill guides">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{name} — Claude Skills Registry">
  <meta name="twitter:description" content="{meta_description}">
  <meta name="twitter:image" content="{PUBLIC_SITE}assets/registry-social-preview.png">
  <meta name="twitter:image:alt" content="Claude Skills Registry — source-linked skill guides">
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
      <dt>Registry scan</dt><dd>{esc(str(record.get('security_status', 'unknown')).replace('_', ' '))}</dd>
      <dt>Source path</dt><dd>{esc(str(record.get('install_status', 'unknown')).replace('_', ' '))}</dd>
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
      <section><h2>Install</h2>{install_details}
      <p class="note">Manual install locations: Claude Code ~/.claude/skills/, Codex ~/.agents/skills/, Cursor ~/.cursor/skills/. Source-path status is inferred from metadata; it does not verify a live download. Scan and quality scores describe registry checks and are not a guarantee of safety.</p></section>
      {scan_details}
      <section><h2>Attribution and permissions</h2><p>{esc(license_notice)}</p><p>This guide links to the author’s instructions and includes metadata only.</p>
      <a href="{REMOVAL_URL}">Request removal or correct attribution</a></section>
      <section><h2>For the skill author</h2><p>Add this badge to your README to link readers to the registry home:</p>
      <p><img src="{BADGE_URL}" alt="Listed on Claude Skills Registry" width="140" height="20"></p>
      <code>{esc(badge_markdown)}</code></section>
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
    urls = [PUBLIC_SITE, f"{PUBLIC_SITE}choose-a-skill.html", f"{PUBLIC_SITE}skills/", *(f"{PUBLIC_SITE}skills/{slug}/" for slug in slugs)]
    if (output_dir / "guides" / "code-review.html").is_file():
        urls.append(f"{PUBLIC_SITE}guides/code-review.html")
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


def build_static_skill_pages(
    catalog_records: Iterable[dict[str, Any]],
    output_dir: Path,
    archive_dir: Path | None = None,
    install_pins: dict[str, dict[str, Any]] | None = None,
    archive_source: Path | None = None,
) -> dict[str, Any]:
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
    pinned_count = 0
    for record, slug in zip(records, slugs, strict=True):
        detail_dir = generated_root / slug
        detail_dir.mkdir()
        related = [item for item in categories[str(record.get("category") or "other")] if item is not record][:4]
        pin = (install_pins or {}).get(str(record.get("id") or ""))
        pin = _current_install_pin(record, pin, archive_source)
        (detail_dir / "index.html").write_text(_render_detail_page(record, slug, related, pin), encoding="utf-8")
        if pin:
            index_path = detail_dir / WELL_KNOWN_DIR / "index.json"
            index_path.parent.mkdir(parents=True)
            index_path.write_text(json.dumps(_discovery_index(pin), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            pinned_count += 1
    browse = f'''<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Skill guides — Claude Skills Registry</title>
<meta name="description" content="Browse skill guides by category with source links, installation commands, and registry quality and scan signals.">
<link rel="canonical" href="{PUBLIC_SITE}skills/">
<meta property="og:type" content="website">
<meta property="og:title" content="Skill guides — Claude Skills Registry">
<meta property="og:description" content="Browse source-linked skill guides and installation instructions by category.">
<meta property="og:url" content="{PUBLIC_SITE}skills/">
<meta property="og:image" content="{PUBLIC_SITE}assets/registry-social-preview.png">
<meta property="og:image:alt" content="Claude Skills Registry — source-linked skill guides">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Skill guides — Claude Skills Registry">
<meta name="twitter:description" content="Browse source-linked skill guides and installation instructions by category.">
<meta name="twitter:image" content="{PUBLIC_SITE}assets/registry-social-preview.png">
<meta name="twitter:image:alt" content="Claude Skills Registry — source-linked skill guides">
<link rel="stylesheet" href="../css/skill-detail.css"></head><body><header><a href="{PUBLIC_SITE}">← Search skills</a></header><main><h1>Browse skill guides</h1>'''
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
    return {"generated_count": len(records), "slugs": slugs, "pinned_count": pinned_count}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--install-pins", type=Path, help="install-pins.json from resolve_install_pins.py")
    parser.add_argument("--archive-source", type=Path, help="Read-only current archive used to verify install pins")
    args = parser.parse_args()
    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    records = payload.get("skills") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise ValueError(f"Catalog artifact has no valid skills list: {args.catalog}")
    pins = None
    if args.install_pins is not None:
        pins = json.loads(args.install_pins.read_text(encoding="utf-8")).get("pins")
        if not isinstance(pins, dict):
            raise ValueError(f"Install pin artifact has no valid pins map: {args.install_pins}")
    summary = build_static_skill_pages(records, args.output, args.archive, pins, args.archive_source)
    print(f"Generated {summary['generated_count']} static skill pages ({summary['pinned_count']} with verified install)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
