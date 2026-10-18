// 面板 JS 行为测试的共享脚手架。
// 被 panel_stale_check.js / panel_filter_check.js / panel_conn_check.js 共用。
//
// 为什么值得单独一份: 这三个脚本刚写出来时各踩了一遍同样的坑, 而且坏掉的
// 脚手架会让一半检查空过(断言值恰好等于初始态时), 空过的检查和通过的检查
// 在输出里长得一模一样。把修法收在一处, 比让三个文件各写一遍注释可靠。
//
// 用法:
//   const H = require('./panel_js_harness.js');
//   const js = H.loadPanelJs();          // 从 PANEL_JS 取, 并剥掉 <script>
//   const parts = H.readParts(js);       // PARTS 清单(不写死)
//   const env = H.makeDom({ fetch });    // 假 DOM + 假浏览器全局
//   const fn = H.compile(js, env, ['conn', 'fails'], body);
//
// 七个坑(都修在下面, 别再各自发明一遍):
//   1. 必须从 PANEL_JS(纯 JS)取, 不能从 gpu_monitor.py 源码切 —— 后者切出来
//      是 Python 字符串字面量, 带引号和 # 注释;
//   2. body 要从它第一个函数的定义处开始切, 少切一段就 xxx is not defined;
//   3. fetch / DOMParser 这些必须在建 env 时就放进去 —— 后补的话
//      Object.keys 里没有, compile 的参数表就没有, 面板里的 fetch 会解析到
//      真全局去请求假 location.href, 每轮都抛;
//   4. DOMParser 要实现 parseFromString, 且返回对象给 PARTS 每个 id 一个可写
//      innerHTML 的元素 —— 少任何一样 then 分支都抛, 整轮落进 catch;
//   5. pull() 里是 fetch().then().catch(), 全是 Promise: 断言前必须 await 到
//      微任务队列排空;
//   6. 只断言 DOM 对象的终态, 不断言 compile 返回的快照 —— 那是同步旧值;
//   7. 每个检查的 want 都要和初始态不同, 否则脚手架坏掉时它会空过。

function loadPanelJs() {
  const js = (process.env.PANEL_JS || '')
    .replace('<script>', '')
    .replace('</script>', '');
  if (!js) {
    console.error('PANEL_JS 环境变量是空的: 由测试用例注入 _PANEL_SCRIPT 的真值');
    process.exit(2);
  }
  return js;
}

