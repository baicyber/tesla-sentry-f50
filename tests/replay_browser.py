"""Run the viewer with isolated H.264 fixtures, then exercise its public web UI."""
import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import types
import zipfile
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from playwright.sync_api import TimeoutError as BrowserTimeout, expect, sync_playwright


def check_overview_scrubbing(browser, base, viewer, archive, event, key, ffmpeg):
    from urllib.parse import parse_qs, urlsplit
    folder = archive / 'SentryClips' / event
    first = key(0)
    # At 24 fps an encoded preview can end one frame before the source timeline.
    source = folder / (first + '-front.mp4')
    preview = viewer._variant_path(str(source), '360p')
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-i', str(source), '-vf', 'scale=480:360',
                    '-frames:v', '47', '-c:v', 'libx264', '-threads', '1', preview], check=True)
    for camera in ('back', 'left_repeater', 'right_repeater'):
        path = str(folder / (first + '-' + camera + '.mp4'))
        assert viewer.get_variant(path, '360p', wanted=lambda: True) != path
        if camera == 'left_repeater':
            shutil.copyfile(preview, viewer._variant_path(path, '360p'))
    context = browser.new_context(viewport={'width': 1280, 'height': 800}, locale='zh-CN')
    context.add_init_script("localStorage.setItem('tsfq3','360p')")
    page = context.new_page()
    media = []
    page.on('request', lambda r: media.append(r.url) if '/media/' in r.url else None)
    ready = {'front'}

    def delayed(route):
        file = parse_qs(urlsplit(route.request.url).query)['file'][0]
        cam = file.rsplit('-', 1)[-1][:-4]
        if first in file and cam not in ready:
            route.fulfill(json={'state': 'pending'})
        else:
            route.continue_()

    page.route('**/api/playback-source?*', delayed)
    page.goto(base)
    page.locator('#grid .card').first.click()
    page.get_by_role('button', name='暂停', exact=True).click()
    slider = page.locator('#seek')

    def slider_position(seconds):
        rect = slider.bounding_box()
        end = float(slider.get_attribute('max'))
        return {'x':8 + (rect['width'] - 16) * seconds / end, 'y':rect['height'] / 2}

    def drag(seconds):
        slider.drag_to(slider, source_position=slider_position(float(slider.input_value())),
                       target_position=slider_position(seconds), steps=8)

    drag(1.2)
    page.locator('#cambar').get_by_text('全览', exact=True).click()
    page.wait_for_function("activeVideos().find(v=>v.dataset.cam==='front')?.videoHeight===360")
    page.evaluate("window.beforeDrag = activeVideos().slice()")
    drag(.35)
    assert page.evaluate("activeVideos().every((v,i)=>v===beforeDrag[i])"), 'scrub reloaded a playing or pending camera'
    ready.add('left_repeater')
    page.wait_for_function("""activeVideos().filter(v=>v.videoHeight===360&&!v.seeking&&v.paused&&
        Math.abs(v.currentTime-.35)<.1).length===2""")
    page.get_by_role('button', name='播放', exact=True).click()
    drag(1.5)
    page.wait_for_timeout(2200)
    assert page.evaluate("curClipIx===0 && wantedPlay && activeVideos().every(v=>v.paused)"), 'overview advanced past pending cameras'
    ready.update(('back','right_repeater'))
    page.wait_for_function("curClipIx===1 && wantedPlay", timeout=4000)
    page.get_by_role('button', name='暂停', exact=True).click()
    drag(4.6)
    drag(.7)
    page.wait_for_function("""curClipIx===0 && activeVideos().length===4 && activeVideos().every(
        v=>v.videoHeight===360&&v.paused&&!v.seeking&&Math.abs(v.currentTime-.7)<.1)""")
    drag(22.3)
    page.wait_for_function("curClipIx===9 && activeVideos().every(v=>v.videoHeight===360&&!v.seeking)", timeout=30000)
    page.get_by_role('button', name='播放', exact=True).click()
    slider.hover(position=slider_position(22.4))
    page.mouse.down()
    assert page.evaluate('editingSeek')
    slider.hover(position=slider_position(4.4))
    page.wait_for_function("activeVideos().every(v=>v.ended)")
    page.mouse.up()
    page.wait_for_function("curClipIx===2 && wantedPlay && activeVideos().some(v=>!v.paused&&v.currentTime>.4)", timeout=6000)
    assert media and all('q=360p&exact=1' in url for url in media)
    context.close()


