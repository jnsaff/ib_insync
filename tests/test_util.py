"""Unit tests for utility functions."""

import asyncio
import datetime as dt
import math
import pytest
from dataclasses import dataclass
from typing import Optional
from unittest.mock import patch, MagicMock

import ib_insync.util as util


@dataclass
class UtilTestDataclass:
    """Test dataclass for utility function tests."""
    name: str = 'test'
    value: int = 42
    optional: Optional[str] = None


class TestDataclassUtilities:
    """Test dataclass utility functions."""

    def test_dataclass_as_dict(self):
        """Test dataclassAsDict function."""
        obj = UtilTestDataclass(name='example', value=100, optional='optional_value')
        result = util.dataclassAsDict(obj)
        
        expected = {
            'name': 'example',
            'value': 100,
            'optional': 'optional_value'
        }
        assert result == expected

    def test_dataclass_as_dict_with_defaults(self):
        """Test dataclassAsDict with default values."""
        obj = UtilTestDataclass()
        result = util.dataclassAsDict(obj)
        
        expected = {
            'name': 'test',
            'value': 42,
            'optional': None
        }
        assert result == expected

    def test_dataclass_as_dict_non_dataclass(self):
        """Test dataclassAsDict with non-dataclass object."""
        with pytest.raises(TypeError, match="Object .* is not a dataclass"):
            util.dataclassAsDict("not a dataclass")

    def test_dataclass_as_tuple(self):
        """Test dataclassAsTuple function."""
        obj = UtilTestDataclass(name='example', value=100, optional='optional_value')
        result = util.dataclassAsTuple(obj)
        
        expected = ('example', 100, 'optional_value')
        assert result == expected

    def test_dataclass_as_tuple_with_defaults(self):
        """Test dataclassAsTuple with default values."""
        obj = UtilTestDataclass()
        result = util.dataclassAsTuple(obj)
        
        expected = ('test', 42, None)
        assert result == expected

    def test_dataclass_as_tuple_non_dataclass(self):
        """Test dataclassAsTuple with non-dataclass object."""
        with pytest.raises(TypeError, match="Object .* is not a dataclass"):
            util.dataclassAsTuple("not a dataclass")

    def test_dataclass_non_defaults(self):
        """Test dataclassNonDefaults function."""
        obj = UtilTestDataclass(name='changed', value=42, optional=None)
        result = util.dataclassNonDefaults(obj)
        
        # Only 'name' should be different from default
        expected = {'name': 'changed'}
        assert result == expected

    def test_dataclass_non_defaults_all_changed(self):
        """Test dataclassNonDefaults with all values changed."""
        obj = UtilTestDataclass(name='changed', value=100, optional='set')
        result = util.dataclassNonDefaults(obj)
        
        expected = {
            'name': 'changed',
            'value': 100,
            'optional': 'set'
        }
        assert result == expected

    def test_dataclass_non_defaults_empty_list(self):
        """Test dataclassNonDefaults with empty list (should be filtered out)."""
        @dataclass
        class TestWithList:
            items: list = None
            
            def __post_init__(self):
                if self.items is None:
                    self.items = []
        
        obj = TestWithList()
        result = util.dataclassNonDefaults(obj)
        
        # Empty list should be filtered out
        assert result == {}

    def test_dataclass_non_defaults_non_dataclass(self):
        """Test dataclassNonDefaults with non-dataclass object."""
        with pytest.raises(TypeError, match="Object .* is not a dataclass"):
            util.dataclassNonDefaults("not a dataclass")


class TestDataclassUpdate:
    """Test dataclass update functionality."""

    def test_dataclass_update_basic(self):
        """Test basic dataclass update."""
        obj = UtilTestDataclass()
        updated = util.dataclassUpdate(obj, name='updated', value=200)
        
        assert updated.name == 'updated'
        assert updated.value == 200
        assert updated.optional is None
        
        # dataclassUpdate modifies the original object in-place
        assert obj.name == 'updated'
        assert obj.value == 200

    def test_dataclass_update_partial(self):
        """Test partial dataclass update."""
        obj = UtilTestDataclass(name='original', value=100)
        updated = util.dataclassUpdate(obj, name='changed')
        
        assert updated.name == 'changed'
        assert updated.value == 100  # Should remain unchanged
        assert updated.optional is None


