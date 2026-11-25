// Verify four-view failure isolation, healthy buffering synchronization, single-cell retry, and video reuse.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '..', 'sentry-viewer.py'), 'utf8');

function fn(name) {
  let start = source.indexOf(`async function ${name}(`);
  if (start < 0) start = source.indexOf(`function ${name}(`);
  assert(start >= 0, name);
  const end = source.indexOf('\n}', start) + 2;
  return source.slice(start, end);
}

function makeVideoStub(cam, opts = {}) {
  const listeners = new Map();
  return {
    dataset: {
      cam,
      fileUrl: opts.fileUrl || `/media/SentryClips/ev/clip-${cam}.mp4`,
      quality: opts.quality || '480p',
      ...(opts.mse ? {mse: '1'} : {}),
      ...(opts.loadFailed ? {loadFailed: '1'} : {}),
      ...(opts.positioning ? {positioning: '1'} : {}),
      ...(opts.startBuffer ? {startBuffer: '1'} : {}),
      ...(opts.outside ? {outside: '1'} : {}),
    },
    currentTime: opts.currentTime ?? 10,
    duration: opts.duration ?? 60,
    readyState: opts.readyState ?? 4,
    paused: opts.paused ?? false,
    ended: opts.ended ?? false,
    seeking: opts.seeking ?? false,
    error: opts.error ? {} : null,
    playbackRate: 1,
    _src: opts.src || 'blob:mock',
    _streamAbort: opts.abortController || new AbortController(),
    pause() { this.paused = true; },
    play() { this.paused = false; return Promise.resolve(); },
    getAttribute(k) { return k === 'src' ? this._src : null; },
    removeAttribute(k) { if (k === 'src') this._src = null; },
    load() {},
    addEventListener(type, cb) { listeners.set(type, cb); },
    removeEventListener(type) { listeners.delete(type); },
    parentElement: {querySelector: () => ({hidden: false})},
  };
}

// 1. Test syncOverview: healthy buffering waits together; terminal loadFailed/error is excluded from barrier.
{
  const played = [];
  const ctx = vm.createContext({
    camSel: 'all',
    wantedPlay: true,
    overviewPosition: null,
    clipOffset: 10,
    curQ: '480p',
    PREVIEW_HD_RECOVERY_SECONDS: 2,
    activeVideos: () => ctx._videos,
    resetPlaybackClock() {},
    bufferedPosition: (_v, t) => t,
    hasPreviewLead: v => !v._needsLead,
    previewReadyToPlay: v => v.readyState >= 2 && !v._needsLead,
    tryPlay(v, sync) { played.push([v.dataset.cam, sync]); v.paused = false; },
    console,
  });
  vm.runInContext([fn('holdOverview'), fn('syncOverview')].join('\n'), ctx);

  // Case A: one healthy camera is buffering (readyState=2) -> must hold and return false.
  ctx._videos = [
    makeVideoStub('front', {currentTime: 10}),
    makeVideoStub('back', {currentTime: 10, readyState: 2}),
    makeVideoStub('left_repeater', {currentTime: 10}),
    makeVideoStub('right_repeater', {currentTime: 10}),
  ];
  assert.equal(vm.runInContext('syncOverview(_videos)', ctx), false,
    'healthy buffering camera must still pause overview to stay synchronized');
  assert.equal(ctx._videos[0].paused, true);
  assert.equal(ctx.overviewPosition, 10);

  // Case B: back camera becomes terminally loadFailed while the other 3 are ready -> 3 healthy cameras resume!
  ctx._videos[1].dataset.loadFailed = '1';
  played.length = 0;
  assert.equal(vm.runInContext('syncOverview(_videos)', ctx), true,
    'terminal failure must be excluded so remaining healthy cameras resume');
  assert.equal(ctx.overviewPosition, null);
  assert.deepEqual(played, [['front', true], ['left_repeater', true], ['right_repeater', true]]);

  // Case C: small drift <= 0.15s does not force-pause overview; > 0.15s still holds to realign.
  ctx._videos = [
    makeVideoStub('front', {currentTime: 12.0}),
    makeVideoStub('back', {currentTime: 12.10}),
  ];
  assert.equal(vm.runInContext('syncOverview(_videos)', ctx), true,
    'small drift within 0.15s should be smoothed by playbackRate instead of pausing');
  ctx._videos[1].currentTime = 12.25;
  ctx._videos[1]._needsLead = true;
  ctx._videos[1].dataset.mse = '1';
  assert.equal(vm.runInContext('syncOverview(_videos)', ctx), false,
    'drift > 0.15s still pauses overview to realign');

  // Case D: all cameras failed -> syncOverview returns true without throwing or locking.
  ctx._videos = [
    makeVideoStub('front', {loadFailed: true}),
    makeVideoStub('back', {error: true}),
  ];
  ctx.overviewPosition = null;
  assert.equal(vm.runInContext('syncOverview(_videos)', ctx), true);
}

