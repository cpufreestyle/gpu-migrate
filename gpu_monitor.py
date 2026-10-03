"""
gpu_monitor.py — 监控占用核显的进程，并把高占用的程序迁移到独显。

原理:
  1. 通过 Windows 性能计数器 (PDH, "GPU Engine") 读取按进程、按显卡的利用率。
     计数器实例形如: pid_1234_luid_0x00000000_0x00013502_phys_0_eng_0_engtype_3D
  2. 用实例里的 LUID 通过 D3DKMT 查询显卡名称，从而区分核显 (iGPU) 与独显 (dGPU)。
  3. 某进程在核显上的利用率持续超过阈值时，向注册表
     HKCU\\Software\\Microsoft\\DirectX\\UserGpuPreferences 写入
     "GpuPreference=2;"，等效于 设置->系统->屏幕->显示卡 里的“高性能”。
  4. 注意: Windows 不支持把正在运行的进程迁移到另一块 GPU，
     该设置需程序重启后生效。可配置 auto_restart 让工具自动重启进程。

用法:
  python gpu_monitor.py             # 按 config.json 持续监控
  python gpu_monitor.py --list      # 只列出显卡与当前占用，不做任何修改
  python gpu_monitor.py --once      # 采样一次退出
  python gpu_monitor.py --set 路径  # 手动把某个 exe 设为独显
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unicodedata
import time
import traceback
from collections import defaultdict, deque

# ================================================================ PDH 计数器

pdh = ctypes.WinDLL("pdh", use_last_error=True)
PDH_FMT_DOUBLE = 0x200
_WINDOW_MIN = 0.05          # 采样窗口下限(秒): 防呆, 不是推荐值
# 采样周期/窗口上限(秒)。config.json 是人也能改的文件, interval_seconds
# 敲成 3600/86400 这类值时老代码没有任何上界: 监控变成一小时一采、
# --list 会先打表头再干等一小时才退出, 用户只会以为程序卡死。
# 设 60s 上限: 正常都在 0.5~5s, 足够到"低功耗低频监控"的极限,
# 又不至于一个手误就让整套采集停摆。
_INTERVAL_MAX = 60.0
PDH_MORE_DATA = 0x800007D2
# 展开通配路径时缓冲区是"照 PDH 报回的字符数现分配"(size.value + 1024 余量)。
# 不给这个数设上界, 就等于让一个外部 API 的返回值决定本进程一次分配多少内存:
# 报回荒谬值时 create_unicode_buffer 直接 MemoryError, 而内存吃满那一刻监控
# 线程是硬死的, faulthandler 也未必留得下现场。本机实测一次展开 507 条实例
# 路径 / 54,943 字符, 1M 字符是它的 ~19 倍 —— 拦不到正常用量, 只拦荒谬值。
_EXPAND_CHARS_MAX = 1 << 20

INSTANCE_RE = re.compile(
    r"pid_(\d+)_luid_0x([0-9A-Fa-f]{8})_0x([0-9A-Fa-f]{8})"
    r"_phys_(\d+)(?:_eng_\d+(?:_engtype_\w+)?)?\)"
)


def expand_counter_paths(object_and_counter):
    """展开计数器通配路径（如 \\GPU Engine(*)\\Utilization Percentage）。

    返回的路径可能带机器名前缀，如 \\\\MACHINE\\GPU Engine(pid_...)\\...。
    GPU 实例是动态的，瞬时失败时重试。
    """
    path = "\\" + object_and_counter.lstrip("\\")
    last_err = None
    for _attempt in range(5):
        size = wt.DWORD(0)
        # 注意: 默认 restype 是有符号 int，返回值需先转为无符号再比较
        ret = pdh.PdhExpandWildCardPathW(None, path, None, ctypes.byref(size), 0)
        ret &= 0xFFFFFFFF
        if ret == 0:
            return []
        if ret != PDH_MORE_DATA or size.value == 0:
            last_err = f"0x{ret:08X}"
            time.sleep(0.5)
            continue
        if size.value > _EXPAND_CHARS_MAX:
            # 不重试: 荒谬的长度重试五次也还是荒谬, 只会晚一点把内存吃满。
            raise OSError(
                f"PdhExpandWildCardPath 报回需要 {size.value} 字符, "
                f"超过上限 {_EXPAND_CHARS_MAX}, 拒绝分配缓冲区")
        n = wt.DWORD(size.value + 1024)  # 实例数在两次调用间可能增长
        buf = ctypes.create_unicode_buffer(n.value)
        ret = pdh.PdhExpandWildCardPathW(None, path, buf, ctypes.byref(n), 0)
        if (ret & 0xFFFFFFFF) == 0:
            raw = "".join(buf)[: n.value]
            return [p for p in raw.split("\0") if p]
        last_err = f"0x{ret & 0xFFFFFFFF:08X}"
        time.sleep(0.5)
    raise OSError(f"PdhExpandWildCardPath failed: {last_err}")


def expand_gpu_counter_paths():
    return expand_counter_paths("GPU Engine(*)\\Utilization Percentage")


def _sample_window(interval):
    """利用率采样窗口 = 采样周期; 给个下限防呆。

    窗口必须和周期等长: PDH 是"窗口内引擎活跃时间 / 墙上时间"的比例,
    窗口比周期短就等于周期性漏采 —— interval=5s 时旧代码每 5 秒只看
    1 秒, 中间 4 秒的峰值整段不存在, "持续高占用"被系统性低估。
    """
    try:
        return min(_INTERVAL_MAX, max(_WINDOW_MIN, float(interval)))
    except (TypeError, ValueError):
        return 1.0


def collect_gpu_sample(allowed_luids=None, normalize=False, window=1.0):
    """采样 GPU Engine 利用率 + GPU Process Memory 显存。

    allowed_luids: 只注册这些显卡上的实例 (省 CPU); None = 全部。
    window: 利用率采样窗口(秒)。显存是瞬时值, 不受它影响。
    返回 (usage, shared, dedicated, gpu_index), 均以 (pid, luid, phys) 为键:
      usage:     利用率% (单引擎)
      shared:    共享 GPU 内存字节 (任务管理器"共享 GPU 内存"口径)
      dedicated: 专用 GPU 内存字节 (任务管理器"专用 GPU 内存"口径)
      gpu_index: {(luid, phys): None} 所有出现过的实例（含 0 利用率）
    """
    counters = []
    gpu_index = {}

    def add_paths(paths, kind):
        for p in paths:
            m = INSTANCE_RE.search(p)
            if not m:
                continue
            pid = int(m.group(1))
            luid = f"{m.group(2).upper()}_{m.group(3).upper()}"
            phys = int(m.group(4))
            gpu_index.setdefault((luid, phys), None)
            if allowed_luids is not None and luid not in allowed_luids:
                continue
            counters.append((p, (pid, luid, phys), kind))

    add_paths(expand_counter_paths("GPU Engine(*)\\Utilization Percentage"), "util")
    # 任务管理器进程页口径: 专用 GPU 内存 / 共享 GPU 内存 分列统计
    add_paths(expand_counter_paths("GPU Process Memory(*)\\Shared Usage"), "mems")
    add_paths(expand_counter_paths("GPU Process Memory(*)\\Dedicated Usage"), "memd")
    if not counters:
        return {}, {}, {}, {}

    q = wt.HANDLE()
    if pdh.PdhOpenQueryW(None, 0, ctypes.byref(q)) != 0:
        raise OSError("PdhOpenQuery failed")
    try:
        handles = []
        for p, key, kind in counters:
            h = wt.HANDLE()
            if pdh.PdhAddCounterW(q, p, 0, ctypes.byref(h)) == 0:
                handles.append((key, kind, h))
        if pdh.PdhCollectQueryData(q) != 0:
            raise OSError("PdhCollectQueryData #1 failed")
        raw1 = {}
        for key, kind, h in handles:
            r = _pdh_read_raw(h)
            if r:
                raw1[(key, kind)] = r
        # 窗口跟着采样周期走: 短于周期会漏采, 长于周期等于把两个周期
        # 的数据平均成一次读数, 都不如"一个周期一窗"贴事实。
        time.sleep(_sample_window(window))
        if pdh.PdhCollectQueryData(q) != 0:
            raise OSError("PdhCollectQueryData #2 failed")
        usage = defaultdict(float)
        shared_by_key = defaultdict(float)
        dedicated_by_key = defaultdict(float)
        for key, kind, h in handles:
            r2 = _pdh_read_raw(h)
            if not r2:
                continue
            if kind == "util":
                r1 = raw1.get((key, kind))
                if not r1:
                    continue
                # PERF_100NSEC_TIMER: FirstValue=引擎活跃100ns数,
                # SecondValue=挂钟100ns数。本机 PDH formatted rate 失效
                # (恒返回0, 疑与虚拟显示环境时间戳有关), 故手动计算。
                n = r2[0] - r1[0]
                d = r2[1] - r1[1]
                if d > 0 and n > 0:
                    v = 100.0 * n / d
                    # >100% 说明 GPU Engine 实例在窗口内被销毁重建、
                    # 句柄读到重排后的脏数据, 丢弃
                    # 下界取 0.01% 而非 0.5%: 完全空闲的引擎第一读与
                    # 第二读的 n 精确为 0 (本机实测一窗 633 个引擎全空),
                    # 而 0.2%/0.4% 这类真实活动任务管理器是显示出来的 ——
                    # 0.5% 的门槛把它们整块抹成 0%, 面板上"看着没人用"
                    # 其实有人在用。对迁移判定无影响: 阈值默认 20~40%。
                    if 0.01 < v <= 100.0 and v > usage[key]:
                        # 同一显卡上的 3D/Copy/VideoDecode 是并发引擎,
                        # 求和会虚高到 100% 以上; 任务管理器取最强引擎
                        usage[key] = v
            elif kind == "mems":   # Shared Usage: raw count 取当前值
                shared_by_key[key] += r2[0]
            else:                  # Dedicated Usage
                dedicated_by_key[key] += r2[0]
        # normalize=True 时按**适配器**分别把进程总和压到 100% (保持相对
        # 占比); 默认关闭 = 任务管理器口径, 单进程读数不失真。
        # 必须一块卡算一笔账: 核显和独显是两块物理上不同的卡, 把两者的
        # 进程搅进同一个"总和"里, 独显一忙就把核显的读数按比例压小。
        # 本机实测: 独显侧进程总和 166.5% + 核显侧 105.5% = 272.6%,
        # 于是一条 82.8% 的进程在面板上写成 30.35%(整表被乘 0.367),
        # 而迁移阈值拿的也是缩水后的数 —— 该迁的一个都没迁。
        if normalize:
            by_luid = defaultdict(float)
            for (_pid, luid, _phys), v in usage.items():
                by_luid[luid] += v
            scale = {luid: 100.0 / total
                     for luid, total in by_luid.items() if total > 100.0}
            if scale:
                # scale 里只有超线的那几块卡: 没超的按 1.0 原样放过
                usage = {(pid, luid, phys): v * scale.get(luid, 1.0)
                         for (pid, luid, phys), v in usage.items()}
        return (dict(usage), dict(shared_by_key),
                dict(dedicated_by_key), gpu_index)
    finally:
        pdh.PdhCloseQuery(q)


# ================================================================ D3DKMT 显卡信息


class _PDH_RAW_COUNTER(ctypes.Structure):
    _fields_ = [("CStatus", wt.DWORD),
                ("TimeStamp", wt.FILETIME),
                ("FirstValue", ctypes.c_longlong),
                ("SecondValue", ctypes.c_longlong),
                ("MultiCount", wt.DWORD)]


def _pdh_read_raw(handle):
    raw = _PDH_RAW_COUNTER()
    if pdh.PdhGetRawCounterValue(handle, None, ctypes.byref(raw)) != 0:
        return None
    return raw.FirstValue, raw.SecondValue

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]


class _OPENFROMLUID(ctypes.Structure):
    _fields_ = [("AdapterLuid", _LUID), ("hAdapter", ctypes.c_uint32)]


class _QAINFO(ctypes.Structure):
    _fields_ = [("hAdapter", ctypes.c_uint32), ("Type", ctypes.c_uint32),
                ("pData", ctypes.c_void_p), ("DataSize", ctypes.c_uint32)]


def luid_to_name(luid_str):
    """'00000000_00013502' -> 显卡名称。失败返回 None。"""
    hi, lo = luid_str.split("_")
    o = _OPENFROMLUID(_LUID(int(lo, 16), int(hi, 16) & 0x7FFFFFFF), 0)
    if gdi32.D3DKMTOpenAdapterFromLuid(ctypes.byref(o)) != 0:
        return None
    try:
        buf = ctypes.create_string_buffer(8192)
        q = _QAINFO(o.hAdapter, 65, ctypes.cast(buf, ctypes.c_void_p), 8192)
        if gdi32.D3DKMTQueryAdapterInfo(ctypes.byref(q)) == 0:
            return buf.raw.decode("utf-16-le", errors="replace").split("\0")[0]
        return None
    finally:
        h = ctypes.c_uint32(o.hAdapter)
        gdi32.D3DKMTCloseAdapter(ctypes.byref(h))


# ================================================================ 显卡分类

VIRTUAL_PAT = re.compile(
    r"basic render|basic display|standard vga|remote display|virtual|"
    r"indirect|display-only|warp", re.I)
DGPU_PAT = re.compile(
    r"nvidia|arc\b(?:\s*\(tm\))?(?:\s+pro)?\s+[ab]\d|"
    r"radeon rx|radeon pro|quadro|geforce|rtx|gtx", re.I)
IGPU_PAT = re.compile(
    r"intel|radeon\(tm\) graphics|amd radeon.*graphics$|uhd|iris|vega \d+|"
    r"780m|8060s|610m|660m|680m|740m|760m|880m|890m|graphics \d{3,}", re.I)


def classify_gpu(name, dedicated_mb=None):
    """返回 'igpu' / 'dgpu' / 'virtual' / 'unknown'。"""
    if not name or VIRTUAL_PAT.search(name):
        return "virtual"
    if DGPU_PAT.search(name) and not IGPU_PAT.search(name):
        return "dgpu"
    if IGPU_PAT.search(name) and not DGPU_PAT.search(name):
        return "igpu"
    if dedicated_mb is not None:
        return "dgpu" if dedicated_mb >= 1500 else "igpu"
    return "unknown"


_DEDICATED_MB = None


def _video_controller_ram():
    """Win32_VideoController 的 AdapterRAM, 以显卡名小写为键。进程内只查一次。

    每个显卡都单独起一个 powershell 冷启动要 ~0.5s, 三块卡会在启动时卡 1.4s,
    故一次性枚举缓存。查询失败也缓存空表: 失败不会自己恢复, 重试更糟。

    这个值只用于显卡分类兜底, 不是显存总量: AdapterRAM 是 32 位无符号,
    4GB 以上的卡本来就会回绕, 而集显上报的往往是划给它的共享内存 aperture,
    拿它当"显存"是错的。
    """
    global _DEDICATED_MB
    if _DEDICATED_MB is not None:
        return _DEDICATED_MB
    mb = {}
    try:
        ps = ("Get-CimInstance Win32_VideoController | "
              "ForEach-Object { $_.Name + '|' + $_.AdapterRAM }")
        # 失败只是退回"按名称猜", 不值得把监控线程按住更久: WMI 卡死时
        # 这段同步等待会连带冻结采样/历史/托盘图标。
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=15).stdout
        for line in out.splitlines():
            if "|" not in line:
                continue
            name, ram = line.rsplit("|", 1)
            try:
                ram = int(ram)
            except ValueError:
                continue
            if ram < 0:
                # AdapterRAM 在 WMI 里是 uint32, 有些驱动把 2^31 以上报成
                # 回绕后的负数。负 MB 会让 classify_gpu 的 ">=1500" 不成立
                # —— 独显被判成核显, 反过来把进程往集显上赶。只在负数时补回
                # 2^32: 正数原样留下, 真的是 8GB 的卡仍读作 8192。
                ram += 1 << 32
            mb[name.strip().lower()] = ram // (1024 * 1024)
    except Exception:
        pass
    _DEDICATED_MB = mb
    return mb


def query_dedicated_mb(name):
    """用 Win32_VideoController 辅助查询专用显存 (MB)，按名称匹配。"""
    return _video_controller_ram().get((name or "").strip().lower())


# ================================================================ 进程信息

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260
_SNAPSHOT_TTL = 2.0          # 进程名快照的缓存秒数


class _PROCESSENTRY32W(ctypes.Structure):
    """Toolhelp 快照里的进程条目; 字段顺序就是 ABI, 动一个都枚举不出东西。"""

    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wt.DWORD),
        ("th32Threads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", wt.LONG),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * MAX_PATH),
    ]


# 原型必须显式声明: 不写 argtypes 时 ctypes 按 c_int 传句柄, 实测
# Process32NextW 会以 ERROR_BAD_LENGTH(24) 中止, 只吐得出一个进程。
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = (wt.DWORD, wt.DWORD)
kernel32.Process32FirstW.restype = wt.BOOL
kernel32.Process32FirstW.argtypes = (wt.HANDLE, ctypes.c_void_p)
kernel32.Process32NextW.restype = wt.BOOL
kernel32.Process32NextW.argtypes = (wt.HANDLE, ctypes.c_void_p)

_name_snapshot = {"at": 0.0, "names": {}}


def _process_names_snapshot():
    """全系统 {pid: 映像名}, 带 2 秒 TTL。

    OpenProcess 对系统进程和受保护进程返回 ERROR_ACCESS_DENIED(5), 实测
    一次采样 39 个进程里有 10 个拿不到名字 (System/csrss/dwm/vmwp/杀软...),
    --list 里整行都是问号。快照只要枚举权限, 这些进程全都列得出来, 代价
    是拿不到完整路径。采样周期是秒级, 2 秒 TTL 足够把成本摊薄。
    """
    now = time.monotonic()
    cached = _name_snapshot
    if cached["names"] and now - cached["at"] < _SNAPSHOT_TTL:
        return cached["names"]
    handle = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    # 失败返回 INVALID_HANDLE_VALUE。restype 是 c_void_p, 按无符号回来是
    # 0xFFFFFFFFFFFFFFFF; 0 和 -1 一并当坏句柄, 免得喂给枚举器。
    if not handle or not 0 < handle <= 0xFFFFFFFF:
        return cached["names"]
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        names = {}
        ok = kernel32.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            names[entry.th32ProcessID] = entry.szExeFile
            ok = kernel32.Process32NextW(handle, ctypes.byref(entry))
        _name_snapshot["names"] = names
        _name_snapshot["at"] = now
        return names
    finally:
        kernel32.CloseHandle(handle)


def pid_to_name(pid):
    """返回 (进程名, 完整路径); 路径拿不到时返回 (进程名, None)。

    调用方必须容忍 full 为 None: 注册表首选项拿完整路径当键, 没有路径
    就既读不出首选项、也写不进去。两条腿走路: OpenProcess 那条拿得到
    完整路径; 被拒绝时退回 Toolhelp 快照, 只剩名字。两者都不成才 None。
    """
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf,
                                                   ctypes.byref(size)):
                return os.path.basename(buf.value), buf.value
        finally:
            kernel32.CloseHandle(h)
    name = _process_names_snapshot().get(pid)
    return (name, None) if name else None

# ================================================================ GPU 首选项

USER_GPU_PREF_KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
HKEY_CURRENT_USER = 0x80000001
KEY_SET_VALUE = 0x0002
KEY_QUERY_VALUE = 0x0001


def set_gpu_preference(exe_path, value="GpuPreference=2;"):
    """写入"高性能(GPU 2)"首选项，对下次启动生效。"""
    hkey = wt.HANDLE()
    if advapi32.RegCreateKeyExW(HKEY_CURRENT_USER, USER_GPU_PREF_KEY, 0, None,
                                0, KEY_SET_VALUE, None, ctypes.byref(hkey),
                                None) != 0:
        raise OSError("RegCreateKeyEx failed")
    try:
        data = ctypes.create_unicode_buffer(value)
        ret = advapi32.RegSetValueExW(hkey, exe_path, 0, 1,  # REG_SZ
                                      data, (len(value) + 1) * 2)
        if ret != 0:
            raise OSError(f"RegSetValueEx failed: 0x{ret:08X}")
    finally:
        advapi32.RegCloseKey(hkey)


def get_gpu_preference(exe_path):
    hkey = wt.HANDLE()
    if advapi32.RegOpenKeyExW(HKEY_CURRENT_USER, USER_GPU_PREF_KEY, 0,
                              KEY_QUERY_VALUE, ctypes.byref(hkey)) != 0:
        return None
    try:
        # 固定小缓冲会把长值判成"没设置过": 于是明明已迁移的进程下一轮又被
        # 重写一遍并再通知一次。ERROR_MORE_DATA 时扩容重试。
        for cap in (128, 4096):
            buf = ctypes.create_unicode_buffer(cap)
            cb = wt.DWORD(cap * 2)
            ret = advapi32.RegQueryValueExW(hkey, exe_path, None, None, buf,
                                            ctypes.byref(cb))
            if ret == 0:
                return buf.value
            if ret != 234:            # ERROR_MORE_DATA 之外: 值不存在/打不开
                return None
        return None
    finally:
        advapi32.RegCloseKey(hkey)


def is_high_performance(value):
    """注册表值是否等效"高性能(独显)"。

    整串相等会漏: 本机实测同时存在 `GpuPreference=2;` 和缺尾分号的
    `GpuPreference=2`, 后者被判成未迁移, 于是本该安静的进程被重写并弹通知。
    """
    if not value:
        return False
    return any(part == "GpuPreference=2" for part in value.split(";"))


def clear_gpu_preference(exe_path):
    """删掉一条 GPU 首选项。三态, 和 list_gpu_prefs 一个尺度:

        True    删掉了
        None    这一条本来就不存在(ERROR_FILE_NOT_FOUND)
        False   真失败: 键打不开(权限/策略)或 RegDeleteValueW 报错

    以前只回布尔, "没东西可删"和"删不掉"共用 False —— 托盘详情窗和
    cmd_unset 于是只能播一句"无设置或清除失败", 把权限被挡也说成
    "可能本来就没有"。分开之后调用方才能分辨该不该管。
    """
    hkey = wt.HANDLE()
    ret = advapi32.RegOpenKeyExW(HKEY_CURRENT_USER, USER_GPU_PREF_KEY, 0,
                                 KEY_SET_VALUE, ctypes.byref(hkey))
    if ret != 0:
        return None if ret == 2 else False   # 2=ERROR_FILE_NOT_FOUND
    try:
        ret = advapi32.RegDeleteValueW(hkey, exe_path)
        if ret == 0:
            return True
        # 错误码就在返回值里: 这类 API 回 LSTATUS, 不走 SetLastError ——
        # 本机实测 ret=2 时 ctypes.get_last_error() 仍是 0。照 last_error
        # 判会把"本来就没有"误报成"清除失败"。
        return None if ret == 2 else False
    finally:
        advapi32.RegCloseKey(hkey)


def _enum_value(advapi32, hkey, idx):
    """枚举一条注册表值, 缓冲不够(ERROR_MORE_DATA)就扩容重试。

    原来两块缓冲都是固定大小, 超长 exe 路径或长首选项串会让 RegEnumValueW
    直接失败 —— 代码把任何非 0 返回都当"枚举结束", 于是 --status /
    --unset-all 静默少报少删那一条。
    """
    name_cap, data_cap = 512, 256
    for _ in range(6):
        name = ctypes.create_unicode_buffer(name_cap)
        cb = wt.DWORD(name_cap)              # 名字缓冲按字符计
        data = ctypes.create_unicode_buffer(data_cap)
        dcb = wt.DWORD(data_cap * 2)         # 数据缓冲按字节计
        ret = advapi32.RegEnumValueW(hkey, idx, name, ctypes.byref(cb),
                                     None, None, data, ctypes.byref(dcb))
        if ret != 234:                # ERROR_MORE_DATA 之外: 成功或别的错误
            return ret, name, data
        name_cap *= 4
        data_cap *= 4
    return ret, name, data


def list_gpu_prefs():
    """枚举 UserGpuPreferences 下全部 (exe, value)。

    打不开键时返回 None, 和"键是空的"([] )不是一回事: 键不存在说明从没
    设过, 权限被策略挡掉则是一条也没读到。老代码两种都回 [], 调用方只能
    把它当"没有设置"播报出去 —— 用户会以为自己从没迁移过。
    ERROR_FILE_NOT_FOUND(2) 才算键不存在。
    """
    hkey = wt.HANDLE()
    ret = advapi32.RegOpenKeyExW(HKEY_CURRENT_USER, USER_GPU_PREF_KEY, 0,
                                 KEY_QUERY_VALUE, ctypes.byref(hkey))
    if ret != 0:
        return None if ret != 2 else []
    result = []
    try:
        idx = 0
        while True:
            ret, name, data = _enum_value(advapi32, hkey, idx)
            if ret == 0:
                result.append((name.value, data.value))
                idx += 1
                continue
            break               # 259=枚举完, 别的错误也只能到此为止
        return result
    finally:
        advapi32.RegCloseKey(hkey)


def clear_all_gpu_prefs(only_dgpu=True):
    """清除 GPU 首选项。only_dgpu=True 只删高性能(2)的。"""
    cleared = []
    for exe, val in list_gpu_prefs() or []:
        if only_dgpu and not is_high_performance(val):
            continue
        if clear_gpu_preference(exe):
            cleared.append(exe)
    return cleared


MB_YESNO = 0x04
MB_ICONQUESTION = 0x20
MB_TOPMOST = 0x40000
MB_SETFOREGROUND = 0x10000
MB_OK = 0x00
MB_ICONINFORMATION = 0x40
MB_ICONERROR = 0x10
IDYES = 6
user32 = ctypes.WinDLL("user32", use_last_error=True)


_MB_TIMEOUT = None      # MessageBoxTimeoutW 的原型; 找不到就一直是 None
_MB_LOOKED = False      # 找过一次就不再把时间花在 GetProcAddress 上
MB_TIMEOUT_SEC = 120    # 没人应答就当"本轮不迁移", 不能无限期冻结监控循环


def _find_messagebox_timeout():
    """取 MessageBoxTimeoutW; user32 没导出它, 只能按地址拿。

    取不到就返回 None, 调用方退回普通 MessageBoxW (那就是原来没有超时的老路)。
    """
    global _MB_TIMEOUT, _MB_LOOKED
    if _MB_LOOKED:
        return _MB_TIMEOUT
    _MB_LOOKED = True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    addr = k32.GetProcAddress(user32._handle, b"MessageBoxTimeoutW")
    if addr:
        proto = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HWND, wt.LPCWSTR,
                                   wt.LPCWSTR, wt.UINT, wt.WORD, wt.DWORD)
        _MB_TIMEOUT = proto(addr)
    return _MB_TIMEOUT


def ask_migrate(pname, reason, logfile=None):
    """确认模式弹窗。返回 True 表示迁移，False 表示忽略。

    模态框会把监控线程整轮按住: 没人点它, 采样/历史/托盘图标全停, 界面停
    在上一次的占用数字上。所以要有超时, 超时按"忽略"处理并记一行日志。
    """
    if logfile is None:
        logfile = _LOGFILE
    text = (f"检测到 {pname} {reason}\n\n"
            f"是否把它迁移到独显？（重启该程序后生效）")
    flags = MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND
    box = _find_messagebox_timeout()
    if box is not None:
        ret = box(None, text, "GPU 迁移确认", flags, 0,
                  MB_TIMEOUT_SEC * 1000)
        if ret == 0:                 # 超时的返回 0, 不是 IDNO
            log(f"确认弹窗 {MB_TIMEOUT_SEC}s 无应答，本轮不迁移 {pname}",
                logfile)
            return False
        return ret == IDYES
    return user32.MessageBoxW(None, text, "GPU 迁移确认", flags) == IDYES


def notify_already_running():
    """单例互斥体撞上时给用户一个交代: 已经在跑, 去哪找它。

    托盘模式(打包版双击)撞上互斥体的老行为是静默 return: --noconsole
    的 pythonw 里连"已有实例在运行"都印不到任何地方, 用户只看到双击后
    什么都没发生, 只会怀疑程序坏了。而 --ensure 是看门狗每 2 分钟的例行
    调用, 绝不能弹窗, 所以这个提示只走 --tray 这一条路。
    """
    text = ("GPU 监控已在运行。\n\n"
            "请查看屏幕右下角托盘区里名为 \"GPU 监控\" 的图标; "
            "双击它可以打开实时面板。")
    try:
        user32.MessageBoxW(None, text, "GPU 迁移工具",
                           MB_OK | MB_ICONINFORMATION | MB_TOPMOST
                           | MB_SETFOREGROUND)
    except OSError:
        pass          # 提示是锦上添花, 弹不出去也不能把退出变成崩溃


def report_tray_failure(exc):
    """托盘启动失败时给用户一个交代, 尤其是 pythonw 下。

    开机自启的 .lnk 指源码跑 --tray: pystray 没装、Python 升级换了 ABI,
    import gpu_tray 或 run_tray 都会炸。pythonw 根本没有控制台, 异常直达
    顶层就是一次"双击没反应" - 用户分不清是依赖没了还是程序坏了。
    所以这里: 控制台下打印完整栈(方便排查), 无窗口环境弹窗说明原因。
    """
    traceback.print_exc()
    try:
        user32.MessageBoxW(None,
                           f"GPU 监控托盘启动失败:\n\n{exc}\n\n"
                           "源码模式请先安装依赖: pip install pystray pillow",
                           "GPU 迁移工具",
                           MB_OK | MB_ICONERROR | MB_TOPMOST
                           | MB_SETFOREGROUND)
    except OSError:
        pass          # 提示是锦上添花, 弹不出去也不能让崩溃升级成二次崩溃


def read_json_dict(path):
    """读一个 JSON 对象文件。文件不存在返回 {}; 内容不是 JSON 对象或解析失败返回 None。

    把"没有"和"读坏了"分成两种返回值: 写入方遇到 None 必须放弃覆盖,
    否则半截文件会被当成空配置, 用户剩下的设置就整份丢了。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_json_dict(path, data):
    """原子写 JSON: 先落同目录的独占临时文件, 再 os.replace 换上去。

    直接 open(path, "w") 会在 json.dump 之前就把原文件清空, 磁盘满或进程
    被杀时留下的就是半截 JSON —— 那正是 config.json 损坏的成因。
    临时名必须独占: 托盘线程和监控线程会并发写同一份配置, 共用一个名字会
    互相截断临时文件, 原子性就又没了。
    """
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                               prefix=os.path.basename(path) + ".",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _str_list(raw):
    """值 -> 字符串列表; 不是字符串/序列就返回 None。

    单个字符串必须当成一项: 不包这一层, exclude_processes 写成
    "chrome.exe" 会被读它的人按 `{x.lower() for x in ...}` 逐字符展开成
    一串字母, 名单静默失效。读配置 (_coerce_list) 和写名单
    (_add_to_config_list) 共用同一个归一, 才不会一边认、一边炸。

    每项还要 strip: 手改 config.json 的名单项常带尾随空格或 tab,
    而进程名比对是集合精确匹配, " chrome.exe " 匹配不到 chrome.exe,
    整条名单静默失效。strip 后为空的项丢掉: 它谁也匹配不上。
    """
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        return None
    return [x.strip() for x in items if x.strip()]


