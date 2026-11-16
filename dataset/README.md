# Datasets

The inputs come from two sources:

- [Matrix Market Collection](https://math.nist.gov/MatrixMarket/matrices.html)
- [neteasefans repository](https://github.com/neteasefans/two-dimensional-bandwidth-minimization-problem)

| Folder | Files | Format |
| --- | --- | --- |
| `regular set/` | 43 graphs | Legacy edge list: optional `%` comment lines, an `n n m` header, then `m` edges |
| `Harwell Boeing set/` | 93 matrices | Matrix Market coordinate format |

The Harwell-Boeing files originate from the [SuiteSparse Matrix Collection](https://sparse.tamu.edu/). Keep their Matrix Market headers, and check the upstream collection for distribution terms.

Four Harwell-Boeing matrices are rectangular: `ash219` (219 × 85), `wm1` (207 × 277), `wm2` (207 × 260), and `wm3` (207 × 260).

## How the parser reads the files

`python/utils/mtx_parser.py` is the authoritative parser for new runs. It:

1. Uses the row count as the number of vertices.
2. Ignores entries whose value is zero. Pattern entries have no value and always count.
3. Treats a square matrix as an adjacency matrix and ignores its diagonal entries.
4. Treats a rectangular matrix as a row-support graph: two rows are adjacent when they share a nonzero column.
5. Reads legacy edge lists as `legacy-edge-list-v1` and checks that the number of edge lines equals `m`.

It accepts the `pattern`, `integer`, `real`, and `complex` fields, and the `general`, `symmetric`, `skew-symmetric`, and `hermitian` symmetry tags.

For square matrices, the DRSA reader in [`drsa/`](../drsa/README.md) follows the same rules. It rejects rectangular matrices.
