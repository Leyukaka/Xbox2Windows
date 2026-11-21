"""Compare a CRT-free EXE with its statically recompiled build, process by process."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from cpu_exe_aot import translate  # noqa: E402

INPUTS = ("low", "high")
# route_value returns 5 + 7, plus 200 for "high"; route_mix adds its first argument back, so the
# entry sees 17 or 217. Below 100 it returns that value as the exit code, otherwise 9.
EXPECTED_STDOUT = {
    "low": b"route_value 5 7 0000000000000000 -> 12\n",
    "high": b"route_value 5 7 0000000000000000 -> 212\n",
}
EXPECTED_EXIT = {"original": {"low": 17, "high": 9}, "inverted": {"low": 9, "high": 217}}
RUNS = 20


def run(image, route_input):
    environment = dict(os.environ, ROUTE_INPUT=route_input)
    result = subprocess.run([str(image)], capture_output=True, env=environment, timeout=30)
    assert result.stderr == b"", (image, result.stderr)
    return result.returncode, result.stdout


def imports(image):
    with pefile.PE(str(image)) as pe:
        return sorted((entry.dll.decode("ascii").lower(), imp.name.decode("ascii"))
                      for entry in pe.DIRECTORY_ENTRY_IMPORT for imp in entry.imports)


def entry_bytes(image):
    with pefile.PE(str(image), fast_load=True) as pe:
        return pe.get_data(pe.OPTIONAL_HEADER.AddressOfEntryPoint, 16)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("original_lifted", type=Path)
    parser.add_argument("inverted", type=Path)
    parser.add_argument("inverted_lifted", type=Path)
    parser.add_argument("original_source", type=Path)
    parser.add_argument("inverted_source", type=Path)
    args = parser.parse_args()
    provider = args.original.parent / "cpu_route_provider.dll"
    builds = {"original": (args.original, args.original_lifted, args.original_source),
              "inverted": (args.inverted, args.inverted_lifted, args.inverted_source)}

    for variant, (native, lifted, source) in builds.items():
        # The translation is reproducible from the native image.
        assert translate(native, (provider, "route_value")) == source.read_text(encoding="utf-8"), variant
        # Both builds import only the provider's route_value, and their entry code differs, so
        # the translated build cannot be running a copy of the original entry.
        for image in (native, lifted):
            assert imports(image) == [("cpu_route_provider.dll", "route_value")], (image, imports(image))
        assert entry_bytes(native) != entry_bytes(lifted), variant
        for route_input in INPUTS:
            expected = (EXPECTED_EXIT[variant][route_input], EXPECTED_STDOUT[route_input])
            for image in (native, lifted):
                observed = {run(image, route_input) for _ in range(RUNS)}
                assert observed == {expected}, (image.name, route_input, observed, expected)
            print(f"{variant} {route_input}: exit {expected[0]}, stdout {expected[1]!r} in {RUNS} runs of each build")

    # The inverted branch changes the exit code for both inputs and leaves the provider's output.
    for route_input in INPUTS:
        assert run(args.original_lifted, route_input)[0] != run(args.inverted_lifted, route_input)[0]

    # A DLL, and a binding that is not the EXE's only import, are rejected.
    for image, symbol, message in ((provider, "route_value", "expected an EXE image"),
                                   (args.original, "mix_args", "expected only the import")):
        try:
            translate(image, (provider, symbol))
        except ValueError as error:
            assert message in str(error), (message, error)
        else:
            raise AssertionError(f"translated {image.name} bound to {symbol}")
    # Patched copies of the original whose entry the profile does not cover are rejected.
    with pefile.PE(str(args.original), fast_load=True) as pe:
        entry = pe.get_offset_from_rva(pe.OPTIONAL_HEADER.AddressOfEntryPoint)
    data = args.original.read_bytes()
    branch = data.index(b"\x48\x83\xf8\x64\x73", entry)
    move_edx = data.index(b"\xba\x07\x00\x00\x00", entry)
    patches = {
        # jmp done (EB 05) becomes EB 01, which lands inside mov eax, 9.
        "branch into an instruction": (data.index(bytes([0xEB, 0x05]), branch) + 1, bytes([1])),
        "expected forward in-function branch": (branch + 5, b"\xfa"),
        # mov edx, 7 becomes mov eax, 7, so RDX is undefined at the call.
        "undefined register": (move_edx, b"\xb8"),
    }
    with TemporaryDirectory() as directory:
        copy = Path(directory) / "patched.exe"
        for message, (offset, value) in patches.items():
            patched = bytearray(data)
            patched[offset:offset + len(value)] = value
            copy.write_bytes(patched)
            try:
                translate(copy, (provider, "route_value"))
            except ValueError as error:
                assert message in str(error), (message, error)
            else:
                raise AssertionError(f"translated a copy that should fail with {message!r}")
    print("Native and translated EXEs match for both inputs; the inverted branch differs.")


if __name__ == "__main__":
    main()
