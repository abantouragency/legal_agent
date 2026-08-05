"""
receipt_verify.py
=================
Intelligent verification of a bank-transfer receipt screenshot using OpenAI
vision (gpt-4o-mini supports images). The bot reads the transferred amount and
date from the picture and decides whether it matches the expected subscription
price. This is best-effort OCR — a determined user can fake a screenshot, so
the caller should still forward the image to an admin for oversight and allow
/revoke.

Requires OPENAI_API_KEY (already used by the bot) and a vision-capable model.
"""

from __future__ import annotations
import base64
import json
import os
import re


def _encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def verify_receipt_image(image_path: str, expected_amount_toman: int,
                         openai_key: str, model: str = "gpt-4o-mini") -> dict:
    """Send the receipt image to OpenAI vision; return a verdict dict.

    Returns:
        {"ok": bool, "amount": int|None, "date": str|None,
         "confidence": str, "reason": str}
    ok=True only when amount is found, confidence is not low, and amount is
    within ±8% of expected_amount_toman.
    """
    from openai import OpenAI
    client = OpenAI(api_key=openai_key, timeout=60, max_retries=2)

    b64 = _encode_image(image_path)
    prompt = (
        "This is a bank transfer / card-to-card receipt screenshot, likely in "
        "Persian (Farsi). Extract the transferred amount and the date.\n"
        "Rules:\n"
        "- Report the amount in TOMAN (تومان). If the receipt shows RIAL, divide by 10.\n"
        "- Ignore the sender/receiver card numbers; we only care about the transferred sum.\n"
        "- Respond with ONLY valid JSON, no prose:\n"
        '{"amount_toman": <integer or null>, "date": "<YYYY-MM-DD or null>", '
        '"confidence": "high|medium|low"}'
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        max_tokens=300,
    )
    raw = resp.choices[0].message.content or ""
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {"ok": False, "amount": None, "date": None,
                "confidence": "low", "reason": "no json in vision response"}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"ok": False, "amount": None, "date": None,
                "confidence": "low", "reason": "invalid json"}

    amount = data.get("amount_toman")
    conf = str(data.get("confidence", "low")).lower()
    if amount is None or conf == "low":
        return {"ok": False, "amount": amount, "date": data.get("date"),
                "confidence": conf, "reason": "amount missing or low confidence"}
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return {"ok": False, "amount": None, "date": data.get("date"),
                "confidence": conf, "reason": "amount not integer"}

    # tolerance ±8%
    lo = expected_amount_toman * 0.92
    hi = expected_amount_toman * 1.08
    if lo <= amount <= hi:
        return {"ok": True, "amount": amount, "date": data.get("date"),
                "confidence": conf, "reason": "amount matches tier price"}
    return {"ok": False, "amount": amount, "date": data.get("date"),
            "confidence": conf,
            "reason": f"amount {amount:,} not within ±8% of {expected_amount_toman:,}"}
