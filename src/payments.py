"""
payments.py
===========
ZarinPal payment integration for the legal-agent bot.

Uses ZarinPal's REST API directly via `requests` (no extra dependency).
Two environments:
  - Sandbox (for testing):  https://sandbox.zarinpal.com    merchant = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  - Production:             https://api.zarinpal.com

Required env var (set in Render dashboard):
  ZARINPAL_MERCHANT   your merchant id (sandbox or production)

Flow:
  1. request_payment(amount_toman, description, callback_url) ->
       returns (authority, payment_url) or raises on error.
  2. User opens payment_url, pays on ZarinPal.
  3. ZarinPal redirects to callback_url?Authority=XXX
  4. verify_payment(authority, amount_toman) -> bool (True if payment ok).

The bot wires this into /buy and a callback handler.
"""

from __future__ import annotations
import os
import requests

ZARINPAL_SANDBOX_BASE = "https://sandbox.zarinpal.com/pg/rest/WebGate"
ZARINPAL_PROD_BASE = "https://api.zarinpal.com/pg/rest/WebGate"

# Flip to production by setting ZARINPAL_ENV=production (or just use a prod merchant).
USE_SANDBOX = os.environ.get("ZARINPAL_ENV", "sandbox").lower() != "production"


def _base() -> str:
    return ZARINPAL_SANDBOX_BASE if USE_SANDBOX else ZARINPAL_PROD_BASE


def merchant_id() -> str:
    return os.environ.get("ZARINPAL_MERCHANT", "")


def request_payment(amount_toman: int, description: str, callback_url: str) -> tuple[str, str]:
    """Create a ZarinPal payment. Returns (authority, payment_url)."""
    m = merchant_id()
    if not m:
        raise RuntimeError("ZARINPAL_MERCHANT not set in environment.")
    payload = {
        "merchant_id": m,
        "amount": int(amount_toman) * 10,  # ZarinPal expects RIAL
        "callback_url": callback_url,
        "description": description,
        "metadata": {"source": "legal_agent_bot"},
    }
    resp = requests.post(f"{_base()}/PaymentRequest.json",
                         json=payload, timeout=30)
    data = resp.json()
    if data.get("Status") != 100:
        raise RuntimeError(f"ZarinPal PaymentRequest failed: {data}")
    authority = data["Authority"]
    prefix = "https://sandbox.zarinpal.com/pg/StartPay/" if USE_SANDBOX else "https://www.zarinpal.com/pg/StartPay/"
    return authority, prefix + authority


def verify_payment(authority: str, amount_toman: int) -> bool:
    """Verify a payment after ZarinPal redirects back. Returns True if paid."""
    m = merchant_id()
    if not m:
        raise RuntimeError("ZARINPAL_MERCHANT not set in environment.")
    payload = {
        "merchant_id": m,
        "amount": int(amount_toman) * 10,  # RIAL
        "authority": authority,
    }
    resp = requests.post(f"{_base()}/PaymentVerification.json",
                         json=payload, timeout=30)
    data = resp.json()
    # Status 100 + success code 1 means verified
    return data.get("Status") == 100 and data.get("code") == 1
