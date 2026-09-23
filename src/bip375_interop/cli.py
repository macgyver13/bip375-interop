from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import asdict, replace
from pathlib import Path

from . import __version__
from .checkouts import inspect_checkout
from .expectations import load_expectations
from .preflight import backend_checkout, run_preflight
from .regression import STEADY, has_failures, label_results, previous_results
from .config import LOCK_NAME, load_config, load_scenario, write_lock
from .errors import InteropError
from .models import KNOWN_VALIDATORS
from .suites import KeyArchitecture
from .suites import get_suite
from .suites import scenario_rounds
from .adapters import (
    BitSagaAdapter, CaravanAdapter, ColdcardAdapter, JadeAdapter, SeedSignerAdapter, SpdkAdapter,
)
from .artifacts import ArtifactRun
from .batch import BatchRun, CaseResult
from .catalog import changed_files, discover, select
from .engine import run_rounds
from .worker import WorkerClient
from .smoke import run_coldcard_smoke, run_jade_smoke
from .fixtures import build_bip375_fixture
from .treasury import build_treasury_descriptor, build_wallet_toml
from .verification import (
    check_case_reason,
    check_case_status,
    run_claim,
    require_input_utxos,
    verify_bip375_completion,
    verify_musig2_sp_completion,
)


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
    check = sub.add_parser("check", help="run a project regression group and write a report")
    check.add_argument("--project", default="harness", choices=(
        "harness", "coldcard", "jade", "seedsigner", "bitsaga-seedsigner", "caravan", "spdk",
    ))
    check.add_argument(
        "--exhaustive", action="store_true",
        help="also run every independent validator (e.g. caravan, spdk) on each bip375 scenario",
    )
    check.add_argument("--scenarios-dir", type=Path, default=Path("scenarios"))
    check.add_argument("--since", default="HEAD", help="Git revision used to inspect local changes")
    check.add_argument(
        "--psbt", action="append", default=[], metavar="SCENARIO=PATH",
        help="bind a prepared initial PSBT to a MuSig2-SP scenario; may be repeated",
    )
    check.add_argument("--dry-run", action="store_true", help="show the selection without starting workers")
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


def _start_workers(config, scenario, run_artifacts, descriptor: str | None = None):
    """Start each signer's worker, recording an inspected checkout state per signer."""

    workers = {}
    states = []
    for signer in scenario.signers:
        checkout_name = backend_checkout(signer.backend)
        checkout = config.checkouts.get(checkout_name)
        if checkout is None:
            raise InteropError(f"missing checkout configuration for {checkout_name}")
        states.append(inspect_checkout(checkout, config.allow_dirty))
        adapter = {
            "seedsigner": SeedSignerAdapter,
            "jade": JadeAdapter,
            "coldcard": ColdcardAdapter,
            "bitsaga": BitSagaAdapter,
            "bitsaga-seedsigner": BitSagaAdapter,
        }.get(signer.backend)
        if adapter is None:
            raise InteropError(f"backend {signer.backend} has no external PSBT worker yet")
        plan = adapter(checkout.path).plan_worker()
        worker = WorkerClient(plan.argv, cwd=plan.cwd, env=plan.env)
        worker.start(
            scenario.suite, signer.seed_id, run_artifacts.path / signer.name,
            scenario.network, descriptor=descriptor,
        )
        workers[signer.name] = worker
    return workers, states


_VALIDATOR_ADAPTERS = {
    "caravan": CaravanAdapter,
    "spdk": SpdkAdapter,
}


def _run_validators(config, scenario, run_artifacts) -> list[dict]:
    """Re-check each run's PSBT snapshots with every opted-in validator."""

    records = []
    for name in scenario.validators:
        checkout = config.checkouts.get(name)
        if checkout is None:
            raise InteropError(f"missing checkout configuration for {name}")
        state = inspect_checkout(checkout, config.allow_dirty)
        adapter_cls = _VALIDATOR_ADAPTERS[name]
        snapshots = sorted(run_artifacts.path.glob(adapter_cls.snapshot_glob))
        results = adapter_cls(checkout.path).validate(snapshots)
        records.append({"name": name, "checkout": asdict(state), "validated": len(results)})
    return records


