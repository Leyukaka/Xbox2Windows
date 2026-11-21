"""Differential returns and unwind ownership controls for a split x64 function."""

import ctypes
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory

import pefile
from iced_x86 import Code, Decoder

from direct_check import SENTINELS, Word, observe, register_probe
from cpu_chained_aot import translate
from cpu_leaf_aot import export_rva
from cpu_ranges import function_ranges


def check(native, compiled, harness, native_jne=None, compiled_jne=None,
          native_zero=None, compiled_zero=None, native_imm=None, compiled_imm=None,
          compiled_wide=None):
    probe = register_probe(str(harness))
    pairs = [(native, compiled, 7)]
    if native_jne is not None and compiled_jne is not None:
        pairs.append((native_jne, compiled_jne, 7))
    if native_zero is not None and compiled_zero is not None:
        pairs.append((native_zero, compiled_zero, 0))
    if native_imm is not None and compiled_imm is not None:
        pairs.append((native_imm, compiled_imm, 7))
    for orig_path, lifted_path, null_expected in pairs:
        source = ctypes.CDLL(str(orig_path)).choose_value
        lifted = ctypes.CDLL(str(lifted_path)).try_lifted
        for value, expected in ((None, null_expected), (0, null_expected), (1, 21), (0xffffffffffffffff, 21)):
            slot = Word(value or 0)
            address = 0 if value is None else ctypes.addressof(slot)
            result, trace, regs = observe(probe, source, address, 0x1122334455667788)
            status, actual, kept = observe(probe, lifted, address, 0x1122334455667788)
            if (result != expected or regs != SENTINELS or kept != regs or status != 1
                    or actual != (trace[0], result, *trace[2:]) or slot.value != (value or 0)):
                raise AssertionError((orig_path.name, value, result, actual, kept))

    if compiled_wide is not None:
        source = ctypes.CDLL(str(native)).choose_wide_value
        lifted = ctypes.CDLL(str(compiled_wide)).try_lifted
        second = 0x1122334455667788
        for value, expected in ((0, (1 << 31) + 21),
                                (1 << 63, second % (1 << 32) + 277)):
            slot = Word(value)
            address = ctypes.addressof(slot)
            result, trace, regs = observe(probe, source, address, second)
            status, actual, kept = observe(probe, lifted, address, second)
            if (result != expected or regs != SENTINELS or kept != regs or status != 1
                    or actual != (trace[0], expected, *trace[2:]) or slot.value != value):
                raise AssertionError(("choose_wide_value", value, expected, result, actual, kept))

    with pefile.PE(str(native), fast_load=True) as pe:
        entry = export_rva(pe, "choose_value")
        neighbor = export_rva(pe, "neighbor")
        ranges = function_ranges(pe, entry)
        assert len(ranges) == 3 and all(not a <= neighbor < b for a, b in ranges)
        body = pe.get_data(entry, ranges[0][1] - entry)
        jump = next(i for i in Decoder(64, body, ip=entry) if i.code == Code.JE_REL8_64)
        jump_opcode_offset = pe.get_offset_from_rva(jump.ip)
        jump_offset = pe.get_offset_from_rva(jump.ip + 1)
        move = next(i for i in Decoder(64, body, ip=entry) if i.code == Code.MOV_R32_IMM32)
        move_offset = pe.get_offset_from_rva(move.ip)
        info = next(r.struct.UnwindData for r in pe.DIRECTORY_ENTRY_EXCEPTION
                    if r.struct.BeginAddress == ranges[1][0])
        header_offset = pe.get_offset_from_rva(info)
        parent_offset = pe.get_offset_from_rva(info + 4)

    data = native.read_bytes()
    assert data[header_offset] == (4 << 3) | 1
    expected_translation = translate(native, "choose_value")
    controls = [(jump_offset, struct.pack("b", target - jump.next_ip), "outside owned instruction")
                for target in (neighbor, ranges[1][0] + 1)]
    controls += [(parent_offset, struct.pack("<I", entry + 1), "invalid unwind chain"),
                 (parent_offset, struct.pack("<III", *ranges[1], info), "invalid unwind chain"),
                 (header_offset, bytes([(4 << 3) | 0]), "unsupported chained unwind header"),
                 (header_offset, bytes([(4 << 3) | 3]), "unsupported chained unwind header"),
                 (header_offset, bytes([(5 << 3) | 2]), "unsupported chained unwind header"),
                 (jump_opcode_offset, b"\x78", "expected JE or JNE after TEST"),
                 (move_offset, bytes.fromhex("31 c8 90 90 90"), "unsupported operation"),
                 (move_offset, bytes.fromhex("bf 15 00 00 00"), "unrestored state")]
    with TemporaryDirectory() as folder:
        for offset, patch, reason in controls:
            damaged = bytearray(data)
            damaged[offset:offset + len(patch)] = patch
            path = Path(folder) / "control.dll"
            path.write_bytes(damaged)
            try:
                translate(path, "choose_value")
            except ValueError as exc:
                if reason not in str(exc):
                    raise
            else:
                raise AssertionError(f"accepted control: {reason}")
        # Version-2 UNWIND_INFO keeps the same chained RUNTIME_FUNCTION trailer layout as version 1.
        v2 = bytearray(data)
        v2[header_offset] = (4 << 3) | 2
        v2_path = Path(folder) / "v2.dll"
        v2_path.write_bytes(v2)
        assert translate(v2_path, "choose_value") == expected_translation
        # Preserve instruction boundaries after a synthetic call through [rbx].
        damaged = bytearray(data)
        damaged[move_offset:move_offset + 5] = bytes.fromhex("ff 13 90 90 90")
        path.write_bytes(damaged)
        _, _, returns, stops = translate(path, "choose_value", partial=True)
        assert len(returns) == 2 and stops == {move.ip}
        try:
            translate(path, "choose_value")
        except ValueError as exc:
            if "unsupported operation" not in str(exc):
                raise
        else:
            raise AssertionError("strict translation accepted an indirect call")
    print("two null paths, two non-null inputs (JE, JNE, XOR zeroing and 32-bit immediates), RBX/RSI, guarded writes, version-2 chained unwind and ten rejection controls passed")


if __name__ == "__main__":
    check(*map(Path, sys.argv[1:]))
