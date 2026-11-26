// 四路画面必须按片源比例铺满格子, 不能被竖屏视口高度压扁留黑边。
// 回归: 旧逻辑 rows===2 时用 min(width/cols*ratio*rows, limit), 手机竖屏塞不下 ->
// 格子被压到 ~16:9, video 又 object-fit:contain -> 四周大黑边 "占不满"。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '..', 'sentry-viewer.py'), 'utf8');

function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  assert(start >= 0, `${name} not found`);
  const end = source.indexOf('\n}', start) + 2;
  return source.slice(start, end);
}
const ctx = vm.createContext({Math, console});
vm.runInContext(extract('gridRowHeight'), ctx);
const rowHeight = (w, c, r, ratio, limit, immersive) =>
  vm.runInContext(`gridRowHeight(${w},${c},${r},${ratio},${limit},${immersive})`, ctx);

// 竖屏手机: 4 路 (2x2), 整网格宽 944 (每格 472), 片源 4:3 (ratio = h/w = .75)。
// 旧逻辑 limit=306 会把每格行高压到约 150-153 (变扁 -> contain 留黑边);
// 新逻辑保持 472*.75 = 354 (铺满, 无黑边)。
const phone = rowHeight(944, 2, 2, 0.75, 306, false);
assert.ok(Math.abs(phone - 354) < 1, `竖屏四路行高应=片源比例高度 354, 实际 ${phone}`);

// 大屏: 片源比例同样铺满, 与 limit 无关
const big = rowHeight(1200, 2, 2, 0.75, 9999, false);
assert.ok(Math.abs(big - 450) < 1, `大屏四路行高应=600*.75=450, 实际 ${big}`);

// 全屏/沉浸模式仍用 limit 兜底, 防撑爆
const immersive = rowHeight(944, 2, 2, 0.75, 306, true);
assert.ok(immersive <= 151.5, `沉浸模式行高应受 limit/rows 约束, 实际 ${immersive}`);

// 单路/两路不受影响: 非沉浸也按片源比例 (单路本来就该按画面高度)
const one = rowHeight(944, 1, 1, 0.75, 306, false);
assert.ok(Math.abs(one - 708) < 1, `单路行高应=944*.75=708, 实际 ${one}`);

// CSS: 播放器 video 必须 cover (任意格子比例都铺满不留黑边)
assert.ok(/\.player \.vcell video \{ object-fit:cover; \}/.test(source),
  '播放器视频必须 object-fit:cover 才能不留黑边');
assert.ok(!/\.player \.vcell video \{ object-fit:contain; \}/.test(source),
  '播放器视频不能是 contain (会留黑边)');
assert.ok(/\.player \.vcell canvas\.hold \{[^}]*object-fit:cover/.test(source),
  '暂停静帧也须 cover');

console.log('player grid fill: ok');
