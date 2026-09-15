# nbody — Benchmark Analysis (Section 3)

Source: `pyperformance`'s `bm_nbody/run_benchmark.py` (Computer Language
Benchmarks Game N-body simulation, ported by Kevin Carson et al.).

## Purpose

Simulates the orbits of the Sun and four outer planets (Jupiter, Saturn,
Uranus, Neptune) under mutual gravity, using leapfrog-style numerical
integration. The benchmark runs many timesteps and computes total system
energy before/after to (indirectly) validate the integration.

## Libraries

- Only `pyperf` (the benchmarking harness itself).
- **No numpy, no third-party numeric library** — every vector/float
  operation is plain Python arithmetic on plain Python lists. This is
  deliberate (it's a "pure Python vs. everything else" reference
  benchmark), and it's exactly why it's so much slower than an equivalent
  numpy/C implementation.

## Data Structures

- `BODIES`: dict mapping body name → `(position [x, y, z], velocity
  [vx, vy, vz], mass)`, where position/velocity are plain 3-element Python
  lists (mutated in place) and mass is a float.
- `SYSTEM`: the 5 body tuples as a list.
- `PAIRS`: **all 10 unique unordered pairs** of the 5 bodies, precomputed
  once at import time via a hand-rolled `combinations()` (reimplementing
  `itertools.combinations` for old-Python compatibility).

## Algorithmic Shape (hot path)

- `advance(dt, n)` — called once per benchmark loop with `n = 20000`
  iterations by default. Each iteration:
  1. Loops over the 10 precomputed pairs, computing the pairwise
     gravitational force (`dx, dy, dz`, then
     `mag = dt * (dx*dx + dy*dy + dz*dz) ** -1.5`) and updating **both**
     bodies' velocities in place. Note: this already exploits Newton's
     third law — each pair is visited once, not twice — so "add symmetry"
     is not an available further optimization here.
  2. Loops over the 5 bodies updating position from velocity.
- `report_energy()` — a similar double loop (pairs for potential energy,
  bodies for kinetic energy), called twice per benchmark loop (before and
  after `advance()`).
- Every arithmetic op (`+`, `-`, `*`, `**`) is a full CPython bytecode
  dispatch + `PyFloat` operation — with 20000 iterations × 10 pairs, that's
  200,000 pairwise updates per benchmark loop, each doing several scalar
  ops, none of them vectorized.

## Relevant to Later Optimization (Section 7)

- The `** -1.5` (and `** 0.5` in `report_energy`) power operator is
  noticeably more expensive in CPython than an equivalent
  multiply + `math.sqrt` combination — e.g. `mag = dt / (r2 * sqrt(r2))`
  instead of `dt * (r2 ** -1.5)`. This is a cheap, high-confidence win since
  it's called on every one of the 200,000+ pairwise updates.
- Beyond that, the natural optimization is vectorizing the pairwise loop
  with numpy (batching the 10 pairs' arithmetic into array ops instead of
  200,000 individual scalar Python operations) — though with only 5
  bodies/10 pairs, numpy's per-call dispatch overhead may cut into the
  win; worth measuring both.
- This matches the profiling data in [original/perf_stat_original.txt](original/perf_stat_original.txt)
  (2.32 insn/cycle, low 3.3% cache-miss rate — the code is compute-bound on
  interpreter overhead, not memory-bound) and the flame graph's dominant
  `_PyEval_EvalFrameDefault`/`binary_op1` frames.