def _add_to_config_list(config_path, key, value):
    """往 config.json 的字符串列表里加一项，返回是否落盘成功。"""
    cfg = read_json_dict(config_path)
    if cfg is None:
        log(f"配置无法解析，已放弃写入 {key}: {value}")
        return False
    items = _str_list(cfg.get(key, []))
    if items is None:
        # 原先是直接 set(cfg.get(key, [])): 值写成单个字符串时会被逐字符
        # 炸成一堆单字母, 写成 42 / null 时 set() 直接 TypeError 把右键菜单
        # 带走 —— 两种都把用户原来那一项连着抹掉。先按读配置的口径归一,
        # 认不出来的那份就当没有, 重写成只含新值的正列表。
        log(f"配置 {key}={cfg.get(key)!r} 不是列表，已重写为列表")
        items = []
    cfg[key] = sorted(set(items) | {value})
    return write_json_dict(config_path, cfg)


def save_exclude_process(config_path, pname):
    """把面板右键"永不迁移"的进程持久化到 config.json 的 exclude_processes。"""
    return _add_to_config_list(config_path, "exclude_processes", pname)


def save_ignore_process(config_path, pname):
    """把确认模式下选择忽略的进程名持久化到 config.json。"""
    return _add_to_config_list(config_path, "ignore_processes", pname)


# ================================================================ 配置与主逻辑

# 这些常驻组件的高核显占用属于正常行为 (桌面合成/图标/输入法等),
# 迁移它们有害无益; exclude_defaults=false 可关闭
DEFAULT_EXCLUDE_PROCESSES = [
    "dwm.exe", "explorer.exe", "taskmgr.exe", "searchhost.exe",
    "shellexperiencehost.exe", "startmenuexperiencehost.exe",
    "textinputhost.exe", "runtimebroker.exe", "lockapp.exe",
    "applicationframehost.exe", "systemsettings.exe", "onedrive.exe",
    "msedgewebview2.exe", "sihost.exe", "ctfmon.exe", "conhost.exe",
]

DEFAULT_CONFIG = {
    "exclude_defaults": True,    # 启用上面的内置排除名单
    "threshold_percent": 50.0,   # 核显利用率超过该值视为高占用
    "vram_threshold_mb": 1024,   # 核显显存(专用+共享)超该 MB 也触发（0 关闭）
    "sustain_samples": 5,        # 连续多少个采样周期超标才迁移
    "interval_seconds": 5,       # 采样周期
    "auto_restart": False,       # 自动结束并重启超标进程（使设置立即生效）
    "auto_restart_processes": [],  # 仅对这些进程自动重启；留空 = 对所有超标进程
    "ai_processes": [],          # AI 名单补充: 永不省电切回/自动重启
    "protect_vram_mb": 2048,     # 独显专用显存>=该值按 AI 保护 (0 关闭)
    "confirm_mode": False,       # 迁移前弹窗确认（确认/忽略都会被记住）
    "ignore_processes": [],      # 确认模式下选择“忽略”的进程名，不再询问
    "power_saver_notify": False, # 独显上低负载程序提醒（可切回核显省电）
    "power_saver_idle_percent": 10.0,  # 独显利用率低于该值视为低负载
    "power_saver_samples": 60,   # 低负载持续多少个采样周期才提醒（60*5s=5分钟）
    "exclude_processes": [],     # 按进程名排除，如 ["chrome.exe"]
    "exclude_full_paths": [],    # 按完整路径前缀排除
    "force_igpu_names": [],      # 手动指定哪些显卡名算核显（覆盖自动判定）
    "notify": True,              # 迁移成功/失败时弹 Windows 通知
    "notify_fullscreen_ignore": True,  # 前台全屏(游戏/视频)时不弹通知
    "power_saver_auto": False,   # 低负载自动把独显设置改回核显 (省电)
    "history": True,             # 每 10 秒记录核显总占用到 history.jsonl
    "normalize_total": False,    # true=所有进程总和强制<=100% (单进程读数会缩水);
                                 # false=任务管理器口径, 单进程读数最准确
    "game_processes": [],        # 游戏名单: 自动确保独显+免打扰+时长统计
    "power_aware": True,         # 电源感知: 用电池时自动切换省电策略
    "battery_threshold_percent": 40,   # 电池下的迁移阈值
    "nvml_temp": True,           # 独显温度监控 (NVML, NVIDIA 驱动自带)
    "temp_limit": 85,            # 独显温度提醒阈值 (摄氏度)
    "web_port": 8787,            # 本地 Web 面板端口, 0=关闭 (http://127.0.0.1:端口)
    "hotkeys": {"panel": "ctrl+alt+g", "pause": "ctrl+alt+p"},  # 全局热键
    "log_to_file": True,
}


