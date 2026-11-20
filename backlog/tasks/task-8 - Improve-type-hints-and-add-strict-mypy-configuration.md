---
id: task-8
title: Improve type hints and add strict mypy configuration
status: Done
assignee: []
created_date: '2025-07-15'
labels: []
dependencies: []
---

## Description

Enhance type hints throughout the codebase and configure stricter mypy settings. Currently mypy configuration is minimal and type hints could be more comprehensive.

## Acceptance Criteria

- [x] Type hints improved for all public methods
- [x] Generic types properly annotated
- [x] Mypy configuration made stricter
- [x] Type checking errors resolved
- [x] Type stubs added where needed
- [x] Documentation updated with type information

## Implementation Plan

1. Add type stubs in `stubs/` for untyped third-party dependencies (`eventkit` / `aeventkit` and `nest_asyncio` / `anest-asyncio`) that do not ship `py.typed`.
2. Enable full `strict = true`, `python_version = "3.13"`, `mypy_path = "stubs"`, and `warn_unused_configs = true` in `pyproject.toml` `[tool.mypy]`, scoping `ignore_missing_imports` only to optional `pandas.*` and `matplotlib.*` modules.
3. Add complete strict parameter/return annotations and generic type arguments across all 14 modules in `ib_insync/`, using Python 3.13+ union/builtin generic syntax (`X | None`, `list[...]`, `dict[...]`, `Self`) and an `@overload`-typed generic `IB._run()`.
4. Fix inaccurate return types caught by strict checking (`IB.reqMatchingSymbols`, `IB.calculateImpliedVolatility`, `IB.calculateOptionPrice`, `IB.reqHistoricalTicks`, `IB.requestFA`).
5. Update `CLAUDE.md` and verify `flake8`, `mypy -p ib_insync`, `pytest -W error`, and `python -m build` across Python 3.13, 3.14, and 3.15.

## Implementation Notes

- **Approach taken:** Enabled full `strict = true` in `pyproject.toml` and resolved all 877 strict mypy errors across all 14 source files without weakening checks or scattering blanket `# type: ignore` comments.
- **Features implemented or modified:**
  - Added `stubs/eventkit/__init__.pyi` and `stubs/nest_asyncio/__init__.pyi` so `Event`/`Op` subclasses in `ticker.py` and `nest_asyncio.apply` type-check cleanly.
  - Updated `[tool.mypy]` in `pyproject.toml` to `strict = true`, `python_version = "3.13"`, `mypy_path = "stubs"`, `warn_unused_configs = true`, with `ignore_missing_imports` limited to `pandas.*` and `matplotlib.*`.
  - Added complete strict type annotations across `ib_insync/connection.py`, `objects.py` (including explicit `TagValue as TagValue` re-export), `order.py`, `contract.py`, `ticker.py`, `flexreport.py`, `ibcontroller.py`, `util.py`, `decoder.py`, `wrapper.py`, `client.py`, and `ib.py`.
  - Added `@overload`-typed generic `IB._run()` (`Awaitable[_T] -> _T`) which surfaced and fixed inaccurate return annotations on `reqMatchingSymbols`, `calculateImpliedVolatility`, `calculateOptionPrice`, `reqHistoricalTicks`, and `requestFA`.
  - Updated `CLAUDE.md` with strict MyPy and `stubs/` documentation.
- **Technical decisions and trade-offs:** Used `fields: list[Any]` in `Decoder` wire handlers because `Decoder` intentionally unpacks raw wire strings onto typed dataclass attributes before coercing them in `Decoder.parse()`; kept `cast(...)` strictly at `Future[Any].result()` boundaries in `IB` async methods.
- **Modified or added files:** `pyproject.toml`, `stubs/eventkit/__init__.pyi`, `stubs/nest_asyncio/__init__.pyi`, `ib_insync/client.py`, `ib_insync/connection.py`, `ib_insync/contract.py`, `ib_insync/decoder.py`, `ib_insync/flexreport.py`, `ib_insync/ib.py`, `ib_insync/ibcontroller.py`, `ib_insync/objects.py`, `ib_insync/order.py`, `ib_insync/ticker.py`, `ib_insync/util.py`, `ib_insync/wrapper.py`, `CLAUDE.md`.
