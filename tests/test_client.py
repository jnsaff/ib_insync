"""Unit tests for Client class."""

import asyncio
import logging
import struct
import time
from unittest.mock import AsyncMock, MagicMock, Mock, patch, call
from collections import deque

import pytest

from ib_insync.client import Client
from ib_insync.connection import Connection
from ib_insync.wrapper import Wrapper
from ib_insync.objects import ConnectionStats
from ib_insync.contract import Stock


class MockWrapper(Wrapper):
    """Mock wrapper for testing."""
    
    def __init__(self):
        # Mock the ib parameter
        mock_ib = Mock()
        super().__init__(mock_ib)
        self.method_calls = []
        
    def __getattr__(self, name):
        """Capture method calls."""
        def method(*args, **kwargs):
            self.method_calls.append((name, args, kwargs))
        return method


class TestClientCreation:
    """Test Client creation and initialization."""

    def test_client_creation(self):
        """Test basic client creation."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        assert client.wrapper is wrapper
        assert client.decoder is not None
        assert client.MinClientVersion == 157
        assert client.MaxClientVersion == 178
        assert client.MaxRequests == 45
        assert client.RequestsInterval == 1
        assert client.connState == Client.DISCONNECTED
        assert client.serverVersion == 0
        assert client.reqIdSeq == 0
        assert client.accounts == []
        assert client.nextValidId == 0
        assert isinstance(client.msgQ, deque)
        assert len(client.msgQ) == 0
        assert client.lastTime == 0
        assert client.reqCount == 0
        assert client.throttleTimer is None

    def test_client_events(self):
        """Test client event initialization."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        # Check all events are initialized
        assert hasattr(client, 'apiStart')
        assert hasattr(client, 'apiEnd')
        assert hasattr(client, 'apiError')
        assert hasattr(client, 'throttleStart')
        assert hasattr(client, 'throttleEnd')
        
        # Check events are callable
        assert callable(client.apiStart.emit)
        assert callable(client.apiEnd.emit)
        assert callable(client.apiError.emit)
        assert callable(client.throttleStart.emit)
        assert callable(client.throttleEnd.emit)

    def test_client_logger(self):
        """Test client logger initialization."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        assert isinstance(client._logger, logging.Logger)
        assert client._logger.name == 'ib_insync.client'


class TestClientConnectionMethods:
    """Test client connection methods."""

    def test_is_connected_false(self):
        """Test isConnected when not connected."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        assert not client.isConnected()

    def test_is_connected_true(self):
        """Test isConnected when connected."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.connState = Client.CONNECTED
        
        assert client.isConnected()

    def test_connection_stats(self):
        """Test connectionStats method."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        # Mock connection
        mock_conn = Mock()
        mock_conn.numBytesSent = 1000
        mock_conn.numMsgSent = 50
        client.conn = mock_conn
        
        stats = client.connectionStats()
        
        assert isinstance(stats, ConnectionStats)
        assert stats.numBytesSent == 1000
        assert stats.numMsgSent == 50

    def test_connection_stats_no_connection(self):
        """Test connectionStats when not connected."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        stats = client.connectionStats()
        
        assert isinstance(stats, ConnectionStats)
        assert stats.numBytesSent == 0
        assert stats.numMsgSent == 0

    @patch('ib_insync.client.getLoop')
    def test_get_req_id(self, mock_get_loop):
        """Test getReqId method."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        # Test sequence increment
        first_id = client.getReqId()
        second_id = client.getReqId()
        
        assert first_id == 1
        assert second_id == 2
        assert client.reqIdSeq == 2

    @patch('ib_insync.client.getLoop')
    def test_get_req_id_wrap_around(self, mock_get_loop):
        """Test getReqId wrap-around behavior."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        # Set sequence near max
        client.reqIdSeq = 999999998
        
        first_id = client.getReqId()
        second_id = client.getReqId()
        third_id = client.getReqId()
        
        assert first_id == 999999999
        assert second_id == 1000000000
        assert third_id == 1  # Should wrap around


class TestClientThrottling:
    """Test client request throttling."""

    def test_throttle_check_no_throttling(self):
        """Test throttle check when throttling is disabled."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.MaxRequests = 0  # Disable throttling
        
        result = client._throttleCheck()
        assert result == 0

    def test_throttle_check_under_limit(self):
        """Test throttle check when under request limit."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.MaxRequests = 10
        client.reqCount = 5
        client.lastTime = time.time() - 2  # 2 seconds ago
        
        result = client._throttleCheck()
        assert result == 0

    def test_throttle_check_over_limit(self):
        """Test throttle check when over request limit."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.MaxRequests = 10
        client.reqCount = 15
        client.lastTime = time.time() - 0.5  # 0.5 seconds ago
        
        result = client._throttleCheck()
        assert result > 0  # Should return positive delay

    def test_throttle_check_reset_after_interval(self):
        """Test throttle check resets after interval."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.MaxRequests = 10
        client.reqCount = 15
        client.lastTime = time.time() - 2  # 2 seconds ago (> RequestsInterval)
        
        result = client._throttleCheck()
        assert result == 0
        assert client.reqCount == 0  # Should reset


class TestClientMessageHandling:
    """Test client message handling."""

    def test_send_msg_not_connected(self):
        """Test sendMsg when not connected."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        # Should not raise exception, just log
        client.sendMsg('test message')

    def test_send_msg_connected(self):
        """Test sendMsg when connected."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.connState = Client.CONNECTED
        
        # Mock connection
        mock_conn = Mock()
        client.conn = mock_conn
        
        client.sendMsg('test message')
        
        mock_conn.sendMsg.assert_called_once_with('test message')

    def test_prepare_buffer(self):
        """Test _prepareBuffer method."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        # Test with fields
        fields = ['field1', 'field2', 123, 45.67]
        buffer = client._prepareBuffer(fields)
        
        assert isinstance(buffer, bytes)
        assert len(buffer) > 0

    def test_prepare_buffer_empty(self):
        """Test _prepareBuffer with empty fields."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        buffer = client._prepareBuffer([])
        
        assert isinstance(buffer, bytes)
        assert len(buffer) == 4  # Just the length prefix

    def test_prepare_buffer_none_values(self):
        """Test _prepareBuffer with None values."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        fields = ['field1', None, 123, None]
        buffer = client._prepareBuffer(fields)
        
        assert isinstance(buffer, bytes)
        assert len(buffer) > 0

    def test_prepare_buffer_boolean_values(self):
        """Test _prepareBuffer with boolean values."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        fields = [True, False, 'field']
        buffer = client._prepareBuffer(fields)
        
        assert isinstance(buffer, bytes)
        assert len(buffer) > 0


class TestClientAsync:
    """Test client async methods."""

    @pytest.mark.asyncio
    async def test_connect_async_success(self):
        """Test successful async connection."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        # Mock connection
        mock_conn = Mock(spec=Connection)
        mock_conn.connectAsync = AsyncMock()
        client.conn = mock_conn
        
        # Mock the connection handshake
        with patch.object(client, '_onConnected') as mock_on_connected:
            mock_on_connected.return_value = asyncio.Future()
            mock_on_connected.return_value.set_result(None)
            
            result = await client.connectAsync('127.0.0.1', 7497, 1)
            
            assert result == client
            mock_conn.connectAsync.assert_called_once_with('127.0.0.1', 7497)

    @pytest.mark.asyncio
    async def test_connect_async_timeout(self):
        """Test async connection timeout."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        # Mock connection that never completes
        mock_conn = Mock(spec=Connection)
        mock_conn.connectAsync = AsyncMock()
        client.conn = mock_conn
        
        # Mock a connection that hangs
        never_complete = asyncio.Future()
        mock_conn.connectAsync.return_value = never_complete
        
        with pytest.raises(asyncio.TimeoutError):
            await client.connectAsync('127.0.0.1', 7497, 1, timeout=0.1)

    @pytest.mark.asyncio
    async def test_connect_async_connection_error(self):
        """Test async connection error."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        # Mock connection
        mock_conn = Mock(spec=Connection)
        mock_conn.connectAsync = AsyncMock()
        mock_conn.connectAsync.side_effect = ConnectionError("Connection failed")
        client.conn = mock_conn
        
        with pytest.raises(ConnectionError):
            await client.connectAsync('127.0.0.1', 7497, 1)

    def test_connect_blocking(self):
        """Test blocking connect method."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        # Mock the async method
        with patch.object(client, 'connectAsync') as mock_connect_async:
            mock_connect_async.return_value = asyncio.Future()
            mock_connect_async.return_value.set_result(client)
            
            with patch('ib_insync.client.run') as mock_run:
                mock_run.return_value = client
                
                result = client.connect('127.0.0.1', 7497, 1)
                
                assert result == client
                mock_connect_async.assert_called_once_with('127.0.0.1', 7497, 1, timeout=2)

    def test_disconnect(self):
        """Test disconnect method."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        # Mock connection
        mock_conn = Mock()
        client.conn = mock_conn
        client.connState = Client.CONNECTED
        
        client.disconnect()
        
        mock_conn.disconnect.assert_called_once()
        assert client.connState == Client.DISCONNECTED

    def test_disconnect_not_connected(self):
        """Test disconnect when not connected."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        # Should not raise exception
        client.disconnect()


class TestClientOrderMethods:
    """Test client order-related methods."""

    def test_place_order(self):
        """Test placeOrder method."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.connState = Client.CONNECTED
        
        # Mock connection
        mock_conn = Mock()
        client.conn = mock_conn
        
        # Mock contract and order
        contract = Stock('AAPL', 'SMART', 'USD')
        order = Mock()
        order.orderId = 123
        order.action = 'BUY'
        order.totalQuantity = 100
        order.orderType = 'MKT'
        
        # Mock all the required attributes
        order.account = ''
        order.openClose = ''
        order.origin = 0
        order.transmit = True
        order.parentId = 0
        order.blockOrder = False
        order.sweepToFill = False
        order.displaySize = 0
        order.triggerMethod = 0
        order.outsideRth = False
        order.hidden = False
        order.goodAfterTime = ''
        order.goodTillDate = ''
        order.overridePercentageConstraints = False
        order.rule80A = ''
        order.allOrNone = False
        order.minQty = 0
        order.percentOffset = 0
        order.trailStopPrice = 0
        order.trailingPercent = 0
        order.faGroup = ''
        order.faProfile = ''
        order.faMethod = ''
        order.faPercentage = ''
        order.shortSaleSlot = 0
        order.designatedLocation = ''
        order.exemptCode = -1
        order.ocaGroup = ''
        order.ocaType = 0
        order.ref = ''
        order.deltaNeutralOrderType = ''
        order.deltaNeutralAuxPrice = 0
        order.deltaNeutralConId = 0
        order.deltaNeutralOpenClose = ''
        order.deltaNeutralShortSale = False
        order.deltaNeutralShortSaleSlot = 0
        order.deltaNeutralDesignatedLocation = ''
        order.continuousUpdate = False
        order.referencePriceType = 0
        order.basisPoints = 0
        order.basisPointsType = 0
        order.scaleInitLevelSize = 0
        order.scaleSubsLevelSize = 0
        order.scalePriceIncrement = 0
        order.scalePriceAdjustValue = 0
        order.scalePriceAdjustInterval = 0
        order.scaleProfitOffset = 0
        order.scaleAutoReset = False
        order.scaleInitPosition = 0
        order.scaleInitFillQty = 0
        order.scaleRandomPercent = False
        order.scaleTable = ''
        order.hedgeType = ''
        order.hedgeParam = ''
        order.clearingAccount = ''
        order.clearingIntent = ''
        order.algoStrategy = ''
        order.algoParams = []
        order.smartComboRoutingParams = []
        order.orderComboLegs = []
        order.orderMiscOptions = []
        order.notHeld = False
        order.solicited = False
        order.modelCode = ''
        order.orderRef = ''
        order.peggedChangeAmount = 0
        order.isPeggedChangeAmountDecrease = False
        order.referenceChangeAmount = 0
        order.referenceExchangeId = ''
        order.adjustedOrderType = ''
        order.triggerPrice = 0
        order.adjustedStopPrice = 0
        order.adjustedStopLimitPrice = 0
        order.adjustedTrailingAmount = 0
        order.adjustableTrailingUnit = 0
        order.lmtPriceOffset = 0
        order.conditions = []
        order.conditionsIgnoreRth = False
        order.conditionsCancelOrder = False
        order.tier = ''
        order.isOmsContainer = False
        order.discretionaryUpToLimitPrice = False
        order.usePriceMgmtAlgo = False
        order.duration = 0
        order.postToAts = 0
        order.autoCancelParent = False
        order.minTradeQty = 0
        order.minCompeteSize = 0
        order.competeAgainstBestOffset = 0
        order.midOffsetAtWhole = 0
        order.midOffsetAtHalf = 0
        order.customerAccount = ''
        order.professionalCustomer = False
        order.bondAccruedInterest = ''
        
        # Mock _prepareBuffer to return some bytes
        with patch.object(client, '_prepareBuffer') as mock_prepare:
            mock_prepare.return_value = b'test_buffer'
            
            client.placeOrder(order.orderId, contract, order)
            
            mock_prepare.assert_called_once()
            mock_conn.sendMsg.assert_called_once_with(b'test_buffer')

    def test_cancel_order(self):
        """Test cancelOrder method."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.connState = Client.CONNECTED
        
        # Mock connection
        mock_conn = Mock()
        client.conn = mock_conn
        
        with patch.object(client, '_prepareBuffer') as mock_prepare:
            mock_prepare.return_value = b'test_buffer'
            
            client.cancelOrder(123, '')
            
            mock_prepare.assert_called_once()
            mock_conn.sendMsg.assert_called_once_with(b'test_buffer')

    def test_req_open_orders(self):
        """Test reqOpenOrders method."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.connState = Client.CONNECTED
        
        # Mock connection
        mock_conn = Mock()
        client.conn = mock_conn
        
        with patch.object(client, '_prepareBuffer') as mock_prepare:
            mock_prepare.return_value = b'test_buffer'
            
            client.reqOpenOrders()
            
            mock_prepare.assert_called_once()
            mock_conn.sendMsg.assert_called_once_with(b'test_buffer')