# 手改 config.json 时最容易写错的就是这几类。类型不对以前不会当场报错,
# 而是跑到 hot_reason 里拿 "40" 和 float 比, TypeError 直接把监控线程带走;
# sustain_samples 写成 0 则第一次超标就迁移。统一在入口收敛。
_NUMBER_RULES = {   # key: (下限, 上限, 取整)
    "threshold_percent": (0.0, 100.0, False),
    "battery_threshold_percent": (0.0, 100.0, False),
    "power_saver_idle_percent": (0.0, 100.0, False),
    "vram_threshold_mb": (0, None, True),
    "sustain_samples": (1, None, True),
    "interval_seconds": (0.5, _INTERVAL_MAX, False),
    "power_saver_samples": (1, None, True),
    "temp_limit": (1, None, True),
    "web_port": (0, 65535, True),
}
_BOOL_KEYS = ("exclude_defaults", "auto_restart", "confirm_mode",
              "power_saver_notify", "power_saver_auto", "notify",
              "notify_fullscreen_ignore", "history", "normalize_total",
              "power_aware", "nvml_temp", "log_to_file")
_LIST_KEYS = ("auto_restart_processes", "ignore_processes",
              "exclude_processes", "exclude_full_paths", "force_igpu_names",
              "game_processes")


def _coerce_number(cfg, key, lo, hi, integer):
    """数字字段: 非数字退回默认值, 越界夹到边界。"""
    default = DEFAULT_CONFIG[key]
    try:
        value = float(cfg.get(key, default))
    except (TypeError, ValueError):
        value = None
    if value is None or value != value or value in (float("inf"),
                                                    float("-inf")):
        log(f"配置 {key}={cfg.get(key)!r} 不是数字，已用默认值 {default}")
        cfg[key] = default
        return
    if lo is not None and value < lo:
        value = lo
    if hi is not None and value > hi:
        value = hi
    cfg[key] = int(round(value)) if integer else value


def _coerce_bool(cfg, key):
    """开关字段: 只认真布尔与常见字符串写法, 其余退回默认值。"""
    default = DEFAULT_CONFIG[key]
    raw = cfg.get(key, default)
    if isinstance(raw, bool):
        return
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in ("1", "true", "yes", "on"):
            cfg[key] = True
            return
        if low in ("0", "false", "no", "off"):
            cfg[key] = False
            return
    elif isinstance(raw, (int, float)) and raw in (0, 1):
        cfg[key] = bool(raw)
        return
    log(f"配置 {key}={raw!r} 不是开关，已用默认值 {default}")
    cfg[key] = default


def _coerce_list(cfg, key):
    """列表字段: 单个字符串当成一项, 别的类型退回默认值。"""
    default = DEFAULT_CONFIG[key]
    raw = cfg.get(key, default)
    items = _str_list(raw)
    if items is None:
        log(f"配置 {key}={raw!r} 不是列表，已用默认值 {default}")
        items = list(default)
    cfg[key] = items


def _coerce_hotkeys(cfg):
    """hotkeys 只留能解析的键; 整体类型不对就退回默认值。"""
    default = dict(DEFAULT_CONFIG["hotkeys"])
    raw = cfg.get("hotkeys", default)
    if not isinstance(raw, dict):
        log(f"配置 hotkeys={raw!r} 不是对象，已用默认值 {default}")
        cfg["hotkeys"] = default
        return
    kept = {}
    for name, spec in raw.items():
        if not spec:
            continue                      # 空串 = 用户主动关掉这个热键
        if isinstance(spec, str) and _parse_hotkey(spec):
            kept[name] = spec
        else:
            log(f"配置 hotkeys.{name}={spec!r} 无法解析，已忽略")
    cfg["hotkeys"] = kept


def load_config(path):
    """读配置并补齐默认值。配置文件损坏时回落到默认值而不是让进程崩掉。"""
    data = read_json_dict(path)
    if data is None:
        log(f"配置无法解析，本次使用默认配置: {path}")
        data = {}
    return merged_config(data)


def merged_config(data):
    """把一份已从文件读出的 dict 补齐默认值, 并收敛类型与区间。

    冷启动与热重载都走这里: 收敛只留一处, 监控循环里拿到手的配置才敢直接用。
    """
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(data)
    for key, (lo, hi, integer) in _NUMBER_RULES.items():
        _coerce_number(cfg, key, lo, hi, integer)
    for key in _BOOL_KEYS:
        _coerce_bool(cfg, key)
    for key in _LIST_KEYS:
        _coerce_list(cfg, key)
    _coerce_hotkeys(cfg)
    return cfg


# 设置窗各数字项的显示名。写回配置失败时要点名是哪一格没吃进输入,
# 只说键名(threshold_percent)对不上用户眼前的那行中文。
SETTINGS_LABELS = {
    "threshold_percent": "迁移阈值 %",
    "battery_threshold_percent": "电池阈值 %",
    "vram_threshold_mb": "显存阈值 MB",
    "interval_seconds": "采样周期 秒",
    "temp_limit": "温度提醒 °C",
    "web_port": "Web 端口",
}

LOG_MAX_BYTES = 5 * 1024 * 1024  # 超过后滚动为 .log.1


def log(msg, logfile=None):
    line = time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg
    try:
        print(line, flush=True)
    except (OSError, ValueError, AttributeError):
        pass  # --noconsole / 重定向句柄失效时静默
    if logfile:
        try:
            if os.path.exists(logfile) \
                    and os.path.getsize(logfile) > LOG_MAX_BYTES:
                os.replace(logfile, logfile + ".1")
            with open(logfile, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


_DIR = (os.path.dirname(os.path.abspath(sys.executable))
        if getattr(sys, "frozen", False)  # PyInstaller: exe 所在目录
        else os.path.dirname(os.path.abspath(__file__)))

TOAST_PS1 = os.path.join(_DIR, "show_toast.ps1")


_user32 = ctypes.WinDLL("user32")


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", wt.DWORD)]


def is_foreground_fullscreen():
    """前台窗口是否覆盖了整个显示器 (全屏游戏/视频)。"""
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return False
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, buf, 256)
        if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return False
        rect = _RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if not _user32.GetMonitorInfoW(
                _user32.MonitorFromWindow(hwnd, 2), ctypes.byref(mi)):
            return False
        m = mi.rcMonitor
        return (rect.left <= m.left and rect.top <= m.top
                and rect.right >= m.right and rect.bottom >= m.bottom)
    except Exception:
        return False


def notify_cfg(cfg, title, msg):
    """带开关、游戏模式静音与全屏免打扰判断的通知。"""
    if not cfg.get("notify") or _GAME_RUNNING:
        return
    if cfg.get("notify_fullscreen_ignore", True) and is_foreground_fullscreen():
        return
    notify(title, msg)


_LOGFILE = None       # cmd_monitor 启动时写入, 供 notify 记录自身的失败


def notify(title, msg, logfile=None):
    """发 Windows Toast 通知（异步、失败不阻塞监控）。

    show_toast.ps1 丢了或 powershell 起不来时, notify:true 会静默失效 —
    — 迁移明明成功了却一个提示都没有。这两条路必须留一行日志。
    """
    if logfile is None:
        logfile = _LOGFILE
    if not os.path.isfile(TOAST_PS1):
        log(f"通知失败: 找不到 {TOAST_PS1}", logfile)
        return
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", TOAST_PS1, title, msg],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        log(f"通知失败: {e}", logfile)


_nvml_cache = None
_GAME_RUNNING = False          # 游戏运行中时静音所有通知
# NVML 单次读失败时, 上一读数的暂留宽限: 面板/托盘的温度行不至于因为一次
# 抖动就闪断; 超时就收回, 免得长期显示一个不再更新的数字。
_TEMP_STALE_S = 60


def nvml_load():
    """加载 NVML (NVIDIA 驱动自带), 失败返回 False。"""
    global _nvml_cache
    if _nvml_cache is not None:
        return _nvml_cache
    lib = False
    for cand in ("nvml.dll", r"C:\Windows\System32\nvml.dll",
                 r"C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll"):
        try:
            l = ctypes.WinDLL(cand)
            if l.nvmlInit() == 0:
                lib = l
                break
        except OSError:
            continue
    _nvml_cache = lib
    return lib


def nvml_gpu_temp(index=0):
    """独显温度摄氏度, 失败返回 None。"""
    lib = nvml_load()
    if not lib:
        return None
    try:
        h = ctypes.c_void_p()
        if lib.nvmlDeviceGetHandleByIndex_v2(index, ctypes.byref(h)) != 0:
            return None
        t = ctypes.c_uint()
        if lib.nvmlDeviceGetTemperature(h, 0, ctypes.byref(t)) != 0:  # 0=GPU
            return None
        return t.value
    except Exception:
        return None


class _POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte),
                ("Reserved1", ctypes.c_byte),
                ("BatteryLifeTime", wt.DWORD),
                ("BatteryFullLifeTime", wt.DWORD)]


def on_battery():
    try:
        st = _POWER_STATUS()
        if ctypes.WinDLL("kernel32").GetSystemPowerStatus(ctypes.byref(st)):
            return st.ACLineStatus == 0
    except Exception:
        pass
    return False


# ---- 全局热键 (隐藏窗口 + RegisterHotKey, 独立线程) ----
_MOD = {"ctrl": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}


def _parse_hotkey(spec):
    parts = [x.strip().lower() for x in spec.split("+") if x.strip()]
    mod, vk = 0, 0
    for x in parts:
        if x in _MOD:
            mod |= _MOD[x]
        elif len(x) == 1:
            vk = ord(x.upper())
        else:
            return None
    return (mod, vk) if mod and vk else None


def hotkey_bindings(cfg, hooks):
    """从配置和 hooks 算出要注册的热键表 {id: (spec, callback)}。

    配置里广告了 hotkeys, 但只有 hooks 同时给出 hotkey_panel / hotkey_pause
    时才有东西可注册 —— 以前这里写在循环里, 看不出来"哪个调用点会把热键接上"。
    """
    hk = cfg.get("hotkeys") or {}
    bindings = {}
    if hk.get("panel") and hooks and hooks.get("hotkey_panel"):
        bindings[1] = (hk["panel"], hooks["hotkey_panel"])
    if hk.get("pause") and hooks and hooks.get("hotkey_pause"):
        bindings[2] = (hk["pause"], hooks["hotkey_pause"])
    return bindings


def hotkey_thread(bindings):
    """bindings: {id: (spec, callback)}; 注册失败静默跳过。"""
    user32 = ctypes.WinDLL("user32")
    for hid, (spec, _cb) in bindings.items():
        parsed = _parse_hotkey(spec)
        if parsed and not user32.RegisterHotKey(None, hid, parsed[0], parsed[1]):
            bindings[hid] = (spec, None)   # 注册失败不触发
    msg = wt.MSG()
    while ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x0312:          # WM_HOTKEY
            item = bindings.get(msg.wParam)
            if item and item[1]:
                try:
                    item[1]()
                except Exception:
                    pass


# ---- 展示: 模式名 / 生效策略 / Web 面板 ----
_MODE_NAMES = {"performance": "性能", "smart": "智能", "saver": "省电"}


def mode_text(mode):
    """模式 key -> 中文名: 菜单、面板、日志共用一份口径, 免得三处各写一遍。"""
    return _MODE_NAMES.get(mode or "smart", "智能")


def policy_text(mode, threshold, ps_auto, on_battery=False):
    """生效策略的一行文字。数值只该来自监控循环里真正在用的那一份。"""
    text = (f"模式 {mode_text(mode)}, 阈值 {threshold:g}%, "
            f"自动切回 {'开' if ps_auto else '关'}")
    return text + (", 电池供电中" if on_battery else "")


def heat_class(pct):
    """占用热度的 CSS 类。阈值与托盘图标一致 (30 / 60), 别只改一处。"""
    if pct >= 60:
        return "hot"
    if pct >= 30:
        return "warm"
    return "cool"


def panel_summary(snap):
    """面板顶部那几个汇总数, 返回 [(标签, 文本, 热度类)]。

    口径只有一份, 托盘面板和 Web 面板都从这里取: 占用取"最强引擎"
    (任务管理器口径), 显存取跨适配器求和。界面自己再算一遍就会出现
    同一台机器三个读数的情况。
    """
    util = snap.get("util_by_pid") or {}
    dgpu = snap.get("dgpu_util_by_pid") or {}
    ig_pct = max(util.values()) if util else 0.0
    dg_pct = max(dgpu.values()) if dgpu else 0.0
    ded_mb = sum((snap.get("dedicated_by_pid") or {}).values()) / 1048576
    sh_mb = sum((snap.get("shared_by_pid") or {}).values()) / 1048576
    pids = set()
    for key in ("util_by_pid", "gpu_all_by_pid", "dgpu_util_by_pid",
                "shared_by_pid", "dedicated_by_pid"):
        pids |= set(snap.get(key) or {})
    return [
        ("核显占用", f"{ig_pct:.1f}%", heat_class(ig_pct)),
        ("独显占用", f"{dg_pct:.1f}%", heat_class(dg_pct)),
        ("专用显存", f"{ded_mb:.0f} MB", ""),
        ("共享显存", f"{sh_mb:.0f} MB", ""),
        ("GPU 进程", str(len(pids)), ""),
        ("生效阈值", f"{snap.get('threshold') or 0:g}%", ""),
    ]


def _html_escape(text):
    """拼进 HTML 的文本, 尖括号与 & 必须转义。

    进程名和显卡名都算外来文本: 进程名来自系统进程表, 显卡名来自
    D3DKMT 的查询结果, 两边都不受我们控制。谁要是带个 "<" 或者
    "&", 不转义就是往面板里塞标签 —— 轻则一行显示错乱, 重则把
    后面的 </p> 提前关掉, 整个 gpuinfo 段都看不到了。
    """
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def spark_ymax(pts, threshold=0):
    """趋势图纵轴上限。托盘和 Web 两张图必须用同一个算法。

    至少 30% 起步(不然 5% 的波动画成心脏病), 峰值和阈值线也都得
    放得进画布 —— 阈值线跑到框外的话, "什么时候会被判成高占用"
   就又看不出来了。
    """
    ymax = max(30.0, max(v for _t, v in pts))
    if threshold:
        ymax = max(ymax, float(threshold) * 1.15)
    return ymax


_WEB_STALE_MIN_S = 25.0   # Web 面板停更判定下限(秒): 短周期时的灵敏度
_SPARK_WINDOW_S = 1800     # 趋势图窗口: 最新一点往前 30 分钟
_SPARK_MAX_POINTS = 180    # 内存里最多留多少个点

def web_stale_after(interval_seconds):
    """Web 面板判"数据已停更"的宽限秒数, 跟着采样周期走。

    原来写死 25 秒, 那只在 interval=5s(文档默认)附近成立。周期调到
    30s/60s 之后, 健康的监控两轮间隔本身就超过 25 秒, 页面会常年挂着
    "数据已停更 (监控进程可能已停止)" —— 把一台好机器说成坏机器。
    取"两个周期 + 5 秒"与 25 秒的高者: 短周期保持原来的灵敏度,
    长周期按比例放宽, 采样真卡住时(卡超过两个周期)照样报得出来。
    """
    try:
        interval = float(interval_seconds)
    except (TypeError, ValueError):
        interval = 5.0
    return max(_WEB_STALE_MIN_S, interval * 2 + 5.0)


def spark_points(hist, window_s=_SPARK_WINDOW_S, limit=_SPARK_MAX_POINTS):
    """趋势图实际要画的点: 时间窗口内 + 最近 limit 个, 按时间升序。

    老代码取"最后 180 点"就动手, 一张图里混着窗口内外的点: 休眠唤醒
    或暂停监控之后, 首末两点能差出几小时, 而 x 按序号等距铺开 ——
    几小时的空白于是被画成一段连续曲线, 角标却写着"最近 30 分钟",
    图和标题互相打脸。按时间戳裁窗口, 窗口外的点一个都不画。
    """
    pts = sorted(hist or [], key=lambda p: p[0])[-limit:]
    if len(pts) < 2:
        return pts
    end = pts[-1][0]
    return [p for p in pts if end - p[0] <= window_s]


def spark_y_of(value, height, ymax):
    """占用值 -> y 像素。托盘和 Web 必须用同一条公式, 不然两条线对不上。"""
    return height - 4 - (height - 10) * value / ymax


def spark_geometry(pts, width, height, ymax):
    """折线坐标与填充多边形的左右端 x; x 按时间戳, y 按占用。

    x 按时间戳而不是序号: 序号等距会把采样中断前后的两点画成相邻,
    间隔的真实长度就丢了。首末两点同一时间戳时退回等距(免得除零),
    正常采样(每 10s 一点)与等距几乎看不出差别。
    """
    if len(pts) < 2:
        return [], 0.0, 0.0
    span = pts[-1][0] - pts[0][0]
    coords = []
    for i, (t, v) in enumerate(pts):
        if span > 0:
            x = (t - pts[0][0]) / span * width
        else:
            x = i * width / (len(pts) - 1)
        coords += [round(x, 2), round(spark_y_of(v, height, ymax), 2)]
    return coords, coords[-2], coords[0]