// 2. Test retryCamera: only rebuilds the failed cell, leaving the other 3 healthy videos untouched.
{
  const released = [];
  const mounted = [];
  const makeCellDom = (cam, video) => {
    const cell = {
      dataset: {cam},
      _video: video,
      querySelector: sel => sel === 'video' ? cell._video : null,
      replaceWith(next) {
        const ix = gridChildren.indexOf(cell);
        if (ix >= 0) gridChildren[ix] = next;
      },
    };
    return cell;
  };
  const vFront = makeVideoStub('front', {currentTime: 15});
  const vBack = makeVideoStub('back', {currentTime: 0, loadFailed: true});
  const vLeft = makeVideoStub('left_repeater', {currentTime: 15});
  const vRight = makeVideoStub('right_repeater', {currentTime: 15});
  const gridChildren = [
    makeCellDom('front', vFront),
    makeCellDom('back', vBack),
    makeCellDom('left_repeater', vLeft),
    makeCellDom('right_repeater', vRight),
  ];
  const ctx = vm.createContext({
    curClips: [{vids: ['front', 'back', 'left_repeater', 'right_repeater'].map(cam => ({cam, url: `/m/${cam}.mp4`, duration: 60}))}],
    curClipIx: 0,
    camSel: 'all',
    renderId: 7,
    wantedPlay: true,
    overviewPosition: null,
    byId: id => id === 'vgrid' ? {children: gridChildren} : null,
    currentClip: () => ctx.curClips[0],
    playbackOffset: () => 15.2,
    activeVideos: () => gridChildren.map(c => c._video).filter(Boolean),
    releaseVideo(v) { released.push(v.dataset.cam); },
    mountCell(cam, file, _p, offset, gen, seekStartup) {
      mounted.push({cam, url: file.url, offset, gen, seekStartup});
      return makeCellDom(cam, makeVideoStub(cam, {currentTime: offset, readyState: 1}));
    },
    showClip() { throw new Error('single-cell retry in overview must not call full showClip'); },
    updateBufferedTimeline() {},
    layoutPlayer() {},
    updateDemand() {},
    console,
  });
  vm.runInContext(fn('retryCamera'), ctx);
  vm.runInContext("retryCamera('back')", ctx);
  assert.deepEqual(released, ['back'], 'only the failed camera video may be released');
  assert.equal(mounted.length, 1);
  assert.equal(mounted[0].cam, 'back');
  assert.equal(mounted[0].offset, 15.2);
  assert.strictEqual(gridChildren[0]._video, vFront, 'healthy front video instance must stay intact');
  assert.strictEqual(gridChildren[2]._video, vLeft, 'healthy left video instance must stay intact');
  assert.strictEqual(gridChildren[3]._video, vRight, 'healthy right video instance must stay intact');
  assert.equal(ctx.overviewPosition, 15.2, 'overview aligns to current offset while retrying the failed cell');
}

