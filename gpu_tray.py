# -*- coding: utf-8 -*-
"""
gpu_tray.py — 托盘模式：系统托盘图标 + 后台监控 + 实时占用面板。

由 gpu_monitor.py --tray 调起。托盘图标实时显示核显总占用（颜色区分），
右键菜单可打开实时面板、暂停/恢复监控、退出。
"""
import ctypes
import os
import queue
import threading
import time
from collections import deque
import tkinter as tk
import winreg
from tkinter import font as tkfont
from tkinter import ttk

import pystray

os_name = os.name
from PIL import Image, ImageDraw, ImageFont

from gpu_monitor import (activity_key, cmd_monitor, clear_gpu_preference,
                         COL_LABELS, MIB,
                         _lum, _ratio,
                         COL_LABELS,
                         empty_row_text, get_gpu_preference,
                         gpu_where_text, heat_class, _HDR_TIPS,
                         hidden_noise_count, is_high_performance, load_config,
                         mode_text, panel_summary, PREF_HIGH_TEXT,
                         pid_to_name, policy_text,
                         read_json_dict, row_is_noise, save_exclude_process,
                         set_gpu_preference, spark_corner_text,
                         spark_geometry, spark_points, spark_y_of,
                         spark_ymax,
                         _utf16_len,
                         SETTINGS_LABELS,
                         _ellipsize, write_json_dict)

import sys
_DIR = (os.path.dirname(os.path.abspath(sys.executable))
        if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))

_UI_FILE = os.path.join(_DIR, "panel_ui.json")
_CONFIG_PATH = os.path.join(_DIR, "config.json")
_APP_ICON = os.path.join(
    getattr(sys, "_MEIPASS", _DIR), "app.ico")


def _dbg(msg):
    """托盘自身的排查日志。

    静默退出过一次的话, 没有这一行就只能靠"日志最后一行"猜; stop 标志
    把"用户点了退出"和"消息循环被外力打断"分开。
    """
    try:
        with open(os.path.join(_DIR, "tray_debug.log"), "a",
                  encoding="utf-8") as f:
            f.write(time.strftime("[%H:%M:%S] ") + msg + "\n")
    except OSError:
        pass


# ========================================================= Windows 11 Fluent
# 两套 Fluent 配色(浅色/深色), 启动时跟着系统"应用/深色"偏好选一套 —— 这是
# WinUI 应用的标准行为, 也是目前 Windows 上最流行的界面风格: Mica 背景、
# 8px 圆角卡片、1px 低对比描边、Segoe UI Variable 字体、强调色只给焦点。
# 与 Web 面板的 _PANEL_STYLE 一一对应, 两个界面看起来是一家。
# 每个色值都按 WCAG AA 小字 4.5:1 选过, TrayPaletteLegibility /
# WebPanelLegibility 两套用例把契约钉死, 换色请先跑测试。
_FLUENT_LIGHT = {
    "bg": "#f3f3f3",        # 桌面底(Fluent layer fill alt / Mica 之上的底)
    "card": "#ffffff",      # 卡片底(Fluent card background fill default)
    "alt": "#f1f1f1",       # 隔行底: 比卡片暗一档, 30 行扫读不串行
    "line": "#e4e4e4",      # 卡片描边(Fluent card stroke)
    "fg": "#1a1a1a",        # 正文(Fluent text fill primary)
    "muted": "#5d5d5d",     # 次要文字(Fluent text fill secondary)
    "hover": "#ebebeb",     # 悬停行底
    "head": "#f9f9f9",      # 表头底
    "accent": "#0078d4",    # Windows 强调色: 焦点框 / 主按钮
    "accent_press": "#0067c0",
    "save_fg": "#ffffff",     # 强调色按键上的字(4.53:1)
    "btn": "#ffffff",       # 普通按钮底
    "btn_hover": "#f1f1f1", # 悬停按钮底
    "sel": "#cfe4f7",       # 选中底(强调色提亮一档)
    "hot": "#b02a1a",       # 热度三档: Win11 自己的红/黄/绿文字色
    "warm": "#8a5200",
    "cool": "#0b5d08",
    "spark_fill": "#dff5df",
}
_FLUENT_DARK = {
    "bg": "#202020",
    "card": "#2d2d2d",
    "alt": "#343434",
    "line": "#404040",
    "fg": "#ffffff",
    "muted": "#b4b4b4",
    "hover": "#3d3d3d",
    "head": "#272727",
    "accent": "#4cc2ff",
    "accent_press": "#2fb6f5",
    "save_fg": "#1a1a1a",      # 深色强调色很亮, 得配深字
    "btn": "#2d2d2d",
    "btn_hover": "#383838",
    "sel": "#2a3f52",
    "hot": "#ff99a4",
    "warm": "#ffce4e",
    "cool": "#6ccb5b",
    "spark_fill": "#1c3a28",
}


def _fluent_scheme():
    """读系统的"应用模式"偏好: 1=浅色(默认), 0=深色。

    关机后再开机、或在设置里切换深浅, 这一份偏好会变; 键读不到(Win10
    之前的版本 / 精简系统)就当浅色 —— 猜错只是配色不跟系统, 功能无影响。
    """
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion"
                r"\Themes\Personalize") as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "dark" if not int(value) else "light"
    except (OSError, ValueError):
        return "light"


def _hex_to_rgb(color):
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


def _darken_hex(color, amount=0.12):
    r, g, b = _hex_to_rgb(color)
    k = 1.0 - amount
    return "#%02x%02x%02x" % (int(r * k), int(g * k), int(b * k))


def _readable_fg(bg_hex):
    bg = _hex_to_rgb(bg_hex)
    white = (255, 255, 255)
    black = (0, 0, 0)
    if _contrast_ratio(bg, white) >= _contrast_ratio(bg, black):
        return "#ffffff"
    return "#000000"


# WCAG 相对亮度与对比度: 判据只有一个出处(gpu_monitor._lum / _ratio),
# 这里只做别名。托盘、Web、测试三方各抄一遍公式的时代过去了 ——
# 抄出来的三份今天数值一致, 但谁改了公式另外两个不知道, 而 legibility
# 守卫用的正是同一个判据: 测试那把尺和生产这把尺一旦分叉, 守的就
# 不再是生产代码了。
_luminance = _lum
_contrast_ratio = _ratio



_DWM_ACCENT_KEY = (r"Software\Microsoft\Windows\DWM", "AccentColor")

_PERSONALIZE_ACCENT_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
    "AccentColor")


