"""Unit tests for contract types."""

import pytest
from datetime import datetime

import ib_insync.contract as contract
from ib_insync.contract import (
    Contract, Stock, Option, Future, Forex, Index, CFD, Commodity, Bond,
    Warrant, Crypto, MutualFund, ComboLeg, ContractDetails, ContractDescription,
    DeltaNeutralContract, ScanData, TagValue
)


class TestContract:
    """Test Contract base class."""

    def test_contract_creation_empty(self):
        """Test creating empty contract."""
        c = Contract()
        assert c.secType == ''
        assert c.conId == 0
        assert c.symbol == ''
        assert c.currency == ''
        assert c.exchange == ''
        assert c.strike == 0.0
        assert c.right == ''
        assert c.multiplier == ''
        assert c.lastTradeDateOrContractMonth == ''
        assert c.primaryExchange == ''
        assert c.localSymbol == ''
        assert c.tradingClass == ''
        assert c.includeExpired is False
        assert c.secIdType == ''
        assert c.secId == ''
        assert c.description == ''

    def test_contract_creation_with_kwargs(self):
        """Test creating contract with keyword arguments."""
        c = Contract(
            symbol='AAPL',
            secType='STK',
            currency='USD',
            exchange='SMART',
            conId=265598
        )
        assert c.symbol == 'AAPL'
        assert c.secType == 'STK'
        assert c.currency == 'USD'
        assert c.exchange == 'SMART'
        assert c.conId == 265598

    def test_contract_equality(self):
        """Test contract equality comparison."""
        c1 = Contract(symbol='AAPL', secType='STK', currency='USD')
        c2 = Contract(symbol='AAPL', secType='STK', currency='USD')
        c3 = Contract(symbol='MSFT', secType='STK', currency='USD')
        
        assert c1 == c2
        assert c1 != c3

    def test_contract_repr(self):
        """Test contract string representation."""
        c = Contract(symbol='AAPL', secType='STK', currency='USD')
        repr_str = repr(c)
        assert 'AAPL' in repr_str
        assert 'STK' in repr_str
        assert 'USD' in repr_str

    def test_contract_with_combo_legs(self):
        """Test contract with combo legs."""
        leg1 = ComboLeg(conId=1, ratio=1, action='BUY', exchange='SMART')
        leg2 = ComboLeg(conId=2, ratio=1, action='SELL', exchange='SMART')
        
        c = Contract(
            symbol='COMBO',
            secType='BAG',
            currency='USD',
            exchange='SMART',
            comboLegs=[leg1, leg2]
        )
        
        assert len(c.comboLegs) == 2
        assert c.comboLegs[0].conId == 1
        assert c.comboLegs[1].conId == 2

    def test_contract_with_delta_neutral(self):
        """Test contract with delta neutral contract."""
        delta_neutral = DeltaNeutralContract(
            conId=123,
            delta=0.5,
            price=100.0
        )
        
        c = Contract(
            symbol='AAPL',
            secType='STK',
            currency='USD',
            deltaNeutralContract=delta_neutral
        )
        
        assert c.deltaNeutralContract.conId == 123
        assert c.deltaNeutralContract.delta == 0.5
        assert c.deltaNeutralContract.price == 100.0


