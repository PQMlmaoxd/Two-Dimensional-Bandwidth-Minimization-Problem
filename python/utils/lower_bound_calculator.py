# lower_bound_calculator.py
# Calculate Lower Bound (LB) for 2D Bandwidth Minimization Problem
#
# Formula: LB = ⌈δ(n) / D(G)⌉
# Where:
#   - δ(n) = theoretical upper bound = min{2⌈(√(2n-1)-1)/2⌉, 2⌈√(n/2)⌉-1}
#   - D(G) = diameter of graph G (longest shortest path between any pair of vertices)

import math
from collections import deque
from typing import List, Tuple, Dict, Set, Optional

from python.utils.mtx_parser import parse_mtx_file as parse_canonical_mtx_file


def calculate_delta_n(n: int) -> int:
    """
    Calculate theoretical upper bound δ(n) using formula:
    δ(n) = min{2⌈(√(2n-1)-1)/2⌉, 2⌈√(n/2)⌉-1}

    This provides a tight upper bound for 2D bandwidth minimization
    based on theoretical analysis of grid placements.

    Args:
        n: Number of vertices

    Returns:
        δ(n) value
    """
    if n <= 0:
        return 0
    if n == 1:
        return 0
    if n == 2:
        return 1

    # First term: 2⌈(√(2n-1)-1)/2⌉
    term1 = 2 * math.ceil((math.sqrt(2*n - 1) - 1) / 2)

    # Second term: 2⌈√(n/2)⌉-1
    term2 = 2 * math.ceil(math.sqrt(n / 2)) - 1

    # Return minimum of both terms
    delta = min(term1, term2)

    return delta


def degree_lower_bound(max_degree: int) -> int:
    """L1-2DBMP degree lower bound.

    Returns the smallest integer beta with 2*beta*(beta + 1) >= max_degree.
    """
    if max_degree <= 0:
        return 0
    return math.ceil((math.sqrt(1 + 2 * max_degree) - 1) / 2)


def build_adjacency_list(n: int, edges: List[Tuple[int, int]]) -> Dict[int, Set[int]]:
    """
    Build adjacency list from edge list.

    Args:
        n: Number of vertices (1-indexed: vertices are 1 to n)
        edges: List of (u, v) edges

    Returns:
        Adjacency list as dict: vertex -> set of neighbors
    """
    adj = {i: set() for i in range(1, n + 1)}

    for u, v in edges:
        adj[u].add(v)
        adj[v].add(u)

    return adj


def find_connected_components(adj: Dict[int, Set[int]], n: int) -> List[List[int]]:
    """
    Find all connected components in a graph using BFS.

    Args:
        adj: Adjacency list
        n: Number of vertices (1-indexed)

    Returns:
        List of components, each component is a list of vertex IDs.
        Components are sorted by size (largest first).
    """
    visited = set()
    components = []

    for start in range(1, n + 1):
        if start not in visited:
            # BFS to find this component
            component = []
            queue = deque([start])
            visited.add(start)

            while queue:
                v = queue.popleft()
                component.append(v)
                for neighbor in adj[v]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)

            components.append(component)

    # Sort by size (largest first)
    components.sort(key=len, reverse=True)
    return components


def get_subgraph_edges(edges: List[Tuple[int, int]], vertices: Set[int]) -> List[Tuple[int, int]]:
    """
    Get edges that belong to a subgraph defined by a set of vertices.

    Args:
        edges: Original edge list
        vertices: Set of vertices in the subgraph

    Returns:
        List of edges where both endpoints are in the vertex set
    """
    return [(u, v) for u, v in edges if u in vertices and v in vertices]


def remap_vertices(vertices: List[int], edges: List[Tuple[int, int]]) -> Tuple[int, List[Tuple[int, int]], Dict[int, int]]:
    """
    Remap vertices to consecutive 1-indexed numbering.

    Args:
        vertices: List of original vertex IDs
        edges: Edges with original vertex IDs

    Returns:
        Tuple of (new_n, remapped_edges, mapping_dict)
    """
    # Create mapping: old_id -> new_id (1-indexed)
    sorted_vertices = sorted(vertices)
    old_to_new = {old: new for new, old in enumerate(sorted_vertices, start=1)}

    # Remap edges
    remapped_edges = [(old_to_new[u], old_to_new[v]) for u, v in edges]

    return len(vertices), remapped_edges, old_to_new


def bfs_shortest_path(adj: Dict[int, Set[int]], start: int, n: int) -> Dict[int, int]:
    """
    BFS to find shortest path from start vertex to all other vertices.

    Args:
        adj: Adjacency list
        start: Starting vertex
        n: Number of vertices

    Returns:
        Dictionary of distances: vertex -> distance from start
        Returns -1 for unreachable vertices
    """
    distances = {i: -1 for i in range(1, n + 1)}
    distances[start] = 0

    queue = deque([start])

    while queue:
        current = queue.popleft()
        for neighbor in adj[current]:
            if distances[neighbor] == -1:
                distances[neighbor] = distances[current] + 1
                queue.append(neighbor)

    return distances


