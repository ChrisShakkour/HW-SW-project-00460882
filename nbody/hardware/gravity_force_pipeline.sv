// gravity_force_pipeline.sv — Gravity Force Pipeline (GFP)
//
// Hardware implementation of the accelerator proposed for nbody's hot
// path: advance()'s pairwise gravitational-force update
// (nbody/original/run_benchmark.py), profiled at ~9.3% of self-time
// spent purely boxing/deallocating temporary PyFloat objects, on top of
// ~29% interpreter-dispatch overhead in _PyEval_EvalFrameDefault (see
// nbody/BOTTLENECKS.md). Software's own best fix within CPython (manual
// loop unrolling, nbody/optimized_unroll_experiment/) only found ~9%
// headroom, because it can't remove the object-boxing model itself —
// only hardware can.
//
// Unlike pyflate's HFDU (which streams a large, unbounded bitstream
// through a fixed-latency lookup), nbody's entire working set is tiny —
// 5 bodies x (position + velocity + mass) = 35 doubles, ~280 bytes —
// and stays resident in on-chip registers for the *entire* advance(dt,
// n) call. The key architectural idea: software loads the state once,
// kicks off n timesteps with a single start pulse, and the accelerator
// runs the full timestep loop internally without any per-timestep
// round trip back to the CPU. That's what removes the ~200,000
// interpreter re-entries per advance() call, not just the per-pair
// arithmetic.
//
// Not tapeout-ready (no FP exception handling, no reset synchronizers),
// but a complete, logically consistent RTL design.