def spark_corner_text(pts):
    """趋势图角标: 时长 + 峰值 + 当前值。

    时长按首末两点的时间戳差 + 一个采样周期算, 不按"点数 x 10 秒"推:
    采样卡顿(单轮超过 10 秒)或系统休眠唤醒后点距会大于 10 秒, 按点数推
    会把两个小时间隔说成"近30分钟" —— 图还是那张图, 标题已经在撒谎。
    不足一分钟按秒说, 不满一小时按分钟说, 再往上按小时说。刚启动那几十秒
    只有几个点, 写"近0分钟"等于告诉用户"没数据", 而图上明明有折线。
    """
    span = int(pts[-1][0] - pts[0][0]) + 10
    if span < 60:
        when = f"近{span}秒"
    elif span < 3600:
        when = f"近{span // 60}分钟"
    else:
        when = f"近{span / 3600:.1f}小时"
    peak = max(v for _t, v in pts)
    return f"{when} 峰值{peak:.1f}%  当前{pts[-1][1]:.1f}%"


IDLE_ROW_TEXT = "（当前无进程占用 GPU）"
NO_MATCH_ROW_TEXT = "没有匹配的进程"

# 一份快照都还没采到时(启动头一秒、暂停发生在首轮之前): 说
# "当前无进程占用 GPU" 等于把"没测"说成"测了是 0"。
STARTING_ROW_TEXT = "（正在采样第一份数据…）"
# 监控线程已经死了: 表格停在最后一份快照上, 那更不是"没人用 GPU"。
STOPPED_ROW_TEXT = "（监控已停止，数据停在最后一次采样）"

PREF_HIGH_TEXT = "已设独显(重启生效)"   # 迁移状态列: 面板/Web 共用同一句


def empty_row_text(total, sampled=True, stopped=False):
    """空表那一行该写什么。

    "没有进程占 GPU" 和 "筛选中没匹配上" 是两件不同的事: 后者明明有
    进程在跑, 只是被筛选词挡住了。混成一句, 用户会先怀疑筛选坏了,
    再怀疑机器莫名其妙空了。托盘和 Web 必须同一口径, 所以收在这。

    还有两种更不能含糊的: sampled=False 是一轮都还没采到,
    stopped=True 是监控线程已经死了 —— 那两种情况继续说
    "当前无进程占用 GPU" 就是谎报军情: 没测和测出来是 0
    不是一回事(和日报里那句"未采到核显占用数据"同一个病)。
    """
    if stopped:
        return STOPPED_ROW_TEXT
    if not sampled:
        return STARTING_ROW_TEXT
    return NO_MATCH_ROW_TEXT if total else IDLE_ROW_TEXT


def hidden_noise_count(snap):
    """表格里被噪声判据滤掉了多少个进程。

    汇总卡的 "GPU 进程" 是真 union 数 (40), 表格只留非噪声行 (34) ——
    两个数摆在同一个窗口里对不上, 用户会以为自己看漏了。所以滤掉几个
    就得说出来, 而不是让用户自己数。
    """
    pids = set()
    for key in ("util_by_pid", "gpu_all_by_pid", "dgpu_util_by_pid",
                "shared_by_pid", "dedicated_by_pid"):
        pids |= set(snap.get(key) or {})
    ded = snap.get("dedicated_by_pid") or {}
    shm = snap.get("shared_by_pid") or {}
    gpu = snap.get("gpu_all_by_pid") or {}
    return sum(1 for pid in pids
               if row_is_noise(gpu.get(pid, 0), ded.get(pid, 0) / 1048576,
                               shm.get(pid, 0) / 1048576))


NOISE_UTIL_PCT = 0.1   # 利用率噪声地板: row_is_noise 与"当前所在"共用同一档


def row_is_noise(gpu_pct, ded_mb, shm_mb):
    """这一行是不是"开着引擎但什么都没干"的噪声行。

    托盘面板和 Web 面板必须共用这一个判据: 进程持有着 GPU 引擎实例,
    这一秒利用率 0.0%、两份显存也都是 0, 列出来只会把真正该看的行
    挤到下面去。判据两边各写一份的话, 迟早会长成两张不一样的脸。
    """
    return gpu_pct < NOISE_UTIL_PCT and ded_mb < 1 and shm_mb < 1


def gpu_where_text(ig, dg):
    """详情窗"当前所在": 这会儿跑在哪张卡上。ig/dg 是核显/独显利用率。

    两侧都必须过 NOISE_UTIL_PCT 这道地板。老代码只有核显侧有
    (ig > 0.1), 独显侧光比大小: dg=0.08/ig=0.05 两个都在噪声级别,
    也敢讲"独显" —— 而 row_is_noise 判同一组数是噪声行, 面板压根
    不显示它。两处当着用户的面打架, 所以地板抽成共用常量, 谁也别
    自己写一个 0.1。
    """
    if dg > NOISE_UTIL_PCT and dg > ig:
        return "独显"
    if ig > NOISE_UTIL_PCT:
        return "核显"
    return "未见 GPU 活动"


def activity_key(gpu_pct, ded_mb, shm_mb):
    """三面共用的行序主键: GPU% 与两列显存的最大值。

    只看核显% 会把"占显存多、利用率低"的行(比如挂着的大模型)埋到底
    部, 只看显存又会把正在计算的低显存行压下去。三者取最大, 让最抢眼
    的行排最前。CLI --list、Web 面板、托盘的"自动"默认顺序都用它, 并
    列再按 pid 升序, 保证同一份快照三面行序逐行相同。
    """
    return max(gpu_pct or 0.0, ded_mb or 0.0, shm_mb or 0.0)


def game_time_credit(names_seen, span, game_secs):
    """给日报的游戏时长记账: 一个名字一轮只加一份, 加的是墙上时钟跨度。

    span 取两轮 now_ts 之差(含 sleep 间隔), 不是上一轮的采样耗时: 后者
    只占周期的一小部分, 拿它计时会把一分钟的游戏记成十几秒。
    0.2s 下限防打桩时钟或极端卡顿把一轮记成 0。
    """
    for name in names_seen:
        game_secs[name] += max(0.2, span)


_SPARK_W, _SPARK_H = 300, 46


def sparkline_html(hist, threshold=0):
    """核显占用趋势的内联 SVG。Web 面板补上托盘早就有的那张图。

    hist: [(ts, 核显总占用%)], 每 10 秒一点, 取最近 180 点 = 30 分钟。
    口径与托盘 _draw_sparkline 对齐 (同 viewBox / 同配色 / 同阈值虚线),
    不然同一串数字在两边画出两张脸。
    """
    pts = spark_points(hist)
    if len(pts) < 2:
        # 有历史点却整个落在窗口外(休眠/长时间暂停后的第一轮), 说清楚是
        # "这段时间没采到", 别拿"每 10 秒一点"当空态文案混过去
        why = ("最近 30 分钟无采样数据 (暂停监控或休眠后会出现)"
               if len(hist or []) >= 2 else "核显占用趋势 (每 10 秒一点)")
        return f"<p class=sparkempty>{why}</p>"
    ymax = spark_ymax(pts, threshold)
    coords, x_last, x_first = spark_geometry(pts, _SPARK_W, _SPARK_H, ymax)
    poly = " ".join("%g" % c for c in coords)
    # viewBox 被 width:100% 横向拉伸(高度固定 46, 所以纵向一像素
    # 对一像素, y 可以直接当像素用), 因为:
    #   1) 线宽写 vector-effect: 不写的话笔画会跟着拉变形;
    #   2) 文字不能放进 SVG —— 它会被同样拉伸, "迁移阈值 40%"
    #      会漂成一坨油腻。所以文字拿到 div 里当覆盖层。
    out = ["<div class=sparkwrap><svg class=spark role='img' "
           "aria-label='核显占用趋势' viewBox='0 0 "
           f"{_SPARK_W} {_SPARK_H}' preserveAspectRatio='none'>"]
    # 顺序与托盘一致: 先扑填充, 再画阈值线, 最后画折线。
    # 阈值线放在填充上面才看得见: 折线翻到阈值上方时,
    # 先画的那半截会被填充整块盖掉。
    # 填充多边形贴着折线的首末端落基线上, 不再默认铺满整幅宽:
    # x 按时间戳之后, 折线左边可能本来就空着一块, 硬铺满会画出
    # "有数据"的假面积
    out.append(f"<polygon class=fill points='{poly} {x_last:g} "
               f"{_SPARK_H:g} {x_first:g} {_SPARK_H:g}'/>")
    thr_y = None
    if threshold:
        thr_y = round(spark_y_of(threshold, _SPARK_H, ymax), 2)
        out.append(f"<line class=thr x1=0 y1={thr_y} x2={_SPARK_W} "
                   f"y2={thr_y}/>")
    out.append(f"<polyline class=line points='{poly}'/>")
    out.append("</svg>")
    if thr_y is not None:
        # 文字高度按 11px 估, 向上偏 14px 才能贴在线上方;
        # 顶/底各留一点余量, 否则阈值高时文字被裁到视口外
        top = min(max(0.0, thr_y - 14.0), _SPARK_H - 15.0)
        out.append(f"<span class=thr style='top:{top:.1f}px'>"
                   f"迁移阈值 {threshold:g}%</span>")
    out.append(f"<span class=corner>{spark_corner_text(pts)}</span>")
    out.append("</div>")
    return "".join(out)

_PANEL_STYLE = (
    # Windows 11 Fluent 配色: 浅色为默认基线, 深色块见文件末尾, 由
    # prefers-color-scheme 切换。所有色值按 WCAG AA 小字 4.5:1 选过;
    # WebPanelLegibility 两个用例把这份契约钉死, 换色先跑测试。
    # 版式也是 Windows 11 的: Segoe UI Variable 字体栈、8px 圆角卡片、
    # 1px 低对比描边(不用深阴影), 强调色只出现在悬停/焦点上。
    "body{font-family:'Segoe UI Variable Text','Segoe UI',system-ui,"
    "sans-serif;background:#f3f3f3;"
    "color:#1a1a1a;margin:0;padding:16px 20px}"
    "h2{font-size:16px;margin:0 0 8px;font-weight:600;color:#1a1a1a}"
    ".policy{color:#0067c0;margin:0 0 2px;font-size:13px}"
    ".cards{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 12px}"
    ".card{background:#ffffff;border:1px solid #e4e4e4;border-radius:8px;"
    "padding:9px 16px;min-width:104px}"
    ".card span{font-size:12px;color:#5d5d5d}"
    ".card b{display:block;font-size:19px;margin-top:2px;font-weight:600;"
    "font-variant-numeric:tabular-nums}"
    "#q{background:#ffffff;border:1px solid #8f8f8f;color:#1a1a1a;"
    "border-radius:4px;padding:6px 10px;width:220px;font-size:13px}"
    "#q:focus{border-color:#0078d4;outline:none}"
    "table{border-collapse:collapse;width:100%;font-size:13px;"
    "font-variant-numeric:tabular-nums}"
    "th,td{padding:6px 10px;border-bottom:1px solid #ebebeb;"
    "text-align:right;white-space:nowrap}"
    "th{background:#f9f9f9;color:#5d5d5d;font-weight:600}"
    "td.n,th.n{text-align:left}"
    "tbody tr:hover{background:#ebebeb}"
    # 迁移状态列: 左对齐 + 次要文字色, 空单元格看起来才不像是渲染坏了
    "td.pref,th.pref{text-align:left;color:#5d5d5d}"
    # 热度三档用 Windows 11 自己的红/黄/绿文字色: #b02a1a / #8a5200 /
    "#0b5d08. 悬停底 #ebebeb 上仍 5.36:1 以上, 桌面底色上 4.86:1 以上"
    ".hot{color:#b02a1a;font-weight:600}.warm{color:#8a5200}"
    ".cool{color:#0b5d08}"
    # 灰字四档按 WCAG AA 小字 4.5:1 配的: #8f8f8f 只有 2.0:1, 在 #f3f3f3
    # 上等于没有。#5d5d5d(次要)/#666666(三级)是同一族里最先过线的两色。
    "#hint{color:#5d5d5d;font-size:12px;margin:8px 0 0}"
    "#conn{color:#b02a1a;font-size:12px;margin:0 0 6px}"
    "#hidden{color:#666666;font-size:12px;margin:2px 0 0}"
    # 窄屏(手机)上表格横向滚动, 不把布局撑破
    ".tw{overflow-x:auto}"
    # 下面两行的 46 必须等于 _SPARK_H: CSS 高度和 viewBox 不一致时纵向会
    # 跟着缩放, 阈值线的位置就算错了。圆角 8px 与卡片一致。
    ".sparkwrap{position:relative;height:46px;margin:0 0 10px}"
    ".spark{display:block;width:100%;height:46px}"
    ".sparkempty{color:#5d5d5d;font-size:12px;margin:0 0 10px}"
    ".spark .thr{stroke:#b02a1a;stroke-width:1;stroke-dasharray:3 3;"
    "vector-effect:non-scaling-stroke}"
    ".spark .line{fill:none;stroke:#0f7b0f;stroke-width:1.5;"
    "vector-effect:non-scaling-stroke}"
    ".spark .fill{fill:#dff5df;stroke:none}"
    ".sparkwrap span{position:absolute;font-size:12px;line-height:1;"
    "white-space:nowrap;pointer-events:none}"
    ".sparkwrap .thr{left:4px;color:#b02a1a}"
    ".sparkwrap .corner{right:4px;top:3px;color:#666666}"

    # 深色档: 只要浏览器/系统报"深色偏好"就整体换成一组等价的 Fluent 值。
    # WebPanelLegibility 只扫这一段之前的内容(浅色基线), 深色值另有
    # WebPanelDarkPalette 用例盯着: 每条规则的色值都要在深色底上仍然过
    # WCAG AA。selector 与浅色一一对应, 少一个就覆盖不到。
    "@media (prefers-color-scheme:dark){"
    "body{background:#202020;color:#ffffff}"
    "h2{color:#ffffff}"
    ".policy{color:#4cc2ff}"
    ".card{background:#2d2d2d;border-color:#404040}"
    ".card span{color:#b4b4b4}"
    "#q{background:#2d2d2d;border-color:#8f8f8f;color:#ffffff}"
    "th,td{border-bottom-color:#3d3d3d}"
    "th{background:#272727;color:#b4b4b4}"
    "tbody tr:hover{background:#3d3d3d}"
    "td.pref,th.pref{color:#b4b4b4}"
    ".hot{color:#ff99a4}.warm{color:#ffce4e}.cool{color:#6ccb5b}"
    "#hint{color:#b4b4b4}"
    "#conn{color:#ff99a4}"
    "#hidden{color:#9a9a9a}"
    ".sparkempty{color:#b4b4b4}"
    ".spark .thr{stroke:#ff99a4}"
    ".spark .line{stroke:#6ccb5b}"
    ".spark .fill{fill:#1c3a28}"
    ".sparkwrap .thr{color:#ff99a4}"
    ".sparkwrap .corner{color:#9a9a9a}"
    "}"
)

# 筛选词存 localStorage: 局部刷新不动输入框, 存它为的是刷新页面时过滤器不丢（和托盘面板的 panel_ui.json 同一个用意）。
_PANEL_SCRIPT = (
    "<script>var q=document.getElementById('q'),"
    "conn=document.getElementById('conn');"
    "q.value=localStorage.getItem('gpumig-filter')||'';"
    # 口径与托盘面板 _matches_filter 对齐: 只认进程名和 PID。
    # 原先拿整行 textContent 匹配, 输入 "12" 会把独显% 12.3 的行也留下来,
    # 筛选结果和托盘对不上, 还会让人以为这些行真的在 12%。
    "function f(){var v=q.value.trim().toLowerCase(),n=0,t=0;"
    "var rows=document.querySelectorAll('tbody tr'),empty=null;"
    "rows.forEach(function(r){"
    "var nc=r.querySelector('td.n');"
    "if(!nc){empty=r;return;}"
    "t++;"
    "var id=(r.querySelector('td.pid')||{textContent:''}).textContent;"
    "var hit=!v||nc.textContent.toLowerCase().indexOf(v)>=0"
    "||id.toLowerCase().indexOf(v)>=0;"
    "r.style.display=hit?'':'none';if(hit)n++;});"
    # 空态行的文案跟着切: 有进程但没匹配上说"没有匹配",
    # 一个进程都没有才说"当前无进程占用 GPU" —— 两件事不能混。
    # t==0 时这句不归 JS 管: 服务端可能还一份都没采到, 它送的是
    # "正在采样第一份数据…", 按 t? 写会当场把军情盖掉。
    "if(empty){empty.style.display=n?'none':'';}"
    "if(!n&&t)empty.cells[0].textContent='没有匹配的进程';"
    "document.getElementById('hint').textContent='显示 '+n+' / '"
    "+t+' 行';}"
    "q.addEventListener('input',function(){"
    "localStorage.setItem('gpumig-filter',q.value);f();});f();"
    # 局部刷新, 不再整页 reload: 整页刷会把滚动位置弹回顶部、输入光标
    # 刷没, 每秒一次的灰屏也很吵。只换会变的那几块, 筛选框原地不动。
    # fetch 不可用(很老的浏览器)才退回整页刷新。
    # 连接状态元素: 连挂 3 次才提示, 恢复后立刻清掉。
    # lastok 记最后一次刷新成功的时刻, 空着说明这次进页面后
    # 一次都没刷成, 文案降级成页面加载时。
    "var fails=0,lastok='';"
    "function connhide(){fails=0;conn.hidden=true;}"
    "function connshow(){conn.textContent='连接已断开,数据停在 '"
    "+(lastok||'页面加载时')+' (监控进程可能已退出)';"
    "conn.hidden=false;}"
    # 第二种"装活": fetch 通着(200), 但快照停在旧时刻 —— 监控线程
    # 静默死亡时就是这样, Web 线程还活着, 每秒发回的都是同一份数。
    # 数据时刻取自服务端渲染的 #snapts(毫秒 epoch), 25 秒约等于
    # 两三个采样周期: 采样卡住或循环死掉, 都该说出来。
    "function staleshow(ts){conn.textContent='数据已停更, 最后更新 '"
    "+new Date(ts).toLocaleTimeString('zh-CN',"
    "{hour12:false})+' (监控进程可能已停止)';"
    "conn.hidden=false;}"
    "var PARTS=['policy','gpuinfo','cards','spark','rows','hint',"
    "'hidden','snapts','paused','staleafter'];"
    "function pull(){"
    "if(typeof fetch!=='function'){"
    "setTimeout(function(){location.reload();},1000);return;}"
    "fetch(location.href).then(function(r){"
    "if(!r.ok)throw new Error('http '+r.status);"
    "return r.text();})"
    ".then(function(t){var d=new DOMParser()"
    ".parseFromString(t,'text/html');"
    "PARTS.forEach(function(id){var a=document.getElementById(id),"
    "b=d.getElementById(id);if(a&&b)a.innerHTML=b.innerHTML;});"
    # 新行是服务端渲染的, 不带 display:none, 得重新套一遍当前筛选词
    "f();lastok=new Date().toLocaleTimeString('zh-CN',"
    "{hour12:false});connhide();"
    # 连得上也要看数据新不新: 手里这份快照是几秒前的?
    # 但先分清“暂停”和“死掉”: 暂停是用户自己选的,
    # snap_ts 同样会停走, 那时说“监控进程可能已停止”
    # 就是妥妥的误报 —— 托盘上都写着 [已暂停] 呢。
    "var pz=document.getElementById('paused');"
    "var paused=pz&&pz.textContent==='1';"
    "if(paused){conn.textContent='监控已暂停, 数据停在 '"
    "+(lastok||'页面加载时')"
    "+' (从托盘菜单或热键恢复)';conn.hidden=false;}"
    "var st=document.getElementById('snapts'),"
    "dts=st?Number(st.textContent||0)/1000:0;"
    # 宽限秒数由服务端按采样周期算好塞在 #staleafter 里:
    # 写死 25 秒的话, 周期 30s/60s 的健康监控会被当成死掉
    "var sa=document.getElementById('staleafter'),"
    "stal=sa?Number(sa.textContent||0):0;"
    "if(!paused&&dts&&Date.now()/1000-dts>(stal||25))"
    "staleshow(dts*1000);})"
    # 取不到数据不能没声: 监控进程一死, 页面停在最后一份快照上,
    # 看着跟还活着一样。连挂 3 次才说, 单次抖动不打扰。
    ".catch(function(){if(++fails>=3)connshow();});}"
    "setInterval(pull,1000);</script>"
)


