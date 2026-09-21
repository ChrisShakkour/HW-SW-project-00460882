// hfdu.sv — Canonical Huffman Fast-Decode Unit (HFDU)
//
// Hardware implementation of the accelerator proposed in
// pyflate/report_pyflate.txt's "Hardware Acceleration Proposal" section.
// Replaces pyflate's HuffmanTable.find_next_symbol() O(n) linear scan
// (find_next_symbol, pyflate/original's bz2 decode path, ~6-7% of total
// self-time) with a fixed-latency two-level table lookup.
//
// Not tapeout-ready — no reset synchronizers, no clock-domain crossing,
// no ECC on the SRAMs — but a complete, logically consistent RTL design:
// real port list, real datapath, real control FSM.
//
// Target: 200-400 MHz on an FPGA prototype (see report for ASIC note).

`timescale 1ns / 1ps

package hfdu_pkg;

  localparam int NUM_BANKS      = 6;   // bzip2: up to 6 Huffman groups/block
  localparam int SYMBOL_WIDTH   = 9;   // bzip2 MTF alphabet, up to ~258 symbols
  localparam int LEN_WIDTH      = 5;   // Huffman code length, up to 20 bits
  localparam int BANK_SEL_WIDTH = 3;   // ceil(log2(NUM_BANKS))
  localparam int WINDOW_WIDTH   = 20;  // bit-window shift register width
  localparam int L1_ADDR_WIDTH  = 8;   // 256-entry Level-1 table
  localparam int L1_DEPTH       = 256;
  localparam int L2_ADDR_WIDTH  = 6;   // 64-entry Level-2 table (escape codes)
  localparam int L2_DEPTH       = 64;
  localparam int RUN_LENGTH     = 50;  // symbols per selector run (bzip2 spec)

  // One decoded-entry: either a direct hit (Level-1) or an escape pointer
  // into Level-2.
  typedef struct packed {
    logic                        escape;   // 1 = code longer than 8 bits, look up L2
    logic [SYMBOL_WIDTH-1:0]     symbol;   // valid when !escape
    logic [LEN_WIDTH-1:0]        length;   // valid when !escape
    logic [L2_ADDR_WIDTH-1:0]    l2_index; // valid when escape
  } l1_entry_t;

  typedef struct packed {
    logic [SYMBOL_WIDTH-1:0] symbol;
    logic [LEN_WIDTH-1:0]    length;
  } l2_entry_t;

  typedef enum logic [2:0] {
    S_IDLE,
    S_LOAD_TABLES,
    S_DECODE,
    S_REFILL,
    S_DONE,
    S_ERROR
  } state_t;

endpackage : hfdu_pkg


module hfdu
  import hfdu_pkg::*;
(
    input  logic clk,
    input  logic rst_n,

    // ---- Control / status (AXI-Lite-style register file, simplified) ----
    input  logic start,
    output logic done,
    output logic error,
    output logic [31:0] symbols_decoded,

    // ---- Table load port: per-bank L1/L2 SRAM write, once per block ----
    input  logic                       tbl_wr_en,
    input  logic [BANK_SEL_WIDTH-1:0]  tbl_bank,
    input  logic                       tbl_is_l2,        // 0 = L1 write, 1 = L2 write
    input  logic [L1_ADDR_WIDTH-1:0]   tbl_addr,         // reused (truncated) for L2
    input  l1_entry_t                  tbl_l1_data,
    input  l2_entry_t                  tbl_l2_data,

    // ---- Selector stream: one 3-bit bank index per 50-symbol run ----
    input  logic                       sel_valid,
    output logic                       sel_ready,
    input  logic [BANK_SEL_WIDTH-1:0]  sel_bank,

    // ---- Bitstream input (streamed, no full-block buffering) ----
    input  logic        bits_valid,
    output logic        bits_ready,
    input  logic [7:0]  bits_data,

    // ---- Decoded symbol output ----
    output logic                     sym_valid,
    input  logic                     sym_ready,
    output logic [SYMBOL_WIDTH-1:0]  sym_symbol,
    output logic [LEN_WIDTH-1:0]     sym_length
);

  // ------------------------------------------------------------------
  // Table storage: NUM_BANKS independent L1/L2 SRAMs, all resident
  // simultaneously so a table-bank switch (every RUN_LENGTH symbols)
  // never stalls the pipeline waiting for a reload.
  // ------------------------------------------------------------------
  l1_entry_t l1_mem [NUM_BANKS][L1_DEPTH];
  l2_entry_t l2_mem [NUM_BANKS][L2_DEPTH];

  always_ff @(posedge clk) begin
    if (tbl_wr_en) begin
      if (tbl_is_l2)
        l2_mem[tbl_bank][tbl_addr[L2_ADDR_WIDTH-1:0]] <= tbl_l2_data;
      else
        l1_mem[tbl_bank][tbl_addr] <= tbl_l1_data;
    end
  end

  // ------------------------------------------------------------------
  // Bit-window shift register: holds the next WINDOW_WIDTH unconsumed
  // bits, refilled from the bitstream input as bits are consumed —
  // mirrors the software Bitfield/RBitfield refill logic.
  // ------------------------------------------------------------------
  logic [WINDOW_WIDTH-1:0] bit_window;
  logic [5:0]               bits_available;  // how many valid bits currently in window

  // ------------------------------------------------------------------
  // Selector / active-bank tracking
  // ------------------------------------------------------------------
  logic [BANK_SEL_WIDTH-1:0] active_bank;
  logic [5:0]                 run_counter;     // counts down from RUN_LENGTH

  // ------------------------------------------------------------------
  // Lookup combinational logic (single cycle for the L1 hit case)
  // ------------------------------------------------------------------
  logic [L1_ADDR_WIDTH-1:0] l1_index;
  l1_entry_t                 l1_hit;
  l2_entry_t                 l2_hit;

  assign l1_index = bit_window[WINDOW_WIDTH-1 -: L1_ADDR_WIDTH];
  assign l1_hit    = l1_mem[active_bank][l1_index];
  assign l2_hit    = l2_mem[active_bank][l1_hit.l2_index];

  logic [LEN_WIDTH-1:0]    decode_length;
  logic [SYMBOL_WIDTH-1:0] decode_symbol;
  assign decode_symbol = l1_hit.escape ? l2_hit.symbol : l1_hit.symbol;
  assign decode_length = l1_hit.escape ? l2_hit.length : l1_hit.length;

  // ------------------------------------------------------------------
  // Control FSM
  // ------------------------------------------------------------------
  state_t state, state_n;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) state <= S_IDLE;
    else        state <= state_n;
  end

  always_comb begin
    state_n = state;
    unique case (state)
      S_IDLE:        state_n = state_t'(start ? S_LOAD_TABLES : S_IDLE);
      S_LOAD_TABLES: state_n = state_t'(sel_valid ? S_DECODE : S_LOAD_TABLES);
      S_DECODE: begin
        if (bits_available < decode_length)
          state_n = S_REFILL;
        else if (decode_length == '0)
          // No table entry matched any prefix of the bit window — a
          // malformed code or a table that wasn't loaded correctly.
          state_n = S_ERROR;
        else
          state_n = S_DECODE; // stays; advances internally each cycle a symbol commits
      end
      S_REFILL: state_n = state_t'(bits_valid ? S_DECODE : S_REFILL);
      S_DONE:   state_n = S_IDLE;
      S_ERROR:  state_n = S_IDLE;
      default:  state_n = S_IDLE;
    endcase
  end

  // ------------------------------------------------------------------
  // Datapath: bit-window refill, symbol commit, run-length/bank switch,
  // symbols-decoded counter.
  // ------------------------------------------------------------------
  logic [31:0] sym_count;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      bit_window      <= '0;
      bits_available  <= '0;
      active_bank     <= '0;
      run_counter     <= RUN_LENGTH[5:0];
      sym_count       <= '0;
      sym_valid       <= 1'b0;
      done            <= 1'b0;
      error           <= 1'b0;
    end else begin
      sym_valid <= 1'b0;
      done      <= 1'b0;

      unique case (state)
        S_IDLE: begin
          error <= 1'b0;
        end

        S_LOAD_TABLES: begin
          if (sel_valid) begin
            active_bank <= sel_bank;
            run_counter <= RUN_LENGTH[5:0];
          end
        end

        S_DECODE: begin
          if (bits_available >= decode_length) begin
            // Commit the decoded symbol this cycle.
            bit_window     <= bit_window << decode_length;
            bits_available <= bits_available - decode_length;
            sym_symbol     <= decode_symbol;
            sym_length     <= decode_length;
            sym_valid      <= 1'b1;
            sym_count      <= sym_count + 1;

            // Every RUN_LENGTH symbols, switch to the next selector's bank.
            if (run_counter == 6'd1) begin
              run_counter <= RUN_LENGTH[5:0];
              if (sel_valid) active_bank <= sel_bank;
            end else begin
              run_counter <= run_counter - 6'd1;
            end
          end
        end

        S_REFILL: begin
          if (bits_valid) begin
            bit_window     <= bit_window | (logic'(bits_data) << (WINDOW_WIDTH - 8 - bits_available));
            bits_available <= bits_available + 6'd8;
          end
        end

        S_DONE: begin
          done <= 1'b1;
        end

        S_ERROR: begin
          error <= 1'b1;
        end

        default: ;
      endcase
    end
  end

  assign symbols_decoded = sym_count;
  assign bits_ready       = (state == S_REFILL);
  assign sel_ready         = (state == S_LOAD_TABLES) || (state == S_DECODE && run_counter == 6'd1);

endmodule : hfdu
