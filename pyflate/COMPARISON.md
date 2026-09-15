# pyflate — Before/After Comparison (Section 8)

Baseline artifacts: [`original/perf.data`](original/perf.data),
[`original/perf_report_original.txt`](original/perf_report_original.txt),
[`original/flamegraph_original.svg`](original/flamegraph_original.svg),
[`original/perf_stat_original.txt`](original/perf_stat_original.txt)
(**untouched** by this comparison).

Optimized artifacts (same pipeline, run against
[`optimized/run_benchmark.py`](optimized/run_benchmark.py)):
[`optimized/perf.data`](optimized/perf.data),
[`optimized/perf_report_pyflate_optimized.txt`](optimized/perf_report_pyflate_optimized.txt),
[`optimized/flamegraph_pyflate_optimized.svg`](optimized/flamegraph_pyflate_optimized.svg),
[`optimized/perf_stat_pyflate_optimized.txt`](optimized/perf_stat_pyflate_optimized.txt).

## Timing

| Version   | Mean ± std dev | Speedup |
|-----------|----------------|---------|
| Original  | 3.17 sec ± 0.02 sec | — |
| Optimized | 13.0 ms ± 0.3 ms    | **~244x** |

## Hardware Counters (`perf stat`)

| Metric              | Original         | Optimized       | Change |
|----------------------|------------------|------------------|--------|
| Instructions          | 1,433,334,987,236 | 41,673,773,016 | **34.4x fewer** |
| Cycles                | 659,081,747,854   | 33,018,380,671 | **20.0x fewer** |
| IPC (insn/cycle)       | 2.17             | 1.26            | ↓ (counterintuitive — see below) |
| Cache-miss rate        | 7.0%             | 1.72%           | **4.1x better** |
| Branch-miss rate       | 0.43%            | 3.19%           | 7.4x worse |

**Why IPC dropped and branch-misses rose despite being far faster overall:**
the optimized version spends 58.7% of its (much shorter) run inside
`libbz2`'s compiled C decompression engine (`BZ2_decompress` +
`BZ2_bzDecompress`, see below) doing tight bit-manipulation — Huffman
decode, BWT, RLE — which is inherently less branch-predictable per cycle
than CPython's repetitive bytecode-dispatch loop (most iterations of
`_PyEval_EvalFrameDefault` look alike to the branch predictor, giving
misleadingly "good" IPC/branch stats for work that's still fundamentally
slow). The optimized version needs **34x fewer total instructions** to do
the same job, which is what actually matters — IPC and branch-miss rate
are secondary once the total instruction count differs by over an order
of magnitude.

## Self%-Ranked Hotspots (`perf report --no-children -g none`)

**Before** (top entries, from [BOTTLENECKS.md](BOTTLENECKS.md)):

```
22.40%  _PyEval_EvalFrameDefault      (interpreter loop)
 4.06%  _PyMem_DebugCheckAddress      (allocator)
 2.81%  read_size_t                   (allocator)
 2.74%  __memset_avx2_unaligned_erms  (allocator)
 2.46%  call_function                 (Huffman-scan call overhead)
 2.32%  list_dealloc                  (MTF list churn)
 1.92%  list_ass_slice                (MTF list churn)
 1.41%  lookdict_unicode_nodummy      (Huffman-scan attr lookups)
```

**After:**

```
37.81%  BZ2_decompress       (libbz2.so — the actual decompression engine)
20.87%  BZ2_bzDecompress     (libbz2.so — stream/buffer management)
 3.15%  _PyEval_EvalFrameDefault  (residual: just the wrapper loop)
 2.66%  __memset_avx2_unaligned_erms
 1.88%  validate_list         (debug-build safety check)
 1.39%  lookdict_unicode_nodummy
 0.80%  _PyUnicode_CheckConsistency (debug-build safety check)
 0.78%  _PyMem_DebugCheckAddress
```

Both bottlenecks identified in [BOTTLENECKS.md](BOTTLENECKS.md) — the O(n)
linear Huffman symbol scan (`find_next_symbol`, via `call_function`/
`lookdict_unicode_nodummy` in the "before" list) and the list-reallocation
churn from `move_to_front`/the RLE step (`list_dealloc`/`list_ass_slice`)
— are **entirely gone** from the "after" profile. What remains is almost
all inside `libbz2`'s compiled C code, plus a small, mostly-fixed amount
of CPython debug-build bookkeeping (`validate_list`,
`_PyUnicode_CheckConsistency`, etc.) that doesn't scale down with the
actual work and so becomes proportionally more visible now that there's
so little total work left.

## Conclusion

The optimization directly targeted and eliminated both bottlenecks
identified in Section 6, confirmed not just by the ~244x timing
improvement but by the profile itself: the hand-rolled decode machinery
(Huffman scan, MTF, RLE) no longer appears at all, replaced by
`libbz2`'s compiled implementation. This comfortably exceeds the
assignment's ≥7% improvement target.