// 3. Test setCam -> showClip reuse: preserves healthy ready video when toggling single <-> overview.
{
  const frontAbort = new AbortController();
  const vFront = makeVideoStub('front', {currentTime: 18, readyState: 4, abortController: frontAbort});
  const released = [];
  const mountedArgs = [];
  const grid = {
    children: [],
    classList: {remove() {}, toggle() {}},
    replaceChildren(...cells) { this.children = cells; },
  };
  const ctx = vm.createContext({
    clipRequest: null,
    renderId: 1,
    curClipIx: 0,
    curClips: [{
      key: 'k0', length: 60, event: {name: 'ev1', meta: {}},
      vids: ['front', 'back'].map(cam => ({cam, url: `/media/SentryClips/ev/clip-${cam}.mp4`, duration: 60})),
    }],
    cur: {name: 'ev1', meta: {}},
    allCameras: ['front', 'back'],
    camSel: 'all',
    curQ: '480p',
    clipOffset: 18,
    atEnd: false,
    overviewPosition: null,
    lastTick: 0,
    prefetch: null,
    AbortController,
    performance: {now: () => 1000},
    byId: id => id === 'vgrid' ? grid : {querySelectorAll: () => [], classList: {remove() {}}},
    activeVideos: () => [vFront],
    resetPlaybackClock() {},
    updateDemand() {},
    dropPrefetch() {},
    releaseVideo(v) { released.push(v.dataset.cam); if (v._streamAbort) v._streamAbort.abort(); },
    mountCell(cam, _file, _prep, _off, _gen, _seek, reused) {
      mountedArgs.push({cam, reused});
      return {dataset: {cam}, querySelector: () => reused || makeVideoStub(cam)};
    },
    layoutPlayer() {}, cameraBar() {}, renderInfo() {}, drawTimeline() {}, updateControls() {}, loadTelemetry() {},
    console,
  });
  vm.runInContext(fn('showClip'), ctx);
  vm.runInContext('showClip(0, 18, false, false, true)', ctx);
  assert.strictEqual(mountedArgs[0].reused, vFront, 'ready front video must be reused when switching to overview');
  assert.strictEqual(mountedArgs[1].reused, null, 'missing back video must be freshly mounted');
  assert.deepEqual(released, [], 'reused video must not be released');
  assert.equal(frontAbort.signal.aborted, false, 'reused video stream abort controller must stay active');

  // Quality change (reuseExisting=false or different curQ) must NOT reuse.
  mountedArgs.length = 0;
  ctx.curQ = '720p';
  vm.runInContext('showClip(0, 18, false, false, true)', ctx);
  assert.strictEqual(mountedArgs[0].reused, null, 'different quality must not reuse old video');
  assert.deepEqual(released, ['front']);
  assert.equal(frontAbort.signal.aborted, true, 'discarded video stream must be aborted on release');
}

// 4. Test tick() tail advance with 1 loadFailed camera + 3 ended cameras.
{
  let advanced = false;
  const ctx = vm.createContext({
    curClips: [{length: 60}],
    curClipIx: 0,
    atEnd: false,
    lastTick: 0,
    wantedPlay: true,
    camSel: 'all',
    clipOffset: 59.95,
    baseRate: 1,
    clockVideo: null,
    videoTimes: new WeakMap(),
    editingSeek: false,
    performance: {now: () => 200},
    currentClip: () => ctx.curClips[0],
    activeVideos: () => [
      makeVideoStub('front', {ended: true, currentTime: 59.95}),
      makeVideoStub('back', {loadFailed: true, currentTime: 0, readyState: 0}),
      makeVideoStub('left_repeater', {ended: true, currentTime: 59.95}),
      makeVideoStub('right_repeater', {ended: true, currentTime: 59.95}),
    ],
    syncOverview: () => true,
    tryPlay() {},
    advanceClip() { advanced = true; },
    prepareNext() {},
    updateControls() {},
    updateHud() {},
    console,
  });
  vm.runInContext(fn('tick') + '\ntick();', ctx);
  assert.equal(advanced, true, 'clip must advance to next minute when 3 cameras end and 1 is loadFailed');
}

