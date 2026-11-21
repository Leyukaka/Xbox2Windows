"""Check thread notification order and release from inside a mapped TLS callback."""

import argparse
import ctypes
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from native_map_check import Mapper, STATUS
from native_tls_check import Call, DETACH, ENTRY, TLS, load_sink, new_thread_calls


THREAD_ATTACH = 2
THREAD_DETACH = 3
CASES = ("two-images-native", "two-images", "release-other-during-notification",
         "release-self-during-notification")
CHILD_TIMEOUT = 60


def notification(reason, module):
    return [[TLS, reason, module, 0], [ENTRY, reason, module, 0]]


def expect(label, observed, expected):
    if observed != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {observed!r}")


def process_detaches(sink, base):
    buffer = (Call * 64)()
    count = sink.sink_read(buffer, len(buffer))
    if count >= len(buffer):
        raise AssertionError("sink log reached capacity")
    return [[call.caller, call.reason, "module" if call.module == base else hex(call.module),
             call.reserved] for call in buffer[:count] if call.reason == DETACH]


def run(case, mapper_path, image, sink_path):
    sink = load_sink(sink_path)
    sink.sink_on_thread_attach.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
    sink.sink_on_thread_attach.restype = None
    mapper = Mapper(mapper_path)
    mapper.release.restype = ctypes.c_int
    bindings = {sink_path.name.casefold(): sink}

    def map_copy(directory, name):
        copy = Path(directory) / name
        shutil.copyfile(image, copy)
        base, status, detail, _ = mapper(copy, bindings)
        if status != STATUS["ok"] or not base:
            raise AssertionError(f"{name}: mapping failed (status {status}, detail {detail!r})")
        return base

    if case == "two-images-native":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LoadLibraryW.argtypes = [ctypes.c_wchar_p]
        kernel.LoadLibraryW.restype = ctypes.c_void_p
        with TemporaryDirectory() as a_dir, TemporaryDirectory() as b_dir:
            copies = [Path(a_dir) / "tls_a.dll", Path(b_dir) / "tls_b.dll"]
            for copy in copies:
                shutil.copyfile(image, copy)
            a, b = (kernel.LoadLibraryW(str(copy)) for copy in copies)
            if not a or not b or a == b:
                raise AssertionError(f"native loads failed: {a=} {b=}")
            try:
                expect("native worker thread", new_thread_calls(sink, a),
                       notification(THREAD_ATTACH, "module") + notification(THREAD_ATTACH, hex(b)) +
                       notification(THREAD_DETACH, hex(b)) + notification(THREAD_DETACH, "module"))
            finally:
                kernel.FreeLibrary.argtypes = [ctypes.c_void_p]
                kernel.FreeLibrary(b)
                kernel.FreeLibrary(a)
        return

    with TemporaryDirectory() as a_dir, TemporaryDirectory() as b_dir:
        a = map_copy(a_dir, "tls_a.dll")
        b = None
        if case != "release-self-during-notification":
            b = map_copy(b_dir, "tls_b.dll")
            if b == a:
                raise AssertionError(f"A and B share a module base {a:#x}")

        a_attach = notification(THREAD_ATTACH, "module")
        a_detach = notification(THREAD_DETACH, "module")
        a_release = notification(DETACH, "module")
        b_release = notification(DETACH, hex(b)) if b is not None else []

        if case == "two-images":
            # Measured with LoadLibraryW in two-images-native: attach in load order, detach reversed.
            expect("worker thread", new_thread_calls(sink, a),
                   a_attach + notification(THREAD_ATTACH, hex(b)) +
                   notification(THREAD_DETACH, hex(b)) + a_detach)
            expect("release B", mapper.release(b), 1)
            expect("process detach after release B", process_detaches(sink, a), b_release)
            expect("release A", mapper.release(a), 1)
            expect("process detach after release A", process_detaches(sink, a), b_release + a_release)
        else:
            target = b if case == "release-other-during-notification" else a
            # The sink calls pe_map_release directly from the guest TLS callback.
            hook = ctypes.cast(mapper.release, ctypes.c_void_p)
            arm = lambda thread: sink.sink_on_thread_attach(hook, target, thread)
            if case == "release-other-during-notification":
                # B is released from A's TLS callback while the notifier still holds B's reference.
                # B then gets no thread notification; its process detach runs when the notifier
                # drops that reference, right after A's attach, and B is gone before the detach.
                expect("worker thread", new_thread_calls(sink, a, arm), a_attach + b_release + a_detach)
                expect("second release B", mapper.release(b), 0)
                expect("process detach after second release B", process_detaches(sink, a), b_release)
                expect("release A", mapper.release(a), 1)
                expect("process detach after release A", process_detaches(sink, a), b_release + a_release)
            else:
                expect("worker thread", new_thread_calls(sink, a, arm), a_attach + a_release)
                expect("second release A", mapper.release(a), 0)
                expect("process detach after second release A", process_detaches(sink, a), a_release)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", choices=CASES)
    for name in ("mapper", "image", "sink"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    dlls = [path.resolve() for path in (args.mapper, args.image, args.sink)]
    if args.run:
        run(args.run, *dlls)
        return 0

    for case in CASES:
        command = [sys.executable, "-B", __file__, "--run", case, *map(str, dlls)]
        try:
            child = subprocess.run(command, capture_output=True, text=True, timeout=CHILD_TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"{case}: FAIL (child timed out after {CHILD_TIMEOUT}s)", flush=True)
            return 1
        if child.returncode:
            detail = " ".join((child.stderr or child.stdout).split())
            print(f"{case}: FAIL (child exited {child.returncode:#x}) {detail}".rstrip(), flush=True)
            return 1
        print(f"{case}: PASS", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
