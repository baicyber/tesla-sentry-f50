"""Check that preview work gives way to the current playback and resumes."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch


def load_viewer(folder):
    with patch.dict(os.environ, TSF_CACHE=folder):
        spec = importlib.util.spec_from_file_location("viewer", Path(__file__).resolve().parents[1] / "sentry-viewer.py")
        viewer = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"fcntl": types.ModuleType("fcntl")} if os.name == "nt" else {}):
            spec.loader.exec_module(viewer)
    viewer.FFMPEG_BIN = sys.executable
    return viewer


class CameraSwitchTest(unittest.TestCase):
    def test_configured_usb_controller_is_used_without_falling_back(self):
        with tempfile.TemporaryDirectory() as folder:
            viewer = load_viewer(folder)
            with patch.dict(os.environ, TSF_UDC='selected-controller'):
                with patch.object(viewer.os.path, 'isdir', return_value=True):
                    self.assertEqual(viewer._udc(), 'selected-controller')
                with patch.object(viewer.os.path, 'isdir', return_value=False):
                    self.assertIsNone(viewer._udc())

    def test_switch_resume_and_leave(self):
        self.check_resume(cooling=False)

    def test_overview_thermal_pause(self):
        self.check_resume(cooling=True)

    def test_scrub_between_minutes_of_same_event(self):
        self.check_resume(cooling=False, next_minute=True)

    def check_resume(self, cooling, next_minute=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            viewer = load_viewer(folder)
            group = root / "SentryClips" / "event"
            group.mkdir(parents=True)
            a = str(group / "2026-11-19_14-00-00-front.mp4"), "360p"
            second_name = "2026-11-19_14-01-00-back.mp4" if next_minute else "2026-11-19_14-00-00-back.mp4"
            b = str(group / second_name), "360p"
            Path(a[0]).write_bytes(b"source A")
            Path(b[0]).write_bytes(b"source B")
            selected = [a, b] if cooling else [a]
            cores = [3]
            jobs = viewer.SentryPreviewJobs()
            procs = []
            def launch(args, **kwargs):
                tmp = Path(args[-1])
                tmp.write_bytes(b"partial")
                proc = Mock()
                proc.poll.return_value = None
                def wait(timeout=None):
                    if timeout is None:
                        return 0
                    if not proc.send_signal.called:
                        if cooling:
                            cores[0] = 0
                        else:
                            selected[:] = [b if selected == [a] else a]
                        raise subprocess.TimeoutExpired(args, timeout)
                    assert tmp.read_bytes() == b"partial"
                    tmp.write_bytes(b"finished")
                    proc.poll.return_value = 0
                    return 0
                proc.wait.side_effect = wait
                procs.append(proc)
                return proc

            with (patch.object(viewer, "preview_cores", side_effect=lambda: cores[0]),
                  patch.object(viewer, "live_keys", side_effect=lambda: selected.copy()),
                  patch.object(viewer.os, "sched_getaffinity", return_value={4,5,6}, create=True),
                  patch.object(viewer, "signal", types.SimpleNamespace(SIGSTOP=19, SIGCONT=18)),
                  patch.object(viewer.subprocess, "Popen", side_effect=launch)):
                def encode(key):
                    return viewer.get_variant(*key, slot=False, wanted=lambda: key in selected, jobs=jobs)
                for key in (a, b):
                    self.assertEqual(encode(key), key[0])
                    self.assertFalse(viewer._variant_ready(*key))
                    self.assertEqual(Path(jobs.stopped[key][1]).read_bytes(), b"partial")
                    jobs.prune(selected)
                    self.assertIn(key, jobs.stopped)
                    if cooling:
                        proc = jobs.stopped[key][0]
                        self.assertEqual(encode(key), key[0])
                        self.assertEqual(proc.send_signal.call_count, 1)
                        self.assertIs(jobs.stopped[key][0], proc)
                        cores[0] = 3
                self.assertEqual(Path(encode(a)).read_bytes(), b"finished")
                self.assertEqual(len(procs), 2)
                self.assertEqual([call.args for call in procs[0].send_signal.call_args_list], [(19,), (18,)])
                procs[0].kill.assert_not_called()
                jobs.prune([])
                procs[1].kill.assert_called_once()
                self.assertFalse(jobs.stopped)
                self.assertFalse(list(Path(viewer.RES_DIR).glob("*.tmp")))


class PreviewQueueTest(unittest.TestCase):
    def test_thermal_hold_resumes_gently_without_waiting_for_idle_temperature(self):
        with tempfile.TemporaryDirectory() as folder:
            viewer = load_viewer(folder)
            sensor = Path(folder) / 'temp'
            now = [10.0]
            def sample(temperature, seconds=.5):
                now[0] += seconds
                sensor.write_text(str(int(temperature * 1000)), encoding='utf-8')
                return viewer.preview_cores()
            with (patch.object(viewer, '_thermal_files', [str(sensor)]),
                  patch.object(viewer.time, 'monotonic', side_effect=lambda: now[0])):
                self.assertEqual(sample(78.2), 0)
                for index in range(6):
                    self.assertEqual(sample(66.5 if index % 2 else 68.9), 0)
                self.assertEqual(sample(68.2), 2)
                self.assertFalse(viewer._thermal_hold)
                for _ in range(8):
                    self.assertEqual(sample(66.0), 2)
                for _ in range(6):
                    self.assertEqual(sample(64.0), 2)
                self.assertEqual(sample(64.0), 3)
                self.assertEqual(sample(78.0), 0)
                sensor.unlink()
                now[0] += 5
                self.assertEqual(viewer.preview_cores(), 0, 'missing sensors must retain the hard stop')

    def test_recovery_waits_for_a_continuous_cool_interval(self):
        with tempfile.TemporaryDirectory() as folder:
            viewer = load_viewer(folder)
            sensor = Path(folder) / 'temp'
            now = [10.0]
            with (patch.object(viewer, '_thermal_files', [str(sensor)]),
                  patch.object(viewer.time, 'monotonic', side_effect=lambda: now[0])):
                for temperature, expected in [(78,0),(69,0),(69,0),(70,0),(69,0),(69,0),(69,0),(69,2)]:
                    sensor.write_text(str(temperature*1000), encoding='utf-8')
                    now[0] += 1
                    self.assertEqual(viewer.preview_cores(), expected)

    def test_cancelled_preemptor_does_not_turn_old_read_ahead_into_an_encoding_error(self):
        with tempfile.TemporaryDirectory() as folder:
            viewer = load_viewer(folder)
            source = Path(folder) / '2026-11-19_14-00-00-front.mp4'
            source.write_bytes(b'source')
            started = threading.Event()
            target_connected = threading.Event()
            target_connected.set()
            attempts = []

            def encode(fp, quality, prefix, index, duration, transport, ahead, connected, warm, startup_boost):
                attempts.append(index)
                if len(attempts) == 1:
                    started.set()
                    with viewer._part_queue_changed:
                        self.assertTrue(viewer._part_queue_changed.wait_for(lambda: len(viewer._part_waiters) == 2, timeout=3))
                    self.assertFalse(connected())
                    target_connected.clear()
                    with viewer._part_queue_changed:
                        viewer._part_queue_changed.notify_all()
                        self.assertTrue(viewer._part_queue_changed.wait_for(lambda: len(viewer._part_waiters) == 1, timeout=3))
                    self.assertFalse(connected(), 'cancelled encode must stay cancelled after preemptor leaves')
                    return
                Path(f'{prefix}{index}.{transport}').write_bytes(b'preview')

            with (patch.object(viewer, 'preview_cores', return_value=2),
                  patch.object(viewer, '_encode_preview_parts', side_effect=encode),
                  ThreadPoolExecutor(max_workers=2) as pool):
                background = pool.submit(viewer.preview_part, str(source), '360p', 0, 5,
                                         'mp4', True, lambda: True)
                self.assertTrue(started.wait(3))
                target = pool.submit(viewer.preview_part, str(source), '360p', 2, 5,
                                     'mp4', False, target_connected.is_set)
                self.assertIsNone(target.result(timeout=4))
                self.assertEqual(background.result(timeout=4), b'preview')
                self.assertEqual(attempts, [0, 0])

    def test_background_batch_yields_to_a_playback_request_then_resumes(self):
        self.check_preemption(cache_only=True, prime=False)

    def test_read_ahead_yields_to_another_camera_seek(self):
        self.check_preemption(cache_only=False, prime=False)

    def test_startup_runway_yields_to_another_camera_first_frame(self):
        self.check_preemption(cache_only=False, prime=True)

    def check_preemption(self, cache_only, prime):
        with tempfile.TemporaryDirectory() as folder:
            viewer = load_viewer(folder)
            source = Path(folder) / "2026-11-19_14-00-00-front.mp4"
            source.write_bytes(b"source")
            background_started = threading.Event()
            attempts = []

            def encode(fp, quality, prefix, index, duration, transport, ahead, connected, warm, startup_boost):
                attempts.append(index)
                if attempts == [0]:
                    background_started.set()
                    with viewer._part_queue_changed:
                        queued = viewer._part_queue_changed.wait_for(lambda: len(viewer._part_waiters) == 2, timeout=3)
                    self.assertTrue(queued)
                    self.assertFalse(connected())
                    return
                Path(f"{prefix}{index}.{transport}").write_bytes(f"part-{index}".encode())

            with (patch.object(viewer, "preview_cores", return_value=2),
                  patch.object(viewer, "_encode_preview_parts", side_effect=encode),
                  ThreadPoolExecutor(max_workers=2) as pool):
                background = pool.submit(viewer.preview_part, str(source), "360p", 0, 2,
                                         "mp4", True, lambda: True, cache_only=cache_only, prime=prime)
                self.assertTrue(background_started.wait(timeout=3))
                playback = pool.submit(viewer.preview_part, str(source), "360p", 1, 2,
                                       "mp4", False, lambda: True)
                self.assertEqual(playback.result(timeout=3), b"part-1")
                self.assertEqual(background.result(timeout=3), 2 if cache_only else b"part-0")
            self.assertEqual(attempts, [0, 1, 0])

    def test_streaming_part_returns_before_batch_finishes_and_yields_to_other_camera(self):
        with tempfile.TemporaryDirectory() as folder:
            viewer = load_viewer(folder)
            front = Path(folder) / "2026-11-19_14-00-00-front.mp4"
            back = Path(folder) / "2026-11-19_14-00-00-back.mp4"
            front.write_bytes(b"front")
            back.write_bytes(b"back")
            part0_written = threading.Event()
            stream_yielded = threading.Event()
            back_started = threading.Event()

            def encode(fp, quality, prefix, index, duration, transport, ahead, connected, warm, startup_boost):
                if fp == str(front) and index == 0:
                    Path(f"{prefix}0.{transport}").write_bytes(b"front-0")
                    with viewer._part_ready_changed:
                        viewer._part_ready_changed.notify_all()
                    part0_written.set()
                    # Keep running while idle until another camera enters the queue.
                    deadline = viewer.time.monotonic() + 3
                    while viewer.time.monotonic() < deadline:
                        if not connected():
                            stream_yielded.set()
                            return
                        viewer.time.sleep(0.01)
                    return
                if fp == str(back) and index == 0:
                    back_started.set()
                    Path(f"{prefix}0.{transport}").write_bytes(b"back-0")
                    with viewer._part_ready_changed:
                        viewer._part_ready_changed.notify_all()

            with (patch.object(viewer, "preview_cores", return_value=3),
                  patch.object(viewer, "_encode_preview_parts", side_effect=encode),
                  ThreadPoolExecutor(max_workers=2) as pool):
                first = pool.submit(viewer.preview_part, str(front), "480p", 0, 10, "mp4", False, lambda: True)
                self.assertTrue(part0_written.wait(timeout=3))
                # First HTTP caller returns immediately while the background stream is still alive.
                self.assertEqual(first.result(timeout=2), b"front-0")
                self.assertFalse(stream_yielded.is_set(), "idle background stream should keep encoding")
                second = pool.submit(viewer.preview_part, str(back), "480p", 0, 10, "mp4", False, lambda: True)
                self.assertTrue(stream_yielded.wait(timeout=3), "background stream must yield to another camera")
                self.assertEqual(second.result(timeout=3), b"back-0")
                self.assertTrue(back_started.is_set())

    def test_streaming_read_ahead_finishes_batch_before_yielding_to_same_priority_peer(self):
        with tempfile.TemporaryDirectory() as folder:
            viewer = load_viewer(folder)
            front = Path(folder) / "2026-11-19_14-00-00-front.mp4"
            back = Path(folder) / "2026-11-19_14-00-00-back.mp4"
            front.write_bytes(b"front")
            back.write_bytes(b"back")
            part0_written = threading.Event()
            peer_queued = threading.Event()
            batch_finished_uninterrupted = threading.Event()

            def encode(fp, quality, prefix, index, duration, transport, ahead, connected, warm, startup_boost):
                if fp == str(front) and index == 0:
                    Path(f"{prefix}0.{transport}").write_bytes(b"front-0")
                    with viewer._part_ready_changed:
                        viewer._part_ready_changed.notify_all()
                    part0_written.set()
                    self.assertTrue(peer_queued.wait(timeout=3))
                    # Same-priority peer read-ahead must NOT kill our streaming batch after part 0.
                    self.assertTrue(connected(), "same-priority read-ahead prematurely killed streaming batch")
                    Path(f"{prefix}1.{transport}").write_bytes(b"front-1")
                    batch_finished_uninterrupted.set()
                    return
                if fp == str(back) and index == 0:
                    Path(f"{prefix}0.{transport}").write_bytes(b"back-0")

            with (patch.object(viewer, "preview_cores", return_value=3),
                  patch.object(viewer, "_encode_preview_parts", side_effect=encode),
                  ThreadPoolExecutor(max_workers=2) as pool):
                first = pool.submit(viewer.preview_part, str(front), "480p", 0, 10, "mp4", True, lambda: True)
                self.assertTrue(part0_written.wait(timeout=3))
                self.assertEqual(first.result(timeout=2), b"front-0")
                second = pool.submit(viewer.preview_part, str(back), "480p", 0, 10, "mp4", True, lambda: True)
                with viewer._part_queue_changed:
                    self.assertTrue(viewer._part_queue_changed.wait_for(lambda: len(viewer._part_waiters) == 1, timeout=3))
                peer_queued.set()
                self.assertTrue(batch_finished_uninterrupted.wait(timeout=3))
                self.assertEqual(second.result(timeout=3), b"back-0")

    def test_lowest_remaining_buffer_camera_runs_first_within_same_priority(self):
        with tempfile.TemporaryDirectory() as folder:
            viewer = load_viewer(folder)
            cams = {}
            for name in ("front", "back", "left_repeater"):
                p = Path(folder) / f"2026-11-19_14-00-00-{name}.mp4"
                p.write_bytes(name.encode())
                cams[name] = str(p)
            gate = threading.Event()
            order = []

            def encode(fp, quality, prefix, index, duration, transport, ahead, connected, warm, startup_boost):
                cam = viewer.cam_of(os.path.basename(fp))
                order.append(cam)
                if cam == "front":
                    gate.wait(timeout=3)
                Path(f"{prefix}{index}.{transport}").write_bytes(cam.encode())

            with (patch.object(viewer, "preview_cores", return_value=3),
                  patch.object(viewer, "_encode_preview_parts", side_effect=encode),
                  ThreadPoolExecutor(max_workers=3) as pool):
                f1 = pool.submit(viewer.preview_part, cams["front"], "720p", 0, 10, "mp4", True, lambda: True, lead_ds=50)
                with viewer._part_queue_changed:
                    viewer._part_queue_changed.wait_for(lambda: len(viewer._part_waiters) == 1, timeout=3)
                # Queue back (healthy 6.0s lead) before left_repeater (urgent 0.8s lead).
                f2 = pool.submit(viewer.preview_part, cams["back"], "720p", 0, 10, "mp4", True, lambda: True, lead_ds=60)
                with viewer._part_queue_changed:
                    viewer._part_queue_changed.wait_for(lambda: len(viewer._part_waiters) == 2, timeout=3)
                f3 = pool.submit(viewer.preview_part, cams["left_repeater"], "720p", 0, 10, "mp4", True, lambda: True, lead_ds=8)
                with viewer._part_queue_changed:
                    viewer._part_queue_changed.wait_for(lambda: len(viewer._part_waiters) == 3, timeout=3)
                gate.set()
                self.assertEqual(f1.result(timeout=3), b"front")
                self.assertEqual(f3.result(timeout=3), b"left_repeater")
                self.assertEqual(f2.result(timeout=3), b"back")
            self.assertEqual(order, ["front", "left_repeater", "back"])

    def test_big_core_rotation_excludes_little_cores_under_normal_and_thermal_modes(self):
        with tempfile.TemporaryDirectory() as folder:
            viewer = load_viewer(folder)
            seen_masks = []

            def fake_popen(args, **kwargs):
                # Pin wrapper is either ['taskset', '-c', mask, ...] or [python, '-c', script, mask, ...]
                mask_str = args[2] if args[0] == "taskset" else args[3]
                seen_masks.append(tuple(int(x) for x in mask_str.split(",")))
                proc = Mock()
                proc.poll.return_value = 0
                proc.wait.return_value = 0
                return proc

            with (patch.object(viewer.os, "sched_getaffinity", return_value=set(range(8)), create=True),
                  patch.object(viewer.subprocess, "Popen", side_effect=fake_popen)):
                for cores, pref in ((3, viewer._BIG_CORE_ROTATIONS[0]), (3, viewer._BIG_CORE_ROTATIONS[1]),
                                    (2, viewer._BIG_CORE_ROTATIONS_2[0]), (2, viewer._BIG_CORE_ROTATIONS_2[1])):
                    with patch.object(viewer, "preview_cores", return_value=cores):
                        self.assertTrue(viewer.run_variant(["ffmpeg", "-i", "in", "out"], lambda: True, preferred=pref))
            self.assertEqual(seen_masks, [(4, 5, 6), (5, 6, 7), (4, 5), (6, 7)])
            for mask in seen_masks:
                self.assertTrue(all(cpu >= 4 for cpu in mask), f"little core leaked into mask: {mask}")


if __name__ == "__main__":
    unittest.main()