def _accent_from_registry():
    for path, name in (_PERSONALIZE_ACCENT_KEY, _DWM_ACCENT_KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                value, kind = winreg.QueryValueEx(key, name)
        except (OSError, ValueError):
            continue
        if kind != winreg.REG_DWORD:
            continue
        v = int(value)
        r, g, b = v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF
        hexed = "#%02x%02x%02x" % (r, g, b)
        if hexed.lower() in ("#000000", "#ffffff"):
            continue
        return hexed
    return None


def _fluent_accent(base_accent=None):
    found = _accent_from_registry()
    if found:
        return found
    if base_accent:
        return base_accent
    return _PALETTE["accent"]


_FLUENT_SCHEME = _fluent_scheme()
_PALETTE = _FLUENT_DARK if _FLUENT_SCHEME == "dark" else _FLUENT_LIGHT


def _use_palette(pal):
    """把全套派生色值换到指定 palette 上; 返回换过没有。

    打开面板时调(见面板构建): 用户在设置里切换深浅后不用重启程序, 下次
    打开就跟着换。测试也靠它强制两套主题 —— 开发机是深色的, 浅色那套
    否则一次都没被跑到过。
    """
    global _PALETTE, _BG, _PANEL_BG, _LINE, _FG, _MUTED, _ACCENT
    global _BTN_BG, _BTN_HOVER, _SEL_BG, _SAVE_FG, _HEAT_COLOR
    global _ROW_ALT_BG, _SPARK_FILL
    accent = _fluent_accent(pal.get("accent"))
    if pal is _PALETTE and accent == _PALETTE.get("accent"):
        return False
    pal = dict(pal)
    pal["accent"] = accent
    pal["accent_press"] = _darken_hex(accent)
    pal["save_fg"] = _readable_fg(accent)
    _PALETTE = pal
    _BG = pal["bg"]
    _PANEL_BG = pal["card"]
    _LINE = pal["line"]
    _FG = pal["fg"]
    _MUTED = pal["muted"]
    _ACCENT = pal["accent"]
    _BTN_BG = pal["btn"]
    _BTN_HOVER = pal["btn_hover"]
    _SEL_BG = pal["sel"]
    _SAVE_FG = pal["save_fg"]
    _HEAT_COLOR = {"hot": pal["hot"], "warm": pal["warm"],
                   "cool": pal["cool"], "": _FG}
    _ROW_ALT_BG = pal["alt"]
    _SPARK_FILL = pal["spark_fill"]
    return True


_use_palette(_PALETTE)   # 导入时就按系统偏好定好一套

# 之后全文件都用这几个名字 —— 换肤只改上面的字典, 不用挨个改控件。
_BG = _PALETTE["bg"]
_PANEL_BG = _PALETTE["card"]
_LINE = _PALETTE["line"]
_FG = _PALETTE["fg"]
_MUTED = _PALETTE["muted"]
_ACCENT = _PALETTE["accent"]
_BTN_BG = _PALETTE["btn"]
_BTN_HOVER = _PALETTE["btn_hover"]
_SEL_BG = _PALETTE["sel"]
_SAVE_FG = _PALETTE["save_fg"]
_HEAT_COLOR = {"hot": _PALETTE["hot"], "warm": _PALETTE["warm"],
               "cool": _PALETTE["cool"], "": _FG}
# 隔行浅色(zebra striping): 偶数行用卡片底, 奇数行垫一层暗一档的浅底, 30
# 行的长列表横向扫读时不串行。和 Web 面板的 alt 同一个值。
# 热度 tag 只设 foreground, 隔行 tag 只设 background, 叠在同一行不互覆盖。
_ROW_ALT_BG = _PALETTE["alt"]
# 趋势图折线下面的填充: 浅色下几乎融进卡片, 只作"有面积"的暗示
_SPARK_FILL = _PALETTE["spark_fill"]
_ROW_ALT_TAG = "rowalt"
# Win11 的 UI 字体是 Segoe UI Variable Text; 老系统只有 Segoe UI。都缺
# (精简安装 / 远程桌面会话)退 Tahoma —— 比 Tk 默认那身更像原生控件。
# 机器上到底装了哪个, 由 _rebuild_fonts() 在 Tk 就绪后查出来填上。
_FONT_UI_SPEC = ("Segoe UI Variable Text", 9)
_FONT_UI = _FONT_UI_SPEC
_FONT_UI_BOLD = (_FONT_UI_SPEC[0], _FONT_UI_SPEC[1], "bold")
_FONT_NUM = ("Consolas", 12)
_FILTER_HINT = "筛选进程名或 PID (/ 聚焦, Esc 清空)"
# 列名只有一份, 在 gpu_monitor.COL_LABELS: 托盘 / Web / CLI 三个界面
# 一起用它。这里以前自己抄了一份字典, 改列名漏一处就三张脸。
_COL_LABELS = COL_LABELS


def sort_button_text(state):
    """面板"复原排序"按钮的文案。

    默认档(auto)没有对应表头, 点过某列后就回不来; 这个按钮就是回头
    路。默认档时说明当前就是三面同序的活动量排序, 单列档时提示能
    一键复原。
    """
    col = (state or {}).get("col")
    if col == "auto":
        return "排序: 活动量(默认)"
    return "复原到自动(%s)" % _COL_LABELS.get(col, col)


def pause_button_text(paused):
    """托盘菜单"暂停/恢复"项的文案, 跟着当前是否暂停翻转。

    静态写死"暂停监控"会指错: 用热键停过之后, 悬停已显示 [已暂停],
    菜单却还写"暂停监控", 用户不确定当前状态, 再点一下还以为是"暂停",
    其实是"恢复"。和下面"模式: X"那行一样用动态文案, 让这一项变成
    "点了会发生什么"的准确提示。
    """
    return "恢复监控" if paused else "暂停监控"


# Shell_NotifyIcon 的 szTip 是 TCHAR[128]: 超过 127 个字符的话,
# 整条悬停提示会直接不显示, 而不是被截断。用户看到的就是
# "鼠标停上去什么都没有", 还以为监控挂了。
_TIP_MAX = 127


def fit_tip_title(base, suffix=""):
    """把悬停提示压进 127 个 UTF-16 码元, 优先砍显卡名那一行。

    超长时先砍最后一行(显卡名最长也最不要紧), 占用率和生效策略
    两行必须留住; 砍到放不下再做硬截断。
    """
    title = base + suffix
    if _utf16_len(title) <= _TIP_MAX:
        return title
    head, sep, tail = base.rpartition("\n")
    if sep and tail:
        room = _TIP_MAX - _utf16_len(head) - _utf16_len(sep) \
            - _utf16_len(suffix)
        if room >= 4:
            title = head + sep + _ellipsize(tail, room) + suffix
            if _utf16_len(title) <= _TIP_MAX:
                return title
    # 硬截断也按码元砍: 从中间折断代理对会单留一个孤子代理,
    # Windows 端只看得到一个无效字符
    out, used = "", 0
    for ch in title:
        w = _utf16_len(ch)
        if used + w > _TIP_MAX:
            break
        out += ch
        used += w
    return out


def _row_pid(vals):
    """从一行渲染好的 values 里取 PID, 取不到给 None。

    占位行("还没有采样到数据"那一行)的 PID 列是 "-", 前端 UI 上它和
    真行长得一样。不挡这一下, "回车进详情"会拿 "-" 去建详情窗,
    "永不迁移"会把 "-" 写进 config.json 的排除名单。
    """
    if not vals:
        return None
    try:
        text = str(vals[1]).strip()
    except (TypeError, IndexError):
        return None
    return int(text) if text.isdigit() else None


def _sel_item(tree):
    """当前选中行的 item id; 没有选中或控件不返回序列时给 None。

    ttk.Treeview 的 selection() 在真 Tk 下返回 tuple; 测试替身与
    MagicMock 不一定有这个方法, 所以不许它把调用方带崩。
    """
    try:
        sel = tree.selection()
    except (AttributeError, TypeError):
        return None
    if not isinstance(sel, (tuple, list)) or not sel:
        return None
    return sel[0]


def _reselect_pid(tree, pid):
    """重新渲染后把选中项还给 pid 那一行。

    刷新是每秒一次的 tree.delete + tree.insert, 选中态每次都跟着被
    清空 —— 于是用键盘上下键选择时, 光标每秒被踢回起点, 键盘导航
    根本用不了。这里按进程身份(PID)而不是行号找回选中项: 排序一变
    行号就全变, PID 才是用户心里盯着的那个东西。
    """
    if pid is None:
        return False
    try:
        kids = tree.get_children()
        if not isinstance(kids, (tuple, list)):
            return False
    except (AttributeError, TypeError):
        return False
    for iid in kids:
        try:
            vals = tree.item(iid, "values")
        except (AttributeError, TypeError):
            return False
        if _row_pid(vals) == pid:
            tree.selection_set(iid)
            try:
                tree.focus(iid)
                tree.see(iid)
            except (AttributeError, TypeError):
                pass
            return True
    return False


def _row_num(vals, idx, bad=0.0):
    """把表格单元格("45.2%" / "120")还原成 float, 缺列或脏值一律 0.0。

    详情窗要的是"点击那一行"的读数, 而行是以渲染好的字符串存着的;
    直接 float() 会把 "45.2%" 和缺列两种情况一起抛, 详情窗就开不出来。

    排序那一侧原来自己写了一份 `str(v).replace("%","").replace(" MB","")`,
     .rstrip("%") 管不了嵌在中间的单位。口径只有一份: 这里既服务详情窗,
    也服务点表头排序 —— 两处对同一列给出两种读数的规矩不可接受。
    """
    try:
        return float(str(vals[idx]).strip().rstrip("%").replace("MiB", "")
                     .replace("MB", ""))
    except (IndexError, ValueError, AttributeError):
        return bad


# clam 是 Windows 内置主题里唯一肯整体改色的一个
_DARK_THEME = "clam"


def _heat_color(cls):
    """热度类 -> 颜色。空类用正文色, 免得汇总数一排三种灰。"""
    return _HEAT_COLOR.get(cls, _FG)


_TIP_DELAY_MS = 500        # 停多久才弹: 一扫而过不该弹泡
_TIP_WRAP_PX = 320          # 弹泡内容折行宽度, 比面板还宽就没意义了


def _bind_tip(widget, text):
    """给 Tk 控件挂原生 tooltip (Enter 弹 / Leave 收)。

    Tk 的 Label 没有 tooltip 属性, 汇总卡又是"标签 + 数字"两个 Label
    拼的, 光看那两样分不清"占用取最忙进程"和"显存取跨适配器求和"
    的区别。Web 端用 title 属性就有了, 托盘这边自己实现一个, 两边
    文案从 panel_summary 同一份取。
    """
    if not text:
        return
    state = {"win": None, "after": None}

    def _hide(_event=None):
        if state["after"] is not None:
            try:
                widget.after_cancel(state["after"])
            except Exception:
                pass
            state["after"] = None
        win = state["win"]
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
            state["win"] = None

    def _show(_event=None):
        if state["win"] is not None or not widget.winfo_exists():
            return
        win = tk.Toplevel(widget)
        state["win"] = win
        try:
            win.wm_overrideredirect(True)
            win.wm_attributes("-topmost", True)
            win.configure(bg=_LINE)
            lab = tk.Label(win, text=text, bg=_PANEL_BG, fg=_FG,
                           font=_FONT_UI, justify="left",
                           wraplength=_TIP_WRAP_PX, padx=8, pady=6)
            lab.pack()
            widget.update_idletasks()
            x = widget.winfo_rootx()
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            # 弹泡超出屏幕右缘时收回内侧: 汇总卡在窗口右侧, 面板又常
            # 常贴着屏幕右边放, 不收敛的话 tooltip 整个开在视口外。
            w = win.winfo_reqwidth()
            sw = win.winfo_screenwidth()
            if x + w + 8 > sw:
                x = max(0, sw - w - 8)
            win.wm_geometry("+%d+%d" % (x, y))
        except Exception:
            _hide()

    def _schedule(_event=None):
        if state["win"] is not None:
            return
        _hide()
        state["after"] = widget.after(_TIP_DELAY_MS, _show)

    widget.bind("<Enter>", _schedule)
    widget.bind("<Leave>", _hide)
    widget.bind("<ButtonPress>", _hide)
    return state


def _bind_tree_tip(tree, col, text):
    """给 ttk.Treeview 的某一列列头挂悬停解释。

    Treeview 的列头不是独立 widget, 拿不到可以直接绑 Enter 的东西;
    heading 名称之外的整个表头区都要经过一次 Motion 分发。鼠标移进
    列头时才弹, 移开即收, 与卡片 tooltip 同一套延迟。
    """
    if not text:
        return
    state = {"win": None, "after": None, "col": col}

    def _hide(_event=None):
        if state["after"] is not None:
            try:
                tree.after_cancel(state["after"])
            except Exception:
                pass
            state["after"] = None
        win = state["win"]
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
            state["win"] = None

    def _show(_event=None):
        if state["win"] is not None or not tree.winfo_exists():
            return
        try:
            win = tk.Toplevel(tree)
            state["win"] = win
            win.wm_overrideredirect(True)
            win.wm_attributes("-topmost", True)
            win.configure(bg=_LINE)
            tk.Label(win, text=text, bg=_PANEL_BG, fg=_FG,
                     font=_FONT_UI, justify="left",
                     wraplength=_TIP_WRAP_PX, padx=8, pady=6).pack()
            # 列头没有现成 bbox: 取列在表内的 x 偏移, y 贴表格顶边。
            x = tree.winfo_rootx() + (tree.column(col, "width") or 0)
            y = tree.winfo_rooty()
            w = win.winfo_reqwidth()
            sw = win.winfo_screenwidth()
            if x + w + 8 > sw:
                x = max(0, sw - w - 8)
            win.wm_geometry("+%d+%d" % (x, y))
        except Exception:
            _hide()

    def _motion(event):
        # identify_column 返回 '#N'; 表头之外(行区/空白)一律不当列头
        ident = tree.identify_column(event.x)
        if not ident.startswith("#"):
            _hide()
            return
        try:
            idx = int(ident[1:]) - 1
        except ValueError:
            _hide()
            return
        if idx < 0 or idx >= len(cols_all):
            _hide()
            return
        cid = cols_all[idx]
        if cid != col:
            _hide()
            return
        if state["win"] is not None:
            return
        if state["after"] is not None:
            return
        state["after"] = tree.after(_TIP_DELAY_MS, _show)

    cols_all = tuple(tree.cget("columns"))
    tree.bind("<Motion>", _motion, add=True)
    # 窗口一关, 弹泡不能留在屏幕上: 面板 destroy 时 Toplevel 子窗会
    # 跟着走, 但 after 链不会, 这里补一次兜底。
    tree.bind("<Leave>", _hide, add=True)
    state["hide"] = _hide
    return state
def _rebuild_fonts():
    """按机器上真装了的字体挑 UI 字族(没有 Tk 解释器时保持首选值)。

    模块导入阶段就有一堆代码引用 _FONT_UI, 那时还没有 Tk 实例; 这个函数
    只在 _apply_fluent_theme() 里被调用(面板窗口已经建出来了), 所以查询
    失败只需保持首选值, 不能崩 —— 首选值 Tk 自己会替换, 最多字形不同。
    """
    global _FONT_UI, _FONT_UI_BOLD
    try:
        have = {f.lower() for f in tkfont.families()}
    except Exception:
        return
    fam = next((c for c in ("Segoe UI Variable Text", "Segoe UI", "Tahoma")
                if c.lower() in have), None)
    if fam and fam != _FONT_UI[0]:
        _FONT_UI = (fam, _FONT_UI[1])
        _FONT_UI_BOLD = (fam, _FONT_UI_BOLD[1], _FONT_UI_BOLD[2])


def _apply_fluent_theme():
    """给 ttk 刷 Fluent(Windows 11)皮肤; 主题不可用就保持默认。

    clam 是 ttk 内置主题里唯一肯整体换色的一个, 别的主题只吃几个属性,
    刷完一半新一半旧。配色全部来自 _PALETTE, 深浅两套通用。
    """
    _rebuild_fonts()
    style = ttk.Style()
    try:
        style.theme_use(_DARK_THEME)
    except tk.TclError:
        return
    style.configure(".", background=_BG, foreground=_FG,
                    fieldBackground=_PANEL_BG, troughColor=_BG,
                    selectBackground=_SEL_BG, selectForeground=_FG,
                    bordercolor=_LINE, lightcolor=_PANEL_BG,
                    darkcolor=_PANEL_BG, insertcolor=_FG)
    # Win11 的行高点: 12px 数字留出上下呼吸, 也够着触摸目标的高度
    style.configure("Treeview", background=_PANEL_BG, foreground=_FG,
                    fieldbackground=_PANEL_BG, borderwidth=0, rowheight=28,
                    font=_FONT_UI)
    style.configure("Treeview.Heading", background=_PALETTE["head"],
                    foreground=_MUTED, relief="flat", padding=(6, 5),
                    font=_FONT_UI)
    style.map("Treeview.Heading",
              background=[("active", _PALETTE["hover"])])
    style.configure("TEntry", fieldbackground=_PANEL_BG, foreground=_FG,
                    bordercolor=_LINE, insertcolor=_FG)
    # Fluent 按钮: 扁平、留白大、悬停/聚焦才换色
    style.configure("TButton", background=_BTN_BG, foreground=_FG,
                    bordercolor=_LINE, focusthickness=0, padding=(10, 6))
    style.map("TButton", background=[("active", _BTN_HOVER)],
              focuscolor=[("focus", _ACCENT)])
    # 行按占用上色: 一眼看出谁在顶阈值
    for cls, color in _HEAT_COLOR.items():
        if cls:
            style.configure(f"heat.{cls}", foreground=color)


# DWM: Windows 11 的 Mica 背景 + 圆角窗口 + 深色标题栏。都是"能用就用":
# Win10 / 精简系统上 DwmSetWindowAttribute 直接失败, 界面照常是 Fluent 实心
# 配色 —— 材质是加分项, 不是可用性的前提。
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20        # Win10 1809+: 深色标题栏
_DWMWA_WINDOW_CORNER_PREFERENCE = 33       # Win11 22000+: 圆角窗口
_DWMWA_SYSTEMBACKDROP_TYPE = 38            # Win11 22621+: Mica
_DWMWCP_ROUND = 2
_DWMSBT_MAINWINDOW = 2


def _apply_win11_chrome(root):
    """让窗口按 Win11 的形状 / 标题栏 / 材质亮出来。

    圆角: Tk 画不出圆角控件, 但窗口本身圆了, 整框一眼就是 Win11 的形。
    标题栏深浅: 深色主题下不给深色标题栏, 客户区和标题栏会割裂成两半。
    Mica 如实说: Tk 的客户区是不透明的, 墙纸透不出来, 所以这条只是向
    Windows 声明窗口材质类型; 真正看得见的是下面那张 Fluent 桌面底。
    留着它是为了将来不铺满的窗口(托盘气泡/详情窗)能直接吃到 Mica。
    """
    try:
        root.update_idletasks()
        hwnd = int(root.winfo_id())
        dwm = ctypes.windll.dwmapi
    except Exception:
        return

    def _put(attr, value):
        try:
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value),
                                      ctypes.sizeof(value))
        except Exception:
            pass

    _put(_DWMWA_SYSTEMBACKDROP_TYPE, ctypes.c_int(_DWMSBT_MAINWINDOW))
    _put(_DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.c_int(_DWMWCP_ROUND))
    _put(_DWMWA_USE_IMMERSIVE_DARK_MODE,
         ctypes.c_int(1 if _PALETTE is _FLUENT_DARK else 0))
