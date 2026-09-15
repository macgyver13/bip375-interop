from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

from . import __version__
from .checkouts import inspect_checkout
from .config import LOCK_NAME, load_config, load_scenario, write_lock
from .errors import InteropError
from .suites import get_suite
from .suites import scenario_rounds
from .adapters import BitSagaAdapter, ColdcardAdapter, JadeAdapter, SeedSignerAdapter
from .artifacts import ArtifactRun
from .engine import run_rounds
from .worker import WorkerClient
from .smoke import run_coldcard_smoke, run_jade_smoke
from .fixtures import build_bip375_fixture
from .treasury import build_wallet_toml


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bip375-interop")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, default=Path("interop.yaml"))
    parser.add_argument("--allow-dirty", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("pin", help="write interop.lock with the current commit id of every checkout")
    smoke = sub.add_parser("smoke")
    smoke.add_argument("backend", choices=("coldcard", "jade"))
    validate = sub.add_parser("validate")
    validate.add_argument("scenario", type=Path)
    plan = sub.add_parser("plan")
    plan.add_argument("scenario", type=Path)
    run = sub.add_parser("run")
    run.add_argument("scenario", type=Path)
    run.add_argument("--psbt", required=True, type=Path)
    generated = sub.add_parser("run-generated")
    generated.add_argument("scenario", type=Path)
    treasury = sub.add_parser("treasury-wallet")
    treasury.add_argument("seed_ids", nargs="+", help="published test seed ids, e.g. test-a test-b test-c")
    treasury.add_argument("--network", default="signet")
    treasury.add_argument("--last-derivation-index", type=int, default=0)
    treasury.add_argument("--change-derivation-index", type=int, default=0)
    treasury.add_argument("--out", type=Path, help="write the wallet TOML here instead of stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "treasury-wallet":
            text = build_wallet_toml(
                args.seed_ids,
                network=args.network,
                last_derivation_index=args.last_derivation_index,
                change_derivation_index=args.change_derivation_index,
            )
            if args.out:
                args.out.write_text(text)
                print(f"wrote {args.out}")
            else:
                print(text, end="")
            return 0
        config = load_config(args.config)
        if args.allow_dirty:
            config = replace(config, allow_dirty=True)
        if args.command == "doctor":
            states = [
                inspect_checkout(c, config.allow_dirty)
                for c in config.checkouts.values()
            ]
            print(json.dumps([asdict(state) for state in states], indent=2))
            return 0
        if args.command == "pin":
            # A dirty git checkout is never pinned: HEAD would omit the uncommitted changes.
            pins = {
                c.name: inspect_checkout(replace(c, revision=None)).revision
                for c in config.checkouts.values()
            }
            write_lock(args.config.parent / LOCK_NAME, pins)
            print(json.dumps(pins, indent=2))
            return 0
        if args.command == "smoke":
            runner = run_jade_smoke if args.backend == "jade" else run_coldcard_smoke
            final_psbt, manifest, size = runner(config)
            print(json.dumps({
                "scope": f"single-device {args.backend} BIP-375 transport",
                "mixed_device_psbt_interoperability": "not exercised",
                "final_psbt": str(final_psbt),
                "manifest": str(manifest),
                "bytes": size,
            }, indent=2))
            return 0
        scenario = load_scenario(args.scenario)
        suite = get_suite(scenario.suite)
        suite.validate(scenario)
        if args.command == "plan":
            print(json.dumps([
                {"phase": item.name, "signers": list(item.signers)}
                for item in scenario_rounds(scenario)
            ], indent=2))
            return 0
        if args.command in {"run", "run-generated"}:
            adapters = {}
            workers = {}
            run_artifacts = ArtifactRun(config.artifact_root, scenario.name)
            try:
                for signer in scenario.signers:
                    checkout_name = (
                        "bitsaga-seedsigner" if signer.backend in {"bitsaga", "bitsaga-seedsigner"}
                        else signer.backend
                    )
                    checkout = config.checkouts.get(checkout_name)
                    if checkout is None:
                        raise InteropError(f"missing checkout configuration for {checkout_name}")
                    if signer.backend == "seedsigner":
                        adapter = SeedSignerAdapter(checkout.path)
                    elif signer.backend == "jade":
                        adapter = JadeAdapter(checkout.path)
                    elif signer.backend == "coldcard":
                        adapter = ColdcardAdapter(checkout.path)
                    elif signer.backend in {"bitsaga", "bitsaga-seedsigner"}:
                        adapter = BitSagaAdapter(checkout.path)
                    else:
                        raise InteropError(
                            f"backend {signer.backend} has no external PSBT worker yet"
                        )
                    plan = adapter.plan_worker()
                    worker = WorkerClient(plan.argv, cwd=plan.cwd, env=plan.env)
                    worker.start(
                        scenario.suite,
                        signer.seed_id,
                        run_artifacts.path / signer.name,
                        scenario.network,
                    )
                    adapters[signer.name] = adapter
                    workers[signer.name] = worker
                initial_psbt = (
                    args.psbt.read_bytes()
                    if args.command == "run"
                    else build_bip375_fixture(scenario)
                )
                final_psbt = run_rounds(initial_psbt, scenario_rounds(scenario), workers, run_artifacts)
                manifest = run_artifacts.finalize({
                    "scenario": scenario.name,
                    "suite": scenario.suite,
                    "signers": [asdict(signer) for signer in scenario.signers],
                    "checkouts": [],
                })
                print(json.dumps({
                    "final_psbt": str(run_artifacts.path / "final.psbt"),
                    "manifest": str(manifest), "bytes": len(final_psbt),
                }, indent=2))
                return 0
            finally:
                for worker in workers.values():
                    worker.stop()
        print(f"valid: {scenario.name} ({scenario.suite})")
        return 0
    except InteropError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
