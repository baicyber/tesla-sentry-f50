"""Original MP4 frame sequences distinguish recording gaps from rounded filenames."""
import importlib.util
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


def box(kind, payload):
    return struct.pack('>I4s', len(payload) + 8, kind) + payload


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result + bytes([value]))


def recording(path, first=1000, frames=80, step=250, broken=False, sei=True, trim=0):
    samples = []
    for index in range(frames):
        sequence = first + index + (5 if broken and index == 5 else 0)
        payload = b'\x08\x01\x18' + varint(sequence)
        # Tesla SEI framing; these fixtures exercise metadata, never video decoding.
        payload = payload.replace(b'\x00\x00', b'\x00\x00\x03')
        nal = b'\x06\x05\x42\x42\x69' + payload + b'\x80' if sei else b'\x65\x80'
        samples.append(struct.pack('>I', len(nal)) + nal)
    header = b'\0' * 12 + struct.pack('>II', 10000, (frames - 1) * step)
    avcc = box(b'avcC', b'\x01\x64\x00\x1f\xff')
    stsd = box(b'stsd', b'\0' * 8 + avcc)
    stts = box(b'stts', struct.pack('>IIIIII', 0, 2, frames - 1, step, 1, 0))
    stsz = box(b'stsz', struct.pack('>III', 0, 0, frames) + b''.join(struct.pack('>I', len(s)) for s in samples))
    stco = box(b'stco', struct.pack('>III', 0, 1, 8))
    stsc = box(b'stsc', struct.pack('>IIIII', 0, 1, 1, frames, 1))
    stbl = box(b'stbl', stsd + stts + stsz + stco + stsc)
    mdia = box(b'mdia', box(b'mdhd', header) + box(b'minf', stbl))
    edit = box(b'edts', box(b'elst', struct.pack('>IIIiI', 0, 1, (frames-1)*step, trim, 65536))) if trim else b''
    moov = box(b'moov', box(b'mvhd', header) + box(b'trak', mdia + edit))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(box(b'mdat', b''.join(samples)) + moov)


class ClipContinuityTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, TSF_ARCHIVE=str(self.root), TSF_CACHE=str(self.root/'cache')))
        spec = importlib.util.spec_from_file_location('viewer', Path(__file__).resolve().parents[1]/'sentry-viewer.py')
        self.viewer = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'fcntl': types.ModuleType('fcntl')} if os.name == 'nt' else {}):
            spec.loader.exec_module(self.viewer)

    def metadata(self, name='clip.mp4', **options):
        path = self.root/name
        recording(path, **options)
        stat = path.stat()
        return self.viewer.clip_continuity(str(path), stat.st_size, stat.st_mtime_ns)

    def test_original_sample_timing_and_sequence_are_read_without_decoding(self):
        info = self.metadata(first=0)
        self.assertEqual((info['first'], info['last']), (0, 79))
        self.assertAlmostEqual(info['head_step'], .025)
        self.assertAlmostEqual(info['tail_step'], .025)

    def test_no_metadata_or_unstable_sequence_is_unknown(self):
        self.assertIsNone(self.metadata(sei=False))
        self.assertIsNone(self.metadata(broken=True))

    def test_truncated_indexes_and_trimmed_media_do_not_claim_valid_edges(self):
        self.assertIsNone(self.metadata(frames=4100))
        self.assertIsNone(self.metadata(trim=250))
        path = self.root/'broken.mp4'
        path.write_bytes(b'not an mp4')
        stat = path.stat()
        self.assertIsNone(self.viewer.clip_continuity(str(path), stat.st_size, stat.st_mtime_ns))

    def test_metadata_cache_tracks_file_replacement(self):
        first = self.metadata(first=1000)
        path = self.root/'clip.mp4'
        before = path.stat()
        recording(path, first=2000)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000))
        stat = path.stat()
        second = self.viewer.clip_continuity(str(path), stat.st_size, stat.st_mtime_ns)
        self.assertEqual((first['first'], second['first']), (1000, 2000))

    def test_missing_frames_override_the_small_clock_gap(self):
        before = self.metadata(first=1000)
        after = self.metadata('next.mp4', first=1104)
        gap = self.viewer.recording_gap(before, after, 2, 1.975)
        self.assertEqual(gap['basis'], 'sequence')
        self.assertEqual(gap['missing_frames'], 24)
        self.assertAlmostEqual(gap['seconds'], .6)

    def test_contiguous_frames_do_not_turn_rounding_into_a_gap(self):
        before = self.metadata(first=1000)
        after = self.metadata('next.mp4', first=1080)
        gap = self.viewer.recording_gap(before, after, 3, 1.975)
        self.assertEqual(gap['basis'], 'sequence')
        self.assertEqual(gap['seconds'], 0)

    def test_near_threshold_estimate_is_not_a_certain_lower_bound(self):
        before = self.metadata(first=1000)
        after = self.metadata('next.mp4', first=1101)
        gap = self.viewer.recording_gap(before, after, 2, 1.975)
        self.assertAlmostEqual(gap['seconds'], .525)
        self.assertLess(gap['minimum'], .5)

    def test_edge_frame_rate_is_not_extrapolated_across_a_long_pause(self):
        before = self.metadata(first=1000)
        after = self.metadata('next.mp4', first=2080)
        gap = self.viewer.recording_gap(before, after, 27, 1.975)
        self.assertEqual(gap['basis'], 'clock')

    def test_cancelled_scan_stops_before_reading_a_file(self):
        video = dict(f='2026-11-20_08-00-00-front.mp4',cam='front',url='/media/missing')
        self.viewer.annotate_timeline_gaps([dict(videos=[video])],connected=lambda:False)
        self.assertNotIn('gap_before',video)

    def test_reset_rate_change_and_recording_pause_are_not_false_sequence_matches(self):
        before = self.metadata(first=1000)
        reset = self.metadata('reset.mp4', first=0)
        changed = self.metadata('changed.mp4', first=1104, step=400)
        self.assertEqual(self.viewer.recording_gap(before, reset, 2, 1.975)['basis'], 'unknown')
        self.assertEqual(self.viewer.recording_gap(before, changed, 2, 1.975)['basis'], 'unknown')
        contiguous = self.metadata('next.mp4', first=1080)
        self.assertEqual(self.viewer.recording_gap(before, contiguous, 122, 1.975)['basis'], 'clock')

    def test_no_sei_requires_more_than_half_second_after_clock_uncertainty(self):
        for gap in (.05, .49, .5, 1, 1.3, 1.5):
            self.assertEqual(self.viewer.recording_gap(None, None, 60, 60-gap)['basis'], 'unknown')
        gap = self.viewer.recording_gap(None, None, 120, 60)
        self.assertEqual(gap, {'basis': 'clock', 'seconds': 60, 'minimum': 59})

    def test_cameras_are_measured_independently(self):
        events = []
        for index, stamp in enumerate(('2026-11-20_08-00-00', '2026-11-20_08-00-02')):
            videos = []
            for camera in ('front', 'back'):
                filename = stamp+'-'+camera+'.mp4'
                first = 1000 + (104 if camera == 'front' else 80) * index
                recording(self.root/'DriveClips'/stamp/filename, first=first)
                videos.append(dict(f=filename,cam=camera,duration=1.975,url='/media/DriveClips/'+stamp+'/'+filename))
            events.append(dict(name=stamp,videos=videos))
        self.viewer.annotate_timeline_gaps(events)
        gaps = {v['cam']: v['gap_before'] for v in events[1]['videos']}
        self.assertAlmostEqual(gaps['front']['seconds'], .6)
        self.assertEqual(gaps['back']['seconds'], 0)
        self.assertEqual(gaps['front']['previous'], events[0]['name'])


if __name__ == '__main__':
    unittest.main()
