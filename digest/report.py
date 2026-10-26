"""把总结结果渲染成 Markdown 周报（并可转成邮件用 HTML）。"""
from __future__ import annotations

from collections import OrderedDict

import markdown as md


def _stars(n) -> str:
    n = int(n or 0)
    return "★" * n + "☆" * (5 - n)


def _authors(a: list[str], k: int = 3) -> str:
    if not a:
        return "作者未知"
    return ", ".join(a[:k]) + (" 等" if len(a) > k else "")


def _tags(p: dict) -> str:
    tags = (p.get("methods") or []) + (p.get("applications") or [])
    return " ".join(f"`{t}`" for t in tags)


def _full_entry(p: dict) -> str:
    return "\n".join([
        f"### [{p['title']}]({p['url']})",
        f"*{p['journal']}* · {_authors(p['authors'])} · {p.get('published') or ''} · 相关度 {_stars(p.get('relevance'))}",
        "",
        f"**核心贡献**：{p.get('contribution') or ''}",
        "",
        p.get("summary") or "",
        "",
        _tags(p),
        "",
        f"> 推荐理由：{p.get('relevance_why') or ''}",
        "",
    ])


def _compact_entry(p: dict) -> str:
    return "\n".join([
        f"- **[{p['title']}]({p['url']})**  ",
        f"  {_authors(p['authors'])} · {p.get('published') or ''} · {_stars(p.get('relevance'))}  ",
        f"  {p.get('summary') or ''}  ",
        f"  {_tags(p)}",
        "",
    ])


def build_markdown(papers: list[dict], run_date: str, overview: str, cfg: dict) -> str:
    threshold = cfg["settings"].get("min_relevance_for_top", 4)
    order = [j["name"] for j in cfg["journals"]]
    top = sorted([p for p in papers if (p.get("relevance") or 0) >= threshold],
                 key=lambda p: (-(p.get("relevance") or 0), p["journal"]))
    rest = [p for p in papers if (p.get("relevance") or 0) < threshold]

    by_journal: "OrderedDict[str, list]" = OrderedDict((n, []) for n in order)
    for p in rest:
        by_journal.setdefault(p["journal"], []).append(p)
    for lst in by_journal.values():
        lst.sort(key=lambda p: (-(p.get("relevance") or 0), p.get("published") or ""))

    counts = OrderedDict()
    for p in papers:
        counts[p["journal"]] = counts.get(p["journal"], 0) + 1

    out = [f"# 运筹期刊新文速递 · {run_date}", ""]
    out.append(f"> 本期共 **{len(papers)}** 篇新文章，来自 {len(counts)} 本期刊；"
               f"其中 **{len(top)}** 篇与你的研究方向高度相关。")
    out.append("")
    if counts:
        out.append("| 期刊 | 篇数 |\n|---|---|")
        out += [f"| {j} | {counts[j]} |" for j in sorted(counts, key=lambda j: order.index(j) if j in order else 99)]
        out.append("")

    if overview:
        out += ["## 本期看点", "", overview, ""]

    if top:
        out += [f"## ⭐ 本期精选（相关度 ≥ {threshold}）", ""]
        out += [_full_entry(p) for p in top]

    if rest:
        out += ["## 按期刊浏览", ""]
        for j, lst in by_journal.items():
            if not lst:
                continue
            out += [f"### {j}（{len(lst)}）", ""]
            out += [_compact_entry(p) for p in lst]

    if not papers:
        out.append("本期没有新文章。")

    used = sorted({p.get("llm") for p in papers if p.get("llm")})
    who = "、".join(used) if used else "大模型"
    out += ["---", f"*总结与标签由 {who} 自动生成，可能有误，请以原文为准。*"]
    return "\n".join(out)


EMAIL_CSS = """
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",Segoe UI,sans-serif;max-width:760px;
margin:0 auto;padding:16px;color:#1f2328;line-height:1.65;font-size:15px}
h1{font-size:22px;border-bottom:2px solid #0b5cad;padding-bottom:8px}
h2{font-size:18px;margin-top:28px;color:#0b5cad}
h3{font-size:16px;margin:20px 0 4px}
a{color:#0b5cad;text-decoration:none}
blockquote{margin:8px 0;padding:6px 12px;background:#f3f6fa;border-left:3px solid #0b5cad;color:#444}
blockquote p{margin:0}
code{background:#eef2f7;border-radius:4px;padding:1px 6px;font-size:12px;color:#334}
table{border-collapse:collapse;font-size:13px}td,th{border:1px solid #d8dee4;padding:3px 10px}
li{margin-bottom:12px}
"""


def to_html(markdown_text: str) -> str:
    body = md.markdown(markdown_text, extensions=["tables", "sane_lists"])
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<style>{EMAIL_CSS}</style></head><body>{body}</body></html>")
