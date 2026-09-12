# HW-SW-project-00460882

HW/SW co-design project: profiling, optimizing, and proposing hardware
acceleration for benchmarks from the `pyperformance` suite.

## Environment

- Benchmarks run inside a QEMU/KVM Ubuntu 22.04 (jammy) VM.
- Profiling uses `perf` with Python debug symbols (`python3-dbg`) so that
  internal CPython function calls are visible in the report.
- `pyperformance` is used to run the benchmarks themselves.

## Running a Benchmark and Generating a perf Report

### Step 1 — Record with `perf`

```
perf record -F 999 -g -e cpu-clock -- python3-dbg -m pyperformance run --bench <benchmark_name>
```

Replace `<benchmark_name>` with one of the approved benchmark names (see table
below).

**Note on `-e cpu-clock`:** the plain command from the original guide
(`perf record -F 999 -g -- python3-dbg -m pyperformance run --bench <name>`,
i.e. the default hardware `cycles` event) runs without error inside this VM
but silently records **zero samples** — `perf report` then fails with
`The perf.data data has no samples!`. This happens because frequency-based
sampling with a hardware event relies on a PMI (performance-monitoring
interrupt) to trigger each sample, and this QEMU/KVM guest's virtual PMU does
not reliably deliver that interrupt (plain counter reads via `perf stat -e
cycles` still work fine — only interrupt-driven sampling is affected).
Passing `-e cpu-clock` switches to a software, timer-based event that doesn't
depend on a hardware PMI, which reliably produces samples in this VM.

Example, for `json_dumps`:

```
perf record -F 999 -g -e cpu-clock -- python3-dbg -m pyperformance run --bench json_dumps
```

### Step 2 — Export the report to text

```
perf report --stdio > perf_report.txt
```

Full example for `json_dumps`:

```
perf record -F 999 -g -e cpu-clock -- python3-dbg -m pyperformance run --bench json_dumps
perf report --stdio > perf_report.txt
```

Thanks to the Python debug symbols, `perf_report.txt` reveals internal Python
function calls and stack traces (e.g. `_PyEval_EvalFrameDefault`,
`pymalloc_pool_extend`) used during the benchmark, which helps identify
performance bottlenecks within Python itself.

## Approved Benchmarks

| Index | Name of Benchmark      |
|-------|-------------------------|
| 1     | Raytrace                |
| 2     | Deepcopy                |
| 3     | Mdp                     |
| 4     | Pathlib                 |
| 5     | Pickle and pickle_dict  |
| 6     | Pyflate                 |
| 7     | unpack_sequence         |
| 8     | tornado_http            |
| 9     | sqlite_synth            |
| 10    | Nbody                   |
| 11    | Btree                   |
| 12    | deepblue                |
| 13    | go                      |

Two benchmarks must be selected from this list for the project.

