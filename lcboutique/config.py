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
    # Cheltuieli fixe / transport, în lei
    shipping_lei: int = 100
    # Terminațiile „comerciale" permise (ultimele două cifre ale prețului)
    nice_endings: tuple[int, ...] = (50, 80, 90)
    # Ordinea în care se alege prețul producătorului dacă sunt mai multe
    price_priority: tuple[str, ...] = ("drop", "opt", "unspecified", "retail")


@dataclass(frozen=True)
class Config:
    telegram_token: str
    source_chat_id: int
    target_chat_id: int
    report_chat_id: int
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: str = "medium"
    allowed_user_ids: frozenset[int] = frozenset()
    # Secunde de așteptare după ultima poză dintr-un album
    album_wait: float = 3.0
    # Secunde în care un text și pozele postate separat sunt unite în același produs
    pair_wait: float = 25.0
    # Secunde de liniște în grup după care produsele adunate sunt publicate
    # (sortate pe categorii). 0 = publicare imediată.
    publish_delay: float = 60.0
    confirm_in_source: bool = True
    pricing: PricingConfig = field(default_factory=PricingConfig)

    @classmethod
    def from_env(cls) -> "Config":
        source = int(_env("SOURCE_CHAT_ID"))
        endings = tuple(int(x) for x in _env_list("PRICE_ENDINGS", "50,80,90"))
        priority = tuple(_env_list("PRICE_PRIORITY", "drop,opt,unspecified,retail"))
        allowed = frozenset(int(x) for x in _env_list("ALLOWED_USER_IDS", ""))
        return cls(
            telegram_token=_env("TELEGRAM_BOT_TOKEN"),
            source_chat_id=source,
            target_chat_id=int(_env("TARGET_CHAT_ID")),
            report_chat_id=int(os.environ.get("REPORT_CHAT_ID") or source),
            anthropic_model=os.environ.get("ANTHROPIC_MODEL") or "claude-opus-5",
            anthropic_effort=os.environ.get("ANTHROPIC_EFFORT") or "medium",
            allowed_user_ids=allowed,
            album_wait=_env_float("ALBUM_WAIT_SECONDS", 3.0),
            pair_wait=_env_float("PAIR_WAIT_SECONDS", 25.0),
            publish_delay=_env_float("PUBLISH_DELAY_SECONDS", 60.0),
            confirm_in_source=_env_bool("CONFIRM_IN_SOURCE", True),
            pricing=PricingConfig(
                uah_to_lei=_env_float("UAH_TO_LEI", 0.50),
                shipping_lei=_env_int("SHIPPING_LEI", 100),
                nice_endings=endings,
                price_priority=priority,
            ),
        )
