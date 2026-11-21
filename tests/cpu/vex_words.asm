; coverage: source cpu_aot_vex_words
; VEX.128 16-bit packed-integer leaf functions. Each takes RCX and RDX, the addresses of two
; 16-byte inputs, and R8, the address of a 16-byte output, and returns nothing.
.code

; Widen the bytes of both inputs to words, combine them and pack back with unsigned saturation.
widen_mix PROC FRAME
    .endprolog
    vmovdqu xmm0, xmmword ptr [rcx]
    vmovdqu xmm1, xmmword ptr [rdx]
    vpxor xmm2, xmm2, xmm2
    vpunpcklbw xmm3, xmm0, xmm2
    vpunpckhbw xmm4, xmm0, xmm2
    vpunpcklbw xmm5, xmm1, xmm2
    vpunpckhbw xmm1, xmm1, xmm2
    vpaddw xmm3, xmm3, xmm5
    vpsubw xmm4, xmm4, xmm1
    vpsraw xmm3, xmm3, 1
    vpsllw xmm4, xmm4, 2
    vpackuswb xmm0, xmm3, xmm4
    vmovdqu xmmword ptr [r8], xmm0
    vzeroupper
    ret
widen_mix ENDP

; Signed word arithmetic with memory sources, then interleave and combine the results.
word_ops PROC FRAME
    .endprolog
    vmovdqu xmm0, xmmword ptr [rcx]
    vpmulhw xmm1, xmm0, xmmword ptr [rdx]
    vpavgw xmm2, xmm0, xmmword ptr [rdx]
    vpmaxsw xmm3, xmm1, xmm2
    vpminsw xmm4, xmm1, xmm2
    vpunpcklwd xmm5, xmm3, xmm4
    vpunpckhwd xmm2, xmm3, xmm4
    vpxor xmm5, xmm5, xmm2
    vpor xmm5, xmm5, xmm1
    vpand xmm5, xmm5, xmm0
    vpaddw xmm5, xmm5, xmm3
    vmovdqu xmmword ptr [r8], xmm5
    ret
word_ops ENDP

; Shift counts at and past the word width: arithmetic shifts fill with the sign, logical shifts
; clear the lane. Only the volatile XMM0-XMM5 are used.
shift_edges PROC FRAME
    .endprolog
    vmovdqu xmm0, xmmword ptr [rcx]
    vmovdqu xmm5, xmmword ptr [rdx]
IFDEF LOGICAL_SHIFT
    vpsrlw xmm1, xmm0, 15
ELSE
    vpsraw xmm1, xmm0, 15
ENDIF
    vpsraw xmm2, xmm5, 16
    vpsllw xmm3, xmm0, 16
    vpsllw xmm4, xmm5, 1
    vpaddw xmm1, xmm1, xmm2
    vpor xmm1, xmm1, xmm3
    vpxor xmm1, xmm1, xmm4
    vpsrlw xmm2, xmm0, 0
    vpaddw xmm1, xmm1, xmm2
    vpsrlw xmm3, xmm5, 16
    vpor xmm1, xmm1, xmm3
    vmovdqu xmmword ptr [r8], xmm1
    ret
shift_edges ENDP

; Store, then read the inputs again and store a second result. When the output aliases an
; input, the second reads see the first store, so the order of reads and writes is observable.
store_reload PROC FRAME
    .endprolog
    vmovdqu xmm0, xmmword ptr [rcx]
    vpaddw xmm0, xmm0, xmmword ptr [rdx]
    vmovdqu xmmword ptr [r8], xmm0
    vpsubw xmm1, xmm0, xmmword ptr [rcx]
    vpmaxsw xmm1, xmm1, xmmword ptr [rdx]
    vmovdqu xmmword ptr [r8], xmm1
    ret
store_reload ENDP

; Aligned forms: every buffer must be 16-byte aligned, or vmovdqa faults.
aligned_ops PROC FRAME
    .endprolog
    vmovdqa xmm0, xmmword ptr [rcx]
    vmovdqa xmm1, xmmword ptr [rdx]
    vmovdqa xmm2, xmm0
    vpavgw xmm2, xmm2, xmm1
    vpunpckhwd xmm3, xmm2, xmm1
    vmovdqa xmmword ptr [r8], xmm3
    ret
aligned_ops ENDP

; Byte forms: average, shuffle with control bytes from the second input, unsigned-by-signed
; multiply-add, then word forms with signed saturation, the low product and AND NOT.
byte_ops PROC FRAME
    .endprolog
    vmovdqu xmm0, xmmword ptr [rcx]
    vmovdqu xmm1, xmmword ptr [rdx]
    vpavgb xmm2, xmm0, xmm1
    vpshufb xmm3, xmm0, xmm1
    vpmaddubsw xmm4, xmm0, xmm1
    vpaddsw xmm4, xmm4, xmm2
    vpmullw xmm5, xmm3, xmm1
    vpandn xmm5, xmm5, xmm4
    vpxor xmm5, xmm5, xmm3
    vmovups xmmword ptr [r8], xmm5
    ret
byte_ops ENDP

; Dword and whole-register forms, and the narrow moves: an 8-byte and a 4-byte load zero the
; upper bytes, and the final 8-byte store leaves the upper half of the output untouched.
dword_ops PROC FRAME
    .endprolog
    vmovups xmm0, xmmword ptr [rcx]
    vmovq xmm1, qword ptr [rdx]
    vmovd xmm2, dword ptr [rcx]
    vpshufd xmm3, xmm0, 1Bh
    vpsrad xmm4, xmm3, 31
    vpsrad xmm5, xmm0, 40
    vpaddw xmm4, xmm4, xmm5
    vpsrldq xmm5, xmm0, 3
    vpslldq xmm3, xmm1, 5
    vpor xmm4, xmm4, xmm5
    vpxor xmm4, xmm4, xmm3
    vpsrldq xmm5, xmm0, 16
    vpor xmm4, xmm4, xmm5
    vmovaps xmm5, xmm2
    vpaddw xmm4, xmm4, xmm5
    vmovq xmm1, xmm4
    vpshufd xmm0, xmmword ptr [rdx], 0E4h
    vpxor xmm1, xmm1, xmm0
    vmovdqu xmmword ptr [r8], xmm1
    vmovq qword ptr [r8], xmm4
    vmovd dword ptr [r8], xmm2
    ret
dword_ops ENDP
END
