import asyncio
from types import SimpleNamespace

from lcboutique.bot import LCBoutiqueBot, _topic_name
from lcboutique.config import Config
from lcboutique.pipeline import Media, ReadyPost
from lcboutique.topics import TopicStore, match_topic, parse_env


def test_match_topic_names_with_emoji_and_diacritics():
    assert match_topic("Rochițe 👗") == "rochite"
    assert match_topic("ROCHII ELEGANTE ✨") == "rochii_elegante"
    assert match_topic("Costume") == "costume"
    assert match_topic("Costume sport 🏃‍♀️") == "costume_sport"
    assert match_topic("Fustițe / șorți") == "fustite_sorti"
    assert match_topic("Haine pentru bărbați") == "barbati"
    assert match_topic("Discuții") is None


def test_parse_env_accepts_keys_and_names():
    assert parse_env("rochite=12;Costume sport=15; Plajă = 40") == {"rochite": 12, "costume_sport": 15, "plaja": 40}


def test_store_learns_and_persists(tmp_path):
    path = tmp_path / "topics.json"
    store = TopicStore(path)
    assert store.learn("Rochițe", 7) == "rochite"
    assert TopicStore(path).thread_for("rochite") == 7
    assert "rochite=7" in store.env_line()
    assert "✅ Rochițe" in store.status() and "❌ Paltoane" in store.status()


def test_env_overrides_file(tmp_path):
    path = tmp_path / "topics.json"
    TopicStore(path).learn("Rochițe", 7)
    assert TopicStore(path, "rochite=9").thread_for("rochite") == 9


def test_topic_name_from_topic_creation_message():
    created = SimpleNamespace(forum_topic_created=SimpleNamespace(name="Paltoane"), forum_topic_edited=None)
    msg = SimpleNamespace(forum_topic_created=None, forum_topic_edited=None, reply_to_message=created)
    assert _topic_name(msg) == "Paltoane"


def test_post_goes_to_its_topic(tmp_path):
    calls = []

    class FakeBot:
        async def send_media_group(self, chat, items, **kw):
            calls.append(kw)

        async def send_photo(self, chat, photo, **kw):
            calls.append(kw)

    cfg = Config(
        telegram_token="1:a", source_chat_id=-1, target_chat_id=-2, report_chat_id=-1,
        data_dir=str(tmp_path), topics="paltoane=33",
    )
    bot = LCBoutiqueBot(cfg)
    bot.app = SimpleNamespace(bot=FakeBot())
    post = ReadyPost(category="paltoane", text="x", media=[Media("photo", "a"), Media("photo", "b")], summary="")
    asyncio.run(LCBoutiqueBot._send_post(bot, post))
    post = ReadyPost(category="rochite", text="x", media=[Media("photo", "a")], summary="")
    asyncio.run(LCBoutiqueBot._send_post(bot, post))
    assert calls[0]["message_thread_id"] == 33
    assert calls[1]["message_thread_id"] is None  # topic neînvățat → General
