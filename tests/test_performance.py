"""Performance benchmarks, memory allocation checks, and regression tests."""

import datetime as dt
import struct
import time
import tracemalloc
from unittest.mock import MagicMock

import pytest

from ib_insync import (
    IB, BarData, CommissionReport, Contract, Execution, RealTimeBar,
    TickAttrib, TickAttribBidAsk, TickAttribLast, Ticker)
from ib_insync.client import Client
from ib_insync.decoder import Decoder
from ib_insync.order import PriceCondition
from ib_insync.ticker import Bar
from ib_insync.util import (
    UNSET_DOUBLE, UNSET_INTEGER, dataclassAsDict, dataclassAsTuple,
    dataclassNonDefaults, dataclassUpdate, getZoneInfo, parseIBDatetime)


def _frame(fields: list[str]) -> bytes:
    payload = ('\0'.join(fields) + '\0').encode()
    return struct.pack('>I', len(payload)) + payload


def _build_tick_burst(num_msgs: int) -> bytes:
    chunks: list[bytes] = []
    for i in range(num_msgs):
        m = i % 6
        if m == 0:
            chunks.append(_frame([
                '1', '6', '1', '1', f'{150.0 + (i % 100) * 0.01:.2f}',
                str(100 + (i % 50)), '1']))
        elif m == 1:
            chunks.append(_frame([
                '1', '6', '1', '2', f'{150.05 + (i % 100) * 0.01:.2f}',
                str(200 + (i % 50)), '1']))
        elif m == 2:
            tt = ('0', '3', '8', '21')[i % 4]
            chunks.append(_frame(['2', '6', '1', tt, str(50 + i)]))
        elif m == 3:
            val = (
                f'{150.02 + (i % 50) * 0.01:.2f};{10 + (i % 20)};'
                f'1700000000123;{100000 + i};150.01;true')
            chunks.append(_frame(['46', '6', '1', '48', val]))
        elif m == 4:
            chunks.append(_frame(['45', '6', '1', '24', '0.2531']))
        else:
            chunks.append(_frame([
                '99', '1', '1', '1700000000',
                f'{150.03 + (i % 50) * 0.01:.2f}', '25', '1', 'ISLAND', '']))
    return b''.join(chunks)


def _build_open_order_fields() -> list[str]:
    return [
        # header + contract + core order (34)
        '5', '100', '265598', 'AAPL', 'STK', '', '0', '', '', 'SMART', 'USD',
        'AAPL', 'NMS', 'BUY', '100', 'LMT', '150.0', '0.0', 'DAY', '',
        'DU123456', 'O', '0', '', '1', '99999', '0', '0', '0', '', '', '',
        '', '',
        # modelCode..deltaNeutralAuxPrice (29)
        '', '', '', '', '', '0', '', '-1', '0', '', '', '', '', '', '0',
        '0', '0', '0', '', '0', '0', '0', '', '0', '0', '', '', '', '',
        # continuousUpdate..comboLegsDescrip (7)
        '0', '', '', '', '', '', '',
        # numLegs, numOrderLegs, numParams (3)
        '0', '0', '0',
        # scaleInitLevelSize, scaleSubsLevelSize, increment (3)
        '', '', '',
        # hedgeType (1)
        '',
        # optOutSmartRouting..dncPresent (5)
        '0', '', '', '0', '0',
        # algoStrategy (1)
        '',
        # solicited..randomizePrice (19)
        '0', '0', 'Submitted', '1000', '1000', '1000', '0', '0', '0',
        '1000', '1000', '1000', '1.0', '', '', 'USD', '', '0', '0',
        # numConditions (1)
        '0',
        # adjustedOrderType..usePriceMgmtAlgo (16)
        '', '', '', '', '', '', '', '', '', '', '', '', '0', '0', '', '0',
        # duration, postToAts, autoCancelParent (3)
        '', '', '0',
        # minTradeQty..midOffsetAtHalf (5)
        '', '', '', '', '',
    ]


