#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <exception>
#include <fstream>
#include <iostream>
#include <limits>
#include <random>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

struct Graph {
    int n = 0;
    std::vector<std::pair<int, int>> edges;
};

struct Evaluation {
    int bandwidth = 0;
    long double score = 0.0L;
};

constexpr double kInitialTemperature = 1000.0;
constexpr double kFinalTemperature = 1.0e-12;
constexpr double kCoolingFactor = 0.99;
constexpr int kInitialMarkovLength = 400;
constexpr int kRotationLimit = 5;

Graph read_matrix_market(const std::string& path) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("cannot open input file: " + path);
    }

    std::string line;
    int rows = 0;
    int cols = 0;
    long long declared_entries = 0;
    bool dimensions_read = false;
    std::set<std::pair<int, int>> edge_set;

    while (std::getline(input, line)) {
        const auto first = line.find_first_not_of(" \t\r\n");
        if (first == std::string::npos || line[first] == '%') {
            continue;
        }

        std::istringstream fields(line);
        if (!dimensions_read) {
            if (!(fields >> rows >> cols >> declared_entries)) {
                throw std::runtime_error("invalid Matrix Market dimensions");
            }
            if (rows < 0 || cols < 0 || declared_entries < 0 || rows != cols) {
                throw std::runtime_error("input must be a square coordinate matrix");
            }
            dimensions_read = true;
            continue;
        }

        int u = 0;
        int v = 0;
        if (!(fields >> u >> v)) {
            throw std::runtime_error("invalid coordinate entry");
        }
        if (u < 1 || v < 1 || u > rows || v > cols) {
            throw std::runtime_error("coordinate is outside the declared matrix");
        }
        if (u != v) {
            --u;
            --v;
            if (u > v) {
                std::swap(u, v);
            }
            edge_set.emplace(u, v);
        }
    }

    if (!dimensions_read) {
        throw std::runtime_error("input does not contain Matrix Market dimensions");
    }

    return {rows, {edge_set.begin(), edge_set.end()}};
}

long long choose_three(long long value) {
    return value < 3 ? 0 : value * (value - 1) * (value - 2) / 6;
}

std::vector<long long> grid_distance_pair_counts(int side) {
    const int max_distance = 2 * (side - 1);
    std::vector<long long> counts(static_cast<std::size_t>(max_distance + 1), 0);
    for (int distance = 1; distance <= max_distance; ++distance) {
        const long long d = distance;
        const long long eta = side;
        if (distance < side) {
            counts[distance] = 2 * d * eta * eta - 2 * d * d * eta
                + 2 * choose_three(d + 1);
        } else {
            counts[distance] = 2 * choose_three(2 * eta - d + 1);
        }
    }
    return counts;
}

Evaluation evaluate(
    const std::vector<int>& permutation,
    const std::vector<int>& inverse,
    const std::vector<std::pair<int, int>>& edges,
    int side,
    const std::vector<long long>& pair_counts) {
    (void)permutation;
    std::vector<long long> edge_counts(pair_counts.size(), 0);
    int bandwidth = 0;

    for (const auto& [u, v] : edges) {
        const int pu = inverse[u];
        const int pv = inverse[v];
        const int distance = std::abs(pu / side - pv / side)
            + std::abs(pu % side - pv % side);
        ++edge_counts[distance];
        bandwidth = std::max(bandwidth, distance);
    }

    // This recurrence is the normalized mixed-radix rank from Eq. (17).
    long double rank = 0.0L;
    for (int distance = 1; distance <= bandwidth; ++distance) {
        rank = (rank + static_cast<long double>(edge_counts[distance]))
            / (1.0L + static_cast<long double>(pair_counts[distance]));
    }
    return {bandwidth, static_cast<long double>(bandwidth) + rank};
}

void rebuild_inverse(const std::vector<int>& permutation, std::vector<int>& inverse) {
    for (int position = 0; position < static_cast<int>(permutation.size()); ++position) {
        const int token = permutation[position];
        if (token >= 0) {
            inverse[token] = position;
        }
    }
}

void swap_positions(
    std::vector<int>& permutation,
    std::vector<int>& inverse,
    int left,
    int right) {
    if (left == right) {
        return;
    }
    std::swap(permutation[left], permutation[right]);
    if (permutation[left] >= 0) {
        inverse[permutation[left]] = left;
    }
    if (permutation[right] >= 0) {
        inverse[permutation[right]] = right;
    }
}

void rotate_left(
    std::vector<int>& permutation,
    std::vector<int>& inverse,
    int first,
    int last) {
    std::rotate(permutation.begin() + first, permutation.begin() + first + 1,
        permutation.begin() + last + 1);
    for (int position = first; position <= last; ++position) {
        if (permutation[position] >= 0) {
            inverse[permutation[position]] = position;
        }
    }
}

void rotate_right(
    std::vector<int>& permutation,
    std::vector<int>& inverse,
    int first,
    int last) {
    std::rotate(permutation.begin() + first, permutation.begin() + last,
        permutation.begin() + last + 1);
    for (int position = first; position <= last; ++position) {
        if (permutation[position] >= 0) {
            inverse[permutation[position]] = position;
        }
    }
}

