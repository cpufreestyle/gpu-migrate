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
import time
from collections import defaultdict

# ================================================================ PDH 计数器

pdh = ctypes.WinDLL("pdh", use_last_error=True)
PDH_FMT_DOUBLE = 0x200
PDH_MORE_DATA = 0x800007D2

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


def collect_gpu_sample():
    """采样 GPU Engine 利用率 + GPU Process Memory 专用显存。

    返回 (usage, mem_usage, gpu_index):
      usage:     {(pid, luid, phys): 利用率%}
      mem_usage: {(pid, luid, phys): 专用显存字节}
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
            counters.append((p, (pid, luid, phys), kind))

    add_paths(expand_counter_paths("GPU Engine(*)\\Utilization Percentage"), "util")
    add_paths(expand_counter_paths("GPU Process Memory(*)\\Dedicated Usage"), "mem")
    if not counters:
        return {}, {}, {}

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
        time.sleep(0.5)  # 利用率窗口
        if pdh.PdhCollectQueryData(q) != 0:
            raise OSError("PdhCollectQueryData #2 failed")
        usage = defaultdict(float)
        mem_usage = defaultdict(float)
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
                    if v > 0.5:
                        usage[key] += v
            else:  # mem (Dedicated Usage): raw count, 取当前值
                mem_usage[key] += r2[0]
        return dict(usage), dict(mem_usage), gpu_index
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
    r"basic render|virtual|indirect|display-only|warp", re.I)
DGPU_PAT = re.compile(r"nvidia|arc|radeon rx|radeon pro|quadro|geforce|rtx|gtx", re.I)
IGPU_PAT = re.compile(
    r"intel|radeon\(tm\) graphics|amd radeon.*graphics$|uhd|iris|vega \d+|"
    r"780m|8060s|graphics \d{3,}", re.I)


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


def query_dedicated_mb(name):
    """用 Win32_VideoController 辅助查询专用显存 (MB)，按名称匹配。"""
    try:
        ps = ("Get-CimInstance Win32_VideoController | "
              "ForEach-Object { $_.Name + '|' + $_.AdapterRAM }")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=30).stdout
        for line in out.splitlines():
            if "|" not in line:
                continue
            n, ram = line.rsplit("|", 1)
            if n.strip().lower() == name.strip().lower():
                return int(ram) // (1024 * 1024)
    except Exception:
        pass
    return None


# ================================================================ 进程信息

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def pid_to_name(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value), buf.value
        return None
    finally:
        kernel32.CloseHandle(h)


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
        cb = wt.DWORD(256)
        buf = ctypes.create_unicode_buffer(128)
        if advapi32.RegQueryValueExW(hkey, exe_path, None, None, buf,
                                     ctypes.byref(cb)) == 0:
            return buf.value
        return None
    finally:
        advapi32.RegCloseKey(hkey)


def clear_gpu_preference(exe_path):
    hkey = wt.HANDLE()
    if advapi32.RegOpenKeyExW(HKEY_CURRENT_USER, USER_GPU_PREF_KEY, 0,
                              KEY_SET_VALUE, ctypes.byref(hkey)) != 0:
        return False
    try:
        return advapi32.RegDeleteValueW(hkey, exe_path) == 0
    finally:
        advapi32.RegCloseKey(hkey)


def list_gpu_prefs():
    """枚举 UserGpuPreferences 下全部 (exe, value)。"""
    hkey = wt.HANDLE()
    if advapi32.RegOpenKeyExW(HKEY_CURRENT_USER, USER_GPU_PREF_KEY, 0,
                              KEY_QUERY_VALUE, ctypes.byref(hkey)) != 0:
        return []
    result = []
    try:
        idx = 0
        while True:
            name = ctypes.create_unicode_buffer(2048)
            cb = wt.DWORD(2048)
            data = ctypes.create_unicode_buffer(256)
            dcb = wt.DWORD(512)
            ret = advapi32.RegEnumValueW(hkey, idx, name, ctypes.byref(cb),
                                         None, None, data, ctypes.byref(dcb))
            if ret != 0:
                break
            result.append((name.value, data.value))
            idx += 1
        return result
    finally:
        advapi32.RegCloseKey(hkey)


def clear_all_gpu_prefs(only_dgpu=True):
    """清除 GPU 首选项。only_dgpu=True 只删 GpuPreference=2; 的。"""
    cleared = []
    for exe, val in list_gpu_prefs():
        if only_dgpu and val != "GpuPreference=2;":
            continue
        if clear_gpu_preference(exe):
            cleared.append(exe)
    return cleared


MB_YESNO = 0x04
MB_ICONQUESTION = 0x20
MB_TOPMOST = 0x40000
MB_SETFOREGROUND = 0x10000
IDYES = 6
user32 = ctypes.WinDLL("user32", use_last_error=True)


def ask_migrate(pname, reason):
    """确认模式弹窗。返回 True 表示迁移，False 表示忽略。"""
    text = (f"检测到 {pname} {reason}\n\n"
            f"是否把它迁移到独显？（重启该程序后生效）")
    return user32.MessageBoxW(None, text, "GPU 迁移确认",
                              MB_YESNO | MB_ICONQUESTION | MB_TOPMOST
                              | MB_SETFOREGROUND) == IDYES


def save_ignore_process(config_path, pname):
    """把确认模式下选择忽略的进程名持久化到 config.json。"""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    ign = set(cfg.get("ignore_processes", []))
    ign.add(pname)
    cfg["ignore_processes"] = sorted(ign)
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ================================================================ 配置与主逻辑

DEFAULT_CONFIG = {
    "threshold_percent": 50.0,   # 核显利用率超过该值视为高占用
    "vram_threshold_mb": 1024,   # 核显专用显存超过该 MB 也触发迁移（0 关闭）
    "sustain_samples": 5,        # 连续多少个采样周期超标才迁移
    "interval_seconds": 5,       # 采样周期
    "auto_restart": False,       # 自动结束并重启超标进程（使设置立即生效）
    "auto_restart_processes": [],  # 仅对这些进程自动重启；留空 = 对所有超标进程
    "confirm_mode": False,       # 迁移前弹窗确认（确认/忽略都会被记住）
    "ignore_processes": [],      # 确认模式下选择“忽略”的进程名，不再询问
    "power_saver_notify": False, # 独显上低负载程序提醒（可切回核显省电）
    "power_saver_idle_percent": 10.0,  # 独显利用率低于该值视为低负载
    "power_saver_samples": 60,   # 低负载持续多少个采样周期才提醒（60*5s=5分钟）
    "exclude_processes": [],     # 按进程名排除，如 ["chrome.exe"]
    "exclude_full_paths": [],    # 按完整路径前缀排除
    "force_igpu_names": [],      # 手动指定哪些显卡名算核显（覆盖自动判定）
    "notify": True,              # 迁移成功/失败时弹 Windows 通知
    "log_to_file": True,
}


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


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


def notify(title, msg):
    """发 Windows Toast 通知（异步、失败静默，不阻塞监控）。"""
    if not os.path.isfile(TOAST_PS1):
        return
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", TOAST_PS1, title, msg],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def restart_process(pid, full_path):
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, check=True)
    except subprocess.CalledProcessError:
        return False
    time.sleep(1)
    try:
        subprocess.Popen([full_path], cwd=os.path.dirname(full_path))
        return True
    except OSError:
        return False


def discover_gpus(gpu_index):
    """gpu_index: {(luid, phys): None} -> {luid: {"phys","name","kind"}}"""
    gpus = {}
    for luid, phys in gpu_index:
        if luid in gpus:
            gpus[luid]["phys"] = min(gpus[luid]["phys"], phys)
            continue
        name = luid_to_name(luid) or f"LUID {luid}"
        kind = classify_gpu(name, query_dedicated_mb(name))
        gpus[luid] = {"phys": phys, "name": name, "kind": kind}
    return gpus


def cmd_list():
    print("正在枚举显卡 (GPU Engine 实例)...")
    _usage, _mem, gpu_index = collect_gpu_sample()
    if not gpu_index:
        print("未发现任何活动的 GPU 引擎实例。")
        return
    gpus = discover_gpus(gpu_index)
    print("发现 %d 块显卡:" % len(gpus))
    for luid, g in sorted(gpus.items(), key=lambda kv: kv[1]["phys"]):
        tag = {"igpu": "核显", "dgpu": "独显",
               "virtual": "虚拟/WARP", "unknown": "未知"}[g["kind"]]
        print("  [LUID ...%s]  %-42s [%s]" % (luid[-8:], g["name"], tag))

    print("\n采样当前核显占用...")
    usage, mem_usage, _ = collect_gpu_sample()
    per_pid = defaultdict(float)
    mem_by_pid = defaultdict(float)
    for (pid, luid, _phys), v in usage.items():
        if gpus[luid]["kind"] == "igpu":
            per_pid[pid] = max(per_pid[pid], v)
    for (pid, luid, _phys), v in mem_usage.items():
        if gpus[luid]["kind"] == "igpu" and v > 1024 * 1024:  # 忽略 <1MB 噪音
            mem_by_pid[pid] += v
    if not per_pid and not mem_by_pid:
        print("  (当前无进程占用核显)")
        return
    rows = {}
    for pid, v in per_pid.items():
        rows[pid] = [v, 0.0]
    for pid, v in mem_by_pid.items():
        rows.setdefault(pid, [0.0, 0.0])[1] = v
    print("  %-32s %-8s %10s %12s" % ("进程", "pid", "利用率", "专用显存"))
    for pid, (u, m) in sorted(rows.items(), key=lambda x: -(x[1][0] + x[1][1])):
        info = pid_to_name(pid)
        name = info[0] if info else "?"
        print("  %-32s %-8d %9.1f%% %10.0f MB" % (name, pid, u, m / (1024 * 1024)))


def kind_of_luid(luid, cache, forced):
    """带缓存的 LUID -> 'igpu'/'dgpu'/'virtual'/'unknown'。"""
    if luid in cache:
        return cache[luid]
    name = luid_to_name(luid) or f"LUID {luid}"
    kind = ("igpu" if name.lower() in forced
            else classify_gpu(name, query_dedicated_mb(name)))
    cache[luid] = (kind, name)
    return cache[luid]


_CONFIG_PATH = None


def cmd_monitor(cfg, config_path=None, hooks=None):
    """监控主循环。hooks 可选字段:
      before_cycle() -> 返回 False 时退出循环
      on_sample(snapshot)  每轮采样后回调（托盘/面板用）
    """
    global _CONFIG_PATH
    _CONFIG_PATH = config_path
    logfile = (os.path.join(_DIR, "gpu_monitor.log")
               if cfg["log_to_file"] else None)
    log(f"启动监控: 阈值 {cfg['threshold_percent']}%, 显存阈值 "
        f"{cfg.get('vram_threshold_mb', 0)}MB, 确认模式={cfg['confirm_mode']}, "
        f"自动重启={cfg['auto_restart']}", logfile)
    log("提示: 迁移通过写入图形首选项实现，进程重启后生效。", logfile)

    forced = {n.lower() for n in cfg["force_igpu_names"]}
    streak = defaultdict(int)
    done = set()
    kind_cache = {}
    ignore = {n.lower() for n in cfg["ignore_processes"]}
    ps_notified = set()   # 省电提醒过的 exe，只提醒一次
    ps_streak = defaultdict(int)
    last_mtime = None
    if config_path and os.path.exists(config_path):
        try:
            last_mtime = os.path.getmtime(config_path)
        except OSError:
            pass
    vram_mb = cfg.get("vram_threshold_mb", 1024)

    def apply_config(new_cfg):
        nonlocal cfg, forced, vram_mb, ignore
        cfg = new_cfg
        forced = {n.lower() for n in cfg["force_igpu_names"]}
        vram_mb = cfg.get("vram_threshold_mb", 1024)
        ignore = {n.lower() for n in cfg["ignore_processes"]}
        log("检测到配置修改，已热重载", logfile)

    while True:
        if hooks and hooks.get("before_cycle") \
                and hooks["before_cycle"]() is False:
            break
        # 配置热重载 (mtime 变化即重新加载)
        if config_path and os.path.exists(config_path):
            try:
                mtime = os.path.getmtime(config_path)
                if last_mtime is not None and mtime != last_mtime:
                    apply_config(load_config(config_path))
                last_mtime = mtime
            except OSError:
                pass

        try:
            usage, mem_usage, gpu_index = collect_gpu_sample()
        except OSError as e:
            log(f"采样失败: {e}，{cfg['interval_seconds']}s 后重试", logfile)
            time.sleep(cfg["interval_seconds"])
            continue

        # 识别本次采样中的核显/独显 LUID
        igpu_luids, dgpu_luids = set(), set()
        for (luid, _phys) in gpu_index:
            kind, _name = kind_of_luid(luid, kind_cache, forced)
            if kind == "igpu":
                igpu_luids.add(luid)
            elif kind == "dgpu":
                dgpu_luids.add(luid)

        # 按进程汇总核显/独显/其他GPU(WARP、虚拟)上的利用率与专用显存
        # 口径与任务管理器一致: 进程利用率 = 单引擎最大值 (多引擎求和会虚高)
        util_by_pid = defaultdict(float)
        mem_by_pid = defaultdict(float)
        dgpu_util_by_pid = defaultdict(float)
        other_util_by_pid = defaultdict(float)
        for (pid, luid, _phys), v in usage.items():
            if luid in igpu_luids:
                util_by_pid[pid] = max(util_by_pid[pid], v)
            elif luid in dgpu_luids:
                dgpu_util_by_pid[pid] = max(dgpu_util_by_pid[pid], v)
            else:
                other_util_by_pid[pid] = max(other_util_by_pid[pid], v)
        for (pid, luid, _phys), v in mem_usage.items():
            if luid in igpu_luids:
                mem_by_pid[pid] += v

        if hooks and hooks.get("on_sample"):
            hooks["on_sample"]({
                "util_by_pid": dict(util_by_pid),
                "mem_by_pid": dict(mem_by_pid),
                "dgpu_util_by_pid": dict(dgpu_util_by_pid),
                "other_util_by_pid": dict(other_util_by_pid),
                "igpu_luids": set(igpu_luids),
                "dgpu_luids": set(dgpu_luids),
                "kind_cache": dict(kind_cache),
            })

        # 省电提醒: 独显上持续低负载且已迁移过的程序 (可切回核显省电)
        if cfg.get("power_saver_notify"):
            idle_pct = cfg.get("power_saver_idle_percent", 10.0)
            need = cfg.get("power_saver_samples", 60)
            for pid in list(ps_streak):
                if pid not in dgpu_util_by_pid:
                    ps_streak[pid] = 0
            for pid, u in dgpu_util_by_pid.items():
                if u >= idle_pct:
                    ps_streak[pid] = 0
                    continue
                info = pid_to_name(pid)
                if not info:
                    continue
                pname, full = info
                if full in ps_notified:
                    continue
                if get_gpu_preference(full) != "GpuPreference=2;":
                    continue
                ps_streak[pid] += 1
                if ps_streak[pid] >= need:
                    ps_notified.add(full)
                    log(f"省电提醒: {pname} 在独显上持续低负载({u:.0f}%)", logfile)
                    if cfg["notify"]:
                        notify("省电提醒",
                               f"{pname} 在独显上持续低负载，"
                               "不用时可切回核显省电（--unset 后重开程序）")
                break  # 每轮最多推进一个进程的计数

        hot = []
        for pid in set(util_by_pid) | set(mem_by_pid):
            u = util_by_pid.get(pid, 0.0)
            mb = mem_by_pid.get(pid, 0.0) / (1024 * 1024)
            if u >= cfg["threshold_percent"]:
                reason = f"核显利用率 {u:.0f}%"
            elif vram_mb and mb >= vram_mb:
                reason = f"核显专用显存 {mb:.0f} MB"
            else:
                continue
            info = pid_to_name(pid)
            if not info:
                continue
            pname, full = info
            if pname.lower() in {x.lower() for x in cfg["exclude_processes"]}:
                continue
            if any(full.lower().startswith(p.lower())
                   for p in cfg["exclude_full_paths"]):
                continue
            if pname.lower() in ignore:
                continue
            if get_gpu_preference(full) == "GpuPreference=2;":
                done.add(full)
                continue
            hot.append((pid, pname, full, reason))

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
                try:
                    set_gpu_preference(full)
                    done.add(full)
                    log(f"  -> 已设置 {full} 为高性能GPU(GpuPreference=2;)", logfile)
                    ar_list = {x.lower()
                               for x in cfg.get("auto_restart_processes", [])}
                    if cfg["auto_restart"] and (not ar_list
                                                or pname.lower() in ar_list):
                        ok = restart_process(pid, full)
                        log(f"  -> {'已自动重启 ' + pname if ok else '自动重启失败，请手动重启'}",
                            logfile)
                        if cfg["notify"]:
                            notify("GPU 迁移成功",
                                   f"{pname} 已设为独显运行"
                                   + ("，进程已自动重启" if ok else "，自动重启失败请手动重启"))
                    elif cfg["notify"]:
                        notify("GPU 迁移成功",
                               f"{pname} {reason}，已设为独显。重启该程序后生效。")
                    else:
                        log("  -> 重启该程序后即可运行在独显上", logfile)
                except OSError as e:
                    log(f"  -> 写注册表失败: {e}", logfile)
                    if cfg["notify"]:
                        notify("GPU 迁移失败", f"{pname}: {e}")
                streak[full] = 0
            else:
                log(f"检测到 {pname} (pid {pid}) {reason} "
                    f"({streak[full]}/{cfg['sustain_samples']})", logfile)
        for full in list(streak):
            if full not in active:
                streak[full] = 0

        time.sleep(cfg["interval_seconds"])


def cmd_set(paths):
    for p in paths:
        full = os.path.abspath(p)
        if not os.path.isfile(full):
            print(f"文件不存在: {full}")
            continue
        set_gpu_preference(full)
        print(f"已设置独显: {full}")


def cmd_unset(paths):
    for p in paths:
        full = os.path.abspath(p)
        if clear_gpu_preference(full):
            print(f"已清除设置: {full}")
        else:
            print(f"无设置或清除失败: {full}")


def cmd_status():
    prefs = list_gpu_prefs()
    migrated = [(e, v) for e, v in prefs if v == "GpuPreference=2;"]
    others = [(e, v) for e, v in prefs if v != "GpuPreference=2;"]
    if not migrated and not others:
        print("当前没有任何 GPU 首选项设置。")
        return
    print(f"已迁移到独显 ({len(migrated)} 个):")
    for exe, _v in migrated:
        print("  " + exe)
    if others:
        print(f"\n其他设置 ({len(others)} 个):")
        for exe, v in others:
            print(f"  {exe}  =  {v}")


def cmd_unset_all():
    targets = [(e, v) for e, v in list_gpu_prefs() if v == "GpuPreference=2;"]
    if not targets:
        print("没有需要清除的独显设置。")
        return
    print("将清除以下独显设置 (GpuPreference=2):")
    for exe, _v in targets:
        print("  " + exe)
    try:
        ans = input(f"共 {len(targets)} 项，输入 y 确认清除: ").strip().lower()
    except EOFError:
        ans = ""
    if ans != "y":
        print("已取消，未做任何修改。")
        return
    cleared = clear_all_gpu_prefs(only_dgpu=True)
    print(f"已清除 {len(cleared)} 项。")


def main():
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

    if args.tray:
        import gpu_tray
        gpu_tray.run_tray(args.config)
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
    if args.list:
        cmd_list()
        return
    if args.once:
        cmd_list()
        return

    cfg = load_config(args.config)
    try:
        cmd_monitor(cfg, args.config)
    except KeyboardInterrupt:
        print("\n已退出。")


if __name__ == "__main__":
    main()