class TestSpecializedContracts:
    """Test specialized contract classes."""

    def test_stock_creation(self):
        """Test Stock contract creation."""
        stock = Stock('AAPL', 'SMART', 'USD')
        assert stock.symbol == 'AAPL'
        assert stock.secType == 'STK'
        assert stock.exchange == 'SMART'
        assert stock.currency == 'USD'

    def test_stock_with_primary_exchange(self):
        """Test Stock with primary exchange."""
        stock = Stock('AAPL', 'SMART', 'USD', primaryExchange='NASDAQ')
        assert stock.primaryExchange == 'NASDAQ'

    def test_option_creation(self):
        """Test Option contract creation."""
        option = Option('AAPL', '20231215', 150.0, 'C', 'SMART')
        assert option.symbol == 'AAPL'
        assert option.secType == 'OPT'
        assert option.lastTradeDateOrContractMonth == '20231215'
        assert option.strike == 150.0
        assert option.right == 'C'
        assert option.exchange == 'SMART'

    def test_option_with_multiplier(self):
        """Test Option with multiplier."""
        option = Option('AAPL', '20231215', 150.0, 'C', 'SMART', multiplier='100')
        assert option.multiplier == '100'

    def test_future_creation(self):
        """Test Future contract creation."""
        future = Future('ES', '20231215', 'GLOBEX')
        assert future.symbol == 'ES'
        assert future.secType == 'FUT'
        assert future.lastTradeDateOrContractMonth == '20231215'
        assert future.exchange == 'GLOBEX'

    def test_future_with_multiplier(self):
        """Test Future with multiplier."""
        future = Future('ES', '20231215', 'GLOBEX', multiplier='50')
        assert future.multiplier == '50'

    def test_forex_creation(self):
        """Test Forex contract creation."""
        forex = Forex('EURUSD')
        assert forex.symbol == 'EUR'
        assert forex.secType == 'CASH'
        assert forex.currency == 'USD'
        assert forex.exchange == 'IDEALPRO'

    def test_forex_custom_exchange(self):
        """Test Forex with custom exchange."""
        forex = Forex('EURUSD', exchange='FXCONV')
        assert forex.exchange == 'FXCONV'

    def test_index_creation(self):
        """Test Index contract creation."""
        index = Index('SPX', 'CBOE')
        assert index.symbol == 'SPX'
        assert index.secType == 'IND'
        assert index.exchange == 'CBOE'

    def test_index_with_currency(self):
        """Test Index with currency."""
        index = Index('SPX', 'CBOE', currency='USD')
        assert index.currency == 'USD'

    def test_cfd_creation(self):
        """Test CFD contract creation."""
        cfd = CFD('IBUS30')
        assert cfd.symbol == 'IBUS30'
        assert cfd.secType == 'CFD'
        assert cfd.exchange == ''  # Default is empty
        assert cfd.currency == ''  # Default is empty

    def test_cfd_with_custom_params(self):
        """Test CFD with custom parameters."""
        cfd = CFD('IBUS30', exchange='IDEALPRO', currency='EUR')
        assert cfd.exchange == 'IDEALPRO'
        assert cfd.currency == 'EUR'

    def test_commodity_creation(self):
        """Test Commodity contract creation."""
        commodity = Commodity('XAUUSD', 'SMART', 'USD')
        assert commodity.symbol == 'XAUUSD'
        assert commodity.secType == 'CMDTY'
        assert commodity.exchange == 'SMART'
        assert commodity.currency == 'USD'

    def test_bond_creation(self):
        """Test Bond contract creation."""
        bond = Bond(secIdType='ISIN', secId='US03076KAA60')
        assert bond.secType == 'BOND'
        assert bond.secIdType == 'ISIN'
        assert bond.secId == 'US03076KAA60'

    def test_bond_with_exchange(self):
        """Test Bond with exchange."""
        bond = Bond(secIdType='ISIN', secId='US03076KAA60', exchange='SMART')
        assert bond.exchange == 'SMART'

    def test_warrant_creation(self):
        """Test Warrant contract creation."""
        warrant = Warrant(
            symbol='AAPL', 
            lastTradeDateOrContractMonth='20231215', 
            strike=150.0, 
            right='C', 
            exchange='SMART'
        )
        assert warrant.symbol == 'AAPL'
        assert warrant.secType == 'WAR'
        assert warrant.lastTradeDateOrContractMonth == '20231215'
        assert warrant.strike == 150.0
        assert warrant.right == 'C'
        assert warrant.exchange == 'SMART'

    def test_crypto_creation(self):
        """Test Crypto contract creation."""
        crypto = Crypto('BTC', 'PAXOS', 'USD')
        assert crypto.symbol == 'BTC'
        assert crypto.secType == 'CRYPTO'
        assert crypto.exchange == 'PAXOS'
        assert crypto.currency == 'USD'

    def test_mutual_fund_creation(self):
        """Test MutualFund contract creation."""
        fund = MutualFund(symbol='FXAIX', exchange='FUNDSERV', currency='USD')
        assert fund.symbol == 'FXAIX'
        assert fund.secType == 'FUND'
        assert fund.exchange == 'FUNDSERV'
        assert fund.currency == 'USD'


