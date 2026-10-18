// Web 面板断连计数 connshow/connhide 的行为测试。
// 由 tests/test_gpu_monitor.py 的 PanelConnBehaviour 调用; 也可手动跑:
//   PANEL_JS="$(python -c 'import gpu_monitor as g; print(g._PANEL_SCRIPT)')" \
//     node tests/panel_conn_check.js
// 脚手架在 panel_js_harness.js。这个文件只写断连自己的检查。
//
// 主体包在 async main 里: 顶层 await 和 require 混在一起, node 会认为这是
// ESM 而拒绝加载(ERR_AMBIGUOUS_MODULE_SYNTAX)。
const H = require('./panel_js_harness.js');

const js = H.loadPanelJs();
const PARTS = H.readParts(js);

function makeEnv(fetchImpl) {
  const conn = { textContent: '', hidden: true };
  const q = { value: '', addEventListener() {} };
  const hint = { textContent: '' };
  return {
    conn,
    env: H.makeDom({
      document: {
        getElementById: (id) => (id === 'conn' ? conn
                                : id === 'hint' ? hint : null),
        querySelectorAll: () => [],
      },
      DOMParser: H.domParserFor(PARTS),
      fetch: fetchImpl,        // 必须在这里, 不能后补
      f() {},
    }),
  };
}

// 从 connhide() 的定义切到 setInterval(pull,1000): 这一段自带
// connhide / connshow / staleshow / pull 四个函数, 只差 conn/fails/lastok。
function panelBody() {
  return H.sliceBetween(js, 'function connhide()', 'setInterval(pull,1000)');
}

async function pullTimes(n, fetchImpl) {
  const { conn, env } = makeEnv(fetchImpl);
  const fn = H.compile(js, env, ['conn', 'fails', 'lastok'],
                       panelBody()
                       + '; for (let i = 0; i < ' + n + '; i++) pull();');
  fn(...Object.keys(env).map((k) => env[k]), conn, 0, '');
  await H.drain(n);
  return conn;
}

const dead = () => Promise.reject(new Error('ECONNREFUSED'));
const http502 = () => Promise.resolve({ ok: false, status: 502 });
const alive = () => Promise.resolve({
  ok: true,
  text: () => Promise.resolve('<html><body></body></html>'),
});

async function main() {
  const checks = [];
  const c = (name, got, want) => checks.push([name, got, want]);

  let r = await pullTimes(1, dead);
  c('单次失败不提示', r.hidden, true);

  r = await pullTimes(3, dead);
  c('连挂 3 次才提示', r.hidden, false);
  c('提示文案说连接已断开', r.textContent.includes('连接已断开'), true);

  r = await pullTimes(3, http502);
  c('502 也按断连算', r.hidden, false);

  let n = 0;
  r = await pullTimes(4, () => (n++ < 3 ? dead() : alive()));
  c('恢复后清掉提示', r.hidden, true);

  r = await pullTimes(3, dead);
  c('从没成功过时文案有兜底', r.textContent.includes('页面加载时'), true);

  n = 0;
  r = await pullTimes(2, () => (n++ < 1 ? dead() : alive()));
  c('抖一次再恢复不提示', r.hidden, true);

  // 反向: 两次失败还没到 3 次, 页面上什么都不能出现
  r = await pullTimes(2, dead);
  c('两次失败还不出提示', r.hidden, true);
  c('两次失败时文案是空的', r.textContent.length, 0);

  H.report(checks, 'all panel conn checks passed');
}

main();
