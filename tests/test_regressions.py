"""Regression tests for correctness fixes across ib_insync."""

import asyncio
import datetime as dt
import json
import math
import struct
import sys
import types
from unittest.mock import AsyncMock, Mock, patch

import pytest
from eventkit import Event

from ib_insync import util
from ib_insync.client import Client
from ib_insync.connection import Connection
from ib_insync.contract import (
    Bag, ContFuture, Contract, ContractDetails, Forex, Future, Stock, Warrant)
from ib_insync.decoder import Decoder
from ib_insync.flexreport import FlexError, FlexReport
from ib_insync.ib import IB
from ib_insync.ibcontroller import IBC, Watchdog
from ib_insync.objects import (
    BarData, BarDataList, OptionChain, PnL, PnLSingle, Position,
    ScannerSubscription, TickAttrib, TickData, WshEventData)
from ib_insync.order import LimitOrder, Order, OrderStatus, Trade
from ib_insync.ticker import Bar, Midpoints, TickBars, Ticker, TimeBars, VolumeBars
from ib_insync.wrapper import RequestError, Wrapper


def frame_msg(*fields) -> bytes:
    payload = ('\0'.join(str(f) for f in fields) + '\0').encode()
    return struct.pack('>I', len(payload)) + payload


@pytest.fixture
def loop():
    ev_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(ev_loop)
    try:
        yield ev_loop
    finally:
        ev_loop.close()
        asyncio.set_event_loop(None)


