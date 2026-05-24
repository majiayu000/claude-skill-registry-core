# Canonical Taxonomy Governance and Migration Spec

## Problem

The archive has a clean two-level layout, but semantic category quality is still
uneven. Historical import names, broad buckets, and stale model decisions can
leave skills in categories that are not good user-facing browsing choices.

The taxonomy must be a single current contract, not a family of product-version
lines. Old category names may be useful evidence during audit, but they are not
compatibility promises and must not make legacy inputs valid publish categories.

## Goals

- Treat `taxonomy/categories.yaml` as the only current category contract.
- Keep generated classification suggestions deterministic, reviewable, and
  reproducible.
- Reject unknown, review, or deprecated categories at publish boundaries.
- Route legacy names and uncertain inputs into explicit review queues.
- Keep archive mutations separate from planning, model review, and audit.

## Non-Goals

- Do not automatically apply heuristic or model classification candidates.
- Do not directly edit generated files in the merged publish artifact.
- Do not preserve old category names as compatibility obligations.
- Do not delete skills to resolve category or directory conflicts.

## Taxonomy File Contract

`taxonomy/categories.yaml` is the source of truth for category metadata. Its
`schema_version` is only a machine-readable file-format field for parsers and
tests; it is not a public taxonomy product version.

Each category declares:

- `slug`: canonical identifier.
- `code`: compact search-index code.
- `display_name`: user-facing label.
- `keywords`: deterministic text signals used for audit scoring.
- `status`: `active`, `review`, or `deprecated`; missing status is `active`.
- `description`: inclusion rule or migration context.
- `parent`: optional reporting relationship.
- `migrate_to`: required only for temporary deprecated definitions.

Only `active` categories are publishable. `other` is an active fallback bucket,
but it should shrink through reviewed migrations.

Legacy names may remain in the file as audit hints, but default resolution does
not use them. Any tool that wants to inspect legacy mappings must opt in
explicitly and keep the result review-only.

## Classification Boundary Rules

- Source intake should provide a canonical slug.
- Unknown category input is not silently accepted as safe.
- Legacy names are reported as review items rather than silently normalized.
- Model output is accepted only when it names an active canonical category.
- Review and deprecated categories may appear in reports, but they are not valid
  publish targets.

## Migration Plan Contract

`scripts/plan_category_migration.py` emits JSON with:

- `schema_version`, `generated_at`, `skills_dir`, `policy`, `summary`,
  `changes`, and `notes`.
- Per change: `action`, `confidence`, `review_required`, `path`, `name`,
  `current_category`, `proposed_category`, `target_path_preview`,
  `raw_sources`, `resolved_sources`, `score`, `current_score`, `signals`, and
  `reason`.

The plan is review-only. A later apply tool must recompute collision-safe
targets using the same deterministic directory rules as the archive
normalizers.

Legacy alias findings use the existing `normalize_alias` action name for report
continuity, but they are review-required. They are not publish-time
compatibility behavior.

## Model Review Contract

`scripts/review_category_plan_with_llm.py` runs a second-pass review against an
existing migration plan. It stays separate from deterministic planning so the
base plan remains reproducible and offline.

Defaults:

- OpenAI-compatible endpoint: `https://token-plan-sgp.xiaomimimo.com/v1`.
- Model: `mimo-v2.5-pro`.
- API key source: `MIMO_API_KEY`.
- Candidate actions: `heuristic_reclassify` and `resolve_source_conflict`.
- Selection order: `risky-first`, reviewing `low`, then `medium`, then `high`.
- Optional checkpoint: `--checkpoint-jsonl <path>` appends one JSONL row per
  completed review and `--resume` skips matching completed `review_key` values.
- Apply mode: `review-only`.

The allowed category payload contains only active canonical categories. Unknown,
inactive, malformed, or missing model outputs are kept as non-`ok` rows so
downstream migration stays fail-closed.

Secrets must not be written to files or committed. Reports record the
environment variable name, never the API key value.

## Apply Contract

`scripts/apply_category_migration.py` converts reviewed classification results
into a concrete directory move plan. It is separate from planning and model
review so archive mutations stay explicit.

Defaults:

- Input rows include `path`, `current_category`, `llm_category`, `confidence`,
  and `status`.
- Minimum confidence is `0.9`.
- Only active target categories are eligible unless an operator explicitly
  widens `--target-status` for a diagnostic run.
- `other` is not an eligible target unless `--allow-target-other` is passed.
- Default mode is dry-run. Only `--apply` mutates the archive.
- `--movable-only` skips blocked duplicates and fills the requested `--limit`
  with apply-ready moves.

Apply mode refuses blocked plans, moves only standard
`<category>/<skill>/SKILL.md` directories, updates `metadata.json`, and never
deletes or overwrites skills to resolve conflicts.

## Residual Audit Contract

`scripts/audit_category_residuals.py` explains what remains after an apply
batch. It is report-only and must run before another migration batch is
accepted.

The residual report separates:

- `same_policy_plan_summary`: recomputed by `apply_category_migration.py` with
  the same flags.
- live archive residuals: based on source paths that still exist in the
  archive.

Residual reason buckets include low confidence, target category/status excluded
by policy, target `other`, classification status/path failures, stable-key
conflicts, source missing, current archive category filtered out, and movable
candidates under the selected policy.

## Governance Gates

Taxonomy gate:

- `scripts/check_taxonomy_governance.py` fails on schema and relationship
  errors.
- `--strict-canonical` fails if taxonomy definitions still contain non-active
  transitional categories.
- `--publish-category <slug>` fails when a publish target is unknown, review, or
  deprecated.
- The default report includes canonical and noncanonical category counts so
  category cleanup progress is visible.

Category artifact gate:

- `scripts/check_category_artifacts.py` verifies every
  `docs/categories/<category>.json` file is a small pointer.
- It fails if a pointer contains `skills`, lacks `deprecated_full_payload`,
  lacks a manifest reference, references a missing manifest, or exceeds the
  pointer size limit.

## Operating Flow

1. Update category status/name semantics in core.
2. Run taxonomy governance validation.
3. Generate a review-only migration plan against the data archive.
4. Review by action, confidence, and category pair.
5. Apply only small, high-confidence batches in data PRs.
6. Run residual audit with the same policy.
7. Build residual worksets for gaps, low confidence rows, inactive targets, and
   target-`other` rows before running another model pass.
8. Reclassify worksets with checkpoints and apply only `ok`, active,
   high-confidence rows through the migration planner.
9. Publish from pinned core/data refs.
10. Re-run audit and compare `other` share, category conflicts, residual
    reasons, and plan deltas.

## Acceptance Criteria

- Docs and workflow messages describe a canonical taxonomy, not a named product
  version line.
- Historical aliases do not silently make legacy names valid publish
  categories.
- Publish target validation fails on unknown, review, or deprecated categories.
- Model review accepts only active canonical categories.
- Migration planning still produces audited review queues for unknown and legacy
  inputs.
