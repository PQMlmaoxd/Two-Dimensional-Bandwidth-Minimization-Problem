from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from python.solve import SUBMIT_ROOT, run
from python.solvers.entrypoints import METHODS
from python.utils.verification_io import write_json_atomic


def _instances(args: argparse.Namespace) -> list[Path]:
    paths = [Path(value) for value in args.instance]
    if args.instances_file is not None:
        for line in args.instances_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                paths.append(Path(line))
    if not paths:
        raise ValueError("provide at least one --instance or --instances-file entry")
    resolved: list[Path] = []
    for path in paths:
        candidate = path if path.is_absolute() else args.dataset_root / path
        if candidate.suffix != ".mtx":
            candidate = candidate.with_suffix(".mtx")
        resolved.append(candidate.resolve())
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a manifest-listed 2DBMP batch")
    parser.add_argument("--method", choices=sorted(METHODS), required=True)
    parser.add_argument("--solver", default="cadical195")
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, default=SUBMIT_ROOT)
    parser.add_argument("--instance", action="append", default=[])
    parser.add_argument("--instances-file", type=Path)
    parser.add_argument("--sa-k", type=int)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout < 0:
        raise SystemExit("--timeout must be non-negative")
    instances = _instances(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, str]] = []
    for instance in instances:
        output = args.output_dir / f"{instance.stem}__{args.method}_verify.json"
        payload = run(
            instance=instance,
            method_key=args.method,
            solver=args.solver,
            timeout_s=args.timeout or None,
            output=output,
            sa_k=args.sa_k,
            quiet=args.quiet,
        )
        results.append(
            {
                "instance": str(instance),
                "output": output.name,
                "schema": str(payload["schema"]),
            }
        )
    write_json_atomic(
        args.output_dir / "batch_manifest.json",
        {
            "schema": "2dbmp-batch-manifest-v1",
            "method": args.method,
            "solver": args.solver,
            "timeout_s": args.timeout,
            "runs": results,
        },
    )
    return 0 if all(row["schema"] == "2dbmp-verification-v1" for row in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
