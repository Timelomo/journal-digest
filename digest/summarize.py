"""批量总结论文：中文摘要、核心贡献、方法/应用标签、相关度评分。

底层通过 LLMChain 调用 Claude / OpenAI / DeepSeek 等；返回结果统一在这里做校验与清洗
（标签必须在词表内、相关度限制在 1–5），保证不同模型输出格式一致。
"""
from __future__ import annotations

import difflib
import logging

from .llm import LLMChain, LLMError

log = logging.getLogger(__name__)

SYSTEM = """你是运筹学与管理科学领域的资深审稿人，负责为一位研究者筛选和速读期刊新文章。

研究者的研究兴趣：
{interests}

对每篇论文：
1. summary：用{lang}写 2–3 句，说明研究什么问题、用什么方法/模型、得到什么结论。
   面向同行，保留关键术语（可附英文原词），不要空话套话，不要复述标题。
2. contribution：一句话点出最核心的贡献或新意。
3. methods / applications：只能从给定词表中原样选择，各选 1–3 个最贴切的。
   方法词表：{methods}
   应用词表：{applications}
4. relevance：按研究兴趣打 1–5 分（5=高度相关必读，3=相邻领域可浏览，1=无关）。
   relevance_why 用一句话说明理由。
若只有标题没有摘要，请基于标题谨慎推断，并在 summary 开头注明“（仅据标题）”。"""


def build_schema(tags: dict) -> dict:
    return {
        "type": "object",
        "properties": {
            "papers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "输入中论文的编号"},
                        "summary": {"type": "string"},
                        "contribution": {"type": "string"},
                        "methods": {"type": "array", "items": {"type": "string", "enum": tags["methods"]}},
                        "applications": {"type": "array",
                                         "items": {"type": "string", "enum": tags["applications"]}},
                        "relevance": {"type": "integer", "minimum": 1, "maximum": 5},
                        "relevance_why": {"type": "string"},
                    },
                    "required": ["id", "summary", "contribution", "methods", "applications",
                                 "relevance", "relevance_why"],
                },
            }
        },
        "required": ["papers"],
    }


def _normalize_tags(values, vocab: list[str]) -> list[str]:
    """把模型给出的标签映射回词表（容忍轻微措辞差异），去重并最多保留 3 个。"""
    if isinstance(values, str):
        values = [values]
    out = []
    for v in values or []:
        v = str(v).strip()
        if v in vocab:
            hit = v
        else:
            close = difflib.get_close_matches(v, vocab, n=1, cutoff=0.5)
            hit = close[0] if close else None
        if hit and hit not in out:
            out.append(hit)
    return out[:3]


def _clean(r: dict, tags: dict) -> dict | None:
    if not isinstance(r, dict) or not r.get("summary"):
        return None
    try:
        rel = int(round(float(r.get("relevance", 0))))
    except (TypeError, ValueError):
        rel = 0
    return {
        "summary": str(r["summary"]).strip(),
        "contribution": str(r.get("contribution") or "").strip(),
        "methods": _normalize_tags(r.get("methods"), tags["methods"]),
        "applications": _normalize_tags(r.get("applications"), tags["applications"]),
        "relevance": min(5, max(1, rel)) if rel else 1,
        "relevance_why": str(r.get("relevance_why") or "").strip(),
    }


def _paper_block(i: int, p: dict) -> str:
    abstract = p.get("abstract") or "（无摘要）"
    return f"[{i}] 期刊：{p['journal']}\n标题：{p['title']}\n摘要：{abstract}"


class Summarizer:
    def __init__(self, cfg: dict):
        llm_cfg = cfg.get("llm", {})
        self.chain = LLMChain(llm_cfg)
        self.batch_size = int(llm_cfg.get("batch_size", 8))
        self.tags = cfg["tags"]
        self.schema = build_schema(self.tags)
        self.system = SYSTEM.format(
            interests=cfg["research_interests"].strip(),
            lang=llm_cfg.get("summary_language", "中文"),
            methods="、".join(self.tags["methods"]),
            applications="、".join(self.tags["applications"]),
        )

    def _call(self, batch: list[dict]) -> tuple[str, dict[int, dict]]:
        user = ("请总结以下论文，按 JSON 格式返回，papers 数组中每项的 id 对应方括号中的编号：\n\n"
                + "\n\n".join(_paper_block(i, p) for i, p in enumerate(batch)))
        provider, data = self.chain.structured(self.system, user, self.schema)
        results = {}
        for r in (data or {}).get("papers", []) if isinstance(data, dict) else []:
            try:
                idx = int(r.get("id"))
            except (TypeError, ValueError, AttributeError):
                continue
            cleaned = _clean(r, self.tags)
            if cleaned and 0 <= idx < len(batch):
                results[idx] = cleaned
        return provider, results

    def summarize(self, papers: list[dict]):
        """逐批生成总结，yield (paper, result, provider)。批内遗漏的论文会单独重试一次。"""
        for start in range(0, len(papers), self.batch_size):
            batch = papers[start:start + self.batch_size]
            try:
                provider, results = self._call(batch)
            except (LLMError, ValueError) as e:
                log.warning("批量总结失败，改为逐篇：%s", e)
                provider, results = None, {}
            for i, p in enumerate(batch):
                r, prov = results.get(i), provider
                if r is None:
                    try:
                        prov, single = self._call([p])
                        r = single.get(0)
                    except (LLMError, ValueError) as e:
                        log.error("总结失败 %s：%s", p["doi"], e)
                if r:
                    yield p, r, prov

    def overview(self, papers: list[dict]) -> str:
        """基于本期所有总结，写一段“本期看点”。"""
        if not papers:
            return ""
        lines = [f"- [{p['journal']}] {p['title']}（相关度{p.get('relevance')}）：{p.get('summary')}"
                 for p in papers]
        prompt = ("以下是本期新发表论文的总结。请用中文写“本期看点”：3–5 条要点，"
                  "概括值得关注的研究趋势、方法动向或与研究者兴趣最相关的亮点，"
                  "提到具体论文时用简短标题指代。直接输出 Markdown 无序列表（以“- ”开头），"
                  "不要标题和开场白。\n\n" + "\n".join(lines))
        try:
            _, text = self.chain.text(self.system, prompt)
            return text
        except LLMError as e:
            log.warning("本期看点生成失败：%s", e)
            return ""
