"""SQLite journal for every paper buy, reject and exit."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from desk.models import BuyPlan, ExitLeg, Position, Step

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    seat TEXT NOT NULL,
    kind TEXT NOT NULL,
    mint TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash_usd TEXT NOT NULL,
    starting_usd TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    mint TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    decimals INTEGER NOT NULL,
    qty_raw TEXT NOT NULL,
    initial_raw TEXT NOT NULL,
    entry_price_usd TEXT NOT NULL,
    cost_usd TEXT NOT NULL,
    proceeds_usd TEXT NOT NULL,
    last_price_usd TEXT NOT NULL,
    opened_ts TEXT NOT NULL,
    closed_ts TEXT,
    tp1_done INTEGER NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS equity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    equity_usd TEXT NOT NULL,
    cash_usd TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (
    mint TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    final INTEGER NOT NULL,
    reason TEXT NOT NULL,
    ts TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Journal:
    """One file, opened for the life of the process."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def ensure_account(self, starting: Decimal) -> None:
        with self._db:
            row = self._db.execute("SELECT cash_usd FROM account WHERE id = 1").fetchone()
            if row is None:
                text = format(starting, "f")
                self._db.execute(
                    "INSERT INTO account (id, cash_usd, starting_usd) VALUES (1, ?, ?)",
                    (text, text),
                )

    def has_account(self) -> bool:
        row = self._db.execute("SELECT 1 FROM account WHERE id = 1").fetchone()
        return row is not None

    def cash(self) -> Decimal:
        row = self._db.execute("SELECT cash_usd, starting_usd FROM account WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("account is not open")
        return Decimal(str(row["cash_usd"]))

    def starting(self) -> Decimal:
        row = self._db.execute("SELECT starting_usd FROM account WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("account is not open")
        return Decimal(str(row["starting_usd"]))

    def open_mints(self) -> frozenset[str]:
        rows = self._db.execute("SELECT mint FROM positions WHERE status = 'open'").fetchall()
        return frozenset(str(row["mint"]) for row in rows)

    def final_mints(self) -> frozenset[str]:
        rows = self._db.execute("SELECT mint FROM seen WHERE final = 1").fetchall()
        return frozenset(str(row["mint"]) for row in rows)

    def retry_mints(self) -> list[str]:
        rows = self._db.execute(
            "SELECT mint FROM seen WHERE final = 0 ORDER BY ts"
        ).fetchall()
        return [str(row["mint"]) for row in rows]

    def open_positions(self) -> list[Position]:
        rows = self._db.execute(
            "SELECT * FROM positions WHERE status = 'open' ORDER BY opened_ts"
        ).fetchall()
        return [_position(row) for row in rows]

    def all_positions(self) -> list[Position]:
        rows = self._db.execute("SELECT * FROM positions ORDER BY opened_ts").fetchall()
        return [_position(row) for row in rows]

    def equity_points(self) -> list[Decimal]:
        rows = self._db.execute("SELECT equity_usd FROM equity ORDER BY id").fetchall()
        return [Decimal(str(row["equity_usd"])) for row in rows]

    def record_steps(self, mint: str, steps: tuple[Step, ...], source: str) -> None:
        final = _is_final(steps)
        reason = steps[-1].reason if steps else ""
        with self._db:
            for step in steps:
                self._event(step.seat, step.kind, mint, step.reason, step.detail)
            self._seen(mint, source, final, reason)

    def apply_buy(self, mint: str, plan: BuyPlan, source: str) -> None:
        if not plan.accepted:
            raise RuntimeError("refused buy cannot open a position")
        symbol = plan.symbol or mint[:6]
        price = format(plan.price, "f")
        cost = format(plan.cost_usd, "f")
        with self._db:
            cash = self.cash()
            if plan.cost_usd > cash:
                raise RuntimeError("cash changed under the buy")
            self._db.execute(
                "UPDATE account SET cash_usd = ? WHERE id = 1",
                (format(cash - plan.cost_usd, "f"),),
            )
            self._db.execute(
                """
                INSERT INTO positions (
                    mint, symbol, decimals, qty_raw, initial_raw, entry_price_usd,
                    cost_usd, proceeds_usd, last_price_usd, opened_ts, closed_ts,
                    tp1_done, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '0', ?, ?, NULL, 0, 'open')
                """,
                (
                    mint,
                    symbol,
                    plan.decimals,
                    str(plan.tokens_raw),
                    str(plan.tokens_raw),
                    price,
                    cost,
                    price,
                    utc_now(),
                ),
            )
            for step in plan.steps:
                self._event(step.seat, step.kind, mint, step.reason, step.detail)
            self._seen(mint, source, True, plan.steps[-1].reason)

    def apply_exit(self, mint: str, leg: ExitLeg) -> None:
        with self._db:
            row = self._db.execute("SELECT * FROM positions WHERE mint = ?", (mint,)).fetchone()
            if row is None:
                raise RuntimeError("missing position")
            position = _position(row)
            remaining = position.qty_raw - leg.qty_raw
            if remaining < 0:
                raise RuntimeError("exit larger than the position")
            status = "closed" if remaining == 0 else "open"
            proceeds = position.proceeds_usd + leg.proceeds
            tp1 = position.tp1_done or leg.kind == "tp50"
            cash = self.cash()
            self._db.execute(
                "UPDATE account SET cash_usd = ? WHERE id = 1",
                (format(cash + leg.proceeds, "f"),),
            )
            self._db.execute(
                """
                UPDATE positions
                SET qty_raw = ?, proceeds_usd = ?, last_price_usd = ?, tp1_done = ?,
                    status = ?, closed_ts = ?
                WHERE mint = ?
                """,
                (
                    str(remaining),
                    format(proceeds, "f"),
                    format(leg.price, "f"),
                    1 if tp1 else 0,
                    status,
                    utc_now() if status == "closed" else None,
                    mint,
                ),
            )
            self._event(
                "EXIT",
                "exit",
                mint,
                leg.reason,
                {
                    "kind": leg.kind,
                    "fill_price_usd": format(leg.price, "f"),
                    "proceeds_usd": format(leg.proceeds, "f"),
                    "qty_raw": str(leg.qty_raw),
                    "entry_price_usd": format(position.entry_price, "f"),
                },
            )

    def mark(self, mint: str, price: Decimal) -> None:
        with self._db:
            self._db.execute(
                "UPDATE positions SET last_price_usd = ? WHERE mint = ?",
                (format(price, "f"), mint),
            )

    def snapshot(self, equity: Decimal) -> None:
        with self._db:
            self._event("JOURNAL", "mark", "", "평가액 기록", {"equity_usd": format(equity, "f")})
            self._db.execute(
                "INSERT INTO equity (ts, equity_usd, cash_usd) VALUES (?, ?, ?)",
                (utc_now(), format(equity, "f"), format(self.cash(), "f")),
            )

    def note(self, seat: str, reason: str, mint: str = "") -> None:
        with self._db:
            self._event(seat, "error", mint, reason, {})

    def _event(
        self, seat: str, kind: str, mint: str, reason: str, detail: dict[str, object]
    ) -> None:
        encoded = json.dumps(detail, ensure_ascii=False, default=str)
        self._db.execute(
            "INSERT INTO events (ts, seat, kind, mint, reason, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (utc_now(), seat, kind, mint, reason, encoded),
        )

    def _seen(self, mint: str, source: str, final: bool, reason: str) -> None:
        self._db.execute(
            """
            INSERT INTO seen (mint, source, final, reason, ts) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(mint) DO UPDATE SET
                source = excluded.source,
                final = excluded.final,
                reason = excluded.reason,
                ts = excluded.ts
            """,
            (mint, source, 1 if final else 0, reason, utc_now()),
        )


def _position(row: sqlite3.Row) -> Position:
    return Position(
        mint=str(row["mint"]),
        symbol=str(row["symbol"]),
        decimals=int(row["decimals"]),
        qty_raw=int(row["qty_raw"]),
        initial_raw=int(row["initial_raw"]),
        entry_price=Decimal(str(row["entry_price_usd"])),
        cost_usd=Decimal(str(row["cost_usd"])),
        proceeds_usd=Decimal(str(row["proceeds_usd"])),
        last_price=Decimal(str(row["last_price_usd"])),
        tp1_done=bool(row["tp1_done"]),
        status=str(row["status"]),
    )


def _is_final(steps: tuple[Step, ...]) -> bool:
    if not steps:
        return False
    reason = steps[-1].reason
    if steps[-1].kind == "buy":
        return True
    return "오래" not in reason and "확인 실패" not in reason
