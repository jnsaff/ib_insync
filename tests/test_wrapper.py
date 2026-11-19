"""Unit tests for Wrapper class."""

import asyncio
from datetime import date, datetime, timezone
from unittest.mock import Mock

import pytest
from eventkit import Event

from ib_insync.contract import (
    Contract, ContractDescription, ContractDetails, Stock)
from ib_insync.ib import IB
from ib_insync.objects import (
    AccountValue, BarData, BarDataList, CommissionReport, DOMLevel,
    Execution, Fill, HistogramData, HistoricalNews, HistoricalSchedule,
    HistoricalSession, HistoricalTick, NewsArticle, NewsBulletin, NewsProvider,
    NewsTick, OptionChain, PnL, PnLSingle, PortfolioItem, Position,
    PriceIncrement, RealTimeBar, RealTimeBarList, ScanDataList,
    TickAttribBidAsk, TickAttribLast)
from ib_insync.order import (
    LimitOrder, Order, OrderState, OrderStatus, Trade, TradeLogEntry)
from ib_insync.ticker import Ticker
from ib_insync.util import UNSET_DOUBLE, UNSET_INTEGER, isNan
from ib_insync.wrapper import RequestError, Wrapper


def make_mock_ib():
    """Create a mock IB object with real Event instances."""
    ib = Mock()
    ib.RaiseRequestErrors = False
    ib.TimezoneTWS = ''
    ib.client = Mock()
    for name in IB.events:
        setattr(ib, name, Event(name))
    return ib


def make_wrapper():
    """Create a Wrapper wired to a mock IB."""
    return Wrapper(make_mock_ib())


@pytest.fixture
def loop():
    """Provide an isolated event loop for Future-creating Wrapper methods."""
    ev_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(ev_loop)
    try:
        yield ev_loop
    finally:
        ev_loop.close()
        asyncio.set_event_loop(None)


class TestRequestError:
    """Test RequestError exception class."""

    def test_request_error_creation(self):
        error = RequestError(123, 404, 'Not found')
        assert error.reqId == 123
        assert error.code == 404
        assert error.message == 'Not found'
        assert str(error) == 'API error: 404: Not found'

    def test_request_error_inheritance(self):
        error = RequestError(123, 404, 'Not found')
        assert isinstance(error, Exception)
        assert isinstance(error, RequestError)


class TestWrapperInitialization:
    """Test Wrapper initialization, reset, and helper methods."""

    def test_wrapper_creation(self):
        wrapper = make_wrapper()

        assert isinstance(wrapper.accountValues, dict)
        assert isinstance(wrapper.acctSummary, dict)
        assert isinstance(wrapper.portfolio, dict)
        assert isinstance(wrapper.positions, dict)
        assert isinstance(wrapper.trades, dict)
        assert isinstance(wrapper.permId2Trade, dict)
        assert isinstance(wrapper.fills, dict)
        assert isinstance(wrapper.newsTicks, list)
        assert isinstance(wrapper.msgId2NewsBulletin, dict)
        assert isinstance(wrapper.tickers, dict)
        assert isinstance(wrapper.pendingTickers, set)
        assert isinstance(wrapper.reqId2Ticker, dict)
        assert isinstance(wrapper.ticker2ReqId, dict)
        assert isinstance(wrapper.reqId2Subscriber, dict)
        assert wrapper._logger.name == 'ib_insync.wrapper'

    def test_wrapper_reset(self):
        wrapper = make_wrapper()

        wrapper.accountValues[('DU1', 'Tag', 'USD', '')] = AccountValue(
            'DU1', 'Tag', '100', 'USD', ''
        )
        wrapper.trades[(1, 10)] = Trade()
        wrapper.newsTicks.append(NewsTick(123, 'BZ', 'A1', 'Headline', ''))
        wrapper.pendingTickers.add(Ticker())

        wrapper.reset()

        assert wrapper.accountValues == {}
        assert wrapper.trades == {}
        assert wrapper.newsTicks == []
        assert wrapper.pendingTickers == set()

    def test_order_key(self):
        wrapper = make_wrapper()
        assert wrapper.orderKey(clientId=1, orderId=10, permId=999) == (1, 10)
        assert wrapper.orderKey(clientId=0, orderId=0, permId=999) == 999
        assert wrapper.orderKey(clientId=0, orderId=-5, permId=888) == 888

    def test_start_and_end_req(self, loop):
        wrapper = make_wrapper()
        contract = Stock('AAPL', 'SMART', 'USD')
        fut = wrapper.startReq(10, contract=contract)

        assert wrapper._reqId2Contract[10] is contract
        wrapper._results[10].append('item')
        wrapper._endReq(10)

        assert fut.done()
        assert fut.result() == ['item']
        assert 10 not in wrapper._futures
        assert 10 not in wrapper._reqId2Contract

    def test_end_req_with_exception(self, loop):
        wrapper = make_wrapper()
        fut = wrapper.startReq(11)
        err = RequestError(11, 200, 'No security definition')
        wrapper._endReq(11, err, success=False)

        with pytest.raises(RequestError) as exc_info:
            fut.result()
        assert exc_info.value.code == 200

    def test_start_and_end_ticker(self):
        wrapper = make_wrapper()
        contract = Stock('AAPL', 'SMART', 'USD')
        ticker = wrapper.startTicker(1, contract, 'mktData')

        assert ticker.contract is contract
        assert wrapper.tickers[id(contract)] is ticker
        assert wrapper.reqId2Ticker[1] is ticker
        assert wrapper.ticker2ReqId['mktData'][ticker] == 1

        # Second subscription on same contract reuses Ticker
        ticker2 = wrapper.startTicker(2, contract, 'mktDepth')
        assert ticker2 is ticker

        req_id = wrapper.endTicker(ticker, 'mktData')
        assert req_id == 1
        assert 1 not in wrapper._reqId2Contract
        assert ticker not in wrapper.ticker2ReqId['mktData']