class TestWrapperRegressions:
    """Regression tests for Wrapper bug fixes."""

    def test_rt_volume_does_not_freeze_after_equal_prices(self):
        ib = IB()
        ticker = ib.wrapper.startTicker(1, Stock('AAPL', 'SMART', 'USD'), 'mktData')

        ib.wrapper.tickString(1, 48, '100.0;10;1700000000000;100;100.0;true')
        ib.wrapper.tickString(1, 48, '100.0;10;1700000001000;110;100.0;true')
        ib.wrapper.tickString(1, 48, '105.0;25;1700000002000;135;101.0;true')

        assert ticker.prevLast == 100.0
        assert ticker.last == 105.0
        assert ticker.prevLastSize == 10.0
        assert ticker.lastSize == 25.0

    def test_tick_option_computation_maps_vega_and_theta_sentinel_to_none(self):
        ib = IB()
        ticker = ib.wrapper.startTicker(1, Contract(symbol='AAPL', secType='OPT'), 'mktData')

        ib.wrapper.tickOptionComputation(
            1, 13, 0, -1, -2, -1, -1, -2, -2, -2, -1
        )
        greeks = ticker.modelGreeks
        assert greeks is not None
        assert greeks.impliedVol is None
        assert greeks.delta is None
        assert greeks.optPrice is None
        assert greeks.pvDividend is None
        assert greeks.gamma is None
        assert greeks.vega is None
        assert greeks.theta is None
        assert greeks.undPrice is None

    def test_user_info_returns_white_branding_id(self, loop):
        ib = IB()
        fut = ib.wrapper.startReq(99)
        ib.wrapper.userInfo(99, 'ACME_BRAND')
        assert fut.result() == 'ACME_BRAND'

    def test_end_req_pops_results_when_explicit_result_given(self, loop):
        ib = IB()
        fut = ib.wrapper.startReq(100)
        ib.wrapper._endReq(100, 'explicit')
        assert fut.result() == 'explicit'
        assert 100 not in ib.wrapper._results

    def test_end_ticker_and_overwrite_clean_req_id_mapping(self):
        ib = IB()
        c = Stock('AAPL', 'SMART', 'USD')
        ticker = ib.wrapper.startTicker(1, c, 'mktData')
        # Overwriting same tickType cleans old reqId
        ib.wrapper.startTicker(2, c, 'mktData')
        assert 1 not in ib.wrapper.reqId2Ticker
        assert 2 in ib.wrapper.reqId2Ticker

        req_id = ib.wrapper.endTicker(ticker, 'mktData')
        assert req_id == 2
        assert 2 not in ib.wrapper.reqId2Ticker

    def test_historical_data_update_appends_to_empty_bar_list(self):
        ib = IB()
        events = []
        bars = BarDataList()
        bars.updateEvent += lambda b, is_new: events.append((len(b), is_new))
        ib.wrapper.startSubscription(10, bars)

        ib.wrapper.historicalDataUpdate(
            10, BarData(date='20231215', open=1.0, high=2.0, low=1.0, close=1.5)
        )
        assert len(bars) == 1
        assert events == [(1, True)]

    def test_order_status_emits_cancelled_event_for_api_cancelled(self):
        ib = IB()
        cancelled = []
        trade = Trade(
            contract=Stock('AAPL', 'SMART', 'USD'),
            order=Order(orderId=1, clientId=1, totalQuantity=10),
            orderStatus=OrderStatus(orderId=1, status=OrderStatus.PendingCancel),
        )
        trade.cancelledEvent += cancelled.append
        ib.wrapper.trades[(1, 1)] = trade

        ib.wrapper.orderStatus(
            1, OrderStatus.ApiCancelled, 0, 10, 0.0, 500, 0, 0.0, 1, '', 0.0
        )
        assert cancelled == [trade]

    def test_error_does_not_cancel_live_submitted_order(self):
        ib = IB()
        ib.wrapper.clientId = 1
        cancelled = []

        live_trade = Trade(
            contract=Stock('AAPL', 'SMART', 'USD'),
            order=Order(orderId=10, clientId=1, totalQuantity=100),
            orderStatus=OrderStatus(orderId=10, status=OrderStatus.Submitted),
        )
        live_trade.cancelledEvent += cancelled.append
        ib.wrapper.trades[(1, 10)] = live_trade

        ib.wrapper.error(10, 105, 'Order being modified does not match original order', '')
        assert live_trade.orderStatus.status == OrderStatus.Submitted
        assert cancelled == []
        assert live_trade.log[-1].errorCode == 105

        # New PendingSubmit order rejected IS cancelled locally
        pending_trade = Trade(
            contract=Stock('AAPL', 'SMART', 'USD'),
            order=Order(orderId=11, clientId=1, totalQuantity=100),
            orderStatus=OrderStatus(orderId=11, status=OrderStatus.PendingSubmit),
        )
        pending_cancelled = []
        pending_trade.cancelledEvent += pending_cancelled.append
        ib.wrapper.trades[(1, 11)] = pending_trade

        ib.wrapper.error(11, 201, 'Order rejected', '{"reason": "margin"}')
        assert pending_trade.orderStatus.status == OrderStatus.Cancelled
        assert pending_trade.advancedError == '{"reason": "margin"}'
        assert pending_cancelled == [pending_trade]

    def test_late_callbacks_do_not_raise_key_or_index_error(self):
        ib = IB()
        w = ib.wrapper
        cd = ContractDetails(contract=Stock('AAPL', 'SMART', 'USD'))

        # No startReq registered -> should safely ignore without KeyError
        w.completedOrder(Stock('AAPL'), Order(permId=1), util.dataclassUpdate(
            LimitOrder('BUY', 1, 100.0)
        ) if False else Mock(status='Filled'))
        w.contractDetails(999, cd)
        w.securityDefinitionOptionParameter(999, 'SMART', 1, 'AAPL', '100', [], [])
        w.historicalNews(999, '2023-12-15 10:00:00.0', 'BZ', 'A1', 'Headline')

        # Unknown reqId for DOM
        w.updateMktDepthL2(999, 0, 'ARCA', 1, 1, 100.0, 10)
        # Out-of-range update on known DOM
        ticker = w.startTicker(5, Stock('AAPL'), 'mktDepth')
        w.updateMktDepthL2(5, 0, 'ARCA', 1, 1, 100.0, 10)
        assert len(ticker.domBids) == 1

    def test_set_timeout_zero_preserves_last_time(self):
        ib = IB()
        assert ib.wrapper.lastTime == dt.datetime.min
        ib.wrapper.setTimeout(0)
        assert ib.wrapper.lastTime == dt.datetime.min


