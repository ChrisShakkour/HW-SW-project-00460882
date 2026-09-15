# pyflate — Benchmark Analysis (Section 3)

Source: `pyperformance`'s `bm_pyflate/run_benchmark.py`, embedding Paul
Sladen's stand-alone pure-Python DEFLATE (gzip) and bzip2
decoder/decompressor (2006).

## Purpose

Decompresses a real bzip2-compressed tarball
(`data/interpreter.tar.bz2` — a snapshot of the CPython interpreter
source) entirely in pure Python, and verifies the decompressed output via
an MD5 checksum. The benchmark auto-detects the container format from the
file's magic number; since the test file is bzip2 (`0x425a`), the actual
code path exercised every loop is `bzip2_main()` → `decode_huffman_block()`
(the `gzip_main()` path exists in the file but is never used by this
benchmark).

## Libraries

- `hashlib` — MD5 checksum of the final output only (not part of the hot
  decode loop).
- `struct` — only to build a single-byte packer (`int2byte`).
- `os`, `pyperf` — path handling and the benchmarking harness.
- **No compression/decompression library at all** — this benchmark exists
  specifically to measure a hand-written, from-scratch decompressor,
  which is why it's a good candidate for a "swap in the C-accelerated
  stdlib `bz2` module" optimization.

## Data Structures

- `BitfieldBase` / `Bitfield` / `RBitfield` — manual bit-level stream
  readers: a Python integer used as a bit buffer, refilled **one byte at a
  time** from the underlying file object via `read(1)`.
- `HuffmanLength` / `HuffmanTable` / `OrderedHuffmanTable` — a per-block
  Huffman code table represented as a **flat Python list** of small
  `HuffmanLength` objects (one per symbol in use).
- Plain Python `list`s for the move-to-front (MTF) state and decode
  buffer; `bytes`/`bytearray` for the reconstructed output.

## Algorithmic Shape (hot path)

Per bzip2 block:

1. **Huffman table setup** (`compute_used`, `compute_selectors_list`,
   `compute_tables`) — reads the block header bit-by-bit to reconstruct
   which symbols are in use and their Huffman code lengths.
2. **Main decode loop** (`decode_huffman_block`) — for *every symbol* in
   the block, calls `HuffmanTable.find_next_symbol()`.
3. **Move-to-front decode** (`move_to_front`) turns each decoded MTF index
   back into an actual byte value.
4. **Inverse Burrows-Wheeler Transform** (`bwt_transform` +
   `bwt_reverse`) reconstructs the pre-BWT byte sequence from the decoded
   buffer.
5. A final **run-length decode** pass reassembles the output.

## Relevant to Later Optimization (Section 7)

Two concrete, high-confidence algorithmic bottlenecks stand out while
reading the code (both are strong Section 7 candidates independent of the
"just use `bz2`" library-swap option):

- **`HuffmanTable.find_next_symbol()` does a linear scan** over the full
  symbol table (`for x in self.table: ...`) for **every single symbol
  decoded**, comparing bit-length and code value one entry at a time,
  instead of an O(1)/O(log n) tree or direct lookup-table decode. Since
  this runs once per decoded symbol across the entire decompressed output
  (the CPython source tarball), this linear search is almost certainly the
  single largest algorithmic cost in the benchmark.
- **`move_to_front()` reallocates a new list on every call**
  (`l[:] = l[c:c+1] + l[0:c] + l[c+1:]`) via slicing and concatenation,
  rather than mutating the list in place — called once per decoded symbol
  as well.
- The final RLE-decode loop also allocates a new single-byte `bytes`
  object per output byte (`nearly_there[i:i+1]`), which lines up with the
  very high page-fault/minor-fault counts seen in
  [original/perf_report_original_swevents.txt](original/perf_report_original_swevents.txt)
  (~863K samples) and the elevated 7.0% cache-miss rate in
  [original/perf_stat_original.txt](original/perf_stat_original.txt) compared to nbody's 3.3%.

