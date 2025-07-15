"""Unit tests for Wrapper class."""

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock
from collections import defaultdict

from ib_insync.wrapper import Wrapper, RequestError
from ib_insync.contract import Stock, Contract
from ib_insync.order import Order, OrderState, Trade
from ib_insync.objects import (
    AccountValue, BarData, Execution, Fill, Position, PortfolioItem, 
    NewsTick, NewsBulletin
)
from ib_insync.ticker import Ticker as TickerClass


class TestRequestError:
    """Test RequestError exception class."""

    def test_request_error_creation(self):
        """Test RequestError creation."""
        error = RequestError(123, 404, "Not found")
        
        assert error.reqId == 123
        assert error.code == 404
        assert error.message == "Not found"
        assert str(error) == "API error: 404: Not found"

    def test_request_error_inheritance(self):
        """Test RequestError inheritance."""
        error = RequestError(123, 404, "Not found")
        
        assert isinstance(error, Exception)
        assert isinstance(error, RequestError)

    def test_request_error_attributes(self):
        """Test RequestError attributes accessibility."""
        error = RequestError(456, 500, "Internal server error")
        
        assert hasattr(error, 'reqId')
        assert hasattr(error, 'code')
        assert hasattr(error, 'message')
        
        assert error.reqId == 456
        assert error.code == 500
        assert error.message == "Internal server error"


class TestWrapperInitialization:
    """Test Wrapper initialization."""

    def test_wrapper_creation(self):
        """Test basic wrapper creation."""
        wrapper = Wrapper()
        
        # Test collections are initialized
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

    def test_wrapper_reset(self):
        """Test wrapper reset functionality."""
        wrapper = Wrapper()
        
        # Add some test data
        wrapper.accountValues[(1, 'test', 'USD', '')] = AccountValue()
        wrapper.trades[1] = Trade()
        wrapper.newsTicks.append(NewsTick())
        
        # Reset
        wrapper.reset()
        
        # Check everything is cleared
        assert len(wrapper.accountValues) == 0
        assert len(wrapper.trades) == 0
        assert len(wrapper.newsTicks) == 0
        assert len(wrapper.pendingTickers) == 0

    def test_wrapper_logger(self):
        """Test wrapper logger initialization."""
        wrapper = Wrapper()
        
        assert hasattr(wrapper, '_logger')
        assert wrapper._logger.name == 'ib_insync.wrapper'


class TestWrapperAccountHandling:
    """Test wrapper account-related methods."""

    def test_update_account_value(self):
        """Test updateAccountValue method."""
        wrapper = Wrapper()
        
        wrapper.updateAccountValue('NetLiquidation', '100000', 'USD', 'DU123456')
        
        key = ('DU123456', 'NetLiquidation', 'USD', '')
        assert key in wrapper.accountValues
        
        account_value = wrapper.accountValues[key]
        assert account_value.account == 'DU123456'
        assert account_value.tag == 'NetLiquidation'
        assert account_value.value == '100000'
        assert account_value.currency == 'USD'

    def test_update_account_value_with_model_code(self):
        """Test updateAccountValue with model code."""
        wrapper = Wrapper()
        
        wrapper.updateAccountValue('NetLiquidation', '100000', 'USD', 'DU123456', 'MODEL1')
        
        key = ('DU123456', 'NetLiquidation', 'USD', 'MODEL1')
        assert key in wrapper.accountValues
        
        account_value = wrapper.accountValues[key]
        assert account_value.modelCode == 'MODEL1'

    def test_update_portfolio(self):
        """Test updatePortfolio method."""
        wrapper = Wrapper()
        
        contract = Stock('AAPL', 'SMART', 'USD')
        contract.conId = 265598
        
        wrapper.updatePortfolio(
            contract, 100, 150.0, 15000.0, 14500.0, 500.0, 15500.0, 'DU123456'
        )
        
        assert 'DU123456' in wrapper.portfolio
        assert 265598 in wrapper.portfolio['DU123456']
        
        portfolio_item = wrapper.portfolio['DU123456'][265598]
        assert portfolio_item.contract == contract
        assert portfolio_item.position == 100
        assert portfolio_item.marketPrice == 150.0
        assert portfolio_item.marketValue == 15000.0
        assert portfolio_item.averageCost == 14500.0
        assert portfolio_item.unrealizedPNL == 500.0
        assert portfolio_item.realizedPNL == 15500.0
        assert portfolio_item.account == 'DU123456'

    def test_update_account_time(self):
        """Test updateAccountTime method."""
        wrapper = Wrapper()
        
        test_time = '20231215-10:30:00'
        wrapper.updateAccountTime(test_time)
        
        # Method should complete without error
        assert True

    def test_account_summary(self):
        """Test accountSummary method."""
        wrapper = Wrapper()
        
        wrapper.accountSummary(1, 'DU123456', 'NetLiquidation', '100000', 'USD')
        
        key = ('DU123456', 'NetLiquidation', 'USD')
        assert key in wrapper.acctSummary
        
        account_value = wrapper.acctSummary[key]
        assert account_value.account == 'DU123456'
        assert account_value.tag == 'NetLiquidation'
        assert account_value.value == '100000'
        assert account_value.currency == 'USD'

    def test_account_summary_end(self):
        """Test accountSummaryEnd method."""
        wrapper = Wrapper()
        
        wrapper.accountSummaryEnd(1)
        
        # Method should complete without error
        assert True


