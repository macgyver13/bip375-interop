from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import asdict, replace
from pathlib import Path

from . import __version__
from .build import build, plan_builds
from .fetch import fetch
from .checkouts import inspect_checkout
from .expectations import (
    interop_lab_allowed_findings, load_expectations, require_lock_digest,
)
from .interop_lab import validate_snapshots, write_reports
from .preflight import backend_checkout, run_preflight
from .regression import has_failures, label_results, previous_results, variance_records
from .config import LOCK_NAME, load_config, load_scenario, read_lock, write_lock
from .errors import InteropError
from .models import KNOWN_VALIDATORS
from .psbt_maps import parse_psbt
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
    pin_cmd = sub.add_parser("pin", help="write interop.lock with the current commit id of every checkout")
    pin_cmd.add_argument("--dry-run", action="store_true", help="show pin changes without writing the lock")
    build_cmd = sub.add_parser("build", help="run each checkout's build steps (plan_build)")
    build_cmd.add_argument("only", nargs="*", help="checkout names (default: every one in the profile)")
    build_cmd.add_argument("--dry-run", action="store_true", help="list the steps without running them")
    fetch_cmd = sub.add_parser("fetch", help="clone every checkout at its lock commit and write interop.fetched.yaml")
    fetch_cmd.add_argument("--sources", type=Path, default=Path("sources.yaml"))
    fetch_cmd.add_argument("--checkouts-dir", type=Path, default=Path(".checkouts"))
    smoke = sub.add_parser("smoke")
    smoke.add_argument("backend", choices=("coldcard", "jade"))
    validate = sub.add_parser("validate", help="validate a scenario schema, not a PSBT")
    validate.add_argument("scenario", type=Path)
    validate_psbt = sub.add_parser("validate-psbt", help="check one PSBT through available stages")
    validate_psbt.add_argument("psbt", type=Path)
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
    check.add_argument(
        "--release", action="store_true",
        help=(
            "release gate: Caravan, SPDK, and Interop Lab on every bip375 scenario, "
            "and both MuSig2 regtest architectures"
        ),
    )
    check.add_argument("--scenarios-dir", type=Path, default=Path("scenarios"))
    check.add_argument("--since", default="HEAD", help="Git revision used to inspect local changes")
    check.add_argument(
        "--psbt", action="append", default=[], metavar="SCENARIO=PATH",
        help="bind a prepared initial PSBT to a MuSig2-SP scenario; may be repeated",
    )
    check.add_argument("--dry-run", action="store_true", help="show the selection without starting workers")
    check.add_argument("--progress-json", action="store_true", help="emit JSON progress events on stderr")
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
        if not snapshots:
            raise InteropError(f"{name}: no snapshots matched {adapter_cls.snapshot_glob}")
        try:
            results = adapter_cls(checkout.path).validate(snapshots)
        except InteropError as exc:
            records.append({"name": name, "checkout": asdict(state),
                            "status": "failed", "error": str(exc),
                            "snapshots": len(snapshots), "validated": 0})
        else:
            records.append({"name": name, "checkout": asdict(state),
                            "status": "passed", "snapshots": len(snapshots),
                            "validated": len(results)})
    return records


def _verification_fields(scenario, repairs=()) -> dict:
    claim = run_claim(scenario, repairs=repairs, generated=True)
    return {key: claim[key] for key in ("verification_scope", "reason") if key in claim}



def _manifest_claim(scenario, repairs=(), validators=(), interop_lab=None) -> dict:
    from .verification import manifest_claim_fields

    return manifest_claim_fields(scenario, repairs, validators, interop_lab)


def _stage_records(validators, interop_lab, *, parser_ran: bool = True) -> list[dict]:
    by_name = {item["name"]: item for item in validators}
    stages = [{"name": "parser", "status": "passed" if parser_ran else "not-run"}]
    for name in KNOWN_VALIDATORS:
        record = by_name.get(name)
        stages.append({"name": name, "status": "not-run" if record is None else record["status"],
                       "snapshots": 0 if record is None else record["snapshots"],
                       "reason": None if record is None else record.get("error")})
    stages.append({"name": "interop-lab", "status": interop_lab["status"],
                   "snapshots": interop_lab.get("snapshots", 0),
                   "reason": interop_lab.get("reason")})
    return stages