def _verification_fields(scenario, repairs=()) -> dict:
    claim = run_claim(scenario, repairs=repairs, generated=True)
    return {key: claim[key] for key in ("verification_scope", "reason") if key in claim}


def _recorded_repairs(manifest: Path) -> list:
    payload = json.loads(manifest.read_text())
    repairs = payload.get("repairs") or []
    return list(repairs) if isinstance(repairs, list) else []


def case_result_for_run(scenario, manifest: Path, *, generated: bool) -> CaseResult:
    """Batch row for a finished run. A weak mode is not ``passed``."""

    repairs = _recorded_repairs(manifest)
    return CaseResult(
        scenario.name,
        check_case_status(scenario, generated=generated, repairs=repairs),
        check_case_reason(scenario, generated=generated, repairs=repairs),
        str(manifest),
    )


def run_summary(final_psbt: Path, manifest: Path, size: int) -> dict:
    """CLI summary. The scope label sits beside the artifact path."""

    payload = json.loads(manifest.read_text())
    scope = payload.get("verification_scope")
    if not scope:
        return {"final_psbt": str(final_psbt), "manifest": str(manifest), "bytes": size}
    return {
        "final_psbt": str(final_psbt),
        "verification_scope": scope,
        "manifest": str(manifest),
        "bytes": size,
    }


def _with_utxo_source(payload: dict, utxo_source: str | None) -> dict:
    if utxo_source is None:
        return payload
    return {**payload, "utxo_source": utxo_source}


def _run_generated_scenario(config, scenario, signer_order: tuple[str, ...] | None = None) -> tuple[Path, Path, int]:
    """Run one generated BIP-375 scenario with durable evidence on failure."""

    run_artifacts = ArtifactRun(config.artifact_root, scenario.name)
    workers, states = {}, []
    utxo_source = None
    repairs: tuple = ()
    try:
        workers, states = _start_workers(config, scenario, run_artifacts)
        initial_psbt = build_bip375_fixture(scenario)
        utxo_source = require_input_utxos(initial_psbt)
        rounds = scenario_rounds(scenario)
        if signer_order is not None:
            rounds = _reorder_rounds(rounds, signer_order)
        final_psbt, repairs = run_rounds(
            initial_psbt, rounds, workers, run_artifacts, scenario.merge_policy, scenario=scenario,
        )
        verify_bip375_completion(scenario, final_psbt)
        validators = _run_validators(config, scenario, run_artifacts)
        manifest = run_artifacts.finalize(_with_utxo_source({
            "scenario": scenario.name,
            "suite": scenario.suite,
            "signers": [asdict(signer) for signer in scenario.signers],
            "rounds": [{"phase": item.name, "signers": list(item.signers)} for item in rounds],
            "verification": scenario.verification,
            "checkouts": [asdict(state) for state in states],
            "merge_policy": scenario.merge_policy,
            "repairs": list(repairs),
            "validators": validators,
            **_verification_fields(scenario, repairs),
        }, utxo_source))
        return run_artifacts.path / "final.psbt", manifest, len(final_psbt)
    except Exception as exc:
        run_artifacts.finalize(_with_utxo_source({
            "scenario": scenario.name,
            "suite": scenario.suite,
            "status": "failed",
            "error": str(exc),
            "checkouts": [asdict(state) for state in states],
            **_verification_fields(scenario, repairs),
        }, utxo_source))
        raise
    finally:
        for worker in workers.values():
            worker.stop()