def panel_html(snap, names):
    """Web 面板 HTML。snap = _LAST_SNAPSHOT, names = {luid: 显卡名}。"""
    rows = []
    pids = set()
    for k in ("util_by_pid", "gpu_all_by_pid", "dgpu_util_by_pid",
              "shared_by_pid", "dedicated_by_pid"):
        pids |= set(snap.get(k, {}))
    # 行序口径与 --list / 托盘一致: 主键 GPU% 与两列显存的最大值, 并列
    # 按 pid 升序。单按核显% 排会让排最前面的行顶着个位数的 GPU%, 顺
    # 序和显示出来的数字自相矛盾。
    pid_names = snap.get("pid_names") or {}
    for pid in sorted(pids, key=lambda x: (-activity_key(
        snap.get("gpu_all_by_pid", {}).get(x, 0),
        snap.get("dedicated_by_pid", {}).get(x, 0) / (1024 * 1024),
        snap.get("shared_by_pid", {}).get(x, 0) / (1024 * 1024)), x)):
        gpu = snap.get("gpu_all_by_pid", {}).get(pid, 0)
        ig = snap.get("util_by_pid", {}).get(pid, 0)
        dg = snap.get("dgpu_util_by_pid", {}).get(pid, 0)
        ded = snap.get("dedicated_by_pid", {}).get(pid, 0) / 1048576
        shm = snap.get("shared_by_pid", {}).get(pid, 0) / 1048576
        # 与托盘面板同一个判据: 全零行进表只会把该看的行挤下去
        if row_is_noise(gpu, ded, shm):
            continue
        name = _html_escape(pid_names.get(pid) or f"pid {pid}")
        # "迁移状态"列与托盘面板同文案同来源(PREF_HIGH_TEXT / pid_prefs),
        # 不然同一台机器两个界面说着两套话。
        pref = PREF_HIGH_TEXT if (snap.get("pid_prefs") or {}).get(pid) \
            else ""
        rows.append(
            f"<tr><td class=n>{name}</td><td class=pid>{pid}</td>"
            f"<td class={heat_class(gpu)}>{gpu:.1f}%</td>"
            f"<td class={heat_class(ig)}>{ig:.1f}%</td>"
            f"<td class={heat_class(dg)}>{dg:.1f}%</td>"
            f"<td>{ded:.0f}</td><td>{shm:.0f}</td>"
            f"<td class=pref>{pref}</td></tr>")
    ig = ", ".join(_html_escape(names.get(l, l))
                   for l in snap.get("igpu_luids", [])) or "-"
    dg = ", ".join(_html_escape(names.get(l, l))
                   for l in snap.get("dgpu_luids", [])) or "-"
    temp = snap.get("dgpu_temp")
    # cls 为空时整个 class 属性都不出现: panel_summary 对显存/进程数/
    # 阈值那几张返回空类名, 无条件拼 `class={cls}` 会吐出 `<b class=>`
    # —— 无值属性。浏览器兜底成 class="", 看着没差, 但那是模板在"可选
    # 类名"这条路上没上心: cls 里一旦带进空格或符号, 属性当场截断。
    # (托盘侧 _heat_color("") 有兜底, Web 这边得自己在模板里挡。)
    cards = "".join(
        f"<div class=card><span>{label}</span>"
        + (f"<b class={cls}>{text}</b>" if cls else f"<b>{text}</b>")
        + "</div>"
        for label, text, cls in panel_summary(snap))
    policy = policy_text(snap.get("mode"), snap.get("threshold") or 0,
                         snap.get("power_saver_auto"),
                         snap.get("on_battery"))
    temp_line = f"<br>独显温度: {temp}°C" if temp else ""
    # 空态行常驻(而不是"rows 空才渲染"): 有进程在跑但筛选没中的时候
    # 也要有话可说, 不能只剩一张白表格。文案由前端 JS 按 total 切换,
    # 服务端只负责"真的一个进程都没有"这一种, 见 _PANEL_SCRIPT。
    # 有真实行的候这一行必须带 display:none: 服务端送出去的 HTML 自己
    # 不能说着谎 —— JS 被禁用/报错时、打印或复制页面时, 数据下面明晃晃
    # 挂着一句"当前无进程占用 GPU"是页面在反驳自己。筛选把它放出来是
    # 前端的事(见 _PANEL_SCRIPT 里 empty.style.display 那句)。
    # 空态行的文案跟着"到底采没采到"走: 快照本身还是空的
    # (启动头一秒)时说"当前无进程占用 GPU", 就是把没测说成测了
    # 是 0 —— 和托盘面板同一句 empty_row_text。
    idle_text = empty_row_text(0, sampled=bool(snap))
    idle_row = (
        f"<tr style='display:none'><td colspan=8>{idle_text}</td></tr>"
        if rows else f"<tr><td colspan=8>{idle_text}</td></tr>")
    body = "".join(rows) + idle_row
    hidden = hidden_noise_count(snap)
    # 快照时刻, 毫秒 epoch。隐藏元素, 但进 PARTS: 局部刷新每次都换,
    # 前端才有"新数据是哪一刻的"可比。见 _PANEL_SCRIPT 的停更判定。
    snaps = int((snap.get("snap_ts") or 0) * 1000)
    snap_line = f"<span id=snapts hidden>{snaps}</span>"
    # 宽限秒数同样进 PARTS: 热重载把周期改大后, 前端下一轮就按新尺度判,
    # 不用等用户刷新页面
    stale_line = ("<span id=staleafter hidden>"
                  f"{snap.get('stale_after') or _WEB_STALE_MIN_S:g}</span>")
    hidden_line = (f"<p id=hidden class=hidden>已隐藏 {hidden} 个"
                   f" 0.0% / 0MB 的进程</p>" if hidden else "")
    # 暂停标记: 用户从托盘/热键暂停时采样循环被
    # before_cycle 挡住, snap_ts 也会停走 —— 前端得先
    # 知道这是人为暂停, 不是监控死掉。总是渲染
    # (而不是有才渲), 否则恢复时前端没字段可比。
    paused_line = ("<span id=paused hidden>1</span>" if snap.get("paused")
                   else "<span id=paused hidden>0</span>")
    # 这里全程 f-string: 模板里到处是 100% / 42.0% 这类字面量, 用 % 格式化
    # 会被它们当成格式化指令, 报 "unsupported format character"
    return (
        "<html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,"
    "<meta name='color-scheme' content='light dark'>"
        "initial-scale=1'>"
        "<title>GPU 监控</title>"
        f"<style>{_PANEL_STYLE}</style></head><body>"
        "<h2>GPU 监控</h2>"
        f"<p id=policy class='policy'>生效中: {policy}</p>"
        # 连接状态由前端维护: PARTS 局部刷新不碰它, 正常时隐藏,
        # 连续取不到数据才显示, 见 _PANEL_SCRIPT。
        "<p id=conn hidden></p>"
        f"<p id=gpuinfo>核显: {ig}<br>独显: {dg}{temp_line}</p>"
        f"<div id=cards class=cards>{cards}</div>"
        f"<div id=spark>"
        f"{sparkline_html(snap.get('history'), snap.get('threshold') or 0)}"
        f"</div>"
        "<input id=q aria-label='筛选进程名或 PID' placeholder='筛选进程名或 PID...'>"
        "<div class=tw><table><thead><tr><th class=n>进程</th><th>PID</th>"
        "<th>GPU%</th><th>核显%</th><th>独显%</th>"
        "<th>专用MB</th><th>共享MB</th><th>迁移状态</th>"
        "</tr></thead><tbody id=rows>"
        f"{body}</tbody></table></div>"
        "<p id=hint></p>"
        f"{hidden_line}{snap_line}{paused_line}{stale_line}"
        f"{_PANEL_SCRIPT}"
        "</body></html>")


def web_snapshot_with_pause(snap, is_paused=None):
    """给 Web 面板的快照: 需要时打上"用户已暂停"的标记。

    暂停时 hooks["before_cycle"] 挡住采样循环, _LAST_SNAPSHOT 原地
    不动 —— 前端只看快照的话, 只能看出"数据停了", 
    分不清是用户按了暂停还是监控进程死掉。而把
    paused 写进快照又不行: 恢复采样时循环会覆盖快照, 
    那个键会固定在恢复前的值上。所以在读取的那一刻
    现取, 只给面板一份复制品。
    """
    if is_paused is None or not is_paused():
        return snap
    merged = dict(snap)
    merged["paused"] = True
    return merged


def web_thread(port, snapshot_getter, gpu_names, logfile=None):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def _panel_headers(self, nbytes):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # 前端每秒 fetch 一次这个地址, 是个活端点: 不带 no-store,
            # 浏览器或中间的代理就可能把上一秒的面子整页缓存下来 ——
            # 画面停在旧占用上, 看着像监控卡了。
            self.send_header("Cache-Control",
                             "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Length", str(nbytes))
            self.end_headers()

        def do_GET(self):
            raw = panel_html(snapshot_getter() or {}, gpu_names())
            raw = raw.encode("utf-8")
            self._panel_headers(len(raw))
            self.wfile.write(raw)

        def do_HEAD(self):
            # RFC 7231 4.3.2: HEAD 的响应头必须和 GET 一致, 只是不回 body。
            # 没实现它之前基类直接回 501 —— 健康检查、curl -I、浏览器预取
            # 全都会以为面板挂了, 而它其实好好活着。宁可多算一次页面,
            # 也要让 Content-Length 报的是 GET 真会发出的字节数。
            raw = panel_html(snapshot_getter() or {}, gpu_names())
            self._panel_headers(len(raw.encode("utf-8")))

        def log_message(self, *a):
            pass

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    except OSError as e:
        # 端口被占时原来照样打印一个打不开的 URL, 且不留任何失败记录
        log(f"Web 面板启动失败 (端口 {port} 被占用?): {e}", logfile)
        return
    log(f"Web 面板: http://127.0.0.1:{port}", logfile)
    srv.serve_forever()


def restart_process(pid, full_path):
    """结束进程并重启, 成功返回新 pid, 失败返回 None。"""
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, check=True)
    except subprocess.CalledProcessError:
        return None
    time.sleep(1)
    try:
        proc = subprocess.Popen([full_path], cwd=os.path.dirname(full_path))
        return proc.pid
    except OSError:
        return None


def discover_gpus(gpu_index):
    """gpu_index: {(luid, phys): None} -> {luid: {"phys","name","kind"}}"""
    gpus = {}
    for luid, phys in gpu_index:
        if luid in gpus:
            gpus[luid]["phys"] = min(gpus[luid]["phys"], phys)
            continue
        name = luid_to_name(luid) or f"LUID {luid}"
        kind = classify_gpu(name)
        if kind == "unknown":
            kind = classify_gpu(name, query_dedicated_mb(name))
        gpus[luid] = {"phys": phys, "name": name, "kind": kind}
    return gpus


_GPU_KIND_TAG = {"igpu": "核显", "dgpu": "独显",
                "virtual": "虚拟/WARP", "unknown": "未知"}


# 显卡名的列宽上限。占用表里进程名一列封顶
# 40 列, 显卡列表里名称一列封顶 42 列。
_NAME_COL_MAX = 40
_GPU_NAME_COL_MAX = 42


def _disp_len(text):
    """按终端显示宽度算长度: 中日韩全角字符占两列。

    等宽字体里 "专用MB" 是 8 列宽, 但 len() 只数出 4。printf 的 %-8s
    会只补 4 个空格, 于是表头的中文列比数字列宽出一截, 整列错位。

    组合用字符(拉丁字母加钩子) 和零宽字符不占列: 
    进程名里出现 "é" (e + 结合限幂)这类名字时, 
    按字符数算会多数出一列, 表格右半段整体偏一格。
    """
    total = 0
    for ch in text:
        cat = unicodedata.category(ch)
        # Mn/Mn/Me: 结合用字符(Mn/Me) 和格式字符(Cf, 零宽空格在内)
        if cat in ("Mn", "Me", "Cf"):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in "WF" else 1
    return total


def _fit_disp(text, width, right=False):
    """按显示宽度补空格。right=True 右对齐(数字列)。

    列宽必须和 printf 那一侧一一对应: GPU% 用
    %5.1f%% 是 6 列, 另两个百分比用 %6.1f%% 是 7 列,
    显存用 %8.0f 的 8 列。表头按字符数补空格的话,
    先是中文被多算, 后是列宽对不上, 列线是斜的。
    """
    gap = " " * max(0, width - _disp_len(text))
    return gap + text if right else text + gap


def _utf16_len(text):
    """字符串在 UTF-16 下占几个码元。"""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _ellipsize(text, width):
    """超宽就截断加省略号。宁可少看几个字符, 也不能让整张表散架。"""
    """宽度按 UTF-16 码元算, 不按码点: 托盘提示的
    szTip 是 TCHAR[128], Windows 算的是码元。显卡名里进了
    emoji 或生偏字(代理对)时, len() 说得少一半, 
    按码点截到 127 仍然会超 —— 而 szTip 一超, 
    整条提示直接不显示。"""
    if _utf16_len(text) <= width:
        return text
    out = ""
    used = 0
    for ch in text:
        w = _utf16_len(ch)
        if used + w > width - 3:
            break
        out += ch
        used += w
    return out + "..."


def _ellipsize_disp(text, width):
    """按显示宽度截断加省略号。

    和 _ellipsize 的分工: 表格这一侧量的是"占几列", 提示那一侧量的
    是"几个字符"。中文进程名 len() 只有显示宽度的一半, 拿字符数当
    列宽用, 名字列就会比表头宽一截, 后面的数字整列被顶走。
    """
    if _disp_len(text) <= width:
        return text
    out, used = "", 0
    for ch in text:
        if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            out += ch      # 不占列的字符照拿上, 不算宽度
            continue
        w = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if used + w > width - 3:
            break
        out += ch
        used += w
    return out + "..."


def _gpu_card_line(luid, g):
    """一行一块显卡。名字一律截到 42 **列**, 不是 42 个字符。

    %-42s 有两个毛病: 一是只给最小宽度, 长名字会把后面的
    [核显]/[独显] 推到谁知道哪儿去; 二是按字符数补空格, 中文显卡名
    会短一截。两张卡并排看的时候, 类型标签不在同一列就很难扫。
    """
    name = _ellipsize_disp(g["name"], _GPU_NAME_COL_MAX)
    return "  [LUID ...%s]  %s [%s]" % (
        luid[-8:], _fit_disp(name, _GPU_NAME_COL_MAX),
        _GPU_KIND_TAG[g["kind"]])