class TestWrapperPositionHandling:
    """Test wrapper position-related methods."""

    def test_position(self):
        """Test position method."""
        wrapper = Wrapper()
        
        contract = Stock('AAPL', 'SMART', 'USD')
        contract.conId = 265598
        
        wrapper.position('DU123456', contract, 100, 145.50)
        
        assert 'DU123456' in wrapper.positions
        assert 265598 in wrapper.positions['DU123456']
        
        position = wrapper.positions['DU123456'][265598]
        assert position.account == 'DU123456'
        assert position.contract == contract
        assert position.position == 100
        assert position.avgCost == 145.50

    def test_position_end(self):
        """Test positionEnd method."""
        wrapper = Wrapper()
        
        wrapper.positionEnd()
        
        # Method should complete without error
        assert True

    def test_position_update(self):
        """Test position update overwrites existing position."""
        wrapper = Wrapper()
        
        contract = Stock('AAPL', 'SMART', 'USD')
        contract.conId = 265598
        
        # Add initial position
        wrapper.position('DU123456', contract, 100, 145.50)
        
        # Update position
        wrapper.position('DU123456', contract, 200, 147.00)
        
        position = wrapper.positions['DU123456'][265598]
        assert position.position == 200
        assert position.avgCost == 147.00


class TestWrapperOrderHandling:
    """Test wrapper order-related methods."""

    def test_order_status(self):
        """Test orderStatus method."""
        wrapper = Wrapper()
        
        # Create a mock trade
        trade = Trade()
        trade.order = Order()
        trade.orderStatus = OrderState()
        wrapper.trades[123] = trade
        
        wrapper.orderStatus(123, 'Filled', 100, 50, 150.0, 1, 1, 150.0, 456, 'DU123456', 0.0)
        
        order_status = wrapper.trades[123].orderStatus
        assert order_status.orderId == 123
        assert order_status.status == 'Filled'
        assert order_status.filled == 100
        assert order_status.remaining == 50
        assert order_status.avgFillPrice == 150.0
        assert order_status.permId == 1
        assert order_status.parentId == 1
        assert order_status.lastFillPrice == 150.0
        assert order_status.clientId == 456
        assert order_status.whyHeld == 'DU123456'

    def test_open_order(self):
        """Test openOrder method."""
        wrapper = Wrapper()
        
        contract = Stock('AAPL', 'SMART', 'USD')
        order = Order()
        order.orderId = 123
        order.action = 'BUY'
        order.totalQuantity = 100
        
        order_state = OrderState()
        order_state.status = 'Submitted'
        
        wrapper.openOrder(123, contract, order, order_state)
        
        assert 123 in wrapper.trades
        trade = wrapper.trades[123]
        assert trade.contract == contract
        assert trade.order == order
        assert trade.orderStatus == order_state

    def test_open_order_end(self):
        """Test openOrderEnd method."""
        wrapper = Wrapper()
        
        wrapper.openOrderEnd()
        
        # Method should complete without error
        assert True

    def test_next_valid_id(self):
        """Test nextValidId method."""
        wrapper = Wrapper()
        
        wrapper.nextValidId(1001)
        
        # Method should complete without error
        assert True