class TestDecoderRegressions:
    """Regression tests for Decoder bug fixes."""

    def test_parse_contract_details_fractional_coupon_and_ev_multiplier(self):
        ib = IB()
        decoder = Decoder(ib.wrapper, 178)
        cd = ContractDetails()
        cd.coupon = '4.25'  # type: ignore[assignment]
        cd.evMultiplier = '1.5'  # type: ignore[assignment]
        decoder.parse(cd)
        assert cd.coupon == 4.25
        assert cd.evMultiplier == 1.5

    def test_parse_preserves_numeric_zero_and_parses_infinite(self):
        ib = IB()
        decoder = Decoder(ib.wrapper, 178)
        o = Order()
        o.scalePriceIncrement = 0.0
        o.competeAgainstBestOffset = 'Infinite'  # type: ignore[assignment]
        decoder.parse(o)
        assert o.scalePriceIncrement == 0.0
        assert math.isinf(o.competeAgainstBestOffset)

    def test_contract_details_long_name_escapes_and_utf8(self, loop):
        ib = IB()
        decoder = Decoder(ib.wrapper, 178)

        def decode_with_long_name(name: str) -> str:
            fut = ib.wrapper.startReq(1)
            fields = [
                '10', '1', 'SYM', 'STK', '20250101', '0', '', 'SMART', 'USD',
                'SYM', 'MKT', 'SYM', '123', '0.01', '1', 'MKT', 'SMART', '1',
                '0', name, 'NASDAQ', '', '', '', '', 'US/Eastern', '', '',
                '', '0', '0', '0', '', '', '', '', '', '1', '1', '1',
            ]
            decoder.contractDetails(fields)
            ib.wrapper.contractDetailsEnd(1)
            return fut.result()[0].longName

        assert decode_with_long_name(r'SOCI\u00c9T\u00c9') == 'SOCIÉTÉ'
        assert decode_with_long_name('CAFÉ \\ TEST') == 'CAFÉ \\ TEST'

    def test_security_definition_option_parameter_int_con_id(self, loop):
        ib = IB()
        decoder = Decoder(ib.wrapper, 178)
        fut = ib.wrapper.startReq(7)
        decoder.securityDefinitionOptionParameter([
            '75', '7', 'SMART', '265598', 'AAPL', '100', '1', '20240119', '1', '150.0'
        ])
        ib.wrapper.securityDefinitionOptionParameterEnd(7)
        chain = fut.result()[0]
        assert isinstance(chain, OptionChain)
        assert chain.underlyingConId == 265598
        assert type(chain.underlyingConId) is int

    def test_tick_option_computation_empty_strings(self, loop):
        ib = IB()
        decoder = Decoder(ib.wrapper, 178)
        fut = ib.wrapper.startReq(3)
        decoder.tickOptionComputation(['21', '3', '53', '0', '', '', '', '', '', '', '', ''])
        comp = fut.result()
        assert comp.impliedVol is None
        assert comp.delta is None
        assert comp.vega is None
        assert comp.theta is None

    def test_price_size_tick_ewrapper_fallback(self):
        class StandardEWrapper:
            def __init__(self):
                self.prices = []
                self.sizes = []

            def tickPrice(self, reqId, tickType, price, attrib):
                self.prices.append((reqId, tickType, price, attrib))

            def tickSize(self, reqId, tickType, size):
                self.sizes.append((reqId, tickType, size))

        ewrapper = StandardEWrapper()
        decoder = Decoder(ewrapper, 178)  # type: ignore[arg-type]
        decoder.priceSizeTick(['1', '6', '1', '1', '150.5', '200', '5'])

        assert len(ewrapper.prices) == 1
        req_id, tick_type, price, attrib = ewrapper.prices[0]
        assert (req_id, tick_type, price) == (1, 1, 150.5)
        assert attrib == TickAttrib(canAutoExecute=True, pastLimit=False, preOpen=True)
        assert ewrapper.sizes == [(1, 0, 200.0)]


class TestContractRegressions:
    """Regression tests for Contract equality, hashing, and creation."""

    def test_future_and_contfuture_same_conid_not_equal(self):
        fut = Future(conId=12345, symbol='ES')
        cont = ContFuture(conId=12345, symbol='ES')
        assert fut != cont
        assert hash(fut) == 12345
        assert hash(cont) == -12345
        assert len({fut, cont}) == 2

    def test_distinct_bag_contracts_not_equal(self):
        b1 = Bag(conId=28812380, symbol='AAPL')
        b2 = Bag(conId=28812380, symbol='MSFT')
        b3 = Bag(conId=28812380, symbol='AAPL')
        assert b1 != b2
        assert b1 == b3

    def test_contract_create_preserves_iopt_sec_type(self):
        c = Contract.create(secType='IOPT', symbol='TEST')
        assert isinstance(c, Warrant)
        assert c.secType == 'IOPT'

    def test_forex_repr_handles_empty_sec_type(self):
        fx = Forex('EURUSD')
        fx.secType = ''
        assert 'EURUSD' in repr(fx)

    def test_contract_details_parse_empty_sessions(self):
        cd = ContractDetails()
        assert cd.tradingSessions() == []
        assert cd.liquidSessions() == []

    def test_contract_eq_returns_not_implemented_for_other_types(self):
        assert Contract().__eq__('not-a-contract') is NotImplemented


