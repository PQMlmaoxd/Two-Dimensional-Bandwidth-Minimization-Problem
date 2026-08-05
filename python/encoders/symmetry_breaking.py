from typing import List, Dict, Tuple, Generator
from pysat.formula import IDPool


def select_anchor_vertex(adj: List[List[int]]) -> int:
    """
    Select the best anchor vertex for symmetry breaking based on degree.

    WHY HIGH DEGREE:
    Mathematically, any single vertex reduces search space by 8x.
    But from SAT solver engineering perspective, pinning a high-degree
    vertex (hub) to the first quadrant corner triggers a BCP cascade:
    the bandwidth constraint immediately forces ALL neighbors of the hub
    into a radius-K zone around that corner via unit propagation.
    A degree-1 vertex only constrains 1 neighbor, leaving the rest of
    the graph unconstrained until deep branching.

    Tie-breaking:
    1. Highest degree (primary)
    2. Highest sum of neighbor degrees — the "hub of hubs" wins (secondary)
    3. Lowest vertex index for determinism (tertiary)

    Args:
        adj: Adjacency list (1-indexed). adj[v] = list of neighbors of v.
             adj[0] is unused (or can be empty). Length = n_vertices + 1.

    Returns:
        Anchor vertex index (1-indexed).

    Example:
        adj = [[], [2, 3], [1, 3, 4], [1, 2], [2]]   # 1-indexed, 4 vertices
        select_anchor_vertex(adj) → 2  (degree 3, hub)
    """
    n_vertices = len(adj) - 1  # adj is 1-indexed, adj[0] unused
    if n_vertices <= 0:
        return 1

    degrees = [len(adj[v]) for v in range(n_vertices + 1)]  # degrees[0] unused

    best = 1
    best_deg = degrees[1]
    best_neighbor_deg_sum = sum(degrees[u] for u in adj[1])

    for v in range(2, n_vertices + 1):
        deg_v = degrees[v]
        neighbor_deg_sum_v = sum(degrees[u] for u in adj[v])

        if (deg_v > best_deg
                or (deg_v == best_deg and neighbor_deg_sum_v > best_neighbor_deg_sum)):
            best = v
            best_deg = deg_v
            best_neighbor_deg_sum = neighbor_deg_sum_v
        # ties on both → keep lower index (already processed in order)

    return best


def calculate_symmetry_boundary(n: int) -> int:
    """
    Calculate the boundary m = ⌈n/2⌉ for symmetry breaking.

    Uses ceiling division to ensure center positions are included.

    Args:
        n: Grid dimension (n×n grid)

    Returns:
        m: Boundary value such that valid positions are 1..m

    Examples:
        n=3 → m=2 (positions 1,2 are valid, position 3 is forbidden)
        n=4 → m=2 (positions 1,2 are valid, positions 3,4 are forbidden)
        n=5 → m=3 (positions 1,2,3 are valid, positions 4,5 are forbidden)

    Edge Case Prevention:
        - Using (n+1)//2 ensures ceiling behavior with integer arithmetic
        - NEVER use n//2 which gives floor (could exclude valid center positions)
    """
    # Ceiling division: (n + 1) // 2 = ⌈n/2⌉
    # This is correct for all n ≥ 1
    m = (n + 1) // 2
    return m


