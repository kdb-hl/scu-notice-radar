# -*- coding: utf-8 -*-
"""
ui_v2.py —— 看板式界面组件（暗色紫蓝 · 分格按权重排布）

尺度说明：设计稿按 96dpi 的像素写，运行时会乘 S（=系统 DPI 缩放，如 150% → 1.5），
字体用“点”交给 tk 自动缩放，几何量用 sx() 缩放，保证不同缩放下比例一致。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
import time
from datetime import datetime

# ------------------------------------------------------------------ 主题

BG = "#0b0c11"
PANEL = "#0e1015"
CARD = "#181b23"
CARD_HOVER = "#232733"
CARD_PLAIN = "#141722"
BORDER = "#242833"
BORDER_HOVER = "#3c4354"
TXT = "#f2f5fa"
DIM = "#9aa3b8"
MUTE = "#6b7385"
PURPLE = "#a78bfa"
PURPLE_D = "#7c3aed"
BLUE = "#38bdf8"
GOLD = "#fbbf24"
ORANGE = "#fb923c"
SPARK_BG = "#2c2547"

FONT = "Microsoft YaHei UI"
S = 1.0
_MEASURE = {}


def set_scale(root) -> float:
    """按系统 DPI 定缩放（96dpi = 1.0）。"""
    global S
    try:
        S = max(1.0, root.winfo_fpixels("1i") / 96.0)
    except Exception:  # noqa: BLE001
        S = 1.0
    return S


def sx(v):
    return int(round(v * S))


def F(pt, bold=False):
    """字号用“点”（tk 会随 DPI 自动缩放）。"""
    size = max(7, int(round(pt)))
    return (FONT, size, "bold") if bold else (FONT, size)


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def round_rect(cv, x1, y1, x2, y2, r=16, **kw):
    r = max(1, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def _font_of(font):
    key = (font[0], font[1], font[2] if len(font) > 2 else "normal")
    f = _MEASURE.get(key)
    if f is None:
        f = _MEASURE[key] = tkfont.Font(family=font[0], size=font[1],
                                        weight=(font[2] if len(font) > 2 else "normal"))
    return f


def fit(cv, text, font, maxpx):
    """按像素宽度截断并加省略号。"""
    f = _font_of(font)
    text = text or ""
    if maxpx <= 4:
        return ""
    if f.measure(text) <= maxpx:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if f.measure(text[:mid] + ell) <= maxpx:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ell


def fmt_date(d):
    return (d or "—")[5:]


# ------------------------------------------------------------------ 基础控件

class Pill(tk.Canvas):
    """胶囊按钮 / 标签。"""

    def __init__(self, master, text, command=None, kind="ghost", width=None, height=34, font=None):
        self.kind = kind
        self._text = text
        self._font = font or F(10)
        self._h = sx(height)
        if width is None:
            width = _font_of(self._font).measure(text) + sx(30)
        else:
            width = sx(width)
        self._wid = width
        super().__init__(master, width=width, height=self._h, highlightthickness=0,
                         bg=master["bg"], bd=0, cursor="hand2" if command else "arrow")
        self.command = command
        self._hover = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        if command:
            self.bind("<Button-1>", lambda e: self.command())
        self._draw()

    def set_text(self, text):
        self._text = text
        self._draw()

    def cset_kind(self, kind):
        self.kind = kind
        self._draw()

    def _on_enter(self, _e):
        self._hover = True
        self._draw()

    def _on_leave(self, _e):
        self._hover = False
        self._draw()

    def _draw(self):
        w, h = self._wid, self._h
        self.delete("all")
        if self.kind == "primary":
            round_rect(self, 1, 1, w - 1, h - 1, h / 2,
                       fill=("#8b5cf6" if self._hover else PURPLE_D), outline="")
            fg = "#ffffff"
        elif self.kind == "on":
            round_rect(self, 1, 1, w - 1, h - 1, h / 2, fill="#3a2a63", outline="#7c5cd6")
            fg = "#e6dcff"
        elif self.kind == "off":
            round_rect(self, 1, 1, w - 1, h - 1, h / 2, fill="#1b1f29", outline="#2b3140")
            fg = "#9aa3b8"
        else:
            round_rect(self, 1, 1, w - 1, h - 1, h / 2,
                       fill=("#242a36" if self._hover else "#1b1f29"), outline="#2b3140")
            fg = "#d5dbe8"
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)


class StatCard(tk.Canvas):
    def __init__(self, master, label, value, hot=False):
        self._h = sx(56)
        super().__init__(master, height=self._h, highlightthickness=0, bg=master["bg"], bd=0)
        self.label = label
        self.value = value
        self.hot = hot
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, value, hot=None):
        self.value = value
        if hot is not None:
            self.hot = hot
        self._draw()

    def _draw(self):
        w = self.winfo_width() or 200
        self.delete("all")
        round_rect(self, 1, 1, w - 1, self._h - 1, sx(13), fill="#161a22", outline="#242a36")
        self.create_text(sx(14), sx(11), text=self.label, anchor="nw", fill=MUTE, font=F(8.5))
        self.create_text(sx(14), sx(29), text=self.value, anchor="nw",
                         fill=(PURPLE if self.hot else TXT), font=F(15, True))


class SearchBox(tk.Canvas):
    """圆角搜索框（内含真实 Entry，输入即搜）。"""

    def __init__(self, master, textvariable, on_change, width=360, height=36,
                 placeholder="搜索：推免 / 奖学金 / 补退选…"):
        self._wid = sx(width)
        self._h = sx(height)
        super().__init__(master, width=self._wid, height=self._h, bg=master["bg"],
                         highlightthickness=0, bd=0)
        self.placeholder = placeholder
        self.var = textvariable
        self.on_change = on_change
        self.entry = tk.Entry(self, textvariable=textvariable, bd=0, highlightthickness=0,
                              bg="#161a22", fg=TXT, insertbackground=PURPLE, font=F(9.5))
        self.create_window(sx(34), self._h / 2, anchor="w", window=self.entry,
                           width=self._wid - sx(46), height=self._h - sx(12))
        self.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.entry.bind("<KeyRelease>", self._changed)
        self.entry.bind("<FocusIn>", lambda e: self._draw())
        self.entry.bind("<FocusOut>", lambda e: self._draw())
        self._draw()

    def _changed(self, event=None):
        self.on_change()
        self._draw()

    def _draw(self):
        self.delete("bg")
        focus = self.focus_get() is self.entry
        round_rect(self, 1, 1, self._wid - 1, self._h - 1, self._h / 2,
                   fill="#161a22", outline=(PURPLE if focus else "#242a36"), tags="bg")
        cy = self._h / 2
        self.create_oval(sx(14), cy - sx(6), sx(24), cy + sx(4), outline=MUTE, width=1.4, tags="bg")
        self.create_line(sx(24), cy + sx(4), sx(29), cy + sx(9), fill=MUTE, width=1.6, tags="bg")
        if not self.var.get() and not focus:
            self.create_text(sx(36), cy, text=self.placeholder, anchor="w",
                             fill="#5c6577", font=F(9.5), tags="bg")
        self.tag_lower("bg")


def work_area(root=None):
    """桌面可用区（不含任务栏）。返回 (left, top, right, bottom)。

    恢复上次窗口位置时用它来限制：只保证「左上角在屏幕内」不够，
    窗口太高时底部的状态栏会被任务栏盖住。
    """
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

        r = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            if r.right > r.left and r.bottom > r.top:
                return (r.left, r.top, r.right, r.bottom)
    except Exception:  # noqa: BLE001
        pass
    try:
        w = root.winfo_screenwidth() if root is not None else 1920
        h = root.winfo_screenheight() if root is not None else 1080
    except Exception:  # noqa: BLE001
        w, h = 1920, 1080
    return (0, 0, w, h - 48)          # 粗估一下任务栏


class Tile(tk.Canvas):
    """一个来源的分格。kind: hero / large / mid / small"""

    def __init__(self, master, app, spec, kind, on_open, on_menu):
        super().__init__(master, highlightthickness=0, bg=PANEL, bd=0, cursor="hand2")
        self.app = app
        self.spec = spec
        self.kind = kind
        self.on_open = on_open
        self.on_menu = on_menu
        self._hover = False
        self._press_xy = None
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag_move)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Button-3>", self._menu)

    # 触摸屏友好：手指/鼠标在格子上拖动 = 滑动看板，只有“点一下”（几乎没移动）才打开来源
    def _board(self):
        return getattr(self.app, "board", None)

    def _press(self, event):
        self._press_xy = (event.x_root, event.y_root)
        b = self._board()
        if b is not None:
            b.pan_start(event)

    def _drag_move(self, event):
        b = self._board()
        if b is not None:
            b.pan_move(event)

    def _release(self, event):
        b = self._board()
        if b is not None:
            b.pan_end(event)
        if self._press_xy:
            dx = abs(event.x_root - self._press_xy[0])
            dy = abs(event.y_root - self._press_xy[1])
            if dx + dy < 12:                 # 算“点一下”
                self.on_open(self.spec["name"])
        self._press_xy = None

    def _enter(self, _e):
        self._hover = True
        self._draw()

    def _leave(self, _e):
        self._hover = False
        self._draw()

    def _menu(self, event):
        try:
            self.on_menu(self.spec, event.x_root, event.y_root)
        except Exception:  # noqa: BLE001
            pass

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < sx(30) or h < sx(30):
            return
        s = self.spec
        self.delete("all")
        is_new = s.get("new", 0) > 0
        key = s.get("key")
        base = CARD_PLAIN if self.kind in ("small", "mid") else CARD
        fill = mix(base, "#ffffff", 0.05) if self._hover else base
        outline = PURPLE if is_new else (BORDER_HOVER if self._hover else BORDER)
        round_rect(self, 1, 1, w - 1, h - 1, sx(18), fill=fill, outline=outline)
        if is_new and self.kind in ("hero", "large"):
            round_rect(self, sx(4), sx(4), w - sx(4), h - sx(4), sx(16),
                       fill=fill, outline="#4a3a80")
        if key and self.kind in ("hero", "large"):
            self.create_line(sx(13), sx(22), sx(13), h - sx(22), fill=PURPLE, width=sx(4),
                             capstyle="round")

        pad = sx(24 if (key and self.kind in ("hero", "large")) else 18)
        if self.kind == "small":
            pad = sx(14)
        nfont = {"hero": F(26, True), "large": F(21, True),
                 "mid": F(16, True), "small": F(14, True)}[self.kind]
        namefont = {"hero": F(10, True), "large": F(9.5, True),
                    "mid": F(9, True), "small": F(8.5, True)}[self.kind]

        chipwf = _font_of(F(7.5)).measure("★ 重点")
        limit = w - pad * 2 - (chipwf + sx(18) if key else 0)
        self.create_text(pad, sx(16), text=fit(self, s["name"], namefont, limit), anchor="nw",
                         fill=TXT, font=namefont)
        if key and self.kind in ("hero", "large"):
            cw = chipwf + sx(16)
            round_rect(self, w - pad - cw, sx(14), w - pad, sx(32), sx(9),
                       fill="#312a4d", outline="#5b48a8")
            self.create_text(w - pad - cw / 2, sx(23), text="★ 重点", fill="#c9b8ff", font=F(7.5))

        ny = sx(34)
        self.create_text(pad, ny, text=str(s["n"]), anchor="nw", fill=TXT, font=nfont)
        numw = _font_of(nfont).measure(str(s["n"]))
        uy = ny + int(_font_of(nfont).metrics("ascent") * 0.95)
        self.create_text(pad + numw + sx(8), uy, text="条", anchor="sw", fill=MUTE, font=F(8))
        if s.get("new"):
            px = pad + numw + sx(28)
            round_rect(self, px, uy - sx(21), px + sx(46), uy - sx(4), sx(8),
                       fill="#c2410c", outline="")
            self.create_text(px + sx(23), uy - sx(12), text="+%d 新" % s["new"],
                             fill="#ffffff", font=F(7.5, True))

        if self.kind in ("mid", "small"):
            self._draw_dense(w, h, pad)
            return

        self._spark(pad, h - sx(182), w - pad, h - sx(138))
        self.create_text(pad, h - sx(128), anchor="nw", fill=MUTE, font=F(8),
                         text="近 30 天 %d 条 · 命中关键词 %d · 权重 %.1f"
                              % (s["r30"], s["hits"], s["weight"]))
        y = h - sx(100)
        for d, t in s["titles"][:2]:
            self.create_text(pad, y, anchor="nw", fill="#c3cbdb", font=F(8.5),
                             text=fit(self, "%s  %s" % (fmt_date(d), t), F(8.5), w - pad * 2))
            y += sx(22)
        if self.kind == "hero":
            self.create_text(pad, h - sx(14), anchor="sw", fill=PURPLE, font=F(8.5),
                             text="点开查看全部 %d 条 →" % s["n"])

    def _draw_dense(self, w, h, pad):
        s = self.spec
        title = s["titles"][0][1] if s["titles"] else "（暂无）"
        if self.kind == "mid":
            self.create_text(pad, h - sx(40), anchor="nw", fill="#c3cbdb", font=F(8.5),
                             text=fit(self, title, F(8.5), w - pad * 2))
            self.create_text(pad, h - sx(20), anchor="nw", fill=MUTE, font=F(8),
                             text="近 30 天 %d 条 · 命中 %d" % (s["r30"], s["hits"]))
        else:
            self.create_text(pad, h - sx(44), anchor="nw", fill="#c3cbdb", font=F(8),
                             text=fit(self, title, F(8), w - pad * 2))
            self.create_text(pad, h - sx(24), anchor="nw", fill=MUTE, font=F(8),
                             text="最新 %s" % (s["latest"] or "—"))

    def _spark(self, x1, y1, x2, y2):
        series = self.spec.get("series") or [0]
        mx = max(series) or 1
        n = max(1, len(series) - 1)
        step = (x2 - x1) / n
        pts = []
        for i, v in enumerate(series):
            pts += [x1 + i * step, y2 - (v / mx) * (y2 - y1)]
        if len(pts) >= 4:
            self.create_polygon([x1, y2] + pts + [x2, y2], fill=SPARK_BG, outline="")
            self.create_line(pts, fill=PURPLE, width=sx(2), smooth=True, capstyle="round")


# ------------------------------------------------------------------ 看板

class BoardView(tk.Frame):
    COLS = 12

    def __init__(self, master, app, on_open, on_menu):
        super().__init__(master, bg=PANEL)
        self.app = app
        self.on_open = on_open
        self.on_menu = on_menu
        self.ROW_H = sx(150)
        self.GAP = sx(14)
        self._sb_frac = (0.0, 1.0)
        self._pan = None
        self._inertia_job = None
        self.canvas = tk.Canvas(self, bg=PANEL, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb = tk.Canvas(self, width=sx(18), bg=PANEL, highlightthickness=0, cursor="hand2")
        self.vsb.pack(side="right", fill="y")
        self.vsb._no_drag = True          # 滚动条不参与「拖动窗口」
        self.vsb.bind("<Button-1>", self._sb_drag)
        self.vsb.bind("<B1-Motion>", self._sb_drag)
        self.vsb.bind("<ButtonRelease-1>", self._sb_drag)
        self.inner = tk.Frame(self.canvas, bg=PANEL)
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_resize)
        # 触摸屏：手指按住拖动 = 滑动页面（Windows 会把单指拖动当鼠标左键拖动）
        self.canvas.bind("<Button-1>", self.pan_start)
        self.canvas.bind("<B1-Motion>", self.pan_move)
        self.canvas.bind("<ButtonRelease-1>", self.pan_end)

    def _on_resize(self, event):
        self.canvas.itemconfigure(self.win, width=event.width)

    # ---- 拖动内容（手指 / 鼠标按住拖）----
    def _content_h(self):
        box = self.canvas.bbox("all")
        return max(1, box[3] - box[1]) if box else max(1, self.canvas.winfo_height())

    def pan_start(self, event):
        self._stop_inertia()
        self._pan = {"y": event.y_root, "top": self.canvas.yview()[0],
                     "t": time.time(), "v": 0.0}

    def pan_move(self, event):
        if not self._pan:
            return
        dy = event.y_root - self._pan["y"]
        self.canvas.yview_moveto(self._pan["top"] - dy / self._content_h())
        now = time.time()
        dt = now - self._pan["t"]
        if dt > 0.001:
            self._pan["v"] = dy / dt          # 拖动速度 px/s（向下为正）
            self._pan["t"] = now
        self._draw_scrollbar()

    def pan_end(self, event=None):
        if not self._pan:
            return
        v = self._pan.get("v", 0.0)
        self._pan = None
        if abs(v) > 150:                       # 甩一下 → 惯性滑动
            self._inertia(v)

    def _inertia(self, v):
        v = max(-6000.0, min(6000.0, v))       # 加个上限，避免甩太快一下到底
        def step():
            nonlocal v
            v *= 0.90
            if abs(v) < 60:
                self._inertia_job = None
                return
            self.canvas.yview_moveto(self.canvas.yview()[0] - v * 0.03 / self._content_h())
            self._draw_scrollbar()
            self._inertia_job = self.after(30, step)

        step()

    def _stop_inertia(self):
        if self._inertia_job:
            try:
                self.after_cancel(self._inertia_job)
            except Exception:  # noqa: BLE001
                pass
            self._inertia_job = None

    def scroll(self, units):
        """units>0 向下。滚轮事件统一由 app 转发到这里（见 app._on_wheel）。"""
        if units:
            self._stop_inertia()
            self.canvas.yview_scroll(int(units), "units")
            self._draw_scrollbar()

    # ---- 键盘/兜底滚动：方向键、PgUp·PgDn、Home·End、空格 ----
    def scroll_lines(self, n):
        self._stop_inertia()
        self.canvas.yview_scroll(int(n), "units")
        self._draw_scrollbar()

    def scroll_pages(self, n):
        self._stop_inertia()
        self.canvas.yview_scroll(int(n), "pages")
        self._draw_scrollbar()

    def to_top(self):
        self._stop_inertia()
        self.canvas.yview_moveto(0.0)
        self._draw_scrollbar()

    def to_bottom(self):
        self._stop_inertia()
        self.canvas.yview_moveto(1.0)
        self._draw_scrollbar()

    def _wheel(self, event):
        d = event.delta or 0
        self.scroll(int(-d / 120) * 3 or (-1 if d > 0 else 1))

    # ---- 拖动右侧进度条也能滚（以前只能看不能拖）----
    def _sb_drag(self, event):
        h = self.vsb.winfo_height() or 1
        top, bottom = self._sb_frac
        span = max(0.03, bottom - top)
        frac = min(max(event.y / h - span / 2, 0.0), 1.0 - span)
        self.canvas.yview_moveto(frac)
        self._draw_scrollbar()

    def _draw_scrollbar(self):
        self.vsb.delete("all")
        h = self.vsb.winfo_height()
        top, bottom = self.canvas.yview()
        self._sb_frac = (top, bottom)
        if bottom - top >= 0.999 or h < 20:
            return
        round_rect(self.vsb, sx(5), top * h, sx(13), max(bottom * h, top * h + sx(34)),
                   sx(4), fill="#39415a", outline="")

    def render(self, specs, sort_mode="weight", tiers=(3, 4, 8)):
        for c in self.inner.winfo_children():
            c.destroy()
        if sort_mode == "count":
            specs = sorted(specs, key=lambda s: (-s["n"], -s["weight"]))
        elif sort_mode == "latest":
            specs = sorted(specs, key=lambda s: (s["latest"], -s["weight"]), reverse=True)
        spans = self._tiers(specs, tiers)
        placements = self._pack(spans)
        for i in range(self.COLS):
            self.inner.grid_columnconfigure(i, weight=1, uniform="col")
        rows = max([p[0] + p[2] for p in placements] + [1])
        for r in range(rows):
            self.inner.grid_rowconfigure(r, weight=0, minsize=self.ROW_H)
        for (spec, cs, rs), (r, c, _rs, _cs) in zip(spans, placements):
            kind = "hero" if (cs == 4 and rs == 2) else ("large" if rs == 2 else ("mid" if cs == 3 else "small"))
            t = Tile(self.inner, self.app, spec, kind, self.on_open, self.on_menu)
            t.grid(row=r, column=c, rowspan=rs, columnspan=cs, sticky="nsew",
                   padx=self.GAP // 2, pady=self.GAP // 2)
        self.canvas.yview_moveto(0)
        self.after(80, self._draw_scrollbar)

    def _tiers(self, specs, tiers=(3, 4, 8)):
        hero, large, mid = tiers
        out = []
        for i, s in enumerate(specs):
            if i < hero:
                out.append((s, 4, 2))
            elif i < hero + large:
                out.append((s, 3, 2))
            elif i < hero + large + mid:
                out.append((s, 3, 1))
            else:
                out.append((s, 2, 1))
        return out

    def _pack(self, spans):
        grid, placements = set(), []
        for _spec, cs, rs in spans:
            rr = 0
            while True:
                for cc in range(0, self.COLS - cs + 1):
                    cells = [(rr + dr, cc + dc) for dr in range(rs) for dc in range(cs)]
                    if all(x not in grid for x in cells):
                        for x in cells:
                            grid.add(x)
                        placements.append((rr, cc, rs, cs))
                        break
                else:
                    rr += 1
                    continue
                break
        return placements


# ------------------------------------------------------------------ 详情页

class DetailView(tk.Frame):
    def __init__(self, master, app, on_back, on_open):
        super().__init__(master, bg=PANEL)
        self.app = app
        self.on_back = on_back
        self.on_open = on_open
        self.ROW_H = sx(46)
        self.GAP = sx(9)
        self.items = []
        self._hover = -1
        self.site = ""
        self.only_unread = False
        self._sb_frac = (0.0, 1.0)
        self._pan = None
        self._inertia_job = None
        self._press_xy = None
        self._press_idx = -1

        bar = tk.Frame(self, bg=PANEL)
        bar.pack(fill="x", padx=sx(4), pady=(sx(2), sx(10)))
        Pill(bar, "← 返回看板", command=self.on_back, height=32).pack(side="left")
        self.title = tk.Label(bar, text="", bg=PANEL, fg=TXT, font=F(13, True))
        self.title.pack(side="left", padx=(sx(14), sx(10)))
        self.chips = tk.Frame(bar, bg=PANEL)
        self.chips.pack(side="left")
        self.btn_unread = Pill(bar, "只看未读", command=self._toggle_unread, height=32)
        self.btn_unread.pack(side="right")
        self.btn_open = Pill(bar, "打开最新", command=self._open_first, kind="primary", height=32)
        self.btn_open.pack(side="right", padx=(0, sx(8)))

        self.canvas = tk.Canvas(self, bg=PANEL, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb = tk.Canvas(self, width=sx(18), bg=PANEL, highlightthickness=0, cursor="hand2")
        self.vsb.pack(side="right", fill="y")
        self.vsb._no_drag = True          # 滚动条不参与「拖动窗口」
        self.vsb.bind("<Button-1>", self._sb_drag)
        self.vsb.bind("<B1-Motion>", self._sb_drag)
        self.vsb.bind("<ButtonRelease-1>", self._sb_drag)
        self.canvas.bind("<Configure>", lambda e: self._draw())
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(-1))
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self.pan_move)
        self.canvas.bind("<ButtonRelease-1>", self.pan_end)
        self.canvas.bind("<Button-3>", self._menu)

    def show(self, site, rows, weight, n_all):
        self.site = site
        self.items = rows
        self.title.configure(text=site)
        for c in self.chips.winfo_children():
            c.destroy()
        for txt in ("★ 重点来源" if weight >= 18 else "常规来源",
                    "权重 %.1f" % weight, "共 %d 条" % n_all):
            Pill(self.chips, txt, kind="off", height=24, font=F(8)).pack(side="left", padx=(0, sx(6)))
        self.canvas.yview_moveto(0)
        self._draw()

    def _toggle_unread(self):
        self.only_unread = not self.only_unread
        self.btn_unread.cset_kind("on" if self.only_unread else "ghost")
        self.app.filter_detail(self)

    def _open_first(self):
        if self.items:
            self.on_open(self.items[0])

    def _row_at(self, y):
        idx = int((self.canvas.canvasy(y) - sx(6)) // (self.ROW_H + self.GAP))
        return idx if 0 <= idx < len(self.items) else -1

    def _motion(self, event):
        self._set_hover(self._row_at(event.y))

    def _set_hover(self, idx):
        if idx != self._hover:
            self._hover = idx
            self._draw()

    def _click(self, event):
        self._set_hover(self._row_at(event.y))

    # ---- 手指/鼠标按住拖动 = 滑动列表；轻点一下 = 打开这条 ----
    def _press(self, event):
        self._press_xy = (event.x_root, event.y_root)
        self._press_idx = self._row_at(event.y)
        self._set_hover(self._press_idx)
        self.pan_start(event)

    def _content_h(self):
        box = self.canvas.bbox("all")
        return max(1, box[3] - box[1]) if box else max(1, self.canvas.winfo_height())

    def pan_start(self, event):
        self._stop_inertia()
        self._pan = {"y": event.y_root, "top": self.canvas.yview()[0],
                     "t": time.time(), "v": 0.0}

    def pan_move(self, event):
        if not self._pan:
            return
        dy = event.y_root - self._pan["y"]
        self.canvas.yview_moveto(self._pan["top"] - dy / self._content_h())
        now = time.time()
        dt = now - self._pan["t"]
        if dt > 0.001:
            self._pan["v"] = dy / dt
            self._pan["t"] = now
        self._draw_scrollbar()

    def pan_end(self, event=None):
        moved = 0
        if self._press_xy and event is not None:
            moved = (abs(event.x_root - self._press_xy[0])
                     + abs(event.y_root - self._press_xy[1]))
        idx = self._press_idx
        self._press_xy = None
        self._press_idx = -1
        if not self._pan:
            return
        v = self._pan.get("v", 0.0)
        self._pan = None
        if abs(v) > 150:
            self._inertia(v)
        if moved < 12 and 0 <= idx < len(self.items):    # 轻点一下 → 打开这条
            self.on_open(self.items[idx])

    def _inertia(self, v):
        v = max(-6000.0, min(6000.0, v))

        def step():
            nonlocal v
            v *= 0.90
            if abs(v) < 60:
                self._inertia_job = None
                return
            self.canvas.yview_moveto(self.canvas.yview()[0] - v * 0.03 / self._content_h())
            self._draw_scrollbar()
            self._inertia_job = self.after(30, step)

        step()

    def _stop_inertia(self):
        if self._inertia_job:
            try:
                self.after_cancel(self._inertia_job)
            except Exception:  # noqa: BLE001
                pass
            self._inertia_job = None

    def _menu(self, event):
        idx = self._row_at(event.y)
        if idx >= 0:
            self.app.item_menu(self.items[idx], event.x_root, event.y_root)

    def scroll(self, units):
        """units>0 向下。滚轮事件统一由 app 转发到这里。"""
        if units:
            self._stop_inertia()
            self.canvas.yview_scroll(int(units), "units")
            self._draw_scrollbar()

    # ---- 键盘/兜底滚动（同看板）----
    def scroll_lines(self, n):
        self._stop_inertia()
        self.canvas.yview_scroll(int(n), "units")
        self._draw_scrollbar()

    def scroll_pages(self, n):
        self._stop_inertia()
        self.canvas.yview_scroll(int(n), "pages")
        self._draw_scrollbar()

    def to_top(self):
        self._stop_inertia()
        self.canvas.yview_moveto(0.0)
        self._draw_scrollbar()

    def to_bottom(self):
        self._stop_inertia()
        self.canvas.yview_moveto(1.0)
        self._draw_scrollbar()

    def _wheel(self, event):
        d = event.delta or 0
        self.scroll(int(-d / 120) * 3 or (-1 if d > 0 else 1))

    # ---- 拖动右侧进度条也能滚 ----
    def _sb_drag(self, event):
        h = self.vsb.winfo_height() or 1
        top, bottom = self._sb_frac
        span = max(0.03, bottom - top)
        frac = min(max(event.y / h - span / 2, 0.0), 1.0 - span)
        self.canvas.yview_moveto(frac)
        self._draw_scrollbar()

    def _draw_scrollbar(self):
        self.vsb.delete("all")
        h = self.vsb.winfo_height()
        top, bottom = self.canvas.yview()
        self._sb_frac = (top, bottom)
        if bottom - top >= 0.999 or h < 20:
            return
        round_rect(self.vsb, sx(5), top * h, sx(13), max(bottom * h, top * h + sx(34)),
                   sx(4), fill="#39415a", outline="")

    def _draw(self):
        cv = self.canvas
        cv.delete("all")
        w = cv.winfo_width() or sx(900)
        pad = sx(4)
        y = sx(6)
        for i, it in enumerate(self.items):
            hot = (i == self._hover)
            new = self.app.is_new(it)
            fill = mix(CARD, "#ffffff", 0.06) if hot else CARD
            outline = BORDER_HOVER if hot else (PURPLE if new else BORDER)
            round_rect(cv, pad, y, w - pad, y + self.ROW_H, sx(13), fill=fill, outline=outline)
            cy = y + self.ROW_H / 2
            cv.create_text(pad + sx(16), cy, text=fmt_date(it.get("date")), anchor="w",
                           fill=MUTE, font=F(8.5))
            x = w - pad - sx(16)
            if it.get("hits"):
                txt = "★ " + "、".join(it["hits"][:2])
                cv.create_text(x, cy, text=txt, anchor="e", fill=GOLD, font=F(8))
                x -= _font_of(F(8)).measure(txt) + sx(14)
            if new:
                cv.create_text(x, cy, text="新", anchor="e", fill=PURPLE, font=F(8, True))
                x -= sx(24)
            cv.create_text(pad + sx(92), cy,
                           text=fit(self, it.get("title") or "", F(9.5), x - pad - sx(100)),
                           anchor="w", fill=TXT, font=F(9.5))
            y += self.ROW_H + self.GAP
        cv.configure(scrollregion=(0, 0, w, max(y, sx(10))))
        self.after(60, self._draw_scrollbar)