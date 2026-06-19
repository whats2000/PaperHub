---
name: paperhub-merge-prep
description: Use when the user signals a feature/hotfix branch is ready to merge into main on the PaperHub repo (phrases like "ready to merge", "merge prep", "let's land this hotfix", "release this branch") OR when invoked via the /paperhub-merge-prep slash. Do NOT fire on code-level "merge these functions" / git-conflict resolution / cherry-pick discussions.
---

# PaperHub merge-prep checklist

The PaperHub repo has a deterministic release-prep workflow that the user
has asked for across multiple hotfix branches. Encode it here so each
hotfix lands consistently and nothing is missed.

When this skill fires, execute the steps below **in order**. Do not skip
ahead; each step depends on the previous one. The final step is a hard
STOP that surfaces the exact merge commands and waits for explicit
per-instance approval per the global CLAUDE.md restricted-operations
rule.

## 0 — Verify branch state

- Confirm current branch is NOT `main`. If it is, there is nothing to
  merge; ask the user what they meant.
- `git status --short` must be empty (working tree clean). If not, ask
  the user how to handle the uncommitted edits before proceeding.
- `git log main..HEAD --oneline` enumerates the branch's commits — read
  this; it informs the version-bump decision in step 1 and the SRS
  changelog entry in step 4.

## 1 — Pick the next version

The repo uses three coupled packages (`paperhub`, `frontend`,
`paperhub-marker`) all on the same version string. Inspect the commits
since `main`:

- Only `fix:` / `perf:` / `docs:` / `chore:` / `refactor:` → **patch bump**
  (the default for hotfix branches)
- Any `feat:` (additive feature) → **minor bump**
- Any commit body declaring a breaking change → **major bump**

If the kind of bump is ambiguous (e.g. a `feat:` that's really a fix, or
a `fix:` that adds significant new behaviour), **ASK the user**. Don't
guess on minor or major — patch is the only safe default.

## 2 — Run quality gates FIRST (before touching the README)

**Order matters.** The README test-count badge must reflect the most
recent run. Do NOT write counts to README before running the suites — a
prior conversation's count is not authoritative.

The repo uses `uv` exclusively for Python (per CLAUDE.md Conventions —
never `pip`, never `python -m venv`, never system python). The full
gate set:

- Backend tests: `cd backend && uv run pytest` → record `N passed`
- Frontend tests: `cd frontend && npm test -- --run` → record `N passed`
- Backend ruff: `cd backend && uv run ruff check src tests` → must say
  "All checks passed!"
- Backend mypy: `cd backend && uv run mypy src` → must say
  "Success: no issues found"
- Frontend typecheck: `cd frontend && npm run typecheck` → tsc strict,
  no errors
- Frontend lint: `cd frontend && npm run lint` → ESLint, no errors
- Frontend build (only if the branch changed build config or imports
  that could break at production-bundle time): `cd frontend && npm run build`

If any gate fails, STOP and surface the failure to the user. Do not
attempt to fix it under the skill — that's a separate task.

## 3 — Apply version bumps (version-locked files: every pyproject/json AND its lockfile)

The repo has THREE coupled packages on the same version string —
**bumping the manifest alone leaves a lockfile saying the package is
still on the prior version** (the foot-gun that ships an inconsistent
release if missed). For each package, edit the manifest THEN
regenerate the lockfile:

| Package | Manifest | Lockfile | Regen command |
| --- | --- | --- | --- |
| `paperhub` (backend) | `backend/pyproject.toml` | `backend/uv.lock` | `cd backend && uv lock` |
| `frontend` | `frontend/package.json` | `frontend/package-lock.json` | `cd frontend && npm install --package-lock-only` |
| `paperhub-marker` | `marker_service/pyproject.toml` | `marker_service/uv.lock` | `cd marker_service && uv lock` |

After bumping all three manifests + regenerating all three lockfiles,
**verify every lock now shows the new version** (the version string
appears multiple times in `frontend/package-lock.json` — root + a
`packages."".version` entry — both must update):

```
grep -n '"version": "X.Y.Z"' frontend/package-lock.json   # expect ≥2 matches
grep -n 'version = "X.Y.Z"'   backend/uv.lock              # expect 1 match for the paperhub row
grep -n 'version = "X.Y.Z"'   marker_service/uv.lock       # expect 1 match for paperhub-marker
```

Then update the two doc files:

- **All FOUR README language files** — `README.md` (en), `README.ja.md`,
  `README.zh-CN.md`, `README.zh-TW.md`. The badges + version text + the
  citation block are duplicated in EVERY locale file; update all four, with
  **four changes each**:
  1. Tests badge: update both counts from step 2's fresh runs (the stale
     count may differ per file — they drift independently; set all four to
     the same fresh numbers)
  2. Status badge: `release-vX.Y.Z%20(SRS%20vX.Y.Z)-success`
  3. The "shipped through **vX.Y.Z**" sentence in the docs/SRS pointer
     (translated per locale — match each file's existing wording)
  4. The BibTeX **citation** block's `version = {X.Y.Z}` line (under
     `## Citation` / `## 引用`) — easy to miss; a public citation that
     undersells the released version
  After editing, `grep -nE "v?<OLD>" README*.md` MUST return nothing — every
  language file and the citation line must have moved.
