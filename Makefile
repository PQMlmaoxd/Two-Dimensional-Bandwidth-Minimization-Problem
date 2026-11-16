.PHONY: drsa test verify clean

DRSA_BIN := build/drsa
PYTHON ?= python3

drsa: $(DRSA_BIN)

$(DRSA_BIN): drsa/drsa.cpp

	mkdir -p build

	g++ -O3 -std=c++17 -Wall -Wextra $< -o $@

test: drsa

	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s python/tests -p 'test_*.py'

verify:

	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m python.utils.verification_artifacts verify --root results/logs/SAT

clean:

	rm -rf build
