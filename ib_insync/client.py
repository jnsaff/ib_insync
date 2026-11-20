"""Socket client for communicating with Interactive Brokers."""

import asyncio
import logging
import math
import struct
import time
from collections import deque
from typing import Any, TYPE_CHECKING

from eventkit import Event

from .connection import Connection
from .contract import Contract, TagValue
from .decoder import Decoder
from .objects import (
    ConnectionStats, ExecutionFilter, ScannerSubscription, WshEventData)
from .order import Order
from .util import UNSET_DOUBLE, UNSET_INTEGER, dataclassAsTuple, getLoop, run

if TYPE_CHECKING:
    from .wrapper import Wrapper

_HEADER_STRUCT = struct.Struct('>I')
_pack_header = _HEADER_STRUCT.pack
_unpack_header_from = _HEADER_STRUCT.unpack_from


class Client:
    """
    Replacement for ``ibapi.client.EClient`` that uses asyncio.

    The client is fully asynchronous and has its own
    event-driven networking code that replaces the
    networking code of the standard EClient.
    It also replaces the infinite loop of ``EClient.run()``
    with the asyncio event loop. It can be used as a drop-in
    replacement for the standard EClient as provided by IBAPI.

    Compared to the standard EClient this client has the following
    additional features:

    * ``client.connect()`` will block until the client is ready to
      serve requests; It is not necessary to wait for ``nextValidId``
      to start requests as the client has already done that.
      The reqId is directly available with :py:meth:`.getReqId()`.

    * ``client.connectAsync()`` is a coroutine for connecting asynchronously.

    * When blocking, ``client.connect()`` can be made to time out with
      the timeout parameter (default 2 seconds).

    * Optional ``wrapper.priceSizeTick(reqId, tickType, price, size)`` that
      combines price and size instead of the two wrapper methods
      priceTick and sizeTick.

    * Automatic request throttling.

    * Optional ``wrapper.tcpDataArrived()`` method;
      If the wrapper has this method it is invoked directly after
      a network packet has arrived.
      A possible use is to timestamp all data in the packet with
      the exact same time.

    * Optional ``wrapper.tcpDataProcessed()`` method;
      If the wrapper has this method it is invoked after the
      network packet's data has been handled.
      A possible use is to write or evaluate the newly arrived data in
      one batch instead of item by item.

    Parameters:
      MaxRequests (int):
        Throttle the number of requests to ``MaxRequests`` per
        ``RequestsInterval`` seconds. Set to 0 to disable throttling.
      RequestsInterval (float):
        Time interval (in seconds) for request throttling.
      MinClientVersion (int):
        Client protocol version.
      MaxClientVersion (int):
        Client protocol version.

    Events:
      * ``apiStart`` ()
      * ``apiEnd`` ()
      * ``apiError`` (errorMsg: str)
      * ``throttleStart`` ()
      * ``throttleEnd`` ()
    """

    events = ('apiStart', 'apiEnd', 'apiError', 'throttleStart', 'throttleEnd')

    MaxRequests = 45
    RequestsInterval = 1
    MaxMsgLen = 0xFFFFFF

    MinClientVersion = 157
    MaxClientVersion = 178

    (DISCONNECTED, CONNECTING, CONNECTED) = range(3)

    def __init__(self, wrapper: 'Wrapper') -> None:
        self.wrapper = wrapper
        self.decoder = Decoder(wrapper, 0)
        self.apiStart = Event('apiStart')
        self.apiEnd = Event('apiEnd')
        self.apiError = Event('apiError')
        self.throttleStart = Event('throttleStart')
        self.throttleEnd = Event('throttleEnd')
        self._logger = logging.getLogger('ib_insync.client')

        self.conn = Connection()
        self.conn.hasData += self._onSocketHasData
        self.conn.disconnected += self._onSocketDisconnected

        # extra optional wrapper methods
        self._priceSizeTick = getattr(wrapper, 'priceSizeTick', None)
        self._tcpDataArrived = getattr(wrapper, 'tcpDataArrived', None)
        self._tcpDataProcessed = getattr(wrapper, 'tcpDataProcessed', None)

        self.host = ''
        self.port = -1
        self.clientId = -1
        self.optCapab = ''
        self.connectOptions = b''
        self._throttleHandle: asyncio.TimerHandle | None = None
        self.reset()

    def reset(self) -> None:
        self.connState = Client.DISCONNECTED
        self._apiReady = False
        self._serverVersion = 0
        self._data = bytearray()
        self._hasReqId = False
        self._reqIdSeq = 0
        self._accounts: list[str] = []
        self._startTime = time.time()
        self._numBytesRecv = 0
        self._numMsgRecv = 0
        self._isThrottling = False
        if self._throttleHandle:
            self._throttleHandle.cancel()
            self._throttleHandle = None
        self._msgQ: deque[str] = deque()
        self._timeQ: deque[float] = deque()

    def serverVersion(self) -> int:
        return self._serverVersion

    def run(self) -> None:
        loop = getLoop()
        loop.run_forever()

    def isConnected(self) -> bool:
        return self.connState == Client.CONNECTED

    def isReady(self) -> bool:
        """Is the API connection up and running?"""
        return self._apiReady

    def connectionStats(self) -> ConnectionStats:
        """Get statistics about the connection."""
        if not self.isReady():
            raise ConnectionError('Not connected')
        return ConnectionStats(
            self._startTime,
            time.time() - self._startTime,
            self._numBytesRecv, self.conn.numBytesSent,
            self._numMsgRecv, self.conn.numMsgSent)

    def getReqId(self) -> int:
        """Get new request ID."""
        if not self.isReady():
            raise ConnectionError('Not connected')
        newId = self._reqIdSeq
        self._reqIdSeq += 1
        return newId

    def updateReqId(self, minReqId: int) -> None:
        """Update the next reqId to be at least ``minReqId``."""
        self._reqIdSeq = max(self._reqIdSeq, minReqId)

    def getAccounts(self) -> list[str]:
        """Get the list of account names that are under management."""
        if not self.isReady():
            raise ConnectionError('Not connected')
        return self._accounts

    def setConnectOptions(self, connectOptions: str) -> None:
        """
        Set additional connect options.

        Args:
            connectOptions: Use "+PACEAPI" to use request-pacing built
                into TWS/gateway 974+ (obsolete).
        """
        self.connectOptions = connectOptions.encode()

    def connect(
            self, host: str, port: int, clientId: int,
            timeout: float | None = 2.0) -> None:
        """
        Connect to a running TWS or IB gateway application.

        Args:
            host: Host name or IP address.
            port: Port number.
            clientId: ID number to use for this client; must be unique per
                connection.
            timeout: If establishing the connection takes longer than
                ``timeout`` seconds then the ``asyncio.TimeoutError`` exception
                is raised. Set to 0 to disable timeout.
        """
        run(self.connectAsync(host, port, clientId, timeout))

    async def connectAsync(
            self, host: str, port: int, clientId: int,
            timeout: float | None = 2.0) -> None:
        if not isinstance(host, str) or not host.strip() or any(
                ch.isspace() or ord(ch) < 32 for ch in host):
            raise ValueError(f'Invalid host: {host!r}')
        if isinstance(port, bool):
            raise ValueError(f'Invalid port: {port!r}')
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError(f'Port must be between 1 and 65535, got {port}')
        if isinstance(clientId, bool):
            raise ValueError(f'Invalid clientId: {clientId!r}')
        clientId = int(clientId)
        if clientId < 0:
            raise ValueError(f'clientId must be non-negative, got {clientId}')
        if timeout is not None:
            if isinstance(timeout, bool) or timeout < 0:
                raise ValueError(
                    f'timeout must be None or non-negative, got {timeout!r}')
        try:
            self._logger.info(
                f'Connecting to {host}:{port} with clientId {clientId}...')
            self.host = host
            self.port = port
            self.clientId = clientId
            self.connState = Client.CONNECTING
            timeout = timeout or None
            await asyncio.wait_for(self.conn.connectAsync(host, port), timeout)
            self._logger.info('Connected')
            msg = b'API\0' + self._prefix(b'v%d..%d%s' % (
                self.MinClientVersion, self.MaxClientVersion,
                b' ' + self.connectOptions if self.connectOptions else b''))
            self.conn.sendMsg(msg)
            await asyncio.wait_for(self.apiStart, timeout)
            self._logger.info('API connection ready')
        except BaseException as e:
            self.disconnect()
            msg_str = f'API connection failed: {e!r}'
            self._logger.error(msg_str)
            self.apiError.emit(msg_str)
            if isinstance(e, ConnectionRefusedError):
                self._logger.error('Make sure API port on TWS/IBG is open')
            raise

    def disconnect(self) -> None:
        """Disconnect from IB connection."""
        self._logger.info('Disconnecting')
        self.connState = Client.DISCONNECTED
        self.conn.disconnect()
        self.reset()

    def send(self, *fields: Any, makeEmpty: bool = True) -> None:
        """Serialize and send the given fields using the IB socket protocol."""
        if not self.isConnected():
            raise ConnectionError('Not connected')

        parts: list[str] = []
        append = parts.append
        for field in fields:
            typ = type(field)
            if typ is str:
                append(field)
            elif field is None:
                append('')
            elif typ is int:
                append(
                    '' if makeEmpty and field == UNSET_INTEGER
                    else str(field))
            elif typ is float:
                if makeEmpty and (
                        field == UNSET_DOUBLE or field == UNSET_INTEGER):
                    append('')
                elif field == math.inf:
                    append('Infinite')
                else:
                    append(str(field))
            elif typ is bool:
                append('1' if field else '0')
            elif typ is list:
                # list of TagValue
                append(''.join(f'{v.tag}={v.value};' for v in field))
            elif isinstance(field, Contract):
                c = field
                append(
                    f'{c.conId}\0{c.symbol}\0{c.secType}\0'
                    f'{c.lastTradeDateOrContractMonth}\0{c.strike}\0'
                    f'{c.right}\0{c.multiplier}\0{c.exchange}\0'
                    f'{c.primaryExchange}\0{c.currency}\0'
                    f'{c.localSymbol}\0{c.tradingClass}')
            else:
                append(str(field))
        append('')
        self.sendMsg('\0'.join(parts))

    def sendMsg(self, msg: str | None) -> None:
        loop = getLoop()
        t = loop.time()
        times = self._timeQ
        msgs = self._msgQ
        while times and t - times[0] >= self.RequestsInterval:
            times.popleft()
        if msg:
            msgs.append(msg)
        while msgs and (len(times) < self.MaxRequests or not self.MaxRequests):
            msg = msgs.popleft()
            self.conn.sendMsg(self._prefix(msg.encode()))
            times.append(t)
            if self._logger.isEnabledFor(logging.DEBUG):
                self._logger.debug('>>> %s', msg[:-1].replace('\0', ','))
        if msgs:
            if not self._isThrottling:
                self._isThrottling = True
                self.throttleStart.emit()
                self._logger.debug('Started to throttle requests')
            if not self._throttleHandle or self._throttleHandle.cancelled():
                self._throttleHandle = loop.call_at(
                    times[0] + self.RequestsInterval,
                    self._onThrottleTimer)
        else:
            if self._throttleHandle:
                self._throttleHandle.cancel()
                self._throttleHandle = None
            if self._isThrottling:
                self._isThrottling = False
                self.throttleEnd.emit()
                self._logger.debug('Stopped to throttle requests')

    def _onThrottleTimer(self) -> None:
        self._throttleHandle = None
        self.sendMsg(None)

    def _prefix(self, msg: bytes) -> bytes:
        # prefix a message with its length
        return _pack_header(len(msg)) + msg

    def _onSocketHasData(self, data: bytes) -> None:
        debug = self._logger.isEnabledFor(logging.DEBUG)
        if self._tcpDataArrived:
            self._tcpDataArrived()

        buf = self._data
        self._numBytesRecv += len(data)
        if buf:
            buf.extend(data)
            view: bytes | bytearray = buf
            using_buf = True
        else:
            view = data
            using_buf = False
        buf_len = len(view)
        pos = 0
        unpack_from = _unpack_header_from
        max_msg_len = self.MaxMsgLen
        interpret = self.decoder.interpret

        while buf_len - pos > 4:
            # 4 byte prefix tells the message length
            msgLen: int = unpack_from(view, pos)[0]
            if msgLen > max_msg_len:
                buf.clear()
                self.conn.disconnect()
                self._onSocketDisconnected(
                    f'Incoming message length {msgLen} exceeds maximum '
                    f'{max_msg_len} bytes')
                return
            msgEnd = pos + 4 + msgLen
            if buf_len < msgEnd:
                # insufficient data for now
                break
            msg = view[pos + 4:msgEnd].decode(errors='backslashreplace')
            pos = msgEnd
            fields = msg.split('\0')
            fields.pop()  # pop off last empty element
            if not fields:
                continue
            self._numMsgRecv += 1

            if debug:
                self._logger.debug('<<< %s', ','.join(fields))

            if self._apiReady:
                interpret(fields)
            elif not self._serverVersion and len(fields) == 2:
                # this concludes the handshake
                version, _connTime = fields
                try:
                    self._serverVersion = int(version)
                except ValueError:
                    self.conn.disconnect()
                    self._onSocketDisconnected(
                        f'Invalid TWS/gateway handshake response: {version!r}')
                    return
                if self._serverVersion < self.MinClientVersion:
                    self.conn.disconnect()
                    self._onSocketDisconnected(
                        f'TWS/gateway server version {self._serverVersion} '
                        f'must be >= {self.MinClientVersion}')
                    return
                self.decoder.serverVersion = self._serverVersion
                self.connState = Client.CONNECTED
                self.startApi()
                self.wrapper.connectAck()
                self._logger.info(
                    f'Logged on to server version {self._serverVersion}')
            else:
                # snoop for nextValidId and managedAccounts response,
                # when both are in then the client is ready
                try:
                    msgId = int(fields[0])
                    if msgId == 9:
                        _, _, validId = fields
                        self.updateReqId(int(validId))
                        self._hasReqId = True
                    elif msgId == 15:
                        _, _, accts = fields
                        self._accounts = [
                            a for a in accts.split(',') if a]
                except (ValueError, IndexError):
                    self._logger.warning(
                        'Malformed pre-ready message: %s', fields)
                if self._hasReqId and self._accounts:
                    self._apiReady = True
                    self.apiStart.emit()

                # decode and handle the message
                interpret(fields)

            if self._data is not buf:
                break

        if self._data is buf:
            if using_buf:
                if pos:
                    if pos == len(buf):
                        buf.clear()
                    else:
                        del buf[:pos]
            elif pos < buf_len:
                buf.extend(data[pos:])

        if self._tcpDataProcessed:
            self._tcpDataProcessed()

    def _onSocketDisconnected(self, msg: str) -> None:
        wasReady = self.isReady()
        if not self.isConnected():
            self._logger.info('Disconnected.')
        elif not msg:
            msg = 'Peer closed connection.'
            if not wasReady:
                msg += f' clientId {self.clientId} already in use?'
        if msg:
            self._logger.error(msg)
            self.apiError.emit(msg)
        self.wrapper.setEventsDone()
        if wasReady:
            self.wrapper.connectionClosed()
        self.reset()
        if wasReady:
            self.apiEnd.emit()

    # client request methods
    # the message type id is sent first, often followed by a version number

    def reqMktData(
            self, reqId: int, contract: Contract, genericTickList: str,
            snapshot: bool, regulatorySnapshot: bool,
            mktDataOptions: list[TagValue] | None) -> None:
        fields: list[Any] = [1, 11, reqId, contract]

        if contract.secType == 'BAG':
            legs = contract.comboLegs or []
            fields += [len(legs)]
            for leg in legs:
                fields += [leg.conId, leg.ratio, leg.action, leg.exchange]

        dnc = contract.deltaNeutralContract
        if dnc:
            fields += [True, dnc.conId, dnc.delta, dnc.price]
        else:
            fields += [False]

        fields += [
            genericTickList, snapshot, regulatorySnapshot, mktDataOptions]
        self.send(*fields)

    def cancelMktData(self, reqId: int) -> None:
        self.send(2, 2, reqId)

    def placeOrder(
            self, orderId: int, contract: Contract, order: Order) -> None:
        version = self.serverVersion()
        fields = [
            3, orderId,
            contract,
            contract.secIdType,
            contract.secId,
            order.action,
            order.totalQuantity,
            order.orderType,
            order.lmtPrice,
            order.auxPrice,
            order.tif,
            order.ocaGroup,
            order.account,
            order.openClose,
            order.origin,
            order.orderRef,
            order.transmit,
            order.parentId,
            order.blockOrder,
            order.sweepToFill,
            order.displaySize,
            order.triggerMethod,
            order.outsideRth,
            order.hidden]

        if contract.secType == 'BAG':
            legs = contract.comboLegs or []
            fields += [len(legs)]
            for leg in legs:
                fields += [
                    leg.conId,
                    leg.ratio,
                    leg.action,
                    leg.exchange,
                    leg.openClose,
                    leg.shortSaleSlot,
                    leg.designatedLocation,
                    leg.exemptCode]

            orderLegs = order.orderComboLegs or []
            fields += [len(orderLegs)]
            for orderLeg in orderLegs:
                fields += [orderLeg.price]

            params = order.smartComboRoutingParams or []
            fields += [len(params)]
            for param in params:
                fields += [param.tag, param.value]

        fields += [
            '',
            order.discretionaryAmt,
            order.goodAfterTime,
            order.goodTillDate,
            order.faGroup,
            order.faMethod,
            order.faPercentage]
        if version < 177:
            fields += [order.faProfile]
        fields += [
            order.modelCode,
            order.shortSaleSlot,
            order.designatedLocation,
            order.exemptCode,
            order.ocaType,
            order.rule80A,
            order.settlingFirm,
            order.allOrNone,
            order.minQty,
            order.percentOffset,
            order.eTradeOnly,
            order.firmQuoteOnly,
            order.nbboPriceCap,
            order.auctionStrategy,
            order.startingPrice,
            order.stockRefPrice,
            order.delta,
            order.stockRangeLower,
            order.stockRangeUpper,
            order.overridePercentageConstraints,
            order.volatility,
            order.volatilityType,
            order.deltaNeutralOrderType,
            order.deltaNeutralAuxPrice]

        if order.deltaNeutralOrderType:
            fields += [
                order.deltaNeutralConId,
                order.deltaNeutralSettlingFirm,
                order.deltaNeutralClearingAccount,
                order.deltaNeutralClearingIntent,
                order.deltaNeutralOpenClose,
                order.deltaNeutralShortSale,
                order.deltaNeutralShortSaleSlot,
                order.deltaNeutralDesignatedLocation]

        fields += [
            order.continuousUpdate,
            order.referencePriceType,
            order.trailStopPrice,
            order.trailingPercent,
            order.scaleInitLevelSize,
            order.scaleSubsLevelSize,
            order.scalePriceIncrement]

        if (0 < order.scalePriceIncrement < UNSET_DOUBLE):
            fields += [
                order.scalePriceAdjustValue,
                order.scalePriceAdjustInterval,
                order.scaleProfitOffset,
                order.scaleAutoReset,
                order.scaleInitPosition,
                order.scaleInitFillQty,
                order.scaleRandomPercent]

        fields += [
            order.scaleTable,
            order.activeStartTime,
            order.activeStopTime,
            order.hedgeType]

        if order.hedgeType:
            fields += [order.hedgeParam]

        fields += [
            order.optOutSmartRouting,
            order.clearingAccount,
            order.clearingIntent,
            order.notHeld]

        dnc = contract.deltaNeutralContract
        if dnc:
            fields += [True, dnc.conId, dnc.delta, dnc.price]
        else:
            fields += [False]

        fields += [order.algoStrategy]
        if order.algoStrategy:
            params = order.algoParams or []
            fields += [len(params)]
            for param in params:
                fields += [param.tag, param.value]

        fields += [
            order.algoId,
            order.whatIf,
            order.orderMiscOptions,
            order.solicited,
            order.randomizeSize,
            order.randomizePrice]

        if order.orderType in ('PEG BENCH', 'PEGBENCH'):
            fields += [
                order.referenceContractId,
                order.isPeggedChangeAmountDecrease,
                order.peggedChangeAmount,
                order.referenceChangeAmount,
                order.referenceExchangeId]

        fields += [len(order.conditions)]
        if order.conditions:
            for cond in order.conditions:
                fields += dataclassAsTuple(cond)
            fields += [
                order.conditionsIgnoreRth,
                order.conditionsCancelOrder]

        fields += [
            order.adjustedOrderType,
            order.triggerPrice,
            order.lmtPriceOffset,
            order.adjustedStopPrice,
            order.adjustedStopLimitPrice,
            order.adjustedTrailingAmount,
            order.adjustableTrailingUnit,
            order.extOperator,
            order.softDollarTier.name,
            order.softDollarTier.val,
            order.cashQty,
            order.mifid2DecisionMaker,
            order.mifid2DecisionAlgo,
            order.mifid2ExecutionTrader,
            order.mifid2ExecutionAlgo,
            order.dontUseAutoPriceForHedge,
            order.isOmsContainer,
            order.discretionaryUpToLimitPrice,
            order.usePriceMgmtAlgo]

        if version >= 158:
            fields += [order.duration]
        if version >= 160:
            fields += [order.postToAts]
        if version >= 162:
            fields += [order.autoCancelParent]
        if version >= 166:
            fields += [order.advancedErrorOverride]
        if version >= 169:
            fields += [order.manualOrderTime]
        if version >= 170:
            if contract.exchange == 'IBKRATS':
                fields += [order.minTradeQty]
            if order.orderType in ('PEG BEST', 'PEGBEST'):
                fields += [
                    order.minCompeteSize,
                    order.competeAgainstBestOffset]
                if order.competeAgainstBestOffset == math.inf:
                    fields += [order.midOffsetAtWhole, order.midOffsetAtHalf]
            elif order.orderType in ('PEG MID', 'PEGMID'):
                fields += [order.midOffsetAtWhole, order.midOffsetAtHalf]

        self.send(*fields)

    def cancelOrder(
            self, orderId: int, manualCancelOrderTime: str = '') -> None:
        fields: list[Any] = [4, 1, orderId]
        if self.serverVersion() >= 169:
            fields += [manualCancelOrderTime]
        self.send(*fields)

    def reqOpenOrders(self) -> None:
        self.send(5, 1)

    def reqAccountUpdates(self, subscribe: bool, acctCode: str) -> None:
        self.send(6, 2, subscribe, acctCode)

    def reqExecutions(self, reqId: int, execFilter: ExecutionFilter) -> None:
        self.send(
            7, 3, reqId,
            execFilter.clientId,
            execFilter.acctCode,
            execFilter.time,
            execFilter.symbol,
            execFilter.secType,
            execFilter.exchange,
            execFilter.side)

    def reqIds(self, numIds: int) -> None:
        self.send(8, 1, numIds)

    def reqContractDetails(self, reqId: int, contract: Contract) -> None:
        fields: list[Any] = [
            9, 8, reqId,
            contract,
            contract.includeExpired,
            contract.secIdType,
            contract.secId]
        if self.serverVersion() >= 176:
            fields += [contract.issuerId]
        self.send(*fields)

    def reqMktDepth(
            self, reqId: int, contract: Contract, numRows: int,
            isSmartDepth: bool,
            mktDepthOptions: list[TagValue] | None) -> None:
        self.send(
            10, 5, reqId,
            contract.conId,
            contract.symbol,
            contract.secType,
            contract.lastTradeDateOrContractMonth,
            contract.strike,
            contract.right,
            contract.multiplier,
            contract.exchange,
            contract.primaryExchange,
            contract.currency,
            contract.localSymbol,
            contract.tradingClass,
            numRows,
            isSmartDepth,
            mktDepthOptions)

    def cancelMktDepth(self, reqId: int, isSmartDepth: bool) -> None:
        self.send(11, 1, reqId, isSmartDepth)

    def reqNewsBulletins(self, allMsgs: bool) -> None:
        self.send(12, 1, allMsgs)

    def cancelNewsBulletins(self) -> None:
        self.send(13, 1)

    def setServerLogLevel(self, logLevel: int) -> None:
        self.send(14, 1, logLevel)

    def reqAutoOpenOrders(self, bAutoBind: bool) -> None:
        self.send(15, 1, bAutoBind)

    def reqAllOpenOrders(self) -> None:
        self.send(16, 1)

    def reqManagedAccts(self) -> None:
        self.send(17, 1)

    def requestFA(self, faData: int) -> None:
        self.send(18, 1, faData)

    def replaceFA(self, reqId: int, faData: int, cxml: str) -> None:
        self.send(19, 1, faData, cxml, reqId)

    def reqHistoricalData(
            self, reqId: int, contract: Contract, endDateTime: str,
            durationStr: str, barSizeSetting: str, whatToShow: str,
            useRTH: bool, formatDate: int, keepUpToDate: bool,
            chartOptions: list[TagValue] | None) -> None:
        fields: list[Any] = [
            20, reqId, contract, contract.includeExpired,
            endDateTime, barSizeSetting, durationStr, useRTH,
            whatToShow, formatDate]

        if contract.secType == 'BAG':
            legs = contract.comboLegs or []
            fields += [len(legs)]
            for leg in legs:
                fields += [leg.conId, leg.ratio, leg.action, leg.exchange]

        fields += [keepUpToDate, chartOptions]
        self.send(*fields)

    def exerciseOptions(
            self, reqId: int, contract: Contract, exerciseAction: int,
            exerciseQuantity: int, account: str, override: int) -> None:
        self.send(
            21, 2, reqId,
            contract.conId,
            contract.symbol,
            contract.secType,
            contract.lastTradeDateOrContractMonth,
            contract.strike,
            contract.right,
            contract.multiplier,
            contract.exchange,
            contract.currency,
            contract.localSymbol,
            contract.tradingClass,
            exerciseAction, exerciseQuantity, account, override)

    def reqScannerSubscription(
            self, reqId: int, subscription: ScannerSubscription,
            scannerSubscriptionOptions: list[TagValue] | None,
            scannerSubscriptionFilterOptions: list[TagValue] | None) -> None:
        sub = subscription
        self.send(
            22, reqId,
            sub.numberOfRows,
            sub.instrument,
            sub.locationCode,
            sub.scanCode,
            sub.abovePrice,
            sub.belowPrice,
            sub.aboveVolume,
            sub.marketCapAbove,
            sub.marketCapBelow,
            sub.moodyRatingAbove,
            sub.moodyRatingBelow,
            sub.spRatingAbove,
            sub.spRatingBelow,
            sub.maturityDateAbove,
            sub.maturityDateBelow,
            sub.couponRateAbove,
            sub.couponRateBelow,
            sub.excludeConvertible,
            sub.averageOptionVolumeAbove,
            sub.scannerSettingPairs,
            sub.stockTypeFilter,
            scannerSubscriptionFilterOptions,
            scannerSubscriptionOptions)

    def cancelScannerSubscription(self, reqId: int) -> None:
        self.send(23, 1, reqId)

    def reqScannerParameters(self) -> None:
        self.send(24, 1)

    def cancelHistoricalData(self, reqId: int) -> None:
        self.send(25, 1, reqId)

    def reqCurrentTime(self) -> None:
        self.send(49, 1)

    def reqRealTimeBars(
            self, reqId: int, contract: Contract, barSize: int,
            whatToShow: str, useRTH: bool,
            realTimeBarsOptions: list[TagValue] | None) -> None:
        self.send(
            50, 3, reqId, contract, barSize, whatToShow,
            useRTH, realTimeBarsOptions)

    def cancelRealTimeBars(self, reqId: int) -> None:
        self.send(51, 1, reqId)

    def reqFundamentalData(
            self, reqId: int, contract: Contract, reportType: str,
            fundamentalDataOptions: list[TagValue] | None) -> None:
        options = fundamentalDataOptions or []
        self.send(
            52, 2, reqId,
            contract.conId,
            contract.symbol,
            contract.secType,
            contract.exchange,
            contract.primaryExchange,
            contract.currency,
            contract.localSymbol,
            reportType, len(options), options)

    def cancelFundamentalData(self, reqId: int) -> None:
        self.send(53, 1, reqId)

    def calculateImpliedVolatility(
            self, reqId: int, contract: Contract, optionPrice: float,
            underPrice: float,
            implVolOptions: list[TagValue] | None) -> None:
        options = implVolOptions or []
        self.send(
            54, 3, reqId, contract, optionPrice, underPrice,
            len(options), options)

    def calculateOptionPrice(
            self, reqId: int, contract: Contract, volatility: float,
            underPrice: float,
            optPrcOptions: list[TagValue] | None) -> None:
        options = optPrcOptions or []
        self.send(
            55, 3, reqId, contract, volatility, underPrice,
            len(options), options)

    def cancelCalculateImpliedVolatility(self, reqId: int) -> None:
        self.send(56, 1, reqId)

    def cancelCalculateOptionPrice(self, reqId: int) -> None:
        self.send(57, 1, reqId)

    def reqGlobalCancel(self) -> None:
        self.send(58, 1)

    def reqMarketDataType(self, marketDataType: int) -> None:
        self.send(59, 1, marketDataType)

    def reqPositions(self) -> None:
        self.send(61, 1)

    def reqAccountSummary(
            self, reqId: int, groupName: str, tags: str) -> None:
        self.send(62, 1, reqId, groupName, tags)

    def cancelAccountSummary(self, reqId: int) -> None:
        self.send(63, 1, reqId)

    def cancelPositions(self) -> None:
        self.send(64, 1)

    def verifyRequest(self, apiName: str, apiVersion: str) -> None:
        self.send(65, 1, apiName, apiVersion)

    def verifyMessage(self, apiData: str) -> None:
        self.send(66, 1, apiData)

    def queryDisplayGroups(self, reqId: int) -> None:
        self.send(67, 1, reqId)

    def subscribeToGroupEvents(self, reqId: int, groupId: int) -> None:
        self.send(68, 1, reqId, groupId)

    def updateDisplayGroup(self, reqId: int, contractInfo: str) -> None:
        self.send(69, 1, reqId, contractInfo)

    def unsubscribeFromGroupEvents(self, reqId: int) -> None:
        self.send(70, 1, reqId)

    def startApi(self) -> None:
        self.send(71, 2, self.clientId, self.optCapab)

    def verifyAndAuthRequest(
            self, apiName: str, apiVersion: str, opaqueIsvKey: str) -> None:
        self.send(72, 1, apiName, apiVersion, opaqueIsvKey)

    def verifyAndAuthMessage(self, apiData: str, xyzResponse: str) -> None:
        self.send(73, 1, apiData, xyzResponse)

    def reqPositionsMulti(
            self, reqId: int, account: str, modelCode: str) -> None:
        self.send(74, 1, reqId, account, modelCode)

    def cancelPositionsMulti(self, reqId: int) -> None:
        self.send(75, 1, reqId)

    def reqAccountUpdatesMulti(
            self, reqId: int, account: str, modelCode: str,
            ledgerAndNLV: bool) -> None:
        self.send(76, 1, reqId, account, modelCode, ledgerAndNLV)

    def cancelAccountUpdatesMulti(self, reqId: int) -> None:
        self.send(77, 1, reqId)

    def reqSecDefOptParams(
            self, reqId: int, underlyingSymbol: str, futFopExchange: str,
            underlyingSecType: str, underlyingConId: int) -> None:
        self.send(
            78, reqId, underlyingSymbol, futFopExchange,
            underlyingSecType, underlyingConId)

    def reqSoftDollarTiers(self, reqId: int) -> None:
        self.send(79, reqId)

    def reqFamilyCodes(self) -> None:
        self.send(80)

    def reqMatchingSymbols(self, reqId: int, pattern: str) -> None:
        self.send(81, reqId, pattern)

    def reqMktDepthExchanges(self) -> None:
        self.send(82)

    def reqSmartComponents(self, reqId: int, bboExchange: str) -> None:
        self.send(83, reqId, bboExchange)

    def reqNewsArticle(
            self, reqId: int, providerCode: str, articleId: str,
            newsArticleOptions: list[TagValue] | None) -> None:
        self.send(84, reqId, providerCode, articleId, newsArticleOptions)

    def reqNewsProviders(self) -> None:
        self.send(85)

    def reqHistoricalNews(
            self, reqId: int, conId: int, providerCodes: str,
            startDateTime: str, endDateTime: str, totalResults: int,
            historicalNewsOptions: list[TagValue] | None) -> None:
        self.send(
            86, reqId, conId, providerCodes, startDateTime, endDateTime,
            totalResults, historicalNewsOptions)

    def reqHeadTimeStamp(
            self, reqId: int, contract: Contract, whatToShow: str,
            useRTH: bool, formatDate: int) -> None:
        self.send(
            87, reqId, contract, contract.includeExpired,
            useRTH, whatToShow, formatDate)

    def reqHistogramData(
            self, tickerId: int, contract: Contract, useRTH: bool,
            timePeriod: str) -> None:
        self.send(
            88, tickerId, contract, contract.includeExpired,
            useRTH, timePeriod)

    def cancelHistogramData(self, tickerId: int) -> None:
        self.send(89, tickerId)

    def cancelHeadTimeStamp(self, reqId: int) -> None:
        self.send(90, reqId)

    def reqMarketRule(self, marketRuleId: int) -> None:
        self.send(91, marketRuleId)

    def reqPnL(self, reqId: int, account: str, modelCode: str) -> None:
        self.send(92, reqId, account, modelCode)

    def cancelPnL(self, reqId: int) -> None:
        self.send(93, reqId)

    def reqPnLSingle(
            self, reqId: int, account: str, modelCode: str,
            conid: int) -> None:
        self.send(94, reqId, account, modelCode, conid)

    def cancelPnLSingle(self, reqId: int) -> None:
        self.send(95, reqId)

    def reqHistoricalTicks(
            self, reqId: int, contract: Contract, startDateTime: str,
            endDateTime: str, numberOfTicks: int, whatToShow: str,
            useRth: bool, ignoreSize: bool,
            miscOptions: list[TagValue] | None) -> None:
        self.send(
            96, reqId, contract, contract.includeExpired,
            startDateTime, endDateTime, numberOfTicks, whatToShow,
            useRth, ignoreSize, miscOptions)

    def reqTickByTickData(
            self, reqId: int, contract: Contract, tickType: str,
            numberOfTicks: int, ignoreSize: bool) -> None:
        self.send(97, reqId, contract, tickType, numberOfTicks, ignoreSize)

    def cancelTickByTickData(self, reqId: int) -> None:
        self.send(98, reqId)

    def reqCompletedOrders(self, apiOnly: bool) -> None:
        self.send(99, apiOnly)

    def reqWshMetaData(self, reqId: int) -> None:
        self.send(100, reqId)

    def cancelWshMetaData(self, reqId: int) -> None:
        self.send(101, reqId)

    def reqWshEventData(self, reqId: int, data: WshEventData) -> None:
        fields: list[Any] = [102, reqId, data.conId]
        if self.serverVersion() >= 171:
            fields += [
                data.filter,
                data.fillWatchlist,
                data.fillPortfolio,
                data.fillCompetitors]
        if self.serverVersion() >= 173:
            fields += [
                data.startDate,
                data.endDate,
                data.totalLimit]
        self.send(*fields, makeEmpty=False)

    def cancelWshEventData(self, reqId: int) -> None:
        self.send(103, reqId)

    def reqUserInfo(self, reqId: int) -> None:
        self.send(104, reqId)
