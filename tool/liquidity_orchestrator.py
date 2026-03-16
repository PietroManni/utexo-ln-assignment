import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = PROJECT_ROOT / "logs" / "attempts.jsonl"

DEFAULT_NETWORK = "testnet"
DEFAULT_PAYER = "cln_a"
DEFAULT_PAYEE = "cln_b"


def run(cmd: List[str], timeout: int = 120) -> Tuple[int, str, str]:
    """Run a subprocess and return (code, stdout, stderr)."""
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def cln_rpc(service: str, method: str, *args: str, network: str = DEFAULT_NETWORK, timeout: int = 120) -> Dict[str, Any]:
    """
    Call lightning-cli inside a docker compose service.
    Returns parsed JSON on success, raises RuntimeError on failure.
    """
    base = ["docker", "compose", "exec", "-T", service, "lightning-cli", f"--network={network}", method]
    base += list(args)

    code, out, err = run(base, timeout=timeout)
    if code != 0:
        raise RuntimeError(f"[{service}] lightning-cli {method} failed (code={code}): {err or out}")

    # lightning-cli outputs JSON by default
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        # Some commands can output non-json; return as text wrapper
        return {"raw": out}


def bitcoind_rpc(method: str, *args: str, timeout: int = 120) -> Dict[str, Any]:
    """
    Call bitcoin-cli inside bitcoind container.
    Returns parsed JSON if possible, else raw text.
    """
    base = ["docker", "compose", "exec", "-T", "bitcoind", "bitcoin-cli", "-testnet", "-rpcuser=bitcoin", "-rpcpassword=bitcoin", method]
    base += list(args)

    code, out, err = run(base, timeout=timeout)
    if code != 0:
        # bitcoind may return -28 during startup; keep message
        raise RuntimeError(f"[bitcoind] bitcoin-cli {method} failed (code={code}): {err or out}")

    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"raw": out}


def safe_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def msat_to_sat(msat: Any) -> Optional[int]:
    """
    Convert CLN msat fields like '1234msat' or integer-like to sat (rounded down).
    """
    if msat is None:
        return None
    if isinstance(msat, int):
        return msat // 1000
    s = str(msat)
    if s.endswith("msat"):
        s = s[:-4]
    try:
        return int(s) // 1000
    except Exception:
        return None


