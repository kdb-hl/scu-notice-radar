# -*- coding: utf-8 -*-
"""
app.py —— 川大通知雷达（看板版 v2）

界面结构：
  ┌ 顶栏：品牌 / 检索框 / 立即刷新 / 导出 ───────────────────────────────┐
  │ 统计条：总通知 · 新增 · 命中关键词 · 来源数 · 最后刷新               │
  │ 筛选：全部 教务处 学工部 学校 学院 ｜ 只看新增 ｜ 命中关键词 ｜ 排序  │
  │ 看板：每个来源一个分格，格子大小 = 权重（4/3/2 列）                  │
  │ 点格子 → 该来源的通知列表（二级页）                                 │
  └ 状态栏 ────────────────────────────────────────────────────────────┘

稳定性：网络请求全在后台线程、单站失败不影响其他站、本地缓存离线可用、日志落盘。
"""

from __future__ import annotations

import os
import queue
import re
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox
    TK_AVAILABLE = True
except Exception:  # noqa: BLE001
    tk = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    TK_AVAILABLE = False

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import core  # noqa: E402
import radar  # noqa: E402

if TK_AVAILABLE:      # 没有 tkinter 时（--selftest 专用环境）跳过界面模块
    import ui_v2 as U  # noqa: E402
else:
    U = None  # type: ignore[assignment]

DEFAULT_WEIGHTS = {
    "priority": {
        "教务处·通知公告": 3, "学工部·通知公告": 3, "川大主站·通知公告": 3, "水利水电学院": 3,
    },
    "default_priority": 1,
    "formula": {"priority": 3, "recent30": 0.8, "hits": 0.6, "unread": 2.0},
    "tiers": {"hero": 3, "large": 4, "mid": 8},
}


