"""The paper desk has no signer, no swap submit, and no key material."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "desk"
BANNED = (
    "sendTransaction",
    "send_transaction",
    "Keypair",
    "solders",
    "private_key",
    "mnemonic",
    "/swap/v1/swap",
    "/execute",
)


def test_desk_source_has_no_signing_or_live_order() -> None:
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for word in BANNED:
            if word in text:
                offenders.append(f"{path.name}:{word}")
    assert offenders == []
