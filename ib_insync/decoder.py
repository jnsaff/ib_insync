"""Deserialize and dispatch messages."""

import dataclasses
import logging
import math
import re
from collections.abc import Callable
from datetime import datetime, timezone
from itertools import islice
from typing import Any, cast

from .contract import (
    ComboLeg, Contract, ContractDescription, ContractDetails,
    DeltaNeutralContract)
from .objects import (
    BarData, CommissionReport, DepthMktDataDescription, Execution, FamilyCode,
    HistogramData, HistoricalSession, HistoricalTick, HistoricalTickBidAsk,
    HistoricalTickLast, NewsProvider, PriceIncrement, SmartComponent,
    SoftDollarTier, TagValue, TickAttrib, TickAttribBidAsk, TickAttribLast)
from .order import Order, OrderComboLeg, OrderCondition, OrderState
from .util import UNSET_DOUBLE, getZoneInfo, parseIBDatetime
from .wrapper import Wrapper

_PRICE_SIZE_TICK_MAP: dict[int, int] = {
    1: 0, 2: 3, 4: 5, 66: 69, 67: 70, 68: 71}

_UNICODE_ESCAPE_RE = re.compile(r'\\u([0-9a-fA-F]{4})')

_TYPE_INT = 0
_TYPE_FLOAT = 1
_TYPE_BOOL = 2
_TYPE_NAMES = ('int', 'float', 'bool')

_PARSE_FIELDS_CACHE: dict[type[Any], tuple[tuple[str, int, Any], ...]] = {}
_CONDITION_ARITY_CACHE: dict[type[Any], int] = {}


def _parse_int(v: Any) -> int:
    return int(v) if v else 0


def _parse_float(v: Any) -> float:
    return float(v) if v else 0.0


def _parse_bool(v: Any) -> bool:
    return bool(int(v)) if v else False


def _parse_str(v: Any) -> Any:
    return v


_TYPE_CONVERTERS: dict[type[Any], Callable[[Any], Any]] = {
    str: _parse_str,
    int: _parse_int,
    float: _parse_float,
    bool: _parse_bool,
}


def _get_parse_fields(cls: type[Any]) -> tuple[tuple[str, int, Any], ...]:
    spec = _PARSE_FIELDS_CACHE.get(cls)
    if spec is None:
        items: list[tuple[str, int, Any]] = []
        for f in dataclasses.fields(cls):
            typ = type(f.default)
            if typ is int:
                items.append((f.name, _TYPE_INT, f.default))
            elif typ is float:
                items.append((f.name, _TYPE_FLOAT, f.default))
            elif typ is bool:
                items.append((f.name, _TYPE_BOOL, f.default))
        spec = tuple(items)
        _PARSE_FIELDS_CACHE[cls] = spec
    return spec


def _get_condition_arity(cls: type[Any]) -> int:
    n = _CONDITION_ARITY_CACHE.get(cls)
    if n is None:
        n = len(dataclasses.fields(cls)) - 1
        _CONDITION_ARITY_CACHE[cls] = n
    return n


