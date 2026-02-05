# Scheme 2: Core + Data Repo (Git-browsable archive)

Goal: keep the **full `skills/**` archive browsable on GitHub**, while making this repo fast to `clone/pull` by moving the huge working tree into a separate **data repo**.

## Repos

### 1) `registry-core` (this repo)

Contains:
- Crawlers + build scripts (`scripts/`, `crawler/`)
- Source lists (`sources/`)
- Schemas (`schema/`)
- Published metadata (`registry.json`)
- GitHub Pages site sources (`docs/` static assets)

Does **not** contain:
- The expanded skill archive `skills/**` (moved to the data repo)

### 2) `registry-data` (new repo)

Contains:
- archived skill contents (category folders like `development/`, `documents/`, `data/`, etc.)

This is the repo you’ll browse on GitHub to view a skill.

## How sync works

This repo’s GitHub Actions workflow checks out the data repo into `./skills/` (as a separate checkout). That keeps paths stable (e.g. `./skills/<category>/<skill>/SKILL.md`) while the core repo stays small.

You’ll need:
- A GitHub repository variable: `REGISTRY_DATA_REPO` (e.g. `yourname/claude-skill-registry-data`)
- A secret: `DATA_REPO_TOKEN` (PAT with `repo` scope for private, or `public_repo` for public)

## Local sync (core + data → main)

Use the merge script to rebuild the main repo from core + data:

```bash
bash scripts/sync_main_repo.sh \
  --core ../claude-skill-registry-core \
  --data ../claude-skill-registry-data \
  --main ../claude-skill-registry
```
