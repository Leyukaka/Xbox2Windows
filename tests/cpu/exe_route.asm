; A CRT-free console EXE. The entry calls route_mix directly, route_mix calls the provider's
; route_value through its IAT slot. Below 100 the entry returns route_mix's result itself, so
; the callee's arithmetic reaches the exit code; otherwise it returns 9. The provider reads the process input, so the EXE itself makes one import call.
; R8 is cleared so both builds pass the same null third argument.
EXTERN __imp_route_value:QWORD
.code
route_mix PROC FRAME
    push rbx
    .pushreg rbx
    push rsi
    .pushreg rsi
    sub rsp, 28h
    .allocstack 28h
    .endprolog
    mov rbx, rcx
    call QWORD PTR [__imp_route_value]
    add rax, rbx
    add rsp, 28h
    pop rsi
    pop rbx
    ret
route_mix ENDP

entry PROC FRAME
    push rbx
    .pushreg rbx
    push rsi
    .pushreg rsi
    sub rsp, 28h
    .allocstack 28h
    .endprolog
    mov ecx, 5
    mov edx, 7
    xor r8d, r8d
    call route_mix
    cmp rax, 100
IFDEF INVERT_BRANCH
    jb above
ELSE
    jae above
ENDIF
    jmp done
above:
    mov eax, 9
done:
    add rsp, 28h
    pop rsi
    pop rbx
    ret
entry ENDP
END
