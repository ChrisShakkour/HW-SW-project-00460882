# pyflate — Profiling Artifacts

This directory holds the baseline profiling data for the `pyflate` benchmark
(a pure-Python DEFLATE/gzip decompressor from `pyperformance`). The
artifacts themselves live under [`original/`](original/) — this file
documents how they were produced. The bz2-swap optimization has its own
sibling directory ([`optimized/`](optimized/)) with the same set of
artifacts — see [COMPARISON.md](COMPARISON.md) for the before/after.

Baseline result: `pyflate: Mean +- std dev: 3.17 sec +- 0.02 sec`

## Files (under `original/`)

- `original/perf.data` — raw perf sample data from the call-graph (`cpu-clock`) run.
- `original/perf_report_original.txt` — call-graph text report generated from `perf.data`.
- `original/flamegraph_original.svg` — interactive flame graph generated from the same
  run (open in a browser).
- `original/perf_stat_original.txt` — aggregate hardware performance-counter stats
  (cycles, instructions, cache, branches) for the whole benchmark run.
- `original/perf_report_original_swevents.txt` — call-graph report sampled on software
  events (page faults, minor faults, context switches, CPU migrations),
  generated from `perf_swevents.data` (not committed — see below).
- `venv/` — pyperformance's per-benchmark virtual environment (git-ignored).

Note: `perf_swevents.data` itself is **not** committed (it's well over
GitHub's 100 MB per-file limit) — only the derived
`perf_report_original_swevents.txt` is kept. Regenerate the raw data
locally with the command below if needed.

## Commands Used

Run from inside [`original/`](original/) (`HW-SW-project-00460882/pyflate/original`),
directly against the local `run_benchmark.py` (not the `pyperformance`
CLI wrapper — this is the same script `pyperformance run --bench pyflate`
would otherwise invoke internally, just run standalone so it can be
profiled/modified per variant).

### 1. Call-graph recording with perf

```
perf record -F 999 -g -e cpu-clock -- python3-dbg run_benchmark.py
perf report --stdio > perf_report_original.txt
```

`-e cpu-clock` is required instead of the default hardware `cycles` event —
see the top-level [README.md](../../README.md#4-profile-with-perf) for why
(this VM's virtual PMU doesn't deliver the performance-monitoring interrupt
that hardware-event sampling relies on, so `cycles` silently records zero
samples here).

### 2. Flame graph

Uses the shared [FlameGraph](https://github.com/brendangregg/FlameGraph)
scripts cloned once into `../../tools/FlameGraph`:

```
perf script -i perf.data > out.perf
../../tools/FlameGraph/stackcollapse-perf.pl out.perf > out.folded
../../tools/FlameGraph/flamegraph.pl out.folded > flamegraph_original.svg
```

### 3. Hardware counter stats (`perf stat`)

Aggregate counts (not sampled) for the whole run — cycles, instructions
(→ IPC), cache references/misses, branch instructions/misses, bus cycles,
reference cycles. Unlike `perf record`, `perf stat` reads counters directly
and works reliably in this VM even for hardware events:

```
perf stat -e cycles,instructions,cache-references,cache-misses,branch-instructions,branch-misses,bus-cycles,ref-cycles \
  -- python3-dbg run_benchmark.py > /dev/null 2> perf_stat_original.txt
```

Result summary: **2.17 insn/cycle**, **7.0% cache-miss rate**, **0.43%
branch-miss rate** — the highest cache-miss rate of the two benchmarks,
consistent with pyflate's irregular, branch-heavy bit-level Huffman/LZ77
decode logic.

### 4. Software-event sampling

Samples on discrete software-tracked events instead of a hardware/timer
event — useful for seeing *where* faults, context switches, or migrations
happen, since these aren't tied to hardware PMI support:

```
perf record -c 1 -g -e page-faults,minor-faults,context-switches,cpu-migrations \
  -o perf_swevents.data -- python3-dbg run_benchmark.py
perf report --stdio -i perf_swevents.data > perf_report_original_swevents.txt
```

`-c 1` samples every occurrence (these events are rare/discrete, unlike
frequency-based cycle/clock sampling). Result: ~863K page-fault/minor-fault
samples, 751 context-switch samples, zero cpu-migrations.

## Reproduce End-to-End

```
cd HW-SW-project-00460882/pyflate/original

# call graph + flame graph
perf record -F 999 -g -e cpu-clock -- python3-dbg run_benchmark.py
perf report --stdio > perf_report_original.txt
perf script -i perf.data > out.perf
../../tools/FlameGraph/stackcollapse-perf.pl out.perf > out.folded
../../tools/FlameGraph/flamegraph.pl out.folded > flamegraph_original.svg

# hardware counter stats
perf stat -e cycles,instructions,cache-references,cache-misses,branch-instructions,branch-misses,bus-cycles,ref-cycles \
  -- python3-dbg run_benchmark.py > /dev/null 2> perf_stat_original.txt

# software-event sampling
perf record -c 1 -g -e page-faults,minor-faults,context-switches,cpu-migrations \
  -o perf_swevents.data -- python3-dbg run_benchmark.py
perf report --stdio -i perf_swevents.data > perf_report_original_swevents.txt
```
