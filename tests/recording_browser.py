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


def main(CHROME, FFMPEG, ROOT):
    ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='f50-recording-ui-') as temporary:
        root = Path(temporary)
        archive = root/'archive'
        os.environ.update(TSF_ARCHIVE=str(archive), TSF_CACHE=str(root/'cache'),
            TSF_AUTH_FILE=str(root/'auth.conf'), TSF_SETTINGS=str(root/'settings.json'), TSF_FFMPEG=FFMPEG)
        sys.modules['fcntl'] = types.ModuleType('fcntl')
        spec = importlib.util.spec_from_file_location('viewer', REPO/'sentry-viewer.py')
        viewer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(viewer)
        viewer.ensure_dirs()
        viewer._acquire_usb_flock = lambda: True
        viewer._release_usb_flock = lambda: None
        viewer.live_status = lambda: dict(running=True,sys=dict(cpu_temp=47,cpu_pct=12,mem_used=512<<20,mem_total=1024<<20))
        subprocess.run([FFMPEG,'-v','error','-f','lavfi','-i','testsrc2=size=320x240:rate=24','-t','2',
            '-c:v','libx264','-threads','1',str(root/'sample.mp4')],check=True)
        first, second = '2026-11-22_08-00-00','2026-11-22_08-00-02'
        for cat in viewer.CATS:
            for key in (first, second):
                folder = archive/cat/(key if cat in ('DriveClips','RecentClips') else first)
                folder.mkdir(parents=True,exist_ok=True)
                for cam in ('front','back','left_repeater','right_repeater'):
                    shutil.copyfile(root/'sample.mp4',folder/(key+'-'+cam+'.mp4'))
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
                expect(page.locator('#sys-temp')).to_have_text('47°C')
                expect(page.locator('#sys-mem')).to_have_text('50%')
                assert page.locator('text=查看存储').count()==0
                assert page.locator('text=行驶归档').count()==0
                for width,height in ((320,800),(390,844),(768,1024),(1024,800),(1440,1000),(844,390)):
                    page.set_viewport_size(dict(width=width,height=height))
                    page.wait_for_timeout(120)
                    bounds=page.locator('.app-header').evaluate('''el=>({width:innerWidth,scroll:el.scrollWidth,rects:[...el.querySelectorAll('.system-status,.tabs,#theme,#gear')].map(e=>{const r=e.getBoundingClientRect();return [e.className||e.id,r.x,r.y,r.right,r.bottom]})})''')
                    assert bounds['scroll']<=width+1,bounds
                    header_bottom=page.locator('.app-header').evaluate('el=>el.getBoundingClientRect().bottom')
                    for name,x,y,right,bottom in bounds['rects']:
                        assert x>=0 and right<=width+1 and bottom<=header_bottom+1,(width,bounds)
                    page.screenshot(path=str(ROOT/f'local-home-{width}.png'))
                    report.append(dict(width=width,height=height,header=bounds))
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
                expect(page.locator('#record-lock')).to_have_text('锁定')
                viewer.recording_action = original_action
                page.locator('#tabs').get_by_text('哨兵录像', exact=True).click()
                page.locator('#grid .card').first.click()
                expect(page.locator('#record-lock')).to_have_text('解锁')
                page.locator('#record-lock').click()
                expect(page.locator('#record-delete')).to_be_enabled()
                page.locator('.pback').click()
                report.append(dict(switch_during_action=True))
                for label,cat in (('哨兵录像','SentryClips'),('手动保存','SavedClips'),('行车记录','DriveClips')):
                    page.locator('#tabs').get_by_text(label,exact=True).click()
                    page.locator('#grid .card').first.click()
                    page.wait_for_function('curClips.length>0')
                    expect(page.locator('#record-lock')).to_be_enabled()
                    page.locator('#record-lock').click()
                    expect(page.locator('#record-lock')).to_have_text('解锁')
                    expect(page.locator('#record-lock')).to_be_enabled()
                    expect(page.locator('#record-delete')).to_be_disabled()
                    page.reload()
                    page.locator('#tabs').get_by_text(label,exact=True).click()
                    page.locator('#grid .card').first.click()
                    expect(page.locator('#record-lock')).to_have_text('解锁')
                    page.screenshot(path=str(ROOT/f'local-player-{cat}.png'))
                    page.locator('#record-lock').click()
                    expect(page.locator('#record-delete')).to_be_enabled()
                    page.once('dialog',lambda d:d.dismiss())
                    page.locator('#record-delete').click()
                    assert (archive/cat/first).is_dir()
                    page.once('dialog',lambda d:d.accept())
                    page.locator('#record-delete').click()
                    expect(page.locator('#player')).not_to_have_class(__import__('re').compile(r'\bopen\b'))
                    expect(page.locator('#grid .card')).to_have_count(0)
                    assert not list((archive/cat).glob('*/'))
                    report.append(dict(cat=cat,lock=True,reload=True,unlock=True,cancel=True,delete=True))
                page.locator('#tabs').get_by_text('最近1小时监控',exact=True).click()
                page.locator('#grid .card').first.click()
                expect(page.locator('#record-lock')).to_have_attribute('aria-disabled','true')
                expect(page.locator('#record-delete')).to_have_attribute('aria-disabled','true')
                rect=page.locator('#record-lock').bounding_box()
                page.mouse.click(rect['x']+rect['width']/2,rect['y']+rect['height']/2)
                expect(page.locator('#toast')).to_have_text('循环录像不支持锁定和删除')
                page.locator('.pback').click()
                page.locator('#live-btn').click()
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
            print('PASS local UI: six viewport sizes, all archive management actions and source validation')
        finally:
            server.shutdown();server.server_close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description='Isolated recording controls and responsive status bar checks')
    parser.add_argument('--chrome', required=True)
    parser.add_argument('--ffmpeg', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    main(args.chrome, args.ffmpeg, args.output)
