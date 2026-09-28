"""Botul Telegram: ascultă grupul sursă și publică pe canalul LC boutique."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from telegram import InputMediaPhoto, InputMediaVideo, Message, ReplyParameters, Update
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from .analyzer import AnalysisError, Analyzer
from .config import Config
from .formatter import fits_caption
from .models import TOPICS
from .pipeline import Media, ReadyPost, build_posts
from .topics import TopicStore, decode, encode, match_topic

log = logging.getLogger(__name__)

MAX_ALBUM = 10  # limita Telegram pentru un album
PAUSE_BETWEEN_POSTS = 3.0  # evită limitele de trimitere ale Telegram
MAX_PHOTOS_FOR_ANALYSIS = 20  # câte poze vede Claude (se publică oricum toate)
PREVIEW_MAX_SIDE = 800  # rezoluția pozelor trimise la analiză (mai mică = mai ieftin)


@dataclass
class Unit:
    """O postare din grupul sursă: un album, o poză sau un text."""

    chat_id: int
    first_message_id: int
    photos: list[Media] = field(default_factory=list)
    videos: list[Media] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)

    @property
    def has_media(self) -> bool:
        return bool(self.photos or self.videos)

    @property
    def has_text(self) -> bool:
        return any(t.strip() for t in self.texts)

    def add_message(self, msg: Message) -> None:
        if msg.photo:
            small = [p for p in msg.photo if max(getattr(p, "width", 0), getattr(p, "height", 0)) <= PREVIEW_MAX_SIDE]
            preview = (small[-1] if small else msg.photo[-1]).file_id
            self.photos.append(Media("photo", msg.photo[-1].file_id, preview))
        elif msg.video:
            self.videos.append(Media("video", msg.video.file_id))
        text = msg.caption or msg.text
        if text:
            self.texts.append(text)

    def merge(self, other: "Unit") -> None:
        self.first_message_id = min(self.first_message_id, other.first_message_id)
        self.photos += other.photos
        self.videos += other.videos
        self.texts += other.texts


class Timer:
    """Un apel întârziat care poate fi anulat/repornit."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self, delay: float, coro_factory) -> None:
        self.cancel()

        async def runner() -> None:
            await asyncio.sleep(delay)
            self._task = None
            await coro_factory()

        self._task = asyncio.create_task(runner())

    def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None


