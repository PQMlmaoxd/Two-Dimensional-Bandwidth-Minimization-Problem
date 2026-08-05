# Dataset

The artifact contains 136 Matrix Market files: 43 Regular graphs in `regular
set/` and 93 Harwell-Boeing matrices in `Harwell Boeing set/`.

The parser in `python/utils/mtx_parser.py` is authoritative for new runs. It
uses the matrix row count as the vertex order, interprets square matrices as
adjacency data, ignores explicit numeric zero entries, and projects rectangular
matrices through row support. Harwell-Boeing source files originate from the
SuiteSparse Matrix Collection; retain their Matrix Market headers and consult
the upstream collection for distribution terms.