class TestWrapperAccountAndPositionHandling:
    """Test wrapper account, portfolio, position, and PnL callbacks."""

    def test_managed_accounts(self):
        wrapper = make_wrapper()
        wrapper.managedAccounts('DU123456,DU789012,')
        assert wrapper.accounts == ['DU123456', 'DU789012']

    def test_update_account_value_and_download_end(self, loop):
        wrapper = make_wrapper()
        emitted = []
        wrapper.ib.accountValueEvent += emitted.append
        fut = wrapper.startReq('accountValues')

        wrapper.updateAccountValue('NetLiquidation', '100000', 'USD', 'DU123456')

        key = ('DU123456', 'NetLiquidation', 'USD', '')
        assert key in wrapper.accountValues
        val = wrapper.accountValues[key]
        assert val == AccountValue('DU123456', 'NetLiquidation', '100000', 'USD', '')
        assert emitted == [val]

        wrapper.accountDownloadEnd('DU123456')
        assert fut.done()

    def test_account_update_multi(self, loop):
        wrapper = make_wrapper()
        fut = wrapper.startReq(5)
        wrapper.accountUpdateMulti(
            5, 'DU123456', 'MODEL1', 'NetLiquidation', '100000', 'USD'
        )
        key = ('DU123456', 'NetLiquidation', 'USD', 'MODEL1')
        assert wrapper.accountValues[key].modelCode == 'MODEL1'

        wrapper.accountUpdateMultiEnd(5)
        assert fut.done()

    def test_account_summary(self, loop):
        wrapper = make_wrapper()
        emitted = []
        wrapper.ib.accountSummaryEvent += emitted.append
        fut = wrapper.startReq(1)

        wrapper.accountSummary(1, 'DU123456', 'NetLiquidation', '100000', 'USD')
        key = ('DU123456', 'NetLiquidation', 'USD')
        assert wrapper.acctSummary[key].value == '100000'
        assert len(emitted) == 1

        wrapper.accountSummaryEnd(1)
        assert fut.done()

    def test_update_portfolio_add_update_and_remove(self):
        wrapper = make_wrapper()
        emitted = []
        wrapper.ib.updatePortfolioEvent += emitted.append

        contract = Stock('AAPL', 'SMART', 'USD', conId=265598)
        wrapper.updatePortfolio(
            contract, 100, 150.0, 15000.0, 145.0, 500.0, 0.0, 'DU123456'
        )

        item = wrapper.portfolio['DU123456'][265598]
        assert isinstance(item, PortfolioItem)
        assert isinstance(item.contract, Stock)
        assert item.position == 100
        assert item.marketPrice == 150.0
        assert item.marketValue == 15000.0
        assert item.averageCost == 145.0
        assert item.unrealizedPNL == 500.0
        assert item.account == 'DU123456'
        assert len(emitted) == 1

        # Zero position removes entry from portfolio dict
        wrapper.updatePortfolio(
            contract, 0, 150.0, 0.0, 0.0, 0.0, 500.0, 'DU123456'
        )
        assert 265598 not in wrapper.portfolio['DU123456']

    def test_position_add_update_remove_and_end(self, loop):
        wrapper = make_wrapper()
        emitted = []
        wrapper.ib.positionEvent += emitted.append
        fut = wrapper.startReq('positions')

        contract = Stock('AAPL', 'SMART', 'USD', conId=265598)
        wrapper.position('DU123456', contract, 100, 145.50)
        wrapper.position('DU123456', contract, 200, 147.00)

        pos = wrapper.positions['DU123456'][265598]
        assert isinstance(pos, Position)
        assert pos.position == 200
        assert pos.avgCost == 147.00
        assert len(emitted) == 2

        wrapper.positionEnd()
        assert len(fut.result()) == 2

        # Zero position removes entry
        wrapper.position('DU123456', contract, 0, 0.0)
        assert 265598 not in wrapper.positions['DU123456']

    def test_pnl_and_pnl_single(self):
        wrapper = make_wrapper()
        pnl_events = []
        single_events = []
        wrapper.ib.pnlEvent += pnl_events.append
        wrapper.ib.pnlSingleEvent += single_events.append

        pnl = PnL('DU1', '')
        wrapper.reqId2PnL[1] = pnl
        wrapper.pnl(1, 250.5, 100.0, 150.5)
        assert pnl.dailyPnL == 250.5
        assert pnl.unrealizedPnL == 100.0
        assert pnl.realizedPnL == 150.5
        assert pnl_events == [pnl]

        pnl_single = PnLSingle('DU1', '', 265598)
        wrapper.reqId2PnlSingle[2] = pnl_single
        wrapper.pnlSingle(2, 50, 12.5, 10.0, 2.5, 7500.0)
        assert pnl_single.position == 50
        assert pnl_single.dailyPnL == 12.5
        assert pnl_single.value == 7500.0
        assert single_events == [pnl_single]


