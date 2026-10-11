# Sample repository

A tiny, self-contained Python project used by the Phase 4 coding-agent tests
(T085). It is deliberately real — a module, a test, a README — so an end-to-end
coding task can be pointed at a genuine git repository: the tests copy this
directory, `git init` it, register it as a project, and run a coding agent over
an isolated clone of it.

There is no `.git` here on purpose: the copy step creates the repository, so the
fixture stays a plain set of files that git would otherwise treat as a
submodule.

## Layout

- `calculator.py` — the code under change.
- `test_calculator.py` — the tests the verification runs.
- `requirements.txt` — intentionally empty of third-party deps; the tests run
  with the interpreter's standard library and pytest.

Run the suite the way the coding agent's verification would:

```sh
python -m pytest -q
```
