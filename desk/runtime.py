"""Process helpers shared by the CLI paper commands and the daemon."""

from __future__ import annotations

import os
from pathlib import Path

from desk.feeds import PUBLIC_RPC, FixtureMarket, LiveMarket
from pumpgrok.config.sections.desk import DeskSettings


def journal_path(cfg: DeskSettings) -> Path:
    text = cfg.journal.strip()
    return Path(text) if text else Path("var") / "desk.sqlite"


def market_for(cfg: DeskSettings) -> FixtureMarket | LiveMarket:
    fixture = cfg.fixture.strip()
    if fixture:
        return FixtureMarket(Path(fixture))
    return LiveMarket(
        rpc_url=cfg.rpc_url.strip() or PUBLIC_RPC,
        quote_url=cfg.jupiter_quote_url,
        jupiter_key=cfg.jupiter_api_key.get_secret_value().strip(),
        rugcheck_key=cfg.rugcheck_api_key.get_secret_value().strip(),
    )


def load_env_file(path: Path) -> None:
    """Fill missing environment variables from a local file. Values are never logged."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.replace("_", "").isalnum():
            continue
        cleaned = value.split("#", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key, cleaned)