// PARTS 清单从面板 JS 里读, 不写死: 服务端加了新块这里自动跟上
function readParts(js) {
  const m = js.match(/PARTS=\[([^\]]*)\]/);
  const set = new Set(
    (m ? m[1] : '').split(',').map((s) => s.trim().replace(/'/g, ''))
      .filter(Boolean));
  if (!set.size) throw new Error('面板 JS 里没有 PARTS 清单');
  return set;
}

// 从 js 里切出 [from, to) 一段。找不到任一端就抛 —— 静默返回空串会让
// 后面的 new Function 拿到不完整的代码, 而报错信息指不到真正的原因。
function sliceBetween(js, from, to) {
  const a = js.indexOf(from);
  if (a < 0) throw new Error('面板 JS 里找不到 ' + from);
  const b = js.indexOf(to, a);
  if (b < 0) throw new Error('面板 JS 里找不到 ' + to + '(在 ' + from + ' 之后)');
  return js.slice(a, b);
}

// 假浏览器环境。extra 里的每一项都会成为 compile 出来的函数的参数,
// 所以 fetch / DOMParser 必须在这里给, 不能之后往对象上补。
function makeDom(extra) {
  const env = Object.assign({
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout,
    setInterval,
    location: { href: 'http://127.0.0.1:8787/', reload() {} },
    DOMParser: function () {
      return {
        parseFromString() {
          return { getElementById: () => null };
        },
      };
    },
  }, extra || {});
  return env;
}

// 给 DOMParser 装上能用的 parseFromString: PARTS 里每个 id 给一个可写
// innerHTML 的元素, 别的 id 给 null(和真页面一致)。
function domParserFor(parts) {
  return function DOMParser() {
    return {
      parseFromString() {
        return {
          getElementById: (id) => (parts.has(id) ? { innerHTML: '' } : null),
        };
      },
    };
  };
}

// 把一段面板 JS 编成一个函数。params 是面板代码里引用、但自己不声明的
// 那些名字(conn / fails / lastok / q ...), 由调用方按同样顺序传值。
function compile(js, env, params, body) {
  const keys = Object.keys(env);
  return new Function(...keys, ...params, body);
}

// 把这一轮里攒下的 Promise 全部排空。
//
// 一个 pull() 的成功路径有几级 Promise(实测, 见 .tmptest/drain_probe.js):
//   fetch() -> .then(r.text()) -> .then(parseFromString) -> .then(判定)
// 加上假 text() 自己也返回 Promise, 一共 4 个 tick 落地。轮数不累加 ——
// 每轮各自往微任务队列里排一次, 不是串起来的链。
//
// 所以 perRound 给的是"每轮 4 个 + 8 个余量", 与轮数成正比只是顺带。
// 余量是给将来的: 面板里再多一级 then 时, 这里不用跟着数。
async function drain(rounds, perRound) {
  const need = rounds * (perRound || 12) + 8;
  for (let i = 0; i < need; i++) await Promise.resolve();
}

// 汇总输出 + 退出码。bad 为 0 时打一行 all ... passed, 供 Python 侧断言。
//
// mode 决定怎么比:
//   'eq'(默认) 严格相等, 适合可见行列表、hidden 标志这类确定的输出;
//   'includes' 包含, 适合文案 —— 整句话该说的都在就行, 确切措辞交给
//               WebPanelLiveUpdate 那几条字符串断言去管, 两边别抢活。
// 每条检查的 want 都要和初始态不同: 脚手架坏掉时, 等于初始态的检查会
// 空过, 而空过的检查和通过的检查在输出里长得一模一样。
function report(checks, allPassedLine, mode) {
  const cmp = mode === 'includes'
    ? (got, want) => String(got).includes(want)
    : (got, want) => got === want;
  let bad = 0;
  for (const [name, got, want] of checks) {
    const ok = cmp(got, want);
    if (!ok) bad++;
    console.log((ok ? 'ok   ' : 'FAIL ') + name);
    if (!ok) {
      console.log('       want: ' + JSON.stringify(want)
                  + '  got: ' + JSON.stringify(got));
    }
  }
  console.log(bad ? `\n${bad} check(s) failed` : '\n' + allPassedLine);
  process.exit(bad ? 1 : 0);
}


// drain() 的自检: 拿一个只有 pull() 成功路径才会写的信号(PARTS 元素的
// innerHTML), 断言 drain(1) 之后它确实落地了。
//
// 为什么值得单独一个函数: drain 的 tick 数曾经是拍脑袋的 magic number,
// 而"不够"的后果是断言读到还没落地的状态 —— 那种失败看起来像产品有
// bug, 实际是脚手架没等够。页面里再多一级 then 时, 这里会先红。
async function selfCheck(js, parts) {
  const probe = {
    conn: { textContent: '', hidden: true },
    hint: { textContent: '' },
    q: { value: '', addEventListener() {} },
  };
  const marks = {};
  for (const id of parts) marks[id] = { innerHTML: 'INIT' };
  const env = makeDom({
    document: {
      getElementById: (id) => (id === 'conn' ? probe.conn
                               : id === 'hint' ? probe.hint
                               : marks[id] || null),
      querySelectorAll: () => [],
    },
    DOMParser: domParserFor(parts),
    fetch: () => Promise.resolve({
      ok: true, text: () => Promise.resolve('<html></html>'),
    }),
  });
  const body = sliceBetween(js, 'function f(){', 'setInterval(pull,1000)');
  const fn = compile(js, env, ['conn', 'fails', 'lastok', 'q'],
                     body + '; pull();');
  fn(...Object.keys(env).map((k) => env[k]),
     probe.conn, 0, '', probe.q);
  await drain(1);
  const wrote = Object.keys(marks)
    .filter((id) => marks[id].innerHTML !== 'INIT');
  return wrote.length > 0;
}

module.exports = {
  loadPanelJs, readParts, sliceBetween, makeDom, domParserFor,
  compile, drain, report, selfCheck,
};