class TestWrapperOrderAndExecutionHandling:
    """Test wrapper order, status, execution, and commission callbacks."""

    def test_open_order_new_and_update(self):
        wrapper = make_wrapper()
        events = []
        wrapper.ib.openOrderEvent += events.append

        contract = Stock('AAPL', 'SMART', 'USD')
        order = LimitOrder('BUY', 100, 150.0, orderId=123, clientId=1, permId=9001)
        order_state = OrderState(status='Submitted')

        wrapper.openOrder(123, contract, order, order_state)

        key = (1, 123)
        assert key in wrapper.trades
        trade = wrapper.trades[key]
        assert trade.contract == contract
        assert trade.order.totalQuantity == 100
        assert trade.orderStatus.status == 'Submitted'
        assert wrapper.permId2Trade[9001] is trade
        assert events == [trade]
        wrapper.ib.client.updateReqId.assert_called_with(124)

        # Updating existing trade modifies fields in place
        updated_order = LimitOrder(
            'BUY', 150, 151.0, orderId=123, clientId=1, permId=9001, orderRef='ref1'
        )
        wrapper.openOrder(123, contract, updated_order, order_state)
        assert trade.order.totalQuantity == 150
        assert trade.order.lmtPrice == 151.0
        assert trade.order.orderRef == 'ref1'

    def test_open_order_what_if(self, loop):
        wrapper = make_wrapper()
        fut = wrapper.startReq(50)
        order = LimitOrder('BUY', 10, 150.0, orderId=50, whatIf=True)
        state = OrderState(initMarginChange='1500.0', commission=1.0)

        wrapper.openOrder(50, Stock('AAPL', 'SMART', 'USD'), order, state)
        assert fut.done()
        assert fut.result() is state

    def test_completed_order(self, loop):
        wrapper = make_wrapper()
        fut = wrapper.startReq('completedOrders')
        contract = Stock('AAPL', 'SMART', 'USD')
        order = LimitOrder('BUY', 10, 150.0, orderId=0, permId=777)
        state = OrderState(status='Filled')

        wrapper.completedOrder(contract, order, state)
        wrapper.completedOrdersEnd()

        trades = fut.result()
        assert len(trades) == 1
        assert trades[0].orderStatus.status == 'Filled'
        assert wrapper.permId2Trade[777] is trades[0]

    def test_order_status_updates_and_events(self):
        wrapper = make_wrapper()
        status_events = []
        filled_events = []
        cancelled_events = []

        trade = Trade(
            contract=Stock('AAPL', 'SMART', 'USD'),
            order=Order(orderId=123, clientId=1, totalQuantity=100),
            orderStatus=OrderStatus(orderId=123, status=OrderStatus.PendingSubmit),
        )
        trade.statusEvent += status_events.append
        trade.filledEvent += filled_events.append
        trade.cancelledEvent += cancelled_events.append
        wrapper.trades[(1, 123)] = trade

        wrapper.orderStatus(
            123, OrderStatus.Submitted, 0, 100, 0.0, 9001, 0, 0.0, 1, '', 0.0
        )
        assert trade.orderStatus.status == OrderStatus.Submitted
        assert len(status_events) == 1
        assert filled_events == []

        # Order modification acknowledgement
        trade.log.append(
            TradeLogEntry(datetime.now(timezone.utc), OrderStatus.Submitted, 'Modify')
        )
        wrapper.orderStatus(
            123, OrderStatus.Submitted, 0, 100, 0.0, 9001, 0, 0.0, 1, '', 0.0
        )
        assert trade.log[-1].message == 'Modified'

        # Fill emits filledEvent
        wrapper.orderStatus(
            123, OrderStatus.Filled, 100, 0, 150.5, 9001, 0, 150.5, 1, '', 0.0
        )
        assert trade.orderStatus.filled == 100
        assert trade.orderStatus.avgFillPrice == 150.5
        assert filled_events == [trade]

    def test_exec_details_and_commission_report(self):
        wrapper = make_wrapper()
        fill_events = []
        comm_events = []
        wrapper.ib.execDetailsEvent += lambda t, f: fill_events.append((t, f))
        wrapper.ib.commissionReportEvent += lambda t, f, r: comm_events.append((t, f, r))

        contract = Stock('AAPL', 'SMART', 'USD', conId=265598)
        trade = Trade(
            contract=contract,
            order=Order(orderId=456, clientId=1, permId=999, totalQuantity=100),
            orderStatus=OrderStatus(orderId=456, status=OrderStatus.Submitted),
        )
        wrapper.trades[(1, 456)] = trade
        wrapper.permId2Trade[999] = trade

        execution = Execution(
            execId='EXEC123', orderId=456, clientId=1, permId=999,
            shares=100, price=150.0,
        )
        wrapper.execDetails(-1, contract, execution)

        assert 'EXEC123' in wrapper.fills
        fill = wrapper.fills['EXEC123']
        assert isinstance(fill, Fill)
        assert fill.contract is contract
        assert fill.execution is execution
        assert trade.fills == [fill]
        assert len(fill_events) == 1

        report = CommissionReport(
            execId='EXEC123', commission=1.05, currency='USD',
            realizedPNL=UNSET_DOUBLE, yield_=UNSET_DOUBLE,
        )
        wrapper.commissionReport(report)
        assert fill.commissionReport.commission == 1.05
        assert fill.commissionReport.realizedPNL == 0.0
        assert fill.commissionReport.yield_ == 0.0
        assert len(comm_events) == 1

    def test_exec_details_for_req_executions(self, loop):
        wrapper = make_wrapper()
        fut = wrapper.startReq(77)
        contract = Stock('AAPL', 'SMART', 'USD')
        execution = Execution(execId='HIST1', orderId=UNSET_INTEGER, shares=10, price=100.0)

        wrapper.execDetails(77, contract, execution)
        wrapper.execDetailsEnd(77)

        assert execution.orderId == 0
        fills = fut.result()
        assert len(fills) == 1
        assert fills[0].execution.execId == 'HIST1'