def _interop_stage(run_artifacts, release: bool, allowed_findings: set[str]) -> dict:
    snapshots = sorted(run_artifacts.path.glob("*.psbt"))
    if not release:
        return {"name": "interop-lab", "status": "not-run", "reason": "not requested",
                "snapshots": len(snapshots), "validated": 0, "files": []}
    return validate_snapshots(snapshots, allowed_findings)


MUSIG2_RELEASE_ARCHITECTURES = ("aggregate-then-derive", "derive-then-aggregate")
_MUSIG2_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "musig2-regtest.sh"


def musig2_pass_line(output: str) -> str | None:
    """The script's PASS line, if it printed one. Any other output does not count."""

    for line in output.splitlines():
        stripped = line.strip()
        if stripped == "PASS" or stripped.startswith("PASS "):
            return stripped
    return None


def musig2_leg_result(architecture: str, stdout: str, returncode: int) -> dict:
    line = musig2_pass_line(stdout)
    return {
        "architecture": architecture,
        "passed": bool(line) and returncode == 0,
        "line": line,
    }


def release_legs_ok(legs) -> bool:
    """True only when each key architecture printed PASS."""

    if not legs or len(legs) != len(MUSIG2_RELEASE_ARCHITECTURES):
        return False
    by_arch = {leg.get("architecture"): leg for leg in legs}
    if set(by_arch) != set(MUSIG2_RELEASE_ARCHITECTURES):
        return False
    return all(
        leg.get("passed") and isinstance(leg.get("line"), str) and leg["line"].startswith("PASS")
        for leg in by_arch.values()
    )


def attach_musig2_legs(summary: dict, legs) -> dict:
    """Attach leg results, or refuse a release summary that never ran them."""

    if not legs:
        raise InteropError(
            "refusing to emit a release summary: MuSig2 regtest legs were not run"
        )
    return {**summary, "musig2_legs": legs}