def encode_symmetry_breaking_constraints(
    n: int,
    X_vars: Dict[int, List[int]],
    Y_vars: Dict[int, List[int]],
    first_vertex: int = 1,
    verbose: bool = True
) -> Generator[List[int], None, None]:
    """
    Restrict one anchor to the upper-left quarter of a square host.

    Horizontal and vertical reflections map every embedding to one whose anchor
    satisfies x <= ceil(n/2) and y <= ceil(n/2). This function intentionally does
    not add a diagonal/axis-swap constraint, so it is Q1-only rather than complete
    D4 symmetry breaking.

    Encoding:
    - Unit clauses: [¬X_v_pos] for all pos > m (Q1 constraint on X)
    - Unit clauses: [¬Y_v_pos] for all pos > m (Q1 constraint on Y)

    Args:
        n: Grid dimension (n×n grid)
        X_vars: Position variables for X axis. X_vars[v][pos] = variable for vertex v at position pos+1
        Y_vars: Position variables for Y axis. Y_vars[v][pos] = variable for vertex v at position pos+1
        first_vertex: Which vertex to constrain (default: 1). ONLY constrain ONE vertex!
        verbose: Whether to print detailed encoding information

    Yields:
        List[int]: Unit clauses implementing the Q1 anchor restriction

    Example for n=4, first_vertex=1:
        m = ⌈4/2⌉ = 2
        Q1 constraints (4 unit clauses):
          [-X_1_3], [-X_1_4]  # x ≤ 2
          [-Y_1_3], [-Y_1_4]  # y ≤ 2
        Valid anchor positions are the four cells in the 2x2 upper-left quarter.

    WARNING:
        - NEVER apply this to multiple vertices simultaneously!
        - Doing so can eliminate valid optimal solutions that cannot be recovered
          by rotation/reflection.
    """
    # Edge case: n=1, only one position, no symmetry to break
    if n <= 1:
        if verbose:
            print(f"Symmetry breaking: n={n}, no constraints needed (single position)")
        return

    # Calculate boundary using ceiling division
    m = calculate_symmetry_boundary(n)

    if verbose:
        print(f"\n=== Symmetry Breaking Constraints (Q1 anchor) ===")
        print(f"Grid size: {n}×{n}")
        print(f"Boundary m = ⌈{n}/2⌉ = {m}")
        print(f"Valid region for v_{first_vertex}: x ∈ [1,{m}], y ∈ [1,{m}]")

    clause_count = 0
    # Get the position variables for the first vertex
    # X_vars[v] is 0-indexed: X_vars[v][pos-1] = variable for position pos
    v = first_vertex

    if v not in X_vars or v not in Y_vars:
        raise ValueError(f"Vertex {v} not found in position variables")

    x_positions = X_vars[v]  # List of variables for X positions (0-indexed)
    y_positions = Y_vars[v]  # List of variables for Y positions (0-indexed)

    # Verify we have the right number of position variables
    if len(x_positions) != n or len(y_positions) != n:
        raise ValueError(f"Expected {n} positions, got X={len(x_positions)}, Y={len(y_positions)}")

    # PART 1: Q1 Constraint - Forbid X positions > m
    forbidden_x_positions = []
    for pos in range(m + 1, n + 1):  # pos = m+1, m+2, ..., n
        var_index = pos - 1  # Convert to 0-indexed
        x_var = x_positions[var_index]

        # Unit clause: ¬X_v_pos
        yield [-x_var]
        forbidden_x_positions.append(pos)
        clause_count += 1

    # PART 2: Q1 Constraint - Forbid Y positions > m
    forbidden_y_positions = []
    for pos in range(m + 1, n + 1):  # pos = m+1, m+2, ..., n
        var_index = pos - 1  # Convert to 0-indexed
        y_var = y_positions[var_index]

        # Unit clause: ¬Y_v_pos
        yield [-y_var]
        forbidden_y_positions.append(pos)
        clause_count += 1

    if verbose:
        print(f"Generated {clause_count} symmetry breaking clauses:")
        print(f"  Q1 unit clauses: {len(forbidden_x_positions) + len(forbidden_y_positions)}")
        print(f"    Forbidden X positions: {forbidden_x_positions}")
        print(f"    Forbidden Y positions: {forbidden_y_positions}")

        # Calculate valid positions in Q1 square
        valid_positions_count = m * m  # Q1 square
        total_grid_positions = n * n
        print(f"  Valid positions in Q1: {valid_positions_count}/{total_grid_positions}")
        print(f"  Theoretical speedup: ~4x (Q1 symmetry breaking)")
        print(f"=== End Symmetry Breaking ===\n")