class RadarApp:
    def __init__(self, root: tk.Tk, autostart: bool = True):
        self.root = root
        U.set_scale(root)          # 按系统 DPI 定缩放
        self.cfg = core.load_config()
        self.wcfg = self._load_weights()
        self.cache = core.load_cache()
        self.ui_state = core.load_ui()
        self.seen = self.cache.get("seen", {})
        self.items = []
        self.errors = self.cache.get("errors", [])
        self.busy = False
        self.destroying = False
        self.msgq = queue.Queue()
        self.filter_category = self.ui_state.get("category", "全部")
        self.only_unread = tk.BooleanVar(value=self.ui_state.get("only_unread", False))
        self.keywords_only = tk.BooleanVar(value=self.ui_state.get("keywords_only", False))
        self.auto_var = tk.BooleanVar(value=self.ui_state.get("auto_refresh", False))
        self.auto_minutes = tk.IntVar(value=self.ui_state.get("auto_minutes", 30))
        self.sort_mode = self.ui_state.get("sort_mode", "weight")
        self.detail_site = ""
        self._typing_job = None
        self._drag = None
        self._wheel_acc = 0                 # 小增量滚轮的累积器
        self._wheel_seen = {}

        self._build_window()
        self._build_menu()
        self._build_bar()
        self._build_stats()
        self._build_filters()
        self._build_views()
        self._build_status()
        self._bind_drag()
        self._bind_keys()
        self._load_cached()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(120, self._pump)
        if self.auto_var.get():
            self._schedule_auto()
        if not self.cache.get("items") and autostart:
            self.root.after(400, self.refresh)
        else:
            self.set_status("已载入本地缓存（更新于 %s）｜按住拖动 / 滚轮 / 方向键·PgUp·PgDn 都能滚动；"
                            "点「立即刷新」抓取最新通知"
                            % (self.cache.get("updated") or "未知"))
        core.get_logger().info("看板界面启动完成")

    # ------------------------------------------------------------ 配置

    def _load_weights(self):
        w = self.cfg.get("weights")
        changed = False
        if not isinstance(w, dict):
            w = {}
            changed = True
        for k, v in DEFAULT_WEIGHTS.items():
            if k not in w:
                w[k] = v
                changed = True
        if changed:
            self.cfg["weights"] = w
            core.save_config(self.cfg)
        return w

    def _formula(self):
        f = dict(DEFAULT_WEIGHTS["formula"])
        f.update(self.wcfg.get("formula") or {})
        return f

    # ------------------------------------------------------------ 界面搭建

    def _build_window(self):
        self.root.title("%s v%s" % (core.APP_TITLE, core.APP_VERSION))
        s = U.sx
        lw = self.root.winfo_screenwidth() / U.S
        lh = self.root.winfo_screenheight() / U.S
        w = int(min(1280, max(960, lw - 60)))
        h = int(min(860, max(620, lh - 70)))
        geo = self._safe_geometry(self.ui_state.get("geometry"), "%dx%d" % (s(w), s(h)))
        self.root.geometry(geo)
        self.root.minsize(s(920), s(600))
        self.root.configure(bg=U.BG)

    def _safe_geometry(self, saved, default):
        """恢复上次窗口位置，但保证整扇窗口都留在桌面可用区（任务栏以上）。

        换显示器 / 改分辨率后，上次保存的坐标可能落在屏幕外；
        以前只保证左上角可见，窗口偏高时底部的状态栏会被任务栏盖住。
        """
        al, at, ar, ab = U.work_area(self.root)
        aw, ah = max(320, ar - al), max(240, ab - at)
        if not saved:
            return default
        m = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", str(saved).strip())
        if not m:
            return default
        w, h = int(m.group(1)), int(m.group(2))
        if w > aw or h > ah:                 # 上次是在更大的屏上开的：缩到当前桌面能放下
            w, h = min(w, aw), min(h, ah)
        x = min(max(int(m.group(3)), al), max(al, ar - w))
        y = min(max(int(m.group(4)), at), max(at, ab - h))
        return "%dx%d+%d+%d" % (w, h, x, y)

    def _build_menu(self):
        bar = tk.Menu(self.root, font=U.F(9.5))
        m_file = tk.Menu(bar, tearoff=0, font=U.F(9.5))
        m_file.add_command(label="立即刷新（F5）", command=self.refresh)
        m_file.add_command(label="导出当前列表…", command=self.export_current)
        m_file.add_separator()
        m_file.add_command(label="打开数据目录", command=self.open_data_dir)
        m_file.add_command(label="编辑站点与权重配置 config.json", command=self.open_config)
        m_file.add_separator()
        m_file.add_command(label="退出", command=self.on_close)
        bar.add_cascade(label="文件", menu=m_file)

        m_view = tk.Menu(bar, tearoff=0, font=U.F(9.5))
        m_view.add_command(label="返回看板（Esc）", command=self.back_to_board)
        m_view.add_command(label="全部标记为已读", command=self.mark_all_read)
        m_view.add_checkbutton(label="只看新增", variable=self.only_unread, command=self.on_chip)
        m_view.add_checkbutton(label="只看命中关键词", variable=self.keywords_only, command=self.on_chip)
        m_view.add_command(label="清空检索", command=self.clear_search)
        m_view.add_separator()
        m_view.add_checkbutton(label="自动刷新", variable=self.auto_var, command=self.toggle_auto)
        m_auto = tk.Menu(m_view, tearoff=0, font=U.F(9.5))
        for mins in (15, 30, 60, 120):
            m_auto.add_radiobutton(label="每 %d 分钟" % mins, variable=self.auto_minutes,
                                   value=mins, command=self.toggle_auto)
        m_view.add_cascade(label="自动刷新间隔", menu=m_auto)
        bar.add_cascade(label="视图", menu=m_view)

        m_help = tk.Menu(bar, tearoff=0, font=U.F(9.5))
        m_help.add_command(label="使用说明", command=self.show_help)
        m_help.add_command(label="失败站点", command=self.show_errors)
        m_help.add_command(label="关于", command=self.show_about)
        bar.add_cascade(label="帮助", menu=m_help)
        self.root.config(menu=bar)
        self.root.bind("<F5>", lambda e: self.refresh())
        self.root.bind("<Escape>", lambda e: self.back_to_board())
        # 滚轮在窗口任意位置都能滚（tk 只把滚轮发给「有绑定」的控件，绑在窗口级最省事）
        self.root.bind("<MouseWheel>", self._on_wheel, add="+")

    def _build_bar(self):
        s = U.sx
        bar = tk.Frame(self.root, bg=U.BG)
        bar.pack(fill="x", padx=s(16), pady=(s(12), s(10)))

        logo = tk.Canvas(bar, width=s(40), height=s(40), bg=U.BG, highlightthickness=0)
        logo.pack(side="left")
        U.round_rect(logo, 0, 0, s(40) - 1, s(40) - 1, s(13), fill=U.PURPLE_D, outline="")
        U.round_rect(logo, 0, 0, s(40) - 1, s(25), s(13), fill="#6d4ede", outline="")
        logo.create_text(s(20), s(20), text="雷", fill="#ffffff", font=U.F(13, True))

        brand = tk.Frame(bar, bg=U.BG)
        brand.pack(side="left", padx=(s(12), 0))
        tk.Label(brand, text="川大通知雷达", bg=U.BG, fg=U.TXT,
                 font=U.F(13.5, True)).pack(anchor="w")
        tk.Label(brand, text="教务处 · 学工部 · 学校 · 各学院，只提醒新增",
                 bg=U.BG, fg=U.MUTE, font=U.F(8.5)).pack(anchor="w")

        self.btn_refresh = U.Pill(bar, "立即刷新", command=self.refresh, kind="primary",
                                  width=94, height=36)
        self.btn_refresh.pack(side="right", pady=(s(2), 0))
        U.Pill(bar, "导出", command=self.export_current, width=68, height=36).pack(
            side="right", padx=(0, s(10)), pady=(s(2), 0))

        self.q_var = tk.StringVar(value=self.ui_state.get("query", ""))
        search = U.SearchBox(bar, self.q_var, self._on_typing, width=360, height=36)
        search.pack(side="right", padx=(0, s(14)), pady=(s(2), 0))

    def _build_stats(self):
        s = U.sx
        row = tk.Frame(self.root, bg=U.BG)
        row.pack(fill="x", padx=s(16), pady=(0, s(10)))
        defs = [("总通知", "0", False), ("新增", "0", True), ("命中关键词", "0", False),
                ("来源", "0", False), ("最后刷新", "—", False)]
        self.stats = []
        for i, (label, val, hot) in enumerate(defs):
            c = U.StatCard(row, label, val, hot)
            c.pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else s(5), 0))
            self.stats.append(c)

    def _build_filters(self):
        s = U.sx
        row = tk.Frame(self.root, bg=U.BG)
        row.pack(fill="x", padx=s(16), pady=(0, s(8)))
        self.cat_chips = {}
        for name in ["全部", "教务处", "学工部", "学校", "学院"]:
            c = U.Pill(row, name, command=lambda n=name: self.set_category(n),
                       kind="on" if name == self.filter_category else "off", height=28, font=U.F(8.5))
            c.pack(side="left", padx=(0, s(6)))
            self.cat_chips[name] = c
        self.chip_unread = U.Pill(row, "只看新增", command=self.toggle_unread,
                                  kind="on" if self.only_unread.get() else "off",
                                  height=28, font=U.F(8.5))
        self.chip_unread.pack(side="left", padx=(s(14), s(6)))
        self.chip_kw = U.Pill(row, "★ 命中关键词", command=self.toggle_kw,
                              kind="on" if self.keywords_only.get() else "off",
                              height=28, font=U.F(8.5))
        self.chip_kw.pack(side="left", padx=(0, s(6)))

        tk.Label(row, text="排序", bg=U.BG, fg=U.MUTE, font=U.F(8.5)).pack(side="right", padx=(s(6), 0))
        self.sort_chips = {}
        for key, label in [("latest", "最新优先"), ("count", "条数优先"), ("weight", "权重优先")]:
            c = U.Pill(row, label, command=lambda k=key: self.set_sort(k),
                       kind="on" if key == self.sort_mode else "off", height=28, font=U.F(8.5))
            c.pack(side="right", padx=(s(6), 0))
            self.sort_chips[key] = c

    def _build_views(self):
        s = U.sx
        self.host = tk.Frame(self.root, bg=U.PANEL)
        self.host.pack(fill="both", expand=True, padx=s(16), pady=(0, s(8)))
        self.board = U.BoardView(self.host, self, on_open=self.open_source, on_menu=self.source_menu)
        self.detail = U.DetailView(self.host, self, on_back=self.back_to_board, on_open=self.open_item)
        self.board.pack(fill="both", expand=True)

    def _build_status(self):
        s = U.sx
        bar = tk.Frame(self.root, bg="#141821")
        bar.pack(fill="x", side="bottom")
        self.status = tk.Label(bar, text="就绪", bg="#141821", fg=U.DIM,
                               font=U.F(8.5), anchor="w")
        self.status.pack(side="left", padx=s(14), pady=s(6))
        self.prog = tk.Canvas(bar, width=s(170), height=s(6), bg="#141821", highlightthickness=0)
        self.prog.pack(side="right", padx=s(14))
        self._prog_frac = 0.0
        self._draw_progress()

    def _draw_progress(self):
        s = U.sx
        w = s(170)
        self.prog.delete("all")
        U.round_rect(self.prog, 0, 0, w - 1, s(5), s(3), fill="#232a38", outline="")
        if self._prog_frac > 0:
            U.round_rect(self.prog, 0, 0, max(s(6), w * self._prog_frac), s(5), s(3),
                         fill=U.PURPLE_D, outline="")

    def _set_progress(self, frac):
        self._prog_frac = max(0.0, min(1.0, frac))
        self._draw_progress()

    # ------------------------------------------------------------ 滚轮

    def _on_wheel(self, event):
        """把滚轮转成行数：标准滚轮 120 一档滚 3 行；触控板/高精度滚轮的小增量累积后再滚。"""
        d = getattr(event, "delta", 0) or 0
        if not d:
            return
        if d not in self._wheel_seen and len(self._wheel_seen) < 6:
            self._wheel_seen[d] = 0
            core.get_logger().info("滚轮 delta=%s", d)
        acc = self._wheel_acc + d
        whole = int(acc / 120)
        lines = -whole * 3
        rest = acc - whole * 120
        if not lines and abs(rest) >= 45:     # 小增量攺到 ~1/3 档先滚一行，手感更跟手
            lines = -1 if rest > 0 else 1
            rest = 0
        self._wheel_acc = rest
        if lines:
            if self.detail_site:
                self.detail.scroll(lines)
            else:
                self.board.scroll(lines)

    # ------------------------------------------------------------ 拖动窗口

    def _bind_drag(self):
        """只有「四周装饰区」能按住左键拖动窗口（顶栏/统计条/筛选条/状态栏）。

        内容区（看板、二级列表）不绑这个：用户用触摸屏/鼠标按住拖动时，期望的是
        **滑动页面**（见 ui_v2 的 pan_start/pan_move），而不是窗口跟着跑。
        另：不绑 root —— tkinter 的 bindtags 会把子控件事件冒泡给 toplevel，
        绑 root 则点按钮/输入框也会触发拖动。
        """
        skip = {"Pill", "SearchBox", "Tile", "Entry", "TEntry", "Text",
                "Scrollbar", "Spinbox", "Menu"}
        content = set()

        def collect(w):
            content.add(str(w))
            for c in w.winfo_children():
                collect(c)

        for c in self.host.winfo_children():     # host 里是看板/二级页，内容区
            collect(c)

        widgets = []

        def walk(w):
            widgets.append(w)
            try:
                kids = w.winfo_children()
            except Exception:  # noqa: BLE001
                return
            for c in kids:
                walk(c)

        for child in self.root.winfo_children():   # 跳过 root 自身
            walk(child)

        count = 0
        for w in widgets:
            if (type(w).__name__ in skip or getattr(w, "_no_drag", False)
                    or str(w) in content):
                continue
            try:
                w.bind("<Button-1>", self._drag_start, add="+")
                w.bind("<B1-Motion>", self._drag_move, add="+")
                w.bind("<ButtonRelease-1>", self._drag_end, add="+")
                count += 1
            except Exception:  # noqa: BLE001
                pass
        self._drag_areas = count
        core.get_logger().info("窗口拖动已启用：装饰区可拖动 %d 个（内容区改为拖动滑动）", count)

    # ------------------------------------------------------------ 键盘滚动

    def _bind_keys(self):
        """方向键 / PgUp·PgDn / Home·End / 空格 也能滚动页面。

        触摸手势在某些机器上会被系统吞掉，键盘是不依赖触摸的兑底手段；
        在搜索框里打字时不抢键（见 _on_key）。
        “绑定在 root 上”对子控件同样生效（bindtags 含 toplevel）。
        """
        for seq in ("<Prior>", "<Next>", "<Up>", "<Down>", "<Home>", "<End>", "<space>"):
            try:
                self.root.bind(seq, self._on_key, add="+")
            except Exception:  # noqa: BLE001
                pass

    def _current_view(self):
        if self.detail_site and self.detail.winfo_ismapped():
            return self.detail
        return self.board

    def _on_key(self, event):
        try:
            fw = self.root.focus_get()
        except Exception:  # noqa: BLE001
            fw = None
        if fw is not None and type(fw).__name__ in ("Entry", "TEntry", "Text"):
            return None                    # 正在输入，不抢键
        v = self._current_view()
        k = event.keysym
        if k == "Prior":
            v.scroll_pages(-1)
        elif k == "Next":
            v.scroll_pages(1)
        elif k == "Up":
            v.scroll_lines(-3)
        elif k == "Down":
            v.scroll_lines(3)
        elif k == "Home":
            v.to_top()
        elif k == "End":
            v.to_bottom()
        elif k == "space":
            v.scroll_pages(1)
        else:
            return None
        return "break"

    def _drag_start(self, event):
        self._drag = {
            "x": event.x_root, "y": event.y_root,
            "wx": self.root.winfo_x(), "wy": self.root.winfo_y(),
            "moved": False,
        }

    def _drag_move(self, event):
        d = self._drag
        if not d:
            return
        dx = event.x_root - d["x"]
        dy = event.y_root - d["y"]
        if not d["moved"] and abs(dx) + abs(dy) < 4:
            return                      # 手抖不算拖动，避免误触
        if not d["moved"]:
            d["moved"] = True
            try:
                self.root.configure(cursor="sizeall")
            except Exception:  # noqa: BLE001
                pass
        self.root.geometry("+%d+%d" % (d["wx"] + dx, d["wy"] + dy))

    def _drag_end(self, _event):
        if self._drag and self._drag["moved"]:
            try:
                self.root.configure(cursor="")
            except Exception:  # noqa: BLE001
                pass
            geo = self.root.geometry()
            self.ui_state["geometry"] = geo
        self._drag = None

    # ------------------------------------------------------------ 数据 → 分格

    def _filtered_items(self):
        return [it for it in self.items if self._pass(it)]

    def _pass(self, it, query=None, category=None, site="", only_unread=None):
        query = self.q_var.get() if query is None else query
        category = self.filter_category if category is None else category
        only_unread = self.only_unread.get() if only_unread is None else only_unread
        return core.filter_items([it], query=query, category=category, site=site,
                                 only_unread=only_unread, seen=self.seen,
                                 keywords_only=self.keywords_only.get())

    def source_specs(self, ignore_query=False):
        f = self._formula()
        pri_map = self.wcfg.get("priority") or {}
        default_pri = self.wcfg.get("default_priority", 1)
        src = self.items if ignore_query else self._filtered_items()
        agg = {}
        for it in src:
            s = it.get("site") or "未知来源"
            a = agg.setdefault(s, {"n": 0, "unread": 0, "hits": 0, "r30": 0, "dates": [], "titles": []})
            a["n"] += 1
            if core.is_unread(it, self.seen):
                a["unread"] += 1
            a["hits"] += len(it.get("hits") or [])
            a["titles"].append((it.get("date") or "", it.get("title") or ""))
        today = datetime.now().date()
        specs = []
        for name, a in agg.items():
            a["titles"].sort(key=lambda x: x[0], reverse=True)
            series = [0] * 12
            r30 = 0
            for d, _t in a["titles"]:
                try:
                    dd = datetime.strptime(d, "%Y-%m-%d").date()
                except Exception:  # noqa: BLE001
                    continue
                delta = (today - dd).days
                if delta <= 30:
                    r30 += 1
                k = delta // 7
                if 0 <= k < 12:
                    series[11 - k] += 1
            pri = pri_map.get(name, default_pri)
            weight = round(pri * f["priority"] + r30 * f["recent30"]
                           + a["hits"] * f["hits"] + a["unread"] * f["unread"], 1)
            specs.append({"name": name, "n": a["n"], "new": a["unread"], "unread": a["unread"],
                          "hits": a["hits"], "r30": r30, "series": series, "weight": weight,
                          "titles": a["titles"][:3],
                          "latest": a["titles"][0][0] if a["titles"] else "",
                          "key": pri >= 3})
        specs.sort(key=lambda s: (-s["weight"], -s["n"]))
        return specs

    def render_board(self):
        specs = self.source_specs()
        t = self.wcfg.get("tiers") or DEFAULT_WEIGHTS["tiers"]
        self.board.render(specs, self.sort_mode,
                          (t.get("hero", 3), t.get("large", 4), t.get("mid", 8)))
        total = len(self._filtered_items())
        unread = sum(1 for i in self._filtered_items() if core.is_unread(i, self.seen))
        hits = sum(1 for i in self._filtered_items() if i.get("hits"))
        self.stats[0].set(str(total))
        self.stats[1].set(str(unread), hot=unread > 0)
        self.stats[2].set(str(hits))
        self.stats[3].set(str(len(specs)))
        u = self.cache.get("updated") or ""
        self.stats[4].set((u[5:16] if len(u) >= 16 else (u[:10] or "—")))

    def _load_cached(self):
        self.items = self.cache.get("items", [])
        self.errors = self.cache.get("errors", [])
        self.render_board()

    # ------------------------------------------------------------ 交互

    def on_chip(self, *_a):
        self.chip_unread.cset_kind("on" if self.only_unread.get() else "off")
        self.chip_kw.cset_kind("on" if self.keywords_only.get() else "off")
        self._refresh_view()

    def set_category(self, name):
        self.filter_category = name
        for k, c in self.cat_chips.items():
            c.cset_kind("on" if k == name else "off")
        if self.detail_site:
            self.back_to_board()        # 分类是看板级筛选，在二级页点它就回看板
        else:
            self.render_board()

    def toggle_unread(self):
        self.only_unread.set(not self.only_unread.get())
        self.on_chip()

    def toggle_kw(self):
        self.keywords_only.set(not self.keywords_only.get())
        self.on_chip()

    def set_sort(self, mode):
        self.sort_mode = mode
        for k, c in self.sort_chips.items():
            c.cset_kind("on" if k == mode else "off")
        self._refresh_view()

    def _refresh_view(self):
        if self.detail_site:
            self.open_source(self.detail_site)
        else:
            self.render_board()

    def _on_typing(self):
        if self._typing_job:
            try:
                self.root.after_cancel(self._typing_job)
            except Exception:  # noqa: BLE001
                pass
        self._typing_job = self.root.after(250, self._refresh_view)

    def open_source(self, site):
        self.detail_site = site
        self.board.pack_forget()
        self.detail.pack(fill="both", expand=True)
        self.filter_detail()

    def back_to_board(self):
        self.detail_site = ""
        self.detail.pack_forget()
        self.board.pack(fill="both", expand=True)
        self.render_board()

    def filter_detail(self, dv=None):
        dv = dv or self.detail
        site = self.detail_site or dv.site          # 二级页的来源以当前打开的格子为准
        rows = core.filter_items(self.items, query=self.q_var.get(), category="全部",
                                 site=site, only_unread=(dv.only_unread or self.only_unread.get()),
                                 seen=self.seen, keywords_only=self.keywords_only.get())
        rows.sort(key=lambda i: (i.get("date") or "", i.get("title") or ""), reverse=True)
        allrows = core.filter_items(self.items, query="", category="全部", site=site,
                                    only_unread=False, seen=self.seen, keywords_only=False)
        specs = {s["name"]: s for s in self.source_specs(ignore_query=True)}
        w = specs.get(site, {}).get("weight", 0.0)
        dv.show(site, rows, w, len(allrows))
        self.set_status("%s：显示 %d 条（该来源共 %d 条）" % (site, len(rows), len(allrows)))

    def is_new(self, it):
        return core.is_unread(it, self.seen)

    def open_item(self, it):
        try:
            webbrowser.open(it["url"])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打不开浏览器", str(exc))
            return
        core.set_read(self.cache, it["url"], True)
        self.seen = self.cache.get("seen", {})
        self._after_read()

    def _after_read(self):
        if self.detail_site:
            self.filter_detail()
        self.render_board()
        self.set_status("已标记为已读")

    def source_menu(self, spec, x, y):
        menu = tk.Menu(self.root, tearoff=0, font=U.F(9.5))
        menu.add_command(label="打开「%s」" % spec["name"], command=lambda: self.open_source(spec["name"]))
        if spec["titles"]:
            menu.add_command(label="在浏览器打开最新一条",
                             command=lambda: self.open_item_soft(spec["titles"][0][1]))
        menu.add_separator()
        menu.add_command(label="把这个来源全部标记为已读",
                         command=lambda: self.mark_site_read(spec["name"]))
        try:
            menu.tk_popup(x, y)
        finally:
            try:
                menu.grab_release()
            except Exception:  # noqa: BLE001
                pass

    def open_item_soft(self, title):
        for it in self.items:
            if it.get("title") == title:
                self.open_item(it)
                return

    def mark_site_read(self, site):
        n = 0
        for it in self.items:
            if it.get("site") == site and core.is_unread(it, self.seen):
                core.set_read(self.cache, it["url"], True)
                n += 1
        core.save_cache(self.cache)
        self.seen = self.cache.get("seen", {})
        self._refresh_view()
        self.set_status("已把「%s」的 %d 条标记为已读" % (site, n))

    def item_menu(self, it, x, y):
        menu = tk.Menu(self.root, tearoff=0, font=U.F(9.5))
        menu.add_command(label="在浏览器打开", command=lambda: self.open_item(it))
        menu.add_command(label="复制标题", command=lambda: self._copy(it.get("title", ""), "标题"))
        menu.add_command(label="复制链接", command=lambda: self._copy(it.get("url", ""), "链接"))
        menu.add_separator()
        menu.add_command(label="标记为已读", command=lambda: self.mark_item(it, True))
        menu.add_command(label="标记为未读", command=lambda: self.mark_item(it, False))
        try:
            menu.tk_popup(x, y)
        finally:
            try:
                menu.grab_release()
            except Exception:  # noqa: BLE001
                pass

    def mark_item(self, it, read):
        core.set_read(self.cache, it["url"], read)
        self.seen = self.cache.get("seen", {})
        if self.detail_site:
            self.filter_detail()
        self.render_board()
        self.set_status("已标记为%s" % ("已读" if read else "未读"))

    def _copy(self, text, what):
        self.root.clipboard_clear()
        self.root.clipboard_append(text or "")
        self.set_status("%s已复制" % what)

    def clear_search(self):
        self.q_var.set("")
        self.set_category("全部")
        self.only_unread.set(False)
        self.keywords_only.set(False)
        self.on_chip()

    # ------------------------------------------------------------ 抓取

    def refresh(self):
        if self.busy:
            self.set_status("正在刷新中，请稍候…")
            return
        try:                                  # 每次刷新都重读配置：改了 config.json 无需重启
            self.cfg = core.load_config()
            self.wcfg = self._load_weights()
        except Exception as exc:  # noqa: BLE001
            core.get_logger().warning("重读配置失败，沿用内存中的配置：%s", exc)
        sites = [s for s in self.cfg["sites"] if s.get("enabled", True)]
        if not sites:
            messagebox.showwarning("没有可用站点", "所有站点都被禁用了，请编辑 config.json 启用站点。")
            return
        self.busy = True
        self.btn_refresh.set_text("刷新中…")
        self._set_progress(0.02)
        self.set_status("正在抓取 %d 个站点…" % len(sites))
        core.get_logger().info("开始刷新，共 %d 个站点", len(sites))
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            def prog(done, total, name):
                self.msgq.put(("progress", done, total, name))
            new_cache = core.refresh(self.cfg, self.cache, progress=prog)
            self.msgq.put(("done", new_cache))
        except Exception as exc:  # noqa: BLE001
            core.get_logger().error("刷新失败：%s\n%s", exc, traceback.format_exc())
            self.msgq.put(("fail", "%s: %s" % (type(exc).__name__, exc)))

    def _pump(self):
        try:
            while True:
                msg = self.msgq.get_nowait()
                if msg[0] == "progress":
                    _, done, total, name = msg
                    self._set_progress(done / max(1, total))
                    self.set_status("已抓取 %d/%d：%s" % (done, total, name))
                elif msg[0] == "done":
                    self._on_done(msg[1])
                elif msg[0] == "fail":
                    self._on_fail(msg[1])
        except queue.Empty:
            pass
        if not self.destroying:
            self.root.after(150, self._pump)

    def _on_done(self, new_cache):
        self.busy = False
        self.btn_refresh.set_text("立即刷新")
        self._set_progress(0)
        old_urls = {i["url"] for i in self.items}
        self.cache = new_cache
        self.seen = self.cache.get("seen", {})
        self.items = self.cache.get("items", [])
        self.errors = self.cache.get("errors", [])
        new_cnt = sum(1 for i in self.items if core.is_unread(i, self.seen) and i["url"] not in old_urls)
        first = self.cache.pop("first_run", False)
        if first:
            core.save_cache(self.cache)
            self.set_status("首次运行已完成：已把现有通知全部记为已读，之后只提醒新增。")
        else:
            parts = ["刷新完成 %s" % self.cache.get("updated"), "共 %d 条" % len(self.items),
                     "新增 %d 条" % new_cnt]
            stale = self.cache.get("stale_sites") or []
            if self.errors:
                parts.append("%d 个站点失败%s"
                             % (len(self.errors),
                                "（%s 沿用上次数据）" % "、".join(stale) if stale else ""))
            elif stale:
                parts.append("%d 个来源沿用上次数据" % len(stale))
            self.set_status("｜".join(parts))
        self._refresh_view()
        core.get_logger().info("刷新完成：%d 条，新增 %d，失败 %d",
                               len(self.items), new_cnt, len(self.errors))

    def _on_fail(self, err):
        self.busy = False
        self.btn_refresh.set_text("立即刷新")
        self._set_progress(0)
        self.set_status("刷新失败：%s" % err)
        messagebox.showerror("刷新失败", "抓取过程中出错：\n%s\n\n详情见 data/app.log" % err)

    # ------------------------------------------------------------ 其它

    def export_current(self):
        if self.detail_site:
            rows = core.filter_items(self.items, query=self.q_var.get(), category="全部",
                                     site=self.detail_site,
                                     only_unread=(self.detail.only_unread or self.only_unread.get()),
                                     seen=self.seen, keywords_only=self.keywords_only.get())
            scope = self.detail_site
        else:
            rows = sorted(self._filtered_items(),
                          key=lambda i: (i.get("date") or ""), reverse=True)
            scope = self.filter_category
        if not rows:
            messagebox.showinfo("提示", "当前没有可导出的内容。")
            return
        out_dir = core.data_dir() / "exports"
        try:
            md, html = core.export_view(rows, self.seen, out_dir,
                                        query=self.q_var.get(), category=scope or "全部")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc))
            return
        if messagebox.askyesno("导出完成", "已导出 %d 条：\n%s\n%s\n\n要打开导出目录吗？"
                               % (len(rows), html.name, md.name)):
            try:
                os.startfile(str(out_dir))  # noqa: S606
            except Exception:  # noqa: BLE001
                pass

    def mark_all_read(self):
        if not messagebox.askyesno("确认", "把当前 %d 条通知全部标记为已读？" % len(self.items)):
            return
        core.mark_all_read(self.cache)
        self.seen = self.cache.get("seen", {})
        core.save_cache(self.cache)
        self._refresh_view()
        self.set_status("全部标记为已读")

    def open_data_dir(self):
        try:
            os.startfile(str(core.data_dir()))  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打不开目录", str(exc))

    def open_config(self):
        path = core.CONFIG_PATH()
        if not path.exists():
            core.save_config(self.cfg)
        try:
            os.startfile(str(path))  # noqa: S606
            messagebox.showinfo("编辑配置",
                                "已用记事本打开：\n%s\n\n"
                                "· sites：增删来源\n"
                                "· weights.priority：每个来源的优先级（数值越大格子越大）\n"
                                "· weights.formula：权重公式系数\n"
                                "· weights.tiers：大格/中格的个数\n"
                                "改完保存，再点「立即刷新」即可生效。" % path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打不开配置文件", str(exc))

    def show_errors(self):
        if not self.errors:
            messagebox.showinfo("失败站点", "本次刷新没有站点失败。")
            return
        text = "\n".join("· %s\n  %s" % (e["site"], e["error"]) for e in self.errors)
        messagebox.showwarning("失败的站点", text + "\n\n常见原因：校园网未连接、站点临时维护、该站有反爬。")

    def show_about(self):
        messagebox.showinfo("关于", "%s v%s\n\n"
                                   "看板式界面：每个来源一个分格，格子大小按权重排——"
                                   "教务处、学工部、学校和你所在学院默认最大，有新增会高亮。\n"
                                   "纯本地运行，不需要登录，不上传任何数据。\n\n"
                                   "配置与数据目录：\n%s" % (core.APP_TITLE, core.APP_VERSION, core.data_dir()))

    def show_help(self):
        messagebox.showinfo("使用说明",
                            "1) 看板上的每个格子 = 一个来源，格子越大权重越高；紫色描边表示有新增。\n"
                            "2) 点格子进入该来源的通知列表，点一下某条就在浏览器打开（自动标为已读）。\n"
                            "3) 顶部检索框输入即搜：空格=多个词都要含，-词=排除。\n"
                            "4) 筛选胶囊可只看「教务处/学工部/学校/学院」，也可只看新增或命中关键词。\n"
                            "5) 排序可切「权重优先 / 条数优先 / 最新优先」。\n"
                            "6) 权重公式与每个来源的优先级都能在 config.json 的 weights 里改。\n"
                            "7) 看板/列表里：按住鼠标或手指上下拖动 = 滑动页面（甩一下还有惯性）。\n"
                            "8) 想移动窗口：拖顶栏/统计条/状态栏这些周边装饰区，或直接用系统标题栏；窗口位置会自动记住。\n"
                            "9) 鼠标滚轮在界面任意位置都能滚动，右侧的进度条也可以直接拖。\n"
                            "10) 「视图 → 自动刷新」可让软件每 15/30/60/120 分钟自己抓一次。\n"
                            "11) 断网也能看：软件显示上次抓取的缓存。")

    def _schedule_auto(self):
        if getattr(self, "_auto_job", None):
            try:
                self.root.after_cancel(self._auto_job)
            except Exception:  # noqa: BLE001
                pass
            self._auto_job = None
        if self.auto_var.get():
            minutes = max(5, int(self.auto_minutes.get() or 30))
            self._auto_job = self.root.after(minutes * 60 * 1000, self._auto_tick)
            self.set_status("已开启自动刷新：每 %d 分钟" % minutes)
        else:
            self.set_status("已关闭自动刷新")

    def toggle_auto(self):
        """菜单里的「自动刷新」开关 / 间隔：立即生效并记住设置。"""
        self._schedule_auto()
        try:
            core.save_ui({
                "geometry": self.root.geometry(),
                "query": self.q_var.get(),
                "category": self.filter_category,
                "only_unread": bool(self.only_unread.get()),
                "keywords_only": bool(self.keywords_only.get()),
                "sort_mode": self.sort_mode,
                "auto_refresh": bool(self.auto_var.get()),
                "auto_minutes": int(self.auto_minutes.get() or 30),
            })
        except Exception:  # noqa: BLE001
            pass

    def _auto_tick(self):
        self._auto_job = None
        if self.auto_var.get():
            self.refresh()
            self._schedule_auto()

    def set_status(self, text):
        self.status.config(text=text)

    def on_close(self):
        try:
            core.save_ui({
                "geometry": self.root.geometry(),
                "query": self.q_var.get(),
                "category": self.filter_category,
                "only_unread": bool(self.only_unread.get()),
                "keywords_only": bool(self.keywords_only.get()),
                "sort_mode": self.sort_mode,
                "auto_refresh": bool(self.auto_var.get()),
                "auto_minutes": int(self.auto_minutes.get() or 30),
            })
        except Exception:  # noqa: BLE001
            pass
        self.destroying = True
        self.root.destroy()


# ------------------------------------------------------------------ 自检

def selftest() -> int:
    out = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "steps": []}
    ok = True

    def step(name, fn):
        nonlocal ok
        t0 = time.time()
        try:
            res = fn()
            out["steps"].append({"step": name, "ok": True, "cost": round(time.time() - t0, 2), "result": res})
        except Exception as exc:  # noqa: BLE001
            ok = False
            out["steps"].append({"step": name, "ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})

    def t_config():
        cfg = core.load_config()
        return {"sites": len(cfg["sites"]),
                "enabled": sum(1 for s in cfg["sites"] if s.get("enabled", True)),
                "keywords": len(cfg["keywords"]),
                "weights": bool(cfg.get("weights"))}

    def t_parse():
        html = ('<li><a href="info/1/2.htm"><div class="date"><p>09/20 </p><span>2026</span></div>'
                '<div class="text"><p>测试通知标题</p></div></a></li>')
        items = radar.parse_items(html, "https://example.com/tzgg.htm")
        assert items and items[0]["date"] == "2026-09-20", items
        return items[0]

    def t_fetch_and_cache():
        cfg = core.load_config()
        cache = core.load_cache()
        cache = core.refresh(cfg, cache)
        return {"items": len(cache["items"]), "errors": len(cache.get("errors", [])),
                "updated": cache.get("updated")}

    def t_search():
        cache = core.load_cache()
        hits = core.filter_items(cache["items"], query="推免")
        cats = sorted({i["category"] for i in cache["items"]})
        return {"items": len(cache["items"]), "推免命中": len(hits), "categories": cats}

    def t_weights():
        cfg = core.load_config()
        w = cfg.get("weights") or {}
        assert w.get("formula"), w
        return {"priority_sites": len(w.get("priority") or {}), "formula": w.get("formula")}

    def t_export():
        cache = core.load_cache()
        md, html = core.export_view(cache["items"][:10], cache.get("seen", {}),
                                    core.data_dir() / "exports_selftest", "自检", "全部")
        return {"md": str(md), "html": str(html)}

    step("读取配置与权重", t_config)
    step("解析器", t_parse)
    step("抓取全部站点并写缓存", t_fetch_and_cache)
    step("检索与分类", t_search)
    step("权重配置", t_weights)
    step("导出", t_export)

    target = core.data_dir() / "selftest.json"
    target.write_text(__import__("json").dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    core.get_logger().info("自检结果：%s", "全部通过" if ok else "存在失败")
    return 0 if ok else 1


_SINGLE_HANDLE = None          # 单实例互斥体，必须全局持有（进程结束才释放）


def _focus_existing_window():
    """把已经在运行的那扇窗口顶到最前面。"""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _lp):
            n = user32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value.startswith(core.APP_TITLE):
                    found.append(hwnd)
            return True

        user32.EnumWindows(cb, 0)
        if found:
            user32.ShowWindow(found[0], 9)          # SW_RESTORE
            user32.SetForegroundWindow(found[0])
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _already_running():
    """同一时间只开一个窗口：重复启动时把已有窗口拉到前面。

    两个窗口同时跑会互相覆盖 ui.json / cache.json（各写各的），
    也会同时去抢 21 个站点，没必要。
    """
    if os.name != "nt":
        return False
    global _SINGLE_HANDLE
    try:
        import ctypes
        k = ctypes.windll.kernel32
        _SINGLE_HANDLE = k.CreateMutexW(None, False, "Local\\SCU_Notice_Radar_single")
        if k.GetLastError() == 183:                 # ERROR_ALREADY_EXISTS
            _focus_existing_window()
            return True
    except Exception:  # noqa: BLE001
        return False
    return False


def main():
    if sys.platform == "win32":          # 必须先于 tk.Tk()：否则 tk 按虚拟化坐标工作，界面会整体偏小
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass

    if "--selftest" in sys.argv:
        sys.exit(selftest())

    if not TK_AVAILABLE:
        print("当前 Python 环境没有 tkinter，无法启动图形界面。请用带 tkinter 的 Python 运行。")
        sys.exit(2)

    if "--uicheck" in sys.argv:
        root = tk.Tk()
        root.withdraw()
        app = RadarApp(root, autostart=False)
        _ = app
        root.update()

        def _report():
            try:
                tiles = len(app.board.inner.winfo_children())
                core.get_logger().info("界面自检通过：看板格子 %d 个，缩放 %.2f，窗口 %s",
                                       tiles, U.S, root.winfo_geometry())
            except Exception as exc:  # noqa: BLE001
                core.get_logger().error("界面自检统计失败：%s", exc)
            root.destroy()

        root.after(1500, _report)
        root.mainloop()
        sys.exit(0)

    if "--multi" not in sys.argv and _already_running():
        print("川大通知雷达已经在运行，已把它的窗口移到前面。")
        sys.exit(0)

    root = tk.Tk()
    try:
        app = RadarApp(root)
        _ = app
        root.mainloop()
    except Exception as exc:  # noqa: BLE001
        core.get_logger().error("界面异常：%s\n%s", exc, traceback.format_exc())
        try:
            messagebox.showerror("程序出错", "发生异常：\n%s\n\n详情已写入 data/app.log" % exc)
        except Exception:  # noqa: BLE001
            pass
        raise


if __name__ == "__main__":
    main()