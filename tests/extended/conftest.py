"""Optional reference fixture for extended recovery comparisons."""

import pytest

from tests.fixtures.reference import load_ply_reference


@pytest.fixture(scope="session")
def upstream_ply():
    return load_ply_reference()
