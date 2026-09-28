"""Setările botului, citite din variabile de mediu (fișierul .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Lipsește variabila de mediu obligatorie: {name}")
    return value


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)).replace(",", "."))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "da", "yes", "on"}


def _env_list(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class PricingConfig:
    # Preț în grivne × UAH_TO_LEI
    uah_to_lei: float = 0.50
    # Preț în dolari × USD_TO_LEI
    usd_to_lei: float = 20.0
    # Cheltuieli fixe / transport, în lei
    shipping_lei: int = 100
    # Terminațiile „comerciale" permise (ultimele două cifre ale prețului)
    nice_endings: tuple[int, ...] = (50, 80, 90)
    # Ordinea în care se alege prețul producătorului dacă sunt mai multe
    price_priority: tuple[str, ...] = ("drop", "opt", "unspecified", "retail")


@dataclass(frozen=True)
class Config:
    telegram_token: str
    # 0 = încă nesetat (botul pornește doar cu comanda /id, pentru aflarea ID-urilor)
    source_chat_id: int
    # ID numeric (-100...) sau @numele canalului public
    target_chat_id: int | str
    report_chat_id: int
    anthropic_model: str = "claude-haiku-4-5"
    anthropic_effort: str = "medium"
    allowed_user_ids: frozenset[int] = frozenset()
    # Secunde de așteptare după ultima poză dintr-un album
    album_wait: float = 3.0
    # Cât așteaptă pozele (oricâte albume) textul care închide produsul
    photos_wait: float = 180.0
    # Cât așteaptă un text trimis ÎNAINTEA pozelor ca pozele să vină
    pair_wait: float = 25.0
    # Secunde de liniște în grup după care produsele adunate sunt publicate
    # (sortate pe categorii). 0 = publicare imediată.
    publish_delay: float = 60.0
    confirm_in_source: bool = True
    # Folderul unde botul ține minte topicurile învățate
    data_dir: str = "data"
    # Legăturile categorie=topic salvate permanent (generate de comanda /topics)
    topics: str = ""
    pricing: PricingConfig = field(default_factory=PricingConfig)

    @property
    def is_configured(self) -> bool:
        return bool(self.source_chat_id) and bool(self.target_chat_id)

    @classmethod
    def from_env(cls) -> "Config":
        source = int(os.environ.get("SOURCE_CHAT_ID") or 0)
        target_raw = (os.environ.get("TARGET_CHAT_ID") or "0").strip()
        target: int | str = target_raw if target_raw.startswith("@") else int(target_raw)
        endings = tuple(int(x) for x in _env_list("PRICE_ENDINGS", "50,80,90"))
        priority = tuple(_env_list("PRICE_PRIORITY", "drop,opt,unspecified,retail"))
        allowed = frozenset(int(x) for x in _env_list("ALLOWED_USER_IDS", ""))
        return cls(
            telegram_token=_env("TELEGRAM_BOT_TOKEN"),
            source_chat_id=source,
            target_chat_id=target,
            report_chat_id=int(os.environ.get("REPORT_CHAT_ID") or source),
            anthropic_model=os.environ.get("ANTHROPIC_MODEL") or "claude-haiku-4-5",
            anthropic_effort=os.environ.get("ANTHROPIC_EFFORT") or "medium",
            allowed_user_ids=allowed,
            album_wait=_env_float("ALBUM_WAIT_SECONDS", 3.0),
            photos_wait=_env_float("PHOTOS_WAIT_SECONDS", 180.0),
            pair_wait=_env_float("PAIR_WAIT_SECONDS", 25.0),
            publish_delay=_env_float("PUBLISH_DELAY_SECONDS", 60.0),
            confirm_in_source=_env_bool("CONFIRM_IN_SOURCE", True),
            data_dir=os.environ.get("DATA_DIR") or "data",
            topics=os.environ.get("TOPICS", ""),
            pricing=PricingConfig(
                uah_to_lei=_env_float("UAH_TO_LEI", 0.50),
                usd_to_lei=_env_float("USD_TO_LEI", 20.0),
                shipping_lei=_env_int("SHIPPING_LEI", 100),
                nice_endings=endings,
                price_priority=priority,
            ),
        )
