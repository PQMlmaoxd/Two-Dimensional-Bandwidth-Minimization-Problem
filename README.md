# 2DBMP Experimental Artifact

This artifact contains the exact SAT implementations, benchmark inputs, result
summaries, and independently checked feasible-witness records for the
two-dimensional bandwidth minimization problem.

## Requirements

- Python 3.10 or newer
- `python-sat>=1.9.dev3,<2`
- A C++17 compiler only when building the optional DRSA upper-bound heuristic

Install the core dependency with:

```bash
python3 -m pip install -r requirements.txt
```

The commercial models under `cplex_gurobi/` are optional and are not required
for the SAT implementations or verification tools. Their external requirements
are:

- Gurobi: a working Gurobi installation and license.
- CPLEX CP: IBM CPLEX Optimization Studio and the `docplex` Python package.
- CPLEX MIP: IBM CPLEX Optimization Studio and the matching `cplex` Python
  API.

## Run One Instance

Run commands from this directory. The public entrypoint supervises a worker
process, applies a wall-clock deadline, retains stdout/stderr logs beside the
output, and publishes one terminal JSON result.

```bash
python3 -m python.solve "dataset/regular set/path10.mtx" \
  --method square --solver cadical195 --timeout 3600 \
  --output /tmp/path10_square.json
```

Supported `--method` values are `square`, `square-non-incremental`,
`square-no-symmetry`, `square-naive-amo`, `square-standard`, `nxn`, and
`two-row`. CaDiCaL 1.9.5 (`cadical195`) is the default backend.

For a batch, provide repeatable instance paths in a text manifest, one path per
line:

```bash
python3 -m python.benchmarks.batch --method nxn --output-dir /tmp/nxn \
  --dataset-root . --instances-file instances.txt
```

## Results

- `results/csv_res/` contains eight CSV summaries covering Regular and
  Harwell-Boeing instances on square, n×n, and two-row hosts.
- `results/logs/SAT/` contains JSON witness records for the SAT methods.
- `results/logs/CPLEX/` and `results/logs/GUROBI/` contain the structured
  `.out` records for the commercial CP, CPLEX MIP, and Gurobi runs.

Verify the SAT witness records from this directory:

```bash
python3 -m python.utils.verification_artifacts verify --root results/logs/SAT
```

Harwell-Boeing `E` values in the CSV summaries denote declared Matrix Market
entries, not necessarily normalized simple-graph edge counts.

## DRSA

Build the optional C++17 DRSA-style upper-bound heuristic with `make drsa`.
The output is written to `build/drsa` and is intentionally not stored in the
artifact. It accepts a Matrix Market file, seed, and wall-clock budget, and is
used through `python.utils.drsa_upper_bound`.

## Layout

- `python/encoders/`: SAT encodings.
- `python/solvers/`: exact solver workers and backend factory.
- `python/utils/`: parsing, verification, checkpoint, and process-supervision
  utilities.
- `dataset/`: 43 Regular and 93 Harwell-Boeing Matrix Market inputs.
- `cplex_gurobi/`: optional commercial formulations.
