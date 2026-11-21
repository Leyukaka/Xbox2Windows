; One routine exported under hundreds of long names (see the generated .def), so the export name
; strings span several pages and one page can hold names only.
.code
named_target PROC
    xor eax, eax
    ret
named_target ENDP
END