def check_review_fixes(browser, base, key):
    page = browser.new_page(viewport={"width": 1280, "height": 800}, locale="zh-CN")
    page.set_default_timeout(3500)
    failures = []

    def check(name, action):
        page.goto(base)
        page.locator("#grid .card").first.wait_for()
        try:
            action()
        except (AssertionError, BrowserTimeout) as error:
            failures.append(f"{name}: {str(error).splitlines()[0]}")

    def open_export():
        page.locator("#grid .card").first.click()
        page.wait_for_function("curClipIx === 0 && activeVideos()[0]?.readyState >= 2")
        page.locator(".pdl").click()

    def zip_current_clip():
        open_export()
        with page.expect_download() as downloaded:
            page.locator("#expp").get_by_role("button", name="打包zip").click()
        with zipfile.ZipFile(downloaded.value.path()) as archive:
            clips = sorted(Path(name).name for name in archive.namelist() if name.endswith(".mp4"))
        assert clips == [f"{key(0)}-{cam}.mp4" for cam in ("back", "front", "left_repeater", "right_repeater")], clips

    def fullscreen_export():
        page.locator("#grid .card").first.click()
        page.wait_for_function("activeVideos()[0]?.readyState >= 2")
        page.locator("#screenbtn").click()
        page.wait_for_function("document.fullscreenElement !== null")
        expect(page.locator('.app-header #play-rate')).to_be_in_viewport()
        expect(page.locator('#tabs')).to_be_hidden()
        page.locator(".pdl").click()
        page.locator("#expp .x").click()
        expect(page.locator("#expp")).to_be_hidden()
        page.locator("#player .pback").click()
        page.wait_for_function("document.fullscreenElement === null")

    def expanded_export():
        page.evaluate("() => { document.documentElement.requestFullscreen = () => Promise.reject(new Error('fullscreen unavailable')); }")
        page.locator("#grid .card").first.click()
        page.wait_for_function("activeVideos()[0]?.readyState >= 2")
        page.locator("#screenbtn").click()
        expect(page.locator("#player.expanded")).to_be_visible()
        expect(page.locator('.app-header #play-bytes')).to_be_in_viewport()
        expect(page.locator('#tabs')).to_be_hidden()
        page.locator(".pdl").click()
        expect(page.locator("#expp")).to_be_visible()
        page.locator("#expp .x").click()
        page.locator("#player .pback").click()
        expect(page.locator("#player")).to_be_hidden()

    def archive_directory():
        page.locator("#gear").click()
        page.locator("#st-archive").click()
        path = page.locator("#dest-dir")
        path.fill("/archive")
        page.get_by_role("button", name="跳转", exact=True).click()
        page.locator("#file-list .n").get_by_text("SentryClips", exact=False).click()
        expect(path).to_have_value("/archive/SentryClips", timeout=1500)
        page.locator("#crumb").get_by_text("archive", exact=True).click()
        expect(path).to_have_value("/archive")

    def empty_filter():
        page.locator("#filters select").select_option(label="车门拉动 · 0")
        expect(page.locator("#filters select option:checked")).to_contain_text("车门拉动", timeout=1500)
        expect(page.locator("#grid .card")).to_have_count(0)
        expect(page.locator("#empty")).to_contain_text("该筛选下暂无事件")
        page.locator("#filters select").select_option("all")
        expect(page.locator("#grid .card")).to_have_count(1)

    def quality_failure_message():
        open_export()
        page.locator("#expq .qbtn").click()
        page.locator("#expq .qmenu").get_by_role("button", name="480p", exact=True).click()
        response = lambda route: route.fulfill(status=503, body="")
        page.route("**/api/export/**", response)
        try:
            page.locator("#expp").get_by_role("button", name="打包zip").click()
            expect(page.locator("#toast")).to_contain_text("所选画质暂不可用", timeout=1500)
            expect(page.locator("#player")).to_be_visible()
        finally:
            page.unroute("**/api/export/**", response)

    try:
        check("ZIP follows the current clip", zip_current_clip)
        check("Export remains usable in fullscreen", fullscreen_export)
        check("Export and back remain usable in expanded mode", expanded_export)
        check("Archive subfolder and breadcrumb", archive_directory)
        check("Empty sentry filter remains selected", empty_filter)
        check("Failed export stays in the player", quality_failure_message)
        assert not failures, "\n" + "\n".join(failures)
        print("Review fixes: ZIP, fullscreen, archive browsing, empty filter and export error PASS")
    finally:
        page.close()


