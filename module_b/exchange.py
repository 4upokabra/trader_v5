"""
Shared CCXT exchange instance for Module B.
OKX EU — swap (perpetual) + spot in one client.
"""
from __future__ import annotations

import os

import ccxt.async_support as ccxt


def create_exchange() -> ccxt.okx:
    return ccxt.okx({
        "apiKey": os.environ.get("OKX_API_KEY", ""),
        "secret": os.environ.get("OKX_API_SECRET", ""),
        "password": os.environ.get("OKX_PASSPHRASE", ""),
        "options": {
            "defaultType": "spot",
        },
        "enableRateLimit": True,
    })


def create_swap_exchange() -> ccxt.okx:
    return ccxt.okx({
        "apiKey": os.environ.get("OKX_API_KEY", ""),
        "secret": os.environ.get("OKX_API_SECRET", ""),
        "password": os.environ.get("OKX_PASSPHRASE", ""),
        "options": {
            "defaultType": "swap",
        },
        "enableRateLimit": True,
    })