def _run_psbt_scenario(
    config, scenario, psbt_path: Path, signer_order: tuple[str, ...] | None = None,
) -> tuple[Path, Path, int]:
    """Run an externally prepared PSBT and retain provenance on every outcome."""

    run_artifacts = ArtifactRun(config.artifact_root, scenario.name)
    workers, states = {}, []
    utxo_source = None
    repairs: tuple = ()
    try:
        suite_config = get_suite(scenario.suite).validate(scenario)
        descriptor = None
        if scenario.suite == "musig2-sp":
            descriptor = build_treasury_descriptor(
                [signer.seed_id for signer in scenario.signers],
                network=scenario.network,
                key_architecture=suite_config.key_architecture,
            )
        workers, states = _start_workers(config, scenario, run_artifacts, descriptor=descriptor)
        psbt_bytes = psbt_path.read_bytes()
        utxo_source = require_input_utxos(psbt_bytes)
        musig2_hook = (
            (lambda phase, snapshot: verify_musig2_sp_completion(scenario, phase, snapshot))
            if scenario.suite == "musig2-sp" else None
        )
        rounds = scenario_rounds(scenario)
        if signer_order is not None:
            rounds = _reorder_rounds(rounds, signer_order)
        final_psbt, repairs = run_rounds(
            psbt_bytes, rounds, workers, run_artifacts, scenario.merge_policy,
            scenario=scenario, on_round_complete=musig2_hook,
        )
        if scenario.suite == "bip375":
            verify_bip375_completion(scenario, final_psbt)
        validators = _run_validators(config, scenario, run_artifacts)
        manifest = run_artifacts.finalize(_with_utxo_source({
            "scenario": scenario.name,
            "suite": scenario.suite,
            "input_psbt": str(psbt_path),
            "input_psbt_sha256": hashlib.sha256(psbt_bytes).hexdigest(),
            "descriptor": descriptor,
            "rounds": [{"phase": item.name, "signers": list(item.signers)} for item in rounds],
            "verification": scenario.verification,
            "checkouts": [asdict(state) for state in states],
            "merge_policy": scenario.merge_policy,
            "repairs": list(repairs),
            "validators": validators,
            **_verification_fields(scenario, repairs),
        }, utxo_source))
        return run_artifacts.path / "final.psbt", manifest, len(final_psbt)
    except Exception as exc:
        run_artifacts.finalize(_with_utxo_source({
            "scenario": scenario.name,
            "suite": scenario.suite,
            "status": "failed",
            "error": str(exc),
            "checkouts": [asdict(state) for state in states],
            **_verification_fields(scenario, repairs),
        }, utxo_source))
        raise
    finally:
        for worker in workers.values():
            worker.stop()


