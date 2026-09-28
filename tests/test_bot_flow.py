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
