"""Legătura dintre categoriile produselor și topicurile din grupul de vânzare.

Telegram nu permite botului să citească lista de topicuri, așa că numărul fiecărui
topic se învață: scrieți /id în fiecare topic, o singură dată. Legăturile se țin în
fișierul data/topics.json și pot fi salvate permanent în variabila TOPICS
(comanda /topics o generează).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path

from .models import TOPICS

log = logging.getLogger(__name__)


def normalize(name: str) -> str:
    """„Rochițe 👗" → „rochite": fără diacritice, emoji, semne sau spații în plus."""
    text = unicodedata.normalize("NFKD", name.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


_BY_NAME = {normalize(name): key for key, name in TOPICS.items()}


def match_topic(name: str) -> str | None:
    """Categoria care corespunde numelui unui topic din Telegram (sau None)."""
    norm = normalize(name)
    if not norm:
        return None
    if norm in _BY_NAME:
        return _BY_NAME[norm]
    # numele din Telegram poate avea cuvinte în plus (ex. „Rochițe noi 2025")
    candidates = [key for known, key in _BY_NAME.items() if known in norm or norm in known]
    return max(candidates, key=lambda k: len(normalize(TOPICS[k]))) if candidates else None


def parse_env(raw: str) -> dict[str, int]:
    """TOPICS=rochite=12;costume=15 (acceptă și numele topicului în loc de cheie)."""
    result: dict[str, int] = {}
    for part in re.split(r"[;\n]", raw or ""):
        if "=" not in part:
            continue
        name, _, thread = part.rpartition("=")
        key = name.strip() if name.strip() in TOPICS else match_topic(name)
        if key and thread.strip().isdigit():
            result[key] = int(thread.strip())
    return result


class TopicStore:
    def __init__(self, path: Path | None, env_value: str = "") -> None:
        self.path = path
        self.threads: dict[str, int] = {}
        if path and path.exists():
            try:
                self.threads.update({k: int(v) for k, v in json.loads(path.read_text()).items() if k in TOPICS})
            except (ValueError, OSError):
                log.exception("Nu am putut citi %s", path)
        self.threads.update(parse_env(env_value))

    def thread_for(self, category: str) -> int | None:
        return self.threads.get(category)

    def learn(self, topic_name: str, thread_id: int) -> str | None:
        """Ține minte topicul; întoarce categoria recunoscută (sau None)."""
        key = match_topic(topic_name)
        if key and self.threads.get(key) != thread_id:
            self.threads[key] = thread_id
            self._save()
            log.info("Topic învățat: %s → %s", TOPICS[key], thread_id)
        return key

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.threads, indent=1))
        except OSError:
            log.exception("Nu am putut salva %s", self.path)

    def env_line(self) -> str:
        return ";".join(f"{key}={self.threads[key]}" for key in TOPICS if key in self.threads)

    def status(self) -> str:
        lines = [f"{'✅' if key in self.threads else '❌'} {name}" for key, name in TOPICS.items()]
        return "\n".join(lines)
