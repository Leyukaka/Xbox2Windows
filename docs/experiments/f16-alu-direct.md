# Binary16 arithmetic in the direct compute path

The direct C++ compute frontend supports VOP2 and VOP3 `v_add_f16`, `v_sub_f16`, `v_subrev_f16` and `v_mul_f16`, plus VOP1 and VOP3 `v_rcp_f16`, `v_sqrt_f16`, `v_rsq_f16`, `v_log_f16` and `v_exp_f16`. All nine operations support SDWA. Graphics programs keep the generic direct-lowering refusal.

## Semantics

- Each source is its low 16 bits by default. An inline float uses the binary16 rounding of that f32 value. An integer inline or a literal uses its low 16 bits. SDWA accepts WORD_0, WORD_1 or DWORD (low half).
- abs clears bit 15 of the half, then neg toggles it.
- The operation is an f32 add, sub or mul. An f32 value has a 24-bit significand, which covers a correctly rounded binary16 add, sub or mul.
- Reciprocal computes `1.0 / x` in f32. The other unary operations use DXIL Sqrt (24), Rsqrt (25), Log (23, base two) and Exp (21).
- Reciprocal exchanges signed zeros and signed infinities. Square root preserves signed zero and produces NaN for negative nonzero inputs. Exp returns 1 for either signed zero, infinity at and above 16, and positive zero at and below -25 (the exact tie at -25 is checked in the CPU model only, since an Exp one f32 ULP high rounds it to the smallest denormal) after binary16 rounding.
- The result is rounded with the same integer round-to-nearest-even conversion `v_cvt_f16_f32` uses. The low 16 bits are written and the high half is preserved. SDWA writes the selected half and pads or preserves the other half.
- Rsq maps +0 to +infinity, -0 to -infinity and +infinity to +0. Log maps either signed zero to -infinity, +infinity to +infinity and 1 to +0. Both return a quiet NaN for NaN and negative nonzero inputs, including -infinity.
- The emitter selects these results using the binary16 source bits after abs and neg. D3D Rsqrt does not guarantee -infinity for -0, so the rsq zero result takes its sign directly from the source.
- `v_subrev_f16` subtracts the first source from the second.
- DPP16 VOP2 permutes the full src0 VGPR word before selecting its low half and honors FI, BOUND_CTRL, and row and bank write masks.

The reference is `half_op` in [`tests/f16_packed_model.py`](../../tests/f16_packed_model.py). Reciprocal uses exact rational rounding. Square root, reciprocal square root, log2 and exp2 use float64 before binary16 rounding. Nonzero finite exp2, rsq and log2 results allow one binary16 ULP. Other numeric results match exactly, including signed zeros and infinities. NaN results match by class.

The [plain-form fixture](../../tests/native_f16_alu_check.py) covers signed zeros and infinities, quiet and signaling NaNs, negative finite values, subnormals, 1, powers of two and general values. Its IR interpreter models Rsqrt returning +infinity for -0 to exercise the sign fix. The [SDWA fixture](../../tests/native_sdwa_f16_check.py) exercises source selection and modifiers, destination padding and preservation, and negation of +0 before rsq and log.

## Refused forms

The SDWA operand profile is covered by the [SDWA experiment](native-sdwa-f16.md).

Each of these is a named diagnostic:

- DPP8
- clamp or omod
- a high-half `op_sel` (instruction bits 11 through 14)
- a binary16 rounding mode other than round-to-nearest-even, an f16 denormal mode other than keep on input and output, or `FP16_OVFL`
- `s_setreg_b32` of those MODE bits, because the source is a register

`s_round_mode` and `s_denorm_mode` that leave the f16 fields at that contract, and that leave the f32 fields unchanged, are accepted. A write that changes an f32 rounding or denormal field keeps the previous control-instruction refusal. A guest branch together with a MODE write makes the contract unprovable and is refused with the rounding diagnostic.

For the plain scalar-half forms, fma, fmac, fmamk, fmaak, min, max, ldexp, the min3/max3/med3 forms, div_fixup and the VOP1 ops other than rcp, sqrt, exp, rsq and log stay on the generic direct-lowering diagnostic. The fixture checks `v_fmac_f16` and `v_min_f16`. VOP3P arithmetic has a [separate experiment](native-packed-f16.md).

## Linux

Python 3.12.3. `compile_dxil` is created only in the `WIN32` section of `CMakeLists.txt`, so this host did not build that target. The IR dumper was compiled from `src/gfx1013_compute_native.cpp`, `src/gfx1013_isa.cpp`, `src/guest_shader_container.cpp`, `src/gfx1013_compute_ir.cpp` and `tests/native_compute_ir_dump.cpp` with g++ 13.3.0 and a local stub of `d3d12.h` and `wrl/client.h`. That stub is not in the tree.

```
python3 -B tests/native_f16_alu_check.py model
python3 -B tests/native_f16_alu_check.py ir <native_compute_ir_dump>
```

- Before unary coverage was added, `model` checked 82 results, including controls for a sub/subrev swap and a zeroed upper half.
- Before unary coverage was added, `ir` reported 82 results and 20 refusals. The module used `legacyF16ToF32`, `fadd`, `fsub` and `fmul`, and the integer round contained the constant `939524096`.

`native_f16_alu_hardware` was not run. It is registered with skip code 77, the same code `native_store_replay` returns when the device cannot run a wave32 shader. WARP is expected to take that skip.

## Windows

Pending. On the Windows test machine:

```
cmake --preset dev
cmake --build --preset dev --target compile_dxil offscreen_check
ctest --preset dev --no-tests=error -R "^native_f16_alu_(ir|hardware)$" -V -j 1
```
