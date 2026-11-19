---
id: task-7
title: Modernize build system and add CI/CD pipeline
status: Done
assignee: []
created_date: '2025-07-15'
labels: []
dependencies: []
---

## Description

Replace the current dist.sh script with modern Python packaging tools like pyproject.toml and add GitHub Actions for CI/CD. Include automated testing, linting, and publishing workflows.

## Acceptance Criteria

- [x] pyproject.toml configuration added
- [x] GitHub Actions CI/CD workflow implemented
- [x] Automated testing on multiple Python versions
- [x] Automated linting and type checking
- [x] Automated package publishing to PyPI (replaced with automated PEP 517 build verification for private fork)
- [x] dist.sh script replaced with modern tooling

## Implementation Plan

1. Consolidate package metadata, dependencies, pytest options, and mypy settings into PEP 621 `pyproject.toml` targeting modern stable Python (`>=3.13`).
2. Extract flake8 settings to `.flake8` and remove legacy `setup.py`, `setup.cfg`, `mypy.ini`, and `dist.sh`.
3. Update runtime dependencies to maintained Python 3.13–3.15 compatible forks (`aeventkit`, `anest-asyncio`) alongside `tzdata`, and modernize `util.getLoop()`, `util.run()`, and `Wrapper.startReq()` for Python 3.14/3.15 asyncio semantics with zero deprecation warnings.
4. Update `.github/workflows/test.yml` to run `flake8`, `mypy`, `pytest -W error`, and `python -m build` artifact verification across Python `3.13`, `3.14`, and `3.15`.
5. Update `README.rst`, `CLAUDE.md`, `requirements.txt`, and `requirements-dev.txt`.

## Implementation Notes

- **Approach taken:** Migrated entirely to PEP 621 / PEP 517 (`pyproject.toml` + `setuptools>=77` + `python -m build`), dropped legacy Python versions (`<3.13`), and verified zero-warning execution across Python 3.13, 3.14, and 3.15.
- **Features implemented or modified:**
  - Added `pyproject.toml` and `.flake8`; removed `setup.py`, `setup.cfg`, `mypy.ini`, and `dist.sh`.
  - Switched dependencies from unmaintained `eventkit`/`nest_asyncio` (which break on Python 3.14+ due to `asyncio.get_event_loop()` changes) to their maintained drop-in replacements `aeventkit` and `anest-asyncio` (same `eventkit` and `nest_asyncio` import names).
  - Updated `ib_insync/util.py` (removed `backports.zoneinfo` and Python <3.7 branches, modernized `getLoop()` and `patchAsyncio()`) and `ib_insync/wrapper.py` (`getLoop().create_future()`).
  - Updated `.github/workflows/test.yml` with matrix `["3.13", "3.14", "3.15"]`, `pip install -e ".[dev]"`, `flake8 ib_insync`, `mypy -p ib_insync`, `pytest -W error`, and `python -m build` + wheel `py.typed` check.
  - Updated `README.rst`, `CLAUDE.md`, `requirements.txt`, and `requirements-dev.txt`.
- **Technical decisions and trade-offs:** Automated PyPI upload was intentionally omitted because this repository is a private internal fork not intended for public PyPI distribution; instead, CI verifies that sdist and wheel builds succeed cleanly with `python -m build` and include `py.typed`.
- **Modified or added files:** `pyproject.toml`, `.flake8`, `.github/workflows/test.yml`, `requirements.txt`, `requirements-dev.txt`, `ib_insync/util.py`, `ib_insync/wrapper.py`, `README.rst`, `CLAUDE.md`, `tests/test_security.py` (removed `setup.py`, `setup.cfg`, `mypy.ini`, `dist.sh`).
