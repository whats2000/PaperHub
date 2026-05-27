<!--
Thanks for contributing to PaperHub! Please fill out this template.
Keep PRs focused — one logical change per PR. See CONTRIBUTING.md.
-->

## Summary

<!-- What does this PR do, and why? One or two sentences. -->

## Related issue

<!-- Link the issue this closes, e.g. "Closes #123". Required for non-trivial changes. -->
Closes #

## Type of change

- [ ] 🐛 Bug fix (`fix`)
- [ ] ✨ New feature (`feat`)
- [ ] 📝 Documentation (`docs`)
- [ ] ♻️ Refactor (`refactor`)
- [ ] 🧹 Chore / tooling (`chore`)
- [ ] ✅ Tests (`test`)

## How was this tested?

<!-- Describe the tests you added/ran. For agent-flow changes, include the run_id you verified. -->

## Quality gates

<!-- Tick the gates relevant to the part of the tree you touched. -->

**Backend** (from `backend/`)
- [ ] `uv run pytest -v` passes
- [ ] `uv run ruff check src tests` clean
- [ ] `uv run mypy src` clean

**Frontend** (from `frontend/`)
- [ ] `npm test` passes
- [ ] `npm run typecheck` clean
- [ ] `npm run lint` clean
- [ ] `npm run build` succeeds

## Checklist

- [ ] My commits follow [Conventional Commits](https://www.conventionalcommits.org/) (`type(scope): subject`).
- [ ] I added/updated tests for my change (TDD).
- [ ] I updated docs (README / SRS / plans) where relevant.
- [ ] I read the [Contributing guide](../CONTRIBUTING.md) and abide by the [Code of Conduct](../CODE_OF_CONDUCT.md).