class TestWrapperExecutionHandling:
    """Test wrapper execution-related methods."""

    def test_exec_details(self):
        """Test execDetails method."""
        wrapper = Wrapper()
        
        contract = Stock('AAPL', 'SMART', 'USD')
        execution = Execution()
        execution.execId = 'EXEC123'
        execution.orderId = 456
        execution.shares = 100
        execution.price = 150.0
        
        wrapper.execDetails(1, contract, execution)
        
        assert 'EXEC123' in wrapper.fills
        fill = wrapper.fills['EXEC123']
        assert fill.contract == contract
        assert fill.execution == execution

    def test_exec_details_end(self):
        """Test execDetailsEnd method."""
        wrapper = Wrapper()
        
        wrapper.execDetailsEnd(1)
        
        # Method should complete without error
        assert True

    def test_commission_report(self):
        """Test commissionReport method."""
        wrapper = Wrapper()
        
        # First create a fill
        execution = Execution()
        execution.execId = 'EXEC123'
        fill = Fill(execution=execution)
        wrapper.fills['EXEC123'] = fill
        
        # Now add commission report
        from ib_insync.objects import CommissionReport
        commission_report = CommissionReport()
        commission_report.execId = 'EXEC123'
        commission_report.commission = 1.0
        commission_report.currency = 'USD'
        
        wrapper.commissionReport(commission_report)
        
        assert wrapper.fills['EXEC123'].commissionReport == commission_report


class TestWrapperMarketDataHandling:
    """Test wrapper market data methods."""

    def test_tick_price(self):
        """Test tickPrice method."""
        wrapper = Wrapper()
        
        # Create a ticker
        ticker = TickerClass()
        wrapper.reqId2Ticker[1] = ticker
        
        wrapper.tickPrice(1, 1, 150.0, None)  # BID price
        
        assert ticker.bid == 150.0

    def test_tick_size(self):
        """Test tickSize method."""
        wrapper = Wrapper()
        
        # Create a ticker
        ticker = TickerClass()
        wrapper.reqId2Ticker[1] = ticker
        
        wrapper.tickSize(1, 0, 1000)  # BID size
        
        assert ticker.bidSize == 1000

    def test_tick_string(self):
        """Test tickString method."""
        wrapper = Wrapper()
        
        # Create a ticker
        ticker = TickerClass()
        wrapper.reqId2Ticker[1] = ticker
        
        wrapper.tickString(1, 32, '2023-12-15 10:30:00')  # RT_VOLUME
        
        # Method should complete without error
        assert True

    def test_tick_generic(self):
        """Test tickGeneric method."""
        wrapper = Wrapper()
        
        # Create a ticker
        ticker = TickerClass()
        wrapper.reqId2Ticker[1] = ticker
        
        wrapper.tickGeneric(1, 23, 0.5)  # OPTION_IMPLIED_VOL
        
        # Method should complete without error
        assert True

    def test_tick_snapshot_end(self):
        """Test tickSnapshotEnd method."""
        wrapper = Wrapper()
        
        wrapper.tickSnapshotEnd(1)
        
        # Method should complete without error
        assert True

    def test_market_data_type(self):
        """Test marketDataType method."""
        wrapper = Wrapper()
        
        wrapper.marketDataType(1, 2)
        
        # Method should complete without error
        assert True


