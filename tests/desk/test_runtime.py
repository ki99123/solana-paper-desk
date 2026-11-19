"""Fixture cycle and the CLI paper command. The PumpGrok ledger stays empty."""

import io
import json
import shutil
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path

from desk.daemon import main

from pumpgrok.cli import main as cli_main
from pumpgrok.ledger.store import LedgerStore

ROOT = Path(__file__).resolve().parent / "fixtures" / "happy"
MINT = "Mint11111111111111111111111111111111"
TICKET = "SOL-20260101-001"
APPROVAL = "apr-01ARZ3NDEKTSV4RRFFQ69G5FAV"


def test_fixture_cycle_buys_twenty_dollars_and_reports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    code = main(["run", "--once", "--fixture", str(ROOT)])
    assert code == 0
    journal = sqlite3.connect(tmp_path / "var" / "desk.sqlite")
    cash = journal.execute("SELECT cash_usd FROM account").fetchone()[0]
    kinds = [row[0] for row in journal.execute("SELECT kind FROM events")]
    journal.close()
    assert DecimalCash(cash) == "980"
    assert "buy" in kinds
    text = _report()
    assert "총수익률" in text
    assert "승률" in text


def test_cli_buy_fills_from_a_fixture_and_skips_the_ledger(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    fixture = _sol_fixture(tmp_path / "fixture")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUMPGROK_DESK__FIXTURE", str(fixture))
    code = cli_main(_flags("buy", "0.2"))
    captured = capsys.readouterr()
    assert code == 0
    body = json.loads(captured.out)
    assert body["ok"] is True
    assert body["paper"] is True
    assert body["side"] == "buy"
    assert _ledger_kinds(tmp_path) == []
    journal = sqlite3.connect(tmp_path / "var" / "desk.sqlite")
    try:
        reason = journal.execute("SELECT reason FROM events WHERE kind = 'buy'").fetchone()[0]
    finally:
        journal.close()
    assert "모의 매수" in reason


def test_cli_without_a_quote_refuses_and_records_the_reason(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PUMPGROK_DESK__FIXTURE", raising=False)
    code = cli_main(_flags("buy", "0.2"))
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 1
    assert payload["code"] == "unavailable"
    assert "verified curve price source" in payload["message"]
    assert _ledger_kinds(tmp_path) == []
    journal = sqlite3.connect(tmp_path / "var" / "desk.sqlite")
    try:
        reasons = [row[0] for row in journal.execute("SELECT reason FROM events")]
    finally:
        journal.close()
    assert any("확인 실패" in reason for reason in reasons)


def _flags(side: str, size: str) -> list[str]:
    return [
        "paper",
        side,
        "--ticket",
        TICKET,
        "--mint",
        MINT,
        "--size-sol",
        size,
        "--slippage-bps",
        "100",
        "--approval-id",
        APPROVAL,
        "--seat",
        "bot-04-sniper",
    ]


def _sol_fixture(dest: Path) -> Path:
    shutil.copytree(ROOT, dest)
    quote = {
        "inputMint": "So11111111111111111111111111111111111111112",
        "outputMint": MINT,
        "inAmount": "200000000",
        "outAmount": "1000000",
        "minOut": "990000",
        "platformFeeOut": 0,
        "swapUsd": "30",
    }
    (dest / "quote.json").write_text(json.dumps(quote), encoding="utf-8")
    return dest


def _ledger_kinds(tmp_path: Path) -> list[str]:
    path = tmp_path / "var" / "ledger.sqlite"
    if not path.is_file():
        return []
    store = LedgerStore(str(path))
    try:
        return [str(event["type"]) for event in store.read(limit=100)]
    finally:
        store.close()


def _report() -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert main(["report"]) == 0
    return buffer.getvalue()


def DecimalCash(value: object) -> str:
    return format(value, "f") if not isinstance(value, str) else value
