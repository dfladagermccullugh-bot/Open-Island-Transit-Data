## What this changes

## Why

## Does it change a published number or file?

<!-- If it changes how a number is computed: a test that failed before and
passes now, a version bump for the stage, and lines in docs/decisions.md,
docs/methods.md and docs/codebook.md. If it should change nothing, say what
scripts/replay_diff.py showed. -->

## Checks run

- [ ] `ruff check . && ruff format --check .` and `mypy`
- [ ] `pytest`
- [ ] `scripts/test_site.sh` (if the site changed)
