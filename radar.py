#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
川大校园通知雷达 (radar.py)
===========================
盯住川大教务处 / 学工部 / 学校主站 / 你所在学院的通知公告列表页，
抓回条目 → 和上次的记录比对 → 只报告「新增」的通知，并按关键词高亮。

为什么要这个：保研、奖学金、竞赛报名、补退选这类通知，错过一天就是错过一年。

特点
  * 纯标准库，零依赖（Python 3.8+，Windows/macOS/Linux 都能跑）
  * 站点、关键词全部写在 sites.json 里，加站点不用改代码
  * 首次运行自动「建基线」（把现有通知全部记为已读，不刷屏）
  * 输出 Markdown + HTML 摘要，控制台只看新增
  * `--json` 输出机器可读结果，方便接定时任务推送

常用命令
  python radar.py check                 # 检查新增（日常就这一条）
  python radar.py check --json          # 给定时任务/机器人用
  python radar.py check --all           # 不看状态，列出当前所有通知
  python radar.py list --limit 8        # 只看最近 8 条，不写状态
  python radar.py test <url>            # 验证某个新站点能不能抓
  python radar.py sites                 # 列出所有站点与开关状态
  python radar.py sites --add 名称=URL   # 添加站点
  python radar.py sites --disable 名称   # 停用站点
  python radar.py keywords              # 查看当前关键词
  python radar.py keywords --add 关键词1,关键词2
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from html import unescape as _unescape
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "sites.json"
STATE_PATH = BASE_DIR / "state.json"
DIGEST_DIR = BASE_DIR / "digest"

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
# 博达/正方等 CMS 常见的正文链接形态。以前只认 info/\d+/\d+.htm，导致
# content.jsp?...wbnewsid=N 这类条目被整条丢弃（学工部、公共管理学院都中过招）。
DEFAULT_HREF_PATTERN = (
    r"(?:info/\d+/\d+\.htm"
    r"|/\d{4,}\.htm"
    r"|/content/\d+"
    r"|content\.jsp\?[^\"']*wbnewsid=\d+"
    r"|shownews\.jsp\?[^\"']*(?:newsid|id)=\d+"
    r"|/news/\d+\.htm)"
)

# ------------------------------------------------------------------ 输出

def dwidth(s: str) -> int:
    total = 0
    for ch in str(s):
        o = ord(ch)
        wide = (o <= 0x115F or 0x2E80 <= o <= 0xA4CF or 0xAC00 <= o <= 0xD7A3
                or 0xF900 <= o <= 0xFAFF or 0xFE30 <= o <= 0xFE4F
                or 0xFF00 <= o <= 0xFF60 or 0xFFE0 <= o <= 0xFFE6 or 0x20000 <= o <= 0x3FFFD)
        total += 2 if (wide and o >= 0x1100) else 1
    return total


def pad(s: str, width: int) -> str:
    s = str(s)
    return s + " " * max(0, width - dwidth(s))


def title(text: str) -> None:
    print()
    print("== " + text + " " + "=" * max(0, 62 - dwidth(text)))


def say(text: str) -> None:
    print("  " + text)


def warn(text: str) -> None:
    print("  [!] " + text)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------ 抓取

def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_get(url: str, timeout: int = 25, retry: int = 2) -> str:
    last = None
    for attempt in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
                raw = resp.read()
            for enc in ("utf-8", "gb18030"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"{type(last).__name__}: {last}")


# ------------------------------------------------------------------ 解析

