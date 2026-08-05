# position_constraints.py
# Position constraint encoding for 2D Bandwidth Minimization Problem

import math
from pysat.formula import IDPool
from pysat.card import CardEnc, EncType

def encode_vertex_position_constraints(n, X_vars, Y_vars, vpool):
    """
    Each vertex gets exactly one X and Y position

    Uses Sequential Counter exactly-k encoding for efficient constraint generation.
    MEMORY OPTIMIZED: Yields clauses one by one instead of accumulating in list.
    """
    print(f"Encoding vertex position constraints for {n} vertices...")
    clause_count = 0

    for v in range(1, n + 1):
        # Exactly-One for X using Sequential Counter
        sc_x_clauses = CardEnc.equals(X_vars[v], 1, vpool=vpool, encoding=EncType.seqcounter)
        for clause in sc_x_clauses.clauses:
            yield clause
            clause_count += 1

        # Exactly-One for Y using Sequential Counter
        sc_y_clauses = CardEnc.equals(Y_vars[v], 1, vpool=vpool, encoding=EncType.seqcounter)
        for clause in sc_y_clauses.clauses:
            yield clause
            clause_count += 1

    print(f"Generated {clause_count} clauses for vertex position constraints")

def encode_position_uniqueness_constraints(n, X_vars, Y_vars, vpool):
    """
    Each grid position (x,y) gets at most one vertex.

    Infers grid_side from the length of the position variable lists (X_vars[v]),
    so the outer loops cover exactly the sqrt(n) x sqrt(n) grid positions.

    Creates indicator variables and uses Sequential Counter at-most-k encoding
    for O(n²) complexity per position.
    MEMORY OPTIMIZED: Yields clauses one by one instead of accumulating in list.
    """
    first_vertex = next(iter(X_vars))
    cols = len(X_vars[first_vertex])
    rows = len(Y_vars[first_vertex])
    if cols <= 0 or rows <= 0:
        raise ValueError("Position axes must be non-empty")
    for v in range(1, n + 1):
        if len(X_vars[v]) != cols or len(Y_vars[v]) != rows:
            raise ValueError("All vertices must use the same rectangular host dimensions")
    print(f"Encoding position uniqueness for {rows}x{cols} grid ({n} vertices)...")
    clause_count = 0

    # Each position (x,y) has at most one vertex
    for x in range(cols):
        for y in range(rows):
            # Create indicator variables: node_at_pos[v] = (X_v_x ∧ Y_v_y)
            node_indicators = []
            for v in range(1, n + 1):
                indicator = vpool.id(f'node_{v}_at_{x}_{y}')
                node_indicators.append(indicator)

                # Equivalence: indicator ↔ (X_v_x ∧ Y_v_y)
                # indicator → X_v_x
                yield [-indicator, X_vars[v][x]]
                clause_count += 1
                # indicator → Y_v_y
                yield [-indicator, Y_vars[v][y]]
                clause_count += 1
                # (X_v_x ∧ Y_v_y) → indicator
                yield [indicator, -X_vars[v][x], -Y_vars[v][y]]
                clause_count += 1

            # Sequential Counter constraint: at most 1 node at position (x,y)
            sc_at_most_1 = CardEnc.atmost(node_indicators, 1, vpool=vpool, encoding=EncType.seqcounter)
            for clause in sc_at_most_1.clauses:
                yield clause
                clause_count += 1

    print(f"Generated {clause_count} clauses for position uniqueness")

def encode_all_position_constraints(n, X_vars, Y_vars, vpool):
    """
    Encode all position constraints for the 2D grid.

    n is the number of vertices. The grid dimensions are inferred from the
    length of the position variable lists (grid_side = sqrt(n) for perfect squares).

    Combines vertex position constraints (exactly-one) with
    position uniqueness constraints (at-most-one).
    Uses Sequential Counter encoding for efficient constraint generation.
    MEMORY OPTIMIZED: Generator that yields clauses one by one instead of returning huge list.
    """
    first_vertex = next(iter(X_vars))
    cols = len(X_vars[first_vertex])
    rows = len(Y_vars[first_vertex])
    print(f"\nEncoding position constraints")
    print(f"Problem: {n} vertices on {rows}x{cols} grid (injective placement)")
    print(f"Memory optimization: Streaming clauses via generator (no intermediate list)")

    # 1. Vertex position constraints (exactly-one) - stream via generator
    for clause in encode_vertex_position_constraints(n, X_vars, Y_vars, vpool):
        yield clause

    # 2. Position uniqueness constraints (at-most-one) - stream via generator
    for clause in encode_position_uniqueness_constraints(n, X_vars, Y_vars, vpool):
        yield clause

