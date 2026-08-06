"""
admin_panel.py
==============
Sales/access dashboard logic for the legal-agent bot.

Data model (stored as JSON in data/store.json):
  {
    "users": {
       "<user_id>": {
           "name": "...", "phone": "...", "handle": "...",
           "access": "trial" | "paid" | "admin",
           "trial_used": false,
           "purchase_pending": false,      # requested to buy, awaiting approval
           "paid_at": "2026-08-04" | null,
           "analyses_done": 0,
           "joined_at": "2026-08-04"
       }
    }
  }

All functions are pure-python + file IO (no network) so they can be unit
tested offline. The bot layer calls these.
"""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_PATH = os.path.join(PROJECT_ROOT, "data", "store.json")


# --------------------------------------------------------------------------
# low-level store
# --------------------------------------------------------------------------
def _load() -> dict:
    if not os.path.exists(STORE_PATH):
        return {"users": {}}
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {"users": {}}


def _save(store: dict):
    os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as fh:
        json.dump(store, fh, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# user registration / access
# --------------------------------------------------------------------------
def ensure_user(user_id: int, name: str = "—", phone: str = "—",
                handle: str = "—", admin_ids: Optional[list[int]] = None) -> dict:
    """Create the user record if missing; return it."""
    store = _load()
    uid = str(user_id)
    if uid not in store["users"]:
        access = "admin" if (admin_ids and user_id in admin_ids) else "trial"
        store["users"][uid] = {
            "name": name, "phone": phone, "handle": handle,
            "access": access, "trial_used": False,
            "trial_date": None,        # YYYY-MM-DD of last counted free question
            "trial_count": 0,          # free questions used today (resets daily)
            "tier_id": None, "tier_months": 0, "paid_until": None,
            "purchase_pending": False, "purchase_tier": None,
            "paid_at": None,
            "analyses_done": 0, "joined_at": str(date.today()),
        }
        _save(store)
    return store["users"][uid]


def get_user(user_id: int) -> Optional[dict]:
    return _load()["users"].get(str(user_id))


def has_access(user_id: int, admin_ids: Optional[list[int]] = None) -> bool:
    u = get_user(user_id)
    if u is None:
        return False
    if admin_ids and user_id in admin_ids:
        return True
    return u["access"] in ("paid", "admin")


def daily_trial_limit() -> int:
    return 3  # item 5: free tier = 3 questions per day


def consume_trial(user_id: int) -> bool:
    """If user is on the free tier and hasn't used today's 3 questions yet,
    count one and return True. Resets automatically each calendar day.

    Returns True if a free analysis was just consumed (allowed).
    """
    from brand import FREE_DAILY_LIMIT
    store = _load()
    u = store["users"].get(str(user_id))
    if not u:
        return False
    if u["access"] in ("paid", "admin"):
        return True  # paid users are not limited
    today = str(date.today())
    # reset counter if it's a new day
    if u.get("trial_date") != today:
        u["trial_date"] = today
        u["trial_count"] = 0
        u["trial_used"] = False
    if u.get("trial_count", 0) >= FREE_DAILY_LIMIT:
        return False
    u["trial_count"] = u.get("trial_count", 0) + 1
    u["trial_used"] = True
    _save(store)
    return True


def trial_remaining(user_id: int) -> int:
    """How many free questions left today (for messaging)."""
    from brand import FREE_DAILY_LIMIT
    store = _load()
    u = store["users"].get(str(user_id))
    if not u:
        return FREE_DAILY_LIMIT
    today = str(date.today())
    if u.get("trial_date") != today:
        return FREE_DAILY_LIMIT
    return max(0, FREE_DAILY_LIMIT - u.get("trial_count", 0))


def is_paid(user_id: int) -> bool:
    """True if the user is on a paid (or admin) plan (unlimited)."""
    store = _load()
    u = store["users"].get(str(user_id))
    if not u:
        return False
    return u["access"] in ("paid", "admin")


def incr_analyses(user_id: int):
    store = _load()
    u = store["users"].get(str(user_id))
    if u:
        u["analyses_done"] = u.get("analyses_done", 0) + 1
        _save(store)


# --------------------------------------------------------------------------
# purchase flow
# --------------------------------------------------------------------------
def request_purchase(user_id: int, tier_id: Optional[str] = None,
                     months: int = 0) -> bool:
    """User asks to buy. Flags pending + records chosen tier. Returns True if
    newly requested (so the caller only messages on first request)."""
    store = _load()
    u = store["users"].get(str(user_id))
    if not u:
        return False
    if u["access"] in ("paid", "admin"):
        return False
    if u["purchase_pending"]:
        return False
    u["purchase_pending"] = True
    u["purchase_tier"] = tier_id
    u["tier_months"] = months
    _save(store)
    return True


def approve_purchase(user_id: int, admin_ids: Optional[list[int]] = None,
                     months: Optional[int] = None) -> bool:
    """Admin approves -> user becomes 'paid'. If months given (or stored tier
    months), set paid_until = today + months. Otherwise default 1 month."""
    from datetime import date as _date, timedelta
    store = _load()
    u = store["users"].get(str(user_id))
    if not u:
        return False
    if months is None:
        months = u.get("tier_months") or 1
    u["access"] = "paid"
    u["purchase_pending"] = False
    u["paid_at"] = str(_date.today())
    u["paid_until"] = str(_date.today() + timedelta(days=30 * months))
    _save(store)
    return True


def revoke_access(user_id: int) -> bool:
    """Downgrade a paid user back to trial (e.g. refund/expiry)."""
    store = _load()
    u = store["users"].get(str(user_id))
    if not u:
        return False
    if u["access"] == "admin":
        return False  # never revoke admin
    u["access"] = "trial"
    u["purchase_pending"] = False
    u["paid_at"] = None
    _save(store)
    return True


# --------------------------------------------------------------------------
# dashboard views
# --------------------------------------------------------------------------
def list_pending() -> list[dict]:
    store = _load()
    return [{"user_id": int(uid), **u} for uid, u in store["users"].items()
            if u.get("purchase_pending")]


def stats() -> dict:
    store = _load()
    users = list(store["users"].values())
    return {
        "total": len(users),
        "paid": sum(1 for u in users if u["access"] == "paid"),
        "trial": sum(1 for u in users if u["access"] == "trial"),
        "admin": sum(1 for u in users if u["access"] == "admin"),
        "pending": sum(1 for u in users if u.get("purchase_pending")),
        "total_analyses": sum(u.get("analyses_done", 0) for u in users),
    }


def render_dashboard(admin_ids: Optional[list[int]] = None) -> str:
    s = stats()
    pending = list_pending()
    lines = [
        "📊 داشبورد فروش و دسترسی",
        "━━━━━━━━━━━━━━━━",
        f"👥 کل کاربران: {s['total']}",
        f"✅ پرداخت‌شده (نامحدود): {s['paid']}",
        f"🎁 ترایال: {s['trial']}",
        f"🛡 ادمین: {s['admin']}",
        f"⏳ در انتظار تایید خرید: {s['pending']}",
        f"📈 تعداد تحلیل‌های انجام‌شده: {s['total_analyses']}",
    ]
    if pending:
        lines.append("\n⏳ درخواست‌های خرید:")
        for p in pending:
            lines.append(f"  • ID {p['user_id']} | @{p.get('handle','-')} | {p.get('name','-')}")
        lines.append("\nبرای تایید: /approve <ID>")
    else:
        lines.append("\n✨ در حال حاضراحت درخواست خرید جدیدی نیست.")
    return "\n".join(lines)
