# PumpGrok

![PumpGrok banner](banner.jpg)

PumpGrok is a ten-seat Solana memecoin trading desk packaged as agent instructions, 35 skills, a hard security constitution, and Python helpers that never sign or send. The helpers write only local files, mainly under `var/`. It is loaded into a host agent runtime (Grok Bot, Cursor, Claude Code, or Grok Build). It is not a trading bot, exchange client, or signer: private keys never enter the system, and only a human-approved ticket may be sent.

Version 0.1.0, the package version (see `pyproject.toml`).

## 솔라나 밈코인 모의투자 데스크

이 저장소는 PumpGrok 위에 모의투자 전용 데스크를 얹은 것이다. 지갑, 개인키, 서명, 실주문은 없다. Jupiter는 견적 조회만 하고, 스왑을 만들거나 보내지 않는다. 체결은 모두 가상 장부 `var/desk.sqlite`에 남는다.

좌석 순서는 SCOUT(신규 코인) → RUG(러그 점검) → WHALE(공개 지갑 관찰) → RISK(거부권) → SNIPER(모의 체결) → EXIT(손절·익절) → JOURNAL(기록)이다. 점수는 쓰지 않는다. 통과하지 못하면 사지 않는다. 데이터가 10초보다 오래됐거나 확인에 실패하면 매수하지 않는다.

가상 시드는 1,000달러이고 한 번 매수는 20달러다. 손절은 -25%이고, 가격이 손절선 아래로 갭을 만들면 손절선이 아니라 그 견적의 실제 체결가를 기록한다. 익절은 +50%와 +100%에서 처음 수량의 절반씩이다. 체결가는 견적의 최소 수령액에서 슬리피지와 수수료를 뺀 값이다. 한 주문은 풀 유동성의 2% 이하다. 2%는 허용되고, 그보다 크면 거절된다.

데이터는 PumpPortal 실시간 신규 코인, DexScreener 신규 토큰, RugCheck, 솔라나 공개 RPC, Jupiter 견적 API다. 공개 RPC는 요청이 거절될 수 있다. 그때는 가격을 지어내지 않고 매수를 거절하거나 청산을 다음 회차로 미룬다.

### 설치와 실행

Python 3.12가 필요하다.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

`.env.example`의 값은 비어 있거나 숫자다. 키를 코드에 넣지 않는다. Jupiter와 RugCheck 키는 비워 두면 공개 엔드포인트를 그대로 쓴다. 복사한 `.env`는 실행 전에 불러야 한다.

```bash
set -a
source .env
set +a
export PYTHONPATH=src
python -m desk run
```

한 사이클만 돌려 보고 끝내려면 샘플 피처를 쓴다. 이 샘플은 동작 확인용이며 수익을 보장하지 않는다.

```bash
PYTHONPATH=src python -m desk run --once --fixture tests/desk/fixtures/happy
PYTHONPATH=src python -m desk report
```

성과 요약은 승률, 총수익률, 최대낙폭을 보여 주고, 같은 매수 수량을 청산하지 않고 마지막 평가 시점까지 들고 있는 단순 보유와 비교한다. 둘 다 매도 슬리피지와 수수료를 뺀다. 시간은 한국 표준시다. 장부를 비우려면 `var/desk.sqlite`를 지운다.

수동 모의 주문은 견적이 신선하고 게이트를 통과할 때만 `var/desk.sqlite`에 체결된다. 그렇지 않으면 종료 코드 `unavailable`이고, 메시지에는 여전히 `verified curve price source`가 들어 있다. 검증되지 않은 본딩커브 레이아웃은 가격으로 쓰지 않는다. PumpGrok 원장 `var/ledger.sqlite`에는 주문을 쓰지 않는다.

```bash
PYTHONPATH=src python -m pumpgrok paper buy \
  --ticket SOL-20260101-001 \
  --mint <mint> \
  --size-sol 0.2 \
  --slippage-bps 100 \
  --approval-id apr-01ARZ3NDEKTSV4RRFFQ69G5FAV \
  --seat bot-04-sniper
```

### 화면 없이 계속 실행

프로세스가 죽으면 다시 띄운다.

```bash
chmod +x scripts/run-forever.sh
./scripts/run-forever.sh
```

systemd를 쓸 때는 `deploy/memedesk.service`의 작업 디렉터리와 파이썬 경로를 서버에 맞게 고친 뒤 설치한다. `Restart=always`라서 오류로 빠져도 5초 뒤에 다시 시작된다. 유닛 파일에는 비밀값을 적지 않는다.

이 데스크는 Spectrum Web Co LLC / swcstudiospace의 PumpGrok을 바탕으로 하며 GNU AGPL-3.0을 따른다. 원본은 https://github.com/swcstudiospace/PumpGrok 이다.

## Capabilities

