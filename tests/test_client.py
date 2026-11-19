"""Unit tests for Client class."""

import asyncio
import logging
import math
import struct
from collections import deque
from unittest.mock import AsyncMock, Mock, patch

import pytest
from eventkit import Event

from ib_insync.client import Client
from ib_insync.connection import Connection
from ib_insync.contract import (
    Bag, ComboLeg, DeltaNeutralContract, Stock, TagValue)
from ib_insync.ib import IB
from ib_insync.objects import ConnectionStats, ExecutionFilter, WshEventData
from ib_insync.order import (
    LimitOrder, MarketOrder, OrderComboLeg, PriceCondition)
from ib_insync.util import UNSET_DOUBLE, UNSET_INTEGER
from ib_insync.wrapper import Wrapper


def make_mock_ib():
    """Create a mock IB object with real Event instances for Wrapper."""
    ib = Mock()
    ib.RaiseRequestErrors = False
    ib.TimezoneTWS = ''
    for name in IB.events:
        setattr(ib, name, Event(name))
    ib.client = Mock()
    return ib


def make_wrapper():
    """Create a real Wrapper backed by a mock IB."""
    return Wrapper(make_mock_ib())


def frame_msg(*fields) -> bytes:
    """Encode a list of string fields as a length-prefixed IB wire frame."""
    payload = ('\0'.join(str(f) for f in fields) + '\0').encode()
    return struct.pack('>I', len(payload)) + payload


def decode_sent(raw: bytes) -> list:
    """Decode a length-prefixed IB wire frame into its list of fields."""
    length = struct.unpack('>I', raw[:4])[0]
    assert len(raw) == 4 + length
    fields = raw[4:].decode().split('\0')
    assert fields[-1] == ''
    return fields[:-1]


def make_ready_client(server_version: int = 178):
    """Create a Client in CONNECTED & ready state with a mocked Connection."""
    wrapper = make_wrapper()
    client = Client(wrapper)
    client.conn = Mock(spec=Connection)
    client.conn.numBytesSent = 0
    client.conn.numMsgSent = 0
    client.connState = Client.CONNECTED
    client._apiReady = True
    client._serverVersion = server_version
    client.decoder.serverVersion = server_version
    client._accounts = ['DU123456']
    client._reqIdSeq = 1
    return client


