// Exercise the shipped buffered-range mapping and the active-media lifecycle boundary.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '..', 'sentry-viewer.py'), 'utf8');

function fn(name) {
  const start = source.indexOf(`function ${name}(`);
  assert(start >= 0, name);
  const end = source.indexOf('\n}', start) + 2;
  return source.slice(start, end);
}

const ctx = vm.createContext({console});
vm.runInContext(`
${['intersectBufferedRanges','bufferedTimelineRanges'].map(fn).join('\n')}
`, ctx);

function media(cam, ranges, options = {}) {
  return {
    dataset: {cam, ...(options.loadFailed ? {loadFailed:'1'} : {})},
    error: options.error ? {} : null,
    buffered: {
      length: ranges.length,
      start: index => ranges[index][0],
      end: index => ranges[index][1],
    },
  };
}

const files = ['front','back','left','right'].map(cam => ({cam, duration:18}));
let clips = [{start:100, length:18, vids:files}];
let active = files.map(file => media(file.cam, [[0, ({front:18,back:16,left:5,right:12})[file.cam]] ]));
ctx.clips = clips; ctx.active = active;
assert.deepEqual(Array.from(vm.runInContext('bufferedTimelineRanges(clips,0,"all",active)',ctx), range => Array.from(range)), [[0,5]],
  'four views show only their common buffered interval');

active = [media('front', [[0,2],[4,8]]), media('back', [[0,1],[3,6]])];
clips = [{start:0, length:10, vids:files.slice(0,2)}];
ctx.clips = clips; ctx.active = active;
assert.deepEqual(Array.from(vm.runInContext('bufferedTimelineRanges(clips,0,"all",active)',ctx), range => Array.from(range)), [[0,1],[4,6]],
  'unbuffered holes remain visible');

active[1] = media('back', [[0,10]], {loadFailed:true});
ctx.active = active;
assert.deepEqual(Array.from(vm.runInContext('bufferedTimelineRanges(clips,0,"all",active)',ctx)), [],
  'a failed view cannot be omitted from the common range');
active[1] = media('back', [[0,10]], {error:true});
ctx.active = active;
assert.deepEqual(Array.from(vm.runInContext('bufferedTimelineRanges(clips,0,"all",active)',ctx)), [],
  'an errored view cannot be omitted from the common range');

clips = [{start:100,length:10,vids:[{cam:'front',duration:10}]},
         {start:125,length:10,vids:[{cam:'front',duration:10}]}];
active = [media('front', [[2,5]])];
ctx.clips = clips; ctx.active = active;
assert.deepEqual(Array.from(vm.runInContext('bufferedTimelineRanges(clips,1,"front",active)',ctx), range => Array.from(range)), [[27,30]],
  'clip-local browser times map onto the full timeline after the clip offset');

const bands = {
  items: [],
  querySelectorAll(selector) { return selector === '.buffered' ? this.items.filter(item => item.className === 'buffered') : []; },
  append(item) { this.items.push(item); },
};
const elements = {'track-bands':bands};
const grid = {videos:active};
const activeVideos = () => grid.videos;
ctx.byId = id => elements[id];
ctx.activeVideos = activeVideos;
ctx.document = {createElement:() => ({style:{},remove(){ bands.items = bands.items.filter(item => item !== this); }})};
vm.runInContext(`
${fn('updateBufferedTimeline')}
let curClips=clips, curClipIx=1, camSel='front', bufferedTimelineSignature=null;
`,ctx);
vm.runInContext('updateBufferedTimeline()',ctx);
assert.equal(bands.items.length,1);
assert.equal(bands.items[0].style.left, (27 / 35 * 100) + '%',
  'DOM placement uses timeline-relative seconds instead of subtracting an epoch twice');
assert.equal(bands.items[0].style.width, (3 / 35 * 100) + '%');

assert.match(source, /#track-bands \{[^}]*height:6px;[^}]*background:var\(--line\)/,
  'the timeline base remains six pixels and uses the theme track color');
assert.match(source, /#track-bands \.buffered \{ z-index:1; background:var\(--muted2\); \}/,
  'buffered sections use the theme-adapted middle gray');
assert.match(source, /#track-bands \.gap \{ z-index:3;/);
assert.match(source, /#track-bands \.trigger \{ z-index:3;/);
assert.match(source, /#seek \{ position:relative; z-index:1;/,
  'the seek thumb stays above the track overlays');

// The production caller supplies activeVideos(), which only reads the current vgrid.
grid.videos = [media('front', [[0,8]])];
clips = [{start:0,length:10,vids:[{cam:'front',duration:10}]}];
ctx.clips = clips;
assert.deepEqual(Array.from(vm.runInContext('bufferedTimelineRanges(clips,0,"front",activeVideos())',ctx), range => Array.from(range)), [[0,8]]);

const oldQuality = grid.videos[0];
grid.videos = [media('front', [[0,2]])];
ctx.active = activeVideos();
assert.deepEqual(Array.from(vm.runInContext('bufferedTimelineRanges(clips,0,"front",activeVideos())',ctx), range => Array.from(range)), [[0,2]],
  'after a quality change only the replacement vgrid video contributes');
grid.videos = [media('back', [[0,6]])];
ctx.active = activeVideos();
assert.deepEqual(Array.from(vm.runInContext('bufferedTimelineRanges(clips,0,"front",activeVideos())',ctx)), [],
  'after a camera change the previous camera cache is absent');
assert.equal(oldQuality.buffered.end(0), 8, 'released media may retain bytes but is no longer in activeVideos');

// Server-only prewarm is represented by no media element and never enters activeVideos().
const serverPrewarm = {file:{url:'next.mp4'}, quality:'360p', video:null, done:true};
grid.videos = [];
ctx.prefetch = serverPrewarm;
assert.deepEqual(Array.from(vm.runInContext('bufferedTimelineRanges(clips,0,"front",activeVideos())',ctx)), [],
  'server prewarm metadata does not create browser-buffered ranges');

console.log('PASS: four-view intersection, holes, failures, clip offsets, media lifecycle, and server-only prewarm');