def check_loading_controls(browser, base):
    for category in ("SentryClips", "SavedClips", "RecentClips", "DriveClips", "live"):
        page = browser.new_page(viewport={"width": 1280, "height": 800}, locale="zh-CN")
        held = []
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def hold_timeline(route):
            held.append((route, route.fetch()))

        def pending():
            for _ in range(100):
                if held:
                    return held.pop(0)
                page.wait_for_timeout(50)
            raise AssertionError("timeline request not received")

        try:
            page.goto(base + "/?cat=" + ("RecentClips" if category == "live" else category))
            page.locator("#grid .card").first.wait_for()
            page.route("**/api/timeline?*", hold_timeline)
            if category == "live":
                page.locator("#tab-live").click()
            else:
                page.locator("#grid .card").first.click()
            expect(page.locator("#vgrid")).to_have_text("正在加载录像…")
            button = page.locator("#ppbtn")
            expect(button).to_be_enabled()
            expect(button).to_have_attribute("aria-label", "暂停")
            for label in ("播放", "暂停", "播放"):
                button.click()
                expect(button).to_have_attribute("aria-label", label)
            assert page.evaluate("!wantedPlay && curClips.length===0")
            route, response = pending()
            assert parse_qs(urlsplit(route.request.url).query).get("gaps") == ["0"]
            if category == "live":
                # A paused empty live window must stay paused when recordings arrive.
                route.fulfill(response=response, json=[])
                expect(page.locator("#vgrid")).to_have_text("等待新录像")
                expect(button).to_have_attribute("aria-label", "播放")
                if not held:
                    page.locator("#livep").get_by_text("检查更新", exact=True).click()
                route, response = pending()
            route.fulfill(response=response)
            page.unroute("**/api/timeline?*", hold_timeline)
            page.wait_for_function("curClips.length>0 && activeVideos().length>0 && activeVideos().every(v=>v.readyState>=2&&!v.seeking)")
            assert page.evaluate("!wantedPlay && activeVideos().every(v=>v.paused)")
            paused_at = page.evaluate("activeVideos()[0].currentTime")
            page.wait_for_timeout(250)
            assert abs(page.evaluate("activeVideos()[0].currentTime") - paused_at) < .01
            button.click()
            page.wait_for_function("wantedPlay && activeVideos().some(v=>!v.paused)")
            button.click()
            assert page.evaluate("!wantedPlay && activeVideos().every(v=>v.paused)")
            if category == "SentryClips":
                def fail_timeline(route):
                    route.fulfill(status=503, body="unavailable")

                page.route("**/api/timeline?*", fail_timeline)
                page.locator("#watch-items button").first.click()
                expect(page.locator("#vgrid")).to_contain_text("录像加载失败")
                expect(button).to_be_disabled()
                expect(button).to_have_attribute("aria-label", "播放")
                page.unroute("**/api/timeline?*", fail_timeline)
                page.route("**/api/timeline?*", hold_timeline)
                page.locator("#vgrid").get_by_role("button", name="重试", exact=True).click()
                expect(button).to_be_enabled()
                button.click()
                expect(button).to_have_attribute("aria-label", "播放")
                route, response = pending()
                route.fulfill(response=response)
                page.unroute("**/api/timeline?*", hold_timeline)
                page.wait_for_function("curClips.length>0&&activeVideos().every(v=>v.readyState>=2)")
                assert page.evaluate("!wantedPlay&&activeVideos().every(v=>v.paused)")
            assert not errors, errors
            print(category, "loading controls and deferred playback intent PASS", flush=True)
        finally:
            page.close()


