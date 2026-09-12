# pyflate — Bottleneck Analysis (Section 6)

Based on a flat, self%-ranked view of the existing profiling data (not the
children-nested tree in [perf_report_pyflate.txt](perf_report_pyflate.txt)):

```
perf report --stdio --no-children -g none -i perf.data
```

## Top Self% Hotspots

| Self% | Symbol                          | Category                |
|------:|----------------------------------|--------------------------|
| 22.40 | `_PyEval_EvalFrameDefault`        | Interpreter overhead     |
|  4.06 | `_PyMem_DebugCheckAddress`        | Memory/allocator         |
|  2.81 | `read_size_t`                     | Memory/allocator         |
|  2.74 | `__memset_avx2_unaligned_erms`    | Memory/allocator         |
|  2.46 | `call_function.lto_priv.0`        | Algorithmic (call overhead) |
|  2.32 | `list_dealloc.lto_priv.0`         | Memory/allocator         |
|  1.97 | `pthread_getspecific@@GLIBC_2.34` | Interpreter overhead     |
|  1.92 | `list_ass_slice`                  | Memory/allocator         |
|  1.78 | `_PyObject_Malloc`                | Memory/allocator         |
|  1.43 | `arena_map_get`                   | Memory/allocator         |
|  1.41 | `lookdict_unicode_nodummy`        | Algorithmic (attr lookup)|
|  1.39 | `PyTuple_GetItem`                 | Algorithmic              |
|  1.32 | `PyGILState_Check`                | Interpreter overhead     |
|  1.29 | `binary_op1`                      | Algorithmic (op dispatch)|
|  1.29 | `_PyMem_DebugRawAlloc`            | Memory/allocator         |

(`_PyMem_Debug*`/`_PyObject_Malloc`/`arena_map_get`/`__memset_avx2*` are all
memory-allocator internals — elevated here specifically because this is a
`python3-dbg` debug build, which adds extra allocation-tracking bookkeeping
on top of pymalloc's normal behavior.)

## Two Concrete Bottlenecks (mapped to Section 3's source-level findings)

### 1. Algorithmic — O(n) linear Huffman symbol search

`HuffmanTable.find_next_symbol()` ([ANALYSIS.md](ANALYSIS.md)) scans the
full symbol table with a Python `for` loop for **every symbol decoded**,
rather than an O(1)/O(log n) tree lookup. This directly explains:

- `call_function` (2.46%) — one Python function call per symbol, doing a
  further loop of comparisons inside.
- `lookdict_unicode_nodummy` (1.41%) — attribute lookups (`x.bits`,
  `x.reverse_symbol`, `x.symbol`) on every `HuffmanLength` object visited
  in the scan.
- `PyTuple_GetItem` (1.39%) and `binary_op1` (1.29%) — the per-bit
  comparisons and index arithmetic inside the scan and the underlying
  `Bitfield`/`RBitfield` bit-reading calls it triggers.

This is the single biggest **algorithmic** inefficiency in the benchmark:
symbol-lookup cost scales with table size × symbols decoded, not just
symbols decoded.

### 2. Memory/allocator — list reallocation churn

`move_to_front()` ([ANALYSIS.md](ANALYSIS.md)) rebuilds a new list via
slicing + concatenation (`l[:] = l[c:c+1] + l[0:c] + l[c+1:]`) on every
call, and the final run-length-decode loop allocates a new one-byte
`bytes` object per output byte (`nearly_there[i:i+1]`). Together these
explain the large allocator-related share of the profile:
`_PyMem_DebugCheckAddress` + `_PyMem_DebugRawAlloc` (5.35%) +
`_PyObject_Malloc` + `arena_map_get` (3.21%) + `list_dealloc` +
`list_ass_slice` (4.24%) + `__memset_avx2_unaligned_erms` (2.74%) —
**over 15% of total runtime** spent allocating, copying, and freeing
short-lived list/bytes objects instead of mutating in place.

## Corroborating Data

- [perf_stat_pyflate.txt](perf_stat_pyflate.txt): **7.0% cache-miss rate**
  and **2.17 insn/cycle** — the highest cache-miss rate of the two chosen
  benchmarks, consistent with a large number of small, individually
  heap-allocated objects (`HuffmanLength` instances, list/bytes fragments)
  scattered across memory rather than a tight, cache-resident working set.
- [perf_report_pyflate_swevents.txt](perf_report_pyflate_swevents.txt):
  ~930K page-fault/minor-fault samples over the run — consistent with (if
  not conclusive proof of) heavy allocation churn from the two bottlenecks
  above.

## Target for Section 7

Both bottlenecks point to the same practical fix: **replace the hand-rolled
bzip2 decoder with the C-accelerated stdlib `bz2` module**, which
eliminates the linear Huffman scan and the list-reallocation churn in one
move. If a closer-to-original comparison is wanted instead, the two
bottlenecks can be fixed independently: a dict/array-based O(1) Huffman
symbol lookup, and an in-place (`collections.deque`-based or index-swap)
move-to-front implementation.

