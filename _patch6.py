# -*- coding: utf-8 -*-
"""补丁: 指标全面同步任务管理器 (GPU%/核显%/独显%/专用内存/共享内存)。用后即删。"""
import io

# ================= gpu_monitor.py =================
p = "gpu_monitor.py"
s = io.open(p, encoding="utf-8").read()

old = '''    add_paths(expand_counter_paths("GPU Engine(*)\\Utilization Percentage"), "util")
    # 核显占用在 Shared Usage, 独显在 Dedicated Usage; 两条相加=总显存占用
    add_paths(expand_counter_paths("GPU Process Memory(*)\\Shared Usage"), "mem")
    add_paths(expand_counter_paths("GPU Process Memory(*)\\Dedicated Usage"), "mem")
    if not counters:
        return {}, {}, {}'''
new = '''    add_paths(expand_counter_paths("GPU Engine(*)\\Utilization Percentage"), "util")
    # 任务管理器进程页口径: 专用 GPU 内存 / 共享 GPU 内存 分列统计
    add_paths(expand_counter_paths("GPU Process Memory(*)\\Shared Usage"), "mems")
    add_paths(expand_counter_paths("GPU Process Memory(*)\\Dedicated Usage"), "memd")
    if not counters:
        return {}, {}, {}, {}'''
assert old in s
s = s.replace(old, new, 1)

old = '''    q = wt.HANDLE()
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
        time.sleep(1.0)  # 利用率窗口与 1s 采样周期对齐, 覆盖连续时间轴
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
                    # >100% 说明 GPU Engine 实例在窗口内被销毁重建、
                    # 句柄读到重排后的脏数据, 丢弃
                    if 0.5 < v <= 100.0:
                        usage[key] += v
            else:  # mem (Dedicated Usage): raw count, 取当前值
                mem_usage[key] += r2[0]
        # normalize=True 时把各引擎总和超过 100% 的部分按比例缩放
        # (保持相对占比); 默认关闭 = 任务管理器口径, 单进程读数不失真
        if normalize:
            total = sum(usage.values())
            if total > 100.0:
                k = 100.0 / total
                usage = {kk: v * k for kk, v in usage.items()}
        return dict(usage), dict(mem_usage), gpu_index
    finally:
        pdh.PdhCloseQuery(q)'''
new = '''    q = wt.HANDLE()
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
        time.sleep(1.0)  # 利用率窗口与 1s 采样周期对齐, 覆盖连续时间轴
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
                    if 0.5 < v <= 100.0:
                        usage[key] += v
            elif kind == "mems":   # Shared Usage: raw count 取当前值
                shared_by_key[key] += r2[0]
            else:                  # Dedicated Usage
                dedicated_by_key[key] += r2[0]
        # normalize=True 时把各引擎总和超过 100% 的部分按比例缩放
        # (保持相对占比); 默认关闭 = 任务管理器口径, 单进程读数不失真
        if normalize:
            total = sum(usage.values())
            if total > 100.0:
                k = 100.0 / total
                usage = {kk: v * k for kk, v in usage.items()}
        return (dict(usage), dict(shared_by_key),
                dict(dedicated_by_key), gpu_index)
    finally:
        pdh.PdhCloseQuery(q)'''
assert old in s
s = s.replace(old, new, 1)

# docstring 更新
old = '''    allowed_luids: 只注册这些显卡上的实例 (省 CPU); None = 全部。
    返回 (usage, mem_usage, gpu_index):
      usage:     {(pid, luid, phys): 利用率%}
      mem_usage: {(pid, luid, phys): 显存字节}
      gpu_index: {(luid, phys): None} 所有出现过的实例（含 0 利用率）
    """'''
new = '''    allowed_luids: 只注册这些显卡上的实例 (省 CPU); None = 全部。
    返回 (usage, shared, dedicated, gpu_index), 均以 (pid, luid, phys) 为键:
      usage:     利用率% (单引擎)
      shared:    共享 GPU 内存字节 (任务管理器"共享 GPU 内存"口径)
      dedicated: 专用 GPU 内存字节 (任务管理器"专用 GPU 内存"口径)
      gpu_index: {(luid, phys): None} 所有出现过的实例（含 0 利用率）
    """'''
assert old in s
s = s.replace(old, new, 1)

# ---- cmd_list: 展示任务管理器三列 ----
old = '''    print("\\n采样当前核显占用...")
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
        print("  %-32s %-8d %9.1f%% %10.0f MB" % (name, pid, u, m / (1024 * 1024)))'''
