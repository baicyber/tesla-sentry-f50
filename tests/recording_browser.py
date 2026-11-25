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
from playwright.sync_api import expect, sync_playwright

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def check_status_stability(page):
    page.evaluate("""() => {
        const header = document.querySelector('.app-header');
        const fields = [...header.querySelectorAll('.system-status strong')];
        const cells = fields.flatMap(f => [f.firstElementChild, f.lastElementChild]);
        const saved = cells.map(el => [...el.childNodes].map(node => node.cloneNode(true)));
        const hot = byId('sys-temp').classList.contains('hot'), title = byId('sys-memory').title;
        const anchors = [...header.querySelectorAll('h1,#tabs,#theme,#gear,.system-readings>span,.play-traffic>span'), ...cells];
        const positions = () => anchors.map(el => {
            const r = el.getBoundingClientRect();
            return [r.x, r.y, r.width, r.height];
        });
        const before = positions();
        const decimalPositions = new Map();
        try {
            for (const [temp,cpu,mem,rate,total] of [
                [49.7,0.8,33,0,0], [9,3,7,0,0], [47,10,9,1.25*1024**2,0.5*1024**3],
                [47,10,9,0.08*1024**2,0.01*1024**3], [47,10,9,10*1024**2,10*1024**3],
                [105.5,99.9,100,999.9*1024**2,999.99*1024**3],
                [55.6,12.5,45,943,1024], [149.9,99.9,100,1024**2-1,1024**3-1],
                [80,100,97,1024**3-1,1024**4-1], [null,null,null,null,null]
            ]) {
                updateSystemStatus(temp == null ? null : {cpu_temp:temp,cpu_pct:cpu,mem_total:100,mem_used:mem});
                setTrafficValue('play-rate',rate); setTrafficValue('play-bytes',total);
                const expected = [
                    [temp?.toFixed(1),'°C'], [cpu?.toFixed(1),'%'], [mem?.toString(),'%'],
                    [(rate == null ? null : rate/2**20)?.toFixed(2),'MB/s'],
                    [(total == null ? null : total/2**30)?.toFixed(2),'GB']
                ];
                fields.forEach((field,i)=>{
                    const [value,unit] = expected[i];
                    if (field.firstElementChild.textContent !== (value ?? '—') || field.lastElementChild.textContent.trim() !== unit)
                        throw Error('Unexpected reading: '+field.id+' '+field.textContent);
                    const label = field.parentElement.querySelector('[aria-hidden=true]') ?? field.parentElement.firstChild;
                    const range = document.createRange();
                    range.selectNodeContents(label);
                    if (label.nodeType === Node.TEXT_NODE) range.setEnd(label,label.textContent.trimEnd().length);
                    const labelRight = range.getBoundingClientRect().right;
                    range.selectNodeContents(field.firstElementChild);
                    const gap = range.getBoundingClientRect().left-labelRight;
                    if (gap < 0)
                        throw Error('Reading overlaps label: '+field.id);
                    if (value != null) {
                        const font = parseFloat(getComputedStyle(field).fontSize);
                        const maxGap = value.split('.')[0].length === 1 ? font : font*.44;
                        if (gap > maxGap) throw Error('Excess space after label: '+field.id+' '+gap);
                        if (value.includes('.')) {
                            const fraction = field.firstElementChild.lastElementChild.firstChild;
                            range.setStart(fraction,0); range.setEnd(fraction,1);
                            const left = range.getBoundingClientRect().left;
                            if (decimalPositions.has(field) && Math.abs(left-decimalPositions.get(field))>.1)
                                throw Error('Decimal point moved: '+field.id);
                            decimalPositions.set(field,left);
                        }
                    }
                });
                if (JSON.stringify(positions()) !== JSON.stringify(before)) throw Error('Header moved at '+innerWidth+'px, '+rate+' B/s');
                for (const el of cells) {
                    if (!el.textContent) continue;
                    const range=document.createRange(); range.selectNodeContents(el);
                    const text=range.getBoundingClientRect(), cell=el.getBoundingClientRect();
                    if (text.left < cell.left-.5 || text.right > cell.right+.5)
                        throw Error('Value exceeds its column: '+el.parentElement.id+' '+el.textContent+' at '+innerWidth+'px');
                    const isNumber = el === el.parentElement.firstElementChild;
                    const offset = isNumber ? text.right-cell.right : text.left-cell.left;
                    if (Math.abs(offset)>.1)
                        throw Error('Text moved away from its anchor: '+el.parentElement.id+' '+el.textContent+' '+offset+'px');
                }
            }
        } finally {
            cells.forEach((el,i)=>{ el.replaceChildren(...saved[i]); });
            byId('sys-temp').classList.toggle('hot',hot); byId('sys-memory').title=title;
        }
    }""")


