# nbody — Before/After Comparison (Section 8)

Baseline artifacts: [`original/perf.data`](original/perf.data),
[`original/perf_report_original.txt`](original/perf_report_original.txt),
[`original/flamegraph_original.svg`](original/flamegraph_original.svg),
[`original/perf_stat_original.txt`](original/perf_stat_original.txt)
(**untouched** by this comparison).

Optimized artifacts (same pipeline, run against
[`optimized_unroll_experiment/run_benchmark.py`](optimized_unroll_experiment/run_benchmark.py)
— Attempt 4 from [OPTIMIZATIONS.md](OPTIMIZATIONS.md), manual loop
unrolling):
[`optimized_unroll_experiment/perf.data`](optimized_unroll_experiment/perf.data),
[`optimized_unroll_experiment/perf_report_optimized_unroll_experiment.txt`](optimized_unroll_experiment/perf_report_optimized_unroll_experiment.txt),
[`optimized_unroll_experiment/flamegraph_optimized_unroll_experiment.svg`](optimized_unroll_experiment/flamegraph_optimized_unroll_experiment.svg),
[`optimized_unroll_experiment/perf_stat_optimized_unroll_experiment.txt`](optimized_unroll_experiment/perf_stat_optimized_unroll_experiment.txt).

Note: unlike [pyflate's COMPARISON.md](../pyflate/COMPARISON.md), this isn't
comparing against a variant "carried forward" as the project's headline
result — the first three attempts documented in
[OPTIMIZATIONS.md](OPTIMIZATIONS.md) (numpy, sqrt, indexflat) all measured
*slower* than baseline. This compares against Attempt 4 (loop unrolling),
the only one of the four that actually improved on it.

## Timing

| Version             | Mean ± std dev | Speedup |
|----------------------|----------------|---------|
| Original             | 483 ms ± 4 ms  | — |
| Optimized (unroll)    | 439 ms ± 2 ms  | **~1.10x (~9.1% faster)** |

A modest win compared to pyflate's ~244x — consistent with
[OPTIMIZATIONS.md](OPTIMIZATIONS.md#conclusion)'s conclusion that nbody's
original pure-Python implementation was already close to what CPython can
do at this problem size. This is the first of the four attempts to find
any headroom at all.

## Hardware Counters (`perf stat`)

| Metric            | Original         | Optimized       | Change |
|--------------------|------------------|------------------|--------|
| Instructions        | 260,503,357,966  | 243,990,614,558  | **6.8% fewer** |
| Cycles              | 107,660,144,120  | 99,971,763,683   | **7.7% fewer** |
| IPC (insn/cycle)    | 2.42             | 2.44             | ~unchanged |
| Cache-miss rate     | 2.83%            | 3.25%            | ↑ worse (+0.42pp) |
| Branch-miss rate    | 0.49%            | 0.49%            | unchanged |

**Why the cache-miss rate got slightly worse despite being faster:**
unrolling `advance()`'s inner loop from a `for pair in pairs:` construct
into 10 explicit, fully-written-out pairwise blocks makes the compiled
function body substantially larger (more distinct bytecode = larger
instruction footprint), which plausibly adds a little instruction-cache
pressure. This is the mirror image of pyflate's IPC/branch-miss caveat: a
secondary metric moves the "wrong" way while the metric that actually
matters here — total instructions and cycles executed for the same
20,000-iteration workload — still drops by ~7-8%, and wall-clock time (the
only metric that's ground truth) confirms the net win.

## Self%-Ranked Hotspots (`perf report --no-children -g none`)

**Before** (top entries, from [BOTTLENECKS.md](BOTTLENECKS.md)):

```
29.40%  _PyEval_EvalFrameDefault      (interpreter loop)
 3.37%  binary_op1                    (generic binary-op dispatch)
 2.51%  PyFloat_FromDouble            (float boxing)
 2.35%  float_mul                     (float boxing)
 2.01%  float_dealloc                 (float boxing)
 1.65%  list_ass_item                 (position/velocity list writes)
 1.62%  _Py_CheckSlotResult           (debug-build slot check)
 1.44%  validate_list                 (debug-build safety check)
 1.33%  float_add                     (float boxing)
 1.08%  __ieee754_pow_fma             (`** -1.5` / `** 0.5` power calls)
```

**After:**

```
36.21%  _PyEval_EvalFrameDefault      (interpreter loop)
 5.71%  binary_op1                    (generic binary-op dispatch)
 3.84%  PyFloat_FromDouble            (float boxing)
 3.82%  float_mul                     (float boxing)
 3.15%  float_dealloc                 (float boxing)
 2.80%  _Py_CheckSlotResult           (debug-build slot check)
 2.42%  PyNumber_AsSsize_t            (list-index conversion)
 2.21%  list_ass_item                 (position/velocity list writes)
 2.16%  float_add                     (float boxing)
 1.64%  __ieee754_pow_fma             (unchanged — unroll doesn't touch this)
```

Unlike pyflate's optimization — which swapped the entire decode algorithm
for a different, C-accelerated one and made two specific hotspots vanish
completely — loop unrolling doesn't remove any operation from the profile.
Every hotspot from the original is still present in the optimized version,
generally at a *higher* self% share. This is expected: unrolling removes
the per-pair loop-control/tuple-destructuring overhead that sat *around*
these operations (which never showed up as its own named symbol — it was
inlined into `_PyEval_EvalFrameDefault`'s bytecode dispatch), shrinking
total samples from 45K to 42K, while the operations that remain (float
boxing, `pow()` calls, list writes) become a *larger share* of a *smaller*
total. `__ieee754_pow_fma` in particular is essentially unchanged in both
share and character, since unrolling never touched the `** -1.5`/`** 0.5`
power calls that Attempt 2 (the sqrt experiment) targeted — the two
optimizations are orthogonal and, per [OPTIMIZATIONS.md](OPTIMIZATIONS.md),
have not been combined.

## Flame Graphs

Same underlying `perf.data` as above, rendered as flame graphs via the
shared [FlameGraph](https://github.com/brendangregg/FlameGraph) scripts
(see [README.md](README.md) for the exact
`perf script | stackcollapse-perf.pl | flamegraph.pl` pipeline):
[`original/flamegraph_original.svg`](original/flamegraph_original.svg) vs.
[`optimized_unroll_experiment/flamegraph_optimized_unroll_experiment.svg`](optimized_unroll_experiment/flamegraph_optimized_unroll_experiment.svg)
(open either in a browser — they're interactive/zoomable).

Total sample count drops from **45K to 42K** (`perf record -F 999` samples
at a fixed wall-clock frequency), roughly tracking the ~9% timing
improvement.

A caveat on reading these two graphs numerically: a frame's width in a
flame graph is the *inclusive* sample count for that specific box (itself
plus everything drawn above it in the same tower), not the flat self-time
used in the table above — so the percentages visible in the raw SVG
`<title>` data are not directly comparable 1:1 to the self%-ranked table.
They're most useful here for the qualitative shape comparison, not as a
second set of precise numbers.

By that qualitative read, nbody's flame graph looks structurally similar
before and after — unlike pyflate, where the optimized graph's *shape*
changed completely (two new `libbz2` C-function plateaus replacing the
Python call towers entirely). nbody's `advance()` is pure CPython in both
versions, so both graphs are dominated by the same deep, recursive
`_PyEval_EvalFrameDefault` stacks (every nested Python-level call is
another interpreter re-entry), with the same handful of leaf hotspots
(`binary_op1`, `float_mul`/`float_add`/`float_sub`/`float_dealloc`,
`__ieee754_pow_fma`) visible as thin towers underneath in both. The
visible difference is one of scale, not shape: the "after" graph is
slightly shorter/narrower overall (fewer total samples), consistent with
doing less total interpreter work for the same result rather than
replacing *how* the work is done.

## Conclusion

The manual-unrolling optimization delivered a modest but real **~9%**
wall-clock improvement (483 ms → 439 ms), backed by a **~7-8% reduction**
in both total instructions and cycles executed for the same 20,000
timesteps. Unlike pyflate's algorithmic replacement, this didn't eliminate
any hotspot — every function in the original profile is still present in
the optimized one, generally at a *higher* self% share, because unrolling
shrank the *total* amount of work (removing per-pair loop-control and
tuple-destructuring overhead) without removing any individual operation.
This is consistent with [OPTIMIZATIONS.md](OPTIMIZATIONS.md#conclusion)'s
broader finding that nbody's dominant remaining cost — per-scalar float
boxing/deallocation inherent to CPython's object model — is structural:
real headroom exists within pure Python (this attempt found some), but
it's small compared to what a fundamentally different execution model, or
dedicated hardware acceleration (Section 9), could offer.
