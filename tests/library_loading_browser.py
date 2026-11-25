"""Check first-load recovery and background refresh using the real viewer page."""
import argparse
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types

from playwright.sync_api import expect, sync_playwright


def verify(base, browser):
    page = browser.new_page(locale="zh-CN")
    pending = []
    initial_live = []
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/events", lambda route: pending.append(route))
    def live_status(route):
        if not initial_live:
            initial_live.append(route)
        else:
            route.fulfill(json={"running": True})
    page.route("**/api/live", live_status)
    page.route("**/api/system-status", lambda route: route.fulfill(json={"sys": {"cpu_pct": 42}}))

    def next_request():
        deadline = time.monotonic() + 5
        while not pending and time.monotonic() < deadline:
            page.wait_for_timeout(10)
        assert len(pending) == 1, f"Expected one pending event list, got {len(pending)}"
        return pending.pop()

    page.goto(base + "/?cat=SavedClips&date=2026-11-20", wait_until="domcontentloaded")
    status = page.locator("#library-status-text")
    retry = page.get_by_role("button", name="重试", exact=True)
    expect(status).to_have_text("正在加载录像")
    page.locator(".filter-reset").click()
    page.evaluate("Promise.all([refreshLive(), refreshLive()])")
    expect(page.locator("#empty")).to_be_hidden()
    next_request().fulfill(status=503, json={})
    expect(status).to_have_text("录像加载失败")

    with page.expect_request("**/api/events"):
        retry.click()
    page.evaluate("Promise.all([load(), load(), refreshLive()])")
    expect(retry).to_be_hidden()
    next_request().fulfill(content_type="text/html", body="unavailable")
    expect(status).to_have_text("录像加载失败")
    event = {"name": "2026-11-20_12-30-00", "videos": [], "size": 0}
    events = {"SentryClips": [], "SavedClips": [event], "RecentClips": [], "DriveClips": []}
    with page.expect_request("**/api/events"):
        retry.click()
    next_request().fulfill(json=events)
    expect(page.locator("#library-status")).to_be_hidden()
    expect(page.locator("#tabs button")).to_have_count(5)
    expect(page.locator("#tabs .active")).to_contain_text("手动保存")
    expect(page.locator("#dfilters select")).to_have_value("2026-11-20")
    expect(page.locator("#grid .card")).to_have_count(1)
    fallback = page.locator("#grid .card .thumb svg")
    expect(fallback).to_have_attribute("aria-hidden", "true")
    expect(fallback.locator("use")).to_have_attribute("href", "#i-image")
    initial_live[0].fulfill(json={"running": True})
    expect(page.locator("#sys-cpu")).to_have_text("42.0%")

    with page.expect_request("**/api/events"):
        page.evaluate("void (window.refreshDone = refreshLive())")
    next_request().abort()
    page.evaluate("window.refreshDone")
    expect(page.locator("#grid .card")).to_have_count(1)
    expect(page.locator("#library-status")).to_be_hidden()
    page.locator("#tabs [data-category=SentryClips]").click()
    expect(page.locator("#empty")).to_contain_text("暂无归档视频")
    empty_icon = page.locator("#empty .big svg")
    expect(empty_icon).to_have_attribute("aria-hidden", "true")
    expect(empty_icon.locator("use")).to_have_attribute("href", "#i-archive")
    expect(page.locator("#library-status")).to_be_hidden()
    assert not errors, errors
    page.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", help="Path to the Chromium executable")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    with tempfile.TemporaryDirectory(prefix="f50-library-load-") as temporary:
        root = Path(temporary)
        os.environ.update(TSF_ARCHIVE=str(root / "archive"), TSF_CACHE=str(root / "cache"),
                          TSF_AUTH_FILE=str(root / "auth"), TSF_SETTINGS=str(root / "settings.json"))
        if os.name == "nt":
            sys.modules["fcntl"] = types.ModuleType("fcntl")
        spec = importlib.util.spec_from_file_location("viewer", repo / "sentry-viewer.py")
        viewer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(viewer)
        viewer.ensure_dirs()
        with viewer.BoundedServer(("127.0.0.1", 0), viewer.Handler) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=args.chrome)
                    verify(f"http://127.0.0.1:{server.server_port}", browser)
                    browser.close()
            finally:
                server.shutdown()
    print("PASS first load, failure, retry, deduplication, deep link, refresh, empty library")
