# GPU Migrate — 核显高占用进程迁移到独显

监控各进程对**核显 (iGPU)** 的占用，当某进程持续高占用核显时，自动把该程序的 exe 写入
Windows 图形首选项（“高性能”= 独显），程序**下次启动**即在独显上运行。

纯 Python 标准库实现（Windows 10/11），无需安装任何依赖。

## 重要说明：为什么不能“原地迁移”

Windows 不支持把**正在运行**的进程迁移到另一块 GPU——GPU 首选项只在进程启动时生效。
因此本工具的做法是：

1. 检测到某 exe 持续高占用核显 → 写入注册表 `HKCU\Software\Microsoft\DirectX\UserGpuPreferences`
   该 exe = `GpuPreference=2;`（等效于 设置→系统→屏幕→显示卡→图形 里的“高性能”）。
2. 你手动重启该程序（或配置 `auto_restart: true` 让工具自动重启它），重启后即运行在独显上。

## 用法

```powershell
# 列出显卡与当前核显占用
python gpu_monitor.py --list

# 持续监控（读取同目录 config.json）
python gpu_monitor.py

# 手动把某个 exe 设为独显 / 撤销
python gpu_monitor.py --set "D:\Games\game.exe"
python gpu_monitor.py --unset "D:\Games\game.exe"
```

## 配置（config.json）

| 字段 | 默认 | 说明 |
|---|---|---|
| `threshold_percent` | 50 | 核显利用率超过该百分比视为高占用 |
| `sustain_samples` | 5 | 连续超标多少个采样周期才迁移 |
| `interval_seconds` | 5 | 采样周期 |
| `auto_restart` | false | 迁移后自动结束并重启该进程（慎用，进程会中断） |
| `exclude_processes` | [] | 按进程名排除，如 `["chrome.exe"]` |
| `exclude_full_paths` | [] | 按完整路径前缀排除 |
| `force_igpu_names` | [] | 手动指定哪些显卡名按核显处理（自动判定失败时用） |
| `notify` | true | 迁移成功/失败时弹 Windows 右下角通知（勿扰模式下收进通知中心） |
| `log_to_file` | true | 同时写 gpu_monitor.log |

## 实现原理

- **占用采样**：PDH 性能计数器 `\GPU Engine(*)\Utilization Percentage`，
  实例名形如 `pid_1234_luid_0x00000000_0x00013502_phys_0_eng_0_engtype_3D`，
  按进程 (pid) 聚合每个引擎的利用率。
- **显卡识别**：取实例里的 LUID，通过 `gdi32!D3DKMTOpenAdapterFromLuid` +
  `D3DKMTQueryAdapterInfo`（查询类型 65）获取显卡名称。
  （不使用 DXGI——部分机器上 `CreateDXGIFactory1` 会返回 E_NOINTERFACE。）
- **核显/独显分类**：名称启发式（Intel UHD/Iris → 核显，NVIDIA/Arc/Radeon RX → 独显，
  WARP/虚拟显示适配器忽略），辅以 `Win32_VideoController` 专用显存大小；
  自动判定失败时可用 `force_igpu_names` 手动指定。

## 注意

- 杀毒软件可能对 `taskkill`/写注册表敏感；`auto_restart` 打开前请确认场景合适。
- 日志文件 `gpu_monitor.log` 与脚本同目录。