def invoke_musig2_release(script: Path | None = None, runner=None, config: Path | None = None,
                          on_leg=None, allow_dirty: bool = False) -> list[dict]:
    """Run both MuSig2 key architectures. A leg counts only when it prints PASS."""

    import os
    import subprocess

    script = _MUSIG2_SCRIPT if script is None else script
    if not script.is_file():
        raise InteropError(f"musig2 release script is missing: {script}")
    if runner is None:
        runner = subprocess.run
    env = os.environ.copy()
    if config is not None:
        env["CONFIG"] = str(config)
    if allow_dirty:
        env["ALLOW_DIRTY"] = "1"
    legs = []
    for architecture in MUSIG2_RELEASE_ARCHITECTURES:
        if on_leg is not None:
            on_leg("start", architecture, None)
        result = runner(
            [str(script), architecture],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        stdout = result.stdout if isinstance(result.stdout, str) else ""
        leg = musig2_leg_result(architecture, stdout, result.returncode)
        if not leg["passed"]:
            stderr = result.stderr if isinstance(result.stderr, str) else ""
            leg["reason"] = (stderr.strip() or stdout.strip() or
                             f"regtest script exited {result.returncode} without PASS")[-2000:]
        legs.append(leg)
        if on_leg is not None:
            on_leg("done", architecture, leg["passed"])
    return legs


def attach_bip375_validators(entries, validators=KNOWN_VALIDATORS):
    """Attach validators to bip375 scenarios. Does not rewrite weak modes."""

    return tuple(
        replace(entry, scenario=replace(entry.scenario, validators=tuple(validators)))
        if entry.scenario.suite == "bip375" else entry
        for entry in entries
    )

def _recorded_repairs(manifest: Path) -> list:
    payload = json.loads(manifest.read_text())
    repairs = payload.get("repairs") or []
    return list(repairs) if isinstance(repairs, list) else []


def case_result_for_run(scenario, manifest: Path, *, generated: bool, release: bool = False) -> CaseResult:
    """Batch row for a finished run. A weak mode is not ``passed``."""

    repairs = _recorded_repairs(manifest)
    payload = json.loads(manifest.read_text())
    status = check_case_status(scenario, generated=generated, repairs=repairs)
    reason = check_case_reason(scenario, generated=generated, repairs=repairs)
    if payload.get("verification_scope") == "not-evidence":
        status = "completed"
        reason = payload.get("reason") or reason
    failed_validators = [item for item in payload.get("validators", []) if item.get("status") == "failed"]
    if failed_validators:
        status = "failed"
        reason = "; ".join(item["error"] for item in failed_validators)
    if release and scenario.suite == "bip375" and not failed_validators:
        passed_validators = {
            item["name"] for item in payload.get("validators", [])
            if item.get("status") == "passed" and item.get("validated", 0) > 0
            and item.get("validated") == item.get("snapshots")
        }
        if not set(KNOWN_VALIDATORS) <= passed_validators:
            status = "failed"
            reason = "release validators did not check every required stage"
    if release and scenario.suite == "bip375" and payload.get("interop_lab", {}).get("status") != "passed":
        prior_failure = status == "failed"
        status = "failed"
        lab = payload.get("interop_lab", {})
        lab_reason = lab.get("reason") or "; ".join(
            failure for item in lab.get("files", []) for failure in item.get("failures", [])
        ) or "interop-lab-failed"
        reason = f"{reason}; {lab_reason}" if prior_failure else lab_reason
    return CaseResult(scenario.name, status, reason, str(manifest))


def run_summary(final_psbt: Path, manifest: Path, size: int) -> dict:
    """CLI summary. The scope label sits beside the artifact path."""

    payload = json.loads(manifest.read_text())
    summary = {"final_psbt": str(final_psbt)}
    scope = payload.get("verification_scope")
    if scope:
        summary["verification_scope"] = scope
    independent = payload.get("independent_check")
    if independent:
        summary["independent_check"] = independent
    summary["interop_lab_check"] = payload.get("interop_lab_check", "not-run")
    summary["manifest"] = str(manifest)
    summary["bytes"] = size
    return summary


def _with_utxo_source(payload: dict, utxo_source: str | None) -> dict:
    if utxo_source is None:
        return payload
    return {**payload, "utxo_source": utxo_source}


def _run_generated_scenario(
    config, scenario, signer_order: tuple[str, ...] | None = None,
    *, release: bool = False, allowed_findings: set[str] | None = None,
) -> tuple[Path, Path, int]:
    """Run one generated BIP-375 scenario with durable evidence on failure."""

    run_artifacts = ArtifactRun(config.artifact_root, scenario.name)
    workers, states = {}, []
    utxo_source = None
    repairs: tuple = ()
    validators = ()
    interop_lab = {"status": "not-run", "reason": "not requested", "snapshots": 0}
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
        interop_lab = _interop_stage(run_artifacts, release, allowed_findings or set())
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
            "interop_lab": interop_lab,
            "stages": _stage_records(validators, interop_lab),
            **_manifest_claim(scenario, repairs, validators, interop_lab),
        }, utxo_source))
        return run_artifacts.path / "final.psbt", manifest, len(final_psbt)
    except Exception as exc:
        run_artifacts.finalize(_with_utxo_source({
            "scenario": scenario.name,
            "suite": scenario.suite,
            "status": "failed",
            "error": str(exc),
            "checkouts": [asdict(state) for state in states],
            "interop_lab": interop_lab,
            "stages": _stage_records(validators, interop_lab, parser_ran=False),
            **_manifest_claim(scenario, repairs, validators, interop_lab),
        }, utxo_source))
        raise
    finally:
        for worker in workers.values():
            worker.stop()