def _strip_tags(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    # 用标准库统一解码所有 HTML 实体（&quot; &#34; &amp; &mdash; …），
    # 之前只手工处理了 4 个，导致标题里残留 &quot; 之类的乱码。
    text = _unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_date(seg: str) -> str:
    """把各种写法（2026-09-14 / 09/20+2026 / 21+2026-09 / 03+2026.07）统一成 YYYY-MM-DD。"""
    txt = _strip_tags(seg)
    m = re.search(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", txt)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y}-{mo:02d}-{d:02d}"
    md = re.search(r"(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})(?!\d)", txt)
    y = re.search(r"(20\d{2})", txt)
    if md and y:
        mo, d = int(md.group(1)), int(md.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y.group(1)}-{mo:02d}-{d:02d}"
    ym = re.search(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})(?!\d)", txt)
    if ym:
        y2, mo = int(ym.group(1)), int(ym.group(2))
        if not 1 <= mo <= 12:
            return ""
        tokens = [int(t) for t in re.findall(r"(?<!\d)(\d{1,2})(?!\d)", txt)]
        candidates = [t for t in tokens if t != mo and t != y2 and 1 <= t <= 31]
        if not candidates:
            return f"{y2}-{mo:02d}"        # 只知道年月，不硬造日
        return f"{y2}-{mo:02d}-{candidates[0]:02d}"
    return ""


def _extract_date_region(block: str) -> str:
    """优先取日期容器（class 里带 date/time 的 div），避免标题里的 '2026-2027学年' 之类数字干扰。

    兼容的模板：
      * <div class="date"><p>10/09</p><span>2026</span></div>   （教务处）
      * <div class="date fl"><p>09</p><span>2026-10</span></div>  （公共管理学院）
      * <div class="time"><h3>08</h3><h6>2026.10</h6></div>   （建筑与环境学院）
      * <h6>2026-09-29</h6>                                    （华西基础医学与法医学院）
    """
    m = re.search(r"<div[^>]*class=\"[^\"]*(?:date|time)[^\"]*\"[^>]*>(.*?)</div>", block, re.S | re.I)
    if m:
        return m.group(1)
    m = re.search(r"<span[^>]*>\s*(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2})\s*</span>", block)
    if m:
        return m.group(1)
    m = re.search(r"<h[1-6][^>]*>\s*(20\d{2}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2})\s*</h[1-6]>", block)
    if m:
        return m.group(1)
    # 兜底：在条目开头找完整日期（限前 400 字符，避免正文里的日期干扰）
    head = block[:400]
    m = re.search(r"20\d{2}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}", head)
    if m:
        return m.group(0)
    m = re.search(r"<(?:span|p|em|i|font)[^>]*>\s*(20\d{2}\s*[-/.年]\s*\d{1,2})\s*<", head)
    if m:
        return m.group(1)
    return ""


def parse_items(html: str, base_url: str, href_pattern: str = DEFAULT_HREF_PATTERN) -> list:
    """从列表页 HTML 里抽条目。兼容川大在用的几种模板（博达 CMS 的 li 结构）。"""
    items, seen = [], set()
    blocks = re.split(r"(?i)<li\b", html)
    # 先按 </li> 截断，得到每个 <li> 的自身内容；保留索引，便于向后看兄弟节点里的日期
    blocks = [b[: b.lower().find("</li>")] if b.lower().find("</li>") > 0 else b for b in blocks]
    for i in range(1, len(blocks)):
        block = blocks[i]
        link = re.search(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", block, re.S | re.I)
        if not link:
            continue
        href = link.group(1).strip()
        if not re.search(href_pattern, href, re.I):
            continue
        url = urllib.parse.urljoin(base_url, href)

        # 标题：title 属性 > h3 > p.title > 链接内文字
        title_text = ""
        attr = re.search(r"title=[\"']([^\"']+)[\"']", link.group(0), re.I)
        if attr:
            title_text = attr.group(1)
        if not title_text:
            h3 = re.search(r"<h3[^>]*>(.*?)</h3>", block, re.S | re.I)
            if h3:
                title_text = h3.group(1)
        if not title_text:
            pt = re.search(r"<p[^>]*class=\"[^\"]*title[^\"]*\"[^>]*>(.*?)</p>", block, re.S | re.I)
            if pt:
                title_text = pt.group(1)
        if not title_text:
            dt = re.search(r"<div[^>]*class=\"[^\"]*(?:text|con|news-box)[^\"]*\"[^>]*>(.*?)</div>",
                           block, re.S | re.I)
            if dt:
                title_text = dt.group(1)
        if not title_text:
            title_text = link.group(2)
        title_text = _strip_tags(title_text)
        # 去掉开头/结尾混进来的日期与「查看详情」
        title_text = re.sub(
            r"^\s*(?:\d{1,2}\s*/\s*\d{1,2}\s*20\d{2}|\d{1,2}\s+20\d{2}\s*[-/.]\s*\d{1,2}"
            r"|20\d{2}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2}|\d{1,2}\s*日?\s*20\d{2}\s*年?\s*\d{1,2}\s*月?)\s*",
            "", title_text)
        title_text = re.sub(r"\s*(查看详情|更多|详情)\s*>?\s*$", "", title_text).strip(" >")
        if not title_text or len(title_text) < 4:
            continue

        # 摘要（学工部/主站的列表里带正文前几句）
        summary = ""
        for pat in (r"<h4[^>]*>(.*?)</h4>", r"<p[^>]*class=\"[^\"]*(?:content|summary)[^\"]*\"[^>]*>(.*?)</p>"):
            mm = re.search(pat, block, re.S | re.I)
            if mm:
                summary = _strip_tags(mm.group(1))
                break

        date = normalize_date(_extract_date_region(block))
        if not date and i + 1 < len(blocks):
            nxt = blocks[i + 1]
            # 兄弟节点是纯日期块（如 <li class="time">[2026-10-01]</li>）时借用它的日期
            if re.search(r'(?i)class="[^"]*(?:time|date)[^"]*"', nxt) and not re.search(href_pattern, nxt, re.I):
                date = normalize_date(_extract_date_region(nxt))
        if url in seen:
            continue
        seen.add(url)
        items.append({"url": url, "title": title_text, "date": date, "summary": summary[:200]})
    return items


def matched_keywords(text: str, keywords: list) -> list:
    low = text.lower()
    return [k for k in keywords if k and k.lower() in low]


# ------------------------------------------------------------------ 标题/日期补全

_TRUNC_RE = re.compile(r"(?:\.\.\.|…)\s*$")


def is_truncated_title(t: str) -> bool:
    """列表页喜欢把长标题截断成 '……关于2027年春台湾...'，以此判断是否需要去详情页补全。"""
    return bool(_TRUNC_RE.search(t or ""))


def strip_truncation(t: str) -> str:
    """去掉结尾的省略号，便于判断详情页标题是否确实是它的完整版。"""
    return _TRUNC_RE.sub("", t or "").strip()


# 详情页 <title> 会带站点后缀（如 “...的通知-四川大学教务处”），保守地去掉它：
# 要求以单个分符开头、后面是短站点名且以“教务处/学院/…”这类词结尾，
# 避免误伤标题自身的 “——川大建院30周年” 之类。
_SITE_SUFFIX_RE = re.compile(
    r"\s*[-|_]\s*(?:四川大学)?[\u4e00-\u9fa5A-Za-z]{0,10}"
    r"(?:教务处|学生工作部|研究生院|学院|委员会|办公室|中心|大学|部)\s*$"
)


def clean_detail_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    return _SITE_SUFFIX_RE.sub("", t).strip()


def fetch_detail_meta(url: str, timeout: int = 15) -> dict:
    """抓正文页，提取完整标题与发布日期。川大各站详情页都带 <META Name="PubDate">。"""
    try:
        page = http_get(url, timeout=timeout, retry=1)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    # 发布日期：meta PubDate / publishdate / article:published_time
    for tag in re.findall(r"<meta[^>]*>", page, re.I):
        if re.search(r'name="(?:PubDate|publishdate|publishDate)"', tag, re.I) or \
           re.search(r'property="article:published_time"', tag, re.I):
            c = re.search(r'content="([^"]+)"', tag, re.I)
            if c:
                d = normalize_date(c.group(1))
                if d:
                    out["date"] = d
                    break
    if not out.get("date"):
        m = re.search(r"发布时间[：:]?\s*(20\d{2}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2})", page)
        if m:
            d = normalize_date(m.group(1))
            if d:
                out["date"] = d
    # 完整标题
    m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    if m:
        t = clean_detail_title(_strip_tags(m.group(1)))
        if t:
            out["title"] = t
    return out


def find_more_pages(page_html: str, base_url: str, count: int, timeout: int = 15) -> list:
    """从列表页的翻页链接里找出同栏目的历史页（博达 CMS: <栏目目录>/<n>.htm）。

    川大这些站的页码是「数字越大越新」，所以取数字最大的 count 个作为最近的历史页。
    """
    path = urllib.parse.urlsplit(base_url).path
    fname = path.rsplit("/", 1)[-1]
    stem = fname[:-4] if fname.lower().endswith(".htm") else fname
    found = {}
    pat = re.compile(r"(?:\.\./)*" + re.escape(stem) + r"/(\d+)\.htm$", re.I)
    for href in re.findall(r"href=[\"']([^\"']+)[\"']", page_html):
        href = href.strip()
        m = pat.match(href)
        if m:
            found[int(m.group(1))] = urllib.parse.urljoin(base_url, href)
    if not found:
        return []
    nums = sorted(found, reverse=True)[:max(0, count)]
    return [found[n] for n in nums]


# ------------------------------------------------------------------ 配置 / 状态

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"找不到配置文件 {CONFIG_PATH}")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg.setdefault("keywords", [])
    cfg.setdefault("hot_keywords", [])
    cfg.setdefault("sites", [])
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            warn("state.json 解析失败，按空状态处理（会重新建基线）")
    return {"version": 1, "sites": {}}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def site_state(state: dict, url: str) -> dict:
    return state.setdefault("sites", {}).setdefault(url, {"seen": {}, "last_check": ""})


# ------------------------------------------------------------------ 抓取整站

def fetch_site(site: dict, keywords: list, hot: list) -> tuple:
    html = http_get(site["url"])
    items = parse_items(html, site["url"], site.get("href_pattern", DEFAULT_HREF_PATTERN))
    flt = [k for k in site.get("keywords_filter", []) if k]
    if flt:
        items = [i for i in items if matched_keywords(i["title"] + i["summary"], flt)]
    for it in items:
        it["site"] = site["name"]
        hits = matched_keywords(it["title"] + " " + it["summary"], keywords)
        it["hits"] = hits
        it["hot"] = [k for k in hits if k in hot]
    return items, ""


# ------------------------------------------------------------------ 报告

def render_console(new_items: list, errors: list, first_run_sites: list, args) -> None:
    if first_run_sites:
        title("首次运行：已建立基线")
        for name in first_run_sites:
            say(f"{name} —— 现有通知已记为「已读」，以后只提醒新增。")
    if errors:
        title("抓取失败")
        for name, err in errors:
            warn(f"{name}：{err}")
    if not new_items:
        title("没有新通知")
        say(f"检查时间 {now_str()}")
        return
    title(f"发现 {len(new_items)} 条新通知")
    for it in new_items:
        flag = "★" if it["hot"] else " "
        date = it["date"] or "日期未知"
        print(f" {flag} [{it['site']}] {date}")
        print(f"   {it['title']}")
        if it.get("hot"):
            print(f"   命中关键词：{'、'.join(it['hot'])}")
        elif it.get("hits"):
            print(f"   关键词：{'、'.join(it['hits'][:6])}")
        print(f"   {it['url']}")
        print()


def render_markdown(new_items: list, errors: list, cfg: dict) -> str:
    L = ["# 川大校园通知雷达 · 摘要", "", f"- 检查时间：{now_str()}",
         f"- 新增通知：{len(new_items)} 条", ""]
    hot_items = [i for i in new_items if i.get("hot")]
    if hot_items:
        L += ["## ★ 重点（命中高优待办关键词）", "", "| 日期 | 来源 | 标题 |", "| --- | --- | --- |"]
        for it in hot_items:
            L.append(f"| {it['date'] or '-'} | {it['site']} | [{it['title']}]({it['url']}) |")
        L.append("")
    L += ["## 全部新增", "", "| 日期 | 来源 | 关键词 | 标题 |", "| --- | --- | --- | --- |"]
    for it in new_items:
        kw = "、".join(it.get("hits", [])[:4])
        L.append(f"| {it['date'] or '-'} | {it['site']} | {kw} | [{it['title']}]({it['url']}) |")
    if not new_items:
        L.append("| - | - | - | 本次没有新通知 |")
    if errors:
        L += ["", "## 抓取失败", ""]
        L += [f"- {name}：{err}" for name, err in errors]
    L += ["", "---", "", f"关键词：{'、'.join(cfg.get('keywords', [])[:30])} …", ""]
    return "\n".join(L)


def render_html(md_items: list, errors: list, cfg: dict, new_items: list) -> str:
    rows = []
    for it in new_items:
        cls = "hot" if it.get("hot") else ""
        kw = "、".join(it.get("hits", [])[:4])
        rows.append(
            f'<tr class="{cls}"><td class="d">{it["date"] or "-"}</td>'
            f'<td class="s">{it["site"]}</td><td class="k">{kw}</td>'
            f'<td><a href="{it["url"]}" target="_blank">{it["title"]}</a></td></tr>')
    err_html = "".join(f"<li>{n}：{e}</li>" for n, e in errors)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>川大校园通知雷达 {now_str()}</title>
<style>
 body{{font-family:"Microsoft YaHei",system-ui,sans-serif;max-width:1080px;margin:24px auto;padding:0 16px;color:#222}}
 h1{{font-size:22px}} .meta{{color:#666;font-size:13px;margin-bottom:16px}}
 table{{width:100%;border-collapse:collapse;font-size:14px}}
 th,td{{border-bottom:1px solid #e6e6e6;padding:8px 10px;text-align:left;vertical-align:top}}
 th{{background:#fafafa;font-weight:600}} a{{color:#1655b3;text-decoration:none}} a:hover{{text-decoration:underline}}
 tr.hot{{background:#fff8e6}} td.d{{white-space:nowrap;color:#555;width:96px}} td.s{{color:#777;width:200px}}
 td.k{{color:#c05600;width:150px;font-size:12px}}
 .empty{{color:#888}} .err{{color:#b00;font-size:13px}}
</style></head><body>
<h1>川大校园通知雷达</h1>
<div class="meta">检查时间 {now_str()} · 新增 {len(new_items)} 条 · 重点 {sum(1 for i in new_items if i.get('hot'))} 条</div>
<table><thead><tr><th>日期</th><th>来源</th><th>关键词</th><th>标题</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="4" class="empty">本次没有新通知</td></tr>'}</tbody></table>
{f'<h3>抓取失败</h3><ul class="err">{err_html}</ul>' if errors else ''}
</body></html>"""


def write_digest(new_items: list, errors: list, cfg: dict) -> Path:
    DIGEST_DIR.mkdir(exist_ok=True)
    md = render_markdown(new_items, errors, cfg)
    html = render_html(None, errors, cfg, new_items)
    day = datetime.now().strftime("%Y-%m-%d")
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    (DIGEST_DIR / f"{stamp}.md").write_text(md, encoding="utf-8-sig")
    (DIGEST_DIR / f"{stamp}.html").write_text(html, encoding="utf-8")
    for name in ("最新", "latest"):
        (DIGEST_DIR / f"{name}.md").write_text(md, encoding="utf-8-sig")
        (DIGEST_DIR / f"{name}.html").write_text(html, encoding="utf-8")
    _ = day
    return DIGEST_DIR / "最新.html"


# ------------------------------------------------------------------ 命令

def cmd_check(args):
    cfg = load_config()
    state = load_state()
    keywords, hot = cfg["keywords"], cfg["hot_keywords"]
    sites = [s for s in cfg["sites"] if s.get("enabled", True)]
    if args.site:
        sites = [s for s in sites if args.site in s["name"]]
        if not sites:
            raise SystemExit(f"没有匹配「{args.site}」的已启用站点，用 `python radar.py sites` 看看站点名。")

    new_items, errors, baseline = [], [], []
    for site in sites:
        try:
            items, _ = fetch_site(site, keywords, hot)
        except Exception as exc:  # noqa: BLE001
            errors.append((site["name"], str(exc)))
            continue
        st = site_state(state, site["url"])
        seen = st.get("seen", {})
        is_first = not st.get("inited")
        pool = items
        if args.days:
            cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
            dated = [i for i in pool if i["date"]]
            undated = [i for i in pool if not i["date"]]
            pool = [i for i in dated if i["date"] >= cutoff] + undated
        if is_first and not args.all:
            if pool:
                baseline.append(site["name"])
            fresh = []
        elif args.all:
            fresh = pool
        else:
            fresh = [i for i in pool if i["url"] not in seen]
        if args.keywords_only:
            fresh = [i for i in fresh if i.get("hits")]
        for i in fresh:
            i["site"] = site["name"]
            hits = matched_keywords(i["title"] + " " + i["summary"], keywords)
            i["hits"] = hits
            i["hot"] = [k for k in hits if k in hot]
        fresh.sort(key=lambda x: (x["date"] or "", x["title"]), reverse=True)
        new_items.extend(fresh)
        # 更新状态
        for i in pool:
            seen[i["url"]] = {"title": i["title"], "date": i["date"]}
        if len(seen) > 1500:  # 控制体积
            keep = sorted(seen.items(), key=lambda kv: (kv[1].get("date") or ""), reverse=True)[:1000]
            st["seen"] = dict(keep)
        st["seen"] = seen
        st["inited"] = True
        st["last_check"] = now_str()
        st["last_error"] = ""
    state["last_run"] = now_str()
    save_state(state)

    if args.json:
        print(json.dumps({
            "checked_at": now_str(),
            "new_count": len(new_items),
            "hot_count": sum(1 for i in new_items if i.get("hot")),
            "baseline_sites": baseline,
            "errors": [{"site": n, "error": e} for n, e in errors],
            "items": [{"site": i["site"], "date": i["date"], "title": i["title"],
                       "url": i["url"], "hot": bool(i.get("hot")), "keywords": i.get("hits", []),
                       "summary": i.get("summary", "")} for i in new_items],
        }, ensure_ascii=False, indent=2))
        return

    render_console(new_items, errors, baseline, args)
    path = write_digest(new_items, errors, cfg)
    if new_items or errors or baseline:
        title("产出")
        say(f"摘要文件：{path}")


def cmd_list(args):
    cfg = load_config()
    keywords, hot = cfg["keywords"], cfg["hot_keywords"]
    sites = [s for s in cfg["sites"] if s.get("enabled", True)]
    if args.site:
        sites = [s for s in sites if args.site in s["name"]]
    for site in sites:
        title(f"{site['name']}")
        try:
            items, _ = fetch_site(site, keywords, hot)
        except Exception as exc:  # noqa: BLE001
            warn(f"抓取失败：{exc}")
            continue
        items.sort(key=lambda x: (x["date"] or ""), reverse=True)
        say(f"共解析到 {len(items)} 条，显示最近 {min(args.limit, len(items))} 条：")
        for it in items[: args.limit]:
            hit = "★ " if it["hot"] else "  "
            print(f"  {hit}{it['date'] or '??????????'}  {it['title'][:50]}")
            print(f"      {it['url']}")
            if it.get("hits"):
                print(f"      关键词：{'、'.join(it['hits'][:6])}")


def cmd_test(args):
    url = args.url
    if not url.startswith("http"):
        raise SystemExit("请给出完整网址，例如 python radar.py test https://xxx.scu.edu.cn/tzgg.htm")
    title("解析测试")
    say(f"目标：{url}")
    try:
        html = http_get(url)
    except Exception as exc:  # noqa: BLE001
        warn(f"抓取失败：{exc}")
        warn("如果是 412 / 403，说明站点有反爬，需要浏览器打开，脚本抓不了。")
        return
    say(f"页面大小：{len(html)} 字节")
    items = parse_items(html, url, args.pattern or DEFAULT_HREF_PATTERN)
    if not items:
        warn("没有解析到条目。可以试试 --pattern 自定义链接正则（默认匹配 info/数字/数字.htm）。")
        return
    say(f"解析到 {len(items)} 条：")
    for it in items[:12]:
        print(f"   {it['date'] or '??????????'}  {it['title'][:52]}")
        print(f"       {it['url']}")
    say("确认无误后，把这条加进 sites.json（或用 `python radar.py sites --add 名称=网址`）。")


def cmd_sites(args):
    cfg = load_config()
    changed = False
    if args.add:
        if "=" not in args.add:
            raise SystemExit("格式：--add 站点名称=网址")
        name, url = args.add.split("=", 1)
        name, url = name.strip(), url.strip()
        if any(s["url"] == url for s in cfg["sites"]):
            warn(f"该网址已存在：{url}")
        else:
            cfg["sites"].append({"name": name, "url": url, "enabled": True, "note": "自建"})
            changed = True
            say(f"已添加：{name} → {url}")
    if args.enable or args.disable:
        target = (args.enable or args.disable).strip()
        for s in cfg["sites"]:
            if target in s["name"] or target == s["url"]:
                s["enabled"] = bool(args.enable)
                changed = True
                say(f"{'启用' if args.enable else '停用'}：{s['name']}")
        if not changed:
            warn(f"没找到站点「{target}」")
    if args.remove:
        before = len(cfg["sites"])
        cfg["sites"] = [s for s in cfg["sites"] if args.remove not in s["name"] and args.remove != s["url"]]
        changed = len(cfg["sites"]) != before
        say(f"已删除 {before - len(cfg['sites'])} 个站点")
    if changed:
        save_config(cfg)
        print()
    title("站点列表")
    for i, s in enumerate(cfg["sites"], 1):
        flag = "[开]" if s.get("enabled", True) else "[关]"
        print(f"  {i:>2}. {flag} {s['name']}")
        print(f"       {s['url']}")
        if s.get("note"):
            print(f"       备注：{s['note']}")
    print()
    say("新增站点前建议先验证：python radar.py test <网址>")


def cmd_keywords(args):
    cfg = load_config()
    if args.add:
        new = [k.strip() for k in re.split(r"[,，\s]+", args.add) if k.strip()]
        for k in new:
            if k not in cfg["keywords"]:
                cfg["keywords"].append(k)
        save_config(cfg)
        say(f"已添加关键词：{'、'.join(new)}")
    if args.hot:
        new = [k.strip() for k in re.split(r"[,，\s]+", args.hot) if k.strip()]
        for k in new:
            if k not in cfg["hot_keywords"]:
                cfg["hot_keywords"].append(k)
        save_config(cfg)
        say(f"已加入重点（★）：{'、'.join(new)}")
    title("当前关键词")
    say("普通（命中即列出）：")
    print("   " + "、".join(cfg["keywords"]))
    say("重点（命中打 ★ 并在摘要里单列）：")
    print("   " + "、".join(cfg["hot_keywords"]))
    print()
    say("增删直接改 sites.json，或用 `python radar.py keywords --add 新词1,新词2`。")


# ------------------------------------------------------------------ CLI

def build_parser():
    p = argparse.ArgumentParser(
        prog="radar.py",
        description="川大校园通知雷达：盯住教务处/学工部/主站/学院的通知，只提醒新增",
        epilog="日常用法：python radar.py check",
    )
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("check", help="检查新增通知并生成摘要")
    c.add_argument("--site", help="只检查名字包含该关键字的站点")
    c.add_argument("--days", type=int, default=45, help="只看最近 N 天的通知（默认 45，0 = 不限）")
    c.add_argument("--all", action="store_true", help="忽略已读状态，列出当前全部通知")
    c.add_argument("--keywords-only", action="store_true", help="只报告命中关键词的通知")
    c.add_argument("--json", action="store_true", help="输出 JSON（给定时任务/机器人用）")
    c.set_defaults(func=cmd_check)

    l = sub.add_parser("list", help="列出最近通知（不写状态）")
    l.add_argument("--site", help="只看某个站点")
    l.add_argument("--limit", type=int, default=8, help="每个站点显示条数")
    l.set_defaults(func=cmd_list)

    t = sub.add_parser("test", help="测试某个网址能不能抓（加站点前的验证）")
    t.add_argument("url")
    t.add_argument("--pattern", help="自定义条目链接正则")
    t.set_defaults(func=cmd_test)

    s = sub.add_parser("sites", help="查看/增删站点")
    s.add_argument("--add", help="添加站点：名称=网址")
    s.add_argument("--enable", help="启用站点（名称关键字）")
    s.add_argument("--disable", help="停用站点（名称关键字）")
    s.add_argument("--remove", help="删除站点（名称关键字）")
    s.set_defaults(func=cmd_sites)

    k = sub.add_parser("keywords", help="查看/添加关键词")
    k.add_argument("--add", help="添加普通关键词，逗号分隔")
    k.add_argument("--hot", help="添加重点关键词（★），逗号分隔")
    k.set_defaults(func=cmd_keywords)
    return p


def main(argv=None):
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        print("\n提示：日常就一句 —— python radar.py check")
        return
    args.func(args)


if __name__ == "__main__":
    main()