# nbody — Profiling Artifacts

This directory holds the baseline profiling data for the `nbody` benchmark
(a gravitational N-body simulation from `pyperformance`).

Baseline result: `nbody: Mean +- std dev: 483 ms +- 4 ms`

## Files

- `perf.data` — raw perf sample data from the recorded run.
- `report_nbody.txt` — text report generated from `perf.data`.
- `flamegraph_nbody.svg` — interactive flame graph generated from the same
  run (open in a browser).
- `venv/` — pyperformance's per-benchmark virtual environment (git-ignored).

## Commands Used

Run from inside this directory (`HW-SW-project-00460882/nbody`).

### 1. Record with perf

```
perf record -F 999 -g -e cpu-clock -- python3-dbg -m pyperformance run --bench nbody
```

`-e cpu-clock` is required instead of the default hardware `cycles` event —
see the top-level [README.md](../README.md#4-profile-with-perf) for why
(this VM's virtual PMU doesn't deliver the performance-monitoring interrupt
that hardware-event sampling relies on, so `cycles` silently records zero
samples here).

### 2. Export the text report

```
perf report --stdio > report_nbody.txt
```

### 3. Generate the flame graph

Uses the shared [FlameGraph](https://github.com/brendangregg/FlameGraph)
scripts cloned once into `../tools/FlameGraph`:

```
perf script -i perf.data > out.perf
../tools/FlameGraph/stackcollapse-perf.pl out.perf > out.folded
../tools/FlameGraph/flamegraph.pl out.folded > flamegraph_nbody.svg
```

## Reproduce End-to-End

```
cd HW-SW-project-00460882/nbody
perf record -F 999 -g -e cpu-clock -- python3-dbg -m pyperformance run --bench nbody
perf report --stdio > report_nbody.txt
perf script -i perf.data > out.perf
../tools/FlameGraph/stackcollapse-perf.pl out.perf > out.folded
../tools/FlameGraph/flamegraph.pl out.folded > flamegraph_nbody.svg
```

