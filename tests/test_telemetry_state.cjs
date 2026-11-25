// Exercise the playback telemetry state with real shipped functions and pending fetches.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'sentry-viewer.py'), 'utf8');

function fn(name) {
  const start = source.indexOf(`function ${name}(`);
  assert(start >= 0, name);
  const end = source.indexOf('\n}', start) + 2;
  assert(end > 1, `${name} end`);
  return source.slice(start, end);
}

class Element {
  constructor(id = '') {
    this.id = id;
    this.textContent = '';
    this.title = '';
    this.style = {};
    this.children = [];
    this.attributes = {};
    const values = new Set();
    this.classList = {
      add: (...names) => names.forEach(name => values.add(name)),
      remove: (...names) => names.forEach(name => values.delete(name)),
      contains: name => values.has(name),
      toggle: (name, force) => {
        const enabled = force === undefined ? !values.has(name) : !!force;
        if (enabled) values.add(name); else values.delete(name);
        return enabled;
      }
    };
  }
  set textContent(value) { this._textContent = String(value); }
  get textContent() { return this._textContent || ''; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
  querySelector(selector) { return selector === 'svg' ? (this.svg ||= new Element('svg')) : null; }
  focus() {}
}

const frame = [0, 'D', 42, 5, 4, 1, 1, 2, 0, 0, 0, 0.1, 0.2, 0.3];
const telemetry = {ok:true, frames:[frame], brake_ratio:8, count:120, fps:30};

function player(category = 'SavedClips') {
  const nodes = new Map();
  const ids = ['tp-spd','tp-gear','tp-thr-n','tp-brk-n','tp-sig-n','tp-ap-n','tp-brk','tp-bl','tp-br','tp-st',
    'tp-rows','tpanel','tp-details','tp-details-shade','tp-fold','player','seek','cambar','track-bands','ic-rows',
    'clipbar','ptime','track-end','ic-fold','startmask','ppbtn'];
  ids.forEach(id => nodes.set(id, new Element(id)));
  const requests = [];
  const ctx = vm.createContext({
    document: {
      getElementById: id => nodes.get(id),
      createElement: tag => new Element(tag),
      createTextNode: text => ({textContent:String(text)})
    },
    fetch: url => new Promise(resolve => requests.push({url, resolve})),
    byId: id => nodes.get(id), t: value => value,
    reasonLabel: value => value, isSentryEv: () => false, camLabel: value => value,
    CAM_IDX: {}, layoutPlayer() {},
    clearInterval() {}, releaseDemand() {}, resetPlaybackClock() {}, activeVideos: () => [],
    releaseVideo() {}, dropPrefetch() {}, updateRecordingActions() {}, updateControls() {},
    setInterval() {}, syncTimer:null, liveTimer:null, liveStream:null, indexRequest:null,
    clipRequest:null, gapRequest:null, gapSignature:'', gapEvidence:new Map(), prefetch:null,
    sessionId:0, renderId:0, seekPointerStart:null, cur:null, curClips:[], curClipIx:0,
    libraryCat:category, TEL:null, TEL_META:{}, TEL_KEY:'', telRequestId:0, telT0:null,
    triggerTime:null, triggerSource:null, playerRecording:null, wantedPlay:false, clipOffset:0, clockVideo:null, atEnd:false,
    liveDirty:false, liveSignature:'', liveReading:false, editingSeek:false, overviewPosition:null
  });
  vm.runInContext([
    'function currentClip() { return curClips[curClipIx]; }',
    fn('fmtTelDate'), fn('renderTelDetails'), fn('resetHud'), fn('loadTelemetry'), fn('renderHud'),
    fn('playbackOffset'), fn('updateHud'), fn('telFrameAt'), fn('closeTelDetails'), fn('closePlayback')
  ].join('\n'), ctx);
  return {ctx, nodes, requests};
}

function setClip(ctx, {key, url, revision = '1:100', event = 'clip-a'}) {
  ctx.curClips = [{key, vids:[{cam:'front', url, revision}], event:{name:event}, length:60}];
  ctx.curClipIx = 0;
  ctx.cur = {name:event};
}

function respond(request, value = telemetry) {
  request.resolve({json:async()=>value});
}

async function flush() {
  for (let i = 0; i < 6; i++) await Promise.resolve();
}

(async () => {
  const same = player();
  setClip(same.ctx, {key:'clip-a', url:'/media/front-2026-11-23_10-00-00.mp4'});
  same.ctx.loadTelemetry('/media/front-2026-11-23_10-00-00.mp4', {});
  assert.equal(same.nodes.get('tpanel').classList.contains('show'), true, 'ordinary categories show the placeholder while loading');
  same.ctx.loadTelemetry('/media/back-2026-11-23_10-00-00.mp4', {});
  assert.equal(same.requests.length, 1, 'pending telemetry is reused when switching cameras');
  same.nodes.get('tp-details').classList.add('open');
  respond(same.requests[0]); await flush();
  assert.equal(vm.runInContext('TEL.frames.length', same.ctx), 1);
  const detailText = same.nodes.get('tp-rows').children.map(row => row.children.map(child => child.textContent).join('')).join('\n');
  assert.match(detailText, /车速42 km\/h/);
  assert.match(detailText, /刹车占比8%/);
  same.ctx.updateHud(1);
  const refreshedDetails = same.nodes.get('tp-rows').children.map(row => row.children.map(child => child.textContent).join('')).join('\n');
  assert.match(refreshedDetails, /刹车占比8%/, 'frame refresh retains the clip statistics');
  same.ctx.loadTelemetry('/media/back-2026-11-23_10-00-00.mp4', {});
  assert.equal(same.requests.length, 1, 'completed telemetry is reused when switching cameras');

  const stale = player();
  setClip(stale.ctx, {key:'clip-a', url:'/media/front-2026-11-23_10-00-00.mp4'});
  stale.ctx.loadTelemetry('/media/front-2026-11-23_10-00-00.mp4', {});
  const first = stale.requests[0];
  setClip(stale.ctx, {key:'clip-b', url:'/media/front-2026-11-23_10-01-00.mp4', event:'clip-b'});
  stale.nodes.get('tp-details').classList.add('open');
  stale.ctx.loadTelemetry('/media/front-2026-11-23_10-01-00.mp4', {});
  assert.equal(stale.nodes.get('tp-details').classList.contains('open'), true, 'open details stay open across clips');
  assert.equal(stale.nodes.get('tp-spd').textContent, '—', 'new clips clear the previous HUD immediately');
  respond(first); await flush();
  assert.equal(vm.runInContext('TEL', stale.ctx), null, 'old clip response cannot repopulate the HUD');
  assert.equal(stale.nodes.get('tp-gear').textContent, '—');
  respond(stale.requests[1]); await flush();
  assert.equal(stale.nodes.get('tp-spd').textContent, '42');

  const revised = player();
  setClip(revised.ctx, {key:'clip-a', url:'/media/front-2026-11-23_10-00-00.mp4', revision:'1:100'});
  revised.ctx.loadTelemetry('/media/front-2026-11-23_10-00-00.mp4', {});
  setClip(revised.ctx, {key:'clip-a', url:'/media/front-2026-11-23_10-00-00.mp4', revision:'2:200'});
  revised.ctx.loadTelemetry('/media/front-2026-11-23_10-00-00.mp4', {});
  assert.equal(revised.requests.length, 2, 'source revision invalidates same-URL telemetry');

  const sentry = player('SentryClips');
  setClip(sentry.ctx, {key:'sentry-a', url:'/media/front-2026-11-23_10-00-00.mp4'});
  sentry.ctx.loadTelemetry('/media/front-2026-11-23_10-00-00.mp4', {});
  assert.equal(sentry.nodes.get('tpanel').classList.contains('show'), false, 'SentryClips does not reserve a status bar');
  respond(sentry.requests[0]); await flush();
  assert.equal(vm.runInContext('telFrameAt(0)[1]', sentry.ctx), 'D', 'Sentry telemetry remains available to the watermark');

  const closed = player();
  setClip(closed.ctx, {key:'clip-a', url:'/media/front-2026-11-23_10-00-00.mp4'});
  closed.ctx.loadTelemetry('/media/front-2026-11-23_10-00-00.mp4', {});
  const pending = closed.requests[0];
  closed.ctx.closePlayback();
  respond(pending); await flush();
  assert.equal(vm.runInContext('TEL', closed.ctx), null, 'closing playback invalidates pending telemetry');
  assert.equal(closed.nodes.get('tp-spd').textContent, '—');

  console.log('PASS: telemetry reuse, stale-response guards, revisions and Sentry watermark data');
})().catch(error => { console.error(error); process.exitCode = 1; });