def _run_psbt_scenario(
    config, scenario, psbt_path: Path, signer_order: tuple[str, ...] | None = None,
    *, release: bool = False, allowed_findings: set[str] | None = None,
) -> tuple[Path, Path, int]:
    """Run an externally prepared PSBT and retain provenance on every outcome."""

    run_artifacts = ArtifactRun(config.artifact_root, scenario.name)
    workers, states = {}, []
    utxo_source = None
    repairs: tuple = ()
    validators = ()
    interop_lab = {"status": "not-run", "reason": "not requested", "snapshots": 0}
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
        interop_lab = _interop_stage(run_artifacts, release and scenario.suite == "bip375",
                                    allowed_findings or set())
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
            "interop_lab": interop_lab,
            "stages": _stage_records(validators, interop_lab),
            **_manifest_claim(scenario, repairs, validators, interop_lab),
        }, utxo_source))
        return run_artifacts.path / "final.psbt", manifest, len(final_psbt)
    except Exception as exc:
        run_artifacts.finalize(_with_utxo_source({
            "scenario": scenario.name,
            "suite": scenario.suite,
            "status": "failed",
            "error": str(exc),
            "checkouts": [asdict(state) for state in states],
            "interop_lab": interop_lab,
            "stages": _stage_records(validators, interop_lab, parser_ran=False),
            **_manifest_claim(scenario, repairs, validators, interop_lab),
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


def _fully_signed(parsed) -> bool:
    signature_types = {0x02, 0x07, 0x08, 0x13}
    return all(any(entry.key_type in signature_types for entry in item.entries)
               for item in parsed.inputs)


def validate_psbt_file(config, path: Path, allowed_findings: set[str]) -> int:
    """Print a stage verdict for one file without starting a device emulator."""

    try:
        parsed = parse_psbt(path.read_bytes())
    except (OSError, InteropError) as exc:
        print(f"parser: failed ({exc})")
        for name in ("caravan", "spdk", "interop-lab"):
            print(f"{name}: not run (parser failed)")
        print("verdict: failed")
        return 1
    print("parser: passed")
    statuses = []
    for name, adapter_cls in _VALIDATOR_ADAPTERS.items():
        if name == "spdk" and not _fully_signed(parsed):
            print("spdk: not run (PSBT is not fully signed)")
            statuses.append("not-run")
            continue
        checkout = config.checkouts.get(name)
        if checkout is None:
            print(f"{name}: not run (checkout is not configured)")
            statuses.append("not-run")
            continue
        try:
            adapter_cls(checkout.path).validate([path])
        except InteropError as exc:
            print(f"{name}: failed ({exc})")
            statuses.append("failed")
        else:
            print(f"{name}: passed")
            statuses.append("passed")
    lab = validate_snapshots([path], allowed_findings)
    detail = lab.get("reason")
    if lab["status"] == "failed" and lab.get("files"):
        detail = "; ".join(lab["files"][0]["failures"])
    print(f"interop-lab: {lab['status'].replace('-', ' ')}" + (f" ({detail})" if detail else ""))
    statuses.append(lab["status"])
    verdict = "failed" if "failed" in statuses else "partial" if "not-run" in statuses else "passed"
    print(f"verdict: {verdict}")
    return int(verdict == "failed")


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
        if args.command == "validate-psbt" and args.config == Path("interop.yaml") and not args.config.is_file():
            args.config = Path("baseline/interop.yaml")
        config = load_config(args.config)
        if args.allow_dirty:
            config = replace(config, allow_dirty=True)
        if args.command == "validate-psbt":
            expectations_path = args.config.parent / "expectations.yaml"
            allowed = interop_lab_allowed_findings(expectations_path) if expectations_path.is_file() else set()
            return validate_psbt_file(config, args.psbt, allowed)
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
            lock_path = args.config.parent / LOCK_NAME
            if args.dry_run:
                current = read_lock(lock_path)
                print(json.dumps({"current": current, "proposed": pins, "changed": {
                    name: {"from": current.get(name), "to": revision}
                    for name, revision in pins.items() if current.get(name) != revision
                }}, indent=2))
            else:
                write_lock(lock_path, pins)
                print(json.dumps(pins, indent=2))
            return 0
        if args.command == "build":
            if args.dry_run:
                print(json.dumps([
                    {"checkout": name, "step": plan.name, "argv": list(plan.argv), "cwd": str(plan.cwd)}
                    for name, plans in plan_builds(config, args.only) for plan in plans
                ], indent=2))
                return 0
            print(json.dumps({"built": build(config, args.only)}, indent=2))
            return 0
        if args.command == "fetch":
            out, fetched = fetch(args.config, args.sources, args.checkouts_dir)
            print(json.dumps({"config": str(out), "fetched": fetched}, indent=2))
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
            def progress(stage: str, **fields) -> None:
                if args.progress_json:
                    print(json.dumps({"stage": stage, **fields}), file=sys.stderr, flush=True)

            entries = select(discover(args.scenarios_dir), args.project, config.suites)
            if args.exhaustive or args.release:
                entries = attach_bip375_validators(entries)
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
            expectations_path = args.config.parent / "expectations.yaml"
            allowed_findings = set()
            if args.release:
                if not expectations_path.is_file():
                    raise InteropError(f"release expectations are missing: {expectations_path}")
                require_lock_digest(expectations_path, args.config.parent / LOCK_NAME)
                allowed_findings = interop_lab_allowed_findings(expectations_path)
            runnable = [
                entry.scenario for entry in entries
                if entry.runnable_generated or entry.scenario.name in bindings
            ]
            progress("preflight", total=len(entries))
            checkout_states = run_preflight(config, runnable)
            batch = BatchRun(config.artifact_root, args.project)
            for index, entry in enumerate(entries, 1):
                progress("case-start", index=index, total=len(entries), scenario=entry.scenario.name)
                psbt_path = bindings.get(entry.scenario.name)
                if not entry.runnable_generated and psbt_path is None:
                    batch.add(CaseResult(entry.scenario.name, "blocked", entry.reason))
                    progress("case-done", index=index, total=len(entries), scenario=entry.scenario.name, status="blocked")
                    continue
                try:
                    if entry.runnable_generated:
                        _, manifest, _ = _run_generated_scenario(
                            config, entry.scenario, release=args.release,
                            allowed_findings=allowed_findings,
                        )
                        batch.add(case_result_for_run(entry.scenario, manifest, generated=True,
                                                      release=args.release))
                    elif not psbt_path.is_file():
                        batch.add(CaseResult(entry.scenario.name, "failed", f"PSBT is missing: {psbt_path}"))
                    else:
                        _, manifest, _ = _run_psbt_scenario(
                            config, entry.scenario, psbt_path, release=args.release,
                            allowed_findings=allowed_findings,
                        )
                        batch.add(case_result_for_run(entry.scenario, manifest, generated=False,
                                                      release=args.release))
                except InteropError as exc:
                    batch.add(CaseResult(entry.scenario.name, "failed", str(exc)))
                progress("case-done", index=index, total=len(entries), scenario=entry.scenario.name,
                         status=batch.results[-1].status)
            labels = None
            expectations = None
            previous = None
            if expectations_path.is_file():
                expectations = load_expectations(expectations_path)
                previous = previous_results(config.artifact_root, args.project, batch.path)
                labels = label_results(batch.results, expectations, previous)
            lab_reports = write_reports(batch.path, batch.results) if args.release else None
            manifest, report = batch.finalize(labels, checkout_states, lab_reports)
            payload = json.loads(manifest.read_text())
            summary = {
                "counts": payload["counts"],
                "passed": payload["counts"]["passed"],
                "required": payload["required"],
                "report": str(report),
                "manifest": str(manifest),
            }
            if labels is not None:
                summary["labels"] = payload["label_counts"]
                summary["variances"] = variance_records(
                    batch.results, labels, expectations, previous
                )
            if lab_reports is not None:
                summary["interop_lab_reports"] = lab_reports
            if args.release:
                def leg_progress(stage, architecture, passed):
                    progress(f"musig2-{stage}", architecture=architecture, passed=passed)

                summary = attach_musig2_legs(summary, invoke_musig2_release(
                    config=args.config, on_leg=leg_progress, allow_dirty=config.allow_dirty))
                legs_ok = release_legs_ok(summary["musig2_legs"])
            else:
                legs_ok = True
            print(json.dumps(summary, indent=2))
            if not legs_ok:
                return 1
            if not summary["required"]:
                print("every selected scenario was blocked; nothing was actually verified", file=sys.stderr)
                return 1
            if args.release and any(
                result.status == "failed" and result.artifact
                for result in batch.results
            ):
                return 1
            if labels is not None:
                return 1 if has_failures(labels) else 0
            return 0 if summary["passed"] == summary["required"] else 1
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
            return int(any(item.get("status") == "failed" for item in
                           json.loads(manifest.read_text()).get("validators", [])))
        if args.command == "run":
            final_psbt, manifest, size = _run_psbt_scenario(config, scenario, args.psbt, signer_order)
            print(json.dumps(run_summary(final_psbt, manifest, size), indent=2))
            return int(any(item.get("status") == "failed" for item in
                           json.loads(manifest.read_text()).get("validators", [])))
        print(f"valid: {scenario.name} ({scenario.suite})")
        return 0
    except InteropError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