class TestClientAndConnectionRegressions:
    """Regression tests for Client and Connection fixes."""

    def test_throttle_schedules_single_timer_and_cancels_on_reset(self, loop):
        ib = IB()
        client = ib.client
        client.conn = Mock(spec=Connection)
        client.connState = Client.CONNECTED
        client.MaxRequests = 1
        client.RequestsInterval = 1.0

        client.sendMsg('m1\0')
        client.sendMsg('m2\0')
        first_handle = client._throttleHandle
        assert first_handle is not None

        client.sendMsg('m3\0')
        assert client._throttleHandle is first_handle

        client.reset()
        assert first_handle.cancelled()
        assert client._throttleHandle is None

    def test_zero_length_frame_does_not_crash(self, loop):
        ib = IB()
        client = ib.client
        client.conn = Mock(spec=Connection)
        client._serverVersion = 178
        client._apiReady = True
        client.wrapper.currentTime = Mock()

        empty_frame = struct.pack('>I', 0)
        valid_frame = frame_msg('49', '1', '1700000000')
        client._onSocketHasData(empty_frame + valid_frame)

        client.wrapper.currentTime.assert_called_once_with(1700000000)

    def test_old_server_version_disconnects_socket(self, loop):
        ib = IB()
        client = ib.client
        client.conn = Mock(spec=Connection)
        client._onSocketHasData(frame_msg('100', '20261119'))
        client.conn.disconnect.assert_called_once()

    def test_option_calc_accepts_none_options(self, loop):
        ib = IB()
        client = ib.client
        client.conn = Mock(spec=Connection)
        client.connState = Client.CONNECTED
        c = Stock('AAPL', 'SMART', 'USD')
        client.calculateImpliedVolatility(1, c, 5.0, 150.0, None)
        client.calculateOptionPrice(2, c, 0.25, 150.0, None)
        assert client.conn.sendMsg.call_count == 2

    def test_connection_disconnect_respects_can_write_eof(self):
        conn = Connection()
        transport = Mock()
        transport.can_write_eof.return_value = False
        transport.is_closing.return_value = False
        conn.transport = transport

        conn.disconnect()
        transport.write_eof.assert_not_called()
        transport.close.assert_called_once()