def cmd_list(cfg=None):
    """列出显卡与当前占用。窗口取 config 的 interval_seconds。

    和面板/托盘共用同一个口径: PDH 读的是"窗口内引擎忙了多久",
    窗口只看 1 秒的话, 同一个进程在两边能差出数倍 ——
    实测 pid 1380: 窗口 1s 读 83.2%, 3s 只读 28.0%。
    三个界面对不上同一个数时, 用户会以为其中一个坏了。
    """
    window = _sample_window((cfg or {}).get("interval_seconds", 1.0))
    print("正在枚举显卡 (GPU Engine 实例)...")
    # 枚举阶段只要实例清单, 不用等满一个窗口
    _usage, _sh, _de, gpu_index = collect_gpu_sample(window=_WINDOW_MIN)
    if not gpu_index:
        print("未发现任何活动的 GPU 引擎实例。")
        return
    gpus = discover_gpus(gpu_index)
    print("发现 %d 块显卡:" % len(gpus))
    for luid, g in sorted(gpus.items(), key=lambda kv: kv[1]["phys"]):
        print(_gpu_card_line(luid, g))

    print("\n采样当前占用 (任务管理器口径, 窗口 %g 秒 = config 的 interval_seconds)..." % window)
    usage, mem_shared, mem_dedicated, index2 = collect_gpu_sample(
        window=window)
    # 两次采样相隔一秒多, 这期间浏览器/游戏随时起进程,
    # 冒出的新 LUID 不在第一次的 gpus 里, 下面直接下标
    # 就是一个 KeyError 把 --list 直接打死
    fresh = {k for k in index2 if k[0] not in gpus}
    if fresh:
        gpus.update(discover_gpus(fresh))
        # 不报的话, 占用表里会出现一个列表里没列出来的显卡
        for luid in sorted(k[0] for k in fresh):
            print(_gpu_card_line(luid, gpus[luid])
                  + "  (采样期间新出现)")
    per_pid = defaultdict(float)      # 核显利用率 (最强引擎)
    all_by_pid = defaultdict(float)   # GPU 列: 跨适配器最强引擎
    sh_by_pid = defaultdict(float)
    de_by_pid = defaultdict(float)
    dg_by_pid = defaultdict(float)
    for (pid, luid, _phys), v in usage.items():
        all_by_pid[pid] = max(all_by_pid[pid], v)
        kind = gpus.get(luid, {}).get("kind")
        if kind == "igpu":
            per_pid[pid] = max(per_pid[pid], v)
        elif kind == "dgpu":
            dg_by_pid[pid] = max(dg_by_pid[pid], v)
    for (pid, luid, _phys), v in mem_shared.items():
        sh_by_pid[pid] += v
    for (pid, luid, _phys), v in mem_dedicated.items():
        de_by_pid[pid] += v
    pids = set(per_pid) | set(all_by_pid) | set(sh_by_pid) | set(de_by_pid)
    if not pids:
        print("  (当前无进程占用 GPU)")
        return
    rows = []
    for pid in sorted(pids, key=lambda x: (-activity_key(
        all_by_pid.get(x, 0),
        de_by_pid.get(x, 0) / (1024 * 1024),
        sh_by_pid.get(x, 0) / (1024 * 1024)), x)):
        info = pid_to_name(pid)
        name = info[0] if info else "?"
        rows.append((name, pid, all_by_pid.get(pid, 0),
                     per_pid.get(pid, 0), dg_by_pid.get(pid, 0),
                     de_by_pid.get(pid, 0) / (1024 * 1024),
                     sh_by_pid.get(pid, 0) / (1024 * 1024)))
    # 列宽跟着内容走: %-28s 不截断, UWP 那种四五十字符的进程名会把后面
    # 所有列一起推走, 数字错位就没法纵向比大小了。封顶 40, 再长就截断加
    # 省略号 —— 宁可少看几个字符, 也不能让整张表散架。
    # 列宽得按显示宽度算: 中文进程名 len()
    # 只有显示宽度的一半, 按字符数取列宽的话,
    # 名字列会比表头宽一截, 数字列整列被顶斜。
    wide = min(_NAME_COL_MAX, max([_disp_len(r[0]) for r in rows]
                                  + [_disp_len("进程")]))
    # 表头也得按显示宽度补空: CJK 字符在终端占两格,
    # %-7s 按字符数补空, "核显%" 只补 4 个空格却占 6 格,
    # 每列头都比数字列宽, 整张表的列线就是斜的。
    print("  " + _fit_disp("进程", wide) + " " + _fit_disp("pid", 7)
          + " " + _fit_disp("GPU%", 6, True) + " "
          + _fit_disp("核显%", 7, True) + " "
          + _fit_disp("独显%", 7, True) + " "
          + _fit_disp("专用MB", 8, True) + " "
          + _fit_disp("共享MB", 8, True))
    for name, pid, gpu, ig, dg, de, sh in rows:
        print("  %s %-7d %5.1f%% %6.1f%% %6.1f%% %8.0f %8.0f"
              % (_fit_disp(_ellipsize_disp(name, wide), wide), pid, gpu, ig,
                 dg, de, sh))


def kind_of_luid(luid, cache, forced):
    """带缓存的 LUID -> 'igpu'/'dgpu'/'virtual'/'unknown'。

    forced 为名称小写集合, 命中即按核显处理 (覆盖启发式)。
    只在名称启发式判不出来时才查 WMI: AdapterRAM 仅作 tiebreak,
    而每次 powershell 冷启动要 ~0.4s。
    """
    if luid in cache:
        return cache[luid]
    name = luid_to_name(luid) or f"LUID {luid}"
    if name.lower() in forced:
        kind = "igpu"
    else:
        kind = classify_gpu(name)
        if kind == "unknown":
            kind = classify_gpu(name, query_dedicated_mb(name))
    cache[luid] = (kind, name)
    return cache[luid]


_CONFIG_PATH = None
_LAST_SNAPSHOT = {}
_FAULT_FH = None       # faulthandler 端口文件，持有以防回收


def aggregate_usage(usage, mem_shared, mem_dedicated, igpu_luids, dgpu_luids):
    """把逐引擎采样按任务管理器口径聚合成每进程指标。

    利用率取**最强引擎**而非求和 (多引擎求和会虚高到 100% 以上);
    显存跨适配器求和。util_by_pid 只统计核显, 是迁移判定的输入。
    返回 (util_by_pid, gpu_all_by_pid, dgpu_util_by_pid, shared_by_pid,
    dedicated_by_pid, igpu_mem_by_pid)。
    """
    util_by_pid = defaultdict(float)       # 核显% (迁移判定用)
    gpu_all_by_pid = defaultdict(float)    # GPU% (跨适配器最强引擎)
    dgpu_util_by_pid = defaultdict(float)  # 独显%
    shared_by_pid = defaultdict(float)     # 共享 GPU 内存 (跨适配器)
    dedicated_by_pid = defaultdict(float)  # 专用 GPU 内存 (跨适配器)
    igpu_mem_by_pid = defaultdict(float)   # 核显 Shared+Dedicated 合计
    for (pid, luid, _phys), v in usage.items():
        gpu_all_by_pid[pid] = max(gpu_all_by_pid[pid], v)
        if luid in igpu_luids:
            util_by_pid[pid] = max(util_by_pid[pid], v)
        elif luid in dgpu_luids:
            dgpu_util_by_pid[pid] = max(dgpu_util_by_pid[pid], v)
    for src, dst in ((mem_shared, shared_by_pid), (mem_dedicated, dedicated_by_pid)):
        for (pid, luid, _phys), v in src.items():
            dst[pid] += v
    for src in (mem_shared, mem_dedicated):
        for (pid, luid, _phys), v in src.items():
            if luid in igpu_luids:
                igpu_mem_by_pid[pid] += v
    return (util_by_pid, gpu_all_by_pid, dgpu_util_by_pid,
            shared_by_pid, dedicated_by_pid, igpu_mem_by_pid)


def hot_reason(u, mb, threshold, vram_mb):
    """超阈理由。利用率先判, 显存阈为 0 时该规则整体关闭。

    mb 是核显上的"专用 + 共享"合计 (口径见 aggregate_usage): 核显的专用
    显存只是 BIOS 划的那一小块, 只认它这条规则永远打不中, 所以文案必须
    写合计, 不能写"专用"。百分比保留一位小数, 和 --list / 面板同一口径。
    """
    if u >= threshold:
        return f"核显利用率 {u:.1f}%"
    if vram_mb and mb >= vram_mb:
        return f"核显显存(专用+共享) {mb:.1f} MB"
    return None


def select_hot_candidates(cfg, util_by_pid, igpu_mem_by_pid, threshold,
                          vram_mb, ignore, done):
    """筛出本轮该迁移到独显的进程, 返回 [(pid, pname, full, reason)]。

    排除优先级是对外契约: 进程名 -> 完整路径前缀 -> 忽略名单 -> 已迁移。
    命中"已迁移"时顺手补进 done, 让外部撤销设置后能重新纳入。
    """
    excl = {x.lower() for x in cfg["exclude_processes"]}
    if cfg.get("exclude_defaults", True):
        excl |= {x.lower() for x in DEFAULT_EXCLUDE_PROCESSES}
    full_excl = [p.lower() for p in cfg["exclude_full_paths"]]
    hot = []
    for pid in set(util_by_pid) | set(igpu_mem_by_pid):
        reason = hot_reason(util_by_pid.get(pid, 0.0),
                            igpu_mem_by_pid.get(pid, 0.0) / (1024 * 1024),
                            threshold, vram_mb)
        if not reason:
            continue
        info = pid_to_name(pid)
        if not info or not info[1]:
            continue          # 没有完整路径就写不了注册表, 当不了候选
        pname, full = info
        if pname.lower() in excl:
            continue
        if any(full.lower().startswith(p) for p in full_excl):
            continue
        if pname.lower() in ignore:
            continue
        if is_high_performance(get_gpu_preference(full)):
            done.add(full)
            continue
        hot.append((pid, pname, full, reason))
    return hot


def verify_pending(pending_verify, usage, igpu_luids, dgpu_luids, now,
                   cfg, logfile=None):
    """迁移重启后的验证闭环: 确认 / 否认 / 超时, 三种结局都要有回执。

    刚从 cmd_monitor 里抽出来: 那段是整段 dict 增删加通知, 不抽函数就只能
    靠驱动整个循环去验, 而循环里时间是真跑的, 90 秒超时那条根本测不到。
    `usage` 是这一轮的 {(pid, luid, phys): util}。
    """
    for full, pv in list(pending_verify.items()):
        age = now - pv["since"]
        if age > 90:
            # 超时不再静静地把这条删掉: 用户刚看完"迁移成功, 进程已自动
            # 重启", 之后 90 秒一次 GPU 活动都没有 —— 确实无法确认, 但
            # "无法确认"本身就得说出来, 否则和静默失败是同一类病。
            del pending_verify[full]
            log(f"迁移验证: {pv['name']} 重启后 {age:.0f} 秒内没有 GPU 活动，"
                f"无法确认是否运行在独显上", logfile)
            notify_cfg(cfg, "迁移未确认",
                       f"{pv['name']} 重启后一段时间内没有 GPU 活动，"
                       "没能确认是否运行在独显上；如已在使用中可忽略")
            continue
        # 注意是遍历键, 不是 usage.items(): 键是 (pid, luid, phys)
        # 三元组, items() 每个元素却是 (键, 值) 二元组 —— 按三元组
        # 解包会 ValueError, 而这段只在 auto_restart 真的重启成功、
        # pending_verify 非空时才跑到, 测试夹具里重启恒失败所以看不见。
        # 真机上这一下直接把监控循环掀翻(托盘显示"已停止")。
        luids_seen = {luid for pid2, luid, _ph in usage
                      if pid2 == pv["pid"]}
        if not luids_seen:
            continue
        if luids_seen & dgpu_luids:
            del pending_verify[full]
            log(f"迁移验证: {pv['name']} 已确认运行在独显上", logfile)
            notify_cfg(cfg, "迁移验证成功", f"{pv['name']} 已确认运行在独显上")
        elif (luids_seen & igpu_luids) and age > 20:
            del pending_verify[full]
            log(f"迁移验证: {pv['name']} 重启后仍在核显上运行", logfile)
            notify_cfg(cfg, "迁移未生效",
                       f"{pv['name']} 重启后仍在核显上，"
                       "可能被显卡驱动策略覆盖，可到系统图形设置手动确认")


# AI/推理服务: 模型加载进显存要数分钟, 空闲时利用率极低。对它们做
# 省电切回或自动重启会丢掉已加载的模型, 必须完全豁免 (用户明确要求)。
DEFAULT_AI_PROCESSES = [
    "ollama.exe", "llama-server.exe", "llama-cli.exe", "llama-quantize.exe",
    "koboldcpp.exe", "comfyui.exe", "lm studio.exe", "gpt4all.exe",
    "jan.exe", "vllm.exe", "sglang.exe", "tabbyapi.exe", "webui.exe",
]


def is_ai_protected(cfg, pname, pid, dedicated_by_pid):
    """AI 显存保护: 命中 AI 名单, 或独显专用显存占用达到保护阈值。

    模型一旦加载, 即使推理空闲也占着大量专用显存, 用显存兜底可以
    覆盖名单外的自建推理服务。
    """
    if pname and pname.lower() in {x.lower()
                                   for x in DEFAULT_AI_PROCESSES}:
        return True
    if pname and pname.lower() in {x.lower()
                                   for x in cfg.get("ai_processes", [])}:
        return True
    limit = cfg.get("protect_vram_mb", 2048)
    if limit and dedicated_by_pid.get(pid, 0) >= limit * 1024 * 1024:
        return True
    return False


def advance_power_saver(cfg, dgpu_util_by_pid, ps_streak, ps_notified,
                        auto, logfile, dedicated_by_pid=None):
    """独显上长期低负载、且是我们迁移上去的进程: 提醒或改回核显。

    末尾的 break 让每轮最多推进一个进程: 一轮里成批改注册表太激进,
    下一轮再处理其余的。

    缺席即空闲: 程序挂在独显上什么都不干时, 根本不产生 GPU Engine 实例,
    于是不出现在 dgpu_util_by_pid 里。老代码"不在表里就清零 streak", 结果
    最该省电的那个场景永远攒不满 power_saver_samples。现在的口径是
      - 进程还活着且仍挂着高性能首选项 -> 本轮没有采样就按 0% 计;
      - 进程已退出, 或首选项已不是高性能 -> 移出跟踪 (streak 清零)。
    """
    idle_pct = cfg.get("power_saver_idle_percent", 10.0)
    need = cfg.get("power_saver_samples", 60)
    for pid in sorted(set(ps_streak) | set(dgpu_util_by_pid)):
        info = pid_to_name(pid)
        if not info:
            ps_streak.pop(pid, None)         # 进程没了, 计数作废
            continue
        pname, full = info
        if not full:
            # 路径被拒 (系统/受保护进程) 时读不回首选项: 这轮跳过, 但不作废
            # streak —— 那是"进程没了"的语义, 这里只是暂时看不见。
            continue
        if not is_high_performance(get_gpu_preference(full)):
            # 不是我们迁移上去的(或已改回核显), 不该由省电逻辑接手
            ps_streak.pop(pid, None)
            ps_notified.discard(full)
            continue
        if is_ai_protected(cfg, pname, pid, dedicated_by_pid or {}):
            # AI 模型进程: 空闲是常态, 切回核显会毁掉已加载的模型
            ps_streak.pop(pid, None)
            continue
        u = dgpu_util_by_pid.get(pid)
        if u is not None and u >= idle_pct:
            ps_streak[pid] = 0
            continue
        if full in ps_notified:
            continue
        ps_streak[pid] += 1                 # 缺席也算一轮低负载
        if ps_streak[pid] >= need:
            # ps_notified 放在写入确认之后再记: 早记的话,
            # 写入没落地时下一轮会被当成"已提醒过"跳过,
            # 这一条就永远回不到核显了。
            log(f"省电: {pname} 在独显上持续低负载({(u or 0.0):.0f}%)",
                logfile)
            if auto:
                try:
                    set_gpu_preference(full, "GpuPreference=1;")
                except OSError as e:
                    log(f"  -> 改回核显失败: {e}", logfile)
                    ps_streak[pid] = 0
                    break
                # 和主迁移路径同一把尺子: 写完回读。报了
                # "已切回核显省电"而注册表里还是=2 的话, 用户会继续
                # 白跑在独显上。只要不再是高性能首选项就算落了地
                # (包括值被删干净的情况)。ps_notified 也要等确认
                # 落地后再记, 否则下一轮会跳过重试。
                if is_high_performance(get_gpu_preference(full)):
                    log("  -> 回读确认失败: 注册表里仍是独显首选项",
                        logfile)
                    notify_cfg(cfg, "省电切换未生效",
                               f"{pname}: 写入后回读仍在独显上")
                    ps_streak[pid] = 0
                    break
                ps_notified.add(full)
                log(f"  -> 已把 {full} 改回核显设置(GpuPreference=1;)",
                    logfile)
                notify_cfg(cfg, "已切回核显省电",
                           f"{pname} 长期低负载，已改回核显设置，"
                           "重启该程序后生效")
            else:
                notify_cfg(cfg, "省电提醒",
                           f"{pname} 在独显上持续低负载，"
                           "不用时可切回核显省电（--unset 后重开程序）")
                # 和 auto 路径记同一本账: 提醒过就得登记。
                # 否则 ps_streak 一直 >= need, 而提醒模式没有
                # "改回核显"这个动作可以借力 —— 结果就是
                # 每个采样周期都把同一句提醒重弹一遍。
                ps_notified.add(full)
        break  # 每轮最多推进一个进程的计数


