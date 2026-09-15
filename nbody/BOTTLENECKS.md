# nbody — Bottleneck Analysis (Section 6)

Based on a flat, self%-ranked view of the existing profiling data (not the
children-nested tree in [original/perf_report_original.txt](original/perf_report_original.txt)):

```
perf report --stdio --no-children -g none -i original/perf.data
```

## Top Self% Hotspots

| Self% | Symbol                       | Category                    |
|------:|-------------------------------|------------------------------|
| 29.40 | `_PyEval_EvalFrameDefault`     | Interpreter overhead         |
|  3.37 | `binary_op1`                   | Algorithmic (op dispatch)    |
|  2.51 | `PyFloat_FromDouble`           | Memory/allocator (boxing)    |
|  2.35 | `float_mul.lto_priv.0`         | Memory/allocator (boxing)    |
|  2.01 | `float_dealloc.lto_priv.0`     | Memory/allocator (boxing)    |
|  1.65 | `list_ass_item.lto_priv.0`     | Memory/allocator (list write)|
|  1.62 | `_Py_CheckSlotResult`          | Interpreter overhead         |
|  1.44 | `validate_list`                | Interpreter overhead (debug) |
|  1.33 | `float_add.lto_priv.0`         | Memory/allocator (boxing)    |
|  1.30 | `PyNumber_AsSsize_t`           | Algorithmic (index conv.)    |
|  1.27 | `_PyMem_DebugCheckAddress`     | Memory/allocator             |
|  1.18 | `__memset_avx2_unaligned_erms` | Memory/allocator             |
|  1.14 | `float_sub.lto_priv.0`         | Memory/allocator (boxing)    |
|  1.08 | `__ieee754_pow_fma`            | **Algorithmic (pow() call)** |
|  1.08 | `list_ass_subscript.lto_priv.0`| Memory/allocator (list write)|

## Two Concrete Bottlenecks (mapped to Section 3's source-level findings)

### 1. Algorithmic — expensive `pow()` call for a fixed exponent

`__ieee754_pow_fma` (1.08% self) is `libm`'s general transcendental power
function, called directly from `advance()`'s
`mag = dt * (dx*dx + dy*dy + dz*dz) ** -1.5` and `report_energy()`'s
`** 0.5` ([ANALYSIS.md](ANALYSIS.md)). CPython has no special-case for
constant float exponents — `**` with a non-integer/non-trivial exponent
always dispatches to the generic `pow()` path, which is far more expensive
than an equivalent `multiply + sqrt`. `binary_op1` (3.37%, the largest
non-eval-loop hotspot) is the generic binary-operator dispatch layer this
and every other `+`/`-`/`*` in the pairwise loop goes through.

### 2. Memory/allocator — per-operation float boxing

Every scalar float arithmetic op in the pairwise loop allocates a **new**
boxed `PyFloat` object and frees the old ones — Python floats are
immutable heap objects, so `dx = x1 - x2` etc. is not a register
operation, it's a heap allocation. This shows up directly as:
`PyFloat_FromDouble` (2.51%) + `float_mul`/`float_add`/`float_sub`
(2.35 + 1.33 + 1.14 = 4.82%) + `float_dealloc` (2.01%) — **~9.3% of total
runtime** spent purely creating and destroying temporary float objects,
on top of `list_ass_item`/`list_ass_subscript`/`validate_list`
(1.65 + 1.08 + 1.44 = 4.17%) from writing results back into the
plain-Python-list position/velocity vectors.

With 20,000 iterations × 10 pairs = 200,000+ pairwise updates per
benchmark loop, each doing ~6 scalar float ops, this boxing/unboxing
overhead is structural — it's what a `numpy`-vectorized implementation
(operating on contiguous C float buffers, no per-op heap allocation) would
eliminate entirely.

## Corroborating Data

- [original/perf_stat_original.txt](original/perf_stat_original.txt): **2.32 insn/cycle** and a low
  **3.3% cache-miss rate** — much better cache behavior than pyflate
  (7.0%), because nbody's working set (10 pairs, 5 bodies) is tiny and
  stays cache-resident. This confirms the bottleneck here is **not**
  memory locality — it's the sheer number of interpreter-level operations
  and object allocations per unit of actual math performed.
- [original/perf_report_original_swevents.txt](original/perf_report_original_swevents.txt): ~86K
  page-fault/minor-fault samples over the run, consistent with the
  constant churn of small float/list objects (lower total than pyflate,
  proportional to nbody's much shorter runtime).

## Target for Section 7

1. Replace `dt * (r2 ** -1.5)` with `dt / (r2 * sqrt(r2))` (and the
   `** 0.5` in `report_energy` with `math.sqrt`) — a small, high-confidence
   fix directly targeting the `__ieee754_pow_fma` hotspot.
2. Vectorize the pairwise force computation with `numpy` — operating on
   position/velocity/mass as numpy arrays instead of per-body Python
   lists eliminates the float-boxing and list-assignment overhead
   (~13.5% of self-time combined) by keeping all arithmetic in contiguous
   C buffers instead of individually heap-allocated Python objects.

