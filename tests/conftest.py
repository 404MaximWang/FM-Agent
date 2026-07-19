import sys
from pathlib import Path

import pytest

# Make FM-Agent's src/ importable from the repo root.
REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.mock_backend import FixtureBackend
from tests.helpers import DEFAULT_SCENARIO


@pytest.fixture
def scenario():
    """Default declarative test scenario."""
    fixture_dir = DEFAULT_SCENARIO["fixture_dir"]
    if not DEFAULT_SCENARIO["project_src"].is_dir() or not DEFAULT_SCENARIO["golden"].is_dir():
        pytest.skip(
            "Replay fixture is not available. Set FM_AGENT_REPLAY_FIXTURE "
            f"or place it at {fixture_dir}."
        )
    return DEFAULT_SCENARIO


@pytest.fixture
def mock_backend(scenario):
    """Fixture-based backend for deterministic integration tests."""
    return FixtureBackend(scenario)
