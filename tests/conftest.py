"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from contextloom.db import create_index


@pytest.fixture
def project(tmp_path):
    """A temporary project directory with an initialized contextloom index."""
    create_index(tmp_path)
    return tmp_path
