import pytest

from tests import config, helpers


@pytest.fixture(autouse=True)
async def _require_running_instance():
    """Skip (not fail) integration tests when no NeuroFlow instance is reachable."""
    if not await helpers.server_is_up():
        pytest.skip(f"NeuroFlow not reachable at {config.BASE_URL}")
