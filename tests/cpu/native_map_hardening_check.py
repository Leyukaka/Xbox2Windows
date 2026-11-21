"""Check live export page protections and mapped-image release ownership."""

import argparse
import ctypes
from ctypes import wintypes
import mmap
from pathlib import Path
import subprocess
import sys

import pefile

from native_map_check import Mapper, STATUS
from native_tls_check import DETACH, ENTRY, TLS, calls, load_sink

PAGE_NOACCESS = 0x01
PAGE_READWRITE = 0x04
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000

CASES = ("export-directory", "export-name-page", "export-table-page", "double-release", "foreign-pointers",
         "second-detach")


class MemoryBasicInformation(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


def expect(operation, actual, expected):
    if actual != expected:
        raise AssertionError(f"{operation}: got {actual!r}, expected {expected!r}")


def map_image(mapper, image, bindings):
    base, status, detail, requests = mapper(image, bindings)
    if not base or status != STATUS["ok"]:
        raise AssertionError(f"mapping {image.name} failed with status {status}: {detail}")
    if not bindings:
        expect("register probe import requests", requests, [])
    return base


def protect(kernel, page, protection):
    old = wintypes.DWORD()
    if not kernel.VirtualProtect(page, mmap.PAGESIZE, protection, ctypes.byref(old)):
        raise ctypes.WinError(ctypes.get_last_error())
    return old.value


def check_export(mapper, image):
    with pefile.PE(str(image)) as pe:
        directory = pe.OPTIONAL_HEADER.DATA_DIRECTORY[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]]
        probe = next((symbol for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols if symbol.name == b"probe"), None)
        if probe is None:
            raise AssertionError("register probe has no named probe export")
        directory_rva = directory.VirtualAddress
        probe_rva = probe.address

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.DWORD)]
    kernel.VirtualProtect.restype = wintypes.BOOL

    base = map_image(mapper, image, {})
    page = (base + directory_rva) // mmap.PAGESIZE * mmap.PAGESIZE
    old = protect(kernel, page, PAGE_NOACCESS)
    try:
        expect("export lookup with an unreadable page", mapper.export(base, b"probe"), None)
    finally:
        protect(kernel, page, old)
    expect("export lookup after restoring protection", mapper.export(base, b"probe"), base + probe_rva)
    expect("release register probe", mapper.release(base), 1)


def check_export_name_page(mapper, image):
    """Make a page that holds only export name strings unreadable, away from the directory and tables."""
    page_of = lambda rva: rva // mmap.PAGESIZE
    with pefile.PE(str(image)) as pe:
        export = pe.DIRECTORY_ENTRY_EXPORT.struct
        directory = pe.OPTIONAL_HEADER.DATA_DIRECTORY[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]]
        tables = {page_of(directory.VirtualAddress), page_of(directory.VirtualAddress + 39)}
        for rva, width, count in ((export.AddressOfNames, 4, export.NumberOfNames),
                                  (export.AddressOfNameOrdinals, 2, export.NumberOfNames),
                                  (export.AddressOfFunctions, 4, export.NumberOfFunctions)):
            tables.update(range(page_of(rva), page_of(rva + width * count - 1) + 1))
        named = [(symbol.name, pe.get_rva_from_offset(symbol.name_offset), symbol.address)
                 for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols if symbol.name]
    by_page = {}
    for name, rva, address in named:
        by_page.setdefault(page_of(rva), []).append((name, rva, address))
    # A page none of whose bytes belong to the directory or the tables, and whose names start and end on it.
    candidates = [page for page, entries in by_page.items() if page not in tables
                  and all(page_of(rva + len(name)) == page for name, rva, _ in entries)]
    if not candidates:
        raise AssertionError(f"{image.name}: no page holds only export name strings")
    page_index = candidates[len(candidates) // 2]
    target, _, address = by_page[page_index][0]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.DWORD)]
    kernel.VirtualProtect.restype = wintypes.BOOL
    base = map_image(mapper, image, {})
    page = base + page_index * mmap.PAGESIZE
    old = protect(kernel, page, PAGE_NOACCESS)
    try:
        expect("export lookup with an unreadable name page", mapper.export(base, target), None)
    finally:
        protect(kernel, page, old)
    expect("export lookup after restoring protection", mapper.export(base, target), base + address)
    expect("release many-exports image", mapper.release(base), 1)
    return f"name page {page_index:#x} of {len(by_page)} made unreadable, lookup of {target.decode()} returned null"


