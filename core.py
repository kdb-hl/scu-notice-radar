# -*- coding: utf-8 -*-
"""
core.py —— 川大通知雷达的公共数据层（GUI 与命令行共用）

职责：
  * 站点清单与配置读写（首次运行自动生成 config.json）
  * 多线程并发抓取 + 解析（复用 radar.py 的解析器）
  * 本地缓存读写（离线可用、秒开）
  * 检索（多关键词 AND、-排除词）
  * 日志
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import radar  # 复用抓取与解析（纯标准库）  # noqa: E402

APP_TITLE = "川大通知雷达"
APP_VERSION = "2.6"

# ------------------------------------------------------------------ 路径

def app_dir() -> Path:
    if getattr(sys, "frozen", False):          # PyInstaller 打包后
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    """数据目录：优先 exe 同级 data/，不可写则退回 %LOCALAPPDATA%\\SCUNoticeRadar。"""
    target = app_dir() / "data"
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return target
    except Exception:  # noqa: BLE001
        alt = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SCUNoticeRadar"
        alt.mkdir(parents=True, exist_ok=True)
        return alt


CONFIG_PATH = lambda: data_dir() / "config.json"
CACHE_PATH = lambda: data_dir() / "cache.json"
UI_PATH = lambda: data_dir() / "ui.json"
LOG_PATH = lambda: data_dir() / "app.log"

_logger = None


def get_logger():
    global _logger
    if _logger:
        return _logger
    logger = logging.getLogger("radar")
    logger.setLevel(logging.INFO)
    try:
        handler = logging.FileHandler(LOG_PATH(), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    except Exception:  # noqa: BLE001
        pass
    _logger = logger
    return logger


# ------------------------------------------------------------------ 默认站点

DEFAULT_SITES = [
    # ---- 学校层面 ----
    {"name": "教务处·通知公告", "category": "教务处", "url": "https://jwc.scu.edu.cn/tzgg.htm",
     "enabled": True, "more_pages": 2, "note": "教学通知、四六级、选课、毕业论文、交换生项目"},
    {"name": "学工部·通知公告", "category": "学工部", "url": "https://xgb.scu.edu.cn/index/tzgg.htm",
     "enabled": True, "more_pages": 2, "note": "奖学金、助学金、综测、学生资助、评奖评优"},
    {"name": "川大主站·通知公告", "category": "学校", "url": "https://www.scu.edu.cn/index/xw/tzgg.htm",
     "enabled": True, "note": "学校层面公告、比赛、活动安排"},

    # ---- 各学院（均已实测可抓；带★为通知公告栏目，其余为学院新闻动态栏目）----
    {"name": "水利水电学院", "category": "学院", "url": "http://cwrh.scu.edu.cn/tzgg.htm",
     "enabled": True, "note": "★通知公告"},
    {"name": "电气工程学院", "category": "学院", "url": "https://ee.scu.edu.cn/index/tzgg.htm",
     "enabled": True, "note": "★通知公告"},
    {"name": "化学学院", "category": "学院", "url": "http://chem.scu.edu.cn/tzgg.htm",
     "enabled": True, "note": "★通知公告"},
    {"name": "物理学院", "category": "学院", "url": "http://physics.scu.edu.cn/index/tzgg.htm",
     "enabled": True, "note": "★通知公告"},
    {"name": "公共管理学院", "category": "学院", "url": "http://ggglxy.scu.edu.cn/index/tzgg.htm",
     "enabled": True, "note": "★通知公告"},
    {"name": "艺术学院", "category": "学院", "url": "http://art.scu.edu.cn/index/tzgg.htm",
     "enabled": True, "note": "★通知公告（页面未提供日期）"},
    {"name": "建筑与环境学院", "category": "学院", "url": "http://acem.scu.edu.cn/index/tzgg.htm",
     "enabled": True, "note": "★通知公告（页面未提供日期）"},
    {"name": "华西药学院", "category": "学院", "url": "http://pharmacy.scu.edu.cn/tzgg.htm",
     "enabled": True, "note": "★通知公告"},
    {"name": "生命科学学院", "category": "学院", "url": "https://life.scu.edu.cn/xwxx/gsgg.htm",
     "enabled": True, "note": "公告"},
    {"name": "人工智能学院", "category": "学院", "url": "https://ai.scu.edu.cn/xwgg/xwdt.htm",
     "enabled": True, "note": "新闻动态"},
    {"name": "计算机学院", "category": "学院", "url": "https://cs.scu.edu.cn/index/tzgg.htm",
     "enabled": False, "note": "列表由 JS 渲染，暂抓不到（保持关闭）"},
    {"name": "材料科学与工程学院", "category": "学院", "url": "http://mse.scu.edu.cn/xygk/xyxw.htm",
     "enabled": True, "note": "学院新闻（无日期）"},
    {"name": "化学工程学院", "category": "学院", "url": "http://ce.scu.edu.cn/xsgz/xshddt.htm",
     "enabled": True, "note": "学生活动动态"},
    {"name": "生物医学工程学院", "category": "学院", "url": "http://bme.scu.edu.cn/xygk1/xyxw.htm",
     "enabled": True, "note": "学院新闻"},
    {"name": "网络空间安全学院", "category": "学院", "url": "http://ccs.scu.edu.cn/kxyj/kydt.htm",
     "enabled": True, "note": "科研动态"},
    {"name": "碳中和未来技术学院", "category": "学院", "url": "https://ccnft.scu.edu.cn/ky/xsdt.htm",
     "enabled": True, "note": "学术动态"},
    {"name": "体育学院", "category": "学院", "url": "http://pe.scu.edu.cn/kxyj/xsdt.htm",
     "enabled": True, "note": "学术活动预告"},
    {"name": "卓越工程师学院", "category": "学院", "url": "http://ees.scu.edu.cn/djyl/djdt.htm",
     "enabled": True, "note": "学院动态"},
    {"name": "华西基础医学与法医学院", "category": "学院", "url": "http://jcfy.scu.edu.cn/xyxw/xyyw1.htm",
     "enabled": True, "note": "学院要闻"},
]

CATEGORIES = ["教务处", "学工部", "学校", "学院"]

DEFAULT_CONFIG = {
    "version": 1,
    "enrich_detail": True,      # 列表页缺日期/标题被截断时，去详情页补全
    "enrich_limit": 20,         # 每个站点最多补全多少个详情页（防止刷新变慢）
    "keywords": [
        "推免", "推荐免试", "保研", "免试攻读", "夏令营", "预报名", "奖学金", "助学金", "评奖评优", "综测", "综合素质评价",
        "竞赛", "大创", "创新创业", "科研训练", "报名", "选拔", "招生", "分流", "转专业",
        "选课", "补退选", "考试", "四六级", "CET", "体测", "体质测试", "实习", "实践",
        "交换", "访学", "出国", "留学", "毕业论文", "毕业设计", "招聘", "校招", "就业",
        "助教", "辅导员", "学籍", "成绩", "重修", "讲座", "报告会", "比赛",
    ],
    "hot_keywords": ["推免", "推荐免试", "保研", "免试攻读", "夏令营", "奖学金", "大创", "竞赛", "补退选", "四六级", "报名"],
    # 看板权重：格子大小 = 优先级×priority + 近30天条数×recent30 + 关键词命中×hits + 未读×unread
    "weights": {
        "priority": {
            "教务处·通知公告": 3, "学工部·通知公告": 3, "川大主站·通知公告": 3,
            "水利水电学院": 3
        },
        "default_priority": 1,
        "formula": {"priority": 3, "recent30": 0.8, "hits": 0.6, "unread": 2.0},
        "tiers": {"hero": 3, "large": 4, "mid": 8}
    },
    "sites": DEFAULT_SITES,
}


# ------------------------------------------------------------------ 配置 / 缓存

def _read_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        get_logger().warning("读取 %s 失败：%s", path, exc)
    return default


def _write_json(path: Path, obj) -> bool:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception as exc:  # noqa: BLE001
        get_logger().error("写入 %s 失败：%s", path, exc)
        return False


def load_config() -> dict:
    cfg = _read_json(CONFIG_PATH(), None)
    if not cfg or not cfg.get("sites"):
        cfg = json.loads(json.dumps(DEFAULT_CONFIG, ensure_ascii=False))
        _write_json(CONFIG_PATH(), cfg)
    cfg.setdefault("keywords", DEFAULT_CONFIG["keywords"])
    cfg.setdefault("hot_keywords", DEFAULT_CONFIG["hot_keywords"])
    if not isinstance(cfg.get("enrich_detail"), bool):
        cfg["enrich_detail"] = DEFAULT_CONFIG["enrich_detail"]
    if not isinstance(cfg.get("enrich_limit"), int):
        cfg["enrich_limit"] = DEFAULT_CONFIG["enrich_limit"]
    w = cfg.get("weights")
    if not isinstance(w, dict):
        w = {}
    for key, val in DEFAULT_CONFIG["weights"].items():
        if not isinstance(w.get(key), dict):
            w[key] = json.loads(json.dumps(val, ensure_ascii=False))
        else:
            for k2, v2 in val.items():
                w[key].setdefault(k2, v2)
    cfg["weights"] = w
    for s in cfg["sites"]:
        s.setdefault("category", "学院" if "学院" in s.get("name", "") else "学校")
        s.setdefault("enabled", True)
    return cfg


def save_config(cfg: dict) -> bool:
    return _write_json(CONFIG_PATH(), cfg)


def load_cache() -> dict:
    cache = _read_json(CACHE_PATH(), {"items": [], "seen": {}, "errors": [], "updated": ""})
    cache.setdefault("items", [])
    cache.setdefault("seen", {})
    cache.setdefault("errors", [])
    return cache


def save_cache(cache: dict) -> bool:
    return _write_json(CACHE_PATH(), cache)


def load_ui() -> dict:
    return _read_json(UI_PATH(), {})


def save_ui(ui: dict) -> None:
    _write_json(UI_PATH(), ui)


# ------------------------------------------------------------------ 抓取

def fetch_sites(sites: list, keywords: list, hot: list, timeout: int = 15,
                workers: int = 6, progress=None, enrich: bool = True,
                enrich_limit: int = 20) -> tuple:
    """并发抓取多个站点。progress(done, total, site_name) 用于界面进度显示。"""
    items, errors = [], []
    total = len(sites)
    if not total:
        return items, errors

    def one(site):
        pat = site.get("href_pattern", radar.DEFAULT_HREF_PATTERN)
        page = radar.http_get(site["url"], timeout=timeout, retry=2)
        got = radar.parse_items(page, site["url"], pat)
        # 可选：跟看翻页，把同一栏目最近的历史页也抓回来（site.more_pages = N）
        more = int(site.get("more_pages") or 0)
        if more > 0:
            for extra in radar.find_more_pages(page, site["url"], more, timeout=timeout):
                try:
                    p2 = radar.http_get(extra, timeout=timeout, retry=1)
                    got.extend(radar.parse_items(p2, extra, pat))
                except Exception as exc:  # noqa: BLE001
                    get_logger().info("附加页抓取失败 %s：%s", extra, exc)
        # 按 URL 去重
        uniq = {}
        for it in got:
            uniq.setdefault(it["url"], it)
        got = list(uniq.values())
        for it in got:
            it["site"] = site["name"]
            it["category"] = site.get("category", "")
            hits = radar.matched_keywords(it["title"] + " " + it.get("summary", ""), keywords)
            it["hits"] = hits
            it["hot"] = [k for k in hits if k in hot]
        return got

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, s): s for s in sites}
        for fut in as_completed(futures):
            site = futures[fut]
            done += 1
            if progress:
                try:
                    progress(done, total, site["name"])
                except Exception:  # noqa: BLE001
                    pass
            try:
                items.extend(fut.result())
            except Exception as exc:  # noqa: BLE001
                errors.append({"site": site["name"], "url": site["url"], "error": f"{type(exc).__name__}: {exc}"})
                get_logger().warning("抓取失败 %s：%s", site["url"], exc)
    items.sort(key=lambda x: (x.get("date") or "", x.get("title") or ""), reverse=True)

    # 详情页补全：列表页标题被截断、或页面本身不给日期时，去正文页取完整标题/发布日期。
    if enrich and items:
        seen_n = {}
        targets = []
        for it in items:
            if it.get("date") and not radar.is_truncated_title(it.get("title", "")):
                continue
            used = seen_n.get(it["site"], 0)
            if used >= enrich_limit:
                continue
            seen_n[it["site"]] = used + 1
            targets.append(it)
        if targets:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {pool.submit(radar.fetch_detail_meta, it["url"], timeout): it for it in targets}
                for fut in as_completed(futs):
                    it = futs[fut]
                    try:
                        meta = fut.result()
                    except Exception:  # noqa: BLE001
                        meta = {}
                    if meta.get("date") and not it.get("date"):
                        it["date"] = meta["date"]
                        it["date_from"] = "detail"
                    t = meta.get("title")
                    if t and radar.is_truncated_title(it.get("title", "")):
                        # 详情页标题必须与列表页截断前缀一致，且不短于它，才当作完整版
                        base = radar.strip_truncation(it["title"])
                        head = base[:10]
                        if (t.startswith(head) or (head and head in t)) and len(t) >= len(base):
                            it["title"] = t
                            it["title_from"] = "detail"
            items.sort(key=lambda x: (x.get("date") or "", x.get("title") or ""), reverse=True)
    return items, errors


def refresh(cfg: dict, cache: dict, progress=None) -> dict:
    """抓取所有启用站点，更新缓存。返回新的 cache。"""
    sites = [s for s in cfg["sites"] if s.get("enabled", True)]
    items, errors = fetch_sites(sites, cfg["keywords"], cfg["hot_keywords"], progress=progress,
                                enrich=bool(cfg.get("enrich_detail", True)),
                                enrich_limit=int(cfg.get("enrich_limit", 20)))
    old_seen = cache.get("seen", {})
    # 清掉已下线站点的条目
    live_names = {s["name"] for s in cfg["sites"]}
    items = [i for i in items if i["site"] in live_names]
    # 抓取失败的站点：沿用上一次的条目，否则一次网络抖动就把整个来源清空、
    # 下一次恢复时那些旧通知又会全部被当成「新增」。
    failed = {e.get("site") for e in errors}
    kept = [i for i in cache.get("items", [])
            if i.get("site") in failed and i.get("site") in live_names]
    if kept:
        have = {i["url"] for i in items}
        for i in kept:
            if i["url"] not in have:
                i = dict(i)
                i["stale"] = True          # 标记：本轮没抓到，显示的是上次快照
                items.append(i)
        items.sort(key=lambda x: (x.get("date") or "", x.get("title") or ""), reverse=True)
        cache["stale_sites"] = sorted({i["site"] for i in items if i.get("stale")})
    else:
        cache.pop("stale_sites", None)
    prev_updated = cache.get("updated") or ""
    cache["items"] = items
    cache["errors"] = errors
    cache["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not old_seen:
        # 第一次运行：把现有通知全部记为已读（建基线），之后只提醒新增
        cache["seen"] = {i["url"]: stamp for i in items}
        cache["first_run"] = True
    else:
        # 保留历史已读记录；本轮「新出现」的条目才算未读。
        #   * 发布日期 >= 上次刷新日期的 → 真·新增，标未读（界面会高亮）
        #   * 更早的旧条目（解析规则升级、站点重新列出历史内容时会一次性冒出来）
        #     → 直接记已读，避免一次刷出几十条假「新增」
        # 注：旧实现把新条目也立即打上时间戳，导致「新增」永远为 0、高亮永不出现。
        merged = dict(old_seen)
        try:
            last_day = datetime.strptime(prev_updated[:10], "%Y-%m-%d")
        except ValueError:
            last_day = None
        for i in items:
            u = i.get("url")
            if not u or u in merged:
                continue
            fresh = True
            d = (i.get("date") or "")[:10]
            if last_day and len(d) == 10:
                try:
                    fresh = datetime.strptime(d, "%Y-%m-%d") >= last_day
                except ValueError:
                    fresh = True
            merged[u] = "" if fresh else stamp
        if len(merged) > 4000:                      # 控制体积：只保留当前条目与未读记录
            cur = {i["url"] for i in items}
            merged = {u: v for u, v in merged.items() if u in cur or not v}
        cache["seen"] = merged
    save_cache(cache)
    return cache


def mark_all_read(cache: dict) -> dict:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    cache["seen"] = {i["url"]: (cache.get("seen", {}).get(i["url"]) or stamp) for i in cache["items"]}
    save_cache(cache)
    return cache


def set_read(cache: dict, url: str, read: bool) -> dict:
    seen = cache.setdefault("seen", {})
    if read:
        seen[url] = seen.get(url) or datetime.now().strftime("%Y-%m-%d %H:%M")
    else:
        seen[url] = ""
    save_cache(cache)
    return cache


# ------------------------------------------------------------------ 检索

def parse_query(query: str) -> tuple:
    """支持 '推免 报名'（都要包含）和 '奖学金 -华西'（排除）。"""
    must, exclude = [], []
    for token in (query or "").split():
        token = token.strip()
        if not token:
            continue
        if token.startswith("-") and len(token) > 1:
            exclude.append(token[1:].lower())
        else:
            must.append(token.lower())
    return must, exclude


def filter_items(items: list, query: str = "", category: str = "全部", site: str = "",
                 only_unread: bool = False, seen: dict = None, keywords_only: bool = False,
                 days: int = 0) -> list:
    seen = seen or {}
    must, exclude = parse_query(query)
    out = []
    for it in items:
        if site and it.get("site") != site:
            continue
        if category and category != "全部" and it.get("category") != category:
            continue
        if only_unread and seen.get(it["url"]):
            continue
        if keywords_only and not it.get("hits"):
            continue
        if days:
            d = it.get("date") or ""
            if d and len(d) >= 10:
                try:
                    delta = (datetime.now() - datetime.strptime(d[:10], "%Y-%m-%d")).days
                    if delta > days:
                        continue
                except ValueError:
                    pass
        if must or exclude:
            hay = (it.get("title", "") + " " + it.get("summary", "") + " " + it.get("site", "")).lower()
            if any(word not in hay for word in must):
                continue
            if any(word in hay for word in exclude):
                continue
        out.append(it)
    return out


def is_unread(it: dict, seen: dict) -> bool:
    return not seen.get(it["url"])


# ------------------------------------------------------------------ 导出

def export_view(items: list, seen: dict, path_base: Path, query: str = "", category: str = "全部") -> tuple:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    path_base.mkdir(parents=True, exist_ok=True)
    md_path = path_base / f"导出_{stamp}.md"
    html_path = path_base / f"导出_{stamp}.html"
    head = f"# 川大通知导出\n\n- 导出时间：{datetime.now():%Y-%m-%d %H:%M}\n- 范围：{category}" + (f"｜检索：{query}" if query else "") + f"\n- 条数：{len(items)}\n\n"
    lines = [head, "| 日期 | 来源 | 新 | 标题 |", "| --- | --- | --- | --- |"]
    rows = []
    for it in items:
        flag = "新" if is_unread(it, seen) else ""
        lines.append(f"| {it.get('date') or '-'} | {it.get('site')} | {flag} | [{it.get('title')}]({it.get('url')}) |")
        rows.append(f"<tr><td>{it.get('date') or '-'}</td><td>{it.get('site')}</td><td>{flag}</td>"
                    f"<td><a href=\"{it.get('url')}\" target=\"_blank\">{it.get('title')}</a></td></tr>")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>川大通知导出</title>
<style>body{{font-family:"Microsoft YaHei",sans-serif;max-width:1100px;margin:24px auto}}
table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{border-bottom:1px solid #eee;padding:8px;text-align:left}}
a{{color:#1655b3;text-decoration:none}}</style></head><body>
<h2>川大通知导出</h2><p>{head.replace(chr(10), '<br>')}</p>
<table><thead><tr><th>日期</th><th>来源</th><th>新</th><th>标题</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</body></html>"""
    html_path.write_text(html, encoding="utf-8")
    return md_path, html_path