"""gpu_monitor 的回归测试: 纯标准库, 不碰注册表/真实 PDH/真实进程。

跑法: python -X utf8 -m unittest discover -s tests
(-X utf8 只为让失败信息里的中文在 GBK 控制台上不糊, 与测试结果无关)
锁定的是"重构不能改变的行为": 实例路径解析、显卡分类、通配展开的三段式
协议、迁移判定门槛与排除优先级、省电与游戏模式、日志节奏、面板数据契约。
"""

import ast
import atexit
import contextlib
import ctypes
import faulthandler
import inspect
import io
import re
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unicodedata
import time
import types
import unittest
from collections import defaultdict
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gpu_monitor as gm  # noqa: E402
# gpu_tray 顶层是 `from gpu_monitor import pid_to_name` 这种绑定, 绑的是
# import 那一刻的值。谁第一个 import 它, 那些共用名字就绑到谁身上:
# 放进用例里 import, 正好撞上某条用例正拿着 gm.pid_to_name 的替身,
# 托盘就永久拿住那份替身, 而替身的还原只发生在 gm 那一侧。顶层 import
# 保证绑定发生在任何替身出现之前。
import gpu_tray  # noqa: E402

# 托盘的排查日志路径是 `os.path.join(_DIR, ...)` **调用时**才拼的, 而只有
# TrayLifecycleLog 那几条用例自己隔离了 _DIR: 其余用例一碰 _dbg/托盘错误分支,
# 就往仓库里那份真实的 tray_debug.log 写字(实测整套跑一次, 现网日志里多出
# `RuntimeError: 起不来` 和一串 unittest.mock 的 traceback)。顶层把 _DIR 指到
# 套件临时目录, 于是"没隔离"的用例也只会写进临时目录; 守卫见
# TrayDebugLogIsolation。注意只动 gpu_tray._DIR —— gm._DIR 被用来读仓库里的
# README/.gitignore/config.json, 改了那一半守卫就全瞎。
# 套件临时目录按 pid 定名, 不用 mkdtemp: `python tests/test_gpu_monitor.py`
# 会以 `__main__` 跑一遍本文件, 而倒序守卫内部又 discover 出同名的第二份模块
# 实例 —— mkdtemp 会让两份各拿一个目录, 断言"模块上的 _DIR 就是我这份额"必然
# 红。定名后两份指向同一个目录, 这条不变量在任何跑法下都成立。
_SUITE_TMP = os.path.join(tempfile.gettempdir(),
                          "gpumig-suite-%d" % os.getpid())
os.makedirs(_SUITE_TMP, exist_ok=True)
gpu_tray._DIR = _SUITE_TMP
# 真实"exe 所在目录": 不读 gpu_tray._DIR(上面那句已经把它换掉, 而第二份模块
# 实例读到的就是那个临时目录), 而是照 gpu_tray.py 里的同一段算法重算一遍 ——
# 这样"默认状态文件与 exe 同目录"这条不变量在任何跑法下都判得准。
_EXE_DIR = (os.path.dirname(os.path.abspath(sys.executable))
            if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(gpu_tray.__file__)))
atexit.register(shutil.rmtree, _SUITE_TMP, ignore_errors=True)

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
HEAVY_PID = 100          # heavy.exe 的 pid, 采样字典用它当键
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


class SampleEngineFold(unittest.TestCase):
    """collect_gpu_sample 交给聚合层的读数, 也必须是"最强引擎"。

    AggregateRules 只钉住了 aggregate_usage 自己取 max —— 但它拿到的每个键
    早已是逐引擎求和后的数, 虚高发生在它之前。本机实测一个进程核显读数
    101.8% (3D 3.7% + Copy 80.8%), 于是"只有 Copy 在动"的进程被当成高占用
    送去迁移。
    """

    def collect(self, pct_by_path):
        """假 PDH 驱动一次真采样: 每计数器第一读 (0, 1e7)、第二读
        (pct*1e5, 2e7), 差值口径正好还原成 pct。"""

        class FakePdh:
            def __init__(self):
                self.pct = {}      # id(handle) -> 该引擎利用率
                self.reads = {}    # id(handle) -> 已读次数

            def PdhOpenQueryW(self, machine, ctx, out):
                return 0

            def PdhAddCounterW(self, query, path, reserved, out):
                self.pct[id(out._obj)] = pct_by_path[path]
                return 0

            def PdhCollectQueryData(self, query):
                return 0

            def PdhCloseQuery(self, query):
                return 0

        fake = FakePdh()

        def fake_read_raw(handle):
            key = id(handle)
            if key not in fake.pct:
                return None
            fake.reads[key] = fake.reads.get(key, 0) + 1
            if fake.reads[key] == 1:
                return (0, 10_000_000)
            return (int(fake.pct[key] * 100_000), 20_000_000)

        saved = (gm.pdh, gm.expand_counter_paths, gm._pdh_read_raw,
                 gm.time.sleep)
        try:
            gm.pdh = fake
            gm.expand_counter_paths = (
                lambda spec: list(pct_by_path)
                if spec.startswith("GPU Engine") else [])
            gm._pdh_read_raw = fake_read_raw
            gm.time.sleep = lambda _s: None
            usage, _shared, _dedicated, _index = gm.collect_gpu_sample()
        finally:
            (gm.pdh, gm.expand_counter_paths, gm._pdh_read_raw,
             gm.time.sleep) = saved
        return usage

    def test_engines_on_one_adapter_take_the_strongest(self):
        usage = self.collect({
            engine_path(7, IGPU_LUID, 0, 0, "3D"): 3.7,
            engine_path(7, IGPU_LUID, 0, 4, "Copy"): 80.8,
            engine_path(7, DGPU_LUID, 0, 0, "3D"): 89.4})
        self.assertEqual(usage[(7, IGPU_LUID, 0)], 80.8,
                         "3D 3.7% + Copy 80.8% 是并发引擎, 任务管理器读 80.8%")
        self.assertEqual(usage[(7, DGPU_LUID, 0)], 89.4)

    def test_no_process_can_read_above_100_percent(self):
        usage = self.collect({
            engine_path(7, DGPU_LUID, 0, 0, "3D"): 89.4,
            engine_path(7, DGPU_LUID, 0, 4, "Copy"): 12.4,
            engine_path(8, DGPU_LUID, 0, 0, "3D"): 60.0,
            engine_path(8, DGPU_LUID, 0, 3, "VideoDecode"): 45.0})
        for pid in (7, 8):
            with self.subTest(pid=pid):
                self.assertLessEqual(usage[(pid, DGPU_LUID, 0)], 100.0)

    def test_a_tenth_of_a_percent_is_not_wiped_out(self):
        """0.4% 这种真实活动不能被 0.5% 的门槛扎平。

        本机实测: 一个 1 秒窗口里 633 个引擎完全空闲
        (n 精确为 0), 另多个引擎有 0.01%~0.41% 的真实活动,
        任务管理器照常显示 0.4% —— 原先的 0.5% 门槛
        把它们整块抹成 0%, 面板上"看着没人用"其实有人在用。
        """
        usage = self.collect({
            engine_path(7, IGPU_LUID, 0, 0, "3D"): 0.4,
            engine_path(8, IGPU_LUID, 0, 0, "3D"): 0.0})
        self.assertEqual(usage[(7, IGPU_LUID, 0)], 0.4)
        self.assertNotIn((8, IGPU_LUID, 0), usage,
                         "精确空闲的引擎还是不进表")


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

    def test_integrated_intel_arc_is_igpu_without_ram(self):
        """整合 Intel Arc (Core Ultra 集显)不带 A/B 型号, 直接判核显。

        独显 Arc 带型号且名字里的 intel 也让 IGPU_PAT 命中, 按设计交给
        RAM 定夺(见上面的 TABLE); 但整合的 "Intel Arc Graphics" 没有
        型号, 不该也被拖着: WMI 失败时它变 unknown, 拿不准还会被共享
        内存里那个大 aperture 推成 dgpu —— 等于把集显当成了迁移目标。
        """
        for name in ("Intel Arc Graphics", "Intel(R) Arc(TM) Graphics"):
            self.assertEqual(gm.classify_gpu(name, None), "igpu", name)

    def test_discrete_arc_model_still_defers_to_ram(self):
        """带 A/B 型号(含 Arc Pro)是独显: 无 RAM 仍按既有设计留 unknown,
        有 RAM 才判 dgpu。锁住「改整合 Arc 别顺手把独显也一起改掉」。
        """
        self.assertEqual(gm.classify_gpu("Intel Arc A770", None),
                         "unknown")
        self.assertEqual(gm.classify_gpu("Intel Arc A770", 16384),
                         "dgpu")
        self.assertEqual(gm.classify_gpu("Intel Arc Pro A60M", 8192),
                         "dgpu")

    def test_real_world_display_name_forms(self):
        """真机上报的名字带 (TM), 核显 APU 还常不带 Graphics 后缀。

        §29 一度把独显 Arc 判死成核显: 找名字时要求 arc 后紧跟型号, 而
        Intel 真名是 "Intel(R) Arc(TM) A770 Graphics" —— arc 和型号之间
        隔着 (TM), 匹配不上就只剩 IGPU 的 intel 单中, 反而判了 igpu。
        这里用带 (TM) 的真名把独显/整合两种都钉住; 顺带补 AMD 集显 APU
        去掉 Graphics 后缀时(610M/680M/890M)不再漏判成 unknown。
        """
        self.assertEqual(
            gm.classify_gpu("Intel(R) Arc(TM) A770 Graphics", None),
            "unknown")
        self.assertEqual(
            gm.classify_gpu("Intel(R) Arc(TM) A770 Graphics", 16384),
            "dgpu")
        self.assertEqual(
            gm.classify_gpu("Intel(R) Arc(TM) B580 Graphics", 12288),
            "dgpu")
        self.assertEqual(
            gm.classify_gpu("Intel(R) Arc(TM) Pro A60M", 8192), "dgpu")
        self.assertEqual(gm.classify_gpu("AMD Radeon 610M", None),
                         "igpu")
        self.assertEqual(gm.classify_gpu("AMD Radeon 680M", None),
                         "igpu")
        self.assertEqual(gm.classify_gpu("AMD Radeon 890M", None),
                         "igpu")
        self.assertEqual(gm.classify_gpu("AMD Radeon RX 6600", None),
                         "dgpu")

    def test_software_fallback_adapters_are_virtual(self):
        u"""驱动失效或远程会话的回退适配器不是真显卡, 不能落进 igpu。

        驱动起不来时 Windows 会换成软件回退适配器: Win10 是
        "Microsoft Basic Display Adapter", Win11 是 "Microsoft Basic
        Display Driver", 老机器是 "Standard VGA Graphics Adapter",
        RDP 会话里是 "Microsoft Remote Display Adapter"。它们
        AdapterRAM 都是 0, 旧规则下连 IGPU_PAT 都不中, 会靠 RAM 兜底
        判成 igpu —— 于是进程被往这块假"核显"迁移, 迁移就丢了。
        """
        for name in ("Microsoft Basic Display Adapter",
                     "Microsoft Basic Display Driver",
                     "Standard VGA Graphics Adapter",
                     "Microsoft Remote Display Adapter"):
            self.assertEqual(gm.classify_gpu(name), "virtual", name)
            self.assertEqual(gm.classify_gpu(name, 0), "virtual", name)

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

    def test_a_wrapped_adapter_ram_is_read_as_unsigned(self):
        """AdapterRAM 是 uint32: 4GB 以上的卡会被输成负数。

        PowerShell 把 2147483648 以上输成 -2147483648 这样的负数, 直接
        int() 拿去除得到负 MB, classify_gpu 的 ">=1500" 就不成立 ——
        独显被判成核显, 于是反把进程往集显上赶。
        """
        def wrapped(cmd, *a, **k):
            self.calls.append(cmd)
            return subprocess.CompletedProcess(
                cmd, 0, stdout="Mystery 8GB Card|-2147483648\n", stderr="")

        gm.subprocess.run = wrapped
        gm._DEDICATED_MB = None      # 换的是 stdout, 缓存得先清掉
        self.assertEqual(gm.query_dedicated_mb("Mystery 8GB Card"), 2048)
        self.assertEqual(gm.classify_gpu("Mystery 8GB Card", 2048), "dgpu")


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

    def test_absurd_reported_length_is_refused_not_allocated(self):
        # 缓冲区是照 PDH 报回的字符数现分配的。没有上界时这里会真的
        # create_unicode_buffer(2**30+1024): 采到 MemoryError 而不是 OSError,
        # 采样线程硬死。现在要在分配之前就拒绝, 一次都不重试。
        fake = self.FakePDH(gm.PDH_MORE_DATA, 1 << 30)
        gm.pdh = fake
        with self.assertRaises(OSError) as ctx:
            gm.expand_counter_paths(r"GPU Engine(*)\X")
        self.assertIn("拒绝分配", str(ctx.exception))
        self.assertEqual(fake.calls, 1, "荒谬长度不该重试五次, 每次照分配")

    def test_a_length_at_the_limit_still_expands(self):
        # 上限要拦得住荒谬值, 也要够不着真实用量(本机实测 507 条 / 5.5 万字符)。
        paths = [engine_path(1, IGPU_LUID, 0, 0, "3D")]
        gm.pdh = self.FakePDH(gm.PDH_MORE_DATA,
                              gm._EXPAND_CHARS_MAX - 1, paths)
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


class ReadmeConfigTable(unittest.TestCase):
    """README 配置表必须逐键覆盖 DEFAULT_CONFIG。

    加配置键忘写文档, 用户照 README 手改 config.json 时就少一个可调的
    旋钮; 表里写了代码里没有的键, 照着填了也不生效 —— 两个方向都要查。
    文档也是交付物, 和代码一样要有测试看着。
    """

    def _table_keys(self):
        import re
        readme = io.open(os.path.join(gm._DIR, "README.md"),
                         encoding="utf-8").read()
        sec = readme.split("## 配置（config.json）", 1)[1]
        sec = sec.split("\n## ", 1)[0]
        return set(re.findall(r"^\| `([A-Za-z_]+)` \|", sec, re.M))

    def test_every_default_config_key_is_documented(self):
        missing = set(gm.DEFAULT_CONFIG) - self._table_keys()
        self.assertEqual(missing, set(),
                         "DEFAULT_CONFIG 有这些键, README 配置表没写")

    def test_the_table_documents_no_unknown_keys(self):
        extra = self._table_keys() - set(gm.DEFAULT_CONFIG)
        self.assertEqual(extra, set(),
                         "README 配置表有这些键, DEFAULT_CONFIG 里没有")


class ReadmeDefaultsMatchCode(unittest.TestCase):
    """README 配置表里的默认值必须和 DEFAULT_CONFIG 对得上。

    ReadmeConfigTable 只查了"键两边都在", 没查**默认值**: 代码把
    threshold_percent 默认从 50 改成 40、README 忘了改, 键两边都还在, 没人报错,
    但照 README 手改 config.json 的用户拿到的是另一套起点。这里把可比的默认值
    (布尔 / 数字 / 空列表)钉死; dict(hotkeys) 与非空容器只求键在(ReadmeConfigTable
    已覆盖), 不逐字节比 README 的散文式写法。
    """

    def _rows(self):
        import re
        readme = io.open(os.path.join(gm._DIR, "README.md"),
                         encoding="utf-8").read()
        sec = readme.split("## 配置（config.json）", 1)[1]
        sec = sec.split("\n## ", 1)[0]
        out = {}
        for line in sec.splitlines():
            m = re.match(r"^\| `([A-Za-z_]+)` \| ([^|]*) \|", line)
            if m:
                out[m.group(1)] = m.group(2).strip()
        return out

    def _as_doc_default(self, value):
        # 把代码默认值折算成 README 表格里的写法; None=无法/不宜逐字比, 只查键在
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, list):
            return "[]" if not value else None
        if isinstance(value, dict):
            return None
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return str(int(value)) if value.is_integer() else repr(value)
        return str(value)

    def test_documented_defaults_match_code(self):
        rows = self._rows()
        checked = 0
        for key, default in gm.DEFAULT_CONFIG.items():
            want = self._as_doc_default(default)
            if want is None:
                continue
            self.assertIn(key, rows, "README 配置表缺键 %s" % key)
            self.assertEqual(rows[key], want,
                             "README 里 %s 的默认值和 DEFAULT_CONFIG 不一致" % key)
            checked += 1
        self.assertGreater(checked, 15,
                           "比中的行少得可疑, README 表格/抽取逻辑该修了")


class RuntimeConfigIsNotTracked(unittest.TestCase):
    """config.json 是本机运行期文件, 不该被 git 跟踪。

    它一度被提交进仓库, 而托盘/设置窗每次保存都重写它 —— 于是每一台
    跑过程序的机器上, `git status` 都永久挂着一个 modified
    config.json, 顺手 `git add -A` 还会把个人设置(阈值/端口/名单)
    提交出去。其余运行期产物(log/history/report/panel_ui)早就在
    .gitignore 里, 只有它漏了。
    """

    def test_gitignore_covers_config_json(self):
        ignores = io.open(os.path.join(gm._DIR, ".gitignore"),
                          encoding="utf-8").read().splitlines()
        self.assertIn("config.json", [l.strip() for l in ignores],
                      "config.json 要进 .gitignore")

    def test_config_json_is_not_tracked(self):
        try:
            out = subprocess.run(
                ["git", "-C", gm._DIR, "ls-files", "--", "config.json"],
                capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            self.skipTest("这台机器上没有 git, 跳过")
        self.assertEqual(out.strip(), "",
                         "config.json 还在版本控制里, 运行期改动会污染工作树")


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


class ConfigPersistence(unittest.TestCase):
    """config.json 的读写: 坏文件不能带走好设置, 写失败不能留下半截文件。

    起因: config.json 由 5 处代码写、4 处代码读, 之前全是 open(path,"w")
    先清空再写 —— 写一半失败正好制造出损坏文件, 而 load_config 又会为它
    抛异常, 两个毛病会互相放大。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gpumig-cfg-")
        self.path = os.path.join(self.dir, "config.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_raw(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def read_raw(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_corrupt_file_falls_back_to_defaults_instead_of_raising(self):
        self.write_raw('{"threshold_percent": 40, "mode": "sav')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(gm.load_config(self.path), gm.DEFAULT_CONFIG)
        self.assertIn("配置无法解析", buf.getvalue())

    def test_non_object_json_is_not_a_config(self):
        for text in ('[1, 2]', '"nope"', 'null', '3'):
            self.write_raw(text)
            self.assertIsNone(gm.read_json_dict(self.path),
                              f"{text} 不是配置对象")

    def test_missing_file_is_empty_not_broken(self):
        self.assertEqual(gm.read_json_dict(self.path), {})

    def test_adding_an_exclusion_preserves_unrelated_keys(self):
        self.write_raw(json.dumps({"threshold_percent": 40, "mode": "saver",
                                   "hotkeys": {"panel": "ctrl+alt+g"}}))
        self.assertTrue(gm.save_exclude_process(self.path, "chrome.exe"))
        cfg = gm.read_json_dict(self.path)
        self.assertEqual(cfg["exclude_processes"], ["chrome.exe"])
        self.assertEqual(cfg["mode"], "saver")
        self.assertEqual(cfg["hotkeys"], {"panel": "ctrl+alt+g"})

    def test_writer_refuses_to_overload_a_corrupt_config(self):
        broken = '{"threshold_percent": 40, "hotkeys": '
        self.write_raw(broken)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertFalse(gm.save_ignore_process(self.path, "game.exe"))
        self.assertEqual(self.read_raw(), broken, "解析失败时不该覆盖原文件")
        self.assertIn("已放弃写入", buf.getvalue())

    def test_failed_replace_keeps_the_original_and_leaves_no_temp(self):
        original = json.dumps({"threshold_percent": 40})
        self.write_raw(original)
        with mock.patch("os.replace", side_effect=OSError(28, "no space")):
            self.assertFalse(gm.write_json_dict(self.path, {"a": 1}))
        self.assertEqual(self.read_raw(), original)
        self.assertEqual(os.listdir(self.dir), ["config.json"],
                         "写失败要把临时文件清掉, 不然目录里全是垃圾")

    def test_each_write_gets_its_own_temp_file(self):
        """托盘线程和监控线程会并发写配置, 共用一个临时名会互相截断。"""
        seen = []
        real_replace = os.replace

        def spy(src, dst):
            seen.append(src)
            return real_replace(src, dst)

        with mock.patch("os.replace", side_effect=spy):
            self.assertTrue(gm.write_json_dict(self.path, {"a": 1}))
            self.assertTrue(gm.write_json_dict(self.path, {"b": 2}))
        self.assertEqual(len(set(seen)), 2, "两次写用了同一个临时文件")
        self.assertEqual(gm.read_json_dict(self.path), {"b": 2})
        self.assertEqual(os.listdir(self.dir), ["config.json"])

    def test_adding_to_hand_written_string_list_keeps_the_name_whole(self):
        """名单写成单个字符串时, 右键"永不迁移"不能把它炸成单字母。

        set("chrome.exe") 得到的是一串字符, 写回去连用户原来那一项一起
        抹掉 —— 排除名单静默失效正是这么来的。
        """
        self.write_raw(json.dumps({"exclude_processes": "chrome.exe",
                                   "threshold_percent": 40}))
        self.assertTrue(gm.save_exclude_process(self.path, "notepad.exe"))
        cfg = gm.read_json_dict(self.path)
        self.assertEqual(cfg["exclude_processes"],
                         ["chrome.exe", "notepad.exe"])
        self.assertEqual(cfg["threshold_percent"], 40,
                         "顺带别把别的设置冲掉")

    def test_adding_to_a_non_list_value_does_not_explode(self):
        """42 / null / true 也不能把右键菜单炸掉, 更不能写回一串垃圾。"""
        for bad in (42, None, True):
            self.write_raw(json.dumps({"ignore_processes": bad}))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertTrue(gm.save_ignore_process(self.path, "game.exe"))
            self.assertEqual(
                gm.read_json_dict(self.path)["ignore_processes"],
                ["game.exe"], f"{bad!r} 没被重写成正列表")
            self.assertIn("已重写为列表", buf.getvalue())
            self.assertNotIn("Traceback", buf.getvalue())

    def test_adding_a_name_when_the_key_is_absent_logs_nothing(self):
        """键不存在是正常状态, 不该报一句"不是列表"吓用户。"""
        self.write_raw(json.dumps({"threshold_percent": 40}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertTrue(gm.save_exclude_process(self.path, "a.exe"))
        self.assertEqual(buf.getvalue(), "")
        self.assertEqual(gm.read_json_dict(self.path)["exclude_processes"],
                         ["a.exe"])

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

        def fake_collect(allowed_luids=None, normalize=False, window=None):
            fake_collect.windows.append(window)
            item = samples[min(state["i"], len(samples) - 1)]
            state["i"] += 1
            igpu, dgpu = item if isinstance(item, tuple) else (item, {})
            usage = {(pid, IGPU_LUID, 0): v for pid, v in igpu.items()}
            usage.update({(pid, DGPU_LUID, 0): v for pid, v in dgpu.items()})
            dedicated = {(pid, IGPU_LUID, 0): mb * 1024 * 1024
                         for pid, mb in vram.items()}
            return usage, {}, dedicated, {(IGPU_LUID, 0): None,
                                          (DGPU_LUID, 0): None}

        fake_collect.windows = []
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


class MigrationVerification(unittest.TestCase):
    """重启后的迁移验证: 确认/否认/超时三种结局都要有回执。"""

    def _run(self, pending, usage, igpu_luids, dgpu_luids, now):
        lines, notes = [], []
        saved = (gm.log, gm.notify_cfg)
        gm.log = lambda msg, logfile=None: lines.append(msg)
        gm.notify_cfg = lambda cfg, t, m: notes.append((t, m))
        try:
            gm.verify_pending(pending, usage, igpu_luids, dgpu_luids, now,
                              {"notify": True})
        finally:
            gm.log, gm.notify_cfg = saved
        return lines, notes

    def test_confirms_when_the_restarted_process_lands_on_the_dgpu(self):
        pending = {HEAVY: {"pid": 200, "since": 1000.0, "name": "heavy.exe"}}
        lines, notes = self._run(pending, {(200, DGPU_LUID, 0): 30.0},
                                 {IGPU_LUID}, {DGPU_LUID}, 1005.0)
        self.assertEqual(pending, {}, "确认过就该移出跟踪")
        self.assertTrue(any("已确认运行在独显" in l for l in lines), lines)
        self.assertEqual(notes[0][0], "迁移验证成功")

    def test_denies_when_it_is_still_on_the_igpu_after_the_grace_period(self):
        pending = {HEAVY: {"pid": 200, "since": 1000.0, "name": "heavy.exe"}}
        lines, notes = self._run(pending, {(200, IGPU_LUID, 0): 30.0},
                                 {IGPU_LUID}, {DGPU_LUID}, 1030.0)
        self.assertEqual(pending, {})
        self.assertTrue(any("仍在核显" in l for l in lines), lines)
        self.assertEqual(notes[0][0], "迁移未生效")

    def test_stays_pending_while_the_igpu_sighting_is_still_fresh(self):
        """刚重启 5 秒就看到它在核显上: 可能只是还没切过去, 别急着判失败。"""
        pending = {HEAVY: {"pid": 200, "since": 1000.0, "name": "heavy.exe"}}
        lines, notes = self._run(pending, {(200, IGPU_LUID, 0): 30.0},
                                 {IGPU_LUID}, {DGPU_LUID}, 1005.0)
        self.assertIn(HEAVY, pending, "宽限期内不该下结论")
        self.assertEqual(lines, [])
        self.assertEqual(notes, [])

    def test_speaks_up_when_the_restarted_process_never_touches_the_gpu(self):
        """90 秒内一次 GPU 活动都没有时, 老代码静静把这条验证删掉。

        用户那头"迁移成功, 进程已自动重启"刚看完, 然后什么都没有 ——
        既没说生效也没说没生效。这种情况确实无法确认, 但"无法确认"
        本身就得说出来, 否则和 §40/§45/§49 修的沉默失败是同一类病。
        """
        pending = {HEAVY: {"pid": 200, "since": 1000.0, "name": "heavy.exe"}}
        lines, notes = self._run(pending, {}, {IGPU_LUID}, {DGPU_LUID},
                                 1200.0)
        self.assertEqual(pending, {}, "超时还是要移出跟踪")
        self.assertTrue(any("无法确认" in l for l in lines),
                        f"超时要留痕: {lines}")
        self.assertEqual(notes[0][0], "迁移未确认")
        self.assertIn("heavy.exe", notes[0][1], "通知里要点名是哪个程序")


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

    def test_a_write_that_never_lands_is_not_reported_as_success(self):
        """主循环迁移也得回读: 不抵错 != 值落了地。

        §54 给托盘详情窗补了回读, 但监控循环这条主路径一直是裸的:
        RegSetValueExW 返回 0 就播"迁移成功", 并把这条记进 done
        (本轮不再重试)和日报的 migrated(多算一个)。杀软/策略
        拦在半路时用户看到的就是一句假成功。
        """
        saved_set = gm.set_gpu_preference
        gm.set_gpu_preference = lambda full, value="GpuPreference=2;": None
        try:
            self.run_monitor([{100: 80.0}] * 3)
        finally:
            gm.set_gpu_preference = saved_set
        self.assertEqual(self.prefs, {}, "没落地就不该记进 done")
        self.assertEqual([t for t, _m in self.notes],
                         ["GPU 迁移未生效"], self.notes)
        self.assertIn("heavy.exe", self.notes[0][1],
                      "失败也得点名是哪个程序")
        # 日志是用户事后排查的主路径: 写错别字("注内表")会让排查的人
        # 以为自己看错了行, 这句话必须逐字可读。
        self.assertIn("  -> 回读确认失败: 注册表里仍不是独显",
                      self.log_lines, self.log_lines)

    def test_successful_migration_is_still_reported(self):
        """反向一面: 回读确认落地时该报成功。

        只写失败方案的测试会把"永远不知可"当成能用的改法。
        """
        self.run_monitor([{100: 80.0}] * 3)
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;")
        self.assertEqual([t for t, _m in self.notes], ["GPU 迁移成功"])

    def test_migration_notifies_once(self):
        self.run_monitor([{100: 80.0}] * 3)
        self.assertEqual([t for t, _m in self.notes], ["GPU 迁移成功"])

    def test_vram_alone_triggers_migration(self):
        """利用率始终为 0, 仅核显显存超阈也要迁移。"""
        self.run_monitor([{}] * 3, vram={100: 2048},
                         cfg_overrides={"vram_threshold_mb": 1024})
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;")

    def test_vram_reason_says_shared_plus_dedicated(self):
        """文案必须说清口径: 阈值比的是专用+共享合计, 不是单"专用"。

        核显的专用显存只是 BIOS 划的那一小块, 写"专用"会让人以为 1GB
        专用显存才会触发, 从而误判这条规则为什么从来不响。
        """
        self.assertEqual(gm.hot_reason(0.0, 2048.0, 50.0, 1024),
                         "核显显存(专用+共享) 2048.0 MiB")

    def test_utilization_reason_keeps_one_decimal(self):
        self.assertEqual(gm.hot_reason(50.4, 0.0, 50.0, 0),
                         "核显利用率 50.4%",
                         "弹窗/通知里的读数要跟 --list 一致, 不能四舍五入到 50%")

    def test_candidate_without_a_full_path_is_skipped(self):
        """只有名字没有路径的进程当不了候选: 注册表拿路径当键。"""
        cfg = dict(gm.DEFAULT_CONFIG)
        cfg["exclude_defaults"] = False
        saved = gm.pid_to_name
        gm.pid_to_name = lambda pid: ("mystery.exe", None)
        try:
            hot = gm.select_hot_candidates(cfg, {100: 90.0}, {}, 50.0, 0,
                                           set(), set())
        finally:
            gm.pid_to_name = saved
        self.assertEqual(hot, [], "没有完整路径就写不了首选项, 不该进候选")

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

    def test_verification_runs_after_a_real_restart(self):
        """重启成功之后的验证闭环要能真的跑起来, 不能把监控循环掀翻。

        这条曾因 `for (pid2, luid, _ph) in usage.items()` 把 (键,值) 二元组
        按三元组解包, 在真机上一进这段就 ValueError —— 托盘直接显示"监控
        已停止"。夹具的 restart_process 默认返回 None, 迁移之后往往没有
        后续周期, 所以这个分支从来没被跑到过。
        """
        gm.restart_process = lambda pid, full: 4321
        # 重启后的新 pid 落在独显上: (核显%, 独显%) 二元组
        samples = [{100: 80.0}] * 3 + [({4321: 0.0}, {4321: 60.0})] * 3
        self.run_monitor(samples, cfg_overrides={"auto_restart": True})
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;")
        self.assertTrue(any("已自动重启" in m for m in self.log_lines))
        self.assertTrue(any("迁移验证" in m for m in self.log_lines),
                        f"重启之后的验证闭环没跑起来: {self.log_lines[-5:]}")

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
        # 6 个周期只该提醒一次: ps_notified 原来只在 auto
        # 切回核显成功后才登记, 纯提醒模式下谁都不记,
        # 于是每个采样周期都把同一句提醒重弹一遍。
        self.assertEqual([t for t, _m in self.notes], ["省电提醒"],
                         "提醒过一次就记账, 不该每轮都弹")

    def test_active_dgpu_process_not_reverted(self):
        self.prefs[DGPUAPP] = "GpuPreference=2;"
        self.run_monitor([({100: 0.0}, {101: 80.0})] * 6,
                         cfg_overrides=self._cfg(power_saver_auto=True))
        self.assertEqual(self.prefs.get(DGPUAPP), "GpuPreference=2;")

    def test_unmigrated_process_left_alone(self):
        self.run_monitor(self.IDLE * 6,
                         cfg_overrides=self._cfg(power_saver_auto=True))
        self.assertEqual(self.prefs, {}, "没迁移到独显的进程不该被写入设置")

    def test_power_saver_does_not_claim_switchback_that_never_landed(self):
        """省电切回也得回读: 不抵错 != 值落了地。

        和主迁移路径(§63)、命令行(§61)同一把尺子。
        报了"已切回核显省电"而注册表里还是 GpuPreference=2,
        用户会继续白跑在独显上。
        """
        self.prefs[DGPUAPP] = "GpuPreference=2;"
        saved_set = gm.set_gpu_preference
        gm.set_gpu_preference = lambda full, value="GpuPreference=2;": None
        try:
            self.run_monitor(self.IDLE * 6,
                             cfg_overrides=self._cfg(power_saver_auto=True))
        finally:
            gm.set_gpu_preference = saved_set
        self.assertEqual(self.prefs.get(DGPUAPP), "GpuPreference=2;",
                         "没落地就该还是独显首选项")
        titles = [t for t, _m in self.notes]
        self.assertIn("省电切换未生效", titles, titles)
        self.assertNotIn("已切回核显省电", titles,
                         "回读还在独显上, 不能报已切回")
        self.assertIn("  -> 回读确认失败: 注册表里仍是独显首选项",
                      self.log_lines, self.log_lines)

    def test_power_saver_retries_after_a_failed_switchback(self):
        """切换没落地时, 稍后再次符合条件时该再试一次。

        ps_notified 若在确认落地前就记上, 这一条会被当成
        "已提醒过"跳过, 永远回不到核显。
        """
        self.prefs[DGPUAPP] = "GpuPreference=2;"
        calls = []
        saved_set = gm.set_gpu_preference

        def flaky(full, value="GpuPreference=2;"):
            calls.append(value)
            if len(calls) == 1:
                return None            # 第一次写"成功"但不落地
            self.prefs[full] = value   # 第二次真写

        gm.set_gpu_preference = flaky
        try:
            self.run_monitor(self.IDLE * 12,
                             cfg_overrides=self._cfg(power_saver_auto=True))
        finally:
            gm.set_gpu_preference = saved_set
        self.assertEqual(self.prefs.get(DGPUAPP), "GpuPreference=1;")
        self.assertEqual(len(calls), 2, "未生效后应该重试到落地为止")
        self.assertIn("已切回核显省电", [t for t, _m in self.notes])

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

    def test_game_time_counted_once_per_name_per_cycle(self):
        """同名多进程(启动器+主程序)不能让日报的游戏时长翻倍。

        老代码在按 pid 遍历的循环里给 game_secs[pname] 加"每轮耗时":
        一个游戏开 N 个同名进程, 一轮就记 N 份, 日报里"游戏约 N 分钟"
        按进程数翻倍; 加的 elapsed_prev 又只是上一轮采样耗时, 不含间隔,
        墙上过了一分钟只记进去零点几秒。两个方向都错, 这里按墙上时钟跨度
        一次性记满。时钟打桩: 每次 time.time() 前进 15s, 一轮两次调用,
        每轮跨度即 30s。
        """
        gm.pid_to_name = {**PID_NAMES, 103: ("game.exe", GAME)}.get
        seen = []
        real_append, real_time = gm.append_history, gm.time.time
        clock = [1_000_000.0]

        def fake_time():
            clock[0] += 15.0
            return clock[0]

        def spy(now_ts, total, daily, game_secs, cfg):
            seen.append(dict(game_secs))
            return real_append(now_ts, total, daily, game_secs, cfg)

        gm.time.time = fake_time
        gm.append_history = spy
        try:
            self.run_monitor([{102: 5.0, 103: 5.0}] * 4,
                             cfg_overrides=self._cfg(history=True))
        finally:
            gm.time.time = real_time
            gm.append_history = real_append
        # 每轮循环里 time.time() 被调用 5 次(周期起点/取了两次 now_ts/
        # 末尾算 elapsed/迁移验证问一次当前时间), 打桩时钟每次 +15s,
        # 故每轮跨度 75s: 首轮 0.2s 兜底 + 3 轮 x 75s, 同名只记一份
        self.assertAlmostEqual(seen[-1].get("game.exe", 0.0), 225.2, places=1)

    def test_game_time_credit_floor_and_additivity(self):
        """记账直接单测: 每轮一份跨度, 极小跨度有 0.2s 下限。"""
        secs = defaultdict(float)
        gm.game_time_credit({"a.exe"}, 0.01, secs)   # 低于下限按 0.2s
        gm.game_time_credit({"a.exe"}, 30.0, secs)   # 正常累加
        gm.game_time_credit({"a.exe", "b.exe"}, 30.0, secs)
        self.assertAlmostEqual(secs["a.exe"], 60.2, places=6)
        self.assertAlmostEqual(secs["b.exe"], 30.0, places=6)


class NotifyGating(unittest.TestCase):
    """notify_cfg 的免打扰闸门: 游戏运行中 / 全屏前台 / 总开关, 一条不漏。

    游戏静音那条早有测试(GameMode); 但全屏免打扰那条此前没人守 ——
    is_foreground_fullscreen 在 LoopHarness 里被固定 stub 成 False, 真实分支
    一进就静默, 从来没用真值驱动过(全库对它的真实调用 0 次)。全屏打游戏/放
    视频时弹一条"迁移成功"很打扰; 反之有人显式关掉全屏免打扰时又得收得到。
    这里用打桩的 is_foreground_fullscreen + 偷看 gm.notify 把整张门控矩阵钉死。
    """

    def setUp(self):
        self.calls = []
        self.addCleanup(setattr, gm, "notify", gm.notify)
        self.addCleanup(setattr, gm, "is_foreground_fullscreen",
                        gm.is_foreground_fullscreen)
        self.addCleanup(setattr, gm, "_GAME_RUNNING", gm._GAME_RUNNING)
        gm.notify = lambda t, m: self.calls.append((t, m))
        gm.is_foreground_fullscreen = lambda: False
        gm._GAME_RUNNING = False

    def _cfg(self, **over):
        cfg = {"notify": True, "notify_fullscreen_ignore": True}
        cfg.update(over)
        return cfg

    def test_fullscreen_foreground_mutes_the_notification(self):
        gm.is_foreground_fullscreen = lambda: True
        gm.notify_cfg(self._cfg(), "t", "m")
        self.assertEqual(self.calls, [])

    def test_fullscreen_still_leaks_when_ignore_is_off(self):
        # 显式关掉全屏免打扰, 全屏下也该收得到
        gm.is_foreground_fullscreen = lambda: True
        gm.notify_cfg(self._cfg(notify_fullscreen_ignore=False), "t", "m")
        self.assertEqual(self.calls, [("t", "m")])

    def test_not_fullscreen_lets_it_through(self):
        gm.is_foreground_fullscreen = lambda: False
        gm.notify_cfg(self._cfg(), "t", "m")
        self.assertEqual(self.calls, [("t", "m")])

    def test_game_running_mutes_even_when_notify_is_on(self):
        gm._GAME_RUNNING = True
        gm.is_foreground_fullscreen = lambda: False
        gm.notify_cfg(self._cfg(), "t", "m")
        self.assertEqual(self.calls, [])

    def test_master_switch_off_mutes_everything(self):
        gm.is_foreground_fullscreen = lambda: False
        gm.notify_cfg(self._cfg(notify=False), "t", "m")
        self.assertEqual(self.calls, [])


class PowerSaverAdvance(LoopHarness):
    """直接调用 advance_power_saver: 每轮只推进一个进程的语义只有多进程才看得见,
    而循环夹具里同时只有一个独显进程。"""

    def cycle(self, dgpu_util, streak, notified, need=2, auto=True):
        cfg = dict(gm.DEFAULT_CONFIG)
        cfg.update({"power_saver_notify": True, "power_saver_samples": need,
                    "power_saver_idle_percent": 10.0})
        gm.advance_power_saver(cfg, dgpu_util, streak, notified,
                                auto, None)

    def test_only_one_process_advances_per_cycle(self):
        self.prefs[HEAVY] = "GpuPreference=2;"
        self.prefs[DGPUAPP] = "GpuPreference=2;"
        streak, notified = defaultdict(int), set()
        for _ in range(3):
            self.cycle({100: 2.0, 101: 3.0}, streak, notified)
        self.assertEqual(self.prefs[HEAVY], "GpuPreference=1;", "先推进 pid 100")
        self.assertEqual(self.prefs[DGPUAPP], "GpuPreference=2;",
                         "3 轮里 pid 101 只被推进 1 次, 不该跟着改设置")
        self.assertEqual(dict(streak), {101: 1},
                         "pid 100 已改回核显不再跟踪, 101 只数了一轮")
        self.assertEqual(notified, set(),
                         "切回核显后要清掉提醒记录, 以后重迁才能再提醒")

    def test_streak_resets_when_process_goes_active_again(self):
        streak, notified = defaultdict(int), set()
        self.prefs[HEAVY] = "GpuPreference=2;"
        self.cycle({100: 2.0}, streak, notified)
        self.assertEqual(streak[100], 1)
        self.cycle({100: 80.0}, streak, notified)
        self.assertEqual(streak[100], 0, "重新忙起来就该从零再数")

    def test_silent_process_absent_from_samples_still_counts(self):
        """挂在独显上闲着的进程不产生 GPU Engine 实例, 缺席必须算低负载。

        老实现是"不在 dgpu_util_by_pid 里就把 streak 清零", 于是真正占着
        独显又什么都不干的程序永远攒不满 power_saver_samples, 省电提醒对它
        永远不触发 —— 而它正是最该被提醒的那一个。
        """
        streak, notified = defaultdict(int), set()
        self.prefs[DGPUAPP] = "GpuPreference=2;"
        self.cycle({101: 2.0}, streak, notified, need=3)   # 还在动, 低负载
        self.cycle({}, streak, notified, need=3)           # 彻底安静: 没有实例了
        self.cycle({}, streak, notified, need=3)
        self.assertEqual(streak[101], 3, "没有 GPU 活动的轮次也要算低负载")
        self.assertEqual(self.prefs[DGPUAPP], "GpuPreference=1;")
        self.assertEqual(notified, {DGPUAPP}, "处理过一次就登记, 不会每轮重复")

    def test_notify_only_mode_registers_the_reminder(self):
        """只提醒不切回时, 也要把 exe 记进 ps_notified。

        ps_notified 原来只在 auto 切回核显确认落地后才 add;
        提醒分支什么都不记, 结果 streak 一直 >= need,
        下一轮还得到同一句提醒 —— 托盘上就是
        每个采样周期一来一来的 toast。
        """
        streak, notified = defaultdict(int), set()
        self.prefs[DGPUAPP] = "GpuPreference=2;"
        for _ in range(4):
            self.cycle({101: 2.0}, streak, notified, need=2, auto=False)
        self.assertEqual(len(self.notes), 1, f"只该提醒一次: {self.notes}")
        self.assertEqual(self.notes[0][0], "省电提醒")
        self.assertEqual(notified, {DGPUAPP},
                         "提醒完就得登记, 否则下一轮还会弹")
        self.assertEqual(streak[101], 2, "登记后不该再往上积")
        self.assertEqual(self.prefs[DGPUAPP], "GpuPreference=2;",
                         "不自动切回时不该改注册表")

    def test_the_reminder_rearms_after_the_pref_is_dropped(self):
        """只提醒也不是一锤定音: 用户 --unset 后重新迁,

        再度闲置时该再提醒一次。清理时机与 auto
        路径同一个地方: 首选项不再是高性能。
        """
        streak, notified = defaultdict(int), set()
        self.prefs[DGPUAPP] = "GpuPreference=2;"
        self.cycle({101: 2.0}, streak, notified, need=2, auto=False)
        self.cycle({101: 2.0}, streak, notified, need=2, auto=False)
        del self.prefs[DGPUAPP]          # 用户手动 unset
        self.cycle({101: 2.0}, streak, notified, need=2, auto=False)
        self.assertEqual(notified, set(),
                         "不再拥有高性能首选项时要清空记账")
        self.assertEqual(list(streak), [], "同时也不再跟踪")

    def test_dead_process_leaves_the_bookkeeping(self):
        streak, notified = defaultdict(int), set()
        self.prefs[HEAVY] = "GpuPreference=2;"
        self.cycle({100: 2.0}, streak, notified, need=5)
        self.assertEqual(streak[100], 1)
        saved = gm.pid_to_name
        gm.pid_to_name = {k: v for k, v in PID_NAMES.items()
                          if k != 100}.get      # pid 100 退出了
        try:
            self.cycle({}, streak, notified, need=5)
        finally:
            gm.pid_to_name = saved
        self.assertEqual(dict(streak), {}, "进程退出后计数作废, 不留下幽灵条目")

    def test_reverted_preference_leaves_the_bookkeeping(self):
        streak, notified = defaultdict(int), set()
        self.prefs[HEAVY] = "GpuPreference=2;"
        self.cycle({100: 2.0}, streak, notified, need=5)
        self.cycle({100: 2.0}, streak, notified, need=5)
        self.assertEqual(streak[100], 2)
        self.prefs[HEAVY] = "GpuPreference=1;"    # 用户自己改回了核显
        self.cycle({100: 2.0}, streak, notified, need=5)
        self.assertEqual(dict(streak), {},
                         "首选项已不是高性能, 省电逻辑不该再接管它")


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
        self.assertIn("峰值 77.7%", msg)
        self.assertIn("迁移 2 个程序", msg)
        self.assertIn("游戏约 2 分钟", msg)
        self.assertEqual(self.read("report.csv").strip(),
                         "2000-01-01,77.7,12:00,2,2")
        self.assertEqual(dict(secs), {}, "跨日要清游戏时长")

    def test_sub_minute_game_time_is_not_reported_as_zero(self):
        """玩了 20 秒不该报"游戏约 0 分钟"。

        那一句读起来就是"今天没玩游戏", 而事实是玩了半分钟。
        和趋势图角标那句"近1分钟按秒说"同一个道理: 数字得说人话。
        """
        for secs, present, absent in (
                (20.0, "不到 1 分钟", "约 0 分钟"),
                (30.0, "不到 1 分钟", "约 0 分钟"),
                (90.0, "约 2 分钟", "不到 1 分钟")):
            with self.subTest(secs=secs):
                self.notes.clear()
                daily = {"date": "2000-01-01", "peak": 77.7,
                         "peak_ts": "12:00", "migrated": ["a.exe"]}
                gm.append_history(1000.0, 10.0, daily,
                                  defaultdict(float, {"g.exe": secs}), {})
                _title, msg = self.notes[0]
                self.assertIn(present, msg, msg)
                self.assertNotIn(absent, msg,
                                 "%s 秒不该被说成那个意思" % secs)

    def test_an_empty_day_does_not_report_a_fake_zero_peak(self):
        """一整天没采到峰值时不能报 "核显峰值 0.0%()"。

        空括号加假的 0.0% 把"没测"说成了"测了是 0": 刚装上、
        全天休眠、监控没跑, 三种完全不同的日子长得一模一样。
        """
        daily = {"date": "2000-01-01", "peak": 0.0, "peak_ts": "",
                 "migrated": []}
        gm.append_history(1000.0, 0.0, daily, defaultdict(float), {})
        _title, msg = self.notes[0]
        self.assertIn("未采到核显占用数据", msg)
        self.assertNotIn("()", msg, msg)
        self.assertNotIn("0.0%", msg, msg)
        # CSV 还是机器口径: 空时间戳写空字段, 不往数据文件里塞汉字
        self.assertEqual(self.read("report.csv").strip(),
                         "2000-01-01,0.0,,0,0")

    def test_daily_report_csv_keeps_machine_readable_minutes(self):
        """通知文案改了, CSV 里的分钟数不能跟着变。

        report.csv 是给人回头分析的, 写"不到 1 分钟"这种汉字
        就没法求和了。
        """
        daily = {"date": "2000-01-01", "peak": 77.7, "peak_ts": "12:00",
                 "migrated": ["a.exe"]}
        gm.append_history(1000.0, 10.0, daily,
                          defaultdict(float, {"game.exe": 20.0}), {})
        self.assertEqual(self.read("report.csv").strip(),
                         "2000-01-01,77.7,12:00,1,0")
        self.assertIn("不到 1 分钟", self.notes[0][1])

    def test_a_report_csv_that_cannot_be_written_is_logged(self):
        """日报写不进 report.csv 时不能一声不响。

        通知说"2000-01-01: 核显峰值 77.7%, 迁移 2 个程序", 用户去翻
        report.csv 却没有那一行。老代码 except OSError: pass —— 既不
        知道 файл被占用/只读, 也不知道到底写没写。和 §45 修 Web 面板
         bind 失败同一个道理: 起不来可以, 起不来还装成成功了不行。
        """
        daily = {"date": "2000-01-01", "peak": 77.7, "peak_ts": "12:00",
                 "migrated": ["a.exe", "b.exe"]}
        secs = defaultdict(float, {"game.exe": 120.0})
        doomed = os.path.join(gm._DIR, "report.csv")
        with open(doomed, "w", encoding="utf-8") as f:
            f.write("2026-09-01,1.0,09:00,0,0\n")
        os.chmod(doomed, 0o444)          # 只读: 追加必失败
        try:
            gm.append_history(1000.0, 10.0, daily, secs, {})
        finally:
            os.chmod(doomed, 0o644)
        after = self.read("report.csv")
        self.assertNotIn("2000-01-01", after or "", "只读文件不该被写进去")
        self.assertTrue(
            any("report.csv" in l for l in self.log_lines),
            f"日报落盘失败却零日志: {self.log_lines}")
        self.assertTrue(
            any("日报" in l for l in self.log_lines),
            f"日志要点明是日报这一路: {self.log_lines}")
        # 通知已经发了, 所以日志必须说清"通知发了但没落盘"
        self.assertEqual(len(self.notes), 1, "通知照发, 只是没落盘")

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

    def test_corrupt_config_on_reload_neither_crashes_nor_resets(self):
        """半截 config.json 既不能弄死循环, 也不能把运行中的策略悄悄换成默认值。

        实测过的原始故障: 热重载外只有 except OSError, 而 JSONDecodeError 是
        ValueError 的子类 —— 配置文件一坏, 整个监控循环当场崩掉。
        sustain_samples 故意只在文件里给 3: 退回默认值的话是 5, 4 轮跑不满,
        于是不崩溃但悄悄改策略的回归同样会被这条断言抓住。
        """
        path = os.path.join(gm._DIR, "__reload_corrupt__.json")
        write_test_config(path, sustain_samples=3)
        broken = []

        def hook(_snap):
            if not broken:
                broken.append(1)
                later = int((os.path.getmtime(path) + 0.1) * 1e9)
                with open(path, "w", encoding="utf-8") as f:
                    f.write('{"threshold_percent": 50.0, "sustain_sam')
                os.utime(path, ns=(later, later))

        try:
            self.run_monitor([{100: 80.0}] * 4, config_path=path,
                             hooks={"on_sample": hook})
        finally:
            os.remove(path)
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;",
                         "第 3 轮就该迁移; 没迁移说明配置被换回默认值了")
        self.assertIn("配置无法解析，沿用当前配置", self.log_lines)

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

    def test_reload_log_shows_the_effective_values(self):
        """热重载那行要带上生效值: 选了模式后日志得能证明参数真的换了。

        只写"已热重载"时, 菜单上的"模式: 省电"是直接从 config.json 读的,
        监控有没有吃进去无从判断 —— 重载失败时界面照样显示新模式。
        """
        path = os.path.join(gm._DIR, "__reload_mode__.json")
        write_test_config(path, threshold_percent=20.0)
        self.run_monitor([{100: 5.0}] * 4, config_path=path,
                         cfg_overrides={"threshold_percent": 20.0,
                                        "sustain_samples": 1},
                         hooks={"on_sample": self.rewrite_once(
                             path, threshold_percent=40.0, mode="saver",
                             power_saver_auto=True)})
        line = [l for l in self.log_lines if "已热重载" in l][-1]
        self.assertIn("40", line, "阈值要从日志里读得出来")
        self.assertIn("省电", line, "模式名也要在")


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

    def test_snapshot_reports_the_battery_threshold(self):
        """电池覆盖之后, 快照里的阈值必须是电池值。

        界面若去读 config.json, 拔着电源就会显示交流阈值 —— 那恰好是用户
        想确认"现在到底按哪个数在判"的时候。
        """
        gm.on_battery = lambda: True
        self.run_monitor([{100: 5.0}] * 2,
                         cfg_overrides={"power_aware": True,
                                        "threshold_percent": 20.0,
                                        "battery_threshold_percent": 55.0,
                                        "sustain_samples": 1})
        self.assertEqual(gm._LAST_SNAPSHOT["threshold"], 55.0)
        self.assertTrue(gm._LAST_SNAPSHOT["on_battery"])


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
             "dgpu_luids", "kind_cache", "pid_names", "pid_prefs",
             "dgpu_temp", "history", "snap_ts", "stale_after",
             "mode", "threshold", "power_saver_auto", "on_battery",
             "rounds", "hits", "migrations"})
        self.assertEqual(seen["util_by_pid"], {100: 80.0})
        self.assertEqual(seen["gpu_all_by_pid"], {100: 80.0})
        self.assertIn(IGPU_LUID, seen["igpu_luids"])
        self.assertIn(DGPU_LUID, seen["dgpu_luids"])

    def test_the_trend_is_fed_even_when_the_jsonl_is_switched_off(self):
        """关掉 history.jsonl 只是不落盘, 界面上的趋势图不该跟着消失。

        老代码把 "记一个点" 整段塞在 cfg["history"] 里,
        于是关掉落盘的人面板上永远只有那句
        "每 10 秒一点"。
        """
        seen = []
        self.run_monitor([{100: 42.0}], cycles=1,
                         cfg_overrides={"history": False},
                         hooks={"on_sample":
                                lambda s: seen.append(s.get("history"))})
        self.assertEqual(len(seen), 1)
        self.assertEqual([v for _t, v in seen[0]], [42.0])

    def test_snapshot_carries_the_effective_policy(self):
        """面板和 tooltip 用的模式/阈值必须来自循环里的生效值。

        _LAST_SNAPSHOT 原先只装逐进程指标, 界面拿不到"现在按什么在判",
        只能回头读 config.json —— 那等于只能证明"我选了", 证明不了"生效了"。
        """
        self.run_monitor([{100: 5.0}], cycles=1,
                         cfg_overrides={"mode": "saver",
                                        "threshold_percent": 40.0,
                                        "power_saver_auto": True})
        snap = gm._LAST_SNAPSHOT
        self.assertEqual(snap["mode"], "saver")
        self.assertEqual(snap["threshold"], 40.0)
        self.assertTrue(snap["power_saver_auto"])

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

    def test_published_kind_cache_is_a_private_copy(self):
        """发布给别的线程的 dict 必须是副本 —— web 线程会拿着它一直遍历。

        真实现场: 睡眠唤醒/驱动重置后冒出一个新 LUID, 监控线程往 kind_cache
        里插; 同一刻面板刷新, 用 {luid: v[1] for ... in kind_cache.items()}
        遍历那个活 dict, 当场 RuntimeError: dictionary changed size during
        iteration, 浏览器拿到空白页。实测过这个表达式确实会炸。
        """
        extra = "00000000_00009999"
        held = []
        seen = {"i": 0}

        def collector(allowed_luids=None, normalize=False, window=None):
            seen["i"] += 1
            index = {(IGPU_LUID, 0): None, (DGPU_LUID, 0): None}
            if seen["i"] >= 3:            # 第 3 轮才冒出第二块新显卡
                index[(extra, 0)] = None
            return {(100, IGPU_LUID, 0): 5.0}, {}, {}, index

        def on_sample(_snap):
            held.append(gm._LAST_SNAPSHOT["kind_cache"])

        gm.collect_gpu_sample = collector
        gm.luid_to_name = {IGPU_LUID: IGPU_NAME, DGPU_LUID: DGPU_NAME,
                           extra: "Virtual Display Adapter"}.get
        cfg = dict(gm.DEFAULT_CONFIG)
        cfg.update(TEST_CFG_BASE)
        cfg.update({"threshold_percent": 50.0, "sustain_samples": 99,
                    "interval_seconds": 0.001})
        done = {"n": 0}

        def before_cycle():
            done["n"] += 1
            return done["n"] <= 5
        try:
            gm.cmd_monitor(cfg, None,
                           hooks={"before_cycle": before_cycle,
                                  "on_sample": on_sample})
        finally:
            gm.luid_to_name = self.saved["luid_to_name"]
        self.assertGreater(done["n"], 3, "没跑到插新 LUID 的那一轮, 测试无效")
        first = held[0]
        self.assertNotIn(extra, first,
                         "第 1 轮发布出来的 dict 被后面的新显卡插进了条目: "
                         "发布的还是循环在用的那个活对象")
        self.assertEqual(len({id(h) for h in held}), len(held),
                         "每轮发布的都是同一个 dict 对象")

    def test_dgpu_temp_survives_the_snapshot_rebuild(self):
        """温度写进的是"上一轮"的快照, 本轮末尾整字典重建会把它抹掉。

        面板上那条 独显温度 因此永远不出现 —— 看着像 NVML 坏了, 其实是被
        自己的重建覆盖。
        """
        gm.nvml_gpu_temp = lambda index=0: 61.0
        self.run_monitor([{100: 5.0}], cycles=1,
                         cfg_overrides={"nvml_temp": True})
        self.assertEqual(gm._LAST_SNAPSHOT.get("dgpu_temp"), 61.0)


class DgpuTempOutage(LoopHarness):
    """NVML 一次读不到温度时, 上一读数怎么办。

    本机 nvmlDeviceGetTemperature 有过访问冲突现场(见 install_fault_dump),
    单次失败是真会发生的事。原代码 `dgpu_temp = t` 无条件赋值: 抖一下就把
    上一好读数冲成 None, 面板/托盘那条温度行整行消失, 下轮才回来 —— 看着像
    NVML 彻底坏了, 而日志里一个字都没有。9e9f604 修的是“被快照重建抹掉”,
    这条是“被失败读数抹掉”: 同一个症状, 两个来源。

    钉四件事: 失败保住上一读数、失败与恢复各写一行(不是每 10s 刷一行)、
    长期失败到期把冻住的读数收回(一直显示一个不再更新的数字是页面在说谎,
    见 panel_html 里“HTML 自己不能说着谎”那条)、从没读到过温度的机器不报
    故障(没独显/没驱动是常态, 不该每次启动都留一行告警)。
    """

    def _clock(self, step=3.0):
        """每轮 time.time() 被调 5 次, 每次 +step → 每轮跨 15s。

        15s 刚好越过温度块那道 `now_ts - temp_check[0] > 10` 的闸门, 于是
        每轮都真去读一次 NVML; 又比 60s 的宽限小得多, 才能在几轮里同时看到
        “宽限期内保住”和“到期收回”两种行为。
        """
        clock = [1_000_000.0]

        def fake_time():
            clock[0] += step
            return clock[0]

        self.addCleanup(setattr, gm.time, "time", gm.time.time)
        gm.time.time = fake_time

    def _temps(self, readings):
        """按轮次发牌, 列表耗尽后重复最后一个值。"""
        state = {"i": 0}

        def fake_nvml(index=0):
            v = readings[min(state["i"], len(readings) - 1)]
            state["i"] += 1
            return v

        gm.nvml_gpu_temp = fake_nvml

    def _collect(self, cycles):
        """跑 cycles 轮, 回收每轮发布到快照里的 dgpu_temp。"""
        seen = []
        self._clock()
        self.run_monitor([{100: 5.0}] * cycles, cycles=cycles,
                         cfg_overrides={"nvml_temp": True},
                         hooks={"on_sample": lambda _s: seen.append(
                             gm._LAST_SNAPSHOT.get("dgpu_temp"))})
        return seen

    def _lines(self, needle):
        return [l for l in self.log_lines if needle in l]

    def test_a_single_failed_read_keeps_the_last_good_temperature(self):
        self._temps([61.0, None])
        seen = self._collect(3)
        self.assertEqual(seen[0], 61.0)
        self.assertEqual(seen[1], 61.0,
                         "一次抖动就把上一读数冲成 None: 面板温度行会闪断")
        self.assertEqual(gm._LAST_SNAPSHOT.get("dgpu_temp"), 61.0)

    def test_the_outage_is_logged_once_not_every_ten_seconds(self):
        self._temps([61.0, None])
        self._collect(6)
        hits = self._lines("读取失败")
        self.assertEqual(len(hits), 1,
                         f"要么完全静默, 要么每轮刷一行: {hits}")
        self.assertIn("61", hits[0], "该行要带上被暂留的读数, 否则无从对照")

    def test_a_machine_that_never_had_nvml_is_not_an_outage(self):
        self._temps([None])
        seen = self._collect(3)
        self.assertEqual(seen, [None, None, None])
        self.assertEqual(self._lines("读取失败"), [],
                         "从没读到过温度的机器(无独显/无驱动)不该报故障")

    def test_a_long_outage_takes_the_frozen_reading_back(self):
        self._temps([61.0, None])
        seen = self._collect(8)
        self.assertEqual(seen[0], 61.0)
        self.assertEqual(seen[1], 61.0, "宽限期内要保住上一读数")
        self.assertIsNone(seen[-1],
                          "长期读不到还冻着一个数字, 就是页面在说谎")
        self.assertEqual(len(self._lines("已收回暂留")), 1, self.log_lines)

    def test_the_reading_comes_back_and_says_so(self):
        self._temps([61.0, None, 58.0])
        seen = self._collect(4)
        self.assertEqual(seen[-1], 58.0)
        self.assertEqual(len(self._lines("读取恢复")), 1, self.log_lines)


class ListCommand(unittest.TestCase):
    """--list 连着采两次样, 两次之间冒出来的新 LUID 不能把它打死。"""

    NEW_LUID = "00000000_00009BBB"

    def setUp(self):
        self.saved = (gm.collect_gpu_sample, gm.luid_to_name,
                      gm.pid_to_name)

    def tearDown(self):
        (gm.collect_gpu_sample, gm.luid_to_name,
         gm.pid_to_name) = self.saved

    def _run(self, extra=None):
        seen = {"i": 0}

        def fake_collect(allowed_luids=None, normalize=False,
                         window=None):
            seen["i"] += 1
            index = {(IGPU_LUID, 0): None, (DGPU_LUID, 0): None}
            usage = {(100, IGPU_LUID, 0): 40.0}
            if seen["i"] == 2 and extra:   # 第二次采样时才冒出来
                index[(extra, 0)] = None
                usage[(100, extra, 0)] = 12.5
            return usage, {}, {}, index

        gm.collect_gpu_sample = fake_collect
        gm.luid_to_name = {IGPU_LUID: IGPU_NAME, DGPU_LUID: DGPU_NAME,
                           self.NEW_LUID: DGPU_NAME}.get
        gm.pid_to_name = PID_NAMES.get
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            gm.cmd_list()
        return out.getvalue()

    def test_the_kind_tag_lands_on_the_same_column(self):
        """%-42s 只给最小宽度, 不截断; 又按字符数补空格。

        名字一长, [核显]/[独显] 就被推到不知道哪儿去; 中文名字则
        相反会短一截。几张卡并排看的时候类型标签不在同一列, 扫不动。
        """
        long_name = ("Intel(R) Iris(R) Xe Graphics "
                     "(Alder Lake-P, extra)")
        cjk_name = "中文显卡名字很长的显卡名字"
        cards = [
            gm._gpu_card_line("00000000_00000001",
                              {"name": "short", "kind": "igpu"}),
            gm._gpu_card_line("00000000_00000002",
                              {"name": long_name, "kind": "dgpu"}),
            gm._gpu_card_line("00000000_00000003",
                              {"name": cjk_name, "kind": "dgpu"}),
        ]
        # 前缀 22 列 + 名字列封顶 + 分隔空格
        want = 22 + gm._GPU_NAME_COL_MAX + 1
        for line in cards:
            at = line.index("[", 3)
            self.assertEqual(gm._disp_len(line[:at]), want,
                             f"类型标签没落在同一列: {line!r}")
        # 截断得真的发生过, 否则这条测试没在测什么
        for line in (cards[1], cards[2]):
            self.assertIn("...", line,
                          f"这个名字该被截断: {line!r}")
        name0 = cards[0][22:cards[0].index("[", 3)].rstrip()
        self.assertNotIn("...", name0,
                         f"短名字不该被截断: {name0!r}")

    def test_an_adapter_that_appears_between_samples_does_not_crash(self):
        """真机现场: 浏览器/游戏在两次采样之间起进程。

        老代码第二次采样后直接 gpus[luid] 下标, 新 LUID
        不在第一次的 gpus 里 -> KeyError -> --list 崩溃,
        而这正是用户"看看谁在占显卡"时最常用的入口。
        """
        text = self._run(extra=self.NEW_LUID)
        self.assertIn("heavy.exe", text)
        self.assertIn("(采样期间新出现)", text,
                      "占用表里出现的适配器, 列表里也得说一声")

    def test_a_stable_machine_does_not_invent_an_adapter(self):
        text = self._run()
        self.assertIn("heavy.exe", text)
        self.assertNotIn("(采样期间新出现)", text)


def col_edges(line):
    """每列右沿的累计显示宽度。

    顺序扫描, 不能用 line.index(t) 从头找 —— 同一行里 "40.0%" /
    "0" 这类片段会出现多次。名字列跳过: 进程名里带空格的话,
    按空格切分会把整行错开一位。
    """
    edges, pos = [], 0
    for token in line.split():
        i = line.index(token, pos)
        pos = i + len(token)
        edges.append(gm._disp_len(line[:pos]))
    return edges


class ListTableLayout(unittest.TestCase):
    """--list 是对着看的: 列必须对齐, 长进程名不能把表顶散。

    两个坑都是 printf 的: %Ns 按字符数补空格, 而中文表头是双倍宽;
    %-28s 不截断, UWP 那种四五十字符的进程名会把后面所有列一起推走。
    """

    LONG = ("WindowsInternal.Composable.Shell.Experiences"
            ".TextInput.InputApp.exe")

    def setUp(self):
        self.saved = (gm.collect_gpu_sample, gm.pid_to_name,
                      gm.discover_gpus)
        gm.collect_gpu_sample = self._collect
        gm.pid_to_name = self.NAMES.get
        gm.discover_gpus = lambda idx: {
            "L0": {"phys": 0, "name": "Test GPU", "kind": "igpu"}}
        self.out = io.StringIO()

    def tearDown(self):
        (gm.collect_gpu_sample, gm.pid_to_name,
         gm.discover_gpus) = self.saved

    NAMES = {1: ("short.exe", r"D:\a.exe"),
             2: (LONG_DESC := "x" * 40 + ".exe", r"D:\b.exe"),
             3: ("mid.exe", r"D:\c.exe")}

    def _collect(self, allowed_luids=None, normalize=False,
                 window=None):
        # 与 collect_gpu_sample 的真实返回同构: usage 的键是 (pid, luid, phys)
        return ({(1, "L0", 0): 40.0, (2, "L0", 0): 5.0, (3, "L0", 0): 12.5},
                {(1, "L0", 0): 1024 * 1024}, {}, {("L0", 0): None})

    def _run(self):
        with contextlib.redirect_stdout(self.out):
            gm.cmd_list()
        return self.out.getvalue()

    def test_no_name_blows_the_columns_apart(self):
        """截断加省略号: 列宽封顶, 再长的名字也只能吃掉自己的列。"""
        self.assertGreaterEqual(len(self.LONG), 40)
        text = self._run()
        self.assertIn("...", text,
                      "超宽进程名要截断加省略号")
        # 名字列占的显示宽度就是列宽上限 (尾部已补齐),
        # 不管名字原来多长, 后面的数字列都不会被顶走。
        for line in text.splitlines():
            if ".exe" not in line:
                continue
            pad = len(line) - len(line.lstrip())
            name_col = line[pad:].split(" ")[0]
            self.assertLessEqual(gm._disp_len(name_col),
                                 gm._NAME_COL_MAX,
                                 f"进程名未被截断: {name_col!r}")

    def test_a_cjk_process_name_stays_inside_its_column(self):
        """中文进程名的 len() 只有显示宽度的一半。

        列宽要是按字符数取, 名字列就会比表头宽一截(中文占两列),
        后面每一列都被顶斜。先造假一条中文名, 再看列沿还对不对。
        """
        import gpu_tray  # noqa: F401  只为确认托盘侧共用同一个判据
        saved = gm.pid_to_name
        gm.pid_to_name = lambda pid: (
            ("腾讯QQ.exe", "D:\\qq.exe") if pid == 1
            else ("heavy.exe", "D:\\h.exe"))
        try:
            text = self._run()
        finally:
            gm.pid_to_name = saved
        head = next(l for l in text.splitlines() if "PID" in l)
        row = next(l for l in text.splitlines()
                   if "腾讯QQ" in l)
        h, r = col_edges(head), col_edges(row)
        self.assertEqual(len(h), len(r), f"{h} vs {r}")
        for k in range(2, len(h)):
            self.assertEqual(h[k], r[k],
                             f"第 {k} 列右沿不对齐: "
                             f"表头 {h[k]} vs 数据 {r[k]}")

    def test_the_header_lands_on_the_same_column_as_the_numbers(self):
        """中文表头按显示宽度对齐, 不能比数字列宽。

        printf 的 %Ns 按字符数补空格, 而 "核显%"/"专用MiB" 是全角(显示宽度是字符数的两倍),
        结果表头每一列都比对应的数字列宽出一截, 整张表的列线是斜的。
        """
        text = self._run()
        lines = text.splitlines()
        head = next(l for l in lines if "PID" in l)
        row = next(l for l in lines if "short.exe" in l)


        h, r = col_edges(head), col_edges(row)
        self.assertEqual(len(h), len(r),
                         f"列数不一致: {h} vs {r}")
        # 前两列是进程名和 pid
        for k in range(2, len(h)):
            self.assertEqual(
                h[k], r[k],
                f"第 {k} 列右沿不对齐: "
                f"表头 {h[k]} vs 数据 {r[k]}")


class DisplayWidthCombining(unittest.TestCase):
    """组合用字符与零宽字符不占列, 但它们仍然是字符。

    "cafe\u0301.exe"(cafe 加组合钩子)比预组合的 "caf\u00e9.exe"
    多一个字符, 显示宽度却一样。_disp_len 要是按字符数把钩子、
    零宽空格( U+200B )、 ZWJ(U+200D)也各算一列, 名字列就会
    比显示宽度少补一个空格, 后面每一列被顶斜一格。

    反方向同样要锁住: 全角中日韩字符仍算两列, 纯 ASCII 仍算
    一列, 别为了修钩子把主口径改坏了。
    """

    def test_a_combining_accent_costs_no_column(self):
        self.assertEqual(gm._disp_len("e\u0301"), 1)
        # caf + e带钩子(=1列) + .exe: 比预组合写法多一个字符, 宽度不变
        self.assertEqual(gm._disp_len("cafe\u0301.exe"), 8)
        self.assertEqual(gm._disp_len("caf\u00e9.exe"), 8)

    def test_a_zero_width_space_costs_no_column(self):
        self.assertEqual(gm._disp_len("a\u200bb"), 2)

    def test_plain_cjk_is_still_two_columns(self):
        self.assertEqual(gm._disp_len("专用MiB"), 7)

    def test_plain_ascii_is_still_one_column_each(self):
        self.assertEqual(gm._disp_len("heavy.exe"), 9)

    def test_a_combining_mark_does_not_steal_a_column_of_padding(self):
        """补齐数按显示宽度算, 组合写法与预组合写法必须一致。"""
        pre = gm._fit_disp("caf\u00e9.exe", 20)
        comb = gm._fit_disp("cafe\u0301.exe", 20)
        pads = lambda s: len(s) - len(s.rstrip(" "))
        self.assertEqual(pads(comb), 20 - 8,
                         f"组合写法少补了空格: {comb!r}")
        self.assertEqual(pads(pre), pads(comb))

    def test_a_truncated_combining_name_fits_the_asked_width(self):
        out = gm._ellipsize_disp("e\u0301" * 30, 10)
        self.assertEqual(out, "e\u0301" * 7 + "...",
                         f"钩子不占宽度, 10 列该塞得下 7 个: {out!r}")
        self.assertEqual(gm._disp_len(out), 10)

    def test_truncation_never_orphans_a_combining_mark(self):
        """钩子留在基字符后面才有意义, 单独丢在前面只会画出错字。"""
        out = gm._ellipsize_disp("e\u0301" * 30, 10)
        body = out[:-3]
        for i, ch in enumerate(body):
            if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
                self.assertGreater(i, 0, f"行首出现孤立标记: {body!r}")


class WebPanelStartup(unittest.TestCase):
    """端口起不来时不能照样播报一个打不开的 URL。"""

    def setUp(self):
        self.lines = []
        self.saved_log = gm.log
        gm.log = lambda msg, logfile=None: self.lines.append(msg)

    def tearDown(self):
        gm.log = self.saved_log

    def _occupied_port(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        self.addCleanup(s.close)
        return s.getsockname()[1]

    def test_bind_failure_is_logged_instead_of_a_phantom_url(self):
        port = self._occupied_port()
        gm.web_thread(port, lambda: {}, lambda: {})   # 必须返回, 不许挂住
        self.assertTrue(any("Web 面板启动失败" in l for l in self.lines),
                        f"端口 {port} 被占却零日志: {self.lines}")
        self.assertFalse(any("http://127.0.0.1" in l for l in self.lines),
                         "起不来还播报 URL")

    def _roundtrip(self, port, request):
        """发一段原始 HTTP 请求, 回 (响应行, 头dict, body bytes)。"""
        deadline = time.time() + 5
        last = None
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), 0.3) as c:
                    c.sendall(request)
                    buf = b""
                    while True:
                        chunk = c.recv(65536)
                        if not chunk:
                            break
                        buf += chunk
                break
            except OSError as e:
                last = e
                time.sleep(0.05)
        else:
            self.fail("web_thread 没能在限时内起服务: %r" % (last,))
        head, _, body = buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        hdrs = {}
        for line in lines[1:]:
            k, _, v = line.partition(":")
            hdrs[k.strip().lower()] = v.strip()
        return lines[0], hdrs, body

    def _serve(self, snap):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        t = threading.Thread(target=gm.web_thread,
                             args=(port, lambda: snap, lambda: {}),
                             daemon=True)
        t.start()
        self.addCleanup(t.join, 0.1)
        return port

    def test_a_head_probe_gets_the_same_headers_without_a_body(self):
        """HEAD 是标准只读方法: 健康检查/curl -I/预取都在用。

        RFC 7231 4.3.2: HEAD 的响应头必须和 GET 一致, 只是不回 body。
        原来没实现 do_HEAD, BaseHTTPRequestHandler 直接回 501 —— 面板明明
        活着, 监控探针却以为它挂了, 于是"看起来正常的程序"在巡检里是红的。
        """
        port = self._serve({"util_by_pid": {100: 42.0},
                            "igpu_luids": set(), "dgpu_luids": set(),
                            "mode": "saver"})
        status, hdrs, body = self._roundtrip(port, b"HEAD / HTTP/1.0\r\n\r\n")
        self.assertEqual(status.split(" ")[1], "200", status)
        self.assertEqual(body, b"", "HEAD 不许回 body")
        self.assertIn("no-store", hdrs.get("cache-control", "").lower(),
                      "HEAD 也得带 no-store, 否则探针那头可能拿到缓存的旧结论")
        self.assertEqual(hdrs.get("pragma", "").lower(), "no-cache")
        self.assertIn("content-type", hdrs, "HEAD 得报类型, 不然调用方无从判断")

        gstatus, ghdrs, gbody = self._roundtrip(
            port, b"GET / HTTP/1.0\r\n\r\n")
        self.assertEqual(gstatus.split(" ")[1], "200", gstatus)
        self.assertEqual(hdrs.get("content-length"),
                         str(len(gbody)),
                         "HEAD 报的长度必须和 GET 实际发的字节数一致")
        for k in ("content-type", "cache-control", "pragma"):
            self.assertEqual(hdrs.get(k), ghdrs.get(k),
                             "HEAD 与 GET 的 %s 必须一致" % k)

    def test_bind_success_logs_the_url_and_serves_the_panel(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()                       # 让出端口, 交给 web_thread 去 bind
        snap = {"util_by_pid": {100: 42.0}, "gpu_all_by_pid": {100: 42.0},
                "dgpu_util_by_pid": {}, "shared_by_pid": {},
                "dedicated_by_pid": {100: 1024}, "igpu_luids": {IGPU_LUID},
                "dgpu_luids": {DGPU_LUID}, "kind_cache": {
                    IGPU_LUID: ("igpu", IGPU_NAME),
                    DGPU_LUID: ("dgpu", DGPU_NAME)},
                "mode": "saver", "threshold": 40.0, "power_saver_auto": True,
                "on_battery": False}
        t = threading.Thread(target=gm.web_thread,
                             args=(port, lambda: snap,
                                   lambda: {IGPU_LUID: IGPU_NAME}),
                             daemon=True)
        t.start()
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), 0.3) as c:
                    c.sendall(b"GET / HTTP/1.0\r\n\r\n")
                    body = b""
                    while True:
                        chunk = c.recv(65536)
                        if not chunk:
                            break
                        body += chunk
                break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("web_thread 没能在限时内起服务")
        text = body.decode("utf-8", "replace")
        self.assertIn("42", text, "面板该显示采到的占用")
        head = body.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        self.assertIn("Cache-Control", head,
                      "每秒轮询的活端点不带缓存头: 浏览器或中间代理会拿旧占用糊弄人")
        self.assertIn("no-store", head.lower(),
                      "Cache-Control 得有 no-store, 光写 max-age=0 不够")
        self.assertTrue(any("http://127.0.0.1" in l for l in self.lines),
                        f"绑定成功却没播报 URL: {self.lines}")
        t.join(0.1)



class TrayDpiAwareness(unittest.TestCase):
    """缩放显示下托盘界面必须是清晰的, 不能是系统替我们拉伸的糊图。

    打包用的默认 manifest 不带 dpiAware(已在产物里逐字节确认没这几个字),
    于是整进程 DPI-unaware: Windows 按 100% 渲染再拉伸到 150%/200%,
    面板、详情窗、托盘图标全是糊的, 固定 1040x470 的窗口在高分屏上还会
    被切掉一截。没有 manifest 也要在进程内声明 —— 但必须赶在建第一个
    窗口之前, 晚了 Windows 直接拒绝。
    """

    def test_run_tray_declares_dpi_before_building_any_window(self):
        import gpu_tray
        order = []
        saved_run, saved_app = gpu_tray.run_tray, gpu_tray.TrayApp
        saved_dpi = gpu_tray.enable_dpi_awareness
        self.addCleanup(setattr, gpu_tray, "run_tray", saved_run)
        self.addCleanup(setattr, gpu_tray, "TrayApp", saved_app)
        # 下面把 enable_dpi_awareness 换成返回 None 的替身(append 的返回值),
        # 不还原的话倒序跑整套时 test_enable_dpi_awareness_survives_a_missing_api
        # 会拿到 None 而不是 False —— 见本类末尾那条守卫用例。
        self.addCleanup(setattr, gpu_tray, "enable_dpi_awareness", saved_dpi)

        class FakeApp:
            def __init__(self, *a):
                order.append("app")

            def run(self):
                order.append("run")

        gpu_tray.TrayApp = FakeApp
        gpu_tray.enable_dpi_awareness = lambda: order.append("dpi")
        gpu_tray.run_tray("config.json")
        self.assertEqual(order[0], "dpi",
                         f"DPI 感知必须赶在建窗口之前: {order}")
        self.assertEqual(order[1:], ["app", "run"], order)

    def test_enable_dpi_awareness_survives_a_missing_api(self):
        """老系统连 shcore/user32 都取不到时也不能把托盘带崩。

        这里把 ctypes 整个换掉, 不是真去调 Windows: 不然一条测试就把
        整个测试进程的 DPI 感知状态改了, 后面谁还指望默认值就说不清。
        """
        import gpu_tray

        class BrokenCtypes:
            @property
            def windll(self):
                raise AttributeError("这个系统没有 windll")

        saved = (gpu_tray.os_name, gpu_tray.ctypes)
        self.addCleanup(lambda: (setattr(gpu_tray, "os_name", saved[0]),
                                 setattr(gpu_tray, "ctypes", saved[1])))
        gpu_tray.os_name = "nt"          # 走 Windows 分支
        gpu_tray.ctypes = BrokenCtypes()
        try:
            self.assertIs(gpu_tray.enable_dpi_awareness(), False,
                          "拿不到 API 就该安静说'没设上', 不是抛异常")
        except Exception as exc:          # noqa: BLE001 - 就是要它不抛
            self.fail(f"调用点没防异常: {exc!r}")

    def test_the_dpi_stub_does_not_outlive_the_case_that_installed_it(self):
        """上一条用例把 gpu_tray.enable_dpi_awareness 换成替身, 必须还原。

        漏了还原在正序下看不出来(本类里 test_enable… 排在 test_run… 前面),
        倒序跑整套才红: 替身是 `lambda: order.append("dpi")`, 返回 None, 而
        test_enable_dpi_awareness_survives_a_missing_api 断言返回 False。
        与其指望有人记得倒着再跑一遍, 不如在这里把那条用例单独跑一次、再查
        模块属性 —— 与执行顺序无关; 而且断言的是"这个属性属于 gpu_tray 自己",
        所以谁把替身留下都会被逮到, 不限于本类。
        """
        import gpu_tray
        case = TrayDpiAwareness(
            "test_run_tray_declares_dpi_before_building_any_window")
        outcome = case.run()
        self.assertTrue(outcome.wasSuccessful(),
                        f"嵌套跑的那条自己就红了: {outcome.failures} "
                        f"{outcome.errors}")
        self.assertEqual(gpu_tray.enable_dpi_awareness.__module__, "gpu_tray",
                         "enable_dpi_awareness 被替身留下没还原: 之后任何"
                         "直接调它的用例都只会拿到替身的返回值")


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

    def test_status_says_so_when_the_registry_cannot_be_read(self):
        """打不开注册表时不能反过来说"当前没有任何 GPU 首选项设置"。

        老代码 list_gpu_prefs 打不开键就回 [], cmd_status 把这个空列表
        当成"没设置过" —— 权限被策略挡掉时, 用户看到的是"啥也没有",
        于是以为自己从没迁移过, 而其实只是没读到。
        """
        saved = gm.list_gpu_prefs
        gm.list_gpu_prefs = lambda: None     # 读不到, 和"空的"不是一回事
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gm.cmd_status()
        finally:
            gm.list_gpu_prefs = saved
        out = buf.getvalue()
        self.assertIn("读不到", out, out)
        self.assertNotIn("当前没有任何 GPU 首选项设置", out,
                         "读不到不能说成没有")

    def test_unset_all_reports_what_it_could_not_clear(self):
        """清不掉的那几项必须点名, 不能只报一个偏小的成功数。

        老代码 print(f"已清除 {len(cleared)} 项。"): 列了 5 项、删掉 2 项,
        用户看到"已清除 2 项"会以为另外 3 项本来就不存在 —— 而那 3 项
        可能正被权限或占用挡着, 没人告诉他是哪几个。
        """
        prefs = {self.BARE: "GpuPreference=2;", self.FULL: "GpuPreference=2;",
                 self.OTHER: "GpuPreference=2;"}
        deleted = []
        saved_list, saved_del = gm.list_gpu_prefs, gm.clear_gpu_preference
        gm.list_gpu_prefs = lambda: sorted(prefs.items())
        gm.clear_gpu_preference = (
            lambda exe: exe != self.OTHER
            and deleted.append(exe) is None)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gm.cmd_unset_all(assume_yes=True)
        finally:
            gm.list_gpu_prefs, gm.clear_gpu_preference = saved_list, saved_del
        out = buf.getvalue()
        self.assertIn("2/3", out, out)
        self.assertIn(self.OTHER, out.split("未能清除")[-1],
                      f"失败的那项要点名: {out}")

    def test_set_reports_a_write_failure_instead_of_a_traceback(self):
        """注册表写不进去时该说人话, 不是甩一脸 traceback。"""
        saved = gm.set_gpu_preference
        gm.set_gpu_preference = lambda full: (_ for _ in ()).throw(
            OSError("RegCreateKeyEx failed"))
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gm.cmd_set([__file__])       # 随便一个真实存在的文件
        finally:
            gm.set_gpu_preference = saved
        out = buf.getvalue()
        self.assertIn("设置失败", out, out)

    def test_set_does_not_claim_success_when_the_value_never_landed(self):
        """写入不抵 OSError ≠ 值落了地。

        §54 给托盘侧 _pref_apply 补了回读, 却把命令行
        --set 遗在原地: 杀软/策略拦在半路时
        RegSetValueExW 也可能不报错, 而用户看到的还是
        "已设置独显" —于是白重启一次程序。
        """
        saved_set, saved_get = gm.set_gpu_preference, gm.get_gpu_preference
        gm.set_gpu_preference = lambda full: None      # "写成了"
        gm.get_gpu_preference = lambda full: None      # 回读: 什么都没有
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gm.cmd_set([__file__])
        finally:
            gm.set_gpu_preference, gm.get_gpu_preference = saved_set, saved_get
        out = buf.getvalue()
        self.assertIn("设置未生效", out, out)
        self.assertNotIn("已设置独显", out,
                         "回读还不在独显上, 不能报成功")

    def test_set_confirms_the_write_when_the_read_back_agrees(self):
        """反向一面: 回读确认落地时该报成功。

        只写失败方案的测试会把"永远不知可"
        当成能用的改法。
        """
        saved_set, saved_get = gm.set_gpu_preference, gm.get_gpu_preference
        gm.set_gpu_preference = lambda full: None
        gm.get_gpu_preference = lambda full: "GpuPreference=2;"
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gm.cmd_set([__file__])
        finally:
            gm.set_gpu_preference, gm.get_gpu_preference = saved_set, saved_get
        out = buf.getvalue()
        self.assertIn("已设置独显", out, out)
        self.assertNotIn("未生效", out, out)

    def test_unset_tells_cleared_apart_from_nothing_there_to_clear(self):
        """"清除"的三种结局不能混作一句话。

        clear_gpu_preference 以前只回布尔, 于是"本来就没有这一条"和
        "键打不开/删不掉"共用一句"无设置或清除失败"。前者是陈述事实,
        后者是真失败 —— 用户没法分辨该不该管。按 §53 list_gpu_prefs 的
        老办法分成三态: True 删掉了 / None 本来就没有 / False 真失败。
        """
        saved_clear, saved_get = (gm.clear_gpu_preference,
                                  gm.get_gpu_preference)
        # 回读也得打桩: cmd_unset 现在依赖它判定"清除未生效",
        # 不 mock 就会去摸真注册表, 机器上有没有这条值具数据依赖。
        gm.get_gpu_preference = lambda _f: None
        cases = [(True, "已清除"), (None, "本来就没有"), (False, "清除失败")]
        try:
            for ret, want in cases:
                gm.clear_gpu_preference = lambda _f, r=ret: r
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    gm.cmd_unset([__file__])
                out = buf.getvalue()
                self.assertIn(want, out, "%r 该出现在 %r 里" % (want, out))
                for other, word in cases:
                    if other is not ret:
                        self.assertNotIn(word, out,
                                         "%r 不该和 %r 混在一起" % (word, out))
        finally:
            gm.clear_gpu_preference, gm.get_gpu_preference = \
                saved_clear, saved_get

    def test_unset_does_not_claim_success_when_the_value_survived(self):
        """删除接口说成了 ≠ 值真的没了。

        托盘侧 _pref_apply 早就回读, cmd_unset 一直没有:
        杀软/策略拦下时用户看到"已清除设置", 以为过段
        已经回到核显, 而注册表里那条还在。
        """
        saved_clear, saved_get = gm.clear_gpu_preference, gm.get_gpu_preference
        gm.clear_gpu_preference = lambda full: True                  # "删掉了"
        gm.get_gpu_preference = lambda full: "GpuPreference=2;"      # 回读: 还在
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gm.cmd_unset([__file__])
        finally:
            gm.clear_gpu_preference, gm.get_gpu_preference = \
                saved_clear, saved_get
        out = buf.getvalue()
        self.assertIn("清除未生效", out, out)
        self.assertNotIn("已清除设置", out,
                         "回读还在独显上, 不能报已清除")

    def test_status_says_nothing_when_nothing_is_migrated(self):
        """没有已迁移项时不该出现"已迁移到独显 (0 个)"这种空标题。

        它读起来像程序在汇报读数, 实际上什么都没说 —— 只有"其他设置"
        那一节有话要说, 那就只打那一节。
        """
        prefs = {self.OTHER: "GpuPreference=1;", self.BARE: ""}
        saved = gm.list_gpu_prefs
        gm.list_gpu_prefs = lambda: sorted(prefs.items())
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gm.cmd_status()
        finally:
            gm.list_gpu_prefs = saved
        out = buf.getvalue()
        self.assertNotIn("已迁移到独显", out)
        self.assertIn("其他设置 (2 个)", out)
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


class ModeDisplay(unittest.TestCase):
    """模式名只有一个来源, 面板顶部要显示生效中的那一个。"""

    SNAP = {"util_by_pid": {}, "gpu_all_by_pid": {}, "dgpu_util_by_pid": {},
            "shared_by_pid": {}, "dedicated_by_pid": {}, "igpu_luids": set(),
            "dgpu_luids": set(), "kind_cache": {}, "mode": "saver",
            "threshold": 40.0, "power_saver_auto": True, "on_battery": False}

    def test_mode_names(self):
        for mode, want in (("performance", "性能"), ("saver", "省电"),
                           ("smart", "智能"), (None, "智能")):
            with self.subTest(mode=mode):
                self.assertEqual(gm.mode_text(mode), want)

    def test_panel_shows_mode_threshold_and_auto_switch(self):
        html = gm.panel_html(self.SNAP, {})
        self.assertIn("省电", html)
        self.assertIn("40", html)
        self.assertIn("自动切回 开", html)

    def test_panel_rows_are_ordered_like_the_cli_list(self):
        """行序要跟 --list 一个口径: 按 GPU%/显存最大值排。"""
        snap = dict(self.SNAP)
        snap["gpu_all_by_pid"] = {100: 64.6, 200: 5.0}
        snap["util_by_pid"] = {100: 0.0, 200: 5.0}
        snap["shared_by_pid"] = {100: 4 * 1024 * 1024, 200: 6 * 1024 * 1024}
        snap["pid_names"] = {100: "heavy.exe", 200: "light.exe"}
        html = gm.panel_html(snap, {})
        order = re.findall(r"<td class=pid>(\d+)</td>", html)
        self.assertEqual(order, ["100", "200"],
                         "64.6% 那行必须排在 5% 前面, 哪怕它的核显% 是 0")

    def test_panel_marks_the_battery_override(self):
        """电池下生效阈值换了数, 面板得说清这个数不是配置里那个。"""
        html = gm.panel_html({**self.SNAP, "mode": "smart",
                              "threshold": 55.0, "power_saver_auto": False,
                              "on_battery": True}, {})
        self.assertIn("电池", html)
        self.assertIn("55", html)


class TrayModeDisplay(unittest.TestCase):
    """托盘 tooltip: 不点开面板、不右键菜单, 悬停就该看到生效策略。"""

    def _title(self, snap):
        import threading
        import gpu_tray

        class Fake:
            paused = False
            monitor_dead = False
            _title_base = ""

            def __init__(self):
                self.lock = threading.Lock()
                self.snapshot = None
                self.icon = type("I", (), {"title": "", "icon": None})()

            _gpu_name = gpu_tray.TrayApp._gpu_name
            _refresh_title = gpu_tray.TrayApp._refresh_title

        f = Fake()
        gpu_tray.TrayApp._on_sample(f, {
            "util_by_pid": {7: 12.0}, "gpu_all_by_pid": {},
            "dgpu_util_by_pid": {}, "shared_by_pid": {},
            "dedicated_by_pid": {}, "igpu_luids": set(),
            "dgpu_luids": set(), "kind_cache": {}, **snap})
        return f.icon.title

    def test_tooltip_names_the_effective_mode(self):
        title = self._title({"mode": "saver", "threshold": 40.0,
                             "power_saver_auto": True, "on_battery": False})
        self.assertIn("省电", title)
        self.assertIn("40", title)

    def test_tooltip_shows_battery_override(self):
        self.assertIn("电池", self._title({"mode": "smart", "threshold": 55.0,
                                           "power_saver_auto": True,
                                           "on_battery": True}))

    def test_tooltip_shows_dgpu_temp_when_known(self):
        """Web 面板的显卡行有"独显温度", 托盘 tooltip 不能望着空栏。

        on_sample 钩子快照原先不带 dgpu_temp, 托盘想显示也没有数据源;
        现在键补齐了, 悬停就该和 Web 用同一份数、同一句文案。
        """
        title = self._title({"mode": "saver", "threshold": 40.0,
                             "power_saver_auto": False, "on_battery": False,
                             "dgpu_temp": 61})
        self.assertIn("独显温度: 61°C", title)

    def test_tooltip_hides_temp_when_nvml_is_unavailable(self):
        """读不到温度(NVML 不在/非 N 卡)时不能显示 0°C 或 "None"。"""
        title = self._title({"mode": "saver", "threshold": 40.0,
                             "power_saver_auto": False, "on_battery": False,
                             "dgpu_temp": None})
        self.assertNotIn("独显温度", title)

    def test_a_huge_adapter_name_does_not_kill_the_whole_tooltip(self):
        """szTip 是 TCHAR[128]: 超长的话整条提示不显示, 不是被截断。

        显卡名一长, 占用率和生效策略这些真要看的会被名字一起顶出
        窗口外 —— 那时候要砍的是名字那一行, 不是把提示整个丢掉。
        """
        long_a = "Intel(R) UHD Graphics " + "X" * 70
        long_b = "NVIDIA GeForce RTX " + "Y" * 70
        import gpu_tray
        snap = {"mode": "saver", "threshold": 40.0,
                "power_saver_auto": True, "on_battery": True,
                "igpu_luids": {"L1"}, "dgpu_luids": {"L2"},
                "gpu_all_by_pid": {}, "shared_by_pid": {},
                "dedicated_by_pid": {},
                "kind_cache": {"L1": ("igpu", long_a),
                                "L2": ("dgpu", long_b)}}

        class Fake:
            paused = True
            monitor_dead = False
            _title_base = ""
            _refresh_title = gpu_tray.TrayApp._refresh_title
            _gpu_name = gpu_tray.TrayApp._gpu_name

            def __init__(self):
                self.lock = threading.Lock()
                self.snapshot = None
                self.icon = type("I", (), {"title": "", "icon": None})()

        f = Fake()
        gpu_tray.TrayApp._on_sample(f, {"util_by_pid": {7: 12.0}, **snap})
        self.assertLessEqual(len(f.icon.title), gpu_tray._TIP_MAX,
                             "szTip 上限是 127 字符")
        for must in ("核显占用", "省电", "已暂停"):
            self.assertIn(must, f.icon.title)

    def test_surviving_temp_outlives_a_long_adapter_name(self):
        """温度要排在卡名那一行之前: 名字再长也该砍名字, 不能砍温度。"""
        long_a = "Intel(R) UHD Graphics " + "X" * 70
        long_b = "NVIDIA GeForce RTX " + "Y" * 70
        import gpu_tray
        snap = {"mode": "saver", "threshold": 40.0,
                "power_saver_auto": True, "on_battery": True,
                "igpu_luids": {"L1"}, "dgpu_luids": {"L2"},
                "gpu_all_by_pid": {}, "shared_by_pid": {},
                "dedicated_by_pid": {}, "dgpu_temp": 61,
                "kind_cache": {"L1": ("igpu", long_a),
                               "L2": ("dgpu", long_b)}}

        class Fake:
            paused = True
            monitor_dead = False
            _title_base = ""
            _refresh_title = gpu_tray.TrayApp._refresh_title
            _gpu_name = gpu_tray.TrayApp._gpu_name

            def __init__(self):
                self.lock = threading.Lock()
                self.snapshot = None
                self.icon = type("I", (), {"title": "", "icon": None})()

        f = Fake()
        gpu_tray.TrayApp._on_sample(f, {"util_by_pid": {7: 12.0}, **snap})
        self.assertLessEqual(len(f.icon.title), gpu_tray._TIP_MAX,
                             "szTip 上限是 127 字符")
        self.assertIn("独显温度: 61", f.icon.title,
                      "名字一长就把温度整段顶出去了")
        self.assertIn("...", f.icon.title, "该被截断的是名字那一行")



class TrayConfigWrite(unittest.TestCase):
    """托盘切模式要真的落盘, 且不能把 config.json 里别的键写丢。"""

    def setUp(self):
        import gpu_tray
        self.tray = gpu_tray
        self.dir = tempfile.mkdtemp(prefix="gpumig-tray-")
        self.path = os.path.join(self.dir, "config.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"threshold_percent": 20.0,
                       "hotkeys": {"panel": "ctrl+alt+g"},
                       "ignore_processes": ["old.exe"]}, f)
        self.app = object.__new__(gpu_tray.TrayApp)
        self.app.config_path = self.path

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_mode_switch_persists_and_keeps_other_keys(self):
        self.app._apply_mode("saver", 40, True)
        cfg = gm.read_json_dict(self.path)
        self.assertEqual(cfg["mode"], "saver")
        self.assertEqual(cfg["threshold_percent"], 40)
        self.assertTrue(cfg["power_saver_auto"])
        self.assertEqual(cfg["ignore_processes"], ["old.exe"])
        self.assertEqual(cfg["hotkeys"], {"panel": "ctrl+alt+g"})

    def test_menu_label_reads_back_what_was_written(self):
        self.app._apply_mode("performance", 10, False)
        self.assertEqual(self.app._mode_text(), "性能")

    def test_smart_mode_leaves_untouched_knobs_alone(self):
        """智能模式只改 mode, 阈值和用户自己填过的热键不该被顺手重写。"""
        self.app._apply_mode("smart", None, None)
        cfg = gm.read_json_dict(self.path)
        self.assertEqual(cfg["mode"], "smart")
        self.assertEqual(cfg["threshold_percent"], 20.0)

    def test_mode_switch_creates_a_missing_config(self):
        """还没有 config.json 时切模式也得存下来。

        这是本次改动唯一的外部可见差异: 旧实现读不到文件就 except OSError
        静默 return, 全新安装点菜单上任何模式都不会落盘, 而且看不出来。
        """
        os.remove(self.path)
        self.app._apply_mode("saver", 40, True)
        self.assertEqual(gm.read_json_dict(self.path),
                         {"mode": "saver", "threshold_percent": 40,
                          "power_saver_auto": True})

    def test_mode_switch_does_not_clobber_a_corrupt_config(self):
        broken = '{"threshold_percent": 20.0, "hotkeys": '
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(broken)
        self.app._apply_mode("saver", 40, True)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), broken)

    def test_settings_num_reports_the_fields_it_refused(self):
        """填的不是数字, 得让 save() 知道是哪几格, 好去跟用户说。

        旧行为是静默延用当前生效值, 标题还说"已保存" ——
        用户以为存进去了, 其实那格还是旧值。
        """
        class _V:
            def __init__(self, v):
                self._v = v

            def get(self):
                return self._v

        cfg = dict(gm.DEFAULT_CONFIG)
        bad = []
        got = self.tray.TrayApp._settings_num(
            _V("很高"), cfg, "threshold_percent", float, bad)
        self.assertEqual(got, cfg["threshold_percent"])
        self.assertEqual(bad, ["threshold_percent"])
        # 填对了就不该记名, 否则标题会平白多一句
        ok = []
        self.assertEqual(
            self.tray.TrayApp._settings_num(_V("33"), cfg,
                                            "threshold_percent", float, ok),
            33.0)
        self.assertEqual(ok, [])
        # 不传 bad(老调用方式)照旧能用
        self.assertEqual(
            self.tray.TrayApp._settings_num(_V("7788"), cfg,
                                            "web_port", int), 7788)

    def test_the_save_button_names_the_fields_it_refused(self):
        """点了保存却没说哪格没生效, 等于让用户对着空气猜。"""
        body = inspect.getsource(self.tray.TrayApp._settings_build)
        self.assertIn("bad = []", body)
        for key in ("v_thr", "v_vram", "v_bthr", "v_temp", "v_int", "v_port"):
            self.assertIn(key + ", cfg,", body)
            # 五个数字项都得把 bad 传进去
        self.assertEqual(body.count(", bad)"), 6,
                         "每个数字项都要报上自己被拒的键名")
        self.assertIn("仍用原值", body)
        self.assertIn("SETTINGS_LABELS", body)

    def test_settings_labels_cover_the_numeric_fields(self):
        """点名要用设置窗里的中文行名, 不能拿 config.json 的键名搪塞。"""
        for key in ("threshold_percent", "vram_threshold_mb",
                    "battery_threshold_percent", "temp_limit",
                    "interval_seconds", "web_port"):
            self.assertIn(key, gm.SETTINGS_LABELS)
        # 行名就是用户在设置窗里看到的文字
        self.assertEqual(gm.SETTINGS_LABELS["threshold_percent"], "迁移阈值 %")


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
                cfg = {"hotkeys": {}}

                def _monitor_thread(self):
                    pass

                def _hotkey_actions(self):
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


class TrayDebugLogIsolation(unittest.TestCase):
    """套件不许往仓库里那份真实托盘排查日志写字。

    `_dbg` 和 `run_tray` 的 except 分支都是 `os.path.join(_DIR, ...)`, 也就是
    **调用时**才决定写到哪。此前只有 TrayLifecycleLog / TrayStartupFailure 四处
    自己换了 _DIR, 其余用例一触发这些分支就落在仓库根目录: 现网托盘的排查日志和
    测试噪音混进同一个文件, "跑完日志逐字节不变"那句成了假的。
    """

    REPO_LOG = os.path.join(gm._DIR, "tray_debug.log")

    def _repo_bytes(self):
        if not os.path.exists(self.REPO_LOG):
            return b""
        return io.open(self.REPO_LOG, "rb").read()

    def test_the_suite_directs_the_tray_dir_into_a_temp_dir(self):
        # 顶层那句重定向被删掉, 或某条用例换了 _DIR 没还原 —— 都在这条红。
        self.assertEqual(gpu_tray._DIR, _SUITE_TMP,
                         f"gpu_tray._DIR 已不是套件临时目录({gpu_tray._DIR}),"
                         " 排查日志会写回仓库")

    def test_a_debug_line_goes_to_the_temp_dir_not_the_repo(self):
        sentinel = f"gpumig-isolation-sentinel-{id(self)}"
        before = self._repo_bytes()
        try:
            gpu_tray._dbg(sentinel)
        finally:
            self.assertEqual(self._repo_bytes(), before,
                             "_dbg 把测试写的内容落进了仓库那份日志")
        log = os.path.join(_SUITE_TMP, "tray_debug.log")
        text = (io.open(log, encoding="utf-8").read()
                if os.path.exists(log) else "")
        # 哨兵必须真的落在临时目录: 不然上一条"仓库没变"是因为压根没写,
        # 守卫空转。
        self.assertIn(sentinel, text, "哨兵没进套件临时目录 = 写入路径没被触发")


class ConfigValidation(unittest.TestCase):
    """手改坏的 config.json 要在入口被收敛, 不能等它在循环里炸。

    以前 "threshold_percent": "40" 能一路走到 hot_reason 里和 float 比,
    TypeError 直接把监控线程带走; sustain_samples: 0 则第一次超标就迁移。
    """

    def capture(self, data):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cfg = gm.merged_config(data)
        return cfg, buf.getvalue()

    def test_numeric_string_is_accepted(self):
        cfg, out = self.capture({"threshold_percent": "40"})
        self.assertEqual(cfg["threshold_percent"], 40.0)
        self.assertEqual(out, "")

    def test_junk_number_falls_back_to_default(self):
        cfg, out = self.capture({"threshold_percent": "很高"})
        self.assertEqual(cfg["threshold_percent"],
                         gm.DEFAULT_CONFIG["threshold_percent"])
        self.assertIn("threshold_percent", out)
        self.assertIn("已用默认值", out)

    def test_non_numeric_never_reaches_hot_reason(self):
        cfg, _out = self.capture({"threshold_percent": "很高"})
        self.assertEqual(gm.hot_reason(60.0, 0, cfg["threshold_percent"], 0),
                         "核显利用率 60.0%")

    def test_bounds_are_clamped(self):
        cfg, _out = self.capture({"threshold_percent": 500,
                                  "sustain_samples": 0,
                                  "interval_seconds": 0.001,
                                  "web_port": 99999})
        self.assertEqual(cfg["threshold_percent"], 100.0)
        self.assertEqual(cfg["sustain_samples"], 1, "0 会让首次超标即迁移")
        self.assertEqual(cfg["interval_seconds"], 0.5)
        self.assertEqual(cfg["web_port"], 65535)

    def test_bool_strings_are_understood(self):
        cfg, _out = self.capture({"notify": "false", "auto_restart": "yes",
                                  "power_saver_auto": "off"})
        self.assertIs(cfg["notify"], False)
        self.assertIs(cfg["auto_restart"], True)
        self.assertIs(cfg["power_saver_auto"], False)

    def test_unknown_bool_falls_back_to_default(self):
        cfg, out = self.capture({"notify": "也许"})
        self.assertIs(cfg["notify"], gm.DEFAULT_CONFIG["notify"])
        self.assertIn("不是开关", out)

    def test_scalar_list_becomes_single_item(self):
        """写成字符串会被逐字符遍历, 排除名单静默失效。"""
        cfg, _out = self.capture({"exclude_processes": "chrome.exe"})
        self.assertEqual(cfg["exclude_processes"], ["chrome.exe"])

    def test_list_entries_are_stripped(self):
        u"""手改 config.json 的名单项常带尾随空格/tab, 要去掉才匹配得上。

        进程名比对是集合精确匹配: " chrome.exe " != "chrome.exe",
        整条排除名单会静默失效, 用户还以为配错了名字。
        """
        cfg, out = self.capture({"exclude_processes": [" chrome.exe "],
                                 "game_processes": ["game.exe\t", ""]})
        self.assertEqual(cfg["exclude_processes"], ["chrome.exe"])
        self.assertEqual(cfg["game_processes"], ["game.exe"])
        self.assertEqual(out, "")

    def test_stripped_exclusion_actually_excludes(self):
        u"""端到端: 带空白的排除项要真的把进程挡在候选外。"""
        cfg, _out = self.capture({"exclude_processes": [" chrome.exe "]})
        saved = gm.pid_to_name
        gm.pid_to_name = lambda pid: ("chrome.exe", "C:\\chrome.exe")
        try:
            hot = gm.select_hot_candidates(cfg, {1: 99.0}, {}, 50.0, 1024,
                                           set(), set())
        finally:
            gm.pid_to_name = saved
        self.assertEqual(hot, [])

    def test_bad_list_type_falls_back(self):
        cfg, out = self.capture({"exclude_processes": {"a": 1}})
        self.assertEqual(cfg["exclude_processes"], [])
        self.assertIn("不是列表", out)

    def test_unparsable_hotkey_is_dropped(self):
        cfg, out = self.capture({"hotkeys": {"panel": "ctrl+delete",
                                             "pause": ""}})
        self.assertEqual(cfg["hotkeys"], {}, "解析不了的热键不该留给注册阶段")
        self.assertIn("ctrl+delete", out)

    def test_hotkeys_wrong_type_falls_back(self):
        cfg, out = self.capture({"hotkeys": "ctrl+alt+g"})
        self.assertEqual(cfg["hotkeys"], gm.DEFAULT_CONFIG["hotkeys"])
        self.assertIn("不是对象", out)

    def test_valid_hotkeys_survive(self):
        cfg, out = self.capture({"hotkeys": {"panel": "ctrl+alt+g"}})
        self.assertEqual(cfg["hotkeys"], {"panel": "ctrl+alt+g"})
        self.assertEqual(out, "")


class HotkeyBindings(unittest.TestCase):
    """配置广告了热键, 但只有 hooks 同时给回调时才注册得上去。"""

    def test_no_hooks_means_no_bindings(self):
        self.assertEqual(gm.hotkey_bindings(dict(gm.DEFAULT_CONFIG), None), {})
        cfg = dict(gm.DEFAULT_CONFIG)
        self.assertEqual(
            gm.hotkey_bindings(cfg, {"on_sample": lambda s: None}), {})

    def test_hook_only_registers_its_own_combo(self):
        fired = []
        bindings = gm.hotkey_bindings(
            dict(gm.DEFAULT_CONFIG),
            {"hotkey_panel": lambda: fired.append("panel")})
        self.assertEqual(sorted(bindings), [1])
        self.assertEqual(bindings[1][0], "ctrl+alt+g")
        bindings[1][1]()
        self.assertEqual(fired, ["panel"])

    def test_disabled_hotkeys_register_nothing(self):
        cfg = dict(gm.DEFAULT_CONFIG)
        cfg["hotkeys"] = {}
        self.assertEqual(
            gm.hotkey_bindings(cfg, {"hotkey_pause": lambda: None}), {})


class NotifyFailureLogging(unittest.TestCase):
    """notify:true 却一个提示都没有时, 日志里必须有原因。"""

    def setUp(self):
        self.saved = (gm.TOAST_PS1, gm._LOGFILE, gm.subprocess.Popen)

    def tearDown(self):
        gm.TOAST_PS1, gm._LOGFILE, gm.subprocess.Popen = self.saved

    def test_missing_toast_script_is_logged(self):
        gm.TOAST_PS1 = os.path.join(gm._DIR, "__no_such__.ps1")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gm.notify("标题", "内容")
        self.assertIn("通知失败", buf.getvalue())
        self.assertIn("__no_such__.ps1", buf.getvalue())

    def test_popen_failure_is_logged(self):
        def boom(*_a, **_k):
            raise OSError("powershell 起不来")
        gm.subprocess.Popen = boom
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gm.notify("标题", "内容")
        self.assertIn("通知失败", buf.getvalue())
        self.assertIn("powershell 起不来", buf.getvalue())


class ConfirmDialogTimeout(unittest.TestCase):
    """确认弹窗不能无限期按住监控线程: 没人应答就本轮不迁移。"""

    def setUp(self):
        self.saved = gm._find_messagebox_timeout

    def tearDown(self):
        gm._find_messagebox_timeout = self.saved

    def test_timeout_counts_as_ignore_and_is_logged(self):
        gm._find_messagebox_timeout = lambda: lambda *_a: 0
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertFalse(gm.ask_migrate("game.exe", "核显利用率 80%"))
        self.assertIn("无应答", buf.getvalue())
        self.assertIn("game.exe", buf.getvalue())

    def test_yes_and_no_are_passed_through(self):
        gm._find_messagebox_timeout = lambda: lambda *_a: gm.IDYES
        self.assertTrue(gm.ask_migrate("game.exe", "80%"))
        gm._find_messagebox_timeout = lambda: lambda *_a: 7      # IDNO
        self.assertFalse(gm.ask_migrate("game.exe", "80%"))


class _FakeAdvapiBase:
    """替身 advapi32: RegOpenKeyExW/RegCloseKey 走空, 查询类由子类实现。"""

    def RegOpenKeyExW(self, *_a):
        return 0

    def RegCloseKey(self, *_a):
        return 0


class FakeValueQuery(_FakeAdvapiBase):
    """RegQueryValueExW: 缓冲装不下就返回 ERROR_MORE_DATA。"""

    def __init__(self, value):
        self.value = value
        self.calls = []

    def RegQueryValueExW(self, _hkey, _name, _r1, _r2, buf, cb):
        cap = _get_out(cb) // 2          # lpcbData 按字节计
        self.calls.append(cap)
        if cap < len(self.value) + 1:
            return 234                   # ERROR_MORE_DATA
        buf.value = self.value
        return 0


class FakeEnum(_FakeAdvapiBase):
    """RegEnumValueW: 名字缓冲不够就 ERROR_MORE_DATA, 枚举到最后返回 259。"""

    def __init__(self, values, need_chars):
        self.values = values
        self.need_chars = need_chars     # 放得下一条需要的字符数
        self.name_caps = []

    def RegEnumValueW(self, _hkey, idx, name, cb, _r4, _r5, data, _dcb):
        self.name_caps.append(_get_out(cb))
        if idx >= len(self.values):
            return 259                   # ERROR_NO_MORE_ITEMS
        if _get_out(cb) < self.need_chars:
            return 234                   # ERROR_MORE_DATA
        name.value = self.values[idx][0]
        data.value = self.values[idx][1]
        return 0


class FakeWrite(_FakeAdvapiBase):
    """RegSetValueExW 替身: 记下 type / cbData 与实际缓冲内容。

    cbData 按字节计且必须含结尾 NUL —— 少一个字符 Windows 那边读出来
    就是缺尾字的串, 而多送又可能把缓冲外的字节一起写进去。
    """

    def __init__(self, ret=0):
        self.ret = ret
        self.calls = []
        self.opened = 0
        self.closed = 0

    def RegOpenKeyExW(self, *_a):
        self.opened += 1
        return 0

    def RegCreateKeyExW(self, *_a):
        self.opened += 1
        return 0

    def RegCloseKey(self, *_a):
        self.closed += 1
        return 0

    def RegSetValueExW(self, _hkey, name, _r, typ, buf, cb):
        # 只有前 cb 字节是被送进去的, 其余是缓冲余量
        raw = ctypes.string_at(buf, cb)
        self.calls.append((name, typ, cb, raw))
        return self.ret


class FakeDelete(_FakeAdvapiBase):
    """RegDeleteValueW: 回 caller 指定的 LSTATUS, 记下被删的那个名字。"""

    def __init__(self, ret):
        self.ret = ret
        self.deleted = []

    def RegDeleteValueW(self, _hkey, name):
        self.deleted.append(name)
        return self.ret


class RegistryBuffers(unittest.TestCase):
    """固定大小的注册表缓冲会让长值被当成"没有设置过"。"""

    def setUp(self):
        self.saved = gm.advapi32

    def tearDown(self):
        gm.advapi32 = self.saved

    def test_clear_reports_not_found_as_nothing_there_not_failure(self):
        """真打注册表: 删除的错误码在返回值里, 不在 GetLastError。

        本机实测 RegDeleteValueW 回 2(ERROR_FILE_NOT_FOUND) 时
        ctypes.get_last_error() 还是 0 —— 按 last_error 判就会把
        "本来就没有这一条"报成"清除失败(权限被限制?)", 平白吓人。
        2 算空, 别的非零才是真失败。
        """
        EXE = r"D:\app\a.exe"
        cases = [(0, True, "删掉了"), (2, None, "本来就没有"),
                 (5, False, "拒绝访问"), (87, False, "参数错误")]
        for ret, want, why in cases:
            fake = FakeDelete(ret)
            gm.advapi32 = fake
            self.assertIs(gm.clear_gpu_preference(EXE), want,
                          "ret=%d(%s) 该映射成 %r" % (ret, why, want))
            self.assertEqual(fake.deleted, [EXE])

    def test_set_writes_exactly_the_value_bytes_with_a_nul(self):
        """真打注册表: 送出去的字节必须正好是 (值 + 结尾 NUL)。

        cbData 少算一个字符, Windows 读回的就是缺尾分号的串 —— 而
        is_high_performance 是按分号切开逐段比的, 少一个分号整条判不出
        已迁移, 于是进程下一轮又被重写一遍再通知一次。
        """
        fake = FakeWrite()
        gm.advapi32 = fake
        gm.set_gpu_preference(r"D:\app\a.exe")
        self.assertEqual(len(fake.calls), 1, "只该写一次")
        name, typ, cb, raw = fake.calls[0]
        self.assertEqual(name, r"D:\app\a.exe")
        self.assertEqual(typ, 1, "REG_SZ")
        self.assertEqual(cb, len("GpuPreference=2;") * 2 + 2,
                         "cbData 按字节算, 且要含结尾 NUL")
        self.assertEqual(raw.decode("utf-16-le"), "GpuPreference=2;\x00")
        self.assertEqual(fake.closed, 1, "键必须关, 否则句柄泄漏")

    def test_set_failure_raises_oserror_with_the_code(self):
        """RegSetValueExW 报错必须变成 OSError, 好让上层说人话。"""
        fake = FakeWrite(ret=5)                     # ERROR_ACCESS_DENIED
        gm.advapi32 = fake
        with self.assertRaises(OSError) as cm:
            gm.set_gpu_preference(r"D:\app\a.exe")
        self.assertIn("0x00000005", str(cm.exception), str(cm.exception))
        self.assertEqual(fake.closed, 1, "失败也要关键")

    def test_set_failure_to_create_key_raises_without_touching_the_value(self):
        fake = FakeWrite()

        def no_create(*_a):
            raise AssertionError("键都没建成就不该去写值")

        fake.RegCreateKeyExW = no_create

        class NoCreate(FakeWrite):
            def RegCreateKeyExW(self, *_a):
                return 5

        gm.advapi32 = NoCreate()
        with self.assertRaises(OSError):
            gm.set_gpu_preference(r"D:\app\a.exe")

    def test_clear_when_the_key_will_not_open_is_a_failure(self):
        """键都打不开(权限/策略)是真失败, 不是"没有设置"。"""
        class NoOpen(FakeDelete):
            def RegOpenKeyExW(self, *_a):
                return 5                      # ERROR_ACCESS_DENIED

        gm.advapi32 = NoOpen(0)
        self.assertIs(gm.clear_gpu_preference(r"D:\app\a.exe"), False)

    def test_clear_when_the_key_does_not_exist_is_nothing_there(self):
        """键本身不存在: 更是"从没设过", 不是失败。"""
        class NoKey(FakeDelete):
            def RegOpenKeyExW(self, *_a):
                return 2                      # ERROR_FILE_NOT_FOUND

        fake = NoKey(0)
        gm.advapi32 = fake
        self.assertIs(gm.clear_gpu_preference(r"D:\app\a.exe"), None)
        self.assertEqual(fake.deleted, [], "键都没开成就不该去删值")

    def test_long_preference_value_is_read_back(self):
        value = "SpecificAdapter=" + "A" * 3000 + ";GpuPreference=2;"
        fake = FakeValueQuery(value)
        gm.advapi32 = fake
        self.assertEqual(gm.get_gpu_preference(r"D:pp\x.exe"), value)
        self.assertEqual(fake.calls[:2], [128, 4096], "装不下要扩容再试")
        self.assertTrue(gm.is_high_performance(value),
                        "读全了才判得出已迁移")

    def test_enum_grows_buffer_for_long_names(self):
        long_exe = "D:\\app\\" + "b" * 900 + ".exe"
        values = [(long_exe, "GpuPreference=2;"),
                  (r"D:\app\short.exe", "GpuPreference=2;")]
        fake = FakeEnum(values, need_chars=1000)
        gm.advapi32 = fake
        got = gm.list_gpu_prefs()
        self.assertEqual([exe for exe, _v in got], [v[0] for v in values])
        self.assertEqual(fake.name_caps[0], 512, "第一次用小缓冲探")
        self.assertGreater(fake.name_caps[1], 1000, "MORE_DATA 之后要扩容")

    def test_short_values_still_work(self):
        fake = FakeEnum([(r"D:\app\a.exe", "GpuPreference=2;")],
                        need_chars=10)
        gm.advapi32 = fake
        self.assertEqual(gm.list_gpu_prefs(),
                         [(r"D:\app\a.exe", "GpuPreference=2;")])
        self.assertEqual(set(fake.name_caps), {512},
                         "够用时不该反复扩容")


class SingletonGate(unittest.TestCase):
    """并发实例会同时写注册表和 history.jsonl, 第二个必须退出。"""

    class _Mutex:
        def __init__(self):
            self.argtypes = None

        def __call__(self, *_a):
            return 0

    class _FakeKernel:
        def __init__(self, last_error):
            self.last_error = last_error
            self.CreateMutexW = SingletonGate._Mutex()

    class _FakeCtypes:
        def __init__(self, last_error):
            self.last_error = last_error

        def WinDLL(self, *_a, **_k):
            return SingletonGate._FakeKernel(self.last_error)

        def get_last_error(self):
            return self.last_error

    def test_second_instance_refuses(self):
        saved = gm.ctypes
        gm.ctypes = self._FakeCtypes(183)   # ERROR_ALREADY_EXISTS
        try:
            self.assertFalse(gm.ensure_singleton())
        finally:
            gm.ctypes = saved

    def test_first_instance_proceeds(self):
        saved = gm.ctypes
        gm.ctypes = self._FakeCtypes(0)
        try:
            self.assertTrue(gm.ensure_singleton())
        finally:
            gm.ctypes = saved

    def test_console_monitor_also_checks(self):
        """老代码只在 --tray 建互斥体, 命令行双实例没人拦。"""
        saved_argv, saved = sys.argv, (gm.load_config, gm.ensure_singleton)
        sys.argv = ["gpu_monitor.py"]
        gm.load_config = lambda _p: dict(gm.DEFAULT_CONFIG)
        gm.ensure_singleton = lambda: False
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gm.main()
            self.assertIn("已有 GPU 监控实例在运行", buf.getvalue())
        finally:
            sys.argv = saved_argv
            gm.load_config, gm.ensure_singleton = saved

    def test_double_click_tray_when_running_tells_the_user(self):
        """双击托盘版撞上互斥体不能静默消失。

        打包版是 --noconsole 的 pythonw: 老代码 return 之前连一行"已有
        实例"都印不出来, 用户只看到双击后什么都没发生。
        """
        saved_argv, saved = sys.argv, (gm.ensure_singleton, gm.user32)
        sys.argv = ["gpu_monitor.py", "--tray"]
        gm.ensure_singleton = lambda: False
        boxes = []
        gm.user32 = type("U", (), {"MessageBoxW": staticmethod(
            lambda *a: boxes.append(a) or 0)})()
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gm.main()
        finally:
            sys.argv = saved_argv
            gm.ensure_singleton, gm.user32 = saved
        self.assertEqual(len(boxes), 1)
        text, title = boxes[0][1], boxes[0][2]
        self.assertIn("已在运行", text)
        self.assertIn("托盘", text)
        self.assertIn("GPU", title)

    def test_ensure_stays_quiet(self):
        """看门狗每 2 分钟例行 --ensure, 撞互斥体也必须一声不吭。"""
        saved_argv, saved = sys.argv, (gm.ensure_singleton, gm.user32)
        sys.argv = ["gpu_monitor.py", "--ensure"]
        gm.ensure_singleton = lambda: False
        boxes = []
        gm.user32 = type("U", (), {"MessageBoxW": staticmethod(
            lambda *a: boxes.append(a) or 0)})()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                gm.main()
        finally:
            sys.argv = saved_argv
            gm.ensure_singleton, gm.user32 = saved
        self.assertEqual(boxes, [])


class MigrationReporting(LoopHarness):
    """迁移结果要如实上报: 写注册表和重启进程是两件事。"""

    def test_restart_failure_still_counts_as_success(self):
        """taskkill 被杀软拦下时, 首选项其实已经写成功了。"""
        def boom(_pid, _full):
            raise OSError("taskkill 被拒绝")

        gm.restart_process = boom
        self.run_monitor([{100: 80.0}] * 3,
                         cfg_overrides={"auto_restart": True})
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;")
        titles = [title for title, _msg in self.notes]
        self.assertIn("GPU 迁移成功", titles)
        self.assertNotIn("GPU 迁移失败", titles)
        self.assertTrue(any("自动重启失败" in line for line in self.log_lines),
                        "重启失败要单独说, 不能冒充迁移失败")

    def test_registry_failure_reports_failure(self):
        def boom(_path, _value="GpuPreference=2;"):
            raise OSError("注册表拒写")

        gm.set_gpu_preference = boom
        self.run_monitor([{100: 80.0}] * 3)
        titles = [title for title, _msg in self.notes]
        self.assertIn("GPU 迁移失败", titles)
        self.assertNotIn("GPU 迁移成功", titles)
        self.assertEqual(self.prefs, {})


class TrayMonitorDeath(unittest.TestCase):
    """监控线程死掉时, 托盘不能继续装作还在监控。"""

    def setUp(self):
        import gpu_tray
        self.tray = gpu_tray
        self.dir = tempfile.mkdtemp(prefix="gpumig-tray-")
        self.saved_dir = gpu_tray._DIR
        self.saved_cmd = gpu_tray.cmd_monitor
        self.saved_notify = gm.notify
        gpu_tray._DIR = self.dir
        self.notes = []
        gm.notify = lambda t, m: self.notes.append((t, m))

    def tearDown(self):
        self.tray._DIR = self.saved_dir
        self.tray.cmd_monitor = self.saved_cmd
        gm.notify = self.saved_notify
        shutil.rmtree(self.dir, ignore_errors=True)

    def _app(self):
        app = object.__new__(self.tray.TrayApp)
        app.cfg, app.config_path = {}, None
        app.stop = app.monitor_dead = app.paused = False
        app.panel_request = False
        app._title_base = "GPU 监控"
        app.icon = type("I", (), {"title": "", "icon": None})()
        app._before_cycle = app._on_sample = app._on_history_point = \
            lambda *_a: None
        return app

    def test_dead_monitor_is_announced(self):
        def boom(*_a, **_k):
            raise RuntimeError("采样线程故意炸")
        self.tray.cmd_monitor = boom
        app = self._app()
        app._monitor_thread()
        self.assertTrue(app.monitor_dead)
        self.assertTrue(app.stop, "before_cycle 的等待循环要能退出")
        self.assertIn("监控已停止", app.icon.title)
        self.assertEqual([title for title, _m in self.notes],
                         ["GPU 监控已停止"])
        with open(os.path.join(self.dir, "tray_error.log"),
                  encoding="utf-8") as f:
            self.assertIn("RuntimeError: 采样线程故意炸", f.read())

    def test_hotkey_hooks_are_handed_to_the_monitor(self):
        """DEFAULT_CONFIG 广告了 hotkeys, 托盘不给回调就一个都注册不上。"""
        seen = {}
        self.tray.cmd_monitor = lambda _cfg, _path, hooks=None: seen.update(
            hooks or {})
        app = self._app()
        app._monitor_thread()
        self.assertIn("hotkey_panel", seen)
        self.assertIn("hotkey_pause", seen)
        seen["hotkey_panel"]()
        self.assertTrue(app.panel_request,
                        "热键线程只置位, 开面板交给轮询线程")
        seen["hotkey_pause"]()
        self.assertTrue(app.paused)


class TraySingleInterpreter(unittest.TestCase):
    """三个窗口必须共用同一个 Tcl 解释器, 而且整个进程只有一个 mainloop。

    老实现给面板/设置/详情各开一个 tk.Tk() 且各跑一个 mainloop(), 三个
    解释器并发进出 Tcl —— tkinter 不允许这么用, 偶发把进程整个带崩。
    这里用替身 tkinter 假装一条真实的事件循环: 替身 mainloop 跑在 UI 线程
    里逐个派发 after 回调, 主线程只负责发请求和等结果, 于是"建了几个
    root、窗口都挂在谁下面、请求由谁执行"全都被钉死。
    """

    def setUp(self):
        import gpu_tray
        self.gt = gpu_tray
        self.saved_tk, self.saved_ttk = gpu_tray.tk, gpu_tray.ttk
        self.release = threading.Event()
        self.fake_tk = mock.MagicMock(name="tk")
        self.host = self.fake_tk.Tk.return_value
        self.windows = []

        def toplevel(*_args, **_kwargs):
            win = mock.MagicMock(name="toplevel")
            win.geometry.return_value = "1020x360"
            self.windows.append(win)
            return win

        def fake_mainloop():
            # 真 Tcl 的 mainloop 会把每个 after 回调派发一次; 替身照做, 而且
            # 必须跑在 UI 线程里 —— 主线程自己触发回调就测不出线程归属了。
            seen = 0
            while not self.release.is_set():
                pending = list(self.host.after.call_args_list)[seen:]
                seen = len(self.host.after.call_args_list)
                for call in pending:
                    if self.release.is_set():
                        break
                    call.args[1]()
                self.release.wait(0.01)

        self.fake_tk.Toplevel.side_effect = toplevel
        self.host.mainloop.side_effect = fake_mainloop
        gpu_tray.tk = self.fake_tk
        gpu_tray.ttk = mock.MagicMock(name="ttk")
        gpu_tray.ttk.Treeview.return_value.column.return_value = 120
        self.dir = tempfile.mkdtemp(prefix="gpumig-ui-")
        self.path = os.path.join(self.dir, "config.json")
        # _UI_FILE 也必须一起指到临时目录。config.json 指走了、它没指走,
        # 于是 test_closing_a_window_frees_the_slot 走 on_close 时把仓库里
        # 那份真实的布局覆盖了: 列宽全是替身 tree.column 的返回值, geometry
        # 是替身 Toplevel 的返回值 —— 每跑一遍全套测试, 开发者自己的面板
        # (列宽/筛选词/排序列/置顶)就被冲一次, 且无法恢复。
        self.ui_file = os.path.join(self.dir, "panel_ui.json")
        self.saved_ui_file = gpu_tray._UI_FILE
        gpu_tray._UI_FILE = self.ui_file
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"threshold_percent": 30}, f)
        self.app = gpu_tray.TrayApp(self.path)

    def tearDown(self):
        self.release.set()
        if self.app._ui_thread is not None:
            self.app._ui_thread.join(timeout=5)
        self.gt.tk, self.gt.ttk = self.saved_tk, self.saved_ttk
        self.gt._UI_FILE = self.saved_ui_file
        shutil.rmtree(self.dir, ignore_errors=True)

    def _wait_for(self, condition, timeout=5.0, msg="条件始终不成立"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail(msg)

    def test_all_windows_share_one_interpreter(self):
        self.app._on_open_panel()
        self._wait_for(lambda: self.app._ui_windows == 1, msg="面板没建起来")
        self.app._on_open_settings()
        self._wait_for(lambda: self.app._ui_windows == 2, msg="设置没建起来")
        self.app._ui_post(self.app._detail_build, 1234,
                          ["a.exe", "1234", "1%", "1%", "0%", "0", "0"])
        self._wait_for(lambda: self.app._ui_windows == 3, msg="详情没建起来")

        self.assertEqual(self.fake_tk.Tk.call_count, 1,
                         "整个进程只允许存在一个 Tcl 解释器")
        self.assertEqual(len(self.windows), 3,
                         "面板/设置/详情都该是 root 的 Toplevel")
        masters = {c.args[0]
                   for c in self.fake_tk.Toplevel.call_args_list}
        self.assertEqual(masters, {self.host})
        self.assertEqual(self.host.mainloop.call_count, 1, "只跑一个 mainloop")
        self.assertTrue(self.app.panel_open)
        self.assertTrue(self.app._settings_open)

    def test_the_panel_remembers_the_topmost_switch(self):
        """置顶默认开, 但关掉之后要记住。

        全屏游戏时一个置顶小窗正好挡在画面中间,
        关掉了下次开面板又自动置顶的话, 这个开关等于没有。
        """
        ui_file = os.path.join(self.dir, "panel_ui.json")
        with open(ui_file, "w", encoding="utf-8") as f:
            json.dump({"geometry": "1040x470", "topmost": False}, f)

        self.app._on_open_panel()

        def built():
            # _ui_windows 在搭面板的入口就 +1 了, 等它等不完;
            # WM_DELETE_WINDOW 是最后才注册的, 它在就说明搭完了。
            return (len(self.windows) == 1
                    and any(c.args and c.args[0] == "WM_DELETE_WINDOW"
                            for c in self.windows[-1].protocol.call_args_list))

        self._wait_for(built, msg="面板没搭完")
        win = self.windows[-1]
        topmost = [c.args[1] for c in win.attributes.call_args_list
                   if c.args and c.args[0] == "-topmost"]
        self.assertEqual(topmost, [False],
                         "上次关掉的置顶, 开面板不该又强行打开")

        # 点一下开关再点一下: attributes 得跟着翻, 标签也得跟着改
        btn = [c for c in self.fake_tk.Button.call_args_list
               if str(c.kwargs.get("text", "")).startswith("置顶")][-1]
        self.assertEqual(btn.kwargs["text"], "置顶: 关")
        btn.kwargs["command"]()
        self.assertEqual(win.attributes.call_args_list[-1].args,
                         ("-topmost", True))
        btn.kwargs["command"]()
        self.assertEqual(win.attributes.call_args_list[-1].args,
                         ("-topmost", False))

        close = [c.args[1] for c in win.protocol.call_args_list
                 if c.args and c.args[0] == "WM_DELETE_WINDOW"][-1]
        close()
        with open(ui_file, encoding="utf-8") as f:
            self.assertFalse(json.load(f)["topmost"],
                             "关窗时要把当前置顶状态写回去")

    def test_panel_ui_is_written_atomically(self):
        u"""panel_ui.json 也得走原子写。

        直接 open(_, "w") + json.dump 和 config.json 旧违一样:
        写到一半被杀就留半截 JSON, 而这份文件跑的
        是靠近 WM_DELETE_WINDOW 的关窗路径 —— 关窗时被 taskkill
        的概率不低。读一方面已经具备摘要回退能力,
        但丢掉全部布局(位置/列宽/筛选词)仍然是真损失。
        """
        ui_file = os.path.join(self.dir, "panel_ui.json")
        saved_write = self.gt.write_json_dict
        self.addCleanup(setattr, self.gt, "write_json_dict", saved_write)
        seen = []

        def fake_write(path, data):
            seen.append((path, sorted(data)))
            return saved_write(path, data)

        self.gt.write_json_dict = fake_write
        self.app._on_open_panel()

        def built():
            return (len(self.windows) == 1
                    and any(c.args and c.args[0] == "WM_DELETE_WINDOW"
                            for c in self.windows[-1].protocol.call_args_list))

        self._wait_for(built, msg="面板没搭完")
        close = [c.args[1] for c in self.windows[-1].protocol.call_args_list
                 if c.args and c.args[0] == "WM_DELETE_WINDOW"][-1]
        close()
        self.assertEqual(len(seen), 1, "关窗要通过原子写落盘")
        self.assertEqual(seen[0][0], ui_file)
        for key in ("geometry", "topmost", "filter", "sort", "widths"):
            self.assertIn(key, seen[0][1])

    def test_the_panel_remembers_the_filter_and_the_sort_column(self):
        """筛选词和排序列也要记住。

        Web 面板的筛选词存在 localStorage 里, 托盘一关就忘的话,
        同一个动作在两个界面上付出不一样的代价。
        """
        ui_file = os.path.join(self.dir, "panel_ui.json")
        with open(ui_file, "w", encoding="utf-8") as f:
            json.dump({"geometry": "1040x470", "filter": "edge",
                       "sort": {"col": "pid", "desc": False}}, f)

        self.app._on_open_panel()

        def built():
            return (len(self.windows) == 1
                    and any(c.args and c.args[0] == "WM_DELETE_WINDOW"
                            for c in self.windows[-1].protocol.call_args_list))

        self._wait_for(built, msg="面板没搭完")
        # 第一帧就得带着筛选词刷, 不然开面板瞬间先闪一遍全量
        self.assertEqual(self.app.filter_text, "edge")
        self.assertEqual(self.app.sort_state,
                         {"col": "pid", "desc": False})
        fvar = self.fake_tk.StringVar.call_args_list[-1]
        self.assertEqual(fvar.kwargs.get("value",
                                         fvar.args[0] if fvar.args else None),
                         "edge", "筛选框要带着上次的词出场")

        self.app._set_filter("chrome")
        close = [c.args[1] for c in self.windows[-1].protocol.call_args_list
                 if c.args and c.args[0] == "WM_DELETE_WINDOW"][-1]
        close()
        with open(ui_file, encoding="utf-8") as f:
            ui = json.load(f)
        self.assertEqual(ui["filter"], "chrome", "关窗要把筛选词写回去")
        self.assertEqual(ui["sort"], {"col": "pid", "desc": False})

    def test_a_nondict_saved_layout_does_not_kill_the_panel(self):
        """panel_ui.json 存成 [] / "abc" 也是合法 JSON, 面板不能因此打不开。

        这文件是人也能改的。老代码 ui = json.load(f) 之后直接 ui.get(...),
        顶层不是对象就 AttributeError, 被 _panel_build 的总 except 吞掉 ——
        窗口建了一半扔下, 而且每次点"打开实时面板"都照样炸, 用户看到的是
        "菜单点了没反应"。
        """
        for junk in ("[]", '"abc"', "123", "null"):
            ui_file = os.path.join(self.dir, "panel_ui.json")
            with open(ui_file, "w", encoding="utf-8") as f:
                f.write(junk)
            self.app.panel_open = False
            self.app._on_open_panel()

            def built():
                return (len(self.windows) >= 1
                        and any(c.args and c.args[0] == "WM_DELETE_WINDOW"
                                for c in self.windows[-1].protocol
                                .call_args_list))

            self._wait_for(built, msg="面板被 panel_ui.json=%s 打死了" % junk)
            close = [c.args[1] for c in self.windows[-1].protocol
                     .call_args_list
                     if c.args and c.args[0] == "WM_DELETE_WINDOW"][-1]
            close()
            self._wait_for(lambda: self.app._ui_windows == 0,
                           msg="面板没能正常关掉")
            self.windows.clear()

    def test_a_garbage_saved_sort_falls_back_to_the_default(self):
        """panel_ui.json 是外部文件, 列名不合法不能带崩面板。"""
        ui_file = os.path.join(self.dir, "panel_ui.json")
        before = dict(self.app.sort_state)
        with open(ui_file, "w", encoding="utf-8") as f:
            json.dump({"geometry": "1x1", "sort": {"col": "rm -rf"}}, f)

        self.app._on_open_panel()

        def built():
            return (len(self.windows) == 1
                    and any(c.args and c.args[0] == "WM_DELETE_WINDOW"
                            for c in self.windows[-1].protocol.call_args_list))

        self._wait_for(built, msg="面板没搭完")
        self.assertEqual(self.app.sort_state, before,
                         "非法列名要退回默认排序, 不能照单全收")

    def test_clicking_a_column_header_resorts_right_away(self):
        """点列头要当场重排, 不能等下一轮 1 秒刷新。

        "复原排序"按钮是立即刷的, 表头回调却只改 sort_state: 同一种
        操作两种手感, 用户点完盯着表格的那一秒看到的还是旧顺序。
        测试里 Toplevel.after 不会被替身事件循环派发, 所以这一刻
        表格真被刷过, 只能是列头那一下自己触发的。
        """
        self.app._on_open_panel()

        def built():
            return (len(self.windows) == 1
                    and any(c.args and c.args[0] == "WM_DELETE_WINDOW"
                            for c in self.windows[-1].protocol.call_args_list))

        self._wait_for(built, msg="面板没搭完")
        tree = self.gt.ttk.Treeview.return_value
        cmds = {c.args[0]: c.kwargs["command"]
                for c in tree.heading.call_args_list
                if c.args and c.kwargs.get("command")}
        self.assertIn("pid", cmds, "表头没注册点击回调")
        before = tree.delete.call_count
        cmds["pid"]()
        self.assertEqual(self.app.sort_state["col"], "pid",
                         "点列头要切换排序列")
        self.assertGreater(tree.delete.call_count, before,
                           "点列头没有立即重排表格, 要等下一轮刷新")

    def test_requests_run_in_the_ui_thread(self):
        seen = []
        self.app._ui_post(lambda: seen.append(threading.current_thread()))
        self._wait_for(lambda: seen, msg="UI 线程没有执行请求")
        self.assertEqual(len(seen), 1)
        self.assertIsNot(seen[0], threading.current_thread(),
                         "跨线程递过来的请求要在 UI 线程里执行")
        self.assertIs(seen[0], self.app._ui_thread,
                      "请求必须落在持有解释器的那个线程上")

    def test_failed_build_releases_the_window_slot(self):
        self.fake_tk.Toplevel.side_effect = RuntimeError("起不来")
        self.app._on_open_panel()
        ran = []
        self.app._ui_post(lambda: ran.append(1))
        self._wait_for(lambda: ran and self.fake_tk.Tk.called,
                       msg="UI 线程没有跑完这批请求")
        self.assertEqual(self.app._ui_windows, 0, "建失败不该占着窗口计数")
        self.assertFalse(self.app.panel_open, "建失败要把打开标志复位")

    def test_closing_a_window_frees_the_slot(self):
        self.app._on_open_panel()
        self._wait_for(lambda: self.app._ui_windows == 1, msg="面板没建起来")
        window = self.windows[0]
        self._wait_for(lambda: window.protocol.called,
                       msg="面板没注册 WM_DELETE_WINDOW")
        self.app._ui_post(window.protocol.call_args.args[1])
        self._wait_for(lambda: self.app._ui_windows == 0,
                       msg="关闭窗口没有释放计数")
        self.assertFalse(self.app.panel_open)

    def test_detail_window_survives_a_real_process_handle(self):
        """详情窗拿真进程句柄换算启动时间, 不能被自身 except 吞掉。

        `dw` 数组下标取出来本来就是 int, 多包一层 `ctypes.c_ulonglong` 就
        没法做浮点除了 —— 真句柄一走到这里就 TypeError, 窗口建一半被扔下
        (计数退还, 屏幕上还留个孤儿窗)。mock 的 GetProcessTimes 只会回假值,
        抓不到这条, 所以这里拿当前进程的真 pid 跑。
        """
        pid = os.getpid()
        done = []
        self.app._ui_post(self.app._detail_build, pid,
                          ["python.exe", str(pid), "1%", "1%", "0%", "0", "0"])
        # 哨兵排在建窗后面: 它跑完说明整个 build 都跑完了
        self.app._ui_post(lambda: done.append(1))
        self._wait_for(lambda: done and self.app._ui_windows == 1,
                       msg="详情窗没建起来(疑似内部异常被吞)")

        labels = [c.kwargs["text"] for c in self.fake_tk.Label.call_args_list
                  if "text" in c.kwargs]
        stamps = [t for t in labels
                  if len(t) == 19 and t[4] == "-" and t[10] == " "]
        self.assertTrue(stamps, f"启动时间没能从真进程句柄算出来: {labels}")


    def test_detail_reads_the_clicked_row_and_flags_a_gone_pid(self):
        """详情窗每个数都得是点击那一行的同一刻, pid 走了还必须说一声。

        面板每秒刷一次, 双击落在两次刷新之间时进程可能正好退出、pid 被
        复用, 或者从"真在算"掉成噪声行。老代码 GPU% 读点击那一行、显存
        读当前快照, 同一个窗口里就能出现"45.2% 却是 0MB"; pid 没了也只会
        得到一排 0, 分不清是闲着还是已经走了。
        """
        saved = self.gt.pid_to_name
        self.gt.pid_to_name = lambda pid: ("gone.exe", r"D:\app\gone.exe")
        # 这一轮快照里已经没有 1234 了
        self.app.snapshot = {"pid_names": {}, "util_by_pid": {},
                             "gpu_all_by_pid": {}, "dgpu_util_by_pid": {},
                             "shared_by_pid": {}, "dedicated_by_pid": {}}
        try:
            vals = ["gone.exe", "1234", "45.2%", "45.2%", "0.0%",
                    "120", "40", ""]
            self.app._ui_post(self.app._detail_build, 1234, list(vals))
            done = []
            self.app._ui_post(lambda: done.append(1))
            self._wait_for(lambda: done and self.app._ui_windows == 1,
                           msg="详情窗没建起来(疑似内部异常被吞)")
        finally:
            self.gt.pid_to_name = saved

        labels = [c.kwargs["text"] for c in self.fake_tk.Label.call_args_list
                  if "text" in c.kwargs]
        self.assertIn("45.2% / 45.2% / 0.0%", labels, labels)
        self.assertIn("120 MiB / 40 MiB", labels,
                      f"显存读成了另一刻的快照, 不是点击那一行: {labels}")
        self.assertTrue(any("已不在本轮采样" in t for t in labels),
                        f"pid 走了却一个字都不说: {labels}")

    def test_the_detail_window_offers_escape_as_a_way_out(self):
        u"""详情窗也要认 Esc。

        面板那边 Esc 早就绑定上了; 详情窗只有一个"关闭"按钮, 键盘
        用户得把手挪回鼠标去点它 —— 而这个窗存在的全部理由就是
        让键盘用户少碰鼠标 (§115 之后回车进详情、Del 永不迁移)。
        """
        saved = self.gt.pid_to_name
        self.gt.pid_to_name = lambda pid: ("gone.exe", None)
        try:
            self.app._ui_post(self.app._detail_build, 1234,
                              ["gone.exe", "1234", "9%", "9%",
                               "0%", "0", "0", ""])
            done = []
            self.app._ui_post(lambda: done.append(1))
            self._wait_for(lambda: done and self.app._ui_windows == 1,
                           msg="详情窗没建起来(疑似内部异常被吞)")
        finally:
            self.gt.pid_to_name = saved
        keys = [c.args[0] for c in self.windows[-1].bind.call_args_list
                if c.args]
        self.assertIn("<Escape>", keys,
                      f"详情窗没给键盘留出口: {keys}")

    def test_detail_window_without_a_path_says_so_and_keeps_buttons_safe(self):
        """系统进程只有名字: 界面要写明白, 按钮也不能拿 None 去写注册表。"""
        saved = self.gt.pid_to_name
        self.gt.pid_to_name = lambda pid: ("System", None)
        try:
            self.app._ui_post(self.app._detail_build, 4,
                              ["System", "4", "0%", "0%", "0%", "0", "0"])
            done = []
            self.app._ui_post(lambda: done.append(1))
            self._wait_for(lambda: done and self.app._ui_windows == 1,
                           msg="详情窗没建起来(疑似内部异常被吞)")
        finally:
            self.gt.pid_to_name = saved

        labels = [c.kwargs["text"] for c in self.fake_tk.Label.call_args_list
                  if "text" in c.kwargs]
        self.assertIn("未知 (需要管理员权限)", labels)
        self.assertNotIn("None", labels, "不能把 None 直接摆到界面上")
        set_btn = [c for c in self.fake_tk.Button.call_args_list
                   if c.kwargs.get("text") == "立即设为独显"][-1]
        set_btn.kwargs["command"]()        # 以前这里 os.path.isfile(None) 会炸
        self.assertEqual(self.windows[-1].title.call_args[0][0],
                         "拿不到完整路径, 写不了 GPU 首选项")


    def test_summary_cards_shrink_together_in_a_narrow_window(self):
        '''六张汇总卡得能一起缩短, 否则窄窗口下右边几张被切到视口外。

        grid + uniform 只会分多余空间, 列的请求宽度有下限, 窗口一窄还是
        把右边的卡挤出视口; pack(expand=True) 才是一行等分。真机冒烟见
        .tmptest/smoke_panel6.py (500px 窗口下六张卡全部完整可见)。
        '''
        self.app._on_open_panel()
        self._wait_for(lambda: self.app._ui_windows == 1, msg="面板没建起来")
        window = self.windows[0]
        self._wait_for(lambda: window.protocol.called,
                       msg="面板没注册 WM_DELETE_WINDOW")
        packs = self.fake_tk.Frame.return_value.pack.call_args_list
        cards = [c for c in packs
                 if c.kwargs.get("side") == "left"
                 and c.kwargs.get("expand") is True]
        want = len(gm.panel_summary(self.app.snapshot or {}))
        self.assertEqual(len(cards), want,
                         "每张汇总卡都要 pack(expand=True) 才能一起缩短")

    def _open_settings_and_find_save(self):
        # _settings_open 在投递前就被置真, 等它等于不等; 
        # 要等 UI 线程真把窗口和保存键建出来
        before = len(self.windows)
        self.app._on_open_settings()
        self._wait_for(lambda: len(self.windows) > before,
                       msg="设置窗没建起来")
        self._wait_for(
            lambda: any(c.kwargs.get("command") is not None
                        for c in self.fake_tk.Button.call_args_list),
            msg="保存键没建起来")
        win = self.windows[-1]
        buttons = [c for c in self.fake_tk.Button.call_args_list
                   if c.kwargs.get("command") is not None]
        self.assertEqual(len(buttons), 1, "设置窗只该有一个带命令的按钮")
        return win, buttons[0].kwargs["command"]

    def _settings_vars(self, values):
        """按 add_entry 的顺序摆六格的值(采样周期在倒数第二格, 最后一格是 Web 端口)。"""
        vars_ = []
        def make(value=None, **_kw):
            var = mock.MagicMock(name="StringVar")
            var.get.return_value = value
            vars_.append(var)
            return var
        self.fake_tk.StringVar.side_effect = make
        # BooleanVar 也得给真值, 否则 json.dump 会撞上 MagicMock
        self.fake_tk.BooleanVar.side_effect = \
            lambda value=False, **_kw: mock.MagicMock(get=lambda: value)
        win, save = self._open_settings_and_find_save()
        self._wait_for(lambda: len(vars_) == 6,
                       msg="六格数字没建全")
        for var, value in zip(vars_, values):
            var.get.return_value = value
        return win, save

    def test_saving_a_new_web_port_says_a_restart_is_needed(self):
        u"""改了 Web 端口却说"热重载生效"是撒谎: 端口只在启动时注册一次。"""
        win, save = self._settings_vars(
            ["30", "1024", "40", "85", "2", "9999"])
        save()
        title = win.title.call_args.args[0]
        self.assertIn("需重启监控后生效", title,
                      f"端口改了就得说要重启, 实际说的是: {title}")

    def test_saving_the_same_web_port_still_promises_hot_reload(self):
        u"""端口没动就不能乱喊"要重启" —— 废话一样的提示等于没有提示。"""
        win, save = self._settings_vars(
            ["30", "1024", "40", "85", "2", "8787"])
        save()
        title = win.title.call_args.args[0]
        self.assertEqual(title, "已保存, 配置热重载生效")

    def test_a_bad_cell_and_a_new_port_are_both_reported(self):
        u"""同时填坏温度、又改了端口: 两件事都得说, 不能说一件瞒一件。"""
        win, save = self._settings_vars(
            ["30", "1024", "40", "abc", "2", "9999"])
        save()
        title = win.title.call_args.args[0]
        self.assertIn("温度提醒 °C 不是数字", title)
        self.assertIn("需重启监控后生效", title)

    def test_changing_the_sampling_interval_only_promises_hot_reload(self):
        u"""采样周期改就立即生效(下一轮就按新周期睡), 不能谎称要重启。

        web_port / hotkeys 启动时注册一次, 改它们才需要重启; interval_seconds 每轮都
        现读 cfg。这里钉死: 只改采样周期、端口没动时, 标题说的是
        "配置热重载生效", 而不是"需重启监控后生效"。
        """
        win, save = self._settings_vars(
            ["30", "1024", "40", "85", "2", "8787"])
        save()
        title = win.title.call_args.args[0]
        self.assertEqual(title, "已保存, 配置热重载生效")


class TrayPanelRows(unittest.TestCase):
    """面板的行排序 / 过滤 / 详情入口: 这里错一列, 用户看到的就是错的。"""

    def setUp(self):
        import gpu_tray
        self.tray = gpu_tray
        self.saved_pid_to_name = gpu_tray.pid_to_name
        self.app = object.__new__(gpu_tray.TrayApp)
        self.app.sort_state = {"col": "igpu", "desc": True}
        self.app.filter_text = ""
        self.app.lock = threading.Lock()
        self.app._name_cache = {}
        self.app.snapshot = None
        self.app.monitor_dead = False
        self.inserted = []
        self.posted = []

    def tearDown(self):
        self.tray.pid_to_name = self.saved_pid_to_name

    class _Tree:
        """只实现 _refresh_tree / _open_detail 用到的那几个方法。"""

        def __init__(self, sink, item="row0", values=()):
            self.sink = sink
            self._item = item
            self._values = values

        def delete(self, *_items):
            self.sink.append(("delete", _items))

        def insert(self, _parent, _pos, values=None, tags=None):
            self.sink.append(("insert", values))
            return self._item

        def get_children(self):
            return ()

        def identify_row(self, _y):
            return self._item

        def item(self, _iid, _what=None):
            return self._values

    class _Tip:
        def __init__(self):
            self.text = ""

        def config(self, text=None):
            self.text = text

    # ---- 排序 ----

    def test_same_column_toggles_and_new_column_resets(self):
        self.app._toggle_sort("igpu")          # 默认 igpu/降序 -> 翻升序
        self.assertEqual(self.app.sort_state,
                         {"col": "igpu", "desc": False})
        self.app._toggle_sort("name")          # 换列一律先降序
        self.assertEqual(self.app.sort_state, {"col": "name", "desc": True})
        self.app._toggle_sort("name")
        self.assertFalse(self.app.sort_state["desc"],
                         "再点同一列才翻方向")

    def test_numeric_columns_do_not_sort_as_strings(self):
        rows = [(1, ("a", 1, "9%", "9%", "9%", "1", "1", "")),
                (2, ("b", 2, "100%", "100%", "0%", "0", "0", ""))]
        self.app.sort_state = {"col": "gpu", "desc": False}
        out = self.app._sort_rows(rows)
        self.assertEqual([r[1][0] for r in out], ["a", "b"],
                         "数字列必须按数值排; 按字符串排会把 100% 排到 9% 前面")

    def test_sorting_and_the_detail_window_read_a_cell_the_same_way(self):
        u"""点表头排序与详情窗读数必须是同一个换算。

        以前排序自己写 `str(v).replace("%", "").replace(" MB", "")`，
        详情窗用 _row_num 的 rstrip("%") —— 同一列两种读法。列名改成
        "专用MiB" 之后, 万一哪一层把单位渲染进单元格, 老排序会把它读成
        0.0 整列沉底, 而详情窗还能读出真值。
        """
        rows = [(1, ("a", 1, "9.0%", "9.0%", "9.0%", "512 MiB",
                     "0", "")),
                (2, ("b", 2, "80.0%", "80.0%", "80.0%", "1024 MiB",
                     "0", "")),
                (3, ("c", 3, "1%", "1%", "1%", "?", "?", ""))]
        self.app.sort_state = {"col": "dmem", "desc": True}
        out = [r[1][0] for r in self.app._sort_rows(rows)]
        self.assertEqual(out, ["b", "a", "c"],
                         f"带单位的单元格必须按数值排, 坏的沉底: {out}")
        # 同一个单元格, 详情窗的读数和排序的读数必须一致
        self.assertEqual(self.tray._row_num(("a", "1024 MiB"), 1), 1024.0)
        self.assertEqual(self.tray._row_num(("a", "45.2%"), 1), 45.2)
        self.assertEqual(self.tray._row_num(("a", "120"), 1), 120.0)
        self.assertEqual(self.tray._row_num((), 3), 0.0,
                         "缺列既不能抛也不能当 120 用")

    def test_numbers_carrying_units_are_parsed(self):
        rows = [(1, ("a", 1, "1%", "1%", "1%", "10 MB", "10 MB", "")),
                (2, ("b", 2, "1%", "1%", "1%", "2 MB", "2 MB", ""))]
        self.app.sort_state = {"col": "dmem", "desc": True}
        out = self.app._sort_rows(rows)
        self.assertEqual([r[1][0] for r in out], ["a", "b"],
                         "带 MB 后缀的数字列也要按数值排")

    def test_unparsable_numbers_sink_to_the_bottom(self):
        rows = [(1, ("a", 1, "?", "?", "?", "?", "?", "")),
                (2, ("b", 2, "1%", "1%", "1%", "1", "1", "")),
                (3, ("c", 3, "0%", "0%", "0%", "0", "0", ""))]
        self.app.sort_state = {"col": "gpu", "desc": True}
        out = self.app._sort_rows(rows)
        self.assertEqual([r[1][0] for r in out], ["b", "c", "a"],
                         "解析不出数字的行必须排在 0 之后, 不能和 0 并列")

    def test_auto_order_unifies_the_three_surfaces(self):
        # row[0] 是 activity_key(GPU%、专用MB、共享MB 取最大);
        # "auto" 档和 --list / Web 面板同序, 并列按 pid 升序。
        rows = [
            (50.0, ("a.exe", 40, "50%", "50%", "0%", "0", "0", "")),
            (50.0, ("b.exe", 20, "50%", "50%", "0%", "0", "0", "")),
            (60.0, ("c.exe", 30, "60%", "0%", "60%", "0", "0", "")),
        ]
        self.app.sort_state = {"col": "auto", "desc": True}
        out = self.app._sort_rows(rows)
        self.assertEqual([r[1][1] for r in out], [30, 20, 40],
                         "自动档按活动量降序; 并列(40/20)要按 pid 升序")
        self.app.sort_state = {"col": "auto", "desc": False}
        out = self.app._sort_rows(rows)
        self.assertEqual([r[1][1] for r in out], [20, 40, 30],
                         "升序: 活动量升序, 并列仍按 pid 升序")

    def test_tied_rows_do_not_swap_places_between_refreshes(self):
        """主键并列时行序必须定死, 不能跟着 set 迭代顺序每秒换位。

        rows 是拿 set(pids) 迭代攒出来的: 同一份快照两次刷新,
        并列行(一堆 0.0% / 0MB)就能给出两种顺序 —— 表格在自己抖,
        用户刚想点的那一行跳到别处去了。
        """
        rows = [
            (5.0, ("b.exe", 11, "0%", "0%", "0%", "0", "0", "")),
            (5.0, ("a.exe", 22, "0%", "0%", "0%", "0", "0", "")),
            (5.0, ("c.exe", 33, "0%", "0%", "0%", "0", "0", "")),
        ]
        for desc in (True, False):
            self.app.sort_state = {"col": "gpu", "desc": desc}
            once = self.app._sort_rows(rows)
            twice = self.app._sort_rows(list(reversed(rows)))
            self.assertEqual([r[1][1] for r in once], [r[1][1] for r in twice],
                             f"desc={desc}: 同样的行, 两种传入顺序给出两种结果")
            self.assertEqual([r[1][1] for r in once], [11, 22, 33],
                             f"desc={desc}: 并列一律按 pid 升序收尾")

    def test_process_names_are_sorted_ignoring_case(self):
        """进程名列按大小写不敏感排, 和资源管理器一个口径。

        按 str() 排时大写码位全在小写前面: "Zebra.exe" 会排到
        "adc.exe" 前面, 同名的大小写变体(chrome.exe / Chrome.exe)
        也被拆到桌子两头, 一列名字读起来是跳的。
        """
        rows = [
            (0.0, ("chrome.exe", 2, "0%", "0%", "0%", "0", "0", "")),
            (0.0, ("Chrome.exe", 1, "0%", "0%", "0%", "0", "0", "")),
            (0.0, ("Zebra.exe", 3, "0%", "0%", "0%", "0", "0", "")),
            (0.0, ("adc.exe", 4, "0%", "0%", "0%", "0", "0", "")),
        ]
        self.app.sort_state = {"col": "name", "desc": False}
        out = self.app._sort_rows(rows)
        self.assertEqual([r[1][0].lower() for r in out],
                         ["adc.exe", "chrome.exe", "chrome.exe",
                          "zebra.exe"],
                         "名字列要按大小写不敏感排")
        self.assertEqual([r[1][1] for r in out], [4, 1, 2, 3],
                         "同名(忽略大小写)并列时按 pid 升序")

    def test_sort_button_text_nudges_back_to_the_default(self):
        # 默认档(auto)没有表头可点, 得靠按钮文案告诉用户怎么回到三面
        # 同序的活动量排序。
        f = self.tray.sort_button_text
        self.assertEqual(f({"col": "auto"}), "排序: 活动量(默认)")
        self.assertEqual(f({"col": "gpu"}), "复原到自动(GPU%)")
        self.assertEqual(f({"col": "name"}), "复原到自动(进程)")
        self.assertEqual(f({"col": "pref"}), "复原到自动(迁移状态)")

    def test_pause_button_text_reflects_the_paused_state(self):
        # 暂停后菜单要改口说"恢复": 一直写"暂停监控"会跟悬停的 [已暂停]
        # 对不上, 用户也不确定再点一下到底是暂停还是恢复。
        f = self.tray.pause_button_text
        self.assertEqual(f(False), "暂停监控")
        self.assertEqual(f(True), "恢复监控")

    # ---- 行过滤 / 条数上限 ----

    def _snapshot(self, pids):
        snap = {"util_by_pid": {}, "gpu_all_by_pid": {},
                "dgpu_util_by_pid": {}, "shared_by_pid": {},
                "dedicated_by_pid": {}, "igpu_luids": [0], "dgpu_luids": []}
        for pid, (gpu_all, mem_mb) in pids.items():
            snap["gpu_all_by_pid"][pid] = gpu_all
            snap["dedicated_by_pid"][pid] = int(mem_mb * 1024 * 1024)
        return snap

    def _refresh(self, snap):
        tree = self._Tree(self.inserted)
        tip = self._Tip()
        self.app._pid_name = lambda pid: f"p{pid}"
        self.app._pref_of = lambda _p: False
        self.app._gpu_name = lambda luid: f"gpu{luid}"
        self.app.snapshot = snap
        self.app._refresh_tree(tree, tip)
        return tree, tip

    def test_idle_processes_are_not_listed(self):
        snap = self._snapshot({1: (40.0, 0.0), 2: (0.0, 0.0),
                               3: (0.0, 3.0), 4: (0.05, 0.0),
                               5: (0.0, 0.5)})
        self._refresh(snap)
        names = [v[0] for kind, v in self.inserted if kind == "insert"]
        self.assertEqual(names, ["p1", "p3"],
                         "占用低于 0.1% 且显存不到 1MB 的行不该出现")

    def test_table_is_capped_at_thirty_rows(self):
        snap = self._snapshot({p: (1.0, 1.0) for p in range(100, 140)})
        self._refresh(snap)
        inserted = [v for kind, v in self.inserted if kind == "insert"]
        self.assertEqual(len(inserted), 30, "最多只画 30 行")

    def test_empty_snapshot_shows_the_placeholder_row(self):
        _, tip = self._refresh(self._snapshot({}))
        inserted = [v for kind, v in self.inserted if kind == "insert"]
        self.assertEqual(inserted, [("（当前无进程占用 GPU）", "-", "-", "-",
                                    "-", "-", "-", "-")])
        self.assertIn("核显: gpu0", tip.text)

    def test_the_panel_admits_it_has_not_sampled_yet(self):
        """启动头一秒里双击图标, 老代码给的是

        "当前无进程占用 GPU" —— 把"还没测"说成"测了是 0"。
        暂停在首轮之前时这句话会一直挂着, 用户看
        到的就是一叠谏报军情。
        """
        _, tip = self._refresh(None)
        inserted = [v for kind, v in self.inserted if kind == "insert"]
        self.assertEqual(inserted[0][0], gm.STARTING_ROW_TEXT)
        self.assertEqual(tip.text, "",
                         "还没快照就别接卡名了")

    def test_a_dead_monitor_is_not_reported_as_an_idle_machine(self):
        """监控线程死了更不能说"没人用 GPU"。

        启动就崩的话快照一直是 None, 面板会永远挂
        "当前无进程占用 GPU" —— 而托盘背书都已经写
        "GPU 监控已停止", 两处当着用户的面打架。
        """
        self.app.monitor_dead = True
        _, _tip = self._refresh(None)
        inserted = [v for kind, v in self.inserted if kind == "insert"]
        self.assertEqual(inserted[0][0], gm.STOPPED_ROW_TEXT)

    def test_the_tip_stops_promising_a_refresh_once_the_monitor_is_dead(self):
        """监控死了以后表格停在最后一份快照上,

        提示行再写"刷新: 1 秒"就是在承诺一件不会再发生的事。
        """
        self.app.monitor_dead = True
        _tree, tip = self._refresh(self._snapshot({1: (40.0, 0.0)}))
        self.assertIn("监控已停止", tip.text)
        self.assertNotIn("刷新: 1 秒", tip.text)

    def test_the_tip_still_counts_the_refresh_while_the_monitor_lives(self):
        """上一条的对照: 活着的时候该说的刷新节奏得说。"""
        _tree, tip = self._refresh(self._snapshot({1: (40.0, 0.0)}))
        self.assertIn("刷新: 1 秒", tip.text)

    # ---- 双击行进详情 ----

    def test_clicking_a_row_without_a_numeric_pid_opens_nothing(self):
        tree = self._Tree(self.inserted, values=("p1", "abc", "1%", "1%",
                                                 "0%", "0", "0", ""))
        self.app._ui_post = lambda fn, *a: self.posted.append((fn, a))
        self.app._open_detail(tree, type("E", (), {"y": 1})())
        self.assertEqual(self.posted, [], "PID 那列不是数字就不该弹详情")

    def test_percent_columns_keep_one_decimal_like_the_cli(self):
        self._refresh(self._snapshot({1: (0.4, 0.0)}))
        rows = [v for kind, v in self.inserted if kind == "insert"]
        self.assertEqual(rows[0][2], "0.4%",
                         "取整会把 0.4% 显示成 0%, 而 --list 显示 0.4%")

    def test_process_without_a_path_is_not_queried_for_a_preference(self):
        """完整路径拿不到时, 别拿 None 当注册表键去查。"""
        self.app._pid_name = lambda pid: "mystery.exe"
        asked = []
        self.app._pref_of = lambda p: asked.append(p) or False
        self.tray.pid_to_name = lambda pid: ("mystery.exe", None)
        self.app._gpu_name = lambda luid: "gpu0"
        # 不能走 _refresh: 它会把 _pref_of 盖成空操作, 那就什么都测不到
        tree = self._Tree(self.inserted)
        self.app.snapshot = self._snapshot({1: (40.0, 0.0)})
        self.app._refresh_tree(tree, self._Tip())
        self.assertEqual(asked, [], "没有完整路径就没有键可查")
        inserted = [v for kind, v in self.inserted if kind == "insert"]
        self.assertEqual(inserted[0][7], "", "连路径都没有, 谈不上已设独显")

    def test_clicking_a_row_posts_the_detail_request(self):
        vals = ("p1", "123", "1%", "1%", "0%", "0", "0", "")
        tree = self._Tree(self.inserted, values=vals)
        self.app._ui_post = lambda fn, *a: self.posted.append((fn, a))
        self.app._open_detail(tree, type("E", (), {"y": 1})())
        self.assertEqual(len(self.posted), 1)
        fn, args = self.posted[0]
        self.assertEqual(fn, self.app._detail_build,
                         "必须把点击的那一行交给详情窗, 不是别的回调")
        self.assertEqual(args, (123, list(vals)))

    class _LiveTree:
        u"""选中项要跨刷新存活, 就得有一张真的 item -> values 表。

        _Tree 的 insert 只往 sink 里记, 不长子行; 它的 item() 也只认
        一个固定 item。_reselect_pid 要按 PID 找回那一行, 这里把
        Treeview 相关的那几个方法按真语义实现一遍。
        """

        def __init__(self):
            self.rows = []
            self._sel = ()
            self.focused = None
            self.seen = []
            self._n = 0

        def delete(self, *_items):
            # 真 Tk 里选中项被删掉就没了; 这里必须跟着清, 否则替身会
            # 抱住一个已经不存在的 item, 把"选中项跨刷新存活"测成假的。
            gone = set(_items) if _items else {i for i, _v in self.rows}
            self.rows = [r for r in self.rows if r[0] not in gone]
            self._sel = tuple(i for i in self._sel if i not in gone)

        def insert(self, _parent, _pos, values=None, tags=None):
            self._n += 1
            iid = "i%d" % self._n
            self.rows.append((iid, values))
            return iid

        def get_children(self):
            return tuple(i for i, _v in self.rows)

        def item(self, iid, _what=None):
            for i, v in self.rows:
                if i == iid:
                    return v
            return ()

        def selection(self):
            return self._sel

        def selection_set(self, iid):
            self._sel = (iid,)

        def focus(self, iid):
            self.focused = iid

        def see(self, iid):
            self.seen.append(iid)

    def _render(self, tree, snap, count=None):
        u"""往同一棵树里灌一份快照 (面板每秒做的就是这件事)。"""
        self.app._pid_name = lambda pid: "p%d" % pid
        self.app._pref_of = lambda _p: False
        self.app._gpu_name = lambda luid: "gpu%d" % luid
        self.app.snapshot = snap
        self.app._refresh_tree(tree, self._Tip(), count=count)

    def _row_of(self, tree, pid):
        for iid in tree.get_children():
            if tree.item(iid)[1] == pid:
                return iid
        return None


    def test_the_selection_survives_the_next_refresh(self):
        u"""刷新会 delete + insert 一遍, 选中项不许跟着没。

        这是键盘导航能不能用的前提: 上下键选中的行每秒被踢回起点,
        用户就只剩鼠标一条路。按 PID 认行而不按行号认 —— 排序一变
        行号全变, PID 才是用户心里盯着的那个东西。
        """
        tree = self._LiveTree()
        self._render(tree, self._snapshot({111: (40.0, 0.0),
                                           222: (5.0, 0.0)}))
        first = self._row_of(tree, 222)
        self.assertIsNotNone(first, "先把 222 那一行选上")
        tree.selection_set(first)

        self._render(tree, self._snapshot({111: (41.0, 0.0),
                                           222: (6.0, 0.0)}))

        again = self._row_of(tree, 222)
        self.assertNotEqual(again, first,
                            "每次刷新都该是新的 item, 否则这条测的是恒等式")
        self.assertEqual(tree.selection(), (again,),
                         "每秒重渲染之后, 选中项还在同一个进程那一行")
        self.assertEqual(tree.focused, again, "焦点环要跟着选中项走")
        self.assertIn(again, tree.seen, "选中行得滚进可视区")

    def test_a_refresh_with_nothing_selected_selects_nothing(self):
        u"""一次都没选中过就不要自作主张高亮一行。

        这是刻意的选择: 常驻光标(htop 那样)会让一个"只打算瞄一眼"
        的监控面板始终有一条蓝杠。方向键的入口由 _seed_selection 负责,
        不在这里抢先。
        """
        tree = self._LiveTree()
        self._render(tree, self._snapshot({111: (40.0, 0.0)}))
        self.assertEqual(tree.selection(), (),
                         "没人点过就该是一张干净的表")

    def test_a_process_that_disappears_does_not_leak_its_selection(self):
        u"""选中的进程退出了, 不许顺手选中别的行。"""
        tree = self._LiveTree()
        self._render(tree, self._snapshot({111: (40.0, 0.0)}))
        tree.selection_set(self._row_of(tree, 111))
        self._render(tree, self._snapshot({222: (40.0, 0.0)}))
        self.assertEqual(tree.selection(), (),
                         "进程没了就什么都不选, 不能冒名选中别人")

    def test_the_status_line_reports_the_session_counters(self):
        u"""底部那一行会话累计: 轮数 / 命中阈值 / 已迁移。

        只报"刷新: 1 秒"的话, 监控在不在干活只能靠猜。参照 btop /
        bottom 把这类累计数摆到台面上。
        """
        tree = self._LiveTree()
        snap = dict(self._snapshot({111: (40.0, 0.0)}))
        snap.update({"rounds": 7, "hits": 3, "migrations": 1})
        count = self._Tip()
        self._render(tree, snap, count=count)
        self.assertIn("采样 7 轮", count.text)
        self.assertIn("命中阈值 3 次", count.text)
        self.assertIn("已迁移 1 个", count.text)

    def test_the_counter_line_survives_a_snapshot_without_the_keys(self):
        u"""没有这三个键的快照也要能渲染, 不能让刷新链断掉。

        _safe_refresh 的规矩: 一次异常就少刷新一帧, 而计数器是锦上
        添花, 不值得为它把整行状态行赔进去。
        """
        tree = self._LiveTree()
        count = self._Tip()
        self._render(tree, self._snapshot({111: (40.0, 0.0)}),
                      count=count)
        self.assertIn("采样 0 轮", count.text)

    def test_the_counter_line_stays_quiet_before_the_first_sample(self):
        u"""一份快照都还没有时要说"还没采到", 不要报 0 轮假装采过。"""
        tree = self._LiveTree()
        count = self._Tip()
        self._render(tree, None, count=count)
        self.assertIn("还没有采到数据", count.text)


    def test_the_placeholder_row_is_not_a_process(self):
        u"""占位行的 PID 列是 "-", 不是进程。

        键盘把 _open_detail / 永不迁移接到选中项上以后, 这条防线就是
        最后一道: 拿不到真实 PID 就什么都不许做, 否则回车会拿 "-" 去
        建详情窗, Del 会把 "-" 写进 exclude_processes。
        """
        empty = ("还没有采样到数据", "-", "-", "-", "-", "-", "-", "-")
        for vals in (empty, (), None, ("x", None), ("x", "12a"),
                     ("x", 12.5), ("x", "")):
            self.assertIsNone(self.tray._row_pid(vals),
                              "这一行不该被当成有 PID 的进程")
        self.assertEqual(self.tray._row_pid(("p1", 123, "1%", "1%",
                                             "0%", "0", "0", "")), 123)
        self.assertEqual(self.tray._row_pid(("p1", "123", "1%", "1%",
                                             "0%", "0", "0", "")), 123,
                         "渲染好的表里 PID 列可能是字符串")


    def test_settings_num_falls_back_to_the_effective_value(self):
        u"""填不是数字时, 存回去的必须是当前生效值。

        旧代码的兜底是写死的 20/1024/40/85/0:
        threshold_percent 默认早就是 50, 兜底却还写 20
        —— "没配这项的用户填错一次"会被存成一个
        和输入框里显示的生效值不一样的数字。
        """
        class _V:
            def __init__(self, v):
                self._v = v

            def get(self):
                return self._v

        cfg = dict(gm.DEFAULT_CONFIG)
        got = self.tray.TrayApp._settings_num(
            _V("很高"), cfg, "threshold_percent", float)
        self.assertEqual(got, gm.DEFAULT_CONFIG["threshold_percent"])
        self.assertEqual(
            self.tray.TrayApp._settings_num(_V("33"), cfg,
                                            "threshold_percent", float),
            33.0)
        self.assertEqual(
            self.tray.TrayApp._settings_num(_V("8788"), cfg,
                                            "web_port", int), 8788)

    def test_never_migrate_does_not_claim_success_on_write_failure(self):
        u"""写入失败时不许还提示"已加入" —— 那句是假的。

        config.json 解析不了/只读时 write_json_dict 返回
        False, 旧代码照样弹"已加入, 立即生效":
        用户以为排除了, 下一轮监控照旧把进程判成候选。
        """
        self.app.config_path = "/x/config.json"
        saved = self.tray.save_exclude_process
        self.tray.save_exclude_process = lambda path, name: False
        tip = self._Tip()
        try:
            self.app._never_migrate("game.exe", tip)
        finally:
            self.tray.save_exclude_process = saved
        self.assertNotIn("已加入", tip.text)
        self.assertIn("失败", tip.text)

    def test_never_migrate_confirms_with_the_written_state(self):
        self.app.config_path = "/x/config.json"
        saved = self.tray.save_exclude_process
        calls = []

        def fake(path, name):
            calls.append((path, name))
            return True

        self.tray.save_exclude_process = fake
        tip = self._Tip()
        try:
            self.app._never_migrate("game.exe", tip)
        finally:
            self.tray.save_exclude_process = saved
        self.assertEqual(calls, [("/x/config.json", "game.exe")],
                         "写的就是当前配置路径和进程名")
        self.assertIn("已把 game.exe 加入", tip.text)


class TrayPanelKeyboardNav(unittest.TestCase):
    u"""面板的键盘导航: 方向键选行 / 回车详情 / Del 永不迁移 / / 回筛选框。

    GUI 真实按键没法在无头环境里发, 但"绑定有没有挂上、挂上去的那个
    回调拿当前选中项做了什么"是可以钉死的。Tk 的键位派发交给探测脚本
    .tmptest/probe_tk.py 在真窗口里验(选中集为空时按 Down 什么都不会
    发生, 已经选中的情况下交给 ttk 自己的绑定)。
    """

    def setUp(self):
        self.gt = gpu_tray
        self.saved_tk, self.saved_ttk = gpu_tray.tk, gpu_tray.ttk
        self.saved_excl = gpu_tray.save_exclude_process
        self.saved_ui = gpu_tray._UI_FILE
        self.saved_pid_to_name = gpu_tray.pid_to_name
        self.fake_tk = mock.MagicMock(name="tk")
        self.host = self.fake_tk.Tk.return_value
        self.tree = mock.MagicMock(name="treeview")
        self.fake_ttk = mock.MagicMock(name="ttk")
        self.fake_ttk.Treeview.return_value = self.tree
        gpu_tray.tk = self.fake_tk
        gpu_tray.ttk = self.fake_ttk
        self.tree.column.return_value = 120
        self.tree.selection.return_value = ()
        gpu_tray.pid_to_name = lambda pid: (f"p{pid}", None)
        self.dir = tempfile.mkdtemp(prefix="gpumig-keynav-")
        self.path = os.path.join(self.dir, "config.json")
        gpu_tray._UI_FILE = os.path.join(self.dir, "panel_ui.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"threshold_percent": 30}, f)
        self.app = gpu_tray.TrayApp(self.path)
        self.app.monitor_dead = False
        self.app._ui_root = self.host
        self.app._ui_windows = 0
        self.app.panel_open = True
        # snapshot 为 None 时 _refresh_tree 会整段跳过底部状态行,
        # 键盘提示就没有机会渲染出来
        self.app.snapshot = {"util_by_pid": {}, "gpu_all_by_pid": {},
                             "dgpu_util_by_pid": {}, "shared_by_pid": {},
                             "dedicated_by_pid": {}, "igpu_luids": [0],
                             "dgpu_luids": []}
        self.app._gpu_name = lambda luid: "核显"
        self.written = []

        def remember(_path, name):
            self.written.append(name)
            return True

        gpu_tray.save_exclude_process = remember
        self.posted = []
        self.app._ui_post = lambda fn, *a: self.posted.append((fn, a))

    def tearDown(self):
        self.gt.tk, self.gt.ttk = self.saved_tk, self.saved_ttk
        self.gt.save_exclude_process = self.saved_excl
        self.gt._UI_FILE = self.saved_ui
        self.gt.pid_to_name = self.saved_pid_to_name
        shutil.rmtree(self.dir, ignore_errors=True)

    def _build(self):
        self.app._panel_build()
        return self.fake_tk.Toplevel.return_value, self.tree

    def _handler(self, sequence, widget=None):
        widget = self.tree if widget is None else widget
        for call in widget.bind.call_args_list:
            if call.args and call.args[0] == sequence:
                return call.args[1]
        self.fail("没有绑 " + sequence)


    def test_the_panel_opens_with_the_keyboard_on_the_table(self):
        u"""一开面板就让表格拿焦点。

        焦点停在筛选框的话, 第一次按方向键只会挪输入光标, 用户会直接
        得出"这面板没有键盘支持"的结论。
        """
        _window, tree = self._build()
        self.assertTrue(tree.focus_set.called,
                        "面板搭完要把键盘焦点交给表格")

    def test_slash_focuses_the_filter_entry(self):
        u"""/ 是筛选框的快捷键, 和 Ctrl-F 同一个去处 (Web 面板的老约定)。

        已经停在输入框里时不能再抢一次: 用户正打字, 抢焦点会把光标
        从半截进程名后面踢走。
        """
        window = self.fake_tk.Toplevel.return_value
        self._build()
        entry = self.fake_tk.Entry.return_value
        slash = self._handler("<slash>", window)
        slash()
        entry.focus_set.assert_called_once_with()
        entry.focus_get.return_value = entry
        slash()
        self.assertEqual(entry.focus_set.call_count, 1,
                         "已经在输入框里就不该再抢一次焦点")

    def test_the_first_arrow_key_drops_a_cursor_instead_of_doing_nothing(self):
        u"""空选中态下按 Down 必须落到第一行。

        真 Tk 实测: 选中集为空时 Down 什么都不做(见 .tmptest/probe_tk.py),
        也就是说键盘用户必须先拿鼠标点一行, 方向键才认。htop / nvtop
        这类工具不是这样, 它们永远有个光标。
        """
        _window, tree = self._build()
        down = self._handler("<Down>")
        tree.selection.return_value = ()
        tree.get_children.return_value = ("i0", "i1", "i2")
        down()
        tree.selection_set.assert_called_once_with("i0")
        self.assertEqual(tree.focus.call_args.args, ("i0",))
        self.assertEqual(tree.see.call_args.args, ("i0",))

    def test_the_first_up_arrow_drops_a_cursor_on_the_last_row(self):
        _window, tree = self._build()
        up = self._handler("<Up>")
        tree.selection.return_value = ()
        tree.get_children.return_value = ("i0", "i1", "i2")
        up()
        tree.selection_set.assert_called_once_with("i2")

    def test_an_existing_selection_is_left_to_the_widgets_own_binding(self):
        u"""已经有选中项时, 上下键归 ttk.Treeview 自己的绑定管。

        这里抢过来做就等于把方向键从"移动一格"改成"跳到首行/末行",
        所以播种函数必须在已有选中项时原样返回 None(不 break)。
        """
        _window, tree = self._build()
        down = self._handler("<Down>")
        tree.selection.return_value = ("i1",)
        self.assertIsNone(down())
        tree.selection_set.assert_not_called()

    def test_an_empty_table_does_not_break_the_arrow_keys(self):
        u"""没有数据(还没采样到 / 监控已死)时按方向键不许炸。

        get_children() 是空 tuple, 播种直接收手; 事件链不能断。
        """
        _window, tree = self._build()
        tree.selection.return_value = ()
        tree.get_children.return_value = ()
        self._handler("<Down>")()
        tree.selection_set.assert_not_called()

    def test_enter_opens_the_detail_of_the_selected_row(self):
        u"""回车 = 双击的键盘版, 走的是同一条 _ui_post 路径。"""
        _window, tree = self._build()
        vals = ("p1", "123", "1%", "1%", "0%", "0", "0", "")
        tree.selection.return_value = ("i0",)
        tree.item.return_value = vals
        self._handler("<Return>")()
        self.assertEqual(len(self.posted), 1)
        fn, args = self.posted[0]
        self.assertEqual(fn, self.app._detail_build,
                         "回车必须把选中行交给详情窗, 不是别的回调")
        self.assertEqual(args, (123, list(vals)))

    def test_enter_is_also_bound_on_the_window(self):
        u"""焦点万一不在表格上, 窗口级的 Return 也还能用。"""
        window, _tree = self._build()
        self.tree.selection.return_value = ("i0",)
        self.tree.item.return_value = ("p1", "9", "1%", "1%",
                                        "0%", "0", "0", "")
        self._handler("<Return>", window)()
        self.assertEqual([a[1][0] for a in self.posted], [9],
                         "窗口级回车要把选中那一行的 PID 交给详情窗")

    def test_enter_on_the_placeholder_row_opens_nothing(self):
        u"""占位行的 PID 列是 "-", 不是进程。

        以前 _open_detail 只认 event; 键盘进来以后这条防线不能没有,
        否则回车会拿 "-" 去建详情窗。
        """
        _window, tree = self._build()
        tree.selection.return_value = ("i0",)
        tree.item.return_value = ("还没有采样到数据", "-", "-", "-",
                                  "-", "-", "-", "-")
        self._handler("<Return>")()
        self.assertEqual(self.posted, [])

    def test_delete_never_migrates_the_selected_row(self):
        u"""Del = 右键菜单的键盘版, 写的是同一份 config.json。"""
        _window, tree = self._build()
        tree.selection.return_value = ("i0",)
        tree.item.return_value = ("game.exe", "55", "1%", "1%",
                                  "0%", "0", "0", "")
        self._handler("<Delete>")()
        self.assertEqual(self.written, ["game.exe"])

    def test_delete_on_the_placeholder_row_writes_nothing(self):
        u"""占位行不能进排除名单。

        右键路径以前也会把 "-" 写进 exclude_processes: 名单里多一个
        谁都匹配不上的名字, 用户看不出异常, 设置窗口里就躺着这一行。
        """
        _window, tree = self._build()
        tree.selection.return_value = ("i0",)
        tree.item.return_value = ("还没有采样到数据", "-", "-", "-",
                                  "-", "-", "-", "-")
        self._handler("<Delete>")()
        self.assertEqual(self.written, [])

    def test_escape_hands_the_keyboard_back_to_the_table(self):
        u"""Esc 清空筛选词之后要把键盘还给表格。

        不还的话, 用户按完 Esc 按方向键, 动的还是输入框里的光标,
        表格一动不动 —— 键盘导航在最常用的那个出口上断掉,
        而且界面上看不出是为什么。
        """
        window = self.fake_tk.Toplevel.return_value
        self._build()
        self.tree.focus_set.reset_mock()
        fvar = self.fake_tk.StringVar.return_value
        fvar.get.return_value = "chrome"
        escape = self._handler("<Escape>", window)
        escape()
        fvar.set.assert_called_once_with("")
        self.assertTrue(self.tree.focus_set.called,
                        "Esc 清完筛选要把键盘还给表格")
        self.assertEqual(escape(), "break")

    def test_escape_with_an_empty_filter_still_closes_the_window(self):
        u"""对照: 筛选词已经空了, Esc 就该关窗, 不该去抢表格焦点。"""
        window = self.fake_tk.Toplevel.return_value
        self._build()
        self.tree.focus_set.reset_mock()
        # on_close 会把替身 Toplevel 的 geometry() 拿去 JSON 序列化, 那是
        # 替身的形状问题不是被测行为; 拦住写盘只看标志位
        saved_write = gpu_tray.write_json_dict
        gpu_tray.write_json_dict = lambda path, data: True
        try:
            fvar = self.fake_tk.StringVar.return_value
            fvar.get.return_value = ""
            escape = self._handler("<Escape>", window)
            escape()
        finally:
            gpu_tray.write_json_dict = saved_write
        fvar.set.assert_not_called()
        self.assertFalse(self.tree.focus_set.called,
                         "都要关窗了, 别再动焦点")
        self.assertFalse(self.app.panel_open, "Esc 空筛选时要关窗")

    def test_the_sort_button_explains_the_keyboard_shortcuts(self):
        u"""排序入口要找得到: 快捷键挂在"复原到自动"按钮的悬停上。

        底部状态行已经很长, 再塞两个键会被窗口右沿切掉, 等于没说;
        用户找排序入口时鼠标本来就会划过那个按钮。
        """
        self.app.snapshot = None
        tips = []
        saved = gpu_tray._bind_tip
        gpu_tray._bind_tip = lambda w, t: tips.append(t)
        try:
            self._build()
        finally:
            gpu_tray._bind_tip = saved
        self.assertTrue(any("按 s" in t for t in tips),
                        f"排序快捷键没被交代: {tips}")

    def test_the_status_line_announces_the_keyboard_shortcuts(self):
        u"""快捷键不说出来就等于没有。

        底部那一行是面板里唯一常驻的状态说明; 只在那里写"右键…"的话,
        键盘用户永远发现不了这些键。
        """
        _window, _tree = self._build()
        texts = " ".join(
            str(c) for c in self.fake_tk.Label.return_value.config
            .call_args_list)
        for word in ("回车", "Del", "↑↓"):
            self.assertIn(word, texts, "底部状态行没交代 " + word)


class ColumnLabelsSingleSource(unittest.TestCase):
    u"""列名只有一份, 三个界面共用一个单位口径。

    以前托盘自己抄一份列名字典、Web 在 f-string 里写字面量、CLI 又抄一遍,
    改列名要同时记得三个地方 —— 漏一处就是"同一份数据三张脸"。更糟的是
    显存两列标着 MB, 而数字是 字节/1048576 得出来的 MiB, 差 4.86%。
    nvtop / GPU-Z 都老实写 MiB, 这里跟着老实的那一边。
    """

    def test_the_tray_buys_column_names_from_the_same_place(self):
        u"""托盘的 _COL_LABELS 必须是 gpu_monitor 那份对象本身。

        相等不够: 相等的两份字典还是两份, 下一次照样会漏改一处。
        """
        self.assertIs(gpu_tray._COL_LABELS, gm.COL_LABELS,
                         "托盘不许再自己抄一份列名")
        self.assertEqual(list(gpu_tray._COL_LABELS),
                         ["name", "pid", "gpu", "igpu", "dgpu",
                          "dmem", "smem", "pref"])

    def test_the_two_memory_columns_say_mib_not_mb(self):
        u"""显存列的单位名必须和除数的口径一致。

        代码里是 dedicated / 1048576, 得出来的是 MiB。标成 MB 的话,
        8 GiB 的独显会显示成 8192 MB, 和规格书、和 GPU-Z 都对不上。
        """
        self.assertTrue(gm.COL_LABELS["dmem"].endswith("MiB"),
                        gm.COL_LABELS["dmem"])
        self.assertTrue(gm.COL_LABELS["smem"].endswith("MiB"),
                        gm.COL_LABELS["smem"])
        for tip in ("dmem", "smem"):
            self.assertIn("MiB", gm._HDR_TIPS[tip],
                          "悬停解释也要把自己说的单位讲对")
            self.assertNotIn("单位 MB", gm._HDR_TIPS[tip])

    def test_the_web_header_carries_the_table_and_the_right_unit(self):
        u"""Web 表头从 COL_LABELS 取, 并且两列显存写的是 MiB。"""
        html = gm.panel_html(ModeDisplay.SNAP, {})
        for col in gm.COL_LABELS:
            # name 那一格的 <th> 还带 class=n, 所以只钉 title 与列名
            self.assertIn("title=" + chr(39) + gm._HDR_TIPS[col] + chr(39)
                          + ">" + gm.COL_LABELS[col] + "</th>", html)
        self.assertIn(">专用MiB</th>", html)
        self.assertNotIn(">专用MB</th>", html,
                         "MB 是不实的单位名, 别再回去")

    def test_the_cli_header_also_reads_the_table(self):
        u"""--list 的表头也不许再写字面量。

        扫源码而不是真跑一遍: _list_processes 要真实 PDH 数据才出表,
        这里只钉死"表头从 COL_LABELS 取"这件事本身。
        """
        source = inspect.getsource(gm.cmd_list)
        for col in ("dmem", "smem"):
            self.assertIn("COL_LABELS[" + chr(34) + col + chr(34) + "]",
                          source,
                          col + " 列表头没走 COL_LABELS")
        self.assertNotIn("专用MB", source)
        self.assertNotIn("共享MB", source)

class SessionCounters(unittest.TestCase):
    u"""底部那行会话累计: 轮数 / 命中阈值 / 已迁移。

    参照 btop / bottom: 这类工具把"程序到底在干什么事"的累计数摆在台面上。
    只报"刷新: 1 秒"的话, 监控是不是真在干活只能靠猜 ——
    "0 轮"和"3000 轮 0 命中"是两个完全不同的状态,
    而老界面上这两句话长得一模一样。
    """

    def _run(self, snap, cycles, overrides):
        seen = []
        harness = SnapshotContract()
        harness.setUp()
        try:
            harness.run_monitor(snap, cycles=cycles,
                              cfg_overrides=overrides,
                              hooks={"on_sample":
                                     lambda s: seen.append(dict(s))})
        finally:
            harness.tearDown()
        return seen

    def test_counters_are_cumulative_not_instant(self):
        u"""计数是单调不降的累计值, 不是瞬时值。

        写成瞬时值的话, 一轮命中 3 个、下一轮命中 0 个, 用户看到的
        就是"3 次"跳成"0 次" —— 像在被清零, 反而看不出趋势。
        """
        seen = self._run([{100: 90.0, 102: 80.0},
                         {100: 70.0, 102: 60.0}], 2,
                        {"sustain_samples": 99})
        rounds = [s["rounds"] for s in seen]
        self.assertEqual(len(rounds), 2)
        self.assertTrue(all(b >= a for a, b in zip(rounds, rounds[1:])),
                        f"轮数必须单调不降: {rounds}")

    def test_hot_candidates_are_counted_as_hits(self):
        u"""抓到超标候选就计数。

        select_hot_candidates 已经过滤掉排除项, 所以这里数的是
        "真的候选出来几个", 排除名单挡住的不算漏报。
        """
        # 102 也在 PID_NAMES 里: 代不到名字的 pid 拿不到完整路径,
        # select_hot_candidates 会把它们整行丢掉, 那测的就不是计数了
        # 一轮采样里两个进程都在: run_monitor 的 samples 是"每轮一个 dict",
        # 传 list of dict 会被当成多轮, 而不是一轮里的多个进程
        seen = self._run([{100: 90.0, 102: 80.0}], 1,
                        {"sustain_samples": 99})
        self.assertEqual(seen[-1]["hits"], 2,
                         "两个超标进程各算一次命中")

    def test_a_successful_migration_bumps_the_counter(self):
        u"""只有写注册表 + 回读确认都通过了才算迁移成功。

        写失败、回读不符都不该计数 —— 报了不存在的"已迁移"比不报
        更糟: 用户会按这个数去判断要不要重启进程。

        "已迁移"比"命中阈值"晚一个采样周期才出现在快照里, 这是有意的:
        确认弹窗可能阻塞几十秒(MessageBoxTimeoutW 的 120s 宽限),
        把迁移挪到快照前面等于让整个面板等用户点确认。累计数晚 5 秒
        无所谓, 面板卡住不行。
        """
        seen = self._run([{100: 90.0}, {100: 0.0}], 2,
                        {"sustain_samples": 1})
        self.assertEqual(seen[0]["migrations"], 0,
                         "迁移发生在快照之后, 同一轮看不到")
        self.assertEqual(seen[-1]["migrations"], 1,
                         "下一轮的累计里必须出现这次迁移")

    def test_the_web_page_shows_the_counters(self):
        u"""Web 面板也要有这一行, 并且进 PARTS 局部刷新。"""
        snap = dict(ModeDisplay.SNAP)
        snap.update({"rounds": 12, "hits": 3, "migrations": 1})
        html = gm.panel_html(snap, {})
        self.assertIn("id=counters", html)
        self.assertIn("采样 12 轮", html)
        self.assertIn("命中阈值 3 次", html)
        self.assertIn("已迁移 1 个", html)
        self.assertIn('counters', gm._PANEL_SCRIPT,
                      "不出现在 PARTS 里就永远停在首次渲染的值")

    def test_an_empty_snapshot_does_not_crash_the_counter_line(self):
        u"""还没采到一轮时也要能显示出这行, 而不是 KeyError。"""
        html = gm.panel_html(ModeDisplay.SNAP, {})
        self.assertIn("id=counters", html)
        self.assertIn("采样 0 轮", html)

class SettingsKeyboardNav(unittest.TestCase):
    u"""设置窗的键盘出口: Enter 保存, Esc 取消。

    六个输入格 Tab 走完之后, 键盘用户的手原本必须再去摸一次鼠标点
    "保存"按钮。Enter 就是给这一步留的路; Esc 与面板 / 详情窗同一个键。
    """

    def setUp(self):
        import gpu_tray
        self.gt = gpu_tray
        self.saved_tk, self.saved_ttk = gpu_tray.tk, gpu_tray.ttk
        self.saved_ui = gpu_tray._UI_FILE
        self.saved_write = gpu_tray.write_json_dict
        self.fake_tk = mock.MagicMock(name="tk")
        self.host = self.fake_tk.Tk.return_value
        gpu_tray.tk = self.fake_tk
        gpu_tray.ttk = mock.MagicMock(name="ttk")
        self.fake_tk.ttk = gpu_tray.ttk
        self.dir = tempfile.mkdtemp(prefix="gpumig-setnav-")
        self.path = os.path.join(self.dir, "config.json")
        gpu_tray._UI_FILE = os.path.join(self.dir, "panel_ui.json")
        self.written = []

        def remember(path, data):
            self.written.append(dict(data))
            return True

        gpu_tray.write_json_dict = remember
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"threshold_percent": 50}, f)
        self.app = object.__new__(gpu_tray.TrayApp)
        self.app.config_path = self.path
        self.app._settings_open = False
        self.app._ui_windows = 0
        # 只做"是不是 None"的哨兵, _settings_build 只用它判早退
        self.app._ui_root = object()

    def tearDown(self):
        self.gt.tk, self.gt.ttk = self.saved_tk, self.saved_ttk
        self.gt._UI_FILE = self.saved_ui
        self.gt.write_json_dict = self.saved_write
        shutil.rmtree(self.dir, ignore_errors=True)

    def _build(self):
        self.app._settings_build()
        return self.host.Toplevel.return_value

    def test_every_numeric_entry_takes_enter_as_save(self):
        u"""六个数字格每一格都要绑上 Enter -> 保存。

        少绑一格, 用户在那个格子里按回车就什么都不会发生, 而
        "刚才在别的格子里明明是好的" —— 同一张表单两种手感。
        """
        self._build()
        # 六个格子共用同一个 Entry 替身实例, 所以看的是 return_value 上
        # 的 bind 记录, 不是 Entry 这个工厂自己的
        seqs = [c.args[0]
                for c in self.fake_tk.Entry.return_value.bind.call_args_list
                if c.args]
        self.assertEqual(seqs.count("<Return>"), 6,
                         f"六个数字格都该绑 Enter: {seqs}")

    def test_the_window_offers_escape_as_cancel(self):
        u"""Esc = 取消, 与面板 / 详情窗同一个键。"""
        self._build()
        # 设置窗的 root 也是 Toplevel.return_value, 和面板那条链路同一个替身
        keys = [c.args[0]
                for c in self.fake_tk.Toplevel.return_value.bind
                .call_args_list if c.args]
        self.assertIn("<Escape>", keys,
                      f"设置窗没给键盘留取消键: {keys}")
class AiProtectionRules(unittest.TestCase):
    u"""AI 显存保护: 命中名单, 或独显专用显存达到保护阈值。

    这个守卫函数此前零覆盖 —— 全库一次都没直接调它, 只有迁移分支内部
    在用, 于是它是否正确只活在 docstring 里。而它守的恰恰是最不该出事的
    地方: 它决定要不要跳过自动重启。返回 False 会让每个被迁移的模型进程
    当场被杀掉重拉(几十 GB 权重白加载一遍), 恒返回 True 则等于把
    auto_restart 整个关掉。两个方向都是静默的。

    单位: dedicated_by_pid 是字节, protect_vram_mb 是 MiB (与 §116 起显存
    两列改成的 MiB 同一口径), 换算就是 1024 * 1024。
    """

    MB = 1024 * 1024

    def setUp(self):
        self.cfg = dict(gm.DEFAULT_CONFIG)

    def test_a_name_on_the_builtin_list_is_protected(self):
        u"""内置名单里的推理服务一个都不许被自动重启。

        取出名单里第一个真条目来问, 而不是自己拼一个名字 —— 名单
        自己被误删一条时, 这条用例就会红。
        """
        self.assertTrue(gm.DEFAULT_AI_PROCESSES, "内置 AI 名单空了")
        for name in gm.DEFAULT_AI_PROCESSES:
            self.assertTrue(
                gm.is_ai_protected(self.cfg, name, 1, {}),
                name + " 在内置名单里却没被保护")

    def test_the_name_match_ignores_case(self):
        u"""大小写不敏感: Windows 进程表里大小写本来就说不准。

        LM Studio.exe 的真实进程名大小写取决于启动方式, 按精确匹配
        会有一半时候漏掉。
        """
        name = gm.DEFAULT_AI_PROCESSES[0]
        self.assertTrue(
            gm.is_ai_protected(self.cfg, name.upper(), 1, {}),
            "名单命中必须大小写不敏感")

    def test_the_user_list_extends_the_builtin_one(self):
        u"""自建推理服务靠 ai_processes 补, 不用改代码。"""
        cfg = dict(self.cfg, ai_processes=["my-infer.exe"])
        self.assertTrue(gm.is_ai_protected(cfg, "my-infer.exe", 1, {}))
        self.assertTrue(gm.is_ai_protected(cfg, "MY-INFER.EXE", 1, {}),
                        "用户名单同样大小写不敏感")
        self.assertFalse(gm.is_ai_protected(cfg, "notepad.exe", 1, {}))

    def test_the_vram_fallback_covers_processes_off_every_list(self):
        u"""显存兜底: 名单外的自建服务只要吃到阈值也算 AI。

        这一条盯着的是"超过阈值就算"而不是"名单里有才算" —— 否则
        用户自己起的推理进程会被自动重启打掉。
        """
        cfg = dict(self.cfg, protect_vram_mb=100)
        self.assertTrue(
            gm.is_ai_protected(cfg, "unknown.exe", 7, {7: 100 * self.MB}),
            "正好踩在阈值上就该保护(>=, 不是 >)")
        self.assertTrue(
            gm.is_ai_protected(cfg, "unknown.exe", 7,
                                {7: 101 * self.MB}))
        self.assertFalse(
            gm.is_ai_protected(cfg, "unknown.exe", 7,
                                {7: 99 * self.MB}),
            "没到阈值不该顺手保护, 那会关掉 auto_restart")

    def test_zero_threshold_switches_the_fallback_off(self):
        u"""protect_vram_mb=0 是显式关闭, 不是"0 也算保护"。

        `if limit and ...` 的那个 and 是这里唯一的东西: 写成
        `>=` 判断的话, 0 会让每一个进程都命中。
        """
        cfg = dict(self.cfg, protect_vram_mb=0)
        self.assertFalse(
            gm.is_ai_protected(cfg, "unknown.exe", 7,
                                {7: 9000 * self.MB}),
            "阈值为 0 必须是关闭, 不是全部命中")

    def test_a_missing_pid_or_an_empty_name_is_not_protected(self):
        u"""空名字 / pid 不在表里: 两个都不许炸, 也都不算保护。

        采样间隙里 pid 退出是常态, 拿不到名字也是常态; 这里抛异常会
        顺着迁移分支回到监控循环。
        """
        self.assertFalse(gm.is_ai_protected(self.cfg, None, 1, {}))
        self.assertFalse(gm.is_ai_protected(self.cfg, "", 1, {}))
        self.assertFalse(gm.is_ai_protected(self.cfg, "x.exe", 99, {}))

    def test_a_config_without_the_key_still_works(self):
        u"""老 config.json 没有 ai_processes 这个键。"""
        cfg = {k: v for k, v in self.cfg.items() if k != "ai_processes"}
        self.assertTrue(gm.is_ai_protected(cfg, "vllm.exe", 1, {}),
                        "老配置也不该漏掉内置名单")

class AiProcessesConfigCoercion(unittest.TestCase):
    u"""ai_processes 必须吃上配置归一, 否则整条 AI 保护会失效。

    §124 给 is_ai_protected 补覆盖时顺带发现: ai_processes 不在 _LIST_KEYS 里。
    两种坏写法各有各的死法:
      - "ollama.exe" (字符串) -> 原样留下, is_ai_protected 的 set 推导式
        按字符展开, 名单一条都匹配不上 —— 静默失效;
      - null -> None 一路传进去, 集合推导式当场 TypeError, 之后每一轮
        监控都抛一次并把整个迁移分支跳掉。
    §2 给 hot_reason 修过同一类问题(六个数字键收进 _NUMBER_RULES),
    只是名单这一侧漏了一个键。
    """

    def _merged(self, raw):
        return gm.merged_config({**gm.DEFAULT_CONFIG, "ai_processes": raw})

    def test_a_bare_string_becomes_a_one_item_list(self):
        u"""单个字符串当成一项, 不逐字符展开。"""
        cfg = self._merged("ollama.exe")
        self.assertEqual(cfg["ai_processes"], ["ollama.exe"])
        # 真正的判据: 用户自己起的那个推理服务现在认得出吗
        self.assertTrue(
            gm.is_ai_protected(cfg, "ollama.exe", 1, {}),
            "写成字符串的 ai_processes 必须仍然生效")

    def test_a_null_falls_back_to_the_default_not_to_none(self):
        u"""null 退回默认空名单, 不许把 None 留在配置里。

        这一条以前会让 is_ai_protected 抛 TypeError, 而调用点在迁移
        分支深处 —— 监控循环兜底只会让人看到每轮一条 traceback, 
        migration 从此不再发生。
        """
        cfg = self._merged(None)
        self.assertEqual(cfg["ai_processes"], [])
        self.assertFalse(gm.is_ai_protected(cfg, "notepad.exe", 1, {}))

    def test_a_non_list_falls_back_and_says_so(self):
        u"""数字 / dict 之类也退回默认, 并且记一行日志。"""
        lines = []
        saved = gm.log
        gm.log = lambda msg, logfile=None: lines.append(msg)
        try:
            cfg = self._merged(5)
        finally:
            gm.log = saved
        self.assertEqual(cfg["ai_processes"], [])
        self.assertTrue(any("ai_processes" in m for m in lines),
                        f"退回默认值必须留一行日志: {lines}")

    def test_every_list_key_in_the_default_config_is_registered(self):
        u"""新加名单型配置项时必须往 _LIST_KEYS 登记。

        对账而不是逐个点名: 漏一个新键 = 它的归一不存在 = 上面那两种
        死法随便哪一种。这条用例就是那张对账单。
        """
        listy = {k for k, v in gm.DEFAULT_CONFIG.items()
                 if isinstance(v, list)}
        self.assertEqual(listy - set(gm._LIST_KEYS), set(),
                         "这些名单型配置项没有归一: %s"
                         % (listy - set(gm._LIST_KEYS)))

    def test_a_null_ai_list_cannot_crash_the_guard_itself(self):
        u"""守寄存了 null 的字典也不许让守卫函数崩。

        归一在 merged_config 一处, 但 is_ai_protected 也可能拿到别处
        攒的配置(测试替身、将来的调用方)。守卫函数不该把命押在
        调用方身上。
        """
        cfg = {k: v for k, v in gm.DEFAULT_CONFIG.items()
               if k != "ai_processes"}
        cfg["ai_processes"] = None
        self.assertFalse(gm.is_ai_protected(cfg, "notepad.exe", 1, {}))

class EveryConfigurableKeyIsCoerced(unittest.TestCase):
    u"""每个可配置键都必须有归一, 否则手改 config.json 就是埋雷。

    §125 发现 ai_processes 漏登记 _LIST_KEYS。同一个类别的漏登记还有一个:
    protect_vram_mb 不在 _NUMBER_RULES 里。它的失效方式更难看:
      - "2048" / "abc" / [1,2] -> 原样留下, is_ai_protected 的
        `dedicated >= limit * 1024 * 1024` 当场 TypeError;
      - -5 -> `if limit` 为真而阈值恒负, 于是每个进程都被判成 AI 保护,
        auto_restart 被静默关掉, 而界面上什么都看不出来。

    两条用例都是对账而不是点名: 新加配置键忘了登记, 当场红。
    """

    def _coerced(self, key, raw):
        return gm.merged_config({**gm.DEFAULT_CONFIG, key: raw})

    def test_every_numeric_key_is_in_the_number_rules(self):
        u"""DEFAULT_CONFIG 里的数字键必须都被 _NUMBER_RULES 覆盖。

        bool 是 int 的子类, 先剔掉; hotkeys 是 dict, 有自己的 _coerce_hotkeys。
        """
        numeric = {k for k, v in gm.DEFAULT_CONFIG.items()
                   if isinstance(v, (int, float))
                   and not isinstance(v, bool)}
        self.assertEqual(numeric - set(gm._NUMBER_RULES), set(),
                         "这些数字配置项没有归一: %s"
                         % (numeric - set(gm._NUMBER_RULES)))

    def test_every_list_key_is_in_the_list_rules(self):
        u"""名单型键同理, 必须都在 _LIST_KEYS 里。"""
        listy = {k for k, v in gm.DEFAULT_CONFIG.items()
                 if isinstance(v, list)}
        self.assertEqual(listy - set(gm._LIST_KEYS), set(),
                         "这些名单型配置项没有归一: %s"
                         % (listy - set(gm._LIST_KEYS)))

    def test_every_bool_key_is_in_the_bool_rules(self):
        u"""开关型键同理, 必须都在 _BOOL_KEYS 里。"""
        booly = {k for k, v in gm.DEFAULT_CONFIG.items()
                 if isinstance(v, bool)}
        self.assertEqual(booly - set(gm._BOOL_KEYS), set(),
                         "这些开关配置项没有归一: %s"
                         % (booly - set(gm._BOOL_KEYS)))

    def test_a_stringified_protect_vram_mb_is_coerced(self):
        u"""protect_vram_mb 写成字符串必须被当成数字, 不是原样留下。"""
        self.assertEqual(
            self._coerced("protect_vram_mb", "2048")["protect_vram_mb"],
            2048)

    def test_a_non_numeric_protect_vram_mb_falls_back_and_does_not_crash(self):
        u"""None / 列表 / 非数字串都退回默认, 且守卫函数不许抛。

        这三种以前都会让 is_ai_protected 抛 TypeError, 而调用点在迁移
        分支深处 —— 监控循环兜底只会每 5 秒刷一条 traceback, migration
        从此不再发生。
        """
        MB = 1024 * 1024
        for bad in (None, [1, 2], "abc", {"a": 1}):
            cfg = self._coerced("protect_vram_mb", bad)
            self.assertEqual(cfg["protect_vram_mb"], 2048,
                             "%r 该退回默认值" % (bad,))
            # 100MiB 远在默认阈值 2048 之下: 退回默认值的话就不该保护。
            # 换成 3000MiB 的话, 就算退回默认也会被保护, 断言就什么都
            # 证明不了。
            self.assertFalse(
                gm.is_ai_protected(cfg, "x.exe", 1, {1: 100 * MB}),
                "%r 不该让守卫函数抛, 也不该顺手保护" % (bad,))

    def test_the_guard_survives_a_raw_unnormalized_config(self):
        u"""守卫函数自己也得扛住没归过一的配置。

        merged_config 是归一的唯一入口, 但 is_ai_protected 是个纯函数,
        将来任何调用方(新面板、测试替身、别的入口)都可能把没归一过的
        dict 递进来。上一次(isinstance 检查)就是为这种情况留的, 没有
        这条用例它就是死代码 —— 而死代码没人会为它负责。
        """
        MB = 1024 * 1024
        cfg = dict(gm.DEFAULT_CONFIG)
        cfg["protect_vram_mb"] = "2048"      # 故意绕过 merged_config
        self.assertFalse(gm.is_ai_protected(cfg, "x.exe", 1, {}),
                         "非数字阈值必须当作没设, 不该抛 TypeError")

    def test_a_negative_threshold_is_clamped_not_honoured(self):
        u"""负数阈值必须夹到 0(关闭), 不能变成"全都保护"。

        `if limit` 对负数为真, 而 `dedicated >= -N` 恒成立 —— 一路
        静默把 auto_restart 关掉。下限取 0 就是为这个。
        """
        MB = 1024 * 1024
        cfg = self._coerced("protect_vram_mb", -5)
        self.assertEqual(cfg["protect_vram_mb"], 0)
        self.assertFalse(
            gm.is_ai_protected(cfg, "x.exe", 1, {1: 3000 * MB}),
            "负数阈值不许变成全都要保护")

class OneContrastRuler(unittest.TestCase):
    u"""WCAG 亮度和对比度只能有一把尺。

    这个公式此前被抄了三遍: gpu_monitor._lum/_ratio、gpu_tray._luminance/
    _contrast_ratio、测试自己的 _wcag_luminance/_wcag_contrast。三份今天数值
    完全一致(拿十个色值逐个对过), 但那只是巧合式的正确。

    要紧的是第三份: 可读性守卫拿它给生产代码发合格证。尺子和被测对象
    分家的那一刻, 合格证就不再证明任何事 —— 改了生产的公式, 守卫照旧
    全绿。这和 §NoiseRowFilter 里 row_is_noise 那条 assertIs 是同一条规矩:
    判据必须只有一个出处, 不然改一处漏一处。
    """

    def test_the_tray_uses_the_monitors_ruler(self):
        import gpu_tray
        self.assertIs(gpu_tray._luminance, gm._lum)
        self.assertIs(gpu_tray._contrast_ratio, gm._ratio)

    def test_the_legibility_guard_uses_the_monitors_ruler(self):
        u"""守卫自己那两把尺必须就是生产实现, 不是抄写的副本。

        这里钉的是函数身份而不是数值: 数值一致是今天的结果, 会变;
        身份一致是结构, 不会因为有人改了公式而悄悄失效。
        """
        import inspect
        body = inspect.getsource(_wcag_luminance)
        self.assertIn("gm._lum", body,
                      "守卫的亮度尺必须调生产实现")
        body = inspect.getsource(_wcag_contrast)
        self.assertIn("gm._ratio", body,
                      "守卫的对比度尺必须调生产实现")

    def test_the_rulers_agree_on_the_palette_that_is_actually_shipped(self):
        u"""三把尺在同一批真实色值上给出同一个数。

        色值取样式表里实际出现的那些(不是随便编的): 这一条同时证明
        别名没接错、也没人把某一份改成了别的公式。
        """
        import gpu_tray
        import re as _re
        colors = set(_re.findall(r"color:(#[0-9a-fA-F]{3,6})",
                                 gm._PANEL_STYLE))
        self.assertGreater(len(colors), 5, "样式表里的色值太少, 不像真 palette")
        for color in sorted(colors):
            hexs = _hex6(color)
            rgb = tuple(int(hexs[i:i + 2], 16) for i in (1, 3, 5))
            self.assertAlmostEqual(
                gm._lum(rgb), gpu_tray._luminance(rgb), places=12,
                msg="%s 两把尺给出不同亮度" % color)

    def test_a_shorthand_hex_is_expanded_before_measuring(self):
        u"""#abc 这种三位缩写展开成 #aabbcc, 不然切不出三段。

        _hex6 以前只被对比度用例间接用到, 它自己没被直接钉过。
        """
        self.assertEqual(_hex6("#abc"), "#aabbcc")
        self.assertEqual(_hex6("#a1b2c3"), "#a1b2c3")
        self.assertAlmostEqual(_wcag_luminance("#abc"),
                               _wcag_luminance("#aabbcc"), places=12)

class MibConversionHasOneSource(unittest.TestCase):
    u"""字节 -> MiB 的换算只能写一遍。

    这个除法在 gpu_monitor.py 里有十处、gpu_tray.py 里两处, 而且两种写法
    混用: 1024*1024 和 1048576。看着只是风格, 但显存的单位口径正是
    §116 才澄清过的事(MB vs MiB 差 4.86%)。谁哪天把其中一处顺手改成
    1000*1000, 那一列就和其它十一个界面对不上, 而改的人完全看不出
    还有别处存在。常量名叫 MIB 而不是 MB, 就是把答案写进名字里。
    """

    @staticmethod
    def _fold(node):
        u"""把"常量 * 常量"之类的表达式折成整数, 折不动给 None。

        ast.literal_eval 不认乘除(它只放行加减, 免得 "a"*10**9 把内存
        吃穿), 所以这里自己折一层。
        """
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op,
                                                     (ast.Mult, ast.FloorDiv)):
            a = self_fold = None
            left = MibConversionHasOneSource._fold(node.left)
            right = MibConversionHasOneSource._fold(node.right)
            if left is None or right is None:
                return None
            return (left * right if isinstance(node.op, ast.Mult)
                    else left // right)
        return None

    def _mib_writers(self, mod):
        src = io.open(mod.__file__, encoding="utf-8").read()
        tree = ast.parse(src)
        out = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.BinOp):
                continue
            if self._fold(node) == 1048576:
                out.append(node.lineno)
        return out

    def test_the_constant_is_the_one_both_modules_share(self):
        import gpu_tray
        self.assertIs(gpu_tray.MIB, gm.MIB)
        self.assertEqual(gm.MIB, 1048576,
                         "MiB 是 1024*1024; 写成 1000*1000 就成 MB 了")

    def test_the_magic_number_is_written_exactly_once(self):
        u"""手写的 MiB 换算只允许有一处, 就是 MIB 自己的定义。

        扫描而不是逐个点名: 新加一处手写的, 这条当场红。注释和 docstring
        里提到 1048576 是讲道理, 不算违规, 所以扫 AST 而不是文本。
        """
        for mod, want in ((gm, 1), (gpu_tray, 0)):
            hits = self._mib_writers(mod)
            self.assertEqual(
                len(hits), want,
                "%s 里有 %d 处手写 MiB 换算(第 %s 行), 只该 %s"
                % (mod.__name__, len(hits), hits,
                   "定义一次 MIB" if want else "一处都没有"))

    def test_the_palette_math_still_uses_the_constant(self):
        u"""抽查一条真实链路, 证明换成常量没有改变任何读数。"""
        MB = 1024 * 1024
        snap = {"dedicated_by_pid": {1: 3 * MB},
                "shared_by_pid": {2: 2 * MB},
                "util_by_pid": {1: 40.0},
                "gpu_all_by_pid": {1: 40.0}, "dgpu_util_by_pid": {}}
        cards = [c[1] for c in gm.panel_summary(snap)]
        self.assertEqual(cards[2], "3 MiB")
        self.assertEqual(cards[3], "2 MiB")


class HeatPaletteParity(unittest.TestCase):
    u"""热度三色在两个界面、两套主题下必须是同一个值。

    托盘的热度色来自 gpu_tray._FLUENT_LIGHT / _FLUENT_DARK 两张 palette,
    Web 的热度色是 _PANEL_STYLE 里写死的 .hot/.warm/.cool 六条 CSS。两边
    今天完全一致(逐个对过), 但那是靠人记住同步的结果。

    这不是纯风格问题: 热度色是用户判断这台机器现在忙不忙的主要线索,
    而托盘图标和 Web 面板是同一个数的两个视图。哪天只改了一边, 同一个
    62.7% 会在托盘里是橙的、在浏览器里是红的, 而两边都没有错到需要报错的
    程度 —— 于是谁也不改。
    """

    def _web_heat(self, css, cls):
        m = re.search(r"\." + cls + r"\{color:(#[0-9a-fA-F]{6})", css)
        self.assertIsNotNone(m, "%s 没写颜色" % cls)
        return m.group(1)

    def _split_style(self):
        css = gm._PANEL_STYLE
        at = css.find("@media (prefers-color-scheme:dark)")
        self.assertGreater(at, 0, "深色档的 @media 块没了")
        return css[:at], css[at:]

    def test_every_heat_colour_matches_the_tray_palette(self):
        u"""六个色值逐个对: 两套主题 x 热度三档。"""
        import gpu_tray
        light_css, dark_css = self._split_style()
        pairs = ((light_css, gpu_tray._FLUENT_LIGHT, "浅色"),
                 (dark_css, gpu_tray._FLUENT_DARK, "深色"))
        for css, pal, label in pairs:
            for cls in ("hot", "warm", "cool"):
                self.assertEqual(self._web_heat(css, cls), pal[cls],
                                 "%s主题的 %s 两界面不同色" % (label, cls))

    def test_the_tray_derives_them_from_the_palette_not_literals(self):
        u"""托盘那一侧的分发也必须是同一张表, 不许另立门户。

        托盘给控件上色用的是 _HEAT_COLOR 这个由 palette 派生的字典;
        哪里要是写死了三个色值, 上面那条对账就落空了。
        """
        import gpu_tray
        for cls in ("hot", "warm", "cool"):
            self.assertEqual(gpu_tray._HEAT_COLOR[cls],
                             gpu_tray._PALETTE[cls],
                             "%s 没有从 palette 派生" % cls)


class SparkGeometryConstants(unittest.TestCase):
    u"""趋势图的几何常量在两个界面、三处代码里必须是同一个数。

    46 这个数出现在四个地方: gpu_monitor._SPARK_H、CSS 的 .sparkwrap /
    .spark 两个 height、gpu_tray 的 tk.Canvas(height=46)。源码注释里
    已经写明必须等于 _SPARK_H —— CSS 高度和 viewBox 不一致时纵向会跟着
    缩放, 阈值线的位置就算错了。但"必须相等"从来只是一句注释, 没有任何
    东西检查它。180 同理: _SPARK_MAX_POINTS、cmd_monitor 里的
    deque(maxlen=180)、以及 1800 秒窗口(180 点 x 每 10 秒一点)。

    这类分叉的后果不是崩, 是错位: 阈值线画在错误的高度上, 而用户拿着它
    判断"什么时候会被判成高占用"—— 那条线在骗人。
    """

    def _tray_source(self):
        import gpu_tray
        return io.open(gpu_tray.__file__, encoding="utf-8").read()

    def test_the_canvas_height_matches_the_svg_height(self):
        u"""托盘的 Canvas 高度就是 _SPARK_H, 不许另写一个 46。"""
        tray = self._tray_source()
        self.assertIn("tk.Canvas(root, height=%d" % gm._SPARK_H, tray,
                      "托盘 Canvas 高度和 _SPARK_H 不一致: 纵向会跟着缩放, "
                      "阈值线的位置就算错了")

    def test_the_css_height_matches_the_svg_height(self):
        u"""CSS 那两条 height 也必须是 _SPARK_H。"""
        want = "height:%dpx" % gm._SPARK_H
        self.assertIn(".sparkwrap{position:relative;" + want,
                      gm._PANEL_STYLE)
        self.assertIn(".spark{display:block;width:100%;" + want,
                      gm._PANEL_STYLE)

    def test_the_ring_buffer_matches_the_point_cap(self):
        u"""cmd_monitor 里的 deque 上限就是 _SPARK_MAX_POINTS。"""
        mon = io.open(gm.__file__, encoding="utf-8").read()
        want = "deque(maxlen=%d)" % gm._SPARK_MAX_POINTS
        self.assertIn(want, mon,
                      "环形队列上限和点数上限不是同一个数: 趋势图要么拿不到"
                      "本该能画的点, 要么白存一批永远画不出去的")

    def test_the_window_is_the_cap_times_one_sampling_period(self):
        u"""窗口秒数 = 点数上限 x 采样周期(每 10 秒一点)。"""
        self.assertEqual(gm._SPARK_WINDOW_S, gm._SPARK_MAX_POINTS * 10,
                         "窗口和点数上限对不上: 要么白存一批窗口外的点, "
                         "要么窗口内还有没画出来的")


class PanelScriptJsBehaviour(unittest.TestCase):
    u"""面板 JS 的行为测试: 真跑一遍, 不是查字符串在不在。

    Python 侧的断言(WebPanelStaleBudget 那几条)只能查文本在不在。
    这一轮就抓到过一次: 回退暂停分支的计时时, 把 if(paused){...} 删成了
    语法错误(if 后面直接跟 else) —— Python 断言全绿, 而页面 JS 整个
    跑不起来, 面板一秒都不刷新。字符串断言对拼错了完全失明。

    所以这里把 _PANEL_SCRIPT 的真值抽出来交给 node 跑, 用假 DOM 驱动
    停更判定, 断言它说出口的话。node 不在 PATH 时跳过(CI 上未必有),
    但不静默: 跳过的理由写在消息里。
    """

    def setUp(self):
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("PATH 里没有 node: 面板 JS 的行为测试跑不了。"
                          "装一个 Node.js 就能让它生效 —— 它守的是"
                          "字符串断言失明的那一类缺陷")

    def _panel_js(self):
        u"""从 gpu_monitor 里取 _PANEL_SCRIPT 的真值(ast 取常量)。

        不用正则扫源码: 注释里的中文会被当成字符串字面量抠出来,
        拼出来的 JS 直接语法错误。
        """
        import ast
        src = io.open(gm.__file__, encoding="utf-8").read()
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", None) == "_PANEL_SCRIPT"
                    for t in node.targets):
                return node.value.value
        self.fail("_PANEL_SCRIPT 不在 gpu_monitor 里了")

    def test_the_stale_banner_behaves_in_every_state(self):
        u"""七种(暂停 x 数据新旧 x 宽限)组合下, 顶部那行说得对。"""
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "panel_stale_check.js")
        self.assertTrue(os.path.exists(script),
                        "缺少 tests/panel_stale_check.js")
        env = dict(os.environ)
        env["PANEL_JS"] = self._panel_js()
        r = subprocess.run([self.node, script], capture_output=True,
                           text=True, encoding="utf-8", env=env,
                           timeout=60)
        self.assertEqual(r.returncode, 0,
                         "面板 JS 行为测试挂了:\n" + r.stdout + r.stderr)
        self.assertIn("all panel stale checks passed", r.stdout,
                      r.stdout + r.stderr)

class WebPanelFilterBehaviour(unittest.TestCase):
    u"""Web 面板筛选函数 f() 的行为测试: 真跑, 不是查字符串在不在。

    WebPanelFilter 那五条断言查的是 td.n / indexOf / t++ 这些文本在不在。
    §131 已经证明这类断言会失明(把 JS 删成语法错误它们照样绿), 而筛选是
    面板上交互最多的一个功能, 值得真跑一遍。

    这里复用同一套脚手架: ast 取 _PANEL_SCRIPT 真值 -> 环境变量传给 node ->
    假 DOM 驱动 f() -> 断言它让哪些行可见、提示行说什么。
    """

    def setUp(self):
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("PATH 里没有 node: 面板 JS 的行为测试跑不了。"
                          "装一个 Node.js 就能让它生效 —— 它守的是"
                          "字符串断言失明的那一类缺陷")

    def _panel_js(self):
        import ast
        src = io.open(gm.__file__, encoding="utf-8").read()
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", None) == "_PANEL_SCRIPT"
                    for t in node.targets):
                return node.value.value
        self.fail("_PANEL_SCRIPT 不在 gpu_monitor 里了")

    def test_the_filter_behaves_in_every_state(self):
        u"""十三种筛选输入下, 可见行与提示行都对。"""
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "panel_filter_check.js")
        self.assertTrue(os.path.exists(script),
                        "缺少 tests/panel_filter_check.js")
        env = dict(os.environ)
        env["PANEL_JS"] = self._panel_js()
        r = subprocess.run([self.node, script], capture_output=True,
                           text=True, encoding="utf-8", env=env,
                           timeout=60)
        self.assertEqual(r.returncode, 0,
                         "面板筛选行为测试挂了:\n" + r.stdout + r.stderr)
        self.assertIn("all panel filter checks passed", r.stdout,
                      r.stdout + r.stderr)

class PanelConnBehaviour(unittest.TestCase):
    u"""断连提示 connshow/connhide 的行为测试: 真跑, 不是查字符串在不在。

    WebPanelLiveUpdate 那几条断言查的是 ++fails>=3 / connhide() / r.ok 这些
    文本在不在。§131 已经证明这类断言会失明, 而"取不到数据时页面说什么"
    是用户判断监控是否还活着的唯一依据, 值得真跑一遍。

    复用 §131/§132 那套脚手架: ast 取 _PANEL_SCRIPT 真值 -> 环境变量传给
    node -> 可控的假 fetch 驱动 pull() -> 断言 conn 的终态。
    """

    def setUp(self):
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("PATH 里没有 node: 面板 JS 的行为测试跑不了。"
                          "装一个 Node.js 就能让它生效 —— 它守的是"
                          "字符串断言失明的那一类缺陷")

    def _panel_js(self):
        import ast
        src = io.open(gm.__file__, encoding="utf-8").read()
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", None) == "_PANEL_SCRIPT"
                    for t in node.targets):
                return node.value.value
        self.fail("_PANEL_SCRIPT 不在 gpu_monitor 里了")

    def test_the_disconnect_banner_behaves_in_every_state(self):
        u"""七种(失败次数 x 是否恢复 x 是否 502)组合下, 顶部那行说得对。"""
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "panel_conn_check.js")
        self.assertTrue(os.path.exists(script),
                        "缺少 tests/panel_conn_check.js")
        env = dict(os.environ)
        env["PANEL_JS"] = self._panel_js()
        r = subprocess.run([self.node, script], capture_output=True,
                           text=True, encoding="utf-8", env=env,
                           timeout=60)
        self.assertEqual(r.returncode, 0,
                         "面板断连行为测试挂了:\n" + r.stdout + r.stderr)
        self.assertIn("all panel conn checks passed", r.stdout,
                      r.stdout + r.stderr)

class PanelJsHarnessSelfCheck(unittest.TestCase):
    u"""脚手架自检: drain() 给的 tick 必须够一轮 pull() 落地。

    drain 的 tick 数曾经是拍脑袋的 magic number。它不够时的失败形态很坏:
    断言读到还没落地的 Promise 状态, 看起来像产品有 bug, 实际是脚手架
    没等够。页面里再多一级 then, 这里会先红。

    判据是 PARTS 元素的 innerHTML —— 只有 pull() 的成功路径会写它,
    页面初始化时不碰(不像 hint.textContent, f() 一启动就写了)。
    """

    def setUp(self):
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("PATH 里没有 node: 面板 JS 的行为测试跑不了。"
                          "装一个 Node.js 就能让它生效")

    def _panel_js(self):
        import ast
        src = io.open(gm.__file__, encoding="utf-8").read()
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", None) == "_PANEL_SCRIPT"
                    for t in node.targets):
                return node.value.value
        self.fail("_PANEL_SCRIPT 不在 gpu_monitor 里了")

    def test_one_pull_lands_within_the_drain_budget(self):
        u"""drain(1) 之后, 成功路径该写的都必须写完。"""
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "panel_harness_selfcheck.js")
        self.assertTrue(os.path.exists(script),
                        "缺少 tests/panel_harness_selfcheck.js")
        env = dict(os.environ)
        env["PANEL_JS"] = self._panel_js()
        r = subprocess.run([self.node, script], capture_output=True,
                           text=True, encoding="utf-8", env=env,
                           timeout=60)
        self.assertEqual(r.returncode, 0,
                         "脚手架自检挂了:\n" + r.stdout + r.stderr)
        self.assertIn("harness self-check passed", r.stdout,
                      r.stdout + r.stderr)

class SparklineThresholdInEmptyState(unittest.TestCase):
    u"""点数不够时, 阈值线也必须画出来。

    老代码在 len(pts) < 2 时只画一句空态文案就 return, 阈值线整个不画。
    而空态恰恰是最需要参考基准的时候: 用户看到"最近 30 分钟无采样数据",
    想知道的是"阈值在哪、我离它多远", 却只有一句干巴巴的提示。

    btop 在数据不足时同样保留参考线, 这里跟着学。
    """

    class _Canvas:
        u"""记下调用了什么, 不真的画。"""

        def __init__(self, width=860, height=46):
            self._w, self._h = width, height
            self.lines = []
            self.texts = []

        def delete(self, *_a):
            pass

        def winfo_width(self):
            return self._w

        def winfo_height(self):
            return self._h

        def create_line(self, *coords, **kw):
            self.lines.append((coords, kw))

        def create_polygon(self, *_a, **_kw):
            pass

        def create_text(self, *pos, **kw):
            self.texts.append(kw.get("text", ""))

    def _draw(self, history, threshold):
        import gpu_tray
        app = object.__new__(gpu_tray.TrayApp)
        app.history = history
        app._draw_sparkline(self._Canvas(), threshold)
        return self._Canvas()

    def test_the_threshold_line_survives_an_empty_history(self):
        u"""一个点都没有(刚启动)时也画阈值线。"""
        c = self._Canvas()
        import gpu_tray
        app = object.__new__(gpu_tray.TrayApp)
        app.history = []
        app._draw_sparkline(c, 40)
        dashed = [ln for ln, kw in c.lines if kw.get("dash")]
        self.assertEqual(len(dashed), 1,
                         "空态下阈值线没画: 用户看不到自己离阈值多远")
        self.assertTrue(any("迁移阈值 40%" in t for t in c.texts),
                        f"空态下阈值标签没画: {c.texts}")

    def test_the_threshold_line_survives_a_stale_window(self):
        u"""有历史点但整个落在 30 分钟窗口外(休眠唤醒)时也画。"""
        c = self._Canvas()
        import gpu_tray
        app = object.__new__(gpu_tray.TrayApp)
        # spark_points 的窗口相对最新一点算(end - p[0] <= 1800),
        # 不是相对当前时钟。所以要两点相隔超过 30 分钟才落进空态分支 ——
        # 这正是休眠唤醒后的形状: 睡前采一次, 醒来又采一次。
        now = gm.time.time()
        app.history = [(now - 7200, 10.0), (now, 20.0)]
        app._draw_sparkline(c, 40)
        dashed = [ln for ln, kw in c.lines if kw.get("dash")]
        self.assertEqual(len(dashed), 1,
                         "窗口外空态下阈值线没画")
        self.assertTrue(any("无采样数据" in t for t in c.texts),
                        f"空态文案没出现: {c.texts}")

    def test_zero_threshold_draws_no_line_in_empty_state(self):
        u"""阈值为 0(该规则关闭)时不画线, 和有点数时一致。"""
        c = self._Canvas()
        import gpu_tray
        app = object.__new__(gpu_tray.TrayApp)
        app.history = []
        app._draw_sparkline(c, 0)
        self.assertEqual(c.lines, [], "阈值为 0 时不该有阈值线")

class ScratchDirDoesNotShadowStdlib(unittest.TestCase):
    u"""沙箱目录里的临时脚本不许用标准库模块名。

    .tmptest/run_unittest.py 把 .tmptest 插到 sys.path 最前面(为了 stub 顶上
    pystray / PIL)。于是那个目录里任何一个 .py 都能遮蔽同名标准库模块。

    真的踩过: 我为定位一次失败写了 .tmptest/bisect.py, 之后标准库 random
    执行 from bisect import ... 时导入了它, 它又 import ast, 递归到
    MemoryError, 把后面几十条用例全带崩。而现象极具误导性 —— traceback
    的行号对不上磁盘上的内容, 我先后怀疑过 .pyc 缓存、文件被替换、加载了
    另一份文件。

    这条用例就是把那个下午的教训钉住: 往 .tmptest 里丢文件之前, 名字得避开
    标准库。
    """

    def _scratch_dir(self):
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(os.path.dirname(here), ".tmptest")


    @staticmethod
    def _stdlib_names():
        u"""顶层标准库模块名。

        优先用 sys.stdlib_module_names(3.10+)。拿不到就退到一份内置常见
        名单 —— 它覆盖不到全部, 但 os/sys/re/json/ast/random/bisect 这些
        真正会被 from X import ... 导的都在。少扫几个名字远好过
        AttributeError 把整套测试带垮。
        """
        names = getattr(sys, "stdlib_module_names", None)
        if names:
            return set(names)
        return {
            "ast", "bisect", "collections", "ctypes",
            "datetime", "enum", "functools", "glob",
            "inspect", "io", "json", "math",
            "os", "pathlib", "queue", "random",
            "re", "shutil", "socket", "string",
            "struct", "subprocess", "sys", "tempfile",
            "textwrap", "threading", "time", "traceback",
            "types", "unicodedata", "warnings",
        }

    def test_no_scratch_file_shadows_a_stdlib_module(self):
        scratch = self._scratch_dir()
        if not os.path.isdir(scratch):
            self.skipTest("没有 .tmptest 目录")
        # 只扫本进程解释器认识的顶层模块名: 这样在没装某些包的机器上
        # 也不会误报(那些名字不在 sys.stdlib_module_names 里)
        stdlib = set(sys.stdlib_module_names)
        clashes = sorted(
            name for name in os.listdir(scratch)
            if name.endswith(".py")
            and name[:-3] in stdlib)
        # sys.stdlib_module_names 是 3.10 才有的。本项目没声明最低版本,
        # README 也只说"纯标准库 / Windows 10-11"—— 用户可能跑 3.9。
        # 拿不到那份清单就退到内置名单: 少扫几个名字仍能抓住最常见的
        # 那几个(os/sys/re/json/ast/...), 总比 AttributeError 把整套
        # 测试带垮好。
        stdlib = self._stdlib_names()
        self.assertEqual(clashes, [],
                         ".tmptest 里的这些文件会遮蔽同名标准库模块: %s。"
                         "run_unittest.py 把 .tmptest 插在 sys.path 最前面,"
                         "标准库 from X import ... 会导入它们而不是真模块"
                         % clashes)

    def test_the_stub_dir_is_not_shadowing_anything_we_care_about(self):
        u"""反向保险: stub 目录本身也不该引入新的遮蔽。

        stub 里放 pystray / PIL 是故意的(本环境读不到用户级 site-packages),
        但它同样在 sys.path 最前面。这条只确认它没夹带标准库名。
        """
        scratch = self._scratch_dir()
        stub = os.path.join(scratch, "stub")
        if not os.path.isdir(stub):
            self.skipTest("没有 .tmptest/stub 目录")
        stdlib = self._stdlib_names()
        clashes = sorted(
            name for name in os.listdir(stub)
            if name.endswith(".py") and name[:-3] in stdlib)
        self.assertEqual(clashes, [],
                         "stub 目录遮蔽了标准库模块: %s" % clashes)

class CrossSurfaceRowOrder(unittest.TestCase):
    """同一份快照, --list / Web 面板 / 托盘三面必须落在同一个行序上。

    --list 是诊断用, 仍给全量(含 0%/0MB 的噪声行); 面板和托盘用
    row_is_noise 把噪声行滤掉(README 记了这条约定)。所以这里比的是
    "共有的非噪声行"的顺序, 外加 --list 保留噪声行这一既有约定。
    三者共用的行序主键是 activity_key(GPU%、专用MB、共享MB 取最大),
    并列按 pid 升序 —— 这条不变量原先只活在 .tmptest 探针里, 没有
    测试兜底。
    """

    MB = 1024 * 1024

    def setUp(self):
        import gpu_tray
        self.saved = (gm.collect_gpu_sample, gm.luid_to_name,
                      gm.pid_to_name, gpu_tray.pid_to_name)

    def tearDown(self):
        import gpu_tray
        (gm.collect_gpu_sample, gm.luid_to_name, gm.pid_to_name,
         gpu_tray.pid_to_name) = self.saved

    def _name(self, pid):
        return {700: "mem.exe", 500: "mid.exe", 100: "zzz.exe",
                900: "aaa.exe", 300: "idle.exe"}.get(pid)

    def _snap(self):
        mb = self.MB
        return {
            "util_by_pid": {100: 50.0, 900: 50.0, 300: 0.0,
                           700: 5.0},
            "gpu_all_by_pid": {700: 5.0, 500: 60.0, 100: 50.0,
                              900: 50.0, 300: 0.0},
            "dgpu_util_by_pid": {500: 60.0},
            "shared_by_pid": {700: 300 * mb, 100: 50 * mb,
                              900: 50 * mb, 300: 0},
            "dedicated_by_pid": {100: 0, 900: 0, 300: 0},
            "igpu_luids": [0], "dgpu_luids": [1], "kind_cache": {},
            "pid_names": {700: "mem.exe", 500: "mid.exe",
                          100: "zzz.exe", 900: "aaa.exe",
                          300: "idle.exe"},
            "mode": "smart", "threshold": 40.0, "power_saver_auto": False,
            "on_battery": False, "history": [], "dgpu_temp": None,
        }

    def _web_order(self):
        html = gm.panel_html(self._snap(), {})
        return [int(x) for x in
                re.findall(r"<td class=pid>(\d+)</td>", html)]

    def _tray_order(self):
        import gpu_tray
        app = object.__new__(gpu_tray.TrayApp)
        app.sort_state = {"col": "auto", "desc": True}
        app.filter_text = ""
        app.lock = threading.Lock()
        app._name_cache = {}
        app.snapshot = self._snap()
        app._pid_name = self._name
        app._gpu_name = lambda _luid: "gpu"
        gpu_tray.pid_to_name = lambda pid: (self._name(pid), None)
        inserted = []

        class _Tree:
            def delete(self, *_a):
                pass

            def insert(self, _p, _pos, values=None, tags=None):
                inserted.append(values)

            def get_children(self):
                return ()

        class _Tip:
            def config(self, text=None):
                self.text = text

        gpu_tray.TrayApp._refresh_tree(app, _Tree(), _Tip())
        return [int(v[1]) for v in inserted]

    def _cli_order(self):
        mb = self.MB
        usage = {(100, IGPU_LUID, 0): 50.0, (900, IGPU_LUID, 0): 50.0,
                 (700, IGPU_LUID, 0): 5.0, (500, DGPU_LUID, 0): 60.0,
                 (300, IGPU_LUID, 0): 0.0}
        mem_shared = {(100, IGPU_LUID, 0): 50 * mb,
                      (900, IGPU_LUID, 0): 50 * mb,
                      (700, IGPU_LUID, 0): 300 * mb}
        sample = (usage, mem_shared, {},
                  {(IGPU_LUID, 0): None, (DGPU_LUID, 0): None})
        gm.collect_gpu_sample = lambda *a, **k: sample
        gm.luid_to_name = {IGPU_LUID: IGPU_NAME,
                           DGPU_LUID: DGPU_NAME}.get
        gm.pid_to_name = lambda pid: (self._name(pid), "x")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            gm.cmd_list()
        return [int(m.group(1)) for m in
                (re.search(r"\s(\d+)\s+\d+\.\d%", ln)
                 for ln in out.getvalue().splitlines()) if m]

    def test_three_surfaces_share_the_activity_order(self):
        web = self._web_order()
        tray = self._tray_order()
        cli = [p for p in self._cli_order() if p != 300]
        self.assertEqual(web, [700, 500, 100, 900],
                         "Web 行序: 活动量降序(700 靠显存领跑), "
                         "并列(同为 50)按 pid 升序")
        self.assertEqual(tray, web, "托盘自动档必须和 Web 面板同序")
        self.assertEqual(cli, web,
                         "--list 的非噪声行必须和面板同序")
        self.assertLess(web.index(100), web.index(900),
                        "并列该按 pid, 不该被进程名 zzz/aaa 左右")

    def test_list_keeps_the_noise_row_but_panel_hides_it(self):
        self.assertIn(300, self._cli_order(),
                      "--list 是诊断用, 0%/0MB 行仍要看到(README 约定)")
        self.assertNotIn(300, self._web_order(),
                         "面板要把噪声行滤掉")

    def test_default_row_order_is_the_shared_activity_key(self):
        import gpu_tray
        self.assertIs(gpu_tray.activity_key, gm.activity_key,
                      "托盘与面板必须共用同一个 activity_key")
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "config.json")
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump({"threshold_percent": 20}, f)
            app = gpu_tray.TrayApp(cfg)
        self.assertEqual(app.sort_state, {"col": "auto", "desc": True},
                         "托盘默认应落在三面共用的活动量顺序上")


class FakeKernelProcessView:
    """替身 kernel32: denied 里的 pid 一律 OpenProcess 失败。

    paths = {pid: 完整路径}  (OpenProcess + QueryFullProcessImageNameW)
    names = {pid: 映像名}    (Toolhelp 快照, 没有路径)
    snapshot_handle = -1 即 CreateToolhelp32Snapshot 失败。
    """

    def __init__(self, paths=None, names=None, denied=(),
                 snapshot_handle=4321, fail_query=()):
        self.paths = paths or {}
        self.names = names or {}
        self.denied = set(denied)
        self.fail_query = set(fail_query)
        self.snapshot_handle = snapshot_handle
        self.snapshots = 0
        self.entry_sizes = []
        self._enum = None
        self._opened = None

    def OpenProcess(self, _flags, _inherit, pid):
        self._opened = pid
        if pid in self.denied or pid not in self.paths:
            return 0                       # 权限不够, 或进程不存在
        return 1234

    def QueryFullProcessImageNameW(self, _h, _flags, buf, size):
        if self._opened in self.fail_query:
            return 0
        path = self.paths[self._opened]
        buf.value = path
        _set_out(size, len(path))
        return 1

    def CloseHandle(self, _h):
        return 1

    def CreateToolhelp32Snapshot(self, _flags, _pid):
        self.snapshots += 1
        return self.snapshot_handle

    def Process32FirstW(self, _handle, entry):
        self.entry_sizes.append(_deref(entry).dwSize)
        self._enum = iter(self.names.items())
        return self._advance(entry)

    def Process32NextW(self, _handle, entry):
        return self._advance(entry)

    def _advance(self, entry):
        try:
            pid, name = next(self._enum)
        except StopIteration:
            return 0                       # ERROR_NO_MORE_ITEMS
        target = _deref(entry)
        target.th32ProcessID = pid
        target.szExeFile = name
        return 1


class ProcessNameResolution(unittest.TestCase):
    """--list 里成片的 "?" 不是进程不存在, 是 OpenProcess 被拒。

    实测一次采样 39 个进程有 10 个拿不到名字 (System / csrss / dwm / vmwp /
    杀软...), 全是 ERROR_ACCESS_DENIED(5)。Toolhelp 快照只要枚举权限就能把
    名字补上, 代价是拿不到完整路径 —— 于是调用方从此必须容忍 full 为 None。
    """

    def setUp(self):
        self.saved = gm.kernel32
        self.saved_ttl = gm._SNAPSHOT_TTL
        self.saved_cache = gm._name_snapshot
        # 整个换掉, 不是清空: 缓存那两个键必须一直在, 否则取不到就 KeyError
        gm._name_snapshot = {"at": 0.0, "names": {}}

    def tearDown(self):
        gm.kernel32 = self.saved
        gm._SNAPSHOT_TTL = self.saved_ttl
        gm._name_snapshot = self.saved_cache

    def install(self, **kw):
        fake = FakeKernelProcessView(**kw)
        gm.kernel32 = fake
        return fake

    def test_denied_process_still_gets_a_name_without_a_path(self):
        self.install(paths={100: r"D:\app\heavy.exe"},
                     names={4: "System", 100: "heavy.exe"}, denied={4})
        self.assertEqual(gm.pid_to_name(4), ("System", None),
                         "被拒也该有名字, 缺的只是完整路径")
        self.assertEqual(gm.pid_to_name(100),
                         ("heavy.exe", r"D:\app\heavy.exe"),
                         "打得开句柄的老路不能变")

    def test_nothing_found_returns_none(self):
        fake = self.install(paths={}, names={4: "System"})
        self.assertIsNone(gm.pid_to_name(9999))
        self.assertEqual(fake.snapshots, 1,
                         "名字查不到也要真的去翻过快照, 不能直接 None")

    def test_failed_image_name_query_falls_back_to_the_snapshot(self):
        self.install(paths={100: r"D:\app\heavy.exe"},
                     names={100: "heavy.exe"}, fail_query={100})
        self.assertEqual(gm.pid_to_name(100), ("heavy.exe", None),
                         "查不到镜像名不等于进程没了")

    def test_snapshot_entry_sets_dwsize_to_the_struct_size(self):
        fake = self.install(names={4: "System"})
        gm.pid_to_name(9999)
        self.assertEqual(fake.entry_sizes,
                         [ctypes.sizeof(gm._PROCESSENTRY32W)],
                         "dwSize 写错, Windows 直接拒绝枚举")

    def test_snapshot_is_cached_within_the_ttl(self):
        fake = self.install(names={4: "System"})
        gm.pid_to_name(4)
        gm.pid_to_name(4)
        self.assertEqual(fake.snapshots, 1, "TTL 内不该反复拍快照")

    def test_expired_ttl_takes_a_fresh_snapshot(self):
        fake = self.install(names={4: "System"})
        gm._SNAPSHOT_TTL = 0.0
        gm.pid_to_name(4)
        gm.pid_to_name(4)
        self.assertEqual(fake.snapshots, 2, "缓存过期后要重新拍")

    def test_failed_snapshot_is_not_fatal(self):
        fake = self.install(names={}, snapshot_handle=-1)
        self.assertIsNone(gm.pid_to_name(9999))
        self.assertEqual(fake.snapshots, 1, "失败也是真的去试过")
        self.assertEqual(fake.entry_sizes, [],
                         "INVALID_HANDLE_VALUE 不能喂给枚举器")


class SharedLogicIsOneSource(unittest.TestCase):
    """托盘是 gpu_monitor 上的一层薄 UI, 模块级 import 进来的共用逻辑不许被复制/包裹。

    gpu_tray 与 Web 面板必须说同一套话: 凡是模块级 `from gpu_monitor import`
    进来的名字, 在托盘里必须还是**同一个对象**。一旦有人在 gpu_tray 里重写或包装
    其中一个(哪怕只为"微调一下"), 同一个数就会在两个界面给出两套结果。这条把
    activity_key / row_is_noise / heat_class / spark_* / PREF_HIGH_TEXT 那几处
    分散的 is 守卫, 一次性覆盖到模块级 import 的每一个符号; 名单直接从 gpu_tray
    源码顶层 ImportFrom 里 AST 抽取, 将来往 from-import 里加新共用函数会自动纳入。

    只看**模块级** import: 函数体内 `from gpu_monitor import notify` 那种局部引用
    不进名单(它不是 gpu_tray 的模块属性, 与"两界面共用"无关)。
    """

    def _imported_gpu_monitor_names(self):
        import ast
        import gpu_tray
        with io.open(gpu_tray.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = []
        for node in tree.body:            # 只看顶层, 不含函数内局部 import
            if isinstance(node, ast.ImportFrom) and node.module == "gpu_monitor":
                names.extend(a.name for a in node.names)
        return names

    def _divergent(self, names):
        import gpu_tray
        return [n for n in names
                if getattr(gpu_tray, n) is not getattr(gm, n)]

    def test_every_imported_name_is_the_same_object(self):
        names = self._imported_gpu_monitor_names()
        self.assertTrue(names, "没从 gpu_tray 顶层抽到 import 名单, 抽取逻辑该修了")
        self.assertEqual(self._divergent(names), [],
                         "这些共用符号被托盘复制/包装了一份")

    def test_the_tray_is_imported_before_any_stub_can_exist(self):
        """gpu_tray 的 import 必须留在本模块顶层, 不许搬进用例里。

        "共用同一份逻辑"有个隐性前提: gpu_tray 顶层
        `from gpu_monitor import pid_to_name` 绑的是 import 那一刻的值。
        谁第一个 import gpu_tray, 这些名字就绑到那一刻的 gm 属性上 ——
        放进用例里 import, 撞上的正是 ListTableLayout.setUp 挂着的
        NAMES.get 替身; 托盘于是永久拿住那份替身, 而还原只发生在 gm
        那一侧。上面那条 assertIs 拿活的值一比反而看不出来。顶层 import
        让绑定落在任何替身出现之前。这条用 AST 看守这个前提。
        """
        with io.open(__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        top = [n for n in tree.body
               if isinstance(n, (ast.Import, ast.ImportFrom))
               and any(a.name.split(".")[0] == "gpu_tray"
                       for a in n.names)]
        self.assertTrue(top, "gpu_tray 必须在本模块顶层 import, "
                            "否则共用绑定会被 import 顺序污染")

    def test_a_copied_shared_function_is_caught(self):
        """回退验证: 托盘若自造一个同名函数, 分歧名单必须点名 heat_class。"""
        import gpu_tray
        saved = gpu_tray.heat_class
        self.addCleanup(setattr, gpu_tray, "heat_class", saved)
        gpu_tray.heat_class = lambda pct: "cool"   # 模拟有人复制一份
        self.assertIn("heat_class", self._divergent(
            self._imported_gpu_monitor_names()))


class HeatClass(unittest.TestCase):
    """凉/温/热三档的分界线: 图标、表格、汇总卡共用它。"""

    def test_boundaries(self):
        for pct, want in ((0, "cool"), (29.9, "cool"), (30, "warm"),
                          (59.9, "warm"), (60, "hot"), (100, "hot")):
            with self.subTest(pct=pct):
                self.assertEqual(gm.heat_class(pct), want)

    def test_the_tray_shares_the_one_heat_class(self):
        # 热度阈值只能有一处: 托盘图标/表格与 Web 面板共用同一个 heat_class,
        # 别在 gpu_tray 抄一份 30/60, 否则同一个数两面染成两个色。
        import gpu_tray
        self.assertIs(gpu_tray.heat_class, gm.heat_class,
                      "托盘必须复用 gpu_monitor 的 heat_class, 别自己抄阈值")

    def test_icon_rgba_decodes_the_palette(self):
        import gpu_tray
        # 色值跟当前系统的浅/深主题走, 所以不钉某个 hex, 钉同源性。
        hot = gpu_tray._HEAT_COLOR["hot"].lstrip("#")
        self.assertEqual(gpu_tray._icon_rgba("hot"),
                         (int(hot[0:2], 16), int(hot[2:4], 16),
                          int(hot[4:6], 16), 255))
        fg = gpu_tray._FG.lstrip("#")
        self.assertEqual(gpu_tray._icon_rgba(""),
                         (int(fg[0:2], 16), int(fg[2:4], 16),
                          int(fg[4:6], 16), 255),
                         "空类也要有颜色, 和 _heat_color 同一个来源")

    def test_the_icon_paints_with_the_shared_palette(self):
        """图标自己存一套 RGB 的话, 同一个数在托盘和表格里两个色。"""
        import gpu_tray

        fills = []

        class _Recorder:
            def ellipse(self, *a, **kw):
                fills.append(kw["fill"])

            def text(self, *a, **kw):
                pass

            def textbbox(self, _box, _text, font=None):
                return (0, 0, 10, 10)

        saved = gpu_tray.ImageDraw
        gpu_tray.ImageDraw = type("M", (), {"Draw": lambda _i: _Recorder()})
        try:
            for pct in (10, 30, 60, 90):
                gpu_tray._make_icon_image(pct)
        finally:
            gpu_tray.ImageDraw = saved
        self.assertEqual(fills, [gpu_tray._icon_rgba(c) for c in
                                 ("cool", "warm", "hot", "hot")],
                         "图标三档必须落在表格那一套色上")

    def test_icon_font_falls_back_across_missing_fonts(self):
        u"""arialbd 缺失时按序找备用粗体, 全缺失才退内置位图字。

        load_default() 是 11px 左右的位图字, 缩到 16x16 任务栏后数字糊成
        一团 —— 没装 Arial Bold 的机器不能因此就看不清托盘数字。
        """
        import gpu_tray

        tried = []
        saved = gpu_tray.ImageFont.truetype

        def fake_truetype(name, size, **kw):
            tried.append(name)
            if name != "msyhbd.ttc":
                raise OSError(name)
            # load_default() 自己也要走 truetype(..., layout_engine=), 桩把这个
            # 关键字一起吃成 TypeError —— 借真的 truetype 用完就还。
            gpu_tray.ImageFont.truetype = saved
            try:
                return gpu_tray.ImageFont.load_default()
            finally:
                gpu_tray.ImageFont.truetype = fake_truetype

        gpu_tray.ImageFont.truetype = fake_truetype
        try:
            img = gpu_tray._make_icon_image(50)
        finally:
            gpu_tray.ImageFont.truetype = saved
        self.assertEqual(tried, ["arialbd.ttf", "segoeuib.ttf", "msyhbd.ttc"])
        self.assertIsInstance(img, gpu_tray.Image.Image,
                             "回退路径也要画得出一幅图标")



class PanelSummaryCards(unittest.TestCase):
    """面板顶部那排汇总数: 口径只有一份, 托盘悬停不能另算一套。"""

    def _snap(self, **kw):
        return {**ModeDisplay.SNAP, **kw}

    def _card(self, snap, label):
        return {l: (t, c) for l, t, c, _tip in gm.panel_summary(snap)}[label]

    def test_an_idle_machine_still_shows_all_six_cards(self):
        summary = gm.panel_summary(self._snap())
        self.assertEqual([t for _l, t, _c, _tip in summary],
                         ["0.0%", "0.0%", "0 MiB", "0 MiB", "0", "40%"],
                         "开机空转也要有这六项, 少一项面板就塌一块")

    def test_missing_or_null_maps_are_not_fatal(self):
        """托盘和 Web 读到的快照不保证键齐全, 缺一个也不能让面板崩。"""
        for snap in ({}, {"util_by_pid": None, "gpu_all_by_pid": None,
                          "dgpu_util_by_pid": None, "shared_by_pid": None,
                          "dedicated_by_pid": None, "threshold": None}):
            with self.subTest(snap=snap):
                texts = [t for _l, t, _c, _tip in gm.panel_summary(snap)]
                self.assertEqual(texts, ["0.0%", "0.0%", "0 MiB", "0 MiB",
                                         "0", "0%"])

    def test_occupancy_is_the_busiest_process_not_the_sum(self):
        """相加会把 3 个 20% 显示成 60%, 看着像快满了, 其实没人满载。"""
        snap = self._snap(util_by_pid={1: 20.0, 2: 20.0, 3: 20.0},
                          dgpu_util_by_pid={1: 80.0})
        self.assertEqual(self._card(snap, "核显占用"), ("20.0%", "cool"))
        self.assertEqual(self._card(snap, "独显占用"), ("80.0%", "hot"))

    def test_memory_is_summed_across_processes(self):
        mb = 1024 * 1024
        snap = self._snap(dedicated_by_pid={1: mb, 2: 2 * mb},
                          shared_by_pid={1: 3 * mb, 3: mb})
        self.assertEqual(self._card(snap, "专用显存")[0], "3 MiB")
        self.assertEqual(self._card(snap, "共享显存")[0], "4 MiB")

    def test_the_process_count_is_the_union_of_all_maps(self):
        snap = self._snap(util_by_pid={1: 5.0},
                          dedicated_by_pid={2: 1024, 3: 1024},
                          shared_by_pid={4: 1024})
        self.assertEqual(self._card(snap, "GPU 进程")[0], "4",
                         "只在共享显存里露过脸的进程也是 GPU 进程")

    def test_the_cards_agree_with_the_tray_tooltip(self):
        """悬停写的占用和面板汇总卡必须是同一个数, 否则用户以为看错。"""
        # 专挑带小数的值: %.0f 会把 62.7% 收成 63%, 卡片却写 62.7%,
        # 两边对不上正是这里要钉死的。整数值(63.0)会把这条测试放过去。
        util = {7: 3.2, 8: 62.7}
        snap = self._snap(util_by_pid=util)
        title = TrayModeDisplay()._title(snap)
        text, cls = self._card(snap, "核显占用")
        self.assertEqual(text, "62.7%")
        self.assertEqual(cls, "hot")
        self.assertIn("核显占用 62.7%", title,
                      "悬停和汇总卡必须逐字同一个数, 不能一个取整一个小数")


    def test_every_card_carries_a_nonempty_tip(self):
        """六张卡都必须有悬停解释, 空的等于没有实现。

        panel_summary 是四元组之后, 第四项空串会安静地退化成旧行为 ——
        HTML 里 title='' 是个合法属性, 什么测试都不会红。所以这里逐个
        卡查非空, 并且规定最短长度, 免得将来塞个"占用"两个字交差。
        """
        for label, _t, _c, tip in gm.panel_summary(ModeDisplay.SNAP):
            self.assertTrue(tip.strip(), label + " 缺悬停解释")
            self.assertGreater(len(tip), 10)

    def test_the_card_tip_reaches_the_html_title(self):
        """Web 卡片悬停 = panel_summary 第四项, 不是另写的一套文案。

        Web 端没有托盘那种 Tk 弹泡, title 属性就是全部实现; 口径跑偏的
        话两边说着两件事, 用户只能信一个。
        """
        html = gm.panel_html(ModeDisplay.SNAP, {})
        for label, _t, _c, tip in gm.panel_summary(ModeDisplay.SNAP):
            self.assertIn(f"<div class=card title='{tip}'>"
                          f"<span>{label}</span>", html)

    def test_every_header_carries_a_nonempty_tip(self):
        """八个表头都要有解释: 专用MB 和 GPU% 光看列名看不出口径。

        表头解释放在 _HDR_TIPS, 与托盘列头共用; 漏一个的表现是那一列
        悬停什么都不弹, 而别的列都弹 —— 很难看, 也看不出是漏了。
        """
        for col in ("name", "pid", "gpu", "igpu", "dgpu",
                    "dmem", "smem", "pref"):
            tip = gm._HDR_TIPS.get(col, "")
            self.assertTrue(tip.strip(), col + " 缺表头解释")
            html = gm.panel_html(ModeDisplay.SNAP, {})
            self.assertIn(gm._tip(col) + ">", html)

    def test_summary_cards_do_not_emit_an_empty_class_attribute(self):
        """不需要热度类的卡不能输出 `class=`。

        panel_summary 对显存/进程数/阈值那几张返回空类名, 而模板无条件
        拼 `class={cls}`, 于是产出 `<b class=>11712 MB</b>` —— 无值属性
        是畸形 HTML。浏览器会兜底成 class="", 看着没差别, 但它说明模板
        没有为"可选的类名"这条路上心: 哪天 cls 里带进空格或符号, 属性
        就会当场截断。托盘侧 _heat_color("") 有兜底, Web 这边没有。
        """
        html = gm.panel_html({"util_by_pid": {100: 50.0},
                              "igpu_luids": set(), "dgpu_luids": set(),
                              "mode": "saver", "threshold": 40.0}, {})
        self.assertNotIn("class=>", html,
                         "空的 class= 是畸形 HTML, 见 panel_html 汇总卡")
        for label in ("专用显存", "共享显存", "GPU 进程", "生效阈值"):
            self.assertIn(label, html, "汇总卡少了一张: " + label)
        # 有热度类的那两张照旧带 class
        self.assertIn("<b class=warm>50.0%</b>", html)

class TrayNameCacheTtl(unittest.TestCase):
    """pid 会复用: 名字缓存不能白头到老, 否则新进程顶着旧进程的名字。"""

    def setUp(self):
        import gpu_tray
        self.tray = gpu_tray
        self.now = [1000.0]
        self.saved = (gpu_tray.pid_to_name, gpu_tray.time)
        gpu_tray.time = type("T", (), {"time": staticmethod(
            lambda: self.now[0])})()

    def tearDown(self):
        self.tray.pid_to_name, self.tray.time = self.saved

    def _app(self, names):
        app = object.__new__(self.tray.TrayApp)
        app._name_cache = {}
        app._name_ts = 0.0
        seq = list(names)

        def fake(pid):
            return seq.pop(0) if len(seq) > 1 else seq[0]

        self.tray.pid_to_name = fake
        return app

    def test_a_recycled_pid_gets_a_fresh_name(self):
        """同一个 pid, 十分钟前是 chrome, 现在是 game —— 面板不能还写 chrome。

        Windows 会回收 pid。名字缓存原来只按 2000 条上限整体清一次,
        托盘开上几小时不散架, 复用 pid 的行就一直顶着上个进程的名字:
        那一行的 GPU% 是新进程的, 名字是旧进程的, 面板当面指鹿为马。
        """
        app = self._app([("chrome.exe", None), ("game.exe", None)])
        self.assertEqual(app._pid_name(1234), "chrome.exe")
        self.now[0] += 30          # 超过 TTL: 必须重查, 不能再报旧名
        self.assertEqual(app._pid_name(1234), "game.exe")

    def test_within_the_ttl_the_name_is_not_refetched(self):
        """TTL 内还要认缓存: 不然每帧每个 pid 一次 OpenProcess。"""
        app = self._app([("chrome.exe", None), ("other.exe", None)])
        self.assertEqual(app._pid_name(1234), "chrome.exe")
        self.now[0] += 1
        self.assertEqual(app._pid_name(1234), "chrome.exe")


class TrayFilterBox(unittest.TestCase):
    """筛选框: 进程名子串 / PID / 大小写不敏感, 空词全显。"""

    def setUp(self):
        import gpu_tray
        self.app = object.__new__(gpu_tray.TrayApp)
        self.app.filter_text = ""

    def test_a_blank_needle_keeps_every_row(self):
        self.assertTrue(self.app._matches_filter("msedge.exe", 100))
        self.assertTrue(self.app._matches_filter("", 0))

    def test_a_name_substring_matches(self):
        self.app._set_filter("edge")
        self.assertTrue(self.app._matches_filter("msedge.exe", 100))
        self.assertFalse(self.app._matches_filter("steam.exe", 200))

    def test_case_and_padding_do_not_matter(self):
        self.app._set_filter("  MSEdge  ")
        self.assertTrue(self.app._matches_filter("msedge.exe", 100))

    def test_the_pid_is_searchable_too(self):
        self.app._set_filter("200")
        self.assertTrue(self.app._matches_filter("steam.exe", 200))
        self.assertFalse(self.app._matches_filter("steam.exe", 201))

    def test_the_setter_survives_a_none(self):
        self.app._set_filter(None)
        self.assertEqual(self.app.filter_text, "")
        self.assertTrue(self.app._matches_filter("x.exe", 1))


class TrayPanelChrome(unittest.TestCase):
    """面板新加的三样: 汇总卡、筛选生效、行内热度色。"""

    def setUp(self):
        import gpu_tray
        self.tray = gpu_tray
        self.app = object.__new__(gpu_tray.TrayApp)
        self.app.sort_state = {"col": "igpu", "desc": True}
        self.app.filter_text = ""
        self.app.lock = threading.Lock()
        self.app._name_cache = {}
        self.app.snapshot = None
        self.rows = []
        self.saved_pid_to_name = gpu_tray.pid_to_name
        gpu_tray.pid_to_name = lambda _pid: None

    def tearDown(self):
        self.tray.pid_to_name = self.saved_pid_to_name

    class _Tree:
        """只记 insert 的 values 和 tags。"""

        def __init__(self, sink):
            self.sink = sink

        def delete(self, *_items):
            self.sink.append(("delete", _items, None))

        def insert(self, _parent, _pos, values=None, tags=None):
            self.sink.append(("insert", values, tags))

        def get_children(self):
            return ()

    class _Label:
        def __init__(self):
            self.calls = []

        def config(self, text=None, fg=None):
            self.calls.append((text, fg))

    def _snap(self, pids, util=None):
        snap = {"util_by_pid": dict(util or {}), "gpu_all_by_pid": {},
                "dgpu_util_by_pid": {}, "shared_by_pid": {},
                "dedicated_by_pid": {}, "igpu_luids": [0], "dgpu_luids": []}
        for pid, (gpu_all, mem_mb) in pids.items():
            snap["gpu_all_by_pid"][pid] = gpu_all
            snap["dedicated_by_pid"][pid] = int(mem_mb * 1024 * 1024)
        return snap

    def _prepare(self, names):
        self.app._pid_name = lambda pid: names[pid]
        self.app._pref_of = lambda _p: False
        self.app._gpu_name = lambda luid: f"gpu{luid}"

    def _inserted(self):
        return [v for kind, v, _tags in self.rows if kind == "insert"]

    def test_summary_cards_take_the_shared_numbers(self):
        cards = {label: self._Label()
                 for label, _t, _c, _tip in gm.panel_summary({})}
        snap = self._snap({100: (50.0, 2.0)}, util={100: 70.0})
        self.app.snapshot = snap
        self._prepare({100: "heavy.exe"})
        self.app._refresh_tree(self._Tree(self.rows), self._Label(), cards,
                               self._Label())
        hot = self.tray._heat_color("hot")
        self.assertEqual(cards["核显占用"].calls, [("70.0%", hot)],
                         "汇总卡必须用 panel_summary 的数, 不能自己再算")
        self.assertEqual(cards["GPU 进程"].calls,
                         [("1", self.tray._heat_color(""))])

    def test_filter_entry_hides_rows_and_the_hint_counts_them(self):
        snap = self._snap({100: (50.0, 2.0), 200: (10.0, 2.0)},
                          util={100: 50.0, 200: 10.0})
        self.app.snapshot = snap
        self._prepare({100: "msedge.exe", 200: "steam.exe"})
        hint = self._Label()
        self.app._set_filter("EDGE")
        self.app._refresh_tree(self._Tree(self.rows), self._Label(), None,
                               hint)
        self.assertEqual([v[0] for v in self._inserted()], ["msedge.exe"])
        self.assertEqual(hint.calls[-1][0],
                         "显示 1/2 行   (按 Esc 关闭窗口)")

    def test_rows_carry_their_heat_colour_tag(self):
        """整行热度跟着"GPU%"列(gpu_all), 不再只看核显%。

        独显吃满、核显闲着的进程(gpu_all 高、核显% 为 0), 以前整行是
        绿的, 却因复合键排在最前, 颜色和顺序自相矛盾; 现在按 gpu_all
        上色, 和它 GPU% 格在 Web 里的上色一致。
        """
        snap = self._snap({100: (70.0, 2.0), 200: (5.0, 2.0)},
                          util={100: 0.0, 200: 5.0})
        snap["dgpu_util_by_pid"] = {100: 70.0}
        self.app.snapshot = snap
        self.app.sort_state = {"col": "gpu", "desc": True}
        self._prepare({100: "hot.exe", 200: "cool.exe"})
        self.app._refresh_tree(self._Tree(self.rows), self._Label())
        self.assertEqual([tags for kind, _v, tags in self.rows
                          if kind == "insert"],
                         [("hot",), ("cool", "rowalt")],
                         "整行按 GPU% 列热度上色, 独显吃满也该是 hot; "
                         "第二行是奇数行, 顺手带上隔行底 tag")
        self.assertEqual([v[0] for v in self._inserted()],
                         ["hot.exe", "cool.exe"])
    def test_hidden_rows_are_counted_in_the_hint(self):
        """表格只放得下 30 行。

        静默截断会让人以为机器上就这些进程在占显卡,
        多出来多少行得说出来。
        """
        pids = range(200, 240)
        snap = self._snap({p: (float(p), 2.0) for p in pids},
                          util={p: float(p) for p in pids})
        self.app.snapshot = snap
        self._prepare({p: f"p{p}.exe" for p in pids})
        hint = self._Label()
        self.app._refresh_tree(self._Tree(self.rows), self._Label(), None,
                               hint)
        self.assertEqual(len(self._inserted()), 30, "表格最多 30 行")
        self.assertIn("显示 40/40 行", hint.calls[-1][0])
        self.assertIn("(还有 10 行未显示)", hint.calls[-1][0])

    def test_the_hint_says_how_many_zero_rows_were_dropped(self):
        """汇总卡报的是真 union 数, 表格只留非噪声行, 差多少要说出来。"""
        snap = self._snap({100: (50.0, 2.0), 300: (0.0, 0.0)},
                          util={100: 50.0, 300: 0.0})
        self.app.snapshot = snap
        self._prepare({100: "heavy.exe", 300: "idle.exe"})
        hint = self._Label()
        self.app._refresh_tree(self._Tree(self.rows), self._Label(), None,
                               hint)
        text = hint.calls[-1][0]
        self.assertIn("显示 1/1 行", text)
        self.assertIn("(已隐藏 1 个 0.0% / 0MiB)", text)

    def test_all_zero_rows_never_reach_the_table(self):
        """0.0% / 0MB / 0MB 的行: Web 面板按同一个判据滤掉。"""
        snap = self._snap({100: (50.0, 2.0), 300: (0.0, 0.0)},
                          util={100: 50.0, 300: 0.0})
        self.app.snapshot = snap
        self._prepare({100: "heavy.exe", 300: "idle.exe"})
        self.app._refresh_tree(self._Tree(self.rows), self._Label())
        self.assertEqual([v[0] for v in self._inserted()],
                         ["heavy.exe"],
                         "全零行进表格只会把该看的行挤下去")


class DetailGpuWhere(unittest.TestCase):
    """详情窗"当前所在": 两侧噪声地板必须对称, 否则面板说噪声、详情窗说独显。"""

    def test_the_dgpu_side_gets_the_same_noise_floor(self):
        """dg=0.08/ig=0.05 老代码也敢讲"独显"。

        核显侧本来就有 ig > 0.1 的地板, 独显侧光比大小: 两个都在噪声
        级别的读数, 会被说成正在独显上跑。而 row_is_noise 判同一组数
        是噪声行, 面板压根不显示这一行 —— 两处当着用户的面打架。
        """
        self.assertEqual(gm.gpu_where_text(0.05, 0.08), "未见 GPU 活动")

    def test_a_busy_dgpu_process_says_dgpu(self):
        self.assertEqual(gm.gpu_where_text(0.0, 70.0), "独显")

    def test_a_busy_igpu_process_says_igpu(self):
        self.assertEqual(gm.gpu_where_text(3.0, 0.05), "核显")

    def test_both_above_the_floor_picks_the_bigger_one(self):
        self.assertEqual(gm.gpu_where_text(2.0, 15.0), "独显")
        self.assertEqual(gm.gpu_where_text(15.0, 2.0), "核显")

    def test_agrees_with_row_is_noise_on_what_counts_as_activity(self):
        """说"未见 GPU 活动"的那一刻, row_is_noise 也必须判这是噪声行。

        地板各写一份的话, 迟早一边 0.1 一边 0.5: 面板留着行,
        详情窗说没活动, 又一处同一台机器两个读数。
        """
        for ig, dg in ((0.05, 0.02), (0.0, 0.09), (0.1 - 1e-9, 0.0)):
            self.assertEqual(gm.gpu_where_text(ig, dg), "未见 GPU 活动")
            self.assertTrue(gm.row_is_noise(max(ig, dg), 0.0, 0.0),
                            f"地板不一致: ig={ig} dg={dg}")
        for ig, dg in ((5.0, 0.0), (0.0, 5.0)):
            self.assertNotEqual(gm.gpu_where_text(ig, dg), "未见 GPU 活动")
            self.assertFalse(gm.row_is_noise(max(ig, dg), 0.0, 0.0))


class TooltipSurrogatePairs(unittest.TestCase):
    u"""szTip 是 TCHAR[128]: 算的是 UTF-16 码元, 不是码点。"""

    def _u16(self, text):
        return len(text.encode("utf-16-le")) // 2

    def _base(self, emoji):
        return ("GPU 监控  核显占用 50.0%\n"
                "模式 省电, 阈值 40%, 自动切回 开\n"
                "独显温度: 61°C\n"
                "核显: " + emoji * 30
                + " Intel(R) UHD Graphics 630")

    def test_a_surrogate_pair_name_still_fits_sztip(self):
        u"""显卡名里进了 emoji / 生偏字, 按码点截会超限。

        那类字符在 UTF-16 下占两个码元, len() 只数出一个:
        按码点截到 127, 实际 utf-16 长度超出去一半,
        szTip 一超限就整条不显示 —— 悬停过去
        什么都没有, 用户还以为监控挂了。
        """
        import gpu_tray

        for sfx in ("", "  [已暂停]"):
            tip = gpu_tray.fit_tip_title(self._base("\U0001F600"), sfx)
            self.assertLessEqual(self._u16(tip), gpu_tray._TIP_MAX,
                                 f"utf-16 超限: {self._u16(tip)}")
            # 不能把占用率/策略两行开掉
            self.assertIn("核显占用 50.0%", tip)
            self.assertIn("模式 省电", tip)

    def test_a_short_tip_is_not_touched(self):
        u"""放得下就一字别动: 无谓截断只会吃信息。"""
        import gpu_tray

        base = ("GPU 监控  核显占用 50.0%\n模式 省电\n"
                "核显: Intel(R) UHD Graphics 630")
        self.assertEqual(gpu_tray.fit_tip_title(base), base)
        self.assertEqual(gpu_tray.fit_tip_title(base, "  [已暂停]"),
                         base + "  [已暂停]")

    def test_a_hard_truncation_never_splits_a_surrogate_pair(self):
        u"""硬截断也得整字符地开: 单留一个孤子代理,
        Windows 端只看得到一个无效字符。"""
        import gpu_tray

        tip = gpu_tray.fit_tip_title("\U0001F600" * 200)
        self.assertLessEqual(self._u16(tip), gpu_tray._TIP_MAX)
        self.assertNotIn("\ud83d", tip[:-1], "剥出了孤子代理")


class TrayWindowChrome(unittest.TestCase):
    """设置窗/详情窗也吃深色: 经典 tk 控件不归 ttk 主题管。"""

    def setUp(self):
        import gpu_tray
        self.tray = gpu_tray

    class _W:
        """假控件: 记下最后一次 configure 的选项合并结果。"""

        def __init__(self, cls, children=()):
            self._cls = cls
            self._children = list(children)
            self.opts = {}

        def winfo_class(self):
            return self._cls

        def winfo_children(self):
            return self._children

        def configure(self, **kw):
            self.opts.update(kw)

    def test_classic_widgets_get_the_panel_palette(self):
        entry = self._W("Entry")
        label = self._W("Label")
        button = self._W("Button")
        root = self._W("Toplevel", [label, entry, button])
        self.tray._darken_tree(root)
        self.assertEqual(root.opts.get("bg"), self.tray._BG)
        self.assertEqual(root.opts.get("fg"), self.tray._FG)
        self.assertEqual(label.opts.get("bg"), self.tray._BG)
        self.assertEqual(label.opts.get("fg"), self.tray._FG)
        self.assertEqual(entry.opts.get("bg"), self.tray._PANEL_BG)
        self.assertEqual(entry.opts.get("insertbackground"), self.tray._FG,
                         "深色底上的光标必须是浅色, 不然击键看不见落点")
        self.assertEqual(button.opts.get("activebackground"),
                         self.tray._BTN_HOVER)

    def test_text_and_menu_get_selection_colours(self):
        text = self._W("Text")
        menu = self._W("Menu")
        self.tray._darken_tree(self._W("Toplevel", [text, menu]))
        self.assertEqual(text.opts.get("selectbackground"),
                         self.tray._SEL_BG)
        self.assertEqual(text.opts.get("selectforeground"), self.tray._FG)
        # Menu 没有 select*, 高亮项走 active*: 同一个色, 同一处生效
        self.assertEqual(menu.opts.get("activebackground"),
                         self.tray._SEL_BG)
        self.assertEqual(menu.opts.get("activeforeground"), self.tray._FG)

    def test_nested_children_are_darkened_too(self):
        deep = self._W("Label")
        mid = self._W("Frame", [deep])
        root = self._W("Toplevel", [mid])
        self.tray._darken_tree(root)
        self.assertEqual(deep.opts.get("bg"), self.tray._BG,
                         "嵌在 Frame 里的 Label 也得刷, 不能只刷一层")

    def test_unknown_classes_only_get_base_colours(self):
        check = self._W("Checkbutton")
        spin = self._W("Spinbox")
        self.tray._darken_tree(self._W("Toplevel", [check, spin]))
        self.assertEqual(spin.opts.get("bg"), self.tray._BG)
        self.assertEqual(check.opts.get("selectcolor"), self.tray._PANEL_BG)
        self.assertNotIn("insertbackground", spin.opts,
                         "不认识的控件不能乱塞选项, 真 tk 会 TclError")


class TrayRightClickSelection(unittest.TestCase):
    """右键"永不迁移"要作用在光标那一行, 不是上次左键选中的那一行。"""

    def setUp(self):
        import gpu_tray
        self.tray = gpu_tray

    class _Tree:
        """够用的假树, 记住选中项; 认定光标下恒为 clicked 行。"""

        def __init__(self, clicked="B", clicked_vals=None, selected=("A",)):
            self.clicked = clicked
            self.clicked_vals = (["payload.exe"] if clicked_vals is None
                                 else clicked_vals)
            self.selected = selected
            self.set_calls = []

        def identify_row(self, _y):
            return self.clicked

        def item(self, _iid, _what=None):
            return self.clicked_vals

        def selection(self):
            return self.selected

        def selection_set(self, item):
            self.set_calls.append(item)
            self.selected = (item,)

    def test_right_click_selects_the_row_under_the_cursor(self):
        tree = self._Tree(clicked="B", selected=("A",))
        self.assertEqual(self.tray.TrayApp._pick_row(tree, 10), "B")
        self.assertEqual(tree.selection(), ("B",),
                         "右键不改选中项, 菜单就会按上次左键的 A 行行事")
        self.assertEqual(tree.set_calls, ["B"])

    def test_right_click_on_blank_space_selects_nothing(self):
        tree = self._Tree(clicked="")
        self.assertIsNone(self.tray.TrayApp._pick_row(tree, 10))
        self.assertEqual(tree.selection(), ("A",),
                         "点在行间空白处不该动选中项")
        self.assertEqual(tree.set_calls, [])

    def test_a_valueless_row_is_skipped(self):
        tree = self._Tree(clicked="Z", clicked_vals=[], selected=("A",))
        self.assertIsNone(self.tray.TrayApp._pick_row(tree, 10))
        self.assertEqual(tree.set_calls, [],
                         "空状态行没有 values, 不该被选中")


class TrayDetailPrefStatus(unittest.TestCase):
    """详情窗现在显示当前 GPU 首选项, "设为独显/撤销"不再盲操作。"""

    def setUp(self):
        import gpu_tray
        self.tray = gpu_tray

    def _app(self, high_perf):
        app = object.__new__(self.tray.TrayApp)
        app._pref_of = lambda _full: high_perf
        return app

    def test_an_existing_exe_reports_its_current_preference(self):
        me = os.path.realpath(__file__)
        self.assertEqual(self._app(True)._pref_status(me),
                         "已设为独显 (重启该程序后生效)")
        self.assertEqual(self._app(False)._pref_status(me),
                         "未设置 (按系统默认/自动)")

    def test_a_missing_path_says_so_without_querying(self):
        probed = []
        app = object.__new__(self.tray.TrayApp)
        app._pref_of = lambda _full: probed.append(1) or True
        for bad in (None, "", "未知 (需要管理员权限)"):
            self.assertEqual(app._pref_status(bad), "未知 (拿不到完整路径)")
        self.assertEqual(probed, [], "拿不到路径就别去查注册表")

    def test_a_fresh_write_makes_the_next_read_hit_the_registry(self):
        """写/清注册表之后下一帧就得是新答案, 不能还按十秒前的缓存说话。"""
        app = object.__new__(self.tray.TrayApp)
        app._pref_cache = {}
        app._pref_ts = 0.0
        # 每个答案对应一次读: 首查 / 写完回读 / 列上那一读 / 清完回读 /
        # 再一读。§54 起写和清都要回读确认, 比原来多两次。
        answers = ["GpuPreference=1;", "GpuPreference=2;", "GpuPreference=2;",
                   "GpuPreference=1;", "GpuPreference=1;"]
        reads = []
        writes = []
        saved = {n: getattr(self.tray, n) for n in
                 ("get_gpu_preference", "set_gpu_preference",
                  "clear_gpu_preference")}

        def fake_get(_full):
            reads.append(1)
            return answers.pop(0)

        self.tray.get_gpu_preference = fake_get
        self.tray.set_gpu_preference = writes.append
        self.tray.clear_gpu_preference = writes.append
        try:
            self.assertFalse(app._pref_of("C:/app/a.exe"))
            self.assertEqual(len(reads), 1, "没改过就该吃缓存, 别每秒查注册表")
            app._pref_apply("C:/app/a.exe")
            self.assertEqual(writes, ["C:/app/a.exe"])
            self.assertTrue(app._pref_of("C:/app/a.exe"),
                            "刚设完独显, 列上不能还写着没设")
            app._pref_apply("C:/app/a.exe", clear=True)
            self.assertEqual(writes, ["C:/app/a.exe"] * 2)
            self.assertFalse(app._pref_of("C:/app/a.exe"),
                             "刚撤销完, 列上也别还写着已设独显")
            self.assertEqual(len(reads), 5,
                             "每次改完该重查注册表, 写完还得自己回读一次")
        finally:
            for name, fn in saved.items():
                setattr(self.tray, name, fn)


    def test_a_write_that_did_not_stick_is_reported_as_failure(self):
        """写完必须回读确认: 写入 API 说不算数, 注册表里真是高性能才算。

        杀软/策略拦截 RegSetValueEx 的上层封装时, 调用可能不报错却什么都没
        落下。老代码写完就弹"已设为独显: 重启后生效", 用户重启程序发现还在
        核显上 —— 比一开始就说失败糟得多。
        """
        app = object.__new__(self.tray.TrayApp)
        app._pref_cache = {}
        app._pref_ts = 0.0
        saved = {n: getattr(self.tray, n) for n in
                 ("get_gpu_preference", "set_gpu_preference",
                  "clear_gpu_preference")}
        writes = []
        self.tray.set_gpu_preference = writes.append
        self.tray.clear_gpu_preference = lambda full: writes.append(full)
        try:
            self.tray.get_gpu_preference = lambda _f: None     # 写是写了, 没落地
            self.assertFalse(app._pref_apply("C:/app/a.exe"),
                             "回读不到高性能就该算失败")
            self.assertEqual(writes, ["C:/app/a.exe"])

            writes.clear()
            self.tray.get_gpu_preference = lambda _f: "GpuPreference=2;"
            self.assertFalse(app._pref_apply("C:/app/a.exe", clear=True),
                             "删了却还在, 也不能算清掉了")
            self.assertEqual(writes, ["C:/app/a.exe"])
        finally:
            for name, fn in saved.items():
                setattr(self.tray, name, fn)

    def test_a_failed_set_reports_failure_without_raising(self):
        u"""写注册表抛 OSError 时不能把异常退到 tk 回调。

        tk 回调里的异常只会往冒号后的 stderr 打一条
        traceback, pythonw/—noconsole 下默认丢掉: 用户看到的就是
        "点完没反应"。
        """
        app = object.__new__(self.tray.TrayApp)
        app._pref_cache = {}
        saved = self.tray.set_gpu_preference

        def boom(_full):
            raise OSError("RegSetValueEx failed")

        self.tray.set_gpu_preference = boom
        try:
            self.assertFalse(app._pref_apply("C:/app/a.exe"))
        finally:
            self.tray.set_gpu_preference = saved
        self.assertEqual(app._pref_cache, {},
                         "没写成就别作废缓存")

    def test_a_clear_with_nothing_there_is_not_a_failure(self):
        """本来就没有独显设置时: 不算失败, 但也不该谎称"已清除"。"""
        app = object.__new__(self.tray.TrayApp)
        app._pref_cache = {}
        app._pref_ts = 0.0
        saved = (self.tray.clear_gpu_preference,
                 self.tray.get_gpu_preference)
        self.tray.clear_gpu_preference = lambda _f: None    # 啥也没有
        self.tray.get_gpu_preference = lambda _f: None
        try:
            self.assertTrue(app._pref_apply("C:/app/a.exe", clear=True),
                            "没东西可清不是失败")
            self.assertEqual(getattr(app, "_pref_err", None), "noop",
                             "得让调用方知道这是空清除, 好改口")
        finally:
            (self.tray.clear_gpu_preference,
             self.tray.get_gpu_preference) = saved

    def test_a_blocked_clear_is_not_blamed_on_a_missing_setting(self):
        """真失败(键打不开/删不掉)不能说成"可能本来就没有"。

        "可能本来就没有独显设置"这句在老代码里是唯一的失败文案: 权限被
        策略挡掉时它也这么说, 于是用户以为一切正常。失败必须自带原因。
        """
        app = object.__new__(self.tray.TrayApp)
        app._pref_cache = {}
        app._pref_ts = 0.0
        saved = (self.tray.clear_gpu_preference,
                 self.tray.get_gpu_preference)
        self.tray.clear_gpu_preference = lambda _f: False   # 真失败
        self.tray.get_gpu_preference = lambda _f: None
        try:
            self.assertFalse(app._pref_apply("C:/app/a.exe", clear=True))
            err = getattr(app, "_pref_err", None)
            self.assertTrue(err and err != "noop",
                            "失败必须留下原因, 拿到的是 %r" % (err,))
            self.assertNotIn("本来就没有", err,
                             "失败不能假扮成'本来就没有': %r" % (err,))
        finally:
            (self.tray.clear_gpu_preference,
             self.tray.get_gpu_preference) = saved

    def test_a_write_that_did_not_stick_names_the_cause(self):
        """写入了却没落地: 原因要点明是回读没过。"""
        app = object.__new__(self.tray.TrayApp)
        app._pref_cache = {}
        app._pref_ts = 0.0
        saved = (self.tray.set_gpu_preference,
                 self.tray.get_gpu_preference)
        self.tray.set_gpu_preference = lambda _f: None
        self.tray.get_gpu_preference = lambda _f: None     # 没落地
        try:
            self.assertFalse(app._pref_apply("C:/app/a.exe"))
            err = getattr(app, "_pref_err", None)
            self.assertTrue(err, "没落地得给个原因")
            self.assertIn("落地", err, "原因要点明是回读没过: %r" % (err,))
        finally:
            self.tray.set_gpu_preference, self.tray.get_gpu_preference = saved

    def test_a_failed_clear_is_not_a_silent_success(self):
        u"""撤销失败(本来就没设置)时不能说“已清除”。"""
        app = object.__new__(self.tray.TrayApp)
        app._pref_cache = {"C:/app/a.exe": True}
        saved = self.tray.clear_gpu_preference
        self.tray.clear_gpu_preference = lambda _full: False
        try:
            self.assertFalse(app._pref_apply("C:/app/a.exe", clear=True))
        finally:
            self.tray.clear_gpu_preference = saved
        self.assertEqual(app._pref_cache, {"C:/app/a.exe": True},
                         "没撤成就别作废缓存, "
                         "否则列上会改口")


class DetailPrefRefresh(unittest.TestCase):
    """详情窗点完按钮, 那一行首选项得跟着换成新答案。"""

    def setUp(self):
        import gpu_tray
        self.tray = gpu_tray

    def test_the_row_follows_the_new_registry_answer(self):
        me = os.path.realpath(__file__)
        state = {"pref": False}      # 打开详情窗那会儿还没设独显
        app = object.__new__(self.tray.TrayApp)
        app._pref_of = lambda _full: state["pref"]
        self.assertEqual(app._pref_status(me),
                         "未设置 (按系统默认/自动)")

        class _Lb:
            def __init__(self):
                self.text = None

            def config(self, text):
                self.text = text

        # 点过"立即设为独显": 注册表已变, _pref_apply 也踢了缓存
        state["pref"] = True
        lb = _Lb()
        app._pref_refresh_label(lb, me)
        self.assertEqual(lb.text, "已设为独显 (重启该程序后生效)",
                         "点了按钮那一行还写着旧状态")

    def test_a_closed_window_does_not_raise(self):
        """用户在回调前关了窗: 刷不了一行不能把异常放到 stderr。"""
        app = object.__new__(self.tray.TrayApp)
        app._pref_of = lambda _full: True

        class _Dead:
            def config(self, text):
                raise RuntimeError("window destroyed")

        app._pref_refresh_label(_Dead(), "C:/app/a.exe")   # 不该抛

    def test_the_buttons_actually_refresh_the_row(self):
        """do_set/do_unset 里必须真调用它, 不然方法写了也没人用。"""
        body = inspect.getsource(self.tray.TrayApp._detail_build)
        self.assertIn("_pref_refresh_label(pref_label, full)", body)
        self.assertIn("pref_label = lb", body)


class TrayIconRedrawGuard(unittest.TestCase):
    """占用数字没变就别重设托盘图标: 每次设都重建(见 _refresh_title 的注释)。

    和标题那道闸同一个理: 空闲时占用率不动, 每秒 self.icon.icon = ... 是白画。
    """

    def _app(self):
        import threading
        import gpu_tray

        class Icon:
            def __init__(self):
                self.title = ""
                self.sets = 0
                self._img = None

            @property
            def icon(self):
                return self._img

            @icon.setter
            def icon(self, img):
                self.sets += 1
                self._img = img

        app = object.__new__(gpu_tray.TrayApp)
        app.lock = threading.Lock()
        app.snapshot = None
        app.paused = False
        app.monitor_dead = False
        app._title_base = ""
        app.icon = Icon()
        return app

    def _snap(self, total):
        return {"util_by_pid": {1: total}, "gpu_all_by_pid": {},
                "dgpu_util_by_pid": {}, "shared_by_pid": {},
                "dedicated_by_pid": {}, "igpu_luids": set(),
                "dgpu_luids": set(), "kind_cache": {},
                "mode": "smart", "threshold": 40, "power_saver_auto": False,
                "on_battery": False}

    def test_the_icon_is_set_once_when_the_number_holds(self):
        import gpu_tray
        app = self._app()
        gpu_tray.TrayApp._on_sample(app, self._snap(30.0))
        first = app.icon.sets
        for _ in range(4):          # 连续几轮同一占用
            gpu_tray.TrayApp._on_sample(app, self._snap(30.0))
        self.assertEqual(app.icon.sets, first,
                         "占用没变就别重设图标, Windows 上每次设都重建托盘图标")

    def test_a_new_number_repaints_the_icon(self):
        import gpu_tray
        app = self._app()
        gpu_tray.TrayApp._on_sample(app, self._snap(20.0))
        before = app.icon.sets
        gpu_tray.TrayApp._on_sample(app, self._snap(77.0))
        self.assertEqual(app.icon.sets, before + 1, "占用变了才该换图标")


class SparklineThreshold(unittest.TestCase):
    """趋势图里的阈值线: 没有它就看不出来什么时候会被判成高占用。"""

    class _Canvas:
        """按调用顺序记下画了什么, 不真的画。"""

        def __init__(self, w=860, h=46):
            self.calls = []
            self._w = w
            self._h = h

        def delete(self, *_a):
            self.calls.append(("clear",))

        def winfo_width(self):
            return self._w

        def winfo_height(self):
            return self._h

        def create_line(self, *a, **kw):
            self.calls.append(("line", a, kw))

        def create_polygon(self, *a, **kw):
            self.calls.append(("poly", a, kw))

        def create_text(self, *a, **kw):
            self.calls.append(("text", a, kw))

    def _app(self, values):
        import gpu_tray
        app = object.__new__(gpu_tray.TrayApp)
        app.history = [(float(i), float(v)) for i, v in enumerate(values)]
        return app

    def test_the_threshold_line_sits_under_the_trend(self):
        canvas = self._Canvas()
        self._app([5, 20, 60, 30])._draw_sparkline(canvas, 40)
        self.assertEqual([c[0] for c in canvas.calls],
                         ["clear", "poly", "line", "line", "text", "text"],
                         "先铺填充再画必须的线; 阈值线在填充上面,"
                         "否则折线翻到阈值上方时那半截被盖掉")
        dashed = [c for c in canvas.calls
                  if c[0] == "line" and c[2].get("dash")]
        self.assertEqual(len(dashed), 1, "阈值线只有一条, 而且是虚线")
        labels = [c[2].get("text", "") for c in canvas.calls
                  if c[0] == "text"]
        self.assertIn("迁移阈值 40%", labels)

    def test_no_threshold_means_no_threshold_line(self):
        canvas = self._Canvas()
        self._app([5, 20, 60, 30])._draw_sparkline(canvas)
        self.assertEqual(len([c for c in canvas.calls if c[0] == "line"]), 1,
                         "没配阈值就只该有折线本身")
        self.assertNotIn("迁移阈值",
                         " ".join(c[2].get("text", "") for c in canvas.calls
                                  if c[0] == "text"))

    def test_a_single_point_only_says_wait(self):
        canvas = self._Canvas()
        self._app([42])._draw_sparkline(canvas, 40)
        # 一个点时仍然只出提示, 但阈值线要画: 空态才是最需要参考基准的
        # 时候(用户想知道"阈值在哪、我离它多远")。参照 btop —— 它数据不足
        # 时同样保留参考线。详见 SparklineThresholdInEmptyState。
        self.assertEqual([c[0] for c in canvas.calls],
                         ["clear", "text", "line", "text"])
        # 提示句仍然在, 只是不再位于最后一次调用(阈值标签排在它后面)
        texts = [c[2].get("text", "") for c in canvas.calls
                 if c[0] == "text"]
        self.assertTrue(any("趋势" in t for t in texts),
                        f"一个点时仍要说明在等数据: {texts}")


class SparklineGeometry(unittest.TestCase):
    """viewBox / CSS 高度必须由 _SPARK_W/_SPARK_H 一处说了算。

    这三个数原来是字面量, 抄在四处: viewBox、填充多边形的右下角、
    阈值线的右端、CSS 的 height。改一次画布大小要同步改四处, 漏一处
    就是"阈值线画在框外"或者"纵向被二次缩放"这种看一眼看不出错的毛病。
    """

    def test_the_svg_uses_the_module_constants(self):
        html = gm.sparkline_html([(0, 10.0), (10, 20.0)], 40)
        self.assertIn(f"viewBox='0 0 {gm._SPARK_W} {gm._SPARK_H}'", html)
        self.assertIn(f"{gm._SPARK_W} {gm._SPARK_H} 0 {gm._SPARK_H}'", html)
        self.assertIn(f"x2={gm._SPARK_W} y2=", html)

    def test_the_css_height_matches_the_viewbox(self):
        """CSS 的 height 和 viewBox 不一致时, 纵向会被二次缩放。"""
        self.assertIn(f"height:{gm._SPARK_H}px",
                      gm._PANEL_STYLE.replace(" ", ""))


class SparklineHtml(unittest.TestCase):
    """Web 面板的内联 SVG 趋势图: 口径必须跟托盘一致。"""

    POINTS = [(float(i * 10), float(v)) for i, v in enumerate([5, 20, 60, 30])]

    def test_before_two_points_it_only_says_wait(self):
        for hist in (None, [], [(0.0, 5.0)]):
            with self.subTest(hist=hist):
                html = gm.sparkline_html(hist, 40)
                self.assertIn("sparkempty", html)
                self.assertNotIn("<svg", html)

    def test_the_threshold_line_and_the_corner_label_are_there(self):
        html = gm.sparkline_html(self.POINTS, 40)
        self.assertIn("<polyline class=line points='", html)
        self.assertIn("<polygon class=fill", html,
                      "先扑底色再画线, 否则折线被填充盖住")
        self.assertIn("<line class=thr", html,
                      "没有阈值线就看不出什么时候会被判成高占用")
        self.assertIn("迁移阈值 40%", html)
        self.assertIn("峰值60.0%", html)
        self.assertIn("当前30.0%", html)
        self.assertIn("white-space:nowrap", gm._PANEL_STYLE,
                      "覆盖层不能换行, 否则面板一窄就折成两行")

    def test_no_threshold_means_no_threshold_line(self):
        html = gm.sparkline_html(self.POINTS, 0)
        self.assertNotIn("class=thr", html)
        self.assertNotIn("迁移阈值", html)

    def test_the_span_label_counts_seconds_before_one_minute(self):
        """刚启动只有几个点时, 不能写成"近0分钟"。"""
        html = gm.sparkline_html(self.POINTS[:3], 40)
        self.assertIn("近30秒", html)
        self.assertNotIn("近0分钟", html)

    def test_the_span_label_counts_minutes_once_past_one_minute(self):
        pts = [(float(i * 10), float(i % 50)) for i in range(6)]
        html = gm.sparkline_html(pts, 40)
        self.assertIn("近1分钟", html)
        self.assertNotIn("近0分钟", html)

    def test_the_tray_and_the_web_share_the_ymax_and_the_corner(self):
        """两张图各写一份的话, 迟早长成两张脸。"""
        import gpu_tray

        self.assertIs(gpu_tray.spark_ymax, gm.spark_ymax)
        self.assertIs(gpu_tray.spark_corner_text, gm.spark_corner_text)

    def test_the_threshold_line_always_fits_in_the_canvas(self):
        """阈值 90 时, 阈值线不能跑到画布顶外面去。"""
        pts = [(float(i), 5.0) for i in range(20)]
        self.assertGreaterEqual(gm.spark_ymax(pts, 90), 90 * 1.15)
        self.assertGreaterEqual(gm.spark_ymax(pts, 0), 30.0)
        self.assertGreaterEqual(gm.spark_ymax(pts, 0), 5.0)

    def test_the_corner_says_seconds_while_there_are_only_a_few_points(self):
        pts = [(float(i * 10), float(i)) for i in range(3)]
        self.assertEqual(gm.spark_corner_text(pts),
                         "近30秒 峰值2.0%  当前2.0%")
        self.assertNotIn("近0分钟", gm.spark_corner_text(pts))
        self.assertEqual(gm.spark_corner_text(
            [(float(i * 10), 1.0) for i in range(12)]),
            "近2分钟 峰值1.0%  当前1.0%")

    def test_the_span_label_uses_timestamps_when_points_are_sparse(self):
        """点距被拉长时, 时长不能按"点数 x 10 秒"虚报。

        采样卡顿(单轮超过 10 秒)会让点距大于 10 秒: 30 个点只跨 29 秒,
        老算法按 30x10 报"近5分钟" —— 图没变, 标题先撒了谎。
        """
        pts = [(float(i), 5.0) for i in range(30)]
        text = gm.spark_corner_text(pts)
        self.assertIn("近39秒", text)
        self.assertNotIn("近5分钟", text)

    def test_the_span_label_survives_machine_sleep(self):
        """休眠唤醒: 首末两点差一小时就得说一小时, 不能说成 30 分钟。"""
        pts = [(0.0, 5.0), (3600.0, 9.0), (3610.0, 7.0)]
        text = gm.spark_corner_text(pts)
        self.assertIn("近1.0小时", text)
        self.assertIn("峰值9.0%", text)
        self.assertIn("当前7.0%", text)

    def test_the_panel_carries_the_sparkline(self):
        """面板得真的把趋势图发出去, 不只是拥有那段 CSS。"""
        snap = {**ModeDisplay.SNAP, "threshold": 40.0,
                "history": self.POINTS}
        html = gm.panel_html(snap, {})
        self.assertIn("class=spark", html)
        self.assertIn("class=sparkwrap", html)
        self.assertIn("role='img'", html)
        self.assertIn("aria-label='核显占用趋势'", html,
                      "趋势图对读屏用户不可见: svg 要有 role=img + aria-label")

    def test_labels_live_outside_the_stretched_viewbox(self):
        """文字不能画在 SVG 里。

        viewBox 被 width:100% 横向拉伸, 放进去的 <text>
        会被同样拉宽——"迁移阈值 40%" 直接漂成一坨腻。
        所以文字得做成 div 里的覆盖层。
        """
        html = gm.sparkline_html(self.POINTS, 40)
        self.assertNotIn("<text", html)
        self.assertIn("<span class=thr", html)
        self.assertIn("<span class=corner", html)

    def test_the_threshold_line_sits_above_the_fill(self):
        """阈值线要画在填充上面。

        否则折线翻到阈值上方时, 先画的那半截红线
        会被 #1c3a28 的填充整块盖掉, 阈值就时有时无。
        """
        html = gm.sparkline_html(self.POINTS, 40)
        self.assertLess(html.index("class=fill"), html.index("class=thr x1"))

    def test_the_stroke_survives_the_viewbox_being_stretched(self):
        """preserveAspectRatio=none 会把 viewBox 横向拉伸。

        不写 vector-effect 的话, 线宽会跟着变形:
        面板多宽时折线变粗。
        """
        snap = {**ModeDisplay.SNAP, "threshold": 40.0,
                "history": self.POINTS}
        self.assertIn("vector-effect:non-scaling-stroke",
                      gm.panel_html(snap, {}))


class PanelHtmlProcessNames(unittest.TestCase):
    """Web 面板要能看出是谁在占显卡, 进程名得先转义再进 HTML。"""

    def test_the_name_column_comes_first(self):
        snap = {**ModeDisplay.SNAP, "gpu_all_by_pid": {100: 50.0},
                "pid_names": {100: "heavy.exe"}}
        html = gm.panel_html(snap, {})
        self.assertIn("<th class=n" + gm._tip("name") + ">进程</th>", html)
        self.assertIn("<td class=n>heavy.exe</td><td class=pid>100</td>",
                      html)

    def test_a_missing_name_falls_back_to_the_pid(self):
        snap = {**ModeDisplay.SNAP, "gpu_all_by_pid": {100: 50.0}}
        html = gm.panel_html(snap, {})
        self.assertIn("<td class=n>pid 100</td>", html)

    def test_a_name_that_looks_like_markup_stays_inert(self):
        snap = {**ModeDisplay.SNAP, "gpu_all_by_pid": {100: 50.0},
                "pid_names": {100: "<b>&edge</b>"}}
        html = gm.panel_html(snap, {})
        self.assertIn("<td class=n>&lt;b&gt;&amp;edge&lt;/b&gt;</td>", html)
        self.assertNotIn("<b>&edge", html,
                         "不转义就等于把用户机器上的进程名当 HTML 渲染")

    def test_adapter_names_are_escaped_too(self):
        """显卡名也不是我们写的, 来自 D3DKMT 查询结果。

        进程名早就转义了, 但 gpuinfo 那一行的核显/独显名字是直接拼的,
        带个 "<" 就会把 </p> 提前关掉。前面几轮只顾着进程名, 漏了这边。
        """
        snap = {**ModeDisplay.SNAP, "dgpu_luids": {"L0"}}
        html = gm.panel_html(snap, {"L0": "<b>&GPU</b>"})
        self.assertIn("&lt;b&gt;&amp;GPU&lt;/b&gt;", html)
        self.assertNotIn("<b>&GPU", html,
                         "显卡名里的尖括号不能当成标签渲染")

    def test_the_summary_cards_and_the_filter_box_are_there(self):
        html = gm.panel_html(ModeDisplay.SNAP, {})
        self.assertIn("<div id=cards class=cards>", html)
        self.assertIn("id=q", html)
        self.assertIn("生效阈值", html)
        self.assertIn("localStorage", html,
                      "刷新页面后筛选词不该丢")

    def test_the_filter_box_is_labelled_for_accessibility(self):
        """筛选框原来只有 placeholder 没有可访问名: 输入后
        placeholder 消失, 读屏/纯键盘用户等于对着一个没标签的
        输入框。补 aria-label。"""
        html = gm.panel_html(ModeDisplay.SNAP, {})
        self.assertIn("aria-label='筛选进程", html,
                      "筛选框要带 aria-label, 不能只靠 placeholder")


class WebPanelPrefColumn(unittest.TestCase):
    """Web 面板也要有"迁移状态"列 —— 托盘面板早就有了。

    托盘那边能看出"这个进程是不是已经被钉在独显上", Web 这边只有七列
    裸数字, 于是浏览器里看到一条 0% 的进程也判断不出它为什么还挂着。
    同一个数据两个界面少一列, 就是 README 里那句"同一套数据的浏览器版"
    对不上的地方。
    """

    def test_the_header_gains_the_column(self):
        html = gm.panel_html(ModeDisplay.SNAP, {})
        for col, label in (("dmem", "专用MiB"), ("smem", "共享MiB"),
                           ("pref", "迁移状态")):
            self.assertIn("<th title='" + gm._HDR_TIPS[col] + "'>"
                          + label + "</th>",
                      html)

    def test_a_pinned_process_is_marked(self):
        snap = {**ModeDisplay.SNAP, "gpu_all_by_pid": {100: 50.0},
                "pid_names": {100: "heavy.exe"}, "pid_prefs": {100: True}}
        html = gm.panel_html(snap, {})
        self.assertIn(f"<td class=pref>{gm.PREF_HIGH_TEXT}</td>", html)

    def test_an_unpinned_process_leaves_the_cell_empty(self):
        snap = {**ModeDisplay.SNAP, "gpu_all_by_pid": {100: 50.0},
                "pid_names": {100: "heavy.exe"}, "pid_prefs": {100: False}}
        self.assertIn("<td class=pref></td>", gm.panel_html(snap, {}))

    def test_a_snapshot_without_prefs_still_renders(self):
        """没带 pid_prefs 的快照(过滤后的老结构)也不能让整页白掉。"""
        snap = {**ModeDisplay.SNAP, "gpu_all_by_pid": {100: 50.0},
                "pid_names": {100: "heavy.exe"}}
        self.assertIn("<td class=pref></td>", gm.panel_html(snap, {}))

    def test_the_idle_row_spans_every_column(self):
        """空态行的 colspan 得跟着列数走, 少一格就跨不满一行。"""
        html = gm.panel_html(ModeDisplay.SNAP, {})
        self.assertIn(f"<td colspan=8>{gm.IDLE_ROW_TEXT}</td>", html)

    def test_the_tray_and_the_web_share_one_string(self):
        """两处各写一遍的话, 迟早一边改词另一边还说着旧话。"""
        import gpu_tray
        self.assertIs(gpu_tray.PREF_HIGH_TEXT, gm.PREF_HIGH_TEXT)


class WebPanelTempLine(unittest.TestCase):
    """Web 面板的"独显温度"行: 有读数就显示 °C, 没有就不凭空冒出来。

    托盘 tooltip 早有这条(见 test_tooltip_shows_dgpu_temp_when_known), 而
    Web 面板 panel_html 里那行 temp_line 却没有专门用例守: 它是面板上唯一
    跟着 dgpu_temp 走的可见文本, 一旦取值来源变了没人会发现面板悄悄多出或
    少了这行。这里把"有读数显示 °C、None 整行不出现"钉死, 并盯住两处格式一致。
    """

    def _html(self, temp):
        return gm.panel_html({**ModeDisplay.SNAP, "dgpu_temp": temp}, {})

    def test_a_real_reading_is_shown(self):
        self.assertIn("独显温度: 61°C", self._html(61))

    def test_no_reading_omits_the_whole_line(self):
        # dgpu_temp=None (NVML 不可用/本轮还没读到) 时不该出现"独显温度"
        self.assertNotIn("独显温度", self._html(None))

    def test_the_web_spells_it_like_the_tray_tooltip(self):
        # 托盘 tooltip 与 Web 面板同一个数必须写成同一串, 别一边℃一边°C
        self.assertIn("独显温度: 61°C", self._html(61))
        self.assertIn("°C", self._html(85))


class SnapshotPrefColumn(LoopHarness):
    """pid_prefs: Web 面板"迁移状态"列的数据来源。"""

    def test_the_snapshot_carries_the_preference_state(self):
        self.prefs[HEAVY] = "GpuPreference=2;"      # 已迁移
        self.prefs[GAME] = "GpuPreference=1;"        # 明改回核显
        self.run_monitor([{100: 80.0, 102: 5.0}], cycles=1)
        self.assertEqual(gm._LAST_SNAPSHOT["pid_prefs"],
                         {100: True, 102: False})

    def test_a_process_without_a_full_path_has_no_entry(self):
        """拿不到完整路径就写不了注册表, 也就没什么首选项可显示。"""
        gm.pid_to_name = {100: ("mystery.exe", None)}.get
        self.run_monitor([{100: 80.0}], cycles=1)
        self.assertEqual(gm._LAST_SNAPSHOT["pid_prefs"], {})
        self.assertEqual(gm._LAST_SNAPSHOT["pid_names"], {100: "mystery.exe"})


class WebPanelResponsive(unittest.TestCase):
    """手机上打开 Web 面板: 不缩放成一页缩略图, 表格能横向滚。"""

    def test_the_panel_declares_a_viewport_for_phones(self):
        """没有 viewport 的话, 手机浏览器按 980px 版面渲染再整体缩小。

        六张卡片加七列表格会被缩成一行蚂蚁字, 趋势图也跟着糊。
        """
        html = gm.panel_html(ModeDisplay.SNAP, {})
        self.assertIn("name='viewport'", html)
        self.assertIn("width=device-width", html)

    def test_the_table_scrolls_instead_of_breaking_the_layout(self):
        html = gm.panel_html(ModeDisplay.SNAP, {})
        self.assertIn("<div class=tw><table>", html)
        self.assertIn("</table></div>", html)
        self.assertIn(".tw{overflow-x:auto}", gm._PANEL_STYLE)


class FilterParity(unittest.TestCase):
    u"""托盘筛选与 Web 筛选必须是同一个口径。

    Web 侧匹配 td.n / td.pid 两个单元格; 托盘侧对应名字与 PID。
    整行 textContent 匹配的老写法会把百分比列也算进去 —— 输入 "12"
    会留下独显% 12.3 的行, 还让人以为它真的在跑 12%。托盘那边
    不会, 于是同一个筛选词在两个界面给出两张名单。
    """

    def setUp(self):
        self.app = object.__new__(gpu_tray.TrayApp)
        self.app.filter_text = ""

    def test_a_number_only_needle_catches_the_pid_not_the_percent(self):
        u"""纯数字词只该命中 PID, 不该顺手把 GPU% 列捎上。

        这一条拿"名字里也带这个数字"做对照: 两种命中都合法,
        但要分得清是哪一种。
        """
        self.app._set_filter("200")
        self.assertTrue(self.app._matches_filter("steam.exe", 200))
        self.assertFalse(self.app._matches_filter("v2ray.exe", 100),
                          "名字里没有 200、PID 也不是 200, 不该留下")

    def test_a_pid_needle_is_not_a_substring_of_a_longer_pid(self):
        u"""PID 匹配也是子串, 这一条只是把这个事实讲明白。

        托盘与 Web 都是 indexOf 语义, 谁想改成"整串相等"都得同时
        改两处 —— 这条用例就是那个提醒。
        """
        self.app._set_filter("20")
        self.assertTrue(self.app._matches_filter("steam.exe", 200))

    def test_blank_and_noise_needles_keep_the_whole_table(self):
        u"""空串 / 全空格都等于"不筛选", 不是"筛掉一切"。"""
        for needle in ("", "   ", None):
            self.app._set_filter(needle)
            self.assertTrue(self.app._matches_filter("a.exe", 7))

    def test_the_web_side_reads_the_same_two_cells(self):
        u"""Web 那边也钉一下: 匹配的是 td.n 和 td.pid, 不是整行。"""
        self.assertIn("td.n", gm._PANEL_SCRIPT)
        self.assertIn("td.pid", gm._PANEL_SCRIPT)

class WebPanelFilter(unittest.TestCase):
    """筛选口径必须和托盘面板 _matches_filter 一致: 只认进程名和 PID。"""

    SNAP = {**ModeDisplay.SNAP,
            "gpu_all_by_pid": {100: 50.0, 200: 12.3},
            "util_by_pid": {100: 50.0, 200: 12.3},
            "pid_names": {100: "heavy.exe", 200: "edge.exe"}}

    def test_the_filter_reads_the_name_and_pid_cells(self):
        self.assertIn("td.n", gm._PANEL_SCRIPT)
        self.assertIn("td.pid", gm._PANEL_SCRIPT)

    def test_the_filter_no_longer_matches_whole_rows(self):
        """整行 textContent 匹配会把百分比列也算进去。

        输入 "12" 会把独显% 12.3 的 edge.exe 留下来, 托盘那边却按
        名字/PID 筛, 两边对不上, 还让人以为这些行真的在 12%。
        """
        self.assertNotIn("r.textContent", gm._PANEL_SCRIPT)
        self.assertIn("nc.textContent.toLowerCase().indexOf(v)>=0",
                      gm._PANEL_SCRIPT)
        self.assertIn("id.toLowerCase().indexOf(v)>=0",
                      gm._PANEL_SCRIPT)

    def test_the_idle_row_survives_the_filter(self):
        """空态行不能当进程行筛: 它只有一个 colspan 格子, 没有 class。

        旧代码把 `td.n` 取不到时的兜底外提
        ((r.querySelector('td.n')||{textContent:''})), 于是空态行的
        nm=''、id='': 只要有筛选词, 两处 indexOf 全不中, 整行被
        display:none —— 空闲的机器上打一个字, "空闲"两个字跟着表一起
        消失, 只剩一张白表格, 用户会以为页面坏了。
        """
        self.assertIn("var nc=r.querySelector('td.n');",
                      gm._PANEL_SCRIPT)
        self.assertIn("if(!nc){empty=r;return;}",
                      gm._PANEL_SCRIPT,
                      "没有 td.n 的行是空态行: 不当进程行筛, 交给空态逻辑")

    def test_the_hint_denominator_counts_process_rows_only(self):
        """分母原来数 rows.length, 把空态行也算成 1 个进程。

        空闲时提示"显示 1 / 1 行", 而机器上一个进程都没占 GPU;
        托盘那边报的是"显示 0/0 行" —— 同一台机器两个界面两个读数。
        """
        self.assertIn("n=0,t=0;", gm._PANEL_SCRIPT)
        self.assertIn("t++;", gm._PANEL_SCRIPT)
        self.assertIn("+t+' 行'", gm._PANEL_SCRIPT)
        self.assertNotIn("rows.length+' 行'", gm._PANEL_SCRIPT)

    def test_the_idle_snapshot_renders_one_row_without_a_name_cell(self):
        """锁住服务端那份: 空闲态就是一个 colspan 单元格, 不带 class。"""
        html = gm.panel_html({"util_by_pid": {}, "igpu_luids": []}, {})
        self.assertIn("<tbody id=rows><tr><td colspan=8>", html)
        self.assertNotIn("td class=n", html)


class EmptyRowText(unittest.TestCase):
    """空表那一行的文案: "没有进程" 和 "筛选中没匹配" 是两件事。"""

    def test_a_busy_machine_with_no_match_is_not_reported_as_idle(self):
        """托盘曾把"筛选中没匹配"显示成"（当前无进程占用 GPU）"。

        明明有进程在跑, 只是被筛选词挡住了 —— 用户会先怀疑筛选坏了,
        再怀疑机器莫名其妙空了。
        """
        self.assertEqual(gm.empty_row_text(0), "（当前无进程占用 GPU）")
        self.assertEqual(gm.empty_row_text(5), "没有匹配的进程")

    def test_a_machine_that_has_not_been_sampled_is_not_reported_as_idle(self):
        """一轮都还没采到就不该说"当前无进程占用 GPU"。

        启动头一秒、暂停在首轮之前、监控启动就崩,
        三种情况下都还一份数据都没有。和日报里句
        "未采到核显占用数据"同一个道理: 没测和
        测出来是 0 不是一回事。
        """
        self.assertEqual(gm.empty_row_text(0, sampled=False),
                         gm.STARTING_ROW_TEXT)

    def test_a_dead_monitor_is_not_reported_as_idle(self):
        self.assertEqual(gm.empty_row_text(0, stopped=True),
                         gm.STOPPED_ROW_TEXT)

    def test_a_sampled_snapshot_keeps_saying_idle(self):
        """采过了才能说"没人用": 上面两条不能把

        真正空闲的机器也拿来当披风。
        """
        self.assertEqual(gm.empty_row_text(0), gm.IDLE_ROW_TEXT)
        self.assertEqual(gm.empty_row_text(3), gm.NO_MATCH_ROW_TEXT)

    def test_the_web_panel_always_carries_the_empty_row(self):
        """空态行常驻: 只在服务端没行时才渲染, 筛不中就只剩白表格。

        有真实行时这一行必须带 display:none —— JS 被禁用/报错、打印、
        复制页面时, 服务端送出去的 HTML 不能自己挂着一句
        "当前无进程占用 GPU"跟上面的数据打架。把它放出来是前端的事。
        """
        html = gm.panel_html({"util_by_pid": {100: 50.0},
                              "gpu_all_by_pid": {100: 50.0},
                              "igpu_luids": []}, {})
        self.assertIn("<tr style='display:none'><td colspan=8>"
                      "（当前无进程占用 GPU）</td></tr>",
                      html)
        self.assertIn("td class=n", html, "真实行照常渲染")

    def test_the_idle_row_is_visible_only_when_nothing_is_running(self):
        """真的一个进程都没有时, 空态行不许藏着掖着。"""
        html = gm.panel_html({"util_by_pid": {}, "igpu_luids": []}, {})
        self.assertIn("<tr><td colspan=8>（当前无进程占用 GPU）</td></tr>",
                      html)
        self.assertNotIn("display:none", html)

    def test_before_the_first_sample_the_web_panel_says_so(self):
        """启动头一秒 web 线程已经起来了, 那时快照还是 {}:

        老代码送出的是"当前无进程占用 GPU", 而事实是
        一轮都还没采到 —— 和托盘面板同一句空态文案。
        """
        html = gm.panel_html({}, {})
        self.assertIn(f"<td colspan=8>{gm.STARTING_ROW_TEXT}</td>", html)
        self.assertNotIn(gm.IDLE_ROW_TEXT, html)

    def test_a_sampled_but_idle_snapshot_still_says_idle(self):
        """真的采过了、确实一个进程都没有, 该说空闲。"""
        html = gm.panel_html({"util_by_pid": {}, "gpu_all_by_pid": {},
                              "igpu_luids": []}, {})
        self.assertIn(f"<td colspan=8>{gm.IDLE_ROW_TEXT}</td>", html)


    def test_the_web_script_switches_the_row_on_visible_count(self):
        """显隐判据必须是有没有"看得见的进程行"(n), 不是有没有进程行(t)。

        t>0 只说明有进程在跑; 全被筛选挡下时 n==0, 空态行必须露出来。
        上一版写的是 t, 于是筛不中时整张表一个行都不剩。
        t==0(一个进程行都没有)时这句话不归 JS 管:
        那时候服务端可能还一份都没采到, 它送的是
        "正在采样第一份数据…" —— 拿 t? 写会当场盖掉。
        """
        self.assertIn("if(!nc){empty=r;return;}", gm._PANEL_SCRIPT)
        self.assertIn("empty.style.display=n?'none':''", gm._PANEL_SCRIPT)
        self.assertIn("if(!n&&t)empty.cells[0].textContent="
                     "'没有匹配的进程';", gm._PANEL_SCRIPT)


class WebPanelLiveUpdate(unittest.TestCase):
    """页面自己 reload 会把滚动位置弹回顶部, 得换成局部刷新。"""

    def test_the_page_does_not_reload_itself_every_second(self):
        """meta refresh 每秒整页重载: 滚动位置归零、表格闪一下、

        正在看的行被顶跑。监控面板是拿来看的, 不是拿来回首的。
        """
        html = gm.panel_html(ModeDisplay.SNAP, {})
        self.assertNotIn("http-equiv", html)
        self.assertIn("setInterval", gm._PANEL_SCRIPT)
        self.assertIn("fetch(", gm._PANEL_SCRIPT)

    def test_the_updater_swaps_only_the_parts_that_change(self):
        """只换会变的六块, 输入框和滚动位置原封不动。"""
        for part in ("policy", "gpuinfo", "cards", "spark", "rows", "hint"):
            self.assertIn("'%s'" % part, gm._PANEL_SCRIPT)
        self.assertIn("DOMParser", gm._PANEL_SCRIPT)
        self.assertIn("innerHTML", gm._PANEL_SCRIPT)

    def test_every_part_the_script_swaps_really_exists(self):
        """PARTS 里写错的 id 会静默不更新 —— 得上页面挨个找。"""
        import re

        script = gm._PANEL_SCRIPT
        ids = re.findall(r"PARTS=\[([^\]]*)\]", script)
        self.assertEqual(len(ids), 1, "没找到 PARTS 清单")
        parts = re.findall(r"'([a-z]+)'", ids[0])
        self.assertEqual(parts, ["policy", "gpuinfo", "cards", "spark",
                                 "rows", "counters", "hint", "hidden",
                                 "snapts", "paused", "staleafter"],
                         parts)
        # hidden 只有真滤掉了噪声行才渲染, 所以这里用带噪声行的快照
        snap = {**ModeDisplay.SNAP,
                "gpu_all_by_pid": {100: 50.0, 300: 0.0},
                "util_by_pid": {100: 50.0, 300: 0.0},
                "pid_names": {100: "heavy.exe", 300: "idle.exe"}}
        html = gm.panel_html(snap, {})
        for part in parts:
            self.assertIn("id=%s" % part, html,
                          "PARTS 里的 %s 在页面上没有对应元素" % part)

    def test_a_stalled_monitor_is_not_left_looking_alive(self):
        """fetch 还通着但快照停在旧时刻, 页面也不能装活。

        监控线程静默死亡(异常掀翻循环)时 Web 线程还活着: 每秒 200,
        发回来的都是同一份旧快照。那种"连接正常但数字不动"比断连更隐蔽,
        所以数据时刻要服务端渲染进页面, 前端拿本地时钟比。
        """
        import time

        snap = dict(ModeDisplay.SNAP)
        snap["snap_ts"] = time.time() - 600      # 10 分钟前的数据
        html = gm.panel_html(snap, {})
        self.assertIn("id=snapts", html)
        # 毫秒 epoch: 前端直接用 Date() 格式化, 不再让服务端决定时区
        self.assertIn(str(int(snap["snap_ts"] * 1000)), html)
        # 没有时间戳的快照(刚启动, 还没采过一轮)不该伪造一个 1970
        self.assertIn("<span id=snapts hidden>0</span>",
                      gm.panel_html(ModeDisplay.SNAP, {}))
        script = gm._PANEL_SCRIPT
        # snapts 必须跟着局部刷新换, 不然前端永远拿首屏那一个时刻比
        self.assertIn("'snapts'", script)
        self.assertIn("数据已停更", script)
        self.assertIn("监控进程可能已停止", script)
        # 宽限秒数由页面上的 #staleafter 决定(按采样周期算), 不再写死 25
        self.assertIn("stal||25", script)
        self.assertNotIn("dts>25", script)

    def test_a_browser_without_fetch_falls_back_to_reloading(self):
        """老浏览器没有 fetch, 也不能就此变成一张静态截图。"""
        self.assertIn("location.reload()", gm._PANEL_SCRIPT)
        self.assertIn("typeof fetch!=='function'", gm._PANEL_SCRIPT)

    def test_a_dead_monitor_is_not_left_looking_alive(self):
        """监控进程死掉后, 页面不能永远停在最后一份数据上装活。"""
        import re

        html = gm.panel_html(ModeDisplay.SNAP, {})
        # 状态行默认隐藏: 监控正常时页面上看不出来
        self.assertIn("<p id=conn hidden></p>", html)
        script = gm._PANEL_SCRIPT
        # catch 曾是静默的空函数: 断网/进程退出和"这一轮刚好没变"
        # 在页面上一模一样, 用户盯着不动的一屏以为监控还活着
        self.assertNotIn(".catch(function(){})", script)
        self.assertIn("conn.hidden=false", script)
        self.assertIn("连接已断开", script)
        self.assertIn("监控进程可能已退出", script)
        # 连挂 3 次才提示, 单次网络抖动不打扰人
        self.assertIn("++fails>=3", script)
        # 一恢复就清掉, 不能一直挂在页面上
        self.assertIn("connhide()", script)
        # 非 2xx 也按断连算, 不然 502 页面会被当成正常数据刷进表格
        self.assertIn("r.ok", script)
        # 一次都没刷新成功过时文案不能留个空的数据时刻
        self.assertIn("页面加载时", script)
        # conn 是前端自己的状态, 不进 PARTS: 被 innerHTML 换掉会闪
        ids = re.findall(r"PARTS=\[([^\]]*)\]", script)
        self.assertEqual(len(ids), 1, "没找到 PARTS 清单")
        self.assertNotIn("conn", ids[0])


    def test_a_paused_monitor_is_not_reported_as_dead(self):
        u"""暂停是用户自己选的, 不是监控死掉。"""
        snap = dict(ModeDisplay.SNAP)
        snap["paused"] = True
        html = gm.panel_html(snap, {})
        # 暂停标记总得渲染: 恢复时前端要有字段可比,
        # 否则一直卡在上一个状态上
        self.assertIn("<span id=paused hidden>1</span>", html)
        self.assertIn("<span id=paused hidden>0</span>",
                      gm.panel_html(ModeDisplay.SNAP, {}))
        script = gm._PANEL_SCRIPT
        # 暂停时的文案, 不能和“监控进程可能已停止”混用
        self.assertIn("监控已暂停", script)
        self.assertIn("从托盘菜单或热键恢复", script)
        # 暂停与死掉要分开说, 但暂停也要报数据年龄 —— 见下面三条与
        # PanelScriptJsBehaviour 的行为测试
        # 暂停时也要报数据年龄: 老代码暂停分支只给一句数据停在,
        # 暂停三分钟和三小时在页面上长得一模一样
        self.assertIn("staleshow(dts*1000,'已暂停')", script,
                      "暂停超时后要说出数据停了多久")
        self.assertIn("else if(dts&&Date.now()", script,
                      "未暂停的分支不能被暂停分支吃掉")
        # staleshow 的原因由调用方给, 否则暂停时会说成可能已停止
        self.assertIn("function staleshow(ts,why)", script)
        self.assertIn("why||'监控进程可能已停止'", script,
                      "默认原因要留在未暂停那一侧")
        # 暂停标记必须跟局部刷新一起换
        self.assertIn("'paused'", script)

    def test_the_paused_flag_is_injected_when_the_page_is_served(self):
        u"""暂停状态得在读取那一刻注入, 否则面板永远看不到。"""
        snap = {"util_by_pid": {1: 1.0}, "snap_ts": 1.0}
        # 未暂停: 不引入这个键, 快照原样返回
        self.assertIs(gm.web_snapshot_with_pause(snap), snap)
        self.assertIs(gm.web_snapshot_with_pause(snap, lambda: False), snap)
        # 暂停: 复制份加标记, 不改写原快照
        got = gm.web_snapshot_with_pause(snap, lambda: True)
        self.assertTrue(got.get("paused"))
        self.assertNotIn("paused", snap,
                         "别的读者(监控循环/托盘)価值只能侧写")
        self.assertEqual(got["snap_ts"], 1.0, "其余字段得原样带过去")


class NoiseRowFilter(unittest.TestCase):
    """全零行在看板上不该占位, 而且判据两边共用一份。"""

    SNAP = {**ModeDisplay.SNAP,
            "gpu_all_by_pid": {100: 50.0, 300: 0.0},
            "util_by_pid": {100: 50.0, 300: 0.0},
            "pid_names": {100: "heavy.exe", 300: "idle.exe"}}

    def test_the_web_panel_drops_all_zero_rows(self):
        """--list 里那种 0.0% / 0 / 0 的行, 看板上不该占位。"""
        html = gm.panel_html(self.SNAP, {})
        self.assertIn("<td class=n>heavy.exe</td>", html)
        self.assertNotIn("idle.exe", html)

    def test_the_threshold_is_shared_not_duplicated(self):
        """判据必须只有一个出处, 不然改一处漏一处。"""
        import gpu_tray

        self.assertIs(gpu_tray.row_is_noise, gm.row_is_noise)

    def test_the_hidden_count_is_disclosed_not_left_to_mental_math(self):
        """卡片说 40 个进程, 表格只列 34 行 —— 得说清差在哪。"""
        snap = {**ModeDisplay.SNAP,
                "gpu_all_by_pid": {100: 50.0, 300: 0.0},
                "util_by_pid": {100: 50.0, 300: 0.0},
                "pid_names": {100: "heavy.exe", 300: "idle.exe"}}
        html = gm.panel_html(snap, {})
        self.assertIn("已隐藏 1 个", html)
        # 没有零占用行时不该出现这一行, 免得平白多一句废话
        clean = gm.panel_html(ModeDisplay.SNAP, {})
        self.assertNotIn("已隐藏", clean)

    def test_the_card_count_stays_the_true_union(self):
        """汇总卡还是真 union 数, 不能为了跟表格一致就少报。"""
        snap = {**ModeDisplay.SNAP,
                "gpu_all_by_pid": {100: 50.0, 300: 0.0},
                "util_by_pid": {100: 50.0, 300: 0.0}}
        texts = [t for _l, t, _c, _tip in gm.panel_summary(snap)]
        self.assertEqual(texts[4], "2")
        self.assertEqual(gm.hidden_noise_count(snap), 1)


class TrayStartupFailure(unittest.TestCase):
    """开机自启跑的是 pythonw --tray, 托盘起不来时必须留个动静。

    自启 .lnk 指源码: pystray 没装、Python 升级换了 ABI, import gpu_tray 或
    run_tray 都会炸。老代码没有任何保护, pythonw 又没有控制台, 结果就是
    "双击/开机后什么都没发生" - 用户根本看不出是依赖没了还是程序坏了。
    """

    @staticmethod
    def _run(boom, ensure=False):
        """用桩换掉 gpu_tray 跑一遍 main(), 回收 (弹窗列表, stderr, 桩)。"""
        saved_argv, saved_dir = sys.argv, gm._DIR
        saved = (gm.ensure_singleton, gm.user32, sys.modules.get("gpu_tray"))
        # main() 第一件事是 install_fault_dump(), 而这里把 _DIR 指去了
        # 临时目录: 它会在那里开一个 crash log 并把句柄缓存进全局
        # _FAULT_FH。不收回的话, 句柄一直开着, 临时目录删不掉
        # (WinError 32), 而且 faulthandler 还指着那个即将消失的文件。
        saved_fh = gm._FAULT_FH
        sys.argv = ["gpu_monitor.py", "--ensure" if ensure else "--tray"]
        gm.ensure_singleton = lambda: True
        boxes = []
        gm.user32 = type("U", (), {"MessageBoxW": staticmethod(
            lambda *a: boxes.append(a) or 0)})()
        stub = types.ModuleType("gpu_tray")
        stub.called = False

        def _run_tray(_cfg):
            if boom is not None:
                raise boom
            stub.called = True

        stub.run_tray = _run_tray
        sys.modules["gpu_tray"] = stub
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                gm.main()
        finally:
            sys.argv, gm._DIR = saved_argv, saved_dir
            gm.ensure_singleton, gm.user32 = saved[0], saved[1]
            fh = gm._FAULT_FH
            gm._FAULT_FH = saved_fh
            if fh is not None and fh is not saved_fh:
                try:
                    faulthandler.disable()   # 先停, 免得关文件后还往里写
                except Exception:
                    pass
                try:
                    fh.close()
                except OSError:
                    pass
            if saved[2] is None:
                del sys.modules["gpu_tray"]
            else:
                sys.modules["gpu_tray"] = saved[2]
        return boxes, err.getvalue(), stub

    def test_tray_start_failure_tells_the_user(self):
        """ImportError 必须变成弹窗, 不能静默死亡。"""
        boxes, err, _stub = self._run(
            ImportError("No module named 'pystray'"))
        self.assertEqual(len(boxes), 1)
        text = boxes[0][1]
        self.assertIn("启动失败", text)
        self.assertIn("pystray", text)
        self.assertIn("Traceback", err)   # 控制台下仍能看到完整栈

    def test_tray_start_ok_stays_quiet(self):
        """正常起托盘不该弹任何窗, 也不能把 run_tray 吞掉。"""
        boxes, _err, stub = self._run(None)
        self.assertEqual(boxes, [])
        self.assertTrue(stub.called)

    def test_ensure_start_failure_stays_quiet(self):
        """--ensure 是看门狗: 托盘起不来只能写日志, 不能每 2 分钟弹一次窗。

        §45 给托盘启动加了异常保护, 但 --tray 和 --ensure 共用同一段代码,
        弹窗会跟着看门狗每 2 分钟冒一次 - 那比静默死亡更吵。
        """
        saved_dir = gm._DIR
        with tempfile.TemporaryDirectory() as tmp:
            gm._DIR = tmp
            try:
                boxes, _err, _stub = self._run(
                    ImportError("No module named 'pystray'"), ensure=True)
                logp = os.path.join(tmp, "gpu_monitor.log")
                logged = os.path.isfile(logp) and \
                    open(logp, encoding="utf-8").read()
            finally:
                gm._DIR = saved_dir
        self.assertEqual(boxes, [])           # 看门狗一声不吭
        self.assertIn("托盘启动失败", logged)  # 但日志里必须留证据
        self.assertIn("pystray", logged)


class TrayRefreshFailure(unittest.TestCase):
    """面板刷新失败: 不能抛给 after 链, 也不能一声不吭, 更不能刷屏。"""

    @staticmethod
    def _app():
        import gpu_tray

        app = object.__new__(gpu_tray.TrayApp)
        app.lock = threading.Lock()
        app.snapshot = {"threshold": 42.0}
        app._refresh_err = None
        return app, gpu_tray

    @staticmethod
    def _boom(*_a):
        raise RuntimeError("树炸了")

    def test_refresh_failure_leaves_a_trace_and_keeps_going(self):
        """_refresh_tree 抛异常: 不向上蔓延, traceback 进 tray_debug.log。"""
        app, gt = self._app()
        logged = []
        real_dbg = gt._dbg
        gt._dbg = lambda msg: logged.append(msg)
        try:
            app._refresh_tree = self._boom
            app._safe_refresh(None, None, None, None, None)   # 不许抛出来
        finally:
            gt._dbg = real_dbg
        self.assertEqual(len(logged), 1)
        self.assertIn("RuntimeError", logged[0])
        self.assertIn("树炸了", logged[0])
        self.assertIn("面板刷新失败", logged[0])

    def test_same_failure_is_logged_only_once(self):
        """同一个错每秒只记一次, 不然 tray_debug.log 被刷成噪音。"""
        app, gt = self._app()
        logged = []
        real_dbg = gt._dbg
        gt._dbg = lambda msg: logged.append(msg)
        try:
            app._refresh_tree = self._boom
            app._safe_refresh(None, None, None, None, None)
            app._safe_refresh(None, None, None, None, None)
            app._safe_refresh(None, None, None, None, None)
        finally:
            gt._dbg = real_dbg
        self.assertEqual(len(logged), 1)
        self.assertIsNotNone(app._refresh_err)

    def test_ok_refresh_says_nothing(self):
        """正常刷新不写日志, 并把上一次的出错标记清掉。"""
        app, gt = self._app()
        logged = []
        real_dbg = gt._dbg
        gt._dbg = lambda msg: logged.append(msg)
        calls = []
        try:
            app._refresh_tree = lambda *a: calls.append("tree")
            app._draw_sparkline = lambda *a: calls.append("spark")
            app._refresh_err = "上一轮的错"
            app._safe_refresh(None, None, None, None, None)
        finally:
            gt._dbg = real_dbg
        self.assertEqual(logged, [])
        self.assertEqual(calls, ["tree", "spark"])
        self.assertIsNone(app._refresh_err)



class SamplingWindow(LoopHarness):
    """采样窗口必须跟采样周期等长, 否则是周期性漏采。"""

    def _windows(self, cycles, **over):
        self.run_monitor([{HEAVY_PID: 10}], cycles=cycles,
                         cfg_overrides=over)
        return list(gm.collect_gpu_sample.windows)

    def test_the_window_matches_the_configured_interval(self):
        """interval=5s 时窗口也得是 5s。

        PDH 读的是"窗口内引擎活跃时长 / 墙上时长"的比例; 窗口只有 1 秒
        而周期是 5 秒, 每个周期中间 4 秒的峰值整段不存在, "持续高占用"
        被系统性低估 —— 把周期调大反而看得更少。
        """
        self.assertEqual(self._windows(3, interval_seconds=5),
                         [5.0, 5.0, 5.0])

    def test_the_window_shrinks_with_the_interval(self):
        self.assertEqual(self._windows(2, interval_seconds=0.5), [0.5, 0.5])

    def test_the_window_is_forwarded_every_cycle_not_only_at_startup(self):
        """热重载改了 interval 后, 下一轮窗口就要跟着变。

        cfg 是循环里的局部量, 窗口却已经在采样函数里写死过一次 ——
        这类"启动时算好就不再问"的写法正是热重载最容易漏的一格。
        """
        path = os.path.join(self._tmp, "config.json")
        write_test_config(path, interval_seconds=2)
        seen = []

        def on_sample(_snap):
            seen.append(gm.collect_gpu_sample.windows[-1])
            if len(seen) < 2:
                LoopHarness.write_and_bump(path, interval_seconds=8)
        self.run_monitor([{HEAVY_PID: 10}], cycles=2, config_path=path,
                         hooks={"on_sample": on_sample})
        self.assertEqual(seen, [0.001, 8.0])


class SampleWindowFloor(unittest.TestCase):
    """窗口换算的兜底: 不猜、不炸、不低于下限。"""

    def test_defaults_to_one_second_when_the_interval_is_unusable(self):
        for bad in (None, "abc", "", object()):
            self.assertEqual(gm._sample_window(bad), 1.0, bad)

    def test_never_drops_below_the_floor(self):
        for tiny in (0, -3, 0.001):
            self.assertEqual(gm._sample_window(tiny), gm._WINDOW_MIN, tiny)

    def test_passes_normal_intervals_through_untouched(self):
        for good in (0.5, 1, 2, 5, 10.0):
            self.assertEqual(gm._sample_window(good), float(good))


class CollectGpuSampleWindow(unittest.TestCase):
    """真 collect_gpu_sample 的窗口: 打桩 PDH, 只放行 time.sleep 记录。"""

    PATH = (r"\GPU Engine(pid_1234_luid_0x00000000_0x000135EF_phys_0_eng_0_"
            r"engtype_3D)\Utilization Percentage")

    def setUp(self):
        self._sleep = gm.time.sleep
        self._pdh = gm.pdh
        self._expand = gm.expand_counter_paths
        self._raw = gm._pdh_read_raw
        self._close = gm.kernel32.CloseHandle
        self.slept = []

        def fake_sleep(seconds):
            self.slept.append(seconds)
        gm.time.sleep = fake_sleep
        gm.pdh = type("P", (), {n: (lambda *a, **kw: 0) for n in
                                ("PdhOpenQueryW", "PdhAddCounterW",
                                 "PdhCollectQueryData", "PdhCloseQuery")})()
        gm.expand_counter_paths = (
            lambda spec: [self.PATH] if "Utilization" in spec else [])
        # 第一读 0s/10s, 第二读 5s/20s => 引擎忙了 5s / 墙上过了 10s = 50%
        reads = iter([(0, 10_000_000), (5_000_000, 20_000_000)])
        gm._pdh_read_raw = lambda h: next(reads)
        gm.kernel32.CloseHandle = lambda h: True

    def tearDown(self):
        gm.time.sleep = self._sleep
        gm.pdh = self._pdh
        gm.expand_counter_paths = self._expand
        gm._pdh_read_raw = self._raw
        gm.kernel32.CloseHandle = self._close

    def test_the_query_sleeps_for_the_requested_window(self):
        """window=5 时内部睡眠就是 5 秒, 不是写死的 1 秒。"""
        usage, _sh, _de, _idx = gm.collect_gpu_sample(window=5)
        self.assertEqual(self.slept, [5.0])
        # 窗口本身没被改坏: (6s-1s)/(20s-10s) = 50%
        self.assertEqual(usage, {(1234, "00000000_000135EF", 0): 50.0})

    def test_the_default_window_is_one_second(self):
        gm.collect_gpu_sample()
        self.assertEqual(self.slept, [1.0])
def _wcag_luminance(hexs):
    """WCAG 相对亮度。

    直接调生产实现的 gm._lum: 测试用的尺和生产用的尺必须是同一把。
    以前这里自己抄了一遍公式 —— 三份抄写今天数值一致, 但谁改了生产
    那份, 这条守卫还在按旧公式发合格证。
    """
    return gm._lum(_hex6_rgb(_hex6(hexs)))


def _hex6_rgb(hexs):
    return tuple(int(hexs[i:i + 2], 16) for i in (1, 3, 5))


def _wcag_contrast(fg, bg):
    return gm._ratio(_hex6_rgb(_hex6(fg)), _hex6_rgb(_hex6(bg)))


def _hex6(color):
    """#abc 这种三位缩写展开成 #aabbcc, 免得亮度函数切不出三段。"""
    if len(color) == 7:
        return color
    return "#" + "".join(c * 2 for c in color[1:])


def _css_block(css, selector):
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


class WebPanelLegibility(unittest.TestCase):
    """深色面板上的灰字必须看得见: WCAG AA 小字 4.5:1。

    #hint / #hidden / .sparkempty 从前是 #666a80(3.43:1) 和
    #555566(2.51:1), 在 #14141e 上等于没有 —— 提示行、空态文案这些
    恰恰是用户最需要读懂的两行。字号也从 11px 提到 12px。
    """

    COLORED = ("#hint", "#hidden", ".sparkempty", ".card span",
               ".sparkwrap .thr", ".sparkwrap .corner")
    SIZED = ("#hint", "#hidden", ".sparkempty", ".card span", "#conn",
             ".sparkwrap span")

    def setUp(self):
        self.css = gm._PANEL_STYLE
        self.bg = re.search(r"body\{[^}]*background:(#[0-9a-fA-F]{6})",
                            self.css).group(1)

    def test_every_small_text_rule_clears_aa_on_the_page_background(self):
        for selector in self.COLORED:
            block = _css_block(self.css, selector)
            color = re.search(r"color:(#[0-9a-fA-F]{6})", block)
            self.assertIsNotNone(color, "%s 没写颜色" % selector)
            ratio = _wcag_contrast(color.group(1), self.bg)
            self.assertGreaterEqual(
                ratio, 4.5,
                "%s 的 %s 在 %s 上只有 %.2f:1, 小字读不出来"
                % (selector, color.group(1), self.bg, ratio))

    def test_small_text_is_at_least_twelve_pixels(self):
        """11px 的灰字叠在 3.4:1 的对比度上, 是双重难读。"""
        for selector in self.SIZED:
            block = _css_block(self.css, selector)
            size = re.search(r"font-size:(\d+)px", block)
            self.assertIsNotNone(size, "%s 没有字号" % selector)
            self.assertGreaterEqual(int(size.group(1)), 12, selector)

    # 这些规则只可能落在 tbody tr:hover 那一行里(thead 不悬停, 卡片和
    # spark 角标在表格外面), 所以"悬停底"这一档对它们是真实路径。
    # 悬停档一并纳入页面底色那一轮, 免得两份名单走散
    HOVERED = ("td.pref,th.pref", ".hot", ".warm", ".cool")
    COLORED = COLORED + HOVERED

    def test_table_colours_clear_aa_on_the_hover_background(self):
        """悬停行换了底, 同一段颜色得在另一个底上也读得出来。

        只按页面底色查是不够的: `tbody tr:hover` 把行底换成 #23233a, 而
        同一段颜色在两个底上的对比度不是一回事。"迁移状态"那一列
        (`td.pref,th.pref`) 以前压根不在这份名单里, 实际是 #8b8ba0 ——
        页面上 5.48:1, 悬停底上只剩 4.59:1, 贴着 AA 线。用户读表时鼠标
        正掠过那一行, 这一档不是边缘情况。现在它复用表头的 #9a9ab0。

        阈值放到 5.0 而不是 4.5: AA 线是及格线, 4.59 那种"压线过"改一个
        色值就掉下去, 而这里没有任何机制会提醒。留半格余量当护栏。
        """
        hover = re.search(r"tbody tr:hover\{background:(#[0-9a-fA-F]{3,6})",
                          self.css)
        self.assertIsNotNone(hover, "tbody tr:hover 没写 background")
        hover = _hex6(hover.group(1))
        body_color = _hex6(re.search(
            r"body\{[^}]*color:(#[0-9a-fA-F]{3,6})", self.css).group(1))
        for selector in self.HOVERED:
            color = re.search(r"color:(#[0-9a-fA-F]{3,6})",
                              _css_block(self.css, selector))
            self.assertIsNotNone(color, "%s 没写 color" % selector)
            ratio = _wcag_contrast(_hex6(color.group(1)), hover)
            self.assertGreaterEqual(
                ratio, 5.0, "%s 的 %s 在悬停底 %s 上只有 %.2f:1, "
                "鼠标掠过那一行时读不出来"
                % (selector, color.group(1), hover, ratio))
        self.assertGreaterEqual(
            _wcag_contrast(body_color, hover), 5.0,
            "表格正文色在悬停底 %s 上只有 %.2f:1" % (hover, _wcag_contrast(
                body_color, hover)))

    def test_the_pref_column_reuses_the_header_grey(self):
        """"迁移状态"那一列和表头同一个灰: 不新增色值, 也走得开。

        §88 定过"不引入新色值"的调子。这里直接复用 th 已经在用的
        #9a9ab0, 而不是再造一个介于 #8b8ba0 和 #ddd 之间的灰 ——  palette
        里每多一个近邻灰, 下次调亮度就多一处要同步的地方。
        """
        pref = re.search(r"color:(#[0-9a-fA-F]{6})",
                        _css_block(self.css, "td.pref,th.pref")).group(1)
        head = re.search(r"color:(#[0-9a-fA-F]{6})",
                         _css_block(self.css, "th")).group(1)
        self.assertEqual(pref, head,
                         "迁移状态列请复用表头那个灰, 别新造近邻色值")

    def test_numbers_use_tabular_figures_so_they_stop_jittering(self):
        """每秒刷新的数字列若用比例数字, 宽度抖动会让整表左右晃。"""
        for selector in ("table", ".card b"):
            self.assertIn("tabular-nums", _css_block(self.css, selector),
                          "%s 的数字会随位数抖动" % selector)

    def test_every_declared_color_in_the_stylesheet_clears_aa(self):
        r"""全量扫两套主题的 color: 声明, 不靠逐个选择器点名。

        Windows 11 风格是浅色基线 + prefers-color-scheme 深色块, 所以这里把
        样式表从 @media 处一拆两半: 浅色声明必须在浅色页面/卡片底上过线,
        深色声明必须在深色页面/卡片底上过线。原先只有一套暗色 palette, 这条
        用例把所有声明都按暗色底扫 —— 现在两套主题各自裁判。谁再加一档灰
        或改一个色值, 只要在**自己那套主题**的底上低于 4.5:1, 这条就红,
        并报出色值和实测比值。

        (?<![\w-]) 是别把 border-color / background-color 这种属性名后半截
        也抓进来。
        """
        split = self.css.find("@media (prefers-color-scheme:dark)")
        self.assertGreater(split, 0, "深色档的 @media 块没了")
        light_css, dark_css = self.css[:split], self.css[split:]
        # (该主题的 CSS, 该主题的桌面底+卡片底)
        surfaces = ((light_css, (self.bg, "#ffffff")),
                    (dark_css, ("#202020", "#2d2d2d")))
        bad = []
        for css, bgs in surfaces:
            colors = re.findall(r"(?<![\w-])color:(#[0-9a-fA-F]{3,6})", css)
            self.assertGreater(len(colors), 5, "扫到的色值少得不像一个主题")
            for color in sorted(set(colors)):
                hexs = (color if len(color) == 7
                        else "#" + "".join(c * 2 for c in color[1:]))
                for bg in bgs:
                    ratio = _wcag_contrast(hexs, bg)
                    if ratio < 4.5:
                        bad.append((hexs, bg, round(ratio, 2)))
        self.assertEqual(bad, [],
                         "这些文字色在自家主题的底上不达 AA: %s" % bad)



class TrayPaletteLegibility(unittest.TestCase):
    """托盘那一侧同一把尺: 弱化色也要过 AA。"""

    def test_muted_foreground_clears_aa_on_both_dark_surfaces(self):
        import gpu_tray
        for bg in (gpu_tray._BG, gpu_tray._PANEL_BG):
            self.assertGreaterEqual(
                _wcag_contrast(gpu_tray._MUTED, bg), 4.5,
                "托盘弱化色 %s 在 %s 上看不清" % (gpu_tray._MUTED, bg))



class SparklineTimeAxis(unittest.TestCase):
    """趋势图的时间轴: 窗口按时间裁, x 按时间戳算。

    老代码取"最后 180 点" + x 按序号等距: 休眠唤醒或暂停监控后, 首末两点
    能差出几小时, 空白被画成一段连续曲线, 而角标写着"近 2.0 小时" ——
    一张图自己打自己脸。投影类图表(Grafana/btop)对时间轴都是一条规矩:
    窗口外的点不画, 窗口内的点按真实时刻排布。
    """

    NORMAL = [(float(i * 10), 10.0 + (i % 7)) for i in range(180)]

    @staticmethod
    def gapped():
        """30 分钟正常采样, 然后断 2 小时(休眠/暂停), 再采 6 个点。"""
        pts = [(float(i * 10), 5.0 + i * 0.1) for i in range(180)]
        return pts + [(7200.0 + i * 10, 30.0 + i * 0.1) for i in range(6)]

    @staticmethod
    def polyline_xs(html):
        poly = html.split("class=line points='")[1].split("'")[0]
        return [float(v) for v in poly.split()[0::2]]

    def test_points_outside_the_window_are_not_drawn(self):
        """2 小时的空档之后, 只画窗口内那 6 个点, 不把陈货也铺上去。"""
        pts = gm.spark_points(self.gapped())
        self.assertEqual(len(pts), 6)
        self.assertEqual([t for t, _v in pts],
                         [7200.0 + i * 10 for i in range(6)])
        html = gm.sparkline_html(self.gapped(), 40)
        self.assertEqual(len(self.polyline_xs(html)), 6)

    def test_normal_sampling_still_fills_the_whole_width(self):
        """正常采样(30 分钟 / 180 点)看起来必须和以前一模一样。"""
        pts = gm.spark_points(self.NORMAL)
        self.assertEqual(len(pts), 180)
        html = gm.sparkline_html(self.NORMAL, 40)
        xs = self.polyline_xs(html)
        self.assertEqual((xs[0], xs[-1]), (0.0, 300.0))
        self.assertEqual(gm.spark_corner_text(pts), "近30分钟 峰值16.0%  当前14.0%")

    def test_x_comes_from_the_timestamp_not_the_position(self):
        """3 个点 t=0/10/60: 按时间第二个点在半途, 按序号会在 1/3 处。"""
        coords, _x_last, _x_first = gm.spark_geometry(
            [(0.0, 10.0), (10.0, 20.0), (60.0, 30.0)], 300, 46, 30.0)
        xs = coords[0::2]
        self.assertEqual(xs, [0.0, 50.0, 300.0], "x 必须按时间戳排布")

    def test_a_gap_alone_does_not_stop_the_chart(self):
        """10 秒一点里有几次 11 秒(调度卡了一下), 不算断档, 照画。"""
        hist = [(0.0, 5.0), (10.0, 6.0), (21.0, 7.0), (31.0, 8.0)]
        html = gm.sparkline_html(hist, 40)
        self.assertEqual(len(self.polyline_xs(html)), 4)
        self.assertIn("<svg", html)

    def test_the_empty_state_names_the_reason_instead_of_the_cadence(self):
        """有历史点却全在窗口外: 说"没采到", 别拿"每 10 秒一点"搪塞。"""
        html = gm.sparkline_html([(0.0, 5.0), (9000.0, 9.0)], 40)
        self.assertIn("sparkempty", html)
        self.assertIn("最近 30 分钟无采样数据", html)
        self.assertNotIn("<svg", html)

    def test_the_tray_chart_drops_the_pre_gap_points_too(self):
        """托盘那张图必须一起改, 不然同一串数字在两边画出两张脸。"""
        import gpu_tray
        app = object.__new__(gpu_tray.TrayApp)
        app.history = self.gapped()
        canvas = SparklineThreshold._Canvas()
        app._draw_sparkline(canvas, 40)
        lines = [c for c in canvas.calls if c[0] == "line"]
        trend = [c for c in lines if not c[2].get("dash")]
        self.assertEqual(len(trend), 1, "折线只有一条")
        self.assertEqual(len(trend[0][1][0]), 6 * 2,
                         "2 小时前的点不该出现在这条线上")

    def test_the_tray_empty_text_is_no_longer_near_invisible(self):
        """空态文案的 #555566 在卡底上只有 2.25:1, 换成 _MUTED。"""
        import gpu_tray
        app = object.__new__(gpu_tray.TrayApp)
        app.history = []
        canvas = SparklineThreshold._Canvas()
        app._draw_sparkline(canvas, 40)
        empty = [c for c in canvas.calls if c[0] == "text"][0]
        self.assertEqual(empty[2]["fill"], gpu_tray._MUTED)
        self.assertGreaterEqual(
            _wcag_contrast(empty[2]["fill"], gpu_tray._PANEL_BG), 4.5)


class WebPanelStaleBudget(LoopHarness):
    """"数据已停更"的宽限秒数必须跟着采样周期走。

    原来 JS 里写死 `dts>25`, 只在 interval=5s 附近成立。周期调到
    30s/60s 之后, 健康监控两轮的间隔本身就超过 25 秒 —— 页面会常年挂着
    "数据已停更 (监控进程可能已停止)", 把一台好机器说成坏机器。
    """

    def test_the_budget_scales_with_the_interval(self):
        self.assertEqual(gm.web_stale_after(5), 25.0, "短周期保持原灵敏度")
        self.assertEqual(gm.web_stale_after(10), 25.0)
        self.assertEqual(gm.web_stale_after(30), 65.0)
        self.assertEqual(gm.web_stale_after(60), 125.0)

    def test_an_unusable_interval_falls_back_to_the_default_one(self):
        for bad in (None, "abc", "", object()):
            self.assertEqual(gm.web_stale_after(bad), 25.0, bad)

    def test_never_drops_below_the_floor(self):
        for tiny in (0, -5, 0.001):
            self.assertEqual(gm.web_stale_after(tiny), 25.0, tiny)

    def test_the_snapshot_carries_the_budget_for_the_current_interval(self):
        seen = []
        self.run_monitor([{HEAVY_PID: 10.0}], cycles=1,
                         cfg_overrides={"interval_seconds": 60},
                         hooks={"on_sample": seen.append})
        self.assertEqual(seen[0]["stale_after"], 125.0)

    def test_the_page_publishes_the_budget_to_the_front_end(self):
        snap = dict(ModeDisplay.SNAP)
        snap["stale_after"] = 125.0
        html = gm.panel_html(snap, {})
        self.assertIn("<span id=staleafter hidden>125</span>", html)
        self.assertIn("'staleafter'", gm._PANEL_SCRIPT,
                      "宽限值要跟着局部刷新走, 热重载改周期才不用刷页面")

    def test_the_script_uses_the_published_budget(self):
        self.assertIn("stal||25", gm._PANEL_SCRIPT)
        self.assertNotIn("dts>25", gm._PANEL_SCRIPT)

    def test_a_missing_budget_still_reports_staleness(self):
        """老快照没有这个键时不能因此永远不报(默认 25 秒)。"""
        self.assertIn("stal||25", gm._PANEL_SCRIPT)
        html = gm.panel_html(ModeDisplay.SNAP, {})
        self.assertIn("<span id=staleafter hidden>25</span>", html)
class TrayTextColorGuard(unittest.TestCase):
    """托盘侧写死的文字色也要过 AA —— §80 只量了 Web 面板那一侧。

    _MUTED(#8b8ba0) 在 #14141e / #1e1e2e 上分别是 5.48 / 4.92:1;
    #666 只有 3.18 / 2.86:1, #555566 2.25:1, #99a 3.4:1。
    面板底部那行状态、详情窗的字段名都曾经是 #666: 小字 + 低对比,
    读不出来就等于没写。
    """

    # 历史遗留的具体色值, 逐个点名: 回归了一眼看得出是谁
    BANNED = ("#666", "#666666", "#555566", "#99a", "#9999aa")

    @staticmethod
    def _code_only(src):
        """剥掉注释再扫: 注释里提到旧色值是为了说明为什么换掉它。"""
        return "\n".join(line.split("#", 1)[0]
                          for line in src.splitlines())

    def _tray_source(self):
        import gpu_tray
        return io.open(gpu_tray.__file__, encoding="utf-8").read()

    def test_the_known_bad_greys_are_gone_from_the_tray_source(self):
        src = self._code_only(self._tray_source())
        for bad in self.BANNED:
            self.assertNotIn('"%s"' % bad, src,
                             "%s 又回来了(在深色底上读不出来)" % bad)

    def test_every_hardcoded_text_color_clears_aa_on_both_surfaces(self):
        """扫源码里所有 fg/foreground 写死的色值, 逐个过 WCAG AA。

        只扫 fg/foreground: canvas 的填充色(fill)是面不是字,
        拿背景的尺子量它本来就不该过线。
        """
        import gpu_tray

        src = self._code_only(self._tray_source())
        bad = []
        for m in re.finditer(r'(?:fg|foreground)="(#[0-9a-fA-F]{3,6})"', src):
            c = m.group(1)
            hexs = c if len(c) == 7 else "#" + "".join(x * 2 for x in c[1:])
            for bg in (gpu_tray._BG, gpu_tray._PANEL_BG):
                ratio = _wcag_contrast(hexs, bg)
                if ratio < 4.5:
                    bad.append((hexs, bg, round(ratio, 2)))
        self.assertEqual(bad, [], "这些写死的文字色在深色底上不达 AA")

    def test_the_panel_status_line_uses_the_shared_muted_color(self):
        """底部状态行(核显/独显名 + 刷新节奏)是面板上唯一的说明行。"""
        src = self._code_only(self._tray_source())
        self.assertIn('tip = tk.Label(root, text="", anchor="w", fg=_MUTED)',
                      src)


class NormalizePerAdapter(unittest.TestCase):
    """normalize_total 按适配器归一: 独显忙不许把核显的读数压小。

    老实现把**所有进程**(不分显卡)的利用率加成一个总和, 超过 100% 就
    全体乘同一个系数。本机实测独显侧总和 166.5% + 核显侧 105.5% =
    272.6%, 于是整表被乘 0.367: 面板上手写着 30.35% 的进程, 任务管理器
    里是 82.8%; 更糟的是迁移阈值拿的也是缩水后的数, 该迁的进程一个都不迁。
    核显和独显是两块卡, 各算各的账才说得通。
    """

    IGPU = "00000000_000135EF"
    DGPU = "00000000_00013A83"
    TICKS = 10_000_000        # 1 秒 = 1e7 个 100ns
    # 独显侧两台机器在跑(90% + 80%), 核显侧只有两台轻的(30% + 20%)
    BUSY = [(111, IGPU, 0.30), (222, IGPU, 0.20),
            (333, DGPU, 0.90), (444, DGPU, 0.80)]
    QUIET = [(111, IGPU, 0.40), (222, IGPU, 0.30), (333, DGPU, 0.60)]

    def setUp(self):
        self._sleep = gm.time.sleep
        self._pdh = gm.pdh
        self._expand = gm.expand_counter_paths
        self._raw = gm._pdh_read_raw
        gm.time.sleep = lambda _s: None
        gm.pdh = type("P", (), {n: (lambda *a, **kw: 0) for n in
                                ("PdhOpenQueryW", "PdhAddCounterW",
                                 "PdhCollectQueryData", "PdhCloseQuery")})()

    def tearDown(self):
        gm.time.sleep = self._sleep
        gm.pdh = self._pdh
        gm.expand_counter_paths = self._expand
        gm._pdh_read_raw = self._raw

    @staticmethod
    def _path(pid, luid):
        """实例名和 PDH 真实吐出来的一模一样: luid 两段各自带 0x。"""
        hi, lo = luid.split("_")
        return (r"\GPU Engine(pid_%d_luid_0x%s_0x%s_phys_0_eng_0_engtype_3D)"
                r"\Utilization Percentage" % (pid, hi, lo))

    def _install(self, engines):
        """engines: [(pid, luid, 窗口内忙碌秒数)]; 忙碌秒数 = 利用率/100。"""
        paths = [self._path(pid, luid) for pid, luid, _b in engines]
        gm.expand_counter_paths = (
            lambda spec: list(paths) if "Utilization" in spec else [])
        reads = iter([(0, 0)] * len(engines)
                     + [(int(b * self.TICKS), self.TICKS)
                        for _p, _l, b in engines])
        gm._pdh_read_raw = lambda _h: next(reads)

    def _usage(self, **kw):
        return gm.collect_gpu_sample(**kw)[0]

    def test_the_integrated_card_is_left_alone_when_the_discrete_one_is_busy(self):
        """独显侧总和早超过 100%, 核显侧 50% 却一个数都不该动。"""
        self._install(self.BUSY)
        usage = self._usage(normalize=True)
        self.assertEqual(usage[(111, self.IGPU, 0)], 30.0)
        self.assertEqual(usage[(222, self.IGPU, 0)], 20.0)

    def test_each_adapter_is_normalized_against_its_own_sum(self):
        """独显侧 90/80 总和 170 -> 缩到 100; 占比不变。"""
        self._install(self.BUSY)
        dgpu = [v for (p, l, _ph), v in self._usage(normalize=True).items()
                if l == self.DGPU]
        self.assertEqual(len(dgpu), 2)
        self.assertAlmostEqual(sum(dgpu), 100.0, places=6)
        self.assertAlmostEqual(dgpu[0] / dgpu[1], 9.0 / 8.0, places=6)

    def test_the_scale_factor_matches_the_adapter_it_belongs_to(self):
        """同一份采样里两块卡各用自己的系数, 不再共用一个。"""
        self._install(self.BUSY)
        usage = self._usage(normalize=True)
        self.assertAlmostEqual(usage[(333, self.DGPU, 0)], 90.0 * 10 / 17,
                               places=6)
        self.assertAlmostEqual(usage[(444, self.DGPU, 0)], 80.0 * 10 / 17,
                               places=6)
        self.assertAlmostEqual(usage[(111, self.IGPU, 0)], 30.0, places=6)

    def test_nothing_is_rescaled_when_every_adapter_stays_under_100(self):
        """两块卡都没超: normalize=True 也必须是恒等变换。"""
        self._install(self.QUIET)
        # 忙碌秒数 0.40/0.30/0.60 -> 40%/30%/60%, 一块卡都没超 100%
        self.assertEqual(sorted(self._usage(normalize=True).values()),
                         [30.0, 40.0, 60.0])

    def test_normalize_off_keeps_the_task_manager_numbers(self):
        """默认关闭: 单进程读数一个数都不动, 90% 就是 90%。"""
        self._install(self.BUSY)
        usage = self._usage()
        self.assertEqual(usage[(333, self.DGPU, 0)], 90.0)
        self.assertEqual(usage[(444, self.DGPU, 0)], 80.0)

    def test_the_monitor_loop_still_forwards_the_flag_to_the_sampler(self):
        """归一化只发生在采样器里: 循环必须还把它按配置传进去。"""
        with io.open(gm.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('normalize=cfg.get("normalize_total", False)', src)


class ListWindowFollowsConfig(unittest.TestCase):
    """--list 的采样窗口要跟 config 的 interval_seconds 一致。

    PDH 读的是"窗口内引擎忙了多久 / 墙上时间": 窗口只看 1 秒而面板看满
    一个周期, 同一个进程在两边能差出数倍(实测 pid 1380: 1s 读 83.2%,
    3s 只读 28.0%)。命令行是诊断用的, 更得跟面板说同一件事。
    """

    NAMES = {1: ("a.exe", r"D:\a.exe"), 2: ("b.exe", r"D:\b.exe")}

    def setUp(self):
        self._collect = gm.collect_gpu_sample
        self._names = gm.pid_to_name
        self._luid = gm.luid_to_name
        self.seen = []
        usage = {(1, "L0", 0): 40.0, (2, "L0", 0): 5.0}
        index = {("L0", 0): None}

        def fake_collect(allowed_luids=None, normalize=False, window=None):
            self.seen.append(window)
            return usage, {}, {}, index

        gm.collect_gpu_sample = fake_collect
        gm.pid_to_name = self.NAMES.get
        gm.luid_to_name = {"L0": "Test GPU"}.get

    def tearDown(self):
        gm.collect_gpu_sample = self._collect
        gm.pid_to_name = self._names
        gm.luid_to_name = self._luid

    def _run(self, cfg=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            gm.cmd_list(cfg)
        return out.getvalue()

    def test_the_table_is_sampled_over_the_configured_interval(self):
        self._run({"interval_seconds": 5})
        # 最后一次采样是占用表, 它必须用满一个配置周期
        self.assertEqual(self.seen[-1], 5.0)

    def test_the_enumeration_pass_does_not_wait_a_full_window(self):
        """枚举只要实例清单, 等满一个窗口纯属白等。"""
        self._run({"interval_seconds": 30})
        self.assertEqual(self.seen[0], gm._WINDOW_MIN)

    def test_no_config_falls_back_to_one_second(self):
        self._run()
        self.assertEqual(self.seen[-1], 1.0)

    def test_a_broken_interval_falls_back_to_one_second(self):
        self._run({"interval_seconds": "abc"})
        self.assertEqual(self.seen[-1], 1.0)
        self._run({"interval_seconds": 0})
        self.assertEqual(self.seen[-1], gm._WINDOW_MIN)

    def test_the_header_says_which_window_it_used(self):
        """窗口决定了这些数字的含义, 得在表头旁边说出来。"""
        text = self._run({"interval_seconds": 7})
        self.assertIn("\u7a97\u53e3 7 \u79d2", text)
        self.assertIn("interval_seconds", text)

    def test_one_shot_commands_read_the_config_before_listing(self):
        """main() 得把配置喂给 cmd_list, 否则窗口永远是默认那 1 秒。"""
        with io.open(gm.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("cmd_list(load_config(args.config))", src)
        self.assertNotIn("        cmd_list()", src)


class OutputEncodingGuard(unittest.TestCase):
    """stdout/stderr 重定向时也得是 UTF-8, 且不许被一个 emoji 打断。

    控制台这条路 Windows 按 PEP 528 走 UTF-8, 没事; 但 `--list > out.txt`
    或别的程序捕捉输出时, Python 换成当前代码页(cp936): 进程名里进了
    emoji 或生偏字就 UnicodeEncodeError, 整张表扔在半道上。
    """

    class _FakeStream:
        def __init__(self):
            self.calls = []

        def reconfigure(self, **kw):
            self.calls.append(kw)

    def test_both_streams_are_switched_to_utf8(self):
        out, err = self._FakeStream(), self._FakeStream()
        with mock.patch.object(sys, "stdout", out), \
                mock.patch.object(sys, "stderr", err):
            gm.ensure_utf8_output()
        self.assertEqual(out.calls, [{"encoding": "utf-8",
                                      "errors": "backslashreplace"}])
        self.assertEqual(out.calls, err.calls)

    def test_a_windowed_frozen_process_has_nothing_to_reconfigure(self):
        """pythonw 下 stdout 是 None, 这也是唯一允许的形状。"""
        with mock.patch.object(sys, "stdout", None), \
                mock.patch.object(sys, "stderr", None):
            gm.ensure_utf8_output()      # 不许抛

    def test_a_stream_without_reconfigure_is_left_alone(self):
        """io.StringIO 之类没有 reconfigure: 跳过, 不是崩。"""
        with mock.patch.object(sys, "stdout", io.StringIO()):
            gm.ensure_utf8_output()

    def test_a_dead_stream_does_not_take_the_command_down(self):
        broken = self._FakeStream()

        def boom(**_kw):
            raise ValueError("underlying buffer detached")
        broken.reconfigure = boom
        with mock.patch.object(sys, "stdout", broken):
            gm.ensure_utf8_output()      # 接管不了的流, 不强求

    def test_main_switches_encoding_before_anything_prints(self):
        with io.open(gm.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("def main():\n    ensure_utf8_output()", src)

    def test_a_redirected_survives_a_process_name_no_code_page_has(self):
        """子进程真跑一遍: cp936 的表达不了的字符也不能让命令死掉。"""
        code = ("import sys; sys.path.insert(0, %r);"
                "import gpu_monitor as gm;"
                "gm.ensure_utf8_output();"
                "print('\U0001F600 \u82f9\u679c \uD83D\uDE00')" % os.getcwd())
        env = dict(os.environ, PYTHONIOENCODING="cp936", PYTHONUTF8="0")
        r = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                           cwd=os.getcwd(), env=env, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        # 出去的字节是 UTF-8: 中文原样, emoji 也还在(不是问号也不是乱码)
        got = r.stdout.decode("utf-8").replace("\r\n", "\n")
        self.assertEqual(got,
                         "\U0001F600 \u82f9\u679c \U0001F600\n")


class TrayRowZebraStriping(unittest.TestCase):
    """托盘表格的隔行浅色: 奇数行垫一层浅底, 长列表扫读不串行。

    整表同一种深底时, 横向读数容易串行, 热度色再把几行染红之后更明显。
    隔行是一个只设 background 的 tag, 和只设 foreground 的热度 tag 叠在
    同一行上, 各管一头: 换底色不会洗掉热度色, 换热度色也不会掀掉条纹。
    """

    def setUp(self):
        import gpu_tray
        self.tray = gpu_tray
        self.app = object.__new__(gpu_tray.TrayApp)
        self.app.sort_state = {"col": "gpu", "desc": True}
        self.app.filter_text = ""
        self.app.lock = threading.Lock()
        self.app._name_cache = {}
        self.app.snapshot = None
        self.rows = []
        self.saved = gpu_tray.pid_to_name
        gpu_tray.pid_to_name = lambda _pid: None

    def tearDown(self):
        self.tray.pid_to_name = self.saved

    class _Tree:
        """只记 insert 的 values 和 tags。"""

        def __init__(self, sink):
            self.sink = sink

        def delete(self, *_items):
            self.sink.append(("delete", _items, None))

        def insert(self, _parent, _pos, values=None, tags=None):
            self.sink.append(("insert", values, tags))

        def get_children(self):
            return ()

    class _Label:
        def config(self, text=None, fg=None):
            pass

    @staticmethod
    def _snap(util):
        snap = {"util_by_pid": {}, "gpu_all_by_pid": {},
                "dgpu_util_by_pid": {}, "shared_by_pid": {},
                "dedicated_by_pid": {}, "igpu_luids": [0], "dgpu_luids": []}
        for pid, gpu_all in util.items():
            snap["gpu_all_by_pid"][pid] = gpu_all
            snap["dedicated_by_pid"][pid] = 64 * 1024 * 1024
        return snap

    def _prepare(self, names):
        self.app._pid_name = lambda pid: names[pid]
        self.app._pref_of = lambda _p: False
        self.app._gpu_name = lambda luid: f"gpu{luid}"

    def _tags(self):
        return [tags for kind, _v, tags in self.rows if kind == "insert"]

    def _refresh(self):
        self.app._refresh_tree(self._Tree(self.rows), self._Label())

    def test_every_other_row_gets_the_alt_background(self):
        """隔行从第一行起算: 下标偶数保持表格底, 奇数才垫浅底。"""
        util = {100: 90.0, 200: 70.0, 300: 40.0, 400: 10.0}
        self.app.snapshot = self._snap(util)
        self._prepare({p: f"p{p}.exe" for p in util})
        self._refresh()
        self.assertEqual(self._tags(),
                         [("hot",), ("hot", "rowalt"),
                          ("warm",), ("cool", "rowalt")],
                         "四行热度是 hot/hot/warm/cool, 只有奇数行带隔行 tag")

    def test_the_alt_tag_never_washes_out_the_heat_colour(self):
        """隔行 tag 只管背景: 每行第一个 tag 必须还是热度类。"""
        util = {100: 90.0, 200: 70.0, 300: 40.0, 400: 10.0}
        self.app.snapshot = self._snap(util)
        self._prepare({p: f"p{p}.exe" for p in util})
        self._refresh()
        want = ["hot", "hot", "warm", "cool"]
        for idx, tags in enumerate(self._tags()):
            self.assertEqual(tags[0], want[idx],
                             "第 %d 行的热度被隔行 tag 顶掉了" % idx)
            self.assertEqual(list(tags[1:]),
                             [self.tray._ROW_ALT_TAG] if idx % 2 else [],
                             "第 %d 行的隔行 tag 不对" % idx)

    def test_filtered_rows_restart_the_stripes_from_the_top(self):
        """筛选后剩下的行从第一行重新隔行, 不沿用筛选前的下标。

        沿用旧下标的话, 筛掉第一行会让整表条纹错位半行, 看着像渲染错了。
        这里留下的两行原本排在第 3、第 5 位, 重启后应当是无条纹 + 有条纹。
        """
        util = {100: 90.0, 200: 70.0, 350: 40.0, 400: 20.0, 550: 10.0}
        self.app.snapshot = self._snap(util)
        self._prepare({100: "a.exe", 200: "b.exe", 350: "c.exe",
                       400: "d.exe", 550: "e.exe"})
        self.app._set_filter("5")
        self._refresh()
        self.assertEqual([v[0] for kind, v, _t in self.rows
                          if kind == "insert"], ["c.exe", "e.exe"])
        self.assertEqual(self._tags(), [("warm",), ("cool", "rowalt")],
                         "筛选后剩下的行要从头开始隔行")

    def test_every_heat_colour_still_clears_aa_on_the_alt_row(self):
        """隔行底比表格底亮一半, 热度色叠上去不能掉到 AA 以下。

        实测: _FG 10.88 / hot 5.33 / warm 8.08 / cool 7.56, 最紧的 hot
        也有 5.33:1。这是将来换底色时的刹车: 再亮一档 hot 就跌破 4.5。
        """
        for cls, color in self.tray._HEAT_COLOR.items():
            ratio = _wcag_contrast(color, self.tray._ROW_ALT_BG)
            self.assertGreaterEqual(
                ratio, 4.5,
                "热度 %r 的 %s 在隔行底 %s 上只有 %.2f:1"
                % (cls, color, self.tray._ROW_ALT_BG, ratio))

    def test_the_alt_row_is_far_enough_from_the_table_background(self):
        """两底亮度差太小的条纹等于没画, 反而像脏屏。

        Fluent 的分层方向跟主题走: 浅色主题里隔行底比表格底**暗**一档
        (白 -> #f1f1f1, 约 10%), 深色主题里比表格底**亮**一档(#2d2d2d ->
        #343434, 约 26%)。所以阈值按当前主题的方向取, 不再永远要求
        "更亮" —— 那样浅色主题下这条会永远红, 或者干脆没人跑浅色。
        """
        base = _wcag_luminance(self.tray._PANEL_BG)
        alt = _wcag_luminance(self.tray._ROW_ALT_BG)
        self.assertNotEqual(self.tray._ROW_ALT_BG, self.tray._PANEL_BG,
                            "隔行底和表格底同一个色, 条纹白画")
        light = self.tray._PALETTE is self.tray._FLUENT_LIGHT
        delta = (alt - base) / base * 100
        if light:
            # 白底上 #f1f1f1 只有约 10% 的差, 阈值定 6%: 再小就看不出
            self.assertLessEqual(delta, -6.0,
                                 "浅色主题里隔行要更暗, 现在只差 %.0f%%" % delta)
        else:
            self.assertGreaterEqual(delta, 25.0,
                                     "隔行底只比表格底亮 %.0f%%, 肉眼分不出来"
                                     % delta)

    def test_the_alt_tag_is_registered_background_only(self):
        """注册隔行 tag 时写 foreground, 会把整片条纹的热度色洗成灰。"""
        body = inspect.getsource(self.tray.TrayApp._panel_build)
        m = re.search(r"tag_configure\(\s*_ROW_ALT_TAG[^)]*\)", body)
        self.assertIsNotNone(m, "隔行 tag 没有在 _panel_build 里注册")
        self.assertIn("background=", m.group(0), "隔行 tag 要设背景")
        self.assertNotIn("foreground", m.group(0),
                         "前景是热度 tag 的地盘, 隔行 tag 不许碰")


class TrayPanelUiIsolation(unittest.TestCase):
    """跑测试不许冲掉仓库里那份真实的 panel_ui.json。

    `TraySingleInterpreter` 只把 config.json 指到临时目录，_UI_FILE 还指着
    仓库里那一份；而 `test_closing_a_window_frees_the_slot` 会真的走一遍
    `on_close`，于是每跑一遍全套测试，列宽/筛选词/排序列/置顶就被替身数据
    覆盖：列宽全是替身 `tree.column` 的返回值，geometry 是替身 `Toplevel`
    的返回值。用户自己的面板布局没有任何恢复办法，属于"跑测试顺手毁数据"。

    这条用例把两件事钉死：用例跑的时候 `_UI_FILE` 必须在临时目录里；
    跑完之后模块级的 `_UI_FILE` 和磁盘上那份都要原样不动。
    """

    def test_closing_the_panel_writes_only_inside_the_temp_dir(self):
        import gpu_tray
        real = gpu_tray._UI_FILE
        had = os.path.exists(real)
        with open(real, "rb") as f:
            before = f.read()

        case = TraySingleInterpreter("test_closing_a_window_frees_the_slot")
        case.setUp()
        try:
            self.assertTrue(case.gt._UI_FILE.startswith(case.dir),
                            "_UI_FILE 还指着仓库: %s" % case.gt._UI_FILE)
            self.assertNotEqual(case.gt._UI_FILE, real,
                               "关窗会往这个路径写布局, 不能是仓库那份")
        finally:
            case.tearDown()

        self.assertEqual(gpu_tray._UI_FILE, real, "_UI_FILE 没被还原")
        if had:
            with open(real, "rb") as f:
                self.assertEqual(f.read(), before,
                                 "跑测试把仓库里的 panel_ui.json 改写了")

    def test_the_state_file_still_lives_next_to_the_exe(self):
        """目录隔离归隔离, 真跑起来还是要落在 exe 旁边(读的是同一份)。

        比的是 `_EXE_DIR` —— 套件顶层把 `gpu_tray._DIR` 指去了临时目录(见
        TrayDebugLogIsolation), 而 `_UI_FILE` 是 import 时按真实目录算好的,
        两者本来就该不同; 这条要守的是"默认状态文件与 import 时同目录"。
        """
        import gpu_tray
        self.assertTrue(gpu_tray._UI_FILE.endswith("panel_ui.json"))
        self.assertEqual(os.path.dirname(gpu_tray._UI_FILE), _EXE_DIR)


class TrayPanelGeometryGuard(unittest.TestCase):
    """手改坏的 geometry 不许把面板打死, 越界的位置不许把窗口藏起来。

    `panel_ui.json` 是人也能改的文件。老代码只校验了"geometry 是不是
    字符串", 而 Tcl 要的是严格的 `宽x高[+X[+Y]]`：存成 "abc" / "1040x" /
    "-10x-10" 一律 `TclError: bad geometry`，被 `_panel_build` 的总 except
    吞掉之后窗口建一半扔下，用户看到的是"打开面板点了没反应"。反过来，
    形状合法但整窗在屏幕外（面板拖到副屏、副屏拔了）Tcl 一声不吭，
    窗口就是开在看不见的地方 —— 两种故障长得一模一样。
    """

    WANT = {"1040x470": (1040, 470, 0, 0),
            "1020x360+100+200": (1020, 360, 100, 200),
            "1020x360+100-200": (1020, 360, 100, -200),
            "800x600-100+50": (800, 600, -100, 50),
            "1920x1080+1920+0": (1920, 1080, 1920, 0),
            "1020x360+0+0": (1020, 360, 0, 0)}

    def test_well_formed_geometry_survives_a_round_trip(self):
        import gpu_tray as gt
        for text, want in self.WANT.items():
            self.assertEqual(gt._parse_geometry(text), want,
                             "形状合法的不许被改坏: %s" % text)

    def test_every_malformed_shape_falls_back_to_the_default(self):
        import gpu_tray as gt
        for junk in ("abc", "1040x", "x470", "1040 x 470", "-10x-10", "",
                     "1040x470+1+2+3", "1040x470++100", "1020X360",
                     "1040x470+", "   ", None, 5, [], {}):
            got = gt._onscreen_geometry(junk, 1280, 720)
            self.assertEqual(got, "1040x470",
                             "%r 喂给 root.geometry() 会炸, 该退回默认" % (junk,))

    def test_a_geometry_saved_off_the_current_desktop_is_dropped(self):
        """副屏拔了之后, +5000+5000 / -3000+0 / 贴着右边缘都要退回默认。"""
        import gpu_tray as gt
        for gone in ("1020x360+5000+5000", "1020x360-3000+0",
                     "1020x360+1200+40", "1020x360+0+900"):
            self.assertEqual(gt._onscreen_geometry(gone, 1280, 720),
                             "1040x470",
                             "%s 整个在屏幕外, 开出来等于没有" % gone)

    def test_a_partially_offscreen_window_is_kept(self):
        """往左探出半截是用户自己摆的, 标题栏还看得见就不该动它。"""
        import gpu_tray as gt
        self.assertEqual(gt._onscreen_geometry("1020x360-50+100", 1280, 720),
                         "1020x360-50+100")
        self.assertEqual(gt._onscreen_geometry("1020x360+100+650", 1280, 720),
                         "1020x360+100+650")

    def test_a_window_that_cannot_report_its_screen_is_not_fatal(self):
        """拿不到桌面尺寸只是少一层保险, 不许让面板打不开。

        winfo_vrootwidth 在替身窗口上返回的不是数字, 在主屏都没映射时也可能
        返回 0。这种时候越界判定退化成"不判"就是了, 抛异常会让整个
        _panel_build 半路死掉 —— 那比窗口位置偏了严重得多。
        """
        import gpu_tray as gt

        class _Dumb:
            def winfo_vrootwidth(self):
                return None

            def winfo_vrootheight(self):
                return None

            def winfo_screenwidth(self):
                return None

            def winfo_screenheight(self):
                return None

        class _Boom:
            def winfo_vrootwidth(self):
                raise RuntimeError("boom")

            def winfo_vrootheight(self):
                raise RuntimeError("boom")

            def winfo_screenwidth(self):
                raise RuntimeError("boom")

            def winfo_screenheight(self):
                raise RuntimeError("boom")

        class _Zero:
            def winfo_vrootwidth(self):
                return 0

            def winfo_vrootheight(self):
                return 0

            def winfo_screenwidth(self):
                return 0

            def winfo_screenheight(self):
                return 0

        for cls in (_Dumb, _Boom, _Zero):
            self.assertEqual(gt._screen_size(cls()), (1280, 800),
                             "%s 拿不到尺寸也要给个能用的兜底" % cls.__name__)

    def test_the_panel_routes_geometry_through_the_guard(self):
        """源码守卫: _panel_build 必须过筛子, 不许把 ui 里的值直接喂 Tcl。"""
        import gpu_tray as gt
        import inspect
        body = inspect.getsource(gt.TrayApp._panel_build)
        self.assertIn("_onscreen_geometry(", body,
                      "geometry 要过 _onscreen_geometry 再生产")
        self.assertNotIn('root.geometry(ui.get("geometry"', body,
                           "直接把 ui 里的 geometry 喂给 Tcl 了")
        self.assertIn("_screen_size(", body, "越界判定要知道桌面尺寸")


class TrayPanelBuildFailureIsLoud(unittest.TestCase):
    """面板建不起来必须留证据, 而且不许漏窗口。

    `_panel_build` 的总 except 以前是这个形状:

        except Exception:
            if counted: self._ui_windows -= 1
            self.panel_open = False

    计数退了、标志也复位了, 看着很周全 —— 但异常本身被吞得干干净净,
    `tray_debug.log` 里一个字都没有, 半建的 Toplevel 也没人 destroy。
    真出问题时用户看到的是"打开实时面板点了没反应", 而日志里连一行痕迹
    都没有, 想排查都没有抓手; 并且每点一次菜单就漏一个看不见的窗口。
    """

    def setUp(self):
        import gpu_tray
        self.gt = gpu_tray
        self.saved = (gpu_tray.tk, gpu_tray.ttk, gpu_tray._dbg,
                      gpu_tray._UI_FILE)
        self.fake_tk = mock.MagicMock(name="tk")
        self.host = self.fake_tk.Tk.return_value
        self.windows = []

        def toplevel(*_args, **_kwargs):
            win = mock.MagicMock(name="toplevel")
            self.windows.append(win)
            return win

        self.fake_tk.Toplevel.side_effect = toplevel
        gpu_tray.tk = self.fake_tk
        gpu_tray.ttk = mock.MagicMock(name="ttk")
        self.notes = []
        gpu_tray._dbg = lambda msg: self.notes.append(msg)
        # 和 §89 同一个教训: _UI_FILE 必须指去临时目录, 不然 setUp 里
        # 谁走一次关窗就把仓库里那份布局冲了
        self.dir = tempfile.mkdtemp(prefix="gpumig-buildfail-")
        self.ui_file = os.path.join(self.dir, "panel_ui.json")
        with open(self.ui_file, "w", encoding="utf-8") as f:
            json.dump({"geometry": "1040x470"}, f)
        gpu_tray._UI_FILE = self.ui_file

        app = object.__new__(gpu_tray.TrayApp)
        app._ui_root = self.host
        app._ui_windows = 0
        app.panel_open = False
        app.paused = False
        app.stop = False
        app.filter_text = ""
        app.sort_state = {"col": "auto", "desc": True}
        app.snapshot = None
        app.lock = threading.Lock()
        app._name_cache = {}
        app._root_ref = None
        app._pref_of = lambda _full: False
        app._pid_name = lambda _pid: "probe.exe"
        app._gpu_name = lambda _luid: "gpu"
        app._title_base = "GPU probe"
        self.app = app

    def tearDown(self):
        self.gt.tk, self.gt.ttk, self.gt._dbg, self.gt._UI_FILE = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def _build(self):
        self.app.panel_open = True
        self.app._panel_build()
        self.assertTrue(self.windows, "_panel_build 连窗口都没建")
        return self.windows[0]

    def test_a_failed_build_leaves_a_traceback_behind(self):
        """面板建失败不许静默: 日志里必须留下罪证。"""
        import gpu_tray
        saved = gpu_tray._apply_fluent_theme

        def boom():
            raise RuntimeError("injected panel failure")

        gpu_tray._apply_fluent_theme = boom
        try:
            self._build()
        finally:
            gpu_tray._apply_fluent_theme = saved
        # _dbg 在 setUp 里已被替换成记账, 不用碰真实日志文件
        joined = "\n".join(self.notes)
        self.assertIn("面板建立失败", joined,
                      "面板建失败居然一个字都没记, 出问题时无从排查")
        self.assertIn("injected panel failure", joined,
                      "记是记了, 但没有 traceback 正文")

    def test_a_failed_build_reclaims_the_half_built_window(self):
        """半建的窗口要 destroy: 不然每点一次面板就漏一个不可见窗口。"""
        import gpu_tray
        saved = gpu_tray._apply_fluent_theme
        gpu_tray._apply_fluent_theme = lambda: (_ for _ in ()).throw(
            RuntimeError("injected"))
        try:
            win = self._build()
        finally:
            gpu_tray._apply_fluent_theme = saved
        self.assertTrue(win.destroy.called,
                        "面板建失败时没有回收半建的窗口")
        self.assertEqual(self.app._ui_windows, 0, "窗口计数没退还")
        self.assertFalse(self.app.panel_open, "打开标志没复位")

    def test_a_healthy_build_never_destroys_its_own_window(self):
        """保险丝: 上面两条不许把正常路径也带坏。"""
        win = self._build()
        win.destroy.assert_not_called()
        self.assertEqual(self.app._ui_windows, 1)
        self.assertTrue(self.app.panel_open)
        self.assertNotIn("面板建立失败", "\n".join(self.notes))

class MonitorLoopSurvivesExceptions(LoopHarness):
    """监控循环靠底: 一轮里任何没接住的异常都不许把循环(甚至托盘)带崩。

    老代码只在 collect_gpu_sample 外接了 OSError; 聚合/进程名/注册表回读/
    NVML/迁移/面板回调任意一步抛个非 OSError, 就一路冒到 _monitor_thread,
    再把整个托盘退掉。循环现在整段兜底: traceback 落盘 + 睡一个周期 +
    继续下一轮。这里把"异常不致命、循环照转"钉死。
    """

    def _raiser(self, boom_on=2):
        calls = {"n": 0}

        def hook(_snapshot):
            calls["n"] += 1
            if calls["n"] == boom_on:
                raise ValueError("boom-from-on-sample")
        return hook, calls

    def test_loop_survives_non_oserror_and_keeps_going(self):
        hook, calls = self._raiser()
        done = self.run_monitor([{100: 80.0}] * 8, cycles=5,
                                hooks={"on_sample": hook})
        # on_sample 被调满 5 次: 第 2 次抛了非 OSError, 后面 3/4/5 次照常
        # 跑到 —— 说明那一轮异常被吃掉后循环没停, 也没往外冒。
        self.assertEqual(calls["n"], 5)
        self.assertGreaterEqual(done, 5)

    def test_exception_logs_traceback_and_keeps_sampling(self):
        hook, calls = self._raiser()
        self.run_monitor([{100: 80.0}] * 8, cycles=4,
                         hooks={"on_sample": hook})
        self.assertEqual(calls["n"], 4)          # 抛过异常之后仍跑满 4 轮
        joined = "\n".join(self.log_lines)
        self.assertIn("监控循环异常", joined)
        self.assertIn("ValueError", joined)
        self.assertIn("Traceback (most recent call last)", joined)

class IntervalBoundsGuard(unittest.TestCase):
    """采样周期要有上界，一个手误不能让整套采集停摆/把 --list 挂死。

    config.json 是人也能改的文件。interval_seconds 敲成 3600 这类值时,
    老代码只在 _NUMBER_RULES 里给了下限 0.5、没有上限, _sample_window 也只有下限:
    监控变成一小时一采、--list 先打表头再干等一小时才退出——用户只会以为程序
    卡死/崩溃。这里把"上限夹到 60s、中间值原样、下限仍是 0.05s 不被上限波及"
    在合并配置与窗口函数两侧都钉死。
    """

    def _merged(self, seconds):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cfg = gm.merged_config({"interval_seconds": seconds})
        return cfg, buf.getvalue()

    def test_huge_interval_clamped_to_max(self):
        cfg, _out = self._merged(3600)
        self.assertEqual(cfg["interval_seconds"], gm._INTERVAL_MAX)
        self.assertEqual(gm._INTERVAL_MAX, 60.0)

    def test_normal_interval_untouched(self):
        cfg, _out = self._merged(3.5)
        self.assertEqual(cfg["interval_seconds"], 3.5)

    def test_sample_window_is_capped(self):
        self.assertEqual(gm._sample_window(3600), gm._INTERVAL_MAX)

    def test_sample_window_mid_value_passes_through(self):
        self.assertEqual(gm._sample_window(30), 30)

    def test_sample_window_floor_still_wins(self):
        # 上界不许把下界盖掉: 0.001 仍被抬到 0.05 的地板
        self.assertEqual(gm._sample_window(0.001), gm._WINDOW_MIN)


class FaultDumpGuard(unittest.TestCase):
    """原生故障留现场, 但这份日志读错了会把活程序当成在崩。

    faulthandler 挂的是向量化异常处理器, 在被调方自己的 SEH 之前先跑: 访问
    冲突即使被对方接住、ctypes 照常返回、Python 收不到任何异常, 这里也照样
    落一条 "Windows fatal exception"。install_fault_dump() 打开 exe 旁的
    gpu_monitor_crash.log 并装 faulthandler(all_threads=True)。这里钉死:
    装了且启用、重复调用复用同一句柄、栈能落盘、main() 在解析命令行之前就
    装好(早期崩也有现场)、新建的日志开头带一行"转储不等于死亡"的说明、
    已存在的日志一个字节都不许多写、超限的旧日志先滚成 .1 而不是无限长。
    """

    def setUp(self):
        # _DIR 指向 exe 所在目录, 测试必须重定向到临时目录, 别污染仓库/
        # 线上 exe 目录; faulthandler 是进程级全局, 用完必须还原并关闭。
        self._orig_dir = gm._DIR
        self._orig_fh = gm._FAULT_FH
        self._enabled_before = faulthandler.is_enabled()
        gm._FAULT_FH = None
        self._tmp = tempfile.mkdtemp(prefix="gpumig-fault-")
        gm._DIR = self._tmp

    def tearDown(self):
        fh = gm._FAULT_FH
        gm._FAULT_FH = self._orig_fh
        gm._DIR = self._orig_dir
        try:
            faulthandler.disable()   # 先停, 免得文件关掉后还被往里写
        except Exception:
            pass
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_install_opens_crash_log_and_enables(self):
        fh = gm.install_fault_dump()
        self.assertIsNotNone(fh)               # 打开成功, 句柄被缓存
        self.assertIs(fh, gm._FAULT_FH)
        self.assertTrue(os.path.isfile(
            os.path.join(self._tmp, "gpu_monitor_crash.log")))
        self.assertTrue(faulthandler.is_enabled())

    def test_repeated_call_reuses_the_same_handle(self):
        first = gm.install_fault_dump()
        second = gm.install_fault_dump()
        self.assertIs(first, second)            # 不重复打开/重复 enable

    def test_dump_traceback_lands_in_the_crash_log(self):
        fh = gm.install_fault_dump()
        faulthandler.dump_traceback(file=fh)
        try:
            fh.flush()
        except (OSError, ValueError):
            pass
        with io.open(os.path.join(self._tmp, "gpu_monitor_crash.log"),
                     encoding="utf-8") as f:
            body = f.read()
        # dump_traceback 把当前 Python 栈写进目标文件: 有栈帧说明
        # "装 handler + 指向该文件"这条路是通的, 真崩时栈能落盘。
        self.assertIn("File", body)

    def _read_crash_log(self, name="gpu_monitor_crash.log"):
        with io.open(os.path.join(self._tmp, name), encoding="utf-8") as f:
            return f.read()

    def test_a_fresh_log_opens_with_a_note_that_a_dump_is_not_a_death(self):
        """说明必须落盘: 光有文件名 "crash" 就会把人引向错误的结论。

        线上真实的一次误读: 165 条 "Windows fatal exception" 全来自同一个
        存活了 17.5 小时的实例(线程 ID 全程一致、期间没重启), 而上一轮交接
        据此前判过"该文件 0 字节 = 上线版至今没崩"。两头都是把条数当死活。
        """
        fh = gm.install_fault_dump()
        fh.flush()
        body = self._read_crash_log()
        self.assertIn("不等于进程已死", body)
        self.assertIn("启动监控", body)
        self.assertIn("有没有重启", body)

    def test_an_existing_log_gets_not_a_single_extra_byte(self):
        """说明只给新建的文件: 每实例追加一行的写法会被子进程用例打穿。

        有的用例是拿 subprocess 真跑 gpu_monitor.py 的, 那种进程的 _DIR 就是
        仓库目录。只要每次启动都往 crash log 追加一行, "跑测试不许动仓库里的
        状态文件"这条契约当场破 —— 首版就是这么写坏的, 一轮测试给线上文件
        插进 3 行。
        """
        path = os.path.join(self._tmp, "gpu_monitor_crash.log")
        with io.open(path, "w", encoding="utf-8") as f:
            f.write("Windows fatal exception: access violation\n")
        before = os.stat(path).st_size
        gm.install_fault_dump().flush()
        self.assertEqual(os.stat(path).st_size, before)
        self.assertEqual(self._read_crash_log(),
                         "Windows fatal exception: access violation\n")

    def test_an_oversized_log_rolls_before_the_new_instance_writes(self):
        """只 append 不滚动的日志会一直长: 实测已经长到 295KB/165 段。

        滚走之后新文件里只该有说明, 旧内容整段进 .1。
        """
        path = os.path.join(self._tmp, "gpu_monitor_crash.log")
        old = "x" * (gm._CRASH_LOG_MAX + 1)
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(old)
        fh = gm.install_fault_dump()
        fh.flush()
        self.assertEqual(self._read_crash_log("gpu_monitor_crash.log.1"),
                         old)
        self.assertIn("不等于进程已死", self._read_crash_log())

    def test_a_log_at_the_cap_is_left_where_it_is(self):
        """边界: 恰好到上限不算超, 不该把还在写的文件搬走。"""
        path = os.path.join(self._tmp, "gpu_monitor_crash.log")
        with io.open(path, "w", encoding="utf-8") as f:
            f.write("y" * gm._CRASH_LOG_MAX)
        fh = gm.install_fault_dump()
        fh.flush()
        self.assertFalse(os.path.exists(path + ".1"))
        self.assertTrue(self._read_crash_log().startswith("y" * 64))

    def test_main_installs_fault_dump_before_arg_parsing(self):
        # 用 AST 而非源码子串: 注释/字符串里出现"install_fault_dump()"
        # 不算, 必须是 main() 里一次真实的 Call, 且在 ap.parse_args 之前
        # (之后才装的话, 解析/启动早期崩了就没现场)。
        def callee(func):
            if isinstance(func, ast.Name):
                return func.id
            if isinstance(func, ast.Attribute):
                return func.attr
            return None

        tree = ast.parse(inspect.getsource(gm))
        main = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        install_lineno = parse_lineno = None
        for call in (c for c in ast.walk(main) if isinstance(c, ast.Call)):
            name = callee(call.func)
            if name == "install_fault_dump" and install_lineno is None:
                install_lineno = call.lineno
            elif name == "parse_args":
                parse_lineno = call.lineno
        self.assertIsNotNone(install_lineno)
        self.assertIsNotNone(parse_lineno)
        self.assertLess(install_lineno, parse_lineno)



class RuntimeLogsAreIgnored(unittest.TestCase):
    """代码往 _DIR 旁边写的每个 .log 都必须登记进 .gitignore。

    漏一个就多一份未跟踪噪声: gpu_monitor_crash.log 挂着 295KB 的 ?? 在
    git status 里, 谁都可能顺手把它 commit 进去。这里不点名, 从源码扫
    os.path.join(_DIR, "...log"), 以后新加日志忘了登记就当场红。
    """

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _logs_the_code_writes(self):
        names = set()
        for mod in ("gpu_monitor.py", "gpu_tray.py"):
            with io.open(os.path.join(self.ROOT, mod),
                         encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "join"
                        and len(node.args) >= 2
                        and isinstance(node.args[0], ast.Name)
                        and node.args[0].id == "_DIR"
                        and isinstance(node.args[1], ast.Constant)
                        and isinstance(node.args[1].value, str)
                        and node.args[1].value.endswith(".log")):
                    names.add(node.args[1].value)
        return names

    def test_the_scan_finds_the_logs_we_know_about(self):
        # 扫不到东西 = 守卫自己空转, 必须比"全绿"先响
        self.assertIn("gpu_monitor.log", self._logs_the_code_writes())
        self.assertIn("tray_error.log", self._logs_the_code_writes())

    def test_every_runtime_log_is_gitignored(self):
        with io.open(os.path.join(self.ROOT, ".gitignore"),
                     encoding="utf-8") as f:
            ignored = {line.strip() for line in f if line.strip()}
        missing = sorted(self._logs_the_code_writes() - ignored)
        self.assertEqual(missing, [],
                         f"这些运行期日志没进 .gitignore: {missing}")


# 倒序跑必须在**子进程**里, 原因见下面那条用例的 docstring。脚本用 -c
# 传进去, 省得在 tests/ 里多放一个不是用例的 .py(discover 会导入它)。
_REVERSE_SCRIPT = r"""
import io, os, sys, unittest
here, exclude = sys.argv[1], sys.argv[2]


def flat(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flat(item)
        else:
            yield item


tests = [t for t in flat(unittest.TestLoader().discover(here))
         if type(t).__name__ != exclude]
print("count=%d" % len(tests))
if len(tests) <= 400:
    sys.exit(3)
tests.reverse()
out = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
    unittest.TestSuite(tests))
if not out.wasSuccessful():
    for t, tb in (out.failures + out.errors)[:3]:
        print("红:", t.id())
        print(tb[-1200:])
    sys.exit(1)
"""


class SuiteRunsInReverseOrder(unittest.TestCase):
    """整套用例倒着再跑一遍也必须全绿 —— “顺序无关”不能只写在 README 里。

    README 那句“全套 N 条用例四种跑法全绿”从 488 条起就没人复验过: 加用例
    只跑正序, 于是 TrayDpiAwareness 里一条把 gpu_tray.enable_dpi_awareness
    换成替身却不还原的用例潜伏了下来 —— 正序时同类里断言返回值的那条按字母序
    排在它前面, 永远咬不到; 倒序一跑就红(拿到 None 而不是 False)。详见
    HANDOFF.md §111。

    所以把倒序跑做成一条正式用例。但**必须在子进程里跑**: 本模块顶层有副作用
    (建 _SUITE_TMP、把 gpu_tray._DIR 指过去), 同进程再 discover 一次会把
    test_gpu_monitor 导入成第二个模块实例 —— 又建一个临时目录、又把 _DIR 指
    过去。守卫按字母序排在所有 Tray* 前面(S<T), 它跑完之后
    TrayDebugLogIsolation / TrayPanelUiIsolation 就拿自己那份 _SUITE_TMP 去
    比被换掉的 _DIR, 全红; pystray 的窗口类原子也会在同进程注册第二次。首版
    正是这么写的, 实测还引出 mock 递归到 C 栈溢出、faulthandler 往仓库的
    gpu_monitor_crash.log 多写一段。见 HANDOFF.md §112。

    两道防线防止套娃: 子进程按类名把自己排除, 并且带着
    GPU_MIG_REVERSE_CHILD 环境变量, 万一类名对不上也不会再生一个进程。
    代价是整套耗时翻倍(实测 +4s 左右)。
    """

    def test_the_whole_suite_passes_when_run_backwards(self):
        if os.environ.get("GPU_MIG_REVERSE_CHILD"):
            self.skipTest("已经在倒序子进程里, 不再套娃")
        here = os.path.dirname(os.path.abspath(__file__))
        mine = type(self).__name__     # 动态取: 改了类名也不会漏排除
        env = dict(os.environ, GPU_MIG_REVERSE_CHILD="1")
        try:
            done = subprocess.run(
                [sys.executable, "-X", "utf8", "-c", _REVERSE_SCRIPT,
                 here, mine],
                cwd=os.path.dirname(here), env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=300)
        except subprocess.TimeoutExpired:
            self.fail("倒序跑 300s 没结束 —— 十有八九是哪条用例真起了"
                      "线程或服务器又没关掉")
        text = (done.stdout or "") + (done.stderr or "")
        hit = re.search(r"^count=(\d+)$", done.stdout or "", re.M)
        if not hit:
            self.fail("倒序子进程没报条数, 输出末尾:"
                      f"\n{text[-2000:]}")
        # 条数下限是防空转的元守卫: discover 拿不到东西时(start_dir 写错、
        # 文件名不再匹配 test*.py)倒序跑一个空列表照样"全绿"。
        self.assertGreater(int(hit.group(1)), 400,
                           f"只 discover 到 {hit.group(1)} 条, 守卫名存实亡")
        if done.returncode:
            self.fail(f"倒序跑红了(退出码 {done.returncode}), 说明有用例"
                      "依赖执行顺序 —— 十有八九是换了模块属性没还原。"
                      f"\n输出末尾:\n{text[-2500:]}")


def _fake_power_dll(line_status, ret=1):
    """假 kernel32: 往调用方给的结构体指针写 ACLineStatus, 再返回 ret。

    line_status 可以给单个字节, 也可以给一串 —— 给一串时按调用顺序依次取,
    取完就停在最后一个, 于是"先拔电再插回"这种翻转也能演出来。
    ret=0 表示 API 失败: 这时结构体一个字节都不写, 而且调用方不该采信它。
    """
    status_type = ctypes.POINTER(gm._POWER_STATUS)
    queue = (list(line_status) if isinstance(line_status, (list, tuple))
             else [line_status])

    def win_dll(_name):
        def get_system_power_status(ptr):
            if ret:
                value = queue.pop(0) if len(queue) > 1 else queue[0]
                ctypes.cast(ptr, status_type).contents \
                    .ACLineStatus = value
            return ret

        return mock.MagicMock(GetSystemPowerStatus=get_system_power_status)

    return win_dll


class OnBatteryGate(unittest.TestCase):
    """on_battery() 的 ctypes 真值: ACLineStatus 三态与两条 fail-safe。

    它是 power_aware 的入口, 但此前从没被真值驱动过 —— LoopHarness 一律
    `gm.on_battery = lambda: ...`, PowerAwarePolicy 那几条也是桩。真实实现里
    对 GetSystemPowerStatus 的调用、ACLineStatus 的三态语义、两条 fail-safe,
    一次都没跑过。拔电源是用户真会做的事, 这里读错就得到一整套错的迁移门槛,
    而界面上没有任何一处会提示"现在到底按哪个数在判"。
    """

    def setUp(self):
        # 先确认正在驱动的确实是源码里那个函数, 而不是别处遗留的替身 ——
        # 否则下面几条即使全绿也是空转。
        self.assertIs(gm.on_battery.__globals__, gm.__dict__,
                      "gm.on_battery 被换成了别处的函数, 先查测试卫生")
        self.assertEqual(gm.on_battery.__name__, "on_battery")

    def _drive(self, **kw):
        patcher = mock.patch.object(gm.ctypes, "WinDLL",
                                    _fake_power_dll(**kw))
        patcher.start()
        self.addCleanup(patcher.stop)
        return gm.on_battery()

    def test_unplugged_reports_battery(self):
        self.assertTrue(self._drive(line_status=0))

    def test_plugged_in_reports_not_battery(self):
        self.assertFalse(self._drive(line_status=1))

    def test_unknown_status_is_not_battery(self):
        """除 0 之外一律算"不在电池上", 未知态尤其不行。

        Win32 的 ACLineStatus 是 0=电池 / 1=外接 / 255=未知, 但结构体
        用的是 c_byte(有符号): 255 写进去读回来是 -1。所以这里按"结构体
        里实际会出现的值"来钉: 未知态的两种写法、外接、以及任何别的数,
        都不该被当成电池 —— 按在电池上处理的话, 一台报告不出电源状态的
        机器就会把性能用户无故按省电阈值砍迁移。
        """
        for value in (1, 2, 42, 127, -1, -128):
            with self.subTest(ac_line_status=value):
                self.assertFalse(self._drive(line_status=value))

    def test_a_failed_call_does_not_trust_the_structure(self):
        # 返回 0 = API 失败。只盯着结构体看的话, 读到的是一块没填过的内存
        self.assertFalse(self._drive(line_status=0, ret=0))

    def test_an_exception_is_swallowed_rather_than_propagated(self):
        """拿不到 kernel32 时, 上层只该得到一句"不在电池上"。"""
        def boom(_ptr):
            raise OSError("no kernel32 here")

        patcher = mock.patch.object(
            gm.ctypes, "WinDLL",
            lambda _name: mock.MagicMock(GetSystemPowerStatus=boom))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertFalse(gm.on_battery())

    def test_the_status_layout_matches_the_windows_struct(self):
        """_POWER_STATUS 的内存布局必须和 Win32 那个对得上。

        ctypes 结构体对不齐不报错, 只会默默读到隔壁字段的字节 —— 这里钉死
        字段顺序与偏移, 免得有人插个字段就把 ACLineStatus 读成 BatteryFlag
        ("电池/外接"于是永远判反)。上面几条用例写结构体用的也是同一个定义,
        布局真错了它们照样全绿, 所以这条不能省。
        """
        fields = gm._POWER_STATUS._fields_
        self.assertEqual([f[0] for f in fields],
                         ["ACLineStatus", "BatteryFlag",
                          "BatteryLifePercent", "Reserved1",
                          "BatteryLifeTime", "BatteryFullLifeTime"])
        self.assertEqual(gm._POWER_STATUS.ACLineStatus.offset, 0)
        self.assertEqual(gm._POWER_STATUS.BatteryFlag.offset, 1)
        self.assertEqual(gm._POWER_STATUS.BatteryLifeTime.offset, 4)
        self.assertEqual(gm._POWER_STATUS.BatteryFullLifeTime.offset, 8)


class PolicyTextBattery(unittest.TestCase):
    """"电池供电中"这半句: Web 面板页眉和托盘悬停必须说同一句话。

    policy_text 的第四个参数此前从没被非默认值驱动过: 它的真实调用只在
    gpu_monitor / gpu_tray 里, 测试一律隔着桩看。电池下真正生效的阈值是
    battery_threshold_percent, 用户想确认"现在按哪个数在判", 唯一看得见的
    地方就是这两个界面上的这一行字。
    """

    def _snap(self, **over):
        snap = {"util_by_pid": {100: 45.0}, "gpu_all_by_pid": {100: 45.0},
                "dgpu_util_by_pid": {}, "shared_by_pid": {},
                "dedicated_by_pid": {}, "igpu_luids": {IGPU_LUID},
                "dgpu_luids": {DGPU_LUID}, "kind_cache": {},
                "pid_names": {100: "heavy.exe"}, "pid_prefs": {},
                "mode": "smart", "threshold": 40.0,
                "power_saver_auto": True, "on_battery": True,
                "history": [], "dgpu_temp": None}
        snap.update(over)
        return snap

    def test_battery_appends_the_word_and_ac_power_does_not(self):
        self.assertTrue(gm.policy_text("smart", 40, True, True)
                        .endswith(", 电池供电中"))
        self.assertFalse(gm.policy_text("smart", 40, True, False)
                         .endswith(", 电池供电中"))

    def test_the_word_is_a_suffix_not_a_substitution(self):
        """后缀只是追加: 模式/阈值/自动切回那三段一个字都不许变。"""
        plain = gm.policy_text("smart", 40, True, False)
        self.assertEqual(gm.policy_text("smart", 40, True, True),
                         plain + ", 电池供电中")

    def test_the_default_is_not_battery(self):
        # 不给第四个参数的调用点不该凭空多出半句话
        self.assertEqual(gm.policy_text("smart", 40, True),
                         gm.policy_text("smart", 40, True, False))

    def test_the_panel_header_carries_the_word(self):
        self.assertIn("电池供电中", gm.panel_html(self._snap(), {}))

    def test_the_tray_tooltip_carries_the_same_word(self):
        import gpu_tray
        app = object.__new__(gpu_tray.TrayApp)
        app.lock = threading.Lock()
        app._refresh_title = lambda: None
        app._gpu_name = lambda _luid: "gpu"
        app.icon = type("I", (), {"icon": None})()
        app._on_sample(self._snap())
        self.assertIn("电池供电中", app._title_base)


class PowerAwareRealPowerStatus(LoopHarness):
    """拔电源/插电源的整条链: 内核 API -> 电源判定 -> 阈值 -> 迁移决定。

    PowerAwarePolicy 那几条是 `gm.on_battery = lambda: True/False`, 验的是
    "判定已经成立时会怎样"。这里把 stub 摘掉, 给 ctypes.WinDLL 放假内核, 让
    真实 on_battery() 在循环里上场 —— 从 GetSystemPowerStatus 的返回值一路
    走到"这个进程迁不迁"。中间任何一环断了(字段错位、30s 闸门不生效、取错
    阈值字段), 用户看到的就是"该迁的时候没迁", 而且只在电池上发生。
    """

    def setUp(self):
        super().setUp()
        # LoopHarness.setUp 把 on_battery 换成了 lambda: False, 换回真身
        gm.on_battery = self.saved["on_battery"]

    def _drive(self, line_status):
        patcher = mock.patch.object(gm.ctypes, "WinDLL",
                                    _fake_power_dll(line_status))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _step_clock(self, step=15.0):
        """每次 time.time() 往前跳 step 秒。

        电源块有 30s 闸门, 而一轮循环里 time.time() 要被调 5 次 —— 每次跳
        15s 等于每轮过去 75s, 闸门每轮都开。否则一次运行只问一次内核, 电源
        状态的翻转就永远看不到了。
        """
        clock = [1_000_000.0]

        def fake_time():
            clock[0] += step
            return clock[0]

        self.addCleanup(setattr, gm.time, "time", gm.time.time)
        gm.time.time = fake_time

    def _first(self, needle):
        for i, line in enumerate(self.log_lines):
            if needle in line:
                return i
        self.fail("日志里没有 %r: %r" % (needle, self.log_lines))

    def test_unplugged_migrates_under_the_battery_threshold(self):
        self._drive(0)
        self._step_clock()
        self.run_monitor([{100: 45.0}] * 2,
                         cfg_overrides={"power_aware": True,
                                        "threshold_percent": 50.0,
                                        "battery_threshold_percent": 40.0,
                                        "sustain_samples": 1})
        self.assertTrue(any("切换到电池" in l for l in self.log_lines),
                        self.log_lines)
        self.assertEqual(gm._LAST_SNAPSHOT["threshold"], 40.0,
                         "快照要报电池阈值, 不是 config 里的交流阈值")
        self.assertTrue(gm._LAST_SNAPSHOT["on_battery"])
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;",
                         "45% 越过电池阈值 40%, 这一轮该迁")

    def test_plugging_the_adapter_back_restores_the_ac_threshold(self):
        self._drive([0, 1])         # 先拔电, 之后插回
        self._step_clock()
        self.run_monitor([{100: 45.0}] * 3,
                         cfg_overrides={"power_aware": True,
                                        "threshold_percent": 50.0,
                                        "battery_threshold_percent": 40.0,
                                        "sustain_samples": 1})
        self.assertLess(self._first("切换到电池"),
                        self._first("已接通电源"),
                        "先拔后插, 日志顺序要跟真实时序一致")
        self.assertEqual(gm._LAST_SNAPSHOT["threshold"], 50.0,
                         "插回电源就该恢复 config 里的交流阈值")
        self.assertFalse(gm._LAST_SNAPSHOT["on_battery"])
        self.assertEqual(self.prefs.get(HEAVY), "GpuPreference=2;",
                         "电池那一轮已经按 40% 迁过, 插回电源不该再动它")


class HostileNamesInPanel(unittest.TestCase):
    """进程名/显卡名是外来文本: 尖括号与 & 必须被转义, 面板不许被塞标签。

    `_html_escape` 这个守卫函数此前**零覆盖** —— 全库一次都没直接调它, 只有
    `panel_html` 内部在用, 于是"它是否正确"只活在 docstring 里。而它守的
    恰恰是最不该出事的地方: 进程名来自系统进程表, 显卡名来自 D3DKMT 查询
    结果, 两边都不受我们控制。更要紧的是 Web 面板的局部刷新走的是
    `_PANEL_SCRIPT` 里那句 `a.innerHTML = b.innerHTML` —— 服务端这一处转义
    是整条链上唯一的屏障, 漏一个字符就是把面板变成注入点。

    这里用敌意名字驱动真实的 `panel_html`, 把"名字永远只是文字"钉死。
    """

    MB = 1024 * 1024
    NAMES = {
        100: "<script>alert(1)</script>",
        200: "a & b </table><img src=x onerror=alert(1)>",
        300: "</p><td class=n>伪造行",
        400: "＆全角＆",
    }

    def _snap(self):
        return {
            "util_by_pid": {100: 45.0, 200: 30.0, 300: 20.0, 400: 10.0},
            "gpu_all_by_pid": {100: 45.0, 200: 30.0, 300: 20.0, 400: 10.0},
            "dgpu_util_by_pid": {},
            "shared_by_pid": {100: 50 * self.MB, 300: 10 * self.MB},
            "dedicated_by_pid": {200: 10 * self.MB, 400: 5 * self.MB},
            "igpu_luids": {"L0"}, "dgpu_luids": {"L1"}, "kind_cache": {},
            "pid_names": self.NAMES, "pid_prefs": {}, "mode": "smart",
            "threshold": 40.0, "power_saver_auto": False, "on_battery": False,
            "history": [], "dgpu_temp": None, "snap_ts": 0,
        }

    def _html(self):
        return gm.panel_html(self._snap(),
                             {"L0": "Intel & <b>Core</b>", "L1": "独显<>"})

    def test_a_tag_in_a_process_name_never_becomes_a_tag(self):
        html = self._html()
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<img", html)
        # 名字本身还得看得见, 只是变成文本
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_an_ampersand_is_escaped_exactly_once(self):
        # 先转 & 再转尖括号才不会二次转义: "&amp;amp;" 就是顺序写反了
        html = self._html()
        self.assertIn("a &amp; b", html)
        self.assertIn("Intel &amp; &lt;b&gt;Core&lt;/b&gt;", html)
        self.assertNotIn("&amp;amp;", html)

    def test_a_name_that_closes_the_table_cannot_add_rows(self):
        # 名字里塞 </table> / </tr> / <td>: 结构标签一条都不许多
        html = self._html()
        self.assertEqual(html.count("<tr"), 6,
                         "1 表头 + 4 数据行 + 1 空态行, 名字不许再变出行来")
        self.assertEqual(html.count("</table>"), 1)
        self.assertEqual(html.count("<tbody"), 1)

    def test_the_fake_row_cannot_impersonate_a_real_one(self):
        # "</p><td class=n>伪造行" 若不被转义, 前端 querySelector('td.n')
        # 会多抓到一行, 筛选计数 "显示 N / M 行" 当场说谎
        html = self._html()
        self.assertEqual(html.count("<td class=n>"), len(self.NAMES),
                         "每个真进程名只贡献一个 td.n; 空态行用 colspan"
                         " 不带 class=n, 名字里的假行不许混进来")
        self.assertIn("&lt;/p&gt;&lt;td class=n&gt;伪造行", html)

    def test_full_width_characters_pass_through_untouched(self):
        # 全角 & 不是 ASCII &, 转义不该碰它 —— 中文进程名是常态
        self.assertIn("＆全角＆", self._html())

    def test_the_escape_helper_handles_the_three_characters(self):
        self.assertEqual(gm._html_escape("a<b>c&d"), "a&lt;b&gt;c&amp;d")
        # 已转义的输入会被再转一次: 这是文本节点的正确语义(显示字面量)
        self.assertEqual(gm._html_escape("&lt;"), "&amp;lt;")

    def test_non_string_input_is_coerced_not_raised(self):
        # panel_html 里有 `pid_names.get(pid) or f"pid {pid}"` 这类兜底,
        # 但 _html_escape 自己也得扛住非字符串, 不然一处脏数据就 500
        self.assertEqual(gm._html_escape(None), "None")
        self.assertEqual(gm._html_escape(7), "7")

    def test_only_the_three_characters_are_touched(self):
        """引号不在转义名单里 —— 所有调用点都必须是文本节点。

        `_html_escape` 只转 & < >。目前三个调用点(进程名、核显名、独显名)
        拼进去的都是 `<td>` / `<p>` 的**文本内容**, 不进属性, 所以引号不转
        是对的; 但这条得留个话在这儿: 谁将来把名字塞进
        `title="..."` / `value="..."` 这类属性, 就得先来这里加 " 和 '。
        """
        self.assertEqual(gm._html_escape('a"b\'c'), 'a"b\'c')


if __name__ == "__main__":
    unittest.main(verbosity=2)
