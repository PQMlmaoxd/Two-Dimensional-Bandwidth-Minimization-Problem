# DRSA upper-bound heuristic

`drsa/drsa.cpp` is an implementation of the DRSA-style simulated annealing heuristic for the square-grid evaluation in:

> Torres-Jimenez, J. et al. "Two dimensional bandwidth minimization problem using a novel evaluation function." *Expert Systems With Applications* 290 (2025), 127986. https://doi.org/10.1016/j.eswa.2025.127986

## Search

The search keeps a position permutation and its inverse. Each step applies one of three moves:

- A random exchange of two positions, with probability 0.6.
- An exchange of the positions of the two endpoints of a random edge, with probability 0.2.
- A left rotation over a span of at most five positions, with probability 0.2.

The score is the bandwidth plus the normalized mixed-radix edge-distance rank from Equation (17) of the paper.

The annealing starts at temperature 1000 and multiplies the temperature by 0.99 after each chain. It stops below 10⁻¹². A chain runs for 400 moves at the start. Its length grows as the temperature falls, up to `max(400, 10·n·m)` moves, where `n` is the number of vertices and `m` is the number of edges.

## Build and run

```bash
make drsa
build/drsa "dataset/regular set/path10.mtx" 42 10
```

The arguments are the matrix file, the seed, and the time limit in seconds. A positive time limit is a wall-clock budget, and `0` runs the complete cooling schedule. The program prints `RESULT <bandwidth>` on stdout. It exits with 2 on a usage error and with 1 on an input error, which it reports on stderr.

The source needs a C++17 compiler. It builds with MinGW GCC 6.3, the version tested here. With MinGW, `make drsa` writes `build/drsa.exe`.

## Input rules

The reader in `drsa.cpp` is separate from the parser in `python/utils/mtx_parser.py`. For square matrices, it applies the same graph rules:

- It accepts square coordinate matrices only. The four rectangular Harwell-Boeing matrices (`ash219`, `wm1`, `wm2`, `wm3`) are rejected.
- It drops diagonal entries and merges duplicate pairs.
- It drops zero-valued entries, as the parser does. Pattern matrices have no values, so all of their entries count. An entry without its value is an error.

For square matrices, the DRSA graph is the parser's graph. The reported value is therefore an upper bound on the minimum bandwidth of that graph on the square host.