def get_symmetry_stats(n: int) -> Dict[str, any]:
    """
    Get statistics about symmetry breaking for a given grid size.

    With Q1 symmetry breaking (approximately 4x reduction).

    Args:
        n: Grid dimension

    Returns:
        Dictionary with symmetry breaking statistics
    """
    m = calculate_symmetry_boundary(n)

    total_positions = n * n
    # Valid positions: Q1 square (x ≤ m and y ≤ m)
    valid_positions_q1 = m * m
    forbidden_positions = total_positions - valid_positions_q1

    # Number of forbidden coordinates on each axis for Q1 constraint
    forbidden_coords = n - m
    q1_unit_clauses = 2 * forbidden_coords  # X and Y axes

    total_clauses = q1_unit_clauses

    return {
        'grid_size': n,
        'boundary_m': m,
        'total_positions': total_positions,
        'valid_positions': valid_positions_q1,
        'forbidden_positions': forbidden_positions,
        'forbidden_coords_per_axis': forbidden_coords,
        'total_unit_clauses': q1_unit_clauses,
        'total_clauses': total_clauses,
        'reduction_percentage': (forbidden_positions / total_positions) * 100,
        'anchor_domain_ratio': total_positions / valid_positions_q1,
        'theoretical_speedup': total_positions / valid_positions_q1,
    }


def encode_rectangular_anchor_symmetry(
    rows: int,
    cols: int,
    X_vars: Dict[int, List[int]],
    Y_vars: Dict[int, List[int]],
    anchor_vertex: int,
) -> Generator[List[int], None, None]:
    """Break row/column reflections on a rectangle using one anchor.

    X denotes columns and Y denotes rows. Axis-swap/diagonal symmetry is not
    used, because it is not an automorphism of a proper rectangle.
    """

    if rows <= 0 or cols <= 0:
        raise ValueError("Host dimensions must be positive")
    if anchor_vertex not in X_vars or anchor_vertex not in Y_vars:
        raise ValueError(f"Anchor vertex {anchor_vertex} is missing")
    if len(X_vars[anchor_vertex]) != cols or len(Y_vars[anchor_vertex]) != rows:
        raise ValueError("Position variables do not match the rectangular host")

    row_boundary = (rows + 1) // 2
    col_boundary = (cols + 1) // 2
    for index in range(col_boundary, cols):
        yield [-X_vars[anchor_vertex][index]]
    for index in range(row_boundary, rows):
        yield [-Y_vars[anchor_vertex][index]]


def get_rectangular_symmetry_stats(rows: int, cols: int) -> Dict[str, int | str]:
    if rows <= 0 or cols <= 0:
        raise ValueError("Host dimensions must be positive")
    row_boundary = (rows + 1) // 2
    col_boundary = (cols + 1) // 2
    return {
        "profile": "rectangle-reflections",
        "rows": rows,
        "cols": cols,
        "row_boundary": row_boundary,
        "col_boundary": col_boundary,
        "valid_anchor_cells": row_boundary * col_boundary,
        "total_cells": rows * cols,
        "total_clauses": rows - row_boundary + cols - col_boundary,
    }


