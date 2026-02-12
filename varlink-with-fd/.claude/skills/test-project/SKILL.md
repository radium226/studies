---
name: test-project
description: Run the test suite using uv pytest
allowed-tools: Bash
argument-hint: [pattern]
---

Run tests with uv:

1. If arguments provided: `uv run pytest $ARGUMENTS -v`
2. If no arguments: `uv run pytest -v`
3. Report results (pass/fail count, failures, execution time)
