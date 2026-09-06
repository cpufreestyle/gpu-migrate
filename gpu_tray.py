# -*- coding: utf-8 -*-
"""
gpu_tray.py — 托盘模式：系统托盘图标 + 后台监控 + 实时占用面板。

由 gpu_monitor.py --tray 调起。托盘图标实时显示核显总占用（颜色区分），
右键菜单可打开实时面板、暂停/恢复监控、退出。
"""
import ctypes
import os
import threading
import time
from collections import deque
import tkinter as tk
from tkinter import ttk

import pystray
from PIL import Image, ImageDraw, ImageFont

from gpu_monitor import (cmd_monitor, clear_gpu_preference,
                         get_gpu_preference, load_config, pid_to_name,
                         save_exclude_process, set_gpu_preference,
                         nvml_gpu_temp)

import sys
_DIR = (os.path.dirname(os.path.abspath(sys.executable))
        if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))

_UI_FILE = os.path.join(_DIR, "panel_ui.json")
_CONFIG_PATH = os.path.join(_DIR, "config.json")
_APP_ICON = os.path.join(
    getattr(sys, "_MEIPASS", _DIR), "app.ico")


def _make_icon_image(pct):
    """按核显占用画托盘图标: 绿<30 <橙<60 <红。"""
    pct = max(0, min(100, pct))
    if pct < 30:
        color = (76, 175, 80, 255)
    elif pct < 60:
        color = (255, 152, 0, 255)
    else:
        color = (244, 67, 54, 255)
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, 61, 61], fill=color)
    try:
        font = ImageFont.truetype("arialbd.ttf", 26)
    except OSError:
        font = ImageFont.load_default()
    text = str(int(round(pct)))
    bbox = d.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((64 - w) / 2 - bbox[0], (64 - h) / 2 - bbox[1]), text,
           fill=(255, 255, 255, 255), font=font)
    return img


