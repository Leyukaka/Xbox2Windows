; Both functions take (uint64_t, uint64_t, uint64_t trace[3]).
IFDEF IMPORT_MIX
EXTERN __imp_mix_args:QWORD
ENDIF
.code
IFNDEF IMPORT_MIX
mix_args PROC FRAME
    push rbx
    .pushreg rbx
    push rsi
    .pushreg rsi
    sub rsp, 8
    .allocstack 8
    .endprolog
    mov rbx, rdx
    mov rsi, rcx
    mov [r8], rsi
    mov [r8+8], rbx
    mov rax, rsi
    sub rax, rbx
    add rax, 11
    mov [r8+16], rax
IFDEF ALTER_RETURN
    add rax, 1
ENDIF
    add rsp, 8
    pop rsi
    pop rbx
    ret
mix_args ENDP
ENDIF

call_mix PROC FRAME
    push rbx
    .pushreg rbx
    push rsi
    .pushreg rsi
    sub rsp, 28h
    .allocstack 28h
    .endprolog
    mov rbx, rcx
    mov rsi, rdx
    add rcx, 7
    ; Keep the argument unchanged while exercising a signed imm8.
    sub rcx, -1
    sub rcx, 1
    sub rdx, 3
IFDEF IMPORT_MIX
    call QWORD PTR [__imp_mix_args]
ELSE
    call mix_args
ENDIF
    add rax, rbx
    xor rax, rsi
    add rsp, 28h
    pop rsi
    pop rbx
    ret
call_mix ENDP
END