class TestWrapperMarketDataHandling:
    """Test wrapper tick, option computation, and DOM callbacks."""

    def test_price_size_tick(self):
        wrapper = make_wrapper()
        ticker = wrapper.startTicker(1, Stock('AAPL', 'SMART', 'USD'), 'mktData')

        wrapper.priceSizeTick(1, 1, 150.0, 200)  # bid
        wrapper.priceSizeTick(1, 2, 150.25, 300)  # ask
        wrapper.priceSizeTick(1, 4, 150.10, 50)  # last
        wrapper.priceSizeTick(1, 6, 152.0, 0)  # high
        wrapper.priceSizeTick(1, 7, 148.0, 0)  # low
        wrapper.priceSizeTick(1, 9, 149.5, 0)  # close
        wrapper.priceSizeTick(1, 14, 149.8, 0)  # open

        assert ticker.bid == 150.0
        assert ticker.bidSize == 200
        assert ticker.ask == 150.25
        assert ticker.askSize == 300
        assert ticker.last == 150.10
        assert ticker.lastSize == 50
        assert ticker.high == 152.0
        assert ticker.low == 148.0
        assert ticker.close == 149.5
        assert ticker.open == 149.8
        assert ticker in wrapper.pendingTickers

        # Duplicate bid tick is ignored
        count_before = len(ticker.ticks)
        wrapper.priceSizeTick(1, 1, 150.0, 200)
        assert len(ticker.ticks) == count_before

    def test_tick_size(self):
        wrapper = make_wrapper()
        ticker = wrapper.startTicker(1, Stock('AAPL', 'SMART', 'USD'), 'mktData')
        ticker.last = 150.0

        wrapper.tickSize(1, 0, 500)  # bidSize
        wrapper.tickSize(1, 3, 600)  # askSize
        wrapper.tickSize(1, 5, 25)  # lastSize
        wrapper.tickSize(1, 8, 100000)  # volume

        assert ticker.bidSize == 500
        assert ticker.askSize == 600
        assert ticker.lastSize == 25
        assert ticker.volume == 100000

    def test_tick_string_exchanges_dividends_and_ratios(self):
        wrapper = make_wrapper()
        ticker = wrapper.startTicker(1, Stock('AAPL', 'SMART', 'USD'), 'mktData')

        wrapper.tickString(1, 32, 'Q')
        wrapper.tickString(1, 33, 'N')
        wrapper.tickString(1, 84, 'P')
        assert ticker.bidExchange == 'Q'
        assert ticker.askExchange == 'N'
        assert ticker.lastExchange == 'P'

        wrapper.tickString(1, 47, 'PE=25.5;SHARES=1000;BAD=-99999.99;')
        assert ticker.fundamentalRatios is not None
        assert ticker.fundamentalRatios.PE == 25.5
        assert ticker.fundamentalRatios.SHARES == 1000
        assert isNan(ticker.fundamentalRatios.BAD)

        wrapper.tickString(1, 59, '0.83,0.92,20230219,0.23')
        assert ticker.dividends is not None
        assert ticker.dividends.past12Months == 0.83
        assert ticker.dividends.next12Months == 0.92
        assert ticker.dividends.nextDate == date(2023, 2, 19)
        assert ticker.dividends.nextAmount == 0.23

        # Malformed tickString is logged without raising
        wrapper.tickString(1, 59, 'malformed')

    def test_tick_generic_and_req_params_and_market_data_type(self):
        wrapper = make_wrapper()
        ticker = wrapper.startTicker(1, Stock('AAPL', 'SMART', 'USD'), 'mktData')

        wrapper.tickGeneric(1, 23, 0.25)
        wrapper.tickGeneric(1, 24, 0.30)
        wrapper.tickGeneric(1, 49, 0.0)
        wrapper.tickReqParams(1, 0.01, '9c', 3)
        wrapper.marketDataType(1, 3)

        assert ticker.histVolatility == 0.25
        assert ticker.impliedVolatility == 0.30
        assert ticker.halted == 0.0
        assert ticker.minTick == 0.01
        assert ticker.bboExchange == '9c'
        assert ticker.snapshotPermissions == 3
        assert ticker.marketDataType == 3

    def test_tick_by_tick_callbacks(self):
        wrapper = make_wrapper()
        ticker = wrapper.startTicker(1, Stock('AAPL', 'SMART', 'USD'), 'AllLast')

        wrapper.tickByTickAllLast(
            1, 1, 1700000000, 150.5, 100.0, TickAttribLast(), 'ISLAND', ''
        )
        wrapper.tickByTickBidAsk(
            1, 1700000001, 150.4, 150.6, 200.0, 300.0, TickAttribBidAsk()
        )
        wrapper.tickByTickMidPoint(1, 1700000002, 150.5)

        assert ticker.last == 150.5
        assert ticker.bid == 150.4
        assert ticker.ask == 150.6
        assert len(ticker.tickByTicks) == 3

    def test_tick_option_computation_for_ticker_and_request(self, loop):
        wrapper = make_wrapper()
        ticker = wrapper.startTicker(1, Contract(symbol='AAPL', secType='OPT'), 'mktData')

        wrapper.tickOptionComputation(
            1, 13, 0, 0.25, 0.55, 5.10, 0.0, 0.04, 0.12, -0.03, 150.0
        )
        assert ticker.modelGreeks is not None
        assert ticker.modelGreeks.impliedVol == 0.25
        assert ticker.modelGreeks.delta == 0.55
        assert ticker.modelGreeks.optPrice == 5.10

        # Request-mode reply (calculateImpliedVolatility / calculateOptionPrice)
        fut = wrapper.startReq(2)
        wrapper.tickOptionComputation(
            2, 53, 0, 0.30, -2, 6.0, -1, -2, 0.10, -0.05, 150.0
        )
        comp = fut.result()
        assert comp.impliedVol == 0.30
        assert comp.delta is None
        assert comp.pvDividend is None
        assert comp.gamma is None

    def test_update_mkt_depth_insert_update_delete(self):
        wrapper = make_wrapper()
        ticker = wrapper.startTicker(1, Stock('AAPL', 'SMART', 'USD'), 'mktDepth')

        # Insert bid level 0 (side=1 -> bid, operation=0 -> insert)
        wrapper.updateMktDepthL2(1, 0, 'ARCA', 0, 1, 150.0, 1000, True)
        assert ticker.domBids == [DOMLevel(150.0, 1000, 'ARCA')]

        # Update bid level 0 (operation=1)
        wrapper.updateMktDepth(1, 0, 1, 1, 150.1, 1200)
        assert ticker.domBids == [DOMLevel(150.1, 1200, '')]

        # Delete bid level 0 (operation=2)
        wrapper.updateMktDepth(1, 0, 2, 1, 0.0, 0.0)
        assert ticker.domBids == []
        assert len(ticker.domTicks) == 3

    def test_tick_snapshot_end(self, loop):
        wrapper = make_wrapper()
        fut = wrapper.startReq(1)
        wrapper.tickSnapshotEnd(1)
        assert fut.done()


