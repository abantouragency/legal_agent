"""Automated self-check for the legal bot before deploy.

Run: uv run python tests/selfcheck.py
Covers: syntax, welcome bolding, buy callback crash, analysis bold conversion,
buyer form + export, out-of-scope guard, global error handler presence.
Exits non-zero on failure so CI / pre-push hooks can block bad deploys.
"""
import os, sys, tempfile, types, importlib, ast, traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", dir=tempfile.gettempdir())
os.environ["STORE_PATH"] = _tmp.name

# ---- fake telegram ----
class FakeMsg:
    def __init__(self): self.sent = []
    async def reply_text(self, text, **kw): self.sent.append((text, kw)); return True
class FakeCallback:
    def __init__(self): self.data = "act:buy"; self.message = FakeMsg()
    async def answer(self): pass
class FakeUpdateCB:
    def __init__(self):
        self.callback_query = FakeCallback(); self.effective_message = self.callback_query.message
        self.message = None
        self.effective_user = types.SimpleNamespace(id=88112233, username="tester")
class FakeUpdateMsg:
    def __init__(self):
        self.message = FakeMsg(); self.effective_message = self.message
        self.effective_user = types.SimpleNamespace(id=88112233, username="tester")
class FakeCtx:
    chat_data = {}; user_data = {}
    def __init__(self): self.bot = types.SimpleNamespace()

results = []
def check(name, cond):
    results.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name)

try:
    # 1) syntax
    for f in ["bot.py", "lawyer_agent.py", "admin_panel.py", "brand.py", "receipt_verify.py"]:
        try:
            ast.parse(open(os.path.join(REPO, "src", f), encoding="utf-8").read())
            check(f"syntax {f}", True)
        except SyntaxError as e:
            check(f"syntax {f}: {e}", False)

    AP = importlib.import_module("admin_panel")
    AP.STORE_PATH = _tmp.name
    AP.ensure_user(88112233, handle="tester", admin_ids=[])
    bot = importlib.import_module("bot")
    law = open(os.path.join(REPO, "src", "lawyer_agent.py"), encoding="utf-8").read()
    bot_src = open(os.path.join(REPO, "src", "bot.py"), encoding="utf-8").read()

    import asyncio
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)

    # 2) bug 4: buy from callback
    up = FakeUpdateCB(); ctx = FakeCtx()
    try:
        loop.run_until_complete(bot.buy(up, ctx))
        check("BUG4 buy() no NoneType crash", len(up.effective_message.sent) > 0)
        check("BUG4 buy() Markdown", any(s[1].get("parse_mode")=="Markdown" for s in up.effective_message.sent))
        check("BUG4 buy() no literal **", all("**" not in s[0] for s in up.effective_message.sent))
    except Exception as e:
        check(f"BUG4 buy() crash: {e}", False)

    # 3) item 1: welcome bolds
    up2 = FakeUpdateMsg(); ctx2 = FakeCtx()
    try:
        loop.run_until_complete(bot.start(up2, ctx2))
        check("ITEM1 welcome Markdown", any(s[1].get("parse_mode")=="Markdown" for s in up2.message.sent))
        check("ITEM1 welcome no **", all("**" not in s[0] for s in up2.message.sent))
        check("ITEM1 welcome bolds بارِ حقوقی‌ات", any("*بارِ حقوقی‌ات*" in s[0] for s in up2.message.sent))
    except Exception as e:
        check(f"ITEM1 start() crash: {e}", False)

    # 4) item 5.2: prompt asks ** + analysis converts
    check("ITEM5.2 prompt **", "**📌 خلاصه موضوع**" in law)
    check("ITEM5.2 prompt no #", "# 📌 خلاصه موضوع" not in law)
    ran = bot_src[bot_src.find("async def _run_analysis"):bot_src.find("async def admin_cmd")]
    check("ITEM5.2 **->*", 'final_text.replace("**", "*")' in ran)
    check("ITEM5.2 analysis Markdown", 'parse_mode="Markdown"' in ran)

    # 5) item 7: buyer form + export
    AP.request_purchase(88112233, tier_id="m6", months=6)
    AP.save_buyer_info(88112233, full_name="علی", last_name="رضایی", mobile="09123456789", national_id="0012345678")
    AP.approve_purchase(88112233, months=6)
    buyers = AP.list_buyers()
    check("ITEM7 buyer stored", len(buyers) == 1 and buyers[0]["full_name"] == "علی")
    check("ITEM7 export text", "علی" in AP.export_buyers_text())
    check("ITEM7 export_cmd wired", 'CommandHandler("export", export_cmd)' in bot_src)
    check("ITEM7 admin-only + support link", "is_admin(update.effective_user.id)" in bot_src and "https://t.me/legal_agent_support" in bot_src)

    # 6) out-of-scope guard
    check("GUARD scope in prompt", "من یه دستیار هوشمند حقوقی هستم" in law)

    # 7) global error handler notifies admin
    check("GLOBAL error handler added", "_app.add_error_handler(_global_error)" in bot_src)
    check("GLOBAL notifies admin", "for adm in CFG.get(\"admin_ids\"" in bot_src)

except Exception as e:
    check(f"UNEXPECTED: {e}", False)
    traceback.print_exc()

passed = sum(1 for _, c in results if c)
total = len(results)
print(f"\nSELFCHECK: {passed}/{total} passed")
try: os.unlink(_tmp.name)
except Exception: pass
sys.exit(0 if passed == total else 1)
