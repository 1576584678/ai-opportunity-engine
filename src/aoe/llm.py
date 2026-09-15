"""OpenAI 兼容接口客户端。

设计要点(来自文档):
- docs/06 §4:解析失败按指数退避重试,最多 3 次。
- docs/05 §7:按租户统计 token 消耗,超限降级到快模型 —— MVP 先记录用量(UsageLedger)。
- docs/05 §7:模型分层(快模型 / 强模型)通过 model 参数切换。

只用标准库,不引入额外依赖,便于私有化部署。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "http://192.168.241.10:3000/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash"

#: docs/06 §4:解析失败按指数退避重试,最多 3 次
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.0

#: max_tokens 放大后的上限
MAX_TOKEN_CEILING = 32000


class LLMError(RuntimeError):
    pass


@dataclass
class LLMConfig:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str = ""
    timeout: float = 180.0
    #: docs/06 §4:抽取类 0-0.2
    extract_temperature: float = 0.1
    #: docs/06 §4:方案生成类 0.3-0.5,不得高于 0.7
    compose_temperature: float = 0.4
    max_tokens: int = 8000
    max_attempts: int = MAX_ATTEMPTS

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> "LLMConfig":
        _load_dotenv(env_file)
        return cls(
            base_url=os.environ.get("AOE_BASE_URL", DEFAULT_BASE_URL),
            model=os.environ.get("AOE_MODEL", DEFAULT_MODEL),
            api_key=os.environ.get("AOE_API_KEY", ""),
            timeout=float(os.environ.get("AOE_TIMEOUT", "180")),
        )

    def require_key(self) -> None:
        if not self.api_key:
            raise LLMError(
                "缺少 API Key。请设置环境变量 AOE_API_KEY,或在仓库根目录创建 .env(已被 gitignore 忽略)。"
            )


def _load_dotenv(env_file: str | Path | None = None) -> None:
    """极简 .env 加载:只填空缺的环境变量,不覆盖已有值。"""
    candidates = [Path(env_file)] if env_file else [Path.cwd() / ".env"]
    for path in candidates:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


@dataclass
class UsageRecord:
    purpose: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    attempts: int = 1
    seconds: float = 0.0


@dataclass
class UsageLedger:
    """token 消耗账本(docs/05 §7 配额与成本的 MVP 形态)。"""

    records: list[UsageRecord] = field(default_factory=list)

    def add(self, record: UsageRecord) -> None:
        self.records.append(record)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(r.prompt_tokens for r in self.records)

    @property
    def total_completion_tokens(self) -> int:
        return sum(r.completion_tokens for r in self.records)

    def summary(self) -> dict:
        return {
            "calls": len(self.records),
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens,
            "reasoning_tokens": sum(r.reasoning_tokens for r in self.records),
            "seconds": round(sum(r.seconds for r in self.records), 2),
            "by_purpose": _group(self.records),
        }


def _group(records: list[UsageRecord]) -> dict:
    out: dict[str, dict] = {}
    for record in records:
        bucket = out.setdefault(
            record.purpose,
            {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "retries": 0},
        )
        bucket["calls"] += 1
        bucket["prompt_tokens"] += record.prompt_tokens
        bucket["completion_tokens"] += record.completion_tokens
        bucket["retries"] += max(0, record.attempts - 1)
    return out


class LLMClient:
    def __init__(self, config: LLMConfig, ledger: UsageLedger | None = None):
        self.config = config
        self.ledger = ledger or UsageLedger()

    # -- 底层调用 ---------------------------------------------------------- #
    def _post(self, payload: dict) -> dict:
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
        )
        request.add_header("Authorization", f"Bearer {self.config.api_key}")
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise LLMError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"无法连接 {self.config.base_url}:{exc}") from exc

    def chat(
        self,
        system: str,
        user: str,
        purpose: str,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> str:
        """调用一次并返回正文字符串。失败按指数退避重试(docs/06 §4)。"""
        self.config.require_key()
        payload = {
            "model": model or self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": (
                temperature
                if temperature is not None
                else self.config.compose_temperature
            ),
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        started = time.time()
        budget = payload["max_tokens"]
        for attempt in range(1, self.config.max_attempts + 1):
            payload["max_tokens"] = budget
            try:
                body = self._post(payload)
                choice = body["choices"][0]
                content = choice["message"].get("content")
                finish = choice.get("finish_reason")
                usage = body.get("usage") or {}
                details = usage.get("completion_tokens_details") or {}
                self.ledger.add(
                    UsageRecord(
                        purpose=purpose,
                        model=payload["model"],
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                        reasoning_tokens=details.get("reasoning_tokens", 0) or 0,
                        attempts=attempt,
                        seconds=time.time() - started,
                    )
                )
                if finish == "length":
                    # 推理模型会把大量预算花在 reasoning 上,容易截断。放大预算后重试。
                    budget = min(int(budget * 1.5), MAX_TOKEN_CEILING)
                    raise LLMError(
                        f"输出被 max_tokens 截断(已用 {usage.get('completion_tokens')} tokens,"
                        f"其中推理 {details.get('reasoning_tokens')});下次预算放大到 {budget}"
                    )
                if not content:
                    raise LLMError(
                        f"返回内容为空(finish_reason={finish}),通常是 max_tokens 不足"
                    )
                return content
            except (LLMError, KeyError, ValueError) as exc:
                last_error = exc
                if attempt < self.config.max_attempts:
                    time.sleep(BACKOFF_SECONDS * (2 ** (attempt - 1)))
        raise LLMError(f"{purpose} 调用失败,已重试 {self.config.max_attempts} 次:{last_error}")

    def chat_json(self, system: str, user: str, purpose: str, **kwargs) -> dict:
        """要求返回纯 JSON。

        docs/06 §4:输出必须为纯 JSON,解析失败按指数退避重试(最多 3 次)。
        注意重试必须发生在**解析层**,否则一次截断就足以让整条 S2 失败。
        """
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                raw = self.chat(system, user, purpose, json_mode=True, **kwargs)
                return parse_json_object(raw)
            except (LLMError, ValueError) as exc:
                last_error = exc
                if attempt < self.config.max_attempts:
                    time.sleep(BACKOFF_SECONDS * (2 ** (attempt - 1)))
        raise LLMError(
            f"{purpose} 连续 {self.config.max_attempts} 次未能拿到合法 JSON:{last_error}"
        )


def parse_json_object(raw: str) -> dict:
    """从模型输出里取出 JSON 对象。容忍 ```json 包裹与前后说明文字。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    candidate = text
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"模型输出不是合法 JSON:{raw[:300]}")
        candidate = text[start : end + 1]
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"模型输出 JSON 被截断或格式错误({exc});输出长度 {len(raw)} 字符"
            ) from exc

    if not isinstance(value, dict):
        raise LLMError(f"模型输出不是 JSON 对象:{raw[:300]}")
    return value
