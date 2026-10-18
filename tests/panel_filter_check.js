// Web 面板筛选函数 f() 的行为测试。
// 由 tests/test_gpu_monitor.py 的 WebPanelFilterBehaviour 调用; 也可手动跑:
//   PANEL_JS="$(python -c 'import gpu_monitor as g; print(g._PANEL_SCRIPT)')" \
//     node tests/panel_filter_check.js
// 脚手架在 panel_js_harness.js。这个文件只写筛选自己的检查。
const H = require('./panel_js_harness.js');

const js = H.loadPanelJs();

// 假单元格: textContent 做成 getter/setter, 这样面板 JS 改写空态文案时
// (empty.cells[0].textContent='...')测试也能观察到
function cell(text, cls, colspan) {
  return {
    cls, colspan,
    get textContent() { return this._t; },
    set textContent(v) { this._t = v; },
    _t: text,
  };
}

function row(cells) {
  return {
    style: {},
    cells,
    querySelector(sel) {
      const want = sel.replace('td.', '');
      return cells.find((c) => c.cls === want) || null;
    },
  };
}

function build() {
  const rows = [
    row([cell('heavy.exe', 'n'), cell('100', 'pid'), cell('50.0%', 'gpu')]),
    row([cell('edge.exe', 'n'), cell('200', 'pid'), cell('12.3%', 'gpu')]),
    row([cell('Chrome.exe', 'n'), cell('1234', 'pid'), cell('3.0%', 'gpu')]),
    // 空态行: 一个 colspan 格子, 不带 class —— 服务端就这么渲染的
    row([cell('当前无进程占用 GPU', null, 8)]),
  ];
  const hint = { textContent: '' };
  const q = { value: '' };
  const env = H.makeDom({
    document: {
      getElementById: (id) => (id === 'hint' ? hint : null),
      querySelectorAll: (sel) => (sel === 'tbody tr' ? rows : []),
    },
    q,
    f() {},
  });
  return { rows, hint, q, env };
}

function runFilter(needle) {
  const { rows, hint, q, env } = build();
  q.value = needle;
  // f() 自己声明了 q/v/n/t/rows/empty/nc/id/hit, 只需要 document —— 而
  // document 已经在 env 里。q 由 env 提供, 这样面板里读到的就是我的值。
  const body = H.sliceBetween(js, 'function f(){', "q.addEventListener('input'");
  const fn = H.compile(js, env, [], body + '; f();');
  fn(...Object.keys(env).map((k) => env[k]));
  const visible = rows.filter((r) => r.style.display !== 'none');
  return {
    visible: visible.map((r) => r.cells[0].textContent),
    idleText: rows[3].cells[0].textContent,
    idleHidden: rows[3].style.display,
    hint: hint.textContent,
  };
}

const checks = [];
const c = (name, got, want) => checks.push([name, got, want]);

// 空词: 三个进程行全显, 分母只数进程行(空态行不算), 空态行藏起来
let r = runFilter('');
c('空词三行全显', r.visible.join(','), 'heavy.exe,edge.exe,Chrome.exe');
c('空词分母只数进程行', r.hint, '显示 3 / 3 行');
c('空词时空态行藏起来', r.idleHidden, 'none');

// 进程名子串, 大小写不敏感
r = runFilter('EDGE');
c('名字大小写不敏感', r.visible.join(','), 'edge.exe');
c('单行命中时提示 1 / 3', r.hint, '显示 1 / 3 行');

// PID 可搜, 而且是子串语义(和托盘 _matches_filter 一致)
r = runFilter('1234');
c('完整 PID 可搜', r.visible.join(','), 'Chrome.exe');
r = runFilter('123');
c('PID 是子串匹配', r.visible.join(','), 'Chrome.exe');

// 关键: 数字词不许把百分比列算进来。"50" 是 heavy 的 GPU% 50.0,
// 但它的名字和 PID 里都没有 50, 所以三个进程行都该被滤掉
r = runFilter('50');
c('数字词不匹配百分比列',
  r.visible.filter((t) => t !== '没有匹配的进程').join(','), '');
c('数字词只命中百分比时提示 0 / 3', r.hint, '显示 0 / 3 行');

// 空态行不能被筛掉: 有词但一个进程都不中时, 它要说"没有匹配"
r = runFilter('zzz');
c('无匹配时空态行仍在', r.visible.join(','), '没有匹配的进程');
c('无匹配时提示 0 / 3', r.hint, '显示 0 / 3 行');
c('无匹配时空态行不隐藏', r.idleHidden, '');

// 空态行的原文案只有在一个进程都不剩时才被改写; 有命中时保持原样
r = runFilter('EDGE');
c('有命中时不动空态文案', r.idleText, '当前无进程占用 GPU');

H.report(checks, 'all panel filter checks passed');
