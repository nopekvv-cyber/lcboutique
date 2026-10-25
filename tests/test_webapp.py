import asyncio
import hashlib
import hmac
import json
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import urlencode

from aiohttp.test_utils import TestClient, TestServer

from lcboutique.bot import LCBoutiqueBot
from lcboutique.config import Config
from lcboutique.storage import new_post
from lcboutique.webapp import WebApp, check_init_data

TOKEN = "123:secret"
OWNER, STRANGER = 7, 99


def init_data(user_id, auth_date=None, token=TOKEN):
    fields = {"auth_date": str(int(auth_date or time.time())), "user": json.dumps({"id": user_id, "first_name": "L"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_init_data_signature():
    assert check_init_data(init_data(OWNER), TOKEN)["id"] == OWNER
    assert check_init_data(init_data(OWNER, token="1:other"), TOKEN) is None  # semnătură falsă
    assert check_init_data(init_data(OWNER, auth_date=time.time() - 3 * 86400), TOKEN) is None  # expirat
    assert check_init_data("", TOKEN) is None


class FakeTelegram:
    id = 42

    def __init__(self):
        self.edits = []

    async def get_chat_member(self, chat, user):
        return SimpleNamespace(status="member" if user == OWNER and chat == -100 else "left")

    async def edit_message_caption(self, chat, message_id, caption, **kw):
        self.edits.append(caption)


def make_bot():
    cfg = Config(
        telegram_token=TOKEN, source_chat_id=-100, target_chat_id=-1002000, report_chat_id=-100,
        data_dir=tempfile.mkdtemp(),
    )
    bot = LCBoutiqueBot(cfg)
    bot.app = SimpleNamespace(bot=FakeTelegram())
    bot.storage.add_post(
        new_post(
            category="rochite",
            product=dict(
                category="rochite", title="Rochie midi", code="R-1", sizes="S-M", material="", composition="",
                colors=["negru"], description="", details=[], measurements=[], prices=[], profit_lei=150,
                photo_indices=[],
            ),
            price_lei=650, text_message_id=555, is_caption=True, message_ids=[555], media_group_ids=[],
            media=[{"kind": "photo", "file_id": "f", "preview_id": "p"}], thread_id=None, calc="700 грн × 0.5 …",
        )
    )
    return bot


async def call(bot, method, path, user=OWNER, body=None):
    async with TestClient(TestServer(WebApp(bot).app)) as client:
        headers = {"X-Init-Data": init_data(user)} if user else {}
        res = await client.request(method, path, headers=headers, json=body)
        return res.status, (await res.json() if res.content_type == "application/json" else await res.text())


def test_owner_lists_and_edits_posts():
    bot = make_bot()
    status, posts = asyncio.run(call(bot, "GET", "/api/posts?q=R-1"))
    assert status == 200 and posts[0]["title"] == "Rochie midi" and posts[0]["price_lei"] == 650

    body = {"title": "Rochie midi elegantă", "price_lei": 690, "colors": ["negru", " bej "], "category": "rochite"}
    status, result = asyncio.run(call(bot, "PUT", f"/api/posts/{posts[0]['id']}", body=body))
    assert status == 200 and result["price_lei"] == 690
    caption = bot.app.bot.edits[-1]
    assert "Rochie midi elegantă" in caption and "💰 Preț: 690 lei" in caption and "negru, bej" in caption


def test_stranger_and_missing_signature_are_rejected():
    bot = make_bot()
    assert asyncio.run(call(bot, "GET", "/api/posts", user=STRANGER))[0] == 403
    assert asyncio.run(call(bot, "GET", "/api/posts", user=None))[0] == 401


def test_invalid_edit_is_refused():
    bot = make_bot()
    status, result = asyncio.run(call(bot, "PUT", "/api/posts/1", body={"price_lei": 0}))
    assert status == 400 and bot.app.bot.edits == []


def test_settings_are_saved():
    bot = make_bot()
    status, meta = asyncio.run(call(bot, "PUT", "/api/settings", body={"uah_to_lei": "0,45", "shipping_lei": 120}))
    assert status == 200 and meta["settings"]["uah_to_lei"] == 0.45 and meta["settings"]["shipping_lei"] == 120
    assert bot.pricing.uah_to_lei == 0.45


def test_index_page_is_public():
    bot = make_bot()
    status, html = asyncio.run(call(bot, "GET", "/", user=None))
    assert status == 200 and "telegram-web-app.js" in html
