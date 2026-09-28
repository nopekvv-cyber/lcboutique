"""Botul Telegram: ascultă grupul sursă și publică pe canalul LC boutique."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from telegram import InputMediaPhoto, InputMediaVideo, Message, ReplyParameters, Update
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from .analyzer import AnalysisError, Analyzer
from .config import Config
from .formatter import fits_caption
from .pipeline import Media, ReadyPost, build_posts

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
        self.app = Application.builder().token(cfg.telegram_token).build()
        # /id în orice grup sau canal: botul răspunde cu ID-ul chatului (pentru configurare)
        self.app.add_handler(
            MessageHandler(filters.Regex(r"^/id(@\w+)?\s*$") & ~filters.UpdateType.EDITED, self.on_id)
        )
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
        log.info("/id în %r (%s): %s", chat.title, chat.type, chat.id)
        text = f"ID-ul acestui chat ({chat.title or chat.type}):\n<code>{chat.id}</code>"
        if chat.type == "channel":
            text += "\n\n(Acest mesaj și /id pot fi șterse din canal.)"
        await msg.reply_text(text, parse_mode=ParseMode.HTML)

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
            analysis = await self.analyzer.analyze("\n\n".join(unit.texts), photos_bytes)
            result = build_posts(analysis, unit.photos, unit.videos, self.cfg.pricing)
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
                        await self._report(post.source_chat_id, post.source_message_id, f"✅ Publicat: {post.summary}")
                except TelegramError as exc:
                    log.exception("Publicarea a eșuat")
                    await self._report(post.source_chat_id, post.source_message_id, f"❌ Publicarea a eșuat: {exc}")
                await asyncio.sleep(PAUSE_BETWEEN_POSTS)

    async def _send_post(self, post: ReadyPost) -> None:
        bot = self.app.bot
        chat = self.cfg.target_chat_id
        if not post.media:
            await _retry(lambda: bot.send_message(chat, post.text, parse_mode=ParseMode.HTML))
            return

        caption_on_media = fits_caption(post.text)
        chunks = [post.media[i : i + MAX_ALBUM] for i in range(0, len(post.media), MAX_ALBUM)]
        for n, chunk in enumerate(chunks):
            if n:
                await asyncio.sleep(PAUSE_BETWEEN_POSTS)  # multe albume la rând: evităm limitele Telegram
            items = []
            for i, m in enumerate(chunk):
                caption = post.text if (caption_on_media and n == 0 and i == 0) else None
                cls = InputMediaPhoto if m.kind == "photo" else InputMediaVideo
                items.append(cls(m.file_id, caption=caption, parse_mode=ParseMode.HTML if caption else None))
            if len(items) == 1:
                one = items[0]
                send = bot.send_photo if chunk[0].kind == "photo" else bot.send_video
                await _retry(lambda: send(chat, one.media, caption=one.caption, parse_mode=one.parse_mode))
            else:
                await _retry(lambda: bot.send_media_group(chat, items))
        if not caption_on_media:
            await _retry(lambda: bot.send_message(chat, post.text, parse_mode=ParseMode.HTML))

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


async def _retry(send, attempts: int = 5):
    for attempt in range(attempts):
        try:
            return await send()
        except RetryAfter as exc:
            if attempt == attempts - 1:
                raise
            delay = exc.retry_after.total_seconds() if hasattr(exc.retry_after, "total_seconds") else exc.retry_after
            await asyncio.sleep(float(delay) + 1)
