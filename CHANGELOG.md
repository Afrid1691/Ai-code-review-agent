# Changelog

All notable changes to the AI Code Review Agent, newest first.

## 2026-11-27

- New demo-mode security rule: the Security reviewer now flags dynamic
  code execution with `eval()` and `exec()` as a high-severity finding,
  with guidance to parse caller input into a fixed set of operations
  (or use `ast.literal_eval` for plain data) instead of evaluating it.
- Planted an `eval()` call in `examples/sample_pr.diff` and refreshed
  `examples/expected_review.md` (now 8 findings: 2 critical, 3 high,
  3 low).
- Added tests: the planted `eval()` is caught as a high-severity
  Security finding, and a standalone diff with `exec()` is flagged too.
- README: the Security reviewer description and the sample-diff
  walkthrough now mention the new rule.

## 2026-11-25

- Added a `--min-severity` CLI filter (and matching `min_severity`
  field on `POST /review`): only findings at the given severity or
  above are reported, and the verdict follows the filtered set.
- Added a GitHub Actions CI workflow running the test suite on
  Python 3.11 and 3.12.
- Added the MIT license.
- Added README badges (CI status, Python version, license).
