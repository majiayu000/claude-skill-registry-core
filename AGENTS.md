## Repository Policy (Split-in-Progress)
- **Main repo must keep full functionality** (scripts, data, and `skills/` archive stay).
- **Core repo** hosts index + site; **Data repo** hosts archived skill files.
- Updates should continue to flow to **all three repos** as needed.

## Case-Conflict Policy (Required)
- The filesystem is case-insensitive for many users. **No paths may differ only by case**.
- When a name conflicts case-insensitively, **append a stable suffix**:
  - Format: `__dup-<short-hash>` (e.g., `annualreports__dup-1a2b3c4d`)
- Use `normalize_name()` and `ensure_unique_dir()` for all skill directory creation.
- Do **not** remove skills to resolve conflicts; **rename** with suffixes instead.

## Non-Compatibility Rule
- Backward compatibility for historical directory names is **not required**.
- Prefer correctness and conflict-free paths over preserving old casing.