def test_symmetry_breaking():
    """
    Test symmetry breaking constraints with various grid sizes.
    Validates correctness of boundary calculation and clause generation.
    Tests Q1 symmetry breaking (4x reduction).
    """
    print("=" * 60)
    print("SYMMETRY BREAKING CONSTRAINT TESTS (Q1 - 4x)")
    print("=" * 60)

    test_cases = [
        (1, 1),   # Edge case: single position
        (2, 1),   # Even, small
        (3, 2),   # Odd, includes center
        (4, 2),   # Even
        (5, 3),   # Odd, includes center
        (6, 3),   # Even
        (10, 5),  # Larger even
        (11, 6),  # Larger odd
    ]

    print("\n1. Testing boundary calculation m = ⌈n/2⌉")
    print("-" * 40)

    for n, expected_m in test_cases:
        actual_m = calculate_symmetry_boundary(n)
        status = "✓ PASS" if actual_m == expected_m else "✗ FAIL"
        print(f"  n={n:2d}: m={actual_m} (expected {expected_m}) {status}")

    print("\n2. Testing clause generation for n=4 (Q1 anchor)")
    print("-" * 40)

    # Create mock position variables for n=4
    vpool = IDPool()
    n = 4
    m = 2
    X_vars = {1: [vpool.id(f'X_1_{p}') for p in range(1, n + 1)]}
    Y_vars = {1: [vpool.id(f'Y_1_{p}') for p in range(1, n + 1)]}

    clauses = list(encode_symmetry_breaking_constraints(n, X_vars, Y_vars, verbose=True))

    print(f"\nGenerated clauses: {clauses}")

    # Expected:
    # - 2 Q1 unit clauses per axis (x>2 and y>2): 4 total
    # Total: 4 clauses
    expected_q1_clauses = 4  # forbid x=3,4 and y=3,4
    expected_total = expected_q1_clauses

    print(f"\nExpected: {expected_total} clauses")
    print(f"  Q1 unit clauses: {expected_q1_clauses}")

    # Verify clauses
    assert len(clauses) == expected_total, f"Expected {expected_total} clauses, got {len(clauses)}"

    # Count unit vs 2-literal clauses
    unit_clauses = [c for c in clauses if len(c) == 1]
    two_literal_clauses = [c for c in clauses if len(c) == 2]

    print(f"\nVerification:")
    print(f"  Unit clauses: {len(unit_clauses)} (expected {expected_q1_clauses})")
    print(f"  2-literal clauses: {len(two_literal_clauses)} (expected 0)")

    assert len(unit_clauses) == expected_q1_clauses, f"Expected {expected_q1_clauses} unit clauses"
    assert len(two_literal_clauses) == 0, "Q1 encoding should contain only unit clauses"

    # Verify valid positions
    # For n=4, m=2: the anchor keeps a 2x2 quarter, i.e. 4 of 16 cells.
    stats = get_symmetry_stats(n)
    print(f"  Valid positions: {stats['valid_positions']}/{stats['total_positions']}")
    print(f"  Reduction: {stats['reduction_percentage']:.1f}% forbidden")
    print(f"  Theoretical speedup: {stats['theoretical_speedup']}x")
    assert stats['anchor_domain_ratio'] == 4, "Anchor domain should shrink by 4x"

    print("\n3. Testing statistics for various grid sizes")
    print("-" * 40)

    for n, _ in test_cases[:5]:
        stats = get_symmetry_stats(n)
        m = stats['boundary_m']
        valid = stats['valid_positions']
        total = stats['total_positions']
        q1_clauses = stats['total_unit_clauses']
        total_clauses = stats['total_clauses']

        print(f"  n={n}, m={m}: valid={valid}/{total} positions, "
              f"clauses={total_clauses} (Q1:{q1_clauses}), "
              f"speedup={stats['theoretical_speedup']}x")

        # Verify formula: the anchor remains in an m x m quarter.
        expected_valid = m * m
        assert valid == expected_valid, f"Valid positions mismatch: {valid} != {expected_valid}"

    print("\n4. Edge case: n=1 (single position)")
    print("-" * 40)

    n = 1
    X_vars = {1: [vpool.id('X_1_1')]}
    Y_vars = {1: [vpool.id('Y_1_1')]}
    clauses = list(encode_symmetry_breaking_constraints(n, X_vars, Y_vars, verbose=True))

    assert len(clauses) == 0, f"Expected 0 clauses for n=1, got {len(clauses)}"
    print("  ✓ No clauses generated (correct)")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED - Q1 ANCHOR SYMMETRY VERIFIED")
    print("=" * 60)


if __name__ == '__main__':
    test_symmetry_breaking()