def create_position_variables(n, vpool, grid_side=None, *, rows=None, cols=None):
    """
    Create position variables for all vertices on a grid_side × grid_side grid.

    For 2DBMP, n vertices are placed on a square grid of exactly n positions.
    The grid side length is grid_side = √n (for perfect-square n).  Each axis
    has grid_side positions, giving a bijection from vertices to grid nodes.

    Args:
        n:         Number of vertices (must equal grid_side² for exact bijection).
        vpool:     Variable pool (IDPool).
        grid_side: Side length of the square grid.  Defaults to math.isqrt(n);
                   raises ValueError if n is not a perfect square and grid_side
                   is not supplied explicitly.

    Returns:
        (X_vars, Y_vars) where X_vars[v][pos] / Y_vars[v][pos] is the SAT
        variable for "vertex v is at column/row pos+1" (0-indexed pos).

    X_vars[v][pos] = vertex v at X position pos+1  (pos = 0 .. grid_side-1)
    Y_vars[v][pos] = vertex v at Y position pos+1  (pos = 0 .. grid_side-1)
    """
    if rows is not None or cols is not None:
        if grid_side is not None:
            raise ValueError("Use grid_side or rows/cols, not both")
        if rows is None or cols is None:
            raise ValueError("Both rows and cols are required for a rectangular host")
    else:
        if grid_side is None:
            grid_side = math.ceil(math.sqrt(n))
        rows = grid_side
        cols = grid_side

    if rows <= 0 or cols <= 0:
        raise ValueError("Host dimensions must be positive")
    if rows * cols < n:
        raise ValueError(f"Host {rows}x{cols} cannot hold {n} vertices")

    X_vars = {}  # X_vars[v][pos] = variable for vertex v at X position pos+1
    Y_vars = {}  # Y_vars[v][pos] = variable for vertex v at Y position pos+1

    for v in range(1, n + 1):
        X_vars[v] = [vpool.id(f'X_{v}_{pos}') for pos in range(1, cols + 1)]
        Y_vars[v] = [vpool.id(f'Y_{v}_{pos}') for pos in range(1, rows + 1)]

    print(f"Created position variables for {n} vertices on {rows}×{cols} grid")
    print(f"X (column) variables: {n} × {cols} = {n*cols}")
    print(f"Y (row) variables: {n} × {rows} = {n*rows}")
    print(f"Total primary coordinate variables: {n * (rows + cols)}")

    return X_vars, Y_vars

def test_position_constraints():
    """
    Test position constraints with small example
    MEMORY OPTIMIZED: Works with generator to stream clauses directly to solver
    """
    from pysat.solvers import Glucose4

    print("Testing position constraints")

    n = 4  # 4 vertices on a 2x2 grid (perfect square, grid_side=2)
    vpool = IDPool()

    # Create position variables (grid_side = sqrt(4) = 2)
    X_vars, Y_vars = create_position_variables(n, vpool)
    grid_side = math.isqrt(n)

    # Encode all position constraints (generator - no intermediate list)
    clause_generator = encode_all_position_constraints(n, X_vars, Y_vars, vpool)

    print(f"\nTesting with streaming clauses...")

    # Test with SAT solver - stream clauses directly
    solver = Glucose4()
    clause_count = 0
    for clause in clause_generator:
        solver.add_clause(clause)
        clause_count += 1

    print(f"Total clauses added: {clause_count}")

    if solver.solve():
        model = solver.get_model()
        print("SAT: Position constraints are satisfiable")

        # Extract and display solution
        print("\nSolution:")
        for v in range(1, n + 1):
            x_pos = None
            y_pos = None

            # Find X position (only grid_side positions exist)
            for pos in range(grid_side):
                if X_vars[v][pos] in model:
                    x_pos = pos + 1
                    break

            # Find Y position
            for pos in range(grid_side):
                if Y_vars[v][pos] in model:
                    y_pos = pos + 1
                    break

            print(f"  Vertex {v}: X={x_pos}, Y={y_pos}")
    else:
        print("UNSAT: Position constraints are unsatisfiable - ERROR!")

    solver.delete()
    print("Position constraints test complete (memory optimized)")

if __name__ == '__main__':
    test_position_constraints()
