---
id: task-3
title: Add async context manager support for IB class
status: Done
assignee: []
created_date: '2025-07-15'
labels: []
dependencies: []
---

## Description

Implement async context manager (__aenter__/__aexit__) for the IB class to properly handle connection lifecycle and resource cleanup. This will allow for cleaner 'async with' usage patterns.

## Acceptance Criteria

- [x] IB class implements __aenter__ and __aexit__ methods
- [x] Context manager properly handles connection establishment and cleanup
- [x] Documentation updated with async context manager examples
- [x] Tests added for context manager functionality
- [x] Backwards compatibility maintained with existing connect/disconnect methods

## Implementation Plan

1. Implement `__aenter__` and `__aexit__` on `IB` mirroring the synchronous `__enter__`/`__exit__` lifecycle while keeping `connectAsync` as a genuine coroutine function.
2. Add `@asynccontextmanager` method `IB.connectContext(...)` that awaits `connectAsync(...)`, yields `self`, and guarantees `disconnect()` in `finally:` on normal exit, exceptions, or cancellation.
3. Update `IB.connectContext` docstring and `README.rst` with an async context manager example.
4. Add unit tests covering `__aenter__`/`__aexit__`, `async with await ib.connectAsync(...)`, `ib.connectContext(...)` (normal exit, body exception, cancellation, connection failure), and sync `with IB()` backward compatibility.

## Implementation Notes

- **Approach taken:** Implemented `__aenter__`/`__aexit__` directly on `IB` and added `IB.connectContext(...)` as an `@asynccontextmanager` instance method so callers can both attach event handlers before entering (`ib = IB(); ib.errorEvent += ...; async with ib.connectContext(...):`) and write concise one-liners (`async with IB().connectContext(...) as ib:`), while keeping `connectAsync()` a standard `async def` coroutine function.
- **Features implemented or modified:**
  - `IB.__aenter__`, `IB.__aexit__`, and `IB.connectContext` in `ib_insync/ib.py`.
  - Async context manager example in `README.rst`.
  - Unit tests in `tests/test_async_context_and_validation.py`.
- **Technical decisions and trade-offs:** Kept `connectAsync()` as a plain `async def` rather than returning a custom dual awaitable/context-manager wrapper so `asyncio.create_task(ib.connectAsync(...))`, `inspect.iscoroutinefunction`, and existing mocks/callers remain 100% compatible.
- **Modified or added files:** `ib_insync/ib.py`, `README.rst`, `tests/test_async_context_and_validation.py`.
