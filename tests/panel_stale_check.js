// 面板停更判定的行为测试。
// 由 tests/test_gpu_monitor.py 的 PanelScriptJsBehaviour 调用; 也可手动跑:
//   PANEL_JS="$(python -c 'import gpu_monitor as g; print(g._PANEL_SCRIPT)')" \
//     node tests/panel_stale_check.js
// 脚手架(取 JS / 假 DOM / 编译 / drain / 汇总)在 panel_js_harness.js,
// 那里记着七个坑的修法。这个文件只写停更判定自己的检查。
const H = require('./panel_js_harness.js');

const js = H.loadPanelJs();
const NOW = 1_700_000_000;      // 固定时钟, 让"数据几秒前"可算

function scenario(paused, snapAgeSec, stale) {
  const conn = { textContent: '', hidden: true };
  const store = {
    q: { value: '', addEventListener() {} },
    conn,
    paused: { textContent: paused ? '1' : '0' },
    snapts: { textContent: String((NOW - snapAgeSec) * 1000) },
    staleafter: { textContent: String(stale) },
    hint: { textContent: '' },
  };
  const fakeDate = class extends Date {
    constructor(...a) { return a.length ? super(...a) : super(NOW * 1000); }
    static now() { return NOW * 1000; }
  };
  const env = H.makeDom({
    document: {
      getElementById: (id) => store[id] || null,
      querySelectorAll: () => [],
    },
    Date: fakeDate,
    f() {}, connhide() {},
  });
  // staleshow 的定义 + pull() 里到停更判定结束为止。两段都要: staleshow
  // 是被判定调用的那个函数, 少切它一段就 "staleshow is not defined"。
  const def = H.sliceBetween(js, 'function staleshow(ts', 'conn.hidden=false;}')
    + 'conn.hidden=false;}';
  const logic = H.sliceBetween(js, 'var pz=document', 'staleshow(dts*1000)')
    + 'staleshow(dts*1000)';
  const fn = H.compile(js, env, ['conn', 'lastok'],
                       def + logic + '; return conn.textContent;');
  return fn(...Object.keys(env).map((k) => env[k]), conn, '10:00:00');
}

// 两类断言分开写, 因为 report 的 includes 模式表达不了"不含":
//   contains  [名称, 文案, 必须出现的子串]
//   lacks     [名称, 文案, 不许出现的子串]
// 空串那一档的语义是"整句为空": includes('') 恒真会空过, 所以比长度。
const contains = [
  ['暂停且数据新: 只报暂停, 不误报停更',
   scenario(true, 5, 25), '监控已暂停'],
  ['暂停且数据旧: 必须报出数据停了多久',
   scenario(true, 300, 25), '数据已停更'],
  ['暂停且数据旧: 原因写已暂停',
   scenario(true, 300, 25), '(已暂停)'],
  ['未暂停且数据新: 什么都不说',
   scenario(false, 5, 25), ''],
  ['未暂停且数据旧: 报可能已停止',
   scenario(false, 300, 25), '监控进程可能已停止'],
  ['周期 60s 时 100s 算新: 不被 25s 写死坑到',
   scenario(true, 100, 125), '监控已暂停'],
  ['周期 60s 时 200s 算旧',
   scenario(true, 200, 125), '数据已停更'],
];

const lacks = [
  ['暂停且数据新: 不提监控已停止',
   scenario(true, 5, 25), '可能已停止'],
  ['暂停且数据旧: 原因不是可能已停止',
   scenario(true, 300, 25), '可能已停止'],
  ['未暂停且数据旧: 不说成已暂停',
   scenario(false, 300, 25), '已暂停'],
];

const checks = [];
for (const [name, got, want] of contains) {
  checks.push([name,
               want === '' ? got.length : got,
               want === '' ? 0 : want]);
}
for (const [name, got, bad] of lacks) {
  checks.push([name, got.includes(bad) ? '含有 ' + bad : '不含', '不含']);
}

H.report(checks, 'all panel stale checks passed', 'includes');
