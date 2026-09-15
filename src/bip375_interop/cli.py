from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict, replace
from pathlib import Path

from . import __version__
from .checkouts import inspect_checkout
from .config import LOCK_NAME, load_config, load_scenario, write_lock
from .errors import InteropError
from .suites import KeyArchitecture
from .suites import get_suite
from .suites import scenario_rounds
from .adapters import BitSagaAdapter, ColdcardAdapter, JadeAdapter, SeedSignerAdapter
from .artifacts import ArtifactRun
from .engine import run_rounds
from .worker import WorkerClient
from .smoke import run_coldcard_smoke, run_jade_smoke
from .fixtures import build_bip375_fixture
from .treasury import build_treasury_descriptor, build_wallet_toml


def _add_signer_order_args(subparser: argparse.ArgumentParser) -> None:
    order = subparser.add_mutually_exclusive_group()
    order.add_argument(
        "--signer-order",
        help="comma-separated signer names giving each round's processing order "
        "(must name every scenario signer exactly once); does not change the "
        "descriptor's participant order, only the order devices are called in",
    )
    order.add_argument(
        "--shuffle-signers",
        action="store_true",
        help="randomize each round's signer processing order (see --shuffle-seed)",
    )
    subparser.add_argument(
        "--shuffle-seed",
        type=int,
        help="seed for --shuffle-signers; omit for a random seed, which is printed "
        "so a run can be reproduced",
    )


def _resolve_signer_order(scenario, args) -> tuple[str, ...] | None:
    names = tuple(signer.name for signer in scenario.signers)
    if getattr(args, "signer_order", None):
        order = tuple(part.strip() for part in args.signer_order.split(","))
        if sorted(order) != sorted(names):
            raise InteropError(
                f"--signer-order must name every signer exactly once: {', '.join(names)}"
            )
        return order
    if getattr(args, "shuffle_signers", False):
        seed = args.shuffle_seed if args.shuffle_seed is not None else random.SystemRandom().randrange(2**32)
        shuffled = list(names)
        random.Random(seed).shuffle(shuffled)
        print(f"shuffle-signers seed: {seed} -> order: {', '.join(shuffled)}", file=sys.stderr)
        return tuple(shuffled)
    return None


def _reorder_rounds(rounds, order: tuple[str, ...]):
    position = {name: index for index, name in enumerate(order)}
    return tuple(
        replace(round_spec, signers=tuple(sorted(round_spec.signers, key=lambda name: position[name])))
        for round_spec in rounds
    )


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
    _add_signer_order_args(plan)
    run = sub.add_parser("run")
    run.add_argument("scenario", type=Path)
    run.add_argument("--psbt", required=True, type=Path)
    _add_signer_order_args(run)
    generated = sub.add_parser("run-generated")
    generated.add_argument("scenario", type=Path)
    _add_signer_order_args(generated)
    treasury = sub.add_parser("treasury-wallet")
    treasury.add_argument("seed_ids", nargs="+", help="published test seed ids, e.g. test-a test-b test-c")
    treasury.add_argument("--network", default="signet")
    treasury.add_argument("--last-derivation-index", type=int, default=0)
    treasury.add_argument("--change-derivation-index", type=int, default=0)
    treasury.add_argument(
        "--key-architecture",
        choices=[item.value for item in KeyArchitecture],
        default=KeyArchitecture.AGGREGATE_THEN_DERIVE.value,
        help="where the multipath step lands; decides the descriptor shape",
    )
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
                key_architecture=args.key_architecture,
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
        suite_config = suite.validate(scenario)
        signer_order = _resolve_signer_order(scenario, args)
        if args.command == "plan":
            rounds = scenario_rounds(scenario)
            if signer_order is not None:
                rounds = _reorder_rounds(rounds, signer_order)
            print(json.dumps([
                {"phase": item.name, "signers": list(item.signers)}
                for item in rounds
            ], indent=2))
            return 0
        if args.command in {"run", "run-generated"}:
            adapters = {}
            workers = {}
            run_artifacts = ArtifactRun(config.artifact_root, scenario.name)
            descriptor = None
            if scenario.suite == "musig2-sp":
                descriptor = build_treasury_descriptor(
                    [signer.seed_id for signer in scenario.signers],
                    network=scenario.network,
                    key_architecture=suite_config.key_architecture,
                )
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
                        descriptor=descriptor,
                    )
                    adapters[signer.name] = adapter
                    workers[signer.name] = worker
                initial_psbt = (
                    args.psbt.read_bytes()
                    if args.command == "run"
                    else build_bip375_fixture(scenario)
                )
                rounds = scenario_rounds(scenario)
                if signer_order is not None:
                    rounds = _reorder_rounds(rounds, signer_order)
                final_psbt = run_rounds(initial_psbt, rounds, workers, run_artifacts)
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