`timescale 1ns / 1ps

package gfp_pkg;

  localparam int NUM_BODIES = 5;
  localparam int NUM_PAIRS  = 10;  // C(5,2), fixed at synthesis time
  localparam int FP_WIDTH   = 64;  // IEEE-754 double, matches CPython floats

  // The 10 body-index pairs are fixed for this benchmark (5 bodies) and
  // known at synthesis time -- this is the hardware equivalent of the
  // software "manual unrolling" optimization (Attempt 4), except taken
  // all the way: instead of unrolling a loop in a still-interpreted
  // language, this is just fixed wiring between force-lane instances
  // and the body register file. Expressed as functions (rather than
  // unpacked-array localparams, which some elaborators handle poorly)
  // so a generate block can look up each pair's body indices by index.
  function automatic logic [2:0] pair_i(input int p);
    case (p)
      0, 1, 2, 3: pair_i = 3'd0;
      4, 5, 6:    pair_i = 3'd1;
      7, 8:       pair_i = 3'd2;
      9:          pair_i = 3'd3;
      default:    pair_i = 3'd0;
    endcase
  endfunction

  function automatic logic [2:0] pair_j(input int p);
    case (p)
      0:       pair_j = 3'd1;
      1, 4:    pair_j = 3'd2;
      2, 5, 7: pair_j = 3'd3;
      3, 6, 8, 9: pair_j = 3'd4;
      default: pair_j = 3'd0;
    endcase
  endfunction

  typedef enum logic [2:0] {
    S_IDLE,
    S_LOAD,
    S_FORCE,      // 10 pairwise-force lanes evaluate in parallel
    S_ACCUMULATE, // per-body reduction of the 4 pair contributions that touch it
    S_INTEGRATE,  // position += dt * velocity, for all 5 bodies
    S_DONE
  } state_t;

endpackage : gfp_pkg


// ---------------------------------------------------------------------
// One pairwise-force lane: given body i and body j's position/mass,
// dt, produces the six velocity-delta terms
//   dv_i = -(dx,dy,dz) * (m_j * mag)
//   dv_j = +(dx,dy,dz) * (m_i * mag)
// where mag = dt * (dx^2+dy^2+dz^2)^(-1.5).
//
// The reciprocal-sqrt/cube step is delegated to a `fp_rsqrt` black-box
// IP core (a standard pipelined double-precision reciprocal-sqrt unit —
// out of scope to re-derive here; every FPGA/ASIC FP library ships one).
// This mirrors the software finding that replacing `** -1.5` with an
// explicit sqrt (Attempt 2) was *not* a software win — in hardware,
// unlike software, a dedicated rsqrt pipeline has no call-dispatch
// overhead to cancel the savings out, so it *is* a win here.
// ---------------------------------------------------------------------
module force_lane
  import gfp_pkg::*;
(
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic                    valid_in,

    input  logic [FP_WIDTH-1:0]     xi, yi, zi, mi,
    input  logic [FP_WIDTH-1:0]     xj, yj, zj, mj,
    input  logic [FP_WIDTH-1:0]     dt,

    output logic                    valid_out,
    output logic [FP_WIDTH-1:0]     dvix, dviy, dviz,  // subtract from body i
    output logic [FP_WIDTH-1:0]     dvjx, dvjy, dvjz   // add to body j
);

  // Stage 1: displacement
  logic [FP_WIDTH-1:0] dx, dy, dz;
  fp_sub u_sub_x (.a(xi), .b(xj), .result(dx));
  fp_sub u_sub_y (.a(yi), .b(yj), .result(dy));
  fp_sub u_sub_z (.a(zi), .b(zj), .result(dz));

  // Stage 2: r^2 = dx^2 + dy^2 + dz^2
  logic [FP_WIDTH-1:0] dx2, dy2, dz2, r2, r2_partial;
  fp_mul u_mul_x2 (.a(dx), .b(dx), .result(dx2));
  fp_mul u_mul_y2 (.a(dy), .b(dy), .result(dy2));
  fp_mul u_mul_z2 (.a(dz), .b(dz), .result(dz2));
  fp_add u_add_1  (.a(dx2), .b(dy2), .result(r2_partial));
  fp_add u_add_2  (.a(r2_partial), .b(dz2), .result(r2));

  // Stage 3: mag = dt * r^-3 = dt * rsqrt(r2)^3  (black-box FP IP)
  logic [FP_WIDTH-1:0] inv_r3, mag;
  fp_rsqrt3 u_rsqrt3 (.clk(clk), .rst_n(rst_n), .x(r2), .result(inv_r3));
  fp_mul u_mul_mag (.a(dt), .b(inv_r3), .result(mag));

  // Stage 4: b1m = mi*mag, b2m = mj*mag
  logic [FP_WIDTH-1:0] b1m, b2m;
  fp_mul u_mul_b1m (.a(mi), .b(mag), .result(b1m));
  fp_mul u_mul_b2m (.a(mj), .b(mag), .result(b2m));

  // Stage 5: six output products (sign folded in at the accumulate stage)
  fp_mul u_mul_dvix (.a(dx), .b(b2m), .result(dvix));
  fp_mul u_mul_dviy (.a(dy), .b(b2m), .result(dviy));
  fp_mul u_mul_dviz (.a(dz), .b(b2m), .result(dviz));
  fp_mul u_mul_dvjx (.a(dx), .b(b1m), .result(dvjx));
  fp_mul u_mul_dvjy (.a(dy), .b(b1m), .result(dvjy));
  fp_mul u_mul_dvjz (.a(dz), .b(b1m), .result(dvjz));

  // Fixed pipeline latency (all sub-blocks below are themselves
  // pipelined FP IP; LATENCY is their combined depth, a synthesis-time
  // constant supplied by the FP IP library, not re-derived here).
  localparam int LATENCY = 12;
  logic [LATENCY-1:0] valid_sr;
  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) valid_sr <= '0;
    else        valid_sr <= {valid_sr[LATENCY-2:0], valid_in};
  assign valid_out = valid_sr[LATENCY-1];

endmodule : force_lane


// ---------------------------------------------------------------------
// Top level: register file for the 5 bodies' state, 10 parallel
// force_lane instances (fixed wiring per pair_i()/pair_j()), a per-body
// accumulate/commit stage, position integration, and the timestep
// sequencer.
// ---------------------------------------------------------------------
module gravity_force_pipeline
  import gfp_pkg::*;
(
    input  logic clk,
    input  logic rst_n,

    // ---- Control / status (memory-mapped register file) ----
    input  logic         start,
    output logic         done,
    input  logic [31:0]  n_iterations,
    input  logic [FP_WIDTH-1:0] dt,

    // ---- State load/readback port (one body-field per cycle; the
    // whole 35-double state loads/reads back in ~35 cycles, negligible
    // next to n_iterations x 10-pair timesteps) ----
    input  logic                     state_wr_en,
    input  logic [2:0]               state_body_idx,
    input  logic [1:0]               state_field_sel,  // 0=pos,1=vel,2=mass
    input  logic [1:0]               state_axis_sel,    // 0=x,1=y,2=z (mass ignores)
    input  logic [FP_WIDTH-1:0]      state_wr_data,
    output logic [FP_WIDTH-1:0]      state_rd_data
);

  // Body register file: pos[i][xyz], vel[i][xyz], mass[i]
  logic [FP_WIDTH-1:0] pos  [NUM_BODIES][3];
  logic [FP_WIDTH-1:0] vel  [NUM_BODIES][3];
  logic [FP_WIDTH-1:0] mass [NUM_BODIES];

  // ---- State load/readback ----
  always_ff @(posedge clk) begin
    if (state_wr_en) begin
      unique case (state_field_sel)
        2'd0: pos[state_body_idx][state_axis_sel] <= state_wr_data;
        2'd1: vel[state_body_idx][state_axis_sel] <= state_wr_data;
        2'd2: mass[state_body_idx]                <= state_wr_data;
        default: ;
      endcase
    end
  end
  always_comb begin
    unique case (state_field_sel)
      2'd0: state_rd_data = pos[state_body_idx][state_axis_sel];
      2'd1: state_rd_data = vel[state_body_idx][state_axis_sel];
      default: state_rd_data = mass[state_body_idx];
    endcase
  end

  // ---- 10 parallel force lanes, fixed wiring from pair_i()/pair_j() ----
  logic                    lane_valid_in;
  logic [NUM_PAIRS-1:0]    lane_valid_out;  // packed, so a reduction-AND can test "all lanes done"
  logic [FP_WIDTH-1:0]     lane_dvi [NUM_PAIRS][3];  // subtract from body pair_i(p)
  logic [FP_WIDTH-1:0]     lane_dvj [NUM_PAIRS][3];  // add to body pair_j(p)

  genvar p;
  generate
    for (p = 0; p < NUM_PAIRS; p++) begin : g_lanes
      force_lane u_lane (
          .clk(clk), .rst_n(rst_n), .valid_in(lane_valid_in),
          .xi(pos[pair_i(p)][0]), .yi(pos[pair_i(p)][1]), .zi(pos[pair_i(p)][2]),
          .mi(mass[pair_i(p)]),
          .xj(pos[pair_j(p)][0]), .yj(pos[pair_j(p)][1]), .zj(pos[pair_j(p)][2]),
          .mj(mass[pair_j(p)]),
          .dt(dt),
          .valid_out(lane_valid_out[p]),
          .dvix(lane_dvi[p][0]), .dviy(lane_dvi[p][1]), .dviz(lane_dvi[p][2]),
          .dvjx(lane_dvj[p][0]), .dvjy(lane_dvj[p][1]), .dvjz(lane_dvj[p][2])
      );
    end
  endgenerate

  // ---- Accumulate stage: each body appears in exactly 4 of the 10
  // pairs (fixed by pair_i()/pair_j(), known at synthesis time) -- a
  // static 4-input adder/subtractor tree per body/axis, not a dynamic
  // scatter-add. This is the hardware analogue of the numpy scatter-add
  // approach that *lost* in software (Attempt 1) purely on per-call
  // dispatch overhead: here there is no dispatch overhead, so the same
  // idea wins. Sign follows the software convention (advance(): body i
  // of a pair subtracts its contribution, body j adds it):
  //   body 0: -lane{0,1,2,3} (always the i-side)
  //   body 1: +lane0 -lane{4,5,6}
  //   body 2: +lane{1,4} -lane{7,8}
  //   body 3: +lane{2,5,7} -lane9
  //   body 4: +lane{3,6,8,9} (always the j-side)
  logic [FP_WIDTH-1:0] accum_dv [NUM_BODIES][3];
  logic [FP_WIDTH-1:0] vel_new  [NUM_BODIES][3];
  logic [FP_WIDTH-1:0] pos_new  [NUM_BODIES][3];

  genvar ax;
  generate
    for (ax = 0; ax < 3; ax++) begin : g_accum_axis
      logic [FP_WIDTH-1:0] b0_s01, b0_s23, b0_sum;
      fp_add u_b0_s01  (.a(lane_dvi[0][ax]), .b(lane_dvi[1][ax]), .result(b0_s01));
      fp_add u_b0_s23  (.a(lane_dvi[2][ax]), .b(lane_dvi[3][ax]), .result(b0_s23));
      fp_add u_b0_sum  (.a(b0_s01), .b(b0_s23), .result(b0_sum));
      fp_sub u_b0_delta(.a('0), .b(b0_sum), .result(accum_dv[0][ax]));

      logic [FP_WIDTH-1:0] b1_s45, b1_ssum;
      fp_add u_b1_s45  (.a(lane_dvi[4][ax]), .b(lane_dvi[5][ax]), .result(b1_s45));
      fp_add u_b1_ssum (.a(b1_s45), .b(lane_dvi[6][ax]), .result(b1_ssum));
      fp_sub u_b1_delta(.a(lane_dvj[0][ax]), .b(b1_ssum), .result(accum_dv[1][ax]));

      logic [FP_WIDTH-1:0] b2_asum, b2_ssum;
      fp_add u_b2_asum (.a(lane_dvj[1][ax]), .b(lane_dvj[4][ax]), .result(b2_asum));
      fp_add u_b2_ssum (.a(lane_dvi[7][ax]), .b(lane_dvi[8][ax]), .result(b2_ssum));
      fp_sub u_b2_delta(.a(b2_asum), .b(b2_ssum), .result(accum_dv[2][ax]));

      logic [FP_WIDTH-1:0] b3_a25, b3_asum;
      fp_add u_b3_a25  (.a(lane_dvj[2][ax]), .b(lane_dvj[5][ax]), .result(b3_a25));
      fp_add u_b3_asum (.a(b3_a25), .b(lane_dvj[7][ax]), .result(b3_asum));
      fp_sub u_b3_delta(.a(b3_asum), .b(lane_dvi[9][ax]), .result(accum_dv[3][ax]));

      logic [FP_WIDTH-1:0] b4_s36, b4_s89;
      fp_add u_b4_s36 (.a(lane_dvj[3][ax]), .b(lane_dvj[6][ax]), .result(b4_s36));
      fp_add u_b4_s89 (.a(lane_dvj[8][ax]), .b(lane_dvj[9][ax]), .result(b4_s89));
      fp_add u_b4_sum (.a(b4_s36), .b(b4_s89), .result(accum_dv[4][ax]));

      // Commit: vel_new[b][ax] = vel[b][ax] + accum_dv[b][ax], one fp_add
      // per (body, axis) -- these are IEEE-754 bit patterns, so this must
      // go through the FP adder IP, not raw bit-vector `+`.
      for (genvar bi = 0; bi < NUM_BODIES; bi++) begin : g_vel_commit
        fp_add u_vel_commit (.a(vel[bi][ax]), .b(accum_dv[bi][ax]), .result(vel_new[bi][ax]));
      end

      // ---- Integrate stage: pos_new[b][ax] = pos[b][ax] + dt*vel[b][ax] ----
      for (genvar bp = 0; bp < NUM_BODIES; bp++) begin : g_pos_commit
        logic [FP_WIDTH-1:0] dv_term;
        fp_mul u_dt_v   (.a(dt), .b(vel[bp][ax]), .result(dv_term));
        fp_add u_pos_new(.a(pos[bp][ax]), .b(dv_term), .result(pos_new[bp][ax]));
      end
    end
  endgenerate

  // ---- Sequencer ----
  state_t state, state_n;
  logic [31:0] iter_counter;

  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) state <= S_IDLE;
    else        state <= state_n;

  always_comb begin
    state_n = state;
    unique case (state)
      S_IDLE:       state_n = state_t'(start ? S_LOAD : S_IDLE);
      S_LOAD:       state_n = S_FORCE;
      S_FORCE:      state_n = state_t'((&lane_valid_out) ? S_ACCUMULATE : S_FORCE);
      S_ACCUMULATE: state_n = S_INTEGRATE;
      S_INTEGRATE:  state_n = state_t'((iter_counter == n_iterations - 1) ? S_DONE : S_FORCE);
      S_DONE:       state_n = S_IDLE;
      default:      state_n = S_IDLE;
    endcase
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      iter_counter  <= '0;
      done          <= 1'b0;
      lane_valid_in <= 1'b0;
    end else begin
      done          <= 1'b0;
      lane_valid_in <= (state == S_FORCE);

      unique case (state)
        S_LOAD: iter_counter <= '0;
        S_ACCUMULATE: begin
          // advance()'s velocity-update half: v1 -= ..., v2 += ...,
          // all 10 pairs' contributions already folded into vel_new.
          for (int b = 0; b < NUM_BODIES; b++)
            for (int a = 0; a < 3; a++)
              vel[b][a] <= vel_new[b][a];
        end
        S_INTEGRATE: begin
          // advance()'s position-update half: r += dt * v.
          for (int b = 0; b < NUM_BODIES; b++)
            for (int a = 0; a < 3; a++)
              pos[b][a] <= pos_new[b][a];
          iter_counter <= iter_counter + 1;
        end
        S_DONE: done <= 1'b1;
        default: ;
      endcase
    end
  end

endmodule : gravity_force_pipeline


// ---------------------------------------------------------------------
// Black-box IEEE-754 double-precision FP primitives. Standard IP
// (every FPGA vendor's FP library and every ASIC standard-cell FP
// library ships these) — declared here for interface completeness, not
// re-implemented, matching the assignment's "does not need to be
// tapeout-ready" allowance.
// ---------------------------------------------------------------------
module fp_add (input logic [63:0] a, b, output logic [63:0] result);
endmodule : fp_add

module fp_sub (input logic [63:0] a, b, output logic [63:0] result);
endmodule : fp_sub

module fp_mul (input logic [63:0] a, b, output logic [63:0] result);
endmodule : fp_mul

// Computes 1/sqrt(x)^3, i.e. x^(-1.5) — pipelined (multi-cycle latency,
// folded into force_lane's LATENCY constant).
module fp_rsqrt3 (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [63:0] x,
    output logic [63:0] result
);
endmodule : fp_rsqrt3