def calculate_diameter(n: int, edges: List[Tuple[int, int]], verbose: bool = False) -> int:
    """
    Calculate diameter D(G) of a graph.

    Diameter is the longest shortest path between any pair of vertices.
    Uses BFS from each vertex to find all-pairs shortest paths.

    Args:
        n: Number of vertices
        edges: List of (u, v) edges
        verbose: Print debug info

    Returns:
        Diameter D(G), or -1 if graph is disconnected
    """
    if n <= 1:
        return 0

    if not edges:
        return -1  # Disconnected (no edges)

    # Build adjacency list
    adj = build_adjacency_list(n, edges)

    diameter = 0

    # BFS from each vertex
    for start in range(1, n + 1):
        distances = bfs_shortest_path(adj, start, n)

        for end in range(start + 1, n + 1):
            dist = distances[end]

            if dist == -1:
                # Graph is disconnected
                if verbose:
                    print(f"Graph is disconnected: no path from {start} to {end}")
                return -1

            if dist > diameter:
                diameter = dist
                if verbose:
                    print(f"New diameter found: d({start}, {end}) = {dist}")

    return diameter


def calculate_eccentricity(adj: Dict[int, Set[int]], vertex: int, n: int) -> int:
    """
    Calculate eccentricity of a vertex.

    Eccentricity is the maximum distance from this vertex to any other vertex.

    Args:
        adj: Adjacency list
        vertex: The vertex to calculate eccentricity for
        n: Number of vertices

    Returns:
        Eccentricity, or -1 if graph is disconnected
    """
    distances = bfs_shortest_path(adj, vertex, n)

    max_dist = 0
    for v in range(1, n + 1):
        if v != vertex:
            if distances[v] == -1:
                return -1  # Disconnected
            max_dist = max(max_dist, distances[v])

    return max_dist


def calculate_radius(n: int, edges: List[Tuple[int, int]], verbose: bool = False) -> int:
    """
    Calculate radius of a graph.

    Radius is the minimum eccentricity among all vertices.

    Args:
        n: Number of vertices
        edges: List of (u, v) edges
        verbose: Print debug info

    Returns:
        Radius, or -1 if graph is disconnected
    """
    if n <= 1:
        return 0

    adj = build_adjacency_list(n, edges)

    min_eccentricity = float('inf')

    for v in range(1, n + 1):
        ecc = calculate_eccentricity(adj, v, n)
        if ecc == -1:
            return -1  # Disconnected

        if ecc < min_eccentricity:
            min_eccentricity = ecc
            if verbose:
                print(f"Vertex {v} has eccentricity {ecc} (current min)")

    return min_eccentricity


def calculate_lower_bound(n: int, edges: List[Tuple[int, int]], verbose: bool = False) -> Tuple[int, Dict]:
    """
    Calculate Lower Bound (LB) for 2D Bandwidth Minimization.

    Formula: LB = ⌈δ(n) / D(G)⌉

    Where:
        - δ(n) = min{2⌈(√(2n-1)-1)/2⌉, 2⌈√(n/2)⌉-1}
        - D(G) = diameter of graph G

    Args:
        n: Number of vertices
        edges: List of (u, v) edges
        verbose: Print calculation details

    Returns:
        Tuple of (LB, info_dict) where info_dict contains intermediate values
    """
    info = {
        'n': n,
        'num_edges': len(edges),
        'delta_n': 0,
        'diameter': 0,
        'radius': 0,
        'lower_bound': 0,
        'is_connected': True,
        'error': None
    }

    # Step 1: Calculate δ(n)
    delta = calculate_delta_n(n)
    info['delta_n'] = delta

    if verbose:
        print(f"δ({n}) = {delta}")

    # Step 2: Calculate diameter D(G)
    diameter = calculate_diameter(n, edges, verbose=verbose)
    info['diameter'] = diameter

    if diameter == -1:
        info['is_connected'] = False
        info['error'] = 'Graph is disconnected'
        info['lower_bound'] = -1
        if verbose:
            print("ERROR: Graph is disconnected, cannot calculate LB")
        return -1, info

    if verbose:
        print(f"D(G) = {diameter}")

    # Step 3: Calculate radius (optional, for information)
    radius = calculate_radius(n, edges, verbose=False)
    info['radius'] = radius

    # Step 4: Calculate LB = ⌈δ(n) / D(G)⌉
    if diameter == 0:
        # All vertices are the same (single vertex or self-loops only)
        lb = delta
    else:
        lb = math.ceil(delta / diameter)

    info['lower_bound'] = lb

    if verbose:
        print(f"LB = ⌈δ({n}) / D(G)⌉ = ⌈{delta} / {diameter}⌉ = {lb}")

    return lb, info