# 经典(tk)控件不吃 ttk 主题: 不显式配色, 设置窗/详情窗就是系统默认的
# 浅色窗, 和深色面板并排像两个程序。按 winfo_class 分类配色, 认不出的
# 类只给底/前景, 不猜它支持什么选项 —— 硬塞不支持的选项真 tk 会 TclError。
_CLASSIC_BASE = {"bg": _BG, "fg": _FG}
_CLASSIC_FLUENT = {
    "Frame": {"bg": _BG},
    "Label": {"bg": _BG, "fg": _FG},
    # Fluent 按钮: 扁平、白底, 悬停才换浅一档(深色主题由 palette 决定)
    "Button": {"bg": _BTN_BG, "fg": _FG,
               "activebackground": _BTN_HOVER, "activeforeground": _FG},
    "Checkbutton": {"bg": _BG, "fg": _FG, "activebackground": _BG,
                    "activeforeground": _FG, "selectcolor": _PANEL_BG},
    "Entry": {"bg": _PANEL_BG, "fg": _FG, "insertbackground": _FG,
              "selectbackground": _SEL_BG, "selectforeground": _FG},
    "Text": {"bg": _PANEL_BG, "fg": _FG, "insertbackground": _FG,
             "selectbackground": _SEL_BG, "selectforeground": _FG},
    "Menu": {"bg": _PANEL_BG, "fg": _FG, "activebackground": _SEL_BG,
             "activeforeground": _FG},
}


def _darken_tree(root):
    """把经典 tk 窗口连同子孙刷成面板的深色 Palette。

    面板 (_panel_build) 是一个控件一个控件显式配色的; 设置窗和详情窗
    走的是同一条 UI 线程、同一个深色审美, 逐个再写一遍既啰嗦又会漏,
    所以按类统一刷。单个控件 configure 失败(个别 Tk 版本选项集不同)
    只跳过它, 不能带崩整窗。
    """
    stack = [root]
    while stack:
        w = stack.pop()
        stack.extend(w.winfo_children())
        try:
            w.configure(**_CLASSIC_FLUENT.get(w.winfo_class(),
                                            _CLASSIC_BASE))
        except tk.TclError:
            pass


def _icon_rgba(cls):
    """热度类 -> PIL 要的 RGBA 元组。

    图标原来自己存了一套 RGB, 和表格/汇总卡的十六进制色对不上, 同一个数
    在托盘和面板里会显出两种热度; 现在都从 _HEAT_COLOR 换算。
    """
    hexed = _heat_color(cls).lstrip("#")
    return (int(hexed[0:2], 16), int(hexed[2:4], 16),
            int(hexed[4:6], 16), 255)


