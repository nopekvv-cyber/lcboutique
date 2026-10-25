"""Botul Telegram: ascultă grupul sursă și publică pe canalul LC boutique."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    MenuButtonWebApp,
    Message,
    ReplyParameters,
    Update,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from .analyzer import AnalysisError, Analyzer
from .config import Config, PricingConfig
from .formatter import fits_caption, format_post, set_price
from .models import TOPICS, Product
from .pipeline import Media, ReadyPost, build_posts
from .storage import Post as StoredPost
from .storage import Storage, new_post
from .topics import TopicStore, decode, encode, match_topic

log = logging.getLogger(__name__)

MAX_ALBUM = 10  # limita Telegram pentru un album
_POST_LINK = re.compile(r"t\.me/(?:c/\d+|\w+)/(\d+)")
_PRICE_CMD = re.compile(r"^/pre[tț](?:@\w+)?\s+(\d[\d ]*)\s*(?:lei)?\s*$", re.I)
PAUSE_BETWEEN_POSTS = 3.0  # evită limitele de trimitere ale Telegram
MAX_PHOTOS_FOR_ANALYSIS = 20  # câte poze vede Claude (se publică oricum toate)
PREVIEW_MAX_SIDE = 800  # rezoluția pozelor trimise la analiză (mai mică = mai ieftin)


@dataclass
class Sent:
    """Rezultatul publicării: mesajele trimise în grupul de vânzare."""

    text_message_id: int  # mesajul care conține textul (cu prețul)
    is_caption: bool  # textul e sub poze (caption) sau mesaj separat
    message_ids: list[int] = field(default_factory=list)
    media_group_ids: list[str] = field(default_factory=list)
    thread_id: int | None = None


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
        self.storage = Storage(Path(cfg.data_dir) / "lcboutique.db")
        self.app = Application.builder().token(cfg.telegram_token).post_init(self._on_startup).post_shutdown(self._on_shutdown).build()
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
        # /start, /app: butonul care deschide mini-aplicația de editare
        self.app.add_handler(
            MessageHandler(filters.Regex(r"^/(start|app|editare)(@\w+)?(\s.*)?$") & ~filters.UpdateType.EDITED, self.on_app)
        )
        # /pret 750 ca răspuns la o postare (sau la confirmarea ✅): schimbă prețul postării
        self.app.add_handler(
            MessageHandler(filters.Regex(_PRICE_CMD) & filters.REPLY & ~filters.UpdateType.EDITED, self.on_price)
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

    EDITABLE_SETTINGS = ("uah_to_lei", "usd_to_lei", "shipping_lei")

    @property
    def pricing(self) -> PricingConfig:
        """Setările de preț: cele din Railway, suprascrise de cele salvate din mini-aplicație."""
        saved = self.storage.get_settings()
        changes = {}
        for key in self.EDITABLE_SETTINGS:
            if key in saved:
                value = float(saved[key])
                changes[key] = int(value) if key == "shipping_lei" else value
        return replace(self.cfg.pricing, **changes)

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

    async def _on_startup(self, app: Application) -> None:
        await self._load_saved_topics(app)
        from .webapp import start_webapp

        try:
            self._web_runner = await start_webapp(self, self.cfg.webapp_port)
        except OSError:
            log.exception("Mini-aplicația nu a putut porni pe portul %s", self.cfg.webapp_port)
        if self.cfg.public_url:
            try:
                await app.bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp("📝 Editare", WebAppInfo(self.cfg.public_url))
                )
            except TelegramError:
                log.exception("Nu am putut seta butonul mini-aplicației")
        else:
            log.warning("PUBLIC_URL lipsește: generați un domeniu în Railway ca să meargă mini-aplicația.")

    async def _on_shutdown(self, app: Application) -> None:
        runner = getattr(self, "_web_runner", None)
        if runner:
            await runner.cleanup()

    async def on_app(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or chat is None:
            return
        if chat.type != "private" and not (msg.text or "").startswith(("/app", "/editare")):
            return  # /start în grupuri nu ne privește
        if not self.cfg.public_url:
            await msg.reply_text(
                "Mini-aplicația nu e activă încă: în Railway → Settings → Networking apăsați „Generate Domain”."
            )
            return
        if chat.type == "private":
            if not (msg.from_user and await self.user_can_edit(msg.from_user.id)):
                await msg.reply_text("Bună! Acesta este asistentul LC boutique.")
                return
            button = InlineKeyboardButton("📝 Deschide editarea", web_app=WebAppInfo(self.cfg.public_url))
        else:
            # în grupuri Telegram nu permite butoane de mini-aplicație: trimitem spre chatul privat
            button = InlineKeyboardButton("📝 Deschide editarea", url=f"https://t.me/{self.app.bot.username}?start=app")
        await msg.reply_text(
            "Aici puteți edita postările publicate: titlu, cod, mărimi, material, culori, preț, topic.",
            reply_markup=InlineKeyboardMarkup([[button]]),
        )

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
            result = build_posts(analysis, unit.photos, unit.videos, self.pricing, text)
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
                    sent = await self._send(post.text, post.media, post.category)
                    stored = self._store(post, sent)
                    if self.cfg.confirm_in_source:
                        topic = TOPICS.get(post.category, post.category)
                        where = (
                            f"în topicul „{topic}”"
                            if sent.thread_id
                            else f"în General (topicul „{topic}” nu e învățat — scrieți /id în el)"
                        )
                        await self._report(
                            post.source_chat_id,
                            post.source_message_id,
                            f"✅ Publicat {where} (nr. {stored.id if stored else '—'}): {post.summary}\n"
                            f"{self._post_link(sent.text_message_id)}\n"
                            "Alt preț? Răspundeți la acest mesaj cu: /pret 750",
                        )
                except TelegramError as exc:
                    log.exception("Publicarea a eșuat")
                    await self._report(post.source_chat_id, post.source_message_id, f"❌ Publicarea a eșuat: {exc}")
                await asyncio.sleep(PAUSE_BETWEEN_POSTS)

    def _store(self, post: ReadyPost, sent: Sent) -> StoredPost | None:
        if post.product is None:
            return None
        return self.storage.add_post(
            new_post(
                category=post.category,
                product=post.product.model_dump(),
                price_lei=post.price_lei,
                text_message_id=sent.text_message_id,
                is_caption=sent.is_caption,
                message_ids=sent.message_ids,
                media_group_ids=sent.media_group_ids,
                media=[m.__dict__ for m in post.media],
                thread_id=sent.thread_id,
                calc=post.calc,
            )
        )

    async def _send(self, text: str, media: list[Media], category: str) -> Sent:
        """Publică textul și pozele în topicul categoriei: întâi pozele, textul la final."""
        bot = self.app.bot
        chat = self.cfg.target_chat_id
        thread_id = self.topics.thread_for(category)
        topic = {"message_thread_id": thread_id}
        if not media:
            msg = await _retry(lambda: bot.send_message(chat, text, parse_mode=ParseMode.HTML, **topic))
            return Sent(msg.message_id, False, [msg.message_id], [], thread_id)

        caption_on_media = fits_caption(text)
        chunks = split_albums(media)
        result = Sent(0, caption_on_media, thread_id=thread_id)
        for n, chunk in enumerate(chunks):
            if n:
                await asyncio.sleep(PAUSE_BETWEEN_POSTS)  # multe albume la rând: evităm limitele Telegram
            items = []
            for i, m in enumerate(chunk):
                # textul la final: pe ultimul album (Telegram îl afișează sub album)
                caption = text if (caption_on_media and n == len(chunks) - 1 and i == 0) else None
                cls = InputMediaPhoto if m.kind == "photo" else InputMediaVideo
                items.append(cls(m.file_id, caption=caption, parse_mode=ParseMode.HTML if caption else None))
            if len(items) == 1:
                one = items[0]
                send = bot.send_photo if chunk[0].kind == "photo" else bot.send_video
                sent = [await _retry(lambda: send(chat, one.media, caption=one.caption, parse_mode=one.parse_mode, **topic))]
            else:
                sent = list(await _retry(lambda: bot.send_media_group(chat, items, **topic)))
            result.message_ids += [m.message_id for m in sent]
            result.media_group_ids += [m.media_group_id for m in sent[:1] if getattr(m, "media_group_id", None)]
            result.text_message_id = sent[0].message_id
        if not caption_on_media:
            msg = await _retry(lambda: bot.send_message(chat, text, parse_mode=ParseMode.HTML, **topic))
            result.text_message_id = msg.message_id
            result.message_ids.append(msg.message_id)
        return result

    def _post_link(self, message_id: int) -> str:
        chat = self.cfg.target_chat_id
        if isinstance(chat, str):
            return f"https://t.me/{chat.lstrip('@')}/{message_id}"
        return f"https://t.me/c/{str(chat).removeprefix('-100')}/{message_id}"

    def _is_target_chat(self, chat) -> bool:
        target = self.cfg.target_chat_id
        if isinstance(target, str):
            return (chat.username or "").lower() == target.lstrip("@").lower()
        return chat.id == target

    # ---------- editarea postărilor (comanda /pret și mini-aplicația) ----------

    async def edit_post(self, post: StoredPost, product: dict, price_lei: int, category: str) -> StoredPost:
        """Rescrie postarea cu datele noi. Dacă se schimbă topicul, postarea e mutată (republicată)."""
        new_text = format_post(Product(**product), price_lei)
        if fits_caption(new_text) is False and post.is_caption:
            raise ValueError("Textul este prea lung pentru a fi sub poze (maxim 1024 de caractere).")
        moved = category != post.category and self.topics.thread_for(category) != post.thread_id
        if moved:
            media = [Media(**m) for m in post.media]
            sent = await self._send(new_text, media, category)
            await self._delete_messages(post.message_ids)
            post.text_message_id, post.is_caption = sent.text_message_id, sent.is_caption
            post.message_ids, post.media_group_ids, post.thread_id = (
                sent.message_ids, sent.media_group_ids, sent.thread_id,
            )
        else:
            await self._edit_text(post, new_text)
        post.product, post.price_lei, post.category = product, price_lei, category
        self.storage.update_post(post)
        return post

    async def _edit_text(self, post: StoredPost, new_text: str) -> None:
        bot = self.app.bot
        try:
            if post.is_caption:
                await bot.edit_message_caption(
                    self.cfg.target_chat_id, post.text_message_id, caption=new_text, parse_mode=ParseMode.HTML
                )
            else:
                await bot.edit_message_text(
                    new_text, self.cfg.target_chat_id, post.text_message_id, parse_mode=ParseMode.HTML
                )
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():
                raise

    async def delete_post(self, post: StoredPost) -> None:
        await self._delete_messages(post.message_ids)
        self.storage.delete_post(post.id)

    async def _delete_messages(self, message_ids: list[int]) -> None:
        if not message_ids:
            return
        try:
            await self.app.bot.delete_messages(self.cfg.target_chat_id, message_ids)
        except TelegramError:
            log.exception("Nu am putut șterge mesajele %s", message_ids)

    async def on_price(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/pret 750 ca răspuns la postare (grupul de vânzare) sau la confirmarea ✅ (grupul privat)."""
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or chat is None or msg.reply_to_message is None:
            return
        price = int(_PRICE_CMD.match(msg.text or "").group(1).replace(" ", ""))
        reply = msg.reply_to_message
        in_sales_group = self._is_target_chat(chat)

        if in_sales_group:
            # în grupul de vânzare: doar administratorii, ca răspuns la postarea botului
            if not await self._is_admin(chat.id, msg):
                return
            if reply.from_user is None or reply.from_user.id != self.app.bot.id:
                return
            post = self.storage.find_by_message(reply.message_id, reply.media_group_id)
        elif chat.id in (self.cfg.source_chat_id, self.cfg.report_chat_id):
            if self.cfg.allowed_user_ids and msg.from_user and msg.from_user.id not in self.cfg.allowed_user_ids:
                return
            match = _POST_LINK.search(reply.text or "")
            post = self.storage.find_by_message(int(match.group(1))) if match else None
        else:
            return

        try:
            if post is not None:
                await self.edit_post(post, post.product, price, post.category)
            elif in_sales_group and (old := _legacy_text(reply)):
                # postare publicată înainte de baza de date: schimbăm doar cifra
                new_text = set_price(old[0], price)
                if new_text is None:
                    raise ValueError("Nu găsesc rândul cu prețul în această postare.")
                await self._edit_text(new_post(
                    category="", product={}, price_lei=price, text_message_id=reply.message_id,
                    is_caption=old[1], message_ids=[], media_group_ids=[], media=[], thread_id=None,
                ), new_text)
            else:
                await msg.reply_text("Răspundeți cu /pret 750 la postarea botului sau la confirmarea ✅.")
                return
        except (TelegramError, ValueError) as exc:
            await msg.reply_text(f"❌ Nu am putut schimba prețul: {exc}")
            return

        if in_sales_group:
            try:
                await msg.delete()  # comanda nu rămâne vizibilă clientelor
            except TelegramError:
                pass
        else:
            await msg.reply_text(f"✅ Prețul a fost schimbat: {price} lei")

    async def _is_admin(self, chat_id: int, msg: Message) -> bool:
        if msg.sender_chat and msg.sender_chat.id == chat_id:
            return True  # administrator care scrie anonim, în numele grupului
        if msg.from_user is None:
            return False
        return await self.user_can_edit(msg.from_user.id, chats=(chat_id,))

    async def user_can_edit(self, user_id: int, chats: tuple | None = None) -> bool:
        """Proprietara/ajutoarele: din ALLOWED_USER_IDS, membre ale grupului privat sau admine în grupul de vânzare."""
        if user_id in self.cfg.allowed_user_ids:
            return True
        bot = self.app.bot
        checks = chats or (self.cfg.source_chat_id, self.cfg.target_chat_id)
        for chat_id in checks:
            if not chat_id:
                continue
            try:
                member = await bot.get_chat_member(chat_id, user_id)
            except TelegramError:
                continue
            if member.status in ("administrator", "creator", "owner"):
                return True
            if chat_id == self.cfg.source_chat_id and member.status in ("member", "restricted"):
                return True
        return False

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


def _legacy_text(msg: Message) -> tuple[str, bool] | None:
    """Textul (HTML) unei postări vechi și dacă e caption; None dacă nu are preț."""
    html_text = msg.caption_html if msg.caption else (msg.text_html if msg.text else None)
    if html_text and "Preț" in html_text:
        return html_text, bool(msg.caption)
    return None


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