def append_history(now_ts, total_util, daily, game_secs, cfg):
    """落盘一笔核显总占用, 顺带处理跨日日报与峰值。返回当日的 daily。

    history.jsonl 超 1MB 滚动为 .1; 跨日时把上一日汇总写进 report.csv 并清零。
    口径与面板一致: total_util 是最强引擎, 恒 <=100%。
    """
    hist_file = os.path.join(_DIR, "history.jsonl")
    try:
        if os.path.exists(hist_file) \
                and os.path.getsize(hist_file) > 1_000_000:
            os.replace(hist_file, hist_file + ".1")
        with open(hist_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(now_ts),
                                "total": round(total_util, 1)}) + "\n")
    except OSError:
        pass
    today = time.strftime("%Y-%m-%d")
    if today != daily["date"]:
        games_min = sum(game_secs.values()) / 60.0
        # 一位小数: 取整的话同一个峰值在通知里是 78%,
        # 到 report.csv / 面板 / --list 里就成了 77.7%,
        # 又一处"同一台机器两个读数"。
        # 游戏时长到不到一分钟时说"约 0 分钟"是在报假数:
        # 用户明明玩了半分钟, 却得到"没玩"的结论。
        # 和趋势图角标那句"(近1分钟按秒说)"同一个道理:
        # 数字得说人话。CSV 里仍然写四舍五入的分钟数, 机器读的不改。
        if games_min >= 1:
            games_text = f", 游戏约 {games_min:.0f} 分钟"
        elif games_min > 0:
            games_text = ", 游戏不到 1 分钟"
        else:
            games_text = ""
        # 一整天一次峰值都没记下(peak_ts 为空)时, "核显峰值 0.0%()"
        # 是把"没测"说成"测了是 0": 括号里空着, 数字还是假的。
        # 刚装上、全天休眠、监控没跑, 三种日子得分清。
        if daily["peak_ts"]:
            peak_text = (f"核显峰值 {daily['peak']:.1f}%"
                         f"({daily['peak_ts']})")
        else:
            peak_text = "未采到核显占用数据(监控可能未在运行)"
        notify_cfg(cfg, "GPU 日报",
                   f"{daily['date']}: {peak_text}, "
                   f"迁移 {len(daily['migrated'])} 个程序"
                   + games_text)
        try:
            with open(os.path.join(_DIR, "report.csv"), "a",
                      encoding="utf-8") as f:
                f.write(f"{daily['date']},{daily['peak']:.1f},"
                        f"{daily['peak_ts']},{len(daily['migrated'])},"
                        f"{games_min:.0f}\n")
        except OSError as e:
            # 通知已经弹出"日报"了, 落盘却失败时不能一声不响: 用户照着
            # 通知去翻 report.csv 找不到那一行, 会以为程序在编数字。
            # 和 notify 里 show_toast.ps1 丢失同一个处理: 得有痕迹。
            # 这里不能用 _LOGFILE 之外的路径, 日志自身也可能正被占着。
            log(f"日报落盘失败 (report.csv): {e}", _LOGFILE)
        daily = {"date": today, "peak": 0.0, "peak_ts": "", "migrated": []}
        game_secs.clear()
    if total_util > daily["peak"]:
        daily["peak"] = total_util
        daily["peak_ts"] = time.strftime("%H:%M")
    return daily


def cmd_monitor(cfg, config_path=None, hooks=None):
    """监控主循环。hooks 可选字段:
      before_cycle() -> 返回 False 时退出循环
      on_sample(snapshot)  每轮采样后回调（托盘/面板用）
      is_paused() -> True 时用户已暂停采样; Web 面板据此
        区分"人为暂停"与"监控死掉", 不做误报
    """
    global _CONFIG_PATH, _LAST_SNAPSHOT, _LOGFILE
    _CONFIG_PATH = config_path
    logfile = (os.path.join(_DIR, "gpu_monitor.log")
               if cfg["log_to_file"] else None)
    _LOGFILE = logfile
    log(f"启动监控: 阈值 {cfg['threshold_percent']}%, 显存阈值 "
        f"{cfg.get('vram_threshold_mb', 0)}MB, 确认模式={cfg['confirm_mode']}, "
        f"自动重启={cfg['auto_restart']}", logfile)
    log("提示: 迁移通过写入图形首选项实现，进程重启后生效。", logfile)

    # 本地 Web 面板
    if cfg.get("web_port"):
        # 显卡名只能从已发布的快照副本里取: 直接遍历监控线程在写的 kind_cache
        # 会撞上 "dictionary changed size during iteration" (新 LUID 插入那刻)
        def gpu_names():
            snap = _LAST_SNAPSHOT or {}
            return {luid: v[1] for luid, v in snap.get("kind_cache", {}).items()}

        def _paused():
            return bool(hooks and hooks.get("is_paused")
                        and hooks["is_paused"]())

        def panel_snapshot():
            return web_snapshot_with_pause(_LAST_SNAPSHOT or {},
                                          _paused)

        threading.Thread(target=web_thread,
                         args=(cfg["web_port"],
                               panel_snapshot,
                               gpu_names, logfile),
                         daemon=True).start()

    # 全局热键 (回调通过 hooks 注入: hotkey_panel / hotkey_pause)
    bindings = hotkey_bindings(cfg, hooks)
    if bindings:
        threading.Thread(target=hotkey_thread, args=(bindings,),
                         daemon=True).start()

    forced = {n.lower() for n in cfg["force_igpu_names"]}
    streak = defaultdict(int)
    done = set()
    kind_cache = {}
    ignore = {n.lower() for n in cfg["ignore_processes"]}
    ps_notified = set()   # 省电处理过的 exe，只处理一次
    ps_streak = defaultdict(int)
    pending_verify = {}   # full -> {pid, since, name}: 迁移重启后的验证队列
    hist_last = 0.0
    hist_points = deque(maxlen=180)   # 趋势图: (ts, 核显总占用%) 最近 180 点
    game_seen = {}        # pname -> 最近一次活跃时间 (游戏模式)
    game_secs = defaultdict(float)   # 游戏时长累计 (秒)
    ps_check = [0.0]
    temp_check = [0.0]
    dgpu_temp = None        # 最近一次 NVML 读数, 每轮跟着快照一起发布
    temp_ok = None          # None=还没读到过, True=读得到, False=当前读不到
    temp_ok_ts = 0.0        # 最近一次读成功的时刻, 给暂留的读数限期
    cycle_ts = [None]        # 上一轮的 now_ts, 供游戏时长算墙上时钟跨度
    temp_last_notify = 0.0
    battery_state = None  # None=未检测, True=电池, False=外接
    eff_threshold = cfg["threshold_percent"]
    eff_ps_auto = cfg.get("power_saver_auto", False)
    daily = {"date": time.strftime("%Y-%m-%d"), "peak": 0.0, "peak_ts": "",
             "migrated": []}
    global _GAME_RUNNING
    last_mtime = None
    if config_path and os.path.exists(config_path):
        try:
            last_mtime = os.path.getmtime(config_path)
        except OSError:
            pass
    vram_mb = cfg.get("vram_threshold_mb", 1024)

    def apply_config(new_cfg):
        nonlocal cfg, forced, vram_mb, ignore, logfile
        nonlocal battery_state, eff_threshold, eff_ps_auto
        # web_port 与 hotkeys 在启动时一次性注册, 换 cfg 改不动它们
        restart_needed = (new_cfg.get("web_port") != cfg.get("web_port")
                          or new_cfg.get("hotkeys") != cfg.get("hotkeys"))
        cfg = new_cfg
        forced = {n.lower() for n in cfg["force_igpu_names"]}
        vram_mb = cfg.get("vram_threshold_mb", 1024)
        ignore = {n.lower() for n in cfg["ignore_processes"]}
        logfile = (os.path.join(_DIR, "gpu_monitor.log")
                   if cfg["log_to_file"] else None)
        # 迁移阈值实际读的是 eff_* 缓存, 只换 cfg 不会生效: 先按新配置直取,
        # 再清掉 battery_state 与 ps_check, 让电源块下一轮立刻重算。
        eff_threshold = cfg["threshold_percent"]
        eff_ps_auto = cfg.get("power_saver_auto", False)
        battery_state = None
        ps_check[0] = 0.0
        log(f"检测到配置修改，已热重载: "
            f"{policy_text(cfg.get('mode'), eff_threshold, eff_ps_auto)}",
            logfile)
        if restart_needed:
            log("提示: web_port / hotkeys 已修改，需重启监控后生效", logfile)

    while True:
        try:
            cycle_start = time.time()
            if hooks and hooks.get("before_cycle") \
                    and hooks["before_cycle"]() is False:
                break
            # 配置热重载 (mtime 变化即重新加载)
            if config_path and os.path.exists(config_path):
                try:
                    mtime = os.path.getmtime(config_path)
                    if last_mtime is not None and mtime != last_mtime:
                        data = read_json_dict(config_path)
                        if data is None:
                            # 退回默认配置会让运行中的策略悄悄变掉，比不改更糟
                            log("配置无法解析，沿用当前配置", logfile)
                        else:
                            apply_config(merged_config(data))
                    last_mtime = mtime
                except OSError:
                    pass

            # 只注册核显/独显实例, 大幅降低每秒采样的 CPU 开销
            allowed = {luid for luid, (k, _n) in kind_cache.items()
                       if k in ("igpu", "dgpu")}
            try:
                usage, mem_shared, mem_dedicated, gpu_index = collect_gpu_sample(
                    allowed if allowed else None,
                    normalize=cfg.get("normalize_total", False),
                    window=cfg["interval_seconds"])
            except OSError as e:
                log(f"采样失败: {e}，{cfg['interval_seconds']}s 后重试", logfile)
                time.sleep(cfg["interval_seconds"])
                continue

            # 识别本次采样中的核显/独显 LUID (新显卡首轮已全采, 这里补缓存)
            igpu_luids, dgpu_luids = set(), set()
            for (luid, _phys) in gpu_index:
                kind, _name = kind_of_luid(luid, kind_cache, forced)
                if kind == "igpu":
                    igpu_luids.add(luid)
                elif kind == "dgpu":
                    dgpu_luids.add(luid)

            # 任务管理器口径: 进程利用率 = 单引擎最大值 (多引擎求和会虚高)
            (util_by_pid, gpu_all_by_pid, dgpu_util_by_pid, shared_by_pid,
             dedicated_by_pid, mem_by_pid) = aggregate_usage(
                usage, mem_shared, mem_dedicated, igpu_luids, dgpu_luids)

            now_ts = time.time()
            # 游戏时长按墙上时钟跨度记账: 两轮 now_ts 之差(含间隔);
            # 首轮没有上一轮时间戳, 先按一个配置间隔算
            cycle_span = (now_ts - cycle_ts[0]) if cycle_ts[0] \
                else cfg["interval_seconds"]
            cycle_ts[0] = now_ts
            # 电源感知: 电池时自动启用省电策略
            if cfg.get("power_aware") and now_ts - (ps_check[0] if ps_check else 0) > 30:
                ps_check[0] = now_ts
                bat = on_battery()
                if bat != battery_state:
                    battery_state = bat
                    if bat:
                        eff_threshold = cfg.get("battery_threshold_percent", 40)
                        eff_ps_auto = True
                        log("切换到电池: 启用省电策略 "
                            f"(阈值 {eff_threshold}%, 低负载自动切回核显)", logfile)
                    else:
                        eff_threshold = cfg["threshold_percent"]
                        eff_ps_auto = cfg.get("power_saver_auto", False)
                        log("已接通电源: 恢复性能策略", logfile)

            # 独显温度 (NVML, 每 10s)
            if cfg.get("nvml_temp") and now_ts - temp_check[0] > 10:
                temp_check[0] = now_ts
                t = nvml_gpu_temp(0)
                if t is not None:
                    if temp_ok is False:
                        log(f"独显温度读取恢复: {t}°C", logfile)
                    dgpu_temp, temp_ok, temp_ok_ts = t, True, now_ts
                elif temp_ok:
                    # 读得到过、这轮读不到: 多半是 NVML 抖了一下(本机
                    # nvmlDeviceGetTemperature 有过访问冲突现场, 见
                    # install_fault_dump)。暂留上一读数, 别让面板闪断。
                    temp_ok = False
                    log(f"独显温度读取失败: 暂留上一读数 {dgpu_temp}°C, "
                        f"{_TEMP_STALE_S}s 内读回则不闪断", logfile)
                elif temp_ok_ts and now_ts - temp_ok_ts > _TEMP_STALE_S:
                    # 但只暂留一阵: 一直读不到还冻着一个数字, 是页面在说谎
                    # (同 panel_html 里“HTML 自己不能说着谎”那条), 到期收回,
                    # 温度行整行不出现。时间戳归零 → 本段只触发一次。
                    log(f"独显温度持续读不到, 已收回暂留的 {dgpu_temp}°C"
                        "(不再显示)", logfile)
                    dgpu_temp, temp_ok_ts = None, 0.0
                if t is not None and t >= cfg.get("temp_limit", 85) \
                        and now_ts - temp_last_notify > 600:
                    temp_last_notify = now_ts
                    log(f"独显温度过高: {t}°C", logfile)
                    notify_cfg(cfg, "独显温度过高",
                               f"GPU 温度 {t}°C，请检查散热或降低负载")

            # 游戏模式: 名单进程自动确保独显 + 免打扰 + 时长统计
            _GAME_RUNNING = False          # 每轮先归零, 名单被热重载清空时才不会永久静音
            if cfg.get("game_processes"):
                games = {g.lower() for g in cfg["game_processes"]}
                seen_games = set()     # 本局在跑的游戏名(去重后每轮只记一份)
                for pid2 in set(gpu_all_by_pid) | set(dgpu_util_by_pid):
                    info2 = pid_to_name(pid2)
                    if not info2 or info2[0].lower() not in games:
                        continue
                    # 先静音再动手: 否则首次发现游戏的那一轮, 下面那条通知会绕过免打扰
                    _GAME_RUNNING = True
                    pname2, full2 = info2
                    game_seen[pname2] = now_ts
                    seen_games.add(pname2)
                    # 没有完整路径就写不了注册表: full2 为 None 时
                    # set_gpu_preference 会把首选项塞进键的默认值里去
                    if full2 and pid2 in util_by_pid and util_by_pid[pid2] > 1 \
                            and not is_high_performance(
                                get_gpu_preference(full2)):
                        try:
                            set_gpu_preference(full2)
                            done.add(full2)
                            log(f"游戏模式: {pname2} 正在核显上运行, 已设为独显(重启生效)",
                                logfile)
                            notify_cfg(cfg, "游戏模式",
                                       f"{pname2} 已设为独显，重启游戏后生效")
                        except OSError as e:
                            log(f"游戏模式设置失败: {e}", logfile)
                # 记账必须在按 pid 的循环外: 同名进程(启动器+主程序)开几个
                # pid 都只算一份, 否则日报里"游戏约 N 分钟"按进程数翻倍
                game_time_credit(seen_games, cycle_span, game_secs)
                for gname in list(game_seen):
                    if now_ts - game_seen[gname] > 300:
                        del game_seen[gname]

            # 历史记录: 每 10 秒记一个点 (内存环形队列给两个面板画趋势,
            # history.jsonl 落盘供事后分析, 超 1MB 滚动)。喂图与落盘解耦:
            # 把 history.jsonl 关掉不该让界面上的趋势图一起消失。
            now_ts = time.time()
            if now_ts - hist_last >= 10:
                hist_last = now_ts
                # 任务管理器口径: 适配器利用率 = 最强引擎 (恒 <=100%)
                total_util = max(util_by_pid.values()) if util_by_pid else 0.0
                hist_points.append((now_ts, total_util))
                if hooks and hooks.get("on_history_point"):
                    hooks["on_history_point"](now_ts, total_util)
                if cfg.get("history", True):
                    daily = append_history(now_ts, total_util, daily,
                                           game_secs, cfg)

            # 生效策略与逐进程指标放在一起: 面板/托盘读快照就够了, 不必回头
            # 猜 config.json 里那套参数有没有真被吃进去。
            # 进程名对界面是刚需: Web 面板原来只发得出 PID, 看见了也没法判断
            # 该不该管。快照兜底那条路带 2 秒 TTL, 一轮几十个 pid 开销可忽略。
            pids_all = (set(util_by_pid) | set(gpu_all_by_pid)
                        | set(dgpu_util_by_pid) | set(shared_by_pid)
                        | set(dedicated_by_pid))
            pid_names = {}
            pid_prefs = {}      # pid -> 是否已设为独显, 给面板"迁移状态"列
            for pid in pids_all:
                info = pid_to_name(pid)
                if not info:
                    continue
                pid_names[pid] = info[0]
                # 托盘那边有 10 秒缓存, 这里是每轮一次: 一轮几十个 pid、
                # 每次三五个注册表调用, 相对 1 秒的采样开销可以忽略。
                # 没有完整路径就写不了注册表, 也就没什么可显示的。
                if info[1]:
                    pid_prefs[pid] = is_high_performance(
                        get_gpu_preference(info[1]))

            policy = {"mode": cfg.get("mode"),
                      "threshold": eff_threshold,
                      "power_saver_auto": eff_ps_auto,
                      "on_battery": bool(battery_state)}
            _LAST_SNAPSHOT = {
                "util_by_pid": util_by_pid, "gpu_all_by_pid": gpu_all_by_pid,
                "dgpu_util_by_pid": dgpu_util_by_pid,
                "shared_by_pid": shared_by_pid,
                "dedicated_by_pid": dedicated_by_pid,
                "igpu_luids": igpu_luids, "dgpu_luids": dgpu_luids,
                # 必须是私有拷贝: web 线程每来一个请求就遍历它一次, 而这里下一轮
                # 会往同一个 dict 里插新 LUID (唤醒/驱动重置后), 遍历会当场炸
                "kind_cache": dict(kind_cache),
                "pid_names": dict(pid_names),
                "pid_prefs": dict(pid_prefs),
                # 这批数据产生的时刻。监控线程静默死亡时 Web 线程还活着,
                # fetch 照样 200, 页面停在最后一份快照上假装在刷新 ——
                # 前端拿它和本地时钟比, 超过阈值就说"数据已停更"。
                "snap_ts": now_ts,
                # 停更宽限也跟着周期走: 周期 60s 时还按 25 秒判, 前端会把
                # 健康的监控报成"可能已停止"。见 web_stale_after。
                "stale_after": web_stale_after(cfg["interval_seconds"]),
                "history": list(hist_points),
                "dgpu_temp": dgpu_temp, **policy}

            if hooks and hooks.get("on_sample"):
                hooks["on_sample"]({
                    "util_by_pid": dict(util_by_pid),
                    "gpu_all_by_pid": dict(gpu_all_by_pid),
                    "dgpu_util_by_pid": dict(dgpu_util_by_pid),
                    "shared_by_pid": dict(shared_by_pid),
                    "dedicated_by_pid": dict(dedicated_by_pid),
                    "igpu_luids": set(igpu_luids),
                    "dgpu_luids": set(dgpu_luids),
                    "kind_cache": dict(kind_cache),
                    "pid_names": dict(pid_names),
                    # 与 _LAST_SNAPSHOT 同一套键: 托盘侧想显示独显温度/迁移
                    # 状态时得从同一个快照拿, 不能 Web 有的字段钩子快照里没有
                    "pid_prefs": dict(pid_prefs),
                    "dgpu_temp": dgpu_temp,
                    "history": list(hist_points),
                    "snap_ts": now_ts,
                    "stale_after": web_stale_after(cfg["interval_seconds"]),
                    **policy,
                })

            # 迁移效果闭环: 观察重启后的进程实际挂在哪块显卡上
            verify_pending(pending_verify, usage, igpu_luids, dgpu_luids,
                           time.time(), cfg, logfile)

            # 省电: 独显上持续低负载且已迁移过的程序 (提醒或自动切回核显)
            if cfg.get("power_saver_notify") or eff_ps_auto:
                advance_power_saver(cfg, dgpu_util_by_pid, ps_streak, ps_notified,
                                    eff_ps_auto, logfile, dedicated_by_pid)

            hot = select_hot_candidates(cfg, util_by_pid, mem_by_pid,
                                        eff_threshold, vram_mb, ignore, done)
            active = {h[2] for h in hot}
            for pid, pname, full, reason in hot:
                streak[full] += 1
                if streak[full] >= cfg["sustain_samples"]:
                    if cfg["confirm_mode"] and not ask_migrate(pname, reason):
                        log(f"进程 {pname} (pid {pid}) {reason}，确认模式下选择忽略",
                            logfile)
                        ignore.add(pname.lower())
                        if _CONFIG_PATH:
                            save_ignore_process(_CONFIG_PATH, pname)
                        streak[full] = 0
                        continue
                    log(f"进程 {pname} (pid {pid}) {reason}，"
                        f"连续 {cfg['sustain_samples']} 次超标", logfile)
                    # 落注册表和重启进程分开 try: taskkill 被杀软拦下来时
                    # (OSError) 设置其实已经写成功了, 捆在一起会被误报成
                    # "迁移失败", 连成功通知都不发。
                    try:
                        set_gpu_preference(full)
                    except OSError as e:
                        log(f"  -> 写注册表失败: {e}", logfile)
                        notify_cfg(cfg, "GPU 迁移失败", f"{pname}: {e}")
                        streak[full] = 0
                        continue
                    # 写完必须回读, 和托盘侧 _pref_apply、命令行 --set 同一把尺子:
                    # API 不抵错不等于值落了地。杀软/策略拦在半路时, 不确认就
                    # 播"迁移成功"会让用户白重启一次; 而 done 记上这一条让本轮不再重试,
                    # 日报里还会多算一个。streak 清零而不是立即重试: 重试节奏跟硬失败对齐,
                    # 不会每秒链de一次。
                    if not is_high_performance(get_gpu_preference(full)):
                        log("  -> 回读确认失败: 注册表里仍不是独显", logfile)
                        notify_cfg(cfg, "GPU 迁移未生效",
                                   f"{pname}: 写入后回读仍不是独显"
                                   f"(可能被杀软/策略拦下)")
                        streak[full] = 0
                        continue
                    done.add(full)
                    if pname not in daily["migrated"]:
                        daily["migrated"].append(pname)
                    log(f"  -> 已设置 {full} 为高性能GPU(GpuPreference=2;)",
                        logfile)
                    ar_list = {x.lower()
                               for x in cfg.get("auto_restart_processes", [])}
                    restarted = None
                    if is_ai_protected(cfg, pname, pid, dedicated_by_pid):
                        # AI 模型进程: 重启会丢模型, 只写偏好, 让用户自己
                        # 挑没有推理任务的时机重启生效
                        log(f"  -> AI 显存保护: 跳过 {pname} 的自动重启",
                            logfile)
                    elif cfg["auto_restart"] and (not ar_list
                                                  or pname.lower()
                                                  in ar_list):
                        try:
                            restarted = restart_process(pid, full)
                        except OSError as e:
                            # 重启失败只是"要用户自己动手", 迁移本身已经成功
                            log(f"  -> 自动重启失败: {e}", logfile)
                        log(f"  -> {'已自动重启 ' + pname if restarted else '自动重启失败，请手动重启'}",
                            logfile)
                        if restarted:
                            pending_verify[full] = {
                                "pid": restarted, "since": time.time(),
                                "name": pname}
                        notify_cfg(cfg, "GPU 迁移成功",
                                   f"{pname} 已设为独显运行"
                                   + ("，进程已自动重启" if restarted
                                      else "，自动重启失败请手动重启"))
                    else:
                        notify_cfg(cfg, "GPU 迁移成功",
                                   f"{pname} {reason}，已设为独显。重启该程序后生效。")
                        log("  -> 重启该程序后即可运行在独显上", logfile)
                    streak[full] = 0
                elif streak[full] == 1 or \
                        streak[full] == cfg["sustain_samples"] - 1:
                    # 只在起头和临近触发时记一行: sustain_samples=20 且十几个进程
                    # 同时超标时, 每轮上百行重复日志会淹没控制台与日志文件
                    # (print 本身实测只占 ~0.003 ms/行, 省的不是 CPU, 是可读性)
                    log(f"检测到 {pname} (pid {pid}) {reason} "
                        f"({streak[full]}/{cfg['sustain_samples']})", logfile)
            for full in list(streak):
                if full not in active:
                    streak[full] = 0

            # 周期对齐: 扣除本轮采样耗时, 使显示频率与任务管理器一致
            elapsed = time.time() - cycle_start
            time.sleep(max(0.05, cfg["interval_seconds"] - elapsed))
        except Exception:
            # 单轮里任何一个没接住的异常都不许把整个监控循环
            # (甚至托盘)一起带走: 采样/聚合/进程名/注册表回读/
            # NVML 温度/迁移/面板回调…… 任意一步抛异常, 老代码会一路
            # 冒到 _monitor_thread, 进而 self.stop=True 把整个托盘退掉
            # —— --noconsole 下用户只看到"程序一直崩溃",
            # 而托盘里连一行原因都没有。这里面底:
            # traceback 落盘, 睡够一个周期再接着跑, 宁可少采
            # 一帧, 也不让监控停摆。
            import traceback
            _exc = traceback.format_exc()
            log("监控循环异常(本轮跳过, 已自动继续):\n" + _exc, logfile)
            # log_to_file 关闭时 log() 只打印(--noconsole 下没人看得见),
            # 崩溃原因无论如何都要留下, 独立追加到固定文件供事后排查。
            try:
                with open(os.path.join(_DIR, "gpu_monitor_error.log"), "a",
                          encoding="utf-8") as _ef:
                    _ef.write(time.strftime("[%Y-%m-%d %H:%M:%S] ")
                              + "监控循环异常(本轮跳过, 已自动继续):\n"
                              + _exc + "\n")
            except OSError:
                pass
            time.sleep(max(0.05, cfg["interval_seconds"]))
            continue


