import asyncio
import tempfile
from types import SimpleNamespace

from lcboutique.bot import LCBoutiqueBot
from lcboutique.config import Config
from lcboutique.storage import new_post

SOURCE, TARGET, BOT_ID = -100, -1002000, 42
PRODUCT = dict(
    category="costume_tricotate", title="Costum din tricot", code="1390", sizes="42-48", material="tricot",
    composition="", colors=["negru", "roz"], description="", details=[], measurements=[], prices=[],
    profit_lei=150, photo_indices=[],
)
OLD_CAPTION = "<b>Costum</b>\n\nCod/Model: 1390\n💰 Preț: 980 lei\n\n📩 Comandă pe Instagram: ..."


class FakeTelegram:
    id = BOT_ID

    def __init__(self, status="administrator"):
        self.edits, self.deleted, self.sent, self.status = [], [], [], status

    async def edit_message_caption(self, chat, message_id, caption, **kw):
        self.edits.append((chat, message_id, caption))

    async def edit_message_text(self, text, chat, message_id, **kw):
        self.edits.append((chat, message_id, text))

    async def get_chat_member(self, chat, user):
        return SimpleNamespace(status=self.status)

    async def delete_messages(self, chat, ids):
        self.deleted += list(ids)

    async def send_media_group(self, chat, items, **kw):
        self.sent.append(kw)
        return [SimpleNamespace(message_id=900 + i, media_group_id="new") for i in range(len(items))]


def make(status="administrator", topics=""):
    cfg = Config(
        telegram_token="1:a", source_chat_id=SOURCE, target_chat_id=TARGET, report_chat_id=SOURCE,
        data_dir=tempfile.mkdtemp(), topics=topics,
    )
    bot = LCBoutiqueBot(cfg)
    tg = FakeTelegram(status)
    bot.app = SimpleNamespace(bot=tg)
    return bot, tg


def stored(bot, **kw):
    data = dict(
        category="costume_tricotate", product=dict(PRODUCT), price_lei=980, text_message_id=555, is_caption=True,
        message_ids=[550, 551, 555], media_group_ids=["album1"],
        media=[{"kind": "photo", "file_id": f"f{i}", "preview_id": ""} for i in range(3)], thread_id=None,
    )
    data.update(kw)
    return bot.storage.add_post(new_post(**data))


def command(text, chat_id, reply, user=7):
    out = []

    async def reply_text(t, **kw):
        out.append(t)

    async def delete():
        out.append("DELETED")

    msg = SimpleNamespace(
        text=text, reply_to_message=reply, reply_text=reply_text, delete=delete,
        from_user=SimpleNamespace(id=user), sender_chat=None,
    )
    update = SimpleNamespace(effective_message=msg, effective_chat=SimpleNamespace(id=chat_id, username=None))
    return update, out


def bot_message(message_id=555, caption=OLD_CAPTION, group=None):
    return SimpleNamespace(
        message_id=message_id, from_user=SimpleNamespace(id=BOT_ID), media_group_id=group,
        caption=caption, caption_html=caption, text=None, text_html=None,
    )


def test_admin_replies_to_post_in_sales_group():
    bot, tg = make()
    post = stored(bot)
    update, out = command("/pret 750", TARGET, bot_message())
    asyncio.run(bot.on_price(update, None))
    assert tg.edits[0][1] == 555 and "💰 Preț: 750 lei" in tg.edits[0][2]
    assert bot.storage.get_post(post.id).price_lei == 750
    assert out == ["DELETED"]  # comanda dispare din grup


def test_non_admin_cannot_change_price():
    bot, tg = make(status="member")
    stored(bot)
    update, out = command("/pret 1", TARGET, bot_message())
    asyncio.run(bot.on_price(update, None))
    assert tg.edits == [] and out == []


def test_reply_to_other_photo_of_album_finds_post():
    bot, tg = make()
    stored(bot)
    update, out = command("/preț 1 190", TARGET, bot_message(message_id=550, caption=None, group="album1"))
    asyncio.run(bot.on_price(update, None))
    assert tg.edits[0][1] == 555 and "1190 lei" in tg.edits[0][2]


def test_reply_to_confirmation_in_private_group():
    bot, tg = make()
    stored(bot)
    confirmation = SimpleNamespace(text="✅ Publicat …\nhttps://t.me/c/2000/555\nAlt preț? …")
    update, out = command("/pret 690", SOURCE, confirmation)
    asyncio.run(bot.on_price(update, None))
    assert "690 lei" in tg.edits[0][2]
    assert out == ["✅ Prețul a fost schimbat: 690 lei"]


def test_old_post_without_database_record_still_editable():
    bot, tg = make()
    update, out = command("/pret 700", TARGET, bot_message(message_id=77))
    asyncio.run(bot.on_price(update, None))
    assert tg.edits == [(TARGET, 77, OLD_CAPTION.replace("980 lei", "700 lei"))]


def test_edit_all_fields_and_move_topic():
    bot, tg = make(topics="costume_tricotate=10;costume_sport=20")
    post = stored(bot, thread_id=10)
    product = dict(post.product, title="Costum sport", colors=["gri"], sizes="S-M")
    updated = asyncio.run(bot.edit_post(post, product, 890, "costume_sport"))
    assert tg.sent[0]["message_thread_id"] == 20  # republicat în topicul nou
    assert tg.deleted == [550, 551, 555]  # mesajele vechi șterse
    assert updated.thread_id == 20 and updated.text_message_id == 900
    saved = bot.storage.get_post(post.id)
    assert saved.category == "costume_sport" and saved.price_lei == 890 and saved.product["colors"] == ["gri"]


def test_delete_post():
    bot, tg = make()
    post = stored(bot)
    asyncio.run(bot.delete_post(post))
    assert tg.deleted == [550, 551, 555] and bot.storage.get_post(post.id) is None


def test_settings_override_pricing():
    bot, _ = make()
    bot.storage.set_setting("uah_to_lei", "0.45")
    bot.storage.set_setting("shipping_lei", "120")
    assert bot.pricing.uah_to_lei == 0.45 and bot.pricing.shipping_lei == 120


def test_post_link_format():
    bot, _ = make()
    assert bot._post_link(555) == "https://t.me/c/2000/555"
