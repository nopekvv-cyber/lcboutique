import asyncio
from types import SimpleNamespace

from lcboutique.bot import LCBoutiqueBot, Published
from lcboutique.config import Config

SOURCE, TARGET, BOT_ID = -100, -1002000, 42
CAPTION = "<b>Costum</b>\n\nCod/Model: 1390\n💰 Preț: 980 lei\n\n📩 Comandă pe Instagram: ..."


class FakeTelegram:
    id = BOT_ID

    def __init__(self, status="administrator"):
        self.edits, self.status = [], status

    async def edit_message_caption(self, chat, message_id, caption, **kw):
        self.edits.append((chat, message_id, caption))

    async def edit_message_text(self, text, chat, message_id, **kw):
        self.edits.append((chat, message_id, text))

    async def get_chat_member(self, chat, user):
        return SimpleNamespace(status=self.status)


def make(status="administrator"):
    cfg = Config(telegram_token="1:a", source_chat_id=SOURCE, target_chat_id=TARGET, report_chat_id=SOURCE)
    bot = LCBoutiqueBot(cfg)
    tg = FakeTelegram(status)
    bot.app = SimpleNamespace(bot=tg)
    return bot, tg


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


def bot_post(message_id=555, caption=CAPTION, group=None):
    return SimpleNamespace(
        message_id=message_id, from_user=SimpleNamespace(id=BOT_ID), media_group_id=group,
        caption=caption, caption_html=caption, text=None, text_html=None,
    )


def test_admin_replies_to_post_in_sales_group():
    bot, tg = make()
    update, out = command("/pret 750", TARGET, bot_post())
    asyncio.run(bot.on_price(update, None))
    assert tg.edits == [(TARGET, 555, CAPTION.replace("980 lei", "750 lei"))]
    assert out == ["DELETED"]  # comanda dispare din grup


def test_non_admin_cannot_change_price():
    bot, tg = make(status="member")
    update, out = command("/pret 1", TARGET, bot_post())
    asyncio.run(bot.on_price(update, None))
    assert tg.edits == [] and out == []


def test_reply_to_other_photo_of_album_finds_caption():
    bot, tg = make()
    bot._remember(Published(555, CAPTION, True, ("album1",)))
    other_photo = bot_post(message_id=550, caption=None, group="album1")
    update, out = command("/preț 1 190", TARGET, other_photo)
    asyncio.run(bot.on_price(update, None))
    assert tg.edits[0][1] == 555 and "1190 lei" in tg.edits[0][2]


def test_reply_to_confirmation_in_private_group():
    bot, tg = make()
    bot._remember(Published(555, CAPTION, True))
    confirmation = SimpleNamespace(text="✅ Publicat în topicul „Costume”: ...\nhttps://t.me/c/2000/555\nAlt preț? ...")
    update, out = command("/pret 690", SOURCE, confirmation)
    asyncio.run(bot.on_price(update, None))
    assert tg.edits == [(TARGET, 555, CAPTION.replace("980 lei", "690 lei"))]
    assert out == ["✅ Prețul a fost schimbat: 690 lei"]


def test_post_link_format():
    bot, _ = make()
    assert bot._post_link(555) == "https://t.me/c/2000/555"
