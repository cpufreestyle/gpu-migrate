# 交接说明（质量工程师会话 · 2026-09-24，共两轮）

给接手的人/会话。本轮做的是"配置持久化与并发读写的健壮性"，不是新功能。

## 0. 现状快照（2026-10-03 第三轮重写；下面 §1–§7 是**第一轮**的原样记录，只作历史）

- 分支 `main`。**已 push**：§166 轮推 54 条（连同其 docs 提交 `66f04b1` 共 55 条）、§167 轮（第二写者）推 2 条，`3bf1c4a..02caf12` 共 57 条在 `origin/main`；§168 轮（第二写者）又推 1 条（`2d51419`）；§169 轮落 1 条 fix（`90e5084`）尚未 push，现共 59 条（`3bf1c4a..90e5084`）、未 push 1（本轮 docs 落地后为 2，push 后 `origin/main` 60 条、0 未 push）；release `v1.0.0` 已建，tag 仍指向 `fdea9ae`；release `v1.0.1` 已建（11-09，tag 指 `d097bf6`，即 §169 现网构建对应的源码树，<https://github.com/cpufreestyle/gpu-migrate/releases/tag/v1.0.1>）；§168 已把现网 exe 换成含 §155-§167 全部间距修复的新构建（活页阈值线实测 `<line class=thr x1=0 y1=10.7 x2=300 y2=10.7 />`）。§87 那段历史（工作树里的东西连同本轮改动一起提交、`config.json` 摘跟踪并按 §76 进 `.gitignore`）仍有效；"工作树只有 HANDOFF.md 未跟踪"这类状态句只对落笔当时有效，以 `git status` 实况为准；§170 / §171 / §172 三轮的 fix + docs 已提交（fix `4e6425f`、docs `0cf2027`）并 push，现共 63 条（`3bf1c4a..0cf2027`）、0 未 push（§0a 以后各节里 60 条那类旧计数以本节与 `git rev-list --count` 实况为准）。
- 体量（§172 轮）：`gpu_monitor.py` 3378 行、`gpu_tray.py` 2870 行、`tests/test_gpu_monitor.py` 13280 行、README 457 行、HANDOFF 8676 行（含 §172）。
- 测试：**693 条**。§166 轮四种跑法实测（pytest 正序 `678 collected`，全量挂 2 条已知漂白、单独复跑全过，根因见 §166；`python -X utf8 tests/test_gpu_monitor.py` `Ran 678 tests ... OK`（40.2s）、`python -X utf8 -m unittest discover -s tests` `Ran 678`（45.7s，2 个 error 同为 `RegisterClassEx` 已知漂白）；整套倒序（含守卫自身）随上述各跑覆盖，倒序那条是仓库里的正式用例 `SuiteRunsInReverseOrder`（子进程按类名自排除防套娃），不再靠人记得手工跑）。§169 轮收尾复跑两种：`python -X utf8 tests/test_gpu_monitor.py` `Ran 679 tests ... OK`（30.7s）、`python -X utf8 -m unittest discover -s tests` `Ran 679`（29.4s）；§170 轮收尾复跑两种：`python -X utf8 tests/test_gpu_monitor.py` `Ran 679 tests ... OK`（31.7s）、`python -X utf8 -m unittest discover -s tests` `Ran 679`（30.7s），两种都带 `-B` 跑过一遍确认与 pyc 无关（见 §170 踩过的坑）；§171 轮收尾复跑两种：`python -X utf8 -B tests/test_gpu_monitor.py` `Ran 685 tests ... OK`（30.7s）、`python -X utf8 -B -m unittest discover -s tests` `Ran 685 tests ... OK`（32.5s），首次跑法一撞过一次 `WinError 1410` 已知漂白（Tk 建窗时序，复跑即绿，见 §171 踩过的坑）；§172 轮收尾复跑两种：`python -X utf8 -B tests/test_gpu_monitor.py` `Ran 693 tests ... OK`（72.5s）、`python -X utf8 -B -m unittest discover -s tests` `Ran 693`（72.7s，1 个 `RegisterClassEx` 已知漂白、单跑即过），本轮新增的 `DetailWrapWidth` 会真建一次详情窗（patch `pid_to_name`），两种跑法下都稳。**跑完整套后 7 个数据/日志文件逐字节不变**：`config.json` / `panel_ui.json` / `report.csv` / `gpu_monitor.log` / `tray_debug.log` / `gpu_monitor_crash.log` / `gpu_monitor_error.log` —— 这句在 §111 时是假的，`tray_debug.log` 一直在被测试写穿，§113 才修真，§169 轮复跑实测仍旧不变（注意 `history.jsonl` 不在这 7 个里：活实例每 10s 往里追一行，拿它当"没变"的判据永远不成立，README 里那条旧声称只在没有活实例跑着的时候成立）。长行基线 §166 轮实测 `gpu_monitor.py 16 / gpu_tray.py 6 / tests 10`，§169 轮复测未变（后两个数比 §165 记录的 5/5 大，增量来自 §166 并入的第二写者内容，见 §166 踩过的坑；约定是别新增）。
- 本文档 172 节、8676 行，最新节号 §172，且**节号有倒挂**（§108 排在 §105 前面，尾部 §92 在 §87 前面）。查某件事按节号搜（`grep -n "^## 105\."`），别按物理位置读；新轮次请续号并顺手确认没有比它更大的号。**注意源码与测试里已出现 §173/§174/§175 的引用**(写者进行中的一轮：§173 详情窗键名列->值列 6px 与 §174 `#gpuinfo` 规则的代码+测试已在 fix `4e6425f` 里但本文档无对应节，§175 设置窗两行间隙 03:00 起写盘未提交)，写者该轮落地后需补 §173-§175 三节文档。
- 现网状态：`GPUMigrate.exe --ensure` 父子对存活（11-09 03:34 换上的新实例，PID 28076/28164，§170-§174 落地后的同步轮；换前 36712/41736），看门狗 `GPUMigrateWatchdog` 每 2 分钟一次，面板 `127.0.0.1:8787` 正常（head 三条 meta 完整、无幽灵文本、阈值线 `x1=0 y1=10.7 x2=300 y2=10.7` 实测正常，迁移态列/汇总卡/火花线都在）。**现网 exe 是 11-09 02:55 重建的构建**（两个 spec 构建 + 冒烟全过，十二条字节码级断言全过——§166 阈值线/`_FORM_BLOCK_GAP`、§167 `_CTL_GAP`(因 §173 新增一处值列 padx，==2 改 ==3)、§169 `_DETAIL_BTN_PAD_Y=8`/两表单框无 expand/全模块 expand==3、§170 `th,td{padding:8px 10px;line-height:15px;`/`_ROW_H=32`、§171 viewport 完整/color-scheme 独立/无 `width=device-width,<meta` 粘团签名），含 §155-§174 全部间距修复（§170 Web 行高、§171 head、§172 折行注释/守卫、§173 详情窗键名列->值列 6px、§174 `#gpuinfo` 规则；**§175 不在其中**——写者该轮 03:00 起仍在收尾，落地后需再走一轮 build->验包->换实例），§109 的崩溃日志滚动、§110 的温度保住、PDH 展开上界这些老修复同样已在其中。`gpu_monitor_crash.log` 的性质见 §109 —— 它是原生故障现场记录，**不是**死亡证明，§105 里"0 字节 = 没崩"和"165 条 = 一直崩"两种读法都错。

## 0a. 第一轮原文（历史，勿当作现状）

## 1. 环境与约束（踩过坑，请照做）

- Python **3.14.3**，pytest **9.1.1**。测试用 `unittest` + `unittest.mock`。
- **仓库里没有 ruff / mypy / pyflakes，也没有 pyproject.toml / requirements.txt。不要擅自装** —— 需要静态检查时先问用户。
- 行宽约定 ≤79 字符（既有违规：gpu_monitor.py 14 处、gpu_tray.py 7 处、tests 3 处，别新增）。
- 中文注释、中文日志、中文提交信息，提交标题是单行祈使句。
- `config.json` 既被 git 跟踪、又被运行期重写 → 工作树永久 dirty。这是个设计味道，本轮只记录未处理。
- MSYS/MinGW 下 `/tmp/xxx` 会被 Windows Python 解释成 `D:\tmp\xxx`，临时文件用 `./__name__.json`。

## 2. 本轮修掉的 5 个缺陷（都有测试锁住）

### (1) 损坏的 config.json 会打死正在运行的监控循环
`cmd_monitor` 的热重载原先只 `except OSError`，而 `json.load` 抛的是 `JSONDecodeError`（属 `ValueError`）→ 监控线程直接异常终止。
**设计决定：热重载读到坏配置时"沿用当前配置"，不回退默认值。** 悄悄改掉运行中的策略比不改更糟。这句已写进代码注释，别"顺手改成回退默认"。
- 修复：`gpu_monitor.py:1158` 附近热重载块；`load_config` 在 `gpu_monitor.py:527`，`merged_config` 在 `:538`。
- 测试：`tests/test_gpu_monitor.py:1029` `test_corrupt_config_on_reload_neither_crashes_nor_resets`（既断言迁移仍按 sustain=3 触发，也断言日志里出现"配置无法解析，沿用当前配置"）。

### (2) 五个配置写入点全是"先清空再写"
`open(path,"w")` + `json.dump`：磁盘满或进程被杀就留下半截 JSON —— 这正是 (1) 的成因。本会话期间 D 盘真的 ENOSPC 过一次，等于现场复现。
- 修复：新增 `read_json_dict`（`:420`）/ `write_json_dict`（`:436`，`tempfile.mkstemp` 独占临时名 + `os.replace`）/ `_add_to_config_list`（`:460`）。
- **临时名必须独占**：托盘线程和监控线程会并发写同一份配置，共用一个临时名会互相截断，原子性就又没了。有测试专门盯这点。
- 读取三态契约：文件缺失 → `{}`；内容坏/不是 JSON 对象 → `None`。**写入方拿到 `None` 必须放弃覆盖**，否则用户剩下的设置整份丢失。

### (3) 设置窗口"保存"会静默丢掉界面上没显示的键
`save()` 原来在配置解析失败时以 `{}` 起步，只写自己那几项 → `mode` / `hotkeys` / `ignore_processes` 被冲掉。
- 修复：`gpu_tray.py` 内层 `save()`（约 `:210`）遇到坏配置直接标题报"保存失败"并 return；写失败也报。
- 测试：`ConfigPersistence` 类 `tests/test_gpu_monitor.py:439`（7 个用例，含 `test_failed_replace_keeps_the_original_and_leaves_no_temp` 用 `mock.patch("os.replace", side_effect=OSError(28,...))`）。

### (4) 托盘切模式在"全新安装、还没有 config.json"时静默无效
- 修复：`_mode_text` / `_apply_mode`（`gpu_tray.py:128-141`）收敛成读-改-写 + 原子落盘。
- 测试：`TrayConfigWrite` 类 `tests/test_gpu_monitor.py:1516`（5 个用例，用 `object.__new__(gpu_tray.TrayApp)` 绕开托盘初始化）。

### (5) Web 面板：端口占用谎报 URL + 跨线程遍历活字典
- `web_thread`（`:788`）现在先 bind 成功才打印 URL，失败则带着 logfile 记一条并 return。
- `_LAST_SNAPSHOT["kind_cache"]` 原来发布的是监控线程**正在写的活 dict**，web 线程每请求遍历一次 → 新 LUID 插入那刻 `dictionary changed size during iteration`。改成 `dict(kind_cache)`（`:1285` 附近）。**注意**：`on_sample` 钩子那边早就拷贝了，两处不一致本身就是 bug，改的时候别只改一处。
- 测试：`SnapshotContract.test_published_kind_cache_is_a_private_copy`（`:1244`，第 3 轮注入第三个 LUID）、`WebPanelStartup`（`:1304`，两条：bind 失败不打印 URL / bind 成功用裸 socket 拉回面板 HTML）。

## 3. 已推翻的误报，别再追

`is_foreground_fullscreen`（`gpu_monitor.py:585`）"把最大化窗口误判成全屏" → **不修**。
实测：`win=(-8,-8,1032,728)`、`rcMonitor=(0,0,1024,768)`、`rcWork=(0,0,1024,720)`、`WS_MAXIMIZE=True`，"覆盖整个显示器"判据 = **False**。最大化只盖到工作区，底部 48px 被任务栏占，`rect.bottom >= m.bottom` 不成立。审查代理自报的数字（3840×2100 覆盖不了 2160 高的屏）本身就自相矛盾。
**残留未验证场景**：任务栏设为"自动隐藏"时 `rcWork==rcMonitor`，本机（4K、有任务栏）复现不了。要动这条先去开自动隐藏再量。

## 4. 确证但故意没修的 3 项（都需要产品决策，不是技术债）

1. **热键是死代码** —— `hotkey_thread` 在 `gpu_monitor.py:708`；注册判据在 `:1104` / `:1106`（`hooks.get("hotkey_panel")` / `"hotkey_pause"`），`:1108` 才 `if bindings:` 起线程。全仓只有两个生产调用点：`gpu_monitor.py:1509`（完全不传 hooks）和 `gpu_tray.py:294`（只传 3 个 hooks，不含热键）。→ **没有一个热键会被注册**。而 `DEFAULT_CONFIG` 仍在广告 `"hotkeys": {"panel": "ctrl+alt+g", "pause": "ctrl+alt+p"}`。README 没提热键，所以无效承诺只活在配置默认值里。修法二选一：接上 hooks，或删掉默认值里那几行。
2. **省电模式计数是反向的** —— `advance_power_saver` 在 `gpu_monitor.py:989`，问题块 `:998-1000`：真正在独显上闲着的进程**不产生 GPU Engine 实例**，因此不在 `dgpu_util_by_pid` 里，它的 streak 每轮被清零，永远到不了 `power_saver_samples`(60)。最该省电的场景永不触发。该分支零测试覆盖。修之前要先定语义：是"清理已退出 pid"还是"存活但缺席 = 计入空闲"。
3. **托盘吞掉监控线程的死讯** —— `gpu_tray.py:298` `except Exception:` 只写 `tray_error.log`。图标继续显示上一次的数值，菜单继续写没人读的配置，用户以为还在监控。

## 5. 审查代理剩下 8 条中/低：**未实测，我不背书**

行号是我改动前的快照，按符号名重定位后大致如下（每条都要自己复现一遍再动手）：

- [中] `notify` 两条失败路径零日志：`gpu_monitor.py:619/624/629`，`show_toast.ps1`（`:569`）缺失或 Popen 失败 → `notify: true` 静默失效。
- [中] `gpu_monitor.py:1353` 的 `try` 同时圈住 `set_gpu_preference`(`:1354`) 和 `restart_process`(`:1363`)：`taskkill`(`:815`) 被 AV 拦截抛 OSError → 已写成功的迁移被误报成"迁移失败"并跳过成功通知。
- [中] `ask_migrate`（`:411`）的模态 `MessageBoxW` 无超时阻塞监控线程整轮：采样/历史/图标全冻结，界面仍显示上次占用值。
- [中] `load_config`/`merged_config`（`:527`/`:538`）不校验类型与边界：`"threshold_percent": "40"` 会在 `hot_reason`(`:945`) 抛 TypeError；`sustain_samples: 0` 首次超标即迁移。
- [中] 互斥体只在 `--tray/--ensure` 路径创建（`:1481-1483`，`ERROR_ALREADY_EXISTS=183`）→ 命令行双实例可并发写注册表与 `history.jsonl`；`停止GPU监控.bat` 只匹配 `python.exe`，停不掉 `pythonw.exe` / `GPUMigrate.exe`。
- [低] `gpu_tray.py`：主线程被 `icon.run()`(`:627`) 占死，而三个 Tk 根窗口全在非主线程 `tk.Tk()`+`mainloop()`（`:162/:362/:499` 配 `:251/:459/:577`）→ 多个 Tcl 解释器并发主循环，偶发整体崩溃。
- [低] `gpu_monitor.py:381` `RegEnumValueW` 把任何非 0 返回都当枚举结束，未区分 `ERROR_NO_MORE_ITEMS(259)` / `ERROR_MORE_DATA(234)`（缓冲固定 256 WCHAR）→ `--status`/`--unset-all` 静默少报少删。代理自己附注"本机实测 52 条、最长 60 字符，暂无实际影响"。
- [低] `gpu_monitor.py:263` 的 powershell `timeout=30` 在监控线程同步等待，WMI 卡死则整段停 30 秒。

## 6. 复现与验证

```bash
python -X utf8 -m unittest discover -s tests     # 应为 Ran 109 tests OK
python -m pytest tests/ -q                        # 109 passed, 40 subtests passed
python gpu_monitor.py --list                      # 需要真实 GPU 数据
```

**非空测试（non-vacuity）配方 —— 本轮每条新测试都这么验过，接手补测试请沿用：**
新增测试必须能因"只回退对应那一处修复"而失败，否则它只是回归锁，不是证据。
1. 复制到临时目录，只把被修的那一处改回旧写法；
2. 在临时目录跑该测试，确认它红；
3. 记下来这条到底证明了什么。
本轮结论：`TrayConfigWrite` 里最初 4 个用例**在旧代码上也是绿的**（旧 `_apply_mode` 已经做读-改-写，也用 `except (OSError, ValueError)` 兜住了坏文件），只有 `test_mode_switch_creates_a_missing_config` 会真的红（旧代码 `AssertionError: {} != {'mode': 'saver', ...}`）。那 4 个已如实降级为"回归锁"。这个坑很容易再掉进去。

其他未跑的验证：**没有 lint / typecheck**（仓库没配也没装）；**浏览器人工检查未做** —— 面板内容只在有真实 GPU 数据时才有，本轮只用裸 socket 验过 HTTP 200 + 正文含数值 + 适配器名。真机冒烟已跑：面板 3276 字节、显卡名在、能刷新。

## 7. 下一步（按编号选）

1. 修热键：把 `hotkey_panel` / `hotkey_pause` 接进 `gpu_tray.py:294` 的 hooks，或删掉 `DEFAULT_CONFIG` 里的无效承诺（二选一，需产品决策）。
2. 修省电计数（`:998-1000`）+ 补该分支测试 —— 先定"存活但缺席"算不算空闲。
3. 把第 5 节 8 条逐条实测，确证后再定改不改。
4. 提交当前批次（5 项修复 + 16 个新测试），提交前记得 `config.json` 不该混进去。

---

# 第二轮（同日 · 继续优化，未提交）

接着第一轮的"下一步"清单往下做。**上一批 5 项修复仍未提交**，现在叠了第二批，
`git stash` 之前先想清楚要分几个提交。

## 1. 本轮修掉的 7 项（都有测试，且都做过"只回退这一处"的非空性验证）

| # | 缺陷 | 改法 | 测试 |
|---|---|---|---|
| 1 | 省电计数反向: 独显上闲着的进程不产生 GPU Engine 实例, 老代码"不在表里就清零 streak", 最该省电的场景永远攒不满 `power_saver_samples` | `advance_power_saver` 改成"缺席即空闲": 进程活着且仍是高性能首选项 → 按 0% 计入; 进程退出或首选项被撤 → 移出跟踪并清 `ps_notified`。**保留了"每轮最多推进一个进程"的 break** | `PowerSaverAdvance` 4 个新用例 |
| 2 | 配置类型不收敛: `"threshold_percent": "40"` 会在 `hot_reason` 抛 TypeError 打死监控线程; `sustain_samples: 0` 首次超标即迁移; `exclude_processes: "chrome.exe"` 被逐字符遍历 | 新增 `_NUMBER_RULES` / `_BOOL_KEYS` / `_LIST_KEYS` + `_coerce_*`, 全部收在 `merged_config` 一个入口(冷启动与热重载共用) | `ConfigValidation` 10 个用例 |
| 3 | `notify: true` 却静默失效: `show_toast.ps1` 缺失或 `Popen` 失败时零日志 | `notify` 两条失败路径都记日志, logfile 走模块级 `_LOGFILE`(由 `cmd_monitor` 赋值) | `NotifyFailureLogging` 2 个用例 |
| 4 | 迁移误报: 一个 `try` 同时圈住 `set_gpu_preference` 和 `restart_process`, `taskkill` 被杀软拦下时已成功的迁移被报成"迁移失败"且不发成功通知 | 拆成两个 try; 重启失败单独记"自动重启失败" | `MigrationReporting` 2 个用例 |
| 5 | 注册表缓冲写死: `get_gpu_preference` 128 WCHAR / `RegEnumValueW` 固定大小, 长值(长 exe 路径、`SpecificAdapter=...;GpuPreference=2;`)被当成"没设置过" → 已迁移的进程被反复重写+重复通知; `--status`/`--unset-all` 静默少报少删 | `ERROR_MORE_DATA(234)` 时扩容重试(名字按字符、数据按字节); 枚举抽成 `_enum_value` | `RegistryBuffers` 3 个用例(假 advapi32) |
| 6 | 热键是无效承诺: `DEFAULT_CONFIG` 广告 `hotkeys`, 但两个生产调用点都不传 hooks, 一个都注册不上 | 绑定逻辑抽成 `hotkey_bindings(cfg, hooks)`; 托盘把 `hotkey_panel`/`hotkey_pause` 接进 hooks; 热键线程只置标志, `_hotkey_actions` 轮询线程负责开面板/改标题(不许在热键线程碰 tkinter) | `HotkeyBindings` 3 个用例 + `TrayMonitorDeath.test_hotkey_hooks_are_handed_to_the_monitor` |
| 7 | 托盘吞掉监控线程死讯: 只写 `tray_error.log` 就返回, 图标停在上次数值, 菜单照旧写没人读的配置 | `_monitor_thread` 失败时置 `monitor_dead`/`stop`, 图标改成"监控已停止", 并发一条通知 | `TrayMonitorDeath` 2 个用例 |
| 附带 | 单实例只在 `--tray` 生效; `ask_migrate` 模态框无超时; WMI `timeout=30` 拖死监控线程 | `ensure_singleton()` 覆盖命令行持续监控(实测双实例第二个打印"已有 GPU 监控实例在运行"并退出); 确认框用 `MessageBoxTimeoutW`(按地址取, 取不到退回老路)120s 超时按忽略处理; WMI 降到 15s | `SingletonGate` 3 个用例 + `ConfirmDialogTimeout` 3 个用例 |

顺带: `停止GPU监控.bat` 原先只匹配 `python.exe`, 独显/托盘形态(`pythonw.exe`/`GPUMigrate.exe`)停不掉, 现在三种都停;
`启动GPU监控.bat` 的"是否已在运行"判断同样放宽。README 补了热键/单实例/弹窗超时/配置收敛/迁移上报口径。

## 2. 验证

- `pytest tests/ -q` → **140 passed, 40 subtests passed**(上一轮是 109 / 40)。
- 非空性: 对 7 处修复逐条"只回退这一处", 对应测试分别变红(1/10/2/1+1/2/1/1 failed),
  没有只写 regression lock 的用例。做法: 整仓拷到 `.tmptest/revert/`, 只改那一处, 跑 `-k` 子集。
- 真机冒烟: `python gpu_monitor.py --list` 正常出表; 双开命令行实例第二个按单实例退出。
- 仍**没有** lint / typecheck(仓库没配也没装)。

## 3. 两个"故意这么设计"的决定, 别顺手改掉

1. `advance_power_saver` 里"首选项不是高性能就 `ps_notified.discard(full)`":
   处理过一次的程序被切回核显后要清掉记录, 否则以后重新迁移永远不会再收到省电提醒。
   副作用是 `test_only_one_process_advances_per_cycle` 里 `notified`/`streak` 的终值和
   老实现不同, 已按新语义改写断言。
2. `merged_config` 对 `"40"` 这类数字字符串是**接受**并转成 40.0 的, 只有真正非数字才退回默认值。
   想改成"字符串一律算坏配置"的话, `ConfigValidation.test_numeric_string_is_accepted` 会红。

## 4. 还剩什么(按优先级)

1. **提交**: 两批改动叠在一起(`gpu_monitor.py` / `gpu_tray.py` / tests / README / 两个 bat),
   建议拆成"配置持久化与并发健壮性"和"本轮 7 项"两个提交; `config.json` 是运行期产物, 别混进去。
2. 第一轮第 3 节(低频疑点)还没实测: 托盘三个 Tk 根窗口都在非主线程、`__pycache__` 里残留
   `.pyc` 与 Python 3.14 版本绑定等。
3. `Web 面板`/面板 UI 仍无自动化覆盖(第一轮只验过 HTTP 200)。
4. `.tmptest/` 是本轮的临时目录, 已加进 `.gitignore`; 里面几个 `mkdtemp` 建的 0700 目录
   在会话沙箱里删不掉(沙箱把 0o700 目录实现为不可写), 需要你在外面 `rmdir /s /q .tmptest` 清一下。

## 5. 环境坑(第二轮新踩)

- 本会话的沙箱**不允许写 `%TEMP%`**, 而且 `os.mkdir(dir, 0o700)` 建出来的目录不能再往里写文件。
  `tempfile.mkdtemp` 用的正是 0o700 → 所有 `ConfigPersistence` / `LoopHarness` 用例要么
  `PermissionError` 要么在 `mkstemp` 里死循环(CPython 的 Windows 兼容分支把 PermissionError
  当成"目录已存在"无限重试, 表现为**测试挂死、CPU 打满**, 不要以为是仓库 bug)。
  跑测试时用了个沙箱外的 runner: 先把 `os.mkdir` 的 0o700 放宽成 0o777 再调 `pytest.main`。
  需要的话照 `HANDOFF` 这段重写一个即可, 仓库里不要留这种东西。

---

# 第三轮(同日 · 单 Tcl 解释器重构, 未提交)

接着第二轮第 4 节"还剩什么"的第 2 条: 三个 Tk 根窗口各跑一个 mainloop。

## 1. 本轮改了什么

**gpu_tray.py: 整个进程只有一个 Tcl 解释器、一个 mainloop。**

- 新增 `_ui_post(fn, *args)` / `_ui_main`(`gpu_tray.py:194-226`): 任何线程都能递请求,
  UI 线程持有唯一的隐藏宿主 `tk.Tk()`(`root.withdraw()`), `mainloop` 里挂了个 50ms 的
  `pump` 派发队列; 单条请求抛异常只吞它自己, 不断消息链。
- 面板/设置/详情从"各自 `tk.Tk()` + 各自 `mainloop()`"改成 `_panel_build` /
  `_settings_build` / `_detail_build`(`gpu_tray.py:228` / `:471` / `:610`), 一律
  `tk.Toplevel(self._ui_root)`; 三个 build 开头的 `if self._ui_root is None` 分支会把
  "窗口开着"的标志复位, 结尾统一 `except` 里用 `counted` 标志退回 `_ui_windows` 计数 ——
  计数是防泄漏用的, 建失败/关窗都必须原样还回去。
- 线程常驻: UI 线程一旦拉起就不再空闲退出(宿主 root 没人 destroy), `_ui_post` 见到
  线程还活着就直接投递, 否则重开 —— 这条是"只有一个解释器"的前提, 别改成空闲退出。

**顺手修掉一个只在真机上现形的 bug(详情窗半成品 + 孤儿窗口)。**

- `_detail_build`(`gpu_tray.py:640` 附近)把 FILETIME 高低位拼成
  `ctypes.c_ulonglong(...)`, 而 ctypes 实例不能参与浮点除法 → 拿到真进程句柄时
  `ft / 1e7` 抛 `TypeError`, 被 `_detail_build` 自己的 `except Exception` 吞掉:
  窗口退还计数但**不 destroy**, 屏幕上留一个永远不消失的半成品窗。
  `dw` 是 `c_ulonglong` 数组, 下标取出来本来就是 int, 直接拼即可。
- 测试: `test_detail_window_survives_a_real_process_handle`, 拿**当前进程的真 pid**
  跑真 ctypes。mock 的 `GetProcessTimes` 只会回假值, 老测试根本抓不到这条。

**tests: 新加 `TrayPanelRows`(9 个用例), 补上面板的行逻辑。**

- 覆盖: 列头排序(同列翻向/换列复位)、数字列必须按数值排("100%" vs "9%")、
  带 MB 后缀也能解析、解析不出数字的行沉底、低占用+小显存的行不进表、
  最多只画 30 行、空表出占位行、双击行时 PID 非数字不弹详情、正常时按
  `(pid, vals)` 投递。
- 这里原本零覆盖, 而"排序键用错/上限写错"全都是用户看得见的数据错误。

**tests: `TraySingleInterpreter` 上一版我写错了, 已重写。**

- 上一版让主线程手动调 `root.after.call_args_list` 里的回调。`call_args_list` 是 mock 的
  活列表, pump 每跑一次又往里 `after` 注册一次 → 边遍历边追加, **死循环 + CPU 打满**
  (现象就是测试进程一直不退出)。这是测试的 bug, 不是仓库的。
- 现在替身 `mainloop` 本身跑在 UI 线程里、按游标逐个派发 after 回调, 主线程只发请求再
  轮询等结果。这样"请求到底在哪个线程执行"才测得出来, 而且主线程碰不到 Tk。
- 顺手修: 关窗用例原来取 `root.protocol.call_args`(宿主上没注册过, 是 None), 改成等
  窗口自己的 `protocol.called` 再取; 另外计数到位 != 窗口已建完(计数器在建窗最前面就加), 时机判断不能只看计数。

## 2. 验证

- `python -X utf8 .tmptest/run_unittest.py` → **Ran 154 tests OK**(第二轮是 140)。
- 非空性(三条探针, 都只改一处):
  - `_ui_post` 改成"每个请求都新建线程" → `AssertionError: 3 != 1 整个进程只允许存在一个 Tcl 解释器`;
  - `_panel_build` 的 master 由 `tk.Toplevel(self._ui_root)` 换成 `tk.Tk()` →
    同样 `AssertionError: 2 != 1 整个进程只允许存在一个 Tcl 解释器`。
  - `ft` 那行改回 `ctypes.c_ulonglong(...)` → `AssertionError: 详情窗没建起来(疑似内部异常被吞)`。
  - `TrayPanelRows` 另跑过 7 条探针(数字列退化成字符串 / 坏数字兜底 0.0 / 点列头不重置方向 /
    不过滤低占用 / 去掉 30 行上限 / 空表不占位 / 详情入口不校验 PID), **7/7 全部变红**。
- **真 tkinter 冒烟**(`.tmptest/smoke_ui.py`, 不起托盘、UI 线程跑真 mainloop):
  面板/设置/详情三个窗口全部建成, 宿主 root 下 `winfo_children()` 正好 3 个
  Toplevel(只有一个解释器); 逐个走 `WM_DELETE_WINDOW` 处理器关闭后 `_ui_windows`
  归 0、`panel_open` 变 False; 之后重开面板仍是同一个 root、同一条 UI 线程, 不会冒出
  第二个解释器。顺带证实 tkinter 的线程亲和是真的: 主线程调 `winfo_children()` 直接
  `RuntimeError: main thread is not in main loop`。
- 行宽复检: gpu_monitor.py 14 处 / gpu_tray.py 7 处 / tests 3 处, 与基线一致, 没新增。

## 3. 环境坑(第三轮新踩)

- **pytest 入口已经不能用了**: 会话中途沙箱收回了
  `C:\Users\qm081\AppData\Roaming\Python\Python314\site-packages` 的读权限,
  pytest / pystray / Pillow 全部 import 不了。改用 `.tmptest/run_unittest.py`
  (放宽 `os.mkdir` 的 0o700 + `unittest.defaultTestLoader.discover`), 并在
  `.tmptest/stub/` 放了 pystray / PIL 的最小替身(sys.path 前置)。
  单跑某个类用 `.tmptest/run_one.py <dotted.name>`。这些都只是环境适配, 别进仓库。
- 执行通道偶发整片 `CreateProcess ... No such file or directory`: 不带 `tty` 的调用全部
  失败, 加 `tty: true` 才通。下次遇到所有命令都起不来, 先加 tty 试, 别判断成仓库问题。

## 4. 还剩什么

1. **提交**: 现在三批改动叠在一起(gpu_monitor.py / gpu_tray.py / tests / README /
   两个 bat), 建议按轮次拆三个提交; `config.json` / `panel_ui.json` / `history.jsonl` /
   `report.csv` 是运行期产物, 别混进提交。
2. **真机只验到"窗口能开能关"**: 冒烟脚本跑过三个窗口的建立、关闭、重开(见上),
   但没有真实 GPU 采样数据, 也没做鼠标交互(双击行进详情、右键"永不迁移"、设置窗保存)。
   接管后请在装了独显+核显的机器上手动过一遍这几条。
3. `_ui_post` 在线程死掉后会重开新线程、也就有了第二个解释器 —— 目前宿主没人 destroy,
   现实里到不了; 真要收严格, 应该线程一退出就置 `_ui_root=None` 并拒绝再建窗。
4. `.tmptest/` 是沙箱专用目录(已 gitignore): 里面除了 runner 和 pystray/PIL 替身,
   还有本轮留下的冒烟脚本(`smoke_ui.py` / `smoke_detail.py`)和几个 mkdtemp 建的
   0700 目录(沙箱删不掉)。要彻底清就 `rmdir /s /q .tmptest`, 但那样跑测试要按
   第 3 节重写 runner。

---

# 第四轮(打包成可执行文件 + 实机运行, 未提交)

这一轮是"打包 + 真机跑", 不是逻辑修改; 但对上一轮的结论有两处补充/修正。

## 1. 打包(PyInstaller 6.20 / Python 3.14.3)

- 命令(README 已同步更新, 比原来多了 `--add-data "app.ico;."`):
  `python -m PyInstaller --noconsole --onefile --name GPUMigrate --paths .
   --collect-all pystray --collect-all PIL --add-data "app.ico;." gpu_monitor.py`
- 产物: `dist/GPUMigrate.exe`(30.6MB, 一体版), 已同步覆盖仓库根目录那份旧的
  32MB 构建(README 提过"旧构建跑的是几周前的代码", 这下同步到当前源码)。
- **一体版在本沙箱跑不起来, 不是打包问题**: 引导器报
  `Failed to extract VCRUNTIME140.dll: failed to open target file! fopen: Permission denied`。
  最小 hello-world 一体 exe 同样报错, 所以是环境禁止往临时目录解包;
  改 `TMP`/`TEMP` 环境变量无效, 加 `--runtime-tmpdir` 也只建出空目录仍写不进文件。
- 为了把"冻结后的应用逻辑"验掉, 另打了 `--onedir` 版(`dist/GPUMigrate-onedir/`,
  5.9MB exe + _internal 共 70.8MB), **实测通过**: `--status` rc=0(31 条注册表),
  `--list` rc=0(3 块显卡), 托盘模式下 Web 面板 200 / 3775 字节。
  所以应用侧的冻结适配(`_DIR`/`_MEIPASS`)是对的, 一体版在正常机器上可用。
- 冻结后 `_DIR` = exe 所在目录, 日志/配置/历史都写在那儿, 不污染仓库 —— 已在 onedir
  运行时确认(`gpu_monitor.log` / `tray_debug.log` / `history.jsonl` 出现在 exe 旁)。

## 2. 实机运行(源码态)

- `--list` / `--once` / `--status`(31 条已迁移)全部正常; 真机 3 块显卡
  (RTX 4060 Ti / WARP / UHD 630), 48 个进程行。
- `--tray` 起过 3 次: 8 线程, 监控/Web 面板/电源策略都正常, stderr 空, 不生成
  `tray_error.log`。
- Web 面板: HTTP 200, 采样后 3775 字节含表格与显卡名, 1 秒自动刷新。
- 单实例: 已有时再启动, 第二个实例打印"已有 GPU 监控实例在运行，本次退出。"并 rc=0。
- **`停止GPU监控.bat` 在沙箱内无法验证**: 它内部走 `Get-CimInstance`, 本环境返回
  PermissionDenied(拒绝访问), 拿不到进程列表。`启动GPU监控.bat` 反而能启动成功
  (那条判定失败时 errorlevel 为 0, 会继续往下启动)。这两条请在有 CIM 权限的
  真实桌面环境复核。
- **托盘 GUI 没能用真键鼠验证**: 非交互会话里 `SendKeys` 报错、也无法点托盘图标。
  但 tk/Tcl 那条路径已由 `smoke_ui.py` 用真 tkinter 覆盖(见第三轮)。

---

# 第五轮(增加准确性, 未提交)

接着第四轮往下, 本轮只做"读出来的数要对得上", 不动任何功能语义。

## 1. 主项: --list 里成片的 "?" 是 OpenProcess 被拒, 不是进程不存在

实机 `--list` 一次采样 39 个进程, 10 个名字解析失败(约 26%), 全是
`OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` 返回 0 + `GetLastError()==5`
(`ERROR_ACCESS_DENIED`): pid 4 System / 252 WUDFHost / 1260 dwm / 1376 csrss /
6696 IntelCpHDCPSvc / 8572 sunshine / 10572 GameViewerServer / 15136 与 12368
nvcontainer / 27440 vmwp / 32972 ToDesk。

改法(`gpu_monitor.py` 进程信息一节):

- 新增 `_PROCESSENTRY32W` + `_process_names_snapshot()`: `CreateToolhelp32Snapshot
  (TH32CS_SNAPPROCESS)` + `Process32FirstW/NextW` 枚举全系统, 带 2 秒 TTL 缓存。
  快照只要枚举权限, 上面那些进程全都列得出来(实机 500+ 条, 一个不漏)。
- `pid_to_name` 两条腿: OpenProcess 那条拿得到完整路径; 被拒时退回快照,
  返回 `(进程名, None)`。两者都不成才返回 None。
- **踩坑记录 —— argtypes 必须显式声明**: 不写 `argtypes` 时 ctypes 按 `c_int`
  传句柄, `Process32NextW` 会以 `ERROR_BAD_LENGTH(24)` 中止, 只吐得出一个进程。
  现象极具误导性(看着像"系统里只有一个进程"), 排查时记得先加原型。
- `szExeFile` 下标取出来是独立 `str`, 不会因为结构体复用而互相覆盖。
- 坏句柄判据说一下: restype 用 `c_void_p`, `INVALID_HANDLE_VALUE` 按无符号回来
  是 `0xFFFFFFFFFFFFFFFF`, 而测试替身可能回 `-1`; 两种都不等于彼此, 所以判据写成
  `not handle or not 0 < handle <= 0xFFFFFFFF`。

**`full` 从此可能是 None, 四个调用点全部收尾**(漏一个就是 AttributeError
或者把首选项写进注册表键的默认值里去):

| 位置 | 处置 |
|---|---|
| `select_hot_candidates` | `if not info or not info[1]: continue` —— 没有路径写不了注册表, 当不了候选 |
| `advance_power_saver` | 本轮跳过但**不作废** streak(那是"进程没了"的语义, 这里只是暂时看不见) |
| 游戏模式 (`cmd_monitor`) | `if full2 and ...` 才读写首选项; 时长统计照记 |
| `gpu_tray._refresh_tree` | `info[1] and self._pref_of(...)`, 别拿 None 当注册表键 |
| `gpu_tray._detail_build` | 完整路径显示"未知 (需要管理员权限)", 两个按钮改提示不静默空转 |

## 2. 三个界面的读数口径对齐

- 托盘面板 `gpu_tray.py:_refresh_tree` 的 GPU%/核显%/独显% 从 `.0f` 改 `.1f`,
  和 `--list` / Web 面板的 `%.1f` 一致(原来 0.4% 会被显示成 0%)。
- `hot_reason` 的显存文案从"核显专用显存"改成"核显显存(专用+共享)", 并提精度到
  `.1f`。**口径问题不是本轮引入的**: `git show 80c671c` 之前 `mem_by_pid` 就是
  Shared+Dedicated 合计, 文案一直写"专用"。没有反改成"只算 dedicated"是因为
  核显的专用显存只是 BIOS 划的那一小块(常见 128MB), 只认它 `vram_threshold_mb`
  默认 1024 永远打不中, 这条规则会形同虚设。README / `DEFAULT_CONFIG` 注释同步。
- Web 面板 `panel_html` 的行序原来按 `util_by_pid`(核显%)排, 改成和 `--list`
  一样的 `max(GPU%, 专用MB, 共享MB)`。原来排最前面的行可能顶着个位数的 GPU%。

## 3. 验证

- `python -X utf8 .tmptest/run_unittest.py` → **Ran 168 tests OK**(上一轮 154)。
- 非空性: 10 条"只回退一处"探针全绿通过(`.tmptest/probe.py <编号>`,
  编号 1~9 加 `.tmptest/probe10.py`)。**这一轮就抓到过一条空断言**: 面板
  "无路径不查注册表"那条最初仍全绿 —— `TrayPanelRows._refresh` 会把
  `_pref_of` 覆盖成空操作, 等于什么都没测; 已改成绕开 `_refresh` 自己搭树。
  另一条 Web 行序的用例最初也设计错了(拿专用显存区分, 两种口径结论一致),
  换成共享显存才真正有区分度。
- 实机: `python -X utf8 gpu_monitor.py --list` 40 行**再无一个 "?"**;
  `--once` / `--status`(32 条)正常。
- 真机跑 4 轮 `cmd_monitor`(真实 PDH 采样, 注册表写操作在脚本里换成记录器,
  未动用户机器): 面板 39 行, 行序与 `--list` 逐行一致(23416MB/8612/1256/405/
  397/106/99/91...), 零注册表写入。
- 冻结产物: onedir 版 `--status` rc=0 / `--list` 38 行 0 个 "?"(与源码态一致);
  桌面快捷方式 `GPU 监控.lnk` 指向 `pythonw.exe <仓库>\gpu_monitor.py --tray`,
  跑的是源码, 不用跟着 exe 重建。
- 行宽复检: gpu_monitor.py 14 处 / gpu_tray.py 7 处 / tests 3 处, 与基线一致。

## 4. 还剩什么

1. **提交**: 现在五批改动叠在一起(`gpu_monitor.py` / `gpu_tray.py` / tests /
   README / 两个 bat), 建议按轮次拆提交; `config.json` / `history.jsonl` /
   `report.csv` / `panel_ui.json` 是运行期产物, 别混进去。
2. **一体版 exe 已重打**: 仓库根 `GPUMigrate.exe` 换成本轮构建(SHA256 前 16 位
   `7276f8cbb4a5e9e3`, 32132230 字节); `dist/GPUMigrate-onedir/` 也换了并实测
   通过(`--status` rc=0 32 条、`--list` 38 行 0 个 "?")。**只剩
   `dist/GPUMigrate.exe` 没换**: 20:28 有两个上一轮冒烟遗留下来的
   `GPUMigrate.exe` 进程(pid 1604 / 5724, 路径正是 dist 下那个)占着文件,
   PyInstaller 删不掉。这两个不是开机自启起的(自启走 `pythonw.exe` 跑源码),
   关掉它们再覆盖即可, 或者自己重打一次。一体版在本沙箱依旧起不来
   (VCRUNTIME140 解包被拒, 见第四轮), 所以只验到 onedir。
3. 快照只有名字没有路径, 所以受保护进程依旧不能自动迁移 —— 这是权限边界,
   不是 bug。真要覆盖得开 SeDebugPrivilege 并以管理员身份运行, 没做。

# 第六轮(界面层与可用性收尾 + 补齐新功能测试, 未提交)

接着第五轮往下。本轮定位是"界面层": 把上一轮做出来的面板新控件用测试
锁住, 顺手清掉一处重复配色。**没有动任何迁移判定语义。**

## 1. 本轮改了什么

### gpu_monitor.py(只补测试, 逻辑未动)
- `heat_class` / `panel_summary` / `_html_escape` / `panel_html` 的 24 条
  回归测试, 见下面第 3 节。
- 托盘图标配色收口: 新增 `gpu_tray._icon_rgba(cls)`, `_make_icon_image`
  不再自己存一套 `(76,175,80)/(255,152,0)/(244,67,54)` 硬编码 RGB,
  改成 `_icon_rgba(heat_class(pct))`。原来表格/汇总卡走 `_HEAT_COLOR` 的
  十六进制色(`#5ad07a` 等), 图标走另一套 RGB, 同一个数在托盘和面板里
  会显出两种绿色。**阈值分档没变**(还是 30/60), 只是色值并成一个来源。

### tests/test_gpu_monitor.py(168 → 192 用例)
新增 6 个测试类:

| 类 | 锁住的东西 |
|---|---|
| `HeatClass` | 三档边界值; `_icon_rgba` 解十六进制色; 图标真的用表格那一套色 |
| `PanelSummaryCards` | 汇总卡六项; 缺键/值为 None 不炸; 占用取最大不求和; 显存跨进程求和; 进程数取五张表的并集; 与托盘 tooltip 同数 |
| `TrayFilterBox` | `_matches_filter`/`_set_filter`: 空词全显、子串、大小写与空白、PID 可搜、`None` 不炸 |
| `TrayPanelChrome` | 汇总卡被 `panel_summary` 填上并上色; 筛选生效 + `显示 N/M 行`; 行带热度 tag |
| `SparklineThreshold` | 阈值线只有一条且是虚线; 先铺填充再画线(z 序); 无阈值就只画折线; 只有一个点时只出提示文字 |
| `PanelHtmlProcessNames` | 进程名在第一列; 缺名字退化成 `pid N`; `<b>&` 这类名字被转义; 汇总卡 / 筛选框 / localStorage 都在 |

### README.md
新增"实时面板"小节(汇总卡 / 进程表 / 筛选框 / 趋势图 / Web 面板对应物),
测试用例数 168 → 192。

## 2. 非空性探针(本轮自己踩到的两个坑)

`.tmptest/probe_r6.py all` 11 条全中, 每条都是"只回退这一处 → 对应测试变红":

| 还原的行为 | 变红的测试 |
|---|---|
| 图标改回自己那套 RGB | `HeatClass.test_the_icon_paints_with_the_shared_palette` |
| 去掉趋势图阈值线 | `SparklineThreshold.test_the_threshold_line_sits_under_the_trend` |
| 汇总卡占用改成求和 | `PanelSummaryCards.test_occupancy_..._not_the_sum` + `test_the_cards_agree_with_the_tray_tooltip` |
| GPU 进程数只看 util | `PanelSummaryCards.test_the_process_count_is_the_union_of_all_maps` |
| Web 面板进程名不转义 / 不显示 | `PanelHtmlProcessNames.test_a_name_that_looks_like_markup_stays_inert` 等 |
| 去掉 Web 汇总卡 | `PanelHtmlProcessNames.test_the_summary_cards_and_the_filter_box_are_there` |
| 托盘筛选不生效 / 汇总卡不更新 / 行无 tag / 提示去掉 | `TrayPanelChrome` 三条对应用例 |

**坑一: 一个案例里多处替换会互相覆盖。** 探针第一版每个案例调多次
`put()`, 而 `put()` 每次都从原件重新生成文本, 于是同一案例里后一处替换把
前一处冲掉了 —— 案例"去掉阈值线"因此全绿, 看着像测试是空的。改成在
当前文本上按顺序叠加才行。

**坑二: 等长改写 + 秒级 mtime = 吃到陈旧字节码。** `max(` → `sum(` 长度
一样, pyc 只按"秒级 mtime + 源文件大小"判新鲜度, 而探针在同一秒内写完
就起子进程, 子进程直接用了上一份 `__pycache__/*.pyc`, 变异等于没发生。
表现为"测试全绿但代码明明改了"。修法: 每次写完删掉该模块的
`__pycache__/<mod>.*.pyc`, 并给子进程加 `PYTHONDONTWRITEBYTECODE=1`。
**以后再写这类变异探针, 这两件事是必做的。**

## 3. 验证

- `python -X utf8 .tmptest/run_unittest.py` → **Ran 192 tests OK**(上一轮 168)。
- 非空性: `.tmptest/probe_r6.py all` 11/11 命中(见上)。
- 真 tkinter 冒烟 `.tmptest/smoke_panel2.py` 通过: 汇总卡 12 项文字、
  4 行数据、热度配色 dict、趋势图 5 个图元、`edge`/`300` 筛选与
  `显示 1/4 行` 提示都符合预期。(脚本注释里"期望 3 行"是旧的, 现行
  规则 `gpu_all<0.1 and de<1 and sh<1` 下 photoshop 的 0.4% 会显示, 不是 bug。)
- 行宽复检: gpu_monitor.py 14 处 / gpu_tray.py 7 处 / tests 3 处, 与基线一致,
  本轮没新增超宽行。
- **产物已重打**(PyInstaller 6.20 / Python 3.14.3, 构建与同步脚本留在
  `.tmptest/build_onefile.py` / `build_onedir.py` / `sync_onefile.py` /
  `sync_onedir.py`):
  - 一体版: `.tmptest/out-onefile/GPUMigrate.exe` → 仓库根 `GPUMigrate.exe`
    (已同步, SHA256 前 16 位 `a4005b6a54cde906`)。
    **本轮它在沙箱里能跑了**(前两轮报的 `Failed to extract VCRUNTIME140.dll:
    Permission denied` 未复现): `--status` rc=0 打印 32 条已迁移程序,
    `--list` rc=0 46 行 0 个 `?`。
  - onedir: `.tmptest/out-onedir/GPUMigrate-onedir` → `dist/GPUMigrate-onedir/`
    (整目录覆盖)。同样 `--status` rc=0 / `--list` 46 行 0 个 `?`。
  - 包完整性: onedir `_internal/` 里有 `PIL/`(含 `_imaging.cp314-win_amd64.pyd`)
    与 `pystray/`; 一体版归档里能数到 96 条 `PIL.*` 条目、`_imaging.cp314`
    和 `app.ico`。
  - **没验到的一格**: 托盘 GUI 路径。单例互斥体还被上一轮遗留的两个
    `dist/GPUMigrate.exe` 进程(1604/5724)占着, `--tray` 会在
    `import gpu_tray` 之前就退出, 所以只验到"进程起得来 + 命令行路径通"。

## 4. 还剩什么

1. **提交**: 六批改动叠在一起(见第五轮第 4 节), 建议按轮次拆;
   `config.json` / `history.jsonl` / `report.csv` / `panel_ui.json` /
   `gpu_monitor.log` / `tray_*.log` 是运行期产物, 别混进去。
2. `dist/GPUMigrate.exe` 仍被 pid 1604 / 5724 占着(第五轮记录的老问题),
   **没有用户确认不要杀**。
3. 界面还可以继续做的(都不阻塞): Web 面板的筛选是纯前端, 趋势图只有托盘版有;
   托盘面板没有"置顶开关"记忆; 进程表 30 行截断时没有"还有 N 行"提示。

## 5. 顺手对齐的环境问题(本轮新发现, 都有实测)

README 第四/五轮写着"启动文件夹快捷方式指向 `pythonw.exe <仓库>\gpu_monitor.py
--tray`", 但实测那个 `GPU监控自启.lnk` 指的是仓库根的 `GPUMigrate.exe` —— 正好是
README 里描述的"界面跑的是几周前旧构建"那个坑。本轮把它改回指向源码, 让文档和现实
一致; 刚重打的 exe 因此也不再是开机跑的那一份(它的去处是分发/给没有 Python 的机器)。

另外两件:

- 桌面(`D:\Users`)上原本**没有** GPU 快捷方式, 已新建 `GPU 监控.lnk`,
  同样指向 `pythonw.exe <仓库>\gpu_monitor.py --tray`, 图标用仓库里的 `app.ico`。
- 启动文件夹里还有一个名字乱码、**目标为空**的快捷方式
  (`GPU鐩戞帶鑷\ue044惎.lnk`, 名字里带私用区字符 U+E044, 手打不出来,
  只能从 `os.listdir` 现取)。那种快捷方式点了什么都不会发生, 已删。
- 单例互斥体现在还被 pid 1604 / 5724 占着(20:28 起的, 上一轮冒烟遗留),
  所以 `--tray` 在本沙箱里只会走到"已有实例"就退出, 托盘 GUI 路径依旧没验到。

# 第七轮(采样准确性俯验 + 三处修补, 未提交)

本轮不做新功能。一半时间花在"确认采样算法对不对"
上（结论性的，见第 1 节），另一半改三处硬缺陷。

## 1. 采样准确性伸验（结论，别再追）

- **typeperf 不能当基准**。微软自带的 `typeperf` 在这台机器上
  整个输出 681 列几乎全 0（最大 0.44%），正是 `gpu_monitor.py`
  注释里记录的"formatted rate 恒返回 0"。所以只能换别的办法。
- **分母己经过核对**：实测 `SecondValue` 差值 = 10,174,182（≈1 秒
  = 10,000,000 个 100ns），说明 `n/d` 百分比口径本身对。
- **量化了 `0.5 < v` 这道门槛的代价**：真实一窗内 633 个引擎
  完全空闲（n 精确为 0），但**7 个引擎有 0.01%~0.41% 的真实活动**，
  被这道门槛全部抹成 0.0%（任务管理器会显示 0.4%）。
  工具脚本：`.tmptest/cutoff_probe.py` / `cutoff2.py` / `check_denom.py`。

## 2. 修掉的三处（都有测试，且做了只回退这一处的非空性验证）

| # | 缺陷 | 改法 | 锁它的测试 |
|---|---|---|---|
| A | `cmd_list` 有 KeyError 崩溃风险且零测试覆盖。它连着调两次 `collect_gpu_sample()`（各约1 秒），第二次采样里冒出的新 LUID 不在第一次的 `gpus` 里，老代码 `gpus[luid]["kind"]` 直接下标——浏览器/游戏随时起进程，`--list` 就崩。而 `--list` 正是用户"看看谁在占显卡"时最常用的入口 | 第二次采样后把 `k[0] not in gpus` 的新 LUID 拼进去，并且打印"采样期间新出现"，避免占用表里出现一个列表里没列出来的显卡；下标改成 `gpus.get(luid, {})` 兜底 | `ListCommand` 2 个用例 |
| B | `0.5% 门槛` 把 0.01%~0.41% 的真实活动整块抹成 0（见第 1 节实测） | 放宽到 `0.01 < v`。对迁移判定无影响（阈值默认 20~40%），但面板上不会再"看着没人用其实有人在用" | `SampleEngineFold.test_a_tenth_of_a_percent_is_not_wiped_out` |
| C | Web 面板没趋势图；而托盘面板那张趋势图还被 `history.jsonl` 开关卡住——关掉落盘，界面上永远只剩"每 10 秒一点"那句提示 | 新增 `sparkline_html(hist, threshold)`（内联 SVG，与 `_draw_sparkline` 同 viewBox/同配色/同阈值虚线，必须写 `vector-effect:non-scaling-stroke`，否则 viewBox 横向拉伸会把线宽拉异形）；`hist_points` 换成独立的循环队列，只把 `append_history` 留在 `cfg["history"]` 判断里；`_LAST_SNAPSHOT` 与 `on_sample` 较屎都带 `history` | `SparklineHtml` 4 个用例 + `SnapshotContract` 2 个 |
| D | 托盘进程表 30 行截断时静默吃掉多余行，看起来就像"机器上就这些进程在占显卡" | 提示加"还有 N 行未显示" | `TrayPanelChrome.test_hidden_rows_are_counted_in_the_hint` |

`SnapshotContract.test_on_sample_snapshot_keys` 断的是精确键集合，所以必须把
`"history"` 加进去，否则必红——这就是该测试该有的作用。

## 3. 非空性探针

`.tmptest/probe_r7.py all` 8/8 命中，每条都是"只回退这一处→对应测试变红"。
第一版探针有两条绿的，原因归结成一句：**断言放在了错的层**。
原先只断片段字在不在，而 CSS 本身正文一直在（只是把 SVG 从面板里换掉）；
第二条只拉掉了两处 `vector-effect` 中的一处，另一处还在。
改成"断 SVG 真的插进面板了 + 两处一起拿掉"之后才红。下次写探针，
先问一句"我这个断言能否被别的子条目偷好"。

另外第 4 条第一次写成了语法错误（替换字符串少了一个引号）——
红了，但不是因为断言。探针的落点要看最后一行是
`FAILED (failures=1)` 还是 `FAILED (errors=1)`（或者 `SyntaxError`），不能只看返回码。

## 4. 验证

- `python -X utf8 .tmptest/run_unittest.py` → **Ran 202 tests OK**（上轮 192，本轮 +10）。
- 非空性探针 8/8（见上）。
- 真 tkinter 冒烟 `.tmptest/smoke_panel2.py` 通过：汇总卡 12 项、4 行数据、
  热度配色、趋势图 5 个图元、`edge`/`300` 筛选与提示都对。
  （脚本注释里"期望 3 行"是旧的，现行规则下 photoshop 的 0.4% 会显示，不是 bug；
  反而是第 B 项改动直接带来的改善。）
- Web 面板冒烟 `.tmptest/smoke_spark.py`：满载/ 空历史/ 单点/ 缺`history`键四种
  情形都不抛异常，SVG 的 polygon/polyline/thr/text 四类图元齐全，
  空历史只出"核显占用趋势 (每 10 秒一点)"。
- 行宽复检：gpu_monitor.py 14 处 / gpu_tray.py 7 处 / tests 3 处，与基线一致，未新增。
- **没做**：lint / typecheck（仓库没配也没装）、浏览器人工看图、托盘 GUI 实测
  （单例互斥体依旧被 pid 1604 / 5724 占着，`--tray` 只能走到"已有实例"
  就退出）。

## 5. 下一步（按序）

1. **提交**：现在已经七批叠在一起。建议按轮次拆；
   `config.json` / `history.jsonl` / `report.csv` / `panel_ui.json` /
   `gpu_monitor.log` / `tray_*.log` 是运行期产物，别混进去。
2. **重打两个 exe**（本轮改了 `gpu_tray.py` 与 `gpu_monitor.py`，
   包里的是第六轮代码）并同步到仓库根 / `dist/`＋实测
   `--status` / `--list`。构建与同步脚本在
   `.tmptest/build_onefile.py` / `build_onedir.py` / `sync_onefile.py` / `sync_onedir.py`，
   `--add-data` 必须传绝对路径。
3. `dist/GPUMigrate.exe` 依旧被 pid 1604 / 5724 占着，**没有用户确认不要杀**。
4. 界面还可以继续做（都不阻塞）：
   托盘趋势图与 SVG 一样把阈值线放到填充之上
   （现在两边都是"先画阈值线再扑底色"，折线翻到阈值线上方时，
   那一小段红线会被填充盖住，只不过两边一致，不影响判读）；
   托盘面板没有"置顶开关"记忆；Web 面板的筛选还是纯前端。

# 第八轮(跟开源项目的平面规范对齐: 绘图块的块层/文字/状态记忆, 未提交)

本轮没新功能，专门收第七轮自己留下的四个毛病：**SVG 文字被横向拉伸、阈值线被填充盖掉、
置顶开关不记忆、探针断言放错层**。前两个是我上轮写的新代码里的，不是历史遗留。

## 1. 修的四处

| # | 问题 | 改法 | 锁它的测试 |
|---|---|---|---|
| E | `sparkline_html` 把文字画成 SVG `<text>`。viewBox 被 `width:100%` 横向拉伸(高度固定 46, 所以纵向一像素对一像素, y 可以直接当像素用), 结果"迁移阈值 40%"在宽面板上漂成一坨腻。平面布局里这类文字本该跟图形分开 | 拆成 `<div class=sparkwrap>` + SVG(只画几何) + 两个绝对定位的 `<span>`。`vector-effect:non-scaling-stroke` 仍然要写(线宽不会被拉异形) | `SparklineHtml.test_labels_live_outside_the_stretched_viewbox` |
| F | 网页和托盘的阈值线都画在填充之**前**。折线翻到阈值上方时, 先画的那半截红线被 `#1c3a28` 整块盖掉, 阈值就时有时无 | 两边统一改成"先填充 → 再阈值线 → 最后折线"。托盘那条旧用例 `SparklineThreshold.test_the_threshold_line_sits_under_the_trend` 断的就是老顺序, 已按新顺序改掉(名字保留: 阈值线仍在折线下面, 只不再被填充盖) | `SparklineHtml.test_the_threshold_line_sits_above_the_fill` + 托盘那条 |
| G | 第六轮留下的"托盘面板没有置顶开关记忆" | 筛选条右侧加一个 `置顶: 开/关` 按钮；默认开(这窗口就是拿来盯着看的)，但能关(全屏游戏时置顶小窗挡画面)；`on_close` 把状态写进 `panel_ui.json` | `TraySingleInterpreter.test_the_panel_remembers_the_topmost_switch` |
| H | 探针自己的问题: 第七轮有两条绿的, 因为断言放在了错的层(断 CSS 片段在不在, 而 CSS 本身正文一直在) | 改成断"SVG 真的插进面板了"; 另一条只拉掉两处 `vector-effect` 中的一处, 现在两处一起拿。探针还加了一个防护: 落点是 `SyntaxError`/`IndentationError` 时算 `BROKEN` 不算 `RED` | `.tmptest/probe_r8.py` |

## 2. 验证

- `python -X utf8 .tmptest/run_unittest.py` → **Ran 205 tests OK**(上轮 202, 本轮 +3)。
- 非空性探针 `.tmptest/probe_r8.py all` → **11/11 全部变红**, 且落点都是
  `FAILED (failures=/errors=)` 而不是语法错误。原 `probe_r7.py` 已删——它的锚点随本轮改动失效,
  再留着会报错误的锚点，所以并成 `probe_r8.py`。
- 真 tkinter 冒烟 `.tmptest/smoke_panel3.py`（新建）通过: 读取上次的
  `topmost: false` → 面板不强行置顶; 点一下开关 → `attributes` 翻成 1、按钮文字变"置顶: 开";
  走真的 `WM_DELETE_WINDOW` 回调 → `panel_ui.json` 落盘 `topmost: true`。
  负责回调得跟 `protocol()` 返回的 Tcl 命令名字符串走 `tk.call(...)`, 直接 `()` 会类型错。
- `.tmptest/smoke_panel2.py` / `smoke_spark.py` 仍通过。
- 行宽复检: gpu_monitor.py 14 / gpu_tray.py 7 / tests 3, 与基线一致。
- **没做**: lint / typecheck（仓库没配也没装）、浏览器人工看图。
- 收尾时又清掉 13 处错别字(前几轮用 `\uXXXX` 写中文留下的): 前 7 处
  `HANDOFF.md` 5 / `tests/test_gpu_monitor.py` 2, 见 `scan_odd2.py`;
  后 6 处是罕见字, 靠 `scan_rare.py`(按"全文只出现一次的汉字"筛)才发现:
  六个都是常用字被写坏成的形近乱码字
  (水/口/竹/土/忄 旁那类), 已按正确写法改回。
  复扫两个脚本都应零输出。

## 3. 下一步

1. **提交**: 八批叠一起, 建议按轮拆; 运行期产物别混进去。
   (仍未提交; 用户没点头就不动 git。)
2. ~~重打两个 exe~~ **已完成**（第九步收尾时做）:
   一体版 `GPUMigrate.exe`(仓库根) 与 `dist/GPUMigrate-onedir/`
   都用本轮源码重打并同步, 构建中间产物 (`build-onefile`/`build-onedir`/
   `out-*`) 已清掉。实测 `--status` rc=0; `--list` rc=0、48 行、0 个问号。
   (附带坑: onefile 是 WINDOWS 子系统, PowerShell 直接调抓不到输出,
   要看输出得 `cmd /c "... > file"` 再按 cp936 读。)
   包里装的是不是本轮源码, 是拿 `PyInstaller.archive.readers.CArchiveReader`
   开 CArchive + `pyimod01_archive.ZlibArchiveReader` 开 PYZ, 把 `gpu_monitor`
   / `gpu_tray` 的 code object 抠出来, 跟 `compile(仓库源码)` 做递归指纹比对
   (co_name/co_names/co_consts/co_code/co_flags/co_varnames/co_argcount) ——
   两个 exe 四个模块全部一致, 见 `.tmptest/verify_exe3.py`。
   `verify_imports.py` 另外确认 PIL/pystray/gpu_monitor/gpu_tray 四个非标准库
   import 在包里都有, tkinter 也带齐了(`_tkinter.pyd` + tcl8/tk8)。
3. **还卡着一件事**: pid 1604 / 5724 又出现了（`dist\GPUMigrate.exe`,
   9-25 18:43 的旧构建), 单例互斥体被它占着, 所以新 exe 的 `--tray`
   会按设计直接 rc=0 退出, 没能真机点亮托盘。`dist\GPUMigrate.exe`
   本身也是旧文件、正在运行所以覆盖不掉。要不要停掉这两个进程、
   顺手把 `dist\GPUMigrate.exe` 也刷新, 等用户点头再做。
   另外 `\GPUMigrateWatchdog` 计划任务指向
   `C:\Users\qm081\.zcode\workspace\default\gpu-migrate\GPUMigrate.exe`,
   那条路径已不存在(Last Result 0x80070002), 自启其实是坏的。
4. 界面还可以继续做(不阻塞): 托盘面板无"置顶"以外的窗口选项记忆；
   Web 面板还是整页刷新 + 前端筛选(没用 fetch 局部更新)。

# 第九轮(Web 面板对齐 + 打包自证, 未提交)

本轮两件事: **筛出口径对齐** 和 **证明 exe 里装的就是当前源码**。

## 1. Web 面板的三处改动 (`gpu_monitor.py`)

第 1.5 件是顺手带出来的口径统一: `--list` 里那种 `0.0% / 0MB / 0MB`
的噪声行, 托盘面板早就滤掉了, Web 面板还照样列。现在抽成
`gpu_monitor.row_is_noise(gpu_pct, ded_mb, shm_mb)` 一个出处,
托盘 `_refresh_tree` 和 `panel_html` 都调它 (`row_is_noise` 也加进了
`gpu_tray` 的 import 清单)。`--list` 保持全量不动 —— CLI 是诊断用,
看板才该精简。测试 `NoiseRowFilter` + `TrayPanelChrome.test_all_zero_rows_never_reach_the_table`。

| # | 问题 | 改法 | 锁它的测试 |
|---|---|---|---|
| I | 筛选拿整行 `textContent` 匹配。输入 `12` 会把"独显% 12.3"的行也留下 —— 托盘 `_matches_filter` 只认进程名和 PID, 两边对不上, 还让人以为那些行真的在 12% | JS 改成只读 `td.n` 和 `td.pid` 两个单元格, 大小写不敏感 + trim | `WebPanelFilter.test_the_filter_no_longer_matches_whole_rows` 等 |
| J | 没有 `viewport`, 手机浏览器按 980px 版面渲染再整体缩小, 六张卡+七列表格缩成蚂蚁字 | head 里加 `width=device-width,initial-scale=1`; 表格外包 `.tw{overflow-x:auto}` | `WebPanelResponsive` 两条 |
| K | 页面每秒整页刷新, 打字时光标被刷没 | 刷新后若筛选框有值, `q.focus()` + `setSelectionRange(len,len)` | `test_the_filter_keeps_the_caret_across_the_second_refresh` |

JS 没法单测, 所以是**真跑**验证的: `.tmptest/export_panel.py` 把 `panel_html`
里的 `<script>` 抽成文件, `.tmptest/filter_check.js` 用 node + 最小 DOM 替身
(只需 `getElementById`/`querySelectorAll`/`style.display`) 跑 8 组输入,
断言可见行集合和 hint 文字 —— 全 PASS。`.tmptest/tray_filter_check.py`
把同样 8 组喂给 `gpu_tray.TrayApp._matches_filter`, 也是全 PASS,
两边口径从此有据可查。

## 2. exe 打包自证(`.tmptest/verify_exe3.py`)

不再靠"文件时间戳比源码新"就下结论:
`PyInstaller.archive.readers.CArchiveReader` 开 CArchive 取 `PYZ.pyz`,
`pyimod01_archive.ZlibArchiveReader` 开 PYZ 取 `gpu_monitor` / `gpu_tray`
的 code object, 跟 `compile(仓库源码)` 比递归指纹
(`co_consts`/`co_names`/`co_code`/`co_flags`/`co_varnames`/`co_argcount`)。
两个 exe 四个模块全部一致。`.tmptest/verify_imports.py` 另外确认
PIL / pystray / gpu_monitor / gpu_tray 在包里都有, tkinter 也齐。
(坑: `CArchiveReader.toc` 和 `ZlibArchiveReader.toc` 是 dict,
`[e[0] for e in toc]` 只会拿到名字首字母 —— 一开始就是这么误判"缺 gpu_tray"的,
实际没缺。)

## 3. Web 面板改成局部刷新(第十轮)

原来靠 `<meta http-equiv='refresh' content='1'>` 整页重载 —— 每秒一次灰屏、
滚动位置被弹回顶部、正在打字的光标被刷没。现在去掉 meta refresh, JS 用
`fetch(location.href)` 取整页, `DOMParser` 解析后只把
`policy / gpuinfo / cards / spark / rows / hint` 六个块的 innerHTML 换掉,
再调一次 `f()` 把当前筛选词重新套到新行上。没有 fetch 的老浏览器退回
`location.reload()`。`panel_html()` 的返回结构没动(只是给那六块加了 id),
服务端渲染和既有单测都不受影响。

验证: `.tmptest/pull_check.js`(node) 用替身 DOM + DOMParser + fetch,
先渲染第一份快照(筛选词 edge), 再 `pull()` 换成多一行的第二份快照,
断言只发一次请求、行数 2→3、筛选被重套成 2 行、hint 变成"显示 2 / 3 行"、
输入框对象没被换过。**ALL-PASS**。

## 4. 验证

- `python -X utf8 .tmptest/run_unittest.py` → **227 tests OK**(上轮 226, +1)。
- `probe_r8.py all` → 11/11 命中; `smoke_panel3.py`(真 tkinter 置顶) 通过;
  `smoke_panel4.py` 验筛选词/排序恢复; `smoke_panel5.py` 念趋势图角标;
  `smoke_panel6.py` 验窄窗口下六张汇总卡全部完整可见。
- **两个 exe 已重打并自证**: `GPUMigrate.exe`(一体) 与
  `dist/GPUMigrate-onedir/` 都用本轮源码重建, `verify_exe3.py` 四个模块
  指纹全部一致; `--list`/`--status` rc=0、0 个问号。构建产物已清理。
  (第十、十一轮之后源码又动过, 每一轮动完都要重打, 别偷懒。)

# 第十一轮(托盘面板的记忆项补齐, 未提交)

`panel_ui.json` 原来只记 geometry / topmost / 列宽, 筛选词和排序列一关就忘。
Web 面板的筛选词一直存在 localStorage 里 —— 同一个动作, 两个界面付出不一样
的代价, 这属于上一轮"口径对齐"没扫干净的尾巴。现在关窗时一起写 `filter`
和 `sort`, 开面板时在 **建 Entry 之前** 就把 `self.filter_text` 喂回去
(否则第一帧 `refresh()` 刷的是全量, 会先闪一下再筛)。读回来的排序列名
会过一遍白名单, `panel_ui.json` 是被外部手改的文件, 非法列名不能照单全收。

测试: `TraySingleInterpreter.test_the_panel_remembers_the_filter_and_the_sort_column`
(落盘 + `Entry` 的 `StringVar` 初值) 和
`test_a_garbage_saved_sort_falls_back_to_the_default`;
`.tmptest/smoke_panel4.py` 用真 tkinter 走完整恢复链路。

# 第十二轮(趋势图角标的时长文案, 未提交)

`sparkline_html` 的角标原来写 `近{len(pts)*10//60}分钟`, 刚启动只有 2~5 个点
(开机头 50 秒)时算出来是 **"近0分钟"** —— 图上明明有折线, 文字说没数据。
现在不足 60 秒按秒说(近20秒/近50秒), 满 60 秒才说分钟。
托盘那边 `_draw_sparkline` 画的是同样的角标吗? —— 不是, 托盘那张图没有文字
角标, 所以这只影响 Web 面板。测试 `SparklineHtml.test_the_span_label_counts_seconds_before_one_minute`。

# 第十三轮(趋势图的算法收成单一出处, 未提交)

第十二轮只修了 Web 面板的角标, 回头看 `gpu_tray._draw_sparkline` 里
**一模一样的那句**还在 —— `近{len(pts)*10//60}分钟`, 开机头 50 秒同样显示
"近0分钟"。两张图的刻度算法(`max(30, peak, 阈值*1.15)`)也是各写一份。
这种"同一个数字在两边算出两个样子"的跑偏, 靠人眼比对迟早漏, 所以这一轮
直接收口: `gpu_monitor.py` 新增 `spark_ymax()` / `spark_corner_text()`,
托盘和 Web 都改成调它们, `gpu_tray` 的 import 清单同步加进去。
测试 `SparklineHtml.test_the_tray_and_the_web_share_the_ymax_and_the_corner`
用 `assertIs` 钉住"两边是同一个函数对象", 以后谁再复制粘贴一份就会红。
`.tmptest/smoke_panel5.py` 用真 tkinter Canvas 念出画布上的文字,
确认托盘画的是"近30秒 峰值60.0%  当前60.0%"、阈值标签也还在。

# 第十四轮(卡片数与表格行数对不上, 未提交)

上一轮把噪声行从两张表里滤掉之后, 留下一个新矛盾: 汇总卡的 "GPU 进程"
报的是真 union 数(本机 40), 表格只剩非噪声行(34), 两个数摆在同一个窗口里,
用户会以为自己看漏了行。这一轮不改卡片语义(它就是"有多少进程碰过 GPU"),
改成**把差额说出来**: 托盘提示行尾部加 "(已隐藏 N 个 0.0% / 0MB)",
Web 面板在表格下面多一行 `id=hidden`, 并挂进局部刷新的 PARTS ——
不然每秒刷新时这行会停在旧数字上。判据收在
`gpu_monitor.hidden_noise_count(snap)`, 还是单一出处。

# 第十五轮(汇总卡在窄窗口下被裁切, 未提交)

症状: 把面板拉到 500px 左右, 顶部六张汇总卡右边两三张整体消失 —— 不是文字
被截断, 是 widget 直接被摆到视口外, 想看只能把窗口拉宽。

一开始的怀疑对象是 `grid_columnconfigure(uniform=...)` + `sticky="ew"`,
试了, **没用**: uniform 只负责分"多余"的空间, 每一列仍然有自己的请求宽度
下限(约 110px),六列合计 ~700px, 窗口比这小的时候后面的列就是放不下。

修法是把卡片容器里的列布局从 `grid` 换成 `pack(side="left", expand=True)`:
`fill="x"` + `expand=True` 告诉 Tcl "宽度按比例压缩", 而不是"保住请求宽度"。
窗口 500px 实测: 六张卡宽 74/74/84/76/76/60, 右缘 484, 全部完整可见。

`gpu_tray.py:_panel_build`, 卡的 `padx` 还是 `(0, 0 if i == last else 8)`
(最后一张不吃右边距), 其余没动。

## 1. 这次真正踩到的坑: `update_idletasks()` 量不出新布局

`smoke_panel6.py` 第一版报"窗口宽 500、卡片右缘 1024、不完整可见",
看着像改动没生效。其实**改动是对的, 量错了**: `root.geometry()` 之后只调
`update_idletasks()` 读到的子部件 `winfo_width()` 是上一次布局的陈旧值
(面板默认 1040 宽, 所以六张卡右缘正好是 1024)。

正确写法是 `root.geometry(...)` → `root.update()` → `time.sleep(0.4)` →
`root.update()`, 等 Tcl 把新布局真的算完再量。改完立刻复现出
"右缘 484 ≤ 500"。**以后凡是要量真实 widget 尺寸的脚本都照这个来**,
`update_idletasks()` 只保证几何请求被处理, 不保证窗口管理器已经换了尺寸。

## 2. 验证

- `python -X utf8 .tmptest/run_unittest.py` → **227 tests OK**(上轮 226, +1)。
- 新测试 `TraySingleInterpreter.test_summary_cards_shrink_together_in_a_narrow_window`:
  在替身 tkinter 下数 `Frame.pack` 的调用, 断言 `side="left"` 且
  `expand=True` 的正好等于 `panel_summary()` 的卡数(6)。做过反向验证:
  把 `expand=True` 摘掉, 这条立刻 `0 != 6` 变红。
- `.tmptest/smoke_panel6.py`(真 tkinter, 修正测量方法后) →
  窗口宽 500 / 卡片数 6 / 右缘 484 / 完整可见 True。
- `probe_r8.py all` → 11/11 命中; `smoke_panel3/4/5.py` 全通过;
  `scan_odd2.py` 零输出; 行宽基线 14/7/3 未变。


# 第十六轮(Web 面板空闲态: "空闲"行会被筛选词吃掉, 未提交)

现象: 一台没有任何进程占 GPU 的机器, 打开 Web 面板, 在筛选框里打任何
一个字 —— 整张表连同"空闲"两个字一起消失, 只剩一张白表格; 提示行写
"显示 0 / 1 行"。反过来不筛的时候它写"显示 1 / 1 行", 而机器上一个
进程都没在跑。托盘面板同一时刻报的是"显示 0/0 行"。

## 1. 根因

`_PANEL_SCRIPT` 的 `f()` 把"取不到 td.n 时的兜底"外提了:

    var nm=(r.querySelector('td.n')||{textContent:''}).textContent;

空闲态的服务端渲染是 `<tr><td colspan=7>空闲</td></tr>` ——
**一个格子, 且没有 class**。于是 `nm=''`、`id=''`; 只要有筛选词,
`''.indexOf(v)` 永远 -1, 这一行就被 `display:none`。分母又用的是
`rows.length`, 于是空态行还被算成"1 个进程"。

## 2. 改法

`f()` 改成先问"这行是不是进程行", 不是就原样留着、直接 return:

    var nc=r.querySelector('td.n');
    if(!nc){r.style.display='';return;}
    t++;

同时把 `t` 单独累加数据行数当分母, `rows.length` 不再出现在提示里。
`id` 也补了 `.toLowerCase()` —— 以前 PID 是字符串直接 indexOf, 大小写
虽然有影响但看不出来, 顺手对齐。

顺带两处文档级对齐: `<html>` 补 `lang='zh-CN'`, `<title>` 从
`GPU Monitor` 改成 `GPU 监控`(和 `<h2>` 一致, 也和托盘窗口标题同源)。

**没有动筛选口径本身** —— 还是只认进程名子串(大小写不敏感)和 PID。

## 3. 顺带清掉的重复代码

`WebPanelFilter` 里 `test_the_filter_reads_the_name_and_pid_cells`
写在类里**两遍**, 第二份把第一份整个盖掉(同名方法后定义者赢)。
也就是说从写下去那天起, 改这个用例等于没改。已删掉尾部那份。

## 4. 验证

- `python -X utf8 .tmptest/run_unittest.py` → **230 tests OK**(上轮 227, +3)。
- 新用例: `test_the_idle_row_survives_the_filter` /
  `test_the_hint_denominator_counts_process_rows_only` /
  `test_the_idle_snapshot_renders_one_row_without_a_name_cell`。
  反向验证: 把 `if(!nc){...}` 那行摘掉 → 立刻 FAIL(不只是显示错,
  真实浏览器里 `nc.textContent` 直接 TypeError, 整个筛选跟着停摆)。
- `.tmptest/probe_idle.js`(node + 替身 DOM, 跑空闲快照): 无筛选和有筛选
  都 PASS, 空态行始终可见, hint 都是"显示 0 / 0 行"。
- `.tmptest/pull_check.js`(node, 局部刷新链路): 仍 ALL-PASS, 有数据的
  路径没退化。
- `probe_r8.py all` → 11/11; `scan_odd2.py` 零输出; 行宽基线 14/7/3 未变。
- **写替身的教训**: 上一版替身 `querySelector` 不区分 `<td>` 的 class,
  一律把第 0 格当成 `td.n`, 于是替身"证明"空态行过滤得对 —— 假绿。
  替身必须和真 DOM 一个语义, 否则就是替身在替产品代码背锅。


# 第十七轮(趋势图画布尺寸: 常量声明了却没人用, 未提交)

第十三轮把趋势图的**算法**收成单一出处(`spark_ymax` /
`spark_corner_text`), 但**几何尺寸**漏了: `_SPARK_W, _SPARK_H = 300, 46`
在 `gpu_monitor.py:1110` 声明, 全文只有 `x_of` / `y_of` 两个闭包在用,
真正决定图样子大小的四处全是字面量:

| 位置 | 字面量 |
|---|---|
| viewBox | `viewBox='0 0 300 46'` |
| 填充多边形右下角 | `points='... 300 46 0 46'` |
| 阈值线右端 | `x2=300 y2=...` |
| CSS 的 height | `height:46px` (两处) |

改一次画布大小要同步改四处, 漏一处就是"阈值线画到框外"或"纵向被二次
缩放"这种看一眼看不出错的毛病 —— 正是第十三轮要消灭的那类问题。

## 1. 改法

三处 SVG 属性换成 `f"{_SPARK_W}"` / `f"{_SPARK_H}"`。CSS 里那两份 46px
**故意留字面量**: `_PANEL_STYLE` 是纯常量字符串, 塞 f-string 会让整段
都要重写; 改为在它上面加两行注释说明"必须等于 _SPARK_H", 并补一条测试
把这两边绑在一起(_SPARK_H 改了 CSS 没改, 测试就红)。

## 2. 验证

- `python -X utf8 .tmptest/run_unittest.py` → **232 tests OK**(上轮 230, +2)。
- 新测试 `SparklineGeometry`(2 条): SVG 必须引用常量; CSS 的
  `height` 必须等于 `_SPARK_H`。
- 反向验证有个**值得记下的坑**: 一开始直接把 viewBox 退成硬编码
  `300 46` 想验红, 结果测试照绿 —— 因为常量本身就是 `300, 46`,
  字面量和常量在数值上完全相同。真正有效的验法是**只动常量**
  (`_SPARK_W` 改成 320) 不动调用点: fill/line 立刻跟着变成 320, 而
  viewBox 停在 300, 测试报 `"viewBox='0 0 320 46'" not found`。
  也就是说这条测试钉住的是"引用关系", 不是"字面值", 别指望改值能验出来。
- `probe_r8.py 8` 的锚文本本来就是我这次改掉的那段, 一开始报 ANCHOR
  (锚点没命中), 更新后 `all` 回到 **11/11**。
- `pull_check.js` / `probe_idle.js` ALL-PASS; `smoke_panel3/4/5/6` 全过;
  `scan_odd2.py` 零输出; 行宽基线 14/7/3 未变。


# 第十八轮("筛选中没匹配" ≠ "没有进程占 GPU", 未提交)

症状: 机器上明明有进程在占 GPU, 在托盘面板筛选框里打一个不存在的名字,
表格里出现一行"（当前无进程占用 GPU）"。Web 面板更糟 —— 同样情况下
整张表是**空的**, 连这句话都没有, 只有提示行写着"显示 0 / 1 行"。

这是两件不同的事被混成了一句: 前者是"有进程, 但都被筛选词挡住了",
后者是"机器真的闲着"。混在一起的后果是用户先怀疑筛选坏了, 再怀疑
机器莫名其妙空了。

## 1. 改法

- `gpu_monitor.py` 新增单一出处 `empty_row_text(total)`
  (total>0 → "没有匹配的进程", total==0 → "（当前无进程占用 GPU）")。
  托盘和 Web 共用, 和 `row_is_noise` / `panel_summary` 一个路子。
- 托盘 `_refresh_tree` 的空态行改调它, 不再硬编码那句话。
- Web 的空态行从"服务端没行时才渲染"改成**常驻**, 由 JS 按
  "看得见的进程行数 n"切显隐和文案。

## 2. 这里踩了两个坑

**坑一: 显隐判据一开始写反了。** 第一版写的是 `t ? 'none' : ''`,
`t` 是进程行**总数**。有进程在跑时 t=1, 于是筛不中时进程行全被隐藏,
空态行还藏着 —— 整张表一个行都不剩, 比原来的 bug 更难看。正确的是
`n`(看得见的进程行数): n>0 藏空态行, n==0 才露出来。

**坑二: `pull_check.js` 读的 `panel.html` 是第十六轮之前的陈旧产物。**
`export_panel.py` 写 `panel.html` + `panel_script.js`,
`export_panel2.py` 写 `panel_next.html` —— 三个文件不在一个脚本里,
改完 `panel_html()` 只跑其中一个, 另一个就是旧的。所以第十六、十七轮
的 pull_check 其实一直在拿旧 HTML 测。本轮把三个文件按顺序重新生成,
立刻暴露出空态行常驻后行数 3 → 4, 断言当场失败。**以后改
`panel_html()` 必须 `export_panel.py` 和 `export_panel2.py` 一起跑。**

## 3. 验证

- `python -X utf8 .tmptest/run_unittest.py` → **235 tests OK**(上轮 232, +3)。
- 新测试类 `EmptyRowText`(3 条):  busy-but-no-match 不能报成空闲;
  空态行常驻; JS 用 n 而不是 t。反向验证: 把 `n?` 换回 `t?` 立刻 FAIL。
- 三个 node 探针全过: `probe_nomatch.js`(本轮新增, 专测"筛不中")、
  `pull_check.js`(局部刷新 + 空态行常驻后 4 行)、`probe_idle.js`(真空闲)。
  替身同步升级: `row.cells` 可写、`_cells` 改成 getter(之前是初始快照,
  JS 改的文案在断言里看不出来)。
- `probe_r8.py all` → 11/11; `smoke_panel3/4/5/6` 全过;
  `scan_odd2.py` 零输出; 行宽基线 14/7/3 未变。
# 第十九轮(`--list` 的列对齐, 以及前几轮遗留的小坑, 未提交)

`--list` 是"对着看"的入口: 竖着一列比谁高谁低。这一轮之前它有两个
printf 留下的毛病, 都是单看不出来、一竖着比就露馄的。

## 1. 症状一: 长进程名把后面所有列一起推走

`%-28s` 是**最小**宽度, 不截断。UWP 应用那种
`WindowsInternal.Composable.Shell.Experiences.TextInput.InputApp.exe`
四五十字符的名字一出现, 后面每一列都跟着右移, 数字列再也对不齐。

## 2. 症状二: 表头每一列都比数字列宽, 列线是斜的

`%-7s` 这类补空格是按**字符数**补的, 而 `核显%` / `专用MB` 是全角,
在等宽终端里占两列。`%-7s` 给"核显%"补 4 个空格, 看着补满了 7, 实际
占 6 列 —— 比对应的 `%6.1f%%` 宽一列。表头七列全比数据宽, 整张表
的竖线从左到右逐列外扩。

## 3. 改法

- `gpu_monitor.py` 新增四个按**显示宽度**干活的 helper(挨着放,
  `_gpu_card_line` 的 `%-42s` 没动):
  `_disp_len`(East Asian Width 算列宽)、`_fit_disp`(按列宽补空格,
  `right=True` 右对齐)、`_ellipsize`、`_ellipsize_disp`。
- `cmd_list()` 改成先收集 `rows` 再统一打印: 列宽跟着内容走,
  封顶 `_NAME_COL_MAX = 40`, 再长就截断加省略号。
- 表头列宽 6/7/7/8/8, 与数据的 `%5.1f%%` / `%6.1f%%` / `%6.1f%%` /
  `%8.0f` / `%8.0f` 一一对应, 用 `_fit_disp` 而不是 printf 拼。

## 4. 顺手带出的第三个坑: 列宽本身也得按显示宽度算

第 3 步只改了对齐方式, 列宽还是 `max(len(name))` 取的 —— 而 `len()`
对中文只数一半。中文进程名 20 个字: 字符数 20, 显示宽度 40。按 20
定的列宽, 名字列实际占 40 列, 表头和数据又错开一截。

所以 `wide` 改用 `_disp_len` 取, 截断也用 `_ellipsize_disp`。
`_ellipsize` 保留字符数口径 —— 托盘提示那边量的是字符数(见第 6 节),
两处不能共用一套。

## 5. 顺手带出的第四个坑: 显卡名没转义就拼进了 HTML

进程名一直走 `_html_escape`, 但 `panel_html()` 的 `gpuinfo` 段里
核显/独显名字是直接拼的。显卡名来自 D3DKMT 查询结果, 同样不受我们
控制; 带个 `<` 就会把后面的 `</p>` 提前关掉, 整段看不见。已补转义。
(`policy_text` 那边全走白名单和数值, 不用动。)

## 6. 顺手带出的第五个坑: 托盘提示有 127 字符上限

`Shell_NotifyIcon` 的 `szTip` 是 `TCHAR[128]`。超长的话整条悬停提示
**直接不显示**, 不是被截断 —— 用户看到的是"鼠标停上去什么都没有",
还以为监控挂了。两块名字长的显卡加"暂停"后缀就能顶到。

`gpu_tray.py` 新增 `fit_tip_title()`: 超长时先砍最后一行(显卡名最长
也最不要紧), 占用率和生效策略两行留住; 砍到还放不下才硬截断。

## 7. 清掉的几处遗留

- `gpu_tray.py` 的 import 块排版斜了(`empty_row_text` 顶到行首),
  重新按字典序排, 顺带把 `_ellipsize` 并进 import。
- `tests/test_gpu_monitor.py` 里上轮留下的 3 处 `\uXXXX` **字面转义**,
  还原成汉字 —— 它们落在注释和断言消息里, 不报错但没法读。
- `ListTableLayout` 和下一个 class 之间少了一个空行(顶层 def 要空两行)。
- `col_edges`(量列沿的小工具)从测试方法里提到模块级, 三个用例共用。
- `README.md` 用例数 235 → 240。
- `HANDOFF.md` 上一轮记录里留了 6 个乱码字形, 本轮改成文字描述,
  否则 `scan_rare.py` 每轮都要人工过一遍它们。

## 8. 验证

- `python -X utf8 .tmptest/run_unittest.py` → **242 tests OK**(上轮 235, +7)。
- 新测试 `ListTableLayout`(3 条): 长进程名截断加省略号、表头与数字列
  列沿一致、中文进程名不把列顶宽。
- 反向验证(三条都命中): 表头退回 `%-*s` → `60 != 57`;
  `wide` 退回 `len()` → `26 != 27`; 显卡名去掉转义 → HTML 里出现裸 `<b>`。
- 真机 `--list` 40 行逐列比对, 右沿显示宽度全部一致。
- `probe_r8.py all` → 11/11; `smoke_panel3/4/5/6` 全过;
  `pull_check.js` / `probe_idle.js` / `probe_nomatch.js` 全 PASS;
  `scan_odd2.py` 零输出; 行宽基线 14/7/3 未变。

## 9. 续: 同一类毛病的最后一处, 和一个死变量

复盘 `--list` 那几处 printf 时顺手扫了全仓库剩下的宽度格式化, 还有
两处:

**`_gpu_card_line` 的 `%-42s`。** 就是上面第 1、2 节那两个毛病的原样
复制: 最小宽度不截断(长显卡名把 `[核显]`/`[独显]` 推走), 又按字符数
补空格(中文显卡名短一截)。真机上三块卡名字都在 30 列以内, 看着一直
是齐的 —— 正因为它只在特定机器上暴露, 才更该按定量修而不是靠运气。
改成 `_ellipsize_disp` + `_fit_disp`, 新增 `_GPU_NAME_COL_MAX = 42`
(比进程名那列宽 2, 因为显卡名普遍更长)。

**`sparkline_html` 里的 `peak = max(...)`。** 算了没人用 —— 角标里的
峰值是 `spark_corner_text(pts)` 自己重算的。留着会让人以为峰值是这一
处供的, 改角标算法时先改错地方。删掉。

## 10. 验证(续)

- `run_unittest.py` → **241 tests OK**(+1: `ListCommand` 新增
  `test_the_kind_tag_lands_on_the_same_column`, 断言三张卡的类型标签
  都落在 `22 + _GPU_NAME_COL_MAX + 1` 列)。
- 反向验证: 把 `_gpu_card_line` 退回 `%-42s` → `73 != 65`, 立刻红。
- 真机 `--list` 输出与改动前逐字节相同(本机名字都短于 42 列, 属于
  "修好了但看不出变化"的那种)。

## 11. 续二: 两处"同一个数两个读数"和"空标题"

- **日报通知的峰值取整。** `append_history` 的通知写过 `:.0f`, 而
  `report.csv` / 面板 / `--list` / `hot_reason` 全是 `.1f`。结果同一个
  峰值在通知里是 78%, 在 CSV 里是 77.7% —— 正是这一路一直在灭的那类
  "同一台机器几个读数"。改 `.1f`, 测试断言跟着从 `峰值 78%` 改成
  `峰值 77.7%`。
- **`--status` 的空标题。** 一个都没迁移过时也打印
  `已迁移到独显 (0 个):`, 读起来像程序在汇报读数, 其实什么都没说。
  改成只有 `migrated` 非空才打这一节; "其他设置"那节的前导换行也跟着
  改成条件式的, 不然两节直接粘在一起。

## 12. 续二验证

- `run_unittest.py` → **242 tests OK**(+1:
  `PreferenceSemantics.test_status_says_nothing_when_nothing_is_migrated`)。
- 反向验证: 日报退回 `.0f` → 断言 `峰值 77.7%` 找不到; `--status` 去掉
  `if migrated:` → 断言 `已迁移到独显` 不该出现却出现。
- 真机 `--status` 输出如常(本机有 32 个已迁移项, 不是空标题那种情形)。
## 13. 续三打包（源码已到批次 D，补齐重打 exe）

上一轮收尾时源码已推进到批次 D，但 exe 还停在批次 C 的产物；本轮补齐。

- **两条构建线都不碰运行中的 `dist\GPUMigrate.exe`。** onefile 产物经
  `sync_onefile.py` 落到仓库根 `GPUMigrate.exe`，onedir 产物经
  `sync_onedir.py` 落到 `dist\GPUMigrate-onedir/` 子目录；被 pid 1604/5724
  占住的只有 `dist\GPUMigrate.exe` 本身。所以**没杀进程就完成了重打**。
- 流程：`build_onefile.py` → `sync_onefile.py`(根 exe sha256 前 16 位
  `c9ca9be13df2d2be`，两份一致) → `build_onedir.py` → `sync_onedir.py`
  → `verify_exe3.py`(四个模块 "与仓库源码一致" 全 `True`) →
  `verify_imports.py`(4 个第三方/项目 import 全找到，0 缺失) →
  `clean_build.py` → `cleancache.py`。
- 冒烟：`GPUMigrate.exe --list`，`rc=0`、48 行、0 个 `?`(cp936 解码)。
  `--list` 是 CLI 模式不占单例互斥体，不受 1604/5724 影响。
- 前置复核：`run_unittest.py` 242 OK、`longlines.py` 14/7/3、
  `scan_odd2.py` 零输出，都与基线一致。

### 仍挂起（需用户点头，勿擅动）
- **托盘默认行序**：`cross_surface.py` 探针发现 `--list`/Web 用
  `max(利用率, 专用MB, 共享MB)` 复合键排序，托盘按单列排且默认 `igpu`，
  三面没有同一把尺子；托盘 `rows` 里 `row[0]` 那个复合值是死值(只被稳定
  排序间接用来保序)。倾向给 `_sort_rows` 加 `"auto"` 分支、默认值改
  `{"col":"auto","desc":True}`、`:590` 白名单放进 `"auto"`——但这是 UX
  行为变更且牵动 `TrayPanelRows`/`TraySingleInterpreter` 一批测试，
  **先报告不动手**。
- `cross_surface.py` 钉住的"三面行集 + 逐格数值一致"这条不变量还没固化成
  unittest(目前零覆盖)，可择要补。
- pid 1604/5724 仍是旧托盘实例，占着单例互斥体 `GPUMigrate_Singleton`，
  新 exe 的 `--tray` 真机点亮仍被挡；`dist\GPUMigrate.exe` 也仍是旧产物
  (本轮按设计没覆盖它)。
- 十几批改动未提交。

## 14. 续四：三面行序统一（activity_key + 确定性并列 + 托盘默认档）

上一轮打包完又回到主线优化。`cross_surface.py` 探针暴露的口径不一致这里
收口：把 `--list` / Web 面板 / 托盘三面的行序真正统一起来。

- **新增共用排序主键 `gpu_monitor.activity_key(gpu_pct, ded_mb, shm_mb)`**
  = 三者取最大。`cmd_list`(原先就是复合) 和 `panel_html` 都改调它，并给
  排序 key 追加 `pid` 作二级键 → `(-activity_key(...), x)`。原来并列靠
  set 迭代顺序兜, 不确定; 现在同一份快照三面逐行同序, 含并列行。
- **托盘 `_sort_rows` 加 `"auto"` 档**: desc 走 `(-r[0], r[1][1])` —— 活动
  量降序 + pid 升序, 和 CLI/Web 逐行一致; 默认 `sort_state` 从
  `{"col":"igpu"}` 改成 `{"col":"auto","desc":True}`。点表头仍按单列排。
  `row[0]` 原先是个死值(只被稳定排序顺带用来保序), 现在 `auto` 档真正
  读它, 并改调共用的 `activity_key`。
- `panel_ui.json` 读回白名单加进 `"auto"`; `gpu_tray` import 加
  `activity_key`。
- 顺手删掉 `cmd_list` 里被上一轮编辑重复粘贴的一段 3 行注释。
- 更新探针 `cross_surface.py`: 托盘改用 `auto` 档 → **ALL-PASS**。

### 测试(242 → 246)
- 新类 `CrossSurfaceRowOrder`(3 条): 同一份快照下真实驱动 `cmd_list` /
  `panel_html` / 托盘 `_refresh_tree`, 断言三者行序逐一相等; 含一个
  **显存主导行**(pid 700: 300MB / 5%)钉住"三者取最大", 一对并列(100/900
  同为 50)钉住 pid 升序, 并证明排序不吃进程名(zzz/aaa); 另断言 `--list`
  保留噪声行(诊断用)、面板滤掉(README 第 44 行的既有约定)。
  `test_default_row_order_is_the_shared_activity_key` 断言默认为 `auto`
  且 `gpu_tray.activity_key is gpu_monitor.activity_key`。
- `TrayPanelRows` 加 `test_auto_order_unifies_the_three_surfaces`(升/降两
  方向 + pid 并列)。

### 反向验证(四处均命中后恢复, 源文件与备份逐字节一致)
- `activity_key` 去掉显存项 → 显存主导行掉队 → Web 行序断言红。
- `panel_html` 去 pid 兜底 → 并列乱序红。
- 托盘 `_sort_rows` 去 pid 兜底 → TrayPanelRows auto 用例红。
- 默认退回 `igpu` → 默认值断言红。

### 复核
- `run_unittest.py` 246 OK; `longlines.py` gpu_monitor 14→12(删重复注释)、
  tray 7、tests 3, 均未新增; `scan_odd2.py` 零输出; 探针 ALL-PASS。
- 真机源码态 `--list` rc=0、48 行; 顶部现在是显存主导行(复合口径生效)。
- smoke_panel3/4/5/6 全过(默认 sort 落盘为 `auto`、pid 排序可恢复)。

### 取舍(未动)
- `--list` 仍给全量(含 0%/0MB 噪声行)、面板/托盘滤除 —— README 第 44 行
  明确这是有意的(CLI 诊断用), 不是 bug, 故未改; 测试比的是"共有的非噪声
  行"顺序。
- 托盘默认改 `auto` 后, 点过表头就锁在该单列排序; 暂无显式"回到自动"的
  入口(先这样, 需要可再加个复位按钮)。

## 15. 续五：托盘面板"复原到自动"按钮

上一轮末尾自己提的缺口这里补上：默认排序改成 `auto` 后, 点过某列表头就
锁死在单列排序, 而 `auto` 没有对应表头, 用户回不到默认。

- **新增纯函数 `sort_button_text(state)`**（`gpu_tray.py`，紧跟
  `_FILTER_HINT` 之后）：默认档返回 `排序: 活动量(默认)`，单列档返回
  `复原到自动(列名)`。可脱离 tk 单测。
- **列名收敛到单一来源 `_COL_LABELS`**：`cols = tuple(_COL_LABELS)`，
  表头循环改 `for cid, text in _COL_LABELS.items()`。原先表头 labels 和
  (潜在的)别处各写一份 8 个中文列名, 容易漂移; 现在表格和复位按钮共用
  同一份。
- **工具栏加"复原"按钮**（`fbar`, 紧挨置顶键）：`_reset_sort()` 把
  `sort_state` 置回 `{"col":"auto","desc":True}`、刷按钮文案、立即
  `refresh()`（不等下一秒的定时刷新）。点表头也会顺带更新按钮文案，
  按钮始终反映当前能否复原。
- 点表头仍走 per-column 排序，不受影响。

### 测试 / 验证
- 新测 `test_sort_button_text_nudges_back_to_the_default`：默认/单列/迁移
  状态三档文案。反向验证：把默认档文案改错 → 该测试红，恢复后绿。
- `run_unittest.py` **247 OK**（246→247）；`longlines.py` tray 7/mon 12/
  tests 3，均未新增；smoke_panel3/4 通过（面板照常建、`auto` 默认落盘）。
- `gpu_tray.py` 编译通过；反向改动用备份逐字节校验后恢复。

### 取舍（未动）
- 复位按钮放在工具栏右侧纯文字，颜色 `_MUTED`（比置顶键更克制）；若嫌
  不显眼可改 `_FG`。
- 点表头后仍等下一秒定时刷新才重排（既有行为），仅复位按钮即时生效。

## 16. 续六：托盘整行热度改跟 GPU% 列（修复"排最前却是绿的"）

上一轮把行序统一到复合键后暴露的新矛盾: 一个把独显吃满、核显闲着的
进程, 按复合键排在最前, 整行却是 cool 绿 —— 因为行色一直用
`heat_class(ig)`, 只看核显%; 而 Web 面板独显%那格早就是 hot 红。

- `gpu_tray.py` `_refresh_tree`: 整行热度 tag 从 `heat_class(ig)` 改成
  `heat_class(gpu_all)` —— 跟着同一行的"GPU%"列走。独显吃满的进程现在
  整行 hot, 和它 GPU% 格在 Web 里的上色、以及复合行序一致。CLI `--list`
  无色, 不涉及。
- 改写 `TrayPanelChrome.test_rows_carry_their_heat_colour_tag`: 原快照本
  就不真实(gpu_all < ig); 现在用一个"独显 70% / 核显 0%"的进程钉住"整行
  按 GPU% 上色、独显吃满也 hot"。用例数不变(仍 247, 本条是改写非新增)。

### 反向验证
- 把 tag 改回 `heat_class(ig)` → 独显吃满进程变 cool → 该测试红; 恢复后
  绿。(恢复时一度误用加热度前的备份, 把修复撤回了, 已重打补丁重新应用并
  核对计数 gpu_all=1 / ig=0。)

### 复核
- `run_unittest.py` 247 OK; `longlines.py` tray7/mon12/tests3 未变;
  `scan_odd2.py` 零输出。

## 17. 续七：删掉托盘两处死代码

静态"已赋值未使用"扫描器 `ast_unused.py`（跳过 nonlocal/global 声明；本会
话修过一版漏判 nonlocal、把 apply_config 误报成死代码的版本）扫
gpu_monitor/gpu_tray 后，只剩两条真死代码:

- `gpu_tray.py` `_draw_sparkline`: `peak = max(v for _t, v in pts)` 赋了
  无人读 —— y 轴上限早就改走 `spark_ymax(pts, threshold)`, `peak` 全文件
  再无第二个引用。整行删。
- `gpu_tray.py` `_never`: `pname = vals[0]` 赋了无人读 —— 真正入名单的是
  `_never_add()`, 它自己重读 `tree.selection()` 取名(第 746 行)并
  `save_exclude_process(...)` + 弹 tip; `_never` 只负责定位行、弹菜单。
  删死行, 保留前面的 `if not vals` 守卫。
- 反例: `apply_config` 的 `forced/vram_mb/ignore/battery_state` 是
  nonlocal 写入(1834-1835), 不是死代码, 切勿删除。

删除手法: `del_dead.py` 按 `line.strip()==目标` 找唯一下标整行删, 避开
缩进转录。

### 验证 / 复核
- 反向确认确为死代码: 删后当前文件已无 `peak = max(`, 也无 `_never` 内的
  `pname = vals[0]`; 而 `_never_add` 的 `pname`(746 行)、`_draw_sparkline`
  的 `ymax = spark_ymax(...)` 邻居均完好。
- `py_compile` 通过; `run_unittest.py` 247 OK(删死代码非行为变更, 计数不
  变); `ast_unused.py gpu_monitor.py gpu_tray.py` 现零输出; `longlines.py`
  tray7/mon12/tests3 未变; `scan_odd2.py` 零输出。
- 源码有变更, 按例重打: build_onefile → sync_onefile → build_onedir →
  sync_onedir → verify_exe3(两 exe 四模块“与仓库源码一致”全 True) →
  verify_imports(0 缺失) → `--list` 冒烟(rc=0 / 48 行 / 0 个 `?`) →
  clean_build → cleancache。现根 `GPUMigrate.exe` 与
  `dist/GPUMigrate-onedir/` 均为最新源码; 未碰 `dist/GPUMigrate.exe`。
- README 用例数仍 247, 未动(CRLF 未触动)。

## 18. 续八：右键"永不迁移"不再排除错的进程

比对开源项目右键惯例时揪出的一处准确度问题。托盘进程表里
"_never_add"(执行"永不迁移此程序")是按 tree.selection() 取进程的, 但
ttk 的右键 <Button-3> 默认不改选中项 —— 而 _never 弹菜单前只用
identify_row 定位了光标那一行, 转头就把这个行号丢了。于是: 左键选中 A,
右键 B 行点"永不迁移", 进名单的却是上次左键的 A —— 排除了错的进程,
而用户以为防住的是右键那一行。

- 抽出静态方法 gpu_tray._pick_row(tree, event_y): 定位光标所在行, 非空
  且该行有 values 时 selection_set(item) 把选中项移过去并返回 item;
  点到行间空白、或空状态行(无 values)返回 None。
- _never 改成 if self._pick_row(tree, event.y) is not None 才弹菜单
  —— 右键即选中光标行, 与资源管理器/进程管理器的右键惯例一致; 菜单命令
  再按 selection() 取到的就是右键那一行。
- 双击进详情的 <Double-1> 本就按 identify_row 直接取行, 不受影响。

### 测试 / 验证
- 新增 TrayRightClickSelection(3 条, 247 -> 250): 右键 B 行后 selection
  变为 ("B",); 点在行间空白不动选中项; 无 values 的行不选、不 selection_set。
- 反向验证: 只删 tree.selection_set(item) 一行 ->
  test_right_click_selects_the_row_under_the_cursor 变红(selection 停在
  ("A",), 正是上面的 bug); 从 _gpu_tray_fixed_rclick.py 恢复后全绿, 并核验
  selection_set 计数=1。
- 全套 250 OK; longlines 仍 12/7/3(未新增); scan_odd2 零输出;
  ast_unused 零输出; py_compile 通过。
- 源码已重打: build_onefile -> sync_onefile -> build_onedir -> sync_onedir
  -> verify_exe3(两 exe 四模块"与仓库源码一致"全 True) -> verify_imports
  (0 缺失) -> --list 冒烟(rc=0 / 48 行 / 0 个 ?) -> clean_build -> cleancache。
  现根 GPUMigrate.exe 与 dist/GPUMigrate-onedir/ 均为最新源码; 未碰
  dist/GPUMigrate.exe。
- README 用例数 247 -> 250(CRLF 保持: 165 CRLF / 0 lone LF)。
- 教训复用: longlines.py 必须带文件路径参数, 空跑是 no-op; 上上轮
  "longlines 干净"的结论作废, 本轮用带参跑复核过 12/7/3 基线。

## 19. 续九：托盘悬停占用与汇总卡逐字对齐(.1f)

同一台机器几个界面的数字要对得上, 是这个仓库反复强调的不变量。托盘悬停
tooltip 里的"核显占用"用的是 %.0f(取整), 汇总卡用的是 %.1f(一位小数):
同一份快照 62.7% 会在 tooltip 显示成 63%, 卡片却写 62.7% —— 悬停看到
的数字和点开面板看到的对不上, 正是 test_the_cards_agree_with_the_tray_tooltip
的 docstring(必须是同一个数)要防的事。原来那条测试用的是 63.0 这种整数,
取整后仍是 63, 恰好蒙对, 把小数场景的裂口放了过去。

- gpu_tray._on_sample: 标题里 核显占用 {total:.0f}% 改成 {total:.1f}%,
  与 panel_summary 的卡片逐字同精度。tooltip 127 字预算下多两个字符无
  感, 取舍划算。
- 加固 test_the_cards_agree_with_the_tray_tooltip: 断言的 util 从
  {7:12.0, 8:63.0} 换成带小数的 {7:3.2, 8:62.7}, 断言 tooltip 里含
  "核显占用 62.7%"(与卡片 text 同一个字符串)。整数样例放不过小数进位的
  bug, 这条路以前是空的。
- 托盘图标仍是整数(_make_icon_image 里 int(round)), 那个是 64px 圆里
  的一个数, 塞不进小数点, 不动。

### 反向验证
- 只把 .1f 改回 .0f -> test_the_cards_agree_with_the_tray_tooltip 红:
  '核显占用 62.7%' not found in '...核显占用 63%...'(tooltip 取整、
  卡片带小数, 正是要消除的裂口); 从 _gpu_tray_fixed_tooltip.py 恢复后全绿。

### 复核
- py_compile 过; 全套 250 OK(改写既有用例, 计数不变); longlines 仍
  12/7/3(未新增); scan_odd2 / ast_unused 零输出。
- 源码已重打: build_onefile -> sync_onefile(hash 96dc66b33e108ebf) ->
  build_onedir -> sync_onedir -> verify_exe3(两 exe 四模块"与仓库源码一致"
  全 True) -> verify_imports(0 缺失) -> --list 冒烟(rc=0 / 48 行 / 0 个 ?)
  -> clean_build -> cleancache。现根 GPUMigrate.exe 与 dist/GPUMigrate-onedir/
  均为最新源码; 未碰 dist/GPUMigrate.exe。
- README 用例数不变(仍 250), 未动。

## 20. 续十：托盘暂停/恢复菜单项改动态文案

美观/可用性: 托盘右键菜单"暂停监控"是写死的静态文案。用热键 (Ctrl+Alt+P)
或菜单暂停后, 悬停 tooltip 会显示 [已暂停], 可菜单仍写"暂停监控" —— 一处说
暂停了、一处说还能暂停, 用户拿不准当前状态, 再点一下也不确定是暂停还是恢复。

- 新增纯函数 gpu_tray.pause_button_text(paused)(紧挨 sort_button_text):
  paused=True 返回"恢复监控", 否则"暂停监控"。抽成纯函数是为了可测, 和
  sort_button_text 一个路数。
- 菜单项改成动态文案: MenuItem(lambda item: pause_button_text(self.paused),
  self._on_toggle_pause)。和它下面"模式: X"那行(lambda item: ...)同一手法,
  pystray 每次弹菜单都会重算这条文案。
- 新测 test_pause_button_text_reflects_the_paused_state: 两态各自文案。
  250 -> 251。

### 反向验证
- 只把 return 改回写死 "暂停监控" -> test_...(assertEqual(f(True),"恢复监控"))
  红: '暂停监控' != '恢复监控'; 从 _gpu_tray_fixed_pauselabel.py 恢复后全绿。

### 复核
- py_compile 过; 全套 251 OK; longlines 仍 12/7/3(未新增); scan_odd2 /
  ast_unused 零输出。
- 源码已重打: build_onefile -> sync_onefile(hash 21a15f9f14b7eb2d) ->
  build_onedir -> sync_onedir -> verify_exe3(两 exe 四模块"与仓库源码一致"
  全 True) -> verify_imports(0 缺失) -> --list 冒烟(rc=0 / 48 行 / 0 个 ?)
  -> clean_build -> cleancache。现根 GPUMigrate.exe 与 dist/GPUMigrate-onedir/
  均为最新源码; 未碰 dist/GPUMigrate.exe。
- README 用例数 250 -> 251。

## 21. 续十一：详情窗补"GPU 首选项"当前态

详情窗(双击进程行弹出)有"立即设为独显 / 撤销独显设置"两个按钮, 却从
来不显示当前首选项是哪一态, 用户只能点了看结果才知道刚才改了什么。托盘
进程表面板早就有"迁移状态"列, 详情窗反而没有 —— 同一处信息, 两处露得不
一样。

- gpu_tray._pref_status(self, full): 拿得到完整路径就按 _pref_of 报
  "已设为独显 (重启该程序后生效)" / "未设置 (按系统默认/自动)"; 拿不到
  路径返回"未知 (拿不到完整路径)", 不去查注册表(和 do_set/do_unset 用
  isfile 把关同一个口径)。抽成方法是为了可测。
- 详情窗行序在"当前所在"之后插一行 ("GPU 首选项", self._pref_status(full)):
  先讲运行时在哪块卡, 再讲注册表里设为哪块, 两个按钮于是不再盲操作。
- 新测 TrayDetailPrefStatus(2 条, 251 -> 253): 已存在 exe 两态各自文案;
  路径缺失/空串/占位串都不查注册表直接说"未知"。

### 反向验证
- 把 _pref_status 主体改回一律返回"未知 (拿不到完整路径)" ->
  test_an_existing_exe_reports_its_current_preference 红
  ('未知...' != '已设为独显...'); 从 _gpu_tray_fixed_detailpref.py 恢复后全绿。

### 复核
- py_compile 过; 全套 253 OK; longlines 仍 12/7/3(未新增); scan_odd2 /
  ast_unused 零输出。
- 源码已重打: build_onefile -> sync_onefile(hash ad8e1669fcb80705) ->
  build_onedir -> sync_onedir -> verify_exe3(两 exe 四模块"与仓库源码一致"
  全 True) -> verify_imports(0 缺失) -> --list 冒烟(rc=0 / 48 行 / 0 个 ?)
  -> clean_build -> cleancache。现根 GPUMigrate.exe 与 dist/GPUMigrate-onedir/
  均为最新源码; 未碰 dist/GPUMigrate.exe。
- README 用例数 251 -> 253。

## 22. 续十二：托盘图标也加"数字没变就不重画"闸

_refresh_title 早就有一道闸(内容没变就不动托盘), 注释里写明"每次改 title
都要重建托盘图标"。但同一函数里的 self.icon.icon = _make_icon_image(total)
却是每秒无条件重设 —— 空闲时占用率不动, 那颗彩点被白画一遍又一遍。

- gpu_tray._on_sample: 取整夹 0..100 得到 icon_num, 和上次显示的
  self._icon_num_shown 比一下, 一样就跳过 self.icon.icon 赋值; 变了才重画。
  和标题那道闸同一个理, 少一串无谓的 Shell_NotifyIcon 重建。
- 新测 TrayIconRedrawGuard(2 条, 253 -> 255): 连续 4 轮同一占用, 图标只被
  设 1 次; 占用从 20% 跳到 77% 才再设一次。

### 反向验证
- 把闸去掉、改回无条件 self.icon.icon = _make_icon_image(total) ->
  test_the_icon_is_set_once_when_the_number_holds 红(5 != 1); 从
  _gpu_tray_fixed_iconguard.py 恢复后全绿。

### 复核
- py_compile 过; 全套 255 OK; longlines 仍 12/7/3(未新增); scan_odd2 /
  ast_unused 零输出。
- 源码已重打: build_onefile -> sync_onefile(hash 8b988405210a615f) ->
  build_onedir -> sync_onedir -> verify_exe3(两 exe 四模块"与仓库源码一致"
  全 True) -> verify_imports(0 缺失) -> --list 冒烟(rc=0 / 48 行 / 0 个 ?)
  -> clean_build -> cleancache。现根 GPUMigrate.exe 与 dist/GPUMigrate-onedir/
  均为最新源码; 未碰 dist/GPUMigrate.exe。
- README 用例数 253 -> 255。

## 23. 续十三：名单写入不再把用户的字符串炸成单字母

gpu_monitor._add_to_config_list 是所有"往 config.json 名单里加一项"的
唯一入口(面板右键"永不迁移"、确认模式"忽略"都走它)。它原来是
`items = set(cfg.get(key, []))` —— 直接对读出来的值做 set():

- 名单被手写成单个字符串 ("exclude_processes": "chrome.exe") 时,
  set() 按字符展开, 写回去变成 ['.', 'c', 'e', 'h', ...], 用户原来那一
  项连同被抹掉。读配置那侧 (_coerce_list) 早就防着这个, 只有写这一侧
  裸奔 —— 同一份文件, 一边认一边炸。
- 值是 42 / null 这类标量时, set() 直接 TypeError, 异常从托盘右键菜单
  一路冒到 tkinter 回调, 菜单点了没反应。

- gpu_monitor._str_list(raw): 把"值 -> 字符串列表"的归一抽成一份,
  单个字符串当成一项、序列逐个 str()、别的返回 None。_coerce_list 与
  _add_to_config_list 共用, 不会再漂。
- _add_to_config_list: 先过 _str_list, 认不出来就记一行
  "不是列表，已重写为列表"并当空名单处理(保留新值), 不再抛也不再炸。
  顺手把 add+sort 合成一步。

新测 ConfigPersistence 三条 (255 -> 258): 字符串名单加项后仍是整名且
别的键不被动; 42 / null / true 三种标量都写回正列表且不抛 Traceback;
键根本不存在时不打那句误导人的"不是列表"。

### 反向验证
- 把 _add_to_config_list 的主体换回 set(cfg.get(key, [])) ->
  test_adding_to_hand_written_string_list_keeps_the_name_whole 红
  (['.', 'c', 'e', ...] != ['chrome.exe', 'notepad.exe']);
  test_adding_to_a_non_list_value_does_not_explode 直接 TypeError。
  从 _gpu_monitor_fixed_listnorm.py 恢复后全绿。

## 24. 续十四：详情窗改完首选项, 面板不再按十秒前的旧答案说话

_pref_of 有 10 秒缓存(避免每秒批量查注册表), 而"迁移状态"列每秒刷
一次。在详情窗点过"立即设为独显"/"撤销独显设置"之后, 注册表已经是新
值, 面板却还要按缓存里的旧答案讲最多十秒: 明明已设独显, 列上还写着
"未设置"。同一处信息, 两处说着反话。

- gpu_tray._pref_apply(self, full, clear=False): 写(或清)注册表首选项,
  写完立刻 self._pref_cache.pop(full)。详情窗的 do_set / do_unset 从
  "直接调 set_gpu_preference" 改成调它, 下一帧(最多 1 秒)即对齐。
  抽成方法是为了把"写"和"作废缓存"绑在一起验。
- 新测 TrayDetailPrefStatus.test_a_fresh_write_makes_the_next_read_hit_
  the_registry (一条, 258 -> 259): 先确认没改过时吃缓存(只读 1 次注册
  表), 再确认 set / clear 之后各重读一次且答案跟着变。三个 stubbed
  模块函数在 finally 里还原。

### 反向验证
- 去掉 _pref_apply 末尾那句 self._pref_cache.pop(full, None) ->
  新测试红在"刚设完独显, 列上不能还写着没设"(False is not true)。
- 中途还试过"只删 do_set/do_unset 两处调用点"这种回退法: 测试居然全
  绿 —— 因为方法还在、只是没人调。这暴露了只测方法的漏洞, 于是改成
  现在这条连"写注册表"一起断言的测试, 并把两处调用收进 _pref_apply
  这一个入口, 一处回退即可验出。

### 复核
- py_compile 过; 全套 259 OK; longlines 仍 12/7/3(未新增); scan_odd2 /
  ast_unused 零输出。
- 源码已重打: build_onefile -> sync_onefile(hash 54518943b725f8a3) ->
  build_onedir -> sync_onedir -> verify_exe3(两 exe 四模块"与仓库源码
  一致"全 True) -> verify_imports(0 缺失) -> --list 冒烟(rc=0 / 48 行 /
  0 个 '?'; 输出是 GBK, 拿 gbk 解码读) -> clean_build -> cleancache。
  现根 GPUMigrate.exe 与 dist/GPUMigrate-onedir/ 均为最新源码;
  未碰 dist/GPUMigrate.exe(还是 9-25 那个)。
- README 用例数 255 -> 259。

## 25. 续十五：清掉一段贴了两遍的注释, 并把"重复注释"纳为常驻检查

append_history 里解释"峰值为什么保留一位小数"的三行注释, 被前一轮的
splice 原地贴了两遍(1756-1758 与 1759-1761 逐字相同)。同一段话说两遍,
读的人会停下来数一遍是不是在讲两件事, 而实际什么都没多说。

- gpu_monitor.append_history: 删掉后一份重复的三行。纯注释, 无行为变化。
- .tmptest/scan_dupcomment.py: 新 lint。longlines 管线宽、scan_odd2 查
  错别字、ast_unused 查死变量, 三项都不管"注释重复", 所以补一项。带
  文件路径参数(空跑=no-op, 与 longlines 同一约定), 窗口 12 行, 跳过
  分隔线、# noqa / # type: 之类的行, 只认真句子。

### 反向验证(非空洞性)
-  lint 是新工具, 用一份故意贴两遍的临时文件验过: 报
  ".tmptest/_dupcheck.txt:3 与第 2 行的注释重复" 且 rc=1; 探针已删。
- 三种改动两两之间无耦合: 本轮只是去重 + 加工具, 源码行为逐字节不变
  (255/258/259 用例数不变), 因此不新增用例 —— 加了也是空转。

### 复核
- py_compile 过; 全套 259 OK(计数不变); longlines 仍 12/7/3(未新增);
  scan_odd2 / ast_unused 零输出; scan_dupcomment 0 处。
- 源码已重打: build_onefile -> sync_onefile(hash 53649fff00cf483a) ->
  build_onedir -> sync_onedir -> verify_exe3(两 exe 四模块"与仓库源码
  一致"全 True) -> verify_imports(0 缺失) -> --list 冒烟(rc=0 / 48 行 /
  0 个 '?') -> clean_build -> cleancache。现根 GPUMigrate.exe 与
  dist/GPUMigrate-onedir/ 均为最新源码; 未碰 dist/GPUMigrate.exe。
- README 用例数不变(259)。

## 26. 续十六：README 里两处"页面每秒整页刷新"是第十轮改造时漏删的

Web 面板早在第十轮就改成了局部刷新(PARTS 里那几块各自换 innerHTML),
README 第 41 行也写着"输入框和滚动位置原封不动"; 可同一段的第 43 行还
留着"页面每秒整页刷新，刷新后焦点和光标会还回筛选框" —— 同一段话前后
打架, 且后半句描述的行为早就不存在了。代码里 _PANEL_SCRIPT 上方那句
"筛选词存 localStorage: 页面每秒整页刷新, 不存的话输入一个字就被刷掉"
也是同一个由来: 理由已经不成立(输入框根本不重建), 但 localStorage 本
身仍然要留(刷新页面/新开标签页不该丢筛选词)。

- README: 删掉那句整页刷新的描述, 只留 viewport / 横向滚动那半句;
  续行缩进跟着补回两空格。
- gpu_monitor._PANEL_SCRIPT 上方的注释改成真实的理由。
- 顺带把测试里同一句错理由的 assert 消息也改掉。

### 复核
- 纯注释/文档, 无行为变化, 用例数不变; 无新增用例(加了也是空转)。

## 27. 续十七：Web 面板补上"迁移状态"列

托盘进程表一直有最后一列"迁移状态"(已设独显(重启生效)), Web 面板只有
七列裸数字 —— 于是浏览器里看到一条 0% 的进程, 判断不出它是不是已经被
钉在独显上。README 写着 Web 面板是"同一套数据的浏览器版", 这一列就是
那句承诺对不上的地方。

- gpu_monitor.PREF_HIGH_TEXT: "迁移状态"那一格的文案。托盘那边原先是
  写字面量, 现在也改成引用这个常量 —— 两处各写一遍, 迟早一边改词另一
  边还说着旧话。
- cmd_monitor: 每轮顺手算出 pid_prefs {pid: 是否已设独显}, 只放进
  _LAST_SNAPSHOT(Web 用), 不进 on_sample —— 托盘自己那套有 10 秒缓存
  和 _pref_apply 的作废逻辑, 混用会两边打脸。拿不到完整路径的 pid 不进
  这个 dict: 没有路径就写不了注册表, 也没什么可显示的。
- panel_html: 表头加 `<th>迁移状态</th>`, 每行多一格 `<td class=pref>`,
  空态行的 colspan 7 -> 8; CSS 加 `td.pref,th.pref` 左对齐 + 弱化色,
  空单元格看起来才不像是渲染坏了。没带 pid_prefs 的快照按空列渲染。

新测 8 条(259 -> 267): WebPanelPrefColumn 6 条(表头/已设/未设/缺键兜底/
colspan 跟着列数走/托盘与 Web 共用同一字面量),
SnapshotPrefColumn(LoopHarness) 2 条(快照带 {100: True, 102: False};
拿不到路径的 pid 不进 dict)。EmptyRowText / WebPanelFilter 里两处断言
colspan=7 的同步改成 8 —— 它们断言的本来就是"空态行跨满一整行"。

### 反向验证
- 去掉 cmd_monitor 里 `pid_prefs[pid] = is_high_performance(...)` ->
  test_the_snapshot_carries_the_preference_state 红({} != {100: True,
  102: False}); 再把 panel_html 的 pref 置成恒空 ->
  test_a_pinned_process_is_marked 红。两处分别从
  _gpu_monitor_fixed_webpref.py 恢复后全绿。

### 复核
- py_compile 过; 全套 267 OK; longlines 仍 12/7/3(未新增); scan_odd2 /
  ast_unused 零输出; scan_dupcomment 0 处。
- 源码已重打: build_onefile -> sync_onefile(hash 9f5ec4affea523bc; 2ede7add85944b4f 是本轮完地前的一次, 因后面又把超长注释改短才再打了一轮) ->
  build_onedir -> sync_onedir -> verify_exe3(两 exe 四模块"与仓库源码
  一致"全 True) -> verify_imports(0 缺失) -> --list 冒烟(rc=0 / 48 行 /
  0 个 '?') -> clean_build -> cleancache。现根 GPUMigrate.exe 与
  dist/GPUMigrate-onedir/ 均为最新源码; 未碰 dist/GPUMigrate.exe。
- README 用例数 259 -> 267, 并补上两边的列说明。

## 28. 续十八：AdapterRAM 回绕把独显误判成核显

`_video_controller_ram()` 把 WMI 的 `AdapterRAM` 除成 MB, 供 `classify_gpu`
在名字两个正则都不命中时兜底判独显/集显。但 `Win32_VideoController.AdapterRAM`
是 uint32, 部分驱动对 >4GB 的卡把 2^31 以上报成回绕后的负数(如 -2147483648)。
`int(ram) // (1024*1024)` 会算出负 MB, `classify_gpu` 的 `>=1500` 就不成立
—— 独显被判成集显, 于是反把进程往核显上赶, 和工具的目的正好相反。只有名字
两个正则都不命中时才走这条兜底路径, 所以 RTX/iGPU 这类常见名不受影响, 用户
不会天天撞见, 但踩到就是方向性错误。

- gpu_monitor._video_controller_ram: 解析出负数时补回 `ram += 1 << 32`。只补
  负数、不动正数: 用 `ram & 0xFFFFFFFF` 是错的, 会把真正 8GB 的卡读出的正数
  8589934592 抹成 0(试过并回退)。补回 2^32 后 -2147483648 -> 2048MB, 而
  8589934592 原样仍是 8192。
- docstring 顺手写清: 这个值只作分类兜底、不是显存总量, AdapterRAM 本就会
  回绕, 集显上报的常是划给它的共享内存 aperture, 拿它当"显存"是错的。

新测 1 条(267 -> 268):
DedicatedMemoryLookup.test_a_wrapped_adapter_ram_is_read_as_unsigned ——
stub `gm.subprocess.run` 返回 `"Mystery 8GB Card|-2147483648\n"`, 断言
`query_dedicated_mb("Mystery 8GB Card") == 2048` 且
`classify_gpu("Mystery 8GB Card", 2048) == "dgpu"`。

### 反向验证
- 去掉 `if ram < 0: ram += 1 << 32` -> 测试红(-2048 != 2048); 从
  _gpu_monitor_fixed_adapterram.py 恢复后全绿。

### 复核
- py_compile 过; 全套 268 OK; longlines 仍 12/7/3(未新增); scan_odd2 /
  ast_unused 零输出; scan_dupcomment 0 处。
- 本机实测: .tmptest/ram_probe.py -> RTX 4060 Ti 真实 AdapterRAM=4293918720
  (正数)读作 4095MB -> dgpu; UHD 630 实际 1073741824 -> 1024MB -> igpu,
  正数路径行为未变。
- 源码已重打: build_onefile -> sync_onefile(hash cda73b38897dd6b1) ->
  build_onedir -> sync_onedir(84ee0a2989c40df9) -> verify_exe3(两 exe 四模块
  "与仓库源码一致"全 True) -> verify_imports(0 缺失) -> --list 冒烟(rc=0 /
  48 行 / 0 个 '?') -> clean_build -> cleancache。现根 GPUMigrate.exe 与
  dist/GPUMigrate-onedir/ 均为最新源码; 未碰 dist/GPUMigrate.exe。
- README 用例数 267 -> 268。

## 29. 续十九：整合 Intel Arc(Core Ultra 集显)不再被当成独显

`classify_gpu` 的名字启发式里, `DGPU_PAT` 有个裸 `arc`、`IGPU_PAT` 有个裸
`intel`。于是"Intel Arc Graphics"(Core Ultra 集显)两个正则都命中, 落在既有
`DGPU and not IGPU` / `IGPU and not DGPU` 的兜底之外, 落到 dedicated_mb:
WMI 查询失败时返回 unknown; 而集显上报的 AdapterRAM 常是划给它的一大块共享
aperture, 一旦 >=1500MB 就被判成 dgpu —— 把集显当成迁移目标, 和工具的目的
正好相反。

带型号的独显 Arc(A770/B580/Arc Pro …)两个正则也都命中, 这是既有测试明确写死
的设计(GpuClassification.TABLE: "Intel(R) Arc A770 Graphics" 无 RAM 时就是
unknown, 靠 RAM 定夺), 本轮不动它。两类只差在型号: 独显 Arc 一定带 A/B 型号,
整合 Arc 一律叫 "Intel Arc Graphics" 不带。

- gpu_monitor.DGPU_PAT: 裸 `arc` 改成 `arc (?:pro )?[ab]\d` —— 只有跟型号的才算
  独显。效果:
  - "Intel Arc Graphics" / "Intel(R) Arc(TM) Graphics": DGPU 不再命中, IGPU 的
    `intel` 单独命中 -> 直接判 igpu, 不再依赖 WMI, 也不会被大 aperture 推成 dgpu。
  - "Intel Arc A770" / "Arc B580" / "Arc Pro A60M": 仍两个都中 -> 按既有设计留给
    RAM, 真机上 A770 报 16GB -> dgpu, 与改动前一致。
  - NVIDIA / Radeon RX(Vega APU 仍靠 `radeon rx`+`vega` 双中走 RAM) 等一律不受
    影响。顺带把 DGPU_PAT 从 86 列超长单行重排成两行, longlines 12 -> 11。

新测 2 条(268 -> 270):
GpuClassification.test_integrated_intel_arc_is_igpu_without_ram —— "Intel Arc
Graphics"/"Intel(R) Arc(TM) Graphics" 无 RAM 即判 igpu;
test_discrete_arc_model_still_defers_to_ram —— "Intel Arc A770" 无 RAM 仍
unknown、有 RAM 判 dgpu、"Arc Pro A60M"+8192 判 dgpu, 锁住独显那条既有设计没被
顺手改掉。

### 反向验证
- 还原裸 `arc`(_gpu_monitor_before_arcintegrated.py) ->
  test_integrated_intel_arc_is_igpu_without_ram 红('unknown' != 'igpu' @
  "Intel Arc Graphics"); test_discrete_arc_model_still_defers_to_ram 仍绿(它断言的
  是被保留的既有行为)。从 _gpu_monitor_fixed_arcintegrated.py 恢复后全绿。

### 复核
- py_compile 过; 全套 270 OK; longlines 现 11/7/3(DGPU_PAT 重排消掉一处, 未新增);
  scan_odd2 / ast_unused 零输出; scan_dupcomment 0 处。
- 探针 .tmptest/classify_probe.py: RTX 4060 Ti/4090/RX 7900/Radeon Pro/Quadro 仍
  dgpu; UHD 630/Iris Xe/780M/Radeon(TM)/Vega 8 仍 igpu; Intel Arc Graphics 现 igpu
  (改前 unknown); Arc A770/B580 无 RAM 仍 unknown(设计不变)。
- 源码已重打: build_onefile -> sync_onefile(hash 88ed26a748bfc59d) -> build_onedir
  -> sync_onedir(efc83636fdb1786d) -> verify_exe3(两 exe 四模块"与仓库源码一致"全
  True) -> verify_imports(0 缺失) -> --list 冒烟(rc=0 / 48 行 / 0 个 '?') ->
  clean_build -> cleancache。现根 GPUMigrate.exe 与 dist/GPUMigrate-onedir/ 均为最新
  源码; 未碰 dist/GPUMigrate.exe。
- README 用例数 268 -> 270; 分类说明改成"Arc Graphics -> 核显 / Arc(A/B 型号) ->
  独显"。

## 30. 续二十：修正 §29 正则(真机 Intel Arc 名带 TM)；补 AMD 集显去后缀漏判

§29 把独显 Arc 的识别改成"arc 后跟型号", 可用带 (TM) 的真名一探就露馅:
探针 classify_probe2 里 `Intel(R) Arc(TM) A770 Graphics` + 16384MB 被判成
igpu —— 真名中 "Arc" 和型号 "A770" 之间隔着一个 "(TM)", `arc (?:pro )?[ab]\d`
匹配不上, 于是只剩 IGPU 的 intel 单中, 独显反过来判成核显(又把进程往集显赶)。
原有测试用的是去掉 (TM) 的 "Intel(R) Arc A770 Graphics", 恰好绕开这个坑, 所以
§29 当时是全绿的 —— 覆盖率漏了真名的形态。同一次探针还撞见一个老坑: AMD 集显
APU(Rembrandt 610M/660M/680M、Strix 890M 等)在不带 "Graphics" 后缀时报成
unknown, 早先只特判了 780m, 漏了同胞型号。

- DGPU_PAT: arc 段改成 `arc\b(?:\s*\(tm\))?(?:\s+pro)?\s+[ab]\d` —— 容忍 (R)/
  (TM) 与 "Pro", 真名 "Arc(TM) A770" / "Arc(TM) Pro A60M" 都算独显, 而整合的
  "Arc Graphics" / "Arc(TM) Graphics" 仍不算(型号必须是 A/B + 数字)。
- IGPU_PAT: AMD 集显型号补齐 610m/660m/680m/740m/760m/880m/890m —— 这些 APU
  名去掉 "Graphics" 后缀时也判核显(原来只有 780m/8060s)。离散 RX 与 Vega APU
  走 RAM 的逻辑没动。

新测 1 条(270 -> 271): GpuClassification.test_real_world_display_name_forms ——
带 (TM) 的真名把独显(无 RAM 留 unknown / 有 RAM 判 dgpu)和整合(直接 igpu)都
钉住, 并覆盖 610M/680M/890M 判 igpu 与 RX 6600 判 dgpu。

### 反向验证
- 还原 §29 版正则(_gpu_monitor_before_arcfix2.py) -> test_real_world_display_name_forms
  红('igpu' != 'unknown' @ "Intel(R) Arc(TM) A770 Graphics"); 从
  _gpu_monitor_fixed_arcfix2.py 恢复后全绿。

### 复核
- py_compile 过; 全套 271 OK; longlines 仍 11/7/3; scan_odd2 / ast_unused 零;
  scan_dupcomment 0 处。
- 探针 classify_probe2 现 31/31 全中(改前 3 处 mismatches: Arc(TM) A770 误判
  igpu、AMD 610M/680M 误判 unknown)。
- 源码已重打: hash 5a87b6b2de3fc2a1(onefile) / 00d0f21277238967(onedir);
  verify_exe3 两 exe 四模块全 True; verify_imports 0 缺失; --list 冒烟 rc=0 /
  48 行 / 0 '?'; clean_build + cleancache 已过。未碰 dist/GPUMigrate.exe。
- README 用例数 270 -> 271。

## 31. 续二十一：决策核心 / 渲染面 / 实机输出审计(结论: 正确, 别再重复扫)

§29/§30 修完显卡分类后, 把其余高后果面也用"真值探针"过了一遍, 想找同类别的
真实缺陷; 结论是这些面对真实/逼真输入已经正确, 记录下来免得以后重复扫。

- 决策核心: `hot_reason` 边界契约 7/7(util>=阈值 / vram>=阈值 的 >= 边界、
  vram=0 关闭、util 优先显示); `select_hot_candidates` 排除优先级实测按
  进程名 -> 完整路径前缀 -> 忽略名单 -> 已迁移逐层生效。sustain 连续超标计数、
  `done` 去重、确认拒绝写忽略、电池阈值切换、配置热重载均有既有测试覆盖。
  (探针 .tmptest/probe_gate.py + probe_gate2.py; 曾因 here-string 把反斜杠写成
  双份, 误报一次路径排除失效, 用 chr(92) 重建确认无误 —— 教训: 路径类断言别用
  r"...\\...")
- 渲染面: `panel_html` 对 `<script>`/`&`/`<>`/中文进程名全部正确转义, 引号在
  文本节点内安全, 前端筛选走 textContent 无注入(.tmptest/probe_esc.py);
  `spark_*` 已防空/单点/全零/极大值; 仅剩 NaN/负 PDH 计数这类真实采集不会产生
  的输入, 按项目"不为不可能输入加复杂度"的作风不动。
- 实机 --list(D:\~/.tmptest/listNN.txt): RTX 4060 Ti→独显、UHD 630→核显、
  Basic Render→虚拟/WARP, 三类判定在真机上全部正确, 反向印证 §29/§30 没把用户
  这台机器判坏。CLI 不过滤 0/0 噪声行、按 activity_key(含显存)降序, 是 README
  写明/§14 的既定设计, 非 bug。
- 用本机真实数据渲染了一份 Web 面板 HTML(.tmptest/panel_real.html +
  render_real.py / inspect_panel.py): 汇总卡、VRAM 大户置顶、warm 着色、迁移状态
  列、温度行、阈值虚线标、空态 colspan=8、隐藏噪声计数——全部正确且三面口径一致。

仍待用户拍板(未擅动): 退旧托盘 pid 1604/5724 以验证新 exe 托盘点亮; 十几批改动
是否按 §23..§30 拆 commit; 开机计划任务 \GPUMigrateWatchdog 指向失效路径要不要修。

## 32. 续二十二：补全 README 配置表缺失的 10 个字段(准确性)

排查 config.json 默认值时发现 README 的"配置(config.json)"表漏了 DEFAULT_CONFIG 里
近半数键不说, 连几个会实打实改变行为的都缺: normalize_total(读数口径切换)、
power_saver_auto(低负载自动切回核显)、power_aware + battery_threshold_percent
(电池省电策略)、game_processes、web_port、nvml_temp + temp_limit、history、
notify_fullscreen_ignore。用户照 README 配根本不知道这些开关存在。

按 README 一贯体例(默认值列给 DEFAULT_CONFIG 的默认, 不是某人 config.json 的现值)
把 10 行补进表内, 各归其位: notify_fullscreen/电源感知/温度/端口 挨着 notify 放,
power_saver_auto/normalize_total/history/game_processes 挨着 power_saver_samples 放。
CRLF 已归一化(loneLF=0)。纯文档改动, 不涉及源码, 未重打 exe, 用例数仍 271。


## 33. 续二十三：VIRTUAL_PAT 补三类软件回退显示适配器(准确性)

用户先要求重打 exe, 按标准链打包并全绿(onefile 155a59140728bb02 / onedir
同步 dist/GPUMigrate-onedir, verify_exe3 四模块 True, verify_imports 0 缺失,
--list 冒烟 RC=0/48 行/0 乱码, 缓存已清)。核对桌面时发现真实桌面被注册表重定向到
D:\Users(C:\Users\qm081\Desktop 是空壳), 两条 GPU 快捷方式一直都在:
"GPU 监控(打包版).lnk"→GPUMigrate.exe(指向新 exe), "GPU 监控.lnk"→pythonw.exe。

趁打包后的空档开了个新探针角度(.tmptest/probe_virtualpat.py): 驱动失效时 Windows
换成软件回退适配器, 真名有 "Microsoft Basic Display Adapter"(Win10)、
"Microsoft Basic Display Driver"(Win11)、"Standard VGA Graphics Adapter"(老机器)、
"Microsoft Remote Display Adapter"(RDP 会话)四种。VIRTUAL_PAT 只认 basic render,
这四个全部漏网: AdapterRAM=0 时被 RAM 兜底判成 igpu, 等于告诉用户"迁移到这块假
核显", 迁移就丢了; 不带 RAM 时更退化成 unknown。

修法最小: VIRTUAL_PAT 加 basic display / standard vga / remote display 三个词
(都是回退驱动独占措辞, 不碰任何真实硬件命名)。新增
test_software_fallback_adapters_are_virtual(真机英文 DisplayName 形态,
assert 带/不带 dedicated_mb=0 两条路径都判 virtual)。按老规程先跑红(unknown !=
virtual), 修完 7/7 绿, 全套 272 用例全绿; 备份
_gpu_monitor_before_virtualpat.py / _gpu_monitor_fixed_virtualpat.py 已存。
longlines 11/7/3、scan_odd2/ast_unused/dupcomment 全零、py_compile OK。
源码已变, exe 按标准链重打: onefile 155a59140728bb02(根目录)、onedir 同步 dist/。
README 用例数 271→272。这轮没动系统状态、没 commit。

仍待用户拍板(未擅动): 退旧托盘 pid 1604/5724 以验证新 exe 托盘点亮; 十几批改动
是否按 §23..§33 拆 commit; 开机计划任务 \GPUMigrateWatchdog 指向失效路径要不要修。


## 34. 续二十四：_str_list 名单项 strip(配置鲁棒性)

沿着"开源工具不怕手滑"的角度继续探输入面。数字/开关/热键三类收敛
(§ConfigValidation)早就位, bool 字符串 "false" 也不会踩 Python truthiness 的坑;
上一层 read_json_dict/merged_config 的损坏回落也验证过。剩下的口子是
_str_list(gpu_monitor.py:615): 读配置(_coerce_list)和写名单
(_add_to_config_list)共用的那个归一函数不 strip。探针
(.tmptest/probe_strlist.py)实测: exclude_processes 写成
[" chrome.exe "] 时 select_hot_candidates 照样选中 chrome —— 进程名比对是
集合精确匹配, 前后空白让整条名单静默失效, 用户完全看不出哪儿错。
tab 和 exclude_full_paths / game_processes / force_igpu_names 同罪。

修法仍是最小根因: _str_list 每项 strip, strip 后空项丢掉(空串本来也匹配
不上任何进程名)。一处改动覆盖六个名单键的读与写。新增两条测试:
test_list_entries_are_stripped(四个键的空白都被去掉、不打日志) 和
test_stripped_exclusion_actually_excludes(端到端挡住候选)。先红(13 例挂 2)
后绿, 全套 274 用例全绿。备份 _gpu_monitor_before_strlist.py /
_gpu_monitor_fixed_strlist.py 已存。longlines 11/7/3、scan_odd2/ast_unused/
dupcomment 全零、py_compile OK。exe 按标准链重打: onefile f8ffac3536da56cc、
onedir 同步 dist/。README 用例数 272→274。没动系统状态、没 commit。

仍待用户拍板(未擅动): 退旧托盘 pid 1604/5724 以验证新 exe 托盘点亮; 十几批改动
是否按 §23..§34 拆 commit; 开机计划任务 \GPUMigrateWatchdog 指向失效路径要不要修。


## 35. 续二十五：托盘图标字体回退链(美观性)

把三档托盘图标渲染成 PNG 亲眼过了一遍(.tmptest/probe_icon.py + icon_0/45/88.png):
凉/温/热三色与表格同一套调色板, 64px 画布上数字居中, 缩到 32px 仍清晰 ——
主路径没问题。但 fallback 路径有隐患: _make_icon_image 只认 arialbd.ttf,
没装的机器直接落 load_default() —— 那是个 11px 位图字, 缩到 16x16 任务栏
后数字糊成一团。本机探测 5 个粗体全在(arialbd/segoeuib/msyhbd/calibrib/
seguisb), 所以这坑只在精简版/部分英文版 Windows 上发作, 平时看不见。

修法: 抽出 _bold_font(size), 按 arialbd.ttf -> segoeuib.ttf -> msyhbd.ttc
顺序找, 全都缺失才退内置位图字。新增 test_icon_font_falls_back_across_
missing_fonts: monkeypatch truetype 让前两个字体抛 OSError, 断言调用链顺序
与"仍然画得出图"。注意 stub PIL 下 Image.new() 返回的对象没有 .size,
断言写成 assertIsInstance(img, gpu_tray.Image.Image), 真 PIL 同样成立。
先红(旧代码只试 arialbd 就直接退内置)后绿, 全套 275 用例全绿。
备份 _gpu_tray_before_fontchain.py / _gpu_tray_fixed_fontchain.py 已存
(注意: before 是从修后源码反转生成的, 与改动点严格对应)。
longlines 11/7/3、scan_odd2/ast_unused/dupcomment 全零、py_compile OK。
exe 按标准链重打: onefile 1cf0d031a1a9518f、onedir 同步 dist/。
README 用例数 274→275。没动系统状态、没 commit。

仍待用户拍板(未擅动): 退旧托盘 pid 1604/5724 以验证新 exe 托盘点亮; 十几批改动
是否按 §23..§35 拆 commit; 开机计划任务 \GPUMigrateWatchdog 指向失效路径要不要修。


## 36. 续二十六：右键“永不迁移”不再谎报成功(准确性)

顺着托盘交互继续查, 抓到一处“假确认”: _panel_build 的右键菜单
_never_add 直接调 save_exclude_process 却不看返回值,  tip 永远显示
“已把 X 加入永不迁移名单 (写入 config.json, 立即生效)”。config.json
解析不了/只读时写入其实失败——用户以为排除了, 下一轮监控照旧把这进程
判成候选, 而且没有任何痕迹解释为什么。设置窗口的保存是拒绝+标题提示,
notify 有失败日志, 只有这里谎报, 违反本项目“静默失效是头号敌人”的
一贯立场。

修法: 抽出可测的 _never_migrate(pname, tip): 成功才显示“已加入”,
失败则 _dbg 记一行 + tip 明说“写入失败”。顺手给 _apply_mode 的
write_json_dict 补返回值检查+_dbg(模式菜单点完没反应时不留痕同样难查)。
测试落 TrayPanelRows(有现成的 _Tip fake): 失败路径断言 tip 不含
“已加入”且含“失败”、成功路径断言写入参数与确认文案。
插测试时踩了两个坑记一下: ① 锚点 class TrayPanelChrome 前面那个类是
TrayFilterBox 不是 TrayPanelRows, 测试先落错了类(run_one 类名对不上
才发现); ② 该类 setUp 里 import gpu_tray 是局部的, 要用 self.tray。
先红(AttributeError: 没有 _never_migrate)后绿, 全套 277 用例全绿。
备份 _gpu_tray_before_nevermigrate.py / _gpu_tray_fixed_nevermigrate.py
由修后源码反转生成。longlines 11/7/3、scan_odd2/ast_unused/dupcomment
全零、py_compile OK。另: config.json 补了缺失的末尾换行(CRLF)。
exe 按标准链重打: onefile 3eed70b29e8097e1、onedir 同步 dist/。
README 用例数 275→277。没动系统状态、没 commit。

仍待用户拍板(未擅动): 退旧托盘 pid 1604/5724 以验证新 exe 托盘点亮; 十几批改动
是否按 §23..§36 拆 commit; 开机计划任务 \GPUMigrateWatchdog 指向失效路径要不要修。


## 37. 续二十七：设置窗口数字兜底与生效值对齐(准确性)

顺着托盘交互把设置窗口也过了一遍: 数字项的兜底值写死在 save() 里
(num(v_thr, cfg2.get("threshold_percent", 20), float)), 其中
threshold_percent 兜 20, 而 DEFAULT_CONFIG 与 README 写的默认都是 50 ——
这类硬编码早就和真默认漂移过(20 是 threshold_percent 早年旧值)。
后果: 用户 config.json 没配这一项、又在框里填了非数字时, 存回去的是
20, 和输入框里显示的生效值(50)不是同一个数, 属于"界面显示一套、
落盘另一套"。vram/battery/temp/port 四个兜底目前碰巧还对得上, 但同一
类漂移风险还在。

修法: 抽 _settings_num(var, cfg, key, cast) 静态方法, 兜底改用
merged cfg 的当前生效值(和输入框预填一致, 也不会再跟默认值漂移),
save() 里五处全部改走它。测试
test_settings_num_falls_back_to_the_effective_value 钉住:
填"很高"时返回默认生效值、填"33"返回 33.0、port 走 int。
先红(AttributeError)后绿, 全套 278 用例全绿。顺带把 gpu_tray.py 的
longlines 从 7 降到 4(三个长行在重构中消失)。备份
_gpu_tray_before_settingsnum.py / _gpu_tray_fixed_settingsnum.py 已存。
longlines 11/4/3、scan_odd2/ast_unused/dupcomment 全零、py_compile OK。
exe 按标准链重打: onefile f6c408fc1a8c66d9、onedir 同步 dist/。
README 用例数 277→278。没动系统状态、没 commit。

另: 本轮把 README 的数值声明逐条对过代码(30/60 两档、120s 确认超时、
趋势图最近 30 分钟 = [-180:] 每 10 秒一点、topmost 默认开、
panel_ui.json 持久化项), 全部属实, 无文档漂移。

仍待用户拍板(未擅动): 退旧托盘 pid 1604/5724 以验证新 exe 托盘点亮; 十几批改动
是否按 §23..§37 拆 commit; 开机计划任务 \GPUMigrateWatchdog 指向失效路径要不要修。


## 38. 续二十八：panel_ui.json 落盘改走原子写(准确性)

CLI 注册表命令(cmd_set/unset/status/unset-all)过了一遍: 文件存在性校验、
成功/失败分支、EOF 处理、空列表措辞都到位, 无需改动。读 panel_ui.json
的恢复路径也已有兜底(解析失败 -> 默认布局, sort 列名白名单校验)。
但**写入侧**是唯一的漏网: on_close 里 open(_UI_FILE, "w") + json.dump
—— 正是本项目在 write_json_dict 的 docstring 里明文批过的写法
("直接 open(path,'w') 会在 json.dump 之前就把原文件清空")。关窗这条
路径恰恰容易被 taskkill/断电打断, 留下半截 JSON 后, 下次开面板的
布局(位置/列宽/筛选词/排序列)全部回默认。读侧有兜底只是不崩,
丢布局本身仍是真损失。

修法: on_close 改调已导入的 write_json_dict(临时文件 + os.replace),
并补注释把两个 JSON 落盘路径的道理并到一起。新增
test_panel_ui_is_written_atomically: monkeypatch gpu_tray.write_json_dict
记录调用, 断言关窗时它被调用一次、路径是 _UI_FILE、数据含
geometry/topmost/filter/sort/widths 五个键。先红(旧代码从不调它)
后绿, 全套 279 用例全绿。备份 _gpu_tray_before_panelui.py /
_gpu_tray_fixed_panelui.py 已存(由修后源码反转生成)。
longlines 11/4/3、scan_odd2/ast_unused/dupcomment 全零、py_compile OK。
exe 按标准链重打: onefile 9bf848620e33133d、onedir 同步 dist/。
README 用例数 278→279。没动系统状态、没 commit。

仍待用户拍板(未擅动): 退旧托盘 pid 1604/5724 以验证新 exe 托盘点亮; 十几批改动
是否按 §23..§38 拆 commit; 开机计划任务 \GPUMigrateWatchdog 指向失效路径要不要修。


## 39. 续二十九：详情窗按钮不再谎报成功(准确性)

源码摸底换了个角度: 不数分支, 而是盯着"哪些失败会让界面说反话"。
详情窗(双击进程行弹出的那个)的"设为独显/清除 GPU 设置"两个按钮坐实了
一类谎报 —— do_set/do_unset 调完 _pref_apply 就无条件弹"已设为独显:
重启 XX 后生效"/"已清除 GPU 设置", 完全不看写入结果: 注册表被安全软件
挡住时 clear_gpu_preference 返回 False(什么也没删), 界面照颁"已清除";
set_gpu_preference 失败抛 OSError 更糟, 它直接逃出 tk 回调, 只有 stderr
里一条没人看的 traceback —— --noconsole 打包后连这都没有, 用户点完
按钮音都没一个, 以为设好了。按钮的语义就是"我干了什么", 干失败必须
说没干成。

修法: _pref_apply 改为返回 bool —— clear 按 clear_gpu_preference 的
返回值(False 即本来就没设置), set 包 try/except OSError; 只有写成功才
把这一条踢出 _pref_cache(失败时保留缓存, 免得下一帧读回旧值更难核对)。
do_set/do_unset 先判 os.path.isfile(full) 早退(原来两条路共用的兜底
消息挪进分支里), 再按返回值给文案: 失败分别讲"写入注册表失败, 首选项
未改变"/"未能清除: 可能本来就没有独显设置"。TrayDetailPrefStatus 补
test_a_failed_set_reports_failure_without_raising 与
test_a_failed_clear_is_not_a_silent_success: 先红 —— 前者复现 OSError
逃逸到测试里, 后者抓"clear 失败还弹已清除 + 缓存被误作废"两宗罪;
类内 5/5, 全套 281 用例全绿。备份 _gpu_tray_before_detailapply.py /
_gpu_tray_fixed_detailapply.py 已存 (§38 收尾态反转生成, diff 双向核对)。
longlines 11/4/3、scan_odd2/ast_unused/dupcomment 全零、py_compile OK。
exe 按标准链重打: onefile 06e1ce4b6b792ee4(onedir 同源码, verify_exe3
双一致)、--list 48 行 0 乱码 RC=0、onedir 同步 dist/。README 279→281。
没动系统状态、没 commit。

仍待用户拍板(未擅动): 退旧托盘 pid 1604/5724 以验证新 exe 托盘点亮; 十几批改动
是否按 §23..§39 拆 commit; 开机计划任务 \GPUMigrateWatchdog 指向失效路径要不要修。


## 40. 续三十：双击托盘版撞单例不再静默消失(准确性)

沿 §39 的"谎报/不报"线索复查另一半入口: main() 里 --tray 撞上单例
互斥体时直接 return, 什么都不说。控制台版还好(有一行"已有 GPU 监控
实例在运行"打到 stdout), 但打包版是 --noconsole 的 pythonw, 双击
GPUMigrate.exe 时 sys.stdout 是 None —— 用户看到的就是"双击后什么
都没发生": 没有窗口, 托盘没多图标, 只会怀疑程序坏了或没装上。而
--ensure 是看门狗每 2 分钟的例行调用, 撞互斥体必须一声不吭, 所以
两条路要分开对待。

修法: gpu_monitor.py 新增 notify_already_running() —— 置顶 + 前置的
MessageBoxW(MB_OK|MB_ICONINFORMATION), 文案说明它在跑、去哪找
(右下角托盘"GPU 监控"图标)、双击图标能打开面板; 弹窗本身包了
try/except OSError, 提示是锦上添花, 弹不出去也不能把退出变成崩溃。
main() 的托盘分支改为: ensure_singleton 失败时, 仅当是 --tray 才弹
这个框, --ensure 保持静默。SingletonGate 补两测:
test_double_click_tray_when_running_tells_the_user(monkeypatch
ensure_singleton=False + 假 user32 收 MessageBoxW 参数, 断言弹了一次
且文案含"已在运行/托盘/GPU"; 修前红: 0 != 1) 与
test_ensure_stays_quiet(同一套夹具下断言 boxes==[], 看门狗不吵)。
全套 283 用例全绿。备份 _gpu_monitor_before_alreadyrun.py /
_gpu_monitor_fixed_alreadyrun.py 已存(由修后源码反转生成, diff 三处
与预期一致)。longlines 11/4/3、scan_odd2/ast_unused/dupcomment 全零、
py_compile OK。exe 按标准链重打: onefile 6190abfa5366c0ea(onedir 同
源码, verify_exe3 双一致)、--list 48 行 0 乱码 RC=0、onedir 同步
dist/。README 281→283。没动系统状态、没 commit。

仍待用户拍板(未擅动): 退旧托盘 pid 1604/5724 以验证新 exe 托盘点亮; 十几批改动
是否按 §23..§40 拆 commit; 开机计划任务 \GPUMigrateWatchdog 指向失效路径要不要修。


## 41. 续三十一：设置窗/详情窗跟上深色主题(美观性)

美观性专项。面板 (_panel_build) 是逐个控件显式配的深色, 但同一个 UI
线程上另外两个窗 —— 设置窗和详情窗(双击进程行弹的) —— 用的是经典
tk 控件且从头到尾没有一行配色: 它们吃的是系统默认浅色。于是托盘点
"打开设置"弹出一个白底黑字的默认窗, 和深色面板并排像两个程序;
详情窗稍好(fg="#666"的标签在浅底上可读), 但窗口本身就是亮的。
_apply_dark_theme() 只换 ttk 皮肤, 管不到 tk.Label/tk.Entry 这些
经典控件 —— 这正是漏的原因。

修法: 新增 _darken_tree(root) + _CLASSIC_DARK 按 winfo_class 分类
刷色(Frame/Label/Button/Checkbutton/Entry/Text/Menu 各有各的选项集,
认不出的类只给 bg/fg 不猜选项 —— 硬塞不支持的选项真 tk 会 TclError),
DFS 遍历整棵控件树, 单个控件 configure 失败只跳过不带崩整窗。设置
窗/详情窗在建完控件后各调一次。保存键的亮绿 #4cd964 在深底上压浅色
字看不清, 换成深绿 #1e7a34 + 浅字, 强调色留住。TrayWindowChrome 补
4 测(假控件树): 基础三色、Entry 的 insertbackground 深色底上必须浅、
Text/Menu 选区同蓝、嵌套 Label 递归刷到、不认识的控件只拿基础色
不塞 insertbackground。全套 287 用例全绿。备份
_gpu_tray_before_darkwin.py / _gpu_tray_fixed_darkwin.py 已存(由修后
源码反转生成, diff 三处与预期一致)。longlines 11/4/3、
scan_odd2/ast_unused/dupcomment 全零、py_compile OK。exe 按标准链
重打: onefile f6d87c7891463da7(onedir 同源码, verify_exe3 双一致)、
--list 48 行 0 乱码 RC=0、onedir 同步 dist/。README 283→287。
(注释定稿后按源码一变就重打的规矩又构建了一次, hash 以 f6d8 为准;
scan_odd2 抓住新增注释里的一个非常用字, 按本项目错字表改掉, 备份同步更正。)
没动系统状态、没 commit。

仍待用户拍板(未擅动): 退旧托盘 pid 1604/5724 以验证新 exe 托盘点亮; 十几批改动
是否按 §23..§41 拆 commit; 开机计划任务 \GPUMigrateWatchdog 指向失效路径要不要修。


## 42. 续三十二：详情窗"当前所在"补上独显侧噪声地板(准确性)

拿"两处说着反话"的尺子量详情窗的"当前所在"一行: 老代码
`on_dgpu = dg > ig`, 独显侧光比大小, 没有地板; 核显侧却有
`ig > 0.1`。dg=0.08/ig=0.05 两个都在噪声级别的读数, 也敢讲
"独显" —— 而 row_is_noise 判同一组数是噪声行(gpu_pct < 0.1),
面板压根不显示它。地板一边有一边无, 就是下一处"同一个数两种说法"
的种子。

修法: 地板抽成 gpu_monitor.NOISE_UTIL_PCT = 0.1, row_is_noise 改引
常量; 判定逻辑抽成纯函数 gpu_where_text(ig, dg) —— 两侧都过地板,
都过了再比大小。详情窗改调它(顺带干掉一个只为排版存在的局部变量)。
DetailGpuWhere 补 5 测: 噪声级读数说"未见 GPU 活动"(修前红:
断言失败, 老逻辑返回"独显")、真占用说独显/核显、都过地板比大的、
以及一条一致性测试 —— 说"未见"时 row_is_noise 必须同判噪声,
防两边地板各自漂移。全套 292 用例全绿。备份
_gpu_monitor_before/after_wherefloor、_gpu_tray_before/after_wherefloor
已存(由修后源码反转生成, diff 与预期一致)。longlines 11/4/3、
scan_odd2/ast_unused/dupcomment 全零、py_compile OK。exe 按标准链
重打: onefile d71d570358cbe9d5(onedir 同源码, verify_exe3 双一致)、
--list 48 行 0 乱码 RC=0、onedir 同步 dist/。README 287→292。
没动系统状态、没 commit。

仍待用户拍板(未擅动): 退旧托盘 pid 1604/5724 以验证新 exe 托盘点亮; 十几批改动
是否按 §23..§42 拆 commit; 开机计划任务 \GPUMigrateWatchdog 指向失效路径要不要修。


## 43. 续三十三：README 配置表补 exclude_defaults 并上锁(准确性/文档)

逐键核对 README 配置表和 DEFAULT_CONFIG: 28 个键里 27 个有文档,
唯独 exclude_defaults(启用内置排除名单)漏了 —— 用户照 README 手改
config.json 时, 想关掉"dwm/explorer 这些常驻组件不迁移"的行为,
翻遍表也找不到这个键。反过来表里写了代码没有的键也会骗人, 两个方向
都得有人看着。

修法: README 配置表补 exclude_defaults 一行(默认 true, 注明内置名单
是 dwm/explorer 等 16 个常驻组件); 新增 ReadmeConfigTable 两个测试
—— DEFAULT_CONFIG 每个键都必须在表里出现(先红: exclude_defaults
缺席), 表里的每个键 DEFAULT_CONFIG 里也必须有(抓反向漂移)。文档
从此和配置结构互相咬着走。全套 294 用例全绿。本轮只碰 README 与
tests, 未动被打包源码, 两个 exe 经 verify_exe3 复核仍与仓库源码
一致, 无需重打。longlines 11/4/3、scan_odd2/ast_unused/dupcomment
全零、py_compile OK。README 292→294。没动系统状态、没 commit。

仍待用户拍板(未擅动): 退旧托盘 pid 1604/5724 以验证新 exe 托盘点亮; 十几批改动
是否按 §23..§43 拆 commit; 开机计划任务 \GPUMigrateWatchdog 指向失效路径要不要修。


## 44. 续三十四：托盘名字缓存加 TTL,pid 复用不再指鹿为马(准确性)

顺着"缓存会让哪处读数变旧"查托盘面板: _pid_name 的 _name_cache 是
pid -> 进程名, 只在条目数超过 2000 时整体清一次 —— 也就是说托盘开
一整天, 缓存里的名字可以白头到老。而 Windows 是会回收 pid 的: 同一个
数字, 十分钟前可能是 chrome, 现在住着 game。一旦复用, 那一行的
GPU% / 显存全是新进程的实时读数, 名字却还是旧进程的, 面板当面指鹿
为马; 监控线程每轮重建 pid_names、Web 面板拿的是新数据, 于是同一台
机器 Web 说 game.exe、托盘说 chrome.exe, 又是那张熟脸。

修法: 给 _name_cache 加 10 秒过期(与 _pref_cache 同一档、同一写法),
__init__ 补 _name_ts; 过期整体清空重查。TrayNameCacheTtl 补两测:
同一 pid 间隔 30 秒先后对应两个进程时必须报新名字(先红: 修前永远
返回 chrome.exe), TTL 内仍吃缓存(每帧每 pid 一次 OpenProcess 的代价
不能回来)。全套 296 用例全绿。备份 _gpu_tray_before/after_namettl.py
已存(由修后源码反转生成, diff 两处与预期一致)。longlines 11/4/3、
scan_odd2/ast_unused/dupcomment 全零、py_compile OK。exe 按标准链
重打: onefile c705ee7baaa5c45a(onedir 同源码, verify_exe3 双一致)、
--list 48 行 0 乱码 RC=0、onedir 同步 dist/。README 294→296。
没动系统状态、没 commit。

仍待用户拍板(未擅动): 退旧托盘 pid 1604/5724 以验证新 exe 托盘点亮; 十几批改动
是否按 §23..§44 拆 commit; 开机计划任务 \GPUMigrateWatchdog 指向失效路径要不要修。

## 45. 续三十五：pythonw 下托盘启动失败不再静默死亡(准确性) + README 自启段按实况改写(文档)

审计 README「开机自启 / 桌面快捷方式」段, 发现文档与系统实况对不上:
文档称桌面有 `GPU 监控.lnk`, 实测真桌面 C:\Users\qm081\Desktop 里没有任何
GPU 快捷方式(还有 emu/game/vr 等文件夹、QRecover.lnk), D:\Users\qm081\Desktop
根本不存在(旧记录把它当桌面是误判); 启动文件夹里只有 `GPU监控自启.lnk`,
用 WScript.Shell 读出来确认它仍指向
`C:\Python314\pythonw.exe D:\aishare\repo\gpu-migrate\gpu_monitor.py --tray`
(源码模式, 仓库搬迁后没坏, 工作目录=仓库, 图标=app.ico)。
schtasks 查询确认看门狗 `\GPUMigrateWatchdog` 还指着搬迁前旧路径
`C:\Users\qm081\.zcode\workspace\default\gpu-migrate\GPUMigrate.exe`,
每 2 分钟触发、Last Result -2147024894 —— 也就是说自启看门狗早已空转,
只是没有任何界面会告诉你。

顺着"pythonw 没有控制台"查代码, 找到一个真 bug: main() 里
`import gpu_tray; gpu_tray.run_tray(args.config)` 毫无保护。自启 lnk 指源码,
pystray 没装、Python 升级换 ABI 时 import 或 run_tray 直接炸, pythonw 下连
traceback 都看不到 —— 用户看到的就是"双击/开机后什么都没发生"。
修法: 新增 report_tray_failure(exc): traceback.print_exc() 留栈(控制台下
不丢线索) + MessageBoxW(MB_ICONERROR) 弹窗说原因和补救(pip install
pystray pillow); main() 只用 try/except Exception 包住托盘启动这一处,
--ensure 与其余路径行为不变。TrayStartupFailure 两测: ImportError 必须
变成弹窗(先红: 修前异常直达顶层), 正常起托盘不弹窗也不吞 run_tray。
全套 298 用例全绿。备份 _gpu_monitor_before/fixed_trayfail.py 已存。

README: 「开机自启」段按实况改写 —— 只承认启动文件夹一个 lnk、桌面默认
不放快捷方式(想要自己建)、.lnk 与看门狗任务都是绝对路径、仓库搬家两处都要
改指向、托盘起不来会弹窗; 用例数 296→298。CRLF 校验通过,
ReadmeConfigTable 防漂移测试仍绿。exe 按标准链重打: onefile
84fa4c660be47c01(旧 c705ee7baaa5c45a)、onedir 同步 dist/、verify_exe3 双一致、
verify_imports 4/4、--list 48 行 0 乱码 RC=0、longlines 仍 11/4/3、
scan_odd2/ast_unused/dupcomment 全零、py_compile OK。
COMMIT_PLAN 同步至 §23..§45 / 23 commits / 298。没动系统状态、没 commit。

## 46. 续三十六：看门狗 --ensure 托盘启动失败不再弹窗(准确性)

§45 给托盘启动加异常保护后回头看自己: main() 里 --tray 和 --ensure 共用
`import gpu_tray; gpu_tray.run_tray(...)`, 保护也一起共享 —— 意味着
pystray 缺失时, 每 2 分钟一次的看门狗也会弹一次错误窗, 直接违反 §40 立下的
"看门狗必须静默"契约(notify_already_running 注释原话: --ensure 绝不能弹窗)。
先红后绿: TrayStartupFailure 新增 test_ensure_start_failure_stays_quiet,
修前 --ensure 失败会弹窗且不写日志(红), 修后按入口分流: --tray 走
report_tray_failure 弹窗, --ensure 只往 gpu_monitor.log 记一行
`托盘启动失败(看门狗 --ensure): ...`(绿)。全套 299 用例全绿。
longlines 仍 11/4/3, scan_odd2/ast_unused/dupcomment 全零, py_compile OK。
备份 _gpu_monitor_before/fixed_ensurequiet.py 已存。README 用例数
298→299, 自启段补"看门狗 --ensure 触发时不弹窗, 只往 gpu_monitor.log 记一行"。
exe 重打: onefile bd075bd5fd7252de(§45 轮 84fa4c660be47c01)、onedir 同步
dist/、verify_exe3 双一致、verify_imports 4/4、--list 48 行 0 乱码 RC=0。
COMMIT_PLAN 同步至 §23..§46 / 24 commits / 299。没动系统状态、没 commit。

## 47. 续三十七：日报"游戏约 N 分钟"按名字去重、按墙上时钟记账(准确性)

查"还有哪处读数会两处说着反话"时看游戏时长: 循环里
`game_secs[pname2] += max(0.2, elapsed_prev[0])` 写在按 pid 遍历的循环内,
时长却按进程名记账 —— 一个游戏开 N 个同名进程(启动器+主程序, 或名单里
放了浏览器), 一轮就被记 N 份, 日报按进程数翻倍; 同时 elapsed_prev 只是
上一轮采样耗时(约 0.2s), 不含 sleep 间隔, 墙上过一分钟只记进去零点几秒。
两个方向都错。打桩时钟实测(每轮 4 次 time.time, 每次 +15s, 两个同名 pid
跑 4 轮): 修前记 272.0s, 正确值 180.2s。
修法: 抽出纯函数 game_time_credit(names_seen, span, game_secs); pid 循环
里只攒 seen_games 集合, 记账挪到循环外按名字去重加一份; span 取两轮
now_ts 的墙上时钟跨度(cycle_ts 记上轮 now_ts, 首轮退化成一个配置间隔);
elapsed_prev 随之无人读取, 连写入一并删掉。
GameMode 两测: 循环级(spy append_history 抓 secs, 先红 272.0 → 绿 180.2)
+ 记账函数直接测(0.2s 下限/可加性)。全套 301 用例全绿。longlines 仍
11/4/3, scan_odd2/ast_unused/dupcomment 全零, py_compile OK。
备份 _gpu_monitor_before/fixed_gametime.py 已存。README 用例数 299→301,
新增"日报口径"条目(进程名去重+墙上时钟)。exe 重打: onefile
94cb19677b3b55db(§46 轮 bd075bd5fd7252de)、onedir 同步 dist/、verify_exe3
双一致、verify_imports 4/4、--list 47 行 0 乱码 RC=0(行数随当时在跑的
GPU 进程数浮动, 基线 48)。COMMIT_PLAN 同步至 §23..§47 / 25 commits / 301。
没动系统状态、没 commit。

## 48. 续三十八：趋势图角标时长改按真实时间戳, 不再按点数虚报(准确性)

顺着"还有哪处读数会两处说着反话"查趋势图: spark_corner_text 的时长是
`len(pts) * 10` 推出来的, 而实际点距只在稳态才正好 10 秒 —— 采样卡顿
(单轮超过 10 秒)或系统休眠唤醒后, 点距拉长, 180 个点能跨好几个小时,
角标却照写"近30分钟": 图还是那张图, 标题已经在撒谎。先红后绿: 新增
两测(30 个点跨 29 秒必须说"近39秒"而非"近5分钟"; 休眠唤醒跨一小时必须
说"近1.0小时"), 修前红。修法: span 改按首末时间戳差 + 一个采样周期,
分秒/分/小时三档表述。两个既有用例的数据原本用 1 秒点距(本身就违背
"每 10 秒一点"的契约), 一并改成 10 秒节陈, 期望值随之按新口径更新。
全套 303 用例全绿。longlines 仍 11/4/3, scan_odd2/ast_unused/dupcomment
全零, py_compile OK。备份 _gpu_monitor_before/fixed_spanspan.py 已存。
README 用例数 301→303, 趋势图条目补"角标时长按真实时间戳算"。
exe 重打: onefile a8c11af2ff35cfd2(§47 轮 94cb19677b3b55db)、onedir 同步
dist/、verify_exe3 双一致、verify_imports 4/4、--list 47 行 0 乱码 RC=0。
COMMIT_PLAN 同步至 §23..§48 / 26 commits / 303。没动系统状态、没 commit。

## 49. 续三十九：面板刷新失败不再完全静默(准确性/排障)

顺着"失败路径"查托盘: 面板的 refresh() 闭包 except 后直接 pass。_refresh_tree
或 _draw_sparkline 一旦每个周期都抛(快照契约变了、控件被销毁), 面板就永远
停在最后一次成功的样子, 用户以为监控卡了, 而 tray_debug.log 里一个字都没有
—— 和 §40 修的单例静默消失同一类病。修法: 抽出 _safe_refresh(tree, tip,
card_labels, hint, spark) 方法: 刷新链照活(异常不抛给 after), traceback 落
tray_debug.log, 且同一个错只记第一次(每秒一轮, 每次都写会把日志刷成噪音),
恢复正常后清掉标记。先红后绿: 拿 _gpu_tray_before_saferefresh.py 临时换回
旧码, 新测 3 个全 AttributeError 红; 换回修复后全绿。TrayRefreshFailure
三测: 失败留痕不抛 / 同错只记一次 / 正常刷新不写日志且清标记。
全套 306 用例全绿。longlines 仍 11/4/3, scan_odd2/ast_unused/dupcomment
全零, py_compile OK。备份 _gpu_tray_before/fixed_saferefresh.py 已存。
README 用例数 303→306; 冻结路径清单补上漏记的 `report.csv`;
 tray_debug.log 用途补了一句。exe 按标准链重打: onefile
f9c76fb191f538fb(§48 轮 a8c11af2ff35cfd2)、onedir 同步 dist/、verify_exe3
双一致、verify_imports 4/4、--list 47 行 0 乱码 RC=0。
COMMIT_PLAN 同步至 §23..§49 / 27 commits / 306。没动系统状态、没 commit。

## 50. 续四十：悬停提示补独显温度，别跟着长卡名一起被砍(准确性/美观性)

Web 面板的汇总区一直有"独显温度"，托盘悬停却没有 —— 用户想确认一眼温度非得
去点面板或翻日志。根因在数据侧：`on_sample` 钩子快照的键集合和
`_LAST_SNAPSHOT` 不一致，托盘侧就算想显示也没有数据源。补 `pid_prefs` 与
`dgpu_temp` 两个键，两边从此同一份快照、同一句文案。

温度排在哪一行是这轮真正的坑。第一版跟在"核显: <卡名>"同一行尾部，
`test_a_huge_adapter_name_does_not_kill_the_whole_tooltip` 那条老测试照样绿
—— 但真机上卡名一长，`fit_tip_title` 按"超长就砍最后一行"把温度和名字一起
砍了。温度每 10s 在变，卡名一万年不变，先死的居然是温度。于是拆成独立一行
排在卡名之前：名字仍是最后一行，被截断的永远是名字。先红后绿：
先加 `test_surviving_temp_outlives_a_long_adapter_name`，在"同行"版上确认
它确实把温度顶没了，再改成"独立行置前"。

SnapshotContract 补 2 键断言(7 测)，TrayModeDisplay 补 2 测：有温度时显示
`独显温度: 61°C`、读不到时整行消失(不显示 0°C 或 None)，再加长卡名存活那条。
全套 309 用例全绿。longlines 仍 11/4/3，scan_odd2/ast_unused/dupcomment 全零，
py_compile OK。备份 _gpu_tray_before/fixed_tiptemp.py、_gpu_monitor_before_snapkeys.py
已存。README：用例数 306→309，悬停那句从"显示显卡信息"改成
"核显占用、生效策略和独显温度（与 Web 面板同一句文案，读不到温度时那行自动消失）"。
exe 按标准链重打：onefile e9d4e8e620c9df7e、onedir 同步 dist/、verify_exe3
双一致、verify_imports 4/4、--list 48 行 0 乱码 RC=0。COMMIT_PLAN 同步至
§23..§50 / 28 commits / 309。没动系统状态、没 commit。

## 51. 续四十一：托盘详情窗别再混两套读数，Web 活端点别再被缓存(准确性)

交接清单里挂着两条，一起做掉。

**详情窗混读两个时刻。** 面板每秒刷一次，双击落在两次刷新之间时，进程可能
正好退出、pid 被复用，或者从"真在算"掉成噪声行。老代码 GPU% 读点击那一行
(`vals`)、专用/共享内存读 `self.snapshot`，于是同一个窗口里能出现
"GPU% 45.2% 但 0 MB / 0 MB" 这种自己打自己的读数；pid 真的没了也只会得到
一排 0，用户分不清是"闲着"还是"已经走了"。改法：这一窗的每个数都取自点击
那一行（行里本来就带齐 GPU%/核显%/独显%/两份显存），另加模块级
`_row_num(vals, idx)` 把 "45.2%" 这类单元格安全还原成 float（缺列/脏值回
0.0，不能再让详情窗开不出来）；pid 不在本轮快照里时，窗口第一行写明
"该进程已不在本轮采样中(可能已退出或 pid 被复用), 下面是点击那一行的读数"。
`gpu_where_text` 的地板判据不动，仍和 `row_is_noise` 同一档。

**Web 活端点没有缓存头。** 前端 `setInterval(pull,1000)` 每秒 fetch 同一个
URL，服务端却只发 `Content-Type`。浏览器或中间代理一旦把上一秒的整页缓存
下来，画面就停在旧占用上，看着像监控卡了。`do_GET` 补
`Cache-Control: no-store, no-cache, must-revalidate` + `Pragma: no-cache`。

先红后绿：`test_detail_reads_the_clicked_row_and_flags_a_gone_pid`（行里的
120 MB / 40 MB 必须原样出现、pid 走了必须说话）与
`test_bind_success_logs_the_url_and_serves_the_panel` 里新增的缓存头断言，
在旧码上分别红在"显存读成快照"和"没有 Cache-Control"。
全套 310 用例全绿。longlines 仍 11/4/3，scan_odd2/ast_unused/dupcomment
全零，py_compile OK。备份 _gpu_monitor_before/fixed_nostore.py、
_gpu_tray_before/fixed_detailrow.py 已存。踩坑一次：插入新测试时把原方法的
`def` 行连带替换掉，两个方法体粘成一个，表现是"同一批回调被派发两次"，
靠 traceback 里的行号才定位到；已补回 def 行。
README：用例数 309→310，Web 面板条目补缓存头一句。exe 按标准链重打：
onefile 63de6a5b7abb9322（§50 轮 e9d4e8e620c9df7e）、onedir 同步 dist/、
verify_exe3 双一致、verify_imports 4/4、--list 48 行 0 乱码 RC=0。

## 52. 续四十二：高分屏不再糊，panel_ui.json 存坏了不再打死面板(美观/准确性)

先查证再动手。**DPI**：PyInstaller 默认 manifest 只带 `asInvoker` +
supportedOS + Common-Controls 6，`dpiAware`/`dpiAwareness` 一个字都没有
（直接在 GPUMigrate.exe 里逐字节搜过）。于是进程 DPI-unaware，Windows 按
100% 渲染再拉伸到 150%/200%，托盘图标、面板、详情窗全是糊的，固定
1040x470 的面板在高分屏上还会被切掉一截。没有自定义 manifest 也能在进程内
声明：`run_tray` 第一件事调 `enable_dpi_awareness()`，依次试
`SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2=-4)` →
`shcore.SetProcessDpiAwareness(2)` → `user32.SetProcessDPAware()`，全失败
就安静返回 False。时机是关键：建过第一个窗口之后再设，Windows 直接拒绝。
真机验过：调用前 awareness=0，调用后=2(per-monitor v2)。
写这条测试时它还抓到一个真 bug——`ctypes.windll` 取不到时我只捕了
`OSError`，AttributeError 会穿出去把托盘带崩；改成两类都捕。

**panel_ui.json**：`_panel_build` 里 `ui = json.load(f)` 之后直接 `ui.get(...)`。
这文件是人也能改的，存成 `[]` / `"abc"` / `123` 都算合法 JSON，顶层一不是
对象就 AttributeError，被 `_panel_build` 的总 except 吞掉 —— 窗口建一半扔下，
而且每次点"打开实时面板"都照样炸，用户看到的是"菜单点了没反应"。新抽
`_load_ui_state()`：顶层不是对象就按默认布局，`geometry` 不是字符串复位，
`widths` 只收得下正整数（字符串/None 会让 `tree.column` 抛 TclError），
`sort` 不是对象置空。丢一次布局能忍，面板从此打不开不能忍。

先红后绿：`TrayDpiAwareness` 两测（建窗口前必须声明感知、拿不到 API 不抛）
+ `test_a_nondict_saved_layout_does_not_kill_the_panel`（`[]`/`"abc"`/`123`/
`null` 四种垃圾各开合一次面板）在旧码上全红。全套 313 用例全绿。
longlines 仍 11/4/3，scan_odd2/ast_unused/dupcomment 全零，py_compile OK。
备份 _gpu_tray_before/fixed_dpiui.py 已存（dpiui2 是取库异常的收尾版）。
exe 按标准链重打：onefile 9de44fb68ebb59a1（§51 轮 63de6a5b7abb9322）、
onedir 同步 dist/、verify_exe3 双一致、verify_imports 4/4、
--list 48 行 0 乱码 RC=0。
README：用例数 310→313；趋势图条目补高分屏一句；panel_ui.json 那句补"存坏
了按默认布局打开"。

## 53. 续四十三：注册表读不到≠没有，--unset-all 清不掉要点名(准确性)

先证伪一个候选：怀疑过 `history.jsonl` 单行损坏会让趋势图整段失效，
查完发现两个面板的趋势都来自内存里的 `hist_points` 环形队列，落盘那份
只写不读 —— 没有这个 bug，不动它。

真正的问题在注册表首选项这一侧的报数口径：
1. `list_gpu_prefs()` 打不开键就回 `[]`，和"键不存在"混作一谈。
   `cmd_status` 于是把"权限被策略挡掉、一条也没读到"播报成
   "当前没有任何 GPU 首选项设置。" —— 用户会以为自己从没迁移过。
   现在 `RegOpenKeyExW` 非 0 时按错误码分岔：`ERROR_FILE_NOT_FOUND(2)`
   回 `[]`，其余（读不到）回 `None`，调用方分别说"没有"和"读不到"。
2. `--unset-all` 只 `print(f"已清除 {len(cleared)} 项。")`。列了 5 项、
   删掉 2 项，用户看到"已清除 2 项"会以为另外 3 项本来就不存在 —— 而那
   几项可能正被权限或占用挡着。现在报 `已清除 N/M 项`，并把
   `failed = [没出现在 cleared 里的]` 逐个点名。顺带给 `cmd_unset_all`
   加 `assume_yes`，测试才跑得到确认之后那段。
3. `cmd_set` 里 `set_gpu_preference` 的 `OSError` 没人接，写失败直接甩
   traceback；而 `--set` 多半是排查时敲的，那会儿最需要的是"哪条没写进去、
   为什么"。

先红后绿三条：`test_status_says_so_when_the_registry_cannot_be_read`、
`test_unset_all_reports_what_it_could_not_clear`（3 项里 1 项删不掉，
必须出现 2/3 和那一项的名字）、`test_set_reports_a_write_failure_instead_of_a_traceback`。
真机验过：本机 `--status` 正常打出 32+21 条；模拟读不到时输出"读不到 GPU
首选项: 注册表键打不开(权限被限制?), 这不等于没有设置。"；全删失败时输出
"已清除 0/1 项"并点名。全套 316 用例全绿。longlines 11/4/3
（新测试里有行 81 字符，已改回），scan_odd2/ast_unused/dupcomment 全零，
py_compile OK。备份 _gpu_monitor_before/fixed_prefmsg.py 已存。
README：用例数 313→316；`--unset-all` 示例下补两行口径说明；
"其它"里新增一条"注册表读不到不等于没有"。
exe 按标准链重打：onefile 11a22955bbcc13c7（§52 轮 9de44fb68ebb59a1）、
onedir 同步 dist/、verify_exe3 双一致、verify_imports 4/4、
--list 48 行 / --status 56 行，均 0 乱码 RC=0。

## 54. 续四十四：迁移验证闭环不再静默丢单，写入必须回读确认(准确性)

两条候选一起做，其中第一条顺带挖出一条真机必崩的潜伏 bug。

**验证闭环超时静默。** `cmd_monitor` 里 `pending_verify` 那段：90 秒内
进程一次 GPU 活动都没有就 `del` 掉走人，零日志零通知。用户刚看完"迁移
成功，进程已自动重启"，然后什么都没有 —— 既没说生效也没说没生效。抽成
模块级 `verify_pending(pending_verify, usage, igpu_luids, dgpu_luids, now,
cfg, logfile)`，超时时补一句"重启后 N 秒内没有 GPU 活动，无法确认是否
运行在独显上"+ 通知"迁移未确认"。三种结局现在都有回执。

**抽函数当场炸出潜伏 bug**：原代码
`luids_seen = {luid for (pid2, luid, _ph) in usage.items() ...}` ——
`usage` 的键是 `(pid, luid, phys)` 三元组，`items()` 每个元素却是
`(键, 值)` 二元组，按三元组解包必然 `ValueError`。这段只在
`auto_restart` 真的重启成功、`pending_verify` 非空时才跑到，而测试夹具的
`restart_process` 默认返回 None、迁移后又往往没有后续周期，所以从来没有
任何用例碰过它 —— **真机上开了 auto_restart 的用户，迁移成功那一刻监控
循环就被掀翻，托盘显示"GPU 监控已停止"**。改成遍历键。并补
`test_verification_runs_after_a_real_restart`：让 restart_process 返回
4321、后续轮采样里放 `({4321: 0.0}, {4321: 60.0})`，验证闭环必须真的跑完。

**写入不回读。** `_pref_apply` 写完就弹"已设为独显：重启后生效"，依据只是
API 没抛异常。杀软/策略拦在半路时调用可能"成功"而值没落地，用户白重启
一次发现还在核显上，比当场说失败糟糕得多。现在写和清都回读
`get_gpu_preference` 确认，没落地就返回 False，详情窗改口"写入注册表失败"。

顺带：`verify_pending` 无条件调一次 `time.time()`，让
`test_game_time_counted_once_per_name_per_cycle` 的白盒时钟假设从 4 次/轮
变 5 次/轮（期望值 180.2 → 225.2），注释已同步说明第 5 次从哪来。

全套 322 用例全绿。longlines 11/4/3，scan_odd2/ast_unused/dupcomment 全零，
py_compile OK。备份 _gpu_monitor_before/fixed_verify.py、
_gpu_tray_before/fixed_readback.py 已存。README：用例数 316→322，
"其它"里新增"迁移效果闭环"一条。exe 已于 §55 按标准链补齐重打。

## 55. 续四十五：§54 的 exe 补齐、桌面快捷方式、纠正一个误判(交付)

上一轮 §54 只改了代码没重打 exe，这一轮按标准链补齐，并把拖着的几件事一起
收口。本轮没有源码改动，因此没有新测试，用例数仍是 322。

**exe 重打(§54 之后正式交付)。** onefile sha256[:16]=95e58c5ccaf18533
(§51 轮 11a22955bbcc13c7)，onedir 同步到 dist/GPUMigrate-onedir/。
verify_exe3 里两个 exe 的 gpu_monitor / gpu_tray 都与仓库源码逐项相同
(co_code、常量池、co_names、co_varnames 全对)；verify_imports 4/4；
--list 48 行 0 乱码 RC=0；--status 56 行 0 乱码 RC=0，真机正常打出
"已迁移到独显 (32 个)"和"其他设置 (21 个)"，说明 §53 改的"读不到不是没有"
这条口径在健康机器上走的确实是正常分支。322 用例全绿，
longlines 11/4/3，scan_odd2/ast_unused/dupcomment 全零，py_compile OK。

**纠正上一轮的一个误判**(关系到怎么读单例)。交接里写"旧托盘 pid
1604/5724 占单例互斥体"，还猜第二个进程卡在 notify_already_running 的模态
MessageBoxW 里没人点确定。这轮退掉旧实例再启动新 exe 之后实测：新 exe 起来
同样是两个 pid、命令行一模一样、启动时间差 2 秒，而且子进程的
ParentProcessId 正是父进程。这是 PyInstaller onefile 引导程序的正常结构：
父进程只负责把包解压到临时目录，子进程才是真正跑 Python 的那一个，
日志里因此也只有一条 monitor enter。所以 1604/5724 从头到尾就是同一个
实例的父子对，不是双实例，更不是卡死的对话框。教训：看到两个同名进程，
先查 ParentProcessId 和 CommandLine 是否一致，再决定要不要按
"互斥体失效"结案，别急着按双实例处理。

**桌面快捷方式**(按你"快捷放到桌面"的要求补的)。落在
D:\Users\qm081\Desktop\GPU迁移工具.lnk —— 这一台把 Desktop 重定向到了
D:\Users(注册表 User Shell Folders 里 Desktop = D:\Users，和
Cursor.exe.lnk、Docker Desktop.lnk 并排),所以用
[Environment]::GetFolderPath("Desktop") 取路径而不是硬编码。目标
D:\aishare\repo\gpu-migrate\GPUMigrate.exe --tray，工作目录设成仓库根，
图标取 app.ico。工作目录必须设：不设的话 exe 按启动目录找 config.json 和
show_toast.ps1，面板和通知都会静默失灵。建完实跑验证：托盘进程在，
http://127.0.0.1:8787/ 返回 200 且带
Cache-Control: no-store, must-revalidate, no-cache + Pragma: no-cache
(§51 那两条头在真机上确认生效)，/data 同样 200。
顺手清掉了桌面上两个上一轮遗留的失效快捷方式(目标指向已经不存在的旧
dist 路径)，现在只有一个入口。

**踩坑：PowerShell 5.1 的 Set-Content -Encoding utf8 不带 BOM。**
第一版用 Set-Content -Encoding utf8 写 .ps1 再 powershell -File 跑，
脚本里的中文被按 GBK 解码，快捷方式文件名变成了
GPU杩佺Щ宸ュ叿.lnk。PS 5.1 的 utf8 是无 BOM 的 UTF-8(PS 6+ 才带 BOM)，
而 -File 读脚本按系统 ANSI 代码页解码，两边对不上就花。正确做法：用
Python 以 utf-8-sig 写脚本文件(带 BOM)再执行。以后凡是带中文的
.ps1/.cmd 都走这条路，别再信 Set-Content -Encoding utf8。

仍待用户拍板(未擅动): §23..§55 是否按 COMMIT_PLAN 拆 commit;
看门狗计划任务 \GPUMigrateWatchdog
修不修(schtasks /create /tn "\GPUMigrateWatchdog" /f /sc minute /mo 2 /st 00:00
/tr "D:\aishare\repo\gpu-migrate\GPUMigrate.exe --ensure" /it)。

## 56. 续四十六：Web 面板的 HEAD 不再回 501(准确性)

真机探针先证伪再动手：对着跑起来的 `http://127.0.0.1:8787/` 连发三种方法，
`GET /` 200 正常，`HEAD /` 回 **501 Unsupported method ('HEAD')**，
`POST /` 同样 501。只修 HEAD，不改别的。

`web_thread` 里的 handler 只实现了 `do_GET`。HEAD 是 RFC 7231 4.3.2 定义的
标准只读方法，`BaseHTTPRequestHandler` 没有默认实现，于是任何发 HEAD 的
东西都吃到一个 501: 健康检查、`curl -I`、监控探针、浏览器预取。面板明明
活着，巡检里却是红的 —— 这正是"程序看起来正常、外部判定它坏了"的那类
不准。POST/PUT 这类没有语义的方法继续留在默认 501, 面板本来就是只读的，
硬造 405 只是噪音。

顺手把发响应头那段抽成 `_panel_headers(nbytes)`，GET 和 HEAD 共用。顺带补上
`Content-Length`: 原来 GET 也不带，靠"连接关闭即正文结束"兜着(协议版本是
HTTP/1.0，凑巧能work)；现在 GET 和 HEAD 都明确报长度，HEAD 报的必须等于 GET
真会发出的字节数。HEAD 里仍然完整算一遍页面而不是报 0 —— 长度报 0 会让
调用方以为资源是空的，比多算一次更糟。

先红后绿：`test_a_head_probe_gets_the_same_headers_without_a_body` 用裸
socket 发 `HEAD / HTTP/1.0`，断言状态码 200、body 为空、带 no-store/no-cache、
Content-Length 与 GET 实际字节数一致、三种头逐个和 GET 对齐。旧码上红在
`HTTP/1.0 501 Unsupported method ('HEAD')`。写完第一版自己的断言倒错了
(拿整行状态行 `startswith("200")`)，已改成拆出状态码比。"只回退这一处"
验证过：把 `do_HEAD` 摘掉、其余全留着，该测试立刻回到 501 失败，还原后绿。

全套 323 用例全绿。longlines 11/4/3，scan_odd2/ast_unused/dupcomment 全零，
py_compile OK。备份 _gpu_monitor_before/fixed_head.py 已存。
README：用例数 322→323，Web 面板条目补 HEAD 一句。exe 按标准链重打: onefile sha256[:16]=3ffa693115ce8313
(§55 轮 95e58c5ccaf18533)，onedir 已同步 dist/GPUMigrate-onedir/，
verify_exe3 双一致、verify_imports 4/4、--list 48 行 / --status 56 行
均 0 乱码 RC=0。真机复测三种方法: GET 200 body=10344、
HEAD 200 body=0 且 Content-Length 同为 10344、POST 仍 501(面板
本就只读)。系统自带 curl -I 也拿到 200 + 完整响应头。


## 57. 续四十七：清除首选项分清"没东西可清"和"清不掉"(准确性)

上一轮 §56 收口后挑候选时, 发现 §54 刚加的"写入回读"只做了一半:
失败时详情窗只有一句"未能清除: 可能本来就没有独显设置"。核对底层语义
发现这句在两个完全不同的场景下都会出现, 而且其中一个是反的。

根因: `clear_gpu_preference` 只回布尔。而"这一条本来就不存在"和
"键打不开/删不掉"都会给它一个 False —— 上游拿到的信息量和失败时
一模一样, 只能猜。按 §53 `list_gpu_prefs` 已经立好的规矩改成三态:
True 删掉了 / None 本来就没有 / False 真失败。`cmd_unset` 和托盘详情窗
都跟着分岔, 各说各的话。

**过程中踩了一个真坑, 值得单独记。** 第一版按 §53 注释里的思路去读
`ctypes.get_last_error()` 判 ERROR_FILE_NOT_FOUND, 单测全绿(因为测试
mock 掉了 `clear_gpu_preference`, 根本没走到真注册表), 但真机一跑:
`--unset` 一个从没设过的文件, 报的是"清除失败(权限被限制?)"。写探针
单独调 `RegDeleteValueW` 才发现: **这类 advapi32 API 把错误码放在返回值
里(LSTATUS), 不走 SetLastError** —— 本机实测 ret=2 时
`ctypes.get_last_error()` 仍然是 0。改读返回值即修复。这也说明
"单测全绿"不等于"路径被覆盖": 三个 mock 掉底层的用例一条都没红,
是 fake advapi32 那条补上来才把 bug 抓住的。

`_pref_apply` 返回值仍是布尔(成功/失败), 但把原因写到
`self._pref_err`: 干净成功=None, 空清除="noop", 失败=一句人话。新增
`_pref_err_text()` 一律 getattr 取值并保证非空 —— tk 回调里取不存在的
属性只会往 stderr 打一条, pythonw 下直接丢, 表现为"点了没反应"。
空清除不再是失败, 但也不谎称"已清除", 改口"本来就没有独显设置"。

先红后绿 7 条: 托盘侧三条(空清除不算失败但要留记号、被挡的清除不许
假扮"本来就没有"、写入没落地要点明是回读没过) + CLI 三种结局分开说
+ fake advapi32 三条走真实 `clear_gpu_preference`(0/2/5/87 的映射、
键打不开是真失败、键不存在是空)。三条 mock 用例在旧码上红在
`_pref_err` 缺失; fake advapi32 那条在"读 last_error"的错版上红在
`ret=2 该映射成 None`。全套 330 用例全绿。longlines 11/4/3,
scan_odd2/ast_unused/dupcomment 全零, py_compile OK。
README: 用例数 323→330, `--unset` 口径补三态说明。
exe 按标准链重打: onefile sha256[:16]=be9167e23db47a38
(§56 轮 3ffa693115ce8313)，onedir 已同步 dist/GPUMigrate-onedir/，
verify_exe3 双一致、verify_imports 4/4、--status 56 行 / --list 47 行
均 0 乱码 RC=0。真机验 --unset 一个从没设过的程序:
“本来就没有独显设置: <路径>”, 文件落盘 0 乱码。


## 58. 续四十八：set_gpu_preference 的真实字节也被盯上了(准确性)

§57 用 fake advapi32 抓住了 `clear_gpu_preference` 读错错误码来源的 bug,
于是顺手查另一边: `set_gpu_preference` 有没有被真实路径覆盖过。结论是
**完全没有** —— 它只在 §53 用 lambda mock 测过 CLI 的错误分支, 真正调
`RegSetValueExW` 的那几行(类型、cbData、缓冲)一个字节都没被验过。
这条路径恰恰是整套迁移的地基: 写错一个字符, 后面所有"已迁移"的判断都
建立在错数据上。

fraude 手法照抄 §57: 假 advapi32 记下 `RegSetValueExW` 收到的
(name, type, cbData, 前 cb 字节), 然后逐个断言。三条:

- **字节数正好是 (值 + 结尾 NUL)**。cbData 少算一个字符, Windows 读回的
  就是缺尾分号的串 —— 而 `is_high_performance` 按分号切开逐段比, 少一个
  分号整条判不出已迁移, 于是进程下一轮又被重写一遍再弹一次通知。
  (缓冲是 `create_unicode_buffer`, 比 cb 长的部分纯属余量, 送不进去。)
- **type 是 REG_SZ(1)**, 且句柄必须 `RegCloseKey` —— 失败路径也要关,
  否则每次写失败漏一个句柄。
- **失败要变成 OSError 且带上错误码**, 上层才能说人话; 键都建不成时
  不许再去碰值。

_ctypes 数组没有 `.raw`, 第一版照 `FakeValueQuery` 的写法取字节直接
AttributeError; 改用 `ctypes.string_at(buf, cb)` 才对 —— 而且这样取出来
的正好是被送进 API 的那一段, 比整个缓冲更贴近事实。

真机交叉验证: 用探针真写一条再 `RegQueryValueExW` 读回, 实测
`type=1`、`cbData=34`(17 个 UTF-16 字符含 NUL), 与 fake 预期逐项相同;
读写闭环后清掉, 再读返回 None。"只回退这一处"验证过: 把
`(len(value) + 1) * 2` 改成 `len(value) * 2`, 新测试立刻红在 32 != 34。

全套 333 用例全绿。longlines 11/4/3, scan_odd2/ast_unused/dupcomment 全零,
py_compile OK。README: 用例数 330→333。
exe 按标准链重打: onefile sha256[:16]=9de32c179c0d445c
(§57 轮 be9167e23db47a38)，onedir 已同步 dist/GPUMigrate-onedir/，
verify_exe3 双一致、verify_imports 4/4、--status 56 行 / --list 47 行
均 0 乱码 RC=0，--unset 未设过程序仍报“本来就没有独显设置”。


## 59. 续四十九：日报写不进 report.csv 不再一声不响(准确性)

§58 收口后接上候选清单里剩下的 report.csv 一条, 一查就是老问题:
`append_history` 跨日写 CSV 那段 `except OSError: pass`。同函数上方
history.jsonl 那个 except 也是 pass, 但那个最多丢一个采样点; CSV 这条
不一样 —— **Toast 通知已经弹出"2000-01-01: 核显峰值 77.7%, 迁移 2 个
程序"了, 文件里却没有那一行**。用户照着通知去翻, 只会以为程序在编数字,
或者以为自己看错了文件。和 §45 修 Web 面板 bind 失败是同一类: 起不来
可以, 起不来还装成成功了不行。

修法沿用 `notify()` 里 show_toast.ps1 丢失时的老规矩: 留一行日志。
`log(f"日报落盘失败 (report.csv): {e}", _LOGFILE)` —— 带上 errno 和路径,
因为"打不开"的原因太多了(被 Excel 占着、只读、磁盘满、路径不存在),
日志得能让人直接定位。用 `_LOGFILE` 而不是硬编码路径, 和 notify 那边一致:
`log_to_file=False` 时它就是 None, 只走 print, 不会凭空多写一个文件。

先红后绿: `test_a_report_csv_that_cannot_be_written_is_logged` 用
`os.chmod(0o444)` 让追加真的失败(不是 mock open, 是真的 OSError),
断言三件事 —— 原来那行没被写坏、日志里出现 report.csv 和"日报"字样、
通知照样发了一条(说明"通知发了但没落盘"这个组合确实存在)。旧码上红在
`日报落盘失败却零日志: []`。"只回退这一处"验证过: 把 log 那行删掉立刻红。

真机复验: 造临时目录 + 只读 report.csv, 日志确实落下
`[2026-09-26 09:24:25] 日报落盘失败 (report.csv): [Errno 13] Permission
denied: ...`, 且原文件内容未被破坏。

全套 334 用例全绿。longlines 11/4/3, scan_odd2/ast_unused/dupcomment 全零,
py_compile OK。README: 用例数 333→334。
exe 按标准链重打: onefile sha256[:16]=a02c4d3bc069f52a
(§58 轮 9de32c179c0d445c)，onedir 已同步 dist/GPUMigrate-onedir/，
verify_exe3 双一致、verify_imports 4/4、--status 56 行 / --list 47 行
均 0 乱码 RC=0。

## 60. 续五十：汇总卡不再吐出 `class=` 空属性(美观/准确性)

§59 之后候选清单见底, 改成系统审计剩余的静默失败模式。摸到 Web 面板时
顺手拉了活页面的真实 HTML 来看(而不是只读源码), 一眼看到:

    <div class=card><span>专用显存</span><b class=>11712 MB</b></div>

`class=` 后面直接跟 `>` —— 无值属性, 畸形 HTML。来源是 `panel_summary`
对显存/进程数/阈值那几张卡返回空类名(热度只对两个占用率有意义), 而
`panel_html` 的模板无条件拼 `class={cls}`。

浏览器会把它兜底成 `class=""`, 所以肉眼看不出差别, 六个卡照常渲染。
但这是个真实缺陷: 无值属性说明模板没有为"类名可选"这条路上心。今天
cls 只会是 hot/warm/cool/空, 哪天里面带进一个空格或引号, 属性就会当场
截断, 而且截断的位置取决于数据 —— 正是最难复现的那类 bug。顺手查了
托盘侧, 那边走 `_heat_color("")`, 字典取不到有兜底, 没问题; 只有 Web
这个字符串模板在裸奔。

改成 cls 非空才输出整个属性。测试 `test_summary_cards_...` 直接断言
产物里不出现 `class=>`(而不是断言"看起来对"), 并保留有热度类那两张仍带
class 的检查。

写这条时踩了自己的坑: 第一次 splice 把测试插进了 `EmptyRowText` 类(锚点
方法名在那边重名), 跑出来是绿的 —— 因为那条断言其实有效, 只是放错了类。
是 `run_one` 报的用例数和预期不符才发现。已把测试移到 `PanelSummaryCards`
并验证它单独跑确实红(旧码上红在 `class=> unexpectedly found`)。
"只回退这一处"也验过: 把模板改回无条件拼接, 立刻回到同样的红。

全套 335 用例全绿。longlines 11/4/3, scan_odd2/ast_unused/dupcomment 全零,
py_compile OK。README: 用例数 334→335。
exe 按标准链重打: onefile sha256[:16]=6fcd7290d323793f
(§59 轮 a02c4d3bc069f52a)，onedir 已同步 dist/GPUMigrate-onedir/，
verify_exe3 双一致、verify_imports 4/4、--status 57 行 / --list 48 行
均 0 乱码 RC=0，--unset 未设过程序仍报"本来就没有独显设置"。
重打前根目录 exe 被两个旧托盘 pid(20624/41676)占着，先停掉它们才同步成功；
托盘已用新 exe 重启(pid 40536/34372，一体版的父子进程对)，gpu_monitor.log
记到"09:42:51 启动监控/Web 面板 http://127.0.0.1:8787"，活页面实测
200/10680 字节、无 `class=>`、Cache-Control: no-store。

## 61. 续五十一：--set / --unset 不再拿一句假成功收尾(准确性)

§60 收口后接着审计"写入声明"这一类。§54 给托盘 _pref_apply 补了回读,
但命令行那两个口子一直是裸的: RegSetValueExW 返回 0 就打印"已设置独显",
RegDeleteValueW 返回 0 就打印"已清除设置", 谁都没有回读确认值真的落了地。

两类用法同一条风险: 杀软/策略拦在半路时调用可以不抛异常却什么都没改,
而 --set 多半是用户手动迁移某个程序前敲的, 照着一句假成功去重启,
下次启动还在核显上, 并且不会有人告诉他为什么。

修法与托盘侧同一把尺子(不新造规矩): 调完 API 立刻 get_gpu_preference
回读, 不符预期就报"设置未生效 / 清除未生效"并点名可能原因, 成功路径
不变。探针先红: monkeypatch 让 set 不抛而 get 回 None, 旧码打印
"已设置独显"; clear 回 True 而 get 仍是 GpuPreference=2;, 旧码打印
"已清除设置"。

测试 3 条: cmd_set 的失败/成功两面 + cmd_unset 回读仍在独显上。
既有那条三态测试原本只 mock clear_gpu_preference, 而 cmd_unset 现在会
调 get_gpu_preference —— 顺手把它也打桩, 不然它会去摸真注册表, 结果
取决于机器上有没有那条值(测试 hermetic 化)。两条新测试都做了"只回退
这一处": 把对应回读块删掉, 立刻分别红在'设置未生效' not found 和
'清除未生效' not found。

真机复验(源码态 + 打包态各一遍): --set notepad → "已设置独显",
--status 里点名, --unset → "已清除设置", 再 --status 确认已不在列表。
先用源码态跑, 再用重打后的 exe 跑, 两条路径结论一致。

全套 338 用例全绿。longlines 11/4/3, scan_odd2/ast_unused/dupcomment 全零,
py_compile OK。README: 用例数 335→338, --set/--unset 段补回读口径。
备份 _gpu_monitor_before_readbackcli.py / _gpu_monitor_fixed_readbackcli.py。
exe 按标准链重打: onefile sha256[:16]=71f11e1027028918
(§60 轮 6fcd7290d323793f)，onedir 已同步 dist/GPUMigrate-onedir/，
verify_exe3 双一致、verify_imports 4/4、--status 57 行 / --list RC=0
均 0 乱码。托盘用新 exe 重启(pid 40772/42868)，Web 面板 200/10631 字节。
项目管理: 看门狗计划任务 \GPUMigrateWatchdog 还指着搬家前的C:\Users\qm081\.
zcode\workspace\default\gpu-migrate，README 本来就规定他得跟着仓库走 —实
测 LastTaskResult=2147942402(0x80070002 找不到文件),
每 2 分钟呃一次。已重指向 D:\aishare\repo\gpu-migrate\GPUMigrate.
exe --ensure(并补上工作目录), 手动跑一次 LastTaskResult=0,
 托盘已在运行时 --ensure 正例静默退出。开机自启 lnk 复核无误(pythonw.
exe + 馅库源码 --tray)。

## 62. 续五十二：日报"游戏约 0 分钟"不再出现(准确性/美观)

顺着 §61 的"数字得说人话"继续, 审到日报通知: game_secs 记 1~30 秒时
`f"{games_min:.0f} 分钟"` 会印出"游戏约 0 分钟"。用户明明开了游戏, 收到的
日报却说没玩 —— 和 §48 修趋势图角标"近0分钟"是同一条道理: 小于一个单位
的量不该被报成零。

改法只动人读的那句: <1 分钟说"游戏不到 1 分钟", ≥1 分钟照旧四舍五入,
没有游戏时不加这段(和原来一样)。report.csv 里的分钟数一个字符没动 ——
那是给人回头求和的, 写汉字母就没法算了。测试 2 条: 通知文案三档
(20s/30s/90s) + CSV 保持机器可读。旧码上红在
"2000-01-01: ... 游戏约 0 分钟"。

## 63. 续五十三：主循环迁移也回读, 别拿假成功让用户白重启(准确性)

§61 补完命令行、§54 补完托盘详情窗, 回头一看**监控主循环那条自动迁移
路径还是裸的**: set_gpu_preference 不抛 OSError 就记日志"已设置为高性能
GPU"、弹"GPU 迁移成功"、done.add(full)、daily["migrated"] 记一笔。

杀软/策略拦在半路时 RegSetValueExW 可能不报错却什么都没改, 而这是全套
工具的核心自动路径 —— 用户按通知重启了程序, 下次启动还在核显上, 并且
没有任何痕迹告诉他为什么。done 记账还顺带压制了本轮重试, 日报里也多算
一个"已迁移"。

修法与前面几处同一把尺子: 写完立刻 get_gpu_preference 回读, 不符预期
改报"GPU 迁移未生效(写入后回读仍不是独显)", 日志同步。刻意**不**立即重试
而是 streak 归零: 重试节奏跟硬失败对齐(等下一轮 sustain), 不会每秒刷一次
通知; 也不记 done / migrated, 让下一轮继续尝试。

测试 2 条全绿且都过了反向验证: 把回读块删掉, 失败方案的用例立刻红在
`['GPU 迁移未生效'] != ['GPU 迁移成功']`; 成功方案那条守住"没有把能用的
改动改成永远不可用"。

全套 342 用例全绿。longlines 11/4/3(测试里新增的第 4 行已改回 79 内),
scan_odd2/ast_unused 全零, py_compile OK。README: 用例数 338→342, 迁移
闭环与 --set/--unset 段补回读口径。
exe 按标准链重打: onefile sha256[:16]=2635ea494d10ea6e
(§61 轮 71f11e1027028918), onedir 已同步 dist/GPUMigrate-onedi
r/，verify_exe3 双一致、verify_imports 4/4、--status 57 行 / --li
st rc=0均 0 乱码。托盘点亮验证时有个额外收获: 停掉托盘后**看门狗 --ensure 自己把托盘拉了起来
**(10:09:01, 用的正是新 exe), 手动再启的那个撞上单例,按 §40 的设计弹"GPU 监控已在运行
"而不是静默死亡 —— 两条路都是预期行为,关掉重复实例后由看门狗起的那个常驻(pid 19072)
。**看门狗动作顺带改成 detach 启动。** 原来动作直接指向`GPUMigrate.
exe --ensure`: 一体版的引导进程会一直等子进程(真托盘), 于是任务实例卡在 Running 上好几天
(MultipleInstances=IgnoreNew 把后面的触发全当未执行,LastTaskResult=0x
800710E0), 而 ExecutionTimeLimit=PT72H 到点还会把任务收掉。
改成 `cmd /c start "" /B "...exe" --ensure` 后动作彻底同步返回,
 实测两条路径:托盘在跑时触发, 任务称回 Ready、LastTaskResult=0; 先杀托盘再触发,
托盘被重新拉起。也回答了"任务停止会不会带走托盘":Stop-ScheduledTask 只终止它自己启动的引导进程
, 子进程(真托盘)依旧常驻。

## 64. 续五十四：省电自动切回核显也不再拿假成功收尾(准确性)

§63 把主迁移路径的回读补上之后, 顺着"写注册表后声称成功"这一类把剩下的
口子数完: `advance_power_saver` 里自动切回核显那段 `set_gpu_preference(full,
"GpuPreference=1;")` 也是写完就通知"已切回核显省电", 同样没有回读。这条
比迁移更阴: 用户看到"已改回核显设置"后会以为负载已经搬回核显, 而注册表
里还是 GpuPreference=2, 程序下一轮启动照样挂在独显上。

判据和迁移对称: 回读只要**不再是高性能首选项**就算落地(GpuPreference=1;
或值被删干净都算), 仍是=2 才报"省电切换未生效"。另外把 `ps_notified.add`
从 streak 达标处挪到确认落地之后 —— 老代码写入没落地也记"已提醒过",
下一轮直接跳过, 这一条就永远回不到核显了。

测试 2 条, 都在复刻的老代码上验红(只回退这一处, 且必须是**逐字**复刻:
第一版 revert 漏了把 `ps_notified.add` 挪回去, 结果重试用例在旧码上是绿的,
是假的绿; 补回去之后两条一起红 —— '省电切换未生效' not found、以及
prefs 停在 GpuPreference=2; 而不是 1;)。

真机复核判据本身: 探针往注册表真写 =2 / =1 / 删除三种状态再读回,
is_high_performance 分别回 True / False / False, 与假注册表的预期一致。

全套 344 用例全绿。longlines 11/4/3, scan_odd2/ast_unused 全零, py_compile OK。
README: 用例数 342→344, 迁移闭环那段补上省电路径同一把尺子。
备份 _gpu_monitor_fixed_powersaver.py 已存(与本轮改后源码逐字对应)
。exe 按标准链重打: onefile sha256[:16]=d716417c6d40a52b(§63 轮 26
35ea494d10ea6e), onedir 已同步 dist/GPUMigrate-onedir/,
verify_exe3 双一致、verify_imports 4/4、--status 57 行 / --list 
rc=0均 0 乱码, --set/--unset 闭环干净。看门狗按新动作把托盘拉起(10:
26:39),任务状态 Ready、LastTaskResult=0。

## 65. 续五十六：Web 面板断连不再装活(美观性/可用性)
§6.1 遗留项收口。`_PANEL_SCRIPT` 的 `pull()` 里 `.catch(function()
{})` 是空函数:监控进程一死, 页面每秒仍在"刷新", 只是永远拿不到新 DOM, 死死停在最后一份快照上,
用户盯着正常显示的一屏以为监控还活着。准确性口径: 用户看到的数据必须能说清它有多旧。做法:
 HTML 加 `<p id=conn hidden>`(默认隐藏), 前端维护 `fails` 计数 —— 连续 
3 次fetch 失败(含 r.ok=false, HTTP 5xx 也算)才显示「连接已断开,
数据停在 HH:MM:SS(监控进程可能已退出)」, 一次成功立即隐藏并清零。时间戳取 lastok(最后一次成功时
刻);一次都没成功过(首屏后就断)文案降级成"页面加载时", 不留空段。conn 不进 PARTS:
它是前端自有状态, 被 innerHTML 整体换掉会闪, 且服务端不渲染它的内容。测试 1 条加在 WebPane
lLiveUpdate: 锁 HTML 隐藏行、catch 非空、连挂 3 次阈值、恢复即清、
5xx 按断连、PARTS 不含 conn、"页面加载时"兜底。两轮 revert 都验红:
(1) 整段功能回退 -> `<p id=conn hidden></p>` not found;
 (2) 只把 catch 换回空的(元素和函数都留着) -> `.catch(function()
{})` unexpectedly found, 红在行为断言上而不是被 HTML 断言挡在前面。
真机验证用 Node vm 做 DOM/fetch 仿真(.tmptest/s65_probe.
js): 四场景全过 ——连挂 2 次不吱声、第 3 次出文案、成功后消失、502 按断连、
断前成功过则带时间戳。踩坑: 探针第一版 FAIL "banner not cleared",
 根因是探针自己的微任务 flush 不够 ——成功路径比失败路径多一跳(r.text() 返回的 Promise 
要多一个 tick 才兑现),换成 flush(10 tick) 后四场景全绿, 产品代码一字未动。
全套 345 用例全绿。longlines 11/4/3 无新增, scan_odd2/ast_unused 零输出
,py_compile OK。README: 用例数 344→345, Web 面板那段补上断连提示口径。
exe 按标准链重打: onefile sha256[:16]=905c473888e14bb9,
 onedir 已同步dist/GPUMigrate-onedir/。verify_exe3 双一致、
verify_imports 4/4、--status 57 行 / --list rc=0 / --set+--u
nset 闭环干净。看门狗拉起托盘后面板 200, 页面里有 conn 状态行与断连文案, 静默 catch 已不在
。

## 66. 续五十七：空峰值日不再报假的 0.0%()(准确性)
审计"数字说人话"一类时发现的真缺陷: 跨日首轮发日报时, 若当天一次峰值都没记下(peak_ts 为空)
, 通知文案是 "2000-01-01: 核显峰值 0.0%(), 迁移 0 个程序" ——空括号,
 外加一个假的 0.0%。这句话把"没测"说成了"测了是 0": 刚装上、全天休眠、监控没跑,
 三种完全不同的日子长得一模一样, 用户会以为机器真的全天没人用 GPU。修法: peak_ts 为空时改说「未采到
核显占用数据(监控可能未在运行)」。report.csv 不动 —— 那个空字段写给机器看,
 往里塞汉字反而没法求和。测试 1 条(test_an_empty_day_does_not_report_a_fa
ke_zero_peak):锁"未采到核显占用数据"在文案里、不能出现 "()"、不能出现 "0.
0%",以及 CSV 仍是 "日期,0.0,,0,0" 五字段空时间戳。revert 验证红在"未采到核显占用数据 
not found", 失败信息里正是旧那句空括号假 0.0%,不是被别的断言挡住的。全套 346 用例全绿。
longlines 11/4/3 无新增, scan_odd2/ast_unused 零输出,
py_compile OK。README: 用例数 345→346, 日报口径补上"无数据日"那句。
exe 按标准链重打: onefile sha256[:16]=0226e1a4210e6c91,
 onedir 已同步dist/GPUMigrate-onedir/。verify_exe3 双一致、
verify_imports 4/4、--status rc=0 / --list rc=0。
托盘由看门狗拉起, 面板 200 且带 conn 状态行。

## 67. 续五十八：详情窗首选项行跟着按钮刷新(准确性)
GUI 侧同类问题: 详情窗点"立即设为独显"/"撤销独显设置"成功之后,只有标题栏换成结果,
 窗里那一行「GPU 首选项」还留着打开时的旧状态(通常是"未设置")。用户是看着窗里最后一眼判断成败的 —— 按钮
点了、注册表也写了, 那一行却还说没设, 就会被理解成"没生效"。修法: 新增 TrayApp.
_pref_refresh_label(lb, full), do_set / do_unset成功后调用它把那一行
换成此刻的 _pref_status。抽成方法而不是在回调里直接 lb.config, 是为了能在没有 tk 的环境
下直接验它。config 抛异常(窗口已在回调前被关掉)时吞掉: 没什么可刷的不该往 stderr 丢 traceb
ack。测试 3 条, 加在 DetailPrefRefresh: (1) 点前"未设置"、
点后那一行变成"已设为独显 (重启该程序后生效)"; (2) label 已死时不抛;(3)
 _detail_build 源码里必须真调用 _pref_refresh_label(pref_label,
 full)并记录 pref_label, 否则方法写了没人用。revert 验证: 把两处调用删掉后(3)
 立刻红在源码断言上。全套 349 用例全绿。longlines 11/4/3 无新增, scan_odd2/ast
_unused 零输出,py_compile OK。README: 用例数 346→349,
 详情窗那段补上这条口径。exe 按标准链重打: onefile sha256[:16]=0c202efd04f2c
27c, onedir 已同步dist/GPUMigrate-onedir/。verify_exe3 双一致、
verify_imports 4/4、--status rc=0 / --list rc=0。
托盘由看门狗拉起。踩坑: 杀完托盘立刻 sync_onefile 会 PermissionError(句柄未释放)
,等 5 秒再同步即可; 看门狗两分钟一触发, 也可能抢在新托盘起来前占住 exe。

## 68. 续五十九：面板区分"断连"与"数据停更"(准确性)
§65 的续章, 补的是同一主题漏掉的另一半。断连提示只管"拿不到响应", 但监控线程被异常静默掀翻时 Web 线程
还活着: 每秒 200, 发回来的都是同一份旧快照 —— fetch一路成功, connshow 永远不触发,
 页面照常"刷新", 数字却纹丝不动。这种比断连更隐蔽:用户看不出是数据没动还是机器真的闲着。
做法: 快照(以及 on_sample 钩子快照, 两处都要)加 snap_ts = 数据产生时刻;
panel_html 渲染隐藏的 <span id=snapts>{毫秒epoch}</span> 并加进 PART
S ——进 PARTS 是必须的, 否则前端永远拿首屏那一个时刻在比。pull 成功后拿本地时钟和它比,
 超过 25 秒(约两三个采样周期)显示「数据已停更, 最后更新 HH:MM:SS(监控进程可能已停止)
」, 数据恢复流动即消失。时区交给前端 toLocaleTimeString,服务端只给 epoch,
 不替浏览器决定格式。测试 1 条(test_a_stalled_monitor_is_not_left_looki
ng_alive): 锁 snapts 元素、毫秒 epoch 出现在页面里、没采过数据的快照渲染 0 而不是 19
70、snapts 在 PARTS 里、停更文案与 25 秒阈值。SnapshotContract 的键集合同步加 
snap_ts —— 契约测试当场抓到"钩子快照少一个键", 正是它该干的事。真机验证扩了 Node vm 探针(.
tmptest/s69_probe.js)两个场景: 陈旧快照 ->"数据已停更, 最后更新 11:
02:39 (监控进程可能已停止)"; 换成新快照 -> 横幅消失。原有 5 个场景(2 次不吱声/第 3 次出文案
/恢复即清/502 按断连/带时间戳)不回退。踩坑两条: 一是补丁脚本拼 JS 时漏了行尾引号、
还把 then 回调的右括号落到了新代码后面(py_compile 抓得到语法, 抓不到闭包位置,
 是靠打印拼接后的 JS 原文看出来的);二是探针 stub 里 els 的 PARTS 列表和 innerHTML
/textContent 不联动, 改了 stub才跑出真行为 —— 两次"假红"都不是产品代码的错。
全套 350 用例全绿。longlines 11/4/3 无新增, scan_odd2/ast_unused 零输出
,py_compile OK。README: 用例数 349→350, Web 面板那段补上停更口径。
exe 按标准链重打: onefile sha256[:16]=1d02490960ca94cf,
 onedir 已同步dist/GPUMigrate-onedir/。verify_exe3 双一致、
verify_imports 4/4、--status rc=0 / --list rc=0。
托盘由看门狗拉起, 面板 200 且页面带id=snapts 与停更判定。

## 69. 续六十：设置窗保存不再把没吃进的输入说成已保存(准确性)

设置窗那五格数字（迁移阈值 %、显存阈值 MB、电池阈值 %、温度提醒 °C、Web 端口）填成
「abc」这类不是数字的值时，旧 save() 会静默 fallback 到当前生效值，然后标题打一句
「已保存, 设置已生效」。用户点了保存、看到的是已保存，实际那几格还是旧值 —— 界面
撒谎。更糟的是它落进 config.json 的就是旧值，下次读起来还是旧值，用户无从知道
自己刚才敲的字根本没进去。

做法：`_settings_num(var, cfg, key, cast, bad=None)` 收一个可选的 `bad` 列表，被拒时
把键名记进去（不传 bad 的老调用方式照旧能跑，向后兼容）；save() 里 `bad = []`，五个
数字项都把它传下去，标题改成
「已保存, 但 迁移阈值 %/Web 端口(0关) 不是数字, 仍用原值」，点名用的是设置窗里的
中文行名（新增 `SETTINGS_LABELS`，不放 config.json 的键名），关窗延迟也从 1200ms
拉到 2500ms —— 长句得给人留够读的时间。

测试 3 条，都挂在 `TrayConfigWrite`：
- `test_settings_num_reports_the_fields_it_refused`：坏值回退当前生效值、且坏键名
  进 bad；填对不记名；不传 bad 的老式调用仍正常。
- `test_the_save_button_names_the_fields_it_refused`：锁 save() 里 `bad = []`、五个
  调用点都带 `, bad`（`count(", bad)") == 5`）、标题含 SETTINGS_LABELS 与「仍用原值」。
- `test_settings_labels_cover_the_numeric_fields`：五个数字项都有中文行名。

revert 验证（重做过一遍，这次是真红）：把 save() 的数字项块连标题块整段回退成
旧实现（去掉 `bad = []`、五处 `, bad`、标题退回「已保存, 设置已生效」+ 1200ms），
`test_the_save_button_names_the_fields_it_refused` 当场失败；从备份恢复后恢复绿。

踩坑三条：
1. **上一轮那三条测试被插错了类** —— 落在了 `TrayPanelRows` 而不是 `TrayConfigWrite`，
   所以上一轮"revert 了测试还绿"是假的：跑 `TrayConfigWrite.test_*` 根本加载不到，
   而全套跑起来时 `assertIn("bad = []", body)` 本该失败。本轮先把它们搬到
   `TrayConfigWrite`（并给该类的 setUp 补 `self.tray = gpu_tray`），再重做 revert 验证。
2. `_settings_num` 是 `@staticmethod`，签名里没有 self —— 测试里要写成
   `self.tray.TrayApp._settings_num(...)`，不能 `self.tray.TrayApp()._settings_num(...)`。
3. 搬测试时 `scan_odd2.py` 抓到新注释里有个形近乱码字（户字侧边那个，FIX 表里的旧块）
，换成「设置窗」三个字绕开；   另外 PowerShell  Heredoc 里写 `\uXXXX` 转义极易多打
一个字母导致 SyntaxError，   这次就把 `，` 写成了 `\uuff0c`，写补丁脚本优先用字面中文。
全套 353 用例全绿。longlines 11/4/3 无新增, scan_odd2/ast_unused 零输出
,exe 按标准链重打: onefile sha256[:16]=550de2eca3e51366,
 onedir 已同步dist/GPUMigrate-onedir/。verify_exe3 四模块全 True、
verify_imports 4/4、--status rc=0 / --list rc=0。
托盘由看门狗拉起。py_compile OK。README: 用例数 350→353, 新增「设置窗保存要点名」
一条。

## 70. 续六十一：设置窗改端口不再谎称热重载生效, 并修掉 §69 的 NameError(准确性)

两件事，同一处代码。

**(1) 端口改了却说「已生效」。** `gpu_monitor.py` 的 `apply_config` 早就知道
`web_port` / `hotkeys` 是启动时一次性注册的，改完会打日志「需重启监控后生效」；
但托盘设置窗的 `save()` 只会说「已保存, 配置热重载生效」，保存键上还写着
「保存 (热重载立即生效)」。用户把面板端口 8787 改成 9999、看到"已生效"就去刷新
浏览器，刷开的还是旧端口 —— 界面替一个根本没变的服务作了保证。
做法：`save()` 改成先攒一个 `notes` 列表，端口和打开时不一样就往里加一句
「Web 端口已改, 需重启监控后生效」，和 §69 的「哪几格不是数字」用同一套路合并成
标题，关窗延迟同样拉到 2500ms。§69 的标题逻辑顺势从 if/else 改成 notes 列表，
坏值 + 改端口同时发生时两件事都说，不说一件瞒一件。

**(2) §69 留下一个一跑就炸的 NameError。** `gpu_tray.py` 第 30 行导入的是
`SETTINGS_LABELS`，而 §69 写标题时用的是 `_SETTINGS_LABELS` —— 只要保存时有
一格填的不是数字，`save()` 就在拼标题那行抛 NameError，用户点保存看到的是
「什么都没发生」（异常被 UI 线程的 pump 静默吞掉）。§69 的测试全是源码字符串
匹配（`assertIn("SETTINGS_LABELS", body)`）加 `_settings_num` 单测，从来没有
真的把 `save()` 跑起来过，所以这个名字错误活了下来。
修法：把使用处改回 `SETTINGS_LABELS`。

测试 3 条，全在 `TraySingleInterpreter`（复用它的假 tkinter + 真事件循环），
**真的点了一次保存键**，不是再抓一遍源码：
- `test_saving_a_new_web_port_says_a_restart_is_needed`：端口 8787→9999，标题必须
  含「需重启监控后生效」。
- `test_saving_the_same_web_port_still_promises_hot_reload`：端口没动，标题仍是
  「已保存, 配置热重载生效」，不许乱喊重启。
- `test_a_bad_cell_and_a_new_port_are_both_reported`：温度填 abc 且端口改成 9999，
  标题必须同时有「温度提醒 °C 不是数字」和「需重启监控后生效」。

revert 验证（把 save() 整块退回 §69 收口时的形态，即无 notes 列表、且写错成
`_SETTINGS_LABELS`）：端口用例红（标题回到「已保存, 配置热重载生效」），
坏值用例直接 NameError —— 第二条正是 §69 缺陷的现场复现，说明这组测试真的
在跑代码而不是在读源码。

踩坑：`_on_open_settings` 先把 `_settings_open` 置真再投递给 UI 线程，测试里
等这个标志等于不等；要等 fake tkinter 真把窗口和保存键建出来。另外假 tkinter 的
`BooleanVar`/`StringVar` 得给真 `get()`，否则 `json.dump` 会撞上 MagicMock。
`.tmptest/bisect.py` 这类调试残留会抢在标准库 `bisect` 前面被导入，删掉了。

全套 356 用例全绿。longlines 11/4/3 无新增, scan_odd2/ast_unused 零输出,
exe 按标准链重打: onefile sha256[:16]=88ad20036940e6c5,
 onedir 已同步dist/GPUMigrate-onedir/。verify_exe3 四模块全 True、
verify_imports 4/4、--status rc=0。托盘由看门狗拉起。py_compile OK。
README: 用例数 353→356, 配置热重载那段补上设置窗的口径。

## 71. 续六十二：暂停监控不再被面板冤枉成"进程可能已停止"(准确性)

从托盘菜单或热键暂停监控时, `_before_cycle` 会把采样循环挡在 `while` 里,
`_LAST_SNAPSHOT` 原地不动, `snap_ts` 也停走。§68 的停更判定只看"数据是不是
25 秒没动", 于是暂停 25 秒后 Web 面板弹出:
「数据已停更, 最后更新 12:34:56 (监控进程可能已停止)」—— 而监控好好活着,
托盘 tooltip 上明明写着 [已暂停]。用户会以为自己把监控搞挂了, 或者去乱重启进程。

做法: 把"暂停"和"死掉"分成两件事。
- 新增 `web_snapshot_with_pause(snap, is_paused)` (`gpu_monitor.py`, `web_thread`
  上面): 暂停时返回打了 `paused=True` 的**副本**, 原快照不改写 —— 写进
  `_LAST_SNAPSHOT` 的话, 恢复采样那一次循环会把它盖掉, 键会停留在旧值上。
- `cmd_monitor` 里 web 线程改拿 `panel_snapshot()`(读的那一刻现取暂停状态),
  并往 hooks 契约里加了可选的 `is_paused()`; 托盘 `_monitor_thread` 传
  `lambda: self.paused`。`cmd_monitor` 的 docstring 同步补上这一条。
- `panel_html` 常驻渲染 `<span id=paused hidden>0|1</span>` 并进 PARTS ——
  不常驻的话恢复时前端没有可比的东西。
- `_PANEL_SCRIPT`: 暂停时显示「监控已暂停, 数据停在 … (从托盘菜单或热键恢复)」,
  并且 `if(!paused&&dts&&...)` 才判停更。断连的 `.catch` 分支不受影响,
  fetch 挂了照样说"连接已断开"。

测试 3 条, 都在 `WebPanelLiveUpdate`:
- `test_a_paused_monitor_is_not_reported_as_dead`: 暂停标记常驻渲染(0/1 都有)、
  文案含「监控已暂停」「从托盘菜单或热键恢复」、停更判断带 `!paused` 前缀、
  `paused` 在 PARTS 里。
- `test_the_paused_flag_is_injected_when_the_page_is_served`: 未暂停时原样返回
  (`assertIs`), 暂停时副本带 `paused` 且原快照不被侧写, 其余字段原样带过去。
- 契约测试 `test_every_part_the_script_swaps_really_exists` 的 PARTS 清单
  同步加 `paused` —— 它当场抓到了"渲染端忘了加这个元素", 正是它该干的活。

真机验证: `.tmptest/s71_probe.js`(Node vm 仿真 DOM/fetch) 5 个场景全过:
暂停+陈旧 -> 只报已暂停; 未暂停+陈旧 -> 仍报停更(不连坐); 恢复+新数据 -> 横幅消失;
刚暂停(数据还新) -> 也报已暂停; 断连三连 -> 仍走断连文案。
原有 §69 探针 5 个场景重跑不回退。

revert 验证: 把 getter 改回直接返回 `_LAST_SNAPSHOT` 且不再渲染 paused 元素,
契约测试报 `id=paused 在页面上没有对应元素`, 暂停文案测试跟着红; 恢复后 358 全绿。

踩坑两条: 一是改 `_PANEL_SCRIPT` 时把 if/else 重构嵌进了 `.then` 回调, 括号闭不合,
最后改成不加新块、只前置一个 `if(paused){...}` + 给停更判断加 `!paused&&` 前缀,
`node --check` 直接验证语法; 二是 draft 里我引用过一个不存在的 `run_web_panel`,
改成抽出真正可测的 `web_snapshot_with_pause` 才落得下脚。

全套 358 用例全绿。longlines 11/4/3 无新增, scan_odd2/ast_unused 零输出,
py_compile OK。README: 用例数 356→358, 新增「暂停 ≠ 故障」一条。
exe 按标准链重打: onefile sha256[:16]=20601bf5ea796eaa,
 onedir 已同步dist/GPUMigrate-onedir/。verify_exe3 四模块全 True、
verify_imports 4/4、--status rc=0 / --list rc=0。
托盘由看门狗拉起。

## 72. 续六十三：托盘悬停提示按 UTF-16 码元算长度(准确性)

`fit_tip_title` 一直用 `len()` 数字符, 但 `Shell_NotifyIcon` 的 `szTip` 是
`TCHAR[128]`, Windows 数的是 **UTF-16 码元**。显卡名/进程名里一旦进了 emoji
或生僻字(代理对, 占两个码元), len() 就只能数出一半: 按码点截到 127, 实际
utf-16 长度 142 —— 超限的后果不是被截断, 而是**整条悬停提示不显示**。用户
鼠标停上去什么都没有, 还以为监控挂了。

做法:
- 新增 `_utf16_len(text)`(`gpu_monitor.py`): `len(text.encode("utf-16-le"))//2`,
  顺带用 `surrogatepass` 兜住本身就带着孤子代理的怪字符串。
- `fit_tip_title`(`gpu_tray.py`)的三处长度比较全部换成 `_utf16_len`; 硬截断也
  改成逐字符累计码元, 不再 `title[:127]` —— 从中间折断代理对会单留一个孤子,
  Windows 端只看得到一个无效字符。
- `_ellipsize` 同步换成码元口径(它只被 `fit_tip_title` 用); 表格那一侧的
  `_ellipsize_disp` / `_disp_len` 量的是终端显示宽度, 和这里不是一回事, 不动。

测试 3 条, 新开 `TooltipSurrogatePairs` 类:
- `test_a_surrogate_pair_name_still_fits_sztip`: 显卡名塞 30 个 emoji, 带/不带
  「[已暂停]」后缀, utf-16 长度都必须 <=127, 且占用率和策略两行不许被砍掉。
- `test_a_short_tip_is_not_touched`: 放得下就一字不动, 无谓截断只会吃信息。
- `test_a_hard_truncation_never_splits_a_surrogate_pair`: 全 emoji 触发硬截断,
  结果里不允许出现孤子代理。

revert 验证: 把 `fit_tip_title` 整块换回 `len()` 口径, emoji 场景当场报
`utf-16 超限: 142 not less than or equal to 127`, 硬截断用例跟着红; 恢复后绿。

顺带记两笔排查过的非缺陷: `--list` 不过 `row_is_noise`(含 0%/0MB 的噪声行)
是既有约定且有测试锁着(`test_list_keeps_the_noise_row_but_panel_hides_it`),
面板显存汇总是跨进程求和, 所以大于任何单行, 两者都不是 bug。

全套 361 用例全绿。longlines 11/4/3 无新增, scan_odd2/ast_unused 零输出,
py_compile OK。README: 用例数 358→361。
exe 按标准链重打: onefile sha256[:16]=5d9506d23434c60d,
 onedir 已同步dist/GPUMigrate-onedir/。verify_exe3 四模块全 True、
verify_imports 4/4、--status rc=0 / --list rc=0。
托盘由看门狗拉起。

## 73. 续六十四：_disp_len 不计组合字符与零宽字符的列宽(美观)

`--list` 的表格对齐全靠 `_disp_len` 量"占几列"。它原来对每个字符都按
`east_asian_width in "WF"` 判 2 列否则 1 列, 于是:
- 组合用字符(Mn/Me: 拉丁字母上的钩子 U+0301、越南语声调、各类附标)
  被多数成 1 列 —— 而钩子是画在基字符身上的, 一个格子都不多占;
- 格式字符(Cf: 零宽空格 U+200B、ZWJ U+200D)同样被多数成 1 列。

进程名一旦带上这类字符("cafe\u0301.exe" 比预组合的 "caf\u00e9.exe"
只多一个字符、显示宽度一模一样), 名字列就会比真实宽度少补一个空格,
`printf` 的数字列整列被顶斜一格。名字越长越明显, 而这类字符在从
文件系统/注册表读出来的进程名里并不罕见。

做法(`gpu_monitor.py`):
- `_disp_len` 遇 `unicodedata.category(ch) in ("Mn","Me","Cf")` 直接
  `continue`, 不计列也不计入字符; 其余仍按 `east_asian_width in "WF"`
  算 2 列。主口径(CJK 算 2、ASCII 算 1)没动。
- `_ellipsize_disp` 的逐字累计循环同样先判 Mn/Me/Cf: 命中则原样带
  上、不算宽度、不占预算 —— 钩子必须跟着基字符一起走, 只留钩子丢基
  字符反而会画出一个错字。

测试 7 条, 新开 `DisplayWidthCombining` 类(插在 `ListTableLayout` 后):
- `test_a_combining_accent_costs_no_column`: `_disp_len("e\u0301")==1`;
  组合写法与预组合写法同为 8 列。
- `test_a_zero_width_space_costs_no_column`: `_disp_len("a\u200bb")==2`。
- `test_plain_cjk_is_still_two_columns` / `test_plain_ascii_is_still_one_
  column_each`: 主口径回归, 专用MB=6、heavy.exe=9。
- `test_a_combining_mark_does_not_steal_a_column_of_padding`: 同一宽度下
  组合写法与预组合写法补齐的尾部空格数必须一致。
- `test_a_truncated_combining_name_fits_the_asked_width`:
  `_ellipsize_disp("e\u0301"*30, 10)` 恰为 7 个 é 加省略号, 总宽 10。
- `test_truncation_never_orphans_a_combining_mark`: 省略号前面不许留下
  没有基字符的孤立标记。

revert 验证: 把 `_disp_len` 换回 `sum(...)` 一行、`_ellipsize_disp` 去掉
Mn/Me/Cf 分支, `DisplayWidthCombining` 当场 4 条红(disp 宽度 3!=2、
补空格 11!=12、截断结果串不相等、孤立标记); 从备份逐字恢复后 368 全绿。

全套 368 用例全绿(361+7)。longlines 11/4/3 无新增, scan_odd2/ast_unused
零输出, py_compile OK。README: 用例数 361→368。
exe 按标准链重打: onefile sha256[:16]=9e8f8e79c4f01c2a,
 onedir 已同步dist/GPUMigrate-onedir/。verify_exe3 四模块全 True、
verify_imports 4/4、--status rc=0 / --list rc=0。
托盘由看门狗拉起。

## 74. 续六十五：托盘表格并列行不再每秒抖, 点列头当场重排(美观)

三处都在托盘实时面板, 前两处是用户每秒都看得见的:

**(1) 并列行行序不定, 表格在自己抖。** `_refresh_tree` 的 rows 是拿
`set(pids)` 迭代攒出来的, 而 `_sort_rows` 的数字列/文字列比较只排主键、
不给定序 —— `sorted()` 是稳定的, 于是并列行的先后由 set 的迭代顺序
决定。同一份快照两次刷新, 一串 0.0% / 0MB 的行就能给出两种顺序:
用户刚想点的那一行跳到别处去了。实测同一组行两种传入顺序,
输出从 [11,22,33] 变成 [33,22,11]。
修法: 先按 pid 升序打底, 再稳定排序主键。pid 口径和 auto 档
(`(-activity, pid)`)以及 --list / Web 面板一致, 三个界面同向。

**(2) 进程名列区分大小写。** 非数字列直接 `str(v)` 比较, 大写码位
全排在小写前面: "Zebra.exe" 排到 "adc.exe" 前头, 同名的大小写变体
(chrome.exe / Chrome.exe)被拆到桌子两头。资源管理器/任务管理器都是
大小写不敏感排序。改成 `str(v).lower()` 后: adc < chrome(两个) < Zebra,
同名的两个再按 pid 收尾。
修法: 非数字列主键取小写形式; 与 (1) 的 pid 打底叠加。

**(3) 点列头要等最多 1 秒才重排。** 表头的 command 只调
`_toggle_sort` + 按钮文案, 重排要等下一轮 `root.after(1000, refresh)`;
而"复原排序"按钮是立即 `refresh()` 的。同一种操作两种手感, 用户点完
盯着表格的那一秒看到的还是旧顺序。修法: 表头回调末尾补一次 `refresh()`
(lambda 调用时才查名, 定义在后面的 refresh 赶得上)。

顺带清掉 `_PANEL_SCRIPT` 里整段重复了 6 行的注释(§71 收口时粘重的),
免得下一个人以为暂停判定有两套逻辑。

测试 3 条:
- `TrayPanelRows.test_tied_rows_do_not_swap_places_between_refreshes`:
  同一组行两种传入顺序, desc 两个方向输出都必须逐行相同且按 pid 升序。
- `TrayPanelRows.test_process_names_are_sorted_ignoring_case`: 小写化后
  顺序为 adc/chrome/chrome/zebra, 同名的两个按 pid 升序。
- `TraySingleInterpreter.test_clicking_a_column_header_resorts_right_away`:
  用 §70 的假 tkinter 真把面板建起来, 点一次 pid 列头, `tree.delete`
  调用数必须当场 +1; 测试里 Toplevel.after 不会被替身事件循环派发,
  所以这一下刷出来的只能是列头自己触发的。

revert 验证(逐处): `_sort_rows` 换回旧实现 -> 前两条红(行序抖动实测
复现, 名字列回到 Chrome/Zebra/adc/chrome); 表头回调去掉 refresh() ->
第三条红(`点列头没有立即重排表格`); 各自恢复后 371 全绿。
中途踩坑: 第一次写 revert 脚本少带了一个右括号, 把 tree.heading( 的
括号配平弄坏了, 17 条用例集体 SyntaxError —— 从备份恢复后用正确的
旧代码重验, 判别依据本身没变。

全套 371 用例全绿(368+3)。longlines 11/4/3 无新增, scan_odd2/ast_unused
零输出, py_compile OK。README: 用例数 368→371。
exe 按标准链重打: onefile sha256[:16]=aba188571078123d,
 onedir 已同步dist/GPUMigrate-onedir/。verify_exe3 四模块全 True、
verify_imports 4/4、--status rc=0 / --list rc=0。
托盘由看门狗拉起。

## 75. 续六十六：清理十六处错别字, 含两条用户可见的日志文案(美观)

顺着 §74 往下过一遍注释和用户可见文本, 挑出一批错别字, 其中最要紧的
两条是**用户会在 gpu_monitor.log 里逐字读到的**:

- `gpu_monitor.py` 回读确认失败的两条日志原写的是"**注内表**里", 正确的
  词是"注册表"。日志是用户事后排查的主路径(杀软拦下写入时全靠它), 词错了
  排查的人会以为自己看错了行。已改成:
  - `  -> 回读确认失败: 注册表里仍不是独显`(主迁移路径)
  - `  -> 回读确认失败: 注册表里仍是独显首选项`(省电切回路径)

其余是注释/文档字符串里的同类问题, 一并清掉:
- "死亢"->"死掉"(gpu_monitor.py 四处, tests 两处): 原文"监控进程死亢"
  不是词, 想说的是监控死了。
- "总是渲本"->"总是渲染"(gpu_monitor.py 1542 / tests 5973, 和同一行括号
  里的"(而不是有才渲)"对上了)。
- "占用卧列表卡片列表里名称一列"->"显卡列表里名称一列"(gpu_monitor.py 1671
  的 _GPU_NAME_COL_MAX 注释, 原文整句粘坏了)。
- "是洽寳"->"是谎话"(gpu_tray.py 557, 保存按钮热重载口径那段)。
- "旷代码"->"旧代码"(gpu_tray.py 875, 永不迁移按钮那段)。
- "紓合条件"->"符合条件"(tests 1165, 省电重试用例)。
- "注内表"->"注册表"(gpu_monitor.py 另两处注释、tests 三处注释)。

测试: 没有新增用例数(仍是 371), 而是把两条用户可见日志的措辞**锁进**
已有的两条回读用例 —— `MigrationRules.test_a_write_that_never_lands_is_
not_reported_as_success` 断言日志里逐字出现"注册表里仍不是独显",
`PowerSaver.test_power_saver_does_not_claim_switchback_that_never_landed`
断言"注册表里仍是独显首选项"。这两条路径此前只断言了通知标题, 措辞是裸的。

revert 验证: 把两条日志改回"注内表", 对应用例当场红(期望串和实际日志
逐字不符); 从备份恢复后 371 全绿。

踩坑: 第一个清理脚本按"完整子串+假定出现次数"写, 把 "监控进程死亢" 按
3 处估, 实际 "和“死亢”" 那种写法不含"监控进程"前缀, 计数断言当场失败 ——
改为按短词整体替换, 不再猜次数。

全套 371 用例全绿。longlines 11/4/3 无新增, scan_odd2/ast_unused 零输出,
py_compile OK。README 用例数不变(本轮不增用例)。
exe 按标准链重打: onefile sha256[:16]=3897b42c190d049c,
 onedir 已同步dist/GPUMigrate-onedir/。verify_exe3 四模块全 True、
verify_imports 4/4、--status rc=0 / --list rc=0。
托盘由看门狗拉起。

## 76. 续六十七：config.json 退出版本控制, 工作树不再永久 dirty(美观)

交接里记了很久的一条: `config.json` 被 git 跟踪, 而托盘/设置窗每次保存
(改模式、加永不迁移名单、保存设置窗)都重写它。后果:
- 每一台跑过程序的机器上 `git status` 都永久挂着一个 modified config.json;
- 顺手 `git add -A` 会把本机个人设置(阈值 40、interval 1s、省电模式…)提交出去;
- 而仓库里那一份本来就是开发机的运行期产物, 对克隆者没有意义 ——
  新机器上程序本来就会按 DEFAULT_CONFIG 自动补齐缺失的键。

做法:
- `git rm --cached config.json`: 取消跟踪, 工作区文件原样留着
  (在跑的托盘继续读它, 设置不丢)。
- `.gitignore` 末尾加 `config.json`, 和 history.jsonl / panel_ui.json /
  report.csv / *.log 那批运行期产物待在同一处。
- README 配置小节开头补一段: config.json 是本机运行期文件、已 gitignore,
  删掉只回默认值, 手动改也安全(写坏时沿用当前配置, 见既有的说明)。

测试 2 条, 新开 `RuntimeConfigIsNotTracked`(挨着 ReadmeConfigTable):
- `test_gitignore_covers_config_json`: .gitignore 里必须有 config.json 一行。
- `test_config_json_is_not_tracked`: `git ls-files config.json` 必须为空;
  没装 git 的机器 skip, 不把环境依赖带进测试。

revert 验证(两处各自): `git add -f config.json` 重新跟踪 -> 第二条红
("config.json 还在版本控制里"); 删掉 .gitignore 里那一行 -> 第一条红;
各自恢复后 373 全绿。
踩坑: 第一次 revert 用 `git add config.json` 没生效 —— gitignore 里的条目
会让不带 -f 的 add 静默跳过, 得 `git add -f` 才真的把它塞回索引。

全套 373 用例全绿(371+2)。longlines 11/4/3 无新增, scan_odd2/ast_unused
零输出, py_compile OK。README: 用例数 371→373, 配置小节补运行期文件说明。
本轮没动 gpu_monitor.py / gpu_tray.py, 打进 exe 的两个模块源码与 §75 重打
时逐字一致, 故不重打: 现有 onefile sha256[:16]=3897b42c190d049c 仍然有效
(verify_exe3 会再次确认"与仓库源码一致")。

## 77. 续六十八：Web 面板空态行不再跟真实数据同屏说谎(准确性)

渲染一份快照时发现: 明明有一行 chrome.exe 的完整数据, `tbody` 里紧跟
着第二行「(当前无进程占用 GPU)」。它是靠前端 JS 藏起来的 —— JS 没跑
起来(被禁用/报错)、打印页面、或者复制表格到别处时, 服务端送出去的
HTML 就在自己反驳自己: 上面 62.7% 的占用, 下面一句"当前无进程"。
服务端渲染的文档不应该依赖 JS 才说真话。

做法(`gpu_monitor.py` `panel_html`): 空态行照旧常驻(筛不中时要靠它
换文案, 这个设计不变), 但**有真实行时带上 `style='display:none'`** ——
无 JS 场景下它安安静静藏着; 筛不中时由 `_PANEL_SCRIPT` 里既有的
`empty.style.display=n?'none':''` 放出来, 行为和此前逐字一致。

测试:
- 改写 `EmptyRowText.test_the_web_panel_always_carries_the_empty_row`:
  有真实行时必须渲染成带 `display:none` 的常驻行(原断言锁的是旧行为),
  并补回"真实行照常渲染"那条。
- 新增 `test_the_idle_row_is_visible_only_when_nothing_is_running`:
  真的一个进程都没有时, 空态行不许带 display:none(否则没 JS 的机器
  连"空闲"都看不出来)。

真机验证: `.tmptest/s77_probe3.js`(Node vm 仿真 DOM) 9 个场景全过:
无筛选真实行可见/空态行收起、提示"显示 1 / 1 行"(分母不含空态行)、
筛不中真实行收起且空态行换成"没有匹配的进程"、重新筛中两者归位、
清空筛选回到全显。顺带记一笔排查过的非缺陷: 筛不中后清空筛选, 藏起来
的空态行文案停在"没有匹配的进程"不复位 —— 用户看不见(它是收起的),
且下一秒服务端重渲会带回报文, 不属于要修的问题。

revert 验证: 把 idle_row 换回总是原样渲染, 改写后的用例当场红;
恢复后全绿。

全套 374 用例全绿(373+1)。longlines 11/4/3 无新增, scan_odd2/ast_unused
零输出, py_compile OK。README: 用例数 373→374。
exe 按标准链重打: onefile sha256[:16]=c43e37e30cc38ebd,
 onedir 已同步dist/GPUMigrate-onedir/。verify_exe3 四模块全 True、
verify_imports 4/4、--status rc=0 / --list rc=0。
托盘由看门狗拉起。

## 78. 续六十九：省电提醒不再每个周期重弹一遍(准确性)

`power_saver_notify=True` 而 `power_saver_auto=False`(默认就是这对组合,
托盘"省电模式"只开提醒不开自动时正是它) 下, `advance_power_saver`
的 else 分支弹完"省电提醒"什么都不记: `ps_notified` 只在 auto 切回核显
确认落地后才 add, 而 streak 已经 >= need 且永远不回落。于是每个采样周期
开场白都是同一句 toast —— 探针 `.tmptest/s78_probe1.py` 实测连跑 6 轮弹
5 次, 越到后面越是同一句话刷屏, 用户想不知道都难。

做法(`gpu_monitor.py` `advance_power_saver`): else 分支里补一行
`ps_notified.add(full)`, 和 auto 路径记同一本账。提醒过就登记, 之后
`full in ps_notified` 直接 continue, streak 也不再往上积。复位时机沿用
auto 的老规矩: 首选项不再高性能(`--unset` / 被别的东西改掉)时,
`ps_notified.discard` + streak 出队, 重新迁上去再闲置还会再提醒一次 ——
"只处理一次"的粒度是"一次迁移", 不是"进程的一生"。

测试(3 条, 全绿):
- 改写 `PowerSaver.test_idle_dgpu_process_only_notified_when_not_auto`:
  原来只断言 6 周期里出现过"省电提醒", 现在钉死成"整场就一条"。
- 新增 `PowerSaverAdvance.test_notify_only_mode_registers_the_reminder`:
  4 轮 need=2 auto=False, 断言 notes 恰 1 条、notified=={DGPUAPP}、
  streak 冻结在 2、注册表没被改(不自动切回就不该动它)。
- 新增 `PowerSaverAdvance.test_the_reminder_rearms_after_the_pref_is_dropped`:
  删掉首选项后再跑一轮, notified 清空、streak 出队 —— 防止把"登记"
  修成"一锤子买卖"。
`PowerSaverAdvance.cycle` 顺手加了个 `auto=True` 形参, 让仅提醒路径
也能被直接驱动(原先写死 True)。

revert 验证: 只把 gpu_monitor.py 换回 `_gpu_monitor_before_psspam.py`,
两个类当场 2 红(改写那条报 `['省电提醒' x5] != ['省电提醒' x1]`, 新增那条
报 `3 != 1`); 逐字恢复后 14 条全绿, sha256 与修复版一致。

全套 376 用例全绿(374+2)。longlines 11/4/3 无新增, scan_odd2/ast_unused
零输出, py_compile OK。README: 用例数 374→376。
exe 按标准链重打: onefile sha256[:16]=9a22fecb9205c811, onedir 已同步
dist/GPUMigrate-onedir/。verify_exe3 四模块全 True、verify_imports 4/4、
--status rc=0 / --list rc=0。托盘由看门狗拉起: 2 进程, 面板 8787 返回 200,
任务 State=Ready。

排查后确认不是缺陷、未动: `restart_process` 里 `taskkill` 的
FileNotFoundError 由调用方 `except OSError` 兜住并记"自动重启失败"(迁移
本身已成功, 提示口径正确); `full_path` 无目录名时 `os.path.dirname`
返回空串 —— 但重启路径的 full 只来自本机运行中进程的完整路径, 现实里
不会出现。

## 79. 续七十：采样窗口不再固定 1 秒, 跟着周期走(准确性)

`collect_gpu_sample` 里那句 `time.sleep(1.0)` 是写死的: 不管配置里
`interval_seconds` 是 5 秒还是 0.5 秒, PDH 两次采集之间都只隔 1 秒,
剩下的时间由循环尾部那句 `sleep(interval - elapsed)` 补足。于是周期
被补齐了, **观测没有** —— interval=5s(文档默认值)时每个周期只看 1 秒,
中间 4 秒的 GPU 峰值整段不存在, "持续高占用"判定被系统性低估:
把周期调大(为了省 CPU)反而看得更少, 和用户的直觉正好相反。

探针 `.tmptest/s80_probe1.py`(打桩 PDH, 放行 time.sleep 记录时长):
传什么窗口都由不了它, 内部恒睡 1.0s; `_sample_window` 参数根本不存在。

做法(`gpu_monitor.py`):

- 新增 `_sample_window(interval)`: 窗口 = 周期, 下限 `_WINDOW_MIN=0.05s`
  防呆, 周期不可解析(None/"abc")时退回 1.0s。
- `collect_gpu_sample(..., window=1.0)`: 内部睡眠改成
  `time.sleep(_sample_window(window))`。显存是瞬时值, 不受窗口影响。
- `cmd_monitor` 调用点传 `window=cfg["interval_seconds"]`, 于是热重载
  改了周期下一轮就跟着变。

真机量化(`.tmptest/s80_probe3.py`, 带真 PDH 跑 cmd_monitor, 统计"采样函数
耗时 / 墙上时长"): interval=3s 时占空比从 **49%** 升到 **98%**
(1.47s/3s → 3.46s/3s; 每个周期多出来的 0.46s 是 PdhCollectQueryData
自身开销, 与窗口无关)。按文档默认 5s 算, 旧行为只看到约 29% 的时间。

测试(8 条, 全绿):

- `SamplingWindow`(3): interval=5s 与 0.5s 时每轮窗口都等于该值;
  热重载把 2s 改成 8s 后, 下一轮窗口立刻变成 8.0。
- `SampleWindowFloor`(3): None/"abc"/object() 退回 1.0s; 0/-3/0.001
  抬到 `_WINDOW_MIN`; 0.5/1/2/5/10 原样透传。
- `CollectGpuSampleWindow`(2): 打桩 PDH 后用真 `collect_gpu_sample`
  验证 window=5 时内部睡 5.0 秒, 且读数不受影响
  ((5s/10s)=50.0%); 不传窗口时睡 1.0 秒。

revert 验证: 只把睡眠改回 `time.sleep(1.0)` -> `CollectGpuSampleWindow`
当场 1 红; 只把调用点的 `window=` 去掉 -> `SamplingWindow` 当场 3 红;
逐字恢复后全绿。

全套 393 用例全绿(385+8)。longlines 11/4/3 无新增, scan_odd2/ast_unused
零输出, py_compile OK。README: 用例数 385->393, 配置表与实现原理补上
"窗口=周期"的口径。

(上一轮 §78 之后还有一条未记录: `gpu_tray._bold_font` 的 arialbd/segoeuib/
msyhbd 逐级回退, 配上 `HeatClass.test_icon_font_falls_back_across_missing_fonts`。
那条桩在 Pillow 12 下会被 `load_default()` 内部那次
`truetype(..., layout_engine=)` 回调炸掉, 已改成借真 truetype 用完即还。)

## 80. 续七十一：Web 面板灰字过 WCAG AA, 数字改用等宽字形(美观)

`#hint` / `#hidden` / `.sparkempty` 三处灰字在深色底 #14141e 上只有
3.43:1 / 3.43:1 / 2.51:1 的对比度, 字号还是 11px —— 提示行("显示 N/M 行")
和趋势图空态文案恰恰是用户看不懂数据时唯一会去读的两行, 等于把最关键
的说明藏起来。这道不是口味问题: WCAG 对小于 18px 的正文要求 4.5:1。

探针 `.tmptest/s81_probe1.py` 把 `_PANEL_STYLE` 里每个前景色逐个算
WCAG 对比度: 语义色(热/温/凉/策略/断连/折线)都在 6.5:1 以上, 不及格的
只有这三档灰字; `.tmptest/s81_probe2.py` 再在同一族灰色里挑替代值,
#8888a0 是第一个在三种深色底(#14141e/#1b1b28/#1e1e2e)上都过 4.5:1 的。

做法(`gpu_monitor.py` `_PANEL_STYLE`):

- 三处灰字统一换 `#8888a0`(5.29:1), 仍比正文 #ddd 暗一档, 层级没丢;
  `#hint`/`#hidden`/`.sparkempty`/`.card span`/`.sparkwrap span` 的
  字号从 11px 提到 12px。
- 表格和汇总卡数字加 `font-variant-numeric:tabular-nums`: 每秒局部刷新
  时, 数字位数变化不再让整列左右晃(Segoe UI 的默认数字虽然是等宽的,
  但 system-ui 回退到别的字体时不保证)。
- 托盘侧 `_MUTED`(#8b8ba0)本来就在 5.5:1, 没动, 但一起钉进测试。

测试(4 条, 全绿, `WebPanelLegibility` / `TrayPaletteLegibility`):
- 逐个解析 CSS 规则, 断言小字颜色的对比度 >= 4.5:1、字号 >= 12px;
- 断言 `table` 与 `.card b` 都带 `tabular-nums`;
- 托盘 `_MUTED` 在 `_BG`/`_PANEL_BG` 两种底色上都过 4.5:1。
  对比度按 WCAG 公式在测试里现算, 不写死数字, 换底色也不会失真。

revert 验证(四种变体逐个试): 灰字退回 #666a80、空态退回 #555566、
字号退回 11px、去掉 tabular-nums —— 每一种都只让对应的那一条红, 恢复后
全绿。

全套 397 用例全绿(393+4)。longlines 11/4/3 无新增, scan_odd2/ast_unused
零输出, py_compile OK。README: 用例数 393->397, Web 面板一节补上可读性口径。

## 81. 续七十二：趋势图按时间轴画, 不拿几小时的空白当连续曲线(准确性)

角标早就按时间戳报时长(§45 那条"不按点数虚报"), 但图本身还是
"最后 180 点 + x 按序号等距": 休眠唤醒或暂停监控之后, 首末两点能差出
几小时, 序号等距把这段空白铺成一段和其他点间距无异的连续曲线 ——
角标写着"近 2.0 小时", 曲线却像刚采了 30 分钟, 一张图自己打自己脸。
投影类工具(Grafana/btop)对时间轴是同一条规矩: 窗口外的点不画,
窗口内的点按真实时刻排布。

探针 `.tmptest/s82_probe1.py` 取证: 构造 174 个暂停前采样 + 空 2 小时
+ 6 个唤醒后采样, 旧代码画出 180 个点、跨空白处间隔 1.68 画布单位,
与正常 10 秒步长完全一样。`.tmptest/s82_probe2.py` 复验修后行为。

做法(托盘 / Web 共用一套, 口径不分成两半):

- `gpu_monitor.spark_points(hist)`: 窗口 = 最新一点往前 30 分钟
  (`_SPARK_WINDOW_S`), 再取最近 180 个, 按时间升序 —— 窗口外的点一个都不画。
- `spark_geometry(pts, w, h, ymax)`: x 由时间戳算 `(t - t0) / span * w`,
  首末同时间戳(除零)时退回等距; 同时返回填充多边形的左右端 x,
  填充贴着折线首末端落基线, 不再默认铺满整幅宽。
- `spark_y_of(v, h, ymax)`: 把原来各写一遍的 y 公式收成一处, 阈值线位置
  与折线从此不可能算出两个刻度。
- `gpu_tray._draw_sparkline` 改用同一套; 顺手把空态/角标文字的
  #555566 2.25:1 换成 `_MUTED`(5.1:1), 和 §80 同一把尺。

测试(7 条, `SparklineTimeAxis`, 全绿): 窗口外 174 个点不画(只剩 6 个)、
正常 180 点仍铺满整幅且角标不变、x 由时间戳算(t=0/10/60 -> 0/50/300)、
单轮 11 秒的抖动不算断档照画、空态文案说明"无采样数据"而非节奏、
托盘那条折线同样只剩 6 个点、托盘空态文字过 AA。

revert 验证(四种变体逐个试): 去掉窗口过滤(3 红)、x 退回序号等距(1 红)、
空态文案退回只说节奏(1 红)、托盘空态色退回 #555566(1 红); 逐字恢复后全绿。

全套 404 用例全绿(397+7)。longlines 11/4/3 无新增, scan_odd2/ast_unused
零输出, py_compile OK。README: 用例数 397->404, 趋势图一节补上时间轴口径。

## 82. 续七十三：停更宽限不再写死 25 秒, 跟着采样周期走(准确性)

Web 面板判"数据已停更"的那句 `Date.now()/1000-dts>25` 是 JS 里写死的,
只在 interval=5s(文档默认)附近成立。`interval_seconds` 的配置上限是空的
(`(0.5, None, False)`), 调到 30s/60s 之后, 健康监控两轮的间隔本身就超过
25 秒 —— 页面会常年挂着"数据已停更, 最后更新 HH:MM:SS (监控进程可能已停止)",
把一台好机器说成坏机器。§71 专门修过"暂停被冤枉成进程停止", 这是同一类
误报的另一半: 阈值不随被测对象的节奏走。投影类系统的经验也很一致:
Grafana 的 no-data / stale 阈值由 scrape_interval 推导, 从不写死。

做法(`gpu_monitor.py`):

- 新增 `web_stale_after(interval)`: 取 `max(25, 周期*2 + 5)` 秒。
  短周期保持原来的灵敏度, 长周期按比例放宽; 真卡住(卡过两个周期)
  照样报得出来。周期不可解析时退回 25。
- 快照(_LAST_SNAPSHOT 与 on_sample 钩子)新增 `stale_after` 键,
  `panel_html` 渲成 `<span id=staleafter hidden>` 并加入 JS 的 PARTS,
  热重载改周期后前端下一轮就按新尺度判, 不用刷新页面。
- `_PANEL_SCRIPT` 改成读 `#staleafter`(`stal||25`), 老页面没有这个元素时
  退回 25, 不会因此永远不报。

测试(7 条, `WebPanelStaleBudget`, 全绿): 5s/10s 保持 25、30s=65、60s=125;
None/"abc"/object() 与 0/-5/0.001 都有兜底; interval=60 跑一轮真循环,
钩子快照里 `stale_after` 就是 125; 页面带 `#staleafter` 且在 PARTS 里;
JS 用 `stal||25` 且不再有 `dts>25`; 缺这个键的旧快照仍按 25 秒渲染。

revert 验证(三种变体逐个试): 宽限函数退回常量(2 红)、不再向页面发布(2 红)、
JS 不读宽限值(2 红); 逐字恢复后全绿。

全套 411 用例全绿(404+7)。longlines 11/4/3 无新增, scan_odd2/ast_unused
零输出, py_compile OK。README: 用例数 404->411, Web 面板一节补上宽限口径。

## 83. 续七十四：托盘侧剩下的两处 #666 也抬到 AA(美观)

§80 只量了 Web 面板那一侧, 托盘里还漏着两个 `fg="#666"`: 面板底部那行
状态("核显: ... 独显: ... 刷新: 1 秒 右键进程可设为永不迁移")和详情窗
的字段名(#666 在 #14141e 上只有 3.18:1, 在卡底 #1e1e2e 上 2.86:1)。
前者是面板上唯一的说明行, 后者是详情窗的阅读骨架 —— 小字叠低对比,
读不出来就等于没写。

做法(`gpu_tray.py`): 两处都换成模块里已有的 `_MUTED`(#8b8ba0,
5.48 / 4.92:1), 不引入新色值, 保持"一整套灰"而不是"一整套灰 + 一个例外"。

测试(3 条, `TrayTextColorGuard`, 全绿):
- 历史遗留的具体色值(#666/#666666/#555566/#99a/#9999aa)逐个点名, 源码里
  再出现就红(扫的是剥掉注释的代码, 注释里为说明原因提到旧值不算);
- 正则扫源码里所有 `fg=`/`foreground=` 写死的色值, 逐个对 `_BG` 与
  `_PANEL_BG` 两种底色算 WCAG 对比度, 低于 4.5:1 就报出"色值 + 底色 +
  实测比值"; 只扫文字色, canvas 的 `fill`(面积填充)不拿背景的尺子量;
- 底部状态行必须用的是 `_MUTED` 而不是就近再写一个灰。

revert 验证(三种变体逐个试): tip 行退回 #666 / 详情窗字段名退回 #666 /
tip 行退回 #555566, 各自都让对应断言当场红; 逐字恢复后全绿。

全套 414 用例全绿(411+3)。longlines 11/4/3 无新增, scan_odd2/ast_unused
零输出, py_compile OK。README: 用例数 411->414, 可读性一段补上托盘侧的守卫。

上一轮留的选项里, "断连提示按周期放宽"核实后**不需要**改: 前端轮询固定在
`setInterval(pull,1000)`, 与 `interval_seconds` 无关, 连挂 3 次约等于
3 秒无响应, 尺度本来就是对的。

## 84. 续七十五：Web 面板的可读性守卫从"点名"升级为全量扫描(美观)

§80 那条可读性测试是逐个选择器点名量的(`#hint` / `#hidden` / `.sparkempty` /
`.card span` / `.sparkwrap .thr` / `.sparkwrap .corner`)。点名式守卫只覆盖
写测试那一刻想到的那几档灰: 以后谁再加一档灰、或者把某个色值改暗一档,
只要选择器不在名单里, 测试一声不响, 灰字照样悄悄回到读不出来的状态。

做法(`tests/test_gpu_monitor.py`, 不动源码): 新增
`test_every_declared_color_in_the_stylesheet_clears_aa`, 用
`(?<![\w-])color:(#[0-9a-fA-F]{3,6})` 全量扫 `_PANEL_STYLE` 里每一条
`color:` 声明, 对页面底色 `#14141e` 和卡片底 `#1e1e2e` 两种底都要求
≥4.5:1, 不合格就把"色值 + 底色 + 实测比值"报出来。两条配套设计:
- `(?<![\w-])` 是负向后查, 别把 `border-color:` / `background-color:`
  这种属性名的后半截也抓进来(实测 `#q:focus{border-color:#5b8def}` 扫不到);
- 断言扫到的色值 >8, 防止"扫了个空面板也全绿"这种假绿。

取证(`.tmptest/s85_probe1.py`): 现在 `_PANEL_STYLE` 有 15 条 `color:`
声明、9 个不同色值, 最低的一档是 `#8888a0` 在卡片底上 4.75:1, 其余都在
4.9:1 以上(最高 `#7fd1b9` 10.21:1)。也就是说这条守卫现在是"已经过线",
守的是将来。

测试(1 条, `WebPanelLegibility`, 全绿): 全量扫描那条; 另三条(逐选择器
颜色、字号 ≥12px、tabular-nums)保持原样继续点名当前这几档。

revert 验证: 往 CSS 里塞一档不过线的灰 `.muted2{color:#777777}`(在
`#14141e` 上 3.9:1, 卡片底更低), 该条当场 FAIL 并打出色值和比值
(`.tmptest/_revert_cssscan.py`, 跑完自动逐字还原); 恢复后全绿。

全套 415 用例全绿(414+1)。longlines 无新增, scan_odd2/ast_unused 零输出,
py_compile OK。顺手修掉本轮新代码自己带的一个告警: 新测试的 docstring 里
写了正则 `(?<![\w-])`, 普通 docstring 会在 3.14 上刷
`SyntaxWarning: invalid escape sequence '\w'`, 改成 raw docstring(`r"""`)
后 `python -W error::SyntaxWarning -m py_compile` 零告警 —— 告警本身就是
噪声, 带着它跑测试会淹掉真信息。

README: 用例数 414->415, 可读性一段补上"Web 侧的守卫升级成全量扫描:
`_PANEL_STYLE` 里每一条 `color:` 声明都要在页面底色与卡片底色上都过
4.5:1, 不再靠逐个选择器点名"。

下一步: 按打包链重打 exe(当前托盘还在用 §83 的构建)。

## 85. 续七十六：normalize_total 改成"一块卡算一笔账", 不再跨卡拉平(准确性)

起因是一次三面口径实测(`.tmptest/s86_probe2.py` 同时抓 Web 面板表格和
`--list` 输出): 同一个进程, 面板写 31.6%, 命令行写 82.6%; System 更是
11.5% 对 30.3%, 全表稳定差 2~3 倍。顺着差距往下挖(`s86_probe3.py`),
根因不在采样窗口, 在 `normalize_total`:

老实现把**所有进程**(不分核显/独显/虚拟)的利用率加成一个总和, 超过 100%
就全体乘同一个系数 100/total。本机实测独显侧进程总和 166.5% + 核显侧
105.5% = 272.6%, 于是整表被乘 0.367: sunshine(82.8%) 在面板上写成
30.35%, 任务管理器里是 82.8%。两块物理上不同的卡被搅进同一个"总和",
独显一忙就把核显的读数按比例压小。更要命的是 `select_hot_candidates` 比
阈值用的就是这一份缩水后的 `util_by_pid` —— 该迁移的进程一个都没迁。

做法(`gpu_monitor.py` `collect_gpu_sample`): 归一化按适配器(LUID)分别
算, 每块卡只跟自己的进程总和比, 超 100% 才缩, 没超的原样放过
(`scale.get(luid, 1.0)`)。只有 `normalize=True` 才走这条路,
默认(任务管理器口径)一个数都不动。

实机复测(同一台机器, 同一份采样): 核显侧总和 126.7% -> 87.44% 的进程
现在显示 68.75%(旧实现会给到 27.8%), 独显侧总和 187.0% -> 82.9% 显示
43.87%。跨卡不再互相拉平, 核显的读数不再被独显的负载决定。

测试(6 条, `NormalizePerAdapter`, 全绿; 打桩 PDH 走真 `collect_gpu_sample`,
两块卡四个引擎):
- 独显侧早超过 100% 时, 核显侧 50% 一个数都不动;
- 每块卡只跟自己的总和比: 独显侧 90/80 总和 170 -> 缩到 100 且占比不变;
- 同一份采样里两块卡各用自己的系数(90->52.94, 80->47.06, 核显 30 不动);
- 两块卡都没超 100% 时 normalize=True 也是恒等变换;
- normalize 关闭时保持任务管理器口径(90% 就是 90%);
- 源码守卫: 监控循环必须还按配置把开关传给采样器。

revert 验证(三种变体逐个试): 退回全局一个系数(4 红)、忘了给没超线的卡
留 1.0 直接下标(3 红)、整个 if 分支删掉(2 红); 每种跑完都逐字还原,
最后全绿。

全套 421 用例全绿(415+6)。longlines 无新增, scan_odd2/ast_unused 零输出,
py_compile OK(顺手用 `with` 关掉新守卫里那个未关闭文件的 ResourceWarning)。

README: 用例数 415->421; `normalize_total` 那行改成"按每块显卡分别压到
100%, 独显再忙也不会把核显的读数按比例压小"; 新增"占用读数不跨卡拉平"
一条, 把实测数字写进去(166.5% + 105.5% = 272.6% -> 0.367; 30.35% 对 82.8%)。

exe 已重打并自证(sha256[:16] = `9d615975c7749d59`, 替换 §83 的
`6a5e4fe8e78150f5`; onedir 同步到 `dist/GPUMigrate-onedir/`,
`show_toast.ps1` + `config.json` 都在位): 链是 Disable-ScheduledTask ->
Stop-Process -> clean_build -> build_onefile -> build_onedir -> sync_onefile
-> sync_onedir。`verify_exe3.py` 两个产物四模块全部 True(gpu_monitor /
gpu_tray 与仓库源码逐字一致), `verify_imports.py` 0 缺失, 一体版与 onedir 的
`--list` / `--status` 冒烟正常。看门狗恢复后托盘用新构建拉起, Web 面板 200。

实机复核(`s86_probe2.py`, 新旧构建前后各跑一次): 同一个 igpu 进程
修复前面板 28% 级 -> 现在 69.7%(汇总卡同步), 与 `--list` 的任务管理器
口径只差"归一化份额"这一个比例(面板 38.8% 对 CLI 82.6%, 系数正是
独显侧自己的 100/187)。剩下这个差是有意保留的开关行为, README 已把边界
写清(`--list` 永远不看 `normalize_total`)。

## 86. 续七十七：--list 的采样窗口不再写死 1 秒, 跟着 config 走(准确性)

§85 修完跨卡拉平之后, `s86_probe2.py` 的三面比对暴露出第二个口径差:
Web 25.9% / CLI 39.1% 这种同一进程两个数的现象仍在。量化一下窗口本身的
影响(`s87_probe1.py`, 同一台机器连着采三遍):

  窗口 1s  pid1380=83.2%  窗口 3s  pid1380=28.0%  窗口 5s  pid18864=65.4%

PDH 读的是"窗口内引擎活跃时长 / 墙上时长"的比例, 所以窗口定口径: 面板和
托盘按 `interval_seconds` 看满一个周期, 而 `cmd_list` 一直用默认的 1 秒
窗口, 同一进程在两边能差出数倍 —— 周期调成 5s 以后, 这个差会一直摆在
用户眼前, 而他会以为其中一个数坏了。

做法(`gpu_monitor.py`):
- `cmd_list(cfg=None)`: 占用表那一次采样改用
  `window=_sample_window(cfg["interval_seconds"])`, 表头改成
  "采样当前占用 (任务管理器口径, 窗口 N 秒 = config 的 interval_seconds)"
  —— 窗口决定这些数字的含义, 必须写在数字旁边;
- 枚举显卡那一遍改为 `window=_WINDOW_MIN`: 它只要实例清单, 等满一个窗口
  纯属白等, 所以 `--list` 本身反而快一秒;
- `main()` 把 `args.list` / `args.once` 合并, 都走
  `cmd_list(load_config(args.config))` —— 不读配置就永远拿不到周期。

测试(6 条, `ListWindowFollowsConfig`, 全绿):
占用表用配置的周期(5s -> 5.0); 枚举那遍不占满窗口(30s 配置下仍是 0.05);
没给 cfg 时退回 1.0; interval 写成 "abc"/0 时也有兜底; 表头把窗口秒数打出来;
源码守卫 main() 必须把 load_config 的结果喂给 cmd_list。

另修了两个旧夹具签名: `ListCommand` / `ListTableLayout` 的假采样器都不收
`window=`, 现在补上(与 LoopHarness 的 fake_collect 一致)。

revert 验证(三种变体逐个试): 占用表退回默认窗口(3 红)、main() 不再喂配置
(1 红)、枚举那遍也等满窗口(1 红); 逐字恢复后全绿。

全套 427 用例全绿(421+6)。longlines 无新增, scan_odd2/ast_unused 零输出,
py_compile OK。实机: `--list` 输出表头带上"窗口 1 秒", 耗时与改动前持平
(枚举那一遍不再空等 1 秒, 正好抵掉占用表多出来的那点)。

README: 用例数 421->427; 代码块里补一行"采样窗口跟 interval_seconds 一致";
新增"命令行与面板同一个窗口"一条, 把 83.2% / 28.0% 的实测写进去。

下一步: 重打 exe(本节又动了 `gpu_monitor.py`)。

## 93. 续八十四：interval_seconds 补上上限, 手误填个 3600 不再把 --list 挂死(准确性/健壮性)

下一项队列表里的老候选：`_NUMBER_RULES` 里 `interval_seconds` 只有下限 0.5、没有上限，
`_sample_window` 也只用 `_WINDOW_MIN` 兜底下限。config.json 是人也能改的文件，把
interval_seconds 敲成 3600/86400 这类值时，老代码一声不响全盘接受：
- 监控: `time.sleep(cfg["interval_seconds"])` 一小时一采，等于监控停摆；
- `--list` / `--once`: 先打表头，再 `time.sleep(_sample_window(3600))` 干等一小时才退出——
  用户只会以为“程序卡死/崩溃”，而这纯粹是一个 config 手误。

改法(`gpu_monitor.py`)：引入 `_INTERVAL_MAX = 60.0`，两处都夹上界：
- `_NUMBER_RULES["interval_seconds"]` 从 `(0.5, None, False)` 改为
  `(0.5, _INTERVAL_MAX, False)`，走既有的入口收敛（`merged_config` -> `_coerce_number`），
  所以监控睡眠、`--list` 窗口、热重载全被同一个闸管住；
- `_sample_window` 的返回值加 `min(_INTERVAL_MAX, ...)` 做最后一公里兜底，即便将来有
  代码绕过 config 直接传大窗口也挂不死。60s 是上限不是推荐值：正常 0.5~5s，60s 够
  “低功耗低频监控”的极限。

测试(5 条, `IntervalBoundsGuard`, 全绿)：3600 经 merged_config 夹到 60；3.5 原样；
`_sample_window(3600)==60`、中值 30 不被误伤、0.001 仍被抬到 0.05 地板（证明上界没把
下界盖掉）。

revert 验证（只回退这一处）：把 `min(_INTERVAL_MAX, ...)` 和规则里的 `_INTERVAL_MAX` 一起退回
`None`/去掉 min，5 条里恰好 2 条（两个“上限”断言）变红，另 3 条（中值/下界/普通值）仍绿，
还原恢复全绿——上界测试是判得出来的。

全套 457 用例全绿(452+5)。longlines 0, scan_odd2/ast_unused 零输出, py_compile OK。
README：用例数 452->457，配置表 interval_seconds 行补“超过 60s 夹到 60”。

exe 已重打并自证：一体版 sha256[:16] = `f3797e43e210a7f8`＋两产物均经 veri
fy_exe3.py 证与仓库源码逐字一致、verify_imports 0 缺失。`--list` 子进程 rc=
0、UTF-8 解得回。42 行、显卡在。看门狗恢复后托盘用新构建拉起(2 进程)，Web 面板 HTTP 200，
重启后无 tray_error.log / 无 gpu_monitor_error.log。

## 94. 续八十五：采样周期搬进设置窗, 不再只能手改 config.json(准确性/易用)

§89 起的另一处“说了不算”缺口：设置窗只暴露 `threshold_percent / vram_threshold_mb /
battery_threshold_percent / temp_limit / web_port` 五个数字格，`interval_seconds` 只能
手改 config.json。而它恰恰是最常被调的旋钮之一——改采样快慢要退出、找文件、改 JSON、
看格式别写坏。这次把它搬进设置窗。

先确认它“配立即生效”：`apply_config` 里 `nonlocal cfg; cfg = new_cfg`，热重载替换的就是
监控循环手里的那个 cfg，所以 `time.sleep(cfg["interval_seconds"])` 与
`collect_gpu_sample(window=...)` 下一轮就用新值——和 web_port 不同（端口启动时注册一次，
改了必须重启）。所以它归“热重载生效”那一档，不该弹“需重启”。

改法：
- `gpu_monitor.py` `SETTINGS_LABELS` 加 `"interval_seconds": "采样周期 秒"`。
- `gpu_tray.py` `_settings_build`: 在“温度提醒”和“Web 端口”之间插一行
  `add_entry("采样周期 秒", "interval_seconds")`；`save()` 里同样进
  `self._settings_num(v_int, cfg, "interval_seconds", float, bad)`——
  沿用和别的数字格同一把尺子：填非数字就报“采样周期 秒 不是数字, 仍用原值”，
  绝不静默。越界(>60s 等)由上游 `merged_config` 收敛，和 §93 一脉相承。

因为设置窗的“哪几格被拒/报没生效”是数出来的，这一格加进来同步改了既有测试：
- `test_the_save_button_names_the_fields_it_refused`：`, bad)` 计数 5->6，
  var 名单加 `v_int`；
- `test_settings_labels_cover_the_numeric_fields`：键名单加 `interval_seconds`；
- `TraySingleInterpreter._settings_vars`：按 add_entry 顺序摆的值从 5 格变 6 格，
  三处保存用例的值列表在“温度”和“端口”之间插入采样周期值。
- 新增 `test_changing_the_sampling_interval_only_promises_hot_reload`：只改采样周期、
  端口没动时，标题必须是“已保存, 配置热重载生效”，而不是“需重启监控后生效”——
  钉死它不谎称要重启。

revert 验证（只回退这一处）：把 SETTINGS_LABELS 的键、`add_entry`、`save()` 那行三处一起撤掉，
上述 3 条立即变红（count!=6 / 键缺失 / `_settings_vars` 等不到第 6 格），还原恢复绿。

全套 458 用例全绿(457+1)。longlines 0, scan_odd2/ast_unused 零输出, py_compile OK。
README：用例数 457->458，配置表 interval_seconds 行补“设置窗可改, 保存后下一轮生效”。

exe 已重打并自证：一体版 sha256[:16] = `3a1d593e16936ed7`＋verify_exe
3.py 证 gpu_monitor 与 gpu_tray 两模块都与仓库源码逐字一致＋verify_imports
 0 缺失。`--list` rc=0/UTF-8/显卡在，`--status` rc=0。
看门狗恢复后托盘用新构建拉起(2 进程，含新的 gpu_tray 设置格)，Web 面板端口 8787 HTTP 2
00，重启后无 tray_error.log / 无 gpu_monitor_error.log。

## 108. 续九十九：git bash 走完整重建链, 把 §106/§107 的可及性修复真正发进 exe(交付)

用户指示“调用gitbash 完成项目” —— 用 git bash 驱动完整重建+部署, 让源码里的
修复真正进入用户正在跑的 GPUMigrate.exe。本轮完成并已在线验证。

- 重建: `_buildchain.py` (clean_build -> build_onefile -> build_onedir) 全绿, CHAIN_DONE。
  新 onefile sha256[:16]=d2fcc130c532aafb (旧 1e8c04df1e9c4e46, 旧 exe 已备份 .tmptest/GPUMigrate.exe.old 作回滚)。
- 部署顺序(关键: 覆盖 root exe 前必先停托盘, Windows 锁着运行中的 exe):
  停看门狗 GPUMigrateWatchdog -> taskkill 停托盘 -> sync_onedir+sync_onefile ->
  verify_exe3+verify_imports -> 起新 exe --tray -> 恢复看门狗。全链带回滚保护。
- verify_exe3: gpu_monitor/gpu_tray 在 dist-onedir 与 root onefile 两处, 四组合
  “与仓库源码一致” 全 True; verify_imports: 两侧都找不到的: []。
- 现场确认(新 exe 真在跑): panel HTTP 200, input-aria=True 且 svg-a11y=True
  (§106 输入框 aria-label + §107 趋势图 role=img/aria-label 均已在线生效);
  托盘 2 进程, 看门狗已重新启用。
- 踩坑(别再犯): sync/verify 脚本都在 .tmptest 里, 但内部用仓库根相对路径
  (dist/...、GPUMigrate.exe、gpu_monitor.py); 必须“全路径调用 + cwd=仓库根”。
  前两次 deploy 分别栽在 cwd=.tmptest(verifier 找不到 dist/)与 cwd=ROOT(找不到脚本名)。
目标“调用gitbash 完成项目”已达成: exe 已重建、校验通过、上线运行、看门狗正常。

## 107. 续九十八：趋势图 SVG 补 role=img + aria-label, 读屏用户也看得见走势(美观性/可及性)

接续上轮的面板可及性(panel a11y)主题: 把最后一块对读屏软件隐形的视觉元素补上。

- gpu_monitor.py `sparkline_html`: 核显趋势那张 <svg> 对读屏软件是块空白图形。
  加 `role='img'` + `aria-label='核显占用趋势'`。角上的峰值/当前值和“迁移阈值 40%”
  本就是普通文本 span, AT 照常能读; 图形本身再挂个可访问名即可。
- 扩 `test_the_panel_carries_the_sparkline` 钉住(role/aria-label 两断言), 用例数不变(499)。
- 反证非空: 只删 `role='img'`, 该测试当场变红。
- 渲染核对: <svg ...> 实测 = class=spark role='img' aria-label='核显占用趋势'
  viewBox='0 0 300 46' preserveAspectRatio='none'。

全套仍 499 passed / 60 subtests; longlines(gpu 16/tests 5, 新增行不在列)/scan_odd2/
ast_unused(仅既有 p@tests:7127) 干净; py_compile OK; gpu_monitor.py 全程 CRLF, tests 全程 LF。
exe 仍未重建(需你点头才停托盘重启, 见 §106)。

## 106. 续九十七：面板筛选框补可访问名, 删一条被遮蔽的死测试(美观性/可读性)

本轮(2026-09-27)只读复核 + 一处小改, 没动任何已验证正确的准确性逻辑。

- gpu_monitor.py `panel_html` 的筛选输入框原来只有 placeholder, 没有可访问名:
  输入后 placeholder 会消失, 读屏/纯键盘用户等于对着一个没标签的输入框。
  补 `aria-label='筛选进程名或 PID'`(该行 74 字符, 不破 longlines 79)。
- 删掉 `PanelHtmlProcessNames` 里一条被遮蔽的死测试: 两个同名
  `test_the_summary_cards_and_the_filter_box_are_there`, 前一条(少一个
  localStorage 断言)在同类里被后一条覆盖, unittest 从来没收集过它。删前一条,
  零覆盖损失; 新增 `test_the_filter_box_is_labelled_for_accessibility` 钉住 aria-label。

验证(全绿):
- 反证非空: 从源码删掉 `aria-label` 那段, 新测试当场变红(AssertionError), 证明确实在校验。
- pytest 全套: 499 passed / 60 subtests。
- longlines gpu_monitor.py 16 处(新增行不在其列); scan_odd2 干净; ast_unused
  仅报一条先前就存在的未用变量 `p`(tests:7124, 与本轮无关)。
- `py_compile -W error::SyntaxWarning` OK; gpu_monitor.py 全程 CRLF, tests 全程 LF, 无混行尾。

README: 用例数 498 -> 499。
仓库卫生: 上一轮遗留的 50 个 _before/_fixed 草稿(.py)已移入 gitignore 目录
  .tmptest/_scrap_backups/(移动而非删除)。
exe 未重建: 改动是纯面板 attribute, 不影响正在跑的 GPUMigrate.exe; 要同步需走
build_onefile->sync 链并重启托盘(会打断已稳跑 24h+ 的实例), 待用户点头再做。

## 105. 续九十六：崩溃定性复核 + §105 三项候选逐一证伪(结论：代码正确, 无需改)
用户再次报“程序一直崩溃”。拿 7 天事件日志核到底：
- GPUMigrate.exe 崩溃(Id 1000) 近 7 天 0 条；唯一相关日志是 7 条关机否决
  (Id 10001“试图否决关机”)，那是另一回事，不是崩溃，见下面“待决策”。
- 真在崩的是别的进程：.NET Runtime 762 条；llama-server.exe / LiveAutoClicker.exe /
  OrtProbe*.exe 等 Application Error 82 条；Codex 沙箱自身 5 条。llama-server 与
  LiveAutoClicker 都在 live-autoclicker 项目目录，与本仓库无关。
- 本程序三层自愈现网健康：cmd_monitor 主循环顶层 except 逐轮 continue
  (gpu_monitor.py:2663，接 §92)；看门狗 GPUMigrateWatchdog 每 2 分钟 --ensure；
  faulthandler 把全线程栈落 gpu_monitor_crash.log —— 该文件 0 字节，上线版至今没崩。
  非破坏性复核：托盘存活时 --ensure 静默退出不重开；面板 8787 返 200；稳态为
  冻结父子对(2 进程)；tray_error.log / gpu_monitor_error.log 都不存在。

§105 原定审计三项，逐一拿真值跑完，全是“看着可疑实则正确”，别再追：
- hidden_noise_count 与“GPU 进程”卡、三面行集口径完全一致。现网实测卡片
  “GPU 进程 30” = 25 行 + “已隐藏 5 个 0.0%/0MB”，shown+hidden==union 恒等；
  panel_summary / panel_html / 托盘 _refresh_tree 共用同一个五键 union、
  同一个 row_is_noise、同一个 1048576 除数，不可能对不上。
- spark_ymax / spark_y_of：ymax = max(30, 峰值, 阈值×1.15) 恒 ≥ 每个绘制值，
  折线最顶端 y=6、底端 y=42，都在 46px 画布内，不存在“值超界被 SVG 裁掉”。

WARP“软件渲染”适配器——被测试钉死的设计，别当 bug 改：
- --list 里 dwm / explorer / ToDesk 落在“Microsoft Basic Render Driver”(virtual)
  上的利用率进了 GPU% 列(ToDesk 1.4%)，但核显%/独显% 都是 0。我一度以为是
  “列加不起来”的瑕疵，想按 GPU%=max(核显,独显) 收口。
- 但 tests/test_gpu_monitor.py:142 test_unclassified_adapter_counts_only_in_gpu_all
  明确钉死：虚拟/未知卡计入 gpu_all(展示)，但不进 util/dgpu/igpu_mem(迁移判定)。
  所以 1.4/0/0 是既定口径，不是缺陷。面板 steady-state 因 cmd_monitor 的 allowed
  只采核显+独显(性能取舍)，故不显虚拟卡占用；CLI 全采、面板省采，非数值错误。保持原状。

验证：全套 498 用例 + 60 subtests 当前复跑全绿(4.18s)。本轮纯审计归档，无源码改动，
故不涉及 revert 非空性、不重打 exe、不动 README 用例数。

待用户拍板的真问题(都不该擅自定 / 都不便在本机安全复现)：
- 关机否决：托盘进程关机时被 Windows 记“试图否决关机”(7 天 7 次，关机卡几秒)。
  两头都查了：pystray 的 _dispatcher 不接 WM_QUERYENDSESSION、落 DefWindowProc 回 TRUE；
  又用一次性进程给 tk withdrawn 根窗与 Toplevel 直发 WM_QUERYENDSESSION，也都回 1(放行)。
  没有任何窗口回 0 否决关机 —— 这条是“关机广播时消息泵没来得及回应”(疑 GIL/时序，监控线程
  采样例程占着 GIL 时消息泵线程被饿住)，属时序问题，加窗口 handler 无效；定位需真机 shutdown
  /logoff 复现，不在用户机上擅自触发，故只记录不动手。
- WARP 三面拉平：要让面板也显虚拟占用(与 CLI/测试契约一致)需让面板也采 WARP，
  代价是每轮多几个 PDH 计数器且面板可能多出 0/0 行，属产品取舍。
- 子进程隔离采样：根治原生段错误的唯一手段，但 exe 从未崩，不建议，除非要零停机。


本轮还抽验了旧会话 §4/§5 里“未实测”的线索，均已闭环，别再追：
- 配置类型/边界：merged_config 已对 DEFAULT_CONFIG 全部 9 数字/12 布尔/6 列表字段逐一
  _coerce_number/_coerce_bool/_coerce_list，_NUMBER_RULES 覆盖齐 —— “threshold_percent
  写成字符串会在 hot_reason 炸 TypeError” 的旧隐患已不存在。
- RegEnumValueW 的 ERROR_MORE_DATA(234) 已扩容重试(_enum_value)，--status 静默少报已修。

## 104. 续九十五：迁移状态列在悬停底色上压线 —— 可读性守卫补上悬停底这一档(准确性/可读性)
§103 收尾后, 拿 §88/§90 的 WCAG 方法论把 `_PANEL_STYLE` 又过了一遍。这次盯的是
`tbody tr:hover`: 鼠标掠过某一行时行底换成 `#23233a`, 而原来的守卫
(`WebPanelLegibility`) 只拿页面底色 `#14141e` 查 —— **悬停底这一档从头到尾
没进过名单**。逐条量过去, 只有一处真掉下去:
  td.pref,th.pref(迁移状态列)  #8b8ba0
    页面底 #14141e   5.48:1   过
    悬停底 #23233a   4.59:1   正踩 AA 线(4.5:1)
  这一列压根不在 `COLORED` 名单里, 所以从来没被守卫看过一眼。而"鼠标正在读
  的那一行"恰恰是最不该省对比度的一行 —— 用户就是冲着这一行来的。
顺手把非文字的 UI 组件也核了, 结论是都不用动(记下来免得下个会话重跑):
- `#q:focus` 的聚焦边框 `#5b8def` 在卡片底 `#1e1e2e` 上 5.08:1, 过
  WCAG 1.4.11 的 3:1;
- 输入框静止边框 `#3a3a4e`、卡片边框 `#2e2e3e`、表格分隔线 `#262636` 都低于
  3:1, 但都是纯装饰(没有状态语义), 1.4.11 明确豁免, 不改。
改法(`gpu_monitor.py`, 只动一行色值): `td.pref,th.pref` 的
`color:#8b8ba0` -> `color:#9a9ab0`, 直接复用 `th` 已经在用的那一档灰 ——
§88 定过"不引入新色值": palette 里每多一个近邻灰, 下次调亮度就多一处要同步。
悬停底上 5.55:1, 页面底上 6.64:1。
`gpu_monitor.py` sha256[:16] = `300893e4af832c73`(动手前 `8713a9dc2863fec4`)。
测试(`WebPanelLegibility`, 2 条新增 + 1 处改):
- `test_table_colours_clear_aa_on_the_hover_background`: 解析
  `tbody tr:hover{background:...}`, 对 `td.pref,th.pref` / `.hot` /
  `.warm` / `.cool` 和 body 正文色按 **5.0** 阈值查悬停底。阈值不放 4.5:
  AA 只是及格线, 4.59 那种"压线过"换个色值就掉下去, 而这里没有任何机制会
  提醒, 所以留半格余量当护栏;
- `test_the_pref_column_reuses_the_header_grey`: 钉死这一列和 `th` 用同一个
  灰, 不许新造近邻色;
- 新增 helper `_hex6()` 展开 `#ddd` 这类三位缩写(不展开 `_wcag_luminance`
  切不出三段会炸); `COLORED = COLORED + HOVERED`, 两份名单不至于走散。
revert 验证(本轮两种, 每次逐字还原 + sha 校验):
  T pref 退回 #8b8ba0           2 红(4.59 not >= 5.0)
  U pref 换成近邻灰 #a0a0b8     1 红(不复用表头那档)
  V 名单不再纳入悬停档         0 红(预期: hover 那条自己就查了悬停底, 不构成覆盖损失)
  还原后 sha256[:16] 一致: `300893e4af832c73`。
全套 498 用例: `python -X utf8 tests/test_gpu_monitor.py` OK, `pytest`
498 passed / 60 subtests, 反序 498 tests 0 failures / 0 errors。
longlines / ast_unused / scan_odd2 / py_compile(-W error::SyntaxWarning) /
乱码终扫全 clean。
README: 用例数 496->498, 在 §103 那条后面插入"迁移状态列在悬停底色上也看得清",
把 5.48:1 -> 4.59:1 -> 5.55:1 三个实测比值和"不引入新色值"的理由写进去。
exe 已重打并自证(源码改过就必须重打): 一体版 sha256[:16] =
`1e8c04df1e9c4e46`(替换 §102 的 `3a1d593e16936ed7`); verify_exe3.py 两产物
四模块全 True, verify_imports 0 缺失。停旧托盘 -> 同步 -> 重启之后:
`--list` rc=0 / 40 行, 面板 200, HTML 里
`td.pref,th.pref{text-align:left;color:#9a9ab0}` 已生效。
顺手记两笔排障事实, 免得下一个会话从零查:
- 一体版 exe 本身就是**两个进程**: PyInstaller onefile 的父引导进程 + 真正
  跑应用的子进程。任务管理器里看到两个同名 GPUMigrate.exe 是正常的, 不是双实例。
- "程序一直崩溃"这条, 拉事件日志逐条对过: GPUMigrate.exe 的 Application Error
  **0 条**; 同一时段崩的是 llama-server.exe(ucrtbase 0xc0000409) 和
  LiveAutoClicker.exe(MSVCP140 0xc0000005), 都属别的项目。22:57 / 23:01
  两条 WER `GDIObjectLeak` 是 python.exe 3.14.3, 时间点落在 live-autoclicker
  的构建窗口里。在跑的托盘进程用 `GetGuiResources` 量 GDI=12 / USER=23,
  没有泄漏曲线; `gpu_monitor_crash.log` 0 字节。本仓库源码态 python 唯一一次
  真崩是 16:45 的 `_ctypes.pyd` 0xc0000005(采样循环), 看门狗 2 分钟一次
  `--ensure` 能兜住重启。要根治得把采样隔离到子进程, 属架构级改动, 未获
  批准前不动。

## 103. 续九十四：面板里的进程名永远只是文字 —— _html_escape 的零覆盖补上(准确性/安全)

按老规矩先扫一遍全库: 把 `gpu_monitor.py` / `gpu_tray.py` 每个顶层函数在
`tests/test_gpu_monitor.py` 里的引用数列出来, 专挑 **0 引用** 的看。零引用的
十几个里, 只有一个同时满足"用户可见 + 外来输入 + 守的是正确性": `_html_escape`。

它在 `gpu_monitor.py:1197`, 全库三个调用点全在 `panel_html` 内部(进程名、
核显名、独显名), **测试一次都没直接调过它**, 也没有一条用例驱动过带尖括号
或 `&` 的名字走到它跟前。也就是说这个守卫函数的正确性此前只活在 docstring
里。而它守的东西不该出事:

- 进程名来自系统进程表, 显卡名来自 D3DKMT 查询结果, 两边都不受我们控制;
- Web 面板的前端局部刷新(`_PANEL_SCRIPT` 每秒一次)走的是
  `a.innerHTML = b.innerHTML` —— DOMParser 解析服务端响应之后把 innerHTML
  倒回活 DOM。**服务端这一处转义是整条链上唯一的屏障**, 漏一个字符就是把
  面板变成注入点;
- 漏转义的后果不只是难看: `</table>` / `<tr>` 能改表结构, 而前端筛选那一行
  是按 `querySelectorAll('tbody tr')` 和 `td.n` 计数的 —— 名字里塞一个假
  `<td class=n>`, "显示 N / M 行" 当场说谎。

新增 8 条(`HostileNamesInPanel`), 全部用敌意名字驱动**真实** `panel_html`,
不断言"我调了转义函数":
- `<script>alert(1)</script>` / `a & b </table><img ...>` / `</p><td class=n>伪造行`
  三种名字: 输出里 `<script>alert` / `<img` 一条不出现, 但转义后的字面量在;
- 结构标签条数与名字内容无关: `<tr>` 恒为 6(1 表头 + 4 数据行 + 1 空态行)、
  `</table>` 恒为 1、`<td class=n>` 恒为进程数(空态行用的是 colspan);
- `&` 只转一次 —— `&amp;amp;` 出现就说明"先转尖括号再转 &"的顺序写反了;
- 全角 `＆` 原样通过(中文进程名是常态, 不该被误伤);
- 直接单测 `_html_escape`: 三角色、已转义输入会被再转一次(文本节点的正确
  语义)、非字符串入参被 `str()` 兜住而不是抛;
- 最后一条把**边界**写进用例: 引号不在转义名单里, 现在三个调用点全是文本
  节点所以是对的, 但谁将来把名字塞进 `title="..."` / `value="..."` 这类属性,
  就得先来这里加 `"` 和 `'`。这条是留给下一个人的话, 不是断言。

顺手核过但**没有 bug** 的相邻路径(记下来免得下一个会话重跑):
- `history.jsonl` 是只写的: `snap['history']` 来自内存里的 `hist_points`
  deque(`gpu_monitor.py:2330` / `2501`), 不从磁盘读, 所以"手改坏的
  history.jsonl 打死 panel_html"这条路不存在;
- `policy_text` 的 `f"{threshold:g}"` 看起来怕字符串, 但 `threshold_percent`
  在 `_NUMBER_RULES` 里被 `_coerce_number` 收过, `mode` 走 `_MODE_NAMES.get`
  有默认值, 都到不了格式化那一层;
- `_ellipsize` / `fit_tip_title` 按 UTF-16 码元截断且按码点遍历, 不会把代理对
  折成孤子代理; `room >= 4` 才走软截断, 否则落到硬截断, 两条路都守得住上界。

revert 验证(六种变异, 每次逐字还原 + sha 校验):
  N 完全不转义                         7 红
  O 只转 <, 漏掉 >                      6 红
  P 只转 >, 漏掉 <                      6 红
  Q 先转尖括号再转 &(二次转义)           6 红
  R 进程名不再过转义                     5 红
  S 显卡名不再过转义                     1 红
`gpu_monitor.py` sha256[:16] = `8713a9dc2863fec4`, 与动手前一致。

全套 496 用例: `python tests/test_gpu_monitor.py` OK, `pytest` 496 passed,
反序 0 failures / 0 errors。longlines / ast_unused / scan_odd2 / py_compile /
乱码终扫全 clean。`gpu_monitor.py` / `gpu_tray.py` 一行未改, exe 无需重打,
`verify_exe3.py` 两产物四模块仍与源码逐字一致。

README: 用例数 488->496; 新增"面板里的进程名永远只是文字"一条。
顺手把 `README.md` / `HANDOFF.md` 的行尾统一成 CRLF —— 本轮前几次插入留下
了 20 + 317 行 LF 尾, 在 `core.autocrlf=true` 的工作树里跟邻居不一致,
diff 看的时候整块都是噪音。

## 102. 续九十三：on_battery 的 ctypes 真值驱动, 电池文案与端到端链(准确性/可观测性)

照 §100 的老规矩找下一个缺口: 拿真值去核用户可见路径。这次核的是
`on_battery()` —— power_aware 的总入口。结论和 §100 一模一样: **这个函数
只被 stub 驱动过**。LoopHarness 一律 `gm.on_battery = lambda: False`,
PowerAwarePolicy 三条也是 `lambda: True`, 于是真实实现里对
`GetSystemPowerStatus` 的调用、`ACLineStatus` 的三态语义、两条 fail-safe
分支, 全库零覆盖。拔电源是用户真会做的事, 这里读错就得到一整套错的迁移
门槛, 而界面上没有任何一处会提示"现在到底按哪个数在判"。

新增 14 条(共 474->488):

- **`OnBatteryGate` 6 条**: 用假 kernel32 真值驱动真实 `on_battery()`。
  `mock.patch.object(gm.ctypes, "WinDLL", ...)`, 假 API 拿调用方给的指针,
  `ctypes.cast(ptr, POINTER(gm._POWER_STATUS)).contents.ACLineStatus = v`
  写进去再返回 1 —— 走的完全是真实那条路。0→True(电池)、1→False(外接)、
  其余(1/2/42/127/-1/-128)全 False; fail-safe 两条: API 返回 0(此时结构体
  一个字节都不写, 老实现若只盯结构体读到的是一块没填过的内存)→ False;
  WinDLL 直接抛 OSError → False, 不许冒到上层。
  - **踩到的真坑**: Win32 的 `ACLineStatus` 是 BYTE(255=未知), 而结构体用的
    是 `ctypes.c_byte`(有符号), **写 255 读回来是 -1**。所以"按真值钉 255"
    这种写法是假的 —— 第一版就写了 `line_status=255`, 拿
    `in (0, 255)` 去变异它照样全绿。现在用例按"结构体里实际会出现的值"写,
    -1/2/42/127 全覆盖, 并在 docstring 里把 255→-1 这件事写明。**没动源码**:
    `c_byte` 在唯一的 `== 0` 比较上与 `c_ubyte` 等价, 改它属于无收益的镀金。
  - 顺带一条 `test_the_status_layout_matches_the_windows_struct`: 钉死字段
    名/顺序/偏移(0/1/4/8)。这条不能省 —— 前面几条写结构体用的也是同一个
    定义, 布局真错了它们照样全绿, 而"插个字段把 ACLineStatus 读成
    BatteryFlag"会让电池/外接永远判反。
- **`PolicyTextBattery` 5 条**: `policy_text` 的第四个参数此前从没被非默认
  值驱动过。后缀只是追加(模式/阈值/自动切回三段一字不变)、默认参数不带
  后缀、面板页眉带这句话、托盘悬停带同一句话(`object.__new__(TrayApp)` +
  打桩 `_refresh_title` / `icon` 跑真 `_on_sample`)。
- **`PowerAwareRealPowerStatus` 3 条**: 把 LoopHarness 的 stub 摘掉
  (`gm.on_battery = self.saved["on_battery"]`), 换成假内核 + 打点时钟
  (`gm.time.time` 每次 +15s, 一轮里它被调 5 次即每轮 +75s, 冲开电源块的
  30s 闸门 —— 否则一次运行只问一次内核, 状态翻转永远看不到)。于是从
  `GetSystemPowerStatus` 一路验到迁移决定: 45% 越过电池阈值 40% 所以真的
  迁了; 先拔后插时 `切换到电池` 必须出现在 `已接通电源` 之前; 插回后阈值
  回到 config 里的交流值 50, 且电池那一轮已经迁过就不再动它。

revert 验证(九种变异, 每次逐字还原 + sha 校验):
  A 电池判反(通电当电池)                 10 红
  B 未知态(-1)也当电池                    1 红
  C 不看 API 返回值, 直接信结构体          10 红
  D 只吞 NameError, OSError 冒出去        1 红
  E 电源块跳过不查(on_battery 白写)       5 红
  F 电池阈值取错字段(用交流阈值)           5 红
  G policy_text 丢掉电池后缀              6 红
  H 托盘不把 on_battery 喂给 policy_text   2 红
  I struct 多插一个字段(偏移全错位)        1 红
逐字还原后 `gpu_monitor.py` sha256[:16] = `8713a9dc2863fec4`, 与动手前一致。

全套 488 用例: `python tests/test_gpu_monitor.py` OK, `pytest` 488 passed,
反序 0 failures / 0 errors。longlines 三个文件全 clean, ast_unused /
scan_odd2 零输出, py_compile OK, 乱码终扫 clean。
`gpu_monitor.py` / `gpu_tray.py` **一行未改**, 所以 §95 的两个构建
(`GPUMigrate.exe` sha256[:16] = `e8bd5fbcecf6c632`、
onedir `9aec456f9b09f57a`)无需重打, `verify_exe3.py` 复核两产物四模块
仍与源码逐字一致。

## 101. 续九十二：测试套件与执行顺序无关 —— 四处模块级全局泄漏(测试卫生)

### 缘起: 换第二个跑法一照, "全绿"就塌了

想把基线坐实, 顺手用 pytest 跑了一遍(`python -X utf8 -m pytest
tests/test_gpu_monitor.py`) —— 而 README 写的是 `python
tests/test_gpu_monitor.py`。结果 **5 红**。一开始以为 pytest 和 unittest
收集顺序不同导致的事故, 查下去发现根因在仓库里: **四处模块级全局会被用例
留下来, 只是 unittest 的字母序恰好让守卫先跑、污染后发生, 于是看不见。**
同一套代码换任何别的顺序就红 —— 这不是 pytest 的问题, 是夹具的漏洞。

### 四处泄漏

1. **`ListTableLayout` 不还原 `discover_gpus`**(tests 2121)。setUp 把
   `gm.discover_gpus` 换成只认 `"L0"` 的替身, 但 `self.saved` 只收了
   `(collect_gpu_sample, pid_to_name)`, tearDown 也就只还原这两个。替身
   于是漏给后面每一条用例。`CrossSurfaceRowOrder._cli_order()` 偏偏要跑
   真的 `cmd_list()`, 拿到替身之后 `gpus.update(discover_gpus(fresh))`
   填不进去, `gpus[luid]` 当场 `KeyError: '00000000_00013897'` 把 `--list`
   打死。字母序下 `CrossSurfaceRowOrder` 排在 `ListTableLayout` 前面, 所以
   文档那个跑法看不见。
2. **`TraySingleInterpreter` 五条用例的 cleanup 还原错值**(tests 3678+)。
   五处都是同一个形状: 用例体里
   ```python
   saved_ui_file = self.gt._UI_FILE
   self.addCleanup(setattr, self.gt, "_UI_FILE", saved_ui_file)
   self.gt._UI_FILE = ui_file
   ```
   而 `setUp` 早在第 3730 行把 `_UI_FILE` 换成临时路径了, 所以这里"取原值"
   取到的是**临时值**; 又因为 unittest 的顺序是 setUp -> 用例 -> **tearDown
   -> doCleanups**, tearDown 刚刚还原真值, 紧接着 cleanup 又把它改回一个
   已经 `rmtree` 掉的临时目录。于是 `TrayPanelUiIsolation` 两条(它专门守
   这件事)在 pytest 顺序下必红, 而且 `SharedLogicIsOneSource` 也会跟着黄。
   其中 `test_panel_ui_is_written_atomically` 那条还叠着一个真需要的
   `write_json_dict` cleanup, 只删 `_UI_FILE` 那三行, 保留另一条。
3. **`gpu_tray` 顶层 from-import 被 import 顺序污染**(最隐蔽的一处)。
   `gpu_tray.pid_to_name` 是 `from gpu_monitor import pid_to_name` 在
   **import 那一刻**绑的值。放进用例里 `import gpu_tray`, 而它第一次被
   import 时(实测是 `ListTableLayout::test_a_cjk_process_name_...`)正好
   挂着 `ListTableLayout.NAMES.get` 这个替身 —— 托盘于是**永久**拿住那份
   替身; 还原只发生在 `gm` 那一侧, `SharedLogicIsOneSource` 拿活的值
   一比两头都"对", 这条守卫被顺序耍成了假绿。
4. **`TrayStartupFailure._run` 泄漏一个开着的文件句柄**。这条只影响倒序。
   `_run` 让 `gm.main()` 在临时 `_DIR` 上跑, 而 `main()` 第一件事是
   `install_fault_dump()`, 它在那里开了 `gpu_monitor_crash.log` 并把句柄
   缓存进全局 `_FAULT_FH`。测试从不收回 → 句柄一直开着, 临时目录删不掉
   (`WinError 32: 另一个程序正在使用此文件`), 而且 faulthandler 还指着
   一个即将消失的文件。

### 修法(只动测试文件)

- 前三处按最小 diff 修: `ListTableLayout` 的 `self.saved` 补上
  `gm.discover_gpus` 并按四元组还原; 五处 `_UI_FILE` 的"取原值 + cleanup
  + 赋值"整块删掉(setUp 已经做好隔离, 那段本来就是重复劳动);
  `gpu_tray` 提到测试模块顶层 import, 紧跟 `import gpu_monitor as gm`
  之后, 并写清"顶层 import 保证绑定发生在任何替身出现之前"。
- 新增一条 AST 守卫 `SharedLogicIsOneSource.
  test_the_tray_is_imported_before_any_stub_can_exist`: 解析测试模块自身
  源码, 顶层没有 `import gpu_tray` 就红。将来谁为了"顺手"把它搬回用例里,
  或者删掉顶层那行, 都会立刻红。用的还是 §95 `install_fault_dump` 那条
  AST 守卫的路数(看真实 Call, 不看字符串)。
- `TrayStartupFailure._run` 的 finally 里收回 `_FAULT_FH`: 先存原值, 拿到
  main() 装上去的新句柄, 还原全局, 再 `faulthandler.disable()` + `close()`
  —— 先停 handler 再关文件, 免得关掉之后还往里写。

### 验证

revert 验证(四种变异, 每次逐字还原 + sha 校验):
  J ListTableLayout 不还原 discover_gpus           20 红
  K gpu_tray 的 import 搬回用例里                    2 红
  L 托盘悬停不再喂 on_battery 给 policy_text         1 红
  M TraySingleInterpreter 把 cleanup 加回来          2 红

顺序无关性: 正序(`python tests/test_gpu_monitor.py`)488 OK;
反序(类表反转)0 failures / 0 errors; `pytest` 488 passed;
且 `pytest` 连跑三遍结果一致, 不再复现 §§101 开头那 5 红。
`gpu_monitor.py` / `gpu_tray.py` **一行未改**(sha256[:16] 仍为
`8713a9dc2863fec4` / `8bc896c42b2662d6`), exe 无需重打, `verify_exe3.py`
两产物四模块仍与源码逐字一致。

README: 用例数 474->488; 新增两条 bullet(电池判定真值驱动、测试与执行顺序
无关), 后者把三处泄漏各写了一段, 免得下一个人再踩。

## 100. 续九十一：给 notify_cfg 的全屏免打扰分支补上门控测试(准确性/体验)

用户目标含准确性与美观性, 大到崩溃诊断、小到通知别在错误时机弹, 都算。这轮盯上
is_foreground_fullscreen / notify_cfg 这一组此前没被真值驱动过的门控。

发现真实缺口: notify_cfg 的全屏免打扰分支(notify_fullscreen_ignore=True 且前台全屏时
抑制通知)此前**没有任何针对性测试** —— is_foreground_fullscreen 在 LoopHarness 里被固定
stub 成 False, 全库对它的真实调用 0 次。也就是说"全屏打游戏/放视频时不弹通知"这条用户
可见行为, 一直没被守; 重构一旦动了那段, 通知就会在全屏上弹出来骚扰, 而测试一片绿。

改动(只加测试): `NotifyGating` 用打桩的 is_foreground_fullscreen + 偷看 gm.notify, 把
整张门控矩阵钉死 —— 全屏前台→静音、显式关掉 notify_fullscreen_ignore→仍送达、非全屏→放行、
游戏运行中→静音、总开关 notify=False→全静音。

revert 验证(只回退这一处): 临时删掉 notify_cfg 里 `if ...notify_fullscreen_ignore... and
is_foreground_fullscreen(): return`, 恰好 test_fullscreen_foreground_mutes_the_notification
一条变红(全屏下本该静音却收到), 其余 4 条仍绿; 逐字还原后回绿。

全套 474 用例全绿(469+5)。ast_unused/scan_odd2 零输出, longlines 0, py_compile OK。
README: 用例数 469->474。**未重打 exe**(仅改测试; verify_exe3 复核见产物自证)。

## 99. 续九十：README 配置表不只查"键在", 还要查"默认值对得上"(文档准确性)

用户目标含美观性与准确性, 文档准确性也算准确性。逐条核对了 README 配置表的每个字段
默认值与 DEFAULT_CONFIG, 并确认两处散文里的具体数字也对: 内置排除名单正好 16 个
(dwm…conhost), 日志 `LOG_MAX_BYTES = 5*1024*1024`(超 5MB 滚 `.log.1)。

发现真实缺口: 已有 `ReadmeConfigTable` 只查"键两边都在(不许多、也不许漏)", **没查默认
值**。代码把 threshold_percent 默认从 50 改成 40、README 忘了改, 键两边都还在, 没人报错,
但照 README 手改 config.json 的用户拿到的是另一套起点。

改动(只加测试): `ReadmeDefaultsMatchCode` 解析 README 配置表每行的 `字段 |
 默认` 两列,把代码默认值折算成 README 写法(布尔→true/false、整数/含 .
0 的 float→整数字符串、空列表→[])逐一比对; dict / 非空容器只求键在(ReadmeConfigT
able 已覆盖), 不逐字节比 README 散文式写法,避免脆弱。revert 验证(只回退这一处)
: 把 README 里 threshold_percent 默认 50 改成 42, 测试精确报"README 里
 threshold_percent 的默认值和 DEFAULT_CONFIG 不一致"变红;
 逐字还原后回绿。全套 469 用例全绿(468+1)。ast_unused/scan_odd2 零输出,
 longlines 0, py_compile OK。README: 用例数 468->469。
**未重打 exe**(仅改测试; verify_exe3 复核见产物自证)。

## 98. 续八十九：一次性守住"托盘是 gpu_monitor 的薄 UI", 模块级 import 全量身份校验(准确性/一致性)

接 §97。项目铁律"同一套数据两个界面说一样的话"目前靠几条分散的 `gpu_tray.X is gm.X`
(activity_key / row_is_noise / heat_class / spark_ymax / spark_corner_text / PREF_HIGH_TEXT)
在守, 但 gpu_tray 从 gpu_monitor 模块级 import 了 20+ 个共用符号, 其余全靠自觉, 没守卫。
一旦有人在 gpu_tray 里复制/包装任何一个(哪怕"微调一下"), 同一个数就会在两个界面给出
两套结果, 而现有测试发现不了。

改动(只加测试): `SharedLogicIsOneSource` 用 **AST 从 gpu_tray 源码顶层 ImportFrom** 自动
抽出 `from gpu_monitor import` 的全部名字(将来往 from-import 加新共用函数会自动纳入),
逐个 `assertIs(getattr(gpu_tray,n), getattr(gm,n))`, 用"分歧名单"一次性报错点名。
- 只取**模块级** import(`tree.body`), 不 `ast.walk` 全树: 函数体内的 `from gpu_monitor
  import notify` 是局部引用、不是 gpu_tray 模块属性, 与"两界面共用"无关, 混进来会误报
  (gpu_tray 无 notify 属性, getattr 直接 AttributeError);
- 自证用例 test_a_copied_shared_function_is_caught: 把 gpu_tray.heat_class 换成另一个
  函数对象, 分歧名单必须点名 heat_class(证明守卫判得出来, 非空跑)。

revert 验证(只回退这一处): 见自证用例; 另在 §97 已对 heat_class 单独做过托盘复制→变红。

全套 468 用例全绿(466+2)。ast_unused/scan_odd2 零输出, longlines 0, py_compile OK。
README: 用例数 466->468。**未重打 exe**(仅改测试; verify_exe3 复核见产物自证)。

## 97. 续八十八：给"托盘与面板共用同一把热度尺"补上身份守卫(准确性/一致性)

本项目反复强调"同一套数据, 托盘和 Web 面板要说着一样的话", 也确实给多处共用逻辑上了
`gpu_tray.X is gm.X` 的身份守卫: activity_key、spark_ymax、spark_corner_text、
row_is_noise、PREF_HIGH_TEXT 都有。唯独热度分级 `heat_class` 没有——尽管它:
- 被 gpu_tray import 并在托盘图标(行 368)与整行热度色(行 1446)复用;
- 自身 docstring 就写着"阈值与托盘图标一致 (30 / 60), 别只改一处"。

也就是说, 目前没有任何测试拦着"哪天有人在 gpu_tray 里复制一份 30/60 的重算一遍"——
一旦复制, 同一个 GPU% 会在托盘和 Web 面板染成两个热度色(美观/一致性回退), 而现有测试
(只测 gm.heat_class 的分界与图标调色板)发现不了。

改动(只加测试, 不动行为): 在 HeatClass 里补 `test_the_tray_shares_the_one_heat_class`,
`assertIs(gpu_tray.heat_class, gm.heat_class)`, 与既有那几处共用守卫一个路子。

revert 验证(只回退这一处): 在 gpu_tray 里 insert 一个同名本地 heat_class(复刻 30/60),
守卫立刻 `AssertionError: ... is not ...` 变红; 逐字还原后回绿。

全套 466 用例全绿(465+1)。ast_unused/scan_odd2 零输出, longlines 0, py_compile OK。
README: 用例数 465->466。
**未重打 exe**: 本轮只改 tests/, gpu_tray.py / gpu_monitor.py 与备份逐字一致, 已部署
exe 仍与源码逐字一致(见 verify_exe3 复核)。

## 96. 续八十七：拿线上真值把准确性/美观面核一遍, 顺手补上网页面板的温度行守卫(准确性/可读性)

用户目标是"优化准确性和美观性"。上一轮把"崩溃"追清楚后, 本轮先拿**这台机器的实时真值**
把几个关键准确性/美观面核一遍, 再补上一个真实的覆盖缺口——不臆改、每步都以证据为准。

线上核验(逐个有据):
- PDH 利用率折算: 确认是标准的两段式采集(CollectQueryData#1 → 读 raw1 → 睡窗口 →
  CollectQueryData#2 → 读 raw2 → (delta/delta)×100), 逻辑正确, 无漏采/误算;
- 行序: 抓线上面板 28 行, 用解析器抽出每行精确列值, 验证严格按 activity_key
  = max(GPU%, 专用MB, 共享MB) 降序 —— llama-server 凭 13536MB 专用显存排第一(大模型
  占显存多、利用率低), 与文档"三者取最大, 最抢眼排最前"一致;
- 面板真值: 实时 HTML 显示 独显温度 69°C, 与本机 NVML 直读一致; 卡片/表格数值齐全,
  无 `class=>` 空值属性、无未替换 `{}` 占位; snapts 新鲜、paused=0、staleafter=25, 判定正常;
- 顺带确认两处"疑似问题"其实是**已做对**的: LUID→卡名有 kind_cache 持久缓存(不是每轮
  hammer D3DKMT); nvml_gpu_temp(0) 在本机(单 NVIDIA, index0=RTX4060Ti)取的是对的卡。

改动(只加了测试, 未动行为): panel_html 的温度行是面板上唯一跟着 dgpu_temp 走的可见文本,
之前只有托盘 tooltip 有守卫, Web 侧没有。新增 `WebPanelTempLine`(3 条): 有读数显示
"独显温度: 61°C"、dgpu_temp=None 时整行不出现、且与托盘 tooltip 同一写法(别一边℃一边°C)。

revert 验证(只回退这一处): 把 panel_html 里 `temp_line = ... if temp else ""` 临时置空,
恰好 2 条"显示"用例变红、"None 不出现"仍绿(FAILED failures=2) —— 判据非空, 还原回绿。

全套 465 用例全绿(462+3)。ast_unused/scan_odd2 零输出, longlines 0, py_compile OK。
README: 用例数 462->465。

**未重打 exe**: 本轮只改了 tests/, gpu_monitor.py / gpu_tray.py 与被 revert 校验的备份
逐字一致(即 §95 那版), 所以已部署 exe 仍与源码逐字一致, 不必重打(见下 verify_exe3 复核)。

## 95. 续八十六：原生段错误抓不住, 那就用 faulthandler 留一份全线程现场(健壮性/排障)

用户反复说"程序一直崩溃"(也就是 §92/§93/§94 的由头)。先把这条追到底。今天 (09-26) 的事件
日志 Application Error 里躺着约 30 条崩溃, 逐条看去:
- 只有 1 条属于本项目: 16:45:26 `C:\Python314\python.exe` 崩在 `_ctypes.pyd`, 异常码
  `0xc0000005`(访问冲突), 错误偏移 `0x11f70` —— 是**源码跑的 python**, 不是线上 exe;
- 其余 29 条全是**另一个项目** `D:\aishare\repo\live-autoclicker` 的
  `LiveAutoClicker.exe` / `OrtProbe*.exe` 崩在 `MSVCP140.dll`(同样是 0xc0000005),
  和 gpu-migrate 半点关系都没有。把窗口拉到近 7 天, `_ctypes.pyd` 崩溃也只有这 1 次。

而线上真正在跑的是**打包版** `GPUMigrate.exe --ensure`(看门狗拉起 2 进程), 近 7 天一次都没
崩, 也没有 `tray_error.log` / `gpu_monitor_error.log`, Web 8787 返回 200, monitor 日志持续在写。
结论: 真正的 C 级段错误偶发(约一天都未必一次、且多发生在源码/探针采样), Python 的
try/except 接不住, 连 §92 的应急日志都不会写 —— 崩了就是崩了, 连"崩在哪一行"都不知道。

本轮不追那个(大概率依赖瞬时状态的)原生 bug 本身, 先把"留痕"补齐: 真崩过至少能定位。

改法(`gpu_monitor.py`):
- 新增模块全局 `_FAULT_FH = None`(持有打开的崩溃日志, 防被 GC 回收后关掉);
- 新增 `install_fault_dump()`: `faulthandler.enable(file=..., all_threads=True)` 指向 exe
  旁的 `gpu_monitor_crash.log`(`all_threads=True` 是关键: 采样循环跑在守护线程里, 只 dump
  主线程只看到 tk 空转); 老 Python 没有 all_threads 参数时 TypeError 回退; 打开文件/装 handler
  任一失败都当没装, 绝不影响启动;
- `main()` 在 `ensure_utf8_output()` 之后、解析命令行之前就调 `install_fault_dump()`
  (启动早期崩也要留现场)。

测试(4 条, `FaultDumpGuard`, 全绿): 装了且启用(`is_enabled()`)、重复调用复用同一句柄、
`dump_traceback` 真把 Python 栈写进 `gpu_monitor_crash.log`、`main()` 在 `ap.parse_args` 之前调用。

源码守卫先写了一版"判字面子串"——revert 时才发现它是**假绿**: 把 `install_fault_dump()` 注释掉
后, 源码里仍留着 `install_fault_dump()` 这串字面量, `assertIn("install_fault_dump()", src)`
照样过。改成用 `ast` 解析 main(), 要求真有一次 `Call` 到 `install_fault_dump`、且在
`parse_args` 之前; 改完在"未还原"处立刻 `assertIsNotNone(install_lineno)` 变红, 证明判得出来。

revert 验证(只回退这一处): 见上 —— AST 守卫精确抓住"调用被注掉"这一处, 逐字还原后回绿。

全套 462 用例全绿(458+4)。longlines 0, scan_odd2/ast_unused 零输出, py_compile
(`-W error::SyntaxWarning`) OK。
README: 用例数 458->462; §92 段补一句"原生段错误会把全线程 Python 栈打进 gpu_monitor_crash.log"。

复现尝试(证据): faulthandler 全程待命, 对真实采样路径(3 类 PDH 计数器 + 全部 LUID
 的kind/D3DKMT + NVML 温度)做密集压测, 250 轮 / 38.4s 无一次原生崩溃,
 崩溃日志始终 0 字节 ——印证那条段错误由瞬时状态(大概率驱动 TDR / 显示模式切换 / GPU reset
 恰发生在某次原生调用中)触发, 而非稳态采样。故暂不值得为它单独立项做子进程隔离, 留 faulthandler 
留痕兜底即可。exe 已重打并自证: 一体版 sha256[:16] = `e8bd5fbcecf6c632`；
两产物(一体版 + onedir)均经verify_exe3.py 证与仓库源码逐字一致(gpu_monitor /
 gpu_tray 均 True), verify_imports 0 缺失。`--list` 子进程 rc=0(3
 块卡、窗口 1s, 既没被上界卡住也没挂死), `--status` rc=0。看门狗恢复后托盘用新构建拉起(2 
进程), Web 8787=200, 重启后无 tray_error.log / 无 gpu_monitor_err
or.log。

## 92. 续八十三：监控循环单轮异常不再杀死整个程序(健壮性/排障)

用户反复报“程序一直崩溃”，但前几轮都复现不出来：仓库里没有 tray_error.log、事件日志零
GPUMigrate 崩溃记录、进程零重启、CLI/面板全正常。本轮不去猜，而是先把监视面铺开，找
“能一击致命的到底有哪几种”：

- Windows 事件日志（Application Error）硬证本会话真有一次原生崩溃：
  `C:\Python314\python.exe` 崩在 `_ctypes.pyd` 偏移 0x11f70，异常码
  `0xc0000005`（访问冲突／段错误）。这是源码态一次探针/测试跑采样时的
  C 级段错误，Python 的 try/except 接不住。因为采样（PDH / D3DKMT / NVML /
  注册表，全是 ctypes WinDLL）跑在托盘进程里，这样一崩就把整个程序连锅端，
  而且不会留任何 Python 级日志。
- 但更普遍、且一定能在 Python 层修掉的，是“一轮非 OSError 异常掀翻整个进程”。

核心道理（代码原话）：采样循环跑在托盘进程的守护线程里（
`gpu_tray._monitor_thread` -> `cmd_monitor`），而 `cmd_monitor` 的 `while True:`
只在 `collect_gpu_sample` 外接了 `OSError`。聚合 / 进程名 / 注册表回读 /
NVML 温度 / 迁移 / 面板回调（hooks["on_sample"]）任意一步抛个非 OSError（ctypes
`ArgumentError`、解码错、回调里的 bug……），就一路冒到
`_monitor_thread` 的 except，进而 `_monitor_failed` 设 `self.stop=True` 把**整个托盘退掉**（
`run_tray` 的 `while not self.stop:` 循环因此退出）。而托盘此刻是 `--noconsole`：没有窗口、
没有控制台，用户侧就是“程序一直崩溃 / 图标没了”。现有的
tray_error.log 是“Python 异常才写”，真正 C 级段错误连它都不会留。

改法(`gpu_monitor.py`)：把整个 `while True` 循环体用一个 `except Exception` 兜底。
一轮里任何异常都：traceback 同时写进 `gpu_monitor.log`（走 `log()`，尊重
log_to_file 与日志滚动）**和永远落盘的** `gpu_monitor_error.log`（即使用户关了日志，崩溃
证据也必须在，和 §91 把 traceback 写进 tray_debug.log 同一诉求），`time.sleep(interval)`
睡一个周期后 `continue`。设计要点：
- 只吞 `Exception`，不吞 `KeyboardInterrupt` / `SystemExit`（它们不是 Exception 子类），
  Ctrl-C / 正常退出照旧。
- 是“整段循环”兜底而不是在 `_monitor_thread` 外层重启：`cmd_monitor` 里
  web_thread / hotkey_thread 只在函数入口拉一次，线程级重拉会二次起 web
  面板（端口占用刷假告警）+ 热键重复注册导致一次按键回调两次；整段兜底不动线程模型，
  streak / hist_points / game_secs 等运行期状态也原样保留，不因一次抖动清零。
- 睡一个周期而非空转，避免“每轮都抛”时把 CPU 打满、日志刷屏。

测试(2 条, `MonitorLoopSurvivesExceptions`，全绿)：`on_sample` 第 2 次回调抛一个
`ValueError`（非 OSError），断言 cmd_monitor 不外抛、on_sample 被调满 5 次
即第 3/4/5 次照常跑到，证明循环没被一轮异常杀死；日志里出现
“监控循环异常 / ValueError / Traceback”。本类继承 `LoopHarness`（假采样 + 打桩注册表 + 把
`gm._DIR` 指去临时目录），不污染仓库状态。

revert 验证（只回退这一处）：把兜底的 `except Exception:` 换成 `except SystemExit:`
（等于不接 ValueError），两条用例立即从 ok 变 error（ValueError 一路冒出），
逐字还原后恢复 ok。

全套 452 用例全绿(450+2)。longlines(gpu_monitor.py / 测试) 0, scan_odd2/ast_unused 零输出,
`-W error::SyntaxWarning` py_compile OK。README：用例数 450->452，新增
“监控循环单轮异常不再杀死整个程序”一条，文件清单补 `gpu_monitor_error.log`。

未做（记录在案）：真正的 C 级段错误（`_ctypes.pyd` 0xc0000005）Python 层接不住，进程被内核直接干掉、连
tray_error.log 都不留——这条只能靠既有的 2 分钟看门狗重启兜底。若要把监控循环和 GUI
彻底隔离到子进程（段错误只杀子进程、父进程复活它），是更大的架构改造，留作后续，
不在本轮贸然动。用户下次若再看到崩溃，优先看 `gpu_monitor_error.log` 末尾的
traceback + 说明现象（图标消失 / 面板打不开 / 界面异常）。

exe 已重打并自证：一体版 sha256[:16] = `fcd2f1177b5bc8b9`，
两产物(一体版 + dist/GPUMigrate-onedir/) 均经 verify_exe3.
py 证与仓库源码逐字一致，verify_imports 0 缺失。`--list` 子进程采样 42 行、
UTF-8 解得回、三块卡(RTX 4060 Ti / Basic Render / UHD 630)
全部命中；`--status` 正常。看门狗恢复后托盘用新构建拉起(2 进程)，Web 面板端口 8787 HTTP
 200(11115 字节)，重启后无 tray_error.log / 无 gpu_monitor_error.
log。

## 91. 续八十二：面板建失败要留证据, 半建窗口要回收(健壮性)

§90 修完之后想确认"面板打不开"这条路是不是真的干净了，于是写了两个探针
（`.tmptest/s91_probe1.py` / `s91_probe2.py`），用真 tkinter 驱动
`_panel_build`，灌 32 种敌意 `panel_ui.json`。结论分两半：

一半是好消息：32 种敌意布局（`geometry` 畸形/越界、`widths` 是字符串/负数/
None/bool/float/列表、`sort` 列名非法或不是对象、`filter` / `topmost` 是
list / dict / int / None）现在全部能正常建起面板，`_load_ui_state` 加上
§90 的 geometry 筛子确实把最后一个洞堵上了。探针的敏感性也验过：把 §90 的
`_onscreen_geometry` 调用摘掉，`abc` / `1040x` / `-10x-10` / `all-bad` 四种
立刻"面板没建成"，说明这个 0/32 不是探针失灵。

另一半是本节要修的：`_panel_build` 的总 except 本身是这个形状 ——

    except Exception:
        if counted: self._ui_windows -= 1
        self.panel_open = False

计数退了、标志也复位了，看着很周全，但异常被吞得干干净净：
`tray_debug.log` 里一个字都没有，半建的 Toplevel 也没人 destroy。真出问题时
用户看到的是"打开实时面板点了没反应"，而日志里连一行痕迹都没有，想排查都
没有抓手；并且每点一次菜单就漏一个看不见的窗口，越漏越多。这正是"程序一直
崩溃却查不出原因"的完美配方 —— 之前几轮找不到崩溃证据， partly 要怪它。

做法（`gpu_tray.py`）：except 分支里补两件事，规矩与 `_safe_refresh` 一致 ——
- `import traceback` + `_dbg("面板建立失败:\n" + traceback.format_exc())`；
- `root.destroy()`（外面再包一层 try，销毁本身不许再抛）。

过程中踩了两个自己的坑，都记一下：
1. 一开始把 `_dbg` 的路径提成模块常量 `_TRACE_LOG`，结果 `TrayLifecycleLog`
   靠改 `gpu_tray._DIR` 重定向日志的 4 条用例全红 —— 路径必须按调用时的
   `_DIR` 现算，已还原成 `os.path.join(_DIR, "tray_debug.log")`；
2. 新测试第一版用了真 `tk.Tk()`，而测试文件压根不 import tkinter（项目约定
   纯打桩、不依赖显示器）。改成与 `TraySingleInterpreter` 同一个替身 harness：
   `gpu_tray.tk` / `ttk` 换成 MagicMock、`_dbg` 换成记账 lambda、`_UI_FILE`
   指去临时目录（§89 的教训）。

测试（3 条，`TrayPanelBuildFailureIsLoud`，全绿）：失败必须留 traceback 正文；
失败必须 destroy 半建窗口并退还计数/复位标志；正常路径不许被带坏
（`destroy` 一次都不该调、`_ui_windows` == 1）。

revert 验证（三种变体逐个试）：except 整个退回 pass（2 红）、只收窗口不留痕
（1 红）、只留痕不收窗口（1 红）；逐字还原后全绿。

全套 450 用例全绿（447+3）。longlines 无新增（三文件 0 条 >100 列），
scan_odd2 / ast_unused 零输出，py_compile OK。跑完后 5 个状态文件哨兵
（panel_ui / config / history / report / gpu_monitor.log）逐字节不变。

README: 用例数 447->450；新增"面板打不开不再无声无息"一条，把 32 种敌意
布局的实测结果写进去。

本节动了 `gpu_tray.py`，需要重打 exe。

## 90. 续八十一：手改坏的 geometry 不再把面板打死(健壮性)

顺着"面板打不开"这条线把 `panel_ui.json` 的校验覆盖面审了一遍，发现
`_load_ui_state` 只校验了三样形状（顶层是不是对象、`widths` 是不是正整数、
`sort` 是不是对象），`geometry` 只校验了"是不是字符串" —— 而 Tcl 要的是
严格的 `宽x高[+X[+Y]]`。拿真 tkinter 实测（`s90_probe1.py`）：

    root.geometry("abc")            -> TclError: bad geometry specifier
    root.geometry("1040x")          -> TclError
    root.geometry("-10x-10")        -> TclError
    root.geometry("1020x360+5000+5000") -> 不报错, 窗口开在屏幕外

也就是说 `panel_ui.json` 里一行手改坏的 geometry，会让 `_panel_build` 在
`root.geometry()` 上炸，被那个总 except 吞掉之后窗口建一半扔下 —— 用户看到
的是"打开面板点了没反应"，和崩溃长得一模一样，正是 `_load_ui_state` 的
docstring 里承诺过"不能忍"的那件事，只是漏了 geometry 这一个字段。
反过来，形状合法但整窗在屏幕外（面板拖到副屏，副屏拔了）Tcl 一声不吭，
窗口每次都开在看不见的地方，也是同一个现象。

做法（`gpu_tray.py`）：
- 新增 `_parse_geometry(text)`：把 `"1040x470+100+200"` 拆成
  `(1040, 470, 100, 200)`，形状不对一律 None。没有位置的那一半按 +0+0 算。
- 新增 `_onscreen_geometry(text, screen_w, screen_h, default)`：先过形状，
  再按当前虚拟屏查位置 —— 标题栏至少留 120x30 在屏内，越界就退回 `1040x470`。
  往左探出半截是用户自己摆的，标题栏还看得见就不动它。
- 新增 `_screen_size(root)`：虚拟桌面尺寸（多屏取并集），拿不到就退主屏，
  再不行给 1280x800。这个插曲是替身窗口逼出来的：第一版直接  `root.winfo_vrootwidt
h()`，在 `TraySingleInterpreter` 的替身窗口上返回的  是 MagicMock 而不是 
int，三条既有用例当场红 —— 拿不到尺寸只是少一层保险，  不许反过来让面板打不开。- `_panel_buil
d` 里 `root.geometry(ui.get(...))` 改为  `root.geometry(_onsc
reen_geometry(ui.get("geometry"), _sw, _sh))`。
测试（6 条，`TrayPanelGeometryGuard`，全绿）：合法形状 round-trip（含`1920
x1080+1920+0` 多屏负偏移）；12 种畸形形状全部退回默认（含 `None` /`5` / `[]
` / `{}` / `1020X360` 大写 X / `1040x470+1+2+3` / `1040x470+
`）；副屏拔了的四种越界全部退回；往左探出半截和贴下边缘的要保留；拿不到桌面尺寸（None / 抛异常 / 返回 0
）都给兜底且不炸；源码守卫 `_panel_build` 必须过筛子。revert 验证（三种变体逐个试）
：不打筛子直接喂 Tcl（1 红）、只查形状不管位置（1 红）、形状放太宽到允许宽/高留空（`test_every_
malformed_shape_...`当场 ValueError，1 红）；逐字还原后全绿。
全套 447 用例全绿（441+6）。longlines 无新增（三文件 0 条 >100 列）
，scan_odd2 / ast_unused 零输出，py_compile OK。README:
 用例数 441->447；新增"手改坏的面板布局不再打死面板"一条，把`abc` / `1040x` / `-10
x-10` 的 TclError 和"窗口开在屏幕外"两种故障写清楚。本节动了 `gpu_tray.
py`，需要重打 exe。

## 89. 续八十：测试不再冲掉仓库里的 panel_ui.json(健壮性, 顺手挖出来的)

`panel_ui.json` 的 mtime 是 19:45:02 —— 正好是我跑全套测试的那一刻。顺着查下去
（先备份再拿哨兵值复现，再逐条用例二分），坐实一件一直没人注意的事：

  TraySingleInterpreter 只把 config.json 指到临时目录，`_UI_FILE` 还指着仓库
  里那一份。而 `test_closing_a_window_frees_the_slot` 会真的走一遍 on_close，
  于是每跑一次 `python tests/test_gpu_monitor.py`，仓库里的 panel_ui.json 就
  被替身数据覆盖：列宽全是替身 `tree.column` 的返回值（八列都是同一个数），
  geometry 是替身 `Toplevel` 的返回值（"1020x360"）。用户自己的列宽/筛选词/
  排序列/置顶没有任何恢复办法 —— "跑测试顺手毁数据"。

做法（tests/test_gpu_monitor.py）：
- `TraySingleInterpreter.setUp` 里 `_UI_FILE` 跟着 config.json 一起指到
  `self.dir/panel_ui.json`，tearDown 里还原，跟同文件其余面板用例的写法一致；
- 新增 `TrayPanelUiIsolation` 两条守卫（441 = 439+2）：
  - 用例跑的时候 `_UI_FILE` 必须在临时目录里，且不能是仓库那份
    （关窗会往这个路径写布局）；
  - `_UI_FILE` 跑完必须还原，磁盘上那份一个字节都不许动；
  - 另一条钉死"真跑起来还是要落在 exe 旁边"（`_DIR` 同级），免得有人为了
    隔离把它彻底挪走。

revert 验证（两种变体逐个试）：只重定向 config.json（18 红 + sentinel 被改写）、
tearDown 忘了还原（2 红）；逐字还原后全绿。

实测（把 6 个状态文件都做哈希哨兵，跑完整套）：
  panel_ui.json / config.json / history.jsonl / report.csv / gpu_monitor.log
  全部逐字节不变；tray_debug.log 变化（追加型调试日志，本来就该长）。

顺手把已经被冲掉的那份面板布局按代码默认值写回：geometry 1040x470、
topmost true、sort auto/desc、列宽 name 180 / pid 55 / gpu 55 / igpu 55 /
dgpu 55 / dmem 75 / smem 75 / pref 160。`_load_ui_state()` 认这份，与"没存过"
等价，用户下次开面板再自己拖。

顺手收了 §87 遗留的两条超长行（`OutputEncodingGuard` 里两个`with mock.
patch.object(...)` 被排成了一行，106 / 108 列），拆成反斜杠续行的两行 `with`，
纯排版、零行为变化 —— 项目自己守着“≤100 列”的约定，那是全项目唯一两处越线，收掉之后三个源文件 0 条越线
。本节只动测试文件，`gpu_monitor.py` / `gpu_tray.py` 一行未改，
所以 §88 那个构建（sha256[:16] = `04894ac6c79f84c4`）无需重打，
仍然有效。README: 用例数 439->441; "测试"那条补上"跑测试不会碰仓库里的任何状态文件"，
把替身列宽 / 替身 geometry 的现象和两条守卫写清楚。

## 88. 续七十九：托盘表格隔行浅色, 30 行扫读不串行(美观性)

§87 回头复核界面时剩下的唯一一件: 托盘那张 30 行的表整表同一种深底
(`_PANEL_BG` #1e1e2e), 热度色把某几行染红/染黄之后, 横向扫一行数字容易
串到隔壁行去。投影类工具(btop/Grafana/htop)无一例外都做隔行, 只有这里没有。

色值不是新造的: 复用面板"抬起面"已经在用的 `#26263a`(TButton /
Treeview.Heading 都是它), 和 §83"不引入新色值"的决定一致。实测定夺
(`s89_probe1.py` 全量评估、`s89_probe2.py` 只表格前景、`s89_probe3.py`
终值):

  #26263a 比表格底 _PANEL_BG 亮 50.1%(两底可辨, 不至于像脏屏)
  _FG 10.88:1(原 12.08) / hot 5.33:1(原 5.91) / warm 8.08:1 / cool 7.56:1
  四档全部仍过 WCAG AA 4.5:1

被否掉的候选: `#2e2e52` 把 hot 压到 4.65:1(贴线, 不留余量), `#33335c`
直接把 hot 打到 4.28:1 —— 后者已固化成用例的变体 D。

做法(`gpu_tray.py`):
- 常量区 `_HEAT_COLOR` 下方新增 `_ROW_ALT_BG = "#26263a"` /
  `_ROW_ALT_TAG = "rowalt"`, 注释里写清实测比值与"复用抬起面色"的理由;
- `_panel_build()` 注册 `tree.tag_configure(_ROW_ALT_TAG, background=...)`
  —— 只给背景。前景归热度 tag(`_HEAT_COLOR` 那几个, 只设 foreground),
  两个 tag 叠在同一行各管一头, 这是整块设计的关键: 底色可以随便换,
  热度色不会被洗掉;
- `_refresh_tree()` 把 `tags=(heat,)` 改成按插入下标取奇偶:
  偶数 `(heat,)`, 奇数 `(heat, "rowalt")`。隔着行走不跟数据内容走 ——
  排序或筛选一变整表重排条纹, 不会出现"同一个热度一会儿有条纹一会儿没有"。
  顺带把旧断言 `TrayPanelChrome.test_rows_carry_their_heat_colour_tag`
  的期望值从 `[("hot",), ("cool",)]` 改成 `[("hot",), ("cool", "rowalt")]`。

测试(6 条, `TrayRowZebraStriping`, 全绿): 奇数行才带隔行 tag(四行
hot/hot/warm/cool -> 只有下标 1/3 带); 每行第一个 tag 必须还是热度类
(隔行 tag 不许顶掉热度); 筛选后剩下的行从第一行重新隔行(留在第 3、5 位的
两行变成"无条纹 + 有条纹", 错位半行会当场红); 四种热度前景在隔行底上
全部 >= 4.5:1; 两底亮度差 >= 25%(条纹得看得出来); 源码守卫隔行 tag
只设 background、不许出现 foreground。

revert 验证(四种变体逐个试, 每次逐字还原):
  A 不给隔行 tag(退回 tags=(heat,))              4 红
  B 隔行 tag 排到热度 tag 前面(等于覆盖热度)     4 红
  C 隔行 tag 顺手设了 foreground(热度被洗成灰)   1 红
  D 底色换成更亮的 #33335c(hot 只剩 4.28:1)      1 红
  还原后 439 全绿。

全套 439 用例全绿(433+6)。longlines 无新增, scan_odd2/ast_unused 零输出,
py_compile OK。本节只动 `gpu_tray.py` 的渲染层, 没有动任何口径函数,
Web 面板与 CLI 行为零变化。

README: 用例数 433->439; "托盘那一侧的弱化色"那条后面接一条"托盘表格
隔行浅色", 把 50.1% 亮度差、四档实测比值和 #33335c 的否决理由写进去。

## 87. 续七十八：命令行输出的字节也统一 UTF-8, emoji 进程名不再打断 --list(准确性/健壮性)

§86 冒烟时发现: 源码态 `python -X utf8 gpu_monitor.py --list > f.txt` 落盘的
是 UTF-8, 而同一个 `GPUMigrate.exe --list > f.txt` 落盘的是 GBK。顺着查下去
(`s88_probe1.py`), 这不是"看着别扭", 是会崩的:

  把 stdout 按 cp936 打一遍进程名带 emoji 的表 ->
    UnicodeEncodeError: 'mbcs' codec can't encode characters ...

控制台这条路没事(Windows 按 PEP 528 走 WriteConsoleW); 一旦输出被重定向到
文件或别的程序捕捉, Python 就换成当前代码页。进程名里进了 emoji 或生僻字,
`--list` 会死在一张打到一半的表上 —— exe 还是 --noconsole, 用户连报错都看不见。
日志、config.json、history.jsonl、report.csv 早就是 UTF-8, 只有这一路是例外。

做法(`gpu_monitor.py`): 新增 `ensure_utf8_output()`, 对 stdout/stderr 做
`reconfigure(encoding="utf-8", errors="backslashreplace")`; `main()` 第一件事
就是调它。三点设计:
- 编码统一 UTF-8, 和所有落盘文件同一套, 管道进来的工具不用再猜;
- `errors="backslashreplace"` 是最后一道防线: 真有表达不了的字符(例如
  Windows 文件名里的孤立代理对)也只变成转义写法, 不会让整行消失;
- stdout 为 None(pythonw / 开机自启)或没有 reconfigure 的流直接跳过,
  reconfigure 本身抛错也吞掉 —— 这一步是锦上添花, 不许反过来带崩程序。

测试(6 条, `OutputEncodingGuard`, 全绿): 两个流都被切成
{encoding: utf-8, errors: backslashreplace}; None / 无 reconfigure /
reconfigure 抛错三种形状都不炸; 源码守卫 main() 必须先调它再打印;
端到端子进程(PYTHONIOENCODING=cp936 强制非 UTF-8 stdout 真打印 emoji)
退出码 0 且字节可按 UTF-8 解回原样。

revert 验证(三种变体逐个试): main() 不调用(1 红)、编码留成本地代码页(2 红)、
不给 errors 兜底(2 红); 逐字恢复后全绿。实机: 源码态与 exe 重定向输出
现在都是 UTF-8 字节。

全套 433 用例全绿(427+6)。longlines 无新增, scan_odd2/ast_unused 零输出,
py_compile OK。README: 用例数 427->433, 新增"命令行输出永远是 UTF-8"一条。

exe 已重打并自证(sha256[:16] = `bfe6cdb0c1348c7c`, 替换 §86 的
`f55e28f5cd8da0d9`): verify_exe3.py 两产物四模块 True, verify_imports 0 缺失,
`--list` 38 行正常, 看门狗恢复后托盘用新构建拉起, 面板 200。

## 109. 续一百：把"崩溃日志"读法纠错 + 滚动 + 补齐 .gitignore（准确性/可观测性/仓库卫生）

本轮起点是一次只读审核（2026-10-03）。现场证据推翻了 §105 的结论，也推翻了 §95 的立论。

### 证据（都是当场跑出来的，不是推断）

- `gpu_monitor_crash.log` 295KB / **165 段** `Windows fatal exception: access violation`，首帧 165/165 全是 `gpu_monitor.py:1072 nvmlDeviceGetTemperature`。
- 但 165 段的**线程 ID 集合完全相同**（`0x000041d0/8778/857c/48bc/154c`，去重后组合数=1）→ 全部出自同一个进程实例；`gpu_monitor.log` 的"启动监控"在 `00:06:38` 与 `18:09:07` 之间没有第三条，而最后一段 dump 落在 17:51 → 该实例跨 165 次 AV 连活 17.5 小时。
- 近 2 天事件日志 Id 1000 匹配 `GPUMigrate|python|nvml` **0 条**。
- 机制用一次性探针实测（写 %TEMP%，已删）：`ctypes.windll.kernel32.lstrlenW(c_void_p(0x1234))` → faulthandler **照样**写出 `Windows fatal exception`，而调用**照常返回 0、Python 一个异常都没收到**。faulthandler 挂的是向量化异常处理器，跑在被调方自己的 SEH 之前。
- 现场排除双实例并发：在跑的只有 `GPUMigrate.exe --ensure` 父子对；那个 `pythonw.exe` 属于 `fleetkit/runtime/tools/status_ui.py`，与本仓库无关。

### 改了什么

- `install_fault_dump()` 的 docstring、`FaultDumpGuard` 的 docstring、README"监控循环单轮异常…"一条的末尾：**"C 级段错误 Python 接不住 / 进程被内核直接干掉"改成"接不接得住取决于被调方内部有没有 SEH，不取决于 Python"**；并写明这份文件是现场记录不是死亡证明，判死活要对照 `gpu_monitor.log` 的"启动监控"行。
- 新增 `_CRASH_LOG_MAX = 1<<20`：旧文件超限先 `os.replace` 滚成 `.1`；`getsize`/`replace` 失败只当"这次不滚"，不影响装 handler。
- 新增 `_FAULT_NOTE`：**仅当文件是新建的**（首次或刚滚走）才在开头写一行说明。
- `.gitignore` 补 `gpu_monitor_crash.log` / `.log.1` / `gpu_monitor_error.log`（此前 295KB 以 `??` 挂在 status 里）。

### 本轮自己踩的坑（值得后来的人对照）

首版把说明写成"每个实例追加一行带 pid 的起始线"。跑完两套测试后 `md5sum -c` 报 `gpu_monitor_crash.log` 变了 —— 有的用例是拿 `subprocess` **真跑** `gpu_monitor.py` 的，那种进程的 `_DIR` 就是仓库目录，于是三轮子进程往线上文件插了 3 行，README:217 的"跑测试不会碰仓库里的任何状态文件"当场失效。
- 修法：说明只在文件新建时写；并新增 `test_an_existing_log_gets_not_a_single_extra_byte` 专门钉这条。
- 污染已按哈希复原：备份 `.tmptest/gpu_monitor_crash.log.pre_note_cleanup`（哈希 `42c3d649…`），只剔除那 3 行（903 字节），恢复后哈希 `feeee59261d7563867382fbbffa60c99` 与审核开始时记录的**完全相同**。

### 非空性（逐条在 `.tmptest/nv*` 里只回退一处）

- 删掉 `_FAULT_FH.write(...)` → 新建说明用例红。
- 删掉滚动块 → 滚动用例红（`FAILED (errors=1)`，读 `.1` 时 FileNotFoundError）。
- 把 `>` 改成 `>=` → 边界用例红（`AssertionError: True is not false`）。
- 撤回 `.gitignore` 三行 → `test_every_runtime_log_is_gitignored` 红（`['gpu_monitor_crash.log', 'gpu_monitor_error.log'] != []`）。
- `test_the_scan_finds_the_logs_we_know_about` **不是**修复证明，是防空转的元守卫（扫描 AST 形状变了它先响）。

### 验证

- `Ran 505 tests OK`；pytest `505 passed, 60 subtests passed`；连跑两轮后 `config.json` / `panel_ui.json` / `report.csv` / `gpu_monitor.log` / `gpu_monitor_crash.log` / `gpu_monitor_error.log` 六个哈希全 OK。
- `py_compile -W error::SyntaxWarning` OK；longlines 基线 16/5/5 无新增。
- 现网复核：面板 8787 正常，`独显温度: 42°C`，`nvml_gpu_temp` 连调 2000 次 0 失败。

### 没做 / 待决策

- **exe 未重打**：在跑的旧构建既不滚动也不写说明，本轮改动要生效需 build→sync→重启托盘，会打断当前实例，等用户点头。
- **NVML AV 的根因仍未定位**：无头 RTX 4060 Ti 进电源门控只是假设，本机 2000 次连调复现不了；真治法是子进程隔离采样（§105 已列过，代价是没改动到语义）。
- 选项 2 未做：`gpu_monitor.py:2519` 是 `dgpu_temp = t` 无条件赋值，一次 NVML 失败就把上一读数清成 None，与 commit `9e9f604`（把温度保住）方向相反。
- `expand_counter_paths:74` 的 `size.value + 1024` 无上界，今天 17:23 真触发过一次 `MemoryError`（已被 §92 的逐轮兜底接住，全日志仅 1 次）。

## 110. 续一百零一：NVML 读不到温度时保住上一读数，并让失败可见（可观测性/准确性）

§109"没做/待决策"里的第三条（原编号"选项 2"）本轮闭环。

### 缺陷

`cmd_monitor` 的温度块原来是：

```python
t = nvml_gpu_temp(0)
dgpu_temp = t          # ← 无条件赋值
```

`nvml_gpu_temp()` 失败一律返回 `None`（`:1078` 的 `except Exception`，也包括 `:1075` 那条返回非 0 的分支），于是**一次抖动就把上一好读数清成 None**：`panel_html:1683` 和 `gpu_tray.py:884` 都是 `temp_line = ... if temp else ""`，温度行整行消失，下个 10s 才回来。而全过程日志里一个字都没有。

这和 commit `9e9f604` 是**同一个症状的两个来源**：那次修的是"读数被快照整字典重建抹掉"，这次是"读数被失败读数抹掉"。`dgpu_temp` 的行内注释写的是"最近一次 NVML 读数"，而 `None` 不是一次读数 —— 代码和自己的注释对不上。

按 §109 的证据，`nvmlDeviceGetTemperature` 在本机真的会抖（165 段访问冲突现场全部首帧落在 `:1072`），所以这不是理论问题。

### 改法（`gpu_monitor.py`，TDD 先红后绿）

- 新增模块常量 `_TEMP_STALE_S = 60`（在 `_nvml_cache` 旁）。
- 新增两个循环状态：`temp_ok`（`None`=还没读到过 / `True`=读得到 / `False`=当前读不到，沿用同函数里 `battery_state` 的三态约定）、`temp_ok_ts`（最近一次读成功的时刻）。
- 温度块改成 `if / elif / elif` 三分支（现 `:2523-2541`）：
  1. **读成功** → 更新 `dgpu_temp` 和时间戳；若上一状态是 `False`，写一行"独显温度读取恢复: N°C"。
  2. **读失败但曾经读到过**（`temp_ok` 为真）→ 保住 `dgpu_temp`，翻成 `False`，写一行"独显温度读取失败: 暂留上一读数 N°C, 60s 内读回则不闪断"。**只在翻转时写**，不是每 10s 一行。
  3. **持续失败超过 `_TEMP_STALE_S`** → 收回：`dgpu_temp = None`、时间戳归零（归零使本分支只触发一次），写一行"独显温度持续读不到, 已收回暂留的 N°C(不再显示)"。

两个设计决定，别"顺手改掉"：

- **为什么要有 60s 上限**：无限暂留等于让面板永远显示一个不再更新的数字，那是页面在说谎 —— 同 `panel_html:1688` 那条"服务端送出去的 HTML 自己不能说着谎"。抖动（秒级）被吸收，真坏了（分钟级）退回不显示。
- **为什么"从没读到过"不报故障**：`temp_ok` 初值是 `None` 而不是 `False`。无独显/无驱动的机器每轮都读到 `None`，若按"失败就记一行"来写，每次启动都会在日志里留一条假告警。三态正是为了区分"坏了"和"本来就没有"。
- 高温提醒那段仍只认当轮真读数 `t`（`if t is not None and t >= ...`），不会拿暂留值去触发 `notify_cfg`。

### 测试（`tests/test_gpu_monitor.py` 新增 `DgpuTempOutage(LoopHarness)`，5 条）

用假时钟（每次 `time.time()` +3s → 每轮跨 15s，刚好越过温度块 `> 10` 的闸门、又远小于 60s 宽限）+ 按轮发牌的 `nvml_gpu_temp` 替身，用 `on_sample` 钩子回收每轮发布出来的 `dgpu_temp`：

- `test_a_single_failed_read_keeps_the_last_good_temperature`
- `test_the_outage_is_logged_once_not_every_ten_seconds`（并断言该行带上了被暂留的读数）
- `test_a_machine_that_never_had_nvml_is_not_an_outage`
- `test_a_long_outage_takes_the_frozen_reading_back`
- `test_the_reading_comes_back_and_says_so`

### 非空性（`.tmptest/nonvacuity_option3.py`，逐条只回退一处）

| 回退 | 结果 |
| --- | --- |
| 基线（修复在位） | `5 passed` |
| A 整段换回 `dgpu_temp = t` | `4 failed` —— 保住读数/失败一行/收回/恢复四条全红 |
| B `elif temp_ok:` 换成 `elif True:`（失败就记日志，不看历史） | `3 failed` —— 其中 `test_a_machine_that_never_had_nvml_is_not_an_outage` 红，证明这条守卫非空转 |
| C 删掉第三个 `elif`（永久暂留） | `1 failed` —— 只有 `test_a_long_outage_takes_the_frozen_reading_back` 红，证明 60s 上限确实被钉住 |

第三条基线是绿的（现状即期望），它的价值只在回退 B 下才显出来 —— 这类"防止新代码越界"的用例必须用错误实现去验，不能因为它当场绿就当成没用。

### 本轮踩的坑

非空性脚本第一版锚点写成 `dgpu_temp, temp_ok_ts = t, True, now_ts`，而真实那行是**三个**赋值目标 `dgpu_temp, temp_ok, temp_ok_ts = ...` → `next()` 抛 `StopIteration`。这次是响的（比 §109 那次静默假绿好），但同一个教训第二遍：**改文本的脚本必须断言文本真的变了**。已改成 `assert out != s` + 断言插入位置。

回退脚本用文本模式读、`newline=""` 写，理论上会把 CRLF 抹成 LF。事后核过：`gpu_monitor.py` 工作树是纯 LF（CRLF 0 处），且 8 个文件 `md5sum -c` 全 OK，没有造成损伤 —— 但在有 CRLF 的文件上这么干会静默改行尾，下次要按二进制读写。

### 验证

- `python -m pytest tests/test_gpu_monitor.py -q` → `511 passed, 60 subtests passed`（505 + 本轮 5 条温度用例 + §111 那条守卫）。
- `python tests/test_gpu_monitor.py` → `Ran 511 tests ... OK`。
- `py_compile` OK；长行基线仍是 `gpu_monitor.py 16 / gpu_tray.py 5 / tests 5`，本轮改动区间（`:1039-1046`、`:2407-2409`、`:2520-2541`）**0 条**新增 >79 字符行。
- 回退脚本跑完后 `md5sum -c` 全 OK，`gpu_monitor_crash.log` 仍是 `feeee59261d7563867382fbbffa60c99`。
- README：`- **测试**` 那条的用例数 504 → 511（原值就已过期），并新增一条讲这次修复。

### 没做 / 待决策

- **exe 仍未重打**：在跑的旧构建既没有 §109 的滚动/说明，也没有本轮的温度保住逻辑。生效需 build→sync→重启托盘，会打断当前实例，等用户点头。
- **全部改动仍未提交**（8 个路径，含 `config.json` 的暂存删除）。
- **NVML AV 的根因仍未定位**：本轮只是让它的后果可见且不再抹数据，没有治它。定向复现（无头独显闲置到断电）需要用户许可。
- `README.md:262` 那句"全套 488 条用例…四种跑法全绿"本来数字已过期。本想只记一笔"没重跑所以不改数字"，后来还是把四种跑法**真的**重跑了一遍再改 —— 结果倒序当场红，挖出 §111。
- `gpu_tray.py:36` 仍从 `gpu_monitor` 导入未使用的 `nvml_gpu_temp`（托盘读的是快照里的 `dgpu_temp`，不直接调 NVML）。
- `expand_counter_paths:74` 的无上界缓冲、`HANDOFF.md.bak105` 未跟踪备份，都还在。

## 111. 续一百零二：倒序跑整套会红 —— 一条用例把 `gpu_tray.enable_dpi_awareness` 换成替身不还原（测试隔离）

### 怎么发现的

§110 收尾要把 README 里"全套 488 条用例四种跑法全绿"的数字改成实数。改数字就得真跑，于是补了个倒序 runner（`.tmptest/rev_cwd.py`：把 discover 出来的用例拉平后 `reverse()` 再跑）—— 仓库里**没有**这种东西，那句声明当初是怎么验的已无从查起。

- 正序：`Ran 510 tests OK`。
- 倒序：`Ran 510 tests FAILED (failures=1)` → `TrayDpiAwareness.test_enable_dpi_awareness_survives_a_missing_api`，`AssertionError: None is not False`。

先排掉自己：把本轮新加的 `DgpuTempOutage` 整个类排除后倒序跑（505 条）**照样红** → 与 §110 无关，是工作树里既有的顺序依赖。想拿 HEAD 对照也不行：`git archive HEAD` 那棵树只有 **93** 条用例 —— 套件里绝大部分是前几轮**未提交**的产物，HEAD 根本没有这条用例。

### 根因

`enable_dpi_awareness()`（`gpu_tray.py:1730`）每条路径都返回 True/False，不可能返回 None。返回 None 的是**替身**：同类里 `test_run_tray_declares_dpi_before_building_any_window` 为了记录调用顺序写了

```python
gpu_tray.enable_dpi_awareness = lambda: order.append("dpi")   # append → None
```

而它只 `addCleanup` 了 `run_tray` 和 `TrayApp` 两个，**漏了 `enable_dpi_awareness`**。正序下按字母序 `test_enable…` 排在 `test_run…` 前面，所以从来没被咬到；倒序一跑，替身已经挂在模块上，`assertIs(..., False)` 就拿到 None。

这是 README"测试不再互相污染"那条列的**第四处**模块级全局泄漏，而且是那条声明写下**之后**才长出来的 —— 加用例时只跑正序，"顺序无关"这句从 488 条起就没人复验过。

### 修法

1. 泄漏那条补 `saved_dpi = gpu_tray.enable_dpi_awareness` 和对应的 `addCleanup`。
2. 新增守卫 `test_the_dpi_stub_does_not_outlive_the_case_that_installed_it`：把泄漏那条用例**单独**实例化跑一遍（`case.run()`，含 cleanup），再断言 `gpu_tray.enable_dpi_awareness.__module__ == "gpu_tray"`。

第 2 条的用意：断言的是"这个属性还属于 gpu_tray 自己"，不是"等于我刚才存下来的那个对象"。所以它**与执行顺序无关**（前面无论谁漏了替身都能逮到），也不限于这一个类 —— 换成 `mock.MagicMock`（`__module__` 是 `unittest.mock`）同样会红。指望"记得倒着再跑一遍"来守顺序无关是不现实的。

### 非空性

- 守卫用例在修之前就是红的：`AssertionError: 'test_gpu_monitor' != 'gpu_tray'`（拿到的正是测试模块里那个 lambda）。"修之前"这个状态就等于"删掉那条 addCleanup"，所以回退第 1 步会红是**实测**过的（守卫红 + 倒序红），不是推断。
- 修完之后：`TrayDpiAwareness` 3 条全绿，倒序 `Ran 511 tests OK`。
- 回退第 2 步（只删守卫、保留 addCleanup）→ 倒序仍绿但没人守了，所以守卫本身的价值只能靠"删守卫 + 把泄漏加回去"来证；这一条是**推理**，本轮没跑。

### 验证

- 四种跑法在 511 条上全部实测通过：pytest 正序 `511 passed, 60 subtests passed`、倒序 `Ran 511 OK`、`python tests/test_gpu_monitor.py` `Ran 511 OK`、`python -X utf8 -m unittest discover -s tests` `Ran 511 OK`。
- 跑完 `config.json` / `panel_ui.json` / `report.csv` / 三个日志 `md5sum -c` 全 OK（`tests/test_gpu_monitor.py` 自己变了是本轮在改它，不是污染）。
- README 那条 bullet 已把"三处"改成"四处"、补写第四处，数字 488 → 511。
- 长行基线 16/5/5 未变，新增区间 0 条 >79 字符。

### 留给后面的人（已决策，见 §112）

当时留了两条路：把倒序跑收进 `tests/` 当一条正式用例（约 4s，得把守卫自己排除掉否则自我递归），或者至少在 README 写清"加完用例要倒着跑一遍"并给出命令。本轮没替用户做这个选择。**下一轮用户选了第一条**（`把倒序测试跑进仓库`），已实现并验证，见 §112。`.tmptest/rev_cwd.py` 随之失去意义，已删；`.tmptest/nonvacuity_option3.py` 仍留着备查（§110 引用它），其余一次性脚本（`rev_skip.py` / `run_reversed.py` / HEAD 对照树 `head_tree/` / 各哈希清单）当时已删。

## 112. 续一百零三：把倒序跑收进仓库，让"顺序无关"自己守自己

### 为什么

§111 补的那条守卫只覆盖 `enable_dpi_awareness` 一个属性，第五处模块级泄漏照样会悄悄长出来；而 README 那句"四种跑法全绿"从 488 条起就没人复验过 —— 加用例只跑正序，声明就过期。把倒序跑本身变成一条用例之后，任何人跑 pytest / `python tests/test_gpu_monitor.py` / `unittest discover` 都会自动倒着再来一遍，声明不再是靠记性维持的。

### 改了什么

`tests/test_gpu_monitor.py` 新增 `class SuiteRunsInReverseOrder(unittest.TestCase)`（位置在 `RuntimeLogsAreIgnored` 之后、`def _fake_power_dll` 之前），一条用例 `test_the_whole_suite_passes_when_run_backwards`：

`unittest.TestLoader().discover(here)` → `_flatten()` 递归拉平嵌套 `TestSuite` → 按 `type(self).__name__` 排除自己 → `assertGreater(len(tests), 400)` → `tests.reverse()` → `unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(TestSuite(tests))`；红了由 `_blame()` 给出用例名 + 第一份 traceback 末 1200 字符（不给尾巴的话，失败信息只有"倒序跑红了"，没法查）。

### 四个设计决定（都是踩过或推演过的）

1. **必须把自己排除，否则无限递归**：discover 会把守卫自己也拉进来，守卫跑守卫。用 `type(self).__name__` 动态取而不是写死字符串，改了类名也不会漏排除。
2. **`assertGreater(len(tests), 400)` 是防空转的元守卫**：`start_dir` 写错、或文件名不再匹配 `test*.py` 时 discover 返回空，reverse 一个空列表照样"全绿"，守卫名存实亡。400 是低于当前 511 的宽裕下限，**不要改成 511** —— 将来删用例时它会假报成"倒序跑红了"。
3. **断言不能写 `assertTrue(outcome.wasSuccessful(), msg)`**：`assertTrue` 的 msg 是**提前求值**的，全绿时 `_blame()` 里 `pairs[0]` 直接 `IndexError` —— 守卫自己先崩，还报成倒序跑红。本轮第一版就是这么写的，实测炸过；改成 `if not outcome.wasSuccessful(): self.fail(...)`，并在代码里写明理由，免得后人当冗余"简化"回去。
4. **代价**：全套耗时翻倍，实测 +4s（单跑守卫 `1 passed, 511 deselected in 4.04s`）。用户认了这个代价。

### 本轮另一个坑：CRLF

`tests/test_gpu_monitor.py` 是**纯 CRLF**（8513 行 CRLF、0 行裸 LF），而 `gpu_monitor.py` 是纯 LF。补丁脚本若用文本模式读 + `newline=""` 写，会把这 8513 行的行尾**静默**全转成 LF —— diff 会显示整文件重写。改法：先量行尾、检测 `NL`、断言无混合行尾、按 `NL` split/join，改完断言 CRLF 数等于预期值。另外 `Edit` 工具对含 `\n` 转义的替换匹配不上（`\n` 与 `\\n` 两种写法都试过），最后用按行号打补丁的脚本绕过；脚本已删。

### 非空性（实测，不是推理）

把 §111 修的那条 `self.addCleanup(setattr, gpu_tray, "enable_dpi_awareness", saved_dpi)` 临时删掉 → 守卫红：`失败 2 / 错误 0`，报出 `TrayDpiAwareness.test_the_dpi_stub_does_not_outlive_the_case_that_installed_it` 与 `…test_enable_dpi_awareness_survives_a_missing_api`，traceback 尾巴 `'test_gpu_monitor' != 'gpu_tray'`。随后原样还原并复跑全绿。这条证明守卫抓的正是 §111 那个真实缺陷，不是空转。

**未验的一条**：元守卫 `assertGreater(len(tests), 400)` 自己没被回退验证（把 `start_dir` 指错才会红）—— 这一条是**推理**。

### 验证

- pytest `512 passed, 60 subtests passed in 7.68s`；`python tests/test_gpu_monitor.py` `Ran 512 tests in 6.634s / OK`；`python -X utf8 -m unittest discover -s tests` `Ran 512 tests in 6.681s / OK`；单跑守卫 `1 passed, 511 deselected in 4.04s`。
- 跑完 `config.json` / `panel_ui.json` / `report.csv` / 三个日志 `md5sum -c` 全 OK。
- `tests/test_gpu_monitor.py` 行尾复核 `CRLF: 8513 裸LF: 0`；长行基线 16/5/5 未变；`py_compile -W error::SyntaxWarning` OK。
- README 两处数字 511 → 512，"测试不再互相污染"那条补写倒序已进仓库（含 +4s 代价、自我排除、400 下限、`assertTrue` 提前求值四个要点）；§0 同步 512。

### 没做 / 待决策

- **exe 仍未重打**：在跑的旧构建既不含 §110 的温度保住，也不含本轮改动 —— 但本轮只动测试，不影响运行时；§110 那条要生效仍需 build→sync→重启托盘，会打断当前实例，等用户点头。
- NVML 访问冲突根因仍未定位（§109 遗留），子进程隔离采样仍是最靠谱的真治法。

## 113. 续一百零四：测试把现网排查日志写穿了 —— `_dbg` 的路径是调用时拼的（健壮性/可观测性/测试卫生）

### 怎么发现的

§111 那份待办清单要落地，第一件事是按老规矩把四种跑法真跑一遍拿基线。A（pytest 正序）`512 passed` 绿，跑完哈希 `tray_debug.log` **变了**；再静置 6 秒复哈希不再动 —— 写它的是测试，不是在跑的托盘。`tail` 那份日志看到的是 `File "C:\Python314\Lib\unittest\mock.py", line 1234 ... RuntimeError: 起不来` 和一串 `[22:46:32] panel_ui.json 顶层不是对象(list)`：全是替身现场，混在现网那份追加型日志里。

### 根因

`_dbg`（`gpu_tray.py:49`）和 `run_tray` 的 except（`gpu_tray.py:1771`）都是 `os.path.join(_DIR, ...)`，**调用时**才决定写到哪。全仓只有 `TrayLifecycleLog` 三条 + `TrayStartupFailure` 一条自己换了 `gpu_tray._DIR`，其余用例一碰这些分支就落在仓库根目录。README 里那句"`tray_debug.log` 是追加型调试日志，本来就该长"（§89 那轮的 `panel_ui.json` 豁免同款）把这事合理化掉了 —— **把测试噪音当成预期**，正是 §109 讲崩溃日志读法时踩过的那类错：现象解释得通，不等于现象是对的。

### 修法（生产代码一行未动）

1. 测试模块顶层（紧跟 `import gpu_tray`）把 `gpu_tray._DIR` 指到套件临时目录，`atexit` 收掉。只动 `gpu_tray._DIR`：`gm._DIR` 被用来读仓库里的 README / `.gitignore` / `config.json`，改了那一半守卫就全瞎。
2. `TrayDebugLogIsolation` 两条：一条查不变量（`gpu_tray._DIR == _SUITE_TMP`，顶层那句被删或谁换了没还原都会红），一条查行为（调 `_dbg` 写哨兵 → 仓库那份字节数不许动，且哨兵**必须**出现在临时目录，否则前一条是"压根没写"造成的假绿）。
3. README 那句豁免删掉，改成"跑完 `tray_debug.log` / `tray_error.log` / `gpu_monitor_error.log` 与跑前逐字节一致"。

### 两个坑，都只有 B 跑法才红

- **`mkdtemp` 撞双实例**：`python tests/test_gpu_monitor.py` 会以 `__main__` 跑本文件，而倒序守卫内部 `discover` 又导入出同名的**第二份模块实例** —— 两份各 `mkdtemp` 一个目录，"模块上的 `_DIR` 就是我这份额"当场判不了（B 跑法 `failures=2, errors=1`，A/C/D 全绿）。改成按 pid 定名 `gpumig-suite-<pid>`，两份指向同一个目录。
- **`_EXE_DIR` 不能再读现场**：`TrayPanelUiIsolation.test_the_state_file_still_lives_next_to_the_exe` 原来比 `dirname(_UI_FILE) == gpu_tray._DIR`；顶层一重定向，第二份实例读到的 `_DIR` 已是临时目录 → 该条红。改成照 `gpu_tray.py:39` 同一段算法（frozen 时取 `sys.executable` 目录）重算，实例无关。

这是 §101/§111 那条规矩的第三次应验：**"N 条全绿"只在四种跑法都真跑时才有意义**，而这次的差异不在用例顺序、在**模块实例数**。

### 顺手关掉 §110/§111 留的三条零碎

- `gpu_tray.py:36` 不再 from-import 没用过的 `nvml_gpu_temp`（托盘读快照里的 `dgpu_temp`）。确认过 tests 的 `STUBS`（:821）是 `gm` 侧替身清单，与这行无关。
- `expand_counter_paths` 的缓冲有了上界：`_EXPAND_CHARS_MAX = 1 << 20`（`gpu_monitor.py:48`），超限直接 `OSError` 拒绝分配且**不重试**。配两条用例（超限拒 / 卡在限值下仍能展开）。本机实测一次展开 507 条实例路径 / 54,943 字符，上限约 19 倍。
- `HANDOFF.md.bak105` 从仓库根移进 `.tmptest/`（gitignored），**没删**。
- §111 那条"倒序收进仓库"由 §112 落地；本轮把 §112 设计上排除在外的那一半 —— **含守卫自身的整套倒序** —— 补成 `.tmptest/rev_cwd.py`（用法 `python -X utf8 .tmptest/rev_cwd.py --reverse`，仍 gitignored）。

### 非空性（实测，不是推理）

- 删顶层那句 `gpu_tray._DIR = _SUITE_TMP` 的赋值 → 两条守卫同时红：不变量那条比出"仓库根目录 != 套件临时目录"，行为那条接着报 `tray_debug.log` 哈希当场变化 —— 守卫逮的是真写入路径，不是自说自话。
- 删 `gpu_monitor.py` 里那段上界检查 → 用例拿到的是 `MemoryError`（不是 `OSError`），`1 failed, 6 passed`。这正好把"没有上界会怎样"从推断变成实测。
- 两处变异都逐字节还原并复哈希一致（`gpu_monitor.py` 还原后 md5 与变异前相同）。

### 验证

- 516 条四种跑法全绿：A `516 passed, 60 subtests passed in 8.14s` / B `Ran 516 tests in 6.993s OK` / C `Ran 516 tests in 6.788s OK` / D 整套倒序（含守卫）`共 516 条, 顺序=倒序 / 结果: OK`。
- 跑完 7 个文件 `md5sum -c` 全 OK：`config.json` `panel_ui.json` `report.csv` `gpu_monitor.log` `tray_debug.log` `gpu_monitor_crash.log` `gpu_monitor_error.log`。`history.jsonl` 没进清单 —— 它会因在跑的监控进程心跳而长，与测试无关（§0 早就记过）。
- 长行基线 16/5/5 未变（本轮新增区间 0 条 >79 字符）；`py_compile` 三个文件 OK；README 两处数字 512 → 516。
- 提交 `f443408`：8 个路径、`+10881/−684`，`config.json` 从跟踪里摘掉（磁盘上还在，已 gitignore）。提交后 `git diff HEAD` 空，工作树只剩未跟踪的 `HANDOFF.md`。**未 push**：本地 `main` 与 `origin/main` 分叉（本地 5 条 / 远端 1 条 `d1883f6`），要不要合、怎么合等用户定。

### 留给后面的人（待决策）

- **exe 仍未重打**，而且这轮之后差距更大：源码里有 §109 的崩溃日志滚动、§110 的温度保住、本轮的 PDH 展开上界，线上跑的还是 9 月构建。生效要 build→sync→重启托盘，会打断当前实例。
- **NVML 访问冲突根因**仍未定位（§109 遗留），子进程隔离采样仍是最靠谱的真治法，需要用户允许把独显闲置到断电复现。
- `python tests/...` / `unittest discover` 收尾各报一次 `ResourceWarning: unclosed file 'gpu_monitor_crash.log' mode='a'` —— §101 第 4 处句柄泄漏的同族残留（现在是套件末尾还有句柄没收回），不影响判定，非阻塞。
- **同仓可能有并发会话**：本轮写到一半时 §112 是另一个会话追加进来的（我第一次 `grep "^## "` 只有 §111）。动 `HANDOFF.md` / `README.md` 前先 `grep` 一下最大节号、别按行数假设文件没动过。
- 仓库里仍然**没有**倒序 runner（守卫自己按设计排除自己，只能验"不含守卫"的那一半）；`.tmptest/rev_cwd.py` 是 gitignored 的。要不要把它也收进 `tests/`，本轮没替用户做决定。


## 114. 续一百零五: Web 汇总卡与表头加上悬停解释(可用性)

### 起因

汇总卡只有"标签 + 数字"两样, 表头只有六到八个字。光看这些分不清"核显占用 62.7%"是单进程满载还是三个进程相加, 也看不出"专用MB"是 MB 还是 MiB、是不是跨进程求和。托盘侧原本只有双击进详情、或看底部那行等宽提示, 信息密度不对等。

### 实现

1. `panel_summary` 由三元组 `(标签, 文本, 热度类)` 扩成四元组 `(标签, 文本, 热度类, 悬停解释)`。口径只有一份, Web 与托盘都从这里取, 与既有的"托盘和 Web 共用同一把尺"的约定一致。
   - 文案落在 `gpu_monitor.py:1186` 起的 `panel_summary`; 六张卡的解释覆盖: 占用取最忙进程(不相加)、专用/共享显存口径、GPU 进程含百分比为零的计数器、生效阈值已含电池覆盖。
   - 调用点同步改四元组解包: `gpu_monitor.py` 的 `panel_html` 卡片生成、`gpu_tray.py` 的 `_refresh_tree`、测试里 5 处推导。
2. Web 卡片: `<div class=card title='...'>`。**title 必须用单引号** —— `_html_escape` 只转 `& < >` 不转引号(`tests` 里"引号不在转义名单里"那条钉死的), 双引号属性会被外层 f-string 提前关掉。
3. Web 表头: 八个 `<th>` 各带 `title='...'`, 文案收在 `gpu_monitor.py` 的 `_HDR_TIPS` 字典 + `_tip()` 取属性串。既有断言 `tests:6252`(`<th>专用MB</th>...`)和 `PanelHtmlProcessNames::test_the_name_column_comes_first` 改成按 `_tip()` 拼, 仍逐字校验列序与列在。
4. 托盘卡: Tk Label 没有原生 tooltip, 加 `_bind_tip`(`gpu_tray.py:353` 附近) —— 500ms 延迟弹一个 `overrideredirect` 的 Toplevel, 320px 折行, 右缘越界时收回内侧, 按钮按下即收。UI 线程外不安全的部分都包了 try/except: 面板正在拆的时候 `winfo_*` 会炸。
5. 托盘表头: ttk.Treeview 列头不是独立 widget, `_bind_tree_tip` 挂 `<Motion>`, 用 `identify_column` 判定是否在本列表头上再弹。文案复用同一份 `_HDR_TIPS`(直接 import, 不走模块别名)。

### 测试

`PanelSummaryCards` 新增 3 条: 每张卡解释非空(防四元组里第四项空串安静退化成旧行为)、卡片解释进了 HTML title、八个表头解释非空且都出现在 HTML 里。全套 **519 条**, 只跑沙箱专用 runner(见 §1 环境节): `C:/Python314/python.exe -X utf8 .tmptest/run_unittest.py`。

### 本轮两个既有失败(与本次改动无关, 修它属于另一件事)

- `SuiteRunsInReverseOrder::test_the_whole_suite_passes_when_run_backwards`: 子进程 `discover` 只数到 1 条 —— 它 `sys.path` 里没有 `.tmptest/stub`, `import pystray` 当场失败。正常机器上装了 pystray, 这条是绿的。**别把它当回归修**。
- `TrayRowZebraStriping::test_the_alt_row_is_far_enough_from_the_table_background`: 隔行底色比表格底暗 12%(要求亮 25% 以上)。这是 `ef36cff` Fluent 配色(`_PALETTE["alt"]` vs `["card"]`)的手尾, 与悬停无关。

### 用户决策待办

现网 exe 还是旧构建(见 §0 现状快照)。要把悬停解释同步到现网, 仍需 build → sync → 重启托盘(会打断当前实例), 等用户点头。
## 115. 续一百零六：面板键盘导航 —— 方向键选行、回车详情、Del 永不迁移（可用性）

### 起因

参照 `htop` / `nvtop` / 任务管理器的表格交互。面板正文是一张表，但以前只有鼠标一条路：
双击进详情、右键设永不迁移。键盘用户能看不能用。实际一试还发现两个更硬的问题：

1. **选中项根本活不过一秒。** `_refresh_tree` 每秒一次 `tree.delete(*tree.get_children())` +
   重新 insert，选中态跟着一起清掉。就算 ttk 自己支持方向键，光标也会每秒被踢回起点。
2. **空选中态下按方向键什么都不会发生。** 这是真 Tk 的行为，用 `.tmptest/probe_tk.py`
   （真窗口 + `focus_force` + `event_generate`）实测确认：`selection()` 为 `()` 时，
   `<Down>` 既不选中也不报错。也就是说键盘用户必须先拿鼠标点一行，方向键才认。
   早期一版我用 `withdraw()` 躲屏幕，结果显示不出东西 —— 隐藏窗口拿不到焦点，事件
   压根不进绑定；得 `deiconify()` + `focus_force()` 才测得出来。

### 实现（`gpu_tray.py`）

- **选中项按 PID 跨刷新存活**：`_refresh_tree` 删行前记下选中行的 PID，渲染完用
  `_reselect_pid(tree, pid)` 还回去。按 PID 认行而不是按行号 —— 排序一变行号全变，
  PID 才是用户心里盯着的那个东西；顺带 `tree.see()` 把选中行滚进可视区。
- **方向键播种**：`_seed_selection()` 在选中集为空时落到第一行（`<Up>` 落最后一行），
  然后**必须 `return "break"`**。这条是实测逼出来的：不拦的话 ttk 自己的上下键绑定
  紧接着又会推一格，第一次按 Down 直接跳到第二行（探测脚本已复现这个"差一格"）。
  已有选中项时返回 `None` 不拦，移动仍然归 ttk 管。
- **回车进详情**：`_open_detail(tree, event=None)` —— 有 event 走 `identify_row(event.y)`
  （双击的老路），没 event 走 `_sel_item(tree)`（键盘的新路），两条路汇到同一个
  `_ui_post(self._detail_build, ...)`。`<Return>` / `<KP_Enter>` 绑表格，`<Return>` 另绑
  窗口（焦点万一不在表格上也还能用）。
- **Del 永不迁移**：复用右键那个 `_never_add`，同一份 `save_exclude_process`。
- **`/` 与 Ctrl-F 都聚焦筛选框**：`_focus_filter` 先看 `entry.focus_get() is entry`，
  已经在输入框里就不再抢焦点（否则会把用户打字的光标从半截进程名后面踢走）。
- **开面板即 `tree.focus_set()`**：焦点停在筛选框的话，第一次按方向键只挪输入光标，
  用户会直接得出"这面板没有键盘支持"的结论。窗口还没映射时可能抛 `TclError`，
  单独 try 住，不让它把面板搭到一半。
  `_FILTER_HINT` 改成"筛选进程名或 PID (/ 聚焦, Esc 清空)"，底部状态行补上
  "↑↓ 选择  回车详情  Del 永不迁移  / 筛选  右键同菜单"。

### 顺手挖出来的缺陷：占位行不是进程

`_refresh_tree` 没数据时会插一行 `("还没有采样到数据", "-", "-", ...)`，前端它和真行长
得一样。键盘把"回车进详情 / Del 永不迁移"接到选中项上以后，这条防线必须有：否则回车会
拿 "-" 去建详情窗。**右键那条路本来也会中招** —— `_never_add` 原来只取 `values[0]`，
右键点占位行就会把 `"-"` 写进 `config.json` 的 `exclude_processes`：一个谁都匹配不上的
名字，用户看不出异常，设置窗口里就躺着这一行。新增 `_row_pid(vals)` 统一判定
（整数/数字串都收，`None`/`""`/`"12a"`/`12.5`/`-` 一律 `None`），`_open_detail` 与
`_never_add` 都改走它。

### 测试

新增 `TrayPanelKeyboardNav` 12 条 + `TrayPanelRows` 4 条，全套 **535 条**，仍只有 §114
记的那 2 个沙箱既有失败（`SuiteRunsInReverseOrder` 子进程 discover 数到 1 条、
`TrayRowZebraStriping` 隔行亮度）。

`TrayPanelKeyboardNav` 用替身 tkinter/ttk 同步搭面板，从 `bind.call_args_list` 里把回调
掏出来直接调，断言"它拿当前选中项做了什么"。`TrayPanelRows` 里新加了 `_LiveTree` 替身
（`_Tree` 的 `insert` 只往 sink 记、不长子行，`item()` 只认一个固定 item，测不了"按 PID
找回那一行"）—— 按真语义实现 `delete`/`insert`/`item`/`selection_set`/`focus`/`see`。
这个替身自己先坑了一次：`delete(*items)` 分支没同步清 `_sel`，于是它抱住一个已经不存在
的 item，把"选中项跨刷新存活"这条测成了假的（断言拿到 `("i1",)` 而不是 `()`）。

### 非空性验证（6 处逐一回退）

| 回退 | 变红的测试 |
|---|---|
| 去掉 `_reselect_pid(tree, keep_pid)` | `test_the_selection_survives_the_next_refresh` |
| `_open_detail` 回到只认 `event` | 3 条 Enter 用例全红 |
| `_never_add` 去掉 `_row_pid` 防线 | `test_delete_on_the_placeholder_row_writes_nothing` |
| `_seed_selection` 不再真选中 | 2 条方向键播种用例全红 |
| 不调 `tree.focus_set()` | `test_the_panel_opens_with_the_keyboard_on_the_table` |
| 底部状态行退回旧文案 | `test_the_status_line_announces_the_keyboard_shortcuts` |

### 用户决策待办

现网 exe 仍是旧构建。要同步到现网仍需 build → sync → 重启托盘（会打断当前实例），等用户点头。


## 116. 续一百零七：列名单一来源 + 显存单位 MiB 纠错（准确性/可维护性）

### 起因

参照 `nvtop` 的列定义表。列名在三个界面各写一遍:

| 界面 | 列名写在哪 |
|---|---|
| 托盘 `_COL_LABELS` | `gpu_tray.py` 自己一份字典 |
| Web `<th>` | `gpu_monitor.py` 的 f-string 字面量 |
| CLI `--list` | `_fit_disp("专用MB", 8, True)` 字面量 |

改一个列名要同时记得三处。更要紧的是单位: 三处的显存列都写着 "专用MB"
/ "共享MB", 而数字是 `dedicated / (1024 * 1024)` —— 除出来的是 **MiB**, 不是 MB。
差 4.86%: 8 GiB 的独显会被读成 "8192 MB", 和规格书、GPU-Z、nvtop 都对不上。
任务管理器把 MiB 标成 MB 是它自己的老毛病, 不学。

### 实现

1. `gpu_monitor.py` 新增 **`COL_LABELS`**（八列一份表）, 放在 `_HDR_TIPS` 旁:
   CLI `cmd_list` 的表头、Web `panel_html` 的八个 `<th>`、托盘 `_COL_LABELS`
   全部改成从它取。托盘那一行现在是 `_COL_LABELS = COL_LABELS` —— 同一个对象,
   不是拷贝。相等不够, 相等的两份字典下一次照样漏改一处。
2. 显存两列改名: `专用MiB` / `共享MiB`。连带 `_HDR_TIPS` 的 dmem/smem
   文案(并补上"跨进程求和, 加起来可能超过物理显存")、`panel_summary` 两张卡
   (`0 MiB`)、托盘详情窗(`120 MiB / 40 MiB`)、托盘底部"已隐藏 N 个"、Web 的
   `0.0% / 0MiB`、`hot_reason` 的 `核显显存(专用+共享) N MiB`、启动日志里的阈值。
3. 设置窗 `显存阈值 MiB`（`SETTINGS_LABELS`）。
4. **配置键名不动**（`vram_threshold_mb` / `protect_vram_mb`）: 现网 `config.json`
   已经在用这两个名字, 改了等于把用户的阈值清零。只在 `DEFAULT_CONFIG` 旁边
   注明"口径是 MiB, 键名为兼容保留"。
5. `_disp_len` 的 docstring 举的例子跟着换成 `专用MiB`, 对应测试断言改成 7。

### 测试

新增 `ColumnLabelsSingleSource` 4 条, 全套 **539 条**, 仍只有 §114 记的那 2 个
沙箱既有失败。

- `test_the_tray_buys_column_names_from_the_same_place`: `assertIs` 而非
  `assertEqual`, 拷贝一份就红。
- `test_the_two_memory_columns_say_mib_not_mb`: 列名以 MiB 结尾, 且悬停文案里
  不出现"单位 MB"。
- `test_the_web_header_carries_the_table_and_the_right_unit`: 八个 `title=..>列名</th>`
  逐个从 `COL_LABELS` 拼出来对, 并断言 `>专用MB</th>` 不再出现。
- `test_the_cli_header_also_reads_the_table`: `inspect.getsource(gm.cmd_list)`,
  扫源码而不是真跑 —— `--list` 要真实 PDH 数据才出表, 这里只钉死"表头从
  COL_LABELS 取"这件事。（方法名是 `cmd_list` 不是 `_list_processes`, 第一版写错了。）

### 非空性验证（2 处, 只动临时副本）

| 回退 | 变红的测试 |
|---|---|
| 托盘改成 `dict(COL_LABELS)` | `test_the_tray_buys_column_names_from_the_same_place` |
| `COL_LABELS["dmem"]` 退回 "专用MB" | 2 条单位相关的测试 |

### 用户决策待办

现网 exe 仍是旧构建。要同步到现网仍需 build → sync → 重启托盘（会打断当前实例）,等用户点头。

## 117. 续一百零八：托盘/Web 底部常驻会话计数（可观测性）

### 起因

参照 `btop` / `bottom`：这类工具把"程序到底在干什么事"的累计数摆在台面上。本项目的
底部状态行只有"刷新: 1 秒"—— 它只承诺频率，不说明监控有没有真在干活。
"0 轮"和"3000 轮 0 命中"是两个完全不同的状态，而老界面上这两句话一字不差。

### 实现

1. `cmd_monitor` 里加三个会话级计数：`n_rounds` / `n_hits` / `n_migrations`，
   同时写进 `_LAST_SNAPSHOT` 与 `on_sample` 钩子快照（键名 `rounds` / `hits` /
   `migrations`）。口径：`hits` 数的是 `select_hot_candidates` 真的筛出来的候选数，
   被排除名单挡掉的不算漏报；`migrations` 只在"写注册表 + 回读确认"都通过后才 +1，
   写失败/回读不符/被杀软拦下都不计数。
2. **候选筛选从快照之后挪到了快照之前。** 原来 `select_hot_candidates` 排在
   `_LAST_SNAPSHOT` 与 `on_sample` 后面，`n_hits += len(hot)` 因此总是晚一轮 ——
   第一轮明明有两个进程超标，界面却写着"命中阈值 0 次"。它只读 cfg/util/mem 并往
   `done` 里补"No，已设在独显"的项，前移不影响后面的迁移动作。
3. **`migrations` 有意比迁移动作晚一个采样周期。** 确认弹窗可能阻塞几十秒
   （`MessageBoxTimeoutW` 的 120s 宽限），把整段迁移挪到快照前面等于让整个面板
   等用户点确认。累计数晚 5 秒无所谓，面板卡住不行。
4. 托盘底部新增独立一行 `count`（`_refresh_tree` / `_safe_refresh` 各加一个可选参数，
   老调用方不传就一句话都不说）。**不并进原有那行**：显卡名本来就长，再摞三个计数会
   超出窗口宽度被右沿切掉，等于什么都没说。
5. Web 面板同位置加 `<p id=counters class=hidden>`，并进 `PARTS` 局部刷新清单；
   顺手补 `#counters` 的浅色/深色两条 CSS（`#hidden` 那一档弱化灰）。
6. 快照没这三个键时用 `.get()` 兜 0：老快照/别的调用方自己攒的 dict 不该让刷新链
   断掉（`_safe_refresh` 一次异常就少刷新一帧）。

### 测试

新增 `SessionCounters` 5 条 + `TrayPanelRows` 3 条，全套 **547 条**，仍只有 §114 记的
那 2 个沙箱既有失败。

- `run_monitor(samples, cycles=...)` 的 `samples` 是"每轮一个 dict"，不是"一轮里的
  多个进程"——传 `[{100: 90.0}, {102: 80.0}]` 只会采到第一轮，测出来 hits=0。
  这个坑在本轮测试里踩了一次。
- 迁移计数那条断言"同一轮是 0、下一轮才是 1", 把上面第 3 条的有意滞后钉死。

### 非空性验证（4 处, 只动临时副本）

| 回退 | 变红的测试 |
|---|---|
| 去掉 `n_hits += len(hot)` | `test_hot_candidates_are_counted_as_hits` |
| 托盘那一行不渲染 | 3 条 `TrayPanelRows` 计数用例 |
| Web 那行不渲染 | 2 条 `SessionCounters` 的 Web 用例 |
| 候选筛选挪回快照之后（未单独验，见上表第 1 行同源） | — |

### 用户决策待办

现网 exe 仍是旧构建。要同步到现网仍需 build → sync → 重启托盘（会打断当前实例），等用户点头。

## 118. 续一百零九：Esc 清完筛选要把键盘还给表格 + 修一处自己写歪的注释（可用性）

### 起因

§115 把键盘导航补齐之后，还有一个出口是断的：`/` 聚焦筛选框 → 打字 → `Esc`。
老 `_on_escape` 只清空 `fvar` 就 `return "break"`，**焦点还留在输入框里**。用户接着按
方向键，动的是输入框里的文字光标，表格一动不动 —— 键盘导航在自己最常用的那个出口上
断掉，而且界面上看不出是为什么。筛选框是整个面板里唯一会抢走焦点的控件，它交还键盘的
路必须通。

### 实现

`gpu_tray.py` 的 `_on_escape`：清空筛选词之后补一次 `tree.focus_set()`（包 try，窗口
正在拆时 `focus_set` 会抛）。筛选词本来就空的那一支仍然直接关窗，不去动焦点 ——
都要关窗了再抢一次焦点没有意义。

顺手修掉 §116 引入的一处注释缩进：`# 数字本身就是 MiB...` 那行被写成了 8 空格缩进，
而它是 `for cid, text in _COL_LABELS.items():` 循环体内的注释（该是 16 空格）。同时把那
段注释改写清楚 —— §116 之后列名本身已经写明了 MiB，悬停要补的口径是"跨进程求和"，
不再是"是 MB 还是 MiB"。

### 测试

`TrayPanelKeyboardNav` 新增 2 条（全套 **549 条**，仍只有 §114 记的那 2 个沙箱既有失败）：

- `test_escape_hands_the_keyboard_back_to_the_table`：有筛选词时 `fvar.set("")`
  之后 `tree.focus_set()` 必须被调用。`reset_mock()` 是必需的 —— `_panel_build` 自己
  也会 `focus_set()` 一次，不清掉计数的话这条断言恒真。
- `test_escape_with_an_empty_filter_still_closes_the_window`：对照面。这条踩到了替身的
  形状问题：`on_close` 会把替身 `Toplevel.geometry()`（MagicMock）拿去 `json.dump`，
  于是把 `gpu_tray.write_json_dict` 临时换成 `lambda: True` 再断言 `panel_open` 为假。

### 非空性验证

把 `tree.focus_set()` 从 Esc 分支里删掉 → `test_escape_hands_the_keyboard_back_to_the_table`
变红（只在临时副本上验）。

### 用户决策待办

现网 exe 仍是旧构建。要同步到现网仍需 build → sync → 重启托盘（会打断当前实例），等用户点头。

## 119. 续一百一十：排序与详情窗共用同一把"单元格读数"的尺（可维护性/准确性）

### 起因

托盘有两处要把渲染好的单元格字符串还原成数字，写法却是两份：

| 用处 | 写法 |
|---|---|
| 详情窗 `_row_num` | `float(str(v).strip().rstrip("%"))` |
| 点表头排序 `_sort_rows.key` | `float(str(v).replace("%", "").replace(" MB", ""))` |

`rstrip` 只削尾部，`replace` 只认硬编码的 `" MB"`。§116 把列名从"专用MB"改成
"专用MiB"之后，这个坑变得更真实：万一哪一层把单位渲染进单元格，老排序读到的是 `0.0`
（整列沉底），而详情窗还能读出真值 —— 同一张表两个读数。

### 实现

`_row_num(vals, idx, bad=0.0)` 加一个 `bad` 参数，解析逻辑统一成
`float(text.strip().rstrip("%").replace("MiB", "").replace("MB", ""))`
（MiB 必须先于 MB 摘掉，否则 "MiB" 会被 "MB" 吃掉一个字符）。
`_sort_rows` 改调 `_row_num(row[1], idx, -1.0)`：解析失败仍然沉底而不是和 0 并列，
这条既有行为由 `test_unparsable_numbers_sink_to_the_bottom` 继续钉着，`bad` 参数就是
为了不把它弄丢。顺带删掉 `key()` 里已经用不到的 `v = row[1][idx]`。

### 测试

`TrayPanelRows` 新增 1 条（全套 **550 条**，仍只有 §114 记的那 2 个沙箱既有失败）：

`test_sorting_and_the_detail_window_read_a_cell_the_same_way` —— 行里直接写
`"512 MiB"` / `"1024 MiB"`（今天真实的 `_refresh_tree` 不渲染单位，这条测的是"任何一层
将来加了单位也不能两个读数"），断言按数值排而非按字符串排，坏的沉底；再用四个
单元格值直接对 `_row_num` 取读数。

### 非空性验证

把 `_sort_rows` 改回自己那份 `replace(" MB", "")` →
`test_sorting_and_the_detail_window_read_a_cell_the_same_way` 变红（只在临时副本上验）。

### 用户决策待办

现网 exe 仍是旧构建。要同步到现网仍需 build → sync → 重启托盘（会打断当前实例），等用户点头。

## 120. 续一百一十一：筛选口径跨界面对齐（托盘 / Web）

### 起因

两边的筛选历来是"同一个意思、两处实现"：Web 匹配 `td.n` / `td.pid` 两个单元格
（§WebPanelFilter 修好了整行 `textContent` 匹配的老毛病），托盘匹配 `name` 与 `pid`。
行为确实一致，但**"一致"这件事从来没有任何测试钉过** —— 托盘那 5 条用例各测各的，
Web 那 3 条扫源码字符串。谁先改一边，另一边静默漂移。

### 实现

`_matches_filter` 的 docstring 补上这段来历：Web 侧匹配哪两个单元格、为什么不能回到
整行匹配（输入 "12" 会把独显% 12.3 的行留下，还让人以为它真在 12%）。**行为一行没改** ——
本轮做的是把已经成立的事实写成证据。

### 测试

新增 `FilterParity` 4 条（全套 **554 条**，仍只有 §114 记的那 2 个沙箱既有失败）：

- `test_a_number_only_needle_catches_the_pid_not_the_percent`：`"200"` 命中 pid 200，
  而 `v2ray.exe`(pid 100， GPU% 列里有别的数)必须被挡掉 —— 这一条同时防"整行匹配"复活。
- `test_a_pid_needle_is_not_a_substring_of_a_longer_pid`：名字取得有点反直觉，实际讲的
是"PID 匹配是子串语义"（`"20"` 命中 pid 200），`indexOf` 两边一致，谁想改成整串相等得同时
  改两处。
- `test_blank_and_noise_needles_keep_the_whole_table`：`""` / `"   "` / `None` 都等于
  不筛选，不是筛掉一切。
- `test_the_web_side_reads_the_same_two_cells`：把 `td.n` / `td.pid` 那两个单元格
  也钉住，两边凑在同一个用例类里，改一侧时另一侧的断言就在眼皮底下。

### 用户决策待办

1. 现网 exe 仍是旧构建。要同步到现网需 build → sync → 重启托盘（会打断当前实例），等你点头。
2. 仓库根目录散落着 **300+ 个历史备份/补丁脚本**（`_gpu_monitor_before_*.py`、
   `_patch_*.py`、`_revert_*.py`、`_assemble*.py`、`_mut_backup*.py` 等），既未被 git 跟踪也
   未被 `.gitignore` 覆盖（`.gitignore` 只挡住了 `_patch*.py` 和 `archive/` 里的）。它们不
   影响测试与运行，但 `git status` 看不出量级、新人 `ls` 会以为项目有 300 个模块。清理需要
   动用户的文件、不可逆，等你确认后再做。

## 121. 续一百一十二：详情窗补上 Esc 出口（可用性）

### 起因

§115 之后，面板这一侧键盘能用了：`↑↓` 选行、`回车` 进详情、`Del` 永不迁移、
`/` 回筛选框、`Esc` 收起。但**详情窗自己只认一个"关闭"按钮** —— 键盘用户刚靠回车省下
一次摸鼠标，立刻又得为关窗把手挪回去。而 §118 已经承认"面板正文是表格、键盘用户只能用
鼠标看不能用"是个缺陷，详情窗是这条链路的最后一环，断了前面就白做。

### 实现

`gpu_tray.py` 详情窗在 `root.protocol("WM_DELETE_WINDOW", close)` 之后补一行
`root.bind("<Escape>", lambda _e=None: close())`。`close` 就是关闭按钮自己那个函数
（递减 `_ui_windows` + `destroy`），不新写一份 —— 两条出口同一个收尾，不会出现
"按钮关得掉、Esc 关不掉"这种只修一半。

### 测试

`TraySingleInterpreter` 新增 1 条（全套 **555 条**，仍只有 §114 记的那 2 个沙箱既有失败）。
断言的是 `window.bind.call_args_list` 里真的出现了 `<Escape>`，扫的是这条链路自己的
替身窗口，不依赖 `_handler` 那套按序列查找的助手（那个助手只服务面板）。

### 非空性验证

把新加的 `root.bind("<Escape>", ...)` 删掉 → 新用例变红（只在临时副本上验）。

### 用户决策待办

1. 现网 exe 仍是旧构建。要同步到现网需 build → sync → 重启托盘（会打断当前实例），等你点头。
2. 仓库根目录 300+ 个历史备份/补丁脚本的清理仍等你确认（见 §120）。

## 122. 续一百一十三：设置窗补上 Enter / Esc（可用性）

### 起因

三个窗口的键盘出口此前是：面板 `↑↓`/回车/`Del`/`/`/`Esc`、详情窗 `Esc`（§121）、
**设置窗什么都没有**。六个输入格 Tab 走完之后，键盘用户的手必须再去摸一次鼠标点
"保存"按钮 —— §115 的整个出发点就是"鼠标用户能用的一切键盘用户也该能用"，设置窗是
这条链上唯一还断着的一环。

### 实现

`gpu_tray.py` 设置窗两处：

1. `add_entry` 里给每个格子的 `Entry` 绑 `<Return>` -> `save()`。`save` 定义在
   后面，lambda 调用时才查名（与 `_panel_build` 的 `refresh` 同款）。`add_entry` 的
   返回值仍是 `var`，六个调用点一行没动。
2. 窗口绑 `<Escape>` -> `close()`，与面板 / 详情窗同一个键。

两个 `tk.Text`（游戏名单 / 排除名单）**故意不绑**：那两格里回车必须插入换行，
绑了保存就等于没法输入第二个进程名。

### 测试

新增 `SettingsKeyboardNav` 2 条（全套 **557 条**，仍只有 §114 记的那 2 个沙箱既有失败）。
夹具用替身 tkinter **同步**建设置窗（`object.__new__(TrayApp)` + `_ui_root = object()`，
只当"是否 None"的哨兵），不走 `TraySingleInterpreter` 那条 UI 线程链 —— 设置窗的
保存逻辑跟线程无关，同步建更直接。

踩到一个替身设的坑：六个格子共用**同一个** `Entry` 替身实例，所以断言要看
`fake_tk.Entry.return_value.bind`，不是 `fake_tk.Entry.bind`（后者是工厂自己的子 mock，
永远是空的）。同一个坑在详情窗那条用例上以 `self.windows[-1]` 的形式躲过去了。

### 非空性验证

同时删掉 Enter 绑定与 Escape 绑定 → 2 条用例全红（只在临时副本上验；注意
`root.bind("<Escape>", ...)` 在文件里出现两次——设置窗在前、详情窗在后，回退时要按
位置取第一次）。

### 用户决策待办

1. 现网 exe 仍是旧构建。要同步到现网需 build → sync → 重启托盘（会打断当前实例），等你点头。
2. 仓库根目录 300+ 个历史备份/补丁脚本的清理仍等你确认（见 §120）。

## 123. 续一百一十四：排序也能用键盘使（s / r，参照 nvtop）（可用性）

### 起因

§115–§122 把三个窗口的键盘出口补齐了，但**排序本身仍然只能靠鼠标点表头**。这张表默认按
"活动量"排序，想改成"按专用显存看谁吃得多"非得摸鼠标 —— 而 §115 的出发点就是"鼠标用户
能用的一切键盘用户也该能用"。nvtop 用 `s` / `r` 两个键解决这件事（`s` 换列、`r` 反转），
窗口型工具里这是通行做法。

### 实现

`gpu_tray.py` 里加 `_SORT_CYCLE`（九档，`auto` 打头也收尾，保证循环闭合、有路可回）和两个
处理器，表格级与窗口级都绑：

- `s` → `_toggle_sort(下一列)`，随后刷新排序按钮文案并立即 `refresh()`。走既有的
  `_toggle_sort` 而不是另写一套：换列默认降序、再按同一列才翻转，这三个界面早就统一了。
- `r` → 翻转 `sort_state["desc"]`，不换列。
- `_not_typing()` 守卫：`entry.focus_get() is entry` 时一律不动作。这是把两个键绑到
  窗口级必须付的代价 —— 不挡这一下，用户在筛选框里每打一个 `s` 表就重排一次。
- 快捷键的交代挂在 `_bind_tip(sort_btn, ...)` 上，**不并进底部状态行**：那行已经有
  显卡名 + 刷新节奏 + 五个快捷键，再塞两个会被窗口右沿切掉，等于没说。用户找排序入口时
  鼠标本来就会掠过那个按钮。

### 测试

`TrayPanelKeyboardNav` 新增 4 条（全套 **558 条**，仍只有 §114 记的那 2 个沙箱既有失败）：

- `test_the_s_key_cycles_the_sort_column`：`auto` → `name` → `pid`，并且**连按 10 次要
  能回到 `auto`** —— 循环必须闭合，否则键盘用户把排序改乱之后没有回头路（鼠标那边有
  "复原到自动"按钮，键盘这边 `s` 自己是唯一的入口）。
- `test_a_single_reverse_key_flips_the_direction`：`r` 与再点一次同一列表头等价。
- `test_typing_in_the_filter_is_not_a_sort_command`：把 `_toggle_sort` 换成一旦被调用就
  抛 `AssertionError` 的替身，再把 `entry.focus_get()` 指向 entry 自己，两个键按下去
  什么都不该发生。这条是把"守卫真的拦住了"钉死，不是钉"通常情况没问题"。
- `test_the_sort_button_explains_the_keyboard_shortcuts`：把 `gpu_tray._bind_tip` 换成
  记录替身再建面板，断言某条悬停文案里出现"按 s"。

### 非空性验证

四个绑定 + 那条悬停整段删掉 → 4 条全红；**只删表格级那两个绑定 → 仍然全绿**。后者是如实
记录：窗口级绑定已经覆盖了表格有焦点时的情况，表格级那两行目前是冗余的。留着它们不是为了
覆盖率，是因为 Tk 的 bindtags 顺序（控件 → 类 → toplevel → all）意味着将来 `Treeview`
  若引入自己的 `s`/`r` 类绑定，先挂控件级才能拦得住。

### 用户决策待办

1. 现网 exe 仍是旧构建。要同步到现网需 build → sync → 重启托盘（会打断当前实例），等你点头。
2. 仓库根目录 300+ 个历史备份/补丁脚本的清理仍等你确认（见 §120）。

## 124. 续一百一十五：给零覆盖的 AI 显存保护补上测试（可观测性/测试覆盖）

### 起因

扫了一遍 `gpu_monitor.py` 的模块级函数与常量，对着测试文件查引用，找出 62 个测试里
从未被直接提到的名字。逐个看过去，大部分是 `ctypes` 常量（`MB_YESNO` / `TH32CS_SNAPPROCESS`
这类，确实只需在真实 Windows 路径上验证）或仅作转出的别名。**真正值得动手的是
`is_ai_protected`** —— 一个零直接覆盖、却决定"要不要跳过自动重启"的纯函数。

它守的是最不该出事的地方：返回 False 会让每个被迁移的模型进程当场被杀掉重拉
（几十 GB 权重白加载一遍），恒返回 True 则等于把 `auto_restart` 整个关掉。两个方向
都是静默的，而"它是否正确"此前只活在 docstring 里。这和 §HostileNamesInPanel 给
`_html_escape` 补覆盖是同一种情况：守卫函数没人直接调，就等于没有守卫。

### 实现

**没改一行生产代码。** 本轮只补测试：把已经成立的行为写成证据。

### 测试

新增 `AiProtectionRules` 7 条（全套 **565 条**，仍只有 §114 记的那 2 个沙箱既有失败）：

- **内置名单逐条问**：遍历 `DEFAULT_AI_PROCESSES` 而不是自己拼一个 `ollama.exe` ——
  名单自己被误删一条时这条就红，拼死名字只能证明"我记得的那一条还在"。
- 大小写不敏感（`LM Studio.exe` 的真实大小写取决于启动方式，精确匹配会一半时候漏掉）。
- 用户 `ai_processes` 名单叠加在内置名单之上，同样大小写不敏感。
- 显存兜底：`>=` 阈值（正好踩线算保护）与差一点不算保护都查 —— 少了后者，
  "全都要保护"也能让前面几条全绿。
- `protect_vram_mb=0` 是**显式关闭**：这条看着 `if limit and ...` 里那个 `and`。
  写成 `>=` 单独判断的话 0 会让每个进程都命中。
- 空名字 / pid 不在表 / 老配置没有 `ai_processes` 键：都不许抛。采样间隙里 pid 退出
  是常态，这里一抛就顺着迁移分支回到监控循环。

### 非空性验证（4 处逐一回退，只动临时副本）

| 回退 | 变红 |
|---|---|
| 删掉内置名单那条 `if` | 3 条（名单逐条、大小写、老配置） |
| 用户名单改成精确匹配 | 1 条 |
| 显存 `>=` 改成 `>` | 1 条（兜底那条） |
| 去掉 `and limit` 短路 | 1 条（阈值为 0 那条） |

### 用户决策待办

1. 现网 exe 仍是旧构建。要同步到现网需 build → sync → 重启托盘（会打断当前实例），等你点头。
2. 仓库根目录 300+ 个历史备份/补丁脚本的清理仍等你确认（见 §120）。

## 125. 续一百一十六：ai_processes 漏登记导致 AI 保护静默失效（严重缺陷修复）

### 起因

§124 给 `is_ai_protected` 补覆盖时, 为了把 `is_ai_protected` 的输入面测全, 顺手用
`merged_config` 走了几种坏配置, 于是撞见一个真 bug:**`ai_processes` 不在 `_LIST_KEYS` 里**。

对账很清楚 —— `DEFAULT_CONFIG` 里 7 个列表型键, `_LIST_KEYS` 只登记了 6 个:

| 键 | 登记? |
|---|---|
| `auto_restart_processes` / `ignore_processes` / `exclude_processes` / `exclude_full_paths` / `force_igpu_names` / `game_processes` | 是 |
| **`ai_processes`** | **否** |

两种坏写法各有各的死法:

- `"ai_processes": "ollama.exe"`（字符串）→ 原样留在配置里, `is_ai_protected` 的
  `{x.lower() for x in ...}` 按**字符**展开成 `{o,l,a,m,.,e,x}`, 名单一条都匹配不上 ——
  用户自己起的推理服务失去 AI 保护, 自动重启照旧执行, 模型当场被杀。
- `"ai_processes": null` → `None` 一路传进守卫函数, 集合推导式当场 `TypeError`。
  调用点在迁移分支深处, 监控循环的兜底只会让人看到**每 5 秒一条 traceback**, 
  而 migration 从此不再发生。

这正是 §2 修过的那一类问题（六个数字键收进 `_NUMBER_RULES`），只是名单这一侧
漏了一个键。

### 实现（`gpu_monitor.py`，两处）

1. `ai_processes` 加进 `_LIST_KEYS`, 并在表旁写明"新加名单型配置项时必须往这里登记，
   别只改 DEFAULT_CONFIG" —— 这个坑的成因就是那两处改动不在同一处。
2. `is_ai_protected` 里的 `cfg.get("ai_processes", [])` 改成 `cfg.get("ai_processes") or []`:
   前者在键存在但值为 `null` 时返回 `None`，默认值根本用不上。归一应该只有
   `merged_config` 一个入口，但守卫函数不该把命押在调用方身上。

### 测试

新增 `AiProcessesConfigCoercion` 5 条（全套 **570 条**，仍只有 §114 记的那 2 个沙箱既有失败）。
其中 `test_a_bare_string_becomes_a_one_item_list` 断言的不只是"变成了列表"，而是
**`is_ai_protected` 真的认得出这个名字** —— 归一对了但判据没接上，一样是静默失效。
`test_every_list_key_in_the_default_config_is_registered` 是对账而不是点名：把
`DEFAULT_CONFIG` 里所有 `list` 型值与 `_LIST_KEYS` 求差集，将来谁再加一个名单型
配置项忘了登记，这条当场红。

### 非空性验证（2 处, 只动临时副本）

| 回退 | 变红 |
|---|---|
| `ai_processes` 从 `_LIST_KEYS` 摘掉 | 4 条 |
| 守卫改回 `cfg.get("ai_processes", [])` | `test_a_null_ai_list_cannot_crash_the_guard_itself` |

### 用户决策待办

1. 现网 exe 仍是旧构建（而且这一条是真缺陷，比前面几轮的美化更值得先上线）。
   要同步需 build → sync → 重启托盘（会打断当前实例），等你点头。
2. 仓库根目录 300+ 个历史备份/补丁脚本的清理仍等你确认（见 §120）。

## 126. 续一百一十七：protect_vram_mb 也漏了归一（同一类缺陷的第二个实例）

### 起因

§125 修掉 `ai_processes` 之后，顺手把 `DEFAULT_CONFIG` 的每个键和三张归一表
（`_NUMBER_RULES` / `_BOOL_KEYS` / `_LIST_KEYS`）对了账，发现**同一个类别还有第二个**：

| 类别 | 键数 | 已登记 | 漏的 |
|---|---|---|---|
| 数字 `_NUMBER_RULES` | 9 | 8 | **`protect_vram_mb`** |
| 开关 `_BOOL_KEYS` | 12 | 12 | 无 |
| 名单 `_LIST_KEYS` | 7 | 7 | 无（§125 已修） |

`vram_threshold_mb` 在表里而它的同胞 `protect_vram_mb` 不在 —— 加前一个的人没想到
后一个。`protect_vram_mb` 的失效方式比 `ai_processes` 更难看：

- `"2048"` / `"abc"` / `[1,2]` → 原样留下，`is_ai_protected` 里的
  `dedicated >= limit * 1024 * 1024` 当场 `TypeError`，和 §125 一样每轮刷 traceback、
  migration 停摆。
- **`-5` → `if limit` 为真而阈值恒负，于是每个进程都被判成 AI 保护** —— 
  `auto_restart` 被静默关掉，而界面上什么都看不出来。这一条比 TypeError 更坏：
  异常至少会留痕。
- `None` → `if limit` 为假 → 保护静默关闭。

### 实现（`gpu_monitor.py`，两处）

1. `"protect_vram_mb": (0, None, True)` 进 `_NUMBER_RULES`。下限取 0 是因为 0 是合法的
  "关闭"，不能像别的阈值一样从 1 起。
2. `is_ai_protected` 里 `if limit and ...` 改成 `if isinstance(limit, (int, float)) and limit`，
   和 §125 给 `ai_processes` 加的 `or []` 同一个理由：归一该只有一个入口，但守卫函数
   不该把命押在调用方身上。

### 测试

新增 `EveryConfigurableKeyIsCoerced` 7 条（全套 **577 条**，仍只有 §114 记的那 2 个
沙箱既有失败）。三条对账用例把三张表一起钉住：**新加配置键忘了登记就当场红**，这是
这轮真正的产出 —— 前两个缺陷都是这么漏出来的，靠点名永远点不全。

`test_a_non_numeric_protect_vram_mb_falls_back_and_does_not_crash` 用的是 100MiB 而
不是 3000MiB 的进程：退回的默认值是 2048MiB，拿 3000MiB 的进程去测的话，就算真的
退回了默认也照样返回"保护"，断言什么都证明不了。自己第一版就写错了这个。

`test_the_guard_survives_a_raw_unnormalized_config` 故意绕过 `merged_config` 直接递
脏 dict，专为盯着新加的 `isinstance` 那道关卡 —— 没有这条它就是死代码，而死代码没人
会为它负责。

### 非空性验证（3 处, 只动临时副本）

| 回退 | 变红 |
|---|---|
| `protect_vram_mb` 从 `_NUMBER_RULES` 摘掉 | 4 条 |
| 守卫去掉 `isinstance` 检查 | `test_the_guard_survives_a_raw_unnormalized_config` |
| 下限从 0 改成 1 | 负数夹取那条 |

### 用户决策待办

1. 现网 exe 仍是旧构建。**这一轮和上一轮都是真缺陷**，建议优先上线：
   build → sync → 重启托盘（会打断当前实例），等你点头。
2. 仓库根目录 300+ 个历史备份/补丁脚本的清理仍等你确认（见 §120）。

## 127. 续一百一十八：WCAG 亮度的三份抄写收敛成一把尺（可维护性/可观测性）

### 起因

继续扫"同一个判据多个实现"，发现 WCAG 相对亮度这个公式被抄了**三遍**：

| 位置 | 名字 | 入参 |
|---|---|---|
| `gpu_monitor.py` | `_lum` / `_ratio` | RGB 三元组 |
| `gpu_tray.py` | `_luminance` / `_contrast_ratio` | RGB 三元组 |
| `tests/test_gpu_monitor.py` | `_wcag_luminance` / `_wcag_contrast` | `#rrggbb` 字符串 |

拿十个真实色值逐一比对，三份今天数值完全相同（`places=12`）。但那是**巧合式的正确**。

真正要紧的是第三份：可读性守卫（`WebPanelLegibility` / `TrayPaletteLegibility`）拿它给
生产代码发合格证。**尺子和被测对象分家的那一刻，合格证就不再证明任何事** —— 谁改了
生产的公式，守卫照旧全绿。这和 §NoiseRowFilter 里 `row_is_noise` 那条 `assertIs`
是同一条规矩：判据必须只有一个出处，不然改一处漏一处。

### 实现

- `gpu_tray.py`：删掉自己那份 `_luminance` / `_contrast_ratio`，改成
  `_luminance = _lum` / `_contrast_ratio = _ratio` 两个别名，并把它加进已有的
  `from gpu_monitor import (...)` 段。**托盘侧 4 个调用点一行没改** ——
  这正是别名而不是全局替换的原因：改签名会让调用点跟着动，而这次要的就是
  "语义不变、出处唯一"。
- 测试：`_wcag_luminance` / `_wcag_contrast` 改成调 `gm._lum` / `gm._ratio`，
  新加一个 `_hex6_rgb` 做十六进制→RGB 的转换（`_hex6` 负责三位缩写展开，
  原来只被间接用到，现在也一并钉住）。

### 测试

新增 `OneContrastRuler` 4 条（全套 **581 条**，仍只有 §114 记的那 2 个沙箱既有失败）：

- `test_the_tray_uses_the_monitors_ruler`：`assertIs`，和 `row_is_noise` 那条同款。
- `test_the_legibility_guard_uses_the_monitors_ruler`：**钉函数身份而不是数值** ——
  数值一致是今天的结果，会变；身份一致是结构，不会因为有人改了公式而悄悄失效。
  扫 `inspect.getsource` 看它还在不在调 `gm._lum` / `gm._ratio`。
- `test_the_rulers_agree_on_the_palette_that_is_actually_shipped`：色值从
  `_PANEL_STYLE` 里正则抓，不是编的 —— 抓出来要求 >5 个，太少就说明样式表变了，
  这条用例自己去适配。
- `test_a_shorthand_hex_is_expanded_before_measuring`：补 `_hex6` 的直接覆盖。

### 非空性验证（2 处, 只动临时副本）

| 回退 | 变红 |
|---|---|
| 托盘改回自己那份公式 | `test_the_tray_uses_the_monitors_ruler` |
| 测试改回自己那份公式 | 2 条（含 `#abc` 缩写的直接覆盖那条） |

### 用户决策待办

1. 现网 exe 仍是旧构建。**§125/§126 是真缺陷，建议优先上线**；
   本节纯重构，跟着一起走即可。要同步需 build → sync → 重启托盘。
2. 仓库根目录 300+ 个历史备份/补丁脚本的清理仍等你确认（见 §120）。

## 128. 续一百一十九：字节转 MiB 的换算收敛成一个常量（可维护性）

### 起因

接着 §127 清理同一个判据多个实现。这一处更常见: 同一个常量被手写多遍。
gpu_monitor.py 有十处显存换算, gpu_tray.py 两处, 而且两种写法混用 ——
1024 * 1024 和 1048576。

看着只是风格, 但 §116 才刚刚澄清过: 显存的口径是 MiB(1048576) 而不是 MB(10^6),
差 4.86%。谁哪天把其中一处顺手改成 1000 * 1000, 那一列就和其它十一个界面对不上, 而
改的人完全看不出还有别处存在 —— 因为另外十处长得跟它不一样。

### 实现

- gpu_monitor.py 新增 MIB = 1024 * 1024, 放在 _WEB_STALE_MIN_S 那一批常量旁边,
  注释写清楚两种写法混用的后果。常量名叫 MIB 而不是 MB, 就是把答案写进名字里。
- 十一个换算点全部改成 / MIB。gpu_tray.py 那两处通过已有的 from gpu_monitor
  import (...) 段引入, 和 COL_LABELS / _lum / _ratio 一样走别名导入。
- 结果零变化: panel_summary 仍出 3 MiB / 2 MiB, hidden_noise_count 不变, 全套
  测试一条没改就过。这正是该有的样子 —— 纯收敛, 不动行为。

### 测试

新增 MibConversionHasOneSource 3 条(全套 584 条, 仍只有 §114 记的那 2 个沙箱既有失败):

- test_the_magic_number_is_written_exactly_once: 扫 AST 而不是逐个点名。自己写了
  一个 _fold 把常量 * 常量折成整数 —— ast.literal_eval 不认乘除(它只放行加减,
  免得 "a" * 10**9 把内存吃穿), 这是实现中踩到的坑。预期是 gpu_monitor.py
  恰好 1 处(MIB 自己的定义)、gpu_tray.py 0 处。注释和 docstring 里提 1048576 是
  讲道理, 所以扫 AST 常量而不是文本。
- test_the_constant_is_the_one_both_modules_share: assertIs, 同 row_is_noise 那款。
- test_the_palette_math_still_uses_the_constant: 拿真实快照过一遍 panel_summary,
  证明换成常量没改变读数。

### 非空性验证

往 gpu_monitor.py 的 panel_summary 里插一行 _leak = 1024 * 1024 之后,
test_the_magic_number_is_written_exactly_once 当场红。这条是防再发的核心: 新加一处
手写换算就被抓住, 不依赖谁记得住要去改十一处。

### 用户决策待办

1. 现网 exe 仍是旧构建。§125/§126 是真缺陷, 建议优先上线; 本节纯重构, 跟着一起
   走即可。要同步需 build → sync → 重启托盘。
2. 仓库根目录 300+ 个历史备份/补丁脚本的清理仍等你确认(见 §120)。

## 129. 续一百二十：热度三色两界面一致性有据可查（可观测性）

### 起因

沿同一个判据多个实现的线索继续扫, 发现热度三色分布在一对独立维护的真相里:

| 界面 | 色值存在哪 |
|---|---|
| 托盘 | gpu_tray._FLUENT_LIGHT / _FLUENT_DARK 两张 palette, 再派生 _HEAT_COLOR |
| Web | _PANEL_STYLE 里写死的 .hot/.warm/.cool 六条 CSS(浅色三条 + 深色三条) |

六个值逐个比对过, 今天完全一致。但那是靠人记住同步的结果, 没有任何测试看着。

这不是纯风格问题: 热度色是用户判断这台机器现在忙不忙的主要线索, 而托盘图标和 Web
面板是同一个数的两个视图。哪天只改了一边, 同一个 62.7% 会在托盘里是橙的、在浏览器里
是红的 —— 而两边都没有错到需要报错的程度, 于是谁也不改。

热度阈值(30/60)反倒一直是单一来源(heat_class 一个函数), 没有这个问题。

### 实现

没改一行生产代码, 也没把六条 CSS 重构成从 palette 生成 —— 后者要把 _PANEL_STYLE 从
静态字符串变成模板, 动静太大而收益有限。本轮做的是把这个已经成立的事实写成证据。

### 测试

新增 HeatPaletteParity 2 条(全套 586 条, 仍只有 §114 记的那 2 个沙箱既有失败):

- test_every_heat_colour_matches_the_tray_palette: 把 _PANEL_STYLE 在 @media
  (prefers-color-scheme:dark) 处一拆两半, 两套主题各查 hot/warm/cool 三个色值, 和
  托盘对应 palette 逐个对。拆 @media 的写法沿用 WebPanelLegibility 那条已有的做法。
- test_the_tray_derives_them_from_the_palette_not_literals: 反向钉托盘那一侧 ——
  _HEAT_COLOR 必须是从 _PALETTE 派生的, 不许哪里写死三个色值让上面的对账落空。

### 非空性验证

只把 Web 的热度红 #b02a1a 改成 #c50f1f(其它一律不动) ->
test_every_heat_colour_matches_the_tray_palette 当场红。这正是这条用例要抓的情形:
单边改动, 没人会发现。

### 用户决策待办

1. 现网 exe 仍是旧构建。§125/§126 是真缺陷, 建议优先上线; 本节纯补证据, 跟着走即可。
   要同步需 build -> sync -> 重启托盘(会打断当前实例), 等你点头。
2. 仓库根目录 300+ 个历史备份/补丁脚本的清理仍等你确认(见 §120)。

## 130. 续一百二十一：趋势图几何常量的跨处一致性有据可查（可观测性）

### 起因

沿同一个判据多个实现的线索扫到最后一块: 趋势图的几何常量。这几个数散在四个地方,
而源码注释里已经写明它们必须相等 —— 只是从来没有任何东西检查:

| 常量 | 出现的地方 |
|---|---|
| 46 | gpu_monitor._SPARK_H、CSS 的 .sparkwrap 与 .spark 两条 height、gpu_tray 的 tk.Canvas(height=46) |
| 180 | _SPARK_MAX_POINTS、cmd_monitor 里的 deque(maxlen=180) |
| 1800 | _SPARK_WINDOW_S(等于 180 点 x 每 10 秒一点) |

_PANEL_STYLE 上方那条注释写得很明白: 下面两行的 46 必须等于 _SPARK_H, CSS 高度和
viewBox 不一致时纵向会跟着缩放, 阈值线的位置就算错了。

### 为什么值得管

这类分叉的后果不是崩, 是错位: 阈值线画在错误的高度上, 而用户拿着它判断什么时候会
被判成高占用 —— 那条线在骗人。而且是静默的: 数字对不上不会有任何报错, 两张图只是
各自画得不太一样。

### 实现

没改一行生产代码。CSS 高度保持字面量(把它改成 f-string 拼 _SPARK_H 会让样式表失去
静态可读性, 而它本来就是给浏览器看的), 本轮做的是把这个已经成立的事实写成证据。

### 测试

新增 SparkGeometryConstants 4 条(全套 590 条, 仍只有 §114 记的那 2 个沙箱既有失败)。
四条分别对着四处: 托盘 Canvas、CSS 两条 height、deque 上限、窗口秒数。

其中窗口那条断言的不是两个数相等, 而是关系:
_SPARK_WINDOW_S == _SPARK_MAX_POINTS * 10。光写 1800 == 1800 的话, 把点数上限改成
200 而窗口不动, 那条用例照样绿 —— 而那时趋势图会白存一批窗口外的点。

### 非空性验证(4 处逐一回退, 只动临时副本)

| 回退 | 变红 |
|---|---|
| CSS .sparkwrap 的 46px 改 60px | test_the_css_height_matches_the_svg_height |
| 托盘 Canvas 的 height=46 改 60 | test_the_canvas_height_matches_the_svg_height |
| deque(maxlen=180) 改 200 | test_the_ring_buffer_matches_the_point_cap |
| _SPARK_WINDOW_S 改 2400 | test_the_window_is_the_cap_times_one_sampling_period |

四条各自精确对应, 没有一条是顺带红的。

### 用户决策待办

1. 现网 exe 仍是旧构建。§125/§126 是真缺陷, 建议优先上线。要同步需
   build → sync → 重启托盘(会打断当前实例), 等你点头。
2. 仓库根目录 300+ 个历史备份/补丁脚本的清理仍等你确认(见 §120)。



## 131. 续一百二十二：暂停时也要报数据年龄 + 面板 JS 有了行为测试（可用性/可观测性）

### 起因

参照 btop 的暂停视图。它的暂停态保留数据年龄，我们的不保留。停更判定原先长这样：

    if(!paused && dts && Date.now()/1000-dts>(stal||25)) staleshow(dts*1000);

暂停时整个停更判定被跳过，页面只说一句“监控已暂停, 数据停在 HH:MM:SS”。
暂停三分钟和三小时在页面上长得一模一样 —— 而用户想知道的是“我离开这段时间
它到底采到没有”。

### 实现（gpu_monitor.py，两处）

1. staleshow(ts) 改成 staleshow(ts, why)：停更原因由调用方给，默认仍是
   “监控进程可能已停止”（未暂停那一侧一个字没改）。
2. 停更判定从 if(!paused && ...) 改成先判暂停：

    if(paused){
      if(dts && 超时) staleshow(dts*1000,'已暂停');   // 也报数据停了多久
      else { 原来那句“监控已暂停, 数据停在...” }
    } else if(dts && 超时) {
      staleshow(dts*1000);                            // 原因用默认的
    }

暂停分支超时后说“数据已停更, 最后更新 HH:MM:SS (已暂停)” —— 既不说“可能已停止”
（那是误报），也不再把三分钟和三小时混为一谈。

### 过程中发现测试方法本身有盲区

第一版只加了 Python 字符串断言。跑非空性验证时把 JS 删成了语法错误
（if(paused){ 后面直接跟 else），Python 断言却全绿 —— 那条断言查的是“文本在
不在”，而回退后形状相似的文本还在。

字符串断言对“拼错了”完全失明，而这个项目的面板逻辑全在 Python 字符串里拼 JS，
正好是它的射程之外。于是补了真行为测试：

- tests/panel_stale_check.js：用 ast 从 _PANEL_SCRIPT 取真值（正则扫源码会把注释里
  的中文当字符串字面量抠出来，拼出语法错误的 JS —— 这个坑踩过一次），拿假 DOM
  驱动停更判定，断言七种组合下页面说出口的话。new Function 让每次场景都是干净
  作用域；固定时钟 NOW 让“数据几秒前”可算。
- PanelScriptJsBehaviour（Python）：把 JS 真值通过环境变量 PANEL_JS 传给 node 跑。
  node 不在 PATH 时 skipTest 并写明理由，不静默失败。

### 验证

- Node 实测七种场景：暂停+新 / 暂停+旧 / 未暂停+新 / 未暂停+旧 / 周期 60s 下
  100s（新）与 200s（旧），全部符合预期。
- 全套 591 条，仍只有 §114 记的那 2 个沙箱既有失败。
- 非空性验证：删掉暂停分支的计时子句，Node 守卫 rc=1，而 Python 字符串断言全绿。
  同一个回退两种结果，正是补这个守卫的理由。
- 顺手更新了 test_a_paused_monitor_is_not_reported_as_dead 里那条已经过时的断言
  （if(!paused&&dts 描述的是被替换掉的行为）。

### 用户决策待办

1. 现网 exe 仍是旧构建。§125/§126 是真缺陷，建议优先上线。要同步需
   build → sync → 重启托盘（会打断当前实例），等你点头。
2. 仓库根目录 300+ 个历史备份/补丁脚本的清理仍等你确认（见 §120）。
3. tests/panel_stale_check.js 让测试套件依赖 node。本机有（v24.14.0），CI 上没有会
   自动跳过；如果你希望它变成硬依赖，我可以把 skip 改成 fail。


## 132. 续一百二十三：Web 筛选函数也有了行为测试（可观测性）

### 起因

§131 给停更判定搭了 Node 行为测试的脚手架(ast 取 _PANEL_SCRIPT 真值 -> 环境变量
传给 node -> 假 DOM 驱动 -> 断言页面说出口的话), 并证明了字符串断言会失明。
面板 JS 里还剩一个更值得测的函数: f(), 也就是筛选。

WebPanelFilter 那五条断言查的是 td.n 在不在、indexOf 在不在、t++ 在不在。
筛选是面板上交互最多的功能, 而它的真实行为(哪些行可见、提示行说什么、空态行
动不动)一次都没被跑过。

### 实现

tests/panel_filter_check.js, 复用 §131 那套假 DOM。十三个检查分五组:

- 空词: 三个进程行全显、分母只数进程行(空态行不算)、空态行藏起来;
- 进程名: 大小写不敏感, 提示行跟着变成 1 / 3;
- PID: 完整可搜, 且是子串语义(和托盘 _matches_filter 一致);
- 数字词: "50" 是 heavy 的 GPU% 50.0, 但名字和 PID 里都没有 50, 所以三个
  进程行全被滤掉 —— 这一条盯着整行匹配的老毛病;
- 空态行: 一个都不中时它要留在场上并把文案改成"没有匹配的进程", 有命中时
  保持原样不动。

Python 侧新增 WebPanelFilterBehaviour, 和 PanelScriptJsBehaviour 同一形状。

### 第一版写错了三个预期

跑出来三条红, 逐条核对后发现**全是我预期写错, 不是产品 bug**:

1. 空词时我以为空态行该可见 —— 实际上 f() 会把空态行设成 display:none(有进程时
   就该藏), 正确;
2. 我以为 "12" 只该碰百分比列 —— 实际它命中 Chrome 的 PID 1234(子串), 也正确;
3. 无匹配时我既期望空态行文本是原句、又期望它被改写成"没有匹配的进程", 自相矛盾。

把三条改成符合真实行为的预期。这个过程本身说明: 行为测试会逼着你把"我以为"
和"实际是什么"分清楚, 而字符串断言永远不会让你发现自己的理解是错的。

### 非空性验证

摘掉 td.pid 那一支(var id='')后:

- WebPanelFilter(旧)红在 assertIn("td.pid", ...) —— 它只知道这段文本没了,
  说不清坏了什么;
- WebPanelFilterBehaviour(新)红在两条具体检查上: "完整 PID 可搜" want
  Chrome.exe got 没有匹配的进程、"PID 是子串匹配" 同上。

同一个破坏, 新旧两种守卫给出的信息量差一个量级。

### 验证

全套 592 条, 仍只有 §114 记的那 2 个沙箱既有失败。

### 用户决策待办

1. 现网 exe 仍是旧构建。§125/§126 是真缺陷, 建议优先上线。要同步需
   build → sync → 重启托盘(会打断当前实例), 等你点头。
2. 仓库根目录 300+ 个历史备份/补丁脚本的清理仍等你确认(见 §120)。
3. 测试套件现在依赖 node(两个行为测试)。本机有 v24.14.0; 没有会自动跳过
   并写明理由。要变成硬依赖的话我来改。


## 133. 续一百二十四：断连提示也有了行为测试（可观测性）

### 起因

§131/§132 给停更判定和筛选补了 Node 行为测试。面板 JS 里还剩最后一组值得真跑的
逻辑: connshow/connhide 与 fails>=3 的断连计数。

WebPanelLiveUpdate 那几条断言查的是 ++fails>=3 / connhide() / r.ok / 页面加载时
这些文本在不在。而"取不到数据时页面说什么"是用户判断监控是否还活着的唯一依据。

### 实现

tests/panel_conn_check.js, 七个检查分四组: 单次抖动不提示、连挂 3 次才提示(含文案)、
502 也按断连算、恢复后清掉提示、从没成功过时文案有兜底、抖一次再恢复不提示。
Python 侧新增 PanelConnBehaviour, 与前两条同形状。

### 脚手架踩了七个坑(都写进文件头注释了)

第一版跑出来四条红, 逐条挖下去发现全是脚手架的毛病, 不是产品问题:

1. 从 gpu_monitor.py 源码切 body —— 切出来是 Python 字符串字面量(带引号和 # 注释),
   new Function 拿到的东西完全不是一回事。必须从 PANEL_JS(纯 JS)切;
2. 同步循环跑完就断言 —— pull() 里是 fetch().then().catch(), 全是 Promise。
   必须 await 到微任务队列排空;
3. DOMParser 给了空实现 —— then 分支里 d.getElementById(...).innerHTML 当场抛,
   每一轮都落进 catch。要实现 parseFromString, 并给 PARTS 每个 id 一个可写元素;
4. scope.fetch 后补 —— Object.keys(scope) 里没有它, new Function 参数表也就没有,
   pull() 里的 fetch 解析到真全局, 每轮都抛;
5. body 从 connshow() 开始切 —— connhide 定义在它前面, 少切一段就
   "connhide is not defined";
6. 断言 fails/lastok 的返回值快照 —— 那是同步旧值, drain 之后早就变了。只断言
   conn 对象的终态;
7. 空过: "抖一次再恢复"和"单次失败不提示"在脚手架坏掉时也绿(conn.hidden 初始
   就是 true)。这也是为什么必须把每条都做成能失败的形状。

第 7 条最值得记: 坏掉的脚手架会让一半检查空过, 而空过的检查和通过的检查在输出里
长得一样。所以每个检查的 want 都要选一个和初始状态不同的值。

### 非空性验证

把 if(++fails>=3) 改成 if(++fails>=1) 之后:

- WebPanelLiveUpdate(旧)红在 assertIn("++fails>=3", ...);
- PanelConnBehaviour(新)红在 FAIL 单次失败不提示, want true got false。

### 验证

全套 593 条, 仍只有 §114 记的那 2 个沙箱既有失败。

### 用户决策待办

1. 现网 exe 仍是旧构建。§125/§126 是真缺陷, 建议优先上线。要同步需
   build → sync → 重启托盘(会打断当前实例), 等你点头。
2. 仓库根目录 300+ 个历史备份/补丁脚本的清理仍等你确认(见 §120)。
3. 测试套件现在依赖 node(三个行为测试)。要变成硬依赖的话我来改。


## 134. 续一百二十五：三个面板 JS 行为测试收进一份共享脚手架（可维护性）

### 起因

§131/§132/§133 先后加了 panel_stale_check.js / panel_filter_check.js /
panel_conn_check.js。三个文件各写了一套脚手架, 而那七个坑的修法则只活在
各自的注释里 —— 第四个脚本再来一遍, 大概率再踩一遍。

### 实现

新增 tests/panel_js_harness.js, 导出八件工具:

- loadPanelJs(): 从 PANEL_JS 取并剥掉 <script>; 空则退出码 2 并说明原因;
- readParts(js): 从面板 JS 里读 PARTS 清单, 不写死;
- sliceBetween(js, from, to): 切片, 任一端找不到就抛 —— 静默返回空串会让
  new Function 拿到不完整的代码, 而报错指不到真正的原因;
- makeDom(extra): 假浏览器全局; extra 里每一项都会成为编译后函数的参数;
- domParserFor(parts): 能用的 DOMParser;
- compile(js, env, params, body): 编成函数; params 是面板代码引用但不声明的
  那些名字(conn / fails / lastok / q);
- drain(rounds): 排空 Promise;
- report(checks, line, mode): 汇总 + 退出码。mode 有 eq(默认) 与 includes。

三个脚本改成薄壳, 只写自己的检查。顺带把每个脚本的检查补得更硬:

- stale 从 7 条到 10 条: 新增三条"不含"断言(暂停时不许说"可能已停止"、
  未暂停时不许说成"已暂停")。includes 模式表达不了否定, 所以单独一组;
- conn 从 7 条到 9 条: 新增"两次失败还不出提示"与"两次失败时文案是空的"。
  后者盯着 §133 第 7 条那个坑 —— 脚手架坏掉时等于初始态的检查会空过,
  而空过的检查和通过的检查在输出里长得一样;
- filter 13 条不变, 只是改用共享脚手架。

### 两个新踩的坑

1. report 原来只有严格相等, 而文案检查需要"包含"。第一版给 report 加 mode
   时, 空串那一档出问题了: includes('') 恒真, "什么都不说"这条会永远绿。
   改成 want 为空时比长度(got.length === 0)。
2. panel_conn_check.js 顶层 await 和 require 混用, node 判为 ESM 拒绝加载
   (ERR_AMBIGUOUS_MODULE_SYNTAX)。把主体包进 async main()。

### 非空性验证(三个破坏各试一次)

| 破坏 | 被抓住的守卫 |
|---|---|
| 暂停分支不再报数据年龄 | PanelScriptJsBehaviour |
| 筛选退回整行 textContent 匹配 | WebPanelFilterBehaviour |
| 单次失败就提示 | PanelConnBehaviour |

### 验证

全套 593 条, 仍只有 §114 记的那 2 个沙箱既有失败。三个脚本手工跑也都是全绿。

### 用户决策待办

1. 现网 exe 仍是旧构建。§125/§126 是真缺陷, 建议优先上线。要同步需
   build → sync → 重启托盘(会打断当前实例), 等你点头。
2. 仓库根目录 300+ 个历史备份/补丁脚本的清理仍等你确认(见 §120)。
3. 测试套件现在依赖 node(三个行为测试 + 一份共享模块)。要变成硬依赖的
   话我来改。


## 135. 续一百二十六：`tools/verify_build.py` 本身也端到端跑通（可观测性）

### 起因

§133/§134 用**手工分步**的方式验了两个 spec(分别调 PyInstaller、再分别跑 exe)。
但 `tools/verify_build.py` 这个脚本本身从没被端到端跑过 —— 如果它内部有 bug
(冒烟判据写错、路径拼错、退出码逻辑反了), 用户第一次用才会发现, 而那时
看到的是一条绿油油的"验证通过"。

脚本自己的话不可信, 得有人跑过它。

### 实测(同一台机器, Python 3.14.3 + PyInstaller 6.20.0)

    GPUMigrate.spec            build: OK
                               smoke: OK  (退出码 0, 无打包类错误)
    GPUMigrate-onedir.spec     build: OK
                               smoke: OK  (退出码 0, 无打包类错误)

    两个 spec 都构建成功, 且打出来的 exe 能跑 --once

脚本自己报出了全部四项 OK, 退出码 0。这与 §133/§134 手工分步的结果一致 ——
也就是说脚本没把绿的报成红的, 也没把红的报成绿的。

一个过程中的发现: 脚本输出是 Python 默认的块缓冲, 构建期间文件一直是
0 字节。期间我误判过一次"没输出=卡死了", 实际进程 CPU 时间在涨。脚本
将来如果要在 CI 里用, 值得加 `-u` 或 flush; 现在先记着。

### 清理

验证产物 `.tmptest/buildverify` 已删, 仓库根没有残留 build/ 或 dist/。

### 用户决策待办

1. 现网 exe 仍是旧构建。spec 已完整验证(spec 可移植 + 依赖对齐 + 真构建 +
   真运行 + 一键复跑), 要同步需 build → sync → 重启托盘(会打断当前实例)。
2. 10 条新提交未 push, 与 origin/main 分叉。



## 136. 续一百二十七：构建验证不再"静默十几秒"（可观测性）

### 问题

§135 把 `tools/verify_build.py` 端到端跑通了, 但那次实测暴露了一个
观测性缺陷: `build()` 用 `capture_output=True` 把 PyInstaller 的输出全量
吞掉, 构建那十几秒里屏幕上一点动静都没有, 日志文件也一直是 0 字节
(Python 的块缓冲)。我当时据此误判过一次"卡死了", 而进程的 CPU 时间明明
在涨 —— 也就是说, 慢和死在屏幕上看不出区别。

### 改动

`tools/verify_build.py:build()` 重写:

1. 去掉 `run()` 助手, 直接 `subprocess.run` 并把 stdout/stderr 重定向到
   `.tmptest/buildverify/build-<spec>.log` —— 边跑边落盘, 不是全量吞。
2. 子进程加 `-X -u` 无缓冲, 日志能实时看见。
3. 构建前先 `print('... 正在构建(日志 <路径>)...', flush=True)`, 二十秒内
   就能确认在动, 且失败时知道去哪看完整日志。
4. 成功返回 `(True, '')`, 失败返回日志后 8 行 —— 之前是取最后 6 行,
   失败路径原本就只能拿到被截断的尾串。

`main()` 的判定逻辑没动, `SPECS` 清单也没动。

### 实测

`C:/Python314/python.exe -X -u tools/verify_build.py`, 输出:

    GPUMigrate.spec           正在构建(日志 ...build-GPUMigrate.spec.log)...
    GPUMigrate.spec            build: OK
                               smoke: OK  (退出码 0, 无打包类错误)
    GPUMigrate-onedir.spec    正在构建(日志 ...build-GPUMigrate-onedir.spec.log)...
    GPUMigrate-onedir.spec     build: OK
                               smoke: OK  (退出码 0, 无打包类错误)

    两个 spec 都构建成功, 且打出来的 exe 能跑 --once

退出码 0。进度行确实在 20 秒内出现, 与 §135 的结果一致。

### 测试

`BuildVerifierScript` 的 5 条测试改后重跑通过 —— 被测的字符串锚点
(`sys.executable`、`SPECS = (`)都还在, 这次改的是返回值语义和实现, 不涉及
它们。全套 617 条两个 runner 均全绿。

### 清理

`.tmptest/buildverify`、`verify_out.txt`、`verify_err.txt`、
`doverify2.py` 已删; 仓库根无 build/ 或 dist/ 残留。

### 用户决策待办

1. 现网 exe 仍是旧构建。要同步需 build → sync → 重启托盘(会打断当前实例)。
2. 13 条新提交未 push, 与 origin/main 分叉。

## 137. 续一百二十八：NVML 温度读取搬进隔离子进程（稳定性）

### 起点

§109/§111 两处记着同一个未解决的问题: 本机
`gpu_monitor_crash.log` 里 165 段 `access violation` 的首帧**全**落在
`gpu_monitor.py:1072` 的 `nvmlDeviceGetTemperature` 上, 而"子进程隔离采样"
被列为"更大的架构改造, 留作后续"。

### 为什么现在能动

读了一遍 `install_fault_dump()` 的实测结论才发现前提比原文记的更硬:
C 级访问冲突**能不能**被 Python 接住, 取决于被调方内部有没有 SEH, 不取决于
Python。所以"监控线程自己中招"不是理论风险, 是真会发生的事 —— 那就值得治。

但没有照原文说的"搬整个采样"。先做了代价评估:

- 实测每轮采样的构成: `collect_gpu_sample(window=5)` 5111ms, 其中窗口本身
  4930ms、PDH query 建立 12ms、首次 collect 62ms。**采样已经占满整个周期,
  没有可优化空间**, 而"窗口=周期"是 §88 定下的防漏采设计, 不能动。
- `aggregate_usage` 0.07ms、`pid_to_name` 1.5ms(缓存命中), 都不是瓶颈。
- 682 个 util 路径里 550 个属于独显、130 个属于 virtual —— 但 virtual 早就被
  `allowed_luids` 过滤掉了, 独显的数据面板/省电模式真要用, 砍不了。

结论: 值得搬的只有**唯一有实证危险的那一个调用**。

### 改动

1. `nvml_gpu_temp` 拆成 `_nvml_read_in_process`(原实现本体) + 新的
   `nvml_gpu_temp(index=0, isolate=True)`: isolate 时优先走子进程探针, 拿不到
   才退回进程内。调用点一行没改。
2. 新增隐藏子命令 `--nvml-probe INDEX`: 起一个干净子进程读一次温度并打印。
   用 `argparse.SUPPRESS` 不进 `--help`; 放在 `parse_args` 之后、
   `ensure_singleton` 之前 —— 探针是短命进程, 不该跟常驻实例抢单例锁。
3. 探针跑**同一个源文件**(不是 `-c` 内联): 内联的话探针逻辑会和父进程漂移,
   而这里读的逻辑必须是同一份。
4. `CREATE_NO_WINDOW`: 无窗口模式下不能闪控制台。
5. 探针跳过 `install_fault_dump()`: 秒退进程白开一个句柄, 满档时还要跟父进程
   抢一次滚动。

### 一个走不通的方案(记下来省得下次再试)

本来想用 `sys.executable -c "..."` 起探针。实测**不行**: 打包版 exe 的
argparse 会拦截 `-c`(`error: unrecognized arguments: -c ...`), exe 当不了
通用解释器。所以必须走"给源文件加隐藏子命令"这条路。

### 只在源码态隔离

打包版 `sys.executable` 就是 exe, 每次起子进程都要重新解压 onefile 镜像 ——
实测 `GPUMigrate.exe --once` 9.8s, 而温度每 10s 读一次。隔离会把采样周期
整个吃光, 所以那种环境下 `_nvml_probe_in_subprocess` 直接返回 None, 老老实实
进程内直读。

### 实测

| 路径 | 耗时 | 结果 |
| --- | --- | --- |
| `--nvml-probe 0` | 1.0s | 输出 `46`, rc=0 |
| `nvml_gpu_temp(0)`(隔离) | 323/351/331ms | 45°C |
| `nvml_gpu_temp(0, isolate=False)` | 42ms / 0ms(缓存) | 45°C |

五种失败模式全部容错且父进程健在(在真实的 `subprocess.run` 上打桩, 不动
gm 自己的容错逻辑):

    timeout      -> 44    # 回退进程内
    oserror      -> 44
    killed       -> 44    # rc!=0: 子进程被干掉, 隔离生效
    noreading    -> 44
    garbage      -> 44

探针全失败 + 进程内也读不到 → `None`, 不抛。
探针模式 `_FAULT_FH = None`, 正常模式 `True`。

### 测试

新增 `NvmlProbeIsolation` 8 条: 探针成功不回退、rc!=0/超时/OSError/垃圾输出
全部容错、探针自己 `isolate=False`(不生孙进程)、跑同一个源文件且带
`CREATE_NO_WINDOW`、预算有界。

过程中踩到一个自己造的坑: 有一条用例整体替换了
`gm._nvml_probe_in_subprocess`, 而 `tearDown` 只还原了 `subprocess.run` 和
`_nvml_read_in_process` —— 污染了同类的后一条用例。补上还原才好。这也说明
`SuiteRunsInReverseOrder` 那条守顺序敏感性的用例不是摆设。

全套 625 条(617 + 8), 两个 runner 均全绿。README 配置表加了一句"源码态读
温度跑在隔离子进程里", 稳定性一节补了完整说明。

### 用户决策待办

1. 现网 exe 仍是旧构建。要同步需 build → sync → 重启托盘(会打断当前实例)。
   注意现网那个 exe 是 10 月 4 日的, 连 §136 的 verify 改动都不含。
2. 14 条新提交未 push, 与 origin/main 分叉。

## 138. 续一百二十九：面板趋势图被 Treeview 压成 1px（布局/可读性）

### 问题

用户要求"ui 行距 优化"。实测(不是猜)发现真正的毛病不在行距本身,
而在**纵向预算超支导致的关键部件被压扁**:

    卡片 68 + 筛选栏 32 + 表格 370(12 行) + 趋势图 46 + 状态区 51
    = 567px, 而默认窗口只有 470px —— 缺口 97px

`Treeview` 是 `fill=both` + `expand=True`, `pack` 在总量超限时把后面
几个部件按剩余空间压扁。拿真 tkinter 建面板量出来:

    Frame      h=56      <- 卡片
    Frame      h=28      <- 筛选栏
    Treeview   h=362
    Canvas     h=1       <- 趋势图, 代码里 height=46
    Frame      h=1       <- 底部状态行
    Frame      h=1       <- 会话累计

趋势图和底部两行("刷新节奏/快捷键" + "采样 N 轮")**各只剩 1px**, 用户
根本看不见, 而 `height=46` 在源码里写得明明白白。现网 `panel_ui.json`
存的是 `1046x470+55+55`, 正是这个状态。

顺带量了行距本身: 字号 9 的 linespace 是 16px, `rowheight=28` 上下各
留 6px —— 这个是标准 Fluent 密度, 不拥挤, 不用动。

### 修法

表格少了可以滚动, 趋势图和状态行少了没有任何补救, 所以给它们保底:

1. **默认窗口高度 470 -> 580**。扫了一遍 470/560/575/580/590/600, 575
   是临界值: 到这个高度底部两个 Label 才各拿到 23/21px 完整可见, 趋势图
   46px, 表格 12 行一行不少。取 580 留 5px 余量。
2. **`_onscreen_geometry()` 把读到的矮窗口提到保底高度 `_PANEL_MIN_H`**。
   `panel_ui.json` 是人也能改的文件, 老版本存下来的正是装不下一屏的高度;
   原样还回去等于修了默认值但老用户一点没感觉到。位置照旧算数, 只提高度 ——
   "用户故意把窗口拖矮"这个场景不存在: 拖矮的过程中内容是被挤掉的, 没有
   人在追求那个结果。
3. **趋势图容器和状态区改用 `pack_propagate(False)` 的 Frame 锁死高度**,
   不许被邻居挤掉。高度收进 `_SPARK_H` / `_STATUS_H` 常量, 不再散着写。

### 实测

拿现网 `panel_ui.json` 走真实路径:

    saved : 1046x470+55+55
    used  : 1046x580+55+55

    Frame      h=56
    Frame      h=28
    Treeview   h=375
    Canvas     h=46    <- 趋势图容器
    Frame      h=46
    Frame      h=51    <- 底部状态区
      Label    h=23
      Label    h=21

趋势图和底部两行全部完整可见, 表格 12 行一行不少。用户不需要手动改文件。

### 测试

新增 `TrayPanelStatusFloor` 5 条: 默认高度装得下全部区块、状态区装得下
两行 Label、两个 Frame 必须 `pack_propagate(False)`、矮窗口被提升、
够高的窗口不被动手。

同步更新三条既有契约:
- `SparkGeometryConstants`: 原来要求源码里字面写着 `height=46`, 现在改用
  `_SPARK_H` 常量反而更严(另加一条 `assertNotIn` 字面量)。
- `TrayPanelGeometryGuard`: 兜底值 `1040x470` -> `1040x580`; 那条
  "往左探出半截的窗口要保留"现在断言高度被提升、位置不变。
- `TrayTextColorGuard`: 两行 Label 的父控件从 `root` 变成 `bottom`。

全套 **636 条**(629 + 5 新 + 2 条拆分), 两个 runner 均全绿。

### 用户决策待办

1. 现网 exe 仍是旧构建(10-04)。要同步需 build -> sync -> 重启托盘。
2. 31 条新提交未 push, 与 origin/main 分叉。

## 139. 续一百三十：趋势图底边贴住状态行，与 Web 面板的 10px 间距不一致（布局/可读性）

### 现象

§138 把趋势图和底部两行从 1px 救回来之后，肉眼再看一遍面板：趋势图
Canvas 的底边**直接贴住**底部那两行灰字("刷新节奏/快捷键" 和 "采样 N
轮")，折线和文字之间一点空隙都没有。而 Web 面板那边 `.sparkwrap` 写的是
`margin:0 0 10px` —— 同一个"近 30 分钟"，在托盘里糊在字上，在浏览器里
空着 10px。代码里 `spark_box.pack(..., pady=(0, 0))` 写得明明白白。

### 为什么是缺陷

间距不是装饰：折线贴着一行 9 号灰字，视觉上分不清哪段属于图、哪段属于
状态区；Web 面板给了 10px 说明这个空隙本来就是设计意图，托盘这边只是
漏了。三面（托盘/Web/口径文档）不一致，用户在浏览器里看到的样子和托盘
里对不上。

### 改动

- `gpu_tray.py`：新增常量 `_SPARK_GAP = 10`，`spark_box.pack` 从
  `pady=(0, 0)` 改为 `pady=(0, _SPARK_GAP)`。
- 间距吃掉了 10px 纵向预算，保底高度 **580 -> 590**（实测临界值同步从
  575 抬到 585），`_onscreen_geometry` 默认 geometry `1040x580` ->
  `1040x590`。

### 实测

临界高度扫描 570..605（步长 5），`spark_box`/`bottom` 两个 Frame 都
`pack_propagate(False)` 锁高：

    H=580  [56, 28, 370, 46, 46, 46]  bottom=46   <- 差 5px
    H=585  [56, 28, 370, 46, 46, 51]  bottom=51   <- 临界点
    H=590  [56, 28, 375, 46, 46, 51]  bottom=51   <- 取这里，留 5px 余量

老存档 `1046x470+55+55` 自动提升为 `1046x590+55+55`（位置保留），最终
布局：卡片 56 + 筛选栏 28 + 表格 375(12 行) + 趋势图 46 + **间距 10** +
状态区 51，底部两个 Label 各拿到 22px 完整可见。用户无需手动改文件。

### 测试

- `TrayPanelStatusFloor._budget()` 把 `_SPARK_GAP` 纳入纵向预算算式，
  不再漏算这 10px（原来算式加出来 577 对 580 刚刚好，加了 gap 就 587
  对 580，测试当场能红）。
- 新增 `test_the_spark_gap_matches_the_web_panel`：断言源码里写的是
  `pady=(0, _SPARK_GAP)`、`_SPARK_GAP = 10`，并从 `gm._PANEL_STYLE`
  正则抠出 `.sparkwrap` 的 `margin:0 0 Npx` 与 `_SPARK_GAP` 比对 ——
  "三面一致"从此有东西守，不再靠口头约定。
- 同步更新 6 处 `1040x580` / `1046x580` / `1020x580` 断言为 590。

全套 **637 条**（636 + 1 新），两个 runner 均全绿。

## 140. 续一百三十一：汇总卡标签糊在数字上、底部两行灰字只空 1px（布局/可读性）

### 现象

§139 把趋势图与状态区的间距对齐了 Web，这一轮接着把面板剩下的行距
**逐处量出来**和 Web 对比，抓到两处真实不一致：

1. **汇总卡里标签与数值之间 0px**。`gpu_tray.py` 里是
   `pady=(5,0)` 配 `pady=(0,5)`：标签底边直接贴上数值顶边，而 Web 面板
   `.card b` 写着 `margin-top:2px`。六张卡每张都少这两像素，攒成一张卡
   就是标签糊在数字上。
2. **底部状态区两行之间 1px**。原来是 `pady=(0,1)`，两行灰字几乎贴在
   一起，而 Web 面板 `#hidden` / `#counters` 是 `margin:2px 0 0`。

### 改动

- 新增 `_CARD_LABEL_GAP = 2`，卡片标签改用 `pady=(5, _CARD_LABEL_GAP)`，
  数值底部留白相应 5 -> 3 —— **卡片总高仍是 56，纵向预算一点没动**。
- 新增 `_STATUS_LINE_GAP = 2`，底部第一行改用
  `pady=(0, _STATUS_LINE_GAP)`；间距多吃 1px，`_STATUS_H` 同步
  **51 -> 52**，不然第二行 Label 会被压掉 1px。

### 实测

老存档 `1046x470` 自动提升为 `1046x590`，最终：

    卡片内 标签->数值 间隙 = 2px    (Web 2px)
    底部两行 行间        = 2px    (Web 2px)
    底部两行高度         = 23 / 21px (都满血)
    趋势图高度           = 46px
    趋势图 -> 状态区间距 = 10px   (Web 10px)

临界高度重新扫过 575..605：585 仍是临界点，`_PANEL_MIN_H=590` 留 5px
余量，不用改。

### 测试

新增两条契约测试（`test_the_card_label_gap_matches_the_web_panel` /
`test_the_status_line_gap_matches_the_web_panel`）：从 `_PANEL_STYLE`
正则抠出 Web 侧的 `margin` 与托盘常量比对，不一致就红。这两处此前
只靠口头约定，没有任何东西守。

全套 **639 条**（638 + 1 新），两个 runner 均全绿。

### 三面行距对照（本轮审计结论）

| 位置 | 托盘 | Web | 状态 |
| --- | --- | --- | --- |
| 趋势图 -> 状态区 | 10px | `margin:0 0 10px` | §139 已对齐 |
| 卡片内 标签->数值 | 2px | `margin-top:2px` | 本轮对齐 |
| 底部两行 行间 | 2px | `margin:2px 0 0` | 本轮对齐 |
| 卡片区上下 | 上 8 / 下 4 | `margin:10px 0 12px` | 有意不齐 |
| 表格行高 | 28px | 约 29px | 一致 |

最后一项有意保留差异：托盘纵向预算已经顶格（590 是临界值 585 加 5px
余量），把卡片区下边距从 4px 提到 12px 要多吃 8px，只能从表格行数里
抠 —— 那是拿"能看见几行"换"区块之间空一点"，不划算。

## 141. 续一百三十二：面板行距逐处量完，表格行内留白与表头高度有据可查（布局/可观测性）

### 本轮做的事

§139/§140 修完三处能对齐的行距之后，这一轮把面板**剩下所有行距逐处
量出来**，逐项决定"对齐"还是"有意保留"。

### 量到的结果

1. **表格行内留白两侧一致**。托盘 `rowheight=28` 减字号 9 的
   `linespace=16` 得 12px（上下各 6）；Web 侧 `td` 是
   `padding:6px 10px` + 行盒 15.6px + `border-bottom:1px` = 29px，扣掉
   文字和边框也是 12.4px（上下各 6）。两边手感相同，无需改动 —— 但
   此前没有任何东西保证它不变，新增一条契约钉住。
2. **表头高度有意不追**。托盘 `ttk.Treeview` 表头固定 38px；实测把
   `Treeview.Heading` 的 `padding` 从 `(6,3)` 改到 `(6,6)`，表头高度
   一个字节都不动（仍是 38px）—— 它由 ttk 的布局算法定死。Web 的 `th`
   是 28.6px，差 9px。这不是常数能对齐的，硬凑只会把数据行数挤掉。
   差异写进测试 docstring，后来的人不用再量一遍。
3. **详情窗无缺陷**。8 行，行间距 4px（`pady=2` 上下各 2），行内留白
   7px，按钮区与正文间隔 16px，底部余量 6px。
4. **设置窗无缺陷**。`Label` 23px / `Entry` 21px / `Checkbutton` 27px，
   行间 4-5px，底部余量 8px。

### 测试

新增 `test_the_table_row_inner_padding_matches_the_web_panel`：把
"托盘行内留白 == Web 行内留白"这个等式算出来断言。字号 9 的
`linespace` 用字面量 16 而不是 `tkfont.Font` —— 单测里没有 Tk 根窗口，
`Font()` 会抛 `RuntimeError: Too early to use font`（本轮踩过）。

全套 **640 条**（639 + 1 新），两个 runner 均全绿。

### 新待办（非行距，本轮未改）

**趋势图角标与折线峰值路径重叠**。角标钉在 `(w-6, 6)`、`anchor="ne"`，
而 `spark_y_of` 在峰值时 `y` 正好也是 6（公式
`46 - 4 - 36*v/ymax`，顶部余量 6px）。占用冲到顶时折线末段会穿过
"近30分钟 峰值xx% 当前xx%"那行字。托盘和 Web 两侧同样（Web 的
`.corner` 是 `top:3px`）。修它要动 `spark_y_of` 这个共享公式，会同时
改变阈值线位置，影响面超出"行距"范围，单独一轮做。

## 142. 续一百三十三：趋势图两行小字被折线切成两半（布局/可读性）

### 现象

§141 记账的那个问题，这轮修掉。

角标钉在 `(w-6, 6)`、`anchor="ne"`，而 `spark_y_of` 峰值时 `y` 正好也是
6（公式 `height - 4 - (height - 10) * v / ymax`，顶部余量就 6px）。实测
占用冲顶那天，**折线末段 11 个点全落在角标的 bbox 里** ——
"近30分钟 峰值xx% 当前xx%"那行字被线穿过。Web 侧同样：`.corner` 是
`top:3px`，而 `_SPARK_H` 下峰值 `y` 也是 3。

### 为什么垫底，而不是把折线区域缩小

放哪个角都躲不开：折线首点在左、末点在右，固定在一角总有一半序列
撞上。把可绘高度从 36px 砍到 18px 去躲它又太贵 —— 那是拿起伏换不穿
字。所以垫一块与画布同色的底：线从字底下过，字照样完整。

### 改动

- `gpu_tray.py` 新增 `TrayApp._canvas_label(canvas, x, y, anchor,
  text, color)`：先画一块比文字 `bbox` 大 2px 的 `_PANEL_BG` 矩形，再
  `tag_raise` 把文字抬回最上层。三处调用都走它：空态阈值标注、常态
  阈值标注、角标。
- `gpu_monitor.py`：`.sparkwrap .thr` / `.corner` 加
  `background:#f3f3f3`，深色档同步 `#202020`（body 底色）。

### 测试

`SparkGeometryConstants` 新增两条：
- `test_the_canvas_labels_sit_on_a_background_pad`：托盘三处必须走
  `_canvas_label`、底垫必须用 `_PANEL_BG` 且 `tag_raise`。
- `test_the_web_labels_sit_on_a_background_pad`：Web 两档四个规则都
  必须带 `background`。

实测六种场景（末点冲顶 / 全程 100% / 全程 0% / 无阈值 / 空态 /
单点）：有阈值时 2 text + 2 rect，无阈值 1+1，空态 2 text + 1 rect
（中间那句提示不挡折线，不垫）。

全套 **642 条**（640 + 2 新），两个 runner 均全绿。

## 143. 续一百三十四：底部状态行被右沿切成半句话（布局/可读性）

### 现象

底部那一行同时扛着显卡名、刷新节奏和一串快捷键，而 `Label` 是
`fill=x` 的：除了 `anchor` 之外没有任何截断机制，**超宽就是从右边直接
切掉**。实测两块真卡的名字加满快捷键要 **728px**，而表格 8 列默认宽合计
**722px** —— 窗口一拖窄，"Del 永不迁移 / 筛选 右键同菜单"这半句就没了，
而那正是告诉键盘/右键用户入口在哪的文字。

代码注释里早就写着"被右沿切掉等于什么都没说"，但没有任何东西去截。

### 改动

- 新增 `_text_px(text)`：量字符串在面板正文字号下占多少像素。`Font`
  对象缓存起来（这一行每秒刷一次），`_rebuild_fonts` 换字体时作废 ——
  不然拿旧字体的度量截新字体的文本，截多截少都是错的。量不到（没有
  Tk 实例）返回 0，让调用方走"不截断"老路。
- 新增 `TrayApp._status_line(core, keys, widget)`：**二级截断**，顺序按
  信息价值排 —— 先整段丢快捷键（显卡名和刷新节奏说明"这是什么"），
  还不够才二分砍核心串并补省略号。二分而不是逐字符退：这一行每秒刷
  一次，O(n²) 的逐字量宽度在长名字上会白烧 CPU。
- `winfo_width()` 不是 `int` 时（替身控件回来的是 MagicMock，拿它减 16
  再和 0 比较当场 TypeError）原样返回：老行为至少快捷键还在，总比把
  每秒一次的刷新链打断好。

### 实测

    可用宽度 -> 实际占用
      1030 -> 728px  完整
       722 -> 445px  丢快捷键, 核心串完整
       400 -> 398px  核心串完整
       300 -> 299px  核心串 + 省略号
       150 -> 149px  核心串 + 省略号

### 测试

`TrayPanelKeyboardNav` 新增两条：
- `test_the_status_line_never_gets_clipped_at_the_right_edge`：各宽度下
  都不超宽、截断必带省略号、核心串放得下时不许动它。
- `test_the_status_line_survives_a_control_with_no_width`：宽度是
  `None` / 字符串 / 对象 / 浮点 / `True` 时都不抛也不截。

测试里要自己建一个真 `tk.Tk()`：`_text_px` 没有 Tk 根窗口时返回 0，
那样量出来的"放得下的宽度"全是 16，断言就空了。

全套 **644 条**（642 + 2 新），两个 runner 均全绿。

## 144. 续一百三十五：筛选栏提示行只拿到 6px，按钮反被切一半（布局/可读性）

### 现象

§143 那个病在筛选栏还有一处，**比底部状态行更严重** —— 那行字从来没
显示全过。

`hint` 是 `side=left` 不带 `fill`/`expand` 的 Label：空间一紧，pack 压后
声明的元素，而它右边还压着"复原排序"（109px）和"置顶"（53px）两个按钮。
实测：

    窗口 1046  hint 417px / pin 53px   <- 放得下
    窗口  900  hint 278px / pin 29px   <- 按钮先被切一半
    窗口  722  hint 100px / pin 29px
    窗口  560  hint   1px / pin 29px

"按 Esc 关闭窗口"这半句（面板唯一的关窗提示）在 900 宽以下就看不见，
而按钮反而少一半字。

### 改动

- `hint` 改到 `side=right` 组的第一个 + `fill=x` + `expand=True`：pack
  现在压的是 expand 的这个 Label，两个按钮的请求宽度始终被满足。放
  left 组里试过，按钮照样被切。
- 新增 `_fit_text(text, avail_px)`：二分截断加省略号。`_status_line` 与
  这行共用（原来 `_status_line` 里内联了一份二分）。
- 提示行按可用宽度截：从 `hint.master` 算（父容器宽 - 同行其他元素的
  请求宽度）。

### 两个量不到宽度的坑（都写进注释）

1. 问 `hint.winfo_width()` 量的是"我现在多宽"而不是"我能有多宽" ——
   于是永远按当前宽度截，**越截越短，最后只剩一个省略号**（实测）。
2. 问 `_panel_build` 里的局部变量：`_refresh_tree` 是独立方法，拿不到，
   走 `except` 静默变 0，于是**永远不截**。

### 实测

    窗口  提示行文本 px / hint 宽 / pin 宽
     1046   404 / 424 / 53
      900   259 / 278 / 53
      800   153 / 178 / 53
      722    80 / 100 / 53
      650     9 /  28 / 53

### 测试

`TrayPanelKeyboardNav` 新增两条：
- `test_the_row_hint_gets_the_space_the_buttons_leave`：hint 必须先于
  按钮 pack 且带 `expand=True`。
- `test_the_row_hint_is_truncated_to_the_space_it_really_has`：可用宽度
  从父容器算、走 `_fit_text`、量不到时降级。

全套 **646 条**（644 + 2 新），两个 runner 均全绿。

## 145. 续一百三十六：详情窗折行宽度比右列还宽 29px，长路径整行画出窗外（布局/可读性）

### 现象

详情窗（`_detail_build`，520x300）的右列 Label 写着
`wraplength=380`。实测这一列真能用的宽度只有 **351px**：

    窗口 520  -  padx 14x2(28)  =  frm 492
    frm 492   -  键名列 141    =  右列 351

键名列 141 不是估的：最宽那个键是"GPU% / 核显% / 独显%"，9 个键全
grid 出来量 `winfo_reqwidth`，最大就是 141。

`wraplength` 比可用宽大 29px 会怎样？Tk 的语义是"折到不超过
wraplength"，**不是**"折得进窗内"。有空格的长路径（Steam 游戏目录
那种）运气好，断点落在空格上、第二行短，看不出问题；一旦路径里一个
空格都没有（不少安装路径就是这样），实测：

    无空格路径 776px 总宽
      wl 380 -> 渲染 385px   超 34px，右沿被裁
      wl 350 -> 渲染 355px   超  4px
      wl 345 -> 渲染 351px   刚好

行高也从 23 涨到 57（折成 3 行）—— 说明它在折，只是折不完。

### 改动

- 新增 `_DETAIL_WRAP_PX = 345`（`gpu_tray.py:486`），`_detail_build` 里
  的 `wraplength=380` 换成它。345 = 351 - 6px 字体差异余量。
- 注释里写清算式：为什么是 351、为什么不能再写大。

### 顺带确认无缺陷的面

- **折行不溢出**：9 行全折（含"注意"那行 2 行）`frm` 请求高 277，预算
  280（300 - pady 10x2），且 `frm` 是 `expand=True`，窗口高固定 300 兜底。
- **tooltip 弹泡**：`_TIP_WRAP_PX = 320`，`padx=8 pady=6`，行距就是
  linespace 16，没有额外间距可调；`smem` 那条 552px 折 2 行（318+234），
  折点合理。
- **托盘右键菜单**：只有 1 项，行距由 Tk 定，不可调。
- **Web 面板 `#hint`/`#hidden`/`#counters`**：普通 `<p>`，浏览器自己
  换行，不存在 Tk Label 那种右沿切断。

### 实测

    路径（无空格，776px）  wl 345 -> 渲染 351px / 3 行，右沿不裁
    路径（Steam，490px）  wl 345 -> 渲染 295px / 2 行
    9 行全折              frm 277px，预算 280

### 测试

`DetailPrefRefresh` 新增
`test_the_detail_wrap_fits_the_column_it_actually_gets`：真建一个
520x300 的 Tk 根，按详情窗同样的方式 grid 9 个键名，量出键名列宽，
`root.winfo_width() - 28 - col0` 算可用宽，断言 `_DETAIL_WRAP_PX`
不大于它。

全套 **647 条**（646 + 1 新），两个 runner 均全绿。

## 146. 续一百三十七：设置窗保存键被 pack 压成 1px，460x420 装不下自己的内容（布局/可读性）

### 现象

设置窗（`_settings_build`）写着 `460x420`，而它的内容要 406px：

    6 行标签+输入   6 x 27 = 162
    4 个勾选        4 x 31 = 124
    游戏名单        60(标签同行) + 空行 5
    排除名单        60
    合计                 406

可用高度只有 358（420 - pady 8x2 - 保存键 30 - pady 8x2）。`grid` 是
`expand=True`，请求超预算时 pack 压**后声明**的元素 —— 保存键声明在
`grid.pack` 之后，于是：

    grid 请求 406 / 实得 404   保存键 高 1px, y=0

实测：`btn.winfo_height() == 1`，`winfo_y() == 0`。窗口标题、六个格子、
两个名单全都看得见，唯独"保存 (热重载立即生效)"这个**全窗唯一想留住
的强调色按键**只剩一条 1px 的线。代码里 `bg=_ACCENT, fg=_SAVE_FG`
配得清清楚楚，用户一个像素都看不到。

主面板在 470 那一档上踩过完全相同的坑（`_default_ui` 注释里记着：
"pack 把趋势图和底部两行压成 1px 高"），设置窗这一处一直没被发现。

### 改动

三处一起改，少一处都不够：

- `root.geometry("460x420")` -> `"460x440"`。
- 设置窗内 7 处 `pady=2` -> `pady=1`（6 行输入 + 4 勾选 + 2 组名单）。
- 两个名单 `tk.Text(height=4)` -> `height=3`。

只提窗高无效，实测老参数（pady=2 + 4 行）在 420/430/440/450/460
**全都会**把按钮压成 1~2px（406 请求 vs 最多 398 可用）：

    老参数 req=406:  420 -> 1px   430 -> 1px   440 -> 2px
    新参数 req=356:  420 -> 30px  440 -> 30px(余量 22)

### 实测

    新参数: grid req 356, 440 下可用 378, 余量 22px
    保存键: winfo_height 30 == winfo_reqheight 30(满血)
    横向:   436 可用, 键名列 82 + Entry 74 = 156, 余 280(无需改)

### 测试

新增 `SettingsWindowFitsItsOwnFrame` 两条：
- `test_the_save_button_is_not_squeezed_flat`：按 `_settings_build` 的
  真实结构建窗，geometry 从源码里正则抓（改窗高测试会跟着走），断言
  `winfo_height() == winfo_reqheight()`，且 grid 请求高低于预算。
- `test_the_old_geometry_squeezed_the_button_flat`：反面对照，老参数
  在 420..460 五档窗高下都必须被压扁 —— 不然上面那条可能只是 Tk
  心情好。

全套 **649 条**（647 + 2 新），两个 runner 均全绿。

## 147. 续一百三十八：间距守卫自己留着 51 的旧值，另有一处偶发 WinError 1410（布局/测试卫生）

### 本轮审过但没缺陷的面（都量了）

- **汇总卡内边距**：Tk 实测盒高 58px，Web 按盒模型推 59.2px
  （span 14.4 + b margin-top 2 + b 22.8 + padding 9x2 + border 1x2）。
  差 1.2px，不值得动。
- **表格行高**：Tk `rowheight=28`，Web `td` 推 28.6px
  （13px 字号 linespace 15.6 + padding 6x2 + border 1）。差 0.6px。
- **趋势图左右内边距**：Tk 角标 `x=5`，Web `.thr{left:4px}`。差 1px。
- **筛选栏与表格间距**：Tk `pady=(0,4)`，Web `#q` 无 margin 但紧跟
  `.sparkwrap` 的 `margin:0 0 10px`。节奏接近。
- **托盘右键菜单 / `_detail_msg`**：前者菜单项行距由 Tk 定不可调，
  后者只改 `root.title` 不是弹窗，都没有行距可优。
- **表头高度 38px**：§141 已实测 `Treeview.Heading` 的 padding 从
  `(6,3)` 到 `(6,6)` 高度一个字节都不动，ttk 布局算法定死，不追。

### 改动：间距守卫里的旧值 51

§140 把 `_STATUS_H` 从 51 提到 52（底部两行行距 1px -> 2px），同节
的测试注释（`_STATUS_H 同步 51 -> 52`）记得这件事，但
`TrayPanelStatusFloor._budget()` 里 `("status", 51)` 和类文档字符串里
的"状态区 51 = 567, 缺口 97px"没跟着改：

    _BLOCKS = (("cards", 68), ("fbar", 32), ("tree", 370),
               ("spark", 46), ("status", 51))

方向上是安全的（少算 1px，守卫只会更严不会更松），但它是**常量漂移**：
下次谁再改 `_STATUS_H`，这个数照样不会响。

改法：`_budget()` 里的 51 换成 `gt._STATUS_H`，文档字符串的算式改成
52 / 568 / 98。另外 `_BLOCKS` 这个类属性**定义之后从没被任何方法
引用过**（`rg _BLOCKS` 只有定义处一行）—— 死数据正是漂移的温床，删掉。

### 顺带确认：WinError 1410 是偶发，不是本轮引入

`discover` runner 有一次报
`TraySingleInterpreter.setUp` -> `OSError: [WinError 1410] 类已存在`。
用 `git stash` 回到干净 HEAD 复跑：同类单独跑 19 条全绿、整套
discover 也全绿；恢复改动后再跑整套照样全绿。结论是 pystray 托盘
图标类注册的偶发冲突，与改动无关，不留债。

### 测试

无新增用例（改的是测试自己的陈旧数据）。全套 **649 条**，两个 runner
均全绿；`WinError 1410` 复现两次、干净 HEAD 上一次，已定位为偶发。

## 148. 续一百三十九：设置窗行距从 25px 回到 27px，间距守卫改为从源码抓参数（布局/可读性）

### 现象

§146 为了把保存键从 1px 救回来，把设置窗 7 处 `pady` 从 2 砍到 1、
窗高只提到 440。按键是救回来了，但行距跟着塌了：

    pady=1 -> 行距 25px（Entry 21 + 上下各 2）
    pady=2 -> 行距 27px（Entry 21 + 上下各 3）

字号 9 的 linespace 实测 17px，25px 行距意味着行与行之间只剩 4px
空隙，而同一块界面上的主面板表格行是 28px。设置窗是用户要来改
数字的地方，六行输入连着看，25px 偏挤。

### 改动

用高度换行距，宽度一分不加：

- `460x440` -> `460x460`（+20px）。
- 7 处 `pady=1` -> `pady=2`。
- 两个名单 `Text` 仍保持 `height=3`。恢复到 4 要 470+ 且余量只剩
  2px，太贴，不值。

实测（460 宽下）：

    460 + pady=2 + 3 行:  req 380 / avail 398 / 余量 18 / 按钮 30px
    460 + pady=1 + 3 行:  req 356 / avail 398 / 余量 42 / 按钮 30px
    460 + pady=2 + 4 行:  req 406 / avail 398 / 缺口 -8 / 按钮 22px

### 顺带修：间距守卫自己写死了参数

`SettingsWindowFitsItsOwnFrame._build(geom, pad=1, texth=3)` 的默认值
是 §146 那会儿的参数。geometry 早就从源码正则抓了，pady 和 Text
高度却写死 —— 本轮把 pady 改回 2，测试替身还按 1 建窗，于是
**测的是改之前的布局，绿灯常亮什么也没守**。

新增 `_real_params()`：geometry、pady、Text 高度三个全部从
`inspect.getsource(_settings_build)` 抓，任一字面量变了测试就跟着走。

变异验证：把源码里 7 处 `pady=2` 改成 `pady=9`，正测试当场报
`AssertionError: 1 != 30 : 保存键只要 1px` —— 守卫确实在守参数，
不是空转。

### 测试

无新增用例数（改的是既有两条守卫的取参方式）。全套 **649 条**，两个
runner 均全绿。

`WinError 1410` 本轮又撞到一次（discover runner），已连续两轮用
`git stash` 回干净 HEAD 复跑定位为 pystray 图标类注册的偶发冲突，
与改动无关；复跑一次即全绿。

## 149. 续一百四十：区块间距提取为 _BLOCK_GAP，三处裸 4px 收成一个数（布局/可维护性）

### 本轮量过、确认不需要改的

- **详情窗行距**：9 行键值对，行距实测 **27px**（Label 23 + 上下各 2），
  与上轮调到的设置窗 27px 一致，与主面板表格行 28px 差 1px。无需动。
- **主面板区块间距**：`cards->fbar` 4 / `fbar->tree` 8(含 pady 4+4) /
  `tree->spark` 4 / `spark->bottom` 10(`_SPARK_GAP`)。
- **把区块间距从 4 提到 6/8**：算过，不行。590 高度下固定部分 208，
  表格 12 行要 374，余量只有 8px。提到 6 吃掉 6px 只剩 2px，`_PANEL_MIN_H`
  的守卫会当场红。收益小风险大，不做。
- **Web `.cards{margin:10px 0 12px}` 的 12px 下边距**：Web 那边区块顺序是
  卡片 -> 趋势图 -> 筛选框 -> 表格，与托盘的 卡片 -> 筛选栏 -> 表格 ->
  趋势图不同，没有一一对应的 margin 可对齐，硬套数字没有意义。

### 改动：区块间距常量化

`_panel_build` 里三处区块间距是散落的裸 `4`：

    cards.pack(fill="x", padx=8, pady=(8, 4))
    fbar.pack(fill="x", padx=8, pady=(0, 4))
    tree.pack(fill="both", expand=True, padx=6, pady=4)

而间距这件事在这个文件里本来就有既定做法：`_SPARK_GAP` / `_CARD_LABEL_GAP`
/ `_STATUS_LINE_GAP` 每个都是命名常量 + 注释说明"与 Web 面板哪个值对齐"。
这三处是漏网的。后果是改一处不牵动另外两处，区块节奏悄悄歪掉；
`_budget()` 测试里也跟着写死了同一个数（§147 刚修过 `status 51` 那类）。

- 新增 `_BLOCK_GAP = 4`（`gpu_tray.py:276`），注释说明为什么是统一值
  而不是对齐 Web。
- 三处 pack 全部改用 `_BLOCK_GAP`。
- `_budget()` 里 `8 + 56 + 4` / `28 + 4` 改成 `gt._BLOCK_GAP`。

### 测试

`TrayPanelStatusFloor` 新增
`test_the_block_gap_is_one_number_used_in_all_three_places`：常量必须
存在，且 `_panel_build` 源码里 `_BLOCK_GAP` 正好出现 3 次、三处 pack
写法逐一在列。

变异验证：把 tree 那处改回裸 `pady=4`，守卫当场报
`AssertionError: 2 != 3 : 区块间距有三处, 必须都用 _BLOCK_GAP`。

全套 **650 条**（649 + 1 新），两个 runner 均全绿。

`WinError 1410` 本轮第四次撞到（discover runner），复跑一次即全绿；
前三轮已用 `git stash` 回干净 HEAD 定位为 pystray 图标类注册的偶发冲突。

## 150. 续一百四十一：区块左右内边距是三套值（8/6/0），表格比汇总卡宽出 2px（布局）

### 现象

本轮改审横向（此前只审过纵向）。量 pack 之后的 `winfo_x`：

    改前:  cards x=8 w=1030   fbar x=8 w=1030
           tree  x=6 w=1034   spark x=6 w=1034   bottom x=0 w=1046

表格和趋势图是 `padx=6`，汇总卡和筛选栏是 `padx=8` —— 表格比卡片
**左右各宽出 2px**，四个区块的左右边缘不在同一条竖线上。底部状态区
又是 0（贴窗沿，靠里面两行 Label 各自 `padx=8` 补）。

三套值全是早期提交带进来的（`git log -S` 追到 8693c2c / f443408），
没有任何注释说明为什么该不一样。

Web 面板那边不存在这个问题：`body{padding:16px 20px}` 是**所有区块
共用的一个外层内边距**，左右边缘天然对齐。

### 改动

- 新增 `_PANEL_PAD = 8`（`gpu_tray.py:277`），注释说明它对应 Web 的
  `body{padding:...20px}`、以及为什么这个内边距不构成宽度约束
  （表格列宽合计 722px，而窗口可任意拖窄，表格自带横向滚动条）。
- `cards` / `fbar` / `tree` / `spark_box` 的 `padx` 全部换成它；
  底部两行 Label 的 `padx=8` 也换成它。
- `bottom` 本身保持 `padx=0` 并补注释：里面两行 Label 已各带一份，
  外层再来一次就成了 16px，比其他区块多缩进一半。
- 筛选框与 hint 之间的 `entry.pack(padx=6)` **不动** —— 那是元素
  间距，不是区块内边距。

实测改后：

    改后:  cards/fbar/tree/spark  x=8  right_edge=1038(四个全等)
           底部 Label            x=8

### 测试

`TrayPanelStatusFloor` 新增两条：
- `test_every_block_shares_one_left_and_right_edge`：真建 Tk 根、按
  `_panel_build` 同样的 pack 方式搭四个区块，量 `winfo_x` 与右边缘，
  各自必须只有一个值（不查源码，量真渲染）。
- `test_the_panel_pad_is_one_number_used_everywhere`：常量必须存在，
  `_panel_build` 里 `padx=_PANEL_PAD` 至少 5 处，并逐处点名
  `tree.pack(...padx=_PANEL_PAD` 与 `spark_box.pack(...padx=_PANEL_PAD`
  —— 点名是因为注释里也提到 `_PANEL_PAD`，光数次数会被注释凑够
  （第一版就踩了这个：阈值写 6，被注释凑成 6，变异没被抓到）。

变异验证：把 tree 那处改回裸 `padx=6`，点名断言当场报
`tree.pack(fill="both", expand=True, padx=_PANEL_PAD not found`。

全套 **652 条**（650 + 2 新），两个 runner 均全绿。

## 151. 续一百四十二：汇总卡左右内边距 9px vs Web 16px，标签贴着卡边（布局）

### 现象

本轮审卡片内部。Web 面板 `.card{padding:9px 16px}` —— 上下 9、
**左右 16**。托盘这边上下那半边早就按 9px 对齐了（`pady=(5, ...)`
和 `(0, 5 - ...)`），只有左右还是 `padx=9`：

    Tk:  上下 9(对齐) / 左右 9
    Web: 上下 9(对齐) / 左右 16

结果是托盘卡片里的标签和数字贴着卡的左右边缘，比浏览器里挤。

横向余量本来很足：六张卡 `expand=True` 等分一行，1046 宽下每张
165px；实测最宽的文字是标签"GPU 进程"（53px），`padx=16` 时内容
宽仍有 133px。

### 改动

- 新增 `_CARD_PAD_X = 16`（`gpu_tray.py:288`），注释写清算式与
  最窄窗口的验算。
- 卡片里标签与数值两个 Label 的 `padx=9` 换成它。

窄窗验算（每张卡 = (W - 16 - 5x8) / 6）：

    W=1046  卡 165  内容 133  最宽文字 53
    W= 722  卡 111  内容  79
    W= 650  卡  99  内容  67

650 也放得下，而这个宽度已经窄过任何实用场景。

### 测试

`TrayPanelStatusFloor` 新增两条：
- `test_the_card_side_padding_matches_the_web_panel`：常量存在、
  正好两处用它，且与 Web `.card` 的 padding 左右值逐数相等。
- `test_the_card_text_still_fits_at_the_narrowest_window`：真渲染。
  按 `_panel_build` 同样的 pack 方式搭卡片，四个窗口宽度（1046/900/
  722/650）× 空快照 + 满载快照两轮，逐卡逐 Label 断言
  `文字宽 + 2*内边距 <= 卡宽`。

### 这条测试踩了三个坑，都记下来

1. **`Label.winfo_reqwidth()` 不含自己的 padx**。第一版直接拿它和
   卡宽比，等于没比 —— 必须自己把两边内边距加回去。
2. **拿 `panel_summary({})` 的值去量，量不出东西**。空快照的值全是
   `0.0%` / `0 MiB`，最宽 34px；而真实场景里最宽的是标签
   `"GPU 进程"`（53px）和值 `"120 MiB"`（48px）。补一个满载快照
   才量得到。
3. **替身比真布局还挤**。第一版把最宽的那个值塞进每一张卡，最后
   一张卡被挤成 1px —— 量出来的数没有意义。改成按真实数据逐卡填
   自己的标签和值。

变异验证（前两版都漏了，第三版才抓住）：`_CARD_PAD_X = 16` 改成 40，
测试报 `窗口 722px 下卡片只有 130px, 文字 'GPU 进程' 加两边内边距
要 139px`。

全套 **654 条**（652 + 2 新），两个 runner 均全绿。

## 152. 续一百四十三：筛选栏输入框比按钮矮 9px，三者底边不齐（布局）

### 现象

本轮审筛选栏（`fbar`）内部。Tk 的 `pack` 默认按**各自请求高度顶对齐**，
而这一行三个控件请求的高度都不一样：

    控件        请求高    实测 y/h        底边
    按钮        30px      y=0  h=30      30
    Label       23px      y=3  h=23      26
    Entry       21px      y=4  h=21      25

输入框比按钮矮 **9px**、底边差 **5px** —— 这一行看着是歪的：输入框
明显比右边两个按钮短一截，且底部不落在同一条线上。

### 改动

`entry.pack(side="left", padx=6)` -> `..., fill="y")`。

试过并否掉的办法：`pady=(0, 9)`。Tk 里 Entry 的 `pady` 是**内部**
padding，不是外部偏移 —— 加上之后 Entry 底部从 25 变成 21，更糟。

`fill="y"` 让 Entry 拉满这一行的 30px，三者底边齐（实测 entry
`y=0/h=30`）。Tk 会把 Entry 的文字垂直居中在额外空间里，视觉正常
—— Windows 的搜索框本来也就是 30px 上下。

### 测试

`TrayPanelStatusFloor` 新增
`test_the_filter_row_controls_share_one_baseline`：

1. 先查 `_panel_build` 源码里那行必须带 `fill="y"`（防回归，改回去
   就红）。
2. 再按同样的 pack 方式真渲染一遍，量 `entry` / `pin` / `srt` 三者的
   `winfo_y() + winfo_height()`，必须只有一个值；且 entry 与 pin 的
   `winfo_height()` 相等。

变异验证：去掉 `fill="y"`，守卫报
`筛选输入框没有 fill="y": 它比按钮矮 9px, 底边差 5px, 这一行是歪的`。

### 过程中出的两次事故（都记下来）

1. 删一个**未使用的**常量 `_CTRL_ROW_H` 时，补丁脚本的字符串匹配把
   上一轮的 `_CARD_PAD_X` 注释块和定义一起删了 —— 两处引用还在，
   跑起来就是 `NameError`。是 `TrayPanelStatusFloor` 当场红的
   （`module 'gpu_tray' has no attribute '_CARD_PAD_X'`），不是靠眼看。
2. 恢复时又插重了一份、且插错了位置（夹在 `_CARD_LABEL_GAP` 的注释和
   定义之间）。最后 `git checkout gpu_tray.py` 整体还原，只重新打
   `fill="y"` 这一处补丁，diff 才干净。

教训：**删/改常量之后必须看 `git diff`**，这次两次都是 diff 一眼能看穿、
而我只看了测试结果。

全套 **655 条**（654 + 1 新），两个 runner 均全绿。

## 153. 续一百四十四：表格行高 28 -> 32，文字上下留白 5.5px 提到 7.5px（布局/可读性）

### 现象

本轮量表格行内留白。字号 9 的 linespace 实测 **17px**，而
`rowheight=28`：

    (28 - 17) / 2 = 5.5px  文字上下留白

Win11 Fluent 的列表/表格行在这个字号下要高得多（任务管理器约 32px），
而托盘这张表**是要盯着看的** —— 12 行挤在 5.5px 留白里，行与行几乎
糊在一起。

### 改动

三处一起改，缺一不可：

- 新增 `_ROW_H = 32`（`gpu_tray.py:288`），`Treeview` 的 `rowheight`
  换成它。
- `_PANEL_MIN_H` 590 -> 630。12 行从 336 涨到 384（+48），不抬高
  保底高度的话 pack 会反过来把趋势图和底部状态行压扁 —— 那正是
  `_PANEL_MIN_H` 存在的理由。
- `_onscreen_geometry` 的 `default="1040x590"` 改成从 `_PANEL_MIN_H`
  拼：这个默认值此前写死，`_PANEL_MIN_H` 涨了它不涨，退回默认布局时
  开的还是旧高度的窗口。

窗口位置不变（`_onscreen_geometry` 只提高度、不动位置）。768 高屏幕上
y=55 时底部到 685，距屏底还有 83px，放得下。

### 顺带修掉的漂移（第三处同类）

`TrayPanelGeometryGuard` 里有五处 `1040x590` / `1020x590` 写死期望值，
全是 `_PANEL_MIN_H` 的旧值。全部改成 `"1040x%d" % gt._PANEL_MIN_H`。
`_budget()` 里的 `12 * 28` 换成 `12 * gt._ROW_H`。

这已经是 §147（`status 51`）之后第三处同类漂移 —— 间距/尺寸常量改了，
测试里的字面量不跟着变。

### 测试

`TrayPanelStatusFloor` 新增两条：

- `test_the_row_height_and_the_window_floor_move_together`：把
  `_PANEL_MIN_H >= 全部区块请求高之和` 这个等式钉死。行高以后再怎么改，
  忘了抬保底高度当场红。
- `test_the_row_height_leaves_room_for_the_text`：行高必须比 linespace
  高出 4px 以上（真建 Tk 根量 `TkDefaultFont` 的 linespace）。

变异验证：`_ROW_H` 32 -> 48，第一条报
`保底高度 630 装不下全部区块(要 814): 行高 48 与 _PANEL_MIN_H 脱节了, 
趋势图/状态行会被压扁`。

全套 **657 条**（655 + 2 新），两个 runner 均全绿（discover 复跑两次，
中途撞到一次偶发 error，再跑即绿）。

## 154. 续一百四十五：保底高度 630 少算 4px，底部状态区被 pack 压成 48px（布局）

### 现象

接着 §153 往下量。建出**真实面板**（不是手搭的替身：`TrayApp` 要
`stop` / `panel_open` / `lock` / `_pref_cache` / `_name_cache` 这些
属性都补齐才建得起来，`_panel_build` 里总 except 会把缺属性的窗口
静默 destroy 掉），逐块量高度：

    min_h=630  卡片 56  筛选栏 28  表格 418  趋势图 46  状态区 **48**
    min_h=634  卡片 56  筛选栏 28  表格 418  趋势图 46  状态区 **52**

底部那个 `Frame(height=_STATUS_H)` 实测 **48px，而需求是 52px** ——
第二行"会话累计"的下半截被切掉。`_STATUS_H` 注释里写的 52px 一个
字节都没兑现。

### 为什么是缺陷

`_PANEL_MIN_H` 存在的全部理由就是"低于这个高度 pack 会压扁趋势图
和底部两行"（`TrayPanelStatusFloor` 的契约）。它自己的账面上却
少算了 4px，把**唯一不允许被压扁的那个区块**压掉了。

### 那 4px 从哪来

两处，各 2px，方向都是"账面比实测小"：

1. **表头不是 30px，是 32px**。`_budget()` 和
   `test_the_row_height_and_the_window_floor_move_together` 里都写着
   `12 * _ROW_H + 30`，而 `_PANEL_MIN_H` 的注释写的是"表头 38"。
   实测（clam 主题生效后）：表头高 = `22 + 2 * padding_y`，
   `padding=(6,5)` 时是 **32px**。
2. **ttk.Treeview 在 12 行全展开时请求 418px**，不是账面算的
   `12*32 + 32 = 416`，多出 2px。

### 顺带纠正两条错记录

**§141 / §147 记的"表头定死 38px、padding 改动无效"是错的。**
实测改 `Treeview.Heading` 的 padding，表头高度**会**变：

    padding_y: 0 -> 22, 1 -> 24, 3 -> 28, 5 -> 32, 8 -> 38

38px 正是 `padding_y=8` 的值 —— 当初量的那次 clam 主题没生效，
拿到的是 ttk 默认值。本轮已把 §141/§147 与测试 docstring 里这句
错的记录改成实测真相。

### 改动

- `_PANEL_MIN_H` 630 -> **634**。实测 634 时状态区正好 52px，
  700/900/660 等更高的窗口不动。
- 新增 `_TREE_HEAD_H = 32`（表头高）和 `_HEAD_PAD_X` / `_HEAD_PAD_Y`
  （heading 的 padding，原来是 `_apply_fluent_theme` 里的裸 `(6, 5)`）。
  `Treeview.Heading` 改引用这两个常量。
- `_budget()` 与 `test_the_row_height_and_the_window_floor_move_
  together` 里的 `12 * _ROW_H + 30` 换成
  `12 * _ROW_H + gt._TREE_HEAD_H + 2`，账面与实测对齐。

### 测试

新增 `test_the_window_floor_covers_the_table_header`：真建 Tk 根、
真刷主题，量一遍表头实际高度，同时钉死 `_TREE_HEAD_H == 22 + 2*
_HEAD_PAD_Y`。改 padding 忘了改 `_TREE_HEAD_H`、或者改了表头常量
忘了抬 `_PANEL_MIN_H`，两条路都当场红。

变异验证（两条都确认会红）：

- `_HEAD_PAD_Y` 5 -> 8：`AssertionError: 32 != 38 : _TREE_HEAD_H 32
  与 _HEAD_PAD_Y 8 对不上: 表头实际是 38px`。
- `_PANEL_MIN_H` 634 -> 630（本轮的原始缺陷）：`1046x634+55+55`
  期望不符，`test_a_short_window_is_raised_to_the_floor` 报红。

全套 **658 条**（657 + 1 新），两个 runner 均全绿。

### 用户决策待办

1. 现网 exe 仍是旧构建(10-04)，且**默认窗口高度从 630 涨到 634**、
   表格行高 28 -> 32，不同步的话用户看不到这些优化。要同步需
   build -> sync -> 重启托盘（会打断实例）。
2. 32 条新提交未 push, 与 origin/main 分叉。

## 155. 续一百四十六：设置窗/详情窗行距与主面板对不上，统一成一把尺（布局）

### 现象

§154 修完保底高度之后，把三个窗口的行距放在一起量：

    主面板表格行        32px
    设置窗输入行        25px  (裸 pady=2 + Entry 21)
    设置窗勾选行        31px  (裸 pady=2 + Checkbutton 27)
    设置窗名单段        47px  (裸 pady=2 + Text 43)
    详情窗 8 行          27px  (裸 pady=2 + Label 23)

四个数字四种密度，而设置窗内部输入行与勾选行就差 6px。

### 为什么是缺陷

用户在三个窗之间来回切(盯着面板 -> 点开详情看路径 -> 改阈值)，
疏密感对不上。代码里没有任何东西保证它们一致，也没有常量可改
—— pady=2 是散在设置窗六处、详情窗两处的裸数字。

### 根因

同一个 pady 加在不同自然高度的控件上，给不出统一行距。实测控件
自然高度：Label 23 / Entry 21 / Checkbutton 27 / 名单 Text 43。

还有一个容易漏的坑：grid 里相邻两行的间距 = 上一行的 pady 下 +
下一行的 pady 上。所以 pad 必须上下相等，否则交界处的和会在两个
值之间跳(实测输入行 -> 勾选行在 29 与 33 之间交替)。

### 改动

- 新增 `_FORM_ROW_PITCH = 31`：表单行的目标行距。取**奇数** 31 而
  不是 32：Label 23 与 Checkbutton 27 都是奇数高，`(31-23)` 和
  `(31-27)` 才是偶数，两种控件都补得出整数 pad；取 32 时有一个要
  四舍五入，行距立刻裂成两个值。
- 新增 `_row_pad(widget_h)`：按控件高度反推上下留白，
  `(pitch - h) // 2` 上下各一份。控件比 pitch 还高时返回 `(0, 0)`
  (名单 Text 三行 43px，压不动也不该压)。
- 新增 `_ctl_h(master, cls)`：量控件在当前字体/主题下的自然高度。
  **master 必须由调用方传**：自己 `tk.Tk()` 会打破
  `TraySingleInterpreter` 钉死的"整个进程只允许一个 Tcl 解释器"
  (第一版就是这么写的，当场红了两条)。
- 设置窗 `pady=2` 六处 -> `_row_pad(...)`；窗高 460 -> 520
  (行距 25/31 提到 31 之后 grid 要多占 30px)。
- 详情窗 `pady=2` 两处 -> `_row_pad(...)`；窗高 300 -> 320
  (reqheight 310 > 300，底边那行会被裁掉一半)。

### 实测结果

    设置窗: 31,31,31,31,31, [29], 31,31,31, 31, 47
    详情窗: 31,31,31,31,31,31,31,31

输入行/勾选行/详情行全部 31px；跨控件那道交界(输入 -> 勾选)
29px，差 2px，是整数像素下能做到的最好结果；名单段 47px 是三行
文本自身的高度。极差从 22px(25~47) 收到 18px(29~47)。

与主面板表格 `_ROW_H=32` 差 1px：那个数由文字留白定
(17px linespace + 上下各 7.5)，动它会挤文字，所以这里迁就它。

### 测试

- 新增 `test_the_three_windows_share_one_row_pitch`：钉死
  `_FORM_ROW_PITCH` 是正奇数、`_row_pad` 对三种控件都给整数且上下
  对称、pad*2+h 正好等于 pitch、超高控件 pad 为 0、与 `_ROW_H`
  相差不超过 1。
- `SettingsWindowFitsItsOwnFrame` 替身改为用 `gt._row_pad` 现算
  pad(原来从源码正则抓 `pady=(\d+)` 字面量，源码改成函数调用后
  抓到的是名单段那个 2，**测错了对象**)。
- `DetailPrefRefresh.test_the_detail_wrap_fits_the_column_it_actually_gets`
  的 geometry 改为从源码抓，不再写死 520x300。

变异验证(都确认会红)：`_FORM_ROW_PITCH` 31 -> 30 / 32；
`_row_pad` 返回不对称的 `(half+1, half)`。

全套 **659 条**(658 + 1 新)，两个 runner 均全绿。

### 用户决策待办

1. 现网 exe 仍是旧构建(10-04)，且默认窗口高度从 630 涨到 634、
   表格行高 28 -> 32、设置窗 460x460 -> 460x520、详情窗 520x300
   -> 520x320，不同步的话用户看不到这些优化。要同步需
   build -> sync -> 重启托盘(会打断实例)。
2. 32 条新提交未 push, 与 origin/main 分叉。


## 156. 续一百四十七：筛选栏两个 Label 底边比按钮矮 3px, 漏量的正是它们(布局)

### 现象

§155 把三个窗口的行距统一成 `_FORM_ROW_PITCH` 之后, 主面板筛选栏
(`fbar`) 看着还是歪的: 左边"筛选:"和右边"显示 N/T 行 (还有 N 行未显示)"
两个 Label 的底边停在 25, 而 Entry 与两个按钮是 28。

守卫 `test_the_filter_row_controls_share_one_baseline` 一直在跑, 也一直绿 ——
因为它只把 `(entry, pin, srt)` 三个控件放进了 `bottoms` 集合。两个 Label
既没有 `fill="y"` 也不带任何 padding, 量都没人量。

### 为什么是缺陷

这一行的行高由最高的控件(按钮 28px)定, Entry 靠 `fill="y"` 被拉齐,
而 Label 两者都没有: pack 默认顶对齐, 它按自己请求的 22px 站到底。
一栏里三种高度, 疏密感与刚统一好的另外两个窗口又对不上了。

### 改动

- `gpu_tray.py` `_panel_build`: 新增 `_fbar_ipady`, 由本行最高控件
  (按钮 / Entry)与 Label 的自然高度差反推 —— 与设置窗/详情窗同一招
  (`_row_ipady`), 长在控件自己身上, 不依赖邻居。两个 Label 的 `pack`
  都带上它。实测五个控件全部 28px、底边全齐, `fbar` 自身高度从 34
  回落到 28, 省下的 6px 归给表格。
- `tests/test_gpu_monitor.py`:
  - `bottoms` / `heights` 集合从三个控件扩到五个, 两个 Label 进场;
  - 新增源码断言: 两个 Label 的 `pack` 必须带 `ipady=` 且不带 `pady=`。
    取完整调用不能靠正则 —— `hint.pack` 跨两行, 且参数里有
    `padx=(6, 0)` 这种一层嵌套括号, `[^)]*` 会被提前截断。用
    `((?:[^()]|\([^()]*\))*)` 这支"括号配一层"的模式;
  - 替身窗先跑 `_rebuild_fonts()` + `_apply_fluent_theme()`: 默认字体下
    Label 23 / 按钮 30, 拿这组数算 `ipady` 补出来是 29px, 与真实面板
    (28)差 1px —— 不装运行期字体的话, 这条用例守的是个假象。

### 踩坑

- 补丁脚本按行号替换时 off-by-one, 在 `finally` 里留了两句
  `root.destroy()`, 报 `TclError: application has been destroyed`。
  替换区间是**左闭右开**, 且 `lines[i-1]` 才是第 i 行。
- `HANDOFF.md` 是 CRLF + 约 680 个裸 LF 混排, `split("\r\n")` 会漏掉
  一半行。用 `splitlines()` 拆、再按原分隔符拼回去。

### 测试

- 新增一条(`test_the_filter_row_controls_share_one_baseline` 扩写),
  全套 **660 条**(659 + 1), 两个 runner 均全绿。
- 变异验证: 分别去掉 `_filter_hint` 与 `hint` 的 `ipady=`, 测试均红,
  且报错信息准确指向对应控件(`'ipady=' not found in 'side="left"'`)。

### 用户决策待办

1. 现网 exe 仍是旧构建(10-04)，且默认窗口高度从 630 涨到 634、
   表格行高 28 -> 32、设置窗 460x460 -> 460x520、详情窗 520x300
   -> 520x320、筛选栏 34 -> 28，不同步的话用户看不到这些优化。要同步需
   build -> sync -> 重启托盘(会打断实例)。
2. 34 条新提交未 push, 与 origin/main 分叉。


## 157. 续一百四十八：筛选栏与表格之间差 4px, 状态区两行被默认字体撑爆 2px(布局)

### 现象(两处, 同一个根因)

§156 修完筛选栏自身的控件底边之后, 整栏看着还是不够匀。量出来是两
处, 而且根因是同一条: pack/grid 的相邻间距会**相加**, 而少算的那
几个像素总是从最末尾那个不许被压缩的部件身上扣。

**一、区块间距 4/8/4 不匀。** 汇总卡->筛选栏 4px、筛选栏->表格
8px、表格->趋势图 4px。表格原来写裸 `pady=_BLOCK_GAP`(上下各 4),
与筛选栏底部的 4 叠成 8 —— 实测筛选栏底边 96, 表格顶边 104。
§155 在 grid 上为同一件事把 pady 整个换成了 ipady, 但 pack 上从来
没人量过这道间隙。

**二、底部状态区第二行被压掉 2px。** `bottom` 是
`pack_propagate(False)` + `height=_STATUS_H=52` 的保底 Frame, 而两行
Label 的真实请求是 23 + 2 + 23 + 6 = 54 > 52: pack 从最后声明的那个
slave 身上扣, 第二行实测被压成 21px, 文字上下各削 1px。

### 为什么是缺陷

第二处的直接诱因是字体: 这两行 Label 是**全面板唯一没带 `font=` 的
文本**, 落在 `TkDefaultFont`(Microsoft YaHei UI)上。两个后果 ——
一是与同窗其余文字(Segoe UI Variable Text)不是一张脸; 二是那个字体
的 Label 行高 23px, 而 `_STATUS_H` 的账按 22px 算(注释写着"字号 9 的
linespace 是 16, 上下各留 3"), 少算的 1px x 2 行正好把第二行吃掉。

而守卫 `test_the_status_floor_is_taller_than_its_two_labels` 原来写死
`22 + 1 + 22 = 45`, 松得能放过任何少算 —— 所以它一直绿灯, 第二行也
一直是被压扁的。这与 §154(`_PANEL_MIN_H` 630 少算 4px)是同一类:
**保底常数的账面对不上真实请求, 而断言比账面还松。**

### 改动

- `gpu_tray.py`:
  - `tree.pack` 的 `pady=_BLOCK_GAP` -> `pady=(0, _BLOCK_GAP)`。约定:
    每个区块只声明自己的下边距, 上边距一律 0(汇总卡顶部那 8 是窗口
    上沿, 不是区块间距)。表格因此多出 4px 余量, 由 `expand=True`
    吃掉, 窗口总高仍是 634。
  - `tip` / `count` 两个 Label 补 `font=_FONT_UI`。补完之后两行都是
    22px, `_STATUS_H=52` 不多不少, 常量本身不用动。
  - 底部那个裸 `6` 提成 `_STATUS_BOTTOM_PAD`, 与 `_STATUS_LINE_GAP`
    同档 —— 测试要能把这笔账算出来, 就不能有说不出名字的数。
- `tests/test_gpu_monitor.py`:
  - 状态区保底改为按 `_FONT_UI` 现量一个真 Label 的 `reqheight`, 断言
    `_STATUS_H` **恰好等于** 行高 + 行间 + 行高 + 底部(不再用 45)。
  - `_budget()` 补上表格自己的下边距那 4px(原来漏了, 保底高度的账面
    比真实需求少 4px)。
  - 区块间距: 新增真渲染量三处间隙的用例; `_BLOCK_GAP` 那条改为逐处
    点名 `pady` 形状, 并禁止裸 `pady=_BLOCK_GAP` 复发。
  - 新增"状态两行必须用 `_FONT_UI`"; 原 `_MUTED` 色契约改为按名字抓
    调用而不是匹配整行字面量(一改参数就红的断言没人会去改参数)。

### 实测结果

- 区块间距: 汇总卡->筛选栏 4、筛选栏->表格 4、表格->趋势图 4,
  趋势图->状态区 10(`_SPARK_GAP`)。
- 状态两行: 均 22px, `reqheight` 也是 22, 字体 `{Segoe UI Variable
  Text} 9`。
- 全面板文本字体只剩三种: `_FONT_UI` / `_FONT_UI_BOLD` / `_FONT_NUM`
  (Entry 的数字列用 Consolas 12, 那是列对齐需要的)。

### 测试

- 全套 **662 条**(660 + 2 新), 两个 runner 均全绿。
- 变异验证(三条都确认会红): tree 的 `pady` 退回裸 `_BLOCK_GAP`;
  去掉 `tip` 的 `font=_FONT_UI`(同时红两条: 新守卫 + muted 色契约);
  `_STATUS_H` 改回 50。

### 顺带发现(未改, 记档)

设置窗与详情窗**一个 `font=` 都没传**, 全部落在 `TkDefaultFont` /
`TkTextFont` / `TkFixedFont 上; 主面板则全套 `_FONT_UI`。两个窗口
三种字体。改它会把 `_FORM_ROW_PITCH=31` 的标定全推翻(那个数是按
Label 23 / Checkbutton 27 量的), 窗口高度与几何断言都要跟着重算,
超出"行距"这一轮的范畴, 留作独立一轮。

### 用户决策待办

1. 现网 exe 仍是旧构建(10-04)，且默认窗口高度从 630 涨到 634、
   表格行高 28 -> 32、设置窗 460x460 -> 460x520、详情窗 520x300
   -> 520x320、筛选栏 34 -> 28，不同步的话用户看不到这些优化。要同步需
   build -> sync -> 重启托盘(会打断实例)。
2. 34 条新提交未 push, 与 origin/main 分叉。
## 158. 续一百四十九：设置窗/详情窗一个 font= 都不带, 行距尺子只能取奇数 31(布局)

§157 结尾记的档: 设置窗与详情窗**一个 `font=` 都没传**, 全部落在
`TkDefaultFont` 上, 而主面板全套 `_FONT_UI`。两个窗口三种字体。本轮
把它收掉, 顺手把 `_FORM_ROW_PITCH` 从 31 改成 32 —— 31 之所以是奇数,
就是因为这两个窗的控件没带字体。

### 为什么是缺陷

不是"看着不齐"这种观感问题, 是一条**算不出来的账**:

- 行距尺子 `_FORM_ROW_PITCH` 与表格行高 `_ROW_H` 差 1px。用户在三个窗
  之间来回切, 疏密感对不上, 而代码里没有任何东西保证它们一致。
- 这两个窗内部自己也不统一: 输入行 25px(裸 `pady=2` + Entry 21)、
  勾选行 31px, 差 6px。

### 改动

- `gpu_tray.py`:
  - `_FORM_ROW_PITCH = 31` -> `32`, 与 `_ROW_H` 相等。换上 `_FONT_UI`
    之后控件自然高度变成 Label 22 / Entry 20 / Checkbutton 26 /
    Button 28, 全是偶数, 32 能给每种控件都补出整数 ipady:
    22+2*5 / 20+2*6 / 26+2*3 / 28+2*2 全都正好 32。取偶数的理由
    (奇数高度)跟着消失。
  - 两个窗的 13 处文本控件全部补 `font=_FONT_UI`, **含 `_ctl_h` 探针**
    —— 探针量的是当前字体下的自然高度, 与控件本身带同一个字体, 两边
    不一致的话算出来的 ipady 补不出目标行距。
  - 详情窗窗高改成按内容现算, `_DETAIL_MIN_H=320` 只当下限(见下)。
- `tests/test_gpu_monitor.py`: 见"测试"。

### 详情窗窗高: 又一个"保底尺寸对不上真实请求"

改完字体、行距提到 32 之后量 `gone` 分支(进程已退出, 多插一行"注意",
折行的长路径还占两行): 9 行实测 reqheight **364**, 而窗高写死 320。
pack 从后声明的按钮行身上扣 —— 三个按钮实测只剩 **1px** 高, 用户看得见
字点不到键。与 §154(主面板 470)、§157(设置窗 460)是同一个坑。

改成按内容现算, 但有个坑: **必须循环到量稳为止**。第一遍
`update_idletasks()` 之后 Tk 才把折行那一行算完, `winfo_reqheight()`
当场从 316 长到 364; 只量一遍会按旧值设窗高, 按钮照样被压平。第二遍
之后就不再变了, 循环三次足够。常规形态 316 < 320, 一个像素都不涨。

### 实测结果

- 设置窗: 6 输入行 / 4 勾选行 / 2 名单行全部 32px(探针 reqheight 分别
  22 / 20 / 26), 名单 Text 52px, 保存键 28px 满血, 窗内容 484 < 520。
- 详情窗: 8 行全 32px, 3 个按钮 28px, reqheight 316。
- 详情窗 `gone` 分支: 窗高 364, 按钮行仍 28px 满血, 最后一行底边 304
  在窗内。

### 测试

- 全套 **664 条**(662 + 2 新), 两个 runner 均全绿。
- `SettingsWindowFitsItsOwnFrame._build` 替身补 `font=_FONT_UI` 并**先
  跑 `_rebuild_fonts()`**: 不装字体的话替身按 Label 23 算 ipady, 量的
  是改之前的布局, 绿灯常亮 —— 与 §155 替身不跟着改是同一类坑。
- `test_the_three_windows_share_one_row_pitch`: 奇数 -> 偶数; 控件高度
  改为按 `_FONT_UI` **现量**(不再写死 23/21/27)并断言其为偶数; 表格
  行高从"允许差 1px"改成**必须相等**。
- 新增 `test_both_form_windows_set_their_own_font`: 括号配对抓两个窗的
  全部 13 处构造调用, 逐个查 `font=_FONT_UI`。防回退。
- 新增 `test_the_detail_window_grows_for_the_gone_row`: 8 行的账 316
  装得进下限、gone 的账 354 装不进, 且源码必须按量出来的高度设
  geometry。
- 变异验证五条全红: 去掉保存键的 `font=` / `pitch` 改回 31 / 详情窗
  写死 geometry / 去掉键名 Label 的 `font=` / `_DETAIL_MIN_H` 降到 300。

### 踩过的坑(重要)

**全套单元测试从来不建真 `Toplevel`。** 我一开始给 gone 分支写了条真
渲染守卫(`app._detail_build(...)` + `root.update()`), 整套测试直接被
带崩: `Tcl_AsyncDelete: async handler deleted by the wrong thread`,
进程 abort, 连 "Ran N tests" 都打不出来。

最小复现: 真 `tk.Tk()` + 一个真 `Toplevel` + `update_idletasks()`,
后面接 `TraySingleInterpreter`(它要开线程跑 Tcl)就崩。换成纯
Frame + Label(不建 Toplevel)怎么跑都不崩。用 `noupdate` / `nobuild` /
`minimal` 三个变体逐一排除后确认: **建真 Toplevel 是因, 线程只是
放大器**。注意它是确定性的, 不是偶发竞态(连跑四次全崩)。

所以那条守卫改成不建真窗: 守"账算得对(8 行进得进下限、gone 进不去)
+ 代码真的按账设了窗高"。真渲染的结论由探针脚本另外给(见"实测结果")。

另一个坑: `_detail_build` 收尾的 `update_idletasks()` 不能只调一遍,
原因见上。

### 顺带发现(未改, 记档)

- `_FONT_NUM = ("Consolas", 12)` 只被主面板筛选 Entry 用(2 处), 本轮
  没动 —— 它是列对齐需要的, 不是表单行。
- 字体回退链是 `Segoe UI Variable Text` -> `Segoe UI` -> `Tahoma`
  (`_rebuild_fonts`)。本机第一个就有。若回退到 Tahoma 给出奇数高的
  Label, `_row_ipady` 会向下取整、行距当场差 1px ——
  `test_the_three_windows_share_one_row_pitch` 现量控件高度并断言其为
  偶数, 这条现在会被抓住。

### 用户决策待办

1. 现网 exe 仍是旧构建(10-04), 且默认窗口高度 630 -> 634、表格行高
   28 -> 32、设置窗 460x460 -> 460x520、详情窗 520x300 -> 520x320、
   筛选栏 34 -> 28、区块间距都看不到。不同步的话用户看不到这些优化。
   要同步需 build -> sync -> 重启托盘(会打断实例)。
2. 36 条新提交未 push, 与 origin/main 分叉。

## 159. 续一百五十：汇总卡纵向内边距 5/3 不对称, "总纵向 padding 恒定"装置把 9 吃成 5/3(布局)

用户本轮给的新材料: `[$rea-decompiler](...) 用反编译收费的项目来优化我们的项目。`

### 反编译选型与结论

本机唯一相关的**付费**硬件监控产品是 AIDA64 Engineer 5.20.3445 Beta
(FinalWire Ltd.), 位于 `D:\green\_硬件检测aida64engineer\`(目录里有
license.txt / pkey.txt / 序列号.txt, 确认是付费产品)。`rea --version` =
`rea 1.0.0`, CLI 用法 `rea <file>`, 报告写在目标旁边(`re-<name>.md` + `.pdf`),
本轮报告落在 `.tmptest/rea/`。

- `aida64.exe` 4.0MB 被 **UPX 加壳**(UPX0/UPX1/.rsrc, `UPX!` 1 处), 窗体
  数据压在压缩流里, `TFORM_*` 名字以 UTF-16LE 存在 `.rsrc` —— 解不出几何。
- `aida_uires.dll` 3.6MB 未加壳, 但只有 426 个 RCDATA(GAUGE 224 / IMAGE 158 /
  GAUGEICON 42 / DVCLAL / PACKAGEINFO), **没有窗体**。
- 突破口是 `aida_diskbench.dll`(1.1MB, **未加壳**), 含 `TFORM_DISKBENCH`,
  file offset `0x10e1fc`, size 32770。

**二进制 DFM 格式已完全解开**: `TPF0` 签名后是扁平序列, 每个对象
`[namelen:u8][classname][namelen:u8][objname]`, 随后属性
`[namelen][name][valuetype][value]`; `vaSet`(11) 以 `0x00` 收尾, 对象属性
列表也以 `0x00` 收尾。`TValueType`: 0 vaNull / 1 vaList / 2 vaInt8 /
3 vaInt16 / 4 vaInt32 / 5 vaExtended / 6 vaString / 7 vaIdent / 8 vaFalse /
9 vaTrue / 10 vaBinary / 11 vaSet / 12 vaLString / 13 vaNil /
14 vaCollection / 15 vaSingle / 16 vaCurrency / 17 vaDate / 18 vaWString /
19 vaInt64 / 20 vaUTF8String。解析器在 `.tmptest/rea/_dfm4.py`。

量到的 AIDA64 几何(本机, 96 DPI, `MS Sans Serif` 11px, TextHeight=13):

| 项 | 值 |
|---|---|
| 窗体 ClientWidth/Height | 790 / 503 |
| 内容区四边内边距 | **统一 7px**(TabControl Left=7 Top=7), 没有哪一边另算 |
| TLabel 自然高 | 13 |
| TEdit 自然高 | 21 |
| TButton 自然高 | 23 |
| 按钮↔按钮横向 gap | **6px**(Button_Start 7..82, Button_Stop 88) |
| 标签→数值 gap / 数值→下一标签 gap | 5 / 9(不对齐, 但在**控件之间**, 不在框内) |
| 右栏 8 组 label+edit pitch | 48 |

### 为什么是缺陷

Web 面板 `.card{padding:9px 16px}` 上下左右都是明写的 9/9/16/16, 而托盘这边
标签离卡顶 5px、数值离卡底 3px: 同一张卡两种留白, 加起来 8 比浏览器里少 10px。
更糟的是**代码注释说早就按 9px 对齐了** —— `_CARD_PAD_X` 上方写着"上下那半边
早就按 9px 对齐了", 而数值那行实际是 `pady=(0, 5 - _CARD_LABEL_GAP)`: 一个
"总纵向 padding 恒为 8"的装置, 它把 9 这个意图吃成了 5/3。而且改
`_CARD_LABEL_GAP` 会让不对称加剧(改成 3 就变 5/2)。

### 改动

**决议: A 案 + 去掉"总纵向 padding 恒定"装置。**

- `gpu_tray.py`:
  - 新增 `_CARD_PAD_Y = 9`(汇总卡纵向内边距, 上下同一个数, 对齐 Web 的
    上 9 / 下 9)与 `_CARD_H = 66`(= 2px highlight 边框 + 2x9 + 标签 22 +
    间距 2 + 数值 22), 后者只服务于 `_PANEL_MIN_H` 的账面
  - `pady=(5, _CARD_LABEL_GAP)` -> `pady=(_CARD_PAD_Y, _CARD_LABEL_GAP)`
  - `pady=(0, 5 - _CARD_LABEL_GAP)` -> `pady=(0, _CARD_PAD_Y)`
  - `_PANEL_MIN_H` 634 -> **644**(账面 630 -> 640, 实测恒差 4px)
  - 修掉两处说谎注释: `_CARD_PAD_X` 上方那句, 以及测试里同样的假话
- `tests/test_gpu_monitor.py`:
  - 两处 `8 + 56 + gt._BLOCK_GAP` -> `8 + gt._CARD_H + gt._BLOCK_GAP`
    (`_budget()` 与 `test_the_row_height_and_the_window_floor_move_together`)
  - `for h in (56, 28)` -> `for h in (gt._CARD_H, 28)`
  - 两处硬编码 `"1046x634+55+55"` -> `"1046x644+55+55"`
  - `test_the_card_label_gap_matches_the_web_panel` /
    `test_the_card_side_padding_matches_the_web_panel` 的 docstring 假话
    已改真; `pady=(5, ...)` 断言串同步
  - `test_the_card_text_still_fits_at_the_narrowest_window` 的真渲染镜像
    同步(里面有一处**漏了 `gt.` 前缀**导致 NameError, 已修)
  - **新增 `class TrayPanelCardGeometry`**(插在 `class NormalizePerAdapter`
    之前), 3 条守卫:
    - `test_the_card_vertical_padding_matches_the_web_panel`: 从
      `gm._PANEL_STYLE` 正则抠 `.card{...padding:(\d+)px (\d+)px`, 断言上
      内边距 == `_CARD_PAD_Y`、左右 == `_CARD_PAD_X`, 且源码里必须出现
      `pady=(_CARD_PAD_Y, _CARD_LABEL_GAP)` 与 `pady=(0, _CARD_PAD_Y)`
      两种形状
    - `test_the_card_padding_is_symmetric_top_and_bottom`: 真渲染
      (`tk.Tk()`, **必须 `root.update()`**, `update_idletasks()` 量出来
      `winfo_y()` 是 0), 断言上内边距 == 下内边距 == `1 + _CARD_PAD_Y`、
      标签与数值 gap == `_CARD_LABEL_GAP`
    - `test_the_card_height_matches_its_parts`: 断言 `cell.winfo_height()`
      == 按部件算出的值 **且** == `_CARD_H`
  - 辅助 `_cards(self, root, rows)` 按 `_panel_build` 同样 pack 方式搭卡
- `README.md`: 在"窗口太矮时趋势图不再被压成一条线"那条后面插入 §159 说明
  (含 AIDA64 反编译依据)

### 验证结果

- 两个 runner 各 **667 条全绿**(原 664 + 3 新)。
- 变异验证 3 条全红(`.tmptest/_mut159.py`, 备份/还原自
  `.tmptest/_gpu_tray159.bak`, `finally` 里还原并 `os.remove`):
  - `_CARD_PAD_Y = 9 -> 5`: 高度守卫 + Web 对照守卫红(`9 != 5` / `58 != 66`)
  - 底部 pady 改回 `_CARD_PAD_Y - _CARD_LABEL_GAP`: 形状守卫红
    (`pady=(0, _CARD_PAD_Y)` 找不到)
  - `_CARD_H = 66 -> 56`: 高度守卫红(`66 != 56`)
- **已知覆盖边界**: 变异 2 只被**静态形状守卫**抓到, 没被真渲染守卫抓到 ——
  真渲染测试自己复刻了一份布局, 不改 `gpu_tray.py` 的 pack 行。

### 踩过的坑

- 换行符(本轮又踩一次): 读文件用 `newline=""` 时文本**已经含 `\r\n`**,
  此时再 `.replace("\n", "\r\n")` 会把每一行变成 `\r\r\n`, 整个文件显示为
  全量 diff(gpu_tray.py 5456 行、tests 24065 行全变)。已
  `git checkout -- <file>` 还原重做。**正确写法**: 读 `newline=""` 后写回时
  **不做 replace**, 插入新文本块时只对**新块**做。每次写完自检
  `assert "\r\r\n" not in txt`。
- 变异脚本里 `subprocess` 要读 **stderr**(unittest 报告走 stderr), 只读
  stdout 会拿到空摘要。

### 顺带发现(未改, 记档)

§157 立的规矩是"说不出名字的数测试算不出账", 这些未命名间距字面量可作为
下一轮候选:

- `gpu_tray.py` 卡片间横向 gap 裸 `8`(与 Web `.cards{gap:8px}` 同值, 无常量
  无测试)
- 筛选栏 `padx=6` / `padx=(6,0)` 共 4 处; 详情窗三按钮 `padx=6` 共 3 处;
  弹泡 `padx=8, pady=6` 共 2 处; `avail = width - 16  # padx=8 两侧` 1 处
- AIDA64 侧可作外部参照: 按钮↔按钮 gap 6px、窗体四边内边距 7px

另有两条**不是缺口**, 别再当问题报: `app.ico` 已有 7 档尺寸(16..256);
`_bold_font` 已有字体回退链。`aida64.ini` 的 `[Generic]` 有
`HWMonUpdateFreqSensorPage=3000` / `HWMonUpdateFreqGPUPage=3000` /
`HWMonUpdateFreqSensorIcons=5000`(**按界面分面设不同刷新率**)—— 面板现在
三个面共用一个刷新频率, 这可作为下一轮候选。

### 用户决策待办

1. 现网 exe 仍是旧构建(10-04), 且默认窗口高度 630 -> 634 -> **644**、表格
   行高 28 -> 32、设置窗 460x460 -> 460x520、详情窗 520x300 -> 520x320、
   筛选栏 34 -> 28、汇总卡 56 -> **66**、区块间距都看不到。不同步的话用户
   看不到这些优化。要同步需 build -> sync -> 重启托盘(会打断实例)。
2. **39 条新提交未 push**, 与 origin/main 分叉。

## 160. 续一百五十一：汇总卡横向 gap 是裸 8, 与 Web .cards{gap:8px} 之间没有守卫(布局)

§159 扫出来但没处理的那批未命名间距字面量, 本轮取其中最值得动的一条:
卡片与卡片之间的横向 gap。

### 为什么是缺陷

数值本身没错(托盘 8 == Web 8), 问题是**两边没有任何东西保证它们一致**:

- `gpu_tray.py` 的 pack 那句写作 `padx=(0, 0 if i == last else 8)`, 既没有
  常量也说不出来历。
- 浏览器里 `.cards{display:flex;gap:8px}` 是明写的(gpu_monitor.py:1643)。
- 标签间距(`_CARD_LABEL_GAP`)、上下内边距(`_CARD_PAD_Y`)、左右内边距
  (`_CARD_PAD_X`)都各有跨面对照的守卫, 唯独横向 gap 这条一直没有 —— 整个
  测试文件里搜不到 `.cards`。

于是 Web 那边把 `.cards` 的 gap 一改, 托盘这边六张卡的疏密会悄悄分家, 而
整套测试一条都不会红。这正是 §157 立的规矩要治的那类: "说不出名字的数测试
算不出账"。

### 改动

- `gpu_tray.py`: 新增 `_CARD_GAP = 8`(注释写明与 Web `.cards{gap:8px}` 同数,
  以及 AIDA64 按钮↔按钮 6px 这个同类参照), pack 那句的裸 8 换成 `_CARD_GAP`。
- `tests/test_gpu_monitor.py`:
  - 两处镜像同步: `else 8))` -> `else gt._CARD_GAP))`
    (`TrayPanelStatusFloor` 里那条与 `TrayPanelCardGeometry._cards`)
  - `TrayPanelCardGeometry` 新增两条守卫:
    - `test_the_card_gap_matches_the_web_panel`: 从 `gm._PANEL_STYLE` 正则抠
      `.cards{[^}]*gap:(\d+)px`, 断言与 `_CARD_GAP` 同数, 并钉住 pack 那句的
      形状(`padx=(0, 0 if i == last else _CARD_GAP)` 必须出现在源码里)
    - `test_the_card_gap_is_what_the_render_shows`: 真渲染量相邻两张卡的间隔
      == `_CARD_GAP`(量的是"常量没有说谎")
  - 类 docstring "三条分别守" -> "五条分别守"
- `README.md`: 新增 §160 条目; 顺手把 §159 那条末尾的"补上三条"改成"补上三条
  (§160 又添两条, 守的是横向 gap)" —— 不改的话那句话当场变成假话。

### 验证结果

- 两个 runner 各 **669 条全绿**(原 667 + 2 新)。
- 变异验证 3 条全红(`.tmptest/_mut160.py`, 同时备份 gpu_tray.py 与
  gpu_monitor.py, `finally` 里还原并 `os.remove`):
  - `_CARD_GAP = 8 -> 6`: Web 对照守卫红(`8 != 6 : 托盘卡片间距 6px / Web 8px`)
  - pack 那句退回裸 `8`: 形状守卫红(`padx=(0, 0 if i == last else _CARD_GAP)`
    not found)
  - Web `.cards` 的 `gap:8px` -> `gap:12px`: Web 对照守卫红(`12 != 8`)——
    **这一条证明跨面对照真的通了**: 只改浏览器那边的数, 托盘测试也红
- 已知覆盖边界与 §159 相同: 变异 2 只被静态形状守卫抓到, 真渲染那条抓不到
  —— `_cards` 镜像自己复刻了一份布局, 它跟着常量走。

### 踩过的坑

- 类 docstring 锚点缩进是 4 空格不是 5(`三条分别守` 在 class docstring 里),
  第一版补丁 assert 挂掉; 因为脚本先 assert 后写盘, 文件无损。
- 新测试沿用现有写法 `io.open(...).read()` 不关文件, 会刷
  `ResourceWarning: unclosed file`。现有那条
  `test_the_card_vertical_padding_matches_the_web_panel` 一直这样, 不是本轮
  引入的; 要收敛的话得连它一起改。

### 剩下的候选(未动, 记档)

- `cards.pack(..., pady=(8, _BLOCK_GAP))` 里那个裸 `8`(卡片区上边距), 而
  Web 是 `.cards{margin:10px 0 12px}` —— 这一条**不只是没名字, 数值也对不上**
  (托盘 8/4 vs Web 10/12), 是下一轮最值得做的一条
- 筛选栏 `padx=6` / `padx=(6,0)` 共 4 处; 详情窗三按钮 `padx=6` 共 3 处;
  弹泡 `padx=8, pady=6` 共 2 处; `avail = width - 16  # padx=8 两侧` 1 处
- `test_the_card_text_still_fits_at_the_narrowest_window` 的镜像里还有一处
  `pady=(8, 4)`
- AIDA64 侧可作外部参照: 按钮↔按钮 gap 6px、窗体四边内边距 7px
- aida64.ini 的 `[Generic]` 按界面分面设刷新率(SensorPage 3000 / GPUPage
  3000 / SensorIcons 5000), 面板现在三个面共用一个频率

### 用户决策待办

1. 现网 exe 仍是旧构建(10-04), 默认窗口高度 630 -> 634 -> 644、表格行高
   28 -> 32、设置窗 460x460 -> 460x520、详情窗 520x300 -> 520x320、筛选栏
   34 -> 28、汇总卡 56 -> 66、卡片间距都看不到。不同步的话用户看不到这些
   优化。要同步需 build -> sync -> 重启托盘(会打断实例)。
2. 41 条新提交未 push, 与 origin/main 分叉。

## 161. 续一百五十二：汇总卡顶上那个裸 8 提成 _PANEL_PAD, 并纠正 §160 一处错的 Web 对照(布局)

§160 结尾把"下一轮最值得做的一条"记成: `cards.pack(..., pady=(8, _BLOCK_GAP))`
里那个裸 `8` 对 Web 的 `.cards{margin:10px 0 12px}`, "数值也对不上(托盘 8/4
vs Web 10/12)"。本轮先去核实这个对照, 结论是**那个对照是错的**, 于是本节做
两件事: 把该命名的数命名, 把错的结论改正。

### 先纠正: 8 和 10 不是同一个数量

看 Web 面板的 HTML(gpu_monitor.py:1961-1967), 卡片上方还有四段:

```
<h2>GPU 监控</h2>
<p id=policy class='policy'>生效中: ...</p>
<p id=conn hidden></p>
<p id=gpuinfo>核显: ...<br>独显: ...</p>
<div id=cards class=cards>...</div>
```

所以 `.cards{margin:10px 0 12px}` 那个 `10px` 是从 `#gpuinfo` 底边量的,
不是从视口顶。而托盘的汇总卡是 `_panel_build` 里**第一个** pack 的区块, 顶上
就是窗口上沿。两个数量的参照物不同, 对不上是应该的。

托盘自己的注释早就把这个说对了, 在 tests 里:
"汇总卡顶部那 8 是窗口上沿, 不是区块间距, 所以它是唯一带非零上边距的一处。"

另外 `_BLOCK_GAP` 的注释也写着 Web 面板区块顺序不同(卡片 -> 趋势图 -> 筛选框
-> 表格), 没有一一对应的 margin 可对齐 —— 所以"托盘 4 vs Web 12"同样不是一
回事。

按 §160 那个错误结论去把 8 改成 10 会是回退。真正该做的是把这个数**命名**:
它和左右两边是同一个数(`_PANEL_PAD`), 因为它是窗口上沿的外层内边距。

### 改动

- `gpu_tray.py`:
  - `cards.pack(..., pady=(8, _BLOCK_GAP))` -> `pady=(_PANEL_PAD, _BLOCK_GAP)`
  - `_PANEL_PAD` 的注释改写: 它同时管四个区块的左右和第一个区块的顶部; 窗口
    下沿是唯一例外(`_STATUS_BOTTOM_PAD`)。并把"别拿这个数去对 Web 的
    `.cards{margin:10px 0 12px}`"连原因写进注释, 免得下一个人再误读一遍。
  - `_PANEL_MIN_H` 账面注释 `卡片 78 (pad 8 + ...)` -> `(pad _PANEL_PAD 8 + ...)`
- `tests/test_gpu_monitor.py`:
  - 七处裸 `8` 全部换成 `gt._PANEL_PAD`: `_budget()`、行高等式那条、
    `expect` 元组里的形状串、以及四处真渲染镜像
  - `test_the_block_gap_is_one_number_used_in_all_three_places` 里那段注释
    同步(原文"汇总卡顶部那 8 是窗口上沿")
  - 新增 `test_the_first_block_top_pad_is_the_panel_pad`: 静态查
    `pady=(_PANEL_PAD, _BLOCK_GAP)` 形状 + 真渲染量 `cards.winfo_y()` ==
    `_PANEL_PAD`; docstring 里把上面那条纠正写全
- `README.md`: 新增条目, 并把 §160 的错误对照当场改正

### 验证结果

- 两个 runner 各 **670 条全绿**(原 669 + 1 新)。
- 变异验证 3 条(`.tmptest/_mut161.py`, 备份/还原 gpu_tray.py):
  - M1 pack 退回裸 `8`: **红**。`test_the_block_gap_is_one_number_used_in_all_
    three_places` 和新守卫都抓到(`pady=(_PANEL_PAD, _BLOCK_GAP)` not found)
  - M2 按 §160 的错误结论对齐成 `10`: **红**。这条是关键 —— 那个错误改法现在
    会被当场抓住, 不会再有人照着做
  - M3 `_PANEL_PAD` 8 -> 10: **全绿**。一致性检查: 提成常量后顶部间距与左右
    内边距该一起动、账面该跟着走, 全套仍应全绿。红了才说明提常量时漏同步

### 剩下的候选(未动, 记档)

- 筛选栏 `padx=6` / `padx=(6,0)` 共 4 处; 详情窗三按钮 `padx=6` 共 3 处;
  弹泡 `padx=8, pady=6` 共 2 处; `avail = width - 16  # padx=8 两侧` 1 处
- `test_the_card_text_still_fits_at_the_narrowest_window` 的镜像里还有一处
  `pady=(gt._PANEL_PAD, 4)` 的 `4`(本轮只换了 8, 没动它)
- AIDA64 侧可作外部参照: 按钮↔按钮 gap 6px、窗体四边内边距 7px
- aida64.ini 的 `[Generic]` 按界面分面设刷新率(SensorPage 3000 / GPUPage
  3000 / SensorIcons 5000), 面板现在三个面共用一个频率

### 用户决策待办

1. 现网 exe 仍是旧构建(10-04), 默认窗口高度 630 -> 634 -> 644、表格行高
   28 -> 32、设置窗 460x460 -> 460x520、详情窗 520x300 -> 520x320、筛选栏
   34 -> 28、汇总卡 56 -> 66、卡片间距都看不到。不同步的话用户看不到这些
   优化。要同步需 build -> sync -> 重启托盘(会打断实例)。
2. 43 条新提交未 push, 与 origin/main 分叉。

## 162. 续一百五十三：同一行相邻控件的间隙被「两个属主」叠成双倍，收成 _CTL_GAP=6(布局)

§161 扫出来但没处理的那批未命名间距字面量，本轮取其中「已经量出错了」的两条:
筛选栏五个控件之间的四个间隙、详情窗底部三个按钮之间的两个间隙。

### 为什么是缺陷

数值上看着像「已经对了」: 筛选栏三处间隙本来就是 6，详情窗三个按钮也是 6。但真
渲染量出来是歪的:

- 筛选栏四个间隙: **6/12/6/6**，1046/900/722/650 四个宽度下一律如此
- 详情窗三个按钮的两个间隙: **12/12**

病因是**每个间隙有两个属主**: 筛选栏的输入框写 `padx=6`(左右都声明)，右边「显示
N/T 行」又写 `padx=(6, 0)`，两份叠成 12px; 详情窗三个按钮都写 `padx=6`，相邻两份
同样叠加。所以这不是窄窗才有的偶发现象，是每个宽度都错。

而这条以前**没有任何测试看得见**: `test_the_filter_row_controls_share_one_baseline`
只量底边齐不齐，全套单测里没有一个用例量横向间隙。

### 改动

- `gpu_tray.py`
  - 新增 `_CTL_GAP = 6`: 同一行里相邻控件的横向间隙。Web 面板没有这样的一行(它的
    筛选器只有一个 `#q` 输入框)，没有一一对应的 margin 可抠，所以和 `_BLOCK_GAP`
    一样取托盘自己的统一值; 外部参照是反编译 AIDA64 Engineer 5.20 量到的按钮↔按钮
    gap 6px(§159)。
  - 修法统一成「每个间隙只许有一个属主」: 左边的元素不声明自己的右边距，右边的元素
    用 `padx=(_CTL_GAP, 0)` 声明它左边那一份。
  - 筛选栏 4 处(输入框 + 两个按钮 + 右边那个 Label)+ 详情窗 3 处按钮全部改成
    `padx=(_CTL_GAP, 0)`; `entry.pack` 从 `padx=6`(左右都声明)改成
    `padx=(_CTL_GAP, 0)`(只声明左边那一份)。
  - `btns.pack(pady=6)` 提成 `_DETAIL_BTN_PAD_Y = 6` —— 它进详情窗的窗高账，按
    §157 立的规矩「说不出名字的数测试算不出账」。
  - 顺手搬回被甩开的 `_BLOCK_GAP` 注释: 它原本贴在常量上方，后来 `_ROW_H` /
    `_FORM_ROW_PITCH` 两块插进两者之间，注释被甩到 30 行开外、正好压在 `_ROW_H` 的
    注释上头 —— 读代码的人会把「面板四个区块之间的间距...每处都是 4px」当成在说表格
    行高。
- `tests/test_gpu_monitor.py`
  - 镜像同步: `test_the_filter_row_controls_share_one_baseline` 里的 `entry.pack` /
    `hint.pack` / `pin.pack` / `srt.pack` 4 处，加上该用例里 `fill_y` 的字符串断言
    (改成跨两行的拼接串 —— `entry.pack` 那句现在也跨两行了)，以及注释里
    `padx=(6, 0)` 的字样。
  - 详情窗高度账 `budget8` / `budget9` 的 `2 * 6` -> `2 * gt._DETAIL_BTN_PAD_Y`。
  - 新增 `TrayPanelStatusFloor.test_the_filter_row_gaps_are_all_one_number`: 4 个
    宽度真渲染量 4 个间隙 + 静态数「恰好 4 处 `padx=(_CTL_GAP, 0)`」+ 钉
    `_CTL_GAP == 6`。
  - 新增 `TrayPanelStatusFloor.test_the_detail_button_gaps_are_all_one_number`: 数
    3 处 + `btns.pack(pady=_DETAIL_BTN_PAD_Y)` 形状 + 量 2 个间隙 +
    `btns.winfo_height() == 按钮高` 且 `btns.winfo_y() == _DETAIL_BTN_PAD_Y`。
- `README.md`: 新增 §162 两条(控件间隙收成 _CTL_GAP + 搬回被甩开的注释)。

### 验证结果

- 两个 runner 各 **672 条全绿**(原 670 + 2 新)。
- 变异验证 6 条(`.tmptest/_mut162.py` / `_mut162b.py`，`finally` 还原并
  `os.remove`):
  - M1 输入框退回 `padx=6`: 红(老守卫 + `3 != 4`)
  - M2 详情窗按钮退回 `padx=6`: 红(`2 != 3`)
  - M3 `_CTL_GAP` 6->4 / 6->8: **一开始全绿** —— 真渲染镜像跟着常量走，静态计数也
    只数「有几处用了常量」，所以改数值两条都放行。这正是 §160/§161 反复出现的覆盖
    边界: 真渲染镜像证明的是「常量没有说谎」，不是「常量是对的」。补了
    `assertEqual(gt._CTL_GAP, 6)` 之后红。
  - M4 按钮行 `pady` 退回裸 6: 红(形状守卫)
  - M5 `_DETAIL_BTN_PAD_Y` 6->8: 全绿(账面与镜像一起动的一致性检查，符合预期)

### 踩过的坑

- `t.replace(old, new, 1)` 在两处相同文本时只换第一处 -> 中间那个按钮漏改，返工两次。
  同类文本要么全量替换，要么按出现次数循环。
- `assert "padx=6" not in code` 把自己注释里的字样也算进去 -> 写盘没执行。计数类
  断言必须先剥整行注释(`test_the_block_gap_is_one_number_used_in_all_three_places`
  里已有先例，本轮又踩一次)。
- `btns.winfo_height()` **不含** `pady`(pady 在 Frame 的分配区之外)，第一版断言
  `== 按钮高 + 2*pad` 挂; 改为量 `winfo_height()==按钮高` 与
  `winfo_y()==_DETAIL_BTN_PAD_Y`。
- 镜像漏同步一处就红: `hint.pack` 在测试里也跨两行，`fill_y` 那条字符串断言要跟着
  改成拼接串。

### 顺带发现(未改，记档)

- 弹泡 `padx=8, pady=6` 共 2 处(gpu_tray.py 674 / 738)
- `avail = width - 16  # padx=8 两侧` 1 处
- `test_the_card_text_still_fits_at_the_narrowest_window` 镜像里的
  `pady=(gt._PANEL_PAD, 4)` 的裸 `4`
- `_DETAIL_BTN_PAD_Y` 的数值本身无外部参照(只有「进窗高账」这条边界)，未钉
- aida64.ini `[Generic]` 按界面分面设刷新率(SensorPage 3000 / GPUPage 3000 /
  SensorIcons 5000)，面板三个面共用一个频率

### 用户决策待办

1. 现网 exe 仍是旧构建(10-04)，默认窗口高度 630 -> 634 -> 644、表格行高
   28 -> 32、设置窗 460x460 -> 460x520、详情窗 520x300 -> 520x320、筛选栏
   34 -> 28、汇总卡 56 -> 66、卡片间距、控件间隙都看不到。要同步需
   build -> sync -> 重启托盘(会打断实例)。
2. 44 条新提交未 push，与 origin/main 分叉。

## 163. 续一百五十四：底部状态行的截断预算与 _PANEL_PAD 脱钩, 写死的 16 会在改 padding 时悄悄多说 8px(布局)

§162 扫出来但没处理的那批未命名间距字面量, 本轮取其中唯一一条**已经在源码里**的:
`_status_line` 的 `avail = width - 16  # padx=8 两侧`。

### 为什么是缺陷

底部状态行(「核显: ... 独显: ... 刷新: 1 秒」+ 一串快捷键)那个 Label 在
`_panel_build` 里是 `padx=_PANEL_PAD` pack 的, 左右两边同一个数, 所以文字真正
能占的宽度是「实占宽度 - 2*_PANEL_PAD」。而 `_status_line` 原来写死 16, 只在行尾
挂一句「padx=8 两侧」当说明。

这不是洁癖: `_PANEL_PAD` 哪天从 8 改成 12, 那句注释不会报错, 而账目当场说谎 ——
算出来的可用宽度比真实的大 8px, 于是对一段其实会被右沿切掉的文字说「放得下」。
用户看到的就是半句话, 而测试全是绿的(本轮之前全套 672 条没一条量这件事)。

同一批里还有三处测试镜像硬编码 `4`(`pady=(gt._PANEL_PAD, 4)` 两处、
`pady=(0, 4)` 一处, 源码那边是 `_BLOCK_GAP`), 留作下一轮候选。

### 改动

- `gpu_tray.py`
  - `avail = width - 16` -> `avail = width - 2 * _PANEL_PAD`, 并把行尾那句
    「padx=8 两侧」展开成一段注释: 说明这个 Label 的 pack 方式、为什么是两倍、
    以及写死 16 的后果(多说 8px -> 被右沿切掉)。
  - 同一段上方「拿它减 16 得到一个 MagicMock」的注释同步改成 `2*_PANEL_PAD`。
- `tests/test_gpu_monitor.py`
  - `test_the_status_line_never_gets_clipped_at_the_right_edge` 里算「放得下 /
    放不下」的四个 `+ 16` 全部改成 `2 * gpu_tray._PANEL_PAD`。这四个是同一个数
    的副本: 源码改了镜像不改, 那条用例守的就是个假象。
  - `test_the_status_line_survives_a_control_with_no_width` 的 docstring 里
    「拿它减 16」同步。
  - 新增 `TrayPanelStatusFloor.test_the_status_line_budget_comes_from_the_panel_pad`,
    两头钉: 静态钉 `avail = width - 2 * _PANEL_PAD` 与
    `tip.pack(fill="x", padx=_PANEL_PAD`; 真渲染在 1046/900/722/650 四个宽度下
    量「root.winfo_width() - tip.winfo_width()` 必须正好等于 `2*_PANEL_PAD`。
    先拿探针脚本量过: 四个宽度全是 16 == 2*8, 量法可靠才写进用例。

### 验证结果

- 两个 runner 各 **673 条全绿**(原 672 + 1 新)。
- 变异验证 6 条(`.tmptest/_mut163.py` / `_mut163b.py`, `finally` 还原):
  - M1 算式退回裸 `16`: 红
  - M2 系数 `2` -> `3`: 红
  - M3 `tip.pack` 的 `padx=_PANEL_PAD` -> `padx=0`: 红(真渲染那条抓的)
  - M4 `_PANEL_PAD` 8 -> 12(源码与镜像一起动): 绿 —— 覆盖边界
  - M5 `_PANEL_PAD` 8 -> 40 同时把 `wide` 那处镜像退回 `16`: 红 —— 证明镜像现在
    真的承力(小步长时余量会把它吸收掉, 必须大步长才现形)
  - M6 五处镜像全退回 `16` 而 `_PANEL_PAD` 不变: 绿 —— 纯卫生性改动, 无数值差异

### 踩过的坑

- 第一版 M5 用 `_PANEL_PAD` 8 -> 12 + 一处镜像退回 `16`, 结果**全绿**, 与预期
  相反。原因是那条用例只断言相对关系(放得下/放不下), 4px 的偏差被 `+ 40` 的
  余量吸收了。换成 8 -> 40 才红。教训: 镜像类变异要选**足够大的步长**, 否则
  「绿」什么都证明不了。
- PowerShell here-string 里写 python 补丁时, 用 `\uXXXX` 转义写中文容易抄错码位
  (把「替」写成「代」U+4EE3), 匹配静默失败。直接用字面中文更稳(commit message
  那一版就是这么写的)。

### 顺带发现(未改, 记档)

- 测试镜像里硬编码 `4` 共三处: `pady=(gt._PANEL_PAD, 4)` 两处(tests 10760 /
  10765, `test_the_card_text_still_fits_at_the_narrowest_window`)、
  `pady=(0, 4)` 一处(tests 10087, `test_the_filter_row_controls_share_one_baseline`)
  —— 源码那边都是 `_BLOCK_GAP`。下一轮候选。
- 弹泡 `padx=8, pady=6` 共 2 处(gpu_tray.py 674 / 738, `_bind_tip` 与
  `_bind_tree_tip` 各写一遍); 同两处还各有一个屏幕边距裸 `8`
  (`if x + w + 8 > sw`)。
- `_DETAIL_BTN_PAD_Y` 的数值本身无外部参照, 未钉。
- aida64.ini `[Generic]` 按界面分面设刷新率(SensorPage 3000 / GPUPage 3000 /
  SensorIcons 5000), 面板三个面共用一个频率。

### 用户决策待办

1. 现网 exe 仍是旧构建(10-04), 默认窗口高度 630 -> 634 -> 644、表格行高
   28 -> 32、设置窗 460x460 -> 460x520、详情窗 520x300 -> 520x320、筛选栏
   34 -> 28、汇总卡 56 -> 66、卡片间距、控件间隙、状态行截断都看不到。要同步需
   build -> sync -> 重启托盘(会打断实例)。
2. 46 条新提交未 push, 与 origin/main 分叉。

## 164. 续一百五十五：两个对话框的外层内边距各写一套(12/8 vs 14/10), 收成 _DIALOG_PAD_X/_DIALOG_PAD_Y(布局)

§163 扫出来但没处理的那批未命名间距字面量, 本轮取其中唯一还在**源码里**的几条:
设置窗与详情窗的外层内边距。

### 为什么是缺陷

`gpu_tray.py` 里筛完 padx/pady 之后, 只剩这些裸数字:

- 设置窗 `grid.pack(fill="both", expand=True, padx=12, pady=8)`
- 设置窗 `saver.pack(pady=8)`
- 详情窗 `frm.pack(fill="both", expand=True, padx=14, pady=10)`

`git log -S` 查过: 三处都是同一个提交(`9bc4de0`, 最初那一版)带进来的,
**没有任何理由**。两个对话框差 2px, 用户在设置窗与详情窗之间来回时看得见 ——
左边缘不在同一条竖线上。而这三个数同时是两笔账的输入:

- 设置窗 460x520 的纵向余量(注释里写着"加 grid 的 pady 8x2 与保存键 28 +
  pady 8x2 = 484")
- 详情窗右列的折行宽度(`avail = 窗宽 - 2*14 - 键名列 141`, 而
  `_DETAIL_WRAP_PX = 345`, 原来只剩 6px 余量)

裸数字 + 进账 = §157 立的规矩里最该办的那一类。

### 改动

- `gpu_tray.py`
  - 新增 `_DIALOG_PAD_X = 12` / `_DIALOG_PAD_Y = 8`, 紧跟 `_FORM_ROW_PITCH`
    (同一把尺的另一半: 行距管行内, 这个管窗边)。
  - 取**设置窗那一套(较小的)**, 理由写进注释: 设置窗因此一个像素都不动, 只有
    详情窗每边收 2px, 而它是吃宽度的那一个(要显示没空格的长路径), 收 2px 正好
    多给它 4px 内容宽, 折行余量从 6px 涨到 10px。
  - 三处 pack 全部改用常量; 设置窗纵向预算注释与详情窗 wraplength 注释里的账
    同步更新(14x2/351 -> 12x2/355, 余量 6px -> 10px)。
- `tests/test_gpu_monitor.py`
  - 设置窗替身 `grid.pack(...)` 与 `btn.pack(pady=8)` 改用常量。
  - 详情窗替身 `frm.pack(...)` 的 `padx=14, pady=10` 改用常量。
  - 折行宽度那条账的 `avail = root.winfo_width() - 28 - col0` ->
    `- 2 * self.tray._DIALOG_PAD_X - col0`(**28 是 2*14 的副本**)。
  - 详情窗高度账 `budget8` / `budget9` 的两处 `2 * 10` ->
    `2 * gt._DIALOG_PAD_Y`(**10 是 frm pady 单侧的副本**)。
  - 顺手把 §163 扫出的三处镜像裸 `4` 接回 `gt._BLOCK_GAP`:
    `test_the_card_text_still_fits_at_the_narrowest_window` 里两处
    `pady=(gt._PANEL_PAD, 4)`、`test_the_filter_row_controls_share_one_baseline`
    里一处 `pady=(0, 4)`。
  - 新增 `TrayPanelStatusFloor.test_both_dialogs_share_one_outer_pad`: 静态钉两个
    窗的 pack 形状与 `saver.pack`, 真渲染在 460x520 与 520x320 两个几何下量框架
    偏移与内容宽度, 并钉住 12/8 这两个值。

### 验证结果

- 两个 runner 各 **674 条全绿**(原 673 + 1 新)。
- 变异验证 8 条(`.tmptest/_mut164.py`, `finally` 还原):
  - M1 设置窗 pack 退回裸 12/8: 红
  - M2 详情窗 pack 退回裸 14/10: 红
  - M3 `saver.pack` 退回裸 8: 红
  - M4 `_DIALOG_PAD_X` 12 -> 20: 红(数值钉)
  - M5 `_DIALOG_PAD_Y` 8 -> 10: 红(数值钉)
  - M6 折行账镜像 `- 2*_DIALOG_PAD_X` -> `- 28`: 绿
  - M7 高度账镜像 `2*_DIALOG_PAD_Y` -> `2*10`: 绿
  - M8 卡片镜像 `gt._BLOCK_GAP` -> 裸 4: 绿
  - M6/M7/M8 全绿是**预期的**: 当前数值下旧字面量与新算式等值, 镜像改动是卫生性
    的。它们的价值在常量改动之后才现形(和 §163 的 M5/M6 同一形状)。

### 踩过的坑

- **PowerShell here-string 里写 python 补丁, 多行字符串必须每行各自闭合引号**。
  第一版把 `"....取它\r\n" "....都不动"` 写成了跨物理行的单个字符串, 生成的测试
  文件直接 SyntaxError(unterminated string literal)。剥离开来重写才对。
- 注释里的空格数不能猜: `# 加 grid 的 pady 8x2` 实际是 `# ` 单空格, 我按同一段
  里另一行的 `#   ` 三空格去匹配, 静默失败。**匹配注释前先 `repr()` 打一遍**。
- §163 新加的守卫插在 `test_the_panel_pad_is_one_number_used_everywhere` 与
  `test_the_filter_row_controls_share_one_baseline` 之间, 于是原来那个"spark_box
  断言 + filter_row 定义"的锚点再也匹配不上。插新用例前先确认锚点邻居没被改动。

### 顺带发现(未改, 记档)

- 弹泡 `padx=8, pady=6` 共 2 处(gpu_tray.py 674 / 738, `_bind_tip` 与
  `_bind_tree_tip` 各写一遍); 同两处还各有一个屏幕边距裸 `8`
  (`if x + w + 8 > sw`)和一个裸 `4`(`winfo_rooty() + winfo_height() + 4`)。
- `_DETAIL_BTN_PAD_Y` 的数值本身无外部参照, 未钉。
- 弹泡/详情窗/设置窗这三处的数值都没有外部参照(Web 面板没有对应物, AIDA64 的
  7px 是另一个窗体), 所以 `_DIALOG_PAD_X/Y` 的数值钉只能靠"取设置窗原有值"这个
  理由撑着 —— 这是已知覆盖边界, 不是缺陷。
- aida64.ini `[Generic]` 按界面分面设刷新率(SensorPage 3000 / GPUPage 3000 /
  SensorIcons 5000), 面板三个面共用一个频率。

### 用户决策待办

1. 现网 exe 仍是旧构建(10-04), 默认窗口高度 630 -> 634 -> 644、表格行高
   28 -> 32、设置窗 460x460 -> 460x520、详情窗 520x300 -> 520x320、筛选栏
   34 -> 28、汇总卡 56 -> 66、卡片间距、控件间隙、状态行截断、对话框内边距
   都看不到。要同步需 build -> sync -> 重启托盘(会打断实例)。
2. 48 条新提交未 push, 与 origin/main 分叉。

## 165. 续一百六十四：弹泡的两份实现各写一套 8/6/4/8, 收成 _TIP_PAD_X/_TIP_PAD_Y/_TIP_GAP_Y/_TIP_EDGE_GAP(布局)

§163/§164 两次都扫到、都没处理的那批: 弹泡的间距字面量。前两轮记的是「源码里
只剩设置窗/详情窗那几处」, 弹泡那四条当时判为"两份实现各写一遍, 要动就得一起
动"所以搁置; 本轮把它做完。

### 为什么是缺陷

汇总卡 tooltip(`_bind_tip`, gpu_tray.py 657)与表头列头 tooltip
(`_bind_tree_tip`, 724)是同一套视觉的两份实现 —— 同样的 `_PANEL_BG` / `_FG` /
`_FONT_UI` / `justify="left"` / `wraplength=_TIP_WRAP_PX`, 同样的 500ms 延迟。
但间距各写一份:

- Label 的 `padx=8, pady=6`(两处)
- 弹泡离宿主控件的 `+ 4`(只在 `_bind_tip` 有)
- 离屏幕右缘收敛的 `if x + w + 8 > sw:` / `x = max(0, sw - w - 8)`(两处)

两份副本漂开没有任何人会发现: 没有一条守卫同时看这两份, 而它们在屏幕上是同一个
东西 —— 用户不会因为悬停的是表格列头就接受一个更胖、离控件更远的弹泡。更麻烦的是
`_TIP_WRAP_PX` 早就提成常量、两处共用, 偏偏间距没有: 折行宽度统一了,
**折行宽度加上内边距之后的实际弹泡宽度**却不统一。

### 改动

- `gpu_tray.py`
  - 新增四个常量(跟在 `_TIP_WRAP_PX` 后面, 注释写清"两份实现共用一套"):
    `_TIP_PAD_X = 8` / `_TIP_PAD_Y = 6` / `_TIP_GAP_Y = 4` / `_TIP_EDGE_GAP = 8`。
  - 五处使用点逐处接回: 两处 Label 的 padx/pady、`_bind_tip` 的 `+ 4`、两处右缘
    收敛。**本轮不动任何数值** —— 一个像素都不变, 纯粹是给同一件事一个名字, 所以
    现有的几何守卫(674 条)一条都不用改, 也没有测试镜像要接回。
- `tests/test_gpu_monitor.py`
  - 新增 `TrayPanelStatusFloor.test_the_two_tooltips_share_one_pad_and_gap`。
- `README.md`: 新增 §165 条目。

### 守卫怎么写的

- **静态**: 两处 `_show` 各自钉(每处只查自己的 —— 合起来查的话, 其中一处退回裸
  数字会被另一处掩盖过去):
  - 都含 `padx=_TIP_PAD_X, pady=_TIP_PAD_Y`;
  - 各含 `_TIP_EDGE_GAP` **恰好 2 次**(判断一次 + 回填一次; 少一次说明有一半
    退回了裸数字 —— "只改一半"这个更真实的回归形状靠这条抓);
  - `_bind_tip` 含 `+ _TIP_GAP_Y`, `_bind_tree_tip` **不含**。
- **真渲染**: 差分量法。同一个文本/字体/`wraplength`/`justify` 下建两个 Label,
  一个显式 `padx=0, pady=0`, 一个 `padx=_TIP_PAD_X, pady=_TIP_PAD_Y`, 则宽差
  `2*_TIP_PAD_X`(16)、高差 `2*_TIP_PAD_Y`(12)。短/中/长三档文本都量, 并断言
  最宽不超过 `_TIP_WRAP_PX + 2*_TIP_PAD_X`。
  - 差分量而不是绝对量, 两个原因: `tk.Label` 自带 `bd=2` 的边框项, 绝对量里混着
    它; 而 `_text_px()`(`Font.measure`)与 Label 内部量法不一致(短文本实测
    84 vs 88, 差 4px), 拿它直接对 Label 尺寸会假红。
  - `padx=0, pady=0` **必须显式写 0**: 不写 padx 时 `tk.Label` 默认是 1, 差值会
    少 2px(14 而不是 16), 于是恒定假红。

### 验证结果

- 两个 runner 各 **675 条全绿**(原 674 + 1 新)。
- 变异验证 10 条(`.tmptest/_mut165.py` / `_mut165b.py`, `finally` 还原):
  - M1 `_bind_tip` 的 Label 退回裸 `padx=8, pady=6`: 红
  - M2 `_bind_tree_tip` 的 Label 退回裸 `padx=8, pady=6`: 红
  - M3 右缘收敛整句退回裸 8: 红
  - M4 只退判断那一半(`if x + w + 8 > sw`): 红
  - M5 只退回填那一半(`x = max(0, sw - w - 8)`): 红
  - M6 `_TIP_PAD_X` 8 -> 10: 红(数值钉)
  - M7 `_TIP_PAD_Y` 6 -> 2: 红(数值钉)
  - M8 `_TIP_EDGE_GAP` 8 -> 2: 红(数值钉)
  - M9 `_TIP_GAP_Y` 4 -> 10: 红(数值钉)
  - M10 给列头 tooltip 补上 `+ _TIP_GAP_Y`: 红(钉住有意的差异)
- M4/M5 是后来补的: 第一版只做 M3(整句退回去), 漏掉"只改一半"这个更真实的
  回归形状 —— 有人把判断里的常量换成裸数字、忘了回填那一行, 整句变异抓不到。
  补上之后 `body.count(...) == 2` 这条才真的承力。

### 踩过的坑

- **交接摘要说 `gpu_tray.py` 有未提交改动、常量已加, 实际工作区是干净的、常量根本
  不在文件里**。开工先 `git status --porcelain` + `rg` 核一遍, 不要信摘要里"已改
  未提交"这种状态描述: 上一轮的补丁脚本失败在中途, 后续轮次看到的可能是失败后的
  残留, 也可能是根本没落盘。本轮等于从头做了一遍。
- 两处 Label 的续行缩进不同: `_bind_tip` 是 27 空格, `_bind_tree_tip` 是 21 空格。
  用一个想同时匹配两者的 `old` 串会让 `count == 2`, 补丁脚本的断言当场失败
  (上一轮 `_patch165a.py` 就死在这)。两处分开写 `old` 才对。
- 变异脚本一次跑 8 条, stdout 被截断, 截断位置正好在中间, 于是我把 M6 的报错读成
  了 M5 的(`_TIP_PAD_Y=2`), 一度以为 `_TIP_EDGE_GAP` 的数值钉没生效。逐条隔离
  复跑才确认 M6 命中的是对的断言。**批量变异只留一行摘要, 别读长尾**。

### 顺带发现(未改, 记档)

- 弹泡那 1px `_LINE` 边框实际看不见: `win.configure(bg=_LINE)` 设了窗底色, 但
  Label `pack()` 没带 padding, 填满整窗, 把那圈边框盖掉了。§164 就记过, 本轮依旧
  只记录不修(非"行距"范畴, 修它要动 `_show` 的 pack 形状)。
- 全套单测从来不建真 `Toplevel`(`Tcl_AsyncDelete` 崩进程), 所以弹泡的定位逻辑
  (`wm_geometry` 那一行)没有任何真渲染覆盖, 只有静态钉。这是已知边界。
- `_TIP_MAX = 127`(gpu_tray.py 425)与 `_TIP_DELAY_MS = 500` 是弹泡相关的另外两个
  常量, 一个管标题截断长度、一个管延迟, 与间距无关, 本轮没动。

### 用户决策待办

1. 现网 exe 仍是旧构建(10-04), 默认窗口高度 630 -> 634 -> 644、表格行高
   28 -> 32、设置窗 460x460 -> 460x520、详情窗 520x300 -> 520x320、筛选栏
   34 -> 28、汇总卡 56 -> 66、卡片间距、控件间隙、状态行截断、对话框内边距
   都看不到。要同步需 build -> sync -> 重启托盘(会打断实例)。
2. 50 条新提交未 push, 与 origin/main 分叉。

## 166. 续一百六十五：Web 趋势图阈值线少一个空格整条线不见, 与设置窗两段名单之间的 0px 缝隙(缺陷 + 布局)

本轮两件事: 一件是真缺陷(Web 面板趋势图上的迁移阈值线因为一个空格,
在浏览器里整条丢失, 控制台每秒一条报错, 而所有既有守卫都看不见);
一件是布局(设置窗「勾选区 -> 游戏名单 -> 排除名单」两处区块间隙靠
跳过空 grid 行实现, 而空行高度是 0, 两段名单从头到尾贴在一起 ——
用户报的"设置窗文字叠在一起"就是这一处)。

### 为什么是缺陷(阈值线)

`sparkline_html`(gpu_monitor.py 1549)原来写的是:

    <line class=thr x1=0 y1={thr_y} x2={_SPARK_W} y2={thr_y}/>

HTML 对**未加引号**的属性值只认空白符和 `>` 作终止, **不认 `/`**:
`y2={thr_y}/>` 里值被读成 `"10.7/"`, SVG 报 Invalid length 把**整条
线**丢掉。面板每 1000ms 重注入一次 innerHTML, 所以控制台每秒刷一条
错。前缀断言 `"<line class=thr" in html` 对此全无感觉 —— **标签在,
线不在**: 这条线是迁移阈值的可视化, 没有它用户看不出当前读数离阈值
多远。

### 为什么是缺陷(设置窗间隙)

设置窗三段表单原来靠跳过空 grid 行分隔(`r2 = r + 4` 之后
`r2 += 5`), 指望跳过 4 个空行就出来 4px。**没有控件、也没有
rowconfigure(minsize=) 的 grid 行高度是 0** —— 实测两段名单贴在一起
("排除名单" Label 顶边减游戏名单 Text 底边 = 0), 那 4 行从头到尾没
生效过。`git log -S "r2 = r + 4"` 证明这两个偏移量都来自最初提交
9bc4de0: 当时每一行都带 pady=2, 间隙是控件自己的 2+2 凑出来的;
§155 把 pady 换成 ipady 之后那 4px 消失, 是本轮行距工作自己引入的
回退, 不是历史遗留。

### 改动

- `gpu_monitor.py`: `y2={thr_y} />` —— 补一个空格, 让 `/` 落到属性
  值外面。注释写清为什么不能省(省了浏览器画不出来)。
- `gpu_tray.py`: 新增 `_FORM_BLOCK_GAP = 4`(与 `_BLOCK_GAP` 同数是
  有意的 —— 面板四处区块间隙和表单这两处是同一件事, 分两个数的话,
  用户在面板与设置窗之间看到的是两种节奏); 两处间隙各占一整行 +
  `rowconfigure(minsize=_FORM_BLOCK_GAP)`; 纵向预算注释 484 -> 492。
  不占行的改法(给名单 Text 加 pady)会改名单自己的 52px, 而它是有意
  高于 `_FORM_ROW_PITCH` 的(三行文本压到 32px 读不成行)。
- `tests/test_gpu_monitor.py`: 三条新守卫 + `_build` 替身接回
  rowconfigure 形状并交出名单 Text 与最后一个勾选供测量; 另有一条
  "rowconfigure 那一行不许写裸数字"的补充(来源见踩过的坑)。
- `README.md`: 两条(阈值线 + 设置窗间隙)。

### 守卫怎么写的

- 阈值线: **按 HTML 的解析规则**把属性值当真读出来
  (`([a-z0-9]+)=([^\s>]+)`), 再 float() 一遍 —— 解析不出数字, 就是
  浏览器画不出来的线; 顺带断言 y2 == y1(水平线)。前缀断言抓不到这个
  bug: 标签一直在, 线不在。
- 设置窗间隙: 两层。真渲染量「上一条底边到下一条顶边」两处间隙都
  等于 `_FORM_BLOCK_GAP`, 两段名单的 grid row 正好差 2(间隙行在中
  间), 间隙行高度被 `grid_bbox` 量到等于 gap; 静态层钉
  `_FORM_BLOCK_GAP == _BLOCK_GAP`、`minsize=_FORM_BLOCK_GAP` 恰好两
  处且都是 `grid.rowconfigure(`、行号推进只许 +1、名单 Text 的 grid
  不许带 pady、rowconfigure 那一行不许写裸数字。静态那层承力的理由:
  真渲染量的是单一几何, "跳过空行"在某些几何下能蒙对(空行高度 0,
  但哪天真给 grid 加了均匀 row weight, 空行会突然有高度, 量出来的
  间隙就变成随窗口缩放的数)。

### 验证结果

- pytest: 678 collected(原 675 + 3 新), 全量跑挂 2 条已知漂白
  (TclError, 同进程第二个 Tk(), 见下), 单独复跑全过。
- `python -X utf8 tests/test_gpu_monitor.py`: `Ran 678 tests ... OK`
  (40.2s)。
- `python -X utf8 -m unittest discover -s tests`: `Ran 678` (45.7s),
  2 个 error 同为 `RegisterClassEx` 已知漂白(`TraySingleInterpreter`
  两条)。
- 整套倒序是套内正式用例 `SuiteRunsInReverseOrder`(子进程按类名自
  排除防套娃), 随上述各跑覆盖, 本轮全绿。
- 变异 5 条全红: 删 y2 后那个空格、`_FORM_BLOCK_GAP` 4->8、删掉一
  处 rowconfigure、把 `r2 += 1` 写回 `r2 += 5`、`_FORM_BLOCK_GAP`
  与 `_BLOCK_GAP` 分开取值(6 vs 4)。
- 已知漂白复现记录: pytest 全量两次分别挂在
  `SettingsWindowFitsItsOwnFrame::test_the_two_list_blocks_are_separated`
  与 `TrayPanelStatusFloor::test_every_block_gap_in_the_panel_is_the_same_number`,
  discover 跑挂 `TraySingleInterpreter` 两条; 全部单独复跑通过。与
  §165 记的同一根因, 不是本轮改动引入。

### 踩过的坑

- **有第二个写者在动这个仓库**。开工时(23:00)`git status` 干净;
  23:03 跑完一轮 pytest 后, 测试文件里多出一段我没写的 docstring 重
  写(把静态测试的理由从"再钉一层"扩成"变异验证实测跑掉两条"并列出
  三个钉点), 我的 `git add` 把它一并扫进了 fixup 提交 —— fixup 报
  44 插入就是信号(删一个字节不该是 44), 当时没警觉; 23:06 跑变异时
  又多了 10 行(rowconfigure 不许写裸数字)。23:11 抓到进程证据: 另
  一个 pwsh 在跑 `python -X utf8 -m unittest discover -s tests` 和
  一个带 here/exclude 参数的 suite 扁平化脚本, 都不是本会话发的。
  内容本身是好的(三条新守卫全绿, 变异也承力), 已作为独立提交
  fdea9ae 落下并推上去。**教训: 每次 `git add` 之前先 `git diff` 复
  核扫进去的到底是什么; 跑测试会招来写入, 跑完先 status。**
- 测试文件顶部曾被扫进去一个孤立 `\r`(Python 视角是模块文档串前多
  一个空行), 字节级删掉后 fixup 进 7b03f2a。未 push 时改写历史安
  全(`git commit --fixup` + `git rebase -i --autosquash`), 已 push
  就不行了。
- 第一次 autosquash 没并进去: `git rebase -i --autosquash HEAD~2`
  的 upstream 算错了 —— 目标提交 0d3320a 当时就是 HEAD~2, 不在重放
  范围里, fixup 原样留在顶上。fixup 目标在 HEAD~N 时, upstream 要写
  到 HEAD~(N+1)。
- PowerShell 5.1 不认 `&&`; `Start-Process -ArgumentList` 会把带空格
  的 `-k` 表达式拆成多个参数(报 `file or directory not found: or`),
  长 pytest 选择器直接前台跑更稳。

### 顺带发现(未改, 记档)

- **用户说的"文字叠在一起"有第二个来源, 且不在源码里**: 现网跑的
  是 10-04 旧构建 GPUMigrate.exe(进程 9992/16452, 13:37 启动, 带看
  门狗)。本轮之前 51 个提交里的间距修复(卡片 56 -> 66、行高
  28 -> 32、对话框加高、弹泡 padding 收口、趋势图标号垫底……)**全
  在源码里, 一条都不在那个 exe 里**。用探针(.tmptest/probe_ui.py /
  probe_ui2.py)真渲染了面板/详情窗/设置窗/弹泡: 5 种窗口尺寸 x 常规/
  极端数据, 当前源码几何零叠字; Web 面板桌面 + 390px 窄屏也无文本
  重叠。所以设置窗这一处修完之后, 用户要看到效果仍需 build -> sync
  -> 重启托盘。
- Web 面板活页面每秒 fetch + innerHTML 自刷, 已存
  `.tmptest/web_live.html` 备查。
- 用户保存的面板几何 `1636x1050+327+271` 在 1707x960 屏上右掉
  256px、底掉 361px —— `_onscreen_geometry` 只保标题栏在屏内, 属设
  计, 不是缺陷。
- dvpack 无 GUI(海报墙在 tvOS 端 dvplay), UI 话题全程锁定
  gpu-migrate。

### 用户决策待办

1. 本轮 54 条提交已一次性 push(`3bf1c4a..fdea9ae`), 本地与
   origin/main 一致, 0 条未 push; release `v1.0.0` 已建, tag 指向
   fdea9ae。
2. 现网 exe 仍是旧构建(10-04), 本轮两处修复 + 之前 51 条的间距调
   整都看不到。要同步需 build -> sync -> 重启托盘(会打断当前实例,
   重启前问用户)。
3. live-autoclicker 仓库现为 private(已有 v1.0.0), 是否公开由用户
   定; dvpack 已公开且有 v0.1.0; silver-guardian 已公开。

## 167. 续一百六十六：详情窗底部三个按钮的孤儿左 padx 让整组偏离窗心 2.5px(布局)

### 缺陷

详情窗底部「立即设为独显 / 撤销独显设置 / 关闭」三个按钮都写
`padx=(_CTL_GAP, 0)`。§162 立的规矩是"每个间隙只许有一个属主: 左边的元素
不声明自己的右边距, 右边的元素用 `padx=(_CTL_GAP, 0)` 声明它左边那一
份"—— 而**第一个按钮的左边没有邻居**, 它这一份 6px 是孤儿声明, 没有任何
间隙可以归属。

实测(520x320 详情窗, 探针真渲染): `btns` 框架 x=150 w=219 按窗心居中,
但 b1 x=6、b3 右缘=219 —— 视觉组在框架里是左 6 / 右 0。于是三个按钮的
视觉组中心 262.5, 而窗心是 260, **整组偏离窗心 2.5px**。筛选栏那一行做对
了: 最左的 `_filter_hint`(gpu_tray.py 1964)不带 padx, 由它右边的输入框
声明那一份。

单个像素级偏差, 但它是 §162 那条规矩的唯一漏网点: 三个按钮里两个声明是对的,
恰恰是最左那一个写错了。而 `_CTL_GAP` 的注释(gpu_tray.py 375-384)把规矩
写得清清楚楚, 代码没跟上。

### 修法

`gpu_tray.py` 2546-2557: 第一个按钮改 `pack(side="left")` 不带 padx, 后两
个原样。注释说明为什么第一个不声明(它左边没有邻居), 并记下筛选栏那一行是
同规矩的正例。

修完复量: 左留白 0 / 右留白 0, `btns` 宽 213 == 三个按钮 83+83+35 加两个
间隙 12, 视觉组中心 259.5 vs 窗心 260 —— 只差 0.5px, 那是 213px 奇数宽的
`winfo_x` 四舍五入, 不是缺陷。按钮自然高 28 不变,
`btns.winfo_y() == _DETAIL_BTN_PAD_Y == 6` 不变, 所以详情窗的高度账一分钱
没动。

### 守卫怎么写的

`tests/test_gpu_monitor.py` 的
`TrayPanelStatusFloor.test_the_detail_button_gaps_are_all_one_number`(原来
只量"按钮之间的间隙等于 _CTL_GAP")扩成三层:

1. **静态数次数**: `padx=(_CTL_GAP, 0)` 恰好两处(三个按钮两个间隙)。原来
   这里是 3, 本轮改成 2。
2. **静态按按钮点名**: 把剥过注释的源码按 `tk.Button(btns, text=` 切开, 逐
   个取它的 `.pack(` 调用 —— 第一个的 pack 里**不许出现 padx 这三个字母**,
   后两个必须含 `padx=(_CTL_GAP, 0)`。
3. **真渲染量**: 左右留白都是 0、`btns` 宽 == 三个按钮 + 两个间隙、视觉组
   中心落在窗心上(`assertAlmostEqual(delta=1)`, 容差给奇数宽四舍五入)。
   原来就有的"框架贴身 / 上边距是 _DETAIL_BTN_PAD_Y"两条留着 —— padx 改动
   不该牵动高度。

第二层是必需的, 而且是本轮实测逼出来的: 只数次数**数不出"孤儿声明挪到第二
个按钮"** —— 计数还是 2, 而第一个间隙当场掉成 0, 替身几何量不到(替身是自
己搭的, 源码漂开了它照样绿)。第一轮变异 M10 正是这么逃掉的, 补了第二层才红。

### 验证结果

- `python -X utf8 tests/test_gpu_monitor.py`: `Ran 678 tests ... OK`(31.4s)。
- `python -X utf8 -m unittest discover -s tests`: `Ran 678`, 2 个 error 全是
  §165/§166 记的已知漂白(`TraySingleInterpreter` 两条, pystray
  `RegisterClassEx` WinError 1410); 单独复跑该类 19 条全过。
- `TrayPanelStatusFloor` 单跑 30 条全过; `SettingsWindowFitsItsOwnFrame` 4 条
  全过。
- 变异 15 条全红, 0 逃逸(见下)。

### 变异验证(15 条, 全红)

源码 10 条: M1 第一个按钮又把孤儿 padx 写回来 / M2 第二个按钮左右都声明(叠成
12) / M3 第三个按钮不声明(掉成 0) / M4 声明写到右边那一份 / M5 退回裸
`padx=_CTL_GAP` / M6 按钮行上下留白退回裸数字 / M7 `btns` 改 anchor=w /
M8 `btns` 加 fill=x / M9 第一个按钮写 (0,0) 占位 / M10 **孤儿声明挪到
第二个按钮(计数仍 2)**。

替身 5 条(只改测试, 防"守卫自己漂"): M11 计数期望 2->3 / M12 tight 预算多算
一份 / M13 左留白期望 0->6 / M14 替身的第一个按钮也带 padx / M15 把 per-button
静态钉从 assertNotIn 改成 assertIn。

第一轮只跑前 10 条时 M9/M10 两条逃逸: M9 是 `padx=(0,0)`(几何上等于不声明,
但代码里多个死参数), M10 就是上面说的"孤儿声明搬家"。第二层静态钉把两条都
按住了。

### 踩过的坑

- **`git ls-files --eol` 说 gpu_tray.py 是 i/lf w/crlf**: 盘上是
  CRLF(core.autocrlf=true), 索引里是 LF。补丁脚本第一版按 LF 写锚点, 10 条
  源码变异**全部命中 0 次**却只报"跳过(脚本问题)", 差点当成守卫失效。读文件
  必须带 newline 空串参数, 并先断言 CR / LF / CRLF 三个计数相等。
- **`apply_patch` 在这个环境里不存在**(会被 PowerShell 当成命令名报
  ParserError), 只能用 Python 脚本改文件。而 python -c 后面跟双引号会被外层
  PowerShell 吞掉 —— 改用**单引号**包整段 Python 代码, 里面就可以放心用
  双引号, 只是不能出现 ASCII 单引号。
- **`rtk bypass git status --porcelain` 仍然要用**: 本轮两次被压缩输出骗过
  (一次是"干净"的假象)。补钉前先 `git diff-files --name-status`。
- `tests/test_gpu_monitor.py` 与 `gpu_tray.py` 盘上都是 CRLF; 写回必须带
  newline 空串参数, 否则 Python 会把换行再翻倍成 CR CR LF。
- 本会话开头没有第二个写者在活动(三个 pwsh 进程全是本会话的), 但 §166 记过的
  现象随时可能回来, 每轮 `git add` 前仍然先 `git diff`。

### 顺带发现(未改, 记档)

- **§167 候选 B/C/D 仍未动**: B 是详情窗"内容底 -> 按钮行顶"14 vs 设置窗 16
  (差 2px), 但 `_DETAIL_BTN_PAD_Y = 6` 的数值历史上明确记为"无外部参照、未
  钉"(§159/§165), 改它要连带改
  `test_the_detail_window_grows_for_the_gone_row` 的 budget8/budget9
  (tests/test_gpu_monitor.py 10990-10991 / 10998-10999), 是设计决定; C 是
  `_DETAIL_WRAP_PX` 注释里"键名列 141"是假的(实测 132px), 余量是 19px 不是
  注释写的 10px —— 数字偏保守所以不出错, 但注释不实; D 汇总卡已核过没问题
  (卡间间隙 [8,8,8,8,8] == `_CARD_GAP`, 左右留白 16 == `_CARD_PAD_X`)。
- 现网已换新构建（见 §168）: 23:48 构建的 `GPUMigrate.exe`, 本轮这个 3px 的
  修复在包里, 活实例 PID 21856/32336。

### 用户决策待办

1. [已完成] 第二写者自行 push 了 dfbc38b(fix) + 02caf12(docs), 现 0 条未
   push（§168 核实过 `3bf1c4a..HEAD` 共 57 条、本地与 origin/main 一致）。
2. [已完成] 用户本轮说“继续”即放行: §168 走了 build（两个 spec + 冒烟）->
   字节码级验包 -> 换活实例（停 9992/16452、覆盖仓库根 exe、看门狗路径拉起
   21856/32336）, 活页阈值线实测带 ` />`。


## 168. 续一百六十七：把 §155-§167 的间距修复同步进现网 exe —— build -> 字节码级验包 -> 换活实例(部署)

### 起因

§167 的"用户决策待办"两条: (1) 本轮 2 条提交待 push; (2) 现网 exe 仍是
10-04 旧构建, §155-§167 所有间距修复一条都不在那个 exe 里, 要同步需
build -> sync -> 重启托盘(会打断当前实例, 重启前问用户)。本节把这两条闭环。

### 经过

- **第二写者又回来了一轮**(§166 记过的现象): 23:36-23:44 它写了 §167
  (详情窗三个按钮的第一个不带 padx —— 它左边没有邻居, 那一份 6px 是孤儿
  声明, 三个按钮的视觉组偏离窗心 2.5px), dfbc38b(fix) + 02caf12(docs)
  两个提交它自己 push 了; 之后 23:50 又试了一处删 `expand=True`
  (汇总卡 grid), 23:51 自行回退。协议不变: 不碰它在编辑的
  `gpu_tray.py` / `tests/test_gpu_monitor.py`, 等它本轮落地(commit +
  push)再动构建。
- **测试**: 678 条。pytest 全量 677 passed + 1 failed —— 失败的
  `SettingsWindowFitsItsOwnFrame::test_the_two_list_blocks_are_separated`
  单独跑过; 类内跑 `TrayPanelStatusFloor` 挂 2 条同因。都是同进程第二个
  `Tk()` 的 `RegisterClassEx` 漂白(§165/§166 已记, 每次挂不同条), 不是
  真红。
- **build**: `C:\Python314\python.exe -X -u tools\verify_build.py`, 两个
  spec 都 build OK + `--once` 冒烟 OK(退出码 0, 枚举 3 块显卡); 产物转存
  `dist\GPUMigrate.exe`(32314479 字节单文件)与
  `dist\GPUMigrate-onedir\`(1145 文件)。
- **字节码级验包**(新脚本 `.tmptest/_pyzcheck10.py`): 不满足于"构建退出码
  0", 从 exe 里把 code object 掏出来看。`CArchiveReader(exe).extract(
  "PYZ.pyz")` 摘出 PYZ, `ZlibArchiveReader` 取模块字节, `marshal.loads`
  成 code object, 递归走 `co_consts`/`co_names` 并用 `dis` 数指令。三条
  断言, 两个 exe 六项全过:
  1. `gpu_monitor` 的常量里有 `' />'` 且无裸 `'/>'` —— §166 阈值线修复
     (f-string 的最后一片修前是 `'/>'`, HTML 不认 `/` 终止未加引号的
     属性值, 值被读成 `"10.7/"` 整条线丢掉);
  2. `gpu_tray` 的 `co_names` 里有 `_FORM_BLOCK_GAP` —— §166 设置窗两段
     名单间隙修复;
  3. `_detail_build` 里 `LOAD_GLOBAL _CTL_GAP` 恰好 2 次 —— §167 孤儿左
     padx 修复(修前 3 次: 第一个按钮的 `padx=(_CTL_GAP, 0)` 也 load 一次;
     三个按钮只有两个间隙, 每个间隙只许有一个属主)。
- **换活实例**: §167 待办里"重启前问用户"的问询, 用户本轮一个"继续"即
  放行。`swap_exe.ps1` 跑通: 停 9992/16452(13:37 起的旧实例) -> 等文件锁
  释放 -> `dist\GPUMigrate.exe` 覆盖仓库根 -> 看门狗同路径
  `cmd /c start "" /B GPUMigrate.exe --ensure` 立即拉起(没等 2 分钟的
  重复触发器)。新实例 PID 21856/32336, 23:54:47 起, 日志"启动监控:
  阈值 50.0%... Web 面板: http://127.0.0.1:8787"。
- **活体验证**: 拉 `http://127.0.0.1:8787/` 实页, 阈值线标签是
  `<line class=thr x1=0 y1=10.7 x2=300 y2=10.7 />` —— §166 那个修复在
  现网生效, 不再是修前那条被浏览器丢掉的线。

### 踩坑

- `_pyzcheck10.py` 第一版把 `_FORM_BLOCK_GAP` 当 `co_consts` 找, 报了假
  FAIL —— **全局名在 `co_names` 不在 `co_consts`**(`LOAD_GLOBAL` 的参数
  是名字索引)。第二版递归走 `co_names`, 并对序列化块直接查字节
  (`b"_FORM_BLOCK_GAP" in marshal.dumps(gt)`)双保险。
- **PYZ 条目是裸 marshal, 没有 16 字节 pyc 头**; 顶层 CArchive 条目才
  有。`try_marshal` 两种都试(先裸后剥 16 字节)才对两种摘法都成立。
- `swap_exe.ps1` 第一版挂在 PowerShell 5.1 的编码上: **无 BOM 的 UTF-8
  `.ps1` 按 GBK 解码**, 中文 `Write-Host` 的字串被吞得引号不配对,
  ParserError 整个脚本不执行(幸好是解析期炸, 什么都没动)。重写成纯
  ASCII 消息后跑通。教训: 这个环境里给 PowerShell 5.1 的脚本, 用户可见
  消息要么纯 ASCII, 要么存成带 BOM 的 UTF-8。
- 看门狗计划任务 `GPUMigrateWatchdog` 的动作是
  `cmd /c start "" /B "...\GPUMigrate.exe" --ensure` 的重复触发器; 手动
  提前拉起必须用**同一条命令**(`--ensure` 是幂等的单实例路径), 直接
  `Start-Process` 裸 exe 会走常驻托盘路径, 和 2 分钟后的看门狗撞出双实例。

### 顺带发现(未改, 记档)

- §167 候选 B/C/D 仍未动(B 详情窗与设置窗的"内容底 -> 按钮行顶"14 vs 16
  是设计决定; C `_DETAIL_WRAP_PX` 注释里"键名列 141"是假的实测 132;
  D 汇总卡已核过没问题)。

### 用户决策待办

1. ~~release `v1.0.0` 的 tag 仍指 `fdea9ae`(§166 那条), §167 的 3px 修复~~
   ~~不在那个 tag 里 —— 现网跑的已是含 §167 的构建, 要不要补一个 v1.0.1~~
   ~~让 tag 跟上现网, 是产品决定。~~**已完成(11-09, 用户一个要字放行)**:
   tag `v1.0.1` 已打并 push, 指 `d097bf6`(§169 现网构建对应的源码树), release
   已建并发布: <https://github.com/cpufreestyle/gpu-migrate/releases/tag/v1.0.1> 。
   v1.0.0 的发布说明("间距统一, 阈值线修复")本身没写错, 只是慢一站。

## 169. 续一百六十八：两个对话框的表单框 expand=True 把「内容 -> 按钮行」撑成大洞, 视觉间隙 44px vs 22px(布局)

### 缺陷

§167 的候选 B 记的是「详情窗与设置窗的『内容底 -> 按钮行顶』14 vs 16, 判定为设计决定」。
那个数量的是**框 padding**(frm 的 pady=_DIALOG_PAD_Y 8 + 按钮行的 pady 6 或 8), 不是
**视觉间隙**。本轮按真渲染窗口量后者, 差得远:

- 设置窗 460x520: toplevel reqheight 492, 窗高保底松弛 28px, 表单框 reqh 432 -> 分配高 460,
  表单内容底(窗内 y) 440, 按钮行顶 484, **视觉间隙 44px**, 按钮行下留白 8。
- 详情窗 520x320: toplevel reqheight 312, 松弛 8px, 表单框 reqh 256 -> 分配高 264,
  内容底 264, 按钮行顶 286, **视觉间隙 22px**, 按钮行下留白 6。

根因两个窗同一套: grid / frm 都写 pack(fill="both", expand=True, padx=_DIALOG_PAD_X,
pady=_DIALOG_PAD_Y)。expand=True 让表单框吞掉窗高保底的松弛, 而 grid 的行没有 weight,
松弛全落在**表单框底部** —— 正好落在「最后一行 -> 按钮行」那一段里。松弛量两窗不同
(28 vs 8), 所以视觉间隙差 22px。附带: 详情窗上下边距 8/6 不对称(设置窗 8/8 对称)。

### 修法

1. 两个表单框去掉 expand=True, 保留 fill="both"。松弛改落按钮下方(设置窗 36px, 详情窗
   12px), 视觉间隙回到 16px = _DIALOG_PAD_Y + 按钮行 pady, 两窗同一个数。
2. _DETAIL_BTN_PAD_Y 6 -> 8(必须等于 _DIALOG_PAD_Y): 否则详情窗这段比设置窗窄 2px, 且
   详情窗自己的上下边距是 8/6 不对称。窗高账 312 -> 316, 仍装得进 _DETAIL_MIN_H=320。

宽度一个像素都没动, 这点单独验过: Tk 的 fill 只看分配区、与 reqwidth 无关 —— 空框架
pack(fill="both") 不写 expand 实测也是满宽(400 宽窗 + padx 20 -> 360)。所以设置窗的表单框
改前改后都是 436px、Entry 都是 64px, 输入框没有被收窄。

### 改动

- gpu_tray.py
  - _settings_build 的 grid.pack(...) 去掉 expand=True(:1356), 补注释说明松弛落在哪。
  - _detail_build 的 frm.pack(...) 去掉 expand=True(:2496), 同上。
  - _DETAIL_BTN_PAD_Y 6 -> 8(:667), 注释写清为什么必须等于 _DIALOG_PAD_Y。
  - 顺手修两处假注释: 详情窗纵向预算原文写「8 行 x 32 + **padx** 10x2 = 276」—— 那是
    pady, 且 10 是 §164 之前的旧 _DIALOG_PAD_Y; 现在是 pady _DIALOG_PAD_Yx2 = 272,
    按钮行 _DETAIL_BTN_PAD_Yx2 + 28 = 44, 合计 316。设置窗纵向预算注释补一句: 那 28px
    余量落在保存键下方, 不是按钮上方的洞。
- tests/test_gpu_monitor.py
  - 新增 TrayPanelStatusFloor.test_neither_dialog_form_frame_eats_the_button_gap(:10345)。
  - 三处替身镜像同步去掉 expand=True: SettingsWindowFitsItsOwnFrame._build(:3387)、
    折行宽度那条(:8528)、外层内边距那条(:10323)。

### 守卫怎么写的

三层, 缺一层就有漏网:

1. **静态**(防「加回去」): 用括号配对取出 grid = tk.Frame(root) / frm = tk.Frame(root)
   之后那个 pack(...) 调用的全文, 断言形状是 .pack(fill="both", 加那两个常量, 且**不出现
   expand**。先滤掉注释行 —— 源码里 pack 之前正好有一段说明为什么不写 expand, 不滤掉取的
   是那段注释(第一版就踩了这个, 报的是「表单框不是 pack(fill=both...): '# 不写 expand...'」)。
2. **几何**(防「只改替身」和「拿宽度换间隙」): 460x520 真渲染一个同款窗, 断言框架**贴身**
   (winfo_height() == winfo_reqheight())、**满宽**(窗宽减两侧内边距)、
   **间隙 == 2*_DIALOG_PAD_Y**。expand 一加回来第一条就红(分配高 476 vs 请求 132)。
3. **常量等式**: _DETAIL_BTN_PAD_Y == _DIALOG_PAD_Y。这两个数不相等, 两个对话框的
   「内容 -> 按钮」间隙就差一个数, 而它们在屏幕上是同一种东西。

既有断言全部自动跟随(用的都是常量本身): budget8 = 8*32 + 2*8 + 2*8 + 28 = 316 <= 320、
budget9 = 256 + 44 + 16 + 16 + 28 = 360 > 320, 两条都还成立;
test_the_old_geometry_squeezed_the_button_flat 也仍成立 —— 总量超限时 pack 压扁的是
**后声明的那个控件**, 与有没有 expand 无关(单独拿 200x100 + 80px 框 + 40px 按钮验过:
expand 开/关都是按钮被压到 20px)。

### 验证结果

- 探针(独立进程, 真渲染两个对话框): 改前 44/22, 改后 16/16; 表单框宽 436/496 不变,
  内容底 440/264 不变, 窗高 520/320 不变, 详情窗 reqheight 312 -> 316。
- gone 分支(9 行): reqheight 364, 三个按钮仍 28px 满血, 视觉间隙 16。
- 全量: python -X utf8 tests/test_gpu_monitor.py -> Ran 679 tests ... OK(30.9s);
  python -X utf8 -m unittest discover -s tests -> Ran 679 tests ... OK(29.7s)。
  679 = 原 678 + 新守卫 1。

### 变异验证(7 条, 全红)

脚本 C:\Users\qm081\AppData\Local\Temp\gpumig_p168\mutate.py, 每条先备份、改完还原,
结束报 restore byte-identical: True。

- M1 设置窗表单框加回 expand=True -> 新守卫(静态 + 几何)。
- M2 详情窗表单框加回 expand=True -> 新守卫(静态 + 几何)。
- M3 _DETAIL_BTN_PAD_Y 退回 6 -> 新守卫(常量等式)。
- M4 保存键 pady=_DIALOG_PAD_Y 退回裸 4 -> test_both_dialogs_share_one_outer_pad。
- M5 按钮行 pady=_DETAIL_BTN_PAD_Y 退回裸 4 -> test_the_detail_button_gaps_are_all_one_number。
- M6 只在守卫替身里加回 expand=True -> 新守卫(几何: 框架不贴身)。
- M7 详情窗 fill="both" 改成 fill="x" -> 新守卫(静态形状)。

M4/M5 说明新守卫**不**负责按钮行 pady 的裸数字(既有守卫负责), 分工清楚。

### 踩过的坑

- **HEAD 已经不是交接里记的 02caf12**: 动手前 git rev-parse HEAD 是 2d51419 —— 另一个写者
  已经把 **§168** 用掉了(「把 §155-§167 同步进现网 exe」)。本轮改续 **§169**, 源码注释与
  测试文档串里的 §168 全部重新编号(6 处)。**每轮开工先 git log --oneline -3**, 别信交接
  快照里的 SHA。
- **gpu_tray.py 与 tests 盘上是 CRLF**(i/lf w/crlf, core.autocrlf=true): 改它们必须
  newline="" 读、按 \r\n 切行、按 \r\n 写回, 写完后断言
  split("\r\n").length == split("\n").length。README / HANDOFF 是纯 LF, 另用一套。
- **这个环境里 apply_patch 不存在**(被 PowerShell 当命令名报 ParserError)。改文件用
  node_repl 的 fs 按行替换 + 逐条断言锚点命中一次, 比 shell heredoc 可靠;
  node_repl 里 require 未定义, 用 await import("node:fs")。
- python -c "..." 的双引号会被外层 PowerShell 吞掉; 而 python -c '...' 又会被 payload 里的
  Python 单引号截断(本轮因此连废两个命令)。**长脚本一律用 node_repl 写文件、再用 python 跑文件。**
- pwsh 里 ForEach-Object { $_.Line } 写在双层引号中, $_ 被外层 PowerShell 5.1 吞掉
  (报「术语 '.Line' 无法识别」)。过滤输出宁可跑完再看, 或用 Select-Object -Last N。
- 同进程里改源码再重量几何无效(模块缓存), 必须起独立 python 进程 —— 探针每次都是新进程。

### 顺带发现(未改, 记档)

- §167 候选 B 的原始记录「14 vs 16 是设计决定」**量的不是同一个东西**(框 padding vs 视觉间隙),
  现已修掉; 候选 C(_DETAIL_WRAP_PX 注释里「键名列 141」是假的, 实测 132px, 余量是 19px 不是
  注释写的 10px)仍未动。
- 详情窗改后按钮下方留白 12px = 8(_DETAIL_BTN_PAD_Y) + 4(窗高保底松弛), 设置窗 36px =
  8 + 28。两个窗的「按钮下留白」仍然不同, 但那是窗高保底与内容的差, 不是间隙缺陷。
- 主面板已核过没问题: 区块间隙 4/4/4/10 全对, 顶部留白 8, 表格按设计用 expand=True 吸收
  松弛(多显几行), 三面行距尺 _FORM_ROW_PITCH == _ROW_H == 32 早已统一。

### 用户决策待办

1. [已完成] 重建产物同步现网: 11-09 00:23 两个 spec 构建 + 冒烟全过, 七项字节码级
   断言全过(§166 阈值线 / §166 设置窗间隙 / §167 孤儿 padx / §169 的
   _DETAIL_BTN_PAD_Y=8 / 两个表单框无 expand / 全模块 expand 关键字数 == 3),
   00:25 换活实例(停 21856/32336, 看门狗路径拉起 36712/41736), §169 的修复
   已在现网。踩坑两条补在 §168: Python 3.14 模块级小整数用 LOAD_SMALL_INT 不是
   LOAD_CONST; KW_NAMES 是元组常量, 验包遍历必须下沉到元组叶子里, 否则
   "无 expand" 那条断言是空转的。
2. §167 候选 C 仍未动(见上)。
## 170. 续一百六十九：Web 表格行还是 29px, 托盘 32px —— §153 提行高时 Web 没跟上, 而守卫拿写死的 28 在算(布局/跨面)

### 为什么是缺陷

§153 把托盘表格行高从 28 提到 32(文字上下留白 6 -> 8px), Web 面板的 td 却
原封不动: `padding:6px 10px` + 自然行盒, 浏览器实测行高 29.85px、行内留白
12px。同一个"表格行", 两个面差 3px 行高、4px 留白 —— 用户在托盘与浏览器之间
切的时候疏密感对不上, 而这件事自 §153 起躺了一百多轮没人发现, 因为盯着它的
守卫自己拿写死的 28 在算托盘侧:

    §141 立契约时: tray_inner = 28 - 16(两个字面量), web 侧 6*2 + round(13*1.2) + 1
    §153 改常量后: _ROW_H 变成 32, 守卫里的 28 没跟着改 -> 等式照样成立, 绿

与 §147/§153 记过的是同一类病(间距常量改了、测试里的字面量不跟着变), 本轮是又一例。
所以修法不是把 28 改成 32 —— 是让等式两边都从现行来源算, 写字面量的一方从此不存在。

### 改动

- gpu_monitor.py: `th,td` 规则 `padding:6px 10px` ->
  `padding:8px 10px;line-height:15px`, 行盒 = 8 + 15 + 8 + border-bottom 1
  = 32, 与托盘 `rowheight=_ROW_H=32` 相等; 行内留白两边都是 16px。
  `line-height` 写死而不靠自然行盒: 13px Segoe UI 的自然行盒实测 17.26px,
  fallback 到别的字体时会变; 写死之后行高在任何机器上都是 32, 与 Tk 的
  rowheight 一样不随字体漂移。
- gpu_tray.py: `_ROW_H` 注释里"字号 9 的 linespace 是 17px、28px 行高留白 5.5px、提到 32
  留白 7.5px"量的是 TkDefaultFont(Microsoft YaHei UI); 表格实际用 `_FONT_UI`(Segoe UI
  Variable Text 9), 实测 linespace 16, 留白数是 6px/8px。§153 当时量错了字体, 留白数跟着
  差了 0.5px, 注释本轮改正。
- tests/test_gpu_monitor.py: 守卫改名重写,
  `test_the_table_row_inner_padding_matches_the_web_panel`
  -> `test_the_table_row_box_matches_the_web_panel`, 从"只对行内留白"
  升级成"对整个行盒"(行高 + 留白, 两边都从现行来源算)。
- 三处测量纠正(都是本轮实测, 不是推的): th 不是 32 是 31; `_TREE_HEAD_H` 注释里"Web 的 th
  是 28.6px, 差 3.4px"更新为 31px/差 1px; 文档串里"th 跟着变 32"同改。

### 守卫怎么写的

三层, 缺一层就有漏网:

1. **静态**(防"规则被拆坏"和"悄悄去掉 line-height"): 从 `gm._PANEL_STYLE` 正则抠出所有
   `th,td{...}` 块, 断言带 padding 的**只有一个**(深色档只覆盖边框色; 多一个少一个都说明
   规则被拆坏, 留白没法对账); 块里必须同时有 `padding:` 和 `line-height:`, 少后者就红 ——
   行高会随 fallback 字体漂移, 不再是钉死的 32。
2. **等式**(防字面量漂移, 本轮的全部意义): 托盘侧 `gt._ROW_H - 16`, Web 侧从抠出来的
   `padding` 算留白(`padding_y * 2`)、算行高(`padding_y * 2 + line-height + 1`);
   断言留白相等、行高 == `_ROW_H`。两边都不出现字面量, 所以 W6(把托盘侧退回写死
   的 28)立刻红。
3. **linespace 16 是唯一保留的字面量**, 因为单测里没有 Tk 根窗口,
   `tkfont.Font` 会抛 RuntimeError; 16 是 `_FONT_UI` 的实测值, 文档串写清它是哪
   个字体的数(TkDefaultFont 的 17 是另一套字体, 表格不用它), 免得下一个人再把它
   "顺手"改成 17。

既有断言全部自动跟随: `test_the_window_floor_covers_the_table_header` 那组
(`_TREE_HEAD_H == 22 + 2*_HEAD_PAD_Y`、实测表头高)不因本轮放松; `_PANEL_MIN_H` 的
纵向预算账面用的一直是 `_ROW_H` 常量本身, 不涉及 Web 侧。

### 验证结果

- 探针(独立进程, 真渲染): `_FONT_UI`(Segoe UI Variable Text 9) linespace **16**,
  TkDefaultFont(Microsoft YaHei UI) 17, `_FONT_NUM`(Consolas 12) 19; 真 Treeview
  rowheight=32、heading_h=32、row pitch 32。
- 浏览器实测(静态 HTML, DPR 1.68): 改前 td 高 29.85px / offsetHeight 30 / 留白 12, th 30;
  改后 td 31.59 / 32 / 16, th 31。td 的 32 与托盘 rowheight 32 相等; th 31 与
  `_TREE_HEAD_H=32` 差 1px(border-collapse 把 th|td 之间那条 1px 对半劈, th 只落下半条,
  圆整比 td 少 1), 比改之前的 28.6/差 3.4px 近, 不从这条硬凑。
- 全量: `python -X utf8 tests/test_gpu_monitor.py` -> Ran 679 tests ... OK
  (31.7s); `python -X utf8 -m unittest discover -s tests` -> Ran 679 ... OK
  (30.7s)。679 = 原 679: 守卫是改名重写, 不增条数。

### 变异验证(7 条, 全红)

脚本 C:\Users\qm081\AppData\Local\Temp\gpumig_p170\mutate170.py,
每条先备份、改完还原, 结束报 restore byte-identical: True。子进程带
`PYTHONDONTWRITEBYTECODE=1` + `-B`、还原后 sleep 1.2s(原因见踩过的坑)。

- W1 Web padding 8 -> 6(§153 之前的旧值) -> 新守卫(留白等式)。
- W2 去掉 line-height -> 新守卫(静态: line-height 不许缺)。
- W3 line-height 15 -> 17(行高 34) -> 新守卫(行高等式)。
- W4 padding 8 -> 7(留白 14) -> 新守卫(留白等式)。
- W5 _ROW_H 32 -> 28(Web 不跟着变) -> 新守卫(留白 + 行高两个等式)。
- W6 守卫退化成 §153 当时的旧写法(托盘侧写死 28) -> 新守卫。**这条就是本轮的意义**: 旧写法
  正是靠这个漏洞绿了一百多轮。
- W7 守卫把 Web 留白写死 12 -> 新守卫。

### 踩过的坑

- **变异脚本的"还原"与 pyc 的同一秒竞态(本轮最陡的一个坑)**: 第一版
  mutate170.py 每条变异改完立刻还原, 而 Python 的 pyc 校验头只存**整秒**
  mtime —— 变异+还原+跑都在同一秒内完成时, 还原后的文件会被判为"没变", 子进程
  加载的是**变异版字节码**。表现很阴: 7 条变异全红(真阳性), 收尾 baseline 却报
  FAILED, 紧接着手动跑的全量套件也 FAILED(failures=1), 而单独复跑守卫又是 OK
  —— 三个结果互相矛盾, 最容易误判成"守卫 flaky"然后去改守卫。修法: 子进程
  `PYTHONDONTWRITEBYTECODE=1` + `-B`, 还原后 sleep 1.2s; 复跑后首尾 baseline
  全 OK、全量两种跑法 679 OK, 与单个守卫结论一致。**教训: 变异脚本的"还原字节
  一致"只证明文件内容, 不证明下一个进程加载的是什么。**
  `python -c` 的双引号会被外层 PowerShell 吞掉; gpu_tray.py / gpu_monitor.py / tests 盘上是
  CRLF(i/lf w/crlf), 按行替换后断言 crlf 数 == lf 数; README / HANDOFF 是纯 LF, 另用一套。
- th 的 31 vs 32: 文档串第一版写"th 跟着变 32", 浏览器实测 offsetHeight 是 31(rect 31.3)。
  按本仓库"说的必须是量到的同一个东西"的规矩, 源码注释、测试文档串、`_TREE_HEAD_H` 注释三处
  一起改正, 并把成因(border-collapse 对半劈)写进注释, 不然下一个人还会"顺手"把它凑成 32。

### 顺带发现(未改, 记档)

- 托盘表头 `_TREE_HEAD_H=32` 与 Web th 31 差 1px: th 高度由 ttk 线性关系(22 + 2*_HEAD_PAD_Y)
  定, Web 侧要凑这 1px 得给 th 单开一条规则(单独 padding-top 或 line-height), 而 th 与 td
  现在共用一条规则 —— 为 1px 拆规则不值, 记档。
- §169 记的候选 C(`_DETAIL_WRAP_PX` 注释里"键名列 141"是假的, 实测 132px)仍未动。
- ~~现网 exe(11-09 00:23 构建)**不含**本轮 Web 行高修复; 要同步需 build -> 字节码级验包 ->
  换活实例(会打断实例, 需用户同意)。~~ **已完成(11-09 03:34 换活实例, PID 28076/28164; 02:55 构建含 §170-§174, §175 不在——写者该轮 03:00 起仍在收尾, 见 §0 现网状态)。**

### 用户决策待办

1. ~~本轮 fix + docs 改动已写盘未 commit/push, 等放行; 提交后回填 §0 快照的 SHA 与未 push 计数。~~ **已完成(11-09 02:50)**: §170-§172(连带写者同批的 §173/§174 代码与测试)已提交为 fix `4e6425f` + docs `0cf2027` 并 push, §0 快照的 SHA 与计数已回填。
2. ~~现网同步(见上)。~~ **已完成(11-09 03:34 换活实例, PID 28076/28164; 02:55 构建含 §170-§174, §175 不在——写者该轮 03:00 起仍在收尾, 见 §0 现网状态)。**
3. §167 候选 C / §169 候选仍未动。

## 171. 续一百七十：Web 面板 head 破损 —— f443408 把 color-scheme meta 插进 viewport 字符串中间, 两条 meta 粘成一团, 幽灵文本把整页顶下去 21px

### 为什么是缺陷

`panel_html` 模板(gpu_monitor.py, 改动前 1975-1978 行)长这样:

    "<html lang='zh-CN'><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width,"
    "<meta name='color-scheme' content='light dark'>"
    "initial-scale=1'>"

第二三行是 f443408(加深色模式)插进来的, 插在了 viewport 那个字符串的**中间**。
四个字符串拼起来, 浏览器按标签解析, 读到的是:

    <meta name='viewport' content='width=device-width,<meta name='color-scheme' content='light dark'>initial-scale=1'>

一条 meta, 而不是两条。现网 http://127.0.0.1:8787/ 实测同样中招(源码与活实例一致),
浏览器自己序列化出来的那条是 `<meta name="viewport" content="width=device-width,&lt;meta name=" color-scheme'="">`。
后果三条:

1. viewport 的 content 到 "width=device-width,<meta name=" 就被引号闭合了 —— **viewport 从未生效**,
   手机浏览器会按 980px 版面渲染再整体缩小(§147 立这条 meta 防的就是这个);
2. **color-scheme meta 从来不存在** —— 深色模式声明是空的, f443408 想做的事一条都没落地;
3. 尾巴上 "initial-scale=1'>" 是 head 里的裸文本, 规范把 head 里的裸文本挪到 body 开头 ——
   页面顶部多出一行幽灵文本, 整页被顶下去 21px(现网 h2 的 top 实测 37.43, 应为 16)。

而盯着 viewport 的守卫(§147 立的 `WebPanelResponsive.test_the_panel_declares_a_viewport_for_phones`)
是两个子串断言: `assertIn("name='viewport'", html)` + `assertIn("width=device-width", html)` ——
破损形态下两个子串都还在, 照样绿。本轮加了对照实验证实这个盲区(见变异验证节)。

### 改动

- gpu_monitor.py: 模板三行并两行, viewport 的 content 自己闭合, color-scheme 独立成行:

      "<meta name='viewport' content='width=device-width,initial-scale=1'>"
      "<meta name='color-scheme' content='light dark'>"

  顺手修中间行那个怪的 4 空格缩进(其余行都是 8)。
- tests/test_gpu_monitor.py: 新增 `_HeadParser`(html.parser 收集器) + `WebPanelHeadStructure`
  6 条用例; 顶部导入 `from html.parser import HTMLParser`。纯标准库, 解析级, 不起浏览器。

### 守卫怎么写的

子串断言对"引号没闭合"全盲, 所以换成**按浏览器的方式解析**: `html.parser` 解析
`panel_html` 的输出, 盯结构不盯字符串。六条:

1. viewport 的 content 原样等于 `width=device-width,initial-scale=1`(精确等式, 不是 in);
2. color-scheme 是独立的 meta, content 等于 `light dark`;
3. head 里恰好 3 条 meta, 名字序列 `[None, "viewport", "color-scheme"]`(charset 那条没有 name 属性);
4. head 里任何属性值不许含 `<` —— 引号没闭合时, 后面的标签会被吃成值的一部分;
5. head 里不许有 title/style/script 之外的裸文本 —— 这就是幽灵文本的判据;
6. body 开口第一个事件就是 `("start", "h2")`, 前面不许有文本或别的标签。

第 5、6 条是同一个缺陷的两个观测面: 解析器看见裸文本在 head 里, 浏览器看见它在 body 开头。
每条用例都过 5 种快照变体(空历史/有历史/有温度/无阈值/暂停中)的 subTest —— head 与数据无关,
但每种快照都得过同一条, 防的是"某个分支把 head 也顺手改了"。

### 验证结果

- 新守卫 6 条 OK(0.023s)。
- 浏览器真渲染复验: 重生成 HTML -> node http 服务(127.0.0.1:8974) -> playwright 实测:
  viewport content == `width=device-width,initial-scale=1`、color-scheme == `light dark`、
  meta 3 条、body 第一个元素是 h2(前面无文本节点)、全文无 initial-scale 幽灵文本;
  h2 的 `getBoundingClientRect().top` = 15.997(16)。截图存 `gpumig_p171\web_panel_fixed.png`。
  console 只有一条 favicon 404(服务没供图标, 无关)。
- 全量两种跑法: `python -X utf8 -B tests/test_gpu_monitor.py` `Ran 685 tests ... OK`(30.7s)、
  `python -X utf8 -B -m unittest discover -s tests` `Ran 685 tests ... OK`(32.5s)。
  首次跑法一撞过一次 `OSError: [WinError 1410] 类已存在`(TraySingleInterpreter 建窗,
  RegisterClass 时序), 复跑全绿 —— 与本次改动无关(改动不碰 Tk), 与 §166 记的
  RegisterClassEx 已知漂白同源。
- 跑完整套后 5 个可哈希的状态文件逐字节不变, config.json 仍不存在,
  gpu_monitor_crash.log 仍被活实例锁着(与跑前同一状态)。

### 变异验证(6 条, 全红; 外加 1 条对照)

- W1 模板改回破损形态 -> 新守卫(5 条红: viewport content / color-scheme / meta 串 / 属性吞标签 / head 裸文本)。
- W2 删掉 color-scheme 整行 -> 新守卫(color-scheme + meta 串两条红)。
- W3 viewport 去掉 initial-scale -> 新守卫(viewport content 红)。
- W4 viewport 多加 maximum-scale -> 新守卫(viewport content 红, 精确等式)。
- W5 color-scheme 只留 light -> 新守卫(color-scheme content 红)。
- W6 body 开头插裸文本 "x" -> 新守卫(body 开口是 h2 红)。
- **对照实验(不是红, 是证实盲区)**: 拿 §147 那条子串守卫跑破损模板 —— **绿**。
  两个子串都在, 这就是它能绿一百多轮的原因, 也是本轮把守卫换成解析级的理由。
- baseline 前后各跑一次全 OK; 子进程 `-B` + `PYTHONDONTWRITEBYTECODE=1`, 每次还原后
  sleep 1.2s(§170 的 pyc 同一秒竞态)。脚本 `gpumig_p171\mutate171.py`。

### 踩过的坑

- 变异脚本第一版用 bytes 字面量装变异串, 直接 SyntaxError:
  "bytes can only contain ASCII literal characters" —— 模板里有中文("GPU 监控")。
  改成 str, 比较/写入前才 encode。
- CRLF 文件按行替换后断言 crlf 数 == lf 数: 这次三行并两行, LF 3379 -> 3378, CRLF 同步。
- 首次全量撞 WinError 1410(见验证结果节), 复跑即绿; 记录在此, 免得下轮误判成"守卫 flaky"。

### 顺带发现(未改, 记档)

- Web `#q` 输入框到表格 0px vs 托盘 4px: §147 明确记录"节奏接近"是有意决策, 本轮核对后仍不动。
- §169 记的候选 C(`_DETAIL_WRAP_PX` 注释里"键名列 141"是假的, 实测 132px)仍未动。
- ~~现网 exe(11-09 00:23 构建)**不含** §170 的 Web 行高修复, 也不含本轮 head 修复; 同步需
  build -> 字节码级验包 -> 换活实例(会打断实例, 需用户同意)。~~ **已完成(11-09 03:34 换活实例, PID 28076/28164; 02:55 构建含 §170-§174, §175 不在——写者该轮 03:00 起仍在收尾, 见 §0 现网状态)。**

### 用户决策待办

1. ~~§170 + §171 两轮的 fix + docs 改动已写盘未 commit/push, 等放行; 提交后回填 §0 快照的
   SHA 与未 push 计数。~~ **已完成(11-09 02:50, 同 §170 待办 1; 写者该轮实际还含 §173/§174, 一并提交)。**
2. ~~现网同步(见上)。~~ **已完成(11-09 03:34 换活实例, PID 28076/28164; 02:55 构建含 §170-§174, §175 不在——写者该轮 03:00 起仍在收尾, 见 §0 现网状态)。**
3. §167 候选 C / §169 候选仍未动。

## 172. 续一百七十一：详情窗折行宽度的注释拿宽字体的测量当真窗口的数字 —— 真窗口那份预算从来没有任何测试碰过(布局/文档)

### 为什么是缺陷

§169 记下的候选 C, 本轮闭环(`_DETAIL_WRAP_PX` 注释与守卫文档串说着同一个
混版本的数)。`_detail_build` 里 wraplength 的注释原文:

    520 宽窗减掉 padx _DIALOG_PAD_Xx2 与键名列 141 之后只剩 355 ...
    345 留 10px 余量给字体差异。

守卫 `test_the_detail_wrap_fits_the_column_it_actually_gets` 的文档串说着同一个
数("最宽那个键名列实测 141px ... 右列只剩 355px")。真渲染实测(探针
`gpumig_p172\probe_detail.py`, 本机):

- 真窗口的键名 Label 写着 `font=_FONT_UI`, 键名列实测 **132px**, 右列可用
  **364px** —— 345 的余量是 19, 不是注释写的 10;
- 141/355 是**默认 Tk 字体**量同一批键名的数(守卫的替身没写 `font=`)。

也就是同一个窗有两份右列预算: 注释和守卫文档说着小的那份(355), 大的那份
(364)没有任何测试碰过, 而"留 10px 余量"里的 10 只在宽字体那份里成立。拿着
141 当"真能用的宽度"的读者, 自己量真窗口会得到 132/364, 对不上账 —— §169
当时就是这么撞上的。更实际的风险: 守卫量的是替身(手抄的键名列表 + 另一种
字体), 源码把键名改长、或给键名换个宽字体, 替身不跟着变, 守卫照样绿, 而真
窗口的长路径已经画到窗户外。对照实验证实了这条盲区(见变异验证节)。

### 改动

- gpu_tray.py: 注释重写, 两份预算分开说 —— 本行 Label 带 `font=_FONT_UI`
  实测 132/右列可用 364; 换成更宽的字体(默认 Tk 字体量同一批键名是 141)只剩
  355; 345 两份预算都进得去(余量 19 / 10), 窄的那份就是留给字体差异的余量,
  换台机器 `_FONT_UI` 的度量变了, 先报警的是它。`_DETAIL_WRAP_PX` 的值不动:
  364 与 355 两份预算都支持 345, 没有要改的证据。
- tests/test_gpu_monitor.py: 旧的那条守卫从 `DetailPrefRefresh` 里搬出, 重写
  成新类 `DetailWrapWidth` 两条:
  1. `test_the_wrap_fits_the_column_it_actually_gets` —— **真窗口直接建出来
     量**(patch `pid_to_name`、替身 `_pref_status`、真快照, 走完整的
     `_detail_build`): 量真窗口的键名列与窗宽、宽字体替身的键名列, 三条断言:
     折行宽度 <= 真窗口可用、<= 宽字体可用、宽字体键名列 >= 真窗口键名列
     (钉死保守预算名不副实时拦不住任何东西)。
  2. `test_the_mirror_key_list_follows_the_source` —— ast 从 `_detail_build`
     源码取键名列表(含 gone 分支 `insert` 的那行), 与替身手抄的列表比对:
     源码加长键名而镜像不跟着改, 当场红。

### 守卫怎么写的

三层, 缺一层就有漏网:

1. **真窗口实测**(防"键名/字体/窗宽变了没人知道"): 不靠替身, 建出来量。
2. **宽字体替身**(保守方向): 保留原守卫的口径, 但现在明确它是"宽字体那份",
   并且有断言钉住它确实更宽。
3. **静态 ast**(防镜像漂移): 替身键名列表与源码同源。

只有替身 -> M3 那条盲区(旧守卫就是这么绿的); 只有真窗口 -> 换机器字体变宽时
没有提前量; 没有 ast -> 镜像漂移会悄悄把保守预算变成乐观预算。

### 验证结果

- 新守卫 2 条 OK; 真窗口实测键名列 132/右列可用 364, 宽字体替身 141/355,
  与改后注释写的数一致。
- 全量两种跑法(提交前复跑; 写本稿后测试文件又有追加, 686 -> 693):
  `python -X utf8 -B tests/test_gpu_monitor.py` `Ran 693 tests ... OK`(72.5s)、
  `python -X utf8 -B -m unittest discover -s tests` `Ran 693`(72.7s, 1 个
  `RegisterClassEx` 已知漂白见 §166, 单跑即过)。
- 跑完整套后 7 个状态文件与跑前一致。

### 变异验证(5 条, 全红; 外加 1 条对照)

- M1 折行 345->360(真窗口 364 装得下, 宽字体 355 装不下) -> 红(宽字体那条:
  "360 not less than or equal to 355")。**这条证明两份预算是分开断言的**:
  真窗口那份没红。
- M2 折行 345->366(真窗口也装不下) -> 红(真窗口那条: 364)。
- M3 源码键名加长("独显%" 后多 "/温度:") -> 红(ast + 真窗口两条: 键名列
  132 -> 170, 可用 326)。
- M4 替身键名列表删掉最宽那条 -> 红(ast + conservatism 两条: 替身 86 < 真 132)。
- M5 键名 Label 换宽字体(`_FONT_UI` -> `_FONT_NUM`) -> 红(真窗口那条: 键名列 187)。
- **对照实验(不是红, 是证实盲区)**: 旧守卫(硬编码键名 + 不写 font + 不建真窗)
  x M3 -> **绿**。键名加长后真窗口的长路径已经装不下(345 > 326), 旧守卫照着
  141/355 照样绿 —— 这就是本轮把守卫换成"建出来量"的理由。
- baseline 前后各跑一次全 OK; 子进程 `-B` + `PYTHONDONTWRITEBYTECODE=1`, 还原后
  sleep 1.2s。脚本 `gpumig_p172\mutate172.py`。

### 踩过的坑

- `inspect.getsource` 拿到的方法源码带类缩进, 直接 `ast.parse` 报
  IndentationError —— 先 `textwrap.dedent`。既有那条守卫只拿 getsource 做子串
  查找, 没这道坎。
- M5 第一版锚点 `font=_FONT_UI).grid(` 在 gpu_tray.py 里有 3 处
  (`_settings_build` 两处 + 详情窗一处), 变异脚本报"锚点不唯一"—— 带 25 空格
  缩进的整行才唯一。锚点计数这道工序又救了一次。

### 顺带发现(未改, 记档)

- Web `#q` -> 表格 0px vs 托盘 4px: §147 的有意决策, 仍不动。
- 详情窗常规形态 reqheight 332(长路径折行占两行时, 316 + 16), gone 分支 380
  —— 都在"窗高按内容现算"的设计里, 不是缺陷。
- §167 候选 B/C/D: B(详情窗按钮行间隙 14 vs 16)已由 §169 闭环, C 本轮闭环,
  D(汇总卡间隙)已核过没问题 —— **记档的候选至此全部清空**, 下一轮要重新审计。
- ~~现网 exe(11-09 00:23 构建)**不含** §170/§171/§172 的修复; 同步需 build ->
  字节码级验包 -> 换活实例(会打断实例, 需用户同意)。~~ **已完成(11-09 03:34 换活实例, PID 28076/28164; 02:55 构建含 §170-§174, §175 不在——写者该轮 03:00 起仍在收尾, 见 §0 现网状态)。**

### 用户决策待办

1. ~~§170 + §171 + §172 三轮的 fix + docs 改动已写盘未 commit/push, 等放行;~~
   **已完成(11-09)**: fix `4e6425f` + docs `0cf2027` 已提交并 push, §0 快照的
   SHA 与未 push 计数已回填(提交前复跑全量 693 OK, 比写本稿时的 686 多 7 条)。
2. ~~现网同步(见上)。~~ **已完成(11-09 03:34 换活实例, PID 28076/28164; 02:55 构建含 §170-§174, §175 不在——写者该轮 03:00 起仍在收尾, 见 §0 现网状态)。**
3. 记档候选已清空(见顺带发现), 下一轮从新审计开始。
