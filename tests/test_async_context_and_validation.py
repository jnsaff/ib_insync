"""Tests for IB async context manager (task-3) and error handling/validation (task-4)."""

import asyncio
import datetime as dt
import logging
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ib_insync import (
    Contract, ContractDetails, Forex, IB, LimitOrder, OrderStatus, Stock,
    Trade, util)
from ib_insync.client import Client
from ib_insync.decoder import Decoder
from ib_insync.wrapper import Wrapper


class TestIBAsyncContextManager:
    @pytest.mark.asyncio
    async def test_async_with_ib_normal_exit_disconnects(self):
        ib = IB()
        ib.disconnect = MagicMock()  # type: ignore[method-assign]

        async with ib as entered:
            assert entered is ib
            ib.disconnect.assert_not_called()

        ib.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_with_ib_exception_disconnects_and_propagates(self):
        ib = IB()
        ib.disconnect = MagicMock()  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match='boom'):
            async with ib:
                raise RuntimeError('boom')

        ib.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_with_ib_cancellation_disconnects(self):
        ib = IB()
        ib.disconnect = MagicMock()  # type: ignore[method-assign]

        async def worker():
            async with ib:
                await asyncio.sleep(10)

        task = asyncio.create_task(worker())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        ib.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_with_awaited_connect_async(self):
        ib = IB()
        ib.connectAsync = AsyncMock(return_value=ib)  # type: ignore[method-assign]
        ib.disconnect = MagicMock()  # type: ignore[method-assign]

        async with await ib.connectAsync('127.0.0.1', 7497, clientId=7) as conn:
            assert conn is ib
            ib.connectAsync.assert_awaited_once_with(
                '127.0.0.1', 7497, clientId=7)
            ib.disconnect.assert_not_called()

        ib.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_context_connects_and_disconnects(self):
        ib = IB()
        ib.connectAsync = AsyncMock(return_value=ib)  # type: ignore[method-assign]
        ib.disconnect = MagicMock()  # type: ignore[method-assign]

        async with ib.connectContext(
            '127.0.0.1', 4002, clientId=9, timeout=5,
            readonly=True, account='DU123', raiseSyncErrors=True,
        ) as connected:
            assert connected is ib
            ib.connectAsync.assert_awaited_once_with(
                '127.0.0.1', 4002, 9, 5, True, 'DU123', True)
            ib.disconnect.assert_not_called()

        ib.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_context_disconnects_on_body_error(self):
        ib = IB()
        ib.connectAsync = AsyncMock(return_value=ib)  # type: ignore[method-assign]
        ib.disconnect = MagicMock()  # type: ignore[method-assign]

        with pytest.raises(ValueError, match='body error'):
            async with ib.connectContext('127.0.0.1', 7497, clientId=1):
                raise ValueError('body error')

        ib.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_context_propagates_connect_failure(self):
        ib = IB()
        ib.connectAsync = AsyncMock(  # type: ignore[method-assign]
            side_effect=ConnectionRefusedError('refused'))
        ib.disconnect = MagicMock()  # type: ignore[method-assign]

        with pytest.raises(ConnectionRefusedError, match='refused'):
            async with ib.connectContext('127.0.0.1', 7497, clientId=1):
                pytest.fail('should not enter context body')

        # connectAsync handles its own cleanup on failure
        ib.disconnect.assert_not_called()

    def test_sync_context_manager_backwards_compatible(self):
        ib = IB()
        ib.disconnect = MagicMock()  # type: ignore[method-assign]
        with ib as entered:
            assert entered is ib
        ib.disconnect.assert_called_once()


