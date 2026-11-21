"""Real browser settings checks against a temporary authenticated server."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
from urllib.error import HTTPError
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

REPO = Path(__file__).resolve().parents[1]


def main(chrome, output):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='f50-storage-browser-') as directory:
        root = Path(directory)
        auth = root / 'auth.conf'
        auth.write_text('user=admin\npass=isolated-storage-test\nsecret=' + 'a' * 64 + '\nttl=3600\n', encoding='utf-8')
        image = root / 'image'
        image.write_bytes(b'protected original fixture')
        os.environ.update(TSF_ARCHIVE=str(root / 'archive'), TSF_CACHE=str(root / 'cache'),
            TSF_IMG=str(image), TSF_AUTH_FILE=str(auth), TSF_SETTINGS=str(root / 'settings.json'),
            TSF_STORAGE_SETTINGS=str(root / 'storage.json'), TSF_ARCHIVE_TOTAL_GB='34', TSF_DRIVE_MAX_GB='12')
        if os.name == 'nt':
            sys.modules['fcntl'] = types.ModuleType('fcntl')
        spec = importlib.util.spec_from_file_location('storage_ui_viewer', REPO / 'sentry-viewer.py')
        viewer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(viewer)
        viewer.live_status = lambda: {'running': True}
        viewer.ensure_dirs()
        viewer.storage_allocation.snapshot = lambda *args: dict(total_bytes=128 << 30,
            free_bytes=50 << 30, budget_bytes=120 << 30, usb_bytes=64 << 30,
            archive_bytes=40 << 30, drive_bytes=18 << 30, cache_gib=4)
        viewer._exfat_used = lambda *args: 3 << 30
        # Refuse the hardware lock: the browser can exercise failure UI without accessing USB.
        def unavailable_lock(*args):
            time.sleep(.8)
            return False
        viewer._acquire_usb_flock = unavailable_lock
        class Handler(viewer.Handler):
            def log_message(self, *args):
                pass
        server = viewer.BoundedServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:%d' % server.server_port
        try:
            try:
                urlopen(base + '/api/storage')
                raise AssertionError('Storage API exposed without login')
            except HTTPError as exc:
                assert exc.code == 401
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=chrome, headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1100}, locale='zh-CN')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base + '/login')
                page.locator('[name=u]').fill('admin')
                page.locator('[name=p]').fill('isolated-storage-test')
                page.locator('button[type=submit]').click()
                page.locator('#gear').click()
                page.locator('#st-storage').click()
                expect(page.locator('#storage-archive')).to_have_value('34')
                expect(page.locator('#storage-save')).to_be_enabled()
                expect(page.locator('#storage-budget')).to_have_text('还可分配 8.0 GiB')
                page.locator('#storage-drive').fill('20')
                expect(page.locator('#storage-budget')).to_have_text('还可分配 8.0 GiB')
                page.locator('.storage-advanced').filter(has=page.locator('#storage-minimum')).locator('summary').click()
                page.locator('#storage-minimum').fill('6')
                expect(page.locator('#storage-budget')).to_have_text('还可分配 8.0 GiB')
                page.locator('#storage-usb').fill('65')
                expect(page.locator('#storage-budget')).to_have_text('还可分配 7.0 GiB')
                expect(page.locator('#storage-used-usb')).to_have_text('3.0 / 64 GiB')
                expect(page.locator('#storage-used-drive')).to_have_text('18.0 / 12 GiB')
                page.locator('#storage-archive').fill('60')
                expect(page.locator('#storage-budget')).to_contain_text('超过可分配容量 19.0 GiB')
                page.locator('#storage-archive').fill('')
                expect(page.locator('#storage-plan')).to_be_hidden()
                expect(page.locator('#storage-budget')).to_contain_text('请输入有效的整数容量')
                page.get_by_role('button', name='重新加载', exact=True).click()
                expect(page.locator('#storage-budget')).to_have_text('还可分配 8.0 GiB')
                page.locator('.storage-advanced').filter(has=page.locator('#storage-minimum')).locator('summary').click()
                assert page.locator('.storage-nested #storage-drive').count() == 1
                for width in (1440, 1024, 768, 320):
                    page.set_viewport_size({'width': width, 'height': 1100})
                    button = page.locator('[data-help=drive]')
                    button.scroll_into_view_if_needed()
                    before = page.locator('#storage-drive').bounding_box()
                    button.click()
                    expect(page.locator('#storage-tip')).to_be_visible()
                    expect(page.locator('#storage-tip')).to_contain_text('已包含在归档总容量内')
                    after = page.locator('#storage-drive').bounding_box()
                    assert before == after, 'Help changed form layout'
                    assert button.evaluate("element => getComputedStyle(element, '::before').width") == '14px'
                    rect = page.locator('#storage-tip').bounding_box()
                    assert rect['x'] >= 0 and rect['x'] + rect['width'] <= width
                    page.screenshot(path=str(output / ('storage-%d.png' % width)))
                    page.keyboard.press('Escape')
                    expect(page.locator('#storage-tip')).to_be_hidden()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.set_viewport_size({'width': 1024, 'height': 1100})
                page.locator('#storage-archive').fill('36')
                page.locator('#storage-drive').fill('14')
                page.locator('#storage-save').click()
                expect(page.locator('#storage-msg')).to_have_text('归档设置已保存')
                assert json.loads((root / 'storage.json').read_text())['drive_gib'] == 14
                page.locator('#storage-drive').fill('40')
                page.locator('#storage-save').click()
                expect(page.locator('#storage-msg')).to_contain_text('不能超过归档总容量')
                page.locator('#storage-drive').fill('10')
                page.once('dialog', lambda dialog: dialog.dismiss())
                page.locator('#storage-save').click()
                expect(page.locator('#storage-msg')).to_have_text('已取消')
                assert json.loads((root / 'storage.json').read_text())['drive_gib'] == 14
                page.once('dialog', lambda dialog: dialog.accept())
                page.locator('#storage-save').click()
                expect(page.locator('#storage-msg')).to_have_text('归档设置已保存')
                assert json.loads((root / 'storage.json').read_text())['drive_gib'] == 10
                # A stale browser cannot overwrite a newer saved configuration.
                data = page.request.get(base + '/api/storage').json()
                headers = {'X-TSF-Storage': '1'}
                body = dict(values=dict(data['values'], archive_gib=37), revision=data['revision'])
                assert page.request.post(base + '/api/storage', data=body).status == 403
                assert page.request.post(base + '/api/storage', data=body, headers=headers).status == 200
                page.locator('#storage-save').click()
                expect(page.locator('#storage-msg')).to_contain_text('配置已变化')
                page.get_by_role('button', name='重新加载', exact=True).click()
                expect(page.locator('#storage-archive')).to_have_value('37')
                page.locator('#storage-usb').fill('32')
                resize_requests = []
                page.on('request', lambda request: resize_requests.append(request.post_data_json)
                        if request.url.endswith('/api/storage/resize') else None)
                def check_resize_dialog(dialog, accept=False):
                    assert '清空车机 U 盘中的全部录像和其他文件' in dialog.message
                    assert '盒子归档保留' in dialog.message
                    assert '无法撤销' in dialog.message
                    dialog.accept() if accept else dialog.dismiss()
                page.once('dialog', check_resize_dialog)
                page.locator('#storage-resize').click()
                assert not resize_requests, 'Cancel must not send an erase request'
                assert image.read_bytes() == b'protected original fixture'
                legacy = dict(usb_gib=32, revision=page.request.get(base + '/api/storage').json()['revision'], confirmed=True)
                rejected = page.request.post(base + '/api/storage/resize', data=legacy, headers=headers)
                assert rejected.status == 400 and '清空' in rejected.json()['msg']
                page.once('dialog', lambda dialog: check_resize_dialog(dialog, accept=True))
                page.locator('#storage-resize').click()
                expect(page.locator('#storage-save')).to_be_disabled()
                expect(page.locator('#storage-msg')).to_contain_text('其他任务占用', timeout=10000)
                expect(page.locator('#storage-resize-status #storage-msg')).to_contain_text('其他任务占用')
                expect(page.locator('#storage-save')).to_be_enabled()
                assert resize_requests == [dict(usb_gib=32, revision=legacy['revision'], clear_confirmed=True)]
                assert image.read_bytes() == b'protected original fixture'
                assert errors == [], errors
                page.screenshot(path=str(output / 'storage-final.png'))
                touch = browser.new_context(storage_state=page.context.storage_state(), viewport={'width':390, 'height':844}, has_touch=True, is_mobile=True)
                mobile = touch.new_page()
                mobile.goto(base)
                mobile.locator('#gear').click()
                mobile.locator('#st-storage').click()
                expect(mobile.locator('#storage-save')).to_be_enabled()
                mobile_help = mobile.locator('[data-help=drive]')
                assert mobile_help.bounding_box()['width'] >= 44
                assert mobile_help.bounding_box()['height'] >= 44
                mobile_help.tap()
                expect(mobile.locator('#storage-tip')).to_be_visible()
                assert mobile.evaluate('document.documentElement.scrollWidth <= innerWidth')
                mobile.screenshot(path=str(output / 'storage-touch.png'))
                mobile.locator('.settings-content').evaluate('element => element.scrollBy(0, 100)')
                expect(mobile.locator('#storage-tip')).to_be_hidden()
                mobile.locator('.settings-back').click()
                expect(mobile.locator('#st-brand')).to_be_visible()
                mobile.locator('#st-brand').click()
                expect(mobile.locator('#settings-title')).to_have_text('品牌')
                expect(mobile.locator('#brand-name')).to_be_visible()
                mobile.locator('#tabs .tab').first.click()
                expect(mobile.locator('#panel')).to_be_hidden()
                expect(mobile.locator('#livetxt')).to_be_visible()
                mobile.locator('#gear').click()
                mobile.locator('#st-storage').click()
                expect(mobile.locator('#storage-archive')).to_have_value('37')
                mobile.locator('#bname').evaluate("element => element.textContent='测试品牌名称适配手机顶栏测试品牌名称适配手机顶栏'")
                for width in (320,390,768):
                    mobile.set_viewport_size(dict(width=width,height=844))
                    header = mobile.locator('.app-header').bounding_box()
                    nav = mobile.locator('#tabs').bounding_box()
                    assert nav['y']+nav['height'] <= header['y']+header['height']+1
                    assert mobile.evaluate('document.documentElement.scrollWidth<=innerWidth')
                english = browser.new_context(storage_state=page.context.storage_state(),locale='en-US')
                english.add_init_script("localStorage.setItem('tsf_lang','en')")
                translated = english.new_page()
                translated.goto(base)
                expect(translated.locator('#tabs [data-category=SentryClips] .tl')).to_have_text(viewer.LANG_TABLE['en']['哨兵告警'])
                translated.locator('#gear').click()
                for identifier,key in (('files','车机U盘'),('archive','归档区'),('skin','网页背景')):
                    expect(translated.locator('#st-'+identifier)).to_have_text(viewer.LANG_TABLE['en'][key])
                english.close()
                touch.close()
                browser.close()
            print('PASS: allocation math, nested limits, actual usage, 14px help and touch targets, layout at 4 widths, save, validation, confirmation, stale revisions, request protection, USB refusal')
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--chrome', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    main(args.chrome, args.output)