def check_export_table_page(mapper, image):
    """Make a page that holds only export function-table entries unreadable."""
    page_of = lambda rva: rva // mmap.PAGESIZE
    with pefile.PE(str(image)) as pe:
        export = pe.DIRECTORY_ENTRY_EXPORT.struct
        directory = pe.OPTIONAL_HEADER.DATA_DIRECTORY[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]]
        others = {page_of(directory.VirtualAddress), page_of(directory.VirtualAddress + 39)}
        for rva, width, count in ((export.AddressOfNames, 4, export.NumberOfNames),
                                  (export.AddressOfNameOrdinals, 2, export.NumberOfNames)):
            others.update(range(page_of(rva), page_of(rva + width * count - 1) + 1))
        named = [(symbol.name, pe.get_rva_from_offset(symbol.name_offset), symbol.ordinal - export.Base,
                  symbol.address) for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols if symbol.name]
        functions, function_count = export.AddressOfFunctions, export.NumberOfFunctions
    others.update(page_of(rva) for _, rva, _, _ in named)
    others.update(page_of(rva + len(name)) for name, rva, _, _ in named)
    table_pages = range(page_of(functions), page_of(functions + 4 * function_count - 1) + 1)
    candidates = [page for page in table_pages if page not in others]
    if not candidates:
        raise AssertionError(f"{image.name}: no page holds only function-table entries")
    page_index = candidates[0]
    # A name whose function-table slot lies on that page, so a lookup without the table check would read it.
    target, _, _, address = next(entry for entry in named if page_of(functions + 4 * entry[2]) == page_index)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.DWORD)]
    kernel.VirtualProtect.restype = wintypes.BOOL
    base = map_image(mapper, image, {})
    page = base + page_index * mmap.PAGESIZE
    old = protect(kernel, page, PAGE_NOACCESS)
    try:
        expect("export lookup with an unreadable function-table page", mapper.export(base, target), None)
    finally:
        protect(kernel, page, old)
    expect("export lookup after restoring protection", mapper.export(base, target), base + address)
    expect("release many-exports image", mapper.release(base), 1)
    return f"function-table page {page_index:#x} made unreadable, lookup of {target.decode()} returned null"


def check_double_release(mapper, image):
    base = map_image(mapper, image, {})
    expect("first release", mapper.release(base), 1)
    expect("second release", mapper.release(base), 0)


def check_foreign_pointers(mapper):
    expect("null release", mapper.release(None), 0)

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
    kernel.VirtualAlloc.restype = ctypes.c_void_p
    kernel.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.POINTER(MemoryBasicInformation), ctypes.c_size_t]
    kernel.VirtualQuery.restype = ctypes.c_size_t
    kernel.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
    kernel.VirtualFree.restype = wintypes.BOOL

    pattern = b"\xa5" * mmap.PAGESIZE
    base = kernel.VirtualAlloc(None, len(pattern), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
    if not base:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        ctypes.memmove(base, pattern, len(pattern))
        expect("foreign release", mapper.release(base), 0)
        info = MemoryBasicInformation()
        if not kernel.VirtualQuery(base, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        expect("foreign buffer state", info.State, MEM_COMMIT)
        expect("foreign buffer allocation base", info.AllocationBase, base)
        expect("foreign buffer protection", info.Protect, PAGE_READWRITE)
        if ctypes.string_at(base, len(pattern)) != pattern:
            raise AssertionError("foreign buffer contents changed")
    finally:
        kernel.VirtualFree(base, 0, MEM_RELEASE)


def check_second_detach(mapper, image, sink_path):
    sink = load_sink(sink_path)
    base = map_image(mapper, image, {sink_path.name.casefold(): sink})
    sink.sink_clear()
    expect("first TLS image release", mapper.release(base), 1)
    expect("first release notifications", calls(sink, base),
           [[TLS, DETACH, "module", 0], [ENTRY, DETACH, "module", 0]])
    sink.sink_clear()
    expect("second TLS image release", mapper.release(base), 0)
    expect("second release notifications", calls(sink, base), [])


def run(case, mapper_path, probe, tls_image, sink_path, many_exports):
    mapper = Mapper(mapper_path)
    if case == "export-directory":
        return check_export(mapper, probe)
    if case == "export-name-page":
        return check_export_name_page(mapper, many_exports)
    if case == "export-table-page":
        return check_export_table_page(mapper, many_exports)
    if case == "double-release":
        check_double_release(mapper, probe)
    elif case == "foreign-pointers":
        check_foreign_pointers(mapper)
    else:
        check_second_detach(mapper, tls_image, sink_path)


def check(paths):
    for case in CASES:
        command = [sys.executable, "-B", __file__, "--run", case, *map(str, paths)]
        try:
            child = subprocess.run(command, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            raise SystemExit(f"{case}: FAIL (child timed out)") from None
        if child.returncode:
            detail = child.stderr.strip().splitlines()
            message = f"{case}: FAIL (child exited 0x{child.returncode & 0xffffffff:08x})"
            raise SystemExit(f"{message}: {detail[-1]}" if detail else message)
        print(f"{case}: {child.stdout.strip() or 'PASS'}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", choices=CASES)
    for arg in ("mapper", "probe", "tls_image", "sink", "many_exports"):
        parser.add_argument(arg, type=Path)
    args = parser.parse_args()
    paths = (args.mapper, args.probe, args.tls_image, args.sink, args.many_exports)
    if args.run:
        print(run(args.run, *paths) or "PASS")
    else:
        check(paths)