class TestClientDataRequests:
    """Test client data request methods."""

    def test_req_contract_details(self):
        """Test reqContractDetails method."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.connState = Client.CONNECTED
        
        # Mock connection
        mock_conn = Mock()
        client.conn = mock_conn
        
        contract = Stock('AAPL', 'SMART', 'USD')
        
        with patch.object(client, '_prepareBuffer') as mock_prepare:
            mock_prepare.return_value = b'test_buffer'
            
            client.reqContractDetails(1, contract)
            
            mock_prepare.assert_called_once()
            mock_conn.sendMsg.assert_called_once_with(b'test_buffer')

    def test_req_market_data(self):
        """Test reqMktData method."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.connState = Client.CONNECTED
        
        # Mock connection
        mock_conn = Mock()
        client.conn = mock_conn
        
        contract = Stock('AAPL', 'SMART', 'USD')
        
        with patch.object(client, '_prepareBuffer') as mock_prepare:
            mock_prepare.return_value = b'test_buffer'
            
            client.reqMktData(1, contract, '', False, False, [])
            
            mock_prepare.assert_called_once()
            mock_conn.sendMsg.assert_called_once_with(b'test_buffer')

    def test_cancel_market_data(self):
        """Test cancelMktData method."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.connState = Client.CONNECTED
        
        # Mock connection
        mock_conn = Mock()
        client.conn = mock_conn
        
        with patch.object(client, '_prepareBuffer') as mock_prepare:
            mock_prepare.return_value = b'test_buffer'
            
            client.cancelMktData(1)
            
            mock_prepare.assert_called_once()
            mock_conn.sendMsg.assert_called_once_with(b'test_buffer')

    def test_req_historical_data(self):
        """Test reqHistoricalData method."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.connState = Client.CONNECTED
        
        # Mock connection
        mock_conn = Mock()
        client.conn = mock_conn
        
        contract = Stock('AAPL', 'SMART', 'USD')
        
        with patch.object(client, '_prepareBuffer') as mock_prepare:
            mock_prepare.return_value = b'test_buffer'
            
            client.reqHistoricalData(
                1, contract, '', '30 D', '1 hour', 'TRADES', 1, 1, False, []
            )
            
            mock_prepare.assert_called_once()
            mock_conn.sendMsg.assert_called_once_with(b'test_buffer')


