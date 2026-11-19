---
id: task-2
title: Improve security in setup.py and flexreport.py
status: Done
assignee: []
created_date: '2025-07-15'
labels: []
dependencies: []
---

## Description

Replace unsafe exec() call in setup.py with safer alternatives and add proper input validation to FlexReport URL handling. Implement secure HTTP request handling with timeout and error checking.

## Acceptance Criteria

- [x] exec() call in setup.py replaced with secure version reading
- [x] FlexReport URL handling includes proper validation and error checking
- [x] HTTP requests use secure timeout and error handling
- [x] Input validation added to network-facing components
- [x] Security best practices implemented for file and network operations

## Implementation Plan

1. Replace `setup.py` (and its `exec(f.read())` call) with PEP 621 `pyproject.toml` using static AST-based version extraction (`[tool.setuptools.dynamic] version = { attr = "ib_insync.version.__version__" }`) and a static string literal in `ib_insync/version.py`.
2. Harden `ib_insync/flexreport.py` with strict parameter validation, HTTPS + Interactive Brokers domain allowlist validation for returned statement URLs, explicit `FlexError` handling for HTTP/URL/OS/XML errors without leaking tokens, and path validation for `load()`/`save()`.
3. Add input validation for `host`, `port`, `clientId`, and `timeout` in `Client.connectAsync()`.
4. Replace unsafe `eval()` calls in `examples/qt_ticker_table.py` and `examples/tk.py` with an AST-based allowlisted contract parser.
5. Add comprehensive unit tests covering URL/parameter validation, token redaction on HTTP errors, client parameter validation, and safe contract parsing.

## Implementation Notes

- **Approach taken:** Eliminated `setup.py` altogether in favor of `pyproject.toml` with a static `__version__` literal in `ib_insync/version.py` so `setuptools` reads the version via AST without executing code. Hardened `FlexReport` and `Client.connectAsync()` network boundaries and removed `eval()` from example scripts.
- **Features implemented or modified:**
  - Static `__version__ = '0.9.86'` in `ib_insync/version.py` and dynamic metadata in `pyproject.toml` (removed `setup.py`).
  - `_validate_param`, `_validate_flex_url` (HTTPS-only, IB domain allowlist, rejects credentials/query/fragment/control chars), `_fetch_url` (sanitized `HTTPError`/`URLError`/`OSError` wrapping with `err.close()` and no token leakage), and `_parse_xml` in `ib_insync/flexreport.py`.
  - Parameter validation for `host`, `port`, `clientId`, and `timeout` in `ib_insync/client.py`.
  - AST-based `parse_contract()` replacing `eval()` in `examples/qt_ticker_table.py` and `examples/tk.py` (also fixed `'Disonnect'` typo).
  - Unit tests in `tests/test_security.py`.
- **Technical decisions and trade-offs:** Restricted Flex redirect URLs to HTTPS on official Interactive Brokers domains (`interactivebrokers.com` and regional IB TLDs) to prevent SSRF/scheme injection (`file://`, `http://`, arbitrary hosts).
- **Modified or added files:** `ib_insync/version.py`, `ib_insync/flexreport.py`, `ib_insync/client.py`, `examples/qt_ticker_table.py`, `examples/tk.py`, `pyproject.toml`, `tests/test_security.py`, `tests/test_regressions.py` (removed `setup.py`).