def check_gap_threshold(browser, base, key):
    page = browser.new_page(viewport={"width": 1280, "height": 800}, locale="zh-CN", has_touch=True)
    def timeline(route):
        response = route.fetch()
        events = response.json()
        for event in events:
            event['videos'] = [video for video in event['videos']
                               if not (video['f'][:19] == key(0) and video['cam'] == 'back')]
            for video in event['videos']:
                stamp = video['f'][:19]
                # Deliberately disagree with the seek timeline: markers must follow playable ranges.
                video['gap_before'] = dict(basis='sequence',seconds=120,minimum=120)
                if stamp == key(6) and video['cam'] == 'front':
                    video['gap_before'] = dict(basis='sequence', seconds=.51, minimum=.51, previous=key(4))
                video['duration'] = 2
                if video['cam'] == 'front':
                    video['duration'] = {key(0): 1.51, key(2): 1.5, key(4): 1.49}.get(stamp, 2)
                if stamp == key(21):
                    # A sparse one-hour timeline exercises the 1 px minimum and uncapped large gaps.
                    video['f'] = key(3600) + video['f'][19:]
                    if video['cam'] == 'back':
                        video['duration'] = 1
        route.fulfill(response=response,json=events)
    try:
        page.route('**/api/timeline?*', timeline)
        page.goto(base+'/?'+urlencode(dict(ev='SentryClips/'+key(25),cam='front')))
        page.wait_for_function('curClips.length===10')
        page.get_by_role('button', name='暂停', exact=True).click()
        markers = page.locator('#track-bands .gap')
        for viewport in (1280, 390):
            page.set_viewport_size(dict(width=viewport,height=800))
            for camera, ranges in [('前视',[(5.49,6),(12,15),(21,3600)]),
                                   ('后视',[(0,2),(10,15),(21,3600),(3601,3602)]),
                                   ('全览',[(0,2),(5.49,6),(10,15),(21,3600),(3601,3602)])]:
                page.locator('#cambar').get_by_text(camera,exact=True).click()
                expect(markers).to_have_count(len(ranges))
                track = page.locator('#track-bands').bounding_box()
                for marker, (start,end) in zip(markers.all(),ranges):
                    box = marker.bounding_box()
                    width = max(1,(end-start)/3602*track['width'])
                    right = track['x']+end/3602*track['width']
                    assert abs(box['width']-width)<.1 and abs(box['x']+box['width']-right)<.1, (camera,box,(start,end))
                    assert box['height']==6
                assert max(marker.bounding_box()['width'] for marker in markers.all())>8
                if camera == '前视':
                    assert markers.first.get_attribute('title') == '缺录约 0.51 秒'
                if camera == '全览':
                    assert markers.nth(1).get_attribute('title') == '前视 缺录约 0.51 秒'
                    assert '后视 缺录约 5 秒' in markers.nth(2).get_attribute('title')
        print('Gap mapping PASS: 0.49/0.5 excluded, 0.51 included, actual camera coverage, responsive 1 px minimum and no width cap')
    finally:
        page.close()


def check_drive_trips(browser, base, key):
    page = browser.new_page(viewport={"width": 1280, "height": 800}, locale="zh-CN", has_touch=True)
    try:
        # A date-filtered library must not reopen other days or unrelated trips.
        page.goto(base + "/?" + urlencode(dict(cat="DriveClips", date=key(0)[:10])))
        expect(page.locator("#grid .card")).to_have_count(2)
        expect(page.locator("#grid .card .record-time").last).to_contain_text("–")
        page.locator("#grid .card").last.click()
        page.wait_for_function("libraryCat==='DriveClips' && curClips.length===2 && curClipIx===1")
        assert page.evaluate("Number(document.getElementById('seek').max)") == 5
        # A one-second filename difference alone cannot establish missing footage.
        expect(page.locator("#track-bands .gap")).to_have_count(0)
        expect(page.locator("#watch-items button")).to_have_count(2)
        expect(page.locator("#watch-items button.selected")).to_have_count(1)
        page.wait_for_function("atEnd&&!wantedPlay", timeout=6000)
        assert page.evaluate("cur.name") == key(3)
        # Export must continue using the active minute's physical folder.
        page.locator(".pdl").click()
        with page.expect_download() as downloaded:
            page.locator("#expp").get_by_role("button", name="打包zip").click()
        with zipfile.ZipFile(downloaded.value.path()) as archive:
            assert archive.namelist() == [f"{key(3)}/{key(3)}-front.mp4"]
        # Previously shared minute links still land on that minute in its trip.
        page.goto(base + "/?" + urlencode(dict(ev=f"DriveClips/{key(3)}")))
        page.wait_for_function("curClips.length===2 && curClipIx===1")
        assert page.evaluate("currentClip().key") == key(3)
        # Other trips are available as separate selections.
        page.locator("#watch-items button").first.click()
        page.wait_for_function("curClips.length===1")
        assert page.evaluate("currentClip().key") == key(86400)
        print("Drive trips PASS: date filter, short seams, natural end, clip export and old links")
    finally:
        page.close()


