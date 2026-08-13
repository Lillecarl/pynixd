Investigate a way to automatically wrap all test functions in "async with asyncio.timeout(N)" that is 5 seconds lower than the marked pytest timeout. asyncio timeouts give more useful information and let testing continue while the asyncio.timeout is more of a "nuclear option".

## Result
Implemented a `pytest_collection_modifyitems` hook in `tests/conftest.py` that automatically wraps all async tests in an `asyncio.timeout` block. The timeout is dynamically calculated to be 5 seconds shorter than the effective `pytest-timeout`, providing rich Python tracebacks before the process is killed.