class TrayApp:
    def __init__(self, config_path):
        self.config_path = config_path
        self.cfg = load_config(config_path)
        self.paused = False
        self.stop = False
        self.panel_open = False
        self.lock = threading.Lock()
        self.snapshot = None
        self._name_cache = {}
        self._pref_cache = {}       # exe -> 迁移状态, 10 秒过期
        self._pref_ts = 0.0
        self.history = deque(maxlen=600)   # (ts, 核显总占用%) 每 10 秒一点
        self.sort_state = {"col": "igpu", "desc": True}
        self._root_ref = None

        self.icon = pystray.Icon(
            "GPUMigrate",
            icon=_make_icon_image(0),
            title="GPU 监控 (启动中...)",
            menu=pystray.Menu(
                pystray.MenuItem("打开实时面板", self._on_open_panel,
                                 default=True),
                pystray.MenuItem("暂停监控", self._on_toggle_pause),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda item: "模式: " + self._mode_text(),
                                 None, enabled=False),
                pystray.MenuItem("性能模式 (阈值10%)", self._on_mode_perf),
                pystray.MenuItem("智能模式 (恢复配置)", self._on_mode_smart),
                pystray.MenuItem("省电模式 (阈值40%+自动切回)",
                                 self._on_mode_saver),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("打开设置", self._on_open_settings),
                pystray.MenuItem("退出", self._on_quit),
            ))

    # ---- 托盘菜单回调 ----

    def _on_open_panel(self, icon=None, item=None):
        if self.panel_open:
            return
        self.panel_open = True
        threading.Thread(target=self._panel_main, daemon=True).start()

    def _on_toggle_pause(self, icon=None, item=None):
        self.paused = not self.paused
        icon.title = ("GPU 监控 (已暂停)" if self.paused else "GPU 监控")

    def _on_quit(self, icon=None, item=None):
        self.stop = True
        icon.stop()

    # ---- 模式切换 (写 config.json, 热重载生效) ----

    def _mode_text(self):
        try:
            import json as _json
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = _json.load(f)
            return {"performance": "性能", "saver": "省电"}.get(
                cfg.get("mode", "smart"), "智能")
        except (OSError, ValueError):
            return "智能"

    def _apply_mode(self, mode, threshold, ps_auto):
        try:
            import json as _json
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = _json.load(f)
            cfg["mode"] = mode
            if threshold is not None:
                cfg["threshold_percent"] = threshold
            if ps_auto is not None:
                cfg["power_saver_auto"] = ps_auto
            with open(self.config_path, "w", encoding="utf-8") as f:
                _json.dump(cfg, f, ensure_ascii=False, indent=2)
        except (OSError, ValueError):
            pass

    def _on_mode_perf(self, icon=None, item=None):
        self._apply_mode("performance", 10, False)

    def _on_mode_smart(self, icon=None, item=None):
        self._apply_mode("smart", None, None)

    def _on_mode_saver(self, icon=None, item=None):
        self._apply_mode("saver", 40, True)

    # ---- 设置窗口 ----

    def _on_open_settings(self, icon=None, item=None):
        if getattr(self, "_settings_open", False):
            return
        self._settings_open = True
        threading.Thread(target=self._settings_main, daemon=True).start()

    def _settings_main(self):
        try:
            root = tk.Tk()
            root.title("GPU 监控设置")
            root.geometry("460x420")
            root.attributes("-topmost", True)
            cfg = load_config(self.config_path)
            rows = []
            grid = tk.Frame(root)
            grid.pack(fill="both", expand=True, padx=12, pady=8)

            def add_entry(label, key, width=10):
                tk.Label(grid, text=label, anchor="w").grid(
                    row=len(rows), column=0, sticky="w", pady=2)
                var = tk.StringVar(value=str(cfg.get(key, "")))
                e = tk.Entry(grid, textvariable=var, width=width)
                e.grid(row=len(rows), column=1, sticky="w", pady=2)
                rows.append(var)
                return var

            v_thr = add_entry("迁移阈值 %", "threshold_percent")
            v_vram = add_entry("显存阈值 MB", "vram_threshold_mb")
            v_bthr = add_entry("电池阈值 %", "battery_threshold_percent")
            v_temp = add_entry("温度提醒 °C", "temp_limit")
            v_port = add_entry("Web 端口(0关)", "web_port")

            v_ar = tk.BooleanVar(value=bool(cfg.get("auto_restart")))
            v_cm = tk.BooleanVar(value=bool(cfg.get("confirm_mode")))
            v_pa = tk.BooleanVar(value=bool(cfg.get("power_saver_auto")))
            v_ed = tk.BooleanVar(value=bool(cfg.get("exclude_defaults", True)))
            r = len(rows)
            for i, (text, var) in enumerate((
                    ("自动重启迁移的进程", v_ar), ("迁移前弹窗确认", v_cm),
                    ("低负载自动切回核显", v_pa), ("启用内置排除名单", v_ed))):
                tk.Checkbutton(grid, text=text, variable=var).grid(
                    row=r + i, column=0, columnspan=2, sticky="w", pady=2)

            r2 = r + 4
            tk.Label(grid, text="游戏名单(每行一个)", anchor="w").grid(
                row=r2, column=0, sticky="nw", pady=2)
            t_games = tk.Text(grid, width=30, height=4)
            t_games.grid(row=r2, column=1, sticky="w", pady=2)
            t_games.insert("1.0", "\n".join(cfg.get("game_processes", [])))
            r2 += 5
            tk.Label(grid, text="排除名单(每行一个)", anchor="w").grid(
                row=r2, column=0, sticky="nw", pady=2)
            t_excl = tk.Text(grid, width=30, height=4)
            t_excl.grid(row=r2, column=1, sticky="w", pady=2)
            t_excl.insert("1.0", "\n".join(cfg.get("exclude_processes", [])))

            def save():
                import json as _json
                try:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        cfg2 = _json.load(f)
                except (OSError, ValueError):
                    cfg2 = {}
                def num(var, old_v, cast):
                    try:
                        return cast(var.get())
                    except ValueError:
                        return old_v
                cfg2["threshold_percent"] = num(v_thr,
                                                cfg2.get("threshold_percent", 20), float)
                cfg2["vram_threshold_mb"] = num(v_vram,
                                                cfg2.get("vram_threshold_mb", 1024), float)
                cfg2["battery_threshold_percent"] = num(
                    v_bthr, cfg2.get("battery_threshold_percent", 40), float)
                cfg2["temp_limit"] = num(v_temp, cfg2.get("temp_limit", 85), float)
                cfg2["web_port"] = num(v_port, cfg2.get("web_port", 0), int)
                cfg2["auto_restart"] = v_ar.get()
                cfg2["confirm_mode"] = v_cm.get()
                cfg2["power_saver_auto"] = v_pa.get()
                cfg2["exclude_defaults"] = v_ed.get()
                cfg2["game_processes"] = [x.strip() for x in
                                          t_games.get("1.0", "end").splitlines()
                                          if x.strip()]
                cfg2["exclude_processes"] = [x.strip() for x in
                                             t_excl.get("1.0", "end").splitlines()
                                             if x.strip()]
                with open(self.config_path, "w", encoding="utf-8") as f:
                    _json.dump(cfg2, f, ensure_ascii=False, indent=2)
                root.title("已保存, 配置热重载生效")
                self._settings_open = False
                root.after(1200, root.destroy)

            tk.Button(root, text="保存 (热重载立即生效)", command=save,
                      bg="#4cd964").pack(pady=8)
            root.protocol("WM_DELETE_WINDOW",
                          lambda: (setattr(self, "_settings_open", False),
                                   root.destroy()))
            root.mainloop()
        except Exception:
            pass
        finally:
            self._settings_open = False

    # ---- 监控线程 ----

    def _before_cycle(self):
        if self.stop:
            return False
        while self.paused and not self.stop:
            time.sleep(0.5)
        return not self.stop

    def _on_sample(self, snap):
        with self.lock:
            self.snapshot = snap
        total = max(snap["util_by_pid"].values()) if snap["util_by_pid"] else 0.0
        names = {l: self._gpu_name(l) for l in
                 snap["igpu_luids"] | snap["dgpu_luids"]}
        ig = " + ".join(names.get(l, l) for l in snap["igpu_luids"]) or "-"
        self.icon.title = (f"GPU 监控  核显占用 {total:.0f}%\n核显: {ig}"
                           + ("  [已暂停]" if self.paused else ""))
        self.icon.icon = _make_icon_image(total)

    def _gpu_name(self, luid):
        with self.lock:
            snap = self.snapshot
        if snap:
            entry = snap["kind_cache"].get(luid)
            if entry:
                return entry[1]  # (kind, name)
        return "LUID ..." + luid[-4:]

    def _monitor_thread(self):
        try:
            with open(os.path.join(_DIR, "tray_debug.log"), "a",
                      encoding="utf-8") as f:
                f.write(time.strftime("[%H:%M:%S] monitor enter") + chr(10))
        except OSError:
            pass
        try:
            cmd_monitor(self.cfg, self.config_path,
                        hooks={"before_cycle": self._before_cycle,
                               "on_sample": self._on_sample,
                               "on_history_point": self._on_history_point})
        except Exception:  # 后台线程兜底，错误落盘便于排查
            import traceback
            try:
                with open(os.path.join(_DIR, "tray_error.log"), "w",
                          encoding="utf-8") as f:
                    f.write(traceback.format_exc())
            except OSError:
                pass

    def _on_history_point(self, ts, total):
        # 只写队列; 严禁从监控线程调用 tkinter (跨线程会让面板消息循环卡死)
        self.history.append((ts, total))

    _SORT_NUM_COLS = {"pid", "gpu", "igpu", "dgpu", "dmem", "smem"}

    def _toggle_sort(self, col):
        st = self.sort_state
        if st["col"] == col:
            st["desc"] = not st["desc"]
        else:
            st["col"], st["desc"] = col, True

    def _sort_rows(self, rows):
        col = self.sort_state["col"]
        desc = self.sort_state["desc"]
        idx = {"name": 0, "pid": 1, "gpu": 2, "igpu": 3, "dgpu": 4,
               "dmem": 5, "smem": 6, "pref": 7}.get(col, 2)
        numeric = col in self._SORT_NUM_COLS

        def key(row):
            v = row[1][idx]
            if numeric:
                try:
                    return float(str(v).replace("%", "").replace(" MB", ""))
                except ValueError:
                    return -1.0
            return str(v)

        return sorted(rows, key=key, reverse=desc)

    # ---- 实时面板 (tkinter, 独立线程) ----

    def _pid_name(self, pid):
        if pid not in self._name_cache:
            info = pid_to_name(pid)
            self._name_cache[pid] = info[0] if info else f"pid {pid}"
            if len(self._name_cache) > 2000:
                self._name_cache.clear()
        return self._name_cache[pid]

    def _pref_of(self, full_path):
        now = time.time()
        if now - self._pref_ts > 10:   # 缓存 10 秒, 避免每秒批量查注册表
            self._pref_cache.clear()
            self._pref_ts = now
        if full_path not in self._pref_cache:
            self._pref_cache[full_path] = \
                get_gpu_preference(full_path) == "GpuPreference=2;"
        return self._pref_cache[full_path]

    def _panel_main(self):
        try:
            root = tk.Tk()
        except Exception:
            self.panel_open = False
            return
        try:
            root.title("GPU 占用监控")
            try:
                root.iconbitmap(_APP_ICON)
            except Exception:
                pass
            self._root_ref = root

            # 恢复上次窗口位置/大小/列宽
            ui = {}
            try:
                import json as _json
                with open(_UI_FILE, "r", encoding="utf-8") as f:
                    ui = _json.load(f)
            except (OSError, ValueError):
                pass
            root.geometry(ui.get("geometry", "1020x360"))
            root.attributes("-topmost", True)

            cols = ("name", "pid", "gpu", "igpu", "dgpu", "dmem", "smem",
                    "pref")
            widths = {c: w for c, w in ui.get("widths", {}).items()}
            defaults = {"name": 180, "pid": 55, "gpu": 55, "igpu": 55,
                        "dgpu": 55, "dmem": 75, "smem": 75, "pref": 160}
            tree = ttk.Treeview(root, columns=cols, show="headings", height=12)
            for cid, text in (("name", "进程"), ("pid", "PID"),
                              ("gpu", "GPU%"), ("igpu", "核显%"),
                              ("dgpu", "独显%"), ("dmem", "专用MB"),
                              ("smem", "共享MB"), ("pref", "迁移状态")):
                tree.heading(cid, text=text,
                             command=lambda c=cid: self._toggle_sort(c))
                tree.column(cid, width=widths.get(cid, defaults[cid]),
                            anchor="center")
            tree.pack(fill="both", expand=True, padx=6, pady=(6, 0))

            spark = tk.Canvas(root, height=46, bg="#1e1e28",
                              highlightthickness=0)
            spark.pack(fill="x", padx=6, pady=(0, 0))

            tip = tk.Label(root, text="", anchor="w", fg="#666")
            tip.pack(fill="x", padx=8, pady=(0, 6))

            # 右键菜单: 永不迁移
            menu = tk.Menu(root, tearoff=0)

            def _never(event):
                item = tree.identify_row(event.y)
                if not item:
                    return
                vals = tree.item(item, "values")
                if not vals:
                    return
                pname = vals[0]
                menu.tk_popup(event.x_root, event.y_root)

            def _never_add():
                sel = tree.selection()
                if not sel:
                    return
                pname = tree.item(sel[0], "values")[0]
                save_exclude_process(self.config_path, pname)
                tip.config(text=f"已把 {pname} 加入永不迁移名单 (写入 config.json, 立即生效)")

            menu.add_command(label="永不迁移此程序", command=_never_add)
            tree.bind("<Button-3>", _never)
            tree.bind("<Double-1>",
                      lambda e: self._open_detail(tree, e))

            def refresh():
                # 任何异常都不允许中断 after 链, 否则面板从此不再刷新
                try:
                    self._refresh_tree(tree, tip)
                    self._draw_sparkline(spark)
                except Exception:
                    pass
                if self.panel_open and not self.stop:
                    root.after(1000, refresh)

            def on_close():
                self.panel_open = False
                try:
                    import json as _json
                    ui = {"geometry": root.geometry(),
                          "widths": {c: tree.column(c, width=None)
                                     for c in cols}}
                    with open(_UI_FILE, "w", encoding="utf-8") as f:
                        _json.dump(ui, f, ensure_ascii=False)
                except (OSError, ValueError):
                    pass
                root.destroy()

            root.protocol("WM_DELETE_WINDOW", on_close)
            refresh()
            root.mainloop()
        except Exception:
            pass
        finally:
            self.panel_open = False

    def _draw_sparkline(self, canvas):
        try:
            canvas.delete("all")
            pts = list(self.history)[-180:]   # 最近 30 分钟
            w = int(canvas.winfo_width() or 860)
            h = int(canvas.winfo_height() or 46)
            if len(pts) < 2:
                canvas.create_text(w / 2, h / 2, fill="#667",
                                   text="核显占用趋势 (每 10 秒一点)")
                return
            ymax = max(30.0, max(v for _t, v in pts))
            step = w / (len(pts) - 1)
            coords = []
            for i, (_t, v) in enumerate(pts):
                coords += [i * step, h - 4 - (h - 10) * v / ymax]
            canvas.create_line(coords, fill="#4cd964", width=2)
            canvas.create_text(w - 6, 6, anchor="ne", fill="#99a",
                               text=f"近{len(pts)*10//60}分钟 核显占用 峰值{max(v for _t, v in pts):.0f}%")
        except Exception:
            pass

    def _open_detail(self, tree, event):
        item = tree.identify_row(event.y)
        if not item:
            return
        vals = tree.item(item, "values")
        if not vals or not str(vals[1]).isdigit():
            return
        pid = int(vals[1])
        threading.Thread(target=self._detail_window, args=(pid, vals),
                         daemon=True).start()

    def _detail_window(self, pid, vals):
        try:
            root = tk.Tk()
        except Exception:
            return
        try:
            root.title(f"进程详情 - pid {pid}")
            root.geometry("520x300")
            root.attributes("-topmost", True)
            try:
                root.iconbitmap(_APP_ICON)
            except Exception:
                pass
            info = pid_to_name(pid)
            full = info[1] if info else "未知 (需要管理员权限)"
            name = info[0] if info else "?"

            # 启动时间 (GetProcessTimes)
            start_str = "未知"
            k32 = ctypes.WinDLL("kernel32")
            k32.OpenProcess.restype = ctypes.c_void_p
            h = k32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED
            if h:
                class FT2(ctypes.Structure):
                    _fields_ = [("dw", ctypes.c_ulonglong * 2)]
                ct_, et_, kt_, ut_ = FT2(), FT2(), FT2(), FT2()
                if k32.GetProcessTimes(h, ctypes.byref(ct_),
                                       ctypes.byref(et_), ctypes.byref(kt_),
                                       ctypes.byref(ut_)):
                    ft = ctypes.c_ulonglong(ct_.dw[0] | (ct_.dw[1] << 32))
                    if ft:
                        ts = ft / 1e7 - 11644473600
                        start_str = time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(ts))
                k32.CloseHandle(ctypes.c_void_p(h))

            snap = self.snapshot or {}
            ig = snap.get("util_by_pid", {}).get(pid, 0.0)
            dg = snap.get("dgpu_util_by_pid", {}).get(pid, 0.0)
            de = snap.get("dedicated_by_pid", {}).get(pid, 0.0) / 1048576
            sh = snap.get("shared_by_pid", {}).get(pid, 0.0) / 1048576
            on_dgpu = dg > ig
            where = "独显" if on_dgpu else ("核显" if ig > 0.1 else "未见 GPU 活动")

            frm = tk.Frame(root)
            frm.pack(fill="both", expand=True, padx=14, pady=10)
            lines = [
                ("进程名", name), ("PID", str(pid)),
                ("完整路径", full), ("启动时间", start_str),
                ("当前所在", where),
                ("GPU% / 核显% / 独显%",
                 f"{vals[2]} / {vals[3]} / {vals[4]}"),
                ("专用/共享内存", f"{de:.0f} MB / {sh:.0f} MB"),
            ]
            for i, (k, v) in enumerate(lines):
                tk.Label(frm, text=k + ":", anchor="ne", fg="#666").grid(
                    row=i, column=0, sticky="ne", pady=2)
                lb = tk.Label(frm, text=v, anchor="w", wraplength=380,
                              justify="left")
                lb.grid(row=i, column=1, sticky="w", pady=2)
            frm.columnconfigure(1, weight=1)

            def do_set():
                if os.path.isfile(full):
                    set_gpu_preference(full)
                    self._detail_msg(root, f"已设为独显: 重启 {name} 后生效")

            def do_unset():
                if os.path.isfile(full):
                    clear_gpu_preference(full)
                    self._detail_msg(root, f"已清除 GPU 设置: {name}")

            btns = tk.Frame(root)
            btns.pack(pady=6)
            tk.Button(btns, text="立即设为独显", command=do_set).pack(
                side="left", padx=6)
            tk.Button(btns, text="撤销独显设置", command=do_unset).pack(
                side="left", padx=6)
            tk.Button(btns, text="关闭", command=root.destroy).pack(
                side="left", padx=6)
            root.mainloop()
        except Exception:
            pass

    @staticmethod
    def _detail_msg(root, text):
        root.title(text)

    def _refresh_tree(self, tree, tip):
        with self.lock:
            snap = self.snapshot
        rows = []
        if snap:
            pids = (set(snap["util_by_pid"]) | set(snap["gpu_all_by_pid"])
                    | set(snap["dgpu_util_by_pid"])
                    | set(snap["shared_by_pid"])
                    | set(snap["dedicated_by_pid"]))
            for pid in pids:
                gpu_all = snap["gpu_all_by_pid"].get(pid, 0.0)
                ig = snap["util_by_pid"].get(pid, 0.0)
                dg = snap["dgpu_util_by_pid"].get(pid, 0.0)
                de = snap["dedicated_by_pid"].get(pid, 0.0) / (1024 * 1024)
                sh = snap["shared_by_pid"].get(pid, 0.0) / (1024 * 1024)
                if gpu_all < 0.1 and de < 1 and sh < 1:
                    continue
                name = self._pid_name(pid)
                pref = ""
                info = pid_to_name(pid)
                if info and self._pref_of(info[1]):
                    pref = "已设独显(重启生效)"
                rows.append((max(gpu_all, de, sh),
                             (name, pid, f"{gpu_all:.0f}%", f"{ig:.0f}%",
                              f"{dg:.0f}%", f"{de:.0f}", f"{sh:.0f}", pref)))
        rows = self._sort_rows(rows)
        tree.delete(*tree.get_children())
        for _ig, row in rows[:30]:
            tree.insert("", "end", values=row)
        if not rows:
            tree.insert("", "end", values=(
                "（当前无进程占用 GPU）", "-", "-", "-", "-", "-", "-", "-"))
        if snap:
            ig_luids = ", ".join(self._gpu_name(l)
                                 for l in snap["igpu_luids"]) or "-"
            dg_luids = ", ".join(self._gpu_name(l)
                                 for l in snap["dgpu_luids"]) or "-"
            tip.config(text=f"核显: {ig_luids}    独显: {dg_luids}"
                            f"    刷新: 1 秒    右键进程可设为永不迁移")

    def run(self):
        threading.Thread(target=self._monitor_thread, daemon=True).start()
        self.icon.run()


def run_tray(config_path=None):
    if config_path is None:
        config_path = _CONFIG_PATH
    try:
        TrayApp(config_path).run()
    except Exception:  # --noconsole 下主线程错误也要能排查
        import traceback
        try:
            with open(os.path.join(_DIR, "tray_error.log"), "a",
                      encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except OSError:
            pass
        raise