class TestComboLeg:
    """Test ComboLeg class."""

    def test_combo_leg_creation(self):
        """Test ComboLeg creation."""
        leg = ComboLeg()
        assert leg.conId == 0
        assert leg.ratio == 0
        assert leg.action == ''
        assert leg.exchange == ''
        assert leg.openClose == 0
        assert leg.shortSaleSlot == 0
        assert leg.designatedLocation == ''
        assert leg.exemptCode == -1

    def test_combo_leg_with_params(self):
        """Test ComboLeg with parameters."""
        leg = ComboLeg(
            conId=123,
            ratio=1,
            action='BUY',
            exchange='SMART',
            openClose=1,
            shortSaleSlot=2,
            designatedLocation='SMART',
            exemptCode=0
        )
        assert leg.conId == 123
        assert leg.ratio == 1
        assert leg.action == 'BUY'
        assert leg.exchange == 'SMART'
        assert leg.openClose == 1
        assert leg.shortSaleSlot == 2
        assert leg.designatedLocation == 'SMART'
        assert leg.exemptCode == 0


class TestContractDetails:
    """Test ContractDetails class."""

    def test_contract_details_creation(self):
        """Test ContractDetails creation."""
        details = ContractDetails()
        assert details.contract is None  # Default is None
        assert details.marketName == ''
        assert details.minTick == 0.0
        assert details.orderTypes == ''
        assert details.validExchanges == ''
        assert details.priceMagnifier == 0
        assert details.underConId == 0
        assert details.longName == ''
        assert details.contractMonth == ''
        assert details.industry == ''
        assert details.category == ''
        assert details.subcategory == ''
        assert details.timeZoneId == ''
        assert details.tradingHours == ''
        assert details.liquidHours == ''
        assert details.evRule == ''
        assert details.evMultiplier == 0
        assert details.mdSizeMultiplier == 1  # Default is 1
        assert details.aggGroup == 0
        assert details.underSymbol == ''
        assert details.underSecType == ''
        assert details.marketRuleIds == ''
        assert details.secIdList == []
        assert details.realExpirationDate == ''
        assert details.lastTradeTime == ''
        assert details.stockType == ''
        assert details.minSize == 0.0
        assert details.sizeIncrement == 0.0
        assert details.suggestedSizeIncrement == 0.0
        assert details.cusip == ''
        assert details.ratings == ''
        assert details.descAppend == ''
        assert details.bondType == ''
        assert details.couponType == ''
        assert details.callable == False
        assert details.putable == False
        assert details.coupon == 0
        assert details.convertible == False
        assert details.maturity == ''
        assert details.issueDate == ''
        assert details.nextOptionDate == ''
        assert details.nextOptionType == ''
        assert details.nextOptionPartial == False
        assert details.notes == ''

    def test_contract_details_with_contract(self):
        """Test ContractDetails with contract."""
        contract_obj = Stock('AAPL', 'SMART', 'USD')
        details = ContractDetails(contract=contract_obj)
        assert details.contract.symbol == 'AAPL'
        assert details.contract.secType == 'STK'


class TestContractDescription:
    """Test ContractDescription class."""

    def test_contract_description_creation(self):
        """Test ContractDescription creation."""
        desc = ContractDescription()
        assert desc.contract is None  # Default is None
        assert desc.derivativeSecTypes == []

    def test_contract_description_with_params(self):
        """Test ContractDescription with parameters."""
        contract_obj = Stock('AAPL', 'SMART', 'USD')
        desc = ContractDescription(
            contract=contract_obj,
            derivativeSecTypes=['OPT', 'FUT']
        )
        assert desc.contract.symbol == 'AAPL'
        assert desc.derivativeSecTypes == ['OPT', 'FUT']


class TestDeltaNeutralContract:
    """Test DeltaNeutralContract class."""

    def test_delta_neutral_creation(self):
        """Test DeltaNeutralContract creation."""
        delta = DeltaNeutralContract()
        assert delta.conId == 0
        assert delta.delta == 0.0
        assert delta.price == 0.0

    def test_delta_neutral_with_params(self):
        """Test DeltaNeutralContract with parameters."""
        delta = DeltaNeutralContract(
            conId=123,
            delta=0.5,
            price=100.0
        )
        assert delta.conId == 123
        assert delta.delta == 0.5
        assert delta.price == 100.0


