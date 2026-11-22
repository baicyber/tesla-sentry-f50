"""Exercise ZIP selection, exact download quality and the shared export slot."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from zipfile import ZipFile


class ExportRegressionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.archive = self.root / "archive"
        self.category = "SentryClips"
        self.event = "2026-11-19_09-00-08"
        self.first = "2026-11-19_09-00-00"
        env = dict(TSF_ARCHIVE=str(self.archive), TSF_CACHE=str(self.root / "cache"),
                   TSF_FFMPEG=str(self.root / "missing-ffmpeg"), TSF_AUTH_FILE=str(self.root / "auth.conf"),
                   TSF_SETTINGS=str(self.root / "settings.json"), TSF_STITCH="1")
        self.enterContext(patch.dict(os.environ, env))
        viewer_path = Path(__file__).resolve().parents[1] / "sentry-viewer.py"
        spec = importlib.util.spec_from_file_location("viewer", viewer_path)
        self.viewer = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"fcntl": types.ModuleType("fcntl")} if os.name == "nt" else {}):
            spec.loader.exec_module(self.viewer)
        self.viewer.AUTH_ON = False

        self.event_dir = self.archive / self.category / self.event
        self.event_dir.mkdir(parents=True)
        for clip in (self.first, self.event):
            for camera in ("front", "back"):
                name = f"{clip}-{camera}.mp4"
                (self.event_dir / name).write_bytes(name.encode("ascii"))
        (self.event_dir / "event.json").write_text("{}", encoding="utf-8")

        self.server = self.enterContext(self.viewer.BoundedServer(("127.0.0.1", 0), self.viewer.Handler))
        server_thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02})
        server_thread.start()
        self.addCleanup(server_thread.join)
        self.addCleanup(self.server.shutdown)

    def request(self, method, endpoint, **query):
        if query:
            endpoint += "?" + urlencode(query)
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, endpoint)
            response = connection.getresponse()
            return response.status, response.getheader("Content-Type"), response.read()
        finally:
            connection.close()

    @property
    def zip_url(self):
        return f"/api/export/{self.category}/{self.event}"

    def download_path(self, clip, camera):
        return f"/archive/{self.category}/{self.event}/{clip}-{camera}.mp4"

    def cache_variant(self, clip, camera, quality):
        original = self.event_dir / f"{clip}-{camera}.mp4"
        variant = Path(self.viewer._variant_path(str(original), quality))
        variant.parent.mkdir(parents=True, exist_ok=True)
        payload = f"{quality} {camera}".encode("ascii")
        variant.write_bytes(payload)
        return payload

    def test_zip_contains_only_cameras_from_selected_clip(self):
        status, _, data = self.request("GET", self.zip_url, clip=self.first, cams="back")
        self.assertEqual(status, 200)
        with ZipFile(io.BytesIO(data)) as archive:
            self.assertCountEqual(archive.namelist(), [f"{self.event}/{self.first}-back.mp4", f"{self.event}/event.json"])

    def test_zip_missing_clip_does_not_select_latest(self):
        for method in ("GET", "HEAD"):
            with self.subTest(method=method):
                status, _, _ = self.request(method, self.zip_url, clip="2026-11-19_08-59-52")
                self.assertEqual(status, 404)

    def test_legacy_zip_without_clip_keeps_latest(self):
        status, _, data = self.request("GET", self.zip_url)
        self.assertEqual(status, 200)
        with ZipFile(io.BytesIO(data)) as archive:
            cameras = [name for name in archive.namelist() if name.endswith(".mp4")]
            self.assertCountEqual(cameras, [f"{self.event}/{self.event}-{cam}.mp4" for cam in ("front", "back")])

    def test_single_download_refuses_unavailable_exact_quality(self):
        path = self.download_path(self.first, "front")
        for method in ("GET", "HEAD"):
            with self.subTest(method=method):
                status, _, _ = self.request(method, "/dl", path=path, q="480p")
                self.assertEqual(status, 503)
        status, _, data = self.request("GET", "/dl", path=path)
        self.assertEqual((status, data), (200, f"{self.first}-front.mp4".encode("ascii")))

    def test_zip_does_not_mix_available_variant_with_original(self):
        self.cache_variant(self.first, "front", "480p")
        for method in ("GET", "HEAD"):
            with self.subTest(method=method):
                status, _, _ = self.request(method, self.zip_url, clip=self.first, cams="front,back", q="480p")
                self.assertEqual(status, 503)

    def test_downloads_use_cached_exact_quality(self):
        expected = self.cache_variant(self.first, "front", "480p")
        path = self.download_path(self.first, "front")
        self.assertEqual(self.request("GET", "/dl", path=path, q="480p"), (200, "video/mp4", expected))
        status, content_type, body = self.request("HEAD", self.zip_url, clip=self.first, cams="front", q="480p")
        self.assertEqual((status, content_type, body), (200, "application/zip", b""))
        status, _, data = self.request("GET", self.zip_url, clip=self.first, cams="front", q="480p")
        self.assertEqual(status, 200)
        with ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.read(f"{self.event}/{self.first}-front.mp4"), expected)

    def test_export_and_stitch_share_one_active_slot(self):
        probing = threading.Event()
        resume_export = threading.Event()
        viewer = self.viewer

        def probe_duration(path):
            if Path(path).name.startswith(self.first):
                probing.set()
                if not resume_export.wait(5):
                    raise TimeoutError("export test did not release its duration probe")
            return 4.0

        camera_names = ["front", "back"]
        files = [f"{self.first}-{camera}.mp4" for camera in camera_names]
        with (patch.object(viewer, "FFMPEG_BIN", sys.executable),
              patch.object(viewer, "mem_available_mb", return_value=1024),
              patch.object(viewer, "probe_duration", side_effect=probe_duration),
              patch.object(viewer, "_export_worker", lambda *args: None),
              patch.object(viewer, "_stitch_worker", lambda *args: None),
              ThreadPoolExecutor(max_workers=2) as requests):
            first = requests.submit(viewer.start_export, self.category, self.event, camera_names, False, "orig", files)
            try:
                self.assertTrue(probing.wait(5), "first request did not reach the duration probe")
                second = requests.submit(viewer.start_stitch, self.category, self.event, camera_names)
                second_result = second.result(timeout=5)
            finally:
                resume_export.set()
            first_result = first.result(timeout=5)
        outcomes = [first_result, second_result]
        self.assertEqual(sum(job_id is not None for job_id, _ in outcomes), 1, outcomes)
        self.assertEqual(sum(job.get("state") == "running" for job in viewer.STITCH_JOBS.values()), 1)


if __name__ == "__main__":
    unittest.main()
