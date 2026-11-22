"""Exercise cold playback through each viewer entry, with real HTTP and H.264 frames."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import types
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright


def drag_timeline(page, target):
    slider = page.locator('#seek')
    box = slider.bounding_box()
    maximum = float(slider.get_attribute('max'))

    def x(seconds):
        return box['x'] + 8 + (box['width'] - 16) * seconds / maximum

    y = box['y'] + box['height'] / 2
    page.mouse.move(x(float(slider.input_value())), y)
    page.mouse.down()
    page.mouse.move(x(target), y, steps=8)
    page.mouse.up()


def check_paused_seek_runway(browser, base, quality):
    page = browser.new_page(locale='zh-CN', viewport={'width':1280,'height':800})
    held, aborted = [], []
    def is_runway(url):
        query = parse_qs(urlsplit(url).query)
        return ('/2026-11-19_10-01-00-front.mp4' in query.get('file',[''])[0]
                and query.get('part') == ['27'])
    def intercept(route):
        if is_runway(route.request.url): held.append(route)
        else: route.continue_()
    try:
        page.add_init_script(f"localStorage.setItem('tsfq3','{quality}')")
        page.route('**/api/playback-preview/part.mp4?*', intercept)
        page.on('requestfailed',lambda request: aborted.append(request.url) if is_runway(request.url) else None)
        page.goto(base)
        page.locator('#grid .card').first.click()
        page.locator('#cambar').get_by_text('前视',exact=True).click()
        page.get_by_role('button',name='暂停',exact=True).click()
        drag_timeline(page,85.8)
        page.wait_for_function("curClipIx===1 && activeVideos()[0]?.readyState>=2 && !activeVideos()[0].seeking")
        page.get_by_role('button',name='播放',exact=True).click()
        for _ in range(100):
            if held: break
            page.wait_for_timeout(50)
        assert held,'startup runway was not requested'
        page.wait_for_timeout(800)
        assert not aborted,'needed startup runway was cancelled by its own fill timer'
        for route in held: route.continue_()
        page.unroute('**/api/playback-preview/part.mp4?*',intercept)
        page.wait_for_function('!activeVideos()[0].paused && activeVideos()[0].currentTime>28')
        print('Paused seek resume PASS: delayed required runway remains active')
    finally:
        page.close()


def check_tiny_preview_cut(browser, base, quality):
    page=browser.new_page(locale='zh-CN',viewport={'width':1280,'height':800})
    try:
        page.add_init_script(f"localStorage.setItem('tsfq3','{quality}')")
        page.add_init_script("""(() => {
            window.injectCut=false; window.shiftedCut=false;
            const create=MediaSource.prototype.addSourceBuffer;
            MediaSource.prototype.addSourceBuffer=function(...args) {
                const buffer=create.apply(this,args), append=buffer.appendBuffer.bind(buffer);
                buffer.appendBuffer=function(data) {
                    if(window.injectCut&&!window.shiftedCut&&this.timestampOffset===13) {
                        this.timestampOffset=13.01; window.shiftedCut=true;
                    }
                    return append(data);
                };
                return buffer;
            };
        })()""")
        page.goto(base)
        page.locator('#grid .card').first.click()
        page.locator('#cambar').get_by_text('全览',exact=True).click()
        page.get_by_role('button',name='暂停',exact=True).click()
        page.evaluate("injectCut=true; seekWall(curClips[1].start+13)")
        page.wait_for_function('shiftedCut && activeVideos().length===4 && activeVideos().every(v=>v.readyState>=2&&!v.seeking)')
        page.get_by_role('button',name='播放',exact=True).click()
        page.wait_for_function('activeVideos().every(v=>!v.paused&&v.currentTime>15)')
        times=page.evaluate('activeVideos().map(v=>v.currentTime)')
        assert max(times)-min(times)<.15,times
        print('Preview cut offset PASS: one camera starts 10 ms late; four views recover together')
    finally:
        page.close()


def check_seek_promotes_inflight_read_ahead(browser, base, quality):
    page=browser.new_page(locale='zh-CN',viewport={'width':1280,'height':800})
    held=[]
    promoted=[]
    aborted=[]
    def intercept(route):
        query=parse_qs(urlsplit(route.request.url).query)
        if (not held and query.get('ahead')==['1'] and 'prime' not in query
                and 'cache_only' not in query and '-front.mp4' in query.get('file',[''])[0]):
            held.append((route,query))
            return
        if held and query.get('file')==held[0][1]['file'] and query.get('part')==held[0][1]['part'] and query.get('ahead')==['0']:
            promoted.append(query)
        route.continue_()
    try:
        page.add_init_script(f"localStorage.setItem('tsfq3','{quality}')")
        page.route('**/api/playback-preview/part.mp4?*',intercept)
        page.on('requestfailed',lambda request:aborted.append(request.url))
        page.goto(base)
        page.locator('#grid .card').first.click()
        page.locator('#cambar').get_by_text('前视',exact=True).click()
        for _ in range(150):
            if held:break
            page.wait_for_timeout(50)
        assert held,'ordinary read-ahead was not requested'
        target=int(held[0][1]['part'][0])+.2
        page.evaluate('target=>seekWall(currentClip().start+target)',target)
        for _ in range(100):
            if promoted:break
            page.wait_for_timeout(50)
        assert len(promoted)==1,'seek must promote its existing low-priority request exactly once'
        assert held[0][0].request.url in aborted,'obsolete request was not cancelled'
        page.wait_for_function('target=>!activeVideos()[0].paused&&activeVideos()[0].currentTime>target+.3',arg=target)
        print('Seek priority PASS: in-flight read-ahead promoted once at the selected position')
    finally:
        page.close()


def check_stale_waiting_event(browser, base, quality):
    page=browser.new_page(locale='zh-CN',viewport={'width':1280,'height':800})
    try:
        page.add_init_script(f"localStorage.setItem('tsfq3','{quality}')")
        page.goto(base)
        page.locator('#grid .card').first.click()
        page.locator('#cambar').get_by_text('全览',exact=True).click()
        page.wait_for_function('activeVideos().length===4&&activeVideos().every(v=>v.readyState>=3&&!v.paused)')
        state=page.evaluate("""() => {
            activeVideos()[0].dispatchEvent(new Event('waiting'));
            return activeVideos().map(v=>({ready:v.readyState,paused:v.paused}));
        }""")
        assert all(not v['paused'] for v in state),('already recovered waiting event paused the group',state)
        page.evaluate('window.oldCamera=activeVideos()[0]; showClip(1,8)')
        page.wait_for_function('curClipIx===1&&activeVideos().length===4&&activeVideos().every(v=>v.readyState>=3&&!v.paused)')
        state=page.evaluate("""() => {
            oldCamera.removeAttribute('src');oldCamera.load();
            oldCamera.dispatchEvent(new Event('waiting'));
            return activeVideos().map(v=>v.paused);
        }""")
        assert not any(state),'a detached camera must not pause the new group'
        page.wait_for_function('activeVideos().every(v=>!v.paused&&v.currentTime>9)')
        print('Stale waiting PASS: recovered and detached cameras cannot pause the active group')
    finally:
        page.close()


def check_seek_startup_buffer(browser, base, quality):
    page = browser.new_page(locale='zh-CN', viewport={'width': 1280, 'height': 800})
    height = int(quality[:-1])
    held_parts = []
    first_part_requests = []
    delay_next_part = True

    def route_part(route):
        query = parse_qs(urlsplit(route.request.url).query)
        if query.get('part') == ['12'] and '/2026-11-19_10-01-00-' in query.get('file', [''])[0]:
            first_part_requests.append(query)
        if (delay_next_part and query.get('part') == ['13'] and
                '/2026-11-19_10-01-00-' in query.get('file', [''])[0]):
            held_parts.append(route)
        else:
            route.continue_()

    try:
        page.add_init_script(f"localStorage.setItem('tsfq3','{quality}')")
        page.route(lambda url: urlsplit(url).path == '/api/playback-preview/part.mp4', route_part)
        page.goto(base)
        page.locator('#grid .card').first.click()
        page.wait_for_function('height => activeVideos()[0]?.videoHeight === height && !activeVideos()[0].paused', arg=height)
        page.evaluate("""() => {
            window.seekPlaybackEvents = [];
            // Observe the new cell before first paint, even on a fast development host.
            new MutationObserver(() => {
                if (curClipIx !== 1 || window.seekInitialStatus !== undefined) return;
                const label = document.querySelector('#vgrid .cload');
                if (label) window.seekInitialStatus = getComputedStyle(label).visibility;
            }).observe(document.querySelector('#vgrid'), {childList:true});
            for (const type of ['playing', 'waiting']) {
                document.addEventListener(type, event => {
                    if (curClipIx === 1 && event.target.matches('#vgrid video')) {
                        seekPlaybackEvents.push([type, event.target.currentTime]);
                    }
                }, true);
            }
        }""")
        # Land near the end of one encoded second in a cold part of the next clip.
        drag_timeline(page, 72.8)
        page.wait_for_function("""height => {
            const video = activeVideos()[0];
            return curClipIx === 1 && video?.videoHeight === height && video.buffered.length &&
                video.buffered.start(0) <= 12.9 && Math.abs(video.buffered.end(0) - 13) < .05;
        }""", arg=height)
        page.wait_for_timeout(900)
        assert page.evaluate('seekInitialStatus') == 'hidden', 'transient buffering flashed over the frame'
        assert held_parts, 'the next second was not held'
        result = page.evaluate("""() => {
            const video = activeVideos()[0], label = document.querySelector('.cload');
            return {wantedPlay, paused: video.paused, time: video.currentTime,
                    label: label.checkVisibility({visibilityProperty:true}) ? label.textContent : '', events: seekPlaybackEvents};
        }""")
        assert result['wantedPlay'] and result['paused'] and result['label'] == '缓冲中…', result
        assert not any(name == 'playing' for name, _ in result['events']), result
        assert any(q.get('prime') == ['1'] and q.get('ahead') == ['0'] for q in first_part_requests), first_part_requests

        delay_next_part = False
        for route in held_parts:
            route.continue_()
        held_parts.clear()
        page.wait_for_function("t => !activeVideos()[0].paused && activeVideos()[0].currentTime > t + 1.5",
                               arg=result['time'])
        events = page.evaluate('seekPlaybackEvents.map(([type]) => type)')
        assert 'playing' in events and 'waiting' not in events[events.index('playing') + 1:], events

        # Seeking while paused should show the target frame and preserve the pause state.
        page.get_by_role('button', name='暂停', exact=True).click()
        drag_timeline(page, 87.8)
        page.wait_for_function("""activeVideos()[0].paused && !activeVideos()[0].seeking &&
            activeVideos()[0].readyState >= 2 && Math.abs(activeVideos()[0].currentTime - 27.8) < .35""")

        # A paused seek exactly onto the buffered end needs the following part.
        # The previous part's final timestamp is not a decodable target frame.
        page.wait_for_function("""activeVideos()[0].buffered.length &&
            Math.abs(activeVideos()[0].buffered.end(activeVideos()[0].buffered.length-1)-28)<.01""")
        slider = page.locator('#seek')
        steps = round((88 - float(slider.input_value())) / .05)
        for _ in range(abs(steps)):
            slider.press('ArrowRight' if steps > 0 else 'ArrowLeft')
        page.wait_for_function("""activeVideos()[0].paused && activeVideos()[0].readyState>=2 &&
            !activeVideos()[0].seeking && Math.abs(activeVideos()[0].currentTime-28)<.02""", timeout=8000)
        assert not page.locator('#vgrid .cload:visible').count()

        # The startup threshold cannot exceed the amount of video left in a clip.
        page.get_by_role('button', name='播放', exact=True).click()
        drag_timeline(page, 119.6)
        page.wait_for_function("""height => curClipIx === 2 && activeVideos()[0]?.videoHeight === height &&
            !activeVideos()[0].paused && activeVideos()[0].currentTime > .2""", arg=height)
        print(f'{quality} cold seek startup PASS: delayed next part, paused seek, and short clip remainder')
    finally:
        delay_next_part = False
        for route in held_parts:
            route.abort()
        page.close()


def check_preview_tail_seek(browser, base, quality):
    """Cached clip tails must still finish after seeks append earlier fragments."""
    for camera in ('front', 'all'):
        page = browser.new_page(locale='zh-CN', viewport={'width': 1280, 'height': 800})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        tail_requests = []
        page.on('request', lambda request: tail_requests.append(request.url)
                if '/api/playback-preview/part.mp4?' in request.url and
                parse_qs(urlsplit(request.url).query).get('part') == ['59'] and
                'cache_only' not in parse_qs(urlsplit(request.url).query) else None)
        try:
            page.add_init_script(f"localStorage.setItem('tsfq3','{quality}')")
            page.goto(base + '/?' + urlencode(dict(ev='DriveClips/2026-11-19_10-00-00', cam=camera)))
            count = 4 if camera == 'all' else 1
            page.wait_for_function("""count => activeVideos().length === count &&
                activeVideos().every(v => !v.paused && v.currentTime > .2)""", arg=count)
            for cycle in range(2):
                # Cache the tail, then append an earlier unbuffered range to reopen MSE.
                drag_timeline(page, 114)
                page.wait_for_function("""curClipIx === 1 && activeVideos().every(v =>
                    v.buffered.length && v.buffered.end(v.buffered.length - 1) >= v.duration - .02)""")
                if cycle:
                    page.get_by_role('button', name='暂停', exact=True).click()
                drag_timeline(page, 96 if cycle else 106)
                target = 36 if cycle else 46
                page.wait_for_function("""target => activeVideos().every(v => !v.seeking &&
                    v.readyState >= 2 && v.currentTime >= target - .3 && v.currentTime < target + 2)""", arg=target)
                if cycle:
                    assert page.evaluate('!wantedPlay && activeVideos().every(v => v.paused)')
                    page.get_by_role('button', name='播放', exact=True).click()
                drag_timeline(page, 111.75)
                try:
                    page.wait_for_function("""curClipIx === 2 && activeVideos().every(v =>
                        !v.paused && !v.seeking && v.currentTime > .4)""", timeout=22000)
                except PlaywrightTimeout:
                    state = page.evaluate("""({clip:curClipIx, videos:activeVideos().map(v => ({
                        time:v.currentTime, duration:v.duration, paused:v.paused, ended:v.ended,
                        ready:v.readyState, buffered:Array.from({length:v.buffered.length},
                            (_,i) => [v.buffered.start(i),v.buffered.end(i)])}))})""")
                    raise AssertionError(f'{camera} cached tail did not advance: {state}') from None
                times = page.evaluate('activeVideos().map(v => v.currentTime)')
                assert max(times) - min(times) < .15, times
            page.get_by_role('button', name='暂停', exact=True).click()
            page.locator('#seek').press('End')
            page.wait_for_function("""curClipIx===2 && activeVideos().every(v=>v.paused &&
                v.readyState>=2 && !v.seeking && v.currentTime>59.99)""")
            page.wait_for_timeout(150)
            requests_at_end = len(tail_requests)
            page.wait_for_timeout(600)
            assert len(tail_requests) == requests_at_end, 'paused tail repeatedly fetched the final part'
            page.get_by_role('button', name='播放', exact=True).click()
            page.wait_for_function('atEnd')
            requests_at_end = len(tail_requests)
            page.wait_for_timeout(600)
            assert len(tail_requests) == requests_at_end, 'finished clip repeatedly fetched the final part'
            assert not errors, errors
            print(f'{quality} {camera} tail seeks PASS: repeated rewind, paused seek, clip transitions and no tail request loop')
        finally:
            page.close()


def open_live_overview(browser, base, camera_count, quality='orig', init_script=None, camera='all'):
    page = browser.new_page(locale='zh-CN', viewport={'width': 1280, 'height': 800})
    page.add_init_script(f"localStorage.setItem('tsfliveq','{quality}');localStorage.setItem('tsflivecam','{camera}')")
    if init_script:
        page.add_init_script(init_script)
    page.goto(base)
    page.locator('#tab-live').click()
    page.locator('#lv-iv').select_option('0')
    page.wait_for_function("""count => followLatest && activeVideos().length === count &&
        activeVideos().every(v => v.videoHeight && !v.paused && v.currentTime > .4)""", arg=camera_count)
    return page


def check_stalled_live_camera(browser, base):
    page = open_live_overview(browser, base, 4)
    try:
        result = page.evaluate("""async () => {
            const [stalled, ...healthy] = activeVideos();
            stalled.pause();
            const stoppedAt = stalled.currentTime;
            Object.defineProperty(stalled, 'readyState', {configurable:true, get: () => 2});
            stalled.dispatchEvent(new Event('waiting'));
            const timeline = () => Number(document.getElementById('seek').value);
            const start = timeline(), startTimes = healthy.map(v => v.currentTime);
            let previous = start, regressed = false;
            const until = performance.now() + 2400;
            while (performance.now() < until) {
                await new Promise(requestAnimationFrame);
                const value = timeline();
                if (previous - value > .055) regressed = true;
                previous = value;
            }
            return {timelineDelta: timeline() - start, regressed, stoppedAt,
                advancing: healthy.map((v, i) => v.currentTime - startTimes[i])};
        }""")
        assert abs(result['timelineDelta']) < .2, result
        assert max(result['advancing']) < .2, result
        page.get_by_role('button', name='暂停', exact=True).click()
        page.evaluate("delete activeVideos()[0].readyState")
        page.wait_for_timeout(400)
        assert page.evaluate("!wantedPlay && activeVideos().every(v=>v.paused)")
        page.get_by_role('button', name='播放', exact=True).click()
        page.wait_for_function("activeVideos().every(v => !v.paused && !v.seeking)")
        page.wait_for_timeout(900)
        times = page.evaluate("activeVideos().map(v=>v.currentTime)")
        assert min(times) > result['stoppedAt'] + .5 and max(times) - min(times) < .15, times
    finally:
        page.close()


def check_late_live_camera(browser, base, missing_cameras, automatic=False):
    pending_cameras = [(camera, camera.with_suffix('.pending')) for camera in missing_cameras]
    existing_count = 4 - len(pending_cameras)
    page = None
    try:
        for camera, pending in pending_cameras:
            camera.rename(pending)
        page = open_live_overview(browser, base, existing_count)
        page.evaluate("""() => {
            window.existingLiveVideos = activeVideos().map(video => ({video, time: video.currentTime}));
        }""")
        if automatic:
            with page.expect_response(lambda response: urlsplit(response.url).path == '/api/live/status'):
                page.locator('#lv-iv').select_option('3')
            # Let the initial poll cache the partial group's state before the next camera arrives.
            page.wait_for_function('liveSignature && !liveReading')
        camera, pending = pending_cameras[0]
        pending.rename(camera)
        if not automatic:
            page.locator('#livep').get_by_text('检查更新', exact=True).click()
        try:
            page.wait_for_function("""count => activeVideos().length === count &&
                activeVideos().every(v => v.videoHeight && !v.paused)""", arg=existing_count + 1, timeout=6500)
        except PlaywrightTimeout:
            visible_cameras = page.evaluate('activeVideos().map(v => v.dataset.cam)')
            raise AssertionError(f'expected {existing_count + 1} playing cameras, got {visible_cameras}') from None
        result = page.evaluate("""() => {
            const videos = activeVideos();
            const times = videos.map(v => v.currentTime);
            return {sameVideos: existingLiveVideos.every(({video}) => videos.includes(video)),
                keptPlaying: existingLiveVideos.every(({video, time}) => video.currentTime >= time),
                skew: Math.max(...times) - Math.min(...times), following: followLatest};
        }""")
        assert result['sameVideos'] and result['keptPlaying'], result
        assert result['skew'] < .45 and result['following'], result
    finally:
        if page is not None:
            page.close()
        for camera, pending in pending_cameras:
            if pending.exists():
                pending.rename(camera)


def check_manual_refresh_during_seek(browser, base):
    page = open_live_overview(browser, base, 4)
    held, requests = [], []
    def intercept(route):
        query = parse_qs(urlsplit(route.request.url).query)
        if query.get('gaps') != ['0']: return route.continue_()
        requests.append(route.request.url)
        if len(requests) == 1: held.append((route,route.fetch()))
        else: route.continue_()
    try:
        page.get_by_role('button',name='暂停',exact=True).click()
        page.route('**/api/timeline?*',intercept)
        page.locator('#livep').get_by_text('检查更新',exact=True).click()
        for _ in range(60):
            if held: break
            page.wait_for_timeout(50)
        assert held
        box=page.locator('#seek').bounding_box()
        page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2)
        page.mouse.down()
        route,response=held[0]
        route.fulfill(response=response)
        page.wait_for_function('editingSeek&&liveDirty&&!liveReading')
        page.mouse.up()
        page.wait_for_function('!editingSeek&&!liveDirty&&!liveReading')
        assert len(requests)>=2,'manual refresh was stranded after the seek gesture'
        assert page.evaluate('!wantedPlay')
    finally:
        page.close()


def check_live_regressions(browser, base, latest_cameras):
    failures = []
    tests = [('manual refresh during seek',lambda:check_manual_refresh_during_seek(browser,base)),
             ('one camera stops advancing', lambda: check_stalled_live_camera(browser, base)),
             ('fourth camera arrives late', lambda: check_late_live_camera(browser, base, latest_cameras[-1:])),
             ('poll notices another camera in a partial group',
              lambda: check_late_live_camera(browser, base, latest_cameras[1:], automatic=True))]
    for name, test in tests:
        try:
            test()
        except AssertionError as error:
            failures.append(f'{name}: {error}')
    assert not failures, '\n'.join(failures)
    print('Live regressions PASS: all cameras wait for a stalled camera and resume together')


def check_live_prefetch(browser, base, root, recent, cameras, short_sample, camera='all'):
    minute = '04' if camera == 'all' else '05'
    short_stamp, next_stamp = f'2026-11-19_10-{minute}-00', f'2026-11-19_10-{minute}-08'
    visible_cameras = cameras if camera == 'all' else (camera,)
    short_group, next_group = recent / short_stamp, recent / next_stamp
    pending_group = root / 'pending-prefetch-fixture'
    for folder, stamp in ((short_group, short_stamp), (pending_group, next_stamp)):
        folder.mkdir()
        for file_camera in cameras:
            shutil.copyfile(short_sample, folder / f'{stamp}-{file_camera}.mp4')
    trace = """(() => {
        const fetchPart = window.fetch;
        window.warmHistory = [];
        window.fetch = (request, ...args) => {
            const url = new URL(request instanceof Request ? request.url : request, location.href);
            const response = fetchPart(request, ...args);
            if (url.searchParams.get('cache_only') !== '1') return response;
            const record = {file: url.searchParams.get('file'), requestedIn: currentClip().key};
            window.warmHistory.push(record);
            return response.then(result => {
                Object.assign(record, {completedIn: currentClip()?.key, status: result.status,
                    next: Number(result.headers.get('X-Preview-Next-Part'))});
                return result;
            });
        };
    })()"""
    page = open_live_overview(browser, base, len(visible_cameras), quality='360p', init_script=trace, camera=camera)
    try:
        page.wait_for_function("""activeVideos().every(v => v.currentTime > 3 && v.buffered.length &&
            v.buffered.end(v.buffered.length - 1) >= v.duration - .12)""")
        page.get_by_role('button', name='暂停', exact=True).click()
        pending_group.rename(next_group)
        page.locator('#livep').get_by_text('检查更新', exact=True).click()
        page.wait_for_function("key => curClips.some(clip => clip.key === key) && !wantedPlay && !followLatest", arg=next_stamp)
        page.wait_for_timeout(400)
        assert not page.evaluate('window.warmHistory'), 'paused live review requested speculative parts'
    finally:
        page.close()
        if next_group.is_dir():
            next_group.rename(pending_group)

    page = open_live_overview(browser, base, len(visible_cameras), quality='360p', init_script=trace, camera=camera)
    try:
        deferred = []
        if camera != 'all':
            def defer_last_batch(route):
                query = parse_qs(urlsplit(route.request.url).query)
                if (next_stamp in query.get('file', [''])[0] and query.get('cache_only') == ['1']
                        and query.get('part') == ['6'] and not deferred):
                    deferred.append(route)
                else:
                    route.continue_()

            page.route('**/api/playback-preview/part.mp4?*', defer_last_batch)
        pending_group.rename(next_group)
        page.locator('#livep').get_by_text('检查更新', exact=True).click()
        page.wait_for_function("""({key, count}) => currentClip().key === key && activeVideos().length === count &&
            activeVideos().every(v => v.videoHeight === 360 && v.currentTime > .2)""",
                               arg=dict(key=next_stamp, count=len(visible_cameras)))
        if camera != 'all':
            assert deferred, 'the last batch was not requested before the switch'
            deferred[0].continue_()
            page.wait_for_function("""({before, after}) => warmHistory.some(item =>
                item.requestedIn === before && item.completedIn === after &&
                item.status === 204 && item.next === 8)""", arg=dict(before=short_stamp, after=next_stamp))
            print('Live prefetch PASS: single camera finished the pending batch after transition')
            return
        history = page.evaluate('window.warmHistory')
        expected = {f'/media/RecentClips/{next_stamp}/{next_stamp}-{cam}.mp4' for cam in visible_cameras}
        complete_before_switch = {request['file'] for request in history if f'/{next_stamp}/' in request['file'] and
                                  request.get('status') == 204 and
                                  request['requestedIn'] == request['completedIn'] == short_stamp and request['next'] == 8}
        assert complete_before_switch == expected, {'expected': sorted(expected), 'requests': history}
        print(f'Live prefetch PASS: all parts of {len(visible_cameras)} next-clip 360p cameras encoded before transition')
    finally:
        page.close()


def check(root, sample, ffmpeg, chrome, mbps, native_hls, entry, quality, viewer_path=None):
    archive = root / 'archive'
    name = '2026-11-19_10-02-08'
    category = 'RecentClips' if entry == 'live' else entry
    height = int(quality[:-1])
    cams = ('front', 'back', 'left_repeater', 'right_repeater')
    for minute in range(3):
        stamp = f'2026-11-19_10-0{minute}-00'
        folder = archive / category / (stamp if category in ('RecentClips', 'DriveClips') else name)
        folder.mkdir(parents=True, exist_ok=True)
        for cam in cams:
            shutil.copyfile(sample, folder / f'{stamp}-{cam}.mp4')
    if category == 'SentryClips':
        (folder / 'event.json').write_text(json.dumps(dict(timestamp='2026-11-19T10:00:15',
            reason='sentry_aware_object_detection', camera='7')), encoding='utf-8')
    os.environ.update(TSF_ARCHIVE=str(archive), TSF_CACHE=str(root/'cache'), TSF_SETTINGS=str(root/'settings'), TSF_FFMPEG=ffmpeg)
    if os.name == 'nt':
        sys.modules['fcntl'] = types.ModuleType('fcntl')
    spec = importlib.util.spec_from_file_location('viewer', viewer_path or Path(__file__).resolve().parents[1] / 'sentry-viewer.py')
    viewer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(viewer)
    viewer.ensure_dirs()
    viewer.live_status = lambda: {'running': True}
    if os.name == 'nt':
        viewer.preview_cores = lambda: 2
        viewer.run_variant = lambda cmd, wanted, **options: wanted() and subprocess.run(cmd[3:], capture_output=True).returncode == 0
    # Fractional source timestamps must not shift a keyframe into the next numbered part.
    uneven = root / 'offset-front.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=36',
                    '-t', '8', '-output_ts_offset', '0.028', '-c:v', 'libx264', '-threads', '1', str(uneven)], check=True)
    prefix = str(root / 'checked-')
    viewer._encode_preview_parts(str(uneven), quality, prefix, 2, 8, 'mp4', True, lambda: True)
    for i in range(2, 8 if quality == '720p' else 6):
        frames = subprocess.run([ffmpeg, '-v', 'error', '-i', prefix + str(i) + '.mp4', '-f', 'framehash', '-'],
                                capture_output=True, check=True)
        assert sum(line.startswith(b'0,') for line in frames.stdout.splitlines()) == 24
    server = viewer.BoundedServer(('127.0.0.1', 0), viewer.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:' + str(server.server_port)
    try:
        uri = '/media/' + next((archive / category).rglob('*10-01-00-front.mp4')).relative_to(archive).as_posix()
        with urlopen(base + '/api/playback-source?' + urlencode(dict(file=uri, q=quality, parts=1))) as response:
            state = json.load(response)
            assert state['state'] == 'parts', (entry, quality, state)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=chrome)
            if entry == 'DriveClips' and not native_hls and not mbps:
                check_preview_tail_seek(browser, base, quality)
            if entry == 'SentryClips' and quality in ('360p', '720p') and not native_hls and not mbps:
                check_stale_waiting_event(browser, base, quality)
                check_seek_startup_buffer(browser, base, quality)
                check_paused_seek_runway(browser, base, quality)
                check_tiny_preview_cut(browser, base, quality)
                check_seek_promotes_inflight_read_ahead(browser, base, quality)
            if entry == 'live' and quality == '360p' and not native_hls and not mbps:
                stamp = '2026-11-19_10-02-00'
                latest_cameras = [archive / category / stamp / f'{stamp}-{cam}.mp4' for cam in cams]
                check_live_regressions(browser, base, latest_cameras)
            page = browser.new_page(locale='zh-CN', viewport={'width':1280, 'height':800})
            if mbps:
                network = page.context.new_cdp_session(page)
                network.send('Network.enable')
                network.send('Network.emulateNetworkConditions', {'offline': False, 'latency': 80,
                    'downloadThroughput': mbps * 1_000_000 / 8, 'uploadThroughput': 250000})
            page.add_init_script(f"localStorage.setItem('tsfq3','{quality}');localStorage.setItem('tsfliveq','{quality}')")
            if native_hls:
                page.add_init_script("Object.defineProperty(window,'MediaSource',{value:undefined})")
            requests, errors = [], []
            page.on('request', lambda r: requests.append(r.url))
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(base)
            if entry == 'live':
                page.locator('#tab-live').click()
            else:
                page.locator(f'#tabs [data-category="{category}"]').click()
                page.locator('#grid .card').first.click()
            page.wait_for_function(f"activeVideos()[0]?.videoHeight==={height} && activeVideos()[0].currentTime>.2")
            page.get_by_role('button', name='全览', exact=True).click()
            page.wait_for_function(f"activeVideos().length===4 && activeVideos().every(v=>v.videoHeight==={height}&&!v.paused)")
            drag_timeline(page, 78)
            page.wait_for_function(f"curClipIx===1 && activeVideos().every(v=>v.videoHeight==={height}&&!v.paused&&v.currentTime>20)")
            page.wait_for_function("curClipIx===1 && activeVideos().every(v=>v.currentTime>28)")
            page.get_by_role('button', name='暂停', exact=True).click()
            drag_timeline(page, 105)
            drag_timeline(page, 68)
            page.wait_for_function("activeVideos().every(v=>v.paused&&!v.seeking&&Math.abs(v.currentTime-8)<.35)")
            page.get_by_role('button', name='播放', exact=True).click()
            test_hd_prefetch = entry == 'SentryClips' and quality == '720p' and not native_hls and not mbps
            request_start = len(requests)
            drag_timeline(page, 110 if test_hd_prefetch else 118)
            page.wait_for_function(f"curClipIx===2 && activeVideos().every(v=>v.videoHeight==={height}&&v.currentTime>.3)")
            if test_hd_prefetch:
                prefetched = [parse_qs(urlsplit(url).query) for url in requests[request_start:]
                              if urlsplit(url).path == '/api/playback-preview/part.mp4']
                assert len({q['file'][0] for q in prefetched if q.get('part') == ['0'] and
                            q.get('ahead') == ['1'] and '10-02-00-' in q['file'][0]}) == 4, prefetched
            assert not errors, errors
            assert not any('/media/' in url for url in requests), requests
            expected = 'index.m3u8' if native_hls else 'part.mp4'
            selected = [parse_qs(urlsplit(url).query) for url in requests
                        if urlsplit(url).path == '/api/playback-preview/' + expected]
            assert selected and all(q['q'] == [quality] and q['file'][0].startswith(f'/media/{category}/') for q in selected)
            # Switch quality with a paused four-camera timeline; each pane must stay at the seek target.
            page.get_by_role('button', name='暂停', exact=True).click()
            drag_timeline(page, 132)
            page.wait_for_function("activeVideos().every(v=>v.paused&&!v.seeking&&Math.abs(v.currentTime-12)<.35)")
            other = '480p' if quality == '720p' else '720p'
            page.locator('#qsel .qbtn').click()
            page.locator('#qsel .qmenu').get_by_text(other, exact=True).click()
            page.wait_for_function(f"activeVideos().length===4 && activeVideos().every(v=>v.paused&&!v.seeking&&v.videoHeight==={int(other[:-1])}&&Math.abs(v.currentTime-12)<.35)")
            if entry == 'live' and quality == '360p':
                stamp = '2026-11-19_10-03-00'
                latest = archive / category / stamp
                latest.mkdir()
                for cam in cams:
                    shutil.copyfile(sample, latest / f'{stamp}-{cam}.mp4')
                page.locator('#livep').get_by_text('检查更新', exact=True).click()
                page.wait_for_function("curClips.length===4 && curClipIx===2 && !wantedPlay && !followLatest")
                page.locator('#lv-back').click()
                page.wait_for_function(f"followLatest && curClipIx===3 && activeVideos().length===4 && activeVideos().every(v=>v.videoHeight==={int(other[:-1])}&&!v.paused&&v.currentTime>.25)")
            if entry == 'DriveClips' and quality == '360p':
                page.locator('#watch-items button').last.click()
                page.wait_for_function(f"curClipIx===0 && activeVideos()[0]?.videoHeight==={int(other[:-1])}")
                link = urlencode(dict(ev=f'{category}/2026-11-19_10-01-00', cam='all'))
                page.goto(base + '/?' + link)
                page.wait_for_function(f"curClipIx===1 && activeVideos().length===4 && activeVideos().every(v=>v.videoHeight==={height}&&!v.paused&&v.currentTime>.25)")
            assert not any(urlsplit(u).path.startswith('/media/') for u in requests)
            assert not errors, errors
            page.locator('.pback').click()
            page.close()
            if entry == 'live' and quality == '360p' and not native_hls and not mbps:
                check_live_prefetch(browser, base, root, archive / category, cams, uneven)
                check_live_prefetch(browser, base, root, archive / category, cams, uneven, camera='front')
            browser.close()
        playlist = base + '/api/playback-preview/index.m3u8?' + urlencode(dict(file=uri, q=quality, at=12))
        with urlopen(playlist, timeout=4) as response:
            text = response.read().decode()
            assert text.count('#EXTINF:') == 60 and '#EXT-X-ENDLIST' in text
        with urlopen(playlist.replace('/playback-preview/', '/sentry-preview/'), timeout=4) as legacy:
            assert legacy.read().decode() == text
        part = base + '/api/playback-preview/part.mp4?' + urlencode(dict(file=uri, q=quality, part=0, ahead=1))
        with urlopen(part + '&cache_only=1', timeout=30) as warmed:
            assert warmed.status == 204 and not warmed.read()
        with urlopen(part, timeout=4) as media:
            assert media.status == 200 and len(media.read()) > 0
        frames = subprocess.run([ffmpeg, '-v', 'error', '-ss', '12', '-i', playlist, '-t', '4.2',
                                 '-f', 'framehash', '-'], capture_output=True, check=True, timeout=30)
        assert sum(line.startswith(b'0,') for line in frames.stdout.splitlines()) >= 96
        print(f'{entry} {quality} native_hls={native_hls}: four cold views, seek, pause, cross-file playback and HLS decoding PASS')
    finally:
        server.shutdown(); server.server_close()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for key in ('sample', 'ffmpeg', 'chrome'):
        p.add_argument('--'+key, required=True)
    p.add_argument('--mbps', type=float)
    p.add_argument('--native-hls', action='store_true')
    p.add_argument('--entry', choices=['SentryClips','SavedClips','DriveClips','RecentClips','live'], default='SentryClips')
    p.add_argument('--quality', choices=['360p','480p','720p'], default='360p')
    p.add_argument('--viewer', type=Path, help='Viewer source file to test')
    args = p.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        check(Path(temp), args.sample, args.ffmpeg, args.chrome, args.mbps, args.native_hls, args.entry, args.quality, args.viewer)