// 5. Test syncOverview background-tab guard & MSE readyState=2 symmetry (prevents hold/play ping-pong).
{
  const played = [];
  const ctx = vm.createContext({
    camSel: 'all',
    wantedPlay: true,
    overviewPosition: null,
    clipOffset: 12,
    curQ: '480p',
    PREVIEW_HD_RECOVERY_SECONDS: 2,
    document: {hidden: true},
    activeVideos: () => ctx._videos,
    resetPlaybackClock() {},
    bufferedPosition: (_v, t) => t,
    hasPreviewLead: v => !v._needsLead,
    previewReadyToPlay: v => v.readyState >= 2 && !v._needsLead,
    tryPlay(v, sync) { played.push([v.dataset.cam, sync]); v.paused = false; },
    console,
  });
  vm.runInContext([fn('holdOverview'), fn('syncOverview')].join('\n'), ctx);

  // While tab is hidden, syncOverview must not thrash holdOverview() even if readyState drops to 2 or clocks drift.
  ctx._videos = [
    makeVideoStub('front', {mse: true, currentTime: 12.0, readyState: 2}),
    makeVideoStub('back', {mse: true, currentTime: 12.4, readyState: 2}),
  ];
  assert.equal(vm.runInContext('syncOverview(_videos)', ctx), true,
    'hidden tab must not enter holdOverview() thrash loop');
  assert.equal(ctx.overviewPosition, null);

  // When tab becomes visible with MSE readyState=2 and healthy preview lead (no drift), it must not hold.
  ctx.document.hidden = false;
  ctx._videos[1].currentTime = 12.02;
  assert.equal(vm.runInContext('syncOverview(_videos)', ctx), true,
    'visible MSE video at readyState=2 with sufficient preview lead must not ping-pong into holdOverview');
  assert.equal(ctx.overviewPosition, null);

  // If preview lead actually drops below threshold at readyState=2, it must hold.
  ctx._videos[1]._needsLead = true;
  assert.equal(vm.runInContext('syncOverview(_videos)', ctx), false,
    'MSE video at readyState=2 without preview lead must hold overview');
  assert.notEqual(ctx.overviewPosition, null);
}

