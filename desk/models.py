"""Desk records shared by the gates, the journal and the report."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class Candidate:
    mint: str
    symbol: str
    source: str


@dataclass(frozen=True)
class Read:
    name: str
    ok: bool
    fetched_at: float
    payload: dict[str, object]
    error: str = ""


@dataclass(frozen=True)
class Step:
    seat: str
    kind: str
    reason: str
    detail: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class BuyPlan:
    steps: tuple[Step, ...]
    accepted: bool
    tokens_raw: int = 0
    cost_usd: Decimal = Decimal(0)
    price: Decimal = Decimal(0)
    decimals: int = 0
    symbol: str = ""


@dataclass(frozen=True)
class Position:
    mint: str
    symbol: str
    decimals: int
    qty_raw: int
    initial_raw: int
    entry_price: Decimal
    cost_usd: Decimal
    proceeds_usd: Decimal
    last_price: Decimal
    tp1_done: bool
    status: str


@dataclass(frozen=True)
class ExitLeg:
    kind: str
    qty_raw: int
    price: Decimal
    proceeds: Decimal
    reason: str


@dataclass(frozen=True)
class ExitPlan:
    legs: tuple[ExitLeg, ...]
    defer_reason: str = ""


@dataclass(frozen=True)
class BookView:
    cash_usd: Decimal
    open_mints: frozenset[str]
    final_mints: frozenset[str]