int markov_length(double temperature, int vertices, std::size_t edges) {
    const long long final_length = std::max<long long>(
        kInitialMarkovLength, 10LL * vertices * static_cast<long long>(edges));
    const double progress = std::clamp(
        std::log(kInitialTemperature / temperature)
            / std::log(kInitialTemperature / kFinalTemperature),
        0.0,
        1.0);
    return static_cast<int>(std::llround(
        kInitialMarkovLength + progress * (final_length - kInitialMarkovLength)));
}

int solve_drsa(const Graph& graph, unsigned long long seed, double time_limit) {
    if (graph.n == 0 || graph.edges.empty()) {
        return 0;
    }

    int side = static_cast<int>(std::ceil(std::sqrt(static_cast<double>(graph.n))));
    while (side * side < graph.n) {
        ++side;
    }
    const int cells = side * side;
    const auto pair_counts = grid_distance_pair_counts(side);

    std::mt19937_64 random(seed);
    std::vector<int> current_permutation(cells, -1);
    for (int vertex = 0; vertex < graph.n; ++vertex) {
        current_permutation[vertex] = vertex;
    }
    std::shuffle(current_permutation.begin(), current_permutation.end(), random);

    std::vector<int> current_inverse(graph.n, -1);
    rebuild_inverse(current_permutation, current_inverse);
    Evaluation current = evaluate(
        current_permutation, current_inverse, graph.edges, side, pair_counts);
    Evaluation best = current;

    const auto started = std::chrono::steady_clock::now();
    const auto expired = [&] {
        return time_limit > 0.0
            && std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count()
                >= time_limit;
    };
    std::uniform_real_distribution<double> unit_interval(0.0, 1.0);
    std::uniform_int_distribution<int> position_distribution(0, cells - 1);
    std::uniform_int_distribution<int> edge_distribution(
        0, static_cast<int>(graph.edges.size()) - 1);
    double temperature = kInitialTemperature;

    while (temperature > kFinalTemperature && !expired()) {
        const int chain_length = markov_length(temperature, graph.n, graph.edges.size());
        for (int iteration = 0; iteration < chain_length && !expired(); ++iteration) {
            const double choice = unit_interval(random);
            bool rotation = false;
            int first = 0;
            int last = 0;

            if (choice < 0.6) {
                first = position_distribution(random);
                do {
                    last = position_distribution(random);
                } while (last == first);
                swap_positions(current_permutation, current_inverse, first, last);
            } else if (choice < 0.8) {
                const auto& [u, v] = graph.edges[edge_distribution(random)];
                first = current_inverse[u];
                last = current_inverse[v];
                swap_positions(current_permutation, current_inverse, first, last);
            } else {
                first = position_distribution(random);
                const int maximum_span = std::min(kRotationLimit, cells - 1);
                std::uniform_int_distribution<int> span_distribution(1, maximum_span);
                const int span = span_distribution(random);
                last = first + span;
                if (last >= cells) {
                    last = cells - 1;
                    first = last - span;
                }
                rotate_left(current_permutation, current_inverse, first, last);
                rotation = true;
            }

            const Evaluation candidate = evaluate(
                current_permutation, current_inverse, graph.edges, side, pair_counts);
            const long double increase = candidate.score - current.score;
            const bool accept = increase <= 0.0L
                || unit_interval(random) < std::exp(-static_cast<double>(increase) / temperature);

            if (accept) {
                current = candidate;
                if (candidate.score < best.score) {
                    best = candidate;
                }
            } else if (rotation) {
                rotate_right(current_permutation, current_inverse, first, last);
            } else {
                swap_positions(current_permutation, current_inverse, first, last);
            }
        }
        temperature *= kCoolingFactor;
    }
    return best.bandwidth;
}

unsigned long long parse_seed(const char* value) {
    std::size_t parsed = 0;
    const auto seed = std::stoull(value, &parsed);
    if (value[parsed] != '\0') {
        throw std::runtime_error("seed must be an integer");
    }
    return seed;
}

double parse_time_limit(const char* value) {
    std::size_t parsed = 0;
    const double limit = std::stod(value, &parsed);
    if (value[parsed] != '\0' || !std::isfinite(limit) || limit < 0.0) {
        throw std::runtime_error("time_limit must be a finite non-negative number");
    }
    return limit;
}

}  // namespace

int main(int argc, char* argv[]) {
    if (argc != 4) {
        std::cerr << "Usage: " << argv[0] << " <matrix.mtx> <seed> <time_limit_s>\n";
        return 2;
    }

    try {
        const Graph graph = read_matrix_market(argv[1]);
        const int bandwidth = solve_drsa(graph, parse_seed(argv[2]), parse_time_limit(argv[3]));
        std::cout << "RESULT " << bandwidth << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "DRSA error: " << error.what() << '\n';
        return 1;
    }
}
