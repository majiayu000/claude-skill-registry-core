## Repository Policy (Core-First)
- **Core repo is the source of truth** for scripts, workflows, index generation, and site outputs.
- **Data repo** stores the archived `skills/**` tree.
- **Main repo** is a merged publishing artifact (`core + data`), not the canonical place to define pipeline behavior.
- If behavior or docs conflict across repos, follow **core** implementation and sync others to match.

## Case-Conflict Policy (Core Implementation)
- The filesystem is case-insensitive for many users. **No paths may differ only by case**.
- Use `normalize_name()` and `ensure_unique_dir()` for all skill directory creation.
- Directory conflict suffix policy must match `scripts/utils.py`:
  - Prefer repo suffix: `{name}-{owner}-{repo}` (via `get_repo_suffix()`).
  - Fallback when repo suffix is unavailable: `{name}-{short-hash}`.
  - If needed, append numeric disambiguators: `-2`, `-3`, ...
- Reuse an existing directory when the metadata key resolves to the same skill.
- Do **not** remove skills to resolve conflicts; **rename** with suffixes instead.

## Non-Compatibility Rule
- Backward compatibility for historical directory names is **not required**.
- Prefer deterministic, conflict-free paths aligned with core scripts.