def main(root, ffmpeg, chrome, viewer_path, minute_clips=False):
    archive = root / "archive"
    os.environ.update(TSF_ARCHIVE=str(archive), TSF_CACHE=str(root / "cache"),
                      TSF_SETTINGS=str(root / "settings.json"))
    if os.name == "nt":
        sys.modules["fcntl"] = types.ModuleType("fcntl")  # Linux USB management is never started.
    spec = importlib.util.spec_from_file_location("viewer", viewer_path)
    viewer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(viewer)
    viewer.FFMPEG_BIN = ffmpeg
    viewer.live_status = lambda: {"running": True}
    viewer.ensure_dirs()
    Path(viewer.RES_DIR).mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        # Skip the Linux OOM adjustment. Run the viewer's actual FFmpeg arguments.
        def run(args, **kwargs):
            if args[:2] == ["sh", "-c"] and args[3] == ffmpeg:
                args = args[3:]
            return subprocess.run(args, **kwargs)
        viewer.subprocess = types.SimpleNamespace(run=run)
        if hasattr(viewer, "preview_cores"):
            viewer.preview_cores = lambda: 3
            viewer.run_variant = lambda args, wanted, jobs=None, key=None, **options: bool(wanted()) and run(args, capture_output=True).returncode == 0
    for length in (1, 2):
        subprocess.run([ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=320x240:rate=24", "-t", str(length), "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", "-threads", "1", str(root / f"{length}s.mp4")], check=True)

    start = dt.datetime(2026, 11, 18, 18, 42)
    def key(seconds):
        return (start + dt.timedelta(seconds=seconds)).strftime("%Y-%m-%d_%H-%M-%S")

    def record(cat, folder, second, camera, length=2):
        directory = archive / cat / folder
        directory.mkdir(exist_ok=True, parents=True)
        shutil.copyfile(root / f"{length}s.mp4", directory / (key(second) + "-" + camera + ".mp4"))

    event = key(25)
    cameras = ["front", "back", "left_repeater", "right_repeater"]
    starts = [0, 2, 4, 6, 8, 10, 15, 17, 19, 21]
    for i, second in enumerate(starts):
        for cam in cameras:
            if i == 5 and cam == "back":
                continue
            record("SentryClips", event, second, cam, 1 if i == 4 and cam == "back" else 2)
    metadata = dict(timestamp="2026-11-18T18:42:05", reason="sentry_aware_object_detection", camera="7")
    (archive / "SentryClips" / event / "event.json").write_text(json.dumps(metadata), encoding="utf-8")
    for second in (0, 2):
        record("RecentClips", key(second), second, "front")
    record("SavedClips", key(6), 0, "front")
    record("SavedClips", key(6), 1, "front")
    if "DriveClips" in viewer.CATS:
        for second in (0, 3, 40, 86400):
            record("DriveClips", key(second), second, "front")
    for cam in ("back", "left_repeater", "right_repeater", "left_pillar", "right_pillar"):
        record("SavedClips", key(6), 1, cam)
    if minute_clips:
        long_event = archive / "SavedClips" / key(600)
        long_event.mkdir()
        sample = root / "minute.mp4"
        subprocess.run([ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=1280x960:rate=24", "-t", "60", "-c:v", "libx264",
                        "-preset", "ultrafast", "-b:v", "3500k", "-threads", "3", str(sample)], check=True)
        for index in range(10):
            os.link(sample, long_event / (key(index * 60) + "-front.mp4"))

    class Handler(viewer.Handler):
        def log_message(self, *args):
            pass
    server = viewer.BoundedServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        first = f"/media/SentryClips/{event}/{key(0)}-front.mp4"
        with urlopen(Request(base + first, headers={"Range": "bytes=0-63"})) as response:
            assert response.status == 206 and len(response.read()) == 64
        with urlopen(Request(base + first, method="HEAD")) as response:
            assert response.headers.get_all("Content-Length") == [str((root / "2s.mp4").stat().st_size)]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=chrome, headless=True)
            check_loading_controls(browser, base)
            check_review_fixes(browser, base, key)
            page = browser.new_page(viewport={"width": 1280, "height": 800}, locale="zh-CN", has_touch=True)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.set_default_timeout(8500)
            page.goto(base)
            page.locator("#grid .card").first.click()
            page.wait_for_function("curClipIx === 0 && camSel === 'back' && activeVideos()[0]?.currentTime > 0.05")
            page.locator("#ppbtn").click()
            position = page.evaluate("playbackOffset()")
            page.locator("#cambar").get_by_text("前视", exact=True).click()
            page.wait_for_function("activeVideos()[0]?.readyState >= 2 && !activeVideos()[0].seeking")
            assert page.evaluate("activeVideos().every(v => v.paused)")
            assert abs(page.evaluate("playbackOffset()") - position) < .15
            assert page.locator("#seek").get_attribute("max") == "23"
            assert page.locator("#track-bands .gap").count() == 1
            gap = page.locator("#track-bands .gap").bounding_box()
            track = page.locator('#track-bands').bounding_box()
            assert abs(gap['width']-3/23*track['width'])<.1 and gap['width']>8
            assert abs(gap['x']-track['x']-12/23*track['width'])<.1
            assert abs(gap['x']+gap['width']-track['x']-15/23*track['width'])<.1
            gap_x, gap_y = gap['x'] + gap['width'] / 2, gap['y'] + gap['height'] / 2
            page.mouse.move(gap_x, gap_y)
            expect(page.locator('#seek')).to_have_attribute('title', '缺录约 3 秒')
            page.mouse.click(gap_x, gap_y)
            expect(page.locator('#toast')).to_have_text('缺录约 3 秒')
            page.wait_for_function('curClipIx===6 && clipOffset===0 && !wantedPlay')
            page.touchscreen.tap(gap_x, gap_y)
            expect(page.locator('#toast')).to_have_text('缺录约 3 秒')
            page.mouse.move(track['x']+2, gap_y)
            expect(page.locator('#seek')).to_have_attribute('title', '')

            def seek(seconds):
                page.locator("#seek").evaluate("""(e, value) => {
                    e.value = value; e.dispatchEvent(new Event('input')); e.dispatchEvent(new Event('change'));
                }""", seconds)
            seek(12.5)
            page.wait_for_function("curClipIx === 6 && clipOffset === 0")
            expect(page.locator('#toast')).to_have_text('跳过 缺录约 3 秒')
            seek(22.6)
            page.wait_for_function("curClipIx === 9 && activeVideos()[0]?.readyState >= 2 && !activeVideos()[0].seeking")
            page.locator("#skip-back").click()
            page.wait_for_function("curClipIx === 5 && clipOffset > 1.8")
            page.locator("#trigger-marker").click()
            page.wait_for_function("curClipIx === 2 && Math.abs(clipOffset-1)<0.15")
            page.locator("#cambar").get_by_text("全览", exact=True).click()
            seek(10.4)
            assert page.locator("#vgrid video").count() == 3
            assert page.locator("#vgrid").get_by_text("此时段无画面").count() == 1
            page.locator("#cambar").get_by_text("前视", exact=True).click()
            seek(0)
            page.wait_for_function("activeVideos()[0]?.readyState >= 2")
            expect(page.locator('#toast')).to_be_hidden()
            page.evaluate("""() => {
                window.gapNotices = [];
                const notice = document.getElementById('toast');
                new MutationObserver(() => {
                    if (getComputedStyle(notice).display !== 'none') gapNotices.push(notice.textContent);
                }).observe(notice, {attributes:true,childList:true,subtree:true});
            }""")
            page.locator("#ppbtn").click()
            page.evaluate("""() => {
                window.displayed = [];
                function sample() {
                    const v = activeVideos()[0];
                    if (v && v !== window.lastSampled) {
                        window.lastSampled = v;
                        const ix = curClipIx;
                        function frame(now) {
                            window.displayed.push([ix, now]);
                            if (v.isConnected) v.requestVideoFrameCallback(frame);
                        }
                        v.requestVideoFrameCallback(frame);
                    }
                    if (!atEnd) requestAnimationFrame(sample);
                }
                sample();
            }""")
            page.wait_for_function("atEnd", timeout=45000)
            assert not page.evaluate('gapNotices'), 'normal playback displayed a missing-video notice'
            frames = page.evaluate("window.displayed")
            assert sorted({ix for ix, _ in frames}) == list(range(10)), {ix for ix, _ in frames}
            gaps = [round(b[1] - a[1]) for a, b in zip(frames, frames[1:]) if b[0] != a[0]]
            print("10 decoded clips; boundary frame intervals (ms):", gaps, "median:", statistics.median(gaps))
            seek(6.5)
            page.locator("#spdbtn").click()
            page.locator("#cambar").get_by_text("后视", exact=True).click()
            page.wait_for_function("activeVideos()[0]?.readyState >= 2 && activeVideos()[0].playbackRate === 2")
            page.locator("#ic-fold").click()
            assert page.locator("#clipbar button:visible").count() == 10
            page.locator("#ic-fold").click()
            assert not list(Path(viewer.RES_DIR).glob("*.mp4"))
            warm_file = archive / page.evaluate("curVids().find(f=>f.cam===camSel).url.slice('/media/'.length)")
            assert viewer.get_variant(str(warm_file), '360p', wanted=lambda: True) != str(warm_file)
            quality_requests = []
            page.on("request", lambda request: quality_requests.append(request.url) if "/media/" in request.url and "/api/" not in request.url else None)
            page.locator("#qsel .qbtn").click()
            page.locator("#qsel .qmenu").get_by_text("360p", exact=True).click()
            page.wait_for_function("activeVideos()[0]?.videoHeight === 360", timeout=30000)
            variant = page.locator("#vgrid video").get_attribute("src")
            assert "exact=1" in variant
            assert quality_requests and all("exact=1" in url for url in quality_requests)
            with urlopen(Request(base + variant, method="HEAD")) as head:
                with urlopen(Request(base + variant, headers={"Range":"bytes=0-31"})) as segment:
                    assert segment.status == 206 and len(segment.read()) == 32
                    assert segment.headers["Content-Range"].endswith("/" + head.headers["Content-Length"])
            assert page.evaluate("activeVideos()[0].paused")
            page.locator("#qsel .qbtn").click()
            page.locator("#qsel .qmenu").get_by_text("原画", exact=True).click()
            page.wait_for_function("activeVideos()[0]?.videoHeight === 240")
            for width, height in ((1280, 800), (390, 844), (320, 640), (844, 390)):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_function("byId('vgrid').clientWidth > 100")
                assert page.evaluate("byId('player').scrollWidth <= innerWidth")
                assert page.locator(".watch-list").is_visible() == (width >= 1000)
                assert page.locator("#ppbtn").is_visible()
                page.screenshot(path=str(root / f"replay-{width}.png"))
            page.locator(".pback").click()
            page.locator("#tabs .tab").nth(1).click()
            page.locator("#grid .card").last.click()
            page.locator("#cambar").get_by_text("全览", exact=True).click()
            assert page.evaluate("curClips[0].length") == 2
            assert page.evaluate("locateWall(curClips[0].start+1.4).ix") == 0
            seek(2.4)
            page.wait_for_function("activeVideos().length === 6 && activeVideos().every(v => v.readyState >= 2)")
            page.locator(".pback").click()
            page.locator("#tabs .tab").nth(2).click()
            page.locator("#grid .card").last.click()
            page.wait_for_function("curClips.length === 2 && curClipIx === 1")
            page.locator(".pback").click()
            if "DriveClips" in viewer.CATS:
                page.locator("#tabs .tab").nth(3).click()
                page.locator("#grid .card").last.click()
                page.wait_for_function("curClips.length === 2 && curClipIx === 1 && libraryCat === 'DriveClips'")
                page.locator(".pback").click()
            page.locator("#tab-live").click()
            page.wait_for_function("liveMode && curClipIx === 1 && activeVideos()[0]?.readyState >= 2")
            seek(.5)
            page.locator("#ppbtn").click()
            page.wait_for_function("!wantedPlay && !followLatest")
            before = page.evaluate("clipOffset")
            page.locator("#ic-fold").click()
            record("RecentClips", key(4), 4, "front")
            page.locator("#livep").get_by_text("检查更新", exact=True).click()
            page.wait_for_function("curClips.length === 3")
            assert page.locator("#clipbar button").last.inner_text() == "18:42:04"
            assert page.evaluate("curClipIx === 0 && !wantedPlay && !followLatest")
            assert abs(page.evaluate("clipOffset") - before) < .15
            page.locator("#lv-back").click()
            page.wait_for_function("followLatest && atEnd && curClipIx === 2")
            record("RecentClips", key(6), 6, "front")
            page.locator("#livep").get_by_text("检查更新", exact=True).click()
            page.wait_for_function("curClipIx === 3 && activeVideos()[0]?.currentTime > 0.05")
            page.locator("#ppbtn").click()
            record("RecentClips", key(0), 0, "back")
            page.locator("#livep").get_by_text("检查更新", exact=True).click()
            page.locator("#cambar").get_by_text("后视", exact=True).click()
            assert page.locator("#vgrid").get_by_text("此时段无画面").is_visible()
            record("RecentClips", key(6), 6, "back")
            page.locator("#livep").get_by_text("检查更新", exact=True).click()
            page.wait_for_function("activeVideos()[0]?.dataset.cam === 'back' && activeVideos()[0]?.readyState >= 2")
            assert page.evaluate("!wantedPlay && activeVideos()[0].paused && curClipIx === 3")
            if minute_clips:
                page.locator(".pback").click()
                page.locator("#tabs .tab").nth(1).click()
                page.locator("#grid .card").first.click()
                page.wait_for_function("curClips.length === 10 && activeVideos()[0]?.videoHeight === 960")
                page.wait_for_function("activeVideos()[0]?.currentTime > 0.05")
                assert abs(float(page.locator("#seek").get_attribute("max")) - 600) < .05
                while page.locator("#spdbtn").inner_text() != "4×":
                    page.locator("#spdbtn").click()
                page.evaluate("""() => {
                    window.minuteClips = new Set();
                    window.minuteTimer = setInterval(() => {
                        const video = activeVideos()[0];
                        if (video && video.currentTime > .1 && !video.paused && video.videoHeight === 960)
                            window.minuteClips.add(curClipIx);
                    }, 100);
                }""")
                page.wait_for_function("atEnd", timeout=200000)
                assert page.evaluate("window.minuteClips.size") == 10
                page.evaluate("clearInterval(minuteTimer)")
                print("10 x 60 s, 1280x960 H.264, original playback at 4x: PASS")
            assert not errors, errors
            check_overview_scrubbing(browser, base, viewer, archive, event, key, ffmpeg)
            check_drive_trips(browser, base, key)
            check_gap_threshold(browser, base, key)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--chrome", required=True)
    parser.add_argument("--output")
    parser.add_argument("--viewer", type=Path, default=Path(__file__).resolve().parents[1] / "sentry-viewer.py")
    parser.add_argument("--minute-clips", action="store_true")
    args = parser.parse_args()
    if args.output:
        root = Path(args.output)
        root.mkdir(parents=True, exist_ok=False)
        print("Evidence:", root, flush=True)
        main(root, args.ffmpeg, args.chrome, args.viewer, args.minute_clips)
    else:
        with tempfile.TemporaryDirectory() as directory:
            main(Path(directory), args.ffmpeg, args.chrome, args.viewer, args.minute_clips)