class Decoder:
    """Decode IB messages and invoke corresponding wrapper methods."""

    def __init__(self, wrapper: Wrapper, serverVersion: int) -> None:
        self.wrapper = wrapper
        self.serverVersion = serverVersion
        self.logger = logging.getLogger('ib_insync.Decoder')
        self.handlers: dict[int, Callable[[list[Any]], None]] = {
            1: self.priceSizeTick,
            2: self.wrap(
                'tickSize', [int, int, float]),
            3: self.wrap(
                'orderStatus', [
                    int, str, float, float, float, int, int,
                    float, int, str, float], skip=1),
            4: self.errorMsg,
            5: self.openOrder,
            6: self.wrap(
                'updateAccountValue', [str, str, str, str]),
            7: self.updatePortfolio,
            8: self.wrap(
                'updateAccountTime', [str]),
            9: self.wrap(
                'nextValidId', [int]),
            10: self.contractDetails,
            11: self.execDetails,
            12: self.wrap(
                'updateMktDepth', [int, int, int, int, float, float]),
            13: self.wrap(
                'updateMktDepthL2',
                [int, int, str, int, int, float, float, bool]),
            14: self.wrap(
                'updateNewsBulletin', [int, int, str, str]),
            15: self.wrap(
                'managedAccounts', [str]),
            16: self.wrap(
                'receiveFA', [int, str]),
            17: self.historicalData,
            18: self.bondContractDetails,
            19: self.wrap(
                'scannerParameters', [str]),
            20: self.scannerData,
            21: self.tickOptionComputation,
            45: self.wrap(
                'tickGeneric', [int, int, float]),
            46: self.wrap(
                'tickString', [int, int, str]),
            47: self.wrap(
                'tickEFP',
                [int, int, float, str, float, int, str, float, float]),
            49: self.wrap(
                'currentTime', [int]),
            50: self.wrap(
                'realtimeBar',
                [int, int, float, float, float, float, float, float, int]),
            51: self.wrap(
                'fundamentalData', [int, str]),
            52: self.wrap(
                'contractDetailsEnd', [int]),
            53: self.wrap(
                'openOrderEnd', []),
            54: self.wrap(
                'accountDownloadEnd', [str]),
            55: self.wrap(
                'execDetailsEnd', [int]),
            56: self.deltaNeutralValidation,
            57: self.wrap(
                'tickSnapshotEnd', [int]),
            58: self.wrap(
                'marketDataType', [int, int]),
            59: self.commissionReport,
            61: self.position,
            62: self.wrap(
                'positionEnd', []),
            63: self.wrap(
                'accountSummary', [int, str, str, str, str]),
            64: self.wrap(
                'accountSummaryEnd', [int]),
            65: self.wrap(
                'verifyMessageAPI', [str]),
            66: self.wrap(
                'verifyCompleted', [bool, str]),
            67: self.wrap(
                'displayGroupList', [int, str]),
            68: self.wrap(
                'displayGroupUpdated', [int, str]),
            69: self.wrap(
                'verifyAndAuthMessageAPI', [str, str]),
            70: self.wrap(
                'verifyAndAuthCompleted', [bool, str]),
            71: self.positionMulti,
            72: self.wrap(
                'positionMultiEnd', [int]),
            73: self.wrap(
                'accountUpdateMulti', [int, str, str, str, str, str]),
            74: self.wrap(
                'accountUpdateMultiEnd', [int]),
            75: self.securityDefinitionOptionParameter,
            76: self.wrap(
                'securityDefinitionOptionParameterEnd', [int], skip=1),
            77: self.softDollarTiers,
            78: self.familyCodes,
            79: self.symbolSamples,
            80: self.mktDepthExchanges,
            81: self.wrap(
                'tickReqParams', [int, float, str, int], skip=1),
            82: self.smartComponents,
            83: self.wrap(
                'newsArticle', [int, int, str], skip=1),
            84: self.wrap(
                'tickNews', [int, int, str, str, str, str], skip=1),
            85: self.newsProviders,
            86: self.wrap(
                'historicalNews', [int, str, str, str, str], skip=1),
            87: self.wrap(
                'historicalNewsEnd', [int, bool], skip=1),
            88: self.wrap(
                'headTimestamp', [int, str], skip=1),
            89: self.histogramData,
            90: self.historicalDataUpdate,
            91: self.wrap(
                'rerouteMktDataReq', [int, int, str], skip=1),
            92: self.wrap(
                'rerouteMktDepthReq', [int, int, str], skip=1),
            93: self.marketRule,
            94: self.wrap(
                'pnl', [int, float, float, float], skip=1),
            95: self.wrap(
                'pnlSingle', [int, float, float, float, float, float], skip=1),
            96: self.historicalTicks,
            97: self.historicalTicksBidAsk,
            98: self.historicalTicksLast,
            99: self.tickByTick,
            100: self.wrap(
                'orderBound', [int, int, int], skip=1),
            101: self.completedOrder,
            102: self.wrap(
                'completedOrdersEnd', [], skip=1),
            103: self.wrap(
                'replaceFAEnd', [int, str], skip=1),
            104: self.wrap(
                'wshMetaData', [int, str], skip=1),
            105: self.wrap(
                'wshEventData', [int, str], skip=1),
            106: self.historicalSchedule,
            107: self.wrap(
                'userInfo', [int, str], skip=1)
        }
        self._strHandlers: dict[str, Callable[[list[Any]], None]] = {
            str(k): v for k, v in self.handlers.items()}

    def wrap(
            self, methodName: str, types: list[type[Any]],
            skip: int = 2) -> Callable[[list[Any]], None]:
        """
        Create a message handler that invokes a wrapper method
        with the in-order message fields as parameters, skipping over
        the first ``skip`` fields, and parsed according to the ``types`` list.
        """
        n = len(types)
        expected = skip + n
        sig = tuple(types)

        if n == 0:
            def handler0(fields: list[Any]) -> None:
                method = getattr(self.wrapper, methodName, None)
                if not method:
                    return
                if len(fields) < expected:
                    self.logger.error(
                        'Truncated %s message: expected at least %d fields, '
                        'got %d: %s',
                        methodName, expected, len(fields), fields)
                    return
                try:
                    method()
                except Exception:
                    self.logger.exception(f'Error for {methodName}:')
            return handler0

        if sig == (int, int, float):
            i1 = skip + 1
            i2 = skip + 2

            def handler_iif(fields: list[Any]) -> None:
                method = getattr(self.wrapper, methodName, None)
                if not method:
                    return
                if len(fields) < expected:
                    self.logger.error(
                        'Truncated %s message: expected at least %d fields, '
                        'got %d: %s',
                        methodName, expected, len(fields), fields)
                    return
                try:
                    f0 = fields[skip]
                    f1 = fields[i1]
                    f2 = fields[i2]
                    method(
                        int(f0) if f0 else 0,
                        int(f1) if f1 else 0,
                        float(f2) if f2 else 0.0)
                except Exception:
                    self.logger.exception(f'Error for {methodName}:')
            return handler_iif

        if sig == (int, int, str):
            i1 = skip + 1
            i2 = skip + 2

            def handler_iis(fields: list[Any]) -> None:
                method = getattr(self.wrapper, methodName, None)
                if not method:
                    return
                if len(fields) < expected:
                    self.logger.error(
                        'Truncated %s message: expected at least %d fields, '
                        'got %d: %s',
                        methodName, expected, len(fields), fields)
                    return
                try:
                    f0 = fields[skip]
                    f1 = fields[i1]
                    method(
                        int(f0) if f0 else 0,
                        int(f1) if f1 else 0,
                        fields[i2])
                except Exception:
                    self.logger.exception(f'Error for {methodName}:')
            return handler_iis

        if sig == (int,):
            def handler_i(fields: list[Any]) -> None:
                method = getattr(self.wrapper, methodName, None)
                if not method:
                    return
                if len(fields) < expected:
                    self.logger.error(
                        'Truncated %s message: expected at least %d fields, '
                        'got %d: %s',
                        methodName, expected, len(fields), fields)
                    return
                try:
                    f0 = fields[skip]
                    method(int(f0) if f0 else 0)
                except Exception:
                    self.logger.exception(f'Error for {methodName}:')
            return handler_i

        if sig == (str,):
            def handler_s(fields: list[Any]) -> None:
                method = getattr(self.wrapper, methodName, None)
                if not method:
                    return
                if len(fields) < expected:
                    self.logger.error(
                        'Truncated %s message: expected at least %d fields, '
                        'got %d: %s',
                        methodName, expected, len(fields), fields)
                    return
                try:
                    method(fields[skip])
                except Exception:
                    self.logger.exception(f'Error for {methodName}:')
            return handler_s

        indexed: tuple[tuple[int, Callable[[Any], Any]], ...] = tuple(
            (skip + i, _TYPE_CONVERTERS.get(t, _parse_bool))
            for i, t in enumerate(types))

        def handler(fields: list[Any]) -> None:
            method = getattr(self.wrapper, methodName, None)
            if not method:
                return
            if len(fields) < expected:
                self.logger.error(
                    'Truncated %s message: expected at least %d fields, '
                    'got %d: %s',
                    methodName, expected, len(fields), fields)
                return
            try:
                method(*[conv(fields[i]) for i, conv in indexed])
            except Exception:
                self.logger.exception(f'Error for {methodName}:')

        return handler

    def interpret(self, fields: list[Any]) -> None:
        """Decode fields and invoke corresponding wrapper method."""
        if not fields:
            self.logger.warning('Received empty message fields')
            return
        key = fields[0]
        handler = self._strHandlers.get(key) if type(key) is str else None
        if handler is None:
            try:
                msgId = int(key)
            except (ValueError, TypeError):
                self.logger.error(
                    'Invalid non-integer message id %r in fields: %s',
                    key, fields)
                return
            handler = self.handlers.get(msgId)
            if handler is None:
                self.logger.warning('Unknown message id %d: %s', msgId, fields)
                return
        try:
            handler(fields)
        except Exception:
            self.logger.exception(f'Error handling fields: {fields}')

    def parse(self, obj: Any) -> None:
        """Parse the object's properties according to its default types."""
        cls = type(obj)
        spec = _PARSE_FIELDS_CACHE.get(cls)
        if spec is None:
            spec = _get_parse_fields(cls)
        d = getattr(obj, '__dict__', None)
        if d is not None:
            for name, code, default in spec:
                v = d[name]
                if type(v) is not str:
                    continue
                try:
                    if code == _TYPE_INT:
                        d[name] = int(v) if v else default
                    elif code == _TYPE_FLOAT:
                        if not v:
                            d[name] = default
                        elif v == 'Infinite':
                            d[name] = math.inf
                        else:
                            d[name] = float(v)
                    else:
                        d[name] = bool(int(v)) if v else default
                except ValueError:
                    self.logger.warning(
                        'Invalid %s value %r for %s.%s; using default %r',
                        _TYPE_NAMES[code], v, cls.__name__, name, default)
                    d[name] = default
        else:
            for name, code, default in spec:
                v = getattr(obj, name)
                if type(v) is not str:
                    continue
                try:
                    if code == _TYPE_INT:
                        setattr(obj, name, int(v) if v else default)
                    elif code == _TYPE_FLOAT:
                        if not v:
                            setattr(obj, name, default)
                        elif v == 'Infinite':
                            setattr(obj, name, math.inf)
                        else:
                            setattr(obj, name, float(v))
                    else:
                        setattr(obj, name, bool(int(v)) if v else default)
                except ValueError:
                    self.logger.warning(
                        'Invalid %s value %r for %s.%s; using default %r',
                        _TYPE_NAMES[code], v, cls.__name__, name, default)
                    setattr(obj, name, default)

    def priceSizeTick(self, fields: list[Any]) -> None:
        _, _, reqId, tickType, price, size, attrib = fields

        if price:
            reqIdInt = int(reqId)
            tickTypeInt = int(tickType)
            priceFloat = float(price)
            sizeFloat = float(size) if size else 0.0
            priceSizeTick = getattr(self.wrapper, 'priceSizeTick', None)
            if priceSizeTick is not None:
                priceSizeTick(reqIdInt, tickTypeInt, priceFloat, sizeFloat)
            else:
                tickPrice = getattr(self.wrapper, 'tickPrice', None)
                if tickPrice:
                    mask = int(attrib) if attrib else 0
                    ta = TickAttrib(
                        canAutoExecute=bool(mask & 1),
                        pastLimit=bool(mask & 2),
                        preOpen=bool(mask & 4))
                    tickPrice(reqIdInt, tickTypeInt, priceFloat, ta)
                tickSize = getattr(self.wrapper, 'tickSize', None)
                sizeTickType = _PRICE_SIZE_TICK_MAP.get(tickTypeInt)
                if tickSize and sizeTickType is not None and sizeFloat:
                    tickSize(reqIdInt, sizeTickType, sizeFloat)

    def errorMsg(self, fields: list[Any]) -> None:
        _, _, reqId, errorCode, errorString, *fields = fields
        advancedOrderRejectJson = ''
        if self.serverVersion >= 166:
            advancedOrderRejectJson, *fields = fields
        self.wrapper.error(
            int(reqId), int(errorCode), errorString, advancedOrderRejectJson)

    def updatePortfolio(self, fields: list[Any]) -> None:
        c = Contract()
        (
            _, _,
            c.conId,
            c.symbol,
            c.secType,
            c.lastTradeDateOrContractMonth,
            c.strike,
            c.right,
            c.multiplier,
            c.primaryExchange,
            c.currency,
            c.localSymbol,
            c.tradingClass,
            position,
            marketPrice,
            marketValue,
            averageCost,
            unrealizedPNL,
            realizedPNL,
            accountName) = fields

        self.parse(c)
        self.wrapper.updatePortfolio(
            c, float(position), float(marketPrice),
            float(marketValue), float(averageCost), float(unrealizedPNL),
            float(realizedPNL), accountName)

    def contractDetails(self, fields: list[Any]) -> None:
        cd = ContractDetails()
        cd.contract = c = Contract()
        it = iter(fields)
        get = it.__next__
        if self.serverVersion < 164:
            get()
        get()  # msgId or version
        reqId = get()
        c.symbol = get()
        c.secType = get()
        lastTimes = get()
        c.strike = get()
        c.right = get()
        c.exchange = get()
        c.currency = get()
        c.localSymbol = get()
        cd.marketName = get()
        c.tradingClass = get()
        c.conId = get()
        cd.minTick = get()
        if self.serverVersion < 164:
            get()  # obsolete mdSizeMultiplier
        c.multiplier = get()
        cd.orderTypes = get()
        cd.validExchanges = get()
        cd.priceMagnifier = get()
        cd.underConId = get()
        cd.longName = get()
        c.primaryExchange = get()
        cd.contractMonth = get()
        cd.industry = get()
        cd.category = get()
        cd.subcategory = get()
        cd.timeZoneId = get()
        cd.tradingHours = get()
        cd.liquidHours = get()
        cd.evRule = get()
        cd.evMultiplier = get()
        numSecIds = int(get())
        if numSecIds > 0:
            cd.secIdList = [TagValue(get(), get()) for _ in range(numSecIds)]
        cd.aggGroup = get()
        cd.underSymbol = get()
        cd.underSecType = get()
        cd.marketRuleIds = get()
        cd.realExpirationDate = get()
        cd.stockType = get()
        if self.serverVersion == 163:
            cd.suggestedSizeIncrement = get()
        elif self.serverVersion >= 164:
            cd.minSize = get()
            cd.sizeIncrement = get()
            cd.suggestedSizeIncrement = get()

        times = lastTimes.split('-' if '-' in lastTimes else None)
        if len(times) > 0:
            c.lastTradeDateOrContractMonth = times[0]
        if len(times) > 1:
            cd.lastTradeTime = times[1]
        if len(times) > 2:
            cd.timeZoneId = times[2]

        if '\\u' in cd.longName:
            cd.longName = _UNICODE_ESCAPE_RE.sub(
                lambda m: chr(int(m.group(1), 16)),
                cd.longName)
        self.parse(cd)
        self.parse(c)
        self.wrapper.contractDetails(int(reqId), cd)

    def bondContractDetails(self, fields: list[Any]) -> None:
        cd = ContractDetails()
        cd.contract = c = Contract()
        it = iter(fields)
        get = it.__next__
        if self.serverVersion < 164:
            get()
        get()  # msgId or version
        reqId = get()
        c.symbol = get()
        c.secType = get()
        cd.cusip = get()
        cd.coupon = get()
        lastTimes = get()
        cd.issueDate = get()
        cd.ratings = get()
        cd.bondType = get()
        cd.couponType = get()
        cd.convertible = get()
        cd.callable = get()
        cd.putable = get()
        cd.descAppend = get()
        c.exchange = get()
        c.currency = get()
        cd.marketName = get()
        c.tradingClass = get()
        c.conId = get()
        cd.minTick = get()
        if self.serverVersion < 164:
            get()  # obsolete mdSizeMultiplier
        cd.orderTypes = get()
        cd.validExchanges = get()
        cd.nextOptionDate = get()
        cd.nextOptionType = get()
        cd.nextOptionPartial = get()
        cd.notes = get()
        cd.longName = get()
        cd.evRule = get()
        cd.evMultiplier = get()
        numSecIds = int(get())
        if numSecIds > 0:
            cd.secIdList = [TagValue(get(), get()) for _ in range(numSecIds)]

        cd.aggGroup = get()
        cd.marketRuleIds = get()
        if self.serverVersion >= 164:
            cd.minSize = get()
            cd.sizeIncrement = get()
            cd.suggestedSizeIncrement = get()

        times = lastTimes.split('-' if '-' in lastTimes else None)
        if len(times) > 0:
            cd.maturity = times[0]
        if len(times) > 1:
            cd.lastTradeTime = times[1]
        if len(times) > 2:
            cd.timeZoneId = times[2]

        self.parse(cd)
        self.parse(c)
        self.wrapper.bondContractDetails(int(reqId), cd)

    def execDetails(self, fields: list[Any]) -> None:
        c = Contract()
        ex = Execution()
        get = iter(fields).__next__
        get()  # msgId
        reqId = get()
        ex.orderId = get()
        c.conId = get()
        c.symbol = get()
        c.secType = get()
        c.lastTradeDateOrContractMonth = get()
        c.strike = get()
        c.right = get()
        c.multiplier = get()
        c.exchange = get()
        c.currency = get()
        c.localSymbol = get()
        c.tradingClass = get()
        ex.execId = get()
        timeStr = get()
        ex.acctNumber = get()
        ex.exchange = get()
        ex.side = get()
        ex.shares = get()
        ex.price = get()
        ex.permId = get()
        ex.clientId = get()
        ex.liquidation = get()
        ex.cumQty = get()
        ex.avgPrice = get()
        ex.orderRef = get()
        ex.evRule = get()
        ex.evMultiplier = get()
        ex.modelCode = get()
        ex.lastLiquidity = get()
        if self.serverVersion >= 178:
            ex.pendingPriceRevision = get()

        self.parse(c)
        self.parse(ex)
        time = cast(datetime, parseIBDatetime(timeStr))
        if not time.tzinfo:
            tz = self.wrapper.ib.TimezoneTWS
            if tz:
                time = time.replace(tzinfo=getZoneInfo(str(tz)))
        ex.time = time.astimezone(timezone.utc)
        self.wrapper.execDetails(int(reqId), c, ex)

    def historicalData(self, fields: list[Any]) -> None:
        reqIdInt = int(fields[1])
        startDateStr = fields[2]
        endDateStr = fields[3]
        numBars = int(fields[4])
        histData = self.wrapper.historicalData

        for i in range(5, 5 + numBars * 8, 8):
            bar = BarData(
                fields[i],
                float(fields[i + 1]),
                float(fields[i + 2]),
                float(fields[i + 3]),
                float(fields[i + 4]),
                float(fields[i + 5]),
                float(fields[i + 6]),
                int(fields[i + 7]))
            histData(reqIdInt, bar)

        self.wrapper.historicalDataEnd(reqIdInt, startDateStr, endDateStr)

    def historicalDataUpdate(self, fields: list[Any]) -> None:
        get = iter(fields).__next__
        get()  # msgId
        reqId = int(get())

        bar = BarData(
            barCount=int(get() or 0),
            date=get(),
            open=float(get() or 0),
            close=float(get() or 0),
            high=float(get() or 0),
            low=float(get() or 0),
            average=float(get() or 0),
            volume=float(get() or 0))

        self.wrapper.historicalDataUpdate(reqId, bar)

    def scannerData(self, fields: list[Any]) -> None:
        get = iter(fields).__next__
        get()  # msgId
        get()  # version
        reqIdInt = int(get())
        n = int(get())
        scanData = self.wrapper.scannerData

        for _ in range(n):
            cd = ContractDetails()
            cd.contract = c = Contract()
            rank = get()
            c.conId = get()
            c.symbol = get()
            c.secType = get()
            c.lastTradeDateOrContractMonth = get()
            c.strike = get()
            c.right = get()
            c.exchange = get()
            c.currency = get()
            c.localSymbol = get()
            cd.marketName = get()
            c.tradingClass = get()
            distance = get()
            benchmark = get()
            projection = get()
            legsStr = get()

            self.parse(cd)
            self.parse(c)
            scanData(
                reqIdInt, int(rank), cd,
                distance, benchmark, projection, legsStr)

        self.wrapper.scannerDataEnd(reqIdInt)

    def tickOptionComputation(self, fields: list[Any]) -> None:
        (_, reqId, tickTypeInt, tickAttrib, impliedVol, delta, optPrice,
         pvDividend, gamma, vega, theta, undPrice) = fields

        self.wrapper.tickOptionComputation(
            int(reqId), int(tickTypeInt), int(tickAttrib) if tickAttrib else 0,
            float(impliedVol) if impliedVol else -1.0,
            float(delta) if delta else -2.0,
            float(optPrice) if optPrice else -1.0,
            float(pvDividend) if pvDividend else -1.0,
            float(gamma) if gamma else -2.0,
            float(vega) if vega else -2.0,
            float(theta) if theta else -2.0,
            float(undPrice) if undPrice else -1.0)

    def deltaNeutralValidation(self, fields: list[Any]) -> None:
        _, _, reqId, conId, delta, price = fields

        self.wrapper.deltaNeutralValidation(
            int(reqId), DeltaNeutralContract(
                int(conId), float(delta or 0), float(price or 0)))

    def commissionReport(self, fields: list[Any]) -> None:
        _, _, execId, commission, currency, realizedPNL, \
            yield_, yieldRedemptionDate = fields

        self.wrapper.commissionReport(
            CommissionReport(
                execId, float(commission or 0), currency,
                float(realizedPNL or 0), float(yield_ or 0),
                int(yieldRedemptionDate or 0)))

    def position(self, fields: list[Any]) -> None:
        c = Contract()
        (
            _, _,
            account,
            c.conId,
            c.symbol,
            c.secType,
            c.lastTradeDateOrContractMonth,
            c.strike,
            c.right,
            c.multiplier,
            c.exchange,
            c.currency,
            c.localSymbol,
            c.tradingClass,
            position,
            avgCost) = fields

        self.parse(c)
        self.wrapper.position(
            account, c, float(position or 0), float(avgCost or 0))

    def positionMulti(self, fields: list[Any]) -> None:
        c = Contract()
        (
            _, _,
            reqId,
            account,
            c.conId,
            c.symbol,
            c.secType,
            c.lastTradeDateOrContractMonth,
            c.strike,
            c.right,
            c.multiplier,
            c.exchange,
            c.currency,
            c.localSymbol,
            c.tradingClass,
            position,
            avgCost,
            modelCode) = fields

        self.parse(c)
        self.wrapper.positionMulti(
            int(reqId), account, modelCode, c,
            float(position or 0), float(avgCost or 0))

    def securityDefinitionOptionParameter(self, fields: list[Any]) -> None:
        _, reqId, exchange, underlyingConId, tradingClass, multiplier, \
            n = fields[:7]
        n = int(n)
        end = 7 + n
        expirations = fields[7:end]
        strikes = [float(field) for field in fields[end + 1:]]

        self.wrapper.securityDefinitionOptionParameter(
            int(reqId), exchange, int(underlyingConId or 0), tradingClass,
            multiplier, expirations, strikes)

    def softDollarTiers(self, fields: list[Any]) -> None:
        get = iter(fields).__next__
        get()
        reqId = int(get())
        n = int(get())

        tiers = [
            SoftDollarTier(
                name=get(),
                val=get(),
                displayName=get())
            for _ in range(n)]

        self.wrapper.softDollarTiers(reqId, tiers)

    def familyCodes(self, fields: list[Any]) -> None:
        get = iter(fields).__next__
        get()
        n = int(get())

        familyCodes = [
            FamilyCode(
                accountID=get(),
                familyCodeStr=get())
            for _ in range(n)]

        self.wrapper.familyCodes(familyCodes)

    def symbolSamples(self, fields: list[Any]) -> None:
        it = iter(fields)
        get = it.__next__
        get()  # msgId
        reqId = int(get())
        n = int(get())

        cds = []
        for _ in range(n):
            cd = ContractDescription()
            cd.contract = c = Contract()
            c.conId = int(get())
            c.symbol = get()
            c.secType = get()
            c.primaryExchange = get()
            c.currency = get()
            m = int(get())
            cd.derivativeSecTypes = list(islice(it, m))
            if self.serverVersion >= 176:
                c.description = get()
                c.issuerId = get()
            cds.append(cd)

        self.wrapper.symbolSamples(reqId, cds)

    def smartComponents(self, fields: list[Any]) -> None:
        get = iter(fields).__next__
        get()
        reqId = int(get())
        n = int(get())

        components = [
            SmartComponent(
                bitNumber=int(get()),
                exchange=get(),
                exchangeLetter=get())
            for _ in range(n)]

        self.wrapper.smartComponents(reqId, components)

    def mktDepthExchanges(self, fields: list[Any]) -> None:
        get = iter(fields).__next__
        get()
        n = int(get())

        descriptions = [
            DepthMktDataDescription(
                exchange=get(),
                secType=get(),
                listingExch=get(),
                serviceDataType=get(),
                aggGroup=int(get()))
            for _ in range(n)]

        self.wrapper.mktDepthExchanges(descriptions)

    def newsProviders(self, fields: list[Any]) -> None:
        get = iter(fields).__next__
        get()
        n = int(get())

        providers = [
            NewsProvider(
                code=get(),
                name=get())
            for _ in range(n)]

        self.wrapper.newsProviders(providers)

    def histogramData(self, fields: list[Any]) -> None:
        get = iter(fields).__next__
        get()
        reqId = int(get())
        n = int(get())

        histogram = [
            HistogramData(
                price=float(get()),
                count=int(get()))
            for _ in range(n)]

        self.wrapper.histogramData(reqId, histogram)

    def marketRule(self, fields: list[Any]) -> None:
        get = iter(fields).__next__
        get()
        marketRuleId = int(get())
        n = int(get())

        increments = [
            PriceIncrement(
                lowEdge=float(get()),
                increment=float(get()))
            for _ in range(n)]

        self.wrapper.marketRule(marketRuleId, increments)

    def historicalTicks(self, fields: list[Any]) -> None:
        reqId = int(fields[1])
        n = int(fields[2])
        end = 3 + n * 4
        fromtimestamp = datetime.fromtimestamp
        utc = timezone.utc

        ticks: list[HistoricalTick] = [
            HistoricalTick(
                fromtimestamp(int(fields[i]), utc),
                float(fields[i + 2]),
                float(fields[i + 3]))
            for i in range(3, end, 4)]

        done = bool(int(fields[end]))
        self.wrapper.historicalTicks(reqId, ticks, done)

    def historicalTicksBidAsk(self, fields: list[Any]) -> None:
        reqId = int(fields[1])
        n = int(fields[2])
        end = 3 + n * 6
        fromtimestamp = datetime.fromtimestamp
        utc = timezone.utc

        ticks: list[HistoricalTickBidAsk] = []
        append = ticks.append
        for i in range(3, end, 6):
            mask = int(fields[i + 1])
            append(
                HistoricalTickBidAsk(
                    fromtimestamp(int(fields[i]), utc),
                    TickAttribBidAsk(bool(mask & 2), bool(mask & 1)),
                    float(fields[i + 2]),
                    float(fields[i + 3]),
                    float(fields[i + 4]),
                    float(fields[i + 5])))

        done = bool(int(fields[end]))
        self.wrapper.historicalTicksBidAsk(reqId, ticks, done)

    def historicalTicksLast(self, fields: list[Any]) -> None:
        reqId = int(fields[1])
        n = int(fields[2])
        end = 3 + n * 6
        fromtimestamp = datetime.fromtimestamp
        utc = timezone.utc

        ticks: list[HistoricalTickLast] = []
        append = ticks.append
        for i in range(3, end, 6):
            mask = int(fields[i + 1])
            append(
                HistoricalTickLast(
                    fromtimestamp(int(fields[i]), utc),
                    TickAttribLast(bool(mask & 1), bool(mask & 2)),
                    float(fields[i + 2]),
                    float(fields[i + 3]),
                    fields[i + 4],
                    fields[i + 5]))

        done = bool(int(fields[end]))
        self.wrapper.historicalTicksLast(reqId, ticks, done)

    def tickByTick(self, fields: list[Any]) -> None:
        reqId = int(fields[1])
        tickType = int(fields[2])
        time = int(fields[3])

        if tickType in (1, 2):
            mask = int(fields[6])
            attrib: Any = TickAttribLast(bool(mask & 1), bool(mask & 2))

            self.wrapper.tickByTickAllLast(
                reqId, tickType, time, float(fields[4]), float(fields[5]),
                attrib, fields[7], fields[8])

        elif tickType == 3:
            mask = int(fields[8])
            attrib = TickAttribBidAsk(bool(mask & 1), bool(mask & 2))

            self.wrapper.tickByTickBidAsk(
                reqId, time, float(fields[4]), float(fields[5]),
                float(fields[6]), float(fields[7]), attrib)

        elif tickType == 4:
            self.wrapper.tickByTickMidPoint(reqId, time, float(fields[4]))

    def openOrder(self, fields: list[Any]) -> None:
        o = Order()
        c = Contract()
        st = OrderState()
        it = iter(fields)
        get = it.__next__

        get()  # msgId
        o.orderId = get()
        c.conId = get()
        c.symbol = get()
        c.secType = get()
        c.lastTradeDateOrContractMonth = get()
        c.strike = get()
        c.right = get()
        c.multiplier = get()
        c.exchange = get()
        c.currency = get()
        c.localSymbol = get()
        c.tradingClass = get()
        o.action = get()
        o.totalQuantity = get()
        o.orderType = get()
        o.lmtPrice = get()
        o.auxPrice = get()
        o.tif = get()
        o.ocaGroup = get()
        o.account = get()
        o.openClose = get()
        o.origin = get()
        o.orderRef = get()
        o.clientId = get()
        o.permId = get()
        o.outsideRth = get()
        o.hidden = get()
        o.discretionaryAmt = get()
        o.goodAfterTime = get()
        get()  # sharesAllocation
        o.faGroup = get()
        o.faMethod = get()
        o.faPercentage = get()
        if self.serverVersion < 177:
            o.faProfile = get()
        o.modelCode = get()
        o.goodTillDate = get()
        o.rule80A = get()
        o.percentOffset = get()
        o.settlingFirm = get()
        o.shortSaleSlot = get()
        o.designatedLocation = get()
        o.exemptCode = get()
        o.auctionStrategy = get()
        o.startingPrice = get()
        o.stockRefPrice = get()
        o.delta = get()
        o.stockRangeLower = get()
        o.stockRangeUpper = get()
        o.displaySize = get()
        o.blockOrder = get()
        o.sweepToFill = get()
        o.allOrNone = get()
        o.minQty = get()
        o.ocaType = get()
        o.eTradeOnly = get()
        o.firmQuoteOnly = get()
        o.nbboPriceCap = get()
        o.parentId = get()
        o.triggerMethod = get()
        o.volatility = get()
        o.volatilityType = get()
        o.deltaNeutralOrderType = get()
        o.deltaNeutralAuxPrice = get()

        if o.deltaNeutralOrderType:
            o.deltaNeutralConId = get()
            o.deltaNeutralSettlingFirm = get()
            o.deltaNeutralClearingAccount = get()
            o.deltaNeutralClearingIntent = get()
            o.deltaNeutralOpenClose = get()
            o.deltaNeutralShortSale = get()
            o.deltaNeutralShortSaleSlot = get()
            o.deltaNeutralDesignatedLocation = get()

        o.continuousUpdate = get()
        o.referencePriceType = get()
        o.trailStopPrice = get()
        o.trailingPercent = get()
        o.basisPoints = get()
        o.basisPointsType = get()
        c.comboLegsDescrip = get()

        numLegs = int(get())
        c.comboLegs = []
        for _ in range(numLegs):
            leg: Any = ComboLeg()
            leg.conId = get()
            leg.ratio = get()
            leg.action = get()
            leg.exchange = get()
            leg.openClose = get()
            leg.shortSaleSlot = get()
            leg.designatedLocation = get()
            leg.exemptCode = get()
            self.parse(leg)
            c.comboLegs.append(leg)

        numOrderLegs = int(get())
        o.orderComboLegs = []
        for _ in range(numOrderLegs):
            leg = OrderComboLeg()
            leg.price = get()
            self.parse(leg)
            o.orderComboLegs.append(leg)

        numParams = int(get())
        if numParams > 0:
            o.smartComboRoutingParams = [
                TagValue(get(), get()) for _ in range(numParams)]

        o.scaleInitLevelSize = get()
        o.scaleSubsLevelSize = get()
        increment = get()

        o.scalePriceIncrement = float(increment or UNSET_DOUBLE)
        if 0 < o.scalePriceIncrement < UNSET_DOUBLE:
            o.scalePriceAdjustValue = get()
            o.scalePriceAdjustInterval = get()
            o.scaleProfitOffset = get()
            o.scaleAutoReset = get()
            o.scaleInitPosition = get()
            o.scaleInitFillQty = get()
            o.scaleRandomPercent = get()

        o.hedgeType = get()
        if o.hedgeType:
            o.hedgeParam = get()

        o.optOutSmartRouting = get()
        o.clearingAccount = get()
        o.clearingIntent = get()
        o.notHeld = get()
        dncPresent = get()

        if int(dncPresent):
            c.deltaNeutralContract = DeltaNeutralContract(
                int(get() or 0), float(get() or 0), float(get() or 0))

        o.algoStrategy = get()
        if o.algoStrategy:
            numParams = int(get())
            if numParams > 0:
                o.algoParams = [
                    TagValue(get(), get()) for _ in range(numParams)]

        o.solicited = get()
        o.whatIf = get()
        st.status = get()
        st.initMarginBefore = get()
        st.maintMarginBefore = get()
        st.equityWithLoanBefore = get()
        st.initMarginChange = get()
        st.maintMarginChange = get()
        st.equityWithLoanChange = get()
        st.initMarginAfter = get()
        st.maintMarginAfter = get()
        st.equityWithLoanAfter = get()
        st.commission = get()
        st.minCommission = get()
        st.maxCommission = get()
        st.commissionCurrency = get()
        st.warningText = get()
        o.randomizeSize = get()
        o.randomizePrice = get()

        if o.orderType in ('PEG BENCH', 'PEGBENCH'):
            o.referenceContractId = get()
            o.isPeggedChangeAmountDecrease = get()
            o.peggedChangeAmount = get()
            o.referenceChangeAmount = get()
            o.referenceExchangeId = get()

        numConditions = int(get())
        if numConditions > 0:
            for _ in range(numConditions):
                condType = int(get())
                condCls = OrderCondition.createClass(condType)
                n = _get_condition_arity(condCls)
                cond = condCls(condType, *islice(it, n))
                self.parse(cond)
                o.conditions.append(cond)
            o.conditionsIgnoreRth = get()
            o.conditionsCancelOrder = get()

        o.adjustedOrderType = get()
        o.triggerPrice = get()
        o.trailStopPrice = get()
        o.lmtPriceOffset = get()
        o.adjustedStopPrice = get()
        o.adjustedStopLimitPrice = get()
        o.adjustedTrailingAmount = get()
        o.adjustableTrailingUnit = get()
        o.softDollarTier.name = get()
        o.softDollarTier.val = get()
        o.softDollarTier.displayName = get()
        o.cashQty = get()
        o.dontUseAutoPriceForHedge = get()
        o.isOmsContainer = get()
        o.discretionaryUpToLimitPrice = get()
        o.usePriceMgmtAlgo = get()

        if self.serverVersion >= 159:
            o.duration = get()
        if self.serverVersion >= 160:
            o.postToAts = get()
        if self.serverVersion >= 162:
            o.autoCancelParent = get()
        if self.serverVersion >= 170:
            o.minTradeQty = get()
            o.minCompeteSize = get()
            o.competeAgainstBestOffset = get()
            o.midOffsetAtWhole = get()
            o.midOffsetAtHalf = get()

        self.parse(c)
        self.parse(o)
        self.parse(st)
        self.wrapper.openOrder(o.orderId, c, o, st)

    def completedOrder(self, fields: list[Any]) -> None:
        o = Order()
        c = Contract()
        st = OrderState()
        it = iter(fields)
        get = it.__next__

        get()  # msgId
        c.conId = get()
        c.symbol = get()
        c.secType = get()
        c.lastTradeDateOrContractMonth = get()
        c.strike = get()
        c.right = get()
        c.multiplier = get()
        c.exchange = get()
        c.currency = get()
        c.localSymbol = get()
        c.tradingClass = get()
        o.action = get()
        o.totalQuantity = get()
        o.orderType = get()
        o.lmtPrice = get()
        o.auxPrice = get()
        o.tif = get()
        o.ocaGroup = get()
        o.account = get()
        o.openClose = get()
        o.origin = get()
        o.orderRef = get()
        o.permId = get()
        o.outsideRth = get()
        o.hidden = get()
        o.discretionaryAmt = get()
        o.goodAfterTime = get()
        o.faGroup = get()
        o.faMethod = get()
        o.faPercentage = get()
        if self.serverVersion < 177:
            o.faProfile = get()
        o.modelCode = get()
        o.goodTillDate = get()
        o.rule80A = get()
        o.percentOffset = get()
        o.settlingFirm = get()
        o.shortSaleSlot = get()
        o.designatedLocation = get()
        o.exemptCode = get()
        o.startingPrice = get()
        o.stockRefPrice = get()
        o.delta = get()
        o.stockRangeLower = get()
        o.stockRangeUpper = get()
        o.displaySize = get()
        o.sweepToFill = get()
        o.allOrNone = get()
        o.minQty = get()
        o.ocaType = get()
        o.triggerMethod = get()
        o.volatility = get()
        o.volatilityType = get()
        o.deltaNeutralOrderType = get()
        o.deltaNeutralAuxPrice = get()

        if o.deltaNeutralOrderType:
            o.deltaNeutralConId = get()
            o.deltaNeutralShortSale = get()
            o.deltaNeutralShortSaleSlot = get()
            o.deltaNeutralDesignatedLocation = get()

        o.continuousUpdate = get()
        o.referencePriceType = get()
        o.trailStopPrice = get()
        o.trailingPercent = get()
        c.comboLegsDescrip = get()

        numLegs = int(get())
        c.comboLegs = []
        for _ in range(numLegs):
            leg: Any = ComboLeg()
            leg.conId = get()
            leg.ratio = get()
            leg.action = get()
            leg.exchange = get()
            leg.openClose = get()
            leg.shortSaleSlot = get()
            leg.designatedLocation = get()
            leg.exemptCode = get()
            self.parse(leg)
            c.comboLegs.append(leg)

        numOrderLegs = int(get())
        o.orderComboLegs = []
        for _ in range(numOrderLegs):
            leg = OrderComboLeg()
            leg.price = get()
            self.parse(leg)
            o.orderComboLegs.append(leg)

        numParams = int(get())
        if numParams > 0:
            o.smartComboRoutingParams = [
                TagValue(get(), get()) for _ in range(numParams)]

        o.scaleInitLevelSize = get()
        o.scaleSubsLevelSize = get()
        increment = get()

        o.scalePriceIncrement = float(increment or UNSET_DOUBLE)
        if 0 < o.scalePriceIncrement < UNSET_DOUBLE:
            o.scalePriceAdjustValue = get()
            o.scalePriceAdjustInterval = get()
            o.scaleProfitOffset = get()
            o.scaleAutoReset = get()
            o.scaleInitPosition = get()
            o.scaleInitFillQty = get()
            o.scaleRandomPercent = get()

        o.hedgeType = get()
        if o.hedgeType:
            o.hedgeParam = get()

        o.clearingAccount = get()
        o.clearingIntent = get()
        o.notHeld = get()
        dncPresent = get()

        if int(dncPresent):
            c.deltaNeutralContract = DeltaNeutralContract(
                int(get() or 0), float(get() or 0), float(get() or 0))

        o.algoStrategy = get()
        if o.algoStrategy:
            numParams = int(get())
            if numParams > 0:
                o.algoParams = [
                    TagValue(get(), get()) for _ in range(numParams)]

        o.solicited = get()
        st.status = get()
        o.randomizeSize = get()
        o.randomizePrice = get()

        if o.orderType in ('PEG BENCH', 'PEGBENCH'):
            o.referenceContractId = get()
            o.isPeggedChangeAmountDecrease = get()
            o.peggedChangeAmount = get()
            o.referenceChangeAmount = get()
            o.referenceExchangeId = get()

        numConditions = int(get())
        if numConditions > 0:
            for _ in range(numConditions):
                condType = int(get())
                condCls = OrderCondition.createClass(condType)
                n = _get_condition_arity(condCls)
                cond = condCls(condType, *islice(it, n))
                self.parse(cond)
                o.conditions.append(cond)
            o.conditionsIgnoreRth = get()
            o.conditionsCancelOrder = get()

        o.trailStopPrice = get()
        o.lmtPriceOffset = get()
        o.cashQty = get()
        o.dontUseAutoPriceForHedge = get()
        o.isOmsContainer = get()
        o.autoCancelDate = get()
        o.filledQuantity = get()
        o.refFuturesConId = get()
        o.autoCancelParent = get()
        o.shareholder = get()
        o.imbalanceOnly = get()
        o.routeMarketableToBbo = get()
        o.parentPermId = get()
        st.completedTime = get()
        st.completedStatus = get()

        if self.serverVersion >= 170:
            o.minTradeQty = get()
            o.minCompeteSize = get()
            o.competeAgainstBestOffset = get()
            o.midOffsetAtWhole = get()
            o.midOffsetAtHalf = get()

        self.parse(c)
        self.parse(o)
        self.parse(st)
        self.wrapper.completedOrder(c, o, st)

    def historicalSchedule(self, fields: list[Any]) -> None:
        get = iter(fields).__next__
        get()  # msgId
        reqId = int(get())
        startDateTime = get()
        endDateTime = get()
        timeZone = get()
        count = int(get())
        sessions = [HistoricalSession(
            startDateTime=get(), endDateTime=get(), refDate=get())
            for _ in range(count)]
        self.wrapper.historicalSchedule(
            reqId, startDateTime, endDateTime, timeZone, sessions)
