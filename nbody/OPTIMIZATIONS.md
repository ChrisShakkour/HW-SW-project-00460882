# nbody — Optimization (Section 7)

Three optimizations were implemented, each directly targeting a hotspot
identified in [BOTTLENECKS.md](BOTTLENECKS.md), and each was measured with
a full calibrated `pyperf` run (not a quick spot check) against the
[`original/`](original/) baseline (**483 ms ± 4 ms**). Correctness was
independently verified for all three by comparing `report_energy()`
output against the original.

**Result: none of the three improved on the baseline** — all measured
slower once properly calibrated. This is reported honestly below, with
the root cause for each, because it's a meaningful finding in its own
right (see [Conclusion](#conclusion)).

## Attempt 1 — numpy vectorization

Directory: [`optimized_numpy_experiment/`](optimized_numpy_experiment/)

Replaces the per-body Python lists with numpy arrays and vectorizes the
pairwise force computation across all 10 pairs at once
(`np.subtract.at`/`np.add.at` for the scatter-add velocity update).

| Metric  | Value |
|---------|-------|
| Result  | **848 ms** (75% *slower* than baseline) |
| Correctness | Energy values match the original to 15 decimal places |

**Root cause:** numpy's per-call dispatch and scatter-add overhead
dominates for a system this small (5 bodies, 10 pairs). Vectorization
pays off when the per-call overhead is amortized over many elements —
with only 10 pairs, ~8 numpy calls per timestep × 20,000 timesteps adds
more overhead than the pure-Python scalar loop it replaces.

## Attempt 2 — `sqrt()` instead of `**` for the power operator

Directory: [`optimized_sqrt_experiment/`](optimized_sqrt_experiment/)

Directly targets the `__ieee754_pow_fma` hotspot from the profile:
replaces `r2 ** -1.5` with `1.0 / (r2 * sqrt(r2))` and `r2 ** 0.5` with
`sqrt(r2)`.

| Metric  | Value |
|---------|-------|
| Result  | **495 ms ± 2 ms** (~2.5% *slower* than baseline) |
| Correctness | Bit-for-bit identical energy values |

**Root cause:** the profiled hotspot was real (1.08% self-time on
`__ieee754_pow_fma`) but small, and calling `sqrt()` requires its own
`LOAD_GLOBAL` + `CALL_FUNCTION` bytecode overhead — which costs about as
much as the generic `pow()` dispatch it replaces. A correctly-identified
hotspot doesn't automatically mean the "obvious" fix nets a win; it has to
be measured.

## Attempt 3 — flattened index-based pair iteration

Directory: [`optimized_indexflat_experiment/`](optimized_indexflat_experiment/)

Replaces the original's nested-tuple destructuring
(`(([x1,y1,z1], v1, m1), ([x2,y2,z2], v2, m2)) in pairs`) with integer
indices into flat position/velocity/mass tuples, aiming to reduce
per-iteration unpacking overhead.

| Metric  | Value |
|---------|-------|
| Result  | **579 ms ± 2 ms** (~20% *slower* than baseline) |
| Correctness | Bit-for-bit identical energy values |

**Root cause:** this backfired — it *increased* the number of subscript
operations per pair (`positions[i][0]`, `positions[j][0]`, etc.) compared
to the original, which unpacks `x1, y1, z1, x2, y2, z2` into fast local
variables **once** per pair via a single destructuring assignment. The
original's pattern, despite looking more "unwieldy," was already the more
efficient access pattern.

## Conclusion

All three well-motivated, independently-measured optimization attempts
came in slower than the unmodified reference implementation. This
indicates the benchmark's original code (from the Computer Language
Benchmarks Game) is already close to optimal for what pure CPython can do
at this problem size — the dominant remaining cost, per-scalar-operation
float boxing/deallocation (~9.3% of self-time, see
[BOTTLENECKS.md](BOTTLENECKS.md)), is structural to CPython's object
model for arithmetic. Removing it requires a different execution model
entirely (a JIT like PyPy, a C extension/Cython) — or, as explored in
Section 9, dedicated **hardware** acceleration: a fixed-function
multiply-accumulate/FPU pipeline that performs the pairwise force
calculation without any interpreter-level object overhead at all.

Since `pyflate` alone already far exceeds the assignment's ≥7% target
(~228x speedup), `nbody`'s contribution to this project is instead this
negative result and the resulting, better-motivated case for hardware
acceleration.

