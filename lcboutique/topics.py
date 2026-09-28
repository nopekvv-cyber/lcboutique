"""Legătura dintre categoriile produselor și topicurile din grupul de vânzare.

Telegram nu permite botului să citească lista de topicuri, așa că numărul fiecărui
topic se învață: scrieți /id în fiecare topic, o singură dată. Legăturile se țin în
fișierul data/topics.json și pot fi salvate permanent în variabila TOPICS
(comanda /topics o generează).
"""

from __future__ import annotations

import difflib
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
# alte denumiri uzuale pentru aceleași topicuri
_BY_NAME.update(
    {
        "geci": "scurte_trenciuri",
        "scurte": "scurte_trenciuri",
        "trenciuri": "scurte_trenciuri",
        "maieuri": "maiouri",
        "maiou": "maiouri",
        "rochite": "rochite",
        "rochii": "rochite",
        "body": "malete_body",
        "malete": "malete_body",
        "fuste": "fustite_sorti",
        "bluze": "bluzite",
        "barbati": "barbati",
    }
)


def match_topic(name: str) -> str | None:
    """Categoria care corespunde numelui unui topic din Telegram (sau None)."""
    norm = normalize(name)
    if not norm:
        return None
    if norm in _BY_NAME:
        return _BY_NAME[norm]
    # numele din Telegram poate avea cuvinte în plus (ex. „Rochițe noi 2025")
    candidates = [key for known, key in _BY_NAME.items() if known in norm or norm in known]
    if candidates:
        return max(candidates, key=lambda k: len(normalize(TOPICS[k])))
    # greșeli mici de scriere (ex. „Maieuri", „Scurte trenchuri")
    close = difflib.get_close_matches(norm, list(_BY_NAME), n=1, cutoff=0.75)
    if close:
        return _BY_NAME[close[0]]
    # un singur cuvânt din nume (ex. „scurte", „maiouri", „trenciuri")
    words = set(norm.split())
    hits = [key for known, key in _BY_NAME.items() if words & set(known.split()) - {"costume"}]
    return hits[0] if len(hits) == 1 else None


# Topicurile se salvează și în descrierea botului (Telegram), ca să nu se piardă la redeploy.
# Codificare compactă: poziția topicului în TOPICS (se adaugă topicuri noi doar la final!).
_KEYS = list(TOPICS)
DESCRIPTION_MARKER = "topicuri:"


def encode(threads: dict[str, int]) -> str:
    pairs = " ".join(f"{_KEYS.index(k)}.{v}" for k, v in threads.items() if k in TOPICS)
    return f"Asistentul automat LC boutique.\n\n{DESCRIPTION_MARKER} {pairs}"


def decode(text: str | None) -> dict[str, int]:
    if not text or DESCRIPTION_MARKER not in text:
        return {}
    result = {}
    for idx, thread in re.findall(r"(\d+)\.(\d+)", text.split(DESCRIPTION_MARKER, 1)[1]):
        if int(idx) < len(_KEYS):
            result[_KEYS[int(idx)]] = int(thread)
    return result


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

    def merge_saved(self, saved: dict[str, int]) -> None:
        """Adaugă legăturile salvate în Telegram (fără a le suprascrie pe cele din fișier/variabilă)."""
        for key, thread in saved.items():
            self.threads.setdefault(key, thread)

    def learn(self, topic_name: str, thread_id: int) -> str | None:
        """Ține minte topicul după nume; întoarce categoria recunoscută (sau None)."""
        key = match_topic(topic_name)
        if key:
            self.assign(key, thread_id)
        return key

    def assign(self, key: str, thread_id: int) -> bool:
        """Leagă categoria de topic. Întoarce True dacă s-a schimbat ceva."""
        # un topic aparține unei singure categorii
        stale = [k for k, t in self.threads.items() if t == thread_id and k != key]
        if self.threads.get(key) == thread_id and not stale:
            return False
        for k in stale:
            del self.threads[k]
        self.threads[key] = thread_id
        self._save()
        log.info("Topic: %s → %s", TOPICS[key], thread_id)
        return True

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
