# Upstream Sync & Fork-Friendly Changes

> This repo is a fork of [prebid/salesagent](https://github.com/prebid/salesagent) maintained at [Pubx-ai/salesagent](https://github.com/Pubx-ai/salesagent). We add curation functionality (`src/adapters/curation/`) on top of the upstream AdCP reference implementation, and we rebase against `upstream/main` frequently. **Every change must minimize merge friction.**

## Why This Matters

The fork carries non-trivial custom work (curation adapter, admin blueprint, ECS entrypoints, etc.) and pulls upstream regularly. Each line of upstream code we modify is a future merge conflict. The cumulative cost of "small, harmless" upstream edits is the dominant pain point of fork maintenance.

The two principles below are non-negotiable. Read them before every change.

## Core Principles

### Principle 1 — All changes must be sync-friendly

Every change should be made in a way that minimizes merge conflicts when rebasing against `upstream/main`. Concretely:

- **Add new files; don't edit existing upstream files.** New modules, classes, blueprints, repositories, services, tests, and migrations are always preferred. Editing an existing upstream file should be a last resort.
- **Use the existing extension surface; don't expand it.** Register via `ADAPTER_REGISTRY`, override base-class methods, set `manages_own_persistence = True`, attach hooks via class-level methods (e.g. `_for_tool()`). These are the established plugin points.
- **Never string-match adapter names** (e.g. `if adapter_type == "curation"`) inside upstream tool flow. Always go through the registry: `adapter_manages_own_persistence(tenant)`, `adapter_class._for_tool(...)`, etc.
- **Touch upstream files only at registration sites.** Adding `"curation"` to `AVAILABLE_ADAPTERS` is fine. Inlining curation business logic into `src/core/tools/products.py` is not.
- **Prefer `ext` dicts and registry overrides** over schema field additions. If you must add a field to a Pydantic model that mirrors `adcp` library types, place it in `ext` first; only extend the schema via inheritance when absolutely required, and document why.
- **Never reformat or reflow upstream files.** Whitespace-only changes guarantee merge conflicts for zero benefit.

### Principle 2 — Curation changes are strictly additive (no upstream restructuring without explicit user confirmation)

Curation lives in `src/adapters/curation/` and plugs into the prebid sales agent at well-defined seams. New curation features should:

- Live entirely under `src/adapters/curation/` whenever possible.
- Plug into upstream tools through the registry (`adapter_manages_own_persistence()` + `_for_tool()` dispatch), not via new branches in upstream `_impl` functions.
- Use the `AdServerAdapter` ABC as-is. If you need a new lifecycle hook, propose adding it to the ABC so all adapters benefit — do not special-case curation.

**If a curation feature appears to require drastic changes to upstream files** — for example, restructuring `src/core/tools/products.py`, modifying `_impl` signatures, changing `src/core/schemas/_base.py`, rewiring `src/app.py` mounting, or altering `src/adapters/base.py`'s contract — then:

1. **STOP.** Do not start writing code.
2. Surface this to the user with at minimum these alternatives:
   - **(a) Smallest additive workaround** — even if uglier, what does it look like?
   - **(b) Upstream restructure with diff size estimate** — how many lines, which files?
   - **(c) Propose-upstream-first** — could this change be contributed back to prebid/salesagent so we don't carry it locally?
3. Wait for **explicit user confirmation** before touching upstream code in a non-trivial way.

This rule exists because the user has stated the rebase pain is the dominant cost of maintaining the fork. Drastic upstream changes for curation purposes are NOT permitted on agent initiative.

## Repo Layout — Curation vs. Upstream

### Curation-only territory (modify freely; we own these files)

| Path | Purpose |
|------|---------|
| `src/adapters/curation/` | Curation adapter package — http clients, segment converter, ranking, etc. |
| `src/admin/blueprints/curation.py` | Admin UI blueprint for curation config |
| `tests/unit/test_curation*.py` | Curation unit tests |
| `tests/integration/test_curation*.py` | Curation integration tests |
| `tests/helpers/curation_fixtures.py` | Curation test fixtures |
| `alembic/versions/*curation*.py` | Curation-specific migrations |
| `docs/curation-*.md` | Curation documentation (e.g. gap analysis) |
| `src/entrypoints/` | ECS entrypoint scripts (Pubx-only) |

### Upstream-shared territory (modify cautiously; every change is a future merge conflict)

| File | Pubx integration role |
|------|----------------------|
| `src/adapters/__init__.py` | Add `CurationAdapter` to `ADAPTER_REGISTRY` |
| `src/adapters/base.py` | `manages_own_persistence` class var; add new lifecycle hooks here, not on `CurationAdapter` only |
| `src/core/main.py` | `AVAILABLE_ADAPTERS` list includes `"curation"` |
| `src/core/helpers/adapter_helpers.py` | `get_adapter()` factory branch for curation; `adapter_manages_own_persistence()` registry helper |
| `src/core/tools/products.py` | Calls `adapter_manages_own_persistence(tenant)` to dispatch to curation |
| `src/core/tools/media_buy_create.py` | Same dispatch pattern |
| `src/core/tools/media_buy_list.py` | Same dispatch pattern |
| `src/core/tools/media_buy_delivery.py` | Same dispatch pattern |
| `src/core/tools/creatives/_sync.py` | Skip block for tenants whose adapter manages its own persistence |
| `src/core/schemas/product.py` | `relevance_score` field for curation ranking |
| `src/admin/blueprints/adapters.py` | Routes curation config to its own blueprint |
| `src/admin/app.py` | Registers the curation blueprint |

### Conflict watch list (highest-risk on rebase — review every sync)

These files are touched both upstream and by us, so they conflict most often:

1. `src/core/main.py` — upstream commonly grows `AVAILABLE_ADAPTERS` and tool registrations
2. `src/core/helpers/adapter_helpers.py` — `get_adapter()` regularly gains new adapter branches
3. `src/core/tools/{products,media_buy_create,media_buy_list,media_buy_delivery}.py` — most active upstream area
4. `src/adapters/base.py` — ABC contract changes propagate to every adapter
5. `src/core/schemas/product.py` — schema evolves with each AdCP version

## Sync-Friendly Change Patterns

### Adding a new curation tool/method

**Pattern**: extend `CurationAdapter` directly. Do NOT add a new `_impl` function in `src/core/tools/` for curation-specific behavior.

```python
# src/adapters/curation/adapter.py
class CurationAdapter(AdServerAdapter):
    @classmethod
    def _for_tool(cls, tool_name: str) -> Callable | None:
        if tool_name == "list_signals":
            return cls.list_signals
        return super()._for_tool(tool_name)

    async def list_signals(self, ...):
        ...
```

### Adding a curation-specific config field

**Pattern**: extend `CurationConnectionConfig`. Do NOT add fields to upstream `Tenant` or `AdapterConfig` ORM models — the `config_json` column already carries arbitrary curation config.

```python
# src/adapters/curation/config.py
class CurationConnectionConfig(BaseModel):
    catalog_url: HttpUrl
    sales_url: HttpUrl
    activation_url: HttpUrl
    new_feature_flag: bool = False  # ← add here
```

### Hooking into an upstream tool flow

**Pattern**: rely on `adapter_manages_own_persistence(tenant)` + `CurationAdapter._for_tool(...)`. Never add curation-specific branches to upstream tool `_impl` functions.

```python
# Good — already in src/core/tools/products.py
if adapter_manages_own_persistence(tenant):
    return await _delegate_to_adapter(...)

# Bad — string match, breaks the registry pattern
if tenant["adapter"] == "curation":
    ...
```

### Adding a curation database table

**Pattern**: new migration that creates a new table; do NOT add columns to upstream tables (`tenants`, `principals`, `media_buys`, `products`, `creatives`) unless the column is genuinely cross-cutting (and even then, ask first).

If you must add a column to an upstream table:
- Create a fresh migration; never modify an existing one.
- Make the column nullable with a sensible default — upstream migrations must continue to apply cleanly.
- Document the cross-cutting justification in the migration's docstring.

### Adding admin UI for a curation feature

**Pattern**: new file under `src/admin/blueprints/curation.py` (or a new sibling). Register it in `src/admin/app.py` with a one-line import + `app.register_blueprint(...)`. Avoid touching `src/admin/blueprints/adapters.py` or other shared admin files.

## Marking Custom Modifications (Convention)

When modifying an upstream file is unavoidable, leave a one-line marker comment so future rebasers can grep:

```python
# PUBX: register curation adapter (see src/adapters/curation/)
ADAPTER_REGISTRY["curation"] = CurationAdapter
```

```python
# PUBX: dispatch to adapter when it manages its own persistence
if adapter_manages_own_persistence(tenant):
    return await _delegate_to_adapter(...)
```

Use the prefix `# PUBX:` consistently. `git grep "# PUBX:"` then reveals every modification site in the fork — invaluable during rebase.

This is a soft convention being introduced now; older modification sites won't have markers. Add them opportunistically when you touch a file.

## The Upstream Sync Workflow

### 1. Pre-sync checks

```bash
git checkout main
git status                                           # working tree must be clean
git fetch upstream
git fetch origin

# How far behind are we?
git log --oneline main..upstream/main | wc -l
git log --oneline main..upstream/main | head -30

# What custom work do we currently carry?
git log --oneline upstream/main..main
git diff upstream/main..main --stat | tail -20
```

### 2. Create the sync branch

```bash
git checkout -b sync/upstream-main-$(date +%Y%m%d)
git merge upstream/main
```

This repo's prior sync PRs use **merge** (e.g. `merge: sync fork with upstream prebid/salesagent main`), not rebase. Merge preserves history on both sides, is easier to back out, and avoids rewriting our commits. Stay consistent with this pattern unless the user changes it.

### 3. Resolve conflicts

For each conflict, work through the watch list above and apply this rule of thumb:

1. **Open the upstream version and the curation version side by side.** Curation-side edits are usually small additive overlays (registry entry, dispatch branch, schema field).
2. **Re-apply the curation overlay on top of the new upstream baseline** — don't blindly accept either side. Goal: keep the curation change as a minimal additive patch.
3. **Run structural guards on each modified upstream file**: `make quality` will catch most regressions (registry violations, schema misalignment, transport-boundary violations).
4. **Run targeted tests** on every modified upstream file before continuing the rebase.

### 4. Post-sync verification

```bash
make quality                              # format + lint + mypy + unit
./run_all_tests.sh                        # full suite

# Delta inventory — what does the fork add over upstream?
git log --oneline upstream/main..HEAD
git diff upstream/main..HEAD --stat | tail -20
```

The post-sync `git log upstream/main..HEAD` output is the fork's **delta inventory**. Review it. If it grew compared to before the sync (beyond what the new commits added), that's a smell — review whether you accidentally retained a deleted upstream change.

### 5. Open the sync PR

```bash
git push -u origin sync/upstream-main-YYYYMMDD
gh pr create \
  --base main \
  --title "chore: sync fork with upstream prebid/salesagent main" \
  --body "Pulls upstream/main as of <SHA>. Conflicts resolved in: <files>. ..."
```

PR title prefix must match conventional commits (`chore:` for sync PRs).

## Anti-Patterns

| Anti-pattern | Why it's bad | Correct pattern |
|---|---|---|
| `if adapter_type == "curation"` in upstream tool code | Every new adapter requires re-touching upstream code | `adapter_manages_own_persistence(tenant)` |
| Adding a curation-specific field to upstream `Tenant`/`Principal`/`MediaBuy` ORM model | Migration conflicts on every sync; widens the fork's footprint | New table, or `config_json`/`ext` field |
| Modifying `_impl` signatures in `src/core/tools/*.py` | Breaks transport guards; massive merge surface | Add to `CurationAdapter._for_tool()` |
| Inlining curation business logic in `src/admin/blueprints/adapters.py` | Admin UI churn on every sync | New blueprint at `src/admin/blueprints/curation.py` |
| Reformatting / reflowing upstream files | Spurious merge conflicts on whitespace | Touch only the necessary lines; never reflow |
| Renaming upstream functions used internally | Renames cascade through every conflict | Wrap, don't rename |
| Adding curation imports to `src/app.py`, `src/core/main.py` "for convenience" | Imports become merge-conflict magnets | Register in the existing registry; let the lazy import in `get_adapter()` do its job |
| "Drive-by" upstream cleanup ("while I'm here, let me also fix...") | Every drive-by line is a future conflict | File a separate beads issue; stay narrowly scoped |

## When You MUST Modify Upstream Code

Sometimes additive really isn't possible. In that case:

1. **Confirm there's no extension surface.** Re-read `src/adapters/base.py`, the registry helpers in `src/core/helpers/adapter_helpers.py`, and the dispatch points in upstream tools. The right fix is often "add a hook to the ABC, then override it in `CurationAdapter`" rather than "branch inside the upstream tool."
2. **Touch as few lines as possible.** Five-line edits at registration sites are fine. Fifty-line restructures are not (without explicit user confirmation per Principle 2).
3. **Mark with `# PUBX:` comment** explaining why the change exists.
4. **If the change is non-trivial, ask the user first.** "Non-trivial" means: more than ~10 lines, modifies a function signature, changes control flow, restructures a class, or touches `src/adapters/base.py`, `src/core/schemas/_base.py`, `src/app.py`, `src/core/main.py` beyond a registration line.
5. **Consider proposing the change upstream.** If it's a generally useful improvement (not curation-specific), open a PR to prebid/salesagent first. The fork is then a thin overlay rather than a divergent branch.

## Pre-PR Sync-Friendliness Checklist

Before merging any PR to `main`:

- [ ] Reviewed diff against upstream: `git diff upstream/main..HEAD --stat`
- [ ] No new branches in upstream files based on string-matching adapter names
- [ ] No new fields on upstream ORM models (or, if there are, justified and additive-only)
- [ ] No reformatting/reflowing of upstream files
- [ ] Modified upstream files marked with `# PUBX:` comments where reasonable
- [ ] Curation feature works through the existing extension surface (or extends the ABC explicitly with user approval)
- [ ] If upstream code is restructured: user has confirmed in writing
- [ ] `make quality` passes
- [ ] Curation tests pass: `tox -e unit -- tests/unit/test_curation*.py` and `tox -e integration -- tests/integration/test_curation*.py`

## Decision Tree

```
Adding a feature
├── Curation-only?
│   ├── Yes → src/adapters/curation/ + new admin blueprint + new tests. Done.
│   └── No (general improvement to the sales agent) →
│       ├── Could it be contributed upstream? → Open a PR to prebid/salesagent first
│       └── Internal-only? → Add as small a change as possible; mark with `# PUBX:`
│
Changing upstream code
├── Is there an additive alternative?
│   ├── Yes → Take the additive path
│   └── No (we must modify upstream) →
│       ├── < ~10 lines AND at registration / dispatch site → OK, mark with `# PUBX:`
│       ├── Modifies _impl signature, schema base, adapter ABC,
│       │   or restructures upstream files → STOP, ask user (Principle 2)
│       └── Reformatting / reflowing → DON'T
```

## See Also

- `CLAUDE.md` § "Fork Architecture & Upstream Sync"
- `.claude/rules/patterns/code-patterns.md` — code style
- `.claude/rules/patterns/mcp-patterns.md` — MCP/A2A transport boundary patterns
- `.claude/rules/workflows/quality-gates.md` — what `make quality` runs
- `docs/curation-adcp-gap-analysis.md` — what curation has + missing AdCP coverage
