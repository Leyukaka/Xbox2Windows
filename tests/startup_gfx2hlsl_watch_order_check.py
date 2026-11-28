"""Check watch priority and rate-limited rescans with synthetic requests."""
import io
import os
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools' / 'startup'))
import gfx2hlsl  # noqa: E402

JOBS = 1
BATCH_SIZE = JOBS * 4
BACKLOG = range(1, 9)
FRESH = 9
MID_PASS = 10


def legacy_batches(out_dir, skip_failed, completed, jobs, watch_since_ns):
    keys = gfx2hlsl.pending_keys(out_dir, skip_failed, completed=completed)
    if keys:
        yield keys


def run(cache, batches):
    order = []
    scans = []
    clock = 0.0
    start = time.time_ns()
    pending_keys = gfx2hlsl.pending_keys

    def scan(*args, **kwargs):
        scans.append(len(order))
        return pending_keys(*args, **kwargs)

    def publish(key, mtime_ns):
        marker = cache / ('req_%016x.a' % key)
        marker.write_bytes(b'synthetic request\n')
        os.utime(marker, ns=(mtime_ns, mtime_ns))

    for key in BACKLOG:
        publish(key, start - 10_000_000_000)
    publish(FRESH, start + 1_000_000_000)

    def translate(out_dir, key):
        nonlocal clock
        order.append(key)
        if len(order) == 1:
            publish(MID_PASS, start + 2_000_000_000)
        if len(order) == BATCH_SIZE + 1:
            clock += 1.1
        (Path(out_dir) / ('%016x.txt' % key)).write_bytes(b'synthetic manifest %016x\n' % key)
        return 'ok'

    with patch.dict(os.environ, {'X2W_FXC': ''}), patch.object(gfx2hlsl, 'translate_one', translate), \
            patch.object(gfx2hlsl.time, 'monotonic', lambda: clock), patch.object(gfx2hlsl, 'pending_keys', scan), \
            patch.object(gfx2hlsl, 'pending_batches', batches), redirect_stdout(io.StringIO()):
        completed = {}
        assert gfx2hlsl.watch_pass(str(cache), False, JOBS, completed, watch_since_ns=start)
        first_pass = order.copy()
        first_pass_scans = scans.copy()
        # The legacy snapshot leaves the mid-pass request for its next pass.
        assert gfx2hlsl.watch_pass(str(cache), True, JOBS, completed, watch_since_ns=start)
    return first_pass, first_pass_scans, order, {path.name: path.read_bytes() for path in cache.iterdir()}


def check_priority(order):
    assert FRESH in order[:BATCH_SIZE], 'fresh request missed the first batch: %r' % order
    assert order[2 * BATCH_SIZE:2 * BATCH_SIZE + 1] == [MID_PASS], (
        'mid-pass request missed the first rescan: %r' % order)
    assert [key for key in order if key not in (FRESH, MID_PASS)] == list(BACKLOG), (
        'older requests lost their key order: %r' % order)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        current = Path(tmp) / 'current'
        legacy = Path(tmp) / 'legacy'
        current.mkdir()
        legacy.mkdir()
        current_pass, current_scans, current_order, current_files = run(current, gfx2hlsl.pending_batches)
        legacy_pass, legacy_scans, legacy_order, legacy_files = run(legacy, legacy_batches)

    assert sorted(current_order) == sorted(legacy_order) == [*BACKLOG, FRESH, MID_PASS]
    assert current_files == legacy_files, 'request or output bytes differ between orders'
    assert legacy_pass == [*BACKLOG, FRESH], 'legacy pass no longer takes one key-ordered snapshot'
    assert legacy_scans == [0], 'legacy pass scanned at unexpected positions: %r' % legacy_scans
    if '--negative-control' in sys.argv[1:]:
        check_priority(legacy_pass)
    check_priority(current_pass)
    assert current_scans == [0, 2 * BATCH_SIZE], 'watch pass scanned at unexpected positions: %r' % current_scans
    print('startup_gfx2hlsl_watch_order_check passed')


if __name__ == '__main__':
    main()