- `CLAUDE.md` — every literal `v(old)` reference to the SRS pointer
  (use `Edit` with `replace_all: true` after confirming via grep that
  every match is intended — there are typically two: the spec link in
  "What you're working on" and the "Where things live" SRS pointer)

## 4 — Update the SRS

Edit `docs/superpowers/specs/2026-05-17-paperhub-srs.md`:

- Bump the `| Version | vX.Y.Z |` row in the metadata table at the top
- Insert a NEW row at the **top of the Revision History table** (after
  the header `| Version | Date | Summary |` line, BEFORE the prior
  version's row). Match the dense single-line format the existing
  rows use:

  ```
  | **vX.Y.Z** | **YYYY-MM-DD** | **One-line headline summarizing the load-bearing change.** Lead item: <root cause in one paragraph — what broke, why, how the fix works, how it was verified>. **Other fixes/perfs/docs**: (a) <fix> ... (b) <fix> ... (c) <fix> ... **Versions bumped to X.Y.Z** across `paperhub`, `frontend`, `paperhub-marker`. Backend N tests / frontend M tests green; ruff + mypy clean. <"No schema or LLM-contract change" if true.> <Any out-of-scope follow-ups, named explicitly.> |
  ```

  - Lead with the load-bearing change as the headline. Bug fixes that
    silently lost user data, broke a feature in prod, or required a
    deep root-cause investigation always lead.
  - For multi-commit branches, group lesser items as labeled (a), (b),
    (c) ... — don't recite the commit log; synthesize.
  - Cite specific commit hashes only for the lead item and for any
    item that needs future-traceability (e.g. a CLAUDE.md rule
    commit). Don't paste the full git log.
  - Name explicitly any test-fixture or follow-up work that's out of
    scope for this round — future-you will read this changelog when
    diagnosing the next bug.

## 4b — Prepend the in-app changelog entry (FR-16)

`frontend/src/changelog/changelog.json` is the user-facing changelog the
`ChangelogModal` renders. Prepend ONE new entry (newest-first) for this
version:

- `version` = the new X.Y.Z (no `v`), `date` = today (YYYY-MM-DD).
- `highlights` = 1–3 SHORT, user-facing bullets per locale (`en`, `zh-TW`,
  `zh-CN`, `ja`) — what the user can now DO, not the dense SRS prose. `en` is
  source-of-truth; translate the others (en-fallback is tolerated but prefer
  real translations to match the i18n posture).

This is distinct from the SRS Revision-History row (internal/engineering).
Stage `frontend/src/changelog/changelog.json` with the release commit in §5.

**This `en` block is ALSO the GitHub Release note.** `release.yml` reads
`changelog.json` at tag-push time and composes the tagged version's `en`
highlights into the Release body (then GitHub appends its auto-generated
"What's Changed" PR list). So the in-app ChangelogModal and the GitHub
Release page render the **same** user-facing changelog from ONE source —
there is no second place to author release notes. Consequences:

- Write the `en` highlights release-quality (they are public on the
  Release page), and make sure the entry's `version` EXACTLY matches the
  tag minus its `v` (e.g. tag `v2.37.1` ↔ entry `"2.37.1"`) — a mismatch
  silently drops the curated notes and the Release falls back to header +
  image refs + the auto PR list.
- A tag with **no** matching `changelog.json` entry still releases
  cleanly (the highlights section is just omitted), but the curated notes
  are then missing — so never tag a version you skipped here.

## 5 — Commit as one release bump

Stage every file changed in steps 3, 4, and 4b — typically **thirteen files**:
the three manifests, the three lockfiles, the **four README language
files**, CLAUDE.md, the SRS, and the in-app `changelog.json`. Then commit:

```
chore(release): vX.Y.Z

<one-sentence summary of the branch's purpose>

Lead item: <one sentence on the load-bearing change>. Verified <how>.

Versions: paperhub / frontend / paperhub-marker → X.Y.Z.
Backend N tests / frontend M tests green; ruff + mypy clean.
```

Use a HEREDOC for the commit message so the formatting is preserved.

## 6 — STOP. Ask the user for merge approval.

This is a hard stop per the global CLAUDE.md restricted-operations
rule. The following commands are NOT auto-runnable:

- `git checkout main && git merge --no-ff <branch>` (modifies shared
  branch state — requires explicit per-instance approval)
- `git tag -a vX.Y.Z <branch-name> -m "Release vX.Y.Z"` (annotated tag
  on the RELEASE commit — NOT the merge commit; see the warning below)
- `git push origin main` (publishes — requires explicit approval)
- `git push origin vX.Y.Z` (publishes that one tag — requires explicit
  approval; push the **specific** tag, never `--tags`, which also
  publishes any stray local tags)

Describe the **exact** sequence you propose to the user and wait. Do
not execute. Do not assume previous approval from earlier in the
session carries forward — restricted ops need per-instance approval.

Recommended proposal to surface:

```
git checkout main
git pull origin main                  # in case main moved since this branch was cut
git merge --no-ff <branch-name>       # the actual merge (a merge commit becomes main's tip)

# Tag the RELEASE commit, NOT the merge commit. After --no-ff the
# <branch-name> ref still points at the chore(release) commit, so tag THAT:
git tag -a vX.Y.Z <branch-name> -m "Release vX.Y.Z"

git push origin main
git push origin vX.Y.Z                 # the specific tag — NEVER --tags
```

When you surface this, tell the user what the **tag push** triggers so the
publish is not a surprise: `release.yml` fires on `v*`, **builds + pushes
the three GHCR images** (`paperhub-backend/frontend/marker:X.Y.Z` + `latest`)
and **cuts a GitHub Release** whose notes are composed from the `vX.Y.Z`
`changelog.json` entry's `en` highlights (§4b) plus GitHub's auto-generated
PR list. `push origin main` separately runs `ci.yml`. (The push to `main`
is what makes the Release notes' source available at the tagged commit, so
the tag must point at a commit that already contains the §4b changelog
entry — the `chore(release)` commit does.)

**⚠️ Never tag the merge commit — tag-triggered CD does not fire on it.**
A deploy pipeline keyed on `on: push: tags` will NOT run for a tag that
points at a `--no-ff` merge commit; the release looks shipped but never
deploys (silent). Always anchor the tag on the `chore(release): vX.Y.Z`
commit — i.e. the branch tip, which has the **identical tree** to the
merge but is a normal 1-parent commit. If a tag was mistakenly placed on
the merge commit, move it (delete-then-recreate gives CD a clean
tag-create event to fire on):

```
git push origin :refs/tags/vX.Y.Z     # delete the bad remote tag
git tag -d vX.Y.Z                      # delete it locally
git tag -a vX.Y.Z <release-commit-sha> -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

This bit the **v2.31.0** release — the tag first landed on the merge
commit and CD stayed silent until it was moved to the `chore(release)`
commit.

If a frontend rebuild or backend Docker image rebuild is needed for
deployment, NOTE that to the user but DO NOT trigger it. The deploy is
a separate downstream action with its own approval.

## Anti-patterns to refuse

- Bumping minor/major without explicit user approval when the
  branch's commit kinds are ambiguous → patch is default; ask first.
- **Bumping a manifest without regenerating its lockfile** → produces
  an inconsistent release where `pyproject.toml` says vN+1 but the
  lock says vN. The CI / a fresh `uv sync` / `npm ci` will silently
  install the wrong artifact. All three lock pairs must move together.
- Writing test counts to README before running the suites → the
  README is a public artifact; stale counts are misinformation.
- Skipping the SRS changelog row → the SRS is the authoritative
  changelog; commit messages alone are not.
- Forcing through ruff/mypy/typecheck/lint failures with `--no-verify`
  or skip-flags → if a gate fails, surface it and stop.
- Auto-executing the merge / push / tag commands → restricted ops
  always need per-instance approval, even when the user has approved
  similar ops earlier in the same session.
- **Tagging the `--no-ff` merge commit instead of the `chore(release)`
  commit** → tag-triggered CD (`on: push: tags`) does not fire on a
  merge commit, so the release silently never deploys. Always tag the
  release commit (the branch tip). Likewise `git push origin --tags`
  instead of `git push origin vX.Y.Z` → publishes stray local tags.

## Conventions reference (from CLAUDE.md)

For consistency with the wider repo conventions the skill operates
inside:

- **Commits**: Conventional Commits — `action(scope): imperative
  subject`. Use `chore(release): vX.Y.Z` for the release commit.
- **Python tooling**: `uv` only — never `pip`, `python -m venv`, or
  system python. All backend commands run as `uv run <cmd>` from
  `backend/`.
- **Shell**: PowerShell on Windows is the project default; Bash is
  also fine. The commands above use POSIX path style; on PowerShell
  swap `cd backend && uv run pytest` for
  `cd backend; uv run pytest` (or `Set-Location backend`).
- **Restricted operations** (from global CLAUDE.md): `git push`, any
  merge / rebase / cherry-pick onto a shared branch, `gh pr create` /
  `gh pr merge`, posting externally — all need explicit per-instance
  approval each time. Approval earlier in the same session does NOT
  carry forward to the merge.