class TestWrapperHistoricalDataHandling:
    """Test wrapper historical data methods."""

    def test_historical_data(self):
        """Test historicalData method."""
        wrapper = Wrapper()
        
        wrapper.historicalData(1, '20231215', 150.0, 151.0, 149.0, 150.5, 1000000, 1, 150.25, False)
        
        # Method should complete without error
        assert True

    def test_historical_data_end(self):
        """Test historicalDataEnd method."""
        wrapper = Wrapper()
        
        wrapper.historicalDataEnd(1, '20231215', '20231216')
        
        # Method should complete without error
        assert True

    def test_historical_data_update(self):
        """Test historicalDataUpdate method."""
        wrapper = Wrapper()
        
        wrapper.historicalDataUpdate(1, '20231215', 150.0, 151.0, 149.0, 150.5, 1000000, 1, 150.25)
        
        # Method should complete without error
        assert True


class TestWrapperNewsHandling:
    """Test wrapper news-related methods."""

    def test_tick_news(self):
        """Test tickNews method."""
        wrapper = Wrapper()
        
        wrapper.tickNews(1, 123456789, 'BRK', 'Test news article', 'http://example.com')
        
        assert len(wrapper.newsTicks) > 0
        news_tick = wrapper.newsTicks[-1]
        assert news_tick.timeStamp == 123456789
        assert news_tick.providerCode == 'BRK'
        assert news_tick.articleId == 'Test news article'
        assert news_tick.headline == 'http://example.com'

    def test_update_news_bulletin(self):
        """Test updateNewsBulletin method."""
        wrapper = Wrapper()
        
        wrapper.updateNewsBulletin(1, 1, 'Test bulletin', 'NYSE')
        
        assert 1 in wrapper.msgId2NewsBulletin
        bulletin = wrapper.msgId2NewsBulletin[1]
        assert bulletin.msgId == 1
        assert bulletin.newsType == 1
        assert bulletin.newsMessage == 'Test bulletin'
        assert bulletin.originatingExch == 'NYSE'


class TestWrapperContractHandling:
    """Test wrapper contract-related methods."""

    def test_contract_details(self):
        """Test contractDetails method."""
        wrapper = Wrapper()
        
        from ib_insync.contract import ContractDetails
        contract_details = ContractDetails()
        contract_details.contract = Stock('AAPL', 'SMART', 'USD')
        contract_details.minTick = 0.01
        
        wrapper.contractDetails(1, contract_details)
        
        # Method should complete without error
        assert True

    def test_contract_details_end(self):
        """Test contractDetailsEnd method."""
        wrapper = Wrapper()
        
        wrapper.contractDetailsEnd(1)
        
        # Method should complete without error
        assert True

    def test_bond_contract_details(self):
        """Test bondContractDetails method."""
        wrapper = Wrapper()
        
        from ib_insync.contract import ContractDetails
        contract_details = ContractDetails()
        contract_details.contract = Stock('AAPL', 'SMART', 'USD')  # Using stock as placeholder
        
        wrapper.bondContractDetails(1, contract_details)
        
        # Method should complete without error
        assert True


class TestWrapperErrorHandling:
    """Test wrapper error handling methods."""

    def test_error_msg(self):
        """Test error method."""
        wrapper = Wrapper()
        
        # Test with request ID
        wrapper.error(123, 404, 'Contract not found', '')
        
        # Test without request ID (general error)
        wrapper.error(-1, 502, 'Gateway error', '')
        
        # Method should complete without error
        assert True

    def test_connection_closed(self):
        """Test connectionClosed method."""
        wrapper = Wrapper()
        
        wrapper.connectionClosed()
        
        # Method should complete without error
        assert True

    def test_current_time(self):
        """Test currentTime method."""
        wrapper = Wrapper()
        
        wrapper.currentTime(1703509800)  # Unix timestamp
        
        # Method should complete without error
        assert True


