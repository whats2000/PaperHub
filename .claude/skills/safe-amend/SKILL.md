---
name: safe-amend
description: ALWAYS use BEFORE running `git commit --amend` (every time, any commit, any branch, any repo) — and before any other history-rewriting git command (`git rebase`, a `git reset` that drops/rewrites a commit, `git push --force`/`--force-with-lease`). Decides whether the rewrite is safe (the target commit is NOT yet on the remote → amend/rewrite allowed) or prohibited (the commit IS already pushed → rewriting it forces a force-push, which is banned → stack a NEW commit instead). This is NOT tied to release/merge work — it applies to ANY amend. Fires on phrases like "amend", "fix up the last commit", "squash", "rebase", "redo that commit".
---

# safe-amend — never rewrite a pushed commit

Amending (or rebasing/resetting) a commit that is **already on the remote**
forces the next push to be a `--force` push. Force-push is prohibited by the
global CLAUDE.md restricted-operations rule. This skill makes the
amend-vs-stack decision deterministic so a pushed commit is never silently
rewritten (the v2.34.0 mistake: the release commit was amended to add the
READMEs *after* it had been pushed → local diverged from `origin/main` and
could only be reconciled by a force-push).

**Core rule:** amend ONLY a commit the remote has not seen. If it's pushed,
**stack a new commit** instead.

## When this fires

Before you run ANY of:

- `git commit --amend`
- `git rebase` / `git rebase -i` (rewrites commits)
- `git reset --soft|--mixed|--hard` to a commit *below* the current branch tip
  in a way that drops/rewrites commits
- `git push --force` / `--force-with-lease`
- any "squash / fixup / reword / reorder the last commit(s)" request

## The check (run in order)

### 1. Identify the target

For `--amend`, the target is `HEAD`. For a rebase/reset, the target is the
OLDEST commit being rewritten (everything from there to `HEAD` is affected).

### 2. Refresh the remote view

```
git fetch <remote>      # usually: git fetch origin
```

Do NOT skip this — a stale remote-tracking ref can wrongly report a pushed
commit as unpushed.

### 3. Is the target already on the remote?

Determine the upstream branch and test whether the target commit is contained
in it:

```
# upstream of the current branch (e.g. origin/main). If this errors, there is
# NO upstream → nothing has been pushed → amend is ALLOWED (skip to "Allowed").
git rev-parse --abbrev-ref --symbolic-full-name @{u}

# Is the target commit already contained in the upstream?
git merge-base --is-ancestor <target> @{u} ; echo $?
```

- `merge-base --is-ancestor` exit **0** → the target IS on the remote → **PROHIBITED** (see below).
- exit **1** → the target is NOT on the remote (it's local-only / ahead of upstream) → **ALLOWED**.

A cross-check that reads clearly in a report:

```
git branch -r --contains <target>     # if it lists the upstream remote branch → pushed
git status -sb | head -1              # "[ahead N]" with no "behind" and target within those N → unpushed
```

### 4. Decide

**ALLOWED (target is unpushed):** proceed with the amend/rebase/reset as
intended. Local-only history is yours to rewrite.

**PROHIBITED (target is pushed):** do NOT amend/rebase/reset it. It would
require a force-push. Instead **stack a new commit** that contains exactly the
delta you intended to fold in:

```
# Example: you wanted to amend the pushed HEAD to add some files.
# Put those files in a fresh commit on top instead:
git add <files>
git commit -m "<conventional message describing the delta>"
# Now the branch is "ahead" of the remote by a normal commit → a plain
# (non-force) push fast-forwards it.
```

If you had ALREADY amended a pushed commit before this check (the mistake this
skill prevents), recover WITHOUT force-push:

```
git fetch origin
git reset --soft <pushed-sha>     # = origin/<branch>; moves HEAD back, keeps your delta staged
git commit -m "<message for the delta>"   # re-land the delta as a NEW commit
# local is now "ahead 1, behind 0" → a normal push fast-forwards
```

## Hard rules

- **Never** turn an amend of a pushed commit into a force-push to "make it
  work." That is the exact prohibited outcome.
- A force-push (`--force` / `--force-with-lease`) is itself a restricted op
  and needs explicit per-instance user approval REGARDLESS — this skill's job
  is to avoid ever *needing* one.
- Amending an **unpushed** commit is fine and encouraged (cleaner than a
  "fixup" commit that will never be published separately).
- When the upstream/remote state is ambiguous (detached HEAD, no upstream, a
  shared branch others may have pushed to), default to the SAFE choice:
  stack a new commit, or ask the user.