def _psbt_bindings(values: list[str]) -> dict[str, Path]:
    bindings = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path:
            raise InteropError("--psbt must use SCENARIO=PATH")
        if name in bindings:
            raise InteropError(f"--psbt supplied more than once for {name}")
        bindings[name] = Path(raw_path)
    return bindings


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
        if args.command == "check":
            entries = select(discover(args.scenarios_dir), args.project)
            if args.exhaustive:
                entries = tuple(
                    replace(entry, scenario=replace(entry.scenario, validators=KNOWN_VALIDATORS))
                    if entry.scenario.suite == "bip375" else entry
                    for entry in entries
                )
            bindings = _psbt_bindings(args.psbt)
            selected_names = {entry.scenario.name for entry in entries}
            unknown = sorted(bindings.keys() - selected_names)
            if unknown:
                raise InteropError(f"--psbt names no selected scenario: {', '.join(unknown)}")
            # An initial PSBT stored next to its scenario is bound unless --psbt overrides it.
            for entry in entries:
                stored = entry.path.with_suffix(".psbt")
                if not entry.runnable_generated and stored.exists():
                    bindings.setdefault(entry.scenario.name, stored)
            checkout = config.checkouts.get(args.project)
            if args.project != "harness" and checkout is None:
                raise InteropError(f"missing checkout configuration for {args.project}")
            source_root = Path.cwd() if args.project == "harness" else checkout.path
            changes = changed_files(source_root, args.since)
            if args.dry_run:
                print(json.dumps({
                    "project": args.project,
                    "since": args.since,
                    "changed_files": list(changes),
                    "selection_policy": "all backend scenarios (conservative initial mapping)",
                    "cases": [
                        {
                            "scenario": entry.scenario.name,
                            "path": str(entry.path),
                            "validators": list(entry.scenario.validators),
                            "psbt": (
                                str(bindings[entry.scenario.name])
                                if entry.scenario.name in bindings else None
                            ),
                            "status": (
                                "selected" if entry.runnable_generated or entry.scenario.name in bindings
                                else "blocked"
                            ),
                            "reason": (
                                "transport-only: finalize and verify on-chain after the run"
                                if entry.scenario.name in bindings and not entry.runnable_generated
                                else entry.reason
                            ),
                        }
                        for entry in entries
                    ],
                }, indent=2))
                return 0
            runnable = [
                entry.scenario for entry in entries
                if entry.runnable_generated or entry.scenario.name in bindings
            ]
            checkout_states = run_preflight(config, runnable)
            batch = BatchRun(config.artifact_root, args.project)
            for entry in entries:
                psbt_path = bindings.get(entry.scenario.name)
                if not entry.runnable_generated and psbt_path is None:
                    batch.add(CaseResult(entry.scenario.name, "blocked", entry.reason))
                    continue
                try:
                    if entry.runnable_generated:
                        _, manifest, _ = _run_generated_scenario(config, entry.scenario)
                        batch.add(case_result_for_run(entry.scenario, manifest, generated=True))
                    elif not psbt_path.is_file():
                        batch.add(CaseResult(entry.scenario.name, "failed", f"PSBT is missing: {psbt_path}"))
                    else:
                        _, manifest, _ = _run_psbt_scenario(config, entry.scenario, psbt_path)
                        batch.add(case_result_for_run(entry.scenario, manifest, generated=False))
                except InteropError as exc:
                    batch.add(CaseResult(entry.scenario.name, "failed", str(exc)))
            expectations_path = args.config.parent / "expectations.yaml"
            labels = None
            if expectations_path.is_file():
                labels = label_results(
                    batch.results,
                    load_expectations(expectations_path),
                    previous_results(config.artifact_root, args.project, batch.path),
                )
            manifest, report = batch.finalize(labels, checkout_states)
            passed = sum(item.status == "passed" for item in batch.results)
            required = sum(item.status in {"passed", "failed"} for item in batch.results)
            summary = {
                "regression_health": None if not required else round(100 * passed / required),
                "passed": passed,
                "required": required,
                "report": str(report),
                "manifest": str(manifest),
            }
            if labels is not None:
                summary["labels"] = json.loads(manifest.read_text())["label_counts"]
                summary["variances"] = {
                    name: label for name, label in labels.items() if label != STEADY
                }
            print(json.dumps(summary, indent=2))
            if not required:
                print("every selected scenario was blocked; nothing was actually verified", file=sys.stderr)
                return 1
            if labels is not None:
                return 1 if has_failures(labels) else 0
            return 0 if passed == required else 1
        scenario = load_scenario(args.scenario)
        get_suite(scenario.suite).validate(scenario)
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
        if args.command == "run-generated":
            final_psbt, manifest, size = _run_generated_scenario(config, scenario, signer_order)
            print(json.dumps(run_summary(final_psbt, manifest, size), indent=2))
            return 0
        if args.command == "run":
            final_psbt, manifest, size = _run_psbt_scenario(config, scenario, args.psbt, signer_order)
            print(json.dumps(run_summary(final_psbt, manifest, size), indent=2))
            return 0
        print(f"valid: {scenario.name} ({scenario.suite})")
        return 0
    except InteropError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
