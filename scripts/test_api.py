"""Standalone test of the EVC-net API client, without Home Assistant.

Usage (PowerShell):
    $env:EVCNET_EMAIL = "you@example.com"
    $env:EVCNET_PASSWORD = "..."
    .venv\\Scripts\\python scripts\\test_api.py

Only read-only queries are executed. Requires: pip install aiohttp
"""
import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys

# Load api.py directly so the Home Assistant parts of the package are not imported
API_PATH = Path(__file__).resolve().parent.parent / "custom_components" / "evcnet" / "api.py"
spec = importlib.util.spec_from_file_location("evcnet_api", API_PATH)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)

import aiohttp  # noqa: E402


def show(title: str, value) -> None:
    print(f"\n=== {title}")
    print(json.dumps(value, indent=2, ensure_ascii=False))


async def main() -> int:
    logging.basicConfig(level=logging.DEBUG if os.environ.get("EVCNET_DEBUG") else logging.INFO)
    email = os.environ.get("EVCNET_EMAIL")
    password = os.environ.get("EVCNET_PASSWORD")
    if not email or not password:
        print("Set EVCNET_EMAIL and EVCNET_PASSWORD first.")
        return 1

    async with aiohttp.ClientSession() as session:
        client = api.EvcNetApiClient(email, password, session)
        if not await client.authenticate():
            print("Login FAILED")
            return 1
        print("Login OK")

        stations = await client.get_charge_stations()
        show("Charge stations", stations)
        show("Active transaction", await client.get_active_transaction())
        transactions, has_more = await client.get_transactions(0, 5)
        show(f"Last 5 transactions (more: {has_more})", transactions)
        show("Cards", await client.get_cards())

        all_tx = await client.get_all_transactions()
        total_wh = sum(t.get("energyDelivered") or 0 for t in all_tx)
        print(f"\n{len(all_tx)} transactions in total, {total_wh / 1000:.1f} kWh")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
