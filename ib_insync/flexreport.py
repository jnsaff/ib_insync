"""Access to account statement webservice."""

import logging
import os
import time
import xml.etree.ElementTree as et
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

from ib_insync import util
from ib_insync.objects import DynamicObject

_logger = logging.getLogger('ib_insync.flexreport')

_FLEX_SEND_REQUEST_URL = (
    'https://gdcdyn.interactivebrokers.com'
    '/Universal/servlet/FlexStatementService.SendRequest')

_ALLOWED_FLEX_DOMAINS = (
    'interactivebrokers.com',
    'interactivebrokers.co.uk',
    'interactivebrokers.eu',
    'interactivebrokers.ca',
    'interactivebrokers.com.hk',
    'interactivebrokers.com.au',
    'interactivebrokers.com.sg',
    'interactivebrokers.co.jp',
    'interactivebrokers.ie',
    'interactivebrokers.hu',
    'interactivebrokers.in',
)


class FlexError(Exception):
    pass


def _validate_param(name: str, value: str | int) -> str:
    """Validate and normalize a Flex Web Service parameter."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f'{name} must be a non-empty string or integer')
    text = str(value).strip()
    if not text or any(ch.isspace() or ord(ch) < 32 for ch in text):
        raise ValueError(f'Invalid {name}: must not be empty or contain '
                         'whitespace/control characters')
    return text


def _validate_flex_url(url: str) -> str:
    """Validate that a Flex download URL uses HTTPS on an allowed IB host."""
    if not isinstance(url, str):
        raise FlexError('Invalid Flex statement URL')
    cleaned = url.strip()
    if not cleaned or any(ch.isspace() or ord(ch) < 32 for ch in cleaned):
        raise FlexError('Invalid Flex statement URL')
    parsed = urlparse(cleaned)
    if parsed.scheme != 'https':
        raise FlexError(
            f'Refusing non-HTTPS Flex URL scheme: {parsed.scheme!r}')
    if parsed.username or parsed.password:
        raise FlexError(
            'Refusing Flex statement URL with embedded credentials')
    host = (parsed.hostname or '').lower()
    if not host or not any(
            host == domain or host.endswith('.' + domain)
            for domain in _ALLOWED_FLEX_DOMAINS):
        raise FlexError(
            f'Refusing Flex statement URL with untrusted host: {host!r}')
    if parsed.query or parsed.fragment:
        raise FlexError(
            'Refusing Flex statement URL with unexpected query or fragment')
    return cleaned


def _fetch_url(url: str, timeout: float) -> bytes:
    """Fetch a validated HTTPS Flex URL without leaking sensitive tokens."""
    try:
        with urlopen(url, timeout=timeout) as resp:
            data: bytes = resp.read()
            return data
    except HTTPError as err:
        code, reason = err.code, err.reason
        err.close()
        raise FlexError(
            f'Flex request failed with HTTP {code}: {reason}'
        ) from None
    except URLError as err:
        raise FlexError(
            f'Flex request failed: {err.reason}') from None
    except OSError as err:
        raise FlexError(f'Flex request failed: {err}') from None


def _parse_xml(data: bytes) -> et.Element:
    """Parse XML bytes and wrap parse failures in FlexError."""
    try:
        return et.fromstring(data)
    except et.ParseError as err:
        raise FlexError(f'Failed to parse Flex XML response: {err}') from err


class FlexReport:
    """
    To obtain a token:

    * Login to web portal
    * Go to Settings
    * Click on "Configure Flex Web Service"
    * Generate token
    """

    data: bytes
    root: et.Element

    def __init__(
            self, token: str | int | None = None,
            queryId: str | int | None = None,
            path: str | os.PathLike[str] | None = None) -> None:
        """
        Download a report by giving a valid ``token`` and ``queryId``,
        or load from file by giving a valid ``path``.
        """
        self.data = b''
        self.root = et.Element('FlexQueryResponse')
        if token is not None or queryId is not None:
            if token is None or queryId is None:
                raise ValueError('Both token and queryId must be provided')
            self.download(token, queryId)
        elif path is not None:
            self.load(path)

    def topics(self) -> set[str]:
        """Get the set of topics that can be extracted from this report."""
        return set(node.tag for node in self.root.iter() if node.attrib)

    def extract(self, topic: str, parseNumbers: bool = True) -> list[Any]:
        """
        Extract items of given topic and return as list of objects.

        The topic is a string like TradeConfirm, ChangeInDividendAccrual,
        Order, etc.
        """
        cls = type(topic, (DynamicObject,), {})
        results = [cls(**node.attrib) for node in self.root.iter(topic)]
        if parseNumbers:
            for obj in results:
                d = obj.__dict__
                for k, v in d.items():
                    with suppress(ValueError):
                        d[k] = float(v)
                        d[k] = int(v)
        return results

    def df(self, topic: str, parseNumbers: bool = True) -> Any:
        """Same as extract but return the result as a pandas DataFrame."""
        return util.df(self.extract(topic, parseNumbers))

    def download(
            self, token: str | int, queryId: str | int,
            timeout: float = 30, maxRetries: int = 60) -> None:
        """Download report for the given ``token`` and ``queryId``."""
        token_str = _validate_param('token', token)
        query_id_str = _validate_param('queryId', queryId)
        if isinstance(timeout, bool) or timeout <= 0:
            raise ValueError('timeout must be a positive number')
        if isinstance(maxRetries, bool) or not isinstance(maxRetries, int) \
                or maxRetries < 1:
            raise ValueError('maxRetries must be a positive integer')

        params = urlencode({'t': token_str, 'q': query_id_str, 'v': '3'})
        url = f'{_FLEX_SEND_REQUEST_URL}?{params}'
        data = _fetch_url(url, timeout=timeout)

        root = _parse_xml(data)
        elem = root.find('Status')
        if elem is not None and elem.text == 'Success':
            code_elem = root.find('ReferenceCode')
            code = (code_elem.text or '').strip() \
                if code_elem is not None else ''
            if not code:
                raise FlexError('Missing ReferenceCode in Flex response')
            url_elem = root.find('Url')
            raw_url = url_elem.text if url_elem is not None else ''
            baseUrl = _validate_flex_url(raw_url or '')
            _logger.info('Statement is being prepared...')
        else:
            elem = root.find('ErrorCode')
            errorCode = elem.text if elem is not None else ''
            elem = root.find('ErrorMessage')
            errorMsg = elem.text if elem is not None else ''
            raise FlexError(f'{errorCode}: {errorMsg}')

        for _ in range(maxRetries):
            time.sleep(1)
            pollParams = urlencode({'q': code, 't': token_str})
            url = f'{baseUrl}?{pollParams}'
            self.data = _fetch_url(url, timeout=timeout)
            self.root = _parse_xml(self.data)
            if len(self.root) > 0 and self.root[0].tag == 'code':
                msg = self.root[0].text
                if msg and msg.startswith('Statement generation in progress'):
                    _logger.info('still working...')
                    continue
                else:
                    raise FlexError(msg)
            _logger.info('Statement retrieved.')
            return
        raise FlexError('Timed out waiting for statement generation')

    def load(self, path: str | os.PathLike[str]) -> None:
        """Load report from XML file."""
        file_path = Path(path)
        if not str(path).strip():
            raise ValueError('path must not be empty')
        with open(file_path, 'rb') as f:
            data = f.read()
        self.root = _parse_xml(data)
        self.data = data

    def save(self, path: str | os.PathLike[str]) -> None:
        """Save report to XML file."""
        file_path = Path(path)
        if not str(path).strip():
            raise ValueError('path must not be empty')
        with open(file_path, 'wb') as f:
            f.write(self.data)


if __name__ == '__main__':
    util.logToConsole()
    token = os.environ.get('IB_FLEX_TOKEN')
    queryId = os.environ.get('IB_FLEX_QUERY_ID')
    if token and queryId:
        report = FlexReport(token, queryId)
        print(report.topics())
        trades = report.extract('Trade')
        print(trades)