def _bold_font(size):
    """按可用性挑一个粗体字模; 都没装才退 PIL 内置位图字。

    只认 arialbd 的机器(精简版/部分英文版 Windows 可能没装)会落到 load_default(),
    那是个 11px 左右的位图字, 缩到 16x16 任务栏后数字糊成一团。
    依次试 Segoe UI Bold(系统自带)和微软雅黑粗体, 再退内置。
    """
    for name in ("arialbd.ttf", "segoeuib.ttf", "msyhbd.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _load_ui_state():
    """读 panel_ui.json, 任何不对都退回"没存过"。

    这文件是人也能改的: 存成 `[]` / `"abc"` / `123` 都是合法 JSON,
    `widths` 写成列表、`geometry` 写成数字也同样合法。老代码拿到手就
    `.get(...)`, 顶层不是对象当场 AttributeError, 被 _panel_build 的总
    except 吞掉之后 —— 窗口建一半扔下, 且每次点"打开实时面板"都照样炸,
    用户看到的就是"菜单点了没反应"。与其猜用户存了什么, 不如校验型状,
    不对就当没存过: 丢一次布局能忍, 面板从此打不开不能忍。
    """
    try:
        import json as _json
        with open(_UI_FILE, "r", encoding="utf-8") as f:
            ui = _json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(ui, dict):
        _dbg(f"panel_ui.json 顶层不是对象({type(ui).__name__}), 按默认布局打开")
        return {}
    if not isinstance(ui.get("geometry"), str):
        ui["geometry"] = "1040x470"
    widths = ui.get("widths")
    if isinstance(widths, dict):
        # 列宽只收得下正数: 字符串和 None 会让 tree.column 抛 TclError
        ui["widths"] = {c: w for c, w in widths.items()
                        if isinstance(w, int) and not isinstance(w, bool)
                        and w > 0}
    else:
        ui["widths"] = {}
    sort = ui.get("sort")
    if not isinstance(sort, dict):
        ui["sort"] = {}
    return ui


def _parse_geometry(text):
    """把 "1040x470+100+200" 拆成 (宽, 高, x, y); 形状不对返回 None。

    Tcl 只认这一种形状。手里这份是人也能够手改的文件, 存成 "abc" /
    "1040x" / "-10x-10" 一点都不奇怪; 直接喂给 root.geometry() 就是
    TclError, 被 _panel_build 的总 except 吞掉之后窗口建一半扔下 ——
    用户看到的是"打开面板点了没反应", 和崩溃长得一模一样。
    没有位置的那一半("1040x470")按 +0+0 算。
    """
    if not isinstance(text, str):
        return None
    head, sep, rest = text.strip().partition("x")
    if not sep or not head.isdigit() or not rest:
        return None
    cut = 0
    while cut < len(rest) and rest[cut].isdigit():
        cut += 1
    if not cut:
        return None
    w, h = int(head), int(rest[:cut])
    tail = rest[cut:]
    offsets = []
    while tail:
        sign = -1 if tail[0] == "-" else 1
        if tail[0] in "+-":
            tail = tail[1:]
        digits = ""
        while tail and tail[0].isdigit():
            digits += tail[0]
            tail = tail[1:]
        if not digits or len(offsets) == 2:
            return None
        offsets.append(sign * int(digits))
    return w, h, offsets[0] if offsets else 0, offsets[1] if len(offsets) > 1 else 0


def _onscreen_geometry(text, screen_w, screen_h, default="1040x470"):
    """拿得出手的 geometry: 形状不对或整窗在屏幕外, 就还给默认值。

    Tcl 对"窗口开在看不见的地方"一声不吭: 面板上次拖到副屏, 副屏拔了
    之后 geometry 还是 +1920+300 那一串, 窗口每次都开在屏幕外 —— 用户
    看到的还是"点了菜单什么都没发生"。所以位置要按当前虚拟屏收一遍:
    标题栏至少得留 120x30 在屏内, 否则当越界。
    """
    parsed = _parse_geometry(text)
    if parsed is None:
        return default
    w, h, x, y = parsed
    if x + 120 < 0 or x > screen_w - 120 or y + 30 < 0 or y > screen_h - 30:
        return default
    return "%dx%d%+d%+d" % (w, h, x, y)


def _screen_size(root):
    """虚拟桌面尺寸(多屏取并集), 拿不到就退回主屏, 再不行给个常见桌面。

    两个 winfo_* 都可能返回不是数字的东西(替身窗口、还没映射的根窗口、
    平台不实现), 拿不到尺寸不该让面板打不开 —— 越界判定退化成"不判"
    也只是少一层保险, 而抛异常会让整个 _panel_build 半路死掉。
    """
    for probe in (lambda: (root.winfo_vrootwidth(), root.winfo_vrootheight()),
                  lambda: (root.winfo_screenwidth(), root.winfo_screenheight())):
        try:
            w, h = int(probe()[0]), int(probe()[1])
        except Exception:
            continue
        if w > 0 and h > 0:
            return w, h
    return 1280, 800


def _make_icon_image(pct):
    """按核显占用画托盘图标: 凉<30 <温<60 <热, 配色与表格同一份。"""
    pct = max(0, min(100, pct))
    color = _icon_rgba(heat_class(pct))
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, 61, 61], fill=color)
    font = _bold_font(26)
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
        self.monitor_dead = False
        self.panel_request = False
        self.lock = threading.Lock()
        self.snapshot = None
        self._name_cache = {}       # pid -> 名字, 10 秒过期
        self._name_ts = 0.0
        self._title_base = "GPU 监控 (启动中...)"
        self._settings_open = False
        # 所有 tkinter 窗口共用的那一个 Tcl 解释器, 由 _ui_main 线程持有
        self._ui_queue = queue.Queue()
        self._ui_thread = None
        self._ui_root = None
        self._ui_windows = 0
        self._pref_cache = {}       # exe -> 迁移状态, 10 秒过期
        self._pref_ts = 0.0
        self.history = deque(maxlen=600)   # (ts, 核显总占用%) 每 10 秒一点
        self.sort_state = {"col": "auto", "desc": True}
        self.filter_text = ""      # 面板筛选框的子串 (空 = 全显)
        self._root_ref = None

        self.icon = pystray.Icon(
            "GPUMigrate",
            icon=_make_icon_image(0),
            title="GPU 监控 (启动中...)",
            menu=pystray.Menu(
                pystray.MenuItem("打开实时面板", self._on_open_panel,
                                 default=True),
                pystray.MenuItem(
                    lambda item: pause_button_text(self.paused),
                    self._on_toggle_pause),
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
        self._ui_post(self._panel_build)

    def _on_toggle_pause(self, icon=None, item=None):
        self.paused = not self.paused
        self._refresh_title()

    def _refresh_title(self):
        """标题 = 最近一次采样的内容 + 暂停后缀; 内容没变就不动托盘。

        暂停既可能来自菜单也可能来自热键, 两处都走这里。热键轮询线程每 0.2s
        调一次, 而 Windows 上每次改 title 都要重建托盘图标 —— 所以必须先比一下。
        """
        if self.monitor_dead:
            return
        title = fit_tip_title(self._title_base or "GPU 监控",
                              "  [已暂停]" if self.paused else "")
        if title == getattr(self, "_title_shown", None):
            return
        self._title_shown = title
        try:
            self.icon.title = title
        except Exception:
            pass

    def _on_quit(self, icon=None, item=None):
        self.stop = True
        icon.stop()

    # ---- 模式切换 (写 config.json, 热重载生效) ----

    def _mode_text(self):
        """菜单上那行是"选中的模式"(直接读 config.json), 生效值看 tooltip。"""
        return mode_text((read_json_dict(self.config_path) or {}).get("mode"))

    def _apply_mode(self, mode, threshold, ps_auto):
        cfg = read_json_dict(self.config_path)
        if cfg is None:          # 半截 JSON: 覆盖写会把用户剩余设置冲掉
            return
        cfg["mode"] = mode
        if threshold is not None:
            cfg["threshold_percent"] = threshold
        if ps_auto is not None:
            cfg["power_saver_auto"] = ps_auto
        if not write_json_dict(self.config_path, cfg):
            # 写不进还录一行: 否则菜单点完没反应, 下次拉起菜单时才发现模式没切
            _dbg(f"模式切换写入失败: {self.config_path}")

    def _on_mode_perf(self, icon=None, item=None):
        self._apply_mode("performance", 10, False)

    def _on_mode_smart(self, icon=None, item=None):
        self._apply_mode("smart", None, None)

    def _on_mode_saver(self, icon=None, item=None):
        self._apply_mode("saver", 40, True)

    # ---- 设置窗口 ----

    def _on_open_settings(self, icon=None, item=None):
        if self._settings_open:
            return
        self._settings_open = True
        self._ui_post(self._settings_build)

    # ---- 唯一一个 Tcl 解释器 (所有窗口都活在这里) ----
    #
    # tkinter 的规矩: 每一个 Tk 调用都必须发生在创建解释器的那一个线程里。
    # 老实现给面板、设置、详情各开一个 tk.Tk() 且各跑一个 mainloop(), 三个
    # 解释器并发进出 Tcl, 偶发把整个进程带崩 (而且崩了连 tray_error.log 都没)。
    # 现在只有 UI 线程创建一个隐藏的宿主 root, 所有窗口都是它的 Toplevel,
    # 整个进程只有一个 mainloop; 别的线程一律通过 _ui_post 递请求。
    _UI_POLL_MS = 50
    # 类级默认: 只有实例属性的话, 跳过 __init__ 的工具
    # (旧版本迁移剪贴、测试具的 object.__new__)会在 _refresh_tree
    # 里面向一个不存在的属性 —— 面板当场白屏。
    monitor_dead = False

    def _ui_post(self, fn, *args):
        """把函数排进 UI 线程执行; 任何线程都能调。"""
        self._ui_queue.put((fn, args))
        if self._ui_thread is None or not self._ui_thread.is_alive():
            self._ui_thread = threading.Thread(target=self._ui_main,
                                               daemon=True)
            self._ui_thread.start()

    def _ui_main(self):
        try:
            root = tk.Tk()
        except Exception:
            return
        self._ui_root = root
        root.withdraw()          # 宿主不显示, 只为持有这一个解释器

        def pump():
            while True:
                try:
                    fn, args = self._ui_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn(*args)
                except Exception:
                    pass          # 一个请求炸了不能带走消息循环
            root.after(self._UI_POLL_MS, pump)

        root.after(self._UI_POLL_MS, pump)
        root.mainloop()

    def _settings_build(self):
        """在 UI 线程里建设置窗口 (Toplevel, 不新建解释器)。"""
        if self._ui_root is None:
            self._settings_open = False
            return
        counted = False
        try:
            root = tk.Toplevel(self._ui_root)
            counted = True
            self._ui_windows += 1
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
                # Enter 直接保存: 六个格子 Tab 走完再摸鼠标去点那个按钮,
                # 键盘用户的手要在键盘和鼠标之间来回搬一趟。save 定义在
                # 下面, lambda 调用时才查名, 赶得上(与面板 refresh 同款)。
                e.bind("<Return>", lambda _e=None: save())
                rows.append(var)
                return var

            v_thr = add_entry("迁移阈值 %", "threshold_percent")
            v_vram = add_entry("显存阈值 MiB", "vram_threshold_mb")
            v_bthr = add_entry("电池阈值 %", "battery_threshold_percent")
            v_temp = add_entry("温度提醒 °C", "temp_limit")
            v_int = add_entry("采样周期 秒", "interval_seconds")
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
                cfg2 = read_json_dict(self.config_path)
                if cfg2 is None:
                    root.title("保存失败: config.json 无法解析，已保留原文件")
                    return

                bad = []
                cfg2["threshold_percent"] = self._settings_num(
                    v_thr, cfg, "threshold_percent", float, bad)
                cfg2["vram_threshold_mb"] = self._settings_num(
                    v_vram, cfg, "vram_threshold_mb", float, bad)
                cfg2["battery_threshold_percent"] = \
                    self._settings_num(v_bthr, cfg,
                                       "battery_threshold_percent", float, bad)
                cfg2["temp_limit"] = self._settings_num(
                    v_temp, cfg, "temp_limit", float, bad)
                cfg2["interval_seconds"] = self._settings_num(
                    v_int, cfg, "interval_seconds", float, bad)
                cfg2["web_port"] = self._settings_num(
                    v_port, cfg, "web_port", int, bad)
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
                if not write_json_dict(self.config_path, cfg2):
                    root.title("保存失败: 配置文件不可写")
                    return
                # 有格子没吃进输入就得说出来: 只说"已保存"等于宣称
                # 那些值生效了, 而它们还是旧值。窗口会自己关掉,
                # 不说一声用户连哪一格白填了都不知道。
                notes = []
                if bad:
                    notes.append("/".join(SETTINGS_LABELS.get(k, k)
                                          for k in bad)
                                 + " 不是数字, 仍用原值")
                if cfg2.get("web_port") != cfg.get("web_port"):
                    # web_port 只在启动时注册一次, 热重载改不动它: 
                    # 这时说"已生效"是谎话, 用户会去刷新一个根本没变的端口
                    notes.append("Web 端口已改, 需重启监控后生效")
                if notes:
                    root.title("已保存, 但 " + "; ".join(notes))
                    delay = 2500
                else:
                    root.title("已保存, 配置热重载生效")
                    delay = 1200
                root.after(delay, close)

            def close():
                self._settings_open = False
                self._ui_windows -= 1
                root.destroy()

            saver = tk.Button(root, text="保存 (热重载立即生效)",
                              command=save, bg=_ACCENT, fg=_FG)
            saver.pack(pady=8)
            root.protocol("WM_DELETE_WINDOW", close)
            # Esc = 取消, 和面板 / 详情窗同一个键。填到一半按错一下会丢掉
            # 没保存的输入 —— 这是对话框的通行语义, 比"每个键都关不掉"
            # 一致得多; 真想留着的值用户本来就会去点保存。
            root.bind("<Escape>", lambda _e=None: close())
            _darken_tree(root)
            # 保存键是这个窗唯一想留住的强调色: 用 Windows 强调色而不是
            # 自造的高饱和绿 —— 白色按键字压 4.53:1, 比暗底压亮绿清楚。
            # 深色主题里强调的是 #4cc2ff, 得配深色字才够 4.5:1。
            saver.configure(bg=_ACCENT, fg=_SAVE_FG,
                            activebackground=_PALETTE["accent_press"],
                            activeforeground=_SAVE_FG)
        except Exception:
            if counted:
                self._ui_windows -= 1
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
        # 悬停就能看到监控这一轮真正在用的参数, 不必点开面板或翻日志
        policy = policy_text(snap.get("mode"), snap.get("threshold") or 0,
                             snap.get("power_saver_auto"),
                             snap.get("on_battery"))
        # 悬停占用与汇总卡逐字同数(都 .1f): %.0f 会把 62.7% 收成 63%,
        # 卡片却写 62.7%, 悬停一数和卡片就对不上, 用户以为看错。
        # Web 面板的卡片有"独显温度", 悬停也带上: 同一份快照同一个
        # 文案, 不必为了看一度温度去点面板。读不到就整行不出现,
        # 别让 0°C 或 None 占掉 szTip 里宝贵的字数。
        # 排在卡名那一行之前: 超长时 fit_tip_title 砍的是最后一行,
        # 名字一长也该砍名字, 不能把跟着名字尾巴的温度一起带走。
        temp = snap.get("dgpu_temp")
        temp_line = f"独显温度: {temp}°C\n" if temp else ""
        self._title_base = (f"GPU 监控  核显占用 {total:.1f}%\n{policy}\n"
                            f"{temp_line}核显: {ig}")
        self._refresh_title()
        # 图标也加一道同款闸: 数字没变就别重设 self.icon.icon。Windows 上每次
        # 设图标都重建托盘图标(和 _refresh_title 里那条注释同一个理), 空闲时占
        # 用率不动, 没理由每秒重画那颗圆点。
        icon_num = int(round(max(0.0, min(100.0, total))))
        if icon_num != getattr(self, "_icon_num_shown", None):
            self._icon_num_shown = icon_num
            self.icon.icon = _make_icon_image(icon_num)

    def _gpu_name(self, luid):
        with self.lock:
            snap = self.snapshot
        if snap:
            entry = snap["kind_cache"].get(luid)
            if entry:
                return entry[1]  # (kind, name)
        return "LUID ..." + luid[-4:]

    def _on_hotkey_panel(self):
        """热键回调跑在监控线程的热键线程里, 不能直接碰 tkinter。"""
        self.panel_request = True

    def _on_hotkey_pause(self):
        self.paused = not self.paused

    def _monitor_thread(self):
        _dbg("monitor enter")
        failed = False
        try:
            cmd_monitor(self.cfg, self.config_path,
                        hooks={"before_cycle": self._before_cycle,
                               "on_sample": self._on_sample,
                               "is_paused": lambda: self.paused,
                               "on_history_point": self._on_history_point,
                               "hotkey_panel": self._on_hotkey_panel,
                               "hotkey_pause": self._on_hotkey_pause})
        except Exception:  # 后台线程兜底，错误落盘便于排查
            failed = True
            import traceback
            _dbg("monitor error")
            try:
                with open(os.path.join(_DIR, "tray_error.log"), "w",
                          encoding="utf-8") as f:
                    f.write(traceback.format_exc())
            except OSError:
                pass
        _dbg("monitor exit")
        if failed:
            self._monitor_failed()

    def _monitor_failed(self):
        """监控线程已经死了: 别让托盘继续装作还在监控。

        老代码只写 tray_error.log 就返回, 图标停在上一次的占用数字上,
        菜单照旧写没人读的 config.json, 用户完全看不出监控早就停了。
        """
        self.monitor_dead = True
        self.stop = True                 # before_cycle 的等待循环要能退出
        try:
            self.icon.icon = _make_icon_image(0)
            self.icon.title = ("GPU 监控已停止（详见托盘目录 tray_error.log），"
                               "右键可退出")
        except Exception:
            pass
        try:
            from gpu_monitor import notify
            notify("GPU 监控已停止",
                   "监控线程已退出，详见托盘目录 tray_error.log。"
                   "重启 GPUMigrate 后恢复。")
        except Exception:
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
        if col == "auto":
            # 与 --list / Web 面板同口径: 主键为三面共用的活动量
            # (GPU%、专用MiB、共享MiB 取最大), 并列按 pid 升序, 三面
            # 逐行相同。
            if desc:
                return sorted(rows, key=lambda r: (-r[0], r[1][1]))
            return sorted(rows, key=lambda r: (r[0], r[1][1]))
        idx = {"name": 0, "pid": 1, "gpu": 2, "igpu": 3, "dgpu": 4,
               "dmem": 5, "smem": 6, "pref": 7}.get(col, 2)
        numeric = col in self._SORT_NUM_COLS

        def key(row):
            if numeric:
                # 与详情窗共用 _row_num: 以前这里自己 replace 一遍,
                # 对带单位的写法会给错值。解析失败要沉底而不是和 0 并列,
                # 所以 bad 传 -1.0 (详见 test_unparsable_numbers_sink_to_the_bottom)
                return _row_num(row[1], idx, -1.0)
            # 进程名按大小写不敏感排: 大写码位全排在小写前面, 按 str()
            # 直接排会把 "Zebra.exe" 排到 "adc.exe" 前头, 同名的大小写
            # 变体(chrome/Chrome)也被拆到桌子两头 —— 资源管理器从不这么排。
            return str(row[1][idx]).lower()

        # 先按 pid 升序打底再排主键(稳定排序): rows 是拿 set 迭代攒出来的,
        # 主键并列时不定个序, 同一份快照两次刷新就能给出两种行序 ——
        # 表格每秒自己抖。pid 口径与 auto 档一致, 并且和 --list / Web 同向。
        ordered = sorted(rows, key=lambda r: r[1][1])
        ordered.sort(key=key, reverse=desc)
        return ordered

    # ---- 实时面板 (tkinter, 独立线程) ----

    def _pid_name(self, pid):
        """pid -> 进程名, 带 10 秒过期。

        Windows 会回收 pid: 同一个数字, 十分钟前可能是 chrome, 现在住着
        game。缓存原来只按 2000 条上限整体清一次, 托盘开一整天都不散架,
        复用 pid 的行就一直顶着上个进程的名字 —— 那一行的 GPU% 是新进程
        的, 名字是旧进程的, 面板当面指鹿为马。过期时间与 _pref_cache 对齐。
        """
        now = time.time()
        if now - getattr(self, "_name_ts", 0.0) > 10:
            self._name_cache.clear()
            self._name_ts = now
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
                is_high_performance(get_gpu_preference(full_path))
        return self._pref_cache[full_path]

    @staticmethod
    def _settings_num(var, cfg, key, cast, bad=None):
        """设置窗口数字项: 填不是数字就延用当前生效值。

        旧代码的兜底是写死的 20/1024/40/85/0:
        threshold_percent 默认早就是 50, 兜底却还写 20
        —— “没配这项的用户填错一次”会被存成一个和输入框里显示的
        生效值不一样的数字。改用 merged cfg 的当前值:
        和预填内容一致, 也不会跟默认值漂移。

        延用旧值不能让用户以为存进去了: 输入框清空或填了文字,
        旧代码标题照样说"已保存", 而那一格还是旧值 —— 又一次
        "说了不算"。bad 是可选列表, 被拒的键名往里丢,
        由 save() 在标题里点名。
        """
        try:
            return cast(var.get())
        except ValueError:
            if bad is not None:
                bad.append(key)
            return cfg[key]

    def _pref_status(self, full):
        """详情窗的"GPU 首选项"行: 注册表里给这块 exe 设的到底是什么。

        详情窗有"立即设为独显 / 撤销独显设置"两个按钮, 却不显示当前是
        哪一态, 用户只能点了看结果; 面板有"迁移状态"列, 这里补同一口径。
        拿不到完整路径就说清楚, 不假装查过注册表。
        """
        if not full or not os.path.isfile(full):
            return "未知 (拿不到完整路径)"
        return ("已设为独显 (重启该程序后生效)"
                if self._pref_of(full) else "未设置 (按系统默认/自动)")

    def _pref_apply(self, full, clear=False):
        """写(或清)注册表首选项, 写完立刻作废这一条的缓存。
        返回是否写成功。

        _pref_of 有 10 秒缓存, 而"迁移状态"列每秒刷一次: 在详情窗点过按钮
        之后, 面板还要按旧答案讲最多十秒 —— 注册表里已经是独显, 列上还写
        着没设。同一处信息两处说着反话, 所以写完就把它踢出缓存, 下一帧即
        对齐。抽成方法是为了能连"写"带"作废"一起验。

        失败也得说清楚: clear_gpu_preference 失败返回 False
        (本来就没设置),
        set_gpu_preference 失败抛 OSError。详情窗的按钮按这个返回值决定提示词
        —— "已清除"不能是空话, 写失败也不能只给 stderr 留一条
        看不见的 traceback。

        返回值仍然是布尔(成功/失败), 但失败或空清除的原因写在
        self._pref_err 上: 干净成功=None, 空清除="noop", 失败=一句人话。
        没有它, 详情窗只能对所有失败念同一句"可能本来就没有独显设置" ——
        权限被策略挡掉时那句话是反的, 用户会以为一切正常。
        """
        self._pref_err = None
        try:
            if clear:
                res = clear_gpu_preference(full)
                if res is False:
                    self._pref_err = "注册表没有答应(权限被限制?)"
                    return False
                # 回读确认: 删除 API 说不算数, 注册表里真没有了才算。
                # 杀软/策略拦在半路时调用可能不报错却什么都没改。
                if is_high_performance(get_gpu_preference(full)):
                    self._pref_err = "删完了, 回读却还在独显上"
                    return False
                if res is None:
                    self._pref_err = "noop"      # 本来就没有, 不是失败
            else:
                set_gpu_preference(full)
                # 同上: 写完必须回读。写入"成功"但值没落地时, 弹
                # "已设为独显"会让用户白重启一次程序, 比当场说失败糟糕得多。
                if not is_high_performance(get_gpu_preference(full)):
                    self._pref_err = "写入了却没落地(被杀软/策略拦下?)"
                    return False
        except OSError as exc:
            self._pref_err = str(exc) or "写入失败"
            return False
        self._pref_cache.pop(full, None)
        return True

    def _pref_err_text(self):
        """把 self._pref_err 变成一句能给人看的话。

        tk 回调里取一个不存在的属性会抛 AttributeError, 而那个异常只会
        冒号后打一条 stderr —— pythonw 下直接丢, 表现为"点了没反应"。
        所以这里一律 getattr, 并且永远返回非空字符串。
        """
        err = getattr(self, "_pref_err", None)
        return err if err and err != "noop" else "原因未知"

    def _pref_refresh_label(self, lb, full):
        """把详情窗那一行 GPU 首选项换成此刻的答案。

        点过按钮之后注册表已经变了, 窗体里那一行却还写着打开时的旧
        状态(通常是"未设置"), 用户对着窗里最后一眼判断成败, 会以为
        没生效。抽成方法而不是在 do_set/do_unset 里直接 config, 是为
        了能在没有 tk 的情况下连它一起验。
        """
        try:
            lb.config(text=self._pref_status(full))
        except Exception:
            pass          # 窗口已经在回调前被关掉, 没什么可刷的

    def _never_migrate(self, pname, tip):
        """右键"永不迁移": 写进了 config.json 才算数, 写不进要说写不进。

        写入失败(config.json 解析不了/只读时write_json_dict 返回 False)
        旧代码照样弹"已加入, 立即生效":用户以为排除了, 下一轮监控照旧把进程判成候选。
        成功与失败都落到同一行 tip 上, 和设置窗口的保存反馈一致。
        """
        if save_exclude_process(self.config_path, pname):
            tip.config(text=f"已把 {pname} 加入永不迁移名单 "
                            f"(写入 config.json, 立即生效)")
        else:
            _dbg(f"永不迁移写入失败: {pname} "
                 f"({self.config_path})")
            tip.config(text="加入永不迁移名单失败: config.json 写不进")

    @staticmethod
    def _pick_row(tree, event_y):
        """右键定位并选中光标所在行, 返回 item; 空白/空行返回 None。

        ttk 的右键(<Button-3>)不改选中项, 而"永不迁移"菜单按
        tree.selection() 取进程。若右键时不把选中项移到光标这一行,
        右键 B 行就会作用到上次左键选中的 A 行 —— 把 A 加进名单,
        排除了错的程序。所以右键先选中光标这一行, 和资源管理器/
        进程管理器右键即选中的行为一致。
        """
        item = tree.identify_row(event_y)
        if not item or not tree.item(item, "values"):
            return None
        tree.selection_set(item)
        return item

    def _panel_build(self):
        """在 UI 线程里建实时面板 (Toplevel, 不新建解释器)。"""
        if self._ui_root is None:
            self.panel_open = False
            return
        counted = False
        try:
            root = tk.Toplevel(self._ui_root)
            counted = True
            self._ui_windows += 1
            root.title("GPU 占用监控")
            try:
                root.iconbitmap(_APP_ICON)
            except Exception:
                pass
            self._root_ref = root
            root.configure(bg=_BG)
            # 每次打开都重读一次系统偏好: 用户在设置里切换深浅后, 不用
            # 重启程序, 下一次打开面板就跟着换。
            _use_palette(_FLUENT_DARK if _fluent_scheme() == "dark"
                         else _FLUENT_LIGHT)
            _apply_fluent_theme()
            _apply_win11_chrome(root)

            # 恢复上次窗口位置/大小/列宽
            ui = _load_ui_state()
            # geometry 是人也能够手改的: 形状不合法会 TclError, 位置越界则
            # 窗口开在屏幕外, 两种都表现为"面板打不开"。过一遍筛子再喂。
            _sw, _sh = _screen_size(root)
            root.geometry(_onscreen_geometry(ui.get("geometry"), _sw, _sh))
            # 筛选词和排序列也记住: Web 面板的筛选词存在 localStorage,
            # 托盘一关就忘的话, 两边又是两张脸。注意要在建 Entry 之前
            # 把值喂给 self.filter_text, 否则第一帧刷的是全量。
            self.filter_text = str(ui.get("filter") or "")
            saved_sort = ui.get("sort") or {}
            if saved_sort.get("col") in ("auto", "name", "pid",
                                         "gpu", "igpu", "dgpu",
                                         "dmem", "smem", "pref"):
                self.sort_state = {"col": saved_sort["col"],
                                   "desc": bool(saved_sort.get("desc",
                                                               True))}
            # 置顶默认开 (这窗口就是拿来盯着看的), 但得能关掉:
            # 全屏游戏时一个置顶小窗正好挡在画面中间。
            # 上次的选择要记住, 不然每次开面板都得重新关一次。
            top_on = [bool(ui.get("topmost", True))]
            root.attributes("-topmost", top_on[0])

            cols = tuple(_COL_LABELS)
            widths = dict(ui.get("widths", {}))
            defaults = {"name": 180, "pid": 55, "gpu": 55, "igpu": 55,
                        "dgpu": 55, "dmem": 75, "smem": 75, "pref": 160}
            # 顶部汇总条: 口径与 Web 面板共用一份 (panel_summary)
            cards = tk.Frame(root, bg=_BG)
            cards.pack(fill="x", padx=8, pady=(8, 4))
            card_labels = {}
            last = len(panel_summary(self.snapshot or {})) - 1
            tips = {}
            for i, (label, text, cls, tip) in enumerate(
                    panel_summary(self.snapshot or {})):
                cell = tk.Frame(cards, bg=_PANEL_BG, highlightthickness=1,
                                highlightbackground=_LINE)
                # pack(expand=True) 等分一行: 窗口拉窄时六张卡一起缩短,
                # 而不是把右边几张直接切到视口外。grid 做不到这一点 ——
                # 列的请求宽度有下限, uniform 也只是分多余空间。
                cell.pack(side="left", fill="x", expand=True,
                          padx=(0, 0 if i == last else 8))
                tk.Label(cell, text=label, bg=_PANEL_BG, fg=_MUTED,
                         font=_FONT_UI).pack(anchor="w", padx=9, pady=(5, 0))
                val = tk.Label(cell, text=text, bg=_PANEL_BG,
                               fg=_heat_color(cls),
                               font=_FONT_UI_BOLD)
                val.pack(anchor="w", padx=9, pady=(0, 5))
                card_labels[label] = val
                tips[label] = tip
                _bind_tip(val, tip)

            # 筛选框: 进程多的时候这一栏比表格本身更有用
            fbar = tk.Frame(root, bg=_BG)
            fbar.pack(fill="x", padx=8, pady=(0, 4))
            tk.Label(fbar, text=_FILTER_HINT, bg=_BG, fg=_MUTED,
                     font=_FONT_UI).pack(side="left")
            fvar = tk.StringVar(value=self.filter_text)
            entry = tk.Entry(fbar, textvariable=fvar, width=24, relief="flat",
                             bg=_PANEL_BG, fg=_FG, insertbackground=_FG,
                             highlightthickness=1, highlightbackground=_LINE,
                             highlightcolor=_ACCENT, font=_FONT_NUM)
            entry.pack(side="left", padx=6)
            hint = tk.Label(fbar, text="", anchor="w", bg=_BG, fg=_MUTED,
                            font=_FONT_UI)
            hint.pack(side="left", padx=(6, 0))
            fvar.trace_add("write",
                           lambda *_a: self._set_filter(fvar.get()))

            def _toggle_top():
                top_on[0] = not top_on[0]
                root.attributes("-topmost", top_on[0])
                pin.config(text="置顶: 开" if top_on[0]
                           else "置顶: 关")

            pin = tk.Button(fbar, relief="flat", bg=_BTN_BG, fg=_FG,
                            activebackground=_BTN_HOVER, activeforeground=_FG,
                            font=_FONT_UI, cursor="hand2",
                            text="置顶: 开" if top_on[0]
                            else "置顶: 关",
                            command=_toggle_top)
            pin.pack(side="right", padx=(6, 0))

            def _sort_btn_label():
                sort_btn.config(text=sort_button_text(self.sort_state))

            def _reset_sort():
                # 默认档(auto)没有表头, 点过某列后没了回头路; 这个
                # 按钮就是那条回头路, 一键回到三面同序的活动量排序。
                self.sort_state = {"col": "auto", "desc": True}
                _sort_btn_label()
                refresh()

            sort_btn = tk.Button(fbar, relief="flat", bg=_BTN_BG,
                                 fg=_MUTED, activebackground=_BTN_HOVER,
                                 activeforeground=_FG, font=_FONT_UI,
                                 cursor="hand2", command=_reset_sort)
            sort_btn.pack(side="right", padx=(6, 0))
            _sort_btn_label()

            tree = ttk.Treeview(root, columns=cols, show="headings", height=12)
            for cid, text in _COL_LABELS.items():
                # 列头悬停解释跟在 Web 面板用同一份 _HDR_TIPS: 光看
                # "专用MiB" 也看不出是不是跨进程求和 —— 列名写清了单位
                # (字节 / 1048576 = MiB), 口径还要靠悬停讲。
                # Web 那边 title 属性自带, 托盘这边没有等价物, 挂到
                # Enter/Leave 上自己弹。
                # 点列头要当场重排: 只改 sort_state 的话要等下一轮 1 秒
                # 刷新才见效, 而"复原排序"按钮是立即的 —— 同一种操作两种
                # 手感。refresh 定义在下面, lambda 调用时才查名, 赶得上。
                tree.heading(
                    cid, text=text,
                    command=lambda c=cid: (self._toggle_sort(c),
                                           _sort_btn_label(),
                                           refresh()))
                tree.column(cid, width=widths.get(cid, defaults[cid]),
                            anchor="center")
                _bind_tree_tip(tree, cid, _HDR_TIPS.get(cid, ""))
            for cls, color in _HEAT_COLOR.items():
                if cls:
                    tree.tag_configure(cls, foreground=color)
            # 前景归热度 tag 管, 背景归隔行 tag 管, 两个 tag 各管一头
            tree.tag_configure(_ROW_ALT_TAG, background=_ROW_ALT_BG)
            tree.pack(fill="both", expand=True, padx=6, pady=4)

            # Esc: 有筛选词先清空, 已经空了才关窗 (和常见工具的套路一致)
            def _on_escape(_event=None):
                # 清完筛选词顺手把键盘还给表格。不还的话用户按完 Esc 按
                # 方向键, 动的还是输入框里的光标, 表格一动不动 —— 键盘
                # 导航在最常用的那个出口上断掉, 而且看不出是为什么。
                if fvar.get():
                    fvar.set("")
                    try:
                        tree.focus_set()
                    except Exception:
                        pass
                    return "break"
                on_close()
                return "break"

            entry.bind("<Escape>", _on_escape)
            root.bind("<Escape>", _on_escape)
            root.bind("<Control-f>", lambda _e: entry.focus_set())

            spark = tk.Canvas(root, height=46, bg=_PANEL_BG,
                              highlightthickness=0)
            spark.pack(fill="x", padx=6, pady=(0, 0))

            # 和 Web 面板同一把尺: 弱化色在卡片底上必须过 AA。
            # 这行(核显/独显名 + 刷新节奏 + 键盘/右键入口)是面板底部唯一
            # 的状态说明, 读不出来等于没有。快捷键不说出来就等于没有 ——
            # 键盘用户看不见菜单, 只有这一行告诉他们回车能干什么。
            tip = tk.Label(root, text="", anchor="w", fg=_MUTED)
            tip.pack(fill="x", padx=8, pady=(0, 1))
            # 会话累计: 采了多少轮、抓到多少超标、迁走几个。btop / bottom
            # 把这类数摆在台面上 —— 只报"刷新: 1 秒"的话, 监控是不是真在
            # 干活只能靠猜, 而"0 轮"和"3000 轮 0 命中"是两个完全不同的状态。
            count = tk.Label(root, text="", anchor="w", fg=_MUTED)
            count.pack(fill="x", padx=8, pady=(0, 6))

            # 右键菜单: 永不迁移
            menu = tk.Menu(root, tearoff=0)

            def _never(event):
                # 右键先把选中项移到光标这一行, 否则下面的"永不迁移此程序"
                # 会按上次左键选中的那行行事 —— 右键 B 行却把 A 加了名单。
                if self._pick_row(tree, event.y) is not None:
                    menu.tk_popup(event.x_root, event.y_root)

            def _never_add():
                sel = tree.selection()
                if not sel:
                    return
                vals = tree.item(sel[0], "values")
                if _row_pid(vals) is None:
                    # 占位行不是进程, 不能往排除名单里写东西
                    return
                self._never_migrate(vals[0], tip)

            menu.add_command(label="永不迁移此程序", command=_never_add)
            tree.bind("<Button-3>", _never)
            tree.bind("<Double-1>",
                      lambda e: self._open_detail(tree, e))

            # 键盘导航, 参照 htop / nvtop / 任务管理器的表格交互:
            # ↑↓ 移动选中(这条 ttk.Treeview 自己就会, 不用重造, 缺的是
            # "选中项要活着跨过每秒一次的重渲染", 见 _refresh_tree 的
            # keep_pid), 回车进详情, Del 永不迁移, "/" 或 Ctrl-F 回筛选框。
            def _never_key(_event=None):
                _never_add()
                return "break"

            def _focus_filter(_event=None):
                if entry.focus_get() is not entry:
                    entry.focus_set()
                return "break"

            def _seed_selection(last=False):
                """空选中态下按方向键: 先把光标放到第一行/最后一行。

                ttk.Treeview 实测(见 .tmptest/probe_tk.py): 选中集为空时
                按 Down 什么都不会发生 —— 键盘用户必须先拿鼠标点一行, 方向
                键才认。htop / nvtop 这类工具不这样, 它们永远有个光标。
                这里补上“播种”。已经选中的话什么都不做 —— 移动交给 ttk
                自己的绑定。
                """
                if _sel_item(tree) is not None:
                    return False
                kids = tree.get_children()
                if not kids:
                    return False
                iid = kids[-1] if last else kids[0]
                tree.selection_set(iid)
                try:
                    tree.focus(iid)
                    tree.see(iid)
                except (AttributeError, TypeError):
                    pass
                # 已经替用户落了脚, 就得拦住 ttk 自己的上下键绑定: 不拦的
                # 话它会把光标再推一格, 第一次按 Down 直接跳到第二行。
                return True

            def _seed_down(_event=None):
                return "break" if _seed_selection() else None

            def _seed_up(_event=None):
                return "break" if _seed_selection(last=True) else None

            # 面板一开就把键盘交给表格: 否则第一次按方向键没人理, 用户
            # 会以为这个面板没有键盘支持。窗口还没映射时 focus_set 会抛
            # TclError, 那也只是一次焦点变故, 不该把整个面板搭到一半。
            try:
                tree.focus_set()
            except Exception:
                pass

            tree.bind("<Return>", lambda _e=None: self._open_detail(tree))
            tree.bind("<KP_Enter>", lambda _e=None: self._open_detail(tree))
            tree.bind("<Delete>", _never_key)
            tree.bind("<Down>", _seed_down)
            tree.bind("<Up>", _seed_up)
            root.bind("<Return>", lambda _e=None: self._open_detail(tree))
            root.bind("<slash>", _focus_filter)

            # 排序也要能用键盘使: 以前只有点表头一条路, 而这张表默认就是
            # 按活动量排的, 想改成"按专用显存看"非得摸鼠标。参照 nvtop 的
            # s / r 两个键: s 循环换列, r 反转方向。
            _SORT_CYCLE = ("auto", "name", "pid", "gpu", "igpu",
                           "dgpu", "dmem", "smem", "pref")

            def _not_typing(_event=None):
                # 筛选框里按 s/r 是打字, 不是命令。回车同理 —— 输入框拿
                # 着焦点时这些键一概不劫走。
                return entry.focus_get() is not entry

            def _sort_cycle(_event=None):
                if not _not_typing():
                    return
                cur = self.sort_state.get("col", "auto")
                try:
                    nxt = _SORT_CYCLE[(_SORT_CYCLE.index(cur) + 1)
                                      % len(_SORT_CYCLE)]
                except ValueError:
                    nxt = "auto"
                self._toggle_sort(nxt)
                _sort_btn_label()
                refresh()
                return "break"

            def _sort_reverse(_event=None):
                if not _not_typing():
                    return
                self.sort_state["desc"] = not self.sort_state["desc"]
                _sort_btn_label()
                refresh()
                return "break"

            tree.bind("<s>", _sort_cycle)
            tree.bind("<r>", _sort_reverse)
            root.bind("<s>", _sort_cycle)
            root.bind("<r>", _sort_reverse)
            # 快捷键不说出来就等于没有。底部状态行已经很长, 塞进去会被
            # 窗口右沿切掉; 挂在"复原到自动"按钮上 —— 用户找排序入口时
            # 鼠标本来就会划过它。
            _bind_tip(sort_btn, "点击表头或按 s 循环切换排序列, r 反转方向; "
                                "Esc 之后方向键回到表格")

            def refresh():
                # 任何异常都不允许中断 after 链, 否则面板从此不再刷新;
                # 但也不能一声不吭, 详见 _safe_refresh
                self._safe_refresh(tree, tip, card_labels, hint, spark,
                                   count)
                if self.panel_open and not self.stop:
                    root.after(1000, refresh)

            def on_close():
                self.panel_open = False
                self._ui_windows -= 1
                try:
                    ui = {"geometry": root.geometry(),
                          "topmost": top_on[0],
                          "filter": self.filter_text,
                          "sort": dict(self.sort_state),
                          "widths": {c: tree.column(c, width=None)
                                     for c in cols}}
                    # 和 config.json 同一个道理: 直接 open("w")
                    # 会先清空再 dump, 关窗时被杀就留下半截
                    # JSON —— 下次开面板全部布局回默认。
                    write_json_dict(_UI_FILE, ui)
                except (OSError, ValueError):
                    pass
                root.destroy()

            root.protocol("WM_DELETE_WINDOW", on_close)
            refresh()
        except Exception:
            if counted:
                self._ui_windows -= 1
            self.panel_open = False
            # 这一条以前是彻底静默的: 异常吞掉、不记一行、窗口也不收。
            # 于是面板打不开时用户看到的是"菜单点了没反应", 而
            # tray_debug.log / tray_error.log 里一个字都没有 —— 想排查
            # 都没有抓手。与 _safe_refresh 一个道理: 罪证要留下。
            import traceback
            _dbg("面板建立失败:\n" + traceback.format_exc())
            # 半建的窗口得收掉: root/控件已经建了一半, 不 destroy 的话
            # 每点一次"打开实时面板"就漏一个看不见的窗口, 越漏越多。
            try:
                root.destroy()
            except Exception:
                pass

    def _draw_sparkline(self, canvas, threshold=0):
        """画趋势线 + 阈值线。没这条线, 就看不出什么时候会被判成高占用。"""
        try:
            canvas.delete("all")
            # 窗口与 x 轴与 Web 面板共用同一套: 老代码取最后 180 点、
            # 按序号等距, 休眠唤醒后几小时的空白会被画成一段连续曲线,
            # 和角标上的"近 2.0 小时"当场对不上
            pts = spark_points(self.history)
            w = int(canvas.winfo_width() or 860)
            h = int(canvas.winfo_height() or 46)
            if len(pts) < 2:
                canvas.create_text(
                    w / 2, h / 2, fill=_MUTED,
                    text="核显占用趋势 (每 10 秒一点)"
                    if len(self.history) < 2 else
                    "最近 30 分钟无采样数据 (暂停监控或休眠后会出现)")
                return
            ymax = spark_ymax(pts, threshold)
            coords, x_last, x_first = spark_geometry(pts, w, h, ymax)
            # 先铺填充, 再画阈值线, 最后画折线。
            # 阈值线得在填充上面: 折线翻到阈值上方时,
            # 先画的那半截会被填充整块盖掉, 阈值就时有时无了。
            canvas.create_polygon(coords + [x_last, h, x_first, h],
                                  fill=_SPARK_FILL, outline="")
            if threshold:
                y = spark_y_of(threshold, h, ymax)
                canvas.create_line(0, y, w, y, fill=_HEAT_COLOR["hot"],
                                   dash=(3, 3))
            canvas.create_line(coords, fill=_HEAT_COLOR["cool"], width=2,
                               smooth=True)
            if threshold:
                canvas.create_text(5, y - 7, anchor="nw",
                                   fill=_HEAT_COLOR["hot"],
                                   font=_FONT_UI,
                                   text=f"迁移阈值 {threshold:g}%")
            canvas.create_text(w - 6, 6, anchor="ne", fill=_MUTED,
                               font=_FONT_UI,
                               text=spark_corner_text(pts))
        except Exception:
            pass

    def _open_detail(self, tree, event=None):
        """进详情: 鼠标双击给坐标, 键盘给当前选中行, 两条路同一去处。

        以前只认 event: 于是"回车看详情"在键盘上没有任何入口,
        而这个面板的正文就是一张表, 键盘用户只能看不能用。
        """
        item = None
        if event is not None:
            item = tree.identify_row(event.y)
        else:
            item = _sel_item(tree)
        if not item:
            return
        vals = tree.item(item, "values")
        pid = _row_pid(vals)
        if pid is None:
            return
        self._ui_post(self._detail_build, pid, list(vals))

    def _detail_build(self, pid, vals):
        """进程详情窗: 同样挂在 UI 线程那一个解释器上。"""
        if self._ui_root is None:
            return
        counted = False
        try:
            root = tk.Toplevel(self._ui_root)
            counted = True
            self._ui_windows += 1
            root.title(f"进程详情 - pid {pid}")
            root.geometry("520x300")
            root.attributes("-topmost", True)
            try:
                root.iconbitmap(_APP_ICON)
            except Exception:
                pass
            info = pid_to_name(pid)
            # 系统/受保护进程 OpenProcess 被拒, 快照只给得出名字: 完整路径
            # 是注册表首选项的键, 拿不到就只能显示"未知", 按钮也点不得
            full = info[1] if info and info[1] else "未知 (需要管理员权限)"
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
                    # dw 是 c_ulonglong 数组, 下标取出来就是 int(注意别再
                    # 包一层 ctypes 类型, 那种实例不能参与浮点除法)
                    ft = ct_.dw[0] | (ct_.dw[1] << 32)
                    if ft:
                        ts = ft / 1e7 - 11644473600
                        start_str = time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(ts))
                k32.CloseHandle(ctypes.c_void_p(h))

            snap = self.snapshot or {}
            # 双击落在两次刷表之间时, 进程可能正好退出、pid 被复用,
            # 或者从"真在算"掉成噪声行。这个窗口里每个数都得是点击
            # 那一行的同一刻(行里带齐了 GPU%/核显%/独显%/两份显存),
            # 不能 GPU% 读行、显存读快照 —— 那样会冒出"45.2% 却是 0MB"
            # 这种自己打自己的读数。pid 不在快照里更要说明白, 不然
            # 用户会把这一排 0 当成"现在就是这么闲"。
            gone = bool(snap) and not any(
                pid in (snap.get(k) or {})
                for k in ("pid_names", "util_by_pid", "gpu_all_by_pid",
                          "dgpu_util_by_pid"))
            ig = _row_num(vals, 3)
            dg = _row_num(vals, 4)
            de = _row_num(vals, 5)
            sh = _row_num(vals, 6)
            # 判定抽去 gpu_monitor.gpu_where_text: 两侧噪声地板要和
            # row_is_noise 同一档, 详情窗不能跟面板说两样话
            where = gpu_where_text(ig, dg)

            frm = tk.Frame(root)
            frm.pack(fill="both", expand=True, padx=14, pady=10)
            lines = [
                ("进程名", name), ("PID", str(pid)),
                ("完整路径", full), ("启动时间", start_str),
                ("当前所在", where),
                ("GPU 首选项", self._pref_status(full)),
                ("GPU% / 核显% / 独显%",
                 f"{vals[2]} / {vals[3]} / {vals[4]}"),
                ("专用/共享内存", f"{de:.0f} MiB / {sh:.0f} MiB"),
            ]
            if gone:
                lines.insert(0, ("注意",
                                   "该进程已不在本轮采样中(可能已退出或 pid 被复用), "
                                   "下面是点击那一行的读数"))
            pref_label = None
            for i, (k, v) in enumerate(lines):
                tk.Label(frm, text=k + ":", anchor="ne", fg=_MUTED).grid(
                    row=i, column=0, sticky="ne", pady=2)
                lb = tk.Label(frm, text=v, anchor="w", wraplength=380,
                              justify="left")
                lb.grid(row=i, column=1, sticky="w", pady=2)
                if k == "GPU 首选项":
                    pref_label = lb
            frm.columnconfigure(1, weight=1)

            def do_set():
                if not os.path.isfile(full):
                    self._detail_msg(root, "拿不到完整路径, 写不了 GPU 首选项")
                    return
                if self._pref_apply(full):
                    self._pref_refresh_label(pref_label, full)
                    self._detail_msg(root, f"已设为独显: 重启 {name} 后生效")
                else:
                    self._detail_msg(root, "写入注册表失败, 首选项未改变: "
                                           f"{self._pref_err_text()}")

            def do_unset():
                if not os.path.isfile(full):
                    self._detail_msg(root, "拿不到完整路径, 写不了 GPU 首选项")
                    return
                if self._pref_apply(full, clear=True):
                    self._pref_refresh_label(pref_label, full)
                    if getattr(self, "_pref_err", None) == "noop":
                        self._detail_msg(root,
                                         f"{name} 本来就没有独显设置")
                    else:
                        self._detail_msg(root, f"已清除 GPU 设置: {name}")
                else:
                    self._detail_msg(root, "未能清除: "
                                           f"{self._pref_err_text()}")

            def close():
                self._ui_windows -= 1
                root.destroy()

            btns = tk.Frame(root)
            btns.pack(pady=6)
            tk.Button(btns, text="立即设为独显", command=do_set).pack(
                side="left", padx=6)
            tk.Button(btns, text="撤销独显设置", command=do_unset).pack(
                side="left", padx=6)
            tk.Button(btns, text="关闭", command=close).pack(side="left",
                                                             padx=6)
            root.protocol("WM_DELETE_WINDOW", close)
            # Esc 关窗: 面板那边 Esc 已经是"收起键盘面板"的通用键, 详情窗
            # 不认它的话, 键盘用户得把手挪回鼠标去点那个"关闭"按钮 ——
            # 而这个窗存在的全部理由就是让键盘用户少碰鼠标。
            root.bind("<Escape>", lambda _e=None: close())
            _darken_tree(root)
        except Exception:
            if counted:
                self._ui_windows -= 1

    @staticmethod
    def _detail_msg(root, text):
        root.title(text)

    def _set_filter(self, text):
        """筛选框写入口。子串匹配, 大小写不敏感, 空 = 全显。"""
        self.filter_text = text or ""

    def _matches_filter(self, name, pid):
        """进程名子串 / PID 匹配, 大小写不敏感, 空词全显。

        与 Web 面板同一个口径: 那边匹配 `td.n` 与 `td.pid` 两个单元格,
        这里对应名字与 PID。整行 textContent 匹配的老写法会把百分比列
        也算进去 —— 输入 "12" 留下独显% 12.3 的行, 还让人以为它真在 12%。
        """
        needle = self.filter_text.strip().lower()
        if not needle:
            return True
        return needle in str(name).lower() or needle in str(pid)

    def _refresh_tree(self, tree, tip, card_labels=None, hint=None,
                       count=None):
        with self.lock:
            snap = self.snapshot
        if card_labels is not None:
            for label, text, cls, _tip in panel_summary(snap or {}):
                widget = card_labels.get(label)
                if widget is not None:
                    widget.config(text=text, fg=_heat_color(cls))
        rows = []
        shown = total = 0
        if snap:
            pids = (set(snap["util_by_pid"]) | set(snap["gpu_all_by_pid"])
                    | set(snap["dgpu_util_by_pid"])
                    | set(snap["shared_by_pid"])
                    | set(snap["dedicated_by_pid"]))
            for pid in pids:
                gpu_all = snap["gpu_all_by_pid"].get(pid, 0.0)
                ig = snap["util_by_pid"].get(pid, 0.0)
                dg = snap["dgpu_util_by_pid"].get(pid, 0.0)
                de = snap["dedicated_by_pid"].get(pid, 0.0) / MIB
                sh = snap["shared_by_pid"].get(pid, 0.0) / MIB
                if row_is_noise(gpu_all, de, sh):
                    continue
                total += 1
                name = self._pid_name(pid)
                if not self._matches_filter(name, pid):
                    continue
                shown += 1
                pref = ""
                info = pid_to_name(pid)
                if info and info[1] and self._pref_of(info[1]):
                    pref = PREF_HIGH_TEXT
                # 一位小数: 取整会把 0.4% 显示成 0%, 与 --list / Web 面板
                # 的 %.1f 对不上, 同一台机器三个界面三种读数的观感。
                # 整行热度跟着"GPU%"列(gpu_all)走, 不再只看核显%:
                # 独显吃满、核显闲着的进程, 以前整行是绿的却因复合键
                # 排在最前, 自相矛盾; 现在跟它 GPU% 格的 Web 上色一致。
                rows.append((activity_key(gpu_all, de, sh),
                             (name, pid, f"{gpu_all:.1f}%", f"{ig:.1f}%",
                              f"{dg:.1f}%", f"{de:.0f}", f"{sh:.0f}", pref),
                             heat_class(gpu_all)))
        rows = self._sort_rows(rows)
        # 每秒一次 delete + insert 会把选中态一起清掉。先把选中行的
        # PID 记下来, 渲染完再还回去: 否则键盘上下键每秒被踢回起点。
        sel = _sel_item(tree)
        keep_pid = _row_pid(tree.item(sel, "values")) if sel else None
        tree.delete(*tree.get_children())
        hidden = max(0, len(rows) - 30)
        for idx, (_ig, row, heat) in enumerate(rows[:30]):
            # 隔行跟着"第几行"走, 不跟数据内容走: 排序或筛选一变整表重排
            # 条纹, 不会出现同一个热度一会儿有条纹一会儿没有。
            tags = (heat, _ROW_ALT_TAG) if idx % 2 else (heat,)
            tree.insert("", "end", values=row, tags=tags)
        _reselect_pid(tree, keep_pid)
        if hint is not None:
            # 表格只放得下 30 行。静默截断会让人以为机器上只有这些进程
            # 在占显卡, 所以多出来多少行要说出来。
            # 汇总卡的 "GPU 进程" 是真 union 数, 这里是不含零占用的行数,
            # 两个数对不上也得说, 不然用户以为自己看漏了。
            noise = hidden_noise_count(snap) if snap else 0
            hint.config(text=f"显示 {shown}/{total} 行"
                             + (f" (还有 {hidden} 行未显示)"
                                if hidden else "")
                             + (f"  (已隐藏 {noise} 个 0.0% / 0MiB)"
                                if noise else "")
                             + ("   (按 Esc 关闭窗口)" if shown or total
                                else ""))
        if not rows:
            # "筛选中没匹配上"不等于"没有进程占 GPU": 前者明明有进程在跑,
            # 只是被筛选词挡住了, 说成空闲会让人先怀疑筛选坏了。
            # 一份都还没采到 / 监控已经死了, 也各有各的说法 —— 三种都
            # 不是"没人用 GPU"。
            tree.insert("", "end", values=(
                empty_row_text(total, sampled=bool(snap),
                               stopped=self.monitor_dead),
                "-", "-", "-", "-", "-", "-", "-"))
        if snap:
            ig_luids = ", ".join(self._gpu_name(l)
                                 for l in snap["igpu_luids"]) or "-"
            dg_luids = ", ".join(self._gpu_name(l)
                                 for l in snap["dgpu_luids"]) or "-"
            # 监控线程死了以后, 这张表停在最后一份快照上
            # 不会再动: 那时候写"刷新: 1 秒"是在承诺一件不会再发生的事
            beat = ("监控已停止(见托盘目录 tray_error.log)"
                    if self.monitor_dead else "刷新: 1 秒")
            tip.config(text=f"核显: {ig_luids}    独显: {dg_luids}"
                            f"    {beat}    ↑↓ 选择  回车详情  "
                            f"Del 永不迁移  / 筛选  右键同菜单")
        # 会话计数单独一行, 不并进上面那行: 显卡名本来就长, 再摞三个计数
        # 会超出窗口宽度, 被右沿切掉等于什么都没说。这一行不放在上面的
        # if snap 里 —— 一份都还没采到时更要说明白是"还没开始", 而不是
        # 把上一轮的字留着, 让人以为监控已经采了 0 轮。
        if count is not None:
            # 用 .get 而不是下标: 老快照(别的调用方自己攒的 dict)可能没有
            # 这三个键, 下标会 KeyError, 而 _safe_refresh 一次异常就少刷一帧
            count.config(text=(
                f"本次启动: 采样 {snap.get('rounds') or 0} 轮 · 命中阈值"
                f" {snap.get('hits') or 0} 次 · 已迁移"
                f" {snap.get('migrations') or 0} 个" if snap
                else "本次启动: 还没有采到数据"))

    def _safe_refresh(self, tree, tip, card_labels, hint, spark,
                      count=None):
        """面板定时刷新: 异常只记账, 不抛给 after 链, 也不装作没事发生。

        老代码 except 后直接 pass: _refresh_tree / _draw_sparkline 每个
        周期都抛的话(快照契约变了、控件已被销毁), 面板就永远停在最后一次
        成功的样子, 用户以为监控卡了, 而 tray_debug.log 里一个字都没有。
        这里把 traceback 落盘: 刷新链照活, 罪证留下。同一个错只记第一次 ——
        刷新是每秒一轮, 每次都写会把 tray_debug.log 冲成刷屏。
        """
        try:
            self._refresh_tree(tree, tip, card_labels, hint, count)
            with self.lock:
                thr = (self.snapshot or {}).get("threshold") or 0
            self._draw_sparkline(spark, thr)
            self._refresh_err = None
        except Exception:
            import traceback
            sig = traceback.format_exc()
            if sig != getattr(self, "_refresh_err", None):
                self._refresh_err = sig
                _dbg("面板刷新失败(同错不再重复记):\n" + sig)

    def _hotkey_actions(self):
        """把热键线程置的标志位变成实际动作。

        热键回调跑在 RegisterHotKey 的消息循环线程里, 那边碰 tkinter 会炸,
        所以只置位; 这里轮询后再开面板 / 改标题。
        """
        while not self.stop:
            if self.panel_request:
                self.panel_request = False
                self._on_open_panel()
            self._refresh_title()
            time.sleep(0.2)

    def run(self):
        threading.Thread(target=self._monitor_thread, daemon=True).start()
        threading.Thread(target=self._hotkey_actions, daemon=True).start()
        self.icon.run()
        _dbg(f"tray loop end stop={self.stop}")


# 显示缩放。进程不声明 DPI 感知的话, Windows 按 100% 渲染再由系统拉伸,
# 高分屏上整个托盘界面都是糊的, 固定 1040x470 的面板还会被切掉一截。
# PyInstaller 默认 manifest 不带 dpiAware(产物里逐字节确认过), 所以只能
# 在进程内声明; 必须赶在第一个窗口之前 —— 晚了 Windows 直接拒绝。
_DPI_AWARE_V2 = -4            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
_DPI_AWARE = 2                # PROCESS_PER_MONITOR_DPI_AWARE
_SYSTEM_DPI_AWARE = 1         # PROCESS_SYSTEM_DPI_AWARE


def enable_dpi_awareness():
    """声明本进程按每显示器 DPI 渲染; 老系统/异常都安静退回。

    返回是否真的设上了(只用于日志和测试, 调用方不该因为它失败就退出)。
    Win10 1703+ 有 SetProcessDpiAwarenessContext, Win8.1 有
    SetProcessDpiAwareness, Win7 只剩 SetProcessDPAware。
    """
    if os_name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        # 非 Windows / 非 nt 内核: ctypes 上没有 windll 这个属性
        return False
    try:
        fn = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if fn is not None and fn(ctypes.c_void_p(_DPI_AWARE_V2)):
            return True
    except (AttributeError, OSError):
        pass
    try:
        shcore = ctypes.windll.shcore
        if shcore.SetProcessDpiAwareness(_DPI_AWARE) == 0:
            return True
    except (AttributeError, OSError):
        pass
    try:
        return bool(user32.SetProcessDPIAware())
    except (AttributeError, OSError):
        return False


def run_tray(config_path=None):
    if config_path is None:
        config_path = _CONFIG_PATH
    enable_dpi_awareness()      # 必须早于 TrayApp: 建窗口之后再设会被拒
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

