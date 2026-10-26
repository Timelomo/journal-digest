# OR Journal Digest · 运筹期刊新文速递

每周（或每天）自动抓取你关注的运筹优化期刊的新发表论文，由大模型（Claude / OpenAI / DeepSeek 任选，可自动切换）生成中文总结、方法/应用标签和相关度评分，产出一份周报，写入仓库并可推送到邮箱。

**部署步骤见 [DEPLOY.md](DEPLOY.md)。**

```
Crossref（按 ISSN 增量） ─► DOI 去重（SQLite） ─► 补摘要 ─► 大模型总结 / 打标签 / 评分 ─► 周报 ─► 邮件
                                                  │              │
                         OpenAlex → Semantic Scholar → Elsevier   Claude → OpenAI → DeepSeek（失败自动切换）
```

## 特点

- 数据来自 Crossref 开放 API。期刊在线发表（Articles in Advance / Online First）后，当天或次日就能查到。
- 按 DOI 去重。论文正式分期时 DOI 不变，所以只推送一次。
- 根据 `config.yaml` 中的研究兴趣为每篇论文打 1–5 分，高分论文放进“本期精选”。默认已配置为（鲁棒优化、两阶段随机规划、深度强化学习）。
- 内置 Claude（工具调用）和 OpenAI 兼容接口（JSON 模式）两类通道，DeepSeek、通义千问、Kimi、OpenRouter 等都可以接入。不同模型的输出统一校验清洗：标签强制对齐词表，相关度限制在 1–5 分。
- 某家模型 key 失效或余额耗尽时，自动切换到下一家；所有模型都失败时，论文保留到下次重试，本次运行标红报错。
- 新论文暂时缺摘要时，最多等 `abstract_grace_days` 天；到期仍没有就只依据标题总结，并在总结中注明。

## 命令

```bash
python -m digest check            # 校验期刊 ISSN
python -m digest test-llm         # 逐个测试已配置的大模型 key
python -m digest run --no-llm     # 只抓取，不调用大模型，不改变推送状态
python -m digest run              # 完整运行
python -m digest run --since 2026-10-01   # 回溯指定日期之后的论文
```

## 目录结构

```
config.yaml             期刊列表、研究兴趣、标签词表、大模型配置、运行参数
DEPLOY.md               详细部署指南
digest/sources.py       Crossref 抓取与摘要补全
digest/llm.py           多提供方大模型封装与自动切换
digest/summarize.py     批量总结、结果校验
digest/report.py        Markdown / HTML 渲染
digest/notify.py        SMTP 邮件
digest/db.py            SQLite 状态库（data/papers.db，由 Actions 自动提交回仓库）
reports/                历史周报（YYYY-MM-DD.md）与最新一期 latest.md / latest.html
```

## 摘要覆盖率说明

INFORMS、SIAM、SAGE 期刊的摘要在 Crossref 中基本齐全，Springer 期刊大部分齐全。**Elsevier 期刊（EJOR、C&OR、Omega、TR-B 等）不向 Crossref 提供摘要**，要等 OpenAlex 或 Semantic Scholar 收录，通常滞后几天到几周。可以采取的办法：

- 调大 `abstract_grace_days`：多等几天，有摘要的比例更高；
- 配置 `ELSEVIER_API_KEY`：如果学校订阅了 ScienceDirect，通常可以直接获取摘要。这条路径尚未用真实 key 测试，首次使用时请查看日志。

## 费用

数据源（Crossref、OpenAlex、Semantic Scholar）免费。大模型每周处理几十到一百篇论文，token 用量在数万量级，三家的费用都很低，DeepSeek 最便宜。GitHub Actions 每次运行约 2–5 分钟，私有仓库的免费额度足够使用。