def main(CHROME, FFMPEG, ROOT):
    ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='f50-recording-ui-') as temporary:
        root = Path(temporary)
        archive = root/'archive'
        os.environ.update(TSF_ARCHIVE=str(archive), TSF_CACHE=str(root/'cache'),
            TSF_AUTH_FILE=str(root/'auth.conf'), TSF_SETTINGS=str(root/'settings.json'))
        sys.modules['fcntl'] = types.ModuleType('fcntl')
        spec = importlib.util.spec_from_file_location('viewer', REPO/'sentry-viewer.py')
        viewer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(viewer)
        viewer.FFMPEG_BIN = FFMPEG
        viewer.ensure_dirs()
        viewer._acquire_usb_flock = lambda: True
        viewer._release_usb_flock = lambda: None
        viewer.sys_status = lambda: dict(cpu_temp=47,cpu_pct=12,mem_used=512<<20,mem_total=1024<<20)
        viewer.live_status = lambda: dict(running=True,sys=viewer.sys_status(),
            disk=dict(arc_used=viewer.archive_size(), arc_limit=6 << 30, disk_total=8 << 30))
        viewer.telemetry_cached = lambda path: dict(frames=[[0,'D',24,20,0,False,0,0,0,0,0,0,0,0]],
                                                    brake_ratio=0,count=1,fps=24)
        subprocess.run([FFMPEG,'-v','error','-f','lavfi','-i','testsrc2=size=320x240:rate=24','-t','2',
            '-c:v','libx264','-threads','1',str(root/'sample.mp4')],check=True)
        first, second = '2026-11-22_08-00-00','2026-11-22_08-00-02'
        for cat in viewer.CATS:
            for key in (first, second):
                folder = archive/cat/(key if cat in ('DriveClips','RecentClips') else first)
                folder.mkdir(parents=True,exist_ok=True)
                for cam in ('front','back','left_repeater','right_repeater'):
                    shutil.copyfile(root/'sample.mp4',folder/(key+'-'+cam+'.mp4'))
        with (archive/'SentryClips'/first/'fixture-padding').open('wb') as stream:
            stream.truncate(4 << 20)
        class Handler(viewer.Handler):
            def log_message(self,*args): pass
        server=viewer.BoundedServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        base='http://127.0.0.1:'+str(server.server_port)
        report=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=CHROME,headless=True)
                page=browser.new_page(viewport=dict(width=1440,height=1000),locale='zh-CN')
                errors=[]
                page.on('pageerror',lambda err:errors.append(str(err)))
                page.add_init_script("localStorage.setItem('tsf_theme','light');localStorage.setItem('tsfq3','orig')")
                page.goto(base)
                expect(page.locator('#sys-temp')).to_have_text('47.0°C')
                expect(page.locator('#sys-cpu')).to_have_text('12.0%')
                expect(page.locator('#sys-mem')).to_have_text('50%')
                meter=page.locator('.app-header .system-status .play-traffic')
                expect(meter.locator('[aria-hidden="true"]')).to_have_text(['↓','Σ'])
                expect(meter.locator('#play-rate')).to_have_text('0.00 MB/s')
                expect(meter.locator('#play-bytes')).to_have_text('0.00 GB')
                assert page.locator('text=查看存储').count()==0
                assert page.locator('text=行驶归档').count()==0
                for width,height in ((320,800),(375,812),(380,840),(381,840),(390,844),(430,932),(601,960),(768,1024),(1024,800),(1440,1000),(568,320),(844,390)):
                    page.set_viewport_size(dict(width=width,height=height))
                    page.wait_for_timeout(120)
                    check_status_stability(page)
                    bounds=page.locator('.app-header').evaluate('''el=>({width:innerWidth,scroll:el.scrollWidth,rects:[...el.querySelectorAll('.system-status,.tabs,#theme,#gear')].map(e=>{const r=e.getBoundingClientRect();return [e.className||e.id,r.x,r.y,r.right,r.bottom]})})''')
                    assert bounds['scroll']<=width+1,bounds
                    header_bottom=page.locator('.app-header').evaluate('el=>el.getBoundingClientRect().bottom')
                    for name,x,y,right,bottom in bounds['rects']:
                        assert x>=0 and right<=width+1 and bottom<=header_bottom+1,(width,bounds)
                    items=page.locator('.system-readings > span,.play-traffic > span,#tabs,#theme,#gear').evaluate_all(
                        'nodes=>nodes.map(el=>el.getBoundingClientRect().toJSON())')
                    for i,rect in enumerate(items):
                        assert rect['width']>0 and rect['left']>=0 and rect['right']<=width+1 and rect['bottom']<=header_bottom+1,(width,rect)
                        for other in items[:i]:
                            assert (min(rect['right'],other['right'])-max(rect['left'],other['left'])<=1 or
                                    min(rect['bottom'],other['bottom'])-max(rect['top'],other['top'])<=1),(width,rect,other)
                    page.screenshot(path=str(ROOT/f'local-home-{width}.png'))
                    report.append(dict(width=width,height=height,header=bounds))
                page.set_viewport_size(dict(width=381,height=840))
                for style in ('apple','claude','pixel','default'):
                    page.evaluate('style=>applyStyle(style)', style)
                    check_status_stability(page)
                    assert page.locator('.app-header').evaluate('el=>el.scrollWidth<=innerWidth+1'), style
                page.set_viewport_size(dict(width=393,height=852))
                status = page.locator('.system-status')
                for size in ('', '14px', '16px'):
                    status.evaluate('(el,size)=>el.style.fontSize=size',size)
                    check_status_stability(page)
                    fits = page.locator('#play-bytes').evaluate("""field=>{
                        const header=field.closest('header'), range=document.createRange();
                        range.selectNodeContents(field.lastElementChild);
                        const edge=header.getBoundingClientRect().right-parseFloat(getComputedStyle(header).paddingRight);
                        return range.getBoundingClientRect().right<=edge+0.5 && header.scrollWidth<=header.clientWidth+1;
                    }""")
                    assert fits, f'GB exceeds the mobile header at font size {size}'
                page.locator('#gear').click()
                page.wait_for_function("Math.abs(byId('panel').getBoundingClientRect().top-document.querySelector('.app-header').getBoundingClientRect().bottom)<1")
                page.locator('#tabs').get_by_text('哨兵录像',exact=True).click()
                status.evaluate("el=>el.style.removeProperty('font-size')")
                page.set_viewport_size(dict(width=390,height=844))
                # A slow response for the old recording must not change the
                # management controls of a recording opened in the meantime.
                original_action = viewer.recording_action
                started, release = threading.Event(), threading.Event()
                def delayed_action(body):
                    started.set()
                    if not release.wait(10):
                        raise ValueError('测试请求未释放')
                    return original_action(body)
                viewer.recording_action = delayed_action
                page.locator('#grid .card').first.click()
                page.locator('#record-lock').click()
                assert started.wait(3)
                page.locator('#tabs').get_by_text('手动保存', exact=True).click()
                page.locator('#grid .card').first.click()
                release.set()
                page.wait_for_function('!recordingBusy')
                assert page.evaluate("playerRecording.cat === 'SavedClips' && !playerRecording.locked")
                expect(page.locator('#record-lock')).to_have_attribute('aria-label','锁定')
                viewer.recording_action = original_action
                page.locator('#tabs').get_by_text('哨兵录像', exact=True).click()
                page.locator('#grid .card').first.click()
                expect(page.locator('#record-lock')).to_have_attribute('aria-label','解锁')
                page.locator('#record-lock').click()
                expect(page.locator('#record-delete')).to_be_enabled()
                page.locator('.pback').click()
                report.append(dict(switch_during_action=True))
                for label,cat in (('哨兵录像','SentryClips'),('手动保存','SavedClips'),('行车记录','DriveClips')):
                    page.locator('#tabs').get_by_text(label,exact=True).click()
                    page.locator('#grid .card').first.click()
                    page.wait_for_function('curClips.length>0')
                    expect(page.locator('#record-lock')).to_be_enabled()
                    expect(page.locator('#record-lock use')).to_have_attribute('href','#i-unlock')
                    expect(page.locator('#tpanel')).to_be_visible()
                    if cat == 'DriveClips':
                        for width,height in ((320,800),(360,800),(390,844),(430,932),(768,1024),(1440,1000),(844,390)):
                            page.set_viewport_size(dict(width=width,height=height))
                            page.wait_for_timeout(120)
                            bounds=page.evaluate('''() => {
                              const rect = selector => { const r=document.querySelector(selector).getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom}; };
                              return {panel:rect('#tpanel'),video:rect('.vwrap'),top:rect('.ptop'),
                                controls:['#ppbtn','#record-lock','#record-delete','#screenbtn'].map(rect),
                                overflow:document.querySelector('.pscroll').scrollWidth>document.querySelector('.pscroll').clientWidth};
                            }''')
                            assert bounds['panel']['bottom']<=bounds['video']['y'],bounds
                            assert not bounds['overflow'],bounds
                            assert max(b['y'] for b in bounds['controls'][:3])-min(b['y'] for b in bounds['controls'][:3])<1,bounds
                            assert all(0<=b['x'] and b['right']<=width+1 for b in bounds['controls']),bounds
                            if height>width:
                                assert bounds['top']['bottom']-bounds['top']['y']<60,bounds
                            page.screenshot(path=str(ROOT/f'local-controls-{width}.png'))
                            report.append(dict(player_width=width,height=height,bounds=bounds))
                        page.set_viewport_size(dict(width=390,height=844))
                    page.locator('#record-lock').click()
                    expect(page.locator('#record-lock')).to_have_attribute('aria-label','解锁')
                    expect(page.locator('#record-lock use')).to_have_attribute('href','#i-lock')
                    assert page.locator('#record-lock').evaluate("e=>getComputedStyle(e).color === getComputedStyle(document.documentElement).getPropertyValue('--danger').trim() || getComputedStyle(e).color === 'rgb(179, 59, 59)'")
                    expect(page.locator('#record-lock')).to_be_enabled()
                    expect(page.locator('#record-delete')).to_be_disabled()
                    page.reload()
                    page.locator('#tabs').get_by_text(label,exact=True).click()
                    page.locator('#grid .card').first.click()
                    expect(page.locator('#record-lock')).to_have_attribute('aria-label','解锁')
                    page.screenshot(path=str(ROOT/f'local-player-{cat}.png'))
                    page.locator('#record-lock').click()
                    expect(page.locator('#record-delete')).to_be_enabled()
                    page.once('dialog',lambda d:d.dismiss())
                    page.locator('#record-delete').click()
                    assert (archive/cat/first).is_dir()
                    if cat == 'SentryClips':
                        storage = page.locator('#storarc')
                        expect(storage).to_have_text(__import__('re').compile(r'^[4-9]MB / '))
                    page.once('dialog',lambda d:d.accept())
                    page.locator('#record-delete').click()
                    if cat == 'SentryClips':
                        expect(page.locator('#toast')).to_have_text('已删除')
                        expected_mb = (viewer._dir_size(archive, deduplicate=True) + (1 << 19)) >> 20
                        expect(storage).to_have_text(f'{expected_mb}MB / 6.0GB', timeout=2500)
                    expect(page.locator('#player')).not_to_have_class(__import__('re').compile(r'\bopen\b'))
                    expect(page.locator('#grid .card')).to_have_count(0)
                    assert not list((archive/cat).glob('*/'))
                    report.append(dict(cat=cat,lock=True,reload=True,unlock=True,cancel=True,delete=True))
                page.locator('#tabs').get_by_text('最近1小时',exact=True).click()
                page.locator('#grid .card').first.click()
                expect(page.locator('#record-lock')).to_have_attribute('aria-disabled','true')
                expect(page.locator('#record-delete')).to_have_attribute('aria-disabled','true')
                rect=page.locator('#record-lock').bounding_box()
                page.mouse.click(rect['x']+rect['width']/2,rect['y']+rect['height']/2)
                expect(page.locator('#toast')).to_have_text('循环录像不支持锁定和删除')
                page.locator('#tab-live').click()
                rect=page.locator('#record-delete').bounding_box()
                page.mouse.click(rect['x']+rect['width']/2,rect['y']+rect['height']/2)
                expect(page.locator('#toast')).to_have_text('循环录像不支持锁定和删除')
                response=page.request.post(base+'/api/recording',data=dict(cat='RecentClips',event=first,clips=[first],action='delete'))
                assert response.status==403
                response=page.request.post(base+'/api/recording',headers={'X-TSF-Recording':'1','Origin':'http://invalid.example'},data={})
                assert response.status==403
                assert not errors,errors
                browser.close()
            (ROOT/'local-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print('PASS local UI: responsive header, archive management actions and source validation')
        finally:
            server.shutdown();server.server_close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description='Isolated recording controls and responsive status bar checks')
    parser.add_argument('--chrome', required=True)
    parser.add_argument('--ffmpeg', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    main(args.chrome, args.ffmpeg, args.output)
