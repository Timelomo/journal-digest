"""多提供方大模型封装：Claude（anthropic）+ 任意 OpenAI 兼容接口（OpenAI / DeepSeek / 千问 …）。

对上层只暴露两个方法：
  structured(system, user, schema) -> (provider_name, dict)   结构化 JSON 输出
  text(system, user)               -> (provider_name, str)    自由文本
按配置顺序尝试，失败自动切换到下一个；同一提供方连续失败 2 次后本次运行不再使用。
"""
from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def parse_json(text: str) -> dict:
    """从模型输出中稳健地提取 JSON 对象（兼容 ```json 代码块、前后多余文字）。"""
    if not text:
        raise ValueError("模型返回为空")
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I | re.S)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(t[start:end + 1])


# --------------------------------------------------------------------- providers
class AnthropicProvider:
    def __init__(self, spec: dict, api_key: str, timeout: float):
        import anthropic
        self.name, self.model = spec["name"], spec["model"]
        self.client = anthropic.Anthropic(api_key=api_key, base_url=spec.get("base_url") or None,
                                          timeout=timeout, max_retries=3)

    def structured(self, system: str, user: str, schema: dict) -> dict:
        tool = {"name": "submit", "description": "提交结构化结果", "input_schema": schema}
        resp = self.client.messages.create(
            model=self.model, max_tokens=8000, system=system, tools=[tool],
            tool_choice={"type": "tool", "name": "submit"},
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                return block.input
        raise ValueError("Claude 未返回工具调用结果")

    def text(self, system: str, user: str) -> str:
        resp = self.client.messages.create(
            model=self.model, max_tokens=2000, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()


class OpenAICompatProvider:
    """OpenAI Chat Completions 兼容接口。用 JSON 模式 + 在提示词中给出 Schema。"""

    def __init__(self, spec: dict, api_key: str, timeout: float):
        import openai
        self._openai = openai
        self.name, self.model = spec["name"], spec["model"]
        self.client = openai.OpenAI(api_key=api_key, base_url=spec.get("base_url") or None,
                                    timeout=timeout, max_retries=3)
        self._json_mode = True  # 个别模型不支持 response_format，失败一次后自动关闭

    def _chat(self, messages: list[dict], json_mode: bool) -> str:
        kwargs = {"model": self.model, "messages": messages}
        if json_mode and self._json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except self._openai.BadRequestError as e:
            if "response_format" in kwargs and "response_format" in str(e).lower():
                log.info("%s 不支持 JSON 模式，改用纯提示词约束", self.name)
                self._json_mode = False
                kwargs.pop("response_format")
                resp = self.client.chat.completions.create(**kwargs)
            else:
                raise
        return resp.choices[0].message.content or ""

    def structured(self, system: str, user: str, schema: dict) -> dict:
        sys_prompt = (system + "\n\n【输出格式】只输出一个 JSON 对象，不要 Markdown 代码块，不要任何解释。"
                      "JSON 必须符合以下 JSON Schema（数组字段中的字符串必须从 enum 中原样选取）：\n"
                      + json.dumps(schema, ensure_ascii=False))
        out = self._chat([{"role": "system", "content": sys_prompt},
                          {"role": "user", "content": user}], json_mode=True)
        return parse_json(out)

    def text(self, system: str, user: str) -> str:
        return self._chat([{"role": "system", "content": system},
                           {"role": "user", "content": user}], json_mode=False).strip()


PROVIDER_TYPES = {"anthropic": AnthropicProvider, "openai_compatible": OpenAICompatProvider}


# --------------------------------------------------------------------- chain
class LLMChain:
    MAX_FAILS = 2

    def __init__(self, llm_cfg: dict):
        timeout = float(llm_cfg.get("timeout_seconds", 180))
        specs = list(llm_cfg.get("providers", []))
        forced = (os.getenv("LLM_PROVIDER") or "").strip().lower()
        if forced:  # 强制指定的提供方排到最前，其余仍作为后备
            specs.sort(key=lambda s: s["name"].lower() != forced)
            if not any(s["name"].lower() == forced for s in specs):
                log.warning("LLM_PROVIDER=%s 不在配置的 providers 中，忽略", forced)

        self.providers, self.fails = [], {}
        for s in specs:
            key = os.getenv(s.get("api_key_env", ""), "").strip()
            if not key:
                continue
            cls = PROVIDER_TYPES.get(s.get("type"))
            if cls is None:
                log.warning("未知的 provider type：%s（%s）", s.get("type"), s["name"])
                continue
            try:
                self.providers.append(cls(s, key, timeout))
            except ImportError as e:
                log.warning("%s 缺少依赖：%s", s["name"], e)
        if not self.providers:
            names = ", ".join(s.get("api_key_env", "?") for s in specs)
            raise LLMError(f"没有可用的大模型：请至少设置一个 API key 环境变量（{names}）")
        log.info("大模型调用顺序：%s", " → ".join(f"{p.name}({p.model})" for p in self.providers))

    def _run(self, method: str, *args):
        errors = []
        for p in self.providers:
            if self.fails.get(p.name, 0) >= self.MAX_FAILS:
                continue
            try:
                result = getattr(p, method)(*args)
                self.fails[p.name] = 0
                return p.name, result
            except Exception as e:  # 网络、鉴权、额度、解析失败等统一切换
                self.fails[p.name] = self.fails.get(p.name, 0) + 1
                msg = f"{type(e).__name__}: {str(e)[:200]}"
                errors.append(f"{p.name} -> {msg}")
                log.warning("%s 调用失败（%d/%d）：%s", p.name, self.fails[p.name], self.MAX_FAILS, msg)
        raise LLMError("所有大模型均调用失败：" + " | ".join(errors))

    def structured(self, system: str, user: str, schema: dict):
        return self._run("structured", system, user, schema)

    def text(self, system: str, user: str):
        return self._run("text", system, user)
