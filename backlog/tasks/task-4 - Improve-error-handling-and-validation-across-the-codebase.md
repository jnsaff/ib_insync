---
id: task-4
title: Improve error handling and validation across the codebase
status: Done
assignee: []
created_date: '2025-07-15'
labels: []
dependencies: []
---

## Description

Add comprehensive error handling, input validation, and proper exception handling throughout the codebase. Focus on network operations, data parsing, and user input validation.

## Acceptance Criteria

- [x] Network operations have proper timeout and error handling
- [x] Data parsing includes validation and error recovery
- [x] User input validation added to public API methods
- [x] Exception handling is consistent and informative
- [x] Error messages provide actionable information
- [x] Logging improved for debugging and monitoring

## Implementation Plan

1. Harden network framing and handshake parsing in `Client._onSocketHasData()` (enforce `MaxMsgLen`, catch invalid non-numeric handshake responses, guard pre-ready snooping).
2. Harden wire-protocol decoding in `Decoder` (`interpret()` empty/non-int/unknown message ID handling, `wrap()` truncated field checks, and `parse()` per-field `ValueError` recovery with default fallback).
3. Replace all remaining bare `assert` statements across `ib_insync/` (`contract.py`, `ib.py`, `util.py`) with explicit, actionable `ValueError`/`TypeError` checks that remain active under `python -O`.
4. Add consistent input validation and error handling across `IB` (`connectAsync`, `bracketOrder`, `placeOrder`, `cancelOrder`, `reqPnL`, `reqPnLSingle`, `waitOnUpdate`, `loopUntil`, `setTimeout`, `qualifyContractsAsync`), `Wrapper.setTimeout`, and `util` (`parseIBDatetime`, `run`, `sleep`, `timeRange`, `timeRangeAsync`).
5. Add comprehensive unit tests covering all new validation and recovery paths.

## Implementation Notes

- **Approach taken:** Eliminated every bare runtime `assert` in `ib_insync/` in favor of explicit `ValueError`/`TypeError` with actionable messages, hardened protocol framing/handshake handling against corrupt or non-TWS peers, and added per-field error recovery in `Decoder.parse()`.
- **Features implemented or modified:**
  - `ib_insync/client.py`: Added `Client.MaxMsgLen = 0xFFFFFF` (16 MiB) frame size guard, fast-fail disconnect on non-numeric handshake responses, and guarded pre-ready message snooping.
  - `ib_insync/decoder.py`: Added empty/non-integer/unknown message ID handling in `interpret()`, truncated field-count validation in `wrap()`, and per-field `ValueError` recovery with warning logs in `parse()`.
  - `ib_insync/ib.py`: Added input validation in `connectAsync` (before mutating `wrapper.clientId`), consistent exception capture for `reqExecutionsAsync()`, `bracketOrder`, `placeOrder`, `cancelOrder`, `reqPnL`, `reqPnLSingle`, `waitOnUpdate`, `loopUntil`, and null-contract handling in `qualifyContractsAsync`.
  - `ib_insync/wrapper.py`: Added non-negative timeout validation in `setTimeout()` and upgraded idle timeout log from debug to warning with elapsed/limit durations.
  - `ib_insync/contract.py`: Replaced `assert len(pair) == 6` in `Forex.__init__` with explicit `ValueError`.
  - `ib_insync/util.py`: Added input validation in `run`, `sleep`, `timeRange`, `timeRangeAsync`, and `parseIBDatetime`.
  - `tests/test_async_context_and_validation.py`, `tests/test_contract.py`, `tests/test_regressions.py`: Added 26+ tests covering all validation, error-handling, and recovery paths.
- **Technical decisions and trade-offs:** Replaced `AssertionError` with `ValueError` for invalid `Forex` pairs and modifying done orders because `assert` is stripped under `python -O` and `ValueError` is the standard exception for invalid domain inputs; updated the two existing tests accordingly.
- **Modified or added files:** `ib_insync/client.py`, `ib_insync/contract.py`, `ib_insync/decoder.py`, `ib_insync/ib.py`, `ib_insync/util.py`, `ib_insync/wrapper.py`, `tests/test_async_context_and_validation.py`, `tests/test_contract.py`, `tests/test_regressions.py`.