class TestWrapperHistoricalAndScannerHandling:
    """Test historical data, realtime bars, and scanner callbacks."""

    def test_historical_data_and_end(self, loop):
        wrapper = make_wrapper()
        bars = BarDataList()
        fut = wrapper.startReq(1, container=bars)

        bar = BarData(date='20231215', open=150.0, high=151.0, low=149.0, close=150.5, volume=1000)
        wrapper.historicalData(1, bar)
        wrapper.historicalDataEnd(1, '20231215', '20231216')

        result = fut.result()
        assert len(result) == 1
        assert result[0].date == date(2023, 12, 15)
        assert result[0].close == 150.5

    def test_historical_data_update(self):
        wrapper = make_wrapper()
        updates = []
        wrapper.ib.barUpdateEvent += lambda b, is_new: updates.append((len(b), is_new))

        bars = BarDataList([
            BarData(date=date(2023, 12, 15), open=150.0, high=151.0, low=149.0, close=150.5)
        ])
        wrapper.startSubscription(1, bars)

        # Update current bar
        wrapper.historicalDataUpdate(
            1, BarData(date='20231215', open=150.0, high=152.0, low=149.0, close=151.5)
        )
        assert len(bars) == 1
        assert bars[0].high == 152.0
        assert updates[-1] == (1, False)

        # Append newer bar
        wrapper.historicalDataUpdate(
            1, BarData(date='20231216', open=151.5, high=153.0, low=151.0, close=152.5)
        )
        assert len(bars) == 2
        assert updates[-1] == (2, True)

        # Older bar is ignored
        wrapper.historicalDataUpdate(
            1, BarData(date='20231214', open=140.0, high=141.0, low=139.0, close=140.5)
        )
        assert len(bars) == 2

    def test_realtime_bar(self):
        wrapper = make_wrapper()
        bars = RealTimeBarList()
        wrapper.startSubscription(1, bars)

        wrapper.realtimeBar(1, 1703509800, 150.0, 151.0, 149.0, 150.5, 1000.0, 150.25, 100)
        assert len(bars) == 1
        assert isinstance(bars[0], RealTimeBar)
        assert bars[0].time == datetime.fromtimestamp(1703509800, timezone.utc)
        assert bars[0].close == 150.5

    def test_head_timestamp_and_historical_ticks(self, loop):
        wrapper = make_wrapper()
        fut = wrapper.startReq(1)
        wrapper.headTimestamp(1, '1700000000')
        assert fut.result() == datetime.fromtimestamp(1700000000, timezone.utc)

        fut_bad = wrapper.startReq(2)
        wrapper.headTimestamp(2, 'not-a-date')
        with pytest.raises(ValueError):
            fut_bad.result()

        fut_ticks = wrapper.startReq(3)
        tick = HistoricalTick(datetime.now(timezone.utc), 150.0, 10.0)
        wrapper.historicalTicks(3, [tick], done=True)
        assert fut_ticks.result() == [tick]

    def test_scanner_data_and_parameters(self, loop):
        wrapper = make_wrapper()
        scan_list = ScanDataList()
        fut = wrapper.startReq(1, container=scan_list)
        cd = ContractDetails(contract=Stock('AAPL', 'SMART', 'USD'))

        wrapper.scannerData(1, 0, cd, '1.0', 'SPY', 'UP', '')
        wrapper.scannerDataEnd(1)
        assert len(fut.result()) == 1
        assert fut.result()[0].rank == 0

        fut_params = wrapper.startReq('scannerParams')
        wrapper.scannerParameters('<ScanParameterResponse/>')
        assert fut_params.result() == '<ScanParameterResponse/>'

    def test_histogram_schedule_and_option_chain(self, loop):
        wrapper = make_wrapper()

        fut_hist = wrapper.startReq(1)
        wrapper.histogramData(1, [HistogramData(150.0, 42)])
        assert fut_hist.result() == [HistogramData(150.0, 42)]

        fut_sched = wrapper.startReq(2)
        sess = [HistoricalSession('20231215-09:30:00', '20231215-16:00:00', '20231215')]
        wrapper.historicalSchedule(2, '20231215', '20231216', 'US/Eastern', sess)
        assert isinstance(fut_sched.result(), HistoricalSchedule)

        fut_opt = wrapper.startReq(3)
        wrapper.securityDefinitionOptionParameter(
            3, 'SMART', 265598, 'AAPL', '100', ['20240119'], [150.0, 155.0]
        )
        wrapper.securityDefinitionOptionParameterEnd(3)
        assert fut_opt.result() == [
            OptionChain('SMART', 265598, 'AAPL', '100', ['20240119'], [150.0, 155.0])
        ]