class TestClientAccountMethods:
    """Test client account-related methods."""

    def test_req_account_updates(self):
        """Test reqAccountUpdates method."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.connState = Client.CONNECTED
        
        # Mock connection
        mock_conn = Mock()
        client.conn = mock_conn
        
        with patch.object(client, '_prepareBuffer') as mock_prepare:
            mock_prepare.return_value = b'test_buffer'
            
            client.reqAccountUpdates(True, 'U123456')
            
            mock_prepare.assert_called_once()
            mock_conn.sendMsg.assert_called_once_with(b'test_buffer')

    def test_req_positions(self):
        """Test reqPositions method."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.connState = Client.CONNECTED
        
        # Mock connection
        mock_conn = Mock()
        client.conn = mock_conn
        
        with patch.object(client, '_prepareBuffer') as mock_prepare:
            mock_prepare.return_value = b'test_buffer'
            
            client.reqPositions()
            
            mock_prepare.assert_called_once()
            mock_conn.sendMsg.assert_called_once_with(b'test_buffer')

    def test_cancel_positions(self):
        """Test cancelPositions method."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.connState = Client.CONNECTED
        
        # Mock connection
        mock_conn = Mock()
        client.conn = mock_conn
        
        with patch.object(client, '_prepareBuffer') as mock_prepare:
            mock_prepare.return_value = b'test_buffer'
            
            client.cancelPositions()
            
            mock_prepare.assert_called_once()
            mock_conn.sendMsg.assert_called_once_with(b'test_buffer')


class TestClientErrorHandling:
    """Test client error handling."""

    def test_connection_lost_callback(self):
        """Test connection lost callback."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        client.connState = Client.CONNECTED
        
        # Mock connection
        mock_conn = Mock()
        client.conn = mock_conn
        
        # Simulate connection lost
        client._onSocketDisconnected('Connection lost')
        
        assert client.connState == Client.DISCONNECTED

    def test_api_error_emission(self):
        """Test API error event emission."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        # Mock error event
        error_emitted = []
        client.apiError += lambda msg: error_emitted.append(msg)
        
        client.apiError.emit('Test error')
        
        assert len(error_emitted) == 1
        assert error_emitted[0] == 'Test error'

    def test_throttle_events(self):
        """Test throttle events."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        # Mock throttle events
        throttle_start_called = []
        throttle_end_called = []
        
        client.throttleStart += lambda: throttle_start_called.append(True)
        client.throttleEnd += lambda: throttle_end_called.append(True)
        
        client.throttleStart.emit()
        client.throttleEnd.emit()
        
        assert len(throttle_start_called) == 1
        assert len(throttle_end_called) == 1


class TestClientIntegration:
    """Test client integration scenarios."""

    def test_client_state_transitions(self):
        """Test client state transitions."""
        wrapper = MockWrapper()
        client = Client(wrapper)
        
        # Initial state
        assert client.connState == Client.DISCONNECTED
        
        # Mock connection process
        client.connState = Client.CONNECTING
        assert client.connState == Client.CONNECTING
        
        client.connState = Client.CONNECTED
        assert client.connState == Client.CONNECTED
        
        client.connState = Client.DISCONNECTED
        assert client.connState == Client.DISCONNECTED

    def test_client_with_real_wrapper(self):
        """Test client with real wrapper integration."""
        mock_ib = Mock()
        wrapper = Wrapper(mock_ib)
        client = Client(wrapper)
        
        # Test basic functionality
        assert client.wrapper is wrapper
        assert client.isConnected() is False
        assert isinstance(client.connectionStats(), ConnectionStats)
        
        # Test request ID generation
        req_id = client.getReqId()
        assert isinstance(req_id, int)
        assert req_id > 0