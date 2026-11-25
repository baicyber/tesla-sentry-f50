"""Browser and HTTP regressions for the viewer's cached page, dictionaries and recording list."""
from urllib.parse import urlsplit

from playwright.sync_api import expect


def verify_browser_reload(page, base):
    session = page.context.new_cdp_session(page)
    session.send('Network.enable')
    documents = set()
    statuses = {}
    def request(event):
        if event['type'] == 'Document':
            documents.add(event['requestId'])
    def response(event):
        statuses[event['requestId']] = event['statusCode']
    session.on('Network.requestWillBeSent', request)
    session.on('Network.responseReceivedExtraInfo', response)
    try:
        page.goto(base + '/?cache-test=1')
        page.reload()
        assert [statuses[k] for k in statuses if k in documents] == [200, 304]
    finally:
        session.detach()


def verify_text_cache(request, base):
    version = request.get(base + '/api/ver').json()['v']
    identity = request.get(base + '/', headers={'Accept-Encoding': 'identity'})
    packed = request.get(base + '/', headers={'Accept-Encoding': 'gzip'})
    assert identity.status == packed.status == 200
    assert identity.body() == packed.body()
    assert 'content-encoding' not in identity.headers and packed.headers['content-encoding'] == 'gzip'
    assert int(packed.headers['content-length']) < len(identity.body()) / 2
    assert packed.headers['cache-control'] == 'private, no-cache'
    assert packed.headers['vary'].lower() == 'accept-encoding'
    tag = packed.headers['etag']
    assert identity.headers['etag'] != tag
    cond = {'Accept-Encoding': 'gzip', 'If-None-Match': '"old", W/' + tag}
    hit = request.get(base + '/index.html', headers=cond)
    assert hit.status == 304 and hit.body() == b'' and hit.headers['etag'] == tag
    assert request.head(base + '/', headers=cond).status == 304
    assert request.get(base + '/', headers={'Accept-Encoding': 'identity', 'If-None-Match': tag}).status == 200
    plain = request.get(base + '/', headers={'Accept-Encoding': 'gzip;q=0, *;q=1'})
    assert 'content-encoding' not in plain.headers
    assert request.get(base + '/', headers={'Accept-Encoding': 'identity;q=0, gzip;q=0'}).status == 406
    assert 'no-store' in request.get(base + '/api/ver').headers['cache-control']
    for code in ('de', 'en', 'es', 'fr', 'it', 'ja', 'ko', 'nl', 'ru'):
        path = base + '/i18n/' + code + '.json?v=' + version
        r = request.get(path, headers={'Accept-Encoding': 'gzip'})
        assert r.ok and r.headers['content-encoding'] == 'gzip'
        assert 'immutable' in r.headers['cache-control'] and r.json()['重试']
        assert request.get(path, headers={'Accept-Encoding':'gzip', 'If-None-Match':r.headers['etag']}).status == 304
    return tag


def verify_language_and_rows(browser, state, base):
    context = browser.new_context(storage_state=state, locale='zh-CN', viewport={'width': 1280, 'height': 900})
    try:
        page = context.new_page()
        packs = []
        page.on('request', lambda r: packs.append(urlsplit(r.url).path) if '/i18n/' in r.url else None)
        events = {'SavedClips': [], 'DriveClips': [], 'RecentClips': [], 'SentryClips': [
            {'name': f'2026-11-{day}_12-30-{second}', 'videos': [], 'size': 0,
             'meta': {'reason': reason}}
            for day, second, reason in [('20','02','sentry_panic'), ('20','01','sentry_aware_door'), ('19','00','sentry_panic')]
        ]}
        page.route('**/api/events', lambda route: route.fulfill(json=events))
        page.goto(base)
        cards = page.locator('#grid .card')
        expect(cards).to_have_count(3)
        assert not packs
        page.evaluate("window.originalCard = document.querySelector('#grid .card')")
        dates = page.locator('#dfilters select')
        dates.select_option('2026-11-20')
        expect(cards).to_have_count(2)
        expect(page.locator('#grid .dhead .n')).to_have_text('2 条')
        page.locator('#filters select').select_option('panic')
        expect(cards).to_have_count(1)
        expect(page.locator('#grid .dhead .n')).to_have_text('1 条')
        expect(dates.locator('option[value="all"]')).to_contain_text('2')
        expect(page.locator('#filters option[value="door"]')).to_contain_text('1')
        page.locator('.filter-reset').click()
        expect(cards).to_have_count(3)
        assert page.evaluate("window.originalCard === document.querySelector('#grid .card')")
        page.locator('#gear').click()
        page.locator('#st-lang').click()
        grid = page.locator('#lang-grid')
        page.route('**/i18n/de.json?*', lambda route: route.fulfill(status=503, body='unavailable'))
        grid.locator('button', has_text='Deutsch').click()
        expect(page.locator('#toast')).to_contain_text('重试')
        expect(page.locator('html')).to_have_attribute('lang', 'zh-CN')
        page.unroute('**/i18n/de.json?*')
        pending = []
        page.route('**/i18n/de.json?*', lambda route: pending.append(route))
        with page.expect_request('**/i18n/de.json?*'):
            grid.locator('button', has_text='Deutsch').click()
        grid.locator('button', has_text='English').click()
        expect(page.locator('html')).to_have_attribute('lang', 'en')
        pending.pop().continue_()
        page.unroute('**/i18n/de.json?*')
        page.wait_for_function("languagePacks.get('de')")
        expect(page.locator('html')).to_have_attribute('lang', 'en')
        grid.locator('button', has_text='Deutsch').click()
        expect(page.locator('html')).to_have_attribute('lang', 'de')
        assert packs.count('/i18n/de.json') == 2 and packs.count('/i18n/en.json') == 1
        page.reload()
        expect(page.locator('html')).to_have_attribute('lang', 'de')
        expect(cards).to_have_count(3)
        assert set(packs) == {'/i18n/de.json', '/i18n/en.json'}
    finally:
        context.close()
