// 脚手架自检: 调 harness 里的 selfCheck, 确认 drain() 的 tick 预算够。
// 由 tests/test_gpu_monitor.py 的 PanelJsHarnessSelfCheck 调用。
// 失败信息保持 ASCII: CJK 混在 here-string 里会被 shell 截断(踩过)。
const H = require('./panel_js_harness.js');

async function main() {
  const js = H.loadPanelJs();
  const parts = H.readParts(js);
  const ok = await H.selfCheck(js, parts);
  if (!ok) {
    console.log('FAIL drain() did not wait long enough for one pull().');
    console.log('     None of the PARTS elements got written.');
    console.log('     Likely a new .then() stage in the panel while the');
    console.log('     drain budget was sized for the old stage count.');
    process.exit(1);
  }
  console.log('ok   drain() waits long enough for one pull()');
  console.log(String.fromCharCode(10) + 'harness self-check passed');
}

main();
