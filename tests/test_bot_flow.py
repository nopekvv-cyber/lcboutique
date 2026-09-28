"""Fluxul botului (albume, text separat, grupare pe categorii) cu Telegram și Claude simulate."""

import asyncio
from types import SimpleNamespace

import pytest

from lcboutique.bot import LCBoutiqueBot
from lcboutique.config import Config
from lcboutique.models import Analysis, Product, SupplierPrice

SOURCE = -100


def make_bot(**overrides):
    cfg = Config(
        telegram_token="123:abc",
        source_chat_id=SOURCE,
        target_chat_id=-200,
        report_chat_id=SOURCE,
        album_wait=0.05,
        pair_wait=0.2,
        publish_delay=0.1,
        **overrides,
    )
    bot = LCBoutiqueBot(cfg)
    bot.sent, bot.reports, bot.seen = [], [], []

    async def download(file_id):
        return file_id.encode()

    async def analyze(text, photos):
        bot.seen.append((text, photos))
        category = "geaca" if "куртка" in text else "rochie"
        code = text.split()[0] if text else ""
        return Analysis(
            products=[
                Product(
                    category=category, title=category, code=code, sizes="S", material="", composition="",
                    colors=[], description="", details=[], measurements=[],
                    prices=[SupplierPrice(kind="unspecified", amount=800, currency="UAH")],
                    profit_lei=150, photo_indices=[],
                )
            ],
            notes="",
        )

    async def send_post(post):
        bot.sent.append(post)

    async def report(chat_id, reply_to, text):
        bot.reports.append(text)

    bot._download = download
    bot.analyzer.analyze = analyze
    bot._send_post = send_post
    bot._report = report
    return bot


def msg(mid, photo=None, text=None, group=None):
    return SimpleNamespace(
        update=SimpleNamespace(
            effective_message=SimpleNamespace(
                message_id=mid,
                chat_id=SOURCE,
                media_group_id=group,
                photo=[SimpleNamespace(file_id=photo)] if photo else [],
                video=None,
                caption=text if photo else None,
                text=None if photo else text,
                from_user=SimpleNamespace(id=1),
            )
        )
    ).update


async def feed(bot, *updates):
    for u in updates:
        await bot.on_message(u, None)


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr("lcboutique.bot.PAUSE_BETWEEN_POSTS", 0)


def test_album_then_separate_text_is_one_product():
    async def run():
        bot = make_bot()
        await feed(bot, msg(1, "a", group="g"), msg(2, "b", group="g"))
        await asyncio.sleep(0.1)  # albumul se închide
        await feed(bot, msg(3, text="R-77 сукня 800 грн"))
        await asyncio.sleep(0.6)
        return bot

    bot = asyncio.run(run())
    assert len(bot.sent) == 1
    post = bot.sent[0]
    assert [m.file_id for m in post.media] == ["a", "b"]
    assert "Cod/Model: R-77" in post.text
    assert bot.seen[0][1] == [b"a", b"b"]
    assert any(r.startswith("✅ Publicat") for r in bot.reports)


def test_batch_is_grouped_by_category():
    async def run():
        bot = make_bot()
        await feed(
            bot,
            msg(1, "j1", text="G-1 куртка 800"),
            msg(2, "d1", text="D-1 сукня 800"),
            msg(3, "j2", text="G-2 куртка 800"),
            msg(4, "d2", text="D-2 сукня 800"),
        )
        await asyncio.sleep(0.6)
        return bot

    bot = asyncio.run(run())
    codes = [p.text.split("Cod/Model: ")[1].split("\n")[0] for p in bot.sent]
    assert codes == ["D-1", "D-2", "G-1", "G-2"]


def test_text_without_photos_is_reported_not_published():
    async def run():
        bot = make_bot()
        await feed(bot, msg(1, text="R-1 сукня 800"))
        await asyncio.sleep(0.5)
        return bot

    bot = asyncio.run(run())
    assert bot.sent == []
    assert any("doar text" in r for r in bot.reports)


def test_twenty_photos_in_two_albums_with_text_on_last():
    async def run():
        bot = make_bot()
        first = [msg(i, f"a{i}", group="g1") for i in range(1, 11)]
        second = [msg(i, f"a{i}", group="g2") for i in range(11, 21)]
        second[-1].effective_message.caption = "K-20 сукня 800 грн"
        await feed(bot, *first, *second)
        await asyncio.sleep(0.6)
        return bot

    bot = asyncio.run(run())
    assert len(bot.sent) == 1
    assert [m.file_id for m in bot.sent[0].media] == [f"a{i}" for i in range(1, 21)]
    assert "Cod/Model: K-20" in bot.sent[0].text
    assert len(bot.seen) == 1 and len(bot.seen[0][1]) == 20


def test_text_sent_right_after_albums_still_collecting():
    async def run():
        bot = make_bot()
        first = [msg(i, f"a{i}", group="g1") for i in range(1, 11)]
        second = [msg(i, f"a{i}", group="g2") for i in range(11, 21)]
        # textul vine imediat, înainte ca albumele să se fi închis
        await feed(bot, *first, *second, msg(21, text="T-5 сукня 800 грн"))
        await asyncio.sleep(0.6)
        return bot

    bot = asyncio.run(run())
    assert len(bot.sent) == 1
    assert len(bot.sent[0].media) == 20
    assert "Cod/Model: T-5" in bot.sent[0].text


def test_two_products_back_to_back_stay_separate():
    async def run():
        bot = make_bot()
        p1 = [msg(i, f"x{i}", group="p1") for i in range(1, 4)]
        p1[-1].effective_message.caption = "X-1 сукня 800"
        p2 = [msg(i, f"y{i}", group="p2") for i in range(4, 7)]
        p2[-1].effective_message.caption = "Y-2 куртка 800"
        await feed(bot, *p1)
        await asyncio.sleep(0.1)
        await feed(bot, *p2)
        await asyncio.sleep(0.6)
        return bot

    bot = asyncio.run(run())
    assert [[m.file_id for m in p.media] for p in bot.sent] == [["x1", "x2", "x3"], ["y4", "y5", "y6"]]