class TestIBRegressions:
    """Regression tests for IB class fixes."""

    @staticmethod
    def _mock_client(ib: IB) -> Mock:
        mock_client = Mock()
        mock_client.connState = Client.DISCONNECTED
        ib.client = mock_client
        return mock_client

    def test_disconnect_resets_wrapper_and_fails_futures(self, loop):
        ib = IB()
        conn = Mock(spec=Connection)
        conn.numBytesSent = 10
        conn.numMsgSent = 1
        ib.client.conn = conn
        ib.client.connState = Client.CONNECTED
        ib.client._apiReady = True
        ib.wrapper.positions['DU1'][1] = Position('DU1', Stock('AAPL'), 10, 100.0)
        fut = ib.wrapper.startReq(55)

        global_errors = []
        disconnected = []
        util.globalErrorEvent += global_errors.append
        ib.disconnectedEvent += lambda: disconnected.append(True)
        try:
            ib.disconnect()
            # Subsequent async connection_lost callback must not re-emit
            ib.client._onSocketDisconnected('')
        finally:
            util.globalErrorEvent -= global_errors.append

        assert ib.wrapper.positions == {}
        with pytest.raises(ConnectionError, match='Socket disconnect'):
            fut.result()
        assert disconnected == [True]
        assert global_errors == []

    def test_disconnect_before_api_ready_does_not_crash(self):
        ib = IB()
        ib.client.conn = Mock(spec=Connection)
        ib.client.connState = Client.CONNECTED
        ib.client._apiReady = False

        ib.disconnect()
        assert ib.client.connState == Client.DISCONNECTED
        ib.client.conn.disconnect.assert_called_once()

    def test_place_order_validates_done_state_before_sending(self):
        ib = IB()
        client = self._mock_client(ib)
        ib.wrapper.clientId = 1
        contract = Stock('AAPL', 'SMART', 'USD')
        order = LimitOrder('BUY', 10, 150.0, orderId=5, clientId=1)
        ib.wrapper.trades[(1, 5)] = Trade(
            contract=contract,
            order=order,
            orderStatus=OrderStatus(orderId=5, status=OrderStatus.Filled),
        )

        with pytest.raises(AssertionError):
            ib.placeOrder(contract, order)
        client.placeOrder.assert_not_called()

    @pytest.mark.asyncio
    async def test_req_scanner_data_async_cleans_subscription_and_realtime_bars_filters(self):
        ib = IB()
        client = self._mock_client(ib)
        client.getReqId.return_value = 10

        sub = ScannerSubscription(instrument='STK')
        task = asyncio.create_task(ib.reqScannerDataAsync(sub))
        await asyncio.sleep(0)

        # While scanner is active, realtimeBars() must NOT return ScanDataList
        assert ib.realtimeBars() == []
        assert 10 in ib.wrapper.reqId2Subscriber

        ib.wrapper.scannerDataEnd(10)
        await task

        assert 10 not in ib.wrapper.reqId2Subscriber
        client.cancelScannerSubscription.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_req_historical_data_timeout_cleans_subscription_and_future(self):
        ib = IB()
        client = self._mock_client(ib)
        client.getReqId.return_value = 20

        bars = await ib.reqHistoricalDataAsync(
            Stock('AAPL', 'SMART', 'USD'), '', '1 D', '1 min', 'TRADES',
            True, keepUpToDate=True, timeout=0.02
        )
        assert list(bars) == []
        assert 20 not in ib.wrapper._futures
        assert 20 not in ib.wrapper._results
        assert 20 not in ib.wrapper.reqId2Subscriber
        client.cancelHistoricalData.assert_called_once_with(20)

    @pytest.mark.asyncio
    async def test_req_tickers_and_wsh_event_cleanup_on_error(self):
        ib = IB()
        client = self._mock_client(ib)
        client.getReqId.side_effect = [1, 2]
        c = Stock('AAPL', 'SMART', 'USD')

        task = asyncio.create_task(ib.reqTickersAsync(c))
        await asyncio.sleep(0)
        ib.wrapper._endReq(1, RequestError(1, 200, 'Bad contract'), success=False)
        with pytest.raises(RequestError):
            await task
        assert ib.wrapper.ticker2ReqId['snapshot'] == {}

        task2 = asyncio.create_task(ib.getWshEventDataAsync(WshEventData(conId=1)))
        await asyncio.sleep(0)
        ib.wrapper._endReq(2, RequestError(2, 10276, 'No subscription'), success=False)
        with pytest.raises(RequestError):
            await task2
        assert ib.wrapper.wshEventReqId == 0

    def test_cancel_pnl_handles_req_id_zero(self):
        ib = IB()
        client = self._mock_client(ib)
        ib.wrapper.pnlKey2ReqId[('DU1', '')] = 0
        ib.wrapper.reqId2PnL[0] = PnL('DU1', '')

        ib.cancelPnL('DU1', '')
        client.cancelPnL.assert_called_once_with(0)
        assert 0 not in ib.wrapper.reqId2PnL

        ib.wrapper.pnlSingleKey2ReqId[('DU1', '', 123)] = 0
        ib.wrapper.reqId2PnlSingle[0] = PnLSingle('DU1', '', 123)
        ib.cancelPnLSingle('DU1', '', 123)
        client.cancelPnLSingle.assert_called_once_with(0)
        assert 0 not in ib.wrapper.reqId2PnlSingle

    def test_req_account_summary_cancels_previous_and_1102_guards_inactive(self, loop):
        ib = IB()
        client = self._mock_client(ib)
        client.getReqId.side_effect = [10, 11]
        client.isReady.return_value = True

        # 1102 before any accountSummary request must NOT start one
        ib._onError(-1, 1102, 'Connectivity restored', None)
        client.reqAccountSummary.assert_not_called()

        ib.reqAccountSummaryAsync()
        assert ib.wrapper.acctSummaryReqId == 10
        client.cancelAccountSummary.assert_not_called()

        # Subsequent call cancels old subscription before opening new one
        ib.reqAccountSummaryAsync()
        client.cancelAccountSummary.assert_called_once_with(10)
        assert ib.wrapper.acctSummaryReqId == 11

    @pytest.mark.asyncio
    async def test_connect_async_surfaces_non_timeout_sync_errors(self):
        ib = IB()
        client = self._mock_client(ib)
        client.connectAsync = AsyncMock()
        client.getAccounts.return_value = []
        client.serverVersion.return_value = 178
        client.isReady.return_value = True

        async def fail_positions():
            raise RequestError(1, 500, 'Positions error')

        ib.reqPositionsAsync = fail_positions  # type: ignore[method-assign]
        ib.reqExecutionsAsync = AsyncMock(return_value=[])  # type: ignore[method-assign]

        with pytest.raises(ConnectionError, match='positions request failed'):
            await ib.connectAsync(readonly=True, raiseSyncErrors=True)

    def test_loop_until_never_calls_wait_on_update_with_zero_on_timeout(self):
        ib = IB()
        waits = []
        ib.waitOnUpdate = lambda t=0: waits.append(t) or False  # type: ignore[method-assign]

        # Simulate clock jumping past endTime during yield
        times = iter([100.0, 100.5, 101.0])
        with patch('ib_insync.ib.time.monotonic', side_effect=lambda: next(times)):
            results = list(ib.loopUntil(lambda: False, timeout=1.0))

        assert results == [False, False]
        assert waits == []