// 6. Test iOS ManagedMediaSource support (disableRemotePlayback, startstreaming, QuotaExceededError recovery)
//    and desktop visibilitychange wake-up in previewStream().
{
  let now = 1000, tickFn, media, failures = 0;
  const requests = [], removed = [], docListeners = new Map(), mediaListeners = new Map();
  const ranges = [[0, 30]];
  let failQuotaOnce = true;
  const buffer = {
    updating: false,
    buffered: {get length() { return ranges.length; }, start: i => ranges[i][0], end: i => ranges[i][1]},
    appendBuffer() {
      if (failQuotaOnce) {
        failQuotaOnce = false;
        const err = new Error('quota');
        err.name = 'QuotaExceededError';
        throw err;
      }
      ranges[0][1] += 1;
    },
    remove(s, e) { removed.push([s, e]); ranges[0][0] = e; },
    addEventListener() {},
  };
  const video = {dataset: {}, currentTime: 26, seeking: false, disableRemotePlayback: false,
    addEventListener() {}, buffered: buffer.buffered};
  class FakeManagedMediaSource {
    static isTypeSupported() { return true; }
    constructor() { media = this; this.readyState = 'open'; this.streaming = false; }
    addEventListener(type, cb) { mediaListeners.set(type, cb); }
    addSourceBuffer() { return buffer; }
  }
  const ctx = vm.createContext({
    AbortController, URLSearchParams, video, buffer,
    window: {MediaSource: undefined, ManagedMediaSource: FakeManagedMediaSource},
    document: {
      hidden: false,
      addEventListener(type, cb) { docListeners.set(type, cb); },
    },
    URL: {createObjectURL: () => 'blob:ios', revokeObjectURL() {}},
    performance: {now: () => now},
    setTimeout: () => 1, clearTimeout() {},
    setInterval: cb => { tickFn = cb; return 1; }, clearInterval() {},
    fetch: (url, {signal}) => new Promise((resolve, reject) => {
      const req = {url, resolve, reject, aborted: false};
      requests.push(req);
      signal.addEventListener('abort', () => { req.aborted = true; reject(Error('aborted')); });
    }),
    previewMime: () => 'video/mp4; codecs="avc1.42C01E"',
    hasPreviewLead: () => true,
    bufferedPosition: (_v, at) => at,
    updateBufferedTimeline() {},
    PREVIEW_START_SECONDS: 1.5, PREVIEW_HD_START_SECONDS: 2, PREVIEW_HD_READ_AHEAD_SECONDS: 18,
    editingSeek: false, wantedPlay: true, baseRate: 1, camSel: 'front',
    overviewPosition: null, watchId: 'b'.repeat(32), onFailure: () => { failures++; },
  });
  vm.runInContext([fn('supportsPreviewMse'), fn('previewStream')].join('\n'), ctx);
  assert.equal(vm.runInContext("supportsPreviewMse('480p')", ctx), true,
    'iOS ManagedMediaSource must be detected as MSE-capable');
  const url = vm.runInContext("previewStream(video,{url:'clip',duration:60},1,'480p',new AbortController().signal,onFailure)", ctx);
  assert.equal(url, 'blob:ios');
  assert.equal(video.disableRemotePlayback, true,
    'ManagedMediaSource must set video.disableRemotePlayback=true before attaching blob URL');
  delete video.dataset.startBuffer;
  mediaListeners.get('sourceopen')();
  // First fill prunes [0, 16] since at(26) - start(0) > 25.
  assert.deepEqual(removed, [[0, 16]], 'ManagedMediaSource should proactively prune far-behind frames');
  // Because media.streaming === false and bufferedLead (4s) >= 3.5s, ordinary read-ahead pauses until startstreaming.
  tickFn();
  assert.equal(requests.length, 0, 'ManagedMediaSource respects streaming=false once runway >= 3.5s');
  // WebKit fires startstreaming -> immediately resumes fetching!
  media.streaming = true;
  mediaListeners.get('startstreaming')();
  assert.equal(requests.length, 1, 'startstreaming must immediately resume read-ahead');
  // Resolve with QuotaExceededError -> must evict & back off instead of failing camera.
  requests[0].resolve({status: 200, ok: true, arrayBuffer: async () => new ArrayBuffer(16)});
  setTimeout(() => {
    assert.equal(failures, 0, 'QuotaExceededError must recover via eviction instead of terminal failure');
    assert(removed.length >= 2, 'QuotaExceededError must trim played range behind playhead');
    // Returning to foreground via visibilitychange clears retryAt backoff and fetches immediately!
    ctx.document.hidden = false;
    docListeners.get('visibilitychange')();
    assert.equal(requests.length, 2, 'visibilitychange to visible must clear backoff and resume buffering');
    console.log('PASS: overview failure isolation, single-cell retry, video reuse, tail advance, bg-tab sync, and iOS ManagedMediaSource');
  }, 20);
}

// 7. Test 4-view orig prepareNext prefetches all 4 cameras and showClip consumes them.
{
  const created = [];
  const nextVids = ['front', 'back', 'left_repeater', 'right_repeater'].map(cam => ({cam, url: `/media/DriveClips/ev/next-${cam}.mp4`, duration: 60}));
  const ctx = vm.createContext({
    curClips: [
      {key: 'c0', length: 60, vids: [{cam: 'front', url: '/media/DriveClips/ev/c0-front.mp4', duration: 60}]},
      {key: 'c1', length: 60, vids: nextVids},
    ],
    curClipIx: 0,
    clipOffset: 48,
    camSel: 'all',
    curQ: 'orig',
    baseRate: 1,
    wantedPlay: true,
    editingSeek: false,
    prefetch: null,
    clipRequest: new AbortController(),
    currentClip: () => ctx.curClips[0],
    usesPreviewParts: () => false,
    readySource: async file => file.url + '?watch=test',
    makeVideo() { const v = {src: '', loaded: false, load() { this.loaded = true; }}; created.push(v); return v; },
    updateDemand() {},
    console,
  });
  vm.runInContext(fn('prepareNext') + '\nprepareNext();', ctx);
  setTimeout(() => {
    assert.equal(created.length, 4, 'four-view orig prepareNext must prefetch all 4 cameras before clip boundary');
    assert.equal(ctx.prefetch.videos.size, 4);
    assert(created.every(v => v.loaded && v.src.includes('?watch=test')));
  }, 15);
}