class TestWrapperRealTimeData:
    """Test wrapper real-time data methods."""

    def test_realtime_bar(self):
        """Test realtimeBar method."""
        wrapper = Wrapper()
        
        wrapper.realtimeBar(1, 1703509800, 150.0, 151.0, 149.0, 150.5, 1000000, 150.25, 100)
        
        # Method should complete without error
        assert True

    def test_update_mkt_depth(self):
        """Test updateMktDepth method."""
        wrapper = Wrapper()
        
        wrapper.updateMktDepth(1, 0, 1, 1, 150.0, 1000)
        
        # Method should complete without error
        assert True

    def test_update_mkt_depth_l2(self):
        """Test updateMktDepthL2 method."""
        wrapper = Wrapper()
        
        wrapper.updateMktDepthL2(1, 0, 'ARCA', 1, 1, 150.0, 1000, True)
        
        # Method should complete without error
        assert True


class TestWrapperUtilityMethods:
    """Test wrapper utility methods."""

    def test_managed_accounts(self):
        """Test managedAccounts method."""
        wrapper = Wrapper()
        
        wrapper.managedAccounts('DU123456,DU789012')
        
        # Method should complete without error
        assert True

    def test_account_download_end(self):
        """Test accountDownloadEnd method."""
        wrapper = Wrapper()
        
        wrapper.accountDownloadEnd('DU123456')
        
        # Method should complete without error
        assert True

    def test_receive_fa(self):
        """Test receiveFA method."""
        wrapper = Wrapper()
        
        wrapper.receiveFA(1, 'FA XML data')
        
        # Method should complete without error
        assert True

    def test_scanner_parameters(self):
        """Test scannerParameters method."""
        wrapper = Wrapper()
        
        wrapper.scannerParameters('Scanner XML')
        
        # Method should complete without error
        assert True


class TestWrapperIntegration:
    """Test wrapper integration scenarios."""

    def test_full_order_lifecycle(self):
        """Test complete order lifecycle through wrapper."""
        wrapper = Wrapper()
        
        # Create order
        contract = Stock('AAPL', 'SMART', 'USD')
        order = Order()
        order.orderId = 123
        order.action = 'BUY'
        order.totalQuantity = 100
        
        order_state = OrderState()
        order_state.status = 'Submitted'
        
        # Open order
        wrapper.openOrder(123, contract, order, order_state)
        
        # Order status update
        wrapper.orderStatus(123, 'Filled', 100, 0, 150.0, 1, 1, 150.0, 456, '', 0.0)
        
        # Execution details
        execution = Execution()
        execution.execId = 'EXEC123'
        execution.orderId = 123
        execution.shares = 100
        execution.price = 150.0
        
        wrapper.execDetails(1, contract, execution)
        
        # Check final state
        assert 123 in wrapper.trades
        trade = wrapper.trades[123]
        assert trade.orderStatus.status == 'Filled'
        assert 'EXEC123' in wrapper.fills

    def test_account_data_flow(self):
        """Test account data flow through wrapper."""
        wrapper = Wrapper()
        
        # Account values
        wrapper.updateAccountValue('NetLiquidation', '100000', 'USD', 'DU123456')
        wrapper.updateAccountValue('BuyingPower', '50000', 'USD', 'DU123456')
        
        # Portfolio
        contract = Stock('AAPL', 'SMART', 'USD')
        contract.conId = 265598
        wrapper.updatePortfolio(contract, 100, 150.0, 15000.0, 14500.0, 500.0, 15500.0, 'DU123456')
        
        # Positions
        wrapper.position('DU123456', contract, 100, 145.50)
        
        # Verify data integrity
        assert len(wrapper.accountValues) == 2
        assert 'DU123456' in wrapper.portfolio
        assert 'DU123456' in wrapper.positions
        assert 265598 in wrapper.portfolio['DU123456']
        assert 265598 in wrapper.positions['DU123456']