@pytest.fixture
def sync_loop():
    """Provide an isolated current event loop for synchronous Client tests."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        loop.close()
        asyncio.set_event_loop(None)


class TestClientCreation:
    """Test Client creation and initialization."""

    def test_client_creation(self):
        """Test basic client creation and default state."""
        wrapper = make_wrapper()
        client = Client(wrapper)

        assert client.wrapper is wrapper
        assert client.decoder is not None
        assert client.MinClientVersion == 157
        assert client.MaxClientVersion == 178
        assert client.MaxRequests == 45
        assert client.RequestsInterval == 1
        assert client.connState == Client.DISCONNECTED
        assert client.serverVersion() == 0
        assert client.isConnected() is False
        assert client.isReady() is False
        assert client._reqIdSeq == 0
        assert client._accounts == []
        assert isinstance(client._msgQ, deque)
        assert len(client._msgQ) == 0
        assert isinstance(client._timeQ, deque)
        assert len(client._timeQ) == 0
        assert client._isThrottling is False

    def test_client_events(self):
        """Test client event initialization."""
        client = Client(make_wrapper())

        for event_name in Client.events:
            event = getattr(client, event_name)
            assert isinstance(event, Event)
            assert callable(event.emit)

    def test_client_logger(self):
        """Test client logger initialization."""
        client = Client(make_wrapper())

        assert isinstance(client._logger, logging.Logger)
        assert client._logger.name == 'ib_insync.client'


class TestClientConnectionMethods:
    """Test client connection state and accessor methods."""

    def test_is_connected_and_ready(self):
        """Test isConnected and isReady state transitions."""
        client = Client(make_wrapper())
        assert not client.isConnected()
        assert not client.isReady()

        client.connState = Client.CONNECTING
        assert not client.isConnected()

        client.connState = Client.CONNECTED
        assert client.isConnected()
        assert not client.isReady()

        client._apiReady = True
        assert client.isReady()

    def test_connection_stats_when_ready(self):
        """Test connectionStats returns stats when API is ready."""
        client = make_ready_client()
        client.conn.numBytesSent = 1000
        client.conn.numMsgSent = 50
        client._numBytesRecv = 2500
        client._numMsgRecv = 75

        stats = client.connectionStats()

        assert isinstance(stats, ConnectionStats)
        assert stats.numBytesSent == 1000
        assert stats.numMsgSent == 50
        assert stats.numBytesRecv == 2500
        assert stats.numMsgRecv == 75
        assert stats.duration >= 0

    def test_connection_stats_not_ready_raises(self):
        """Test connectionStats raises ConnectionError when not ready."""
        client = Client(make_wrapper())
        with pytest.raises(ConnectionError, match='Not connected'):
            client.connectionStats()

    def test_get_req_id_and_update(self):
        """Test getReqId increments monotonically and respects updateReqId."""
        client = make_ready_client()
        assert client.getReqId() == 1
        assert client.getReqId() == 2

        client.updateReqId(100)
        assert client.getReqId() == 100

        # updateReqId never moves sequence backwards
        client.updateReqId(50)
        assert client.getReqId() == 101

    def test_get_req_id_not_ready_raises(self):
        """Test getReqId raises ConnectionError when not ready."""
        client = Client(make_wrapper())
        with pytest.raises(ConnectionError, match='Not connected'):
            client.getReqId()

    def test_get_accounts(self):
        """Test getAccounts when ready vs not ready."""
        client = Client(make_wrapper())
        with pytest.raises(ConnectionError, match='Not connected'):
            client.getAccounts()

        client._apiReady = True
        client._accounts = ['DU111', 'DU222']
        assert client.getAccounts() == ['DU111', 'DU222']

    def test_set_connect_options(self):
        """Test setConnectOptions encodes string to bytes."""
        client = Client(make_wrapper())
        client.setConnectOptions('+PACEAPI')
        assert client.connectOptions == b'+PACEAPI'


class TestClientMessageSerialization:
    """Test Client.send, _prefix, and sendMsg."""

    def test_prefix(self):
        """Test 4-byte big-endian length prefixing."""
        client = Client(make_wrapper())
        payload = b'hello'
        assert client._prefix(payload) == struct.pack('>I', 5) + payload

    def test_send_not_connected_raises(self):
        """Test send raises ConnectionError when disconnected."""
        client = Client(make_wrapper())
        with pytest.raises(ConnectionError, match='Not connected'):
            client.send(1, 'test')

    def test_send_primitive_fields(self, sync_loop):
        """Test send serializes strings, ints, floats, inf, bools, and unset."""
        client = make_ready_client()
        client.send(
            'abc', 42, 3.5, math.inf, True, False,
            None, UNSET_INTEGER, UNSET_DOUBLE
        )

        client.conn.sendMsg.assert_called_once()
        raw = client.conn.sendMsg.call_args[0][0]
        assert decode_sent(raw) == [
            'abc', '42', '3.5', 'Infinite', '1', '0', '', '', ''
        ]

    def test_send_make_empty_false(self, sync_loop):
        """Test send with makeEmpty=False preserves UNSET_INTEGER."""
        client = make_ready_client()
        client.send(102, 1, UNSET_INTEGER, None, makeEmpty=False)

        raw = client.conn.sendMsg.call_args[0][0]
        assert decode_sent(raw) == ['102', '1', str(UNSET_INTEGER), '']

    def test_send_tag_value_list(self, sync_loop):
        """Test send serializes list of TagValue as semicolon-delimited."""
        client = make_ready_client()
        client.send([TagValue('k1', 'v1'), TagValue('k2', 'v2')])

        raw = client.conn.sendMsg.call_args[0][0]
        assert decode_sent(raw) == ['k1=v1;k2=v2;']

    def test_send_contract(self, sync_loop):
        """Test send serializes Contract into its 12 wire fields."""
        client = make_ready_client()
        contract = Stock('AAPL', 'SMART', 'USD', primaryExchange='NASDAQ')
        contract.conId = 265598
        client.send(contract)

        raw = client.conn.sendMsg.call_args[0][0]
        assert decode_sent(raw) == [
            '265598', 'AAPL', 'STK', '', '0.0', '', '',
            'SMART', 'NASDAQ', 'USD', '', ''
        ]


class TestClientThrottling:
    """Test client request throttling in sendMsg."""

    def test_throttling_disabled(self, sync_loop):
        """When MaxRequests == 0, messages are sent without throttling."""
        client = make_ready_client()
        client.MaxRequests = 0
        started = []
        client.throttleStart += lambda: started.append(True)

        for i in range(60):
            client.sendMsg(f'{i}\0')

        assert client.conn.sendMsg.call_count == 60
        assert len(client._msgQ) == 0
        assert started == []

    def test_throttling_queues_over_limit(self, sync_loop):
        """Messages beyond MaxRequests are queued and emit throttleStart."""
        client = make_ready_client()
        client.MaxRequests = 2
        client.RequestsInterval = 1.0
        events = []
        client.throttleStart += lambda: events.append('start')
        client.throttleEnd += lambda: events.append('end')

        client.sendMsg('m1\0')
        client.sendMsg('m2\0')
        client.sendMsg('m3\0')

        assert client.conn.sendMsg.call_count == 2
        assert list(client._msgQ) == ['m3\0']
        assert client._isThrottling is True
        assert events == ['start']

    @pytest.mark.asyncio
    async def test_throttling_drains_and_emits_end(self):
        """Queued messages flush after RequestsInterval and emit throttleEnd."""
        client = make_ready_client()
        client.MaxRequests = 1
        client.RequestsInterval = 0.02
        events = []
        client.throttleStart += lambda: events.append('start')
        client.throttleEnd += lambda: events.append('end')

        client.sendMsg('m1\0')
        client.sendMsg('m2\0')
        assert client.conn.sendMsg.call_count == 1
        assert events == ['start']

        await asyncio.sleep(0.06)

        assert client.conn.sendMsg.call_count == 2
        assert len(client._msgQ) == 0
        assert client._isThrottling is False
        assert events == ['start', 'end']


class TestClientSocketReceiving:
    """Test _onSocketHasData handshake, framing, and message dispatch."""

    def test_handshake_and_api_ready(self, sync_loop):
        """Server version + nextValidId + managedAccounts completes startup."""
        wrapper = make_wrapper()
        wrapper.connectAck = Mock()
        client = Client(wrapper)
        client.conn = Mock(spec=Connection)
        client.clientId = 7

        started = []
        client.apiStart += lambda: started.append(True)

        # Handshake frame: serverVersion, connTime
        client._onSocketHasData(frame_msg('178', '20261119 12:00:00 UTC'))
        assert client.serverVersion() == 178
        assert client.decoder.serverVersion == 178
        assert client.connState == Client.CONNECTED
        assert client.isReady() is False
        wrapper.connectAck.assert_called_once()
        # startApi was sent (msgId 71, version 2, clientId 7, optCapab '')
        sent = decode_sent(client.conn.sendMsg.call_args[0][0])
        assert sent == ['71', '2', '7', '']

        # nextValidId (msgId 9, version 1, orderId 42)
        client._onSocketHasData(frame_msg('9', '1', '42'))
        assert client._reqIdSeq == 42
        assert client.isReady() is False

        # managedAccounts (msgId 15, version 1, accounts)
        client._onSocketHasData(frame_msg('15', '1', 'DU111,DU222,'))
        assert client.isReady() is True
        assert client.getAccounts() == ['DU111', 'DU222']
        assert wrapper.accounts == ['DU111', 'DU222']
        assert started == [True]

    def test_partial_tcp_frame_reassembly(self, sync_loop):
        """Frames split across multiple TCP packets are reassembled cleanly."""
        client = make_ready_client()
        client.wrapper.currentTime = Mock()

        frame = frame_msg('49', '1', '1700000000')
        client._onSocketHasData(frame[:3])
        client.wrapper.currentTime.assert_not_called()

        client._onSocketHasData(frame[3:7])
        client.wrapper.currentTime.assert_not_called()

        client._onSocketHasData(frame[7:])
        client.wrapper.currentTime.assert_called_once_with(1700000000)
        assert client._numMsgRecv == 1
        assert client._numBytesRecv == len(frame)

    def test_tcp_data_arrived_and_processed_hooks(self, sync_loop):
        """Optional tcpDataArrived and tcpDataProcessed hooks are invoked."""
        wrapper = make_wrapper()
        wrapper.tcpDataArrived = Mock()
        wrapper.tcpDataProcessed = Mock()
        client = Client(wrapper)
        client.conn = Mock(spec=Connection)
        client._serverVersion = 178
        client._apiReady = True

        client._onSocketHasData(frame_msg('49', '1', '1700000000'))
        wrapper.tcpDataArrived.assert_called_once()
        wrapper.tcpDataProcessed.assert_called_once()

    def test_old_server_version_rejected(self, sync_loop):
        """Server version below MinClientVersion triggers apiError."""
        client = Client(make_wrapper())
        client.conn = Mock(spec=Connection)
        errors = []
        client.apiError += lambda msg: errors.append(msg)

        client._onSocketHasData(frame_msg('100', '20261119'))
        assert client.connState == Client.DISCONNECTED
        assert len(errors) == 1
        assert 'version' in errors[0].lower()


class TestClientAsync:
    """Test Client async connection and disconnect lifecycle."""

    @pytest.mark.asyncio
    async def test_connect_async_success(self):
        """Test successful async connection handshake through apiStart."""
        client = Client(make_wrapper())
        mock_conn = Mock(spec=Connection)
        mock_conn.connectAsync = AsyncMock()

        loop = asyncio.get_running_loop()

        def fake_send(msg):
            if msg.startswith(b'API\0'):
                # Simulate TWS responding asynchronously with handshake +
                # nextValidId + managedAccounts after apiStart is awaited
                loop.call_later(
                    0.01,
                    client._onSocketHasData,
                    frame_msg('178', '20261119 12:00:00 UTC')
                    + frame_msg('9', '1', '10')
                    + frame_msg('15', '1', 'DU123456'),
                )

        mock_conn.sendMsg.side_effect = fake_send
        client.conn = mock_conn

        result = await client.connectAsync('127.0.0.1', 7497, 1, timeout=1.0)

        assert result is None
        mock_conn.connectAsync.assert_awaited_once_with('127.0.0.1', 7497)
        assert client.isConnected() is True
        assert client.isReady() is True
        assert client.serverVersion() == 178
        assert client.getAccounts() == ['DU123456']

    @pytest.mark.asyncio
    async def test_connect_async_timeout(self):
        """Test async connection timeout triggers disconnect and apiError."""
        client = Client(make_wrapper())
        mock_conn = Mock(spec=Connection)

        async def hang(*_args):
            await asyncio.sleep(10)

        mock_conn.connectAsync = AsyncMock(side_effect=hang)
        client.conn = mock_conn

        errors = []
        client.apiError += lambda msg: errors.append(msg)

        with pytest.raises(asyncio.TimeoutError):
            await client.connectAsync('127.0.0.1', 7497, 1, timeout=0.05)

        mock_conn.disconnect.assert_called_once()
        assert client.connState == Client.DISCONNECTED
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_connect_async_connection_error(self):
        """Test ConnectionRefusedError propagates and emits apiError."""
        client = Client(make_wrapper())
        mock_conn = Mock(spec=Connection)
        mock_conn.connectAsync = AsyncMock(
            side_effect=ConnectionRefusedError('Connection refused')
        )
        client.conn = mock_conn

        errors = []
        client.apiError += lambda msg: errors.append(msg)

        with pytest.raises(ConnectionRefusedError):
            await client.connectAsync('127.0.0.1', 7497, 1)

        mock_conn.disconnect.assert_called_once()
        assert len(errors) == 1

    def test_connect_blocking(self):
        """Test blocking connect delegates to run(connectAsync(...))."""
        client = Client(make_wrapper())
        sentinel_coro = object()

        with patch.object(
            client, 'connectAsync', new_callable=Mock, return_value=sentinel_coro
        ) as mock_connect_async, patch('ib_insync.client.run') as mock_run:
            client.connect('127.0.0.1', 7497, 1, timeout=3.0)

            mock_connect_async.assert_called_once_with(
                '127.0.0.1', 7497, 1, 3.0
            )
            mock_run.assert_called_once_with(sentinel_coro)

    def test_disconnect(self):
        """Test disconnect closes socket and resets state."""
        client = make_ready_client()
        client.disconnect()

        client.conn.disconnect.assert_called_once()
        assert client.connState == Client.DISCONNECTED
        assert client.isReady() is False
        assert client.serverVersion() == 0

    def test_disconnect_when_already_disconnected(self):
        """Test disconnect is safe when already disconnected."""
        client = Client(make_wrapper())
        client.disconnect()
        assert client.connState == Client.DISCONNECTED


class TestClientRequests:
    """Test client wire-format request methods."""

    def test_place_order_market(self, sync_loop):
        """Test placeOrder encodes a standard MarketOrder."""
        client = make_ready_client(server_version=178)
        contract = Stock('AAPL', 'SMART', 'USD')
        order = MarketOrder('BUY', 100)

        client.placeOrder(123, contract, order)

        fields = decode_sent(client.conn.sendMsg.call_args[0][0])
        assert fields[0] == '3'
        assert fields[1] == '123'
        assert 'AAPL' in fields
        assert 'BUY' in fields
        assert '100' in fields
        assert 'MKT' in fields

    def test_place_order_bag_with_conditions_and_algo(self, sync_loop):
        """Test placeOrder encodes BAG legs, algo params, and conditions."""
        client = make_ready_client(server_version=178)
        contract = Bag(
            symbol='SPY', exchange='SMART', currency='USD',
            comboLegs=[ComboLeg(conId=11, ratio=1, action='BUY', exchange='SMART')],
            deltaNeutralContract=DeltaNeutralContract(conId=22, delta=0.5, price=100.0),
        )
        order = LimitOrder(
            'BUY', 10, 5.25,
            orderComboLegs=[OrderComboLeg(price=2.5)],
            smartComboRoutingParams=[TagValue('NonGuaranteed', '1')],
            algoStrategy='Adaptive',
            algoParams=[TagValue('adaptivePriority', 'Normal')],
            conditions=[PriceCondition(price=400.0, conId=756733, exch='SMART')],
        )

        client.placeOrder(200, contract, order)
        fields = decode_sent(client.conn.sendMsg.call_args[0][0])
        assert fields[0] == '3'
        assert fields[1] == '200'
        assert 'NonGuaranteed' in fields
        assert 'Adaptive' in fields
        assert 'adaptivePriority' in fields
        assert '400.0' in fields

    def test_cancel_order(self, sync_loop):
        """Test cancelOrder includes manualCancelOrderTime on server >= 169."""
        client = make_ready_client(server_version=178)
        client.cancelOrder(123, '20261119 10:00:00')
        assert decode_sent(client.conn.sendMsg.call_args[0][0]) == [
            '4', '1', '123', '20261119 10:00:00'
        ]

        client.conn.sendMsg.reset_mock()
        client._serverVersion = 168
        client.cancelOrder(123, '20261119 10:00:00')
        assert decode_sent(client.conn.sendMsg.call_args[0][0]) == [
            '4', '1', '123'
        ]

    def test_req_open_orders(self, sync_loop):
        client = make_ready_client()
        client.reqOpenOrders()
        assert decode_sent(client.conn.sendMsg.call_args[0][0]) == ['5', '1']

    def test_req_contract_details(self, sync_loop):
        client = make_ready_client(server_version=178)
        contract = Stock('AAPL', 'SMART', 'USD', issuerId='ISS1')
        client.reqContractDetails(1, contract)
        fields = decode_sent(client.conn.sendMsg.call_args[0][0])
        assert fields[:3] == ['9', '8', '1']
        assert fields[-1] == 'ISS1'

    def test_req_and_cancel_market_data(self, sync_loop):
        client = make_ready_client()
        contract = Stock('AAPL', 'SMART', 'USD')
        client.reqMktData(5, contract, '233', False, False, [])
        fields = decode_sent(client.conn.sendMsg.call_args[0][0])
        assert fields[:3] == ['1', '11', '5']
        assert '233' in fields

        client.conn.sendMsg.reset_mock()
        client.cancelMktData(5)
        assert decode_sent(client.conn.sendMsg.call_args[0][0]) == [
            '2', '2', '5'
        ]

    def test_req_historical_data(self, sync_loop):
        client = make_ready_client()
        contract = Stock('AAPL', 'SMART', 'USD')
        client.reqHistoricalData(
            9, contract, '', '30 D', '1 hour', 'TRADES', True, 1, False, []
        )
        fields = decode_sent(client.conn.sendMsg.call_args[0][0])
        assert fields[:2] == ['20', '9']
        assert '30 D' in fields
        assert '1 hour' in fields
        assert 'TRADES' in fields

    def test_req_account_updates_and_positions(self, sync_loop):
        client = make_ready_client()
        client.reqAccountUpdates(True, 'DU123456')
        assert decode_sent(client.conn.sendMsg.call_args[0][0]) == [
            '6', '2', '1', 'DU123456'
        ]

        client.conn.sendMsg.reset_mock()
        client.reqPositions()
        assert decode_sent(client.conn.sendMsg.call_args[0][0]) == ['61', '1']

        client.conn.sendMsg.reset_mock()
        client.cancelPositions()
        assert decode_sent(client.conn.sendMsg.call_args[0][0]) == ['64', '1']

    def test_req_executions(self, sync_loop):
        client = make_ready_client()
        filt = ExecutionFilter(clientId=1, acctCode='DU1', symbol='AAPL')
        client.reqExecutions(12, filt)
        assert decode_sent(client.conn.sendMsg.call_args[0][0]) == [
            '7', '3', '12', '1', 'DU1', '', 'AAPL', '', '', ''
        ]

    def test_req_wsh_event_data(self, sync_loop):
        client = make_ready_client(server_version=178)
        data = WshEventData(conId=8314, filter='{}', totalLimit=10)
        client.reqWshEventData(15, data)
        fields = decode_sent(client.conn.sendMsg.call_args[0][0])
        assert fields[:4] == ['102', '15', '8314', '{}']
        assert fields[-1] == '10'


class TestClientDisconnectAndEvents:
    """Test socket disconnect handling and event emission."""

    def test_on_socket_disconnected_when_ready(self):
        """Ready disconnect notifies wrapper, resets state, and emits apiEnd."""
        client = make_ready_client()
        client.wrapper.setEventsDone = Mock()
        client.wrapper.connectionClosed = Mock()

        errors = []
        ended = []
        client.apiError += lambda msg: errors.append(msg)
        client.apiEnd += lambda: ended.append(True)

        client._onSocketDisconnected('Connection reset')

        assert client.connState == Client.DISCONNECTED
        assert client.isReady() is False
        assert errors == ['Connection reset']
        assert ended == [True]
        client.wrapper.setEventsDone.assert_called_once()
        client.wrapper.connectionClosed.assert_called_once()

    def test_on_socket_disconnected_peer_closed_before_ready(self):
        """Peer close before apiReady hints at duplicate clientId."""
        client = Client(make_wrapper())
        client.clientId = 3
        client.connState = Client.CONNECTED
        client._apiReady = False

        errors = []
        ended = []
        client.apiError += lambda msg: errors.append(msg)
        client.apiEnd += lambda: ended.append(True)

        client._onSocketDisconnected('')

        assert len(errors) == 1
        assert 'clientId 3' in errors[0]
        assert ended == []

    def test_client_with_real_wrapper_integration(self):
        """Test Client wired to a real Wrapper instance."""
        wrapper = make_wrapper()
        client = Client(wrapper)

        assert client.wrapper is wrapper
        assert client.isConnected() is False
        assert client.isReady() is False

        client.connState = Client.CONNECTED
        client._apiReady = True
        assert isinstance(client.connectionStats(), ConnectionStats)
        assert client.getReqId() == 0
        assert client.getReqId() == 1