def log_attempt(event: Dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    event = dict(event)
    event["ts"] = datetime.utcnow().isoformat() + "Z"
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def cmd_status(args: argparse.Namespace) -> None:
    print("== Bitcoin node ==")
    try:
        bi = bitcoind_rpc("getblockchaininfo", timeout=120)
        print(json.dumps({
            "chain": bi.get("chain"),
            "blocks": bi.get("blocks"),
            "headers": bi.get("headers"),
            "verificationprogress": bi.get("verificationprogress"),
            "initialblockdownload": bi.get("initialblockdownload"),
        }, indent=2))
    except Exception as e:
        print(f"bitcoind not ready: {e}")

    try:
        net = bitcoind_rpc("getnetworkinfo", timeout=120)
        print(json.dumps({
            "connections": net.get("connections"),
            "subversion": net.get("subversion"),
        }, indent=2))
    except Exception as e:
        print(f"bitcoind networkinfo failed: {e}")

    print("\n== Lightning nodes ==")
    for svc in [args.payer, args.payee]:
        try:
            gi = cln_rpc(svc, "getinfo", network=args.network, timeout=120)
            print(f"\n[{svc}]")
            print(json.dumps({
                "id": gi.get("id"),
                "alias": gi.get("alias"),
                "network": gi.get("network"),
                "blockheight": gi.get("blockheight"),
                "num_peers": gi.get("num_peers"),
                "warning_bitcoind_sync": gi.get("warning_bitcoind_sync"),
            }, indent=2))
            lf = cln_rpc(svc, "listfunds", network=args.network, timeout=120)
            outputs = lf.get("outputs", [])
            print(f"funds.outputs_count={len(outputs)} channels_count={len(lf.get('channels', []))}")
        except Exception as e:
            print(f"[{svc}] not ready: {e}")


def get_channels_summary(service: str, network: str) -> List[Dict[str, Any]]:
    """
    For CLN, channel balances are visible in listfunds.channels.
    We'll compute capacity/local/remote for each channel if possible.
    """
    lf = cln_rpc(service, "listfunds", network=network, timeout=120)
    chans = lf.get("channels", [])
    out: List[Dict[str, Any]] = []
    for c in chans:
        # Fields vary by version; try best-effort.
        # Common fields: "channel_id", "short_channel_id", "connected", "state",
        # "our_amount_msat", "amount_msat" (total), "funding_txid"
        total_sat = msat_to_sat(c.get("amount_msat"))
        local_sat = msat_to_sat(c.get("our_amount_msat"))
        remote_sat = None
        if total_sat is not None and local_sat is not None:
            remote_sat = max(total_sat - local_sat, 0)

        capacity = total_sat
        local = local_sat
        remote = remote_sat

        outbound_ratio = (local / capacity) if (local is not None and capacity) else None
        inbound_ratio = (remote / capacity) if (remote is not None and capacity) else None

        out.append({
            "peer_id": c.get("peer_id") or c.get("id"),
            "short_channel_id": c.get("short_channel_id"),
            "channel_id": c.get("channel_id"),
            "state": c.get("state"),
            "connected": c.get("connected"),
            "capacity_sat": capacity,
            "local_sat": local,
            "remote_sat": remote,
            "outbound_ratio": outbound_ratio,
            "inbound_ratio": inbound_ratio,
        })
    return out


def cmd_inspect(args: argparse.Namespace) -> None:
    chans = get_channels_summary(args.payer, args.network)
    if not chans:
        print("No channels found yet (listfunds.channels empty).")
        return
    print(json.dumps(chans, indent=2))


def cmd_check(args: argparse.Namespace) -> None:
    amount = args.amount_sat
    chans = get_channels_summary(args.payer, args.network)

    can_send = False
    can_receive = False

    reasons_send = []
    reasons_recv = []

    fee_buffer = args.fee_buffer_sat

    if not chans:
        print("No channels available. can_send=false, can_receive=false")
        return

    for c in chans:
        if c.get("connected") is False:
            continue
        local = c.get("local_sat")
        remote = c.get("remote_sat")
        cap = c.get("capacity_sat")

        if local is not None and local >= amount + fee_buffer:
            can_send = True
            reasons_send.append(f"channel {c.get('short_channel_id') or c.get('channel_id')} local={local} cap={cap}")

        if remote is not None and remote >= amount:
            can_receive = True
            reasons_recv.append(f"channel {c.get('short_channel_id') or c.get('channel_id')} remote={remote} cap={cap}")

    print(json.dumps({
        "amount_sat": amount,
        "fee_buffer_sat": fee_buffer,
        "can_send": can_send,
        "can_receive": can_receive,
        "evidence_send": reasons_send[:5],
        "evidence_receive": reasons_recv[:5],
    }, indent=2))


def cmd_invoice(args: argparse.Namespace) -> None:
    msat = args.amount_sat * 1000
    inv = cln_rpc(args.payee, "invoice", str(msat), args.label, args.description, network=args.network, timeout=120)
    print(json.dumps(inv, indent=2))


def cmd_pay(args: argparse.Namespace) -> None:
    event: Dict[str, Any] = {
        "payer": args.payer,
        "payee": args.payee,
        "amount_sat": args.amount_sat,
        "bolt11": args.bolt11,
    }
    try:
        res = cln_rpc(args.payer, "pay", args.bolt11, network=args.network, timeout=240)
        event.update({
            "result": "success",
            "payment_hash": res.get("payment_hash"),
            "preimage": res.get("payment_preimage"),
            "fee_msat": res.get("fee_msat"),
            "status": res.get("status"),
        })
        print(json.dumps(res, indent=2))
    except Exception as e:
        event.update({
            "result": "failure",
            "failure_reason": str(e),
        })
        print(f"PAY FAILED: {e}", file=sys.stderr)
        sys.exit_code = 1
    finally:
        log_attempt(event)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Liquidity Inspector + Payment Orchestrator (CLN via docker compose exec)")
    p.add_argument("--network", default=DEFAULT_NETWORK, choices=["testnet", "signet", "bitcoin"], help="Lightning network")
    p.add_argument("--payer", default=DEFAULT_PAYER, help="Docker compose service name of payer node (default: cln_a)")
    p.add_argument("--payee", default=DEFAULT_PAYEE, help="Docker compose service name of payee node (default: cln_b)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="Show bitcoind + LN status")
    s.set_defaults(func=cmd_status)

    i = sub.add_parser("inspect", help="Inspect channels and compute liquidity ratios")
    i.set_defaults(func=cmd_inspect)

    c = sub.add_parser("check", help="Check send/receive feasibility for an amount (sat)")
    c.add_argument("--amount-sat", type=int, required=True)
    c.add_argument("--fee-buffer-sat", type=int, default=20, help="Simple fee buffer to avoid false positives")
    c.set_defaults(func=cmd_check)

    inv = sub.add_parser("invoice", help="Create invoice on payee")
    inv.add_argument("--amount-sat", type=int, required=True)
    inv.add_argument("--label", required=True)
    inv.add_argument("--description", default="test payment")
    inv.set_defaults(func=cmd_invoice)

    pay = sub.add_parser("pay", help="Pay a bolt11 invoice from payer and log attempt")
    pay.add_argument("--amount-sat", type=int, required=True)
    pay.add_argument("--bolt11", required=True)
    pay.set_defaults(func=cmd_pay)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
