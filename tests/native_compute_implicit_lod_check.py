"""Check that direct DXIL sampling uses the guest's consecutive-lane quads."""

import math
from pathlib import Path
import subprocess
import sys
import tempfile

from compute_gather4_l_check import FLOAT_TEXTURE_FLAG, HEIGHT, SENTINEL, WIDTH, bits
from native_direct_container_check import compute, words
from native_gather_lod_check import render


CASES = (
    ((32, 1, 1), True),
    ((64, 1, 1), False),
    ((8, 4, 1), True),
    ((8, 4, 2), False),
)
MIP_COLORS = tuple(bits(value) for value in (1.0, 2.0, 4.0))


def vop2(op, dst, src0, src1):
    return (op << 25) | (dst << 17) | (src1 << 9) | src0


def program(group, wave32, implicit):
    # One group at ID (0,0,0), with guest local IDs in v0/v1/v2.
    # The texture descriptor is at table + 0 and output at table + 0x20.
    code = [
        0xBEFE03C1 if wave32 else 0xBEFE04C1,  # s_mov_b32 exec_lo, -1 / s_mov_b64 exec, -1
        0xBE810380, 0xF40C0200, 0xFA000000,
        0xF4080800, 0xFA000020,
    ]
    for reg, value in enumerate((0x12, 0xFFF000, 0x04500000, 0), start=4):
        code += [0xBE8003FF | reg << 16, value]

    _, y, z = group
    if z > 1:
        code += [
            vop2(26, 2, 133, 2),      # v2 = z << 5
            vop2(28, 0, 258, 0),      # v0 |= v2
        ]
    if y > 1:
        code += [
            vop2(26, 1, 131, 1),      # v1 = y << 3
            vop2(28, 0, 257, 0),      # v0 |= v1, completing the linear index
        ]
    code += [
        vop2(26, 5, 130, 0),          # v5 = linear index * 4, the output byte offset
    ]
    if y > 1:
        # V ramps within each pair of guest rows. Consecutive quads step two lanes
        # in ddy, covering one texel (mip 0). Without flattening, the 2D spatial
        # quad steps eight lanes in ddy, covering four texels (mip 2).
        code += [
            0x7E0202F0,              # v_mov_b32 v1, 0.5 (U)
            vop2(27, 2, 143, 0),      # v2 = linear index & 15
            0x7E040D02,              # v_cvt_f32_u32 v2, v2
            vop2(8, 2, 255, 2), bits(1 / (2 * HEIGHT)),  # V
            0x7E060280,              # v_mov_b32 v3, 0 (explicit LOD)
        ]
    else:
        code += [
            vop2(22, 3, 130, 0),      # v3 = linear index >> 2
            vop2(27, 3, 131, 3),      # v3 &= 3
            vop2(19, 3, 130, 3),      # v3 = min(v3, 2), the expected mip
            vop2(26, 4, 151, 3),      # v4 = mip << 23
            vop2(37, 4, 255, 4), bits(1 / WIDTH),  # v4 = bits for (1 / WIDTH) * 2**mip
            vop2(27, 1, 129, 0),      # v1 = linear index & 1
            0x7E020D01,              # v_cvt_f32_u32 v1, v1
            vop2(8, 1, 260, 1),      # v1 = U, giving each quad ddx(U) = 2**mip / WIDTH
            vop2(27, 4, 136, 0),      # v4 = linear index & 8
            0x7E080D04,              # v_cvt_f32_u32 v4, v4
            vop2(8, 4, 255, 4), bits(0.0625),  # v4 = 0.5 when bit 3 of the index is set
            vop2(3, 1, 260, 1),      # Add an offset constant within each quad.
            0x7E0402F0,              # v_mov_b32 v2, 0.5 (V)
            0x7E060D03,              # v_cvt_f32_u32 v3, v3 (explicit LOD)
        ]
    code += [
        0xBF8CC07F,                  # s_waitcnt lgkmcnt(0)
        0xF0000108 | ((32 if implicit else 36) << 18), 0x00220401,
        0xBF8C3F70,                  # s_waitcnt vmcnt(0)
        0xE0701000, 0x80080405,      # buffer_store_dword v4, v5, s[32:35], 0 offen
        0xBF810000,
    ]
    return compute(words(*code), table=True, wave32=wave32, group=group)


def compile_shader(cli, sdk, work, group, wave32, implicit):
    source = work / ('-'.join(map(str, (*group, int(wave32), int(implicit)))) + '.c67')
    source.write_bytes(program(group, wave32, implicit))
    shader = source.with_suffix('.cso')
    run = subprocess.run([cli, sdk, work / 'cache', 'gfx1013-container-cs', source, shader],
                         capture_output=True, text=True, timeout=30)
    return shader, run


def compile_pair(cli, sdk, work, group, wave32):
    for implicit in (True, False):
        shader, run = compile_shader(cli, sdk, work, group, wave32, implicit)
        assert run.returncode == 0 and shader.is_file(), (group, wave32, implicit, run.stdout, run.stderr)
        yield shader


def write_reference(path, group):
    threads = math.prod(group)
    if group[1] > 1:
        expected = [MIP_COLORS[0]] * threads
    else:
        expected = [MIP_COLORS[min((lane // 4) % 4, 2)] for lane in range(threads)]
    expected += [SENTINEL] * (320 - threads)
    texels = [color for mip, color in enumerate(MIP_COLORS)
              for _ in range((WIDTH >> mip) * (HEIGHT >> mip) * 4)]
    path.write_bytes(words(FLOAT_TEXTURE_FLAG | 1, *expected, *texels))


def main(cli, sdk, *render_args):
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        if not render_args:
            for group, wave32 in CASES:
                tuple(compile_pair(cli, sdk, work, group, wave32))
            group = (6, 1, 1)
            _, run = compile_shader(cli, sdk, work, group, True, True)
            assert run.returncode != 0 and 'implicit derivatives require a pixel stage' in run.stderr, (
                run.stdout, run.stderr)
            shader, run = compile_shader(cli, sdk, work, group, True, False)
            assert run.returncode == 0 and shader.is_file(), run.stdout + run.stderr
            return

        renderer, adapter = render_args
        wave = render(renderer, adapter, '--shader-wave32')
        assert wave.returncode == 0, wave.stdout + wave.stderr
        reference = work / 'expected.bin'
        for group, wave32 in CASES:
            write_reference(reference, group)
            for shader in compile_pair(cli, sdk, work, group, wave32):
                result = render(renderer, adapter, '--compute-texture-case', shader, reference)
                assert result.returncode == 0 and 'compared_words=320 mismatches=0' in result.stdout, (
                    shader.name, result.stdout, result.stderr)
            print(f'{group} wave{32 if wave32 else 64}: implicit and explicit LOD matched guest readback')


if __name__ == '__main__':
    main(*sys.argv[1:])
