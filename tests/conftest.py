from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    """Load a captured API response. These are real bodies, not hand-written."""
    return json.loads((FIXTURES / name).read_text())


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def search_rtx() -> Any:
    return load("search_rtx5070.json")


@pytest.fixture
def search_mixed() -> Any:
    return load("search_mixed_kinds.json")


@pytest.fixture
def search_empty() -> Any:
    return load("search_empty.json")


@pytest.fixture
def archive_iphone() -> Any:
    return load("archive_iphone16pro.json")


@pytest.fixture
def deal_detail() -> Any:
    return load("deal_detail.json")


@pytest.fixture
def deal_negative() -> Any:
    return load("deal_detail_negative_temp.json")


@pytest.fixture
def deal_comments() -> Any:
    return load("deal_comments.json")


@pytest.fixture
def stores_search() -> Any:
    return load("stores_search.json")


@pytest.fixture
def feed_hottest() -> Any:
    return load("feed_hottest.json")