def _build_historical_data_fields(num_bars: int) -> list[str]:
    fields = [
        '17', '5', '20231201 09:30:00', '20231215 16:00:00', str(num_bars)]
    for i in range(num_bars):
        day = 1 + (i // 390) % 28
        minute_total = 9 * 60 + 30 + (i % 390)
        h, m = divmod(minute_total, 60)
        fields.extend([
            f'202312{day:02d} {h:02d}:{m:02d}:00 US/Eastern',
            '150.10', '150.50', '149.90', '150.25', '12345', '150.20', '42'
        ])
    return fields


def _make_ready_ib() -> tuple[IB, Contract, Ticker]:
    ib = IB()
    ib.client._serverVersion = 178
    ib.client.decoder.serverVersion = 178
    ib.client._apiReady = True
    contract = Contract(
        conId=265598, symbol='AAPL', secType='STK',
        exchange='SMART', currency='USD')
    ticker = ib.wrapper.startTicker(1, contract, 'mktData')
    return ib, contract, ticker


class TestOptimizationCorrectness:
    """Verify semantic parity of optimized framing, decoding, and utils."""

    def test_fragmented_tcp_framing_reconstructs_all_messages(self):
        ib, _, ticker = _make_ready_ib()
        packet = _build_tick_burst(24)

        # Feed 7-byte chunks so headers and payloads split across boundaries.
        received_ticks = 0
        for offset in range(0, len(packet), 7):
            ib.client._onSocketHasData(packet[offset:offset + 7])
            received_ticks += len(ticker.ticks) + len(ticker.tickByTicks)

        assert ib.client._data == b''
        assert ib.client._numMsgRecv == 24
        assert received_ticks == 24

    def test_disconnect_mid_packet_stops_cleanly(self):
        ib, _, _ = _make_ready_ib()
        ib.client.connState = Client.CONNECTED
        called: list[int] = []

        def stop_on_first(reqId: int) -> None:
            called.append(reqId)
            ib.client.reset()

        ib.wrapper.tickSnapshotEnd = (  # type: ignore[method-assign]
            stop_on_first)
        packet = _frame(['57', '1', '10']) + _frame(['57', '1', '11'])
        ib.client._onSocketHasData(packet)

        assert called == [10]
        assert ib.client._data == b''

    def test_slotted_dataclasses_and_dataclass_update(self):
        for cls in (
            BarData, RealTimeBar, TickAttrib, TickAttribBidAsk,
            TickAttribLast, CommissionReport, Execution, Bar
        ):
            inst = cls() if cls is not Bar else cls(None)
            assert not hasattr(inst, '__dict__')

        dst = CommissionReport(execId='E1', commission=1.0, currency='USD')
        src = CommissionReport(
            execId='E1', commission=2.5, currency='EUR', realizedPNL=42.0)
        dataclassUpdate(dst, src, yield_=0.5)
        assert dst.commission == 2.5
        assert dst.currency == 'EUR'
        assert dst.realizedPNL == 42.0
        assert dst.yield_ == 0.5
        assert dataclassAsDict(dst)['commission'] == 2.5
        assert dataclassAsTuple(dst)[1] == 2.5
        assert 'commission' in dataclassNonDefaults(dst)

    def test_ticker_identity_equality_and_hashability(self):
        t1 = Ticker()
        t2 = Ticker()
        assert t1 == t1
        assert t1 != t2
        s = {t1, t2}
        assert len(s) == 2
        assert t1 in s and t2 in s

    def test_tcp_data_arrived_avoids_churning_empty_lists(self):
        ib, _, ticker = _make_ready_ib()
        # Only populate level-1 ticks; tickByTicks and domTicks remain empty.
        orig_tbt = ticker.tickByTicks
        orig_dom = ticker.domTicks
        ib.client._onSocketHasData(
            _frame(['1', '6', '1', '1', '150.00', '100', '1']))
        prev_ticks = ticker.ticks
        assert len(prev_ticks) == 1

        # Next packet replaces non-empty ticks but keeps untouched empty lists.
        ib.client._onSocketHasData(
            _frame(['1', '6', '1', '1', '150.05', '100', '1']))
        assert ticker.ticks is not prev_ticks
        assert len(prev_ticks) == 1
        assert ticker.tickByTicks is orig_tbt
        assert ticker.domTicks is orig_dom

    def test_parse_ib_datetime_fast_paths_and_fallbacks(self):
        tz_ny = getZoneInfo('America/New_York')
        assert parseIBDatetime('20231215') == dt.date(2023, 12, 15)
        assert parseIBDatetime('20231215 10:30:00 US/Eastern') == dt.datetime(
            2023, 12, 15, 10, 30, 0, tzinfo=tz_ny)
        assert parseIBDatetime('20231215  10:30:00 US/Eastern') == dt.datetime(
            2023, 12, 15, 10, 30, 0, tzinfo=tz_ny)
        assert parseIBDatetime('20231215-10:30:00 US/Eastern') == dt.datetime(
            2023, 12, 15, 10, 30, 0, tzinfo=tz_ny)
        assert parseIBDatetime('20231215  10:30:00') == dt.datetime(
            2023, 12, 15, 10, 30, 0)
        assert parseIBDatetime('20231215-10:30:00') == dt.datetime(
            2023, 12, 15, 10, 30, 0)
        assert parseIBDatetime('2023-12-15 10:30:00.0') == dt.datetime(
            2023, 12, 15, 10, 30, 0)
        assert parseIBDatetime('1700000000') == dt.datetime.fromtimestamp(
            1700000000, dt.timezone.utc)
        with pytest.raises(ValueError):
            parseIBDatetime('20230230 10:30:00 US/Eastern')
        with pytest.raises(ValueError):
            parseIBDatetime('2023-W01 10:30:00 US/Eastern')

    def test_decoder_open_order_with_condition_and_contract_details_unicode(
            self):
        wrapper = MagicMock()
        decoder = Decoder(wrapper, 178)
        # Verify openOrder with a PriceCondition (condType=1, 6 fields).
        oo = _build_open_order_fields()
        # numConditions is at index 102 (34+29+7+3+3+1+5+1+19).
        assert oo[102] == '0'
        oo_with_cond = (
            oo[:102]
            + ['1', '1', 'a', '1', '155.5', '265598', 'SMART', '0', '1', '0']
            + oo[103:])
        decoder.interpret(oo_with_cond)
        wrapper.openOrder.assert_called_once()
        _, _, order, state = wrapper.openOrder.call_args[0]
        assert order.orderId == 100
        assert order.lmtPrice == 150.0
        assert state.status == 'Submitted'
        assert len(order.conditions) == 1
        assert isinstance(order.conditions[0], PriceCondition)
        assert order.conditions[0].price == 155.5

        # Verify contractDetails decodes unicode escape only when present.
        cd_fields = [
            '10', '42', 'AAPL', 'STK', '20250117 16:00:00 US/Eastern', '0', '',
            'SMART', 'USD', 'AAPL', 'NMS', 'NMS', '265598', '0.01', '1',
            'LMT', 'SMART', '1', '0', r'CAF\u00c9 INC', 'NASDAQ', '',
            'Tech', 'Comp', 'Comp', 'US/Eastern', '', '', '', '0', '0',
            '1', '', '', '26', '', 'COMMON', '1', '1', '1'
        ]
        decoder.interpret(cd_fields)
        wrapper.contractDetails.assert_called_once()
        _, cd = wrapper.contractDetails.call_args[0]
        assert cd.longName == 'CAFÉ INC'

    def test_client_send_parity_with_contract_and_unset_values(self):
        ib, contract, _ = _make_ready_ib()
        ib.client.connState = Client.CONNECTED
        sent: list[str] = []
        ib.client.sendMsg = sent.append  # type: ignore[method-assign]

        ib.client.send(
            1, contract, None, UNSET_INTEGER, UNSET_DOUBLE,
            float('inf'), True, False, 42, 3.5)
        assert len(sent) == 1
        fields = sent[0].split('\0')[:-1]
        assert fields[0] == '1'
        assert fields[1:13] == [
            '265598', 'AAPL', 'STK', '', '0.0', '', '',
            'SMART', '', 'USD', '', '']
        assert fields[13:] == ['', '', '', 'Infinite', '1', '0', '42', '3.5']


class TestPerformanceBenchmarksAndRegressions:
    """Throughput, linearity, and memory allocation regression tests."""

    def test_tcp_framing_scales_linearly_with_burst_size(self):
        """Guard against quadratic buffer copying in _onSocketHasData."""
        ib, _, _ = _make_ready_ib()
        pkt_small = _build_tick_burst(1500)
        pkt_large = _build_tick_burst(6000)  # 4x larger burst

        # Warmup
        ib.client._onSocketHasData(pkt_small)

        t0 = time.perf_counter()
        ib.client._onSocketHasData(pkt_small)
        t_small = time.perf_counter() - t0

        t0 = time.perf_counter()
        ib.client._onSocketHasData(pkt_large)
        t_large = time.perf_counter() - t0

        # O(M) scaling expects ~4x; O(M^2) would be ~12-16x.
        assert t_large < max(t_small * 8.0, 0.25)
        assert ib.client._data == b''

    def test_tick_burst_decoding_throughput(self):
        ib, _, _ = _make_ready_ib()
        packet = _build_tick_burst(2000)
        ib.client._onSocketHasData(packet)  # warmup

        t0 = time.perf_counter()
        ib.client._onSocketHasData(packet)
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.5

    def test_historical_data_decoding_throughput(self):
        ib, _, _ = _make_ready_ib()
        fields = _build_historical_data_fields(2000)
        ib.wrapper._results[5] = []
        ib.client.decoder.interpret(fields)  # warmup

        bars: list[BarData] = []
        ib.wrapper._results[5] = bars
        t0 = time.perf_counter()
        ib.client.decoder.interpret(fields)
        elapsed = time.perf_counter() - t0

        assert len(bars) == 2000
        assert elapsed < 0.5

    def test_open_order_decoding_throughput(self):
        ib, _, _ = _make_ready_ib()
        fields = _build_open_order_fields()
        dec = Decoder(ib.wrapper, 178)
        dec.interpret(fields)  # warmup

        t0 = time.perf_counter()
        for _ in range(200):
            dec.interpret(fields)
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.5

    def test_client_send_serialization_throughput(self):
        ib, contract, _ = _make_ready_ib()
        ib.client.connState = Client.CONNECTED
        ib.client.sendMsg = lambda _msg: None  # type: ignore[method-assign]

        t0 = time.perf_counter()
        for i in range(2000):
            ib.client.send(
                1, 11, i, contract, '', False, False, [],
                UNSET_INTEGER, UNSET_DOUBLE, True, 123.45)
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.5

    def test_parse_ib_datetime_throughput(self):
        t0 = time.perf_counter()
        for _ in range(5000):
            parseIBDatetime('20231215 10:30:00 US/Eastern')
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.5

    def test_slotted_tick_and_bar_memory_footprint(self):
        """Ensure slotted tick/bar records stay compact (no __dict__)."""
        n = 2000
        tracemalloc.start()
        try:
            attribs = [TickAttribBidAsk(True, False) for _ in range(n)]
            cur_attribs, _ = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert len(attribs) == n
        # Unslotted dataclasses use ~220-260 B each; slotted use ~48-64 B.
        assert cur_attribs / n < 140

        tracemalloc.start()
        try:
            bars = [
                BarData(
                    dt.date(2023, 12, 15),
                    150.0, 151.0, 149.0, 150.5, 1000.0, 150.2, 10)
                for _ in range(n)]
            cur_bars, _ = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert len(bars) == n
        # Unslotted BarData uses ~320+ B each; slotted uses ~104-120 B.
        assert cur_bars / n < 200

    def test_streaming_tick_memory_remains_bounded(self):
        """Verify repeated tick packets do not accumulate retained memory."""
        ib, _, _ = _make_ready_ib()
        packet = _build_tick_burst(50)
        # Warmup caches and buffers.
        for _ in range(5):
            ib.client._onSocketHasData(packet)

        tracemalloc.start()
        try:
            for _ in range(100):
                ib.client._onSocketHasData(packet)
            current, _peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        # Only the latest packet's ~50 ticks should remain referenced.
        assert current < 32 * 1024
