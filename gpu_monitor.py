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
    r"_phys_(\d+)_eng_(\d+)(?:_engtype_(\w+))?\)"
)


def expand_gpu_counter_paths():
    """展开 \\GPU Engine(*)\\Utilization Percentage 的全部实例路径。

    返回的路径可能带机器名前缀，如 \\\\MACHINE\\GPU Engine(pid_...)\\...。
    GPU Engine 实例是动态的，瞬时失败时重试。
    """
    path = "\\GPU Engine(*)\\Utilization Percentage"
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


def collect_gpu_sample():
    """采样 GPU Engine。

    返回 (usage, gpu_index):
      usage: {(pid, luid, phys): 利用率%}
      gpu_index: {(luid, phys): None} 所有出现过的实例（含 0 利用率）
    """
    paths = expand_gpu_counter_paths()
    gpu_index = {}
    counters = []
    for p in paths:
        m = INSTANCE_RE.search(p)
        if not m:
            continue
        pid = int(m.group(1))
        luid = f"{m.group(2).upper()}_{m.group(3).upper()}"
        phys = int(m.group(4))
        gpu_index.setdefault((luid, phys), None)
        counters.append((p, (pid, luid, phys)))
    if not counters:
        return {}, {}

    q = wt.HANDLE()
    if pdh.PdhOpenQueryW(None, 0, ctypes.byref(q)) != 0:
        raise OSError("PdhOpenQuery failed")
    try:
        handles = []
        for p, key in counters:
            h = wt.HANDLE()
            if pdh.PdhAddCounterW(q, p, 0, ctypes.byref(h)) == 0:
                handles.append((key, h))
        if pdh.PdhCollectQueryData(q) != 0:
            raise OSError("PdhCollectQueryData #1 failed")
        time.sleep(0.5)  # 利用率需要两个采样点
        if pdh.PdhCollectQueryData(q) != 0:
            raise OSError("PdhCollectQueryData #2 failed")
        val = wt.DOUBLE()
        vtype = wt.DWORD()
        usage = defaultdict(float)
        for key, h in handles:
            if pdh.PdhGetFormattedCounterValue(h, PDH_FMT_DOUBLE, None,
                                               ctypes.byref(val)) == 0 \
                    and val.value > 0.5:
                usage[key] += val.value
        return dict(usage), gpu_index
    finally:
        pdh.PdhCloseQuery(q)


# ================================================================ D3DKMT 显卡信息

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


# ================================================================ 配置与主逻辑

DEFAULT_CONFIG = {
    "threshold_percent": 50.0,   # 核显利用率超过该值视为高占用
    "sustain_samples": 5,        # 连续多少个采样周期超标才迁移
    "interval_seconds": 5,       # 采样周期
    "auto_restart": False,       # 自动结束并重启超标进程（使设置立即生效）
    "exclude_processes": [],     # 按进程名排除，如 ["chrome.exe"]
    "exclude_full_paths": [],    # 按完整路径前缀排除
    "force_igpu_names": [],      # 手动指定哪些显卡名算核显（覆盖自动判定）
    "log_to_file": True,
}


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def log(msg, logfile=None):
    line = time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg
    print(line, flush=True)
    if logfile:
        try:
            with open(logfile, "a", encoding="utf-8") as f:
                f.write(line + "\n")
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
    _usage, gpu_index = collect_gpu_sample()
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
    usage, _ = collect_gpu_sample()
    per_pid = defaultdict(float)
    for (pid, luid, _phys), v in usage.items():
        if gpus[luid]["kind"] == "igpu":
            per_pid[pid] += v
    if not per_pid:
        print("  (当前无进程占用核显)")
        return
    for pid, v in sorted(per_pid.items(), key=lambda x: -x[1]):
        info = pid_to_name(pid)
        name = info[0] if info else "?"
        print("  %-32s pid %-6d %5.1f%%" % (name, pid, v))


def kind_of_luid(luid, cache, forced):
    """带缓存的 LUID -> 'igpu'/'dgpu'/'virtual'/'unknown'。"""
    if luid in cache:
        return cache[luid]
    name = luid_to_name(luid) or f"LUID {luid}"
    kind = ("igpu" if name.lower() in forced
            else classify_gpu(name, query_dedicated_mb(name)))
    cache[luid] = (kind, name)
    return cache[luid]


def cmd_monitor(cfg):
    logfile = (os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "gpu_monitor.log")
               if cfg["log_to_file"] else None)
    log(f"启动监控: 阈值 {cfg['threshold_percent']}%, "
        f"持续 {cfg['sustain_samples']} 次采样 (间隔 {cfg['interval_seconds']}s), "
        f"自动重启={cfg['auto_restart']}", logfile)
    log("提示: 迁移通过写入图形首选项实现，进程重启后生效。", logfile)

    forced = {n.lower() for n in cfg["force_igpu_names"]}
    streak = defaultdict(int)
    done = set()
    kind_cache = {}

    while True:
        try:
            usage, gpu_index = collect_gpu_sample()
        except OSError as e:
            log(f"采样失败: {e}，{cfg['interval_seconds']}s 后重试", logfile)
            time.sleep(cfg["interval_seconds"])
            continue

        # 识别本次采样中的核显 LUID
        igpu_luids = set()
        for (luid, _phys) in gpu_index:
            kind, _name = kind_of_luid(luid, kind_cache, forced)
            if kind == "igpu":
                igpu_luids.add(luid)

        hot = []
        for (pid, luid, _phys), v in usage.items():
            if luid not in igpu_luids or v < cfg["threshold_percent"]:
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
            if get_gpu_preference(full) == "GpuPreference=2;":
                done.add(full)
                continue
            hot.append((pid, pname, full, v))

        active = {h[2] for h in hot}
        for pid, pname, full, v in hot:
            streak[full] += 1
            if streak[full] >= cfg["sustain_samples"]:
                log(f"进程 {pname} (pid {pid}) 核显占用 {v:.0f}%，"
                    f"连续 {cfg['sustain_samples']} 次超标", logfile)
                try:
                    set_gpu_preference(full)
                    done.add(full)
                    log(f"  -> 已设置 {full} 为高性能GPU(GpuPreference=2;)", logfile)
                    if cfg["auto_restart"]:
                        ok = restart_process(pid, full)
                        log(f"  -> {'已自动重启 ' + pname if ok else '自动重启失败，请手动重启'}",
                            logfile)
                    else:
                        log("  -> 重启该程序后即可运行在独显上", logfile)
                except OSError as e:
                    log(f"  -> 写注册表失败: {e}", logfile)
                streak[full] = 0
            else:
                log(f"检测到 {pname} (pid {pid}) 核显占用 {v:.0f}% "
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


def main():
    ap = argparse.ArgumentParser(description="核显高占用进程 -> 独显迁移工具")
    ap.add_argument("--list", action="store_true", help="列出显卡和当前占用")
    ap.add_argument("--once", action="store_true", help="采样一次退出")
    ap.add_argument("--set", nargs="+", metavar="EXE", help="手动把 exe 设为独显")
    ap.add_argument("--unset", nargs="+", metavar="EXE", help="清除 exe 的独显设置")
    ap.add_argument("--config", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config.json"))
    args = ap.parse_args()

    if args.set:
        cmd_set(args.set)
        return
    if args.unset:
        cmd_unset(args.unset)
        return
    if args.list:
        cmd_list()
        return
    if args.once:
        cmd_list()
        return

    cfg = load_config(args.config)
    try:
        cmd_monitor(cfg)
    except KeyboardInterrupt:
        print("\n已退出。")


if __name__ == "__main__":
    main()
