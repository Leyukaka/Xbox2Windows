"""Recompile a bounded EXE entry point, its one direct callee and the callee's import call."""

import argparse
from pathlib import Path
import struct

import pefile

from cpu_call_aot import lift_body, resolve_import
from cpu_leaf_aot import CONDITIONS, bounded_code


PROLOGUE = bytes.fromhex("53 56 48 83 ec 28")
EPILOGUE = bytes.fromhex("48 83 c4 28 5e 5b c3")
MOVE_IMMEDIATE = {0xb8: "rax", 0xb9: "rcx", 0xba: "rdx"}


def lift_entry(start, code):
    """Lift the entry body to C++ statements. Returns (source, callee RVA)."""
    if not code.startswith(PROLOGUE) or not code.endswith(EPILOGUE):
        raise ValueError(f"unsupported save/restore frame at RVA {start:#x}")
    body = code[len(PROLOGUE):-len(EPILOGUE)]
    base = start + len(PROLOGUE)
    statements = {}
    # Registers defined at each instruction. The loader's register values are not part of the
    # profile, so nothing is defined at entry; a jump target joins the sets of its predecessors.
    defined_at = {0: frozenset()}
    targets = set()
    callee = None
    pos = 0
    while pos < len(body):
        rva = base + pos
        if pos not in defined_at:
            raise ValueError(f"unreachable instruction at RVA {rva:#x}")
        available = set(defined_at[pos])
        reads, writes, jump, falls_through = set(), set(), None, True
        opcode = body[pos]
        if opcode in MOVE_IMMEDIATE and pos + 5 <= len(body):
            # A 32-bit move zero-extends into the 64-bit register.
            register = MOVE_IMMEDIATE[opcode]
            value = struct.unpack_from("<I", body, pos + 1)[0]
            statement = f"{register} = {value}u;"
            writes = {register}
            length = 5
        elif body[pos:pos + 3] == b"\x45\x33\xc0":
            statement = "r8 = nullptr;"
            writes = {"r8"}
            length = 3
        elif opcode == 0xe8 and pos + 5 <= len(body):
            if callee is not None:
                raise ValueError(f"unexpected additional call at RVA {rva:#x}")
            callee = rva + 5 + struct.unpack_from("<i", body, pos + 1)[0]
            statement = "rax = lifted_route(rcx, rdx, r8);"
            reads = {"rcx", "rdx", "r8"}
            writes = {"rax"}
            length = 5
        elif (body[pos:pos + 3] == b"\x48\x83\xf8" and pos + 6 <= len(body)
              and body[pos + 4] in CONDITIONS):
            # A compare and its immediate consumer form one operation, so flags never cross a join.
            limit = struct.unpack_from("b", body, pos + 3)[0] & 0xffffffffffffffff
            statement = f"if (rax {CONDITIONS[body[pos + 4]]} {limit}ull) "
            reads = {"rax"}
            jump = struct.unpack_from("b", body, pos + 5)[0]
            length = 6
        elif opcode == 0xeb and pos + 2 <= len(body):
            statement = ""
            jump = struct.unpack_from("b", body, pos + 1)[0]
            falls_through = False
            length = 2
        else:
            raise ValueError(f"unsupported or incomplete instruction at RVA {rva:#x}")

        if not reads <= available:
            raise ValueError(f"undefined register at RVA {rva:#x}: {sorted(reads - available)}")
        if opcode == 0xe8:
            # Only RBX and RSI survive the call in this profile, and the entry uses neither.
            available = set()
        available |= writes
        following = pos + length
        if jump is not None:
            target = following + jump
            if target <= pos or target > len(body):
                raise ValueError(f"expected forward in-function branch at RVA {rva:#x}")
            targets.add(target)
            defined_at[target] = defined_at.get(target, frozenset(available)) & frozenset(available)
            statement += "goto exit;" if target == len(body) else f"goto loc_{target:x};"
        if falls_through:
            defined_at[following] = defined_at.get(following, frozenset(available)) & frozenset(available)
        statements[pos] = statement
        pos = following

    invalid = {t for t in targets if t != len(body)} - set(statements)
    if invalid:
        raise ValueError(f"branch into an instruction at RVA {base + min(invalid):#x}")
    if callee is None:
        raise ValueError(f"missing call at RVA {start:#x}")
    if "rax" not in defined_at.get(len(body), frozenset()):
        raise ValueError(f"missing return value at RVA {start + len(code):#x}")
    lines = ['extern "C" unsigned long lifted_entry() {',
             "    word rax, rcx, rdx;",
             "    word* r8;"]
    for offset, statement in statements.items():
        if offset in targets:
            lines.append(f"loc_{offset:x}:")
        lines.append("    " + statement)
    lines += ["exit:", "    return static_cast<unsigned long>(rax);", "}"]
    return "\n".join(lines), callee


def translate(image, binding):
    provider, symbol = Path(binding[0]), binding[1]
    with pefile.PE(str(image), fast_load=True) as pe:
        if pe.FILE_HEADER.Characteristics & pefile.IMAGE_CHARACTERISTICS["IMAGE_FILE_DLL"]:
            raise ValueError("expected an EXE image")
        # Anything the loader would run or bind besides the entry and the one import is outside
        # the profile, so an image with TLS, delay imports or other imports is rejected.
        pe.parse_data_directories(directories=[1, 9, 13])
        if getattr(pe, "DIRECTORY_ENTRY_TLS", None) or getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", None):
            raise ValueError("TLS or delay imports are outside the profile")
        imported = [(entry.dll.decode("ascii").casefold(), imp.name)
                    for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) for imp in entry.imports]
        if imported != [(provider.name.casefold(), symbol.encode("ascii"))]:
            raise ValueError(f"expected only the import {provider.name}!{symbol}")
        start = pe.OPTIONAL_HEADER.AddressOfEntryPoint
        code = bounded_code(pe, start)
        entry, callee_rva = lift_entry(start, code)
        callee_code = bounded_code(pe, callee_rva)
        if callee_rva < start + len(code) and start < callee_rva + len(callee_code):
            raise ValueError(f"overlapping call target at RVA {callee_rva:#x}")

        def bind_import(slot):
            return resolve_import(pe, slot, provider, symbol)

        callee, target = lift_body(callee_rva, callee_code, caller=True, bind_import=bind_import)
        # The target starts with its kind and ends with the bound name.
        kind, name = target[0], target[-1]
        if kind != "import":
            raise ValueError(f"expected an import call in the callee at RVA {callee_rva:#x}")
        # lift_body emits the DLL caller signature; the EXE keeps the callee internal.
        exported = 'extern "C" __declspec(dllexport) __declspec(noinline) word lifted('
        if callee.count(exported) != 1:
            raise ValueError("unexpected callee signature from cpu_call_aot")
        callee = callee.replace(exported, "static __declspec(noinline) word lifted_route(")
    return "\n".join(["#include <cstdint>",
                      "using word = std::uint64_t;",
                      f'extern "C" __declspec(dllimport) word {name}(word, word, word*);',
                      "", callee, "", entry, ""])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--bind", nargs=2, metavar=("PROVIDER_DLL", "SYMBOL"), required=True,
                        help="export name with the fixture's two-word and output-pointer ABI")
    args = parser.parse_args()
    try:
        args.output.write_text(translate(args.image, args.bind), encoding="utf-8")
    except (OSError, ValueError, pefile.PEFormatError) as error:
        parser.exit(1, f"{error}\n")