class TestTickerRegressions:
    """Regression tests for Ticker bar aggregators and Midpoints."""

    def test_tick_bars_and_volume_bars_emit_bar_and_ignore_nan(self):
        tb_emitted = []
        tb = TickBars(2)
        tb += tb_emitted.append
        tb.on_source(None, float('nan'), 10)
        tb.on_source(None, 100.0, 5)
        tb.on_source(None, 102.0, 7)

        assert len(tb_emitted) == 1
        assert isinstance(tb_emitted[0], Bar)
        assert tb_emitted[0].open == 100.0
        assert tb_emitted[0].close == 102.0
        assert tb_emitted[0].volume == 12

        vb_emitted = []
        vb = VolumeBars(10)
        vb += vb_emitted.append
        vb.on_source(None, float('nan'), 20)
        vb.on_source(None, 50.0, 4)
        vb.on_source(None, 51.0, 6)

        assert len(vb_emitted) == 1
        assert isinstance(vb_emitted[0], Bar)
        assert vb_emitted[0].volume == 10

        with pytest.raises(ValueError):
            TickBars(0)
        with pytest.raises(ValueError):
            VolumeBars(0)

    def test_midpoints_skips_when_no_bid_ask_and_timebars_ignores_nan(self):
        ticker = Ticker(ticks=[TickData(dt.datetime.now(dt.timezone.utc), 4, 100.0, 1)])
        emitted = []
        mids = Midpoints(())
        mids += lambda *args: emitted.append(args)
        mids.on_source(ticker)
        assert emitted == []

        ticker.bid, ticker.bidSize = 99.5, 10
        ticker.ask, ticker.askSize = 100.5, 10
        mids.on_source(ticker)
        assert len(emitted) == 1
        assert emitted[0][1] == 100.0

        timer = Event('timer')
        tbars = TimeBars(timer)
        timer.emit(dt.datetime.now(dt.timezone.utc))
        tbars.on_source(None, float('nan'), 1)
        assert tbars.bars[-1].count == 0


