"""
Forwarding loader for root tests directory.
Loads static JSON fixtures under marketscope/tests/fixtures/ for unit testing.
"""

from __future__ import annotations
from typing import Any, Dict, List

try:
    from marketscope.tests.fixture_loader import load_test_fixtures, get_fixture_stock_candles
except ImportError:
    from fixture_loader import load_test_fixtures, get_fixture_stock_candles

__all__ = ["load_test_fixtures", "get_fixture_stock_candles"]

