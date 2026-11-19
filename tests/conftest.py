import pytest_asyncio

import ib_insync as ibi


@pytest_asyncio.fixture(scope='session', loop_scope='session')
async def ib():
    ib = ibi.IB()
    await ib.connectAsync()
    yield ib
    ib.disconnect()
