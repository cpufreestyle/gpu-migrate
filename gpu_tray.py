# -*- coding: utf-8 -*-
"""
gpu_tray.py — 托盘模式：系统托盘图标 + 后台监控 + 实时占用面板。

由 gpu_monitor.py --tray 调起。托盘图标实时显示核显总占用（颜色区分），
右键菜单可打开实时面板、暂停/恢复监控、退出。
"""
import os
import threading
import time
from collections import deque
import tkinter as tk
from tkinter import ttk

import pystray
from PIL import Image, ImageDraw, ImageFont

from gpu_monitor import (cmd_monitor, get_gpu_preference, load_config,
                         pid_to_name, save_exclude_process)

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

    _SORT_NUM_COLS = {"pid", "igpu", "imem", "dgpu"}

    def _toggle_sort(self, col):
        st = self.sort_state
        if st["col"] == col:
            st["desc"] = not st["desc"]
        else:
            st["col"], st["desc"] = col, True

    def _sort_rows(self, rows):
        col = self.sort_state["col"]
        desc = self.sort_state["desc"]
        idx = {"name": 0, "pid": 1, "igpu": 2, "imem": 3,
               "dgpu": 4, "pref": 5}.get(col, 2)
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
            root.geometry(ui.get("geometry", "880x360"))
            root.attributes("-topmost", True)

            cols = ("name", "pid", "igpu", "imem", "dgpu", "pref")
            widths = {c: w for c, w in ui.get("widths", {}).items()}
            defaults = {"name": 220, "pid": 60, "igpu": 60, "imem": 80,
                        "dgpu": 60, "pref": 170}
            tree = ttk.Treeview(root, columns=cols, show="headings", height=12)
            for cid, text in (("name", "进程"), ("pid", "PID"),
                              ("igpu", "核显%"), ("imem", "显存MB"),
                              ("dgpu", "独显%"), ("pref", "迁移状态")):
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

    def _refresh_tree(self, tree, tip):
        with self.lock:
            snap = self.snapshot
        rows = []
        if snap:
            pids = (set(snap["util_by_pid"]) | set(snap["mem_by_pid"])
                    | set(snap["dgpu_util_by_pid"]))
            for pid in pids:
                ig = snap["util_by_pid"].get(pid, 0.0)
                im = snap["mem_by_pid"].get(pid, 0.0) / (1024 * 1024)
                dg = snap["dgpu_util_by_pid"].get(pid, 0.0)
                if ig < 0.1 and im < 1 and dg < 0.1:
                    continue
                name = self._pid_name(pid)
                pref = ""
                info = pid_to_name(pid)
                if info and self._pref_of(info[1]):
                    pref = "已设独显(重启生效)"
                rows.append((max(ig, dg), (name, pid, f"{ig:.0f}%",
                                           f"{im:.0f} MB", f"{dg:.0f}%", pref)))
        rows = self._sort_rows(rows)
        tree.delete(*tree.get_children())
        for _ig, row in rows[:30]:
            tree.insert("", "end", values=row)
        if not rows:
            tree.insert("", "end", values=(
                "（当前核显/独显上无进程活动）", "-", "-", "-", "-", "-"))
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