class TestWrapperNewsContractsAndMisc:
    """Test news, contract details, FA, WSH, and time callbacks."""

    def test_news_callbacks(self, loop):
        wrapper = make_wrapper()

        fut_prov = wrapper.startReq('newsProviders')
        wrapper.newsProviders([NewsProvider('BZ', 'Benzinga')])
        assert fut_prov.result() == [NewsProvider('BZ', 'Benzinga')]

        wrapper.tickNews(1, 123456789, 'BZ', 'ART1', 'Headline', 'extra')
        assert wrapper.newsTicks[-1] == NewsTick(
            123456789, 'BZ', 'ART1', 'Headline', 'extra'
        )

        fut_art = wrapper.startReq(2)
        wrapper.newsArticle(2, 0, 'Body text')
        assert fut_art.result() == NewsArticle(0, 'Body text')

        fut_hist = wrapper.startReq(3)
        wrapper.historicalNews(3, '2023-12-15 10:30:00.0', 'BZ', 'ART1', 'Headline')
        wrapper.historicalNewsEnd(3, False)
        assert len(fut_hist.result()) == 1
        assert isinstance(fut_hist.result()[0], HistoricalNews)

        wrapper.updateNewsBulletin(1, 1, 'Test bulletin', 'NYSE')
        bulletin = wrapper.msgId2NewsBulletin[1]
        assert bulletin == NewsBulletin(1, 1, 'Test bulletin', 'NYSE')

    def test_contract_details_symbol_samples_and_market_rule(self, loop):
        wrapper = make_wrapper()

        fut_cd = wrapper.startReq(1)
        cd = ContractDetails(contract=Stock('AAPL', 'SMART', 'USD'), minTick=0.01)
        wrapper.contractDetails(1, cd)
        wrapper.contractDetailsEnd(1)
        assert fut_cd.result() == [cd]

        fut_bond = wrapper.startReq(2)
        wrapper.bondContractDetails(2, cd)
        wrapper.contractDetailsEnd(2)
        assert fut_bond.result() == [cd]

        fut_sym = wrapper.startReq(3)
        desc = ContractDescription(contract=Stock('AAPL', 'SMART', 'USD'))
        wrapper.symbolSamples(3, [desc])
        assert fut_sym.result() == [desc]

        fut_rule = wrapper.startReq('marketRule-26')
        inc = [PriceIncrement(0.0, 0.01)]
        wrapper.marketRule(26, inc)
        assert fut_rule.result() == inc

    def test_fa_wsh_and_current_time(self, loop):
        wrapper = make_wrapper()

        fut_fa = wrapper.startReq('requestFA')
        wrapper.receiveFA(1, '<Groups/>')
        assert fut_fa.result() == '<Groups/>'

        fut_time = wrapper.startReq('currentTime')
        wrapper.currentTime(1703509800)
        assert fut_time.result() == datetime.fromtimestamp(1703509800, timezone.utc)

        fut_meta = wrapper.startReq(10)
        wrapper.wshMetaData(10, '{"meta": true}')
        assert fut_meta.result() == '{"meta": true}'

        fut_ev = wrapper.startReq(11)
        wrapper.wshEventData(11, '{"events": []}')
        assert fut_ev.result() == '{"events": []}'


