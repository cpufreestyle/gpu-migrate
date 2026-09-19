"""gpu_monitor 的回归测试: 纯标准库, 不碰注册表/真实 PDH/真实进程。

跑法: python -X utf8 -m unittest discover -s tests
(-X utf8 只为让失败信息里的中文在 GBK 控制台上不糊, 与测试结果无关)
锁定的是"重构不能改变的行为": 实例路径解析、显卡分类、通配展开的三段式
协议、迁移判定门槛与排除优先级、省电与游戏模式、日志节奏、面板数据契约。
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gpu_monitor as gm  # noqa: E402

IGPU_LUID = "00000000_000149DA"
DGPU_LUID = "00000000_00013897"
IGPU_NAME = "Intel(R) UHD Graphics 630"
DGPU_NAME = "NVIDIA GeForce RTX 4060 Ti"

# pid 100 核显高占用(该迁移) / 101 已在独显上 / 1 dwm(内置排除名单) / 102 游戏
PID_NAMES = {
    100: ("heavy.exe", r"D:\app\heavy.exe"),
    101: ("dgpuapp.exe", r"D:\app\dgpuapp.exe"),
    102: ("game.exe", r"D:\app\game.exe"),
    1: ("dwm.exe", r"C:\Windows\System32\dwm.exe"),
}
HEAVY = r"D:\app\heavy.exe"
DGPUAPP = r"D:\app\dgpuapp.exe"
GAME = r"D:\app\game.exe"

# 热重载测试用的 config.json: 关掉一切会起线程/落盘的开关, 只留判定逻辑
TEST_CFG_BASE = {
    "threshold_percent": 50.0, "sustain_samples": 1,
    "power_aware": False, "vram_threshold_mb": 0, "history": False,
    "log_to_file": False, "web_port": 0, "notify": False,
    "nvml_temp": False, "hotkeys": {}, "exclude_defaults": False,
    "game_processes": [], "power_saver_notify": False}


def write_test_config(path, **over):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({**TEST_CFG_BASE, **over}, f)


def engine_path(pid, luid, phys, eng, engtype, machine=None):
    hi, lo = luid.split("_")
    head = f"\\{machine}\\" if machine else "\\"
    return (f"{head}GPU Engine(pid_{pid}_luid_0x{hi}_0x{lo}_phys_{phys}"
            f"_eng_{eng}_engtype_{engtype})\\Utilization Percentage")


def _deref(ref):
    """取 ctypes.byref() 背后的对象: 3.14 起叫 _obj, 旧版是 _b_base_。

    不能用 `or` 串联: DWORD(0) 是假值, 而出参初值恰恰是 0。
    """
    for attr in ("_obj", "_b_base_"):
        target = getattr(ref, attr, None)
        if target is not None:
            return target
    return ref


def _set_out(ref, value):
    """写穿 ctypes.byref() 传出的 DWORD。"""
    _deref(ref).value = value


def _get_out(ref):
    return _deref(ref).value


class AggregateRules(unittest.TestCase):
    """aggregate_usage 的任务管理器口径。循环夹具每 pid 只有一个引擎,
    多引擎/跨适配器这些最容易"改回去就虚高"的规则只能直接测。"""

    @staticmethod
    def agg(usage, shared=None, dedicated=None):
        return gm.aggregate_usage(usage, shared or {}, dedicated or {},
                                  {IGPU_LUID}, {DGPU_LUID})

    def unpack(self, usage, shared=None, dedicated=None):
        util, gpu_all, dgpu_util, shr, ded, igpu_mem = self.agg(
            usage, shared, dedicated)
        return dict(util), dict(gpu_all), dict(dgpu_util), dict(shr), \
            dict(ded), dict(igpu_mem)

    def test_igpu_utilization_is_strongest_engine_not_sum(self):
        # 同一进程在核显的两个引擎上分别 50% / 30%
        usage = {(7, IGPU_LUID, 1): 50.0, (7, IGPU_LUID, 2): 30.0}
        util, gpu_all, _d, _s, _e, _m = self.unpack(usage)
        self.assertEqual(util, {7: 50.0}, "求和会虚高到 80%, 任务管理器显示 50%")
        self.assertEqual(gpu_all, {7: 50.0})

    def test_gpu_all_column_spans_adapters(self):
        usage = {(7, IGPU_LUID, 0): 20.0, (7, DGPU_LUID, 0): 75.0}
        util, gpu_all, dgpu_util, _s, _e, _m = self.unpack(usage)
        self.assertEqual((util[7], dgpu_util[7], gpu_all[7]), (20.0, 75.0, 75.0))

    def test_memory_sums_across_adapters_but_igpu_gate_only(self):
        mb = 1024 * 1024
        shared = {(7, IGPU_LUID, 0): 5 * mb, (7, DGPU_LUID, 0): 7 * mb}
        dedicated = {(7, IGPU_LUID, 0): 3 * mb, (7, DGPU_LUID, 0): 11 * mb}
        _u, _g, _d, shr, ded, igpu_mem = self.unpack(
            {}, shared, dedicated)
        self.assertEqual(shr, {7: 12 * mb})
        self.assertEqual(ded, {7: 14 * mb})
        self.assertEqual(igpu_mem, {7: 8 * mb},
                         "vram_threshold 只看核显上的 Shared+Dedicated")

    def test_unclassified_adapter_counts_only_in_gpu_all(self):
        usage = {(7, "00000000_00000001", 0): 90.0}
        util, gpu_all, dgpu_util, _s, _e, igpu_mem = self.unpack(usage)
        self.assertEqual((dict(util), dict(dgpu_util), dict(igpu_mem)),
                         ({}, {}, {}), "虚拟/未知显卡不该参与迁移判定")
        self.assertEqual(gpu_all, {7: 90.0})

    def test_missing_pids_default_to_zero(self):
        util, _g, _d, _s, _e, igpu_mem = self.agg({})
        self.assertEqual(util.get(999, 0.0), 0.0)
        self.assertEqual(igpu_mem[999], 0.0, "defaultdict: 缺键不报错")


class InstancePathParsing(unittest.TestCase):
    """INSTANCE_RE 必须吃下 PDH 实际返回的各种实例名形状。"""

    def test_plain_and_machine_prefixed(self):
        for pfx in (None, r"\\MY-PC"):
            with self.subTest(machine=pfx):
                m = gm.INSTANCE_RE.search(
                    engine_path(1234, IGPU_LUID, 0, 3, "3D", pfx))
                self.assertIsNotNone(m)
                self.assertEqual(int(m.group(1)), 1234)
                self.assertEqual((m.group(2).upper(), m.group(3).upper()),
                                 ("00000000", "000149DA"))
                self.assertEqual(int(m.group(4)), 0)

    def test_engtype_variants_and_bare_phys(self):
        for engtype in ("3D", "Copy", "VideoDecode", "Compute_0"):
            with self.subTest(engtype=engtype):
                self.assertIsNotNone(gm.INSTANCE_RE.search(
                    engine_path(9, DGPU_LUID, 1, 0, engtype)))
        self.assertIsNotNone(gm.INSTANCE_RE.search(
            r"\GPU Engine(pid_7_luid_0x00000000_0x00013502_phys_0)\x"))

    def test_non_gpu_instance_rejected(self):
        self.assertIsNone(gm.INSTANCE_RE.search(
            r"\GPU Process Memory(pid_7_luid_bogus)\Dedicated Usage"))


class GpuClassification(unittest.TestCase):
    """classify_gpu 判定表。AdapterRAM 只在名称启发式打架时作 tiebreak。"""

    TABLE = [
        (IGPU_NAME, None, "igpu"),
        (DGPU_NAME, None, "dgpu"),
        ("Intel(R) Arc A770 Graphics", None, "unknown"),   # intel 与 arc 都中
        ("Intel(R) Arc A770 Graphics", 16384, "dgpu"),     # 靠显存判独显
        ("Intel(R) Iris(R) Xe Graphics", None, "igpu"),
        ("AMD Radeon RX 7600", None, "dgpu"),
        ("Microsoft Basic Render Driver", None, "virtual"),
        ("NVIDIA Virtual GPU", None, "virtual"),
        ("", None, "virtual"),
        ("Some Mystery GPU", 8192, "dgpu"),
        ("Some Mystery GPU", 512, "igpu"),
        ("Some Mystery GPU", None, "unknown"),
    ]

    def test_table(self):
        for name, mb, want in self.TABLE:
            with self.subTest(name=name, mb=mb):
                self.assertEqual(gm.classify_gpu(name, mb), want)

    def test_force_igpu_names_overrides(self):
        orig = gm.luid_to_name
        gm.luid_to_name = lambda _l: "Some Mystery GPU"
        try:
            got = gm.kind_of_luid("x", {}, {"some mystery gpu"})[0]
        finally:
            gm.luid_to_name = orig
        self.assertEqual(got, "igpu")

    def test_cache_prevents_repeated_lookup(self):
        calls = []

        def fake(luid):
            calls.append(luid)
            return DGPU_NAME
        orig = gm.luid_to_name
        gm.luid_to_name = fake
        try:
            cache = {}
            gm.kind_of_luid("L1", cache, set())
            gm.kind_of_luid("L1", cache, set())
        finally:
            gm.luid_to_name = orig
        self.assertEqual(len(calls), 1)


class DedicatedMemoryLookup(unittest.TestCase):
    """WMI 查询: 进程内只起一次 powershell, 结果按名称缓存。"""

    PS_OUT = (f"{IGPU_NAME}|134217728\n"
              f"{DGPU_NAME}|8589934592\n"
              "Some Mystery GPU|8589934592\n"
              "Malformed Line Without Delimiter\n"
              f"{DGPU_NAME}|not-a-number\n")

    def setUp(self):
        gm._DEDICATED_MB = None
        self.calls = []
        self._orig = gm.subprocess.run

        def fake_run(cmd, *a, **k):
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=self.PS_OUT,
                                               stderr="")
        gm.subprocess.run = fake_run

    def tearDown(self):
        gm.subprocess.run = self._orig
        gm._DEDICATED_MB = None

    def test_parses_and_caches_single_launch(self):
        self.assertEqual(gm.query_dedicated_mb(IGPU_NAME), 128)
        self.assertEqual(gm.query_dedicated_mb(DGPU_NAME), 8192)
        self.assertEqual(len(self.calls), 1, "只应启动一次 powershell")

    def test_case_and_whitespace_insensitive_match(self):
        self.assertEqual(gm.query_dedicated_mb(IGPU_NAME.lower()), 128)
        self.assertEqual(gm.query_dedicated_mb(f"  {DGPU_NAME}  "), 8192)

    def test_missing_name_and_unparsable_lines(self):
        self.assertIsNone(gm.query_dedicated_mb("Nonexistent GPU"))
        self.assertIsNone(gm.query_dedicated_mb(None))
        self.assertIsNone(gm.query_dedicated_mb(""))
        self.assertEqual(gm.query_dedicated_mb(DGPU_NAME), 8192,
                         "无法解析的 AdapterRAM 不该覆盖已有的有效值")

    def test_failure_is_cached_and_not_retried(self):
        def boom(cmd, *a, **k):
            self.calls.append(cmd)
            raise OSError("powershell 不存在")
        gm.subprocess.run = boom
        self.assertIsNone(gm.query_dedicated_mb(IGPU_NAME))
        self.assertIsNone(gm.query_dedicated_mb(DGPU_NAME))
        self.assertEqual(len(self.calls), 1,
                         "失败后不该反复重试拖住监控线程")

    def test_wmi_not_consulted_when_name_decides(self):
        orig = gm.luid_to_name
        gm.luid_to_name = lambda _l: DGPU_NAME
        try:
            self.assertEqual(gm.kind_of_luid("a", {}, set())[0], "dgpu")
        finally:
            gm.luid_to_name = orig
        self.assertEqual(self.calls, [], "名称能判时不该起 powershell")

    def test_wmi_consulted_only_for_ambiguous_name(self):
        orig = gm.luid_to_name
        gm.luid_to_name = lambda _l: "Some Mystery GPU"
        try:
            self.assertEqual(gm.kind_of_luid("a", {}, set())[0], "dgpu")
        finally:
            gm.luid_to_name = orig
        self.assertEqual(len(self.calls), 1)


class WildcardExpansion(unittest.TestCase):
    """expand_counter_paths 的三段式: 探长度 -> PDH_MORE_DATA -> 取数据。"""

    class FakePDH:
        def __init__(self, ret_first, size, final_paths=None):
            self.ret_first, self.size = ret_first, size
            self.final_paths = final_paths or []
            self.calls = 0
            self.paths_seen = []

        def PdhExpandWildCardPathW(self, _q, path, buf, ref, _flags):
            self.calls += 1
            self.paths_seen.append(path)
            if buf is None:
                _set_out(ref, self.size)
                return self.ret_first
            payload = "\0".join(self.final_paths) + "\0\0"
            if len(payload) > _get_out(ref):
                # 缓冲区不够时装不下, 真实 PDH 也是再次报 MORE_DATA
                return gm.PDH_MORE_DATA
            for i, ch in enumerate(payload):
                buf[i] = ch
            _set_out(ref, len(payload))
            return 0

    def setUp(self):
        self._pdh, self._sleep = gm.pdh, gm.time.sleep
        gm.time.sleep = lambda _s: None

    def tearDown(self):
        gm.pdh = self._pdh
        gm.time.sleep = self._sleep

    def test_no_instances_returns_empty(self):
        gm.pdh = self.FakePDH(0, 0)
        self.assertEqual(gm.expand_counter_paths(r"GPU Engine(*)\X"), [])

    def test_more_data_then_paths(self):
        paths = [engine_path(1, IGPU_LUID, 0, 0, "3D"),
                 engine_path(2, IGPU_LUID, 0, 0, "3D")]
        fake = self.FakePDH(gm.PDH_MORE_DATA, 64, paths)
        gm.pdh = fake
        self.assertEqual(gm.expand_counter_paths(r"GPU Engine(*)\X"), paths)
        self.assertEqual(fake.calls, 2)

    def test_buffer_grown_for_instances_appearing_between_calls(self):
        # 探长度只报 64 字符, 实际回传 ~280 字符: 全靠代码里的 +1024 余量。
        # 去掉余量后 FakePDH 会因装不下而报 MORE_DATA, 最终抛 OSError。
        paths = [engine_path(i, IGPU_LUID, 0, 0, "3D") for i in range(3)]
        gm.pdh = self.FakePDH(gm.PDH_MORE_DATA, 64, paths)
        self.assertEqual(gm.expand_counter_paths(r"GPU Engine(*)\X"), paths)

    def test_retries_five_times_then_raises(self):
        fake = self.FakePDH(0xC0000BB8, 0)   # 既不是 0 也不是 MORE_DATA
        gm.pdh = fake
        with self.assertRaises(OSError):
            gm.expand_counter_paths(r"GPU Engine(*)\X")
        self.assertEqual(fake.calls, 5)

    def test_leading_backslash_normalized(self):
        fake = self.FakePDH(0, 0)
        gm.pdh = fake
        gm.expand_counter_paths("GPU Engine(*)\\X")
        gm.expand_counter_paths(r"\GPU Engine(*)\X")
        self.assertEqual(set(fake.paths_seen), {r"\GPU Engine(*)\X"})


class ConfigLoading(unittest.TestCase):
    def test_defaults_merged_and_overridable(self):
        cfg = gm.load_config(os.path.join(gm._DIR, "config.json"))
        for key in gm.DEFAULT_CONFIG:
            self.assertIn(key, cfg)
        self.assertGreater(cfg["interval_seconds"], 0)
        self.assertGreater(cfg["sustain_samples"], 0)

    def test_missing_file_gives_pure_defaults(self):
        self.assertEqual(
            gm.load_config(os.path.join(gm._DIR, "__no_such__.json")),
            gm.DEFAULT_CONFIG)


class HotkeySpec(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(gm._parse_hotkey("ctrl+alt+g"), (0x2 | 0x1, ord("G")))
        self.assertEqual(gm._parse_hotkey("  SHIFT +F"), (0x4, ord("F")))
        for bad in ("g", "ctrl+", "ctrl+delete", ""):
            with self.subTest(bad=bad):
                self.assertIsNone(gm._parse_hotkey(bad))


# ==================================================== 监控主循环 (假采样驱动)

STUBS = ("collect_gpu_sample", "pid_to_name", "set_gpu_preference",
         "get_gpu_preference", "notify_cfg", "notify",
         "is_foreground_fullscreen", "restart_process", "luid_to_name",
         "on_battery", "nvml_gpu_temp", "save_ignore_process", "ask_migrate",
         "log")


class LoopHarness(unittest.TestCase):
    """用假采样 + 假注册表驱动 cmd_monitor 的公共夹具; 本类不含用例。"""

    def setUp(self):
        self.prefs = {}
        self.notes = []
        self.log_lines = []
        # 周期对齐的 time.sleep 不是被测行为, 50ms/轮会把测试拖到十几秒
        self._sleep = gm.time.sleep
        gm.time.sleep = lambda _s: None
        # 把 _DIR 换到临时目录: 循环里会往 _DIR 写 history.jsonl / report.csv,
        # 不能让测试污染仓库里真实的运行数据
        self._tmp = tempfile.mkdtemp(prefix="gpumig-test-")
        self._orig_dir = gm._DIR
        gm._DIR = self._tmp
        self.saved = {name: getattr(gm, name) for name in STUBS}
        gm.pid_to_name = PID_NAMES.get
        gm.luid_to_name = {IGPU_LUID: IGPU_NAME, DGPU_LUID: DGPU_NAME}.get
        gm.get_gpu_preference = self.prefs.get
        gm.set_gpu_preference = self._set_pref
        gm.notify_cfg = lambda cfg, t, m: self.notes.append((t, m))
        gm.notify = lambda t, m: self.notes.append((t, m))
        gm.is_foreground_fullscreen = lambda: False
        gm.restart_process = lambda pid, full: None
        gm.on_battery = lambda: False
        gm.nvml_gpu_temp = lambda index=0: None
        gm.save_ignore_process = lambda path, pname: None
        gm.ask_migrate = lambda pname, reason: True
        gm.log = lambda msg, logfile=None: self.log_lines.append(msg)
        gm._GAME_RUNNING = False

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(gm, name, value)
        gm.time.sleep = self._sleep
        gm._DIR = self._orig_dir
        gm._GAME_RUNNING = False
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _set_pref(self, exe_path, value="GpuPreference=2;"):
        self.prefs[exe_path] = value

    @staticmethod
    def write_and_bump(path, **over):
        """改写 config.json 并把 mtime 往后推 100ms。

        NTFS 的时间戳按系统 tick (~15.6ms) 更新, 实测紧邻两次写入有约 22%
        落在同一个 tick 里 -> mtime 一模一样, 循环里的 `mtime != last_mtime`
        发现不了改动, 热重载用例就会偶发失败。真人用编辑器保存不会撞上,
        所以修在测试侧而不是改产品代码。
        """
        later = int((os.path.getmtime(path) + 0.1) * 1e9)
        write_test_config(path, **over)
        os.utime(path, ns=(later, later))

    @staticmethod
    def rewrite_once(path, **over):
        """on_sample 钩子: 第一轮采样后改写 config.json, 触发热重载。"""
        done = []

        def hook(_snapshot):
            if not done:
                done.append(1)
                LoopHarness.write_and_bump(path, **over)
        return hook

    @staticmethod
    def collector(samples, vram=None):
        """samples: [{pid: 核显%}] 或 ({pid: 核显%}, {pid: 独显%}) 序列。

        序列耗尽后重复最后一轮。vram: {pid: 核显专用显存 MB}。
        """
        vram = vram or {}
        state = {"i": 0}

        def fake_collect(allowed_luids=None, normalize=False):
            item = samples[min(state["i"], len(samples) - 1)]
            state["i"] += 1
            igpu, dgpu = item if isinstance(item, tuple) else (item, {})
            usage = {(pid, IGPU_LUID, 0): v for pid, v in igpu.items()}
            usage.update({(pid, DGPU_LUID, 0): v for pid, v in dgpu.items()})
            dedicated = {(pid, IGPU_LUID, 0): mb * 1024 * 1024
                         for pid, mb in vram.items()}
            return usage, {}, dedicated, {(IGPU_LUID, 0): None,
                                          (DGPU_LUID, 0): None}
        return fake_collect

    def run_monitor(self, samples, cycles=None, vram=None, cfg_overrides=None,
                    exclude=(), config_path=None, hooks=None):
        gm.collect_gpu_sample = self.collector(samples, vram)
        cfg = dict(gm.DEFAULT_CONFIG)
        cfg.update({"threshold_percent": 50.0, "sustain_samples": 3,
                    "interval_seconds": 0.001, "vram_threshold_mb": 0,
                    "history": False, "log_to_file": False, "web_port": 0,
                    "notify": False, "nvml_temp": False, "power_aware": False,
                    "hotkeys": {}, "power_saver_notify": False,
                    "exclude_defaults": False,
                    "exclude_processes": list(exclude)})
        cfg.update(cfg_overrides or {})
        limit = cycles if cycles is not None else len(samples)
        done = [0]

        def before_cycle():
            done[0] += 1
            return done[0] <= limit
        all_hooks = dict(hooks or {})
        all_hooks["before_cycle"] = before_cycle
        gm.cmd_monitor(cfg, config_path, hooks=all_hooks)
        return done[0] - 1        # 返回 False 的那一轮没有执行采样


class MigrationRules(LoopHarness):
    """迁移判定的对外可见行为。拆分那个 397 行函数之后必须仍然全绿。"""

    def test_migrates_after_sustained_over_threshold(self):
        self.run_monitor([{100: 80.0}] * 3)
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;")

    def test_below_sustain_count_never_migrates(self):
        self.run_monitor([{100: 80.0}] * 2)
        self.assertEqual(self.prefs, {})

    def test_streak_resets_when_usage_drops(self):
        self.run_monitor([{100: 80.0}, {100: 80.0}, {100: 1.0},
                          {100: 80.0}, {100: 80.0}])
        self.assertEqual(self.prefs, {}, "中断的连续超标不该累计触发")

    def test_under_threshold_never_migrates(self):
        self.run_monitor([{100: 49.9}] * 10)
        self.assertEqual(self.prefs, {})

    def test_excluded_process_is_not_migrated(self):
        self.run_monitor([{100: 80.0}] * 6, exclude=["heavy.exe"])
        self.assertEqual(self.prefs, {})

    def test_default_exclusions_cover_dwm(self):
        self.run_monitor([{1: 99.0}] * 6,
                         cfg_overrides={"exclude_defaults": True})
        self.assertEqual(self.prefs, {}, "dwm.exe 在内置排除名单里")

    def test_exclude_full_paths_prefix(self):
        self.run_monitor([{100: 80.0}] * 6,
                         cfg_overrides={"exclude_full_paths": [r"D:\app"]})
        self.assertEqual(self.prefs, {})

    def test_ignored_process_is_not_migrated(self):
        self.run_monitor([{100: 80.0}] * 6,
                         cfg_overrides={"ignore_processes": ["heavy.exe"]})
        self.assertEqual(self.prefs, {})

    def test_already_migrated_process_stays_quiet(self):
        self.prefs[HEAVY] = "GpuPreference=2;"
        self.run_monitor([{100: 80.0}] * 8)
        self.assertEqual(self.prefs, {HEAVY: "GpuPreference=2;"})
        self.assertEqual(self.notes, [], "已迁移的进程不该反复通知")

    def test_bare_value_without_semicolon_counts_as_migrated(self):
        """`GpuPreference=2` (缺尾分号) 也是已迁移。

        本机注册表实测有 3 条这种写法(其他工具写的), 整串相等会判成未迁移,
        于是本该安静的进程被重写一遍并弹"迁移成功"。
        """
        self.prefs[HEAVY] = "GpuPreference=2"
        self.run_monitor([{100: 80.0}] * 8)
        self.assertEqual(self.prefs, {HEAVY: "GpuPreference=2"})
        self.assertEqual(self.notes, [])

    def test_dgpu_only_load_is_not_migrated(self):
        """已经跑在独显上的负载不该再触发迁移。"""
        self.run_monitor([({}, {101: 80.0})] * 8)
        self.assertEqual(self.prefs, {})

    def test_migration_notifies_once(self):
        self.run_monitor([{100: 80.0}] * 3)
        self.assertEqual([t for t, _m in self.notes], ["GPU 迁移成功"])

    def test_vram_alone_triggers_migration(self):
        """利用率始终为 0, 仅核显专用显存超阈也要迁移。"""
        self.run_monitor([{}] * 3, vram={100: 2048},
                         cfg_overrides={"vram_threshold_mb": 1024})
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;")

    def test_vram_threshold_zero_disables_the_rule(self):
        self.run_monitor([{}] * 3, vram={100: 2048},
                         cfg_overrides={"vram_threshold_mb": 0})
        self.assertEqual(self.prefs, {})

    def test_confirm_mode_reject_persists_ignore(self):
        """确认模式下拒绝 -> 不写注册表, 且把忽略记进 config.json。

        必须给 config_path: 循环里 `if _CONFIG_PATH` 才决定要不要持久化。
        文件内容要与 cfg_overrides 一致, 否则写入触发热重载会改掉行为。
        """
        path = os.path.join(gm._DIR, "__confirm__.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"threshold_percent": 50.0, "sustain_samples": 3,
                       "confirm_mode": True}, f)
        gm.ask_migrate = lambda pname, reason: False
        gm.save_ignore_process = self.saved["save_ignore_process"]  # 用真实现
        try:
            self.run_monitor([{100: 80.0}] * 6, config_path=path,
                             cfg_overrides={"confirm_mode": True})
            self.assertEqual(self.prefs, {}, "拒绝后不该写入独显设置")
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f).get("ignore_processes"),
                                 ["heavy.exe"])
        finally:
            os.remove(path)

    def test_confirm_mode_accept_migrates(self):
        self.run_monitor([{100: 80.0}] * 3,
                         cfg_overrides={"confirm_mode": True})
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;")

    def test_auto_restart_logs_new_pid(self):
        gm.restart_process = lambda pid, full: 4321
        self.run_monitor([{100: 80.0}] * 3,
                         cfg_overrides={"auto_restart": True})
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;")
        self.assertTrue(any("已自动重启" in m for m in self.log_lines))

    def test_auto_restart_skips_processes_not_in_the_list(self):
        gm.restart_process = lambda pid, full: 4321
        self.run_monitor([{100: 80.0}] * 3,
                         cfg_overrides={"auto_restart": True,
                                        "auto_restart_processes": ["other.exe"]})
        self.assertFalse(any("已自动重启" in m for m in self.log_lines),
                         "名单外的进程不该被自动重启")

    def test_config_file_path_does_not_break_the_loop(self):
        path = os.path.join(gm._DIR, "__test_hotreload__.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"threshold_percent": 50.0, "sustain_samples": 3}')
        try:
            self.run_monitor([{100: 80.0}] * 4, config_path=path,
                             cfg_overrides={"sustain_samples": 4})
            self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;")
        finally:
            os.remove(path)


class LogChatter(LoopHarness):
    """日志节奏: 不每轮刷屏, 但仍要能看到计数在推进。"""

    def chatter(self):
        return [l for l in self.log_lines if l.startswith("检测到 ")]

    def test_only_boundary_lines_within_a_long_streak(self):
        cycles = self.run_monitor([{100: 80.0}] * 20,
                                  cfg_overrides={"sustain_samples": 20})
        self.assertEqual(cycles, 20)
        got = self.chatter()
        self.assertEqual(len(got), 2, f"应只有起头与临近触发两行, 实际 {got}")
        self.assertIn("(1/20)", got[0])
        self.assertIn("(19/20)", got[1])

    def test_far_below_one_line_per_cycle(self):
        self.run_monitor([{100: 80.0}] * 18,
                         cfg_overrides={"sustain_samples": 20})
        self.assertLessEqual(
            len(self.chatter()), 2,
            "18 轮若每轮一行就是 18 行, 那正是控制台模式 CPU 的大头")

    def test_final_trigger_line_still_logged(self):
        self.run_monitor([{100: 80.0}] * 3)
        self.assertTrue(any("连续 3 次超标" in l for l in self.log_lines))


class PowerSaver(LoopHarness):
    """独显长期低负载 -> 提醒或自动切回核显。"""

    IDLE = [({100: 0.0}, {101: 2.0})]

    def _cfg(self, **over):
        cfg = {"power_saver_notify": True, "power_saver_samples": 2,
               "power_saver_idle_percent": 10.0}
        cfg.update(over)
        return cfg

    def test_idle_dgpu_process_reverted_when_auto(self):
        self.prefs[DGPUAPP] = "GpuPreference=2;"
        self.run_monitor(self.IDLE * 6,
                         cfg_overrides=self._cfg(power_saver_auto=True))
        self.assertEqual(self.prefs.get(DGPUAPP), "GpuPreference=1;")

    def test_idle_dgpu_process_only_notified_when_not_auto(self):
        self.prefs[DGPUAPP] = "GpuPreference=2;"
        self.run_monitor(self.IDLE * 6,
                         cfg_overrides=self._cfg(power_saver_auto=False))
        self.assertEqual(self.prefs.get(DGPUAPP), "GpuPreference=2;")
        self.assertIn("省电提醒", [t for t, _m in self.notes])

    def test_active_dgpu_process_not_reverted(self):
        self.prefs[DGPUAPP] = "GpuPreference=2;"
        self.run_monitor([({100: 0.0}, {101: 80.0})] * 6,
                         cfg_overrides=self._cfg(power_saver_auto=True))
        self.assertEqual(self.prefs.get(DGPUAPP), "GpuPreference=2;")

    def test_unmigrated_process_left_alone(self):
        self.run_monitor(self.IDLE * 6,
                         cfg_overrides=self._cfg(power_saver_auto=True))
        self.assertEqual(self.prefs, {}, "没迁移到独显的进程不该被写入设置")

    def test_bare_value_process_is_still_reverted(self):
        """缺尾分号的已迁移标记, 省电模式也该认。"""
        self.prefs[DGPUAPP] = "GpuPreference=2"
        self.run_monitor(self.IDLE * 6,
                         cfg_overrides=self._cfg(power_saver_auto=True))
        self.assertEqual(self.prefs.get(DGPUAPP), "GpuPreference=1;")


class GameMode(LoopHarness):
    """游戏名单进程: 自动确保独显 + 通知静音。"""

    def _cfg(self, **over):
        cfg = {"game_processes": ["game.exe"]}
        cfg.update(over)
        return cfg

    def test_game_running_on_igpu_is_forced_to_dgpu(self):
        self.run_monitor([{102: 5.0}] * 2, cfg_overrides=self._cfg())
        self.assertEqual(self.prefs.get(GAME), "GpuPreference=2;")

    def test_migration_notifications_muted_while_game_runs(self):
        """游戏在跑: 迁移照做, 通知静音。

        GAME 预先置为已迁移, 排除掉"游戏模式自身那条动作通知",
        这样 notes 里出现的任何一项都只能是本应被静音的通知。
        用真的 notify_cfg 才有意义 —— 静音闸门就在那个函数里。
        """
        gm.notify_cfg = self.saved["notify_cfg"]
        self.prefs[GAME] = "GpuPreference=2;"
        self.run_monitor([{100: 60.0, 102: 5.0}] * 4,
                         cfg_overrides=self._cfg(threshold_percent=1.0,
                                                 sustain_samples=1,
                                                 notify=True))
        self.assertTrue(gm._GAME_RUNNING)
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;",
                         "静音不等于停止迁移")
        self.assertEqual(self.notes, [], "游戏运行期间不弹迁移通知")

    def test_same_migration_notifies_when_no_game_runs(self):
        """上一条的对照: 没有游戏时同样的场景要弹通知, 否则静音测的是空转。"""
        gm.notify_cfg = self.saved["notify_cfg"]
        self.run_monitor([{100: 60.0}] * 4,
                         cfg_overrides={"threshold_percent": 1.0,
                                        "sustain_samples": 1, "notify": True})
        self.assertEqual([t for t, _m in self.notes], ["GPU 迁移成功"])

    def test_first_game_cycle_mutes_its_own_notice(self):
        """免打扰要盖住"游戏模式"自己那条通知。

        _GAME_RUNNING 原先在游戏块跑完之后才赋值, 首次发现游戏的那一轮闸门
        还停在上一轮的 False —— 游戏刚启动恰恰是最不该弹 Toast 的时刻。
        """
        gm.notify_cfg = self.saved["notify_cfg"]
        self.run_monitor([{102: 5.0}] * 2, cfg_overrides=self._cfg(notify=True))
        self.assertEqual(self.prefs.get(GAME), "GpuPreference=2;",
                         "静音不等于不迁移")
        self.assertEqual(self.notes, [], "首轮的游戏模式动作通知也要静音")

    def test_clearing_game_list_unmutes_later_notifications(self):
        """热重载把 game_processes 清空后, 通知必须恢复。

        归零点原先写在 `if cfg["game_processes"]:` 里面: 名单一空整块就被跳过,
        标记永久停在 True, 之后所有通知都不再弹, 只能重启监控进程才能恢复。
        第 1 轮只有游戏, 重载后第 2 轮起名单为空, heavy 到第 3 轮才超标 ——
        这样那条迁移通知只在标记真的归零时才可能出现。
        """
        gm.notify_cfg = self.saved["notify_cfg"]
        path = os.path.join(gm._DIR, "__reload_game__.json")
        write_test_config(path, notify=True, threshold_percent=1.0,
                          game_processes=["game.exe"])
        self.run_monitor([{102: 5.0}, {102: 5.0}, {100: 60.0}, {100: 60.0}],
                         config_path=path,
                         cfg_overrides=self._cfg(notify=True,
                                                 threshold_percent=1.0),
                         hooks={"on_sample": self.rewrite_once(
                             path, game_processes=[], notify=True,
                             threshold_percent=1.0)})
        self.assertFalse(gm._GAME_RUNNING, "名单清空后不该继续静音")
        self.assertIn("GPU 迁移成功", [t for t, _m in self.notes])

    def test_game_already_on_dgpu_not_rewritten(self):
        self.prefs[GAME] = "GpuPreference=2;"
        self.run_monitor([{102: 5.0}] * 2,
                         cfg_overrides=self._cfg(sustain_samples=99))
        self.assertEqual(self.prefs, {GAME: "GpuPreference=2;"})

    def test_game_already_on_dgpu_bare_value_not_rewritten(self):
        self.prefs[GAME] = "GpuPreference=2"
        self.run_monitor([{102: 5.0}] * 2,
                         cfg_overrides=self._cfg(sustain_samples=99))
        self.assertEqual(self.prefs, {GAME: "GpuPreference=2"})


class PowerSaverAdvance(LoopHarness):
    """直接调用 advance_power_saver: 每轮只推进一个进程的语义只有多进程才看得见,
    而循环夹具里同时只有一个独显进程。"""

    def cycle(self, dgpu_util, streak, notified, need=2):
        cfg = dict(gm.DEFAULT_CONFIG)
        cfg.update({"power_saver_notify": True, "power_saver_samples": need,
                    "power_saver_idle_percent": 10.0})
        gm.advance_power_saver(cfg, dgpu_util, streak, notified, True, None)

    def test_only_one_process_advances_per_cycle(self):
        self.prefs[HEAVY] = "GpuPreference=2;"
        self.prefs[DGPUAPP] = "GpuPreference=2;"
        streak, notified = defaultdict(int), set()
        for _ in range(3):
            self.cycle({100: 2.0, 101: 3.0}, streak, notified)
        self.assertEqual(self.prefs[HEAVY], "GpuPreference=1;", "先推进 pid 100")
        self.assertEqual(self.prefs[DGPUAPP], "GpuPreference=2;",
                         "3 轮里 pid 101 只被推进 1 次, 不该跟着改设置")
        self.assertEqual(notified, {HEAVY})
        self.assertEqual(dict(streak), {100: 2, 101: 1})

    def test_streak_resets_when_process_goes_active_again(self):
        streak, notified = defaultdict(int), set()
        self.prefs[HEAVY] = "GpuPreference=2;"
        self.cycle({100: 2.0}, streak, notified)
        self.assertEqual(streak[100], 1)
        self.cycle({100: 80.0}, streak, notified)
        self.assertEqual(streak[100], 0, "重新忙起来就该从零再数")


class HistoryAndDailyReport(LoopHarness):
    """append_history: history.jsonl 落盘、1MB 滚动、跨日日报与峰值。"""

    def read(self, name):
        path = os.path.join(gm._DIR, name)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_point_appended_and_peak_kept(self):
        daily = {"date": time.strftime("%Y-%m-%d"), "peak": 0.0, "peak_ts": "",
                 "migrated": []}
        secs = defaultdict(float)
        daily = gm.append_history(1000.0, 42.0, daily, secs, {})
        gm.append_history(1010.0, 30.0, daily, secs, {})
        rows = [json.loads(l) for l in self.read("history.jsonl").splitlines()]
        self.assertEqual([r["total"] for r in rows], [42.0, 30.0])
        self.assertEqual(daily["peak"], 42.0, "峰值只升不降")
        self.assertEqual(self.notes, [], "同日不该发日报")

    def test_day_rollover_reports_then_resets(self):
        daily = {"date": "2000-01-01", "peak": 77.7, "peak_ts": "12:00",
                 "migrated": ["a.exe", "b.exe"]}
        secs = defaultdict(float, {"game.exe": 120.0})
        gm.append_history(1000.0, 10.0, daily, secs, {})
        title, msg = self.notes[0]
        self.assertEqual(title, "GPU 日报")
        self.assertIn("2000-01-01", msg)
        self.assertIn("峰值 78%", msg)
        self.assertIn("迁移 2 个程序", msg)
        self.assertIn("游戏约 2 分钟", msg)
        self.assertEqual(self.read("report.csv").strip(),
                         "2000-01-01,77.7,12:00,2,2")
        self.assertEqual(dict(secs), {}, "跨日要清游戏时长")

    def test_history_rotates_beyond_1mb(self):
        with open(os.path.join(gm._DIR, "history.jsonl"), "w",
                  encoding="utf-8") as f:
            f.write("x" * 1_000_001)
        daily = {"date": time.strftime("%Y-%m-%d"), "peak": 0.0,
                 "peak_ts": "", "migrated": []}
        gm.append_history(1000.0, 5.0, daily, defaultdict(float), {})
        self.assertEqual(len(self.read("history.jsonl.1")), 1_000_001)
        self.assertEqual(self.read("history.jsonl").splitlines(),
                         [json.dumps({"ts": 1000, "total": 5.0})])


class ConfigHotReload(LoopHarness):
    """README 承诺"改 config.json 保存即生效": 逐个旋钮验证, 别只信注释。"""

    def test_threshold_change_takes_effect(self):
        path = os.path.join(gm._DIR, "__reload_thr__.json")
        write_test_config(path)
        self.run_monitor([{100: 5.0}] * 6, config_path=path,
                         cfg_overrides={"sustain_samples": 1},
                         hooks={"on_sample": self.rewrite_once(
                             path, threshold_percent=1.0)})
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;",
                         "阈值 50→1 之后 5% 的占用就该被迁移; "
                         "没迁移说明 eff_threshold 一直停在旧值")

    def test_log_to_file_toggle_takes_effect(self):
        """logfile 在循环外只算一次, 关掉开关后必须真的不再落盘。

        触发热重载两次: 第一次把 log_to_file 改成 false, 第二次只推后 mtime
        (内容不变, 免得依赖写文件的时间差)。文件里该有的只有重载前的启动行,
        两条"已热重载"都只出现在控制台。
        """
        path = os.path.join(gm._DIR, "__reload_log__.json")
        write_test_config(path, log_to_file=True)
        gm.log = self.saved["log"]
        stage = []

        def hook(_snap):
            if not stage:
                stage.append(1)
                self.write_and_bump(path, log_to_file=False)
            elif len(stage) == 1:
                stage.append(2)
                later = os.stat(path).st_mtime_ns + 10_000_000_000
                os.utime(path, ns=(later, later))

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.run_monitor([{100: 5.0}] * 8, config_path=path,
                             cfg_overrides={"log_to_file": True},
                             hooks={"on_sample": hook})
        with open(os.path.join(gm._DIR, "gpu_monitor.log"),
                  encoding="utf-8") as f:
            body = f.read()
        self.assertIn("启动监控", body, "重载前该照常写文件")
        self.assertEqual(body.count("已热重载"), 0,
                         "log_to_file 已关, 连重载提示本身也不该再进文件")
        self.assertEqual(buf.getvalue().count("已热重载"), 2, "控制台两次都要有")

    def test_changing_port_or_hotkeys_asks_for_a_restart(self):
        """web_port / hotkeys 在启动时一次性注册, 热重载改不动。

        改这两项时至少要留下一行提示, 而不是静默接受后让人以为已经生效。
        """
        for i, over in enumerate(({"web_port": 8788},
                                 {"hotkeys": {"panel": "ctrl+alt+j"}})):
            with self.subTest(**over):
                path = os.path.join(gm._DIR, f"__reload_hk{i}__.json")
                write_test_config(path)
                self.log_lines.clear()
                self.run_monitor([{100: 5.0}] * 3, config_path=path,
                                 hooks={"on_sample": self.rewrite_once(
                                     path, **over)})
                self.assertTrue(
                    any("需重启" in line for line in self.log_lines),
                    f"改了 {over} 该提示重启")

    def test_restart_hint_only_for_those_two_keys(self):
        """对照: 只改阈值时不该出现重启提示, 否则提示是无条件打印的。"""
        path = os.path.join(gm._DIR, "__reload_hk_ctl__.json")
        write_test_config(path)
        self.run_monitor([{100: 5.0}] * 3, config_path=path,
                         hooks={"on_sample": self.rewrite_once(
                             path, threshold_percent=1.0)})
        self.assertEqual([line for line in self.log_lines
                          if "需重启" in line], [])

    def test_vram_threshold_change_takes_effect(self):
        path = os.path.join(gm._DIR, "__reload_vram__.json")
        write_test_config(path, vram_threshold_mb=0)
        self.run_monitor([{}] * 6, vram={100: 2048}, config_path=path,
                         cfg_overrides={"vram_threshold_mb": 0},
                         hooks={"on_sample": self.rewrite_once(
                             path, vram_threshold_mb=1024)})
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;",
                         "显存阈值 vram_mb 是 nonlocal 的, 该生效")


class PowerAwarePolicy(LoopHarness):
    """power_aware: 用电池时换阈值。apply_config 里的"重算"要真能重算。"""

    def test_battery_switch_uses_battery_threshold(self):
        gm.on_battery = lambda: True
        self.run_monitor([{100: 45.0}] * 6,
                         cfg_overrides={"power_aware": True,
                                        "battery_threshold_percent": 40.0,
                                        "sustain_samples": 1})
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;",
                         "电池下阈值应是 40%, 45% 就该迁移")

    def test_reload_on_battery_reapplies_battery_threshold(self):
        """热重载先按新配置直取交流阈值, 下一轮必须由电源块改回电池阈值。

        30s 的重算闸门 (ps_check) 得被 apply_config 推开, 否则
        "电池 + 改配置"会让交流阈值一直用到下次拔插电源。
        """
        path = os.path.join(gm._DIR, "__reload_batt__.json")
        write_test_config(path)
        gm.on_battery = lambda: True
        self.run_monitor([{100: 5.0}] * 6, config_path=path,
                         cfg_overrides={"power_aware": False,
                                        "threshold_percent": 50.0,
                                        "sustain_samples": 1},
                         hooks={"on_sample": self.rewrite_once(
                             path, power_aware=True,
                             battery_threshold_percent=3.0)})
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;",
                         "重载后应回到电池阈值 3%, 5% 的占用要迁移")


class SnapshotContract(LoopHarness):
    """面板/托盘靠 hooks 与 _LAST_SNAPSHOT 取数, 键名是跨文件契约。"""

    def test_on_sample_snapshot_keys(self):
        seen = {}
        self.run_monitor([{100: 80.0}], cycles=1,
                         hooks={"on_sample": seen.update})
        self.assertEqual(
            set(seen),
            {"util_by_pid", "gpu_all_by_pid", "dgpu_util_by_pid",
             "shared_by_pid", "dedicated_by_pid", "igpu_luids",
             "dgpu_luids", "kind_cache"})
        self.assertEqual(seen["util_by_pid"], {100: 80.0})
        self.assertEqual(seen["gpu_all_by_pid"], {100: 80.0})
        self.assertIn(IGPU_LUID, seen["igpu_luids"])
        self.assertIn(DGPU_LUID, seen["dgpu_luids"])

    def test_history_point_uses_strongest_engine_semantics(self):
        pts = []
        self.run_monitor([{100: 42.0}], cycles=1,
                         cfg_overrides={"history": True,
                                        "interval_seconds": 10},
                         hooks={"on_history_point":
                                lambda ts, total: pts.append(total)})
        self.assertEqual(pts, [42.0], "总量口径=最强引擎, 不该超过 100%")

    def test_snapshot_exposes_same_aggregates(self):
        self.run_monitor([{100: 80.0}], cycles=1)
        snap = gm._LAST_SNAPSHOT
        self.assertEqual(snap["util_by_pid"], {100: 80.0})
        self.assertEqual(snap["dedicated_by_pid"], {})


class PreferenceSemantics(unittest.TestCase):
    """"已迁移"这个判断本身: 注册表字符串的几种真实写法都要落到同一个答案。"""

    BARE = r"D:\app\heavy.exe"
    FULL = r"D:\app\dgpuapp.exe"
    ADAPTER = r"D:\app\adapter.exe"
    OTHER = r"D:\app\other.exe"

    def test_high_performance_forms(self):
        cases = {
            "GpuPreference=2;": True,        # 本应用与 Windows 设置写的形态
            "GpuPreference=2": True,         # 缺尾分号, 本机实测 3 条
            "SpecificAdapter=10DE&2803&41231458;GpuPreference=2;": True,
            "GpuPreference=1;": False,       # 核显
            "GpuPreference=0;": False,
            "GpuPreference=20;": False,      # 前缀相同但不是 2
            "GpuPreference=1073741824;": False,
            "AutoHDREnable=1;SwapEffectUpgradeEnable=1;": False,
            "": False,
            None: False,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(gm.is_high_performance(value), expected)

    def test_status_counts_bare_value_as_migrated(self):
        prefs = {self.BARE: "GpuPreference=2", self.FULL: "GpuPreference=2;",
                 self.ADAPTER: "SpecificAdapter=10DE&2803&41231458;"
                               "GpuPreference=1073741824;",
                 self.OTHER: "GpuPreference=1;"}
        saved = gm.list_gpu_prefs
        gm.list_gpu_prefs = lambda: sorted(prefs.items())
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gm.cmd_status()
        finally:
            gm.list_gpu_prefs = saved
        out = buf.getvalue()
        self.assertIn("已迁移到独显 (2 个)", out)
        self.assertIn(self.BARE, out.split("其他设置")[0])
        self.assertIn(self.OTHER, out.split("其他设置")[1])

    def test_tray_panel_uses_the_same_rule(self):
        """面板上那个"已迁移"标记必须和循环里的判定同源, 否则两处会分叉。"""
        import gpu_tray

        class Fake:
            def __init__(self):
                self._pref_cache = {}
                self._pref_ts = 0.0

        saved = gpu_tray.get_gpu_preference
        gpu_tray.get_gpu_preference = {"a": "GpuPreference=2",
                                       "b": "GpuPreference=1;"}.get
        try:
            self.assertTrue(gpu_tray.TrayApp._pref_of(Fake(), "a"))
            self.assertFalse(gpu_tray.TrayApp._pref_of(Fake(), "b"))
        finally:
            gpu_tray.get_gpu_preference = saved

    def test_clear_only_dgpu_removes_bare_value_too(self):
        prefs = {self.BARE: "GpuPreference=2", self.FULL: "GpuPreference=2;",
                 self.ADAPTER: "SpecificAdapter=10DE&2803&41231458;"
                               "GpuPreference=1073741824;",
                 self.OTHER: "GpuPreference=1;"}
        deleted = []
        saved_list, saved_del = gm.list_gpu_prefs, gm.clear_gpu_preference
        gm.list_gpu_prefs = lambda: sorted(prefs.items())
        gm.clear_gpu_preference = lambda exe: deleted.append(exe) is None
        try:
            cleared = gm.clear_all_gpu_prefs(only_dgpu=True)
        finally:
            gm.list_gpu_prefs, gm.clear_gpu_preference = saved_list, saved_del
        self.assertEqual(set(cleared), {self.BARE, self.FULL})
        self.assertNotIn(self.ADAPTER, cleared, "别人钉的特定独显不该被清掉")
        self.assertNotIn(self.OTHER, cleared)


class TrayLifecycleLog(unittest.TestCase):
    """托盘退出必须留一行日志。

    本机今天连续两次静默退出: stdout/stderr 无 Traceback、tray_error.log 不存在、
    事件日志里也没有崩溃记录 —— 因为应用退出时一个字都不写, 事后无从归因。
    `stop` 标志能区分"用户点了退出"和"消息循环被外力打断"。
    """

    def _capture(self, stop_flag, loop_body):
        import gpu_tray
        tmp = tempfile.mkdtemp(prefix="gpumig-tray-")
        saved = gpu_tray._DIR
        gpu_tray._DIR = tmp
        try:
            class Fake:
                stop = stop_flag

                def _monitor_thread(self):
                    pass

            Fake.icon = type("I", (), {"run": staticmethod(loop_body)})()
            gpu_tray.TrayApp.run(Fake())
            with open(os.path.join(tmp, "tray_debug.log"),
                      encoding="utf-8") as f:
                return f.read()
        finally:
            gpu_tray._DIR = saved
            shutil.rmtree(tmp, ignore_errors=True)

    def test_menu_quit_is_logged_as_stop(self):
        text = self._capture(True, lambda: None)
        self.assertIn("tray loop end", text)
        self.assertIn("stop=True", text)

    def test_forced_loop_exit_is_logged_as_stop_false(self):
        """外力打断消息循环(任务栏/会话变动)时 stop 仍是 False —— 这正是归因点。"""
        text = self._capture(False, lambda: None)
        self.assertIn("tray loop end", text)
        self.assertIn("stop=False", text)

    def test_monitor_thread_reports_its_own_exit(self):
        import gpu_tray
        tmp = tempfile.mkdtemp(prefix="gpumig-tray-")
        saved_dir, saved_cmd = gpu_tray._DIR, gpu_tray.cmd_monitor
        gpu_tray._DIR = tmp
        gpu_tray.cmd_monitor = lambda cfg, path, hooks=None: None
        try:
            app = object.__new__(gpu_tray.TrayApp)
            app.cfg, app.config_path = {}, None
            app._before_cycle = app._on_sample = app._on_history_point = \
                lambda *_a: None
            app._monitor_thread()
            with open(os.path.join(tmp, "tray_debug.log"),
                      encoding="utf-8") as f:
                text = f.read()
        finally:
            gpu_tray._DIR, gpu_tray.cmd_monitor = saved_dir, saved_cmd
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertIn("monitor enter", text)
        self.assertIn("monitor exit", text)

    def test_monitor_thread_error_path_is_logged_twice(self):
        """cmd_monitor 抛异常时: 既要留一行"error", 也要落 tray_error.log。

        这个 except 是吞掉异常的, 少了痕迹就等于又一次静默死亡。
        """
        import gpu_tray
        tmp = tempfile.mkdtemp(prefix="gpumig-tray-")
        saved_dir, saved_cmd = gpu_tray._DIR, gpu_tray.cmd_monitor
        gpu_tray._DIR = tmp

        def boom(cfg, path, hooks=None):
            raise RuntimeError("采样线程故意炸")
        gpu_tray.cmd_monitor = boom
        try:
            app = object.__new__(gpu_tray.TrayApp)
            app.cfg, app.config_path = {}, None
            app._before_cycle = app._on_sample = app._on_history_point = \
                lambda *_a: None
            app._monitor_thread()          # 不许把异常抛出来
            with open(os.path.join(tmp, "tray_debug.log"),
                      encoding="utf-8") as f:
                text = f.read()
            with open(os.path.join(tmp, "tray_error.log"),
                      encoding="utf-8") as f:
                tb = f.read()
        finally:
            gpu_tray._DIR, gpu_tray.cmd_monitor = saved_dir, saved_cmd
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertIn("monitor error", text)
        self.assertIn("RuntimeError: 采样线程故意炸", tb)


if __name__ == "__main__":
    unittest.main(verbosity=2)