class TestUtilRegressions:
    """Regression tests for util.py fixes."""

    def test_format_si_negative_ints_and_special_floats(self):
        assert util.formatSI(-5) == '-5 '
        assert util.formatSI(-999) == '-999 '
        assert util.formatSI(5) == '5 '
        assert util.formatSI(float('nan')) == 'nan '
        assert util.formatSI(float('inf')) == 'inf '
        assert 'e+' in util.formatSI(1e30)

    def test_parse_ib_datetime_preserves_timezone_variants(self):
        tz_ams = util.ZoneInfo('Europe/Amsterdam')
        tz_ny = util.ZoneInfo('US/Eastern')

        r1 = util.parseIBDatetime('20221125  10:00:00 Europe/Amsterdam')
        assert r1 == dt.datetime(2022, 11, 25, 10, 0, tzinfo=tz_ams)

        r2 = util.parseIBDatetime('20220101-10:30:00 US/Eastern')
        assert r2 == dt.datetime(2022, 1, 1, 10, 30, tzinfo=tz_ny)

    def test_tree_json_serializable_for_none_and_bytes(self):
        data = util.tree({'a': None, 'b': b'hello'})
        assert data == {'a': None, 'b': 'hello'}
        assert json.loads(json.dumps(data)) == {'a': None, 'b': 'hello'}

    def test_df_empty_with_labels_returns_none(self):
        fake_pd = types.ModuleType('pandas')
        with patch.dict(sys.modules, {'pandas': fake_pd}):
            assert util.df([], labels=['name']) is None

    def test_run_does_not_raise_stale_global_error_on_cancel(self, loop):
        util.globalErrorEvent.emit(RuntimeError('old unrelated error'))

        async def cancel_self():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            util.run(cancel_self())

    @pytest.mark.asyncio
    async def test_time_range_async_o1_catchup_from_far_past(self):
        now = dt.datetime.now(dt.timezone.utc)
        far_past = now - dt.timedelta(days=365)
        end = now - dt.timedelta(seconds=1)
        items = [t async for t in util.timeRangeAsync(far_past, end, step=0.001)]
        assert items == []


class TestControllerAndFlexRegressions:
    """Regression tests for Watchdog, IBC, and FlexReport fixes."""

    def test_watchdog_probe_contract_not_shared(self):
        w1 = Watchdog(IBC(), IB())
        w2 = Watchdog(IBC(), IB())
        assert w1.probeContract is not w2.probeContract

    @pytest.mark.asyncio
    async def test_watchdog_stop_cancels_startup_sleep_without_connecting(self):
        controller = IBC()
        controller.startAsync = AsyncMock()  # type: ignore[method-assign]
        controller.terminateAsync = AsyncMock()  # type: ignore[method-assign]
        ib = IB()
        ib.connectAsync = AsyncMock()  # type: ignore[method-assign]

        wd = Watchdog(controller, ib, appStartupTime=60.0)
        runner = wd.start()
        await asyncio.sleep(0.01)
        wd.stop()
        await asyncio.wait_for(runner, timeout=1.0)

        ib.connectAsync.assert_not_awaited()
        controller.terminateAsync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ibc_windows_does_not_pass_tws_settings_path_positionally(self):
        ibc = IBC(twsVersion=1012, gateway=True, twsSettingsPath='C:\\Jts')
        ibc._isWindows = True
        captured_cmd = []

        async def fake_exec(*cmd, **_kwargs):
            captured_cmd.extend(cmd)
            proc = Mock()
            proc.stdout = Mock()
            proc.stdout.readline = AsyncMock(return_value=b'')
            return proc

        with patch('asyncio.create_subprocess_exec', side_effect=fake_exec):
            await ibc.startAsync()
            if ibc._monitor:
                await ibc._monitor

        assert 'C:\\Jts' not in captured_cmd
        assert '1012' in captured_cmd

    def test_flex_report_defaults_and_retry_limit(self):
        report = FlexReport()
        assert report.topics() == set()
        assert report.extract('Trade') == []

        send_resp = (
            b'<FlexStatementResponse><Status>Success</Status>'
            b'<ReferenceCode>123</ReferenceCode>'
            b'<Url>https://example.com/get</Url></FlexStatementResponse>'
        )
        progress_resp = (
            b'<FlexStatementResponse>'
            b'<code>Statement generation in progress. Please try again shortly.</code>'
            b'</FlexStatementResponse>'
        )

        class _Resp:
            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        responses = iter([_Resp(send_resp), _Resp(progress_resp), _Resp(progress_resp)])
        with patch('ib_insync.flexreport.urlopen', side_effect=lambda *a, **kw: next(responses)), \
                patch('ib_insync.flexreport.time.sleep'):
            with pytest.raises(FlexError, match='Timed out'):
                report.download('tok', 'qid', maxRetries=2)