class TestClientFrameAndHandshakeHandling:
    def test_oversized_frame_disconnects_with_error(self):
        client = Client(MagicMock())
        client.conn = MagicMock()
        client.connState = Client.CONNECTED
        errors = []
        client.apiError += errors.append

        huge_header = struct.pack('>I', Client.MaxMsgLen + 1)
        client._onSocketHasData(huge_header + b'payload')

        client.conn.disconnect.assert_called_once()
        assert client._data == b''
        assert any('exceeds maximum' in msg for msg in errors)

    def test_non_numeric_handshake_disconnects_fast_with_error(self):
        client = Client(MagicMock())
        client.conn = MagicMock()
        client.connState = Client.CONNECTING
        errors = []
        client.apiError += errors.append

        payload = b'HTTP/1.1 400\0Bad Request\0'
        frame = struct.pack('>I', len(payload)) + payload
        client._onSocketHasData(frame)

        client.conn.disconnect.assert_called_once()
        assert any('Invalid TWS/gateway handshake' in msg for msg in errors)

    def test_malformed_pre_ready_message_does_not_crash(self, caplog):
        client = Client(MagicMock())
        client.conn = MagicMock()
        client.connState = Client.CONNECTED
        client._serverVersion = 176
        client._apiReady = False

        payload = b'not_a_number\0extra\0'
        frame = struct.pack('>I', len(payload)) + payload
        with caplog.at_level(logging.WARNING):
            client._onSocketHasData(frame)

        assert not client._apiReady
        assert any('Malformed pre-ready message' in r.message
                   for r in caplog.records)


class TestDecoderValidationAndRecovery:
    def test_interpret_handles_empty_invalid_and_unknown_messages(self, caplog):
        decoder = Decoder(MagicMock(), serverVersion=176)
        with caplog.at_level(logging.WARNING):
            decoder.interpret([])
            decoder.interpret(['abc', '1'])
            decoder.interpret(['999999', '1'])

        messages = [r.message for r in caplog.records]
        assert any('empty message' in m for m in messages)
        assert any('non-integer message id' in m for m in messages)
        assert any('Unknown message id 999999' in m for m in messages)

    def test_wrap_rejects_truncated_fields_without_calling_wrapper(self, caplog):
        wrapper = MagicMock()
        decoder = Decoder(wrapper, serverVersion=176)
        handler = decoder.wrap('tickSize', [int, int, float], skip=2)

        with caplog.at_level(logging.ERROR):
            handler(['2', '1', '10'])  # missing tickType and size

        wrapper.tickSize.assert_not_called()
        assert any('Truncated tickSize message' in r.message
                   for r in caplog.records)

    def test_parse_recovers_from_invalid_field_values(self, caplog):
        decoder = Decoder(MagicMock(), serverVersion=176)
        c = Contract()
        c.conId = 'not_an_int'  # type: ignore[assignment]
        c.strike = 'bad_float'  # type: ignore[assignment]
        c.includeExpired = 'not_bool'  # type: ignore[assignment]
        c.symbol = 'AAPL'

        with caplog.at_level(logging.WARNING):
            decoder.parse(c)

        assert c.conId == 0
        assert c.strike == 0.0
        assert c.includeExpired is False
        assert c.symbol == 'AAPL'
        assert len(caplog.records) == 3


