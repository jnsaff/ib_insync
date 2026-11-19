"""Security, input validation, and modern Python asyncio tests."""

import ast
import asyncio
import email.message
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

import ib_insync as ibi
from ib_insync.client import Client
from ib_insync.flexreport import (
    FlexError, FlexReport, _validate_flex_url, _validate_param)
from ib_insync.wrapper import Wrapper


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestFlexReportSecurity:
    @pytest.mark.parametrize('url', [
        'https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement',
        'https://ndcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement',
        'https://www.interactivebrokers.co.uk/Universal/servlet/FlexStatementService.GetStatement',
        'https://interactivebrokers.eu/FlexStatementService.GetStatement',
    ])
    def test_validate_flex_url_accepts_allowed_https_ib_urls(self, url):
        assert _validate_flex_url(url) == url

    @pytest.mark.parametrize('bad_url', [
        '',
        '   ',
        'http://gdcdyn.interactivebrokers.com/path',
        'file:///etc/passwd',
        'ftp://gdcdyn.interactivebrokers.com/path',
        'https://evil.example.com/path',
        'https://interactivebrokers.com.evil.example/path',
        'https://notinteractivebrokers.com/path',
        'https://user:pass@gdcdyn.interactivebrokers.com/path',
        'https://gdcdyn.interactivebrokers.com/path?extra=1',
        'https://gdcdyn.interactivebrokers.com/path#frag',
        'https://gdcdyn.interactivebrokers.com/path\r\nX-Injected: 1',
    ])
    def test_validate_flex_url_rejects_unsafe_urls(self, bad_url):
        with pytest.raises(FlexError):
            _validate_flex_url(bad_url)

    @pytest.mark.parametrize('bad_val', [
        '', '   ', None, True, False, 1.5, [], 'abc def', 'abc\n123',
    ])
    def test_validate_param_rejects_invalid_values(self, bad_val):
        with pytest.raises(ValueError):
            _validate_param('token', bad_val)

    def test_validate_param_accepts_str_and_int(self):
        assert _validate_param('token', ' 12345 ') == '12345'
        assert _validate_param('queryId', 98765) == '98765'

    def test_init_rejects_partial_credentials(self):
        with pytest.raises(ValueError, match='Both token and queryId'):
            FlexReport(token='12345')
        with pytest.raises(ValueError, match='Both token and queryId'):
            FlexReport(queryId='67890')

    @pytest.mark.parametrize('kwargs', [
        {'timeout': 0},
        {'timeout': -5},
        {'timeout': True},
        {'maxRetries': 0},
        {'maxRetries': -1},
        {'maxRetries': True},
        {'maxRetries': 1.5},
    ])
    def test_download_validates_timeout_and_retries(self, kwargs):
        report = FlexReport()
        with pytest.raises(ValueError):
            report.download('tok123', 'qry456', **kwargs)

    def test_download_rejects_untrusted_redirect_url_before_second_fetch(self):
        step1_xml = (
            b'<FlexStatementResponse>'
            b'<Status>Success</Status>'
            b'<ReferenceCode>REF123</ReferenceCode>'
            b'<Url>file:///etc/passwd</Url>'
            b'</FlexStatementResponse>'
        )
        with patch('ib_insync.flexreport.urlopen',
                   return_value=_FakeResp(step1_xml)) as mock_open:
            report = FlexReport()
            with pytest.raises(FlexError, match='non-HTTPS'):
                report.download('SECRET_TOKEN_XYZ', '12345')
            assert mock_open.call_count == 1

    def test_download_missing_reference_code_raises_flex_error(self):
        step1_xml = (
            b'<FlexStatementResponse>'
            b'<Status>Success</Status>'
            b'<Url>https://gdcdyn.interactivebrokers.com/get</Url>'
            b'</FlexStatementResponse>'
        )
        with patch('ib_insync.flexreport.urlopen',
                   return_value=_FakeResp(step1_xml)):
            with pytest.raises(FlexError, match='ReferenceCode'):
                FlexReport().download('SECRET_TOKEN_XYZ', '12345')

    def test_download_http_error_sanitizes_token(self):
        secret = 'SUPER_SECRET_TOKEN_99999'
        http_err = HTTPError(
            url=f'https://gdcdyn.interactivebrokers.com/req?t={secret}&q=1',
            code=503,
            msg='Service Unavailable',
            hdrs=email.message.Message(),
            fp=BytesIO(b''),
        )
        with patch('ib_insync.flexreport.urlopen', side_effect=http_err):
            with pytest.raises(FlexError) as exc_info:
                FlexReport().download(secret, '12345')
        assert '503' in str(exc_info.value)
        assert secret not in str(exc_info.value)

    def test_download_url_error_and_timeout_wrapped_without_token(self):
        secret = 'SUPER_SECRET_TOKEN_88888'
        with patch('ib_insync.flexreport.urlopen',
                   side_effect=URLError('connection timed out')):
            with pytest.raises(FlexError) as exc_info:
                FlexReport().download(secret, '12345')
        assert secret not in str(exc_info.value)

        with patch('ib_insync.flexreport.urlopen',
                   side_effect=TimeoutError('timed out')):
            with pytest.raises(FlexError) as exc_info:
                FlexReport().download(secret, '12345')
        assert secret not in str(exc_info.value)

    def test_download_malformed_xml_raises_flex_error(self):
        with patch('ib_insync.flexreport.urlopen',
                   return_value=_FakeResp(b'<<not valid xml>>')):
            with pytest.raises(FlexError, match='Failed to parse Flex XML'):
                FlexReport().download('tok123', '12345')

    def test_load_and_save_validate_path_and_xml(self, tmp_path):
        report = FlexReport()
        with pytest.raises(ValueError):
            report.load('   ')
        with pytest.raises(ValueError):
            report.save('   ')

        bad_file = tmp_path / 'bad.xml'
        bad_file.write_bytes(b'<broken')
        with pytest.raises(FlexError, match='Failed to parse Flex XML'):
            report.load(bad_file)


class TestClientConnectValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize('host,port,clientId,timeout', [
        ('', 7497, 1, 2.0),
        ('   ', 7497, 1, 2.0),
        ('127.0.0.1\n', 7497, 1, 2.0),
        ('127.0.0.1', 0, 1, 2.0),
        ('127.0.0.1', 65536, 1, 2.0),
        ('127.0.0.1', -1, 1, 2.0),
        ('127.0.0.1', True, 1, 2.0),
        ('127.0.0.1', 7497, -1, 2.0),
        ('127.0.0.1', 7497, False, 2.0),
        ('127.0.0.1', 7497, 1, -0.5),
        ('127.0.0.1', 7497, 1, True),
    ])
    async def test_connect_async_rejects_invalid_params(
            self, host, port, clientId, timeout):
        client = Client(MagicMock())
        with pytest.raises(ValueError):
            await client.connectAsync(host, port, clientId, timeout)


class TestModernAsyncioSupport:
    def test_get_loop_recovers_when_no_loop_or_closed_loop(self):
        asyncio.set_event_loop(None)
        loop = ibi.util.getLoop()
        try:
            assert isinstance(loop, asyncio.AbstractEventLoop)
            assert not loop.is_closed()
            loop.close()
            new_loop = ibi.util.getLoop()
            assert new_loop is not loop
            assert not new_loop.is_closed()
            loop = new_loop
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_wrapper_start_req_works_without_preexisting_loop(self):
        asyncio.set_event_loop(None)
        try:
            wrapper = Wrapper(MagicMock())
            fut = wrapper.startReq('testKey')
            assert isinstance(fut, asyncio.Future)
            assert not fut.done()
        finally:
            loop = ibi.util.getLoop()
            loop.close()
            asyncio.set_event_loop(None)

    def test_patch_asyncio_nested_run_with_timeout(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            ibi.util.patchAsyncio()

            async def inner():
                await asyncio.sleep(0.01)
                return 'ok'

            async def outer():
                return ibi.util.run(inner(), timeout=1.0)

            assert ibi.util.run(outer(), timeout=2.0) == 'ok'
        finally:
            loop.close()
            asyncio.set_event_loop(None)


class TestExamplesNoEval:
    @pytest.mark.parametrize('filename', [
        'examples/qt_ticker_table.py',
        'examples/tk.py',
    ])
    def test_example_has_no_eval_and_safe_parse_contract(self, filename):
        path = Path(__file__).resolve().parent.parent / filename
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != 'eval', f'eval() found in {filename}'

        # Extract and test parse_contract directly without importing GUI toolkits
        ns = {
            'ast': ast,
            'Contract': ibi.Contract,
            '_CONTRACT_TYPES': {
                'Forex': ibi.Forex,
                'Stock': ibi.Stock,
                'Option': ibi.Option,
            },
        }
        func_node = next(
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == 'parse_contract'
        )
        mod = ast.Module(body=[func_node], type_ignores=[])
        exec(compile(mod, str(path), 'exec'), ns)
        parse_contract = ns['parse_contract']

        c1 = parse_contract("Forex('EURUSD')")
        assert isinstance(c1, ibi.Forex) and c1.pair() == 'EURUSD'

        c2 = parse_contract("Stock('TSLA', 'SMART', 'USD')")
        assert isinstance(c2, ibi.Stock) and c2.symbol == 'TSLA'

        for payload in (
            "__import__('os').system('echo pwned')",
            "open('/etc/passwd')",
            "Stock(__import__('os').name)",
            "1 + 1",
        ):
            with pytest.raises((ValueError, SyntaxError)):
                parse_contract(payload)