def parse_mtx_file(filepath: str) -> Tuple[int, List[Tuple[int, int]]]:
    """Parse through the shared rows-as-vertices Matrix Market policy."""

    return parse_canonical_mtx_file(filepath)


def analyze_graph(n: int, edges: List[Tuple[int, int]]) -> Dict:
    """
    Analyze graph properties useful for bandwidth minimization.

    For disconnected graphs, calculates LB for the Largest Connected Component (LCC).

    Args:
        n: Number of vertices
        edges: List of (u, v) edges

    Returns:
        Dictionary with graph properties including:
        - num_components: Number of connected components
        - lcc_size: Size of largest connected component
        - For disconnected graphs: LB is computed on LCC
    """
    adj = build_adjacency_list(n, edges)

    # Calculate degree statistics
    degrees = [len(adj[v]) for v in range(1, n + 1)]
    min_degree = min(degrees) if degrees else 0
    max_degree = max(degrees) if degrees else 0
    avg_degree = sum(degrees) / len(degrees) if degrees else 0

    # Find connected components
    components = find_connected_components(adj, n)
    num_components = len(components)
    is_connected = (num_components == 1)

    # Get largest connected component
    lcc = components[0] if components else []
    lcc_size = len(lcc)

    # For disconnected graphs, analyze the LCC
    if is_connected:
        # Graph is connected - use original graph
        diameter = calculate_diameter(n, edges)
        radius = calculate_radius(n, edges)
        delta = calculate_delta_n(n)
        effective_n = n
    else:
        # Graph is disconnected - use LCC for LB calculation
        lcc_set = set(lcc)
        lcc_edges = get_subgraph_edges(edges, lcc_set)

        # Remap to consecutive vertex numbering for correct diameter calculation
        effective_n, remapped_edges, _ = remap_vertices(lcc, lcc_edges)

        diameter = calculate_diameter(effective_n, remapped_edges)
        radius = calculate_radius(effective_n, remapped_edges)
        delta = calculate_delta_n(effective_n)

    # Calculate LB = ⌈δ(n) / D(G)⌉
    if diameter > 0:
        lb = math.ceil(delta / diameter)
    elif diameter == 0:
        lb = delta
    else:
        lb = -1  # Should not happen for LCC

    # Calculate theoretical UB (based on effective n for LCC)
    ub = delta

    return {
        'n': n,
        'num_edges': len(edges),
        'min_degree': min_degree,
        'max_degree': max_degree,
        'avg_degree': round(avg_degree, 2),
        'diameter': diameter,
        'radius': radius,
        'delta_n': delta,
        'lower_bound': lb,
        'upper_bound': ub,
        'is_connected': is_connected,
        'num_components': num_components,
        'lcc_size': lcc_size,
        'effective_n': effective_n  # n used for LB calculation (= lcc_size if disconnected)
    }


def print_graph_analysis(analysis: Dict) -> None:
    """Print formatted graph analysis."""
    print(f"\n{'='*60}")
    print(f"GRAPH ANALYSIS")
    print(f"{'='*60}")
    print(f"Vertices (n): {analysis['n']}")
    print(f"Edges (|E|): {analysis['num_edges']}")
    print(f"Min degree: {analysis['min_degree']}")
    print(f"Max degree: {analysis['max_degree']}")
    print(f"Avg degree: {analysis['avg_degree']}")
    print(f"Connected: {'Yes' if analysis['is_connected'] else 'No'}")

    # Show component info for disconnected graphs
    if not analysis['is_connected']:
        print(f"Number of components: {analysis.get('num_components', '?')}")
        print(f"Largest component size: {analysis.get('lcc_size', '?')}")
        print(f"(LB computed on largest connected component)")

    print(f"\n{'='*40}")
    print(f"BANDWIDTH BOUNDS")
    print(f"{'='*40}")
    print(f"Diameter D(G): {analysis['diameter']}")
    print(f"Radius: {analysis['radius']}")
    print(f"δ(n): {analysis['delta_n']} (n={analysis.get('effective_n', analysis['n'])})")
    print(f"Lower Bound (LB): {analysis['lower_bound']}")
    print(f"Upper Bound (UB): {analysis['upper_bound']}")
    print(f"{'='*60}")


# MAIN - Command Line Interface

def main():
    """Main function for command-line usage."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python lower_bound_calculator.py <mtx_file>")
        print("\nCalculates Lower Bound (LB) for 2D Bandwidth Minimization")
        print("Formula: LB = ⌈δ(n) / D(G)⌉")
        print("  where δ(n) = min{2⌈(√(2n-1)-1)/2⌉, 2⌈√(n/2)⌉-1}")
        print("  and D(G) = diameter of the graph")
        return

    mtx_file = sys.argv[1]

    print(f"Parsing {mtx_file}...")
    n, edges = parse_mtx_file(mtx_file)
    print(f"Graph: {n} vertices, {len(edges)} edges")

    # Full analysis
    analysis = analyze_graph(n, edges)
    print_graph_analysis(analysis)


if __name__ == '__main__':
    main()