class TestPublicAPIValidation:
    def test_bracket_order_validates_action_and_quantity(self):
        ib = IB()
        with pytest.raises(ValueError, match="action must be 'BUY' or 'SELL'"):
            ib.bracketOrder('HOLD', 10, 100.0, 110.0, 90.0)
        for bad_qty in (0, -5, True):
            with pytest.raises(ValueError, match='quantity must be positive'):
                ib.bracketOrder('BUY', bad_qty, 100.0, 110.0, 90.0)

    def test_place_and_cancel_order_validate_types(self):
        ib = IB()
        stock = Stock('AAPL', 'SMART', 'USD')
        order = LimitOrder('BUY', 10, 150.0)

        with pytest.raises(TypeError, match='contract must be a Contract'):
            ib.placeOrder('AAPL', order)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match='order must be an Order'):
            ib.placeOrder(stock, 'BUY')  # type: ignore[arg-type]
        with pytest.raises(TypeError, match='order must be an Order'):
            ib.cancelOrder('order')  # type: ignore[arg-type]

    def test_req_pnl_and_pnl_single_validation(self):
        ib = IB()
        ib.client.getReqId = MagicMock(side_effect=[1, 2, 3, 4])  # type: ignore[method-assign]
        ib.client.reqPnL = MagicMock()  # type: ignore[method-assign]
        ib.client.reqPnLSingle = MagicMock()  # type: ignore[method-assign]

        with pytest.raises(ValueError, match='account must be a non-empty'):
            ib.reqPnL('')
        with pytest.raises(ValueError, match='account must be a non-empty'):
            ib.reqPnLSingle('   ', '', 1234)
        for bad_conid in (0, -1, True):
            with pytest.raises(ValueError, match='conId must be a positive'):
                ib.reqPnLSingle('DU123', '', bad_conid)

        ib.reqPnL('DU123')
        with pytest.raises(ValueError, match='PnL subscription already active'):
            ib.reqPnL('DU123')

        ib.reqPnLSingle('DU123', '', 265598)
        with pytest.raises(
                ValueError, match='PnLSingle subscription already active'):
            ib.reqPnLSingle('DU123', '', 265598)

    def test_timeout_validation_on_ib_and_wrapper(self):
        ib = IB()
        for bad in (-1, -0.1, True):
            with pytest.raises(ValueError, match='timeout must be non-negative'):
                ib.waitOnUpdate(bad)
            with pytest.raises(ValueError, match='timeout must be non-negative'):
                next(ib.loopUntil(lambda: True, timeout=bad))
            with pytest.raises(ValueError, match='timeout must be non-negative'):
                ib.setTimeout(bad)
            with pytest.raises(ValueError, match='timeout must be non-negative'):
                ib.wrapper.setTimeout(bad)

    @pytest.mark.asyncio
    async def test_connect_async_validates_client_id_and_captures_exec_error(self):
        ib = IB()
        ib.wrapper.clientId = 42

        with pytest.raises(ValueError, match='Invalid clientId'):
            await ib.connectAsync('127.0.0.1', 7497, clientId=True)
        assert ib.wrapper.clientId == 42

        with pytest.raises(ValueError, match='clientId must be non-negative'):
            await ib.connectAsync('127.0.0.1', 7497, clientId=-5)
        assert ib.wrapper.clientId == 42

        # Verify non-timeout execution error is captured cleanly
        ib.client.connectAsync = AsyncMock()  # type: ignore[method-assign]
        ib.client.getAccounts = MagicMock(return_value=['DU123'])  # type: ignore[method-assign]
        ib.client.serverVersion = MagicMock(return_value=176)  # type: ignore[method-assign]
        ib.client.isReady = MagicMock(return_value=True)  # type: ignore[method-assign]
        ib.reqPositionsAsync = AsyncMock(return_value=[])  # type: ignore[method-assign]
        ib.reqAccountUpdatesAsync = AsyncMock(return_value=None)  # type: ignore[method-assign]
        ib.reqAccountUpdatesMultiAsync = AsyncMock(return_value=None)  # type: ignore[method-assign]
        ib.reqExecutionsAsync = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError('exec failed'))

        with pytest.raises(ConnectionError, match='executions request failed'):
            await ib.connectAsync(
                readonly=True, raiseSyncErrors=True, timeout=1.0)

    @pytest.mark.asyncio
    async def test_qualify_contracts_handles_missing_contract_in_details(self, caplog):
        ib = IB()
        ib.reqContractDetailsAsync = AsyncMock(  # type: ignore[method-assign]
            return_value=[ContractDetails(contract=None)])

        with caplog.at_level(logging.WARNING):
            res = await ib.qualifyContractsAsync(Stock('AAPL', 'SMART', 'USD'))

        assert res == []
        assert any('missing contract' in r.message for r in caplog.records)

    def test_util_validation_helpers(self):
        with pytest.raises(TypeError, match='Expected IB datetime string'):
            util.parseIBDatetime(12345)  # type: ignore[arg-type]
        for bad in ('', '   ', 'not-a-date', '20249999'):
            with pytest.raises(ValueError, match='Invalid IB datetime string'):
                util.parseIBDatetime(bad)

        for bad_step in (0, -1, True):
            with pytest.raises(ValueError, match='step must be a positive'):
                next(util.timeRange(dt.time(0, 0), dt.time(0, 1), bad_step))
        with pytest.raises(ValueError, match='secs must be non-negative'):
            util.sleep(-1)
        with pytest.raises(ValueError, match='timeout must be None or non-negative'):
            util.run(timeout=-1)

    @pytest.mark.asyncio
    async def test_time_range_async_rejects_invalid_step(self):
        with pytest.raises(ValueError, match='step must be a positive'):
            async for _ in util.timeRangeAsync(dt.time(0, 0), dt.time(0, 1), 0):
                pass
