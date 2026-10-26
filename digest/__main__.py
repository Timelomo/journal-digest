"""命令行入口。

  python -m digest run              抓取 -> 补摘要 -> 大模型总结 -> 生成周报 ->（可选）发邮件
  python -m digest run --no-llm     预览模式：不调用大模型、不改变推送状态，用原始摘要出报告
  python -m digest check            校验 config.yaml 中各期刊 ISSN
  python -m digest test-llm         测试各大模型 API key 是否可用
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from . import report, sources
from .db import DB

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("digest")


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["journals"] = [j for j in cfg["journals"] if j.get("enabled", True)]
    return cfg


def _days_since(d: str | None, today: date) -> int:
    if not d:
        return 999
    try:
        return (today - datetime.strptime(d, "%Y-%m-%d").date()).days
    except ValueError:
        return 999


# ------------------------------------------------------------------ steps
def step_fetch(cfg, db: DB, session, today: date, since_override: date | None) -> int:
    s = cfg["settings"]
    total = 0
    for j in cfg["journals"]:
        key = f"last_fetch:{j['issn']}"
        last = db.get_meta(key)
        if since_override:
            since = since_override
        elif last:
            since = date.fromisoformat(last) - timedelta(days=s.get("overlap_days", 3))
        else:
            since = today - timedelta(days=s.get("first_run_lookback_days", 7))
        try:
            papers = sources.fetch_crossref(session, j, since, cfg.get("contact_email"),
                                            s.get("skip_title_patterns", []))
        except Exception as e:  # 单本期刊失败不影响其他期刊，下次会从上次成功日期重抓
            log.error("抓取失败 %-45s %s", j["name"], e)
            continue
        new = [p for p in papers if not db.has(p["doi"])]
        for p in new:
            db.insert({**p, "first_seen": today.isoformat(), "status": "new"})
        db.set_meta(key, today.isoformat())
        total += len(new)
        log.info("%-48s 自 %s 起 %3d 篇，新增 %3d", j["name"], since, len(papers), len(new))
    return total


def step_enrich(cfg, db: DB, session, today: date) -> list[dict]:
    """补摘要；返回本次可以进入总结的论文（暂缺摘要且仍在等待期内的会延后）。"""
    grace = cfg["settings"].get("abstract_grace_days", 5)
    ready, deferred = [], 0
    for p in db.by_status("new"):
        if not p.get("abstract"):
            abs_, src = sources.find_abstract(session, p["doi"])
            if abs_:
                db.update(p["doi"], abstract=abs_, abstract_src=src)
                p["abstract"], p["abstract_src"] = abs_, src
        if not p.get("abstract") and _days_since(p.get("created") or p.get("first_seen"), today) < grace:
            deferred += 1
            continue
        ready.append(p)
    with_abs = sum(1 for p in ready if p.get("abstract"))
    log.info("待总结 %d 篇（有摘要 %d，仅标题 %d）；暂缺摘要延后 %d 篇",
             len(ready), with_abs, len(ready) - with_abs, deferred)
    return ready


def step_summarize(summarizer, db: DB, papers: list[dict], today: date) -> int:
    n = 0
    for p, r, provider in summarizer.summarize(papers):
        db.update(p["doi"], status="summarized", summary=r["summary"], contribution=r["contribution"],
                  methods=r["methods"], applications=r["applications"],
                  relevance=r["relevance"], relevance_why=r["relevance_why"],
                  summarized_on=today.isoformat(), llm=provider)
        n += 1
    log.info("已总结 %d / %d 篇", n, len(papers))
    return n


def write_report(cfg, papers, overview, today: date, out_dir: Path, suffix: str = "") -> tuple[Path, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    text = report.build_markdown(papers, today.isoformat(), overview, cfg)
    path = out_dir / f"{today.isoformat()}{suffix}.md"
    path.write_text(text, encoding="utf-8")
    (out_dir / f"latest{suffix}.md").write_text(text, encoding="utf-8")
    (out_dir / f"latest{suffix}.html").write_text(report.to_html(text), encoding="utf-8")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    return path, text


# ------------------------------------------------------------------ commands
def cmd_run(args):
    cfg = load_config(Path(args.config))
    db = DB(args.db)
    session = sources.make_session(cfg.get("contact_email"))
    today = date.today()
    since = date.fromisoformat(args.since) if args.since else None

    summarizer = None
    if not args.no_llm:  # 先检查大模型配置，缺 key 时尽早报错
        from .llm import LLMError
        from .summarize import Summarizer
        try:
            summarizer = Summarizer(cfg)
        except LLMError as e:
            log.error("%s", e)
            sys.exit(1)

    step_fetch(cfg, db, session, today, since)
    ready = step_enrich(cfg, db, session, today)

    if args.no_llm:  # 预览：不改状态
        preview = [{**p, "summary": (p.get("abstract") or "（暂无摘要）")[:400],
                    "contribution": "（预览模式，未调用大模型）"} for p in ready]
        path, _ = write_report(cfg, preview, "", today, Path(args.out), suffix="-preview")
        log.info("预览报告：%s", path)
        return

    if ready:
        n = step_summarize(summarizer, db, ready, today)
        if n == 0:
            log.error("所有论文总结均失败，请检查 API key / 额度 / 模型名称。论文已保留，下次运行会重试。")
            sys.exit(2)
    to_report = db.by_status("summarized")
    if not to_report:
        log.info("没有需要推送的新文章。")
        return

    overview = summarizer.overview(to_report)
    path, text = write_report(cfg, to_report, overview, today, Path(args.out))
    log.info("报告已生成：%s", path)

    from .notify import email_configured, send_email
    if not args.no_email and email_configured():
        send_email(f"运筹期刊新文速递 · {today.isoformat()}（{len(to_report)} 篇）",
                   text, report.to_html(text))

    for p in to_report:
        db.update(p["doi"], status="reported", reported_on=today.isoformat())


def cmd_check(args):
    cfg = load_config(Path(args.config))
    session = sources.make_session(cfg.get("contact_email"))
    ok = True
    for j in cfg["journals"]:
        info = sources.check_issn(session, j["issn"])
        if info:
            print(f"✓ {j['issn']}  {j['name']:<48} -> {info['title']}  [{info['publisher']}]")
        else:
            ok = False
            print(f"✗ {j['issn']}  {j['name']:<48} -> Crossref 未找到，请检查 ISSN")
    sys.exit(0 if ok else 1)


def cmd_test_llm(args):
    """逐个测试已配置 key 的大模型提供方能否正常返回结构化结果。"""
    from .llm import PROVIDER_TYPES
    from .summarize import build_schema, _clean
    cfg = load_config(Path(args.config))
    llm_cfg = cfg.get("llm", {})
    schema = build_schema(cfg["tags"])
    sample = ("[0] 期刊：Operations Research\n标题：Distributionally Robust Two-Stage Stochastic "
              "Programming with Wasserstein Ambiguity\n摘要：We study two-stage stochastic programs "
              "under Wasserstein ambiguity and derive tractable reformulations ...")
    system = "你是运筹学审稿人。用中文总结论文，标签从 enum 中选择。"
    any_ok, tested = False, 0
    for spec in llm_cfg.get("providers", []):
        key = os.getenv(spec.get("api_key_env", ""), "").strip()
        if not key:
            print(f"- {spec['name']:<10} 跳过（未设置 {spec.get('api_key_env')}）")
            continue
        tested += 1
        try:
            p = PROVIDER_TYPES[spec["type"]](spec, key, float(llm_cfg.get("timeout_seconds", 180)))
            data = p.structured(system, "请按 JSON 返回：\n" + sample, schema)
            r = _clean((data.get("papers") or [{}])[0], cfg["tags"])
            assert r, f"返回格式不符：{str(data)[:200]}"
            any_ok = True
            print(f"✓ {spec['name']:<10} {spec['model']:<22} 相关度={r['relevance']} 标签={r['methods']}")
            print(f"  {r['summary'][:120]}")
        except Exception as e:
            print(f"✗ {spec['name']:<10} {spec['model']:<22} {type(e).__name__}: {str(e)[:300]}")
    if tested == 0:
        print("没有任何提供方设置了 API key。")
    sys.exit(0 if any_ok else 1)


def main():
    ap = argparse.ArgumentParser(prog="digest", description="运筹优化期刊新文追踪与总结")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--db", default=str(ROOT / "data" / "papers.db"))
    ap.add_argument("--out", default=str(ROOT / "reports"))
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="完整运行一次")
    r.add_argument("--no-llm", action="store_true", help="预览模式，不调用大模型")
    r.add_argument("--no-email", action="store_true", help="不发送邮件")
    r.add_argument("--since", help="手动指定起始日期 YYYY-MM-DD（覆盖增量逻辑）")
    r.set_defaults(func=cmd_run)
    c = sub.add_parser("check", help="校验期刊 ISSN")
    c.set_defaults(func=cmd_check)
    t = sub.add_parser("test-llm", help="测试各大模型 API key 是否可用")
    t.set_defaults(func=cmd_test_llm)

    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args.func(args)


if __name__ == "__main__":
    main()