new = '''    print("\\n采样当前占用 (任务管理器口径)...")
    usage, shared, dedicated, _ = collect_gpu_sample()
    per_pid = defaultdict(float)        # 核显利用率 (最强引擎)
    all_by_pid = defaultdict(float)     # GPU 列: 跨适配器最强引擎
    sh_by_pid = defaultdict(float)
    de_by_pid = defaultdict(float)
    for (pid, luid, _phys), v in usage.items():
        all_by_pid[pid] = max(all_by_pid[pid], v)
        if gpus[luid]["kind"] == "igpu":
            per_pid[pid] = max(per_pid[pid], v)
    for (pid, luid, _phys), v in shared.items():
        sh_by_pid[pid] += v
    for (pid, luid, _phys), v in dedicated.items():
        de_by_pid[pid] += v
    pids = set(per_pid) | set(all_by_pid) | set(sh_by_pid) | set(de_by_pid)
    if not pids:
        print("  (当前无进程占用 GPU)")
        return
    print("  %-28s %-7s %7s %7s %7s %10s %10s"
          % ("进程", "pid", "GPU%", "核显%", "独显%", "专用MB", "共享MB"))
    for pid in sorted(pids, key=lambda x: -max(all_by_pid.get(x, 0),
                                               sh_by_pid.get(x, 0) / (1024 * 1024),
                                               de_by_pid.get(x, 0) / (1024 * 1024))):
        info = pid_to_name(pid)
        name = info[0] if info else "?"
        print("  %-28s %-7d %6.1f%% %6.1f%% %6.1f%% %9.0f %9.0f"
              % (name, pid, all_by_pid.get(pid, 0), per_pid.get(pid, 0),
                 max((v for (p2, l2, _ph), v in usage.items()
                      if p2 == pid and gpus[l2]["kind"] == "dgpu"), default=0),
                 de_by_pid.get(pid, 0) / (1024 * 1024),
                 sh_by_pid.get(pid, 0) / (1024 * 1024)))'''
assert old in s
s = s.replace(old, new, 1)

# ---- cmd_monitor: 汇总层拆分 ----
old = '''        # 按进程汇总核显/独显/其他GPU(WARP、虚拟)上的利用率与专用显存
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
                mem_by_pid[pid] += v'''
new = '''        # 任务管理器口径: 进程利用率 = 单引擎最大值 (多引擎求和会虚高)
        util_by_pid = defaultdict(float)      # 核显% (迁移判定用)
        dgpu_util_by_pid = defaultdict(float)  # 独显%
        gpu_all_by_pid = defaultdict(float)    # GPU% (跨适配器最强引擎)
        shared_by_pid = defaultdict(float)     # 共享 GPU 内存 (跨适配器)
        dedicated_by_pid = defaultdict(float)  # 专用 GPU 内存 (跨适配器)
        for (pid, luid, _phys), v in usage.items():
            gpu_all_by_pid[pid] = max(gpu_all_by_pid[pid], v)
            if luid in igpu_luids:
                util_by_pid[pid] = max(util_by_pid[pid], v)
            elif luid in dgpu_luids:
                dgpu_util_by_pid[pid] = max(dgpu_util_by_pid[pid], v)
        for (pid, luid, _phys), v in mem_shared.items():
            shared_by_pid[pid] += v
        for (pid, luid, _phys), v in mem_dedicated.items():
            dedicated_by_pid[pid] += v
        # 核显显存占用 (Shared+Dedicated 合计), 供 vram_threshold 判定
        mem_by_pid = defaultdict(float)
        for (pid, luid, _phys), v in mem_shared.items():
            if luid in igpu_luids:
                mem_by_pid[pid] += v
        for (pid, luid, _phys), v in mem_dedicated.items():
            if luid in igpu_luids:
                mem_by_pid[pid] += v'''
assert old in s
s = s.replace(old, new, 1)

# 采样调用解包
old = '''            usage, mem_usage, gpu_index = collect_gpu_sample(
                allowed if allowed else None,
                normalize=cfg.get("normalize_total", False))'''
new = '''            usage, mem_shared, mem_dedicated, gpu_index = collect_gpu_sample(
                allowed if allowed else None,
                normalize=cfg.get("normalize_total", False))'''
assert old in s
s = s.replace(old, new, 1)

# snapshot 增加新指标
old = '''        if hooks and hooks.get("on_sample"):
            hooks["on_sample"]({
                "util_by_pid": dict(util_by_pid),
                "mem_by_pid": dict(mem_by_pid),
                "dgpu_util_by_pid": dict(dgpu_util_by_pid),
                "other_util_by_pid": dict(other_util_by_pid),
                "igpu_luids": set(igpu_luids),
                "dgpu_luids": set(dgpu_luids),
                "kind_cache": dict(kind_cache),
            })'''
new = '''        if hooks and hooks.get("on_sample"):
            hooks["on_sample"]({
                "util_by_pid": dict(util_by_pid),
                "gpu_all_by_pid": dict(gpu_all_by_pid),
                "dgpu_util_by_pid": dict(dgpu_util_by_pid),
                "shared_by_pid": dict(shared_by_pid),
                "dedicated_by_pid": dict(dedicated_by_pid),
                "igpu_luids": set(igpu_luids),
                "dgpu_luids": set(dgpu_luids),
                "kind_cache": dict(kind_cache),
            })'''
assert old in s
s = s.replace(old, new, 1)

io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("gpu_monitor.py patched OK")