- Ten specialist seats: CHIEF, SCOUT, RISK, WHALE, SNIPER, RUG, EXIT, SHILL, SIGNAL, JOURNAL (`agents/`)
- 35 skills covering desk constitution, ticket lifecycle, risk audit, Jupiter routing, discovery, journal conventions, paper-trading discipline, safety scoring, and the screening-engine procedure (`skills/grokbot-pipeline`; the vendored tree is retired, see `docs/VENDOR_PIN.md`)
- Always-on desk rule `rules/pumpgrok-team.mdc` (RISK veto, human approval by ticket ID, single-send, no private keys)
- Operator CLI (`pumpgrok`) that never signs or sends. It reads Jupiter quotes, mint/freeze authority, priority fees, and holder concentration; `ticket next` and `create` append a row to the paper ledger `var/ledger.sqlite`. Paper-trade commands simulate a fill in `var/desk.sqlite` when a fresh public quote and the pass/fail gates hold, and otherwise refuse with code `unavailable` (the message still names a verified curve price source). They write no order on the PumpGrok ledger. They still create the local folders, load or create the binding key, open the ledger and seed its starting equity before that decision. `scout pipeline` is registered but not implemented
- Repo linter `scripts/check.sh` (frontmatter, constitution phrases, one-writer convention; no network)
- Plugin manifests for Grok Bot (`plugin.json`), Claude Code (`.claude-plugin/`), Cursor (`.cursor-plugin/`), and Grok Build (`.grok-plugin/`)

The desk starts in **research** mode. **paper** is the simulated-fill level: `pumpgrok run --mode paper --once --replay <dir>` drives the paper adapter on a recorded dataset. `pumpgrok paper buy|sell` fills into `var/desk.sqlite` only from a fresh public quote. Without one it refuses with code `unavailable` and does not price from the unverified bonding-curve layout. There is no live-capital level on this desk: entries beyond paper require an explicit user decision outside these instructions. The desk ships no strategies and makes no return claims.

## Requirements

- A host that can load `agents/`, `skills/`, and `rules/` (Grok Bot, Cursor, Claude Code, or Grok Build)
- Python 3.12 via `uv` (`uv sync` installs the locked deps; `scripts/check.sh` is stdlib only)
- Optional: a private Solana RPC URL via `PUMPGROK_RPC__ENDPOINTS` (reads default to `https://api.mainnet-beta.solana.com`)

A committed `uv.lock` pins every dependency. No API keys are required for the public endpoints the reads call.

## Setup and usage

Full Grok Bot bootstrap (clone, folders, skills, ten seats, Trading Floor, desk record) is in [SETUP.md](SETUP.md). That flow writes only local folders and files, with no keys and no real trades.

From this repository:

```bash
./scripts/check.sh
```

Exit 0 means the instruction tree looks healthy.

Optional reads (schemas print without keys or spends):

```bash
uv run pumpgrok tools market.price --help
uv run pumpgrok tools onchain.authorities --help
uv run pumpgrok tools onchain.priority_fee --help
uv run pumpgrok ticket next --help
uv run pumpgrok tools market.holders_snapshot --help
uv run pumpgrok paper buy --help
uv run pumpgrok scout pipeline --candidates --help
```

Working files for a desk live under `var/` (append-only ledger `var/ledger.sqlite`, operational dir `var/desk/`), not inside the instruction tree. Allocate and list tickets with `pumpgrok ticket next|create|list`, never loose files.

Cursor / Claude Code / Grok Build load `skills/`, `agents/`, and `rules/` from the plugin manifests. On runtimes without persistent Bots, `rules/pumpgrok-team.mdc` says to use subagents or role-labelled passes.

## Project layout

| Path | Role |
|------|------|
| `agents/` | Ten seat definitions plus standing instructions |
| `skills/` | Thirty-five `SKILL.md` procedures |
| `rules/` | Always-applied desk constitution (`pumpgrok-team.mdc`) |
| `src/pumpgrok/` | Operator CLI and tool registry (JSON on stdout; never sign or send) |
| `scripts/` | `check.sh` repository linter |
| `src/pumpgrok/engine/` | Screening engine. Formerly the vendored `vendor/grokbot-pumpfun/` tree, retired in Phase 4; pin `409e74c905faa0e9de42e918efe2c604f206856e` kept as history in `docs/VENDOR_PIN.md`; desk-facing procedure in `skills/grokbot-pipeline` |
| `plugin.json` | Root agent-plugins manifest |
| `.claude-plugin/`, `.cursor-plugin/`, `.grok-plugin/` | Host-specific plugin metadata |
| `SETUP.md` | Step-by-step Grok Bot desk bootstrap |
| `banner.jpg` | README hero art |

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — module boundaries, ticket flow, persistence
- [SETUP.md](SETUP.md) — bootstrap procedure
- [LICENSE](LICENSE) — GNU Affero General Public License v3.0

Subdirectory READMEs: [agents/README.md](agents/README.md), [skills/README.md](skills/README.md), [rules/README.md](rules/README.md), [scripts/README.md](scripts/README.md).

## Ownership and license

Copyright (c) 2026 Spectrum Web Co LLC. Associated identity: swcstudiospace.

PumpGrok is licensed under the GNU Affero General Public License v3.0. See [LICENSE](LICENSE).
