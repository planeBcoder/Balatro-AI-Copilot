from __future__ import annotations

import json
import logging
from pathlib import Path
import socket
import time
from typing import Callable, Any
from urllib import request, error

from ai.schemas import Decision, validate_decision
from core.errors import CopilotError

log = logging.getLogger(__name__)
BASE_URL = "https://api.deepseek.com"


class TransportError(Exception):
    def __init__(self, status: int | None):
        self.status = status
        super().__init__(f"DeepSeek status={status}")


def http_post(payload: dict, key: str, timeout: float) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(BASE_URL + "/chat/completions", data=body, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": "BalatroAICopilot/1.0"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            data = response.read(2_000_000)
            return json.loads(data)
    except error.HTTPError as exc:
        # Never include the remote response body or request headers in errors.
        raise TransportError(exc.code) from None
    except (error.URLError, TimeoutError, socket.timeout, OSError):
        raise TransportError(None) from None
    except (ValueError, TypeError):
        raise TransportError(502) from None


def friendly(status: int | None) -> str:
    return {401: "API Key 无效，请在设置中重新填写。", 402: "DeepSeek 余额不足，请充值后重试。", 403: "DeepSeek 拒绝访问，请检查账户权限。", 429: "DeepSeek 请求繁忙，请稍后再试。", 400: "DeepSeek 请求未被接受，请检查模型设置。", 404: "当前模型不可用，请在设置中切换模型。"}.get(status, "DeepSeek 暂时无法连接，请检查网络后重试。" if status is None else "DeepSeek 服务暂时异常，请稍后再试。")


class DeepSeekClient:
    def __init__(self, key: str, model: str, prompt_path: Path, transport: Callable[[dict, str, float], dict] = http_post, timeout: float = 18.0):
        if model not in {"deepseek-flash", "deepseek-v4-pro"}:
            raise CopilotError("不支持的模型。")
        self.key, self.model, self.transport, self.timeout = key, model, transport, timeout
        self.prompt = prompt_path.read_text(encoding="utf-8")
        self.shop_prompt_path = prompt_path.parent / 'shop_system.md'

    def _call(self, payload: dict) -> str:
        response = self.transport(payload, self.key, self.timeout)
        try:
            choice = response["choices"][0]
            content = choice["message"]["content"]
            if choice.get("finish_reason") == "length" or not isinstance(content, str) or not content.strip():
                raise ValueError("Empty or truncated content")
            return content
        except (KeyError, IndexError, TypeError, ValueError):
            raise ValueError("Invalid provider response") from None

    def decide(self, state: dict, calculation: dict) -> Decision:
        schema = Decision.model_json_schema()
        messages = [{"role": "system", "content": self.prompt + "\nJSON Schema:\n" + json.dumps(schema, ensure_ascii=False)}, {"role": "user", "content": json.dumps({"state": state, "local_calculation": calculation}, ensure_ascii=False, separators=(",", ":"))}]
        for attempt in range(2):
            start = time.perf_counter()
            try:
                raw = self._call(self._payload(messages, 1700))
                result = validate_decision(raw, calculation)
                log.info("DeepSeek response parsed: attempt=%d latency_ms=%.1f", attempt + 1, (time.perf_counter() - start) * 1000)
                return result
            except TransportError as exc:
                log.warning("DeepSeek call failed: status=%s attempt=%d", exc.status, attempt + 1)
                retryable = exc.status is None or exc.status == 429 or (exc.status >= 500 if exc.status else False)
                if attempt or not retryable:
                    raise CopilotError(friendly(exc.status)) from None
                time.sleep(0.6)
            except ValueError:
                log.warning("DeepSeek JSON/schema/action validation failed: attempt=%d", attempt + 1)
                if attempt:
                    raise CopilotError("AI 返回格式或操作无效，请重试。") from None
                messages.append({"role": "user", "content": "上次结果未通过校验。重新输出符合 JSON Schema 的 JSON；candidate_id、action、cards 必须完整照抄候选，win_probability 必须为 null，probability_method 为 unavailable。"})
        raise CopilotError("AI 分析失败，请重试。")

    def _payload(self, messages: list[dict], tokens: int) -> dict:
        # Official ChatCompletions JSON mode; local Pydantic enforces schema.
        # Non-thinking mode prioritizes latency. No third-party runtime/framework.
        return {"model": self.model, "messages": messages, "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"}, "reasoning_effort": "none", "max_tokens": tokens, "stream": False}

    def decide_shop(self, state: dict, calculation: dict):
        from ai.shop_schema import ShopDecision, validate_shop_decision
        prompt=self.shop_prompt_path.read_text(encoding='utf-8')
        messages=[{'role':'system','content':prompt+'\nJSON Schema:\n'+json.dumps(ShopDecision.model_json_schema(),ensure_ascii=False)},
            {'role':'user','content':json.dumps({'state':state,'local_calculation':calculation},ensure_ascii=False,separators=(',',':'))}]
        for attempt in range(2):
            try:
                result=validate_shop_decision(self._call(self._payload(messages,1800)),calculation)
                log.info('DeepSeek shop review validated: attempt=%d',attempt+1)
                return result
            except TransportError as exc:
                retryable=exc.status is None or exc.status==429 or (exc.status>=500 if exc.status else False)
                if attempt or not retryable:raise CopilotError(friendly(exc.status)) from None
                time.sleep(.6)
            except ValueError:
                if attempt:raise CopilotError('AI 商店建议未通过校验，已保留本地建议。') from None
                messages.append({'role':'user','content':'重新输出符合 Schema 的 JSON，仅使用提供的合法 candidate_id，rank 按 1 起连续排列。reason 和 summary 必须完全不含阿拉伯数字及百分号，所有数值由本地界面负责显示。'})
        raise CopilotError('AI 商店分析暂不可用。')

    def smoke(self) -> None:
        messages = [{"role": "system", "content": 'Return only JSON: {"ok": true}.'}, {"role": "user", "content": "Check JSON connectivity."}]
        for attempt in range(2):
            try:
                content = self._call(self._payload(messages, 64))
                if json.loads(content).get("ok") is not True:
                    raise ValueError("Invalid smoke response")
                log.info("DeepSeek API smoke passed: model=%s", self.model)
                return
            except TransportError as exc:
                if attempt or exc.status in {400, 401, 402, 403, 404}:
                    raise CopilotError(friendly(exc.status)) from None
                time.sleep(0.6)
            except (ValueError, AttributeError):
                if attempt:
                    raise CopilotError("API 连通，但 JSON 测试未通过，请重试。") from None
