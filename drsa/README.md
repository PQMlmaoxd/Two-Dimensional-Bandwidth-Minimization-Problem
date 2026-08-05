# DRSA upper-bound heuristic

`drsa.cpp` is a C++17 reconstruction of the DRSA-style simulated-annealing
upper-bound heuristic used by this artifact. It implements the square-grid
evaluation described in:

> Torres-Jimenez, J. et al. "Two dimensional bandwidth minimization problem
> using a novel evaluation function." *Expert Systems With Applications* 290
> (2025), 127986. https://doi.org/10.1016/j.eswa.2025.127986

The search keeps a position permutation and its inverse, uses random exchange,
edge-neighborhood exchange, and short rotations with probabilities 0.6, 0.2,
and 0.2, and applies simulated annealing. Its evaluation is the bandwidth plus
the normalized mixed-radix edge-distance rank from Equation (17).

Build it from the artifact root:

```bash
make drsa
```

The executable interface is:

```bash
build/drsa <matrix.mtx> <seed> <time_limit_s>
```

It writes `RESULT <bandwidth>` to stdout. A positive time limit is a wall-clock
budget; `0` runs the complete cooling schedule.
