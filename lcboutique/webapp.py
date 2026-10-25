"""Mini-aplicația Telegram: pagina de editare a postărilor + API-ul ei (aiohttp).

Se deschide din chatul privat cu botul (butonul „📝 Editare"). Fiecare cerere e verificată
cu semnătura Telegram (initData), iar accesul îl au doar proprietara și ajutoarele ei.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, get_args
from urllib.parse import parse_qsl

from aiohttp import web

from .models import TOPICS, Category

if TYPE_CHECKING:
    from .bot import LCBoutiqueBot

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
MAX_AUTH_AGE = 24 * 3600
TEXT_FIELDS = ("title", "code", "sizes", "material", "composition")
LIST_FIELDS = ("colors", "measurements")


def check_init_data(init_data: str, bot_token: str, now: float | None = None) -> dict | None:
    """Verifică semnătura Telegram a mini-aplicației; întoarce utilizatorul sau None."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received = pairs.pop("hash", "")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not received or not hmac.compare_digest(expected, received):
        return None
    if (now or time.time()) - int(pairs.get("auth_date", "0")) > MAX_AUTH_AGE:
        return None
    try:
        return json.loads(pairs.get("user", "{}")) or None
    except ValueError:
        return None


class WebApp:
    def __init__(self, bot: "LCBoutiqueBot") -> None:
        self.bot = bot
        self._allowed: dict[int, float] = {}  # utilizator → până când e valabilă verificarea
        self._photos: OrderedDict[str, bytes] = OrderedDict()
        self.app = web.Application(middlewares=[self._auth])
        self.app.add_routes(
            [
                web.get("/", self.index),
                web.get("/api/meta", self.meta),
                web.get("/api/posts", self.list_posts),
                web.get("/api/posts/{id}", self.get_post),
                web.put("/api/posts/{id}", self.update_post),
                web.delete("/api/posts/{id}", self.delete_post),
                web.get("/api/posts/{id}/photo", self.photo),
                web.put("/api/settings", self.update_settings),
            ]
        )

    # ---------- securitate ----------

    @web.middleware
    async def _auth(self, request: web.Request, handler):
        if not request.path.startswith("/api/"):
            return await handler(request)
        user = check_init_data(request.headers.get("X-Init-Data", ""), self.bot.cfg.telegram_token)
        if not user or "id" not in user:
            return web.json_response({"error": "Deschideți pagina din Telegram."}, status=401)
        user_id = int(user["id"])
        if self._allowed.get(user_id, 0) < time.time():
            if not await self.bot.user_can_edit(user_id):
                return web.json_response({"error": "Nu aveți acces la editare."}, status=403)
            self._allowed[user_id] = time.time() + 600
        return await handler(request)

    # ---------- pagini ----------

    async def index(self, request: web.Request) -> web.Response:
        return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    async def meta(self, request: web.Request) -> web.Response:
        pricing = self.bot.pricing
        return web.json_response(
            {
                "topics": [
                    {"key": k, "name": n, "learned": self.bot.topics.thread_for(k) is not None}
                    for k, n in TOPICS.items()
                ],
                "settings": {
                    "uah_to_lei": pricing.uah_to_lei,
                    "usd_to_lei": pricing.usd_to_lei,
                    "shipping_lei": pricing.shipping_lei,
                },
            }
        )

    async def list_posts(self, request: web.Request) -> web.Response:
        q = request.query
        posts = self.bot.storage.list_posts(
            query=q.get("q", "").strip(), category=q.get("category", ""), offset=int(q.get("offset", 0) or 0)
        )
        return web.json_response([self._summary(p) for p in posts])

    async def get_post(self, request: web.Request) -> web.Response:
        post = self._post_or_404(request)
        return web.json_response({**self._summary(post), "product": post.product, "calc": post.calc})

    async def update_post(self, request: web.Request) -> web.Response:
        post = self._post_or_404(request)
        try:
            body = await request.json()
            product = dict(post.product)
            for key in TEXT_FIELDS:
                if key in body:
                    product[key] = str(body[key]).strip()
            for key in LIST_FIELDS:
                if key in body:
                    product[key] = [str(x).strip() for x in body[key] if str(x).strip()]
            price = int(body.get("price_lei", post.price_lei))
            category = body.get("category", post.category)
            if category not in TOPICS or price <= 0 or not product.get("title"):
                raise ValueError("Verificați titlul, prețul și topicul.")
            if category in get_args(Category):
                product["category"] = category
            post = await self.bot.edit_post(post, product, price, category)
        except (ValueError, TypeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:  # erori Telegram
            log.exception("Editarea postării %s a eșuat", post.id)
            return web.json_response({"error": f"Telegram: {exc}"}, status=502)
        return web.json_response(self._summary(post))

    async def delete_post(self, request: web.Request) -> web.Response:
        post = self._post_or_404(request)
        await self.bot.delete_post(post)
        return web.json_response({"ok": True})

    async def photo(self, request: web.Request) -> web.Response:
        post = self._post_or_404(request)
        photos = [m for m in post.media if m.get("kind") == "photo"]
        if not photos:
            raise web.HTTPNotFound()
        file_id = photos[0].get("preview_id") or photos[0]["file_id"]
        data = self._photos.get(file_id)
        if data is None:
            data = await self.bot._download(file_id)
            self._photos[file_id] = data
            while len(self._photos) > 300:
                self._photos.popitem(last=False)
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    async def update_settings(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            for key in self.bot.EDITABLE_SETTINGS:
                if key in body:
                    value = float(str(body[key]).replace(",", "."))
                    if value < 0:
                        raise ValueError
                    self.bot.storage.set_setting(key, str(value))
        except (ValueError, TypeError):
            return web.json_response({"error": "Valori invalide."}, status=400)
        return await self.meta(request)

    # ---------- utile ----------

    def _post_or_404(self, request: web.Request):
        try:
            post = self.bot.storage.get_post(int(request.match_info["id"]))
        except ValueError:
            post = None
        if post is None:
            raise web.HTTPNotFound()
        return post

    def _summary(self, post) -> dict:
        product = post.product
        return {
            "id": post.id,
            "title": product.get("title", ""),
            "code": product.get("code", ""),
            "price_lei": post.price_lei,
            "category": post.category,
            "topic": TOPICS.get(post.category, post.category),
            "created_at": post.created_at,
            "photos": sum(1 for m in post.media if m.get("kind") == "photo"),
            "link": self.bot._post_link(post.text_message_id),
        }


async def start_webapp(bot: "LCBoutiqueBot", port: int) -> web.AppRunner:
    runner = web.AppRunner(WebApp(bot).app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Mini-aplicația ascultă pe portul %s", port)
    return runner
