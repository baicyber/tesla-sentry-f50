// Run the shipped timeline functions against overlapping and missing footage.
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
${['clipEpoch','timelineOf','arrangeTimeline','timelineGaps','locateWall','bufferedPosition','hasBufferedTime'].map(fn).join('\n')}
${source.match(/^const videoDuration = .+;$/m)[0]}
let curClips = [], allCameras=['front','back'], camSel='front';
const camLabel = cam=>cam;
function groupVideos(videos) { return [...new Set(videos.map(v=>v.key))].map(key=>({key,vids:videos.filter(v=>v.key===key)})); }
`, ctx);
function clips(rows) {
  ctx.rows = rows;
  vm.runInContext(`curClips=timelineOf(rows.map(([second,durations])=>{
    const key='2026-11-21_17-'+String(Math.floor(second/60)).padStart(2,'0')+'-'+String(second%60).padStart(2,'0');
    return {name:key,videos:Object.entries(durations).map(([cam,duration])=>({key,cam,duration}))}; }));`, ctx);
}
clips([[3,{front:59.38,back:59.52}],[25,{front:60,back:60}],[95,{front:60,back:60}]]);
assert.equal(vm.runInContext('curClips[0].length',ctx),59.52);
assert.equal(vm.runInContext('locateWall(curClips[0].start+45).ix',ctx),0,'tail must remain seekable');
assert.equal(vm.runInContext('locateWall(curClips[1].start+20).ix',ctx),1);
assert.equal(vm.runInContext('curClips[2].start-curClips[1].start-curClips[1].length',ctx),10,'retain real gap after overlap');
clips([[0,{front:60,back:60}],[61,{front:60,back:60}]]);
assert.equal(vm.runInContext('timelineGaps().length',ctx),0,'whole-second filename uncertainty must not become confirmed missing footage');
clips([[0,{front:58,back:60}],[61,{front:60,back:60}]]);
assert.equal(vm.runInContext('timelineGaps().length',ctx),1,'large camera gap remains visible');
vm.runInContext("curClips[1].vids[0].gap_before={previous:curClips[0].key,basis:'sequence',seconds:.69,minimum:.6};",ctx);
assert.equal(vm.runInContext('timelineGaps()[0].seconds',ctx),3,'geometry remains the actual unplayable interval');
assert.equal(vm.runInContext('timelineGaps()[0].label',ctx),'缺录约 0.69 秒','report measured evidence rather than rounded filename subtraction');
clips([[0,{front:60,back:60}],[60,{front:60}],[120,{front:60,back:60}]]);
vm.runInContext("camSel='all'",ctx);
assert.equal(vm.runInContext('timelineGaps()[0].seconds',ctx),60,'missing camera keeps other views accessible');
assert.equal(vm.runInContext('locateWall(curClips[1].start+30).ix',ctx),1);
clips([[0,{front:0}],[70,{front:30}]]);
vm.runInContext('curClips[0].vids[0].duration=30; arrangeTimeline(curClips,curClips[0]);',ctx);
assert.equal(vm.runInContext('curClips[1].start-curClips[0].start',ctx),70,'late duration preserves source gap');
vm.runInContext('curClips[0].vids[0].duration=90; arrangeTimeline(curClips,curClips[0]);',ctx);
assert.equal(vm.runInContext('curClips[1].start-curClips[0].start',ctx),90,'late long duration creates no artificial gap');
vm.runInContext(`const preview={dataset:{mse:'1'},buffered:{length:1,start:()=>13.01,end:()=>16}};`,ctx);
assert.equal(vm.runInContext('bufferedPosition(preview,13)',ctx),13.01);
assert.equal(vm.runInContext('hasBufferedTime(preview,13,14.5)',ctx),true,'tiny encoded start offset must not stall four views');
assert.equal(vm.runInContext('hasBufferedTime(preview,16)',ctx),false,'buffer end stays exclusive');
assert.equal(vm.runInContext('hasBufferedTime(preview,12.99)',ctx),false,'never cover an absent prior part');
console.log('PASS: full tails, overlap, real gaps and missing cameras');
