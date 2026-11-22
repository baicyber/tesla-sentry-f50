// Exercise the shipped request loop with a controlled clock and pending fetches.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '..', 'sentry-viewer.py'), 'utf8');
const begin = source.indexOf('function previewStream(');
const code = source.slice(begin, source.indexOf('\n}', begin) + 2);

async function player() {
  let now = 0, tick, media;
  const requests = [], deadlines = [];
  const ranges = [];
  const buffer = {updating:false, buffered:{get length() { return ranges.length; }, start:i=>ranges[i][0], end:i=>ranges[i][1]}};
  const video = {dataset:{}, currentTime:10.4, seeking:false, addEventListener(){}, buffered:buffer.buffered};
  const ctx = vm.createContext({AbortController, URLSearchParams, video, buffer,
    URL:{createObjectURL:()=> 'blob:test', revokeObjectURL(){}},
    MediaSource:class {
      constructor() { media=this; this.readyState='open'; }
      addEventListener(type, cb) { this.open=cb; }
      addSourceBuffer() { return buffer; }
    },
    performance:{now:()=>now},
    setTimeout:(cb, ms)=>{ deadlines.push(ms); return deadlines.length; },clearTimeout(){},
    setInterval:cb=>{tick=cb; return 1;},clearInterval(){},
    fetch:(url,{signal})=>new Promise((resolve,reject)=>{
      const request={url,resolve,aborted:false}; requests.push(request);
      signal.addEventListener('abort',()=>{request.aborted=true; reject(Error('aborted'));});
    }),
    previewMime:()=>'',supportsPreviewMse:()=>true,hasPreviewLead:()=>false,
    bufferedPosition:(_v,at)=>at,PREVIEW_START_SECONDS:1.5,PREVIEW_HD_START_SECONDS:2.5,
    PREVIEW_HD_READ_AHEAD_SECONDS:12,editingSeek:false,wantedPlay:true,baseRate:1,
    camSel:'front',overviewPosition:null,
  });
  vm.runInContext(code,ctx);
  vm.runInContext("previewStream(video,{url:'clip',duration:60},1,'360p',new AbortController().signal,()=>{throw Error('unexpected failure')})",ctx);
  ranges.push([10,11]);
  delete video.dataset.startBuffer;
  media.open();
  const flush=async()=>{await Promise.resolve();await Promise.resolve();};
  return {ctx,requests,deadlines,video,ranges,tick:()=>tick(),flush,clock:t=>{now=t;}};
}

(async()=>{
  const p=await player();
  assert.equal(p.deadlines[0],30000);
  p.clock(29000);
  p.video.dataset.seekRevision='1';p.video.dataset.seekTarget='11.2';p.video.dataset.startBuffer='1';
  p.video.currentTime=11.2;
  p.tick();await p.flush();p.tick();
  assert.equal(p.requests[0].aborted,true);
  assert.match(p.requests[1].url,/ahead=0/);
  assert.equal(p.deadlines[1],30000,'explicit seek gets a fresh budget after replacing old read-ahead');

  const retry=await player();
  retry.clock(24000);retry.requests[0].resolve({status:503});await retry.flush();retry.tick();
  assert.equal(retry.deadlines[1],6000,'503 retries share one total wait budget');

  const paused=await player();
  paused.ctx.wantedPlay=false;paused.tick();await paused.flush();
  paused.clock(35000);paused.ctx.wantedPlay=true;paused.tick();
  assert.equal(paused.deadlines[1],30000,'cancelled read-ahead must not consume the later resume budget');

  const group=await player();
  group.ctx.camSel='all';group.ctx.overviewPosition=10.4;
  group.tick();await group.flush();group.tick();
  assert.equal(group.requests[0].aborted,true,'group recovery must promote its existing ordinary read-ahead');
  assert.match(group.requests[1].url,/ahead=1&prime=1/,'a paused camera without startBuffer still needs recovery priority');
  group.ranges[0][1]=15;group.ctx.hasPreviewLead=()=>true;
  group.tick();await group.flush();group.tick();
  assert.equal(group.requests[1].aborted,true,'a ready camera yields bandwidth and encoding to its lagging peers');
  assert.equal(group.requests.length,2);
  group.ctx.overviewPosition=null;group.tick();
  assert.equal(group.requests.length,3,'ordinary read-ahead resumes after the group is ready');
  console.log('PASS: request budgets, seek promotion and fair four-camera recovery');
})().catch(error=>{console.error(error);process.exitCode=1;});
