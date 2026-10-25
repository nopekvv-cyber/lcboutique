import asyncio
import tempfile
from types import SimpleNamespace

from telegram.error import BadRequest

from lcboutique.bot import LCBoutiqueBot
from lcboutique.config import Config
from lcboutique.importer import guess_category

SOURCE, TARGET, BOT_ID, OWNER = -100, -1002000, 42, 7
OLD = (
    "<b>Costum elegant</b>\n\nCod/Model: 1390\nMărimi: 42-44\nMaterial: tricot\nCulori: marsala, negru\n"
    "💰 Preț: 980 lei\n\nCostum elegant din tricot turcesc...\n\n📩 Pentru comandă, trimiteți mesaj pe Instagram:"
)


def tg_message(message_id, caption=None, sender=BOT_ID, thread=None):
    return SimpleNamespace(
        message_id=message_id, from_user=SimpleNamespace(id=sender), media_group_id=None, caption=caption,
        caption_html=caption, text=None, text_html=None,
        photo=[SimpleNamespace(file_id=f"s{message_id}", width=320, height=320),
               SimpleNamespace(file_id=f"l{message_id}", width=1280, height=1280)],
        is_topic_message=thread is not None, message_thread_id=thread,
        forward_origin=SimpleNamespace(sender_user=SimpleNamespace(id=sender)),
    )


class FakeTelegram:
    id = BOT_ID

    def __init__(self, group):
        self.group, self.reports, self.edits = group, [], []

    async def get_chat_member(self, chat, user):
        return SimpleNamespace(status="administrator" if user == OWNER else "left")

    async def forward_message(self, chat, from_chat, message_id, **kw):
        if message_id not in self.group:
            raise BadRequest("Message to forward not found")
        original = self.group[message_id]

        async def delete():
            return True

        return SimpleNamespace(**{**original.__dict__, "delete": delete})

    async def send_message(self, chat, text, **kw):
        self.reports.append(text)

        async def edit_text(t):
            self.reports.append(t)

        return SimpleNamespace(edit_text=edit_text)

    async def edit_message_caption(self, chat, message_id, caption, **kw):
        self.edits.append((message_id, caption))


def make(group):
    cfg = Config(
        telegram_token="1:a", source_chat_id=SOURCE, target_chat_id=TARGET, report_chat_id=SOURCE,
        data_dir=tempfile.mkdtemp(), topics="costume=11",
    )
    bot = LCBoutiqueBot(cfg)
    bot.app = SimpleNamespace(bot=FakeTelegram(group))
    return bot


def test_guess_category_from_title():
    assert guess_category("Costum sport din bumbac") == "costume_sport"
    assert guess_category("Rochie midi") == "rochite"
    assert guess_category("Geacă scurtă de puf") == "scurte_trenciuri"
    assert guess_category("Ceva necunoscut") == ""


def test_edit_reply_imports_old_post_with_its_topic():
    bot = make({})
    reports = []

    async def report(chat, reply_to, text):
        reports.append(text)

    async def delete():
        return True

    bot._report = report
    reply = tg_message(500, OLD, thread=11)
    msg = SimpleNamespace(reply_to_message=reply, from_user=SimpleNamespace(id=OWNER), sender_chat=None, delete=delete)
    update = SimpleNamespace(effective_message=msg, effective_chat=SimpleNamespace(id=TARGET, username=None))
    asyncio.run(bot.on_edit(update, None))

    post = bot.storage.find_by_message(500)
    assert post.category == "costume" and post.price_lei == 980 and post.product["colors"] == ["marsala", "negru"]
    assert post.extra["legacy"] and "nr. 1" in reports[0]

    # editarea postării vechi rescrie textul în formatul nou, pe loc
    asyncio.run(bot.edit_post(post, dict(post.product, title="Costum de seară"), 1050, "costume"))
    message_id, caption = bot.app.bot.edits[-1]
    assert message_id == 500 and "Costum de seară" in caption and "1050 lei" in caption and "Pentru comandă" not in caption


def test_bulk_import_finds_only_bot_posts():
    group = {
        10: tg_message(10, OLD),
        9: tg_message(9, None),  # altă poză din album
        8: tg_message(8, "Bună ziua, ce mărimi aveți?", sender=555),  # mesajul unei cliente
        7: tg_message(7, OLD.replace("1390", "2001"), sender=555),  # postare manuală (nu a botului)
        5: tg_message(5, OLD.replace("Costum elegant", "Rochie de vară").replace("1390", "77")),
    }
    bot = make(group)
    asyncio.run(bot._import_history(OWNER, 10))
    posts = bot.storage.list_posts()
    assert sorted(p.product["code"] for p in posts) == ["1390", "77"]
    assert {p.category for p in posts} == {"costume", "rochite"}
    assert "Postări vechi adăugate în aplicație: 2" in bot.app.bot.reports[-1]
    # a doua rulare nu dublează
    asyncio.run(bot._import_history(OWNER, 10))
    assert len(bot.storage.list_posts()) == 2


def test_old_posts_cannot_be_deleted_from_app():
    bot = make({})
    post = bot._import_message(tg_message(500, OLD), 500, None)
    try:
        asyncio.run(bot.delete_post(post))
        raise AssertionError("ar fi trebuit refuzat")
    except ValueError as exc:
        assert "Telegram" in str(exc)