class TestTimeUtilities:
    """Test time-related utility functions."""

    def test_parse_ib_datetime_with_timezone(self):
        """Test parsing IB datetime with timezone."""
        # Test with timezone
        dt_str = "20220101-10:30:00 US/Eastern"
        result = util.parseIBDatetime(dt_str)
        
        assert isinstance(result, dt.datetime)
        assert result.year == 2022
        assert result.month == 1
        assert result.day == 1
        assert result.hour == 10
        assert result.minute == 30
        assert result.second == 0

    def test_parse_ib_datetime_without_timezone(self):
        """Test parsing IB datetime without timezone."""
        dt_str = "20220101-10:30:00"
        result = util.parseIBDatetime(dt_str)
        
        assert isinstance(result, dt.datetime)
        assert result.year == 2022
        assert result.month == 1
        assert result.day == 1
        assert result.hour == 10
        assert result.minute == 30
        assert result.second == 0

    def test_parse_ib_datetime_date_only(self):
        """Test parsing IB date only."""
        dt_str = "20220101"
        result = util.parseIBDatetime(dt_str)
        
        assert isinstance(result, dt.date)
        assert result.year == 2022
        assert result.month == 1
        assert result.day == 1

    def test_parse_ib_datetime_empty_string(self):
        """Test parsing empty datetime string."""
        # Empty string should raise ValueError based on actual implementation
        with pytest.raises(ValueError):
            util.parseIBDatetime("")

    def test_parse_ib_datetime_invalid_format(self):
        """Test parsing invalid datetime format."""
        with pytest.raises(ValueError):
            util.parseIBDatetime("invalid-format")


class TestHelperFunctions:
    """Test helper functions."""

    def test_is_nan(self):
        """Test isNan function."""
        assert util.isNan(float('nan'))
        assert util.isNan(math.nan)
        assert not util.isNan(0)
        assert not util.isNan(42.0)
        assert not util.isNan("not a number")

    def test_unset_values(self):
        """Test UNSET constants."""
        assert util.UNSET_INTEGER == 2 ** 31 - 1
        assert util.UNSET_DOUBLE > 0
        assert math.isfinite(util.UNSET_DOUBLE)


class TestEventLoopUtilities:
    """Test event loop utilities."""

    def test_get_loop(self):
        """Test getLoop function."""
        loop = util.getLoop()
        assert isinstance(loop, asyncio.AbstractEventLoop)

    def test_run_coroutine(self):
        """Test run function with coroutine."""
        async def test_coro():
            return "test_result"
        
        result = util.run(test_coro())
        assert result == "test_result"

    def test_run_coroutine_with_args(self):
        """Test run function with coroutine and arguments."""
        async def test_coro(arg1, arg2):
            return arg1 + arg2
        
        result = util.run(test_coro(10, 20))
        assert result == 30


class TestPandasIntegration:
    """Test pandas integration utilities."""

    def test_df_with_dataclass_objects(self):
        """Test df function with dataclass objects."""
        objects = [
            UtilTestDataclass(name='obj1', value=1),
            UtilTestDataclass(name='obj2', value=2),
            UtilTestDataclass(name='obj3', value=3)
        ]
        
        try:
            df = util.df(objects)
            assert df is not None
            assert len(df) == 3
            assert 'name' in df.columns
            assert 'value' in df.columns
            assert 'optional' in df.columns
        except ImportError:
            # pandas not installed, skip test
            pytest.skip("pandas not installed")

    def test_df_with_labels(self):
        """Test df function with label filtering."""
        objects = [
            UtilTestDataclass(name='obj1', value=1),
            UtilTestDataclass(name='obj2', value=2)
        ]
        
        try:
            df = util.df(objects, labels=['name'])
            assert df is not None
            assert len(df) == 2
            assert 'name' in df.columns
            assert 'value' not in df.columns
            assert 'optional' not in df.columns
        except ImportError:
            # pandas not installed, skip test
            pytest.skip("pandas not installed")

    def test_df_with_empty_list(self):
        """Test df function with empty list."""
        try:
            df = util.df([])
            assert df is None
        except ImportError:
            # pandas not installed, skip test
            pytest.skip("pandas not installed")

    def test_df_with_tuples(self):
        """Test df function with tuple objects."""
        from collections import namedtuple
        TestTuple = namedtuple('TestTuple', ['name', 'value'])
        
        objects = [
            TestTuple(name='obj1', value=1),
            TestTuple(name='obj2', value=2)
        ]
        
        try:
            df = util.df(objects)
            assert df is not None
            assert len(df) == 2
            assert 'name' in df.columns
            assert 'value' in df.columns
        except ImportError:
            # pandas not installed, skip test
            pytest.skip("pandas not installed")


class TestLoggerUtilities:
    """Test logger utilities."""

    def test_logger_configuration(self):
        """Test that logger configuration is properly set."""
        # Test that we can get a logger
        logger = util.logging.getLogger('test_logger')
        assert logger is not None

    def test_global_error_event(self):
        """Test global error event."""
        assert hasattr(util, 'globalErrorEvent')
        assert hasattr(util.globalErrorEvent, 'emit')


class TestZoneInfoCompatibility:
    """Test timezone compatibility."""

    def test_zone_info_import(self):
        """Test that ZoneInfo is properly imported."""
        assert hasattr(util, 'ZoneInfo')
        
        # Test that we can create a timezone
        tz = util.ZoneInfo('UTC')
        assert tz is not None

    def test_epoch_constant(self):
        """Test EPOCH constant."""
        assert util.EPOCH == dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
        assert util.EPOCH.tzinfo is not None