def cmd_set(paths):
    for p in paths:
        full = os.path.abspath(p)
        if not os.path.isfile(full):
            print(f"文件不存在: {full}")
            continue
        try:
            set_gpu_preference(full)
        except OSError as exc:
            # 注册表写不进去(权限/策略)时该说人话: 原来直接把 traceback
            # 甩到控制台上, 而 --set 多半是在排查时敲的, 那会儿最需要的是
            # "哪一条没写进去、为什么"。
            print(f"设置失败: {full} ({exc})")
            continue
        # 写完必须回读, 和托盘侧 _pref_apply 同一把尺子: 写入"成功"
        # 而值没落地(杀软/策略拦在半路)时, 打印"已设置独显"会让
        # 用户白重启一次程序。
        if not is_high_performance(get_gpu_preference(full)):
            print(f"设置未生效: {full} (写入后回读仍不是"
                  f"独显, 可能被杀软/策略拦下)")
            continue
        print(f"已设置独显: {full}")


def cmd_unset(paths):
    for p in paths:
        full = os.path.abspath(p)
        res = clear_gpu_preference(full)
        if res is True:
            # 和 cmd_set 同一把尺子: 删除 API 说成功不算数,
            # 回读真的没了才算。否则用户会以为过段已经返回
            # 核显, 而注册表里那条还在。
            if is_high_performance(get_gpu_preference(full)):
                print(f"清除未生效: {full} (回读仍在独显上, "
                      f"可能被杀软/策略拦下)")
                continue
            print(f"已清除设置: {full}")
        elif res is None:
            print(f"本来就没有独显设置: {full}")
        else:
            print(f"清除失败(权限被限制?): {full}")


def cmd_status():
    prefs = list_gpu_prefs()
    if prefs is None:
        print("读不到 GPU 首选项: 注册表键打不开(权限被限制?), "
              "这不等于没有设置。")
        return
    migrated = [(e, v) for e, v in prefs if is_high_performance(v)]
    others = [(e, v) for e, v in prefs if not is_high_performance(v)]
    if not migrated and not others:
        print("当前没有任何 GPU 首选项设置。")
        return
    # 没迁移过就别打"已迁移 (0 个)"这样的空标题:
    # 看起来像是程序在报读数, 实则什么都没说。
    if migrated:
        print(f"已迁移到独显 ({len(migrated)} 个):")
        for exe, _v in migrated:
            print("  " + exe)
    if others:
        print(("\n" if migrated else "")
              + f"其他设置 ({len(others)} 个):")
        for exe, v in others:
            print(f"  {exe}  =  {v}")


def cmd_unset_all(assume_yes=False):
    prefs = list_gpu_prefs()
    if prefs is None:
        print("读不到 GPU 首选项: 注册表键打不开(权限被限制?), 没有可清除的。")
        return
    targets = [(e, v) for e, v in prefs if is_high_performance(v)]
    if not targets:
        print("没有需要清除的独显设置。")
        return
    print("将清除以下独显设置 (GpuPreference=2):")
    for exe, _v in targets:
        print("  " + exe)
    if assume_yes:
        ans = "y"
    else:
        try:
            ans = input(
                f"共 {len(targets)} 项，输入 y 确认清除: ").strip().lower()
        except EOFError:
            ans = ""
    if ans != "y":
        print("已取消，未做任何修改。")
        return
    cleared = clear_all_gpu_prefs(only_dgpu=True)
    # 清掉的项数小于列出来的项数时, 必须点名是哪几个没清掉: 只报一个偏小
    # 的成功数, 用户会以为其余几项本来就不存在 —— 而那几项可能正被权限
    # 或进程占用挡着。
    failed = [exe for exe, _v in targets if exe not in cleared]
    print(f"已清除 {len(cleared)}/{len(targets)} 项。")
    if failed:
        print("未能清除 (可能被占用或权限不足):")
        for exe in failed:
            print("  " + exe)


def ensure_singleton():
    """已有一个实例(托盘或命令行)在跑就返回 False。

    互斥体原来只在 --tray/--ensure 创建, 于是命令行双实例可以同时写注册表
    和 history.jsonl; 托盘跑着再开一个命令行监控时, 两边的策略还会互相打架。
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = [wt.LPCVOID, wt.BOOL, wt.LPCWSTR]
    k32.CreateMutexW(None, False, "GPUMigrate_Singleton")
    return ctypes.get_last_error() != 183   # ERROR_ALREADY_EXISTS


def ensure_utf8_output():
    """让 print 出去的字节是 UTF-8, 且任何字符都不会把命令打断。

    冷启动时 stdout 是控制台，Windows 按 PEP 528 走 UTF-8，显示没事;
    但一重定向到文件/管道（`--list > out.txt`、看门狗捕捉输出），Python 就换成当前代码页(cp936)。
    进程名里进了 emoji 或生偏字时，就会 UnicodeEncodeError 把整张表扔在半道上
    (实测: 一屏乱码之后直接异常退出)。
    errors=backslashreplace 是最后一道防线: 表达不了的字符变成转义形式，总好过整行消失。
    日志、config.json、history.jsonl、report.csv 早就是 UTF-8, 这一步把控制台输出也拼成同一套。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8",
                              errors="backslashreplace")
        except (ValueError, OSError):
            pass      # 已经 detach 或被别的层接管的 流, 不强求


_CRASH_LOG_MAX = 1 << 20      # 转储文件超这一档就先滚走, 别无限长
_FAULT_NOTE = (
    "[faulthandler] 说明: 下面每条“Windows fatal exception”只是原生故障现场,"
    " 不等于进程已死 —— 被调方自己接住的访问冲突 Python 收不到异常, 向量化"
    "处理器却照样先写一份。判死活请对照 gpu_monitor.log 的“启动监控”行前后"
    "有没有重启。\n")


def install_fault_dump():
    """原生故障留一份现场 —— 但"有转储"不等于"进程死了"。

    faulthandler 挂的是**向量化**异常处理器, 它在被调方自己的 SEH 之前先跑。
    于是访问冲突即使被 nvml / kernel32 内部接住、ctypes 照常返回、Python 一个
    异常都收不到, 这里也照样落一条 "Windows fatal exception"。本机实测: 一个
    实例连跑 17.5 小时写了 165 条转储, 五条线程 ID 全程一致, 期间没有重启。
    真正被内核干掉的进程反而一条都写不出来就没了。所以判健康要问"这些转储
    前后有没有进程重启", 数条数只会把活着的程序读成在崩。
    反过来"C 级段错误 Python 一定接不住"也不成立: 接不接得住取决于被调方
    内部有没有 SEH, 不取决于 Python。
    all_threads=True 是关键: 采样循环跑在守护线程里, 只 dump 主线程等于
    只看得到 tk 空转。--noconsole 下没人看 stdout, 所以只写文件不打印。
    说明只在**文件是新建的**时候写一次(首次或刚滚走): 每个实例都追加一行,
    会让"跑测试不许动仓库里任何状态文件"这条契约当场破 —— 拿 subprocess
    跑 gpu_monitor.py 的用例每次都会往真文件里插一行。
    这一步纯兜底: 任一处失败都当没装, 绝不影响启动。
    """
    global _FAULT_FH
    if _FAULT_FH is not None:
        return _FAULT_FH
    try:
        import faulthandler
        path = os.path.join(_DIR, "gpu_monitor_crash.log")
        try:
            if os.path.getsize(path) > _CRASH_LOG_MAX:
                os.replace(path, path + ".1")   # 上一段留档, 新实例从头写
        except OSError:
            pass               # 不存在或被占用: 这次不滚而已, 照装 handler
        fresh = not os.path.exists(path)
        _FAULT_FH = open(path, "a", encoding="utf-8", buffering=1)
        if fresh:
            _FAULT_FH.write(_FAULT_NOTE)
        try:
            faulthandler.enable(file=_FAULT_FH, all_threads=True)
        except TypeError:          # 老 Python 没有 all_threads 参数
            faulthandler.enable(file=_FAULT_FH)
    except Exception:
        _FAULT_FH = None
    return _FAULT_FH


def main():
    ensure_utf8_output()
    install_fault_dump()
    # pythonw（开机自启/无窗口模式）下没有 stdout，print 会崩，重定向到空设备
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    ap = argparse.ArgumentParser(description="核显高占用进程 -> 独显迁移工具")
    ap.add_argument("--list", action="store_true", help="列出显卡和当前占用")
    ap.add_argument("--once", action="store_true", help="采样一次退出")
    ap.add_argument("--tray", action="store_true",
                    help="托盘模式: 后台监控 + 托盘图标 + 实时面板")
    ap.add_argument("--ensure", action="store_true",
                    help="确保托盘实例在运行 (已在运行则退出), 供看门狗调用")
    ap.add_argument("--set", nargs="+", metavar="EXE", help="手动把 exe 设为独显")
    ap.add_argument("--unset", nargs="+", metavar="EXE", help="清除 exe 的独显设置")
    ap.add_argument("--status", action="store_true",
                    help="列出所有已设置 GPU 首选项的程序")
    ap.add_argument("--unset-all", action="store_true",
                    help="清除全部独显设置（GpuPreference=2）")
    ap.add_argument("--config", default=os.path.join(_DIR, "config.json"))
    if getattr(sys, "frozen", False) and len(sys.argv) == 1:
        sys.argv.append("--tray")  # 打包版双击直接进托盘模式
    args = ap.parse_args()

    if args.tray or args.ensure:
        if not ensure_singleton():
            if args.tray:
                notify_already_running()   # --ensure 是看门狗, 保持静默
            return
        try:
            import gpu_tray
            gpu_tray.run_tray(args.config)
        except Exception as exc:
            # pythonw 下没有控制台, --tray 失败必须弹窗, 否则开机自启等于
            # 静默死亡; 但 --ensure 是看门狗每 2 分钟的例行调用, 不能弹窗,
            # 只往 gpu_monitor.log 记一行, 留着给人查
            if args.tray:
                report_tray_failure(exc)
            else:
                log(f"托盘启动失败(看门狗 --ensure): {exc!r}",
                    os.path.join(_DIR, "gpu_monitor.log"))
        return
    if args.set:
        cmd_set(args.set)
        return
    if args.unset:
        cmd_unset(args.unset)
        return
    if args.status:
        cmd_status()
        return
    if args.unset_all:
        cmd_unset_all()
        return
    if args.list or args.once:
        # 一次性命令也读配置: 窗口要跟 interval_seconds 一致,
        # 否则同一个进程在 --list 和面板里是两个数
        cmd_list(load_config(args.config))
        return

    cfg = load_config(args.config)
    if not ensure_singleton():
        print("已有 GPU 监控实例在运行，本次退出。")
        return
    try:
        cmd_monitor(cfg, args.config)
    except KeyboardInterrupt:
        print("\n已退出。")


if __name__ == "__main__":
    main()
