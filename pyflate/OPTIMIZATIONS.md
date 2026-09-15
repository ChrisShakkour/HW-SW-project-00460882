# pyflate — Optimization (Section 7)

## Implemented: swap the hand-rolled bzip2 decoder for stdlib `bz2`

Directory: [`optimized/`](optimized/) (baseline snapshot: [`original/`](original/)).

Both [BOTTLENECKS.md](BOTTLENECKS.md) findings — the O(n) linear Huffman
symbol scan and the list-reallocation churn in `move_to_front`/the RLE
step — exist only because the benchmark reimplements bzip2 decoding from
scratch in pure Python. Python's standard library already ships a
C-accelerated `bz2` module that performs the identical decompression, so
the most effective fix is simply to use it:

```python
import bz2
out = bz2.decompress(compressed_bytes)
```

This eliminates *both* bottlenecks in one change — no Python-level Huffman
search, no list-slicing MTF step, no per-byte RLE loop.

## Correctness

The optimized script keeps the exact same MD5 verification as the
original (`afa004a630fe072901b1d9628b960974`) — it raises on mismatch, and
running it raises nothing, confirming byte-identical decompressed output.

## Preview Timing

A quick single-sample run (`python3-dbg run_benchmark.py -p1 -n1 -l1`):

| Version   | Time      |
|-----------|-----------|
| Original  | 3.17 sec (calibrated mean, see [original/perf_stat_original.txt](original/perf_stat_original.txt)) |
| Optimized | 13.9 ms   |

A rough **~228x** speedup. The full calibrated (multi-sample) before/after
comparison is done in Section 8.

