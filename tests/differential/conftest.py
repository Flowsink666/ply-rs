"""Session fixtures for pinned PLY and SLY reference packages."""

import pytest

from tests.fixtures.reference import load_ply_reference, load_reference


@pytest.fixture(scope="session")
def upstream_ply():
    return load_ply_reference()


@pytest.fixture(scope="session")
def upstream_sly():
    return load_reference("sly")
