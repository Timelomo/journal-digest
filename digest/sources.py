"""数据源：Crossref 增量抓取 + 多源摘要补全。

- Crossref：按 ISSN + DOI 注册日期（created）增量拉取。DOI 在“在线优先/Articles in
  Advance”阶段就已注册，之后分卷分期时 DOI 与注册日期不变，因此不会重复推送。
- 摘要补全链：Crossref 自带 -> OpenAlex -> Semantic Scholar -> Elsevier API（可选）
"""
from __future__ import annotations

import html
import logging
import os
import re
import time
from datetime import date

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

CROSSREF = "https://api.crossref.org"
OPENALEX = "https://api.openalex.org"
S2 = "https://api.semanticscholar.org/graph/v1"
ELSEVIER = "https://api.elsevier.com/content/article/doi"

SELECT = "DOI,title,author,created,published-online,published-print,issued,abstract,URL,type"


def make_session(contact_email: str | None) -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET",), respect_retry_after_header=True)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    ua = "or-journal-digest/1.0"
    if contact_email and "example.com" not in contact_email:
        ua += f" (mailto:{contact_email})"
    s.headers["User-Agent"] = ua
    return s


# ---------------------------------------------------------------- helpers
def _date_parts(obj) -> str | None:
    try:
        parts = obj["date-parts"][0]
    except (KeyError, IndexError, TypeError):
        return None
    if not parts or parts[0] is None:
        return None
    y, m, d = (list(parts) + [1, 1])[:3]
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def clean_abstract(raw: str | None) -> str | None:
    if not raw:
        return None
    txt = re.sub(r"<[^>]+>", " ", raw)          # 去掉 JATS/HTML 标签
    txt = html.unescape(txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    txt = re.sub(r"^(abstract|summary)[\s.:]*", "", txt, flags=re.I)
    return txt if len(txt) > 40 else None


def _authors(item) -> list[str]:
    out = []
    for a in item.get("author", []) or []:
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name")
        if name:
            out.append(name)
    return out


# ---------------------------------------------------------------- crossref
def fetch_crossref(session: requests.Session, journal: dict, since: date,
                   contact_email: str | None, skip_patterns: list[str]) -> list[dict]:
    """返回该期刊自 since 以来新注册的研究论文。"""
    skip_re = [re.compile(p, re.I) for p in skip_patterns]
    params = {
        "filter": f"from-created-date:{since.isoformat()},type:journal-article",
        "rows": 200,
        "cursor": "*",
        "select": SELECT,
    }
    if contact_email and "example.com" not in contact_email:
        params["mailto"] = contact_email

    papers, seen_cursor = [], set()
    while True:
        r = session.get(f"{CROSSREF}/journals/{journal['issn']}/works", params=params, timeout=60)
        r.raise_for_status()
        msg = r.json()["message"]
        items = msg.get("items", [])
        for it in items:
            title = " ".join(it.get("title") or []).strip()
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(title)))
            if not title or any(p.search(title) for p in skip_re):
                continue
            published = (_date_parts(it.get("published-online")) or _date_parts(it.get("published-print"))
                         or _date_parts(it.get("issued")) or _date_parts(it.get("created")))
            abstract = clean_abstract(it.get("abstract"))
            papers.append({
                "doi": it["DOI"],
                "journal": journal["name"],
                "title": title,
                "authors": _authors(it),
                "url": it.get("URL") or f"https://doi.org/{it['DOI']}",
                "published": published,
                "created": _date_parts(it.get("created")),
                "abstract": abstract,
                "abstract_src": "crossref" if abstract else None,
            })
        cursor = msg.get("next-cursor")
        if len(items) < params["rows"] or not cursor or cursor in seen_cursor:
            break
        seen_cursor.add(cursor)
        params["cursor"] = cursor
    return papers


def check_issn(session: requests.Session, issn: str) -> dict | None:
    r = session.get(f"{CROSSREF}/journals/{issn}", timeout=30)
    if r.status_code != 200:
        return None
    m = r.json()["message"]
    return {"title": m.get("title"), "publisher": m.get("publisher"), "issn": m.get("ISSN")}


# ---------------------------------------------------------------- 摘要补全
def _openalex(session, doi: str) -> str | None:
    params = {"select": "abstract_inverted_index"}
    if os.getenv("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    r = session.get(f"{OPENALEX}/works/doi:{doi}", params=params, timeout=30)
    if r.status_code != 200:
        return None
    inv = (r.json() or {}).get("abstract_inverted_index")
    if not inv:
        return None
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return clean_abstract(" ".join(pos[i] for i in sorted(pos)))


def _semantic_scholar(session, doi: str) -> str | None:
    headers = {}
    if os.getenv("S2_API_KEY"):
        headers["x-api-key"] = os.environ["S2_API_KEY"]
    else:
        time.sleep(1.1)  # 无 key 时共享限流，放慢一点
    r = session.get(f"{S2}/paper/DOI:{doi}", params={"fields": "abstract"}, headers=headers, timeout=30)
    if r.status_code != 200:
        return None
    return clean_abstract((r.json() or {}).get("abstract"))


def _elsevier(session, doi: str) -> str | None:
    key = os.getenv("ELSEVIER_API_KEY")
    if not key or not doi.startswith("10.1016/"):
        return None
    r = session.get(f"{ELSEVIER}/{doi}", params={"view": "META_ABS"},
                    headers={"X-ELS-APIKey": key, "Accept": "application/json"}, timeout=30)
    if r.status_code != 200:
        return None
    core = (r.json().get("full-text-retrieval-response") or {}).get("coredata") or {}
    return clean_abstract(core.get("dc:description"))


ENRICHERS = (("openalex", _openalex), ("semantic_scholar", _semantic_scholar), ("elsevier", _elsevier))


def find_abstract(session: requests.Session, doi: str) -> tuple[str | None, str | None]:
    for name, fn in ENRICHERS:
        try:
            abs_ = fn(session, doi)
        except requests.RequestException as e:
            log.debug("%s failed for %s: %s", name, doi, e)
            abs_ = None
        if abs_:
            return abs_, name
    return None, None
