"""
SAJIM Quant — Strategy Catalog Registry (Single Source of Truth)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Any

CATALOG_PATH = Path(__file__).resolve().parent / "strategy_catalog.json"


def load_strategy_catalog() -> List[Dict[str, Any]]:
    """Loads the unified strategy catalog from JSON."""
    if not CATALOG_PATH.exists():
        return []
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_strategy_by_id(strategy_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves strategy metadata by ID."""
    catalog = load_strategy_catalog()
    for item in catalog:
        if item.get("id") == strategy_id:
            return item
    return None


get_catalog_item = get_strategy_by_id