class LCBoutiqueBot:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.analyzer = Analyzer(cfg.anthropic_model, cfg.anthropic_effort)
        self.topics = TopicStore(Path(cfg.data_dir) / "topics.json", cfg.topics)
        self.app = Application.builder().token(cfg.telegram_token).post_init(self._load_saved_topics).build()
        # /id în orice grup, canal sau topic: botul răspunde cu ID-ul (pentru configurare)
        self.app.add_handler(
            MessageHandler(filters.Regex(r"^/id(@\w+)?\s*$") & ~filters.UpdateType.EDITED, self.on_id)
        )
        self.app.add_handler(
            MessageHandler(filters.Regex(r"^/topics(@\w+)?\s*$") & ~filters.UpdateType.EDITED, self.on_topics)
        )
        self.app.add_handler(
            MessageHandler(filters.Regex(r"^/topic(@\w+)?(\s+.*)?$") & ~filters.UpdateType.EDITED, self.on_topic)
        )
        if cfg.target_chat_id:
            target = cfg.target_chat_id
            target_filter = (
                filters.Chat(username=target.lstrip("@")) if isinstance(target, str) else filters.Chat(target)
            )
            # învață topicurile din orice mesaj din grupul de vânzare (grup separat de handlere)
            self.app.add_handler(MessageHandler(target_filter, self.on_target_message), group=1)
        if cfg.is_configured:
            self.app.add_handler(
                MessageHandler(
                    filters.Chat(cfg.source_chat_id)
                    & ~filters.UpdateType.EDITED
                    & (filters.PHOTO | filters.VIDEO | filters.TEXT)
                    & ~filters.COMMAND,
                    self.on_message,
                )
            )
        self.app.add_error_handler(self.on_error)

        self._albums: dict[str, Unit] = {}
        self._album_timers: dict[str, Timer] = {}
        # poze fără text care așteaptă textul (sau text care așteaptă pozele)
        self._pending: Unit | None = None
        self._pending_timer = Timer()

        self._ready: list[ReadyPost] = []
        self._publish_timer = Timer()
        self._in_flight = 0
        self._publish_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    def run(self) -> None:
        if self.cfg.is_configured:
            log.info("LC boutique bot pornit. Sursă: %s → canal: %s", self.cfg.source_chat_id, self.cfg.target_chat_id)
        else:
            log.warning(
                "SOURCE_CHAT_ID / TARGET_CHAT_ID nu sunt setate. Botul răspunde doar la /id: "
                "scrieți /id în grupul sursă și în canal, apoi completați variabilele."
            )
        self.app.run_polling(allowed_updates=["message", "channel_post"])

    # ---------- primirea mesajelor ----------

    async def on_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        msg = update.effective_message
        if chat is None or msg is None:
            return
        log.info("/id în %r (%s): %s, topic %s", chat.title, chat.type, chat.id, msg.message_thread_id)
        text = f"ID-ul acestui chat ({chat.title or chat.type}):\n<code>{chat.id}</code>"
        if msg.is_topic_message and msg.message_thread_id:
            name = _topic_name(msg)
            key = self.topics.learn(name, msg.message_thread_id) if name else None
            await self._persist_topics()
            text += f"\n\nTopic nr. <code>{msg.message_thread_id}</code>"
            if key:
                text += f"\n✅ Aici se publică: <b>{TOPICS[key]}</b>"
                text += "\nDacă nu e corect, scrieți aici: <code>/topic Numele categoriei</code>"
            else:
                text += (
                    f"\n⚠️ Nu recunosc numele topicului ({name or 'necunoscut'})."
                    "\nScrieți aici, de exemplu: <code>/topic Maiouri</code>"
                )
        if chat.type == "channel":
            text += "\n\n(Acest mesaj și /id pot fi șterse din canal.)"
        await msg.reply_text(text, parse_mode=ParseMode.HTML)

    async def on_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/topic Maiouri — scris într-un topic: aici se publică produsele din categoria dată."""
        msg = update.effective_message
        if msg is None:
            return
        if not (msg.is_topic_message and msg.message_thread_id):
            await msg.reply_text("Scrieți comanda în interiorul topicului, ex.: /topic Maiouri")
            return
        name = (msg.text or "").split(maxsplit=1)[1] if len((msg.text or "").split()) > 1 else ""
        key = match_topic(name) if name else None
        if not key:
            options = "\n".join(f"• {n}" for n in TOPICS.values())
            await msg.reply_text(f"Nu recunosc categoria „{name}”. Categorii posibile:\n{options}")
            return
        self.topics.assign(key, msg.message_thread_id)
        await self._persist_topics()
        await msg.reply_text(f"✅ Aici se publică: {TOPICS[key]}")

    async def on_topics(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if msg is None:
            return
        await msg.reply_text(
            "Topicuri învățate (✅) și lipsă (❌):\n"
            + self.topics.status()
            + "\n\nPentru ❌: scrieți /id în topicul respectiv (sau /topic Nume)."
        )

    async def on_target_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if msg and msg.is_topic_message and msg.message_thread_id:
            # doar topicurile încă necunoscute (nu suprascriem ce a fost setat cu /topic)
            if msg.message_thread_id in self.topics.threads.values():
                return
            name = _topic_name(msg)
            if name and self.topics.learn(name, msg.message_thread_id):
                await self._persist_topics()

    async def _load_saved_topics(self, app: Application) -> None:
        try:
            saved = decode((await app.bot.get_my_description()).description)
        except TelegramError:
            log.exception("Nu am putut citi topicurile salvate")
            return
        self.topics.merge_saved(saved)
        log.info("Topicuri cunoscute: %d din %d", len(self.topics.threads), len(TOPICS))

    async def _persist_topics(self) -> None:
        """Salvează topicurile în descrierea botului, ca să rămână și după redeploy."""
        try:
            await self.app.bot.set_my_description(encode(self.topics.threads))
        except TelegramError:
            log.exception("Nu am putut salva topicurile în Telegram")

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if msg is None:
            return
        if self.cfg.allowed_user_ids and msg.from_user and msg.from_user.id not in self.cfg.allowed_user_ids:
            return

        if msg.media_group_id:
            key = msg.media_group_id
            unit = self._albums.get(key)
            if unit is None:
                unit = self._albums[key] = Unit(msg.chat_id, msg.message_id)
                self._album_timers[key] = Timer()
            unit.first_message_id = min(unit.first_message_id, msg.message_id)
            unit.add_message(msg)
            self._album_timers[key].start(self.cfg.album_wait, lambda: self._album_done(key))
        else:
            unit = Unit(msg.chat_id, msg.message_id)
            unit.add_message(msg)
            await self._unit_ready(unit)

    async def _album_done(self, key: str) -> None:
        unit = self._albums.pop(key, None)
        self._album_timers.pop(key, None)
        if unit:
            await self._unit_ready(unit)

    async def _unit_ready(self, unit: Unit) -> None:
        """Adună pozele până la text: textul (descrierea) închide produsul.

        Un produs poate veni ca mai multe albume/poze la rând (ex. 20 de poze = 2 albume),
        cu descrierea pe ultimele poze sau într-un mesaj separat după ele. Dacă textul
        vine primul, pozele trimise imediat după el se atașează lui.
        """
        pending = self._pending

        if not unit.has_text:
            if pending and not pending.has_media:
                # textul a venit înaintea pozelor
                self._pending = None
                self._pending_timer.cancel()
                pending.merge(unit)
                self._process_later(pending)
                return
            if pending:
                pending.merge(unit)  # încă poze ale aceluiași produs
            else:
                self._pending = unit
            # așteptăm textul; fiecare poză nouă prelungește așteptarea
            self._pending_timer.start(self.cfg.photos_wait, self._flush_pending)
            return

        # a venit textul: luăm și albumele începute înaintea lui, care încă se adună
        earlier = sorted(
            (k for k, a in self._albums.items() if a.first_message_id < unit.first_message_id),
            key=lambda k: self._albums[k].first_message_id,
        )
        collected: Unit | None = pending if pending and pending.has_media else None
        if pending and not pending.has_media:
            await self._flush_pending()  # text anterior rămas fără poze
        self._pending = None
        self._pending_timer.cancel()
        for key in earlier:
            album = self._albums.pop(key)
            self._album_timers.pop(key).cancel()
            if collected is None:
                collected = album
            else:
                collected.merge(album)

        if collected is not None:
            collected.merge(unit)
            self._process_later(collected)
        elif unit.has_media:
            self._process_later(unit)
        else:
            # doar text: așteptăm pozele
            self._pending = unit
            self._pending_timer.start(self.cfg.pair_wait, self._flush_pending)

    async def _flush_pending(self) -> None:
        unit, self._pending = self._pending, None
        self._pending_timer.cancel()
        if unit is None:
            return
        if unit.has_media:
            self._process_later(unit)  # poze fără text: încercăm oricum
        else:
            await self._report(
                unit.chat_id,
                unit.first_message_id,
                "⚠️ Am primit doar text, fără poze — nu am publicat nimic. "
                "Trimiteți pozele împreună cu textul (sau imediat după el).",
            )

    # ---------- procesarea ----------

    def _process_later(self, unit: Unit) -> None:
        self._in_flight += 1
        task = asyncio.create_task(self._process(unit))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _process(self, unit: Unit) -> None:
        try:
            photos_bytes = [
                await self._download(m.preview_id or m.file_id) for m in unit.photos[:MAX_PHOTOS_FOR_ANALYSIS]
            ]
            text = "\n\n".join(unit.texts)
            analysis = await self.analyzer.analyze(text, photos_bytes)
            result = build_posts(analysis, unit.photos, unit.videos, self.cfg.pricing, text)
            for post in result.posts:
                post.source_chat_id = unit.chat_id
                post.source_message_id = unit.first_message_id
            self._ready.extend(result.posts)
            if result.problems:
                await self._report(unit.chat_id, unit.first_message_id, "⚠️ " + "\n⚠️ ".join(result.problems))
        except AnalysisError as exc:
            await self._report(unit.chat_id, unit.first_message_id, f"❌ Nu am putut procesa produsul: {exc}")
        except Exception:
            log.exception("Eroare la procesarea mesajului %s", unit.first_message_id)
            await self._report(unit.chat_id, unit.first_message_id, "❌ Eroare neașteptată la procesarea produsului.")
        finally:
            self._in_flight -= 1
            self._schedule_publish()

    async def _download(self, file_id: str) -> bytes:
        tg_file = await self.app.bot.get_file(file_id)
        return bytes(await tg_file.download_as_bytearray())

    # ---------- publicarea ----------

    def _schedule_publish(self) -> None:
        self._publish_timer.start(self.cfg.publish_delay, self._publish_ready)

    async def _publish_ready(self) -> None:
        if self._in_flight or self._pending or self._albums:
            # mai sunt produse în lucru: așteptăm ca lotul să fie complet
            self._publish_timer.start(max(self.cfg.publish_delay, 5.0), self._publish_ready)
            return
        async with self._publish_lock:
            batch, self._ready = self._ready, []
            # gruparea pe categorii (sortare stabilă: ordinea din grup se păstrează)
            for post in sorted(batch, key=lambda p: p.sort_key):
                try:
                    await self._send_post(post)
                    if self.cfg.confirm_in_source:
                        topic = TOPICS.get(post.category, post.category)
                        where = (
                            f"în topicul „{topic}”"
                            if self.topics.thread_for(post.category)
                            else f"în General (topicul „{topic}” nu e învățat — scrieți /id în el)"
                        )
                        await self._report(
                            post.source_chat_id, post.source_message_id, f"✅ Publicat {where}: {post.summary}"
                        )
                except TelegramError as exc:
                    log.exception("Publicarea a eșuat")
                    await self._report(post.source_chat_id, post.source_message_id, f"❌ Publicarea a eșuat: {exc}")
                await asyncio.sleep(PAUSE_BETWEEN_POSTS)

    async def _send_post(self, post: ReadyPost) -> None:
        bot = self.app.bot
        chat = self.cfg.target_chat_id
        topic = {"message_thread_id": self.topics.thread_for(post.category)}
        if not post.media:
            await _retry(lambda: bot.send_message(chat, post.text, parse_mode=ParseMode.HTML, **topic))
            return

        caption_on_media = fits_caption(post.text)
        chunks = split_albums(post.media)
        for n, chunk in enumerate(chunks):
            if n:
                await asyncio.sleep(PAUSE_BETWEEN_POSTS)  # multe albume la rând: evităm limitele Telegram
            items = []
            for i, m in enumerate(chunk):
                # textul la final: pe ultimul album (Telegram îl afișează sub album)
                caption = post.text if (caption_on_media and n == len(chunks) - 1 and i == 0) else None
                cls = InputMediaPhoto if m.kind == "photo" else InputMediaVideo
                items.append(cls(m.file_id, caption=caption, parse_mode=ParseMode.HTML if caption else None))
            if len(items) == 1:
                one = items[0]
                send = bot.send_photo if chunk[0].kind == "photo" else bot.send_video
                await _retry(lambda: send(chat, one.media, caption=one.caption, parse_mode=one.parse_mode, **topic))
            else:
                await _retry(lambda: bot.send_media_group(chat, items, **topic))
        if not caption_on_media:
            await _retry(lambda: bot.send_message(chat, post.text, parse_mode=ParseMode.HTML, **topic))

    async def _report(self, chat_id: int, reply_to: int, text: str) -> None:
        """Mesaj pentru proprietară (în grupul sursă sau în chatul de rapoarte)."""
        target = self.cfg.report_chat_id
        reply = ReplyParameters(reply_to, allow_sending_without_reply=True) if target == chat_id else None
        try:
            await self.app.bot.send_message(target, text[:4000], reply_parameters=reply)
        except TelegramError:
            log.exception("Nu am putut trimite raportul: %s", text)

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("Eroare Telegram", exc_info=context.error)


def _topic_name(msg: Message) -> str | None:
    """Numele topicului în care a fost scris mesajul (din mesajul de creare a topicului)."""
    for source in (msg, msg.reply_to_message):
        if source is None:
            continue
        for attr in ("forum_topic_created", "forum_topic_edited"):
            info = getattr(source, attr, None)
            if info is not None and getattr(info, "name", None):
                return info.name
    return None


def split_albums(media: list, size: int = MAX_ALBUM) -> list[list]:
    """Împarte pozele în albume de maxim `size`, cât mai egale (ex. 21 → 7 + 7 + 7)."""
    if not media:
        return []
    count = -(-len(media) // size)
    base, extra = divmod(len(media), count)
    chunks, start = [], 0
    for i in range(count):
        end = start + base + (1 if i < extra else 0)
        chunks.append(media[start:end])
        start = end
    return chunks


async def _retry(send, attempts: int = 5):
    for attempt in range(attempts):
        try:
            return await send()
        except RetryAfter as exc:
            if attempt == attempts - 1:
                raise
            delay = exc.retry_after.total_seconds() if hasattr(exc.retry_after, "total_seconds") else exc.retry_after
            await asyncio.sleep(float(delay) + 1)
