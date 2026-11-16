<div align="center">

# Two-Dimensional Bandwidth Minimization

**Exact SAT methods, benchmark inputs, and result records for the two-dimensional bandwidth minimization problem.**

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](requirements.txt)

[Quick start](#quick-start) · [Methods](#methods) · [Results](#results) · [Verification](#verification) · [Datasets](dataset/README.md) · [DRSA heuristic](drsa/README.md)

</div>

This repository accompanies the paper *An Exact Approach for the 2D Bandwidth Minimization Problem using SAT-based Sequence Counting*. It contains the SAT encodings and solver workers, the benchmark inputs, the recorded results, and a checker for those results.

## The problem

Place the vertices of an undirected graph on distinct cells of a grid. The bandwidth of a placement is the largest Manhattan distance between the cells of two adjacent vertices. The goal is a placement with the smallest possible bandwidth. The [methods table](#methods) lists the host grid each method uses.

## Installation

Requirements:

- Python 3.10 or newer.
- `python-sat` 1.9.dev3 or newer, below 2.0, as pinned in `requirements.txt`.
- GNU make (optional). The Makefile wraps the DRSA build, the unit tests, and the verification.
- A C++17 compiler for the DRSA heuristic, which `make test` also builds. MinGW GCC 6.3 builds `drsa/drsa.cpp`. Other compilers are untested.

```bash
python3 -m pip install -r requirements.txt
```

### Windows

- Use `python` instead of `python3`, and pass `PYTHON=python` to `make`.
- Clone to a short path, because some record names are long and a deep folder can exceed the 260-character path limit:

  ```bash
  git clone https://github.com/PQMlmaoxd/Two-Dimensional-Bandwidth-Minimization-Problem.git C:/src/2dbmp
  ```

  `.gitattributes` keeps the `.mtx` inputs with LF endings, whatever `core.autocrlf` is set to. The verifier compares their SHA-256 hashes with the files on disk, so CRLF endings would fail `make verify`. A clone made before `.gitattributes` was added has CRLF copies of these files, so clone again rather than editing them.
- The workers run in UTF-8 mode, so the solver's Unicode output works on any Windows code page.
- A wall-clock limit stops the worker and every process it started, on Windows as on POSIX systems. The run then ends with its normal record.
- `make drsa` writes `build/drsa.exe`. Set `DRSA_BINARY=build/drsa.exe` so that `python.utils.drsa_upper_bound` finds the binary.

## Quick start

Run one instance with the `square` method. On Windows, read the [Windows notes](#windows) first.

```bash
python3 -m python.solve "dataset/regular set/path10.mtx" \
  --method square --solver cadical195 --timeout 3600 \
  --output build/path10_square.json
```

The command writes one JSON record to `--output`, with `.stdout.log` and `.stderr.log` files beside it. The record's `outcome.status` is `OPTIMAL_BY_UNSAT`, `OPTIMAL_BY_BOUND`, `TIMEOUT_FEASIBLE`, or `INTERRUPTED_FEASIBLE`. If the run fails, for example because the input file is missing, the record has schema `2dbmp-run-error-v1`. The command exits with 0 when it writes a verification record and with 2 otherwise.

## Methods

| `--method` | Description | Host grid |
| --- | --- | --- |
| `square` (default) | Incremental SAT with cutoff and symmetry breaking | ⌈√n⌉ × ⌈√n⌉ |
| `square-non-incremental` | `square` without incremental solving | ⌈√n⌉ × ⌈√n⌉ |
| `square-no-symmetry` | `square` without symmetry breaking | ⌈√n⌉ × ⌈√n⌉ |
| `square-naive-amo` | `square` with a naive at-most-one encoding | ⌈√n⌉ × ⌈√n⌉ |
| `square-standard` | `square` with the standard symmetry encoding | ⌈√n⌉ × ⌈√n⌉ |
| `nxn` | Incremental SAT with cutoff and symmetry breaking | n × n |
| `two-row` | Incremental SAT for the two-row host | 2 × ⌈n/2⌉ |

| Option | Default | Meaning |
| --- | --- | --- |
| `--method` | `square` | A method from the table above |
| `--solver` | `cadical195` | PySAT solver name. `cadical195` is CaDiCaL 1.9.5. |
| `--timeout` | `3600` | Wall-clock limit in seconds. `0` disables the limit. |
| `--output` | required | Path of the JSON record |
| `--sa-k` | none | Reference bound, `two-row` only |
| `--quiet` | off | Silences progress output, `two-row` only |

## Batch runs

```bash
python3 -m python.benchmarks.batch --method nxn \
  --output-dir build/nxn --dataset-root . --instances-file instances.txt
```

The manifest holds one instance per line. Relative paths resolve against `--dataset-root`, and lines that start with `#` are skipped:

```text
# One instance per line
dataset/regular set/path10.mtx
dataset/Harwell Boeing set/arc130.mtx
```

Repeated `--instance` options add instances without a manifest. The batch writes `<stem>__<method>_verify.json` for each instance, plus a `batch_manifest.json` beside them. It exits with 0 when every record has the verification schema.

## Results

`results/csv_res/` holds eight CSV summaries for the Regular and Harwell-Boeing sets on square, n × n, and two-row hosts. Each row gives the instance, its vertex count `n`, its declared entry count `E`, and, for each solver, the value of `K`, the solve time in seconds, and the status. For Harwell-Boeing matrices, `E` counts the declared Matrix Market entries, which need not equal the number of simple-graph edges.

`results/logs/SAT/` holds 908 JSON records from the SAT methods, plus one `manifest.json`. Each record names the instance, method, host grid, status, bounds, and placement. Timed-out runs that found no placement use the schema `2dbmp-no-witness-v1` and have no placement.

| Status | Meaning | Records |
| --- | --- | --- |
| `OPTIMAL_BY_UNSAT` | No placement has a bandwidth one below the reported value | 434 |
| `OPTIMAL_BY_BOUND` | A certified lower bound equals the reported bandwidth | 91 |
| `TIMEOUT_FEASIBLE` | The time limit ended the run with a verified placement, without an optimality proof | 354 |
| `TIMEOUT_NO_WITNESS` | The time limit ended the run before any feasible placement was found | 29 |

`results/logs/CPLEX/` (with `CP/` and `MIP/` subfolders, 728 files) and `results/logs/GUROBI/` (364 files) hold the `.out` logs of the commercial runs.

## Verification

```bash
make verify
```

This runs `python -m python.utils.verification_artifacts verify --root results/logs/SAT`. For each record, the verifier:

- checks the SHA-256 hash of the input file,
- parses the input and compares the graph with the metadata in the record,
- recomputes the bandwidth of the stored placement on the recorded host,
- checks the status against the bounds and the termination K.

A folder with its own `manifest.json` is verified as a separate tree, and its manifest must match its records. `results/logs/SAT/Harwell Boeing two row/` is such a folder, so `make verify` covers all 908 records. To check one tree on its own:

```bash
python3 -m python.utils.verification_artifacts verify \
  --root "results/logs/SAT/Harwell Boeing two row"
```

This reports `Verified: 93 artifacts`.

## DRSA heuristic

[`drsa/`](drsa/README.md) contains a simulated-annealing heuristic that returns an upper bound on the bandwidth. `make drsa` builds it into `build/drsa`, and `python.utils.drsa_upper_bound` calls it.

## Optional commercial models

`cplex_gurobi/` holds formulations for IBM CPLEX (CP Optimizer and MIP) and Gurobi. The repository does not include these solvers or their licenses.

- `2dbmp_cp.py` builds a CP Optimizer model with the `docplex` package. It looks for the `cpoptimizer` executable from IBM CPLEX Optimization Studio, set through `CPOPTIMIZER_BINARY` or on `PATH`.
- `2dbmp_ilp.py` with `--solver lp` writes an LP file and needs no solver. `--solver gurobi` needs `gurobipy` and a Gurobi license. `--solver cplex` runs the `cplex` executable, set through `CPLEX_BINARY` or on `PATH`, and falls back to the `docplex` package when no executable is found.

## Repository layout

| Path | Contents |
| --- | --- |
| `python/encoders/` | SAT encodings for distance, position, and symmetry-breaking constraints |
| `python/solvers/` | Solver workers, the method registry in `entrypoints.py`, and the solver factory |
| `python/benchmarks/` | Batch runner |
| `python/utils/` | Input parsing, host grids, process supervision, and verification |
| `python/tests/` | Unit tests |
| `drsa/` | DRSA-style upper-bound heuristic in C++17 |
| `dataset/` | Benchmark inputs |
| `results/` | CSV summaries and run logs |
| `cplex_gurobi/` | Optional commercial formulations |

## Tests

`make test` builds the DRSA binary and runs the unit tests in `python/tests/`. Install the pinned `python-sat` first. One test uses the `cadical300` solver, which needs `python-sat` 1.9.dev3 or newer.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). NOTICE lists the third-party components: PySAT, CaDiCaL and Glucose (used through PySAT), the Harwell-Boeing inputs from the SuiteSparse Matrix Collection, and the optional IBM CPLEX and Gurobi tools, which are not included.