class TestWrapperErrorAndLifecycleHandling:
    """Test error handling, disconnect cleanup, and TCP packet batching."""

    def test_error_on_request_silent_vs_raising(self, loop):
        wrapper = make_wrapper()
        emitted = []
        wrapper.ib.errorEvent += lambda *args: emitted.append(args)

        # Silent mode (RaiseRequestErrors=False) resolves with empty list
        fut1 = wrapper.startReq(100)
        wrapper.error(100, 200, 'No security definition', '')
        assert fut1.result() == []
        assert len(emitted) == 1

        # Raising mode (RaiseRequestErrors=True) fails future with RequestError
        wrapper.ib.RaiseRequestErrors = True
        fut2 = wrapper.startReq(101)
        wrapper.error(101, 200, 'No security definition', '')
        with pytest.raises(RequestError) as exc_info:
            fut2.result()
        assert exc_info.value.code == 200

    def test_error_warning_code_does_not_fail_request(self, loop):
        wrapper = make_wrapper()
        fut = wrapper.startReq(102)
        wrapper.error(102, 2104, 'Market data farm connection is OK', '')
        assert not fut.done()

    def test_error_317_resets_dom_and_165_clears_scanner(self):
        wrapper = make_wrapper()

        ticker = wrapper.startTicker(1, Stock('AAPL', 'SMART', 'USD'), 'mktDepth')
        ticker.domBids.append(DOMLevel(150.0, 100, 'ARCA'))
        ticker.domAsks.append(DOMLevel(150.1, 200, 'ARCA'))
        wrapper.error(1, 317, 'Market depth data has been RESET', '')
        assert ticker.domBids == []
        assert ticker.domAsks == []
        assert len(ticker.domTicks) == 2

        scan = ScanDataList(['item'])
        wrapper.startSubscription(2, scan)
        wrapper.error(2, 165, 'No scanner results', '')
        assert list(scan) == []

    def test_connection_closed_fails_pending_futures_and_resets(self, loop):
        wrapper = make_wrapper()
        fut = wrapper.startReq(1)
        wrapper.positions['DU1'][1] = Position('DU1', Stock('AAPL'), 10, 100.0)

        wrapper.connectionClosed()

        with pytest.raises(ConnectionError, match='Socket disconnect'):
            fut.result()
        assert wrapper.positions == {}

    def test_tcp_data_arrived_and_processed(self):
        wrapper = make_wrapper()
        updated = []
        pending_batches = []
        wrapper.ib.updateEvent += lambda: updated.append(True)
        wrapper.ib.pendingTickersEvent += lambda s: pending_batches.append(set(s))

        ticker = wrapper.startTicker(1, Stock('AAPL', 'SMART', 'USD'), 'mktData')
        wrapper.tcpDataArrived()
        wrapper.priceSizeTick(1, 1, 150.0, 100)
        wrapper.tcpDataProcessed()

        assert updated == [True]
        assert pending_batches == [{ticker}]
        assert ticker.time == wrapper.lastTime

        # Next packet clears previous tick list
        wrapper.tcpDataArrived()
        assert ticker.ticks == []
        assert wrapper.pendingTickers == set()


