"""Keep driving trips isolated without changing original clip URLs or exports."""
from http.client import HTTPConnection
import importlib.util
import io
import json
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


class DriveTripsTest(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.archive = root / "archive"
        self.enterContext(patch.dict(os.environ, TSF_ARCHIVE=str(self.archive), TSF_CACHE=str(root / "cache"),
                                     TSF_SETTINGS=str(root / "settings.json")))
        spec = importlib.util.spec_from_file_location("viewer", Path(__file__).resolve().parents[1] / "sentry-viewer.py")
        self.viewer = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"fcntl": types.ModuleType("fcntl")} if os.name == "nt" else {}):
            spec.loader.exec_module(self.viewer)
        self.viewer.AUTH_ON = False
        self.clips = {
            "2026-11-18_08-00-00": 59.4,
            "2026-11-18_08-01-00": 60,
            "2026-11-18_08-02-08": 60,
            "2026-11-18_08-03-30": 60,
            "2026-11-18_08-10-00": 2,
            "2026-11-18_08-10-40": 60,
            "2026-11-18_23-59-30": 60,
            "2026-11-19_00-00-30": 60,
        }
        for category in ("DriveClips", "SavedClips"):
            for stamp in self.clips:
                folder = self.archive / category / stamp
                folder.mkdir(parents=True)
                for camera in ("front", "back"):
                    name = f"{stamp}-{camera}.mp4"
                    (folder / name).write_bytes(name.encode("ascii"))
        self.enterContext(patch.object(self.viewer, "movie_duration", side_effect=lambda path, *_: self.clips[Path(path).parent.name]))
        self.server = self.enterContext(self.viewer.BoundedServer(("127.0.0.1", 0), self.viewer.Handler))
        worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02})
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(self.server.shutdown)

    def get(self, endpoint, **query):
        if query:
            endpoint += "?" + urlencode(query)
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request("GET", endpoint)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_library_groups_adjacent_clips_and_preserves_source_urls(self):
        status, body = self.get("/api/events")
        self.assertEqual(status, 200)
        data = json.loads(body)
        trips = data["DriveClips"]
        self.assertEqual(len(trips), 6)
        trip = trips[-1]
        self.assertEqual(trip["name"], "2026-11-18_08-00-00")
        self.assertEqual(trip["clips"], list(self.clips)[:3])
        self.assertEqual(trip["end"], "2026-11-18_08-03-08")
        self.assertEqual(len(trip["videos"]), 6)
        self.assertTrue(any("/2026-11-18_08-02-08/" in video["url"] for video in trip["videos"]))
        self.assertEqual(len(data["SavedClips"]), len(self.clips))

    def test_each_member_opens_only_its_trip(self):
        for selected in list(self.clips)[:3]:
            with self.subTest(selected=selected):
                status, body = self.get("/api/timeline", cat="DriveClips", event=selected)
                self.assertEqual(status, 200)
                events = json.loads(body)
                self.assertEqual([event["name"] for event in events], list(self.clips)[:3])
                self.assertEqual({event["trip"] for event in events}, {"2026-11-18_08-00-00"})

    def test_long_gaps_short_clips_and_midnight_split_trips(self):
        for selected in list(self.clips)[3:]:
            with self.subTest(selected=selected):
                status, body = self.get("/api/timeline", cat="DriveClips", event=selected)
                self.assertEqual(status, 200)
                self.assertEqual([event["name"] for event in json.loads(body)], [selected])

    def test_missing_selection_never_loads_the_entire_archive(self):
        self.assertEqual(self.get("/api/timeline", cat="DriveClips")[0], 400)
        status, body = self.get("/api/timeline", cat="DriveClips", event="2026-11-20_08-00-00")
        self.assertEqual((status, json.loads(body)), (200, []))

    def test_current_clip_zip_keeps_its_original_directory(self):
        selected = "2026-11-18_08-02-08"
        status, body = self.get(f"/api/export/DriveClips/{selected}", clip=selected, cams="back", q="orig")
        self.assertEqual(status, 200)
        with ZipFile(io.BytesIO(body)) as archive:
            self.assertEqual(archive.namelist(), [f"{selected}/{selected}-back.mp4"])

    def test_playback_timeline_skips_optional_frame_analysis(self):
        selected = next(iter(self.clips))
        with patch.object(self.viewer, "annotate_timeline_gaps") as annotate:
            status, body = self.get("/api/timeline", cat="DriveClips", event=selected, gaps="0")
        self.assertEqual(status, 200)
        annotate.assert_not_called()
        events = json.loads(body)
        self.assertEqual([event["name"] for event in events], list(self.clips)[:3])
        self.assertTrue(all(video["duration"] > 0 for event in events for video in event["videos"]))
        self.assertFalse(any("gap_before" in video for event in events for video in event["videos"]))

    def test_timeline_keeps_frame_metadata_by_default(self):
        selected = next(iter(self.clips))
        for query in ({}, {"gaps": "1"}):
            with self.subTest(query=query), patch.object(self.viewer, "annotate_timeline_gaps",
                    wraps=self.viewer.annotate_timeline_gaps) as annotate:
                status, body = self.get("/api/timeline", cat="DriveClips", event=selected, **query)
                self.assertEqual(status, 200)
                annotate.assert_called_once()
                self.assertTrue(any("gap_before" in video for event in json.loads(body) for video in event["videos"]))


if __name__ == "__main__":
    unittest.main()
