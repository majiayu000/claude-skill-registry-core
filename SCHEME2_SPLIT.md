# Scheme 2: Core + Data Repo (Core-First)

Goal: keep the full `skills/**` archive browsable on GitHub, while keeping pipeline authority and automation in `registry-core`.

## Repo Roles

### 1) `registry-core` (source of truth)

Contains:
- Crawlers + build scripts (`scripts/`, `crawler/`)
- Source lists (`sources/`)
- Schemas (`schema/`)
- Published metadata (`registry.json`)
- GitHub Pages site sources (`docs/`)
- Authoritative workflows and sync policy

Does **not** commit:
- The expanded skill archive `skills/**` (stored in `registry-data`)

### 2) `registry-data` (archive store)

Contains:
- archived skill contents (category folders like `development/`, `documents/`, `data/`, etc.)

### 3) `registry-main` (publish artifact)

Contains:
- merged tree built from `core + data` for browsing/compatibility consumers

Policy:
- `main` is not canonical for pipeline behavior.
- If docs/workflows conflict between repos, `core` wins.

## Sync Model (Single Writer)

1. Only `core` runs scheduled discovery/download/sync jobs.
2. `core` checks out `registry-data` into `./skills/` during CI and updates archive/index outputs.
3. `core` pushes archive changes to `registry-data` and index/site outputs in `core`.
4. `core` can trigger a `main` publish workflow using pinned `core_sha` + `data_sha`.
5. `main` rebuilds merged outputs from those SHAs for reproducibility.

Avoid:
- Running crawler/sync schedules in `main`
- Letting `main` write to `data`
- Publishing metrics without clear raw vs deduplicated labels

## Required CI Configuration (Core Repo)

- Repository variable: `REGISTRY_DATA_REPO` (e.g. `yourname/claude-skill-registry-data`)
- Secret: `DATA_REPO_TOKEN` (PAT with `repo` scope for private or `public_repo` for public)
- Repository variable: `REGISTRY_MAIN_REPO` (e.g. `yourname/claude-skill-registry`)
- Secret: `MAIN_REPO_TOKEN` (token that can dispatch workflows in main repo)

## Local Merge (core + data -> main)

Use the merge script to rebuild the main repo from core + data:

```bash
bash scripts/sync_main_repo.sh \
  --core ../claude-skill-registry-core \
  --data ../claude-skill-registry-data \
  --main ../claude-skill-registry
```