class TestWrapperIntegration:
    """Test end-to-end flows through Wrapper."""

    def test_full_order_lifecycle(self):
        wrapper = make_wrapper()
        contract = Stock('AAPL', 'SMART', 'USD', conId=265598)
        order = LimitOrder('BUY', 100, 150.0, orderId=123, clientId=1, permId=5555)

        wrapper.openOrder(123, contract, order, OrderState(status='Submitted'))
        wrapper.orderStatus(
            123, 'Filled', 100, 0, 150.0, 5555, 0, 150.0, 1, '', 0.0
        )
        execution = Execution(
            execId='EXEC123', orderId=123, clientId=1, permId=5555,
            shares=100, price=150.0,
        )
        wrapper.execDetails(-1, contract, execution)

        trade = wrapper.trades[(1, 123)]
        assert trade.orderStatus.status == 'Filled'
        assert trade.isDone() is True
        assert trade.filled() == 100
        assert 'EXEC123' in wrapper.fills

    def test_account_data_flow(self):
        wrapper = make_wrapper()
        wrapper.updateAccountValue('NetLiquidation', '100000', 'USD', 'DU123456')
        wrapper.updateAccountValue('BuyingPower', '50000', 'USD', 'DU123456')

        contract = Stock('AAPL', 'SMART', 'USD', conId=265598)
        wrapper.updatePortfolio(
            contract, 100, 150.0, 15000.0, 145.0, 500.0, 0.0, 'DU123456'
        )
        wrapper.position('DU123456', contract, 100, 145.50)

        assert len(wrapper.accountValues) == 2
        assert 265598 in wrapper.portfolio['DU123456']
        assert 265598 in wrapper.positions['DU123456']