class TestScanData:
    """Test ScanData class."""

    def test_scan_data_creation(self):
        """Test ScanData creation."""
        from ib_insync.contract import ContractDetails
        contract_details = ContractDetails()
        scan = ScanData(
            rank=1,
            contractDetails=contract_details,
            distance='10.5',
            benchmark='SPY',
            projection='UP',
            legsStr='COMBO'
        )
        assert scan.rank == 1
        assert scan.contractDetails == contract_details
        assert scan.distance == '10.5'
        assert scan.benchmark == 'SPY'
        assert scan.projection == 'UP'
        assert scan.legsStr == 'COMBO'

    def test_scan_data_with_params(self):
        """Test ScanData with parameters."""
        from ib_insync.contract import ContractDetails
        contract_obj = Stock('AAPL', 'SMART', 'USD')
        contract_details = ContractDetails(contract=contract_obj)
        scan = ScanData(
            rank=1,
            contractDetails=contract_details,
            distance='10.5',
            benchmark='SPY',
            projection='UP',
            legsStr='COMBO'
        )
        assert scan.contractDetails.contract.symbol == 'AAPL'
        assert scan.rank == 1
        assert scan.distance == '10.5'
        assert scan.benchmark == 'SPY'
        assert scan.projection == 'UP'
        assert scan.legsStr == 'COMBO'


class TestTagValue:
    """Test TagValue class."""

    def test_tag_value_creation(self):
        """Test TagValue creation."""
        tag = TagValue('', '')
        assert tag.tag == ''
        assert tag.value == ''

    def test_tag_value_with_params(self):
        """Test TagValue with parameters."""
        tag = TagValue(tag='allocation', value='50')
        assert tag.tag == 'allocation'
        assert tag.value == '50'

    def test_tag_value_string_representation(self):
        """Test TagValue string representation."""
        tag = TagValue(tag='allocation', value='50')
        str_repr = str(tag)
        assert 'allocation' in str_repr
        assert '50' in str_repr


class TestContractValidation:
    """Test contract validation and edge cases."""

    def test_option_right_validation(self):
        """Test option right validation."""
        # Test valid rights
        option_call = Option('AAPL', '20231215', 150.0, 'C', 'SMART')
        option_put = Option('AAPL', '20231215', 150.0, 'P', 'SMART')
        
        assert option_call.right == 'C'
        assert option_put.right == 'P'

    def test_forex_pair_parsing(self):
        """Test forex pair parsing."""
        # Test standard pairs
        eur_usd = Forex('EURUSD')
        assert eur_usd.symbol == 'EUR'
        assert eur_usd.currency == 'USD'
        
        gbp_jpy = Forex('GBPJPY')
        assert gbp_jpy.symbol == 'GBP'
        assert gbp_jpy.currency == 'JPY'
        
        # Test non-standard length
        with pytest.raises(AssertionError):
            Forex('INVALID')

    def test_contract_serialization(self):
        """Test contract serialization methods."""
        stock = Stock('AAPL', 'SMART', 'USD')
        
        # Test dict conversion
        stock_dict = stock.dict()
        assert isinstance(stock_dict, dict)
        assert stock_dict['symbol'] == 'AAPL'
        assert stock_dict['secType'] == 'STK'
        
        # Test tuple conversion
        stock_tuple = stock.tuple()
        assert isinstance(stock_tuple, tuple)
        assert 'AAPL' in stock_tuple
        assert 'STK' in stock_tuple
        
        # Test nonDefaults
        non_defaults = stock.nonDefaults()
        assert isinstance(non_defaults, dict)
        assert 'symbol' in non_defaults
        assert 'secType' in non_defaults

    def test_contract_update(self):
        """Test contract update functionality."""
        stock = Stock('AAPL', 'SMART', 'USD')
        updated = stock.update(currency='EUR', exchange='IBKRATS')
        
        assert updated.currency == 'EUR'
        assert updated.exchange == 'IBKRATS'
        assert updated.symbol == 'AAPL'  # Original values preserved
        assert updated.secType == 'STK'
        
        # update modifies original object in-place
        assert stock.currency == 'EUR'
        assert stock.exchange == 'IBKRATS'