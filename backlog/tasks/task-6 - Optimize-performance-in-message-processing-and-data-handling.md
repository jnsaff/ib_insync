---
id: task-6
title: Optimize performance in message processing and data handling
status: Done
assignee: []
created_date: '2025-07-15'
labels: []
dependencies: []
---

## Description

Improve performance in the decoder and message processing pipeline. Optimize data structure operations and reduce memory allocations in high-frequency operations like tick data processing.

## Acceptance Criteria

- [x] Message decoding performance improved
- [x] Data structure operations optimized
- [x] Memory allocations reduced in tick processing
- [x] Benchmark tests added for performance validation
- [x] Performance regression tests implemented
- [x] CPU and memory profiling results show improvement

## Implementation Plan

1. Profile the end-to-end TCP framing, decoding, tick processing, serialization, and utility pipelines with `cProfile` and `tracemalloc` to establish baseline metrics.
2. Replace O(M^2) repeated bytes slicing in `Client._onSocketHasData` with an O(N) cursor over `bytes`/`bytearray` (with zero-copy fast path when no trailing partial frame is buffered) and replace `io.StringIO` serialization in `Client.send` with a direct list join and precompiled `struct.Struct('>I')`.
3. Optimize `Decoder`: add string-keyed fast-path dispatch in `interpret`, inline hot-path converters in `wrap`, cache per-class numeric/bool field metadata in `parse`, eliminate O(N^2) `*fields = fields` copies and `fields.pop(0)` shifts in `openOrder`/`completedOrder`/`contractDetails`/`bondContractDetails`/`scannerData`/`symbolSamples`, use stride indexing in `historicalData`/`historicalTicks*`, and hoist regex/tick-map constants.
4. Optimize high-frequency tick/bar handling and memory footprint: add `@dataclass(slots=True)` to high-frequency data records (`BarData`, `RealTimeBar`, `TickAttrib*`, `Execution`, `CommissionReport`, `HistogramData`, `HistoricalSession`, `TradeLogEntry`, `Bar`), switch `Ticker` to `@dataclass(eq=False)` for C-level identity hashing/equality, avoid churning empty tick lists in `Wrapper.tcpDataArrived`, and replace long `if/elif` chains in `Wrapper` with module-level lookup tables.
5. Optimize `util.py`: add patch-safe dict caching to `getZoneInfo`, C-level `datetime.fromisoformat` fast paths to `parseIBDatetime`, and cached dataclass field metadata supporting both standard and slotted dataclasses.
6. Add benchmark, memory allocation, linearity, and correctness regression tests in `tests/test_performance.py` and verify across Python 3.13, 3.14, and 3.15.

## Implementation Notes

- **Profiling results (CPython 3.15.2, before vs. after):**
  - Combined hot-path `cProfile`: **482,444 -> 177,344 function calls (-63.2%)**, **224 ms -> 107 ms (2.09x faster)**.
  - `Decoder.parse`: **66 ms -> 8 ms cumtime (8.25x faster)**.
  - `HistoricalData` (3,000 bars with legacy timezone): **140.3 ms -> 35.7–41.2 ms (~3.5x faster)**, peak memory **1196.6 KiB -> 866.9 KiB (-27.5%)**.
  - `HistoricalTicksBidAsk` (5,000 ticks): peak memory **1952.7 KiB -> 1522.9 KiB (-22.0%)**.
  - `openOrder` decode (300x): **23.9 ms -> 12.5–18.3 ms (~1.5–1.9x faster)**, peak memory **7.7 KiB -> 6.1 KiB (-20.8%)**.
  - `contractDetails` decode (500x): **21.5 ms -> 12.8–14.3 ms (~1.5–1.7x faster)**.
  - `Client.send` serialization (5,000x): **110.9 ms -> 73.8–86.2 ms (~1.3–1.5x faster)**.
  - `Client._onSocketHasData` burst (2,000 msgs/1 pkt): **27.7 ms -> 18.5 ms (1.50x faster)** with O(N) burst scaling.
  - Dataclass + datetime utilities: **661.4 ms -> 108.3–109.5 ms (6.0x faster)**, peak memory **3.4 KiB -> 0.6 KiB (-82%)**.
- **Modified files:** `ib_insync/client.py`, `ib_insync/decoder.py`, `ib_insync/wrapper.py`, `ib_insync/objects.py`, `ib_insync/ticker.py`, `ib_insync/util.py`.
- **Added tests:** `tests/test_performance.py` (16 tests covering fragmented TCP framing, disconnect-mid-packet safety, slotted dataclasses, `parseIBDatetime` fast paths & fallbacks, O(N) burst scaling, throughput benchmarks, and `tracemalloc` footprint/bounded-memory regression tests).
- **Verified:** `pytest -W error` (298 passed, 4 skipped, 2 deselected), `mypy -p ib_insync` (0 issues), and `flake8` (0 issues) on Python 3.13, 3.14, and 3.15.
