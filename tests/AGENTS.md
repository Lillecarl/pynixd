# Test Conventions

## `@pytest.mark.asyncio`

Do **NOT** add `@pytest.mark.asyncio` markers to any test function. The conftest.py hook `pytest_collection_modifyitems` already auto-detects `async def` test functions and wraps them with `asyncio.timeout`. Adding the explicit marker is redundant.
