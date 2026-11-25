"""Check startup image_load d16 decoding, HLSL packing and refusals."""
from pathlib import Path
import re
import struct
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'startup'))
import gfx2hlsl as G  # noqa: E402

# Initialize x, y and mip in v0 through v2. Bind the SRV via descriptor table 0.
PREFIX_WORDS = bytes.fromhex('04040cf4000000fa 8002007e 8002027e 8202047e 7fc08cbf')
SUFFIX_WORDS = bytes.fromhex('703f8cbf 0f1800f804050607 000081bf')
VS_WORDS = bytes.fromhex('8002007e cf0800f800000000 000081bf')
LOAD = 't = r0.Load(int3(v0, v1, 0));'
MIP_LOAD = 't = r0.Load(int3(v0, v1, v2));'
CASES = (
    (0x1, 'v4', ('v4 = f32tof16(t.x);',)),
    (0x3, 'v4', ('v4 = f32tof16(t.x) | (f32tof16(t.y) << 16);',)),
    (0x7, 'v[4:5]', ('v4 = f32tof16(t.x) | (f32tof16(t.y) << 16);', 'v5 = f32tof16(t.z);')),
    (0xf, 'v[4:5]', ('v4 = f32tof16(t.x) | (f32tof16(t.y) << 16);',
                    'v5 = f32tof16(t.z) | (f32tof16(t.w) << 16);')),
    (0xa, 'v4', ('v4 = f32tof16(t.y) | (f32tof16(t.w) << 16);',)),
)


def root_blob():
    # RTS0 fixture with one descriptor table. The translator uses its type and index.
    payload = struct.pack('<9I', 1, 1, 24, 0, 0, 0, 0, 0, 0)
    header = b'DXBC' + bytes(16) + struct.pack('<4I', 1, 44 + len(payload), 1, 36)
    return header + b'RTS0' + struct.pack('<I', len(payload)) + payload


def encode(dmask, *, opcode=0, d16=True):
    # MIMG 2D, vaddr=v0, vdata=v4, srsrc=s[16:23], ssamp=s[24:27].
    return struct.pack('<II', 0xf0000008 | (opcode << 18) | (dmask << 8),
                       0x00c40400 | (int(d16) << 31))


def shader(image):
    return G.Pipeline(VS_WORDS, PREFIX_WORDS + image + SUFFIX_WORDS, root_blob())


def check_emission():
    # Uses the same float-returning view assumption as the existing plain-load path.
    for opcode, name, addr, load in ((0, 'image_load', 'v[0:1]', LOAD),
                                     (1, 'image_load_mip', 'v[0:2]', MIP_LOAD)):
        for mask, dst, writes in CASES:
            image = encode(mask, opcode=opcode)
            asm = f'{name} {dst}, {addr}, s[16:23] dmask:{mask:#x} dim:SQ_RSRC_IMG_2D d16'
            assert G.decode(image) == [(0, 8, name, asm)]
            source = shader(image).hlsl_ps()
            assert load in source, source
            actual = tuple(re.findall(r'^\s*(v[4-7] = .*;)$', source, re.MULTILINE))
            assert actual == writes, ('d16 vdata writes', name, mask, actual)


def refuse(reason, action, *args):
    try:
        action(*args)
    except G.Unsupported as error:
        assert reason in str(error), error
    else:
        raise AssertionError(f'expected Unsupported containing {reason!r}')


def check_refusals():
    stage = shader(encode(0xf)).ps
    # The decoder derives the range from dmask, so inject malformed printed operands.
    refuse('requires 2 vdata VGPRs, got v4', stage.one, 'image_load',
           'image_load v4, v[0:1], s[16:23] dmask:0x7 dim:SQ_RSRC_IMG_2D d16')
    refuse('requires 1 vdata VGPRs, got v[4:5]', stage.one, 'image_load_mip',
           'image_load_mip v[4:5], v[0:2], s[16:23] dmask:0x3 dim:SQ_RSRC_IMG_2D d16')
    refuse('invalid dmask 0x10', stage.one, 'image_load',
           'image_load v4, v[0:1], s[16:23] dmask:0x10 dim:SQ_RSRC_IMG_2D d16')
    refuse('image_load d16: invalid dmask 0x0', shader, encode(0))
    refuse('image_sample modifier d16', shader, encode(0xf, opcode=0x20))
    refuse('image_gather4_lz modifier d16', shader, encode(0x1, opcode=0x47))
    refuse('image_store', shader, encode(0xf, opcode=0x08))


def check_negative_control():
    original = G.Stage.image

    def unpacked_load(stage, ops, mods, how):
        return original(stage, ops, [modifier for modifier in mods if modifier != 'd16'], how)

    with patch.object(G.Stage, 'image', unpacked_load):
        try:
            check_emission()
        except AssertionError as error:
            assert error.args and error.args[0][0] == 'd16 vdata writes', error
        else:
            raise AssertionError('the packed-result check accepted an unpacked load')


if __name__ == '__main__':
    check_emission()
    check_refusals()
    check_negative_control()
    source = shader(encode(0xf, d16=False)).hlsl_ps()
    assert LOAD in source and all(f'v{n} = U(t.{c});' in source for n, c in enumerate('xyzw', 4)), source
    print('image_load d16: decoder ranges, HLSL packing, refusals and unpacked control')
