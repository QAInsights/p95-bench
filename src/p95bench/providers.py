"""Direct provider APIs and explicit per-million-token pricing."""

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from http.client import HTTPException
from typing import Protocol
from urllib.error import HTTPError, URLError

DEFAULT_OUTPUT_TOKENS = 64_000
OUTPUT_LIMIT_FIELDS = ("max_tokens", "max_completion_tokens", "maxOutputTokens")


@dataclass
class Response:
    text: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    latency_seconds: float = 0


class Provider(Protocol):
    def send(self, model: str, messages: list[dict[str, str]]) -> Response: ...


class ProviderUnavailable(RuntimeError):
    pass


class StreamInterrupted(ConnectionError):
    pass


class SpendLimitExceeded(RuntimeError):
    pass


@dataclass
class GeminiResponse(Response):
    thinking_tokens: int = 0
    finish_reason: str = ""

    @property
    def thinking_exhausted(self):
        return not self.text.strip() and self.thinking_tokens > 0 and self.finish_reason == "MAX_TOKENS"


@dataclass
class MuseResponse(Response):
    reasoning_tokens: int = 0
    finish_reason: str = ""

    @property
    def thinking_exhausted(self):
        return not self.text.strip() and self.reasoning_tokens > 0 and self.finish_reason == "length"


@dataclass
class DeepSeekResponse(Response):
    reasoning_tokens: int = 0
    finish_reason: str = ""

    @property
    def thinking_exhausted(self):
        return not self.text.strip() and self.reasoning_tokens > 0 and self.finish_reason == "length"


@dataclass
class AnthropicResponse(Response):
    finish_reason: str = ""

    @property
    def thinking_exhausted(self):
        return not self.text.strip() and self.finish_reason == "max_tokens"


@dataclass(frozen=True)
class Price:
    input: float
    cached: float
    output: float

    def cost(self, response: Response) -> float:
        cached = max(0, min(response.cached_tokens, response.input_tokens))
        return ((response.input_tokens - cached) * self.input
                + cached * self.cached
                + response.output_tokens * self.output) / 1_000_000


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    key_env: str
    prices: dict[str, Price]
    temperature: float | None = 0
    extra: dict = field(default_factory=dict)
    timeout_seconds: float = 600
    max_retries: int = 0
    cost_note: str | None = None
    pricing_source: str | None = None
    transport_retries: int = 2
    reasoning_mode: str = "provider-default"
    model_output_limits: dict[str, int] = field(default_factory=dict)

    def output_cap(self, model=None):
        cap = next((self.extra[key] for key in OUTPUT_LIMIT_FIELDS
                    if self.extra.get(key) is not None), DEFAULT_OUTPUT_TOKENS)
        return min(cap, self.model_output_limits.get(model, cap))

    def request_extra(self, model):
        return {**self.extra, **{key: self.output_cap(model) for key in OUTPUT_LIMIT_FIELDS
                                if key in self.extra}}


PROVIDERS = {
    "meta": ProviderConfig(
        "https://api.meta.ai/v1", "MODEL_API_KEY",
        {"muse-wonder-1.0": Price(4, .2, 20),
         "muse-spark-1.3": Price(1.25, .15, 4.25),
         "muse-spark-1.2": Price(1.25, .15, 4.25),
         "muse-ember-1.0": Price(.1, .004, .5)},
        temperature=None,
        extra={"max_completion_tokens": DEFAULT_OUTPUT_TOKENS, "stream": True,
               "stream_options": {"include_usage": True}},
        timeout_seconds=600,
        max_retries=5,
        cost_note="USD · list-price estimate (includes reasoning tokens).",
        pricing_source="https://dev.meta.ai/products/meta-model-api",
        reasoning_mode="provider-default",
    ),
    "gemini": ProviderConfig(
        "https://generativelanguage.googleapis.com/v1beta", "GEMINI_API_KEY",
        {"gemini-4-carbon": Price(2, .1, 10),
         "gemini-4-argon": Price(2, .1, 10),
         "gemini-4-hydrogen": Price(1.5, .075, 7.5),
         "gemini-3.8-flash": Price(.75, .075, 3.75)},
        temperature=None,
        extra={"maxOutputTokens": DEFAULT_OUTPUT_TOKENS},
        timeout_seconds=600,
        max_retries=5,
        cost_note="USD · list-price estimate (includes thinking tokens).",
        pricing_source="https://ai.google.dev/gemini-api/docs/pricing",
        reasoning_mode="provider-default",
    ),
    "qwen": ProviderConfig(
        "https://api-inference.modelscope.ai/v1", "MODELSCOPE_API_KEY",
        {"Qwen-Ambassador/Qwen4-Max": Price(1.5, .075, 4.5),
         "Qwen-Ambassador/Qwen3.8-Max": Price(2, .2, 6),
         "Qwen-Ambassador/Qwen3.8-Flash-Next": Price(.15, .015, .47),
         "Qwen-Ambassador/Qwen3.7-Max": Price(2.5, .25, 7.5)},
        temperature=None,
        extra={"enable_thinking": True, "reasoning_effort": "high",
               "max_completion_tokens": DEFAULT_OUTPUT_TOKENS, "stream": True,
               "stream_options": {"include_usage": True}},
        timeout_seconds=600,
        max_retries=5,
        cost_note="List-price estimate, run via ModelScope free tier.",
        pricing_source="https://www.alibabacloud.com/help/en/model-studio/model-pricing",
        reasoning_mode="thinking-high",
    ),
    "deepseek": ProviderConfig(
        "https://api.deepseek.com", "DEEPSEEK_API_KEY",
        {"deepseek-flash": Price(0.30, 0.006, 1.20),
         "deepseek-v4-pro": Price(1.32, 0.044, 3.96)},
        temperature=None,
        extra={"thinking": {"type": "enabled"}, "reasoning_effort": "high",
               "max_tokens": DEFAULT_OUTPUT_TOKENS},
        max_retries=3,
        reasoning_mode="thinking-high",
    ),
    "anthropic": ProviderConfig(
        "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY",
        {"claude-sonnet-5-5": Price(2, 2, 10),
         "claude-opus-5-5": Price(4, 4, 20)},
        temperature=None,
        extra={"max_tokens": DEFAULT_OUTPUT_TOKENS, "stream": True,
               "thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}},
        max_retries=3,
        reasoning_mode="thinking-high",
    ),
    "thinkingmachines": ProviderConfig(
        "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1", "TINKER_API_KEY",
        {"thinkingmachines/Inkling": Price(1.87, 0.374, 4.68),
         "thinkingmachines/Inkling-Small": Price(0.58, 0.116, 1.44),
         "thinkingmachines/Inkling-2-Small": Price(0.30, 0.03, 1.00)},
        extra={"max_tokens": DEFAULT_OUTPUT_TOKENS},
        timeout_seconds=600,
        max_retries=3,
        reasoning_mode="provider-default",
    ),
    "openai": ProviderConfig(
        "https://api.openai.com/v1", "OPENAI_API_KEY",
        {"gpt-6.2-sol": Price(2, 2, 10),
         "gpt-6.2-astra": Price(8, 8, 40)},
        temperature=None,
        extra={"max_completion_tokens": DEFAULT_OUTPUT_TOKENS},
        max_retries=3,
        reasoning_mode="provider-default",
    ),
}


def gemini_prices(run_date: datetime) -> dict[str, Price]:
    if run_date.tzinfo is None or run_date.utcoffset() is None:
        raise ValueError("run_date must be timezone-aware")
    day = run_date.astimezone(UTC).date().isoformat()
    prices = dict(PROVIDERS["gemini"].prices)
    if day >= "2026-12-01":
        prices.update({"gemini-4-carbon": Price(4, .2, 20),
                       "gemini-4-argon": Price(4, .2, 20)})
    if day >= "2027-01-01":
        prices["gemini-3.8-flash"] = Price(1.5, .15, 7.5)
    return prices


class OpenAICompatible:
    def __init__(self, config: ProviderConfig):
        self.config = config

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                "User-Agent": "p95bench/0.1.0"}

    def _request_once(self, request):
        body = json.loads(request.data) if request.data is not None else {}
        with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
            if body.get("stream") is not True:
                return json.load(response)
            return self._stream_response(response)

    def _stream_response(self, response):
        text, reasoning = [], []
        finish_reason = usage = None
        for raw_line in response:
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError as error:
                raise StreamInterrupted(f"Malformed stream event: {error}") from error
            if "error" in chunk:
                error = chunk["error"]
                message = error.get("message", error) if isinstance(error, dict) else error
                raise ProviderUnavailable(f"Stream error: {message}")
            if "usage" in chunk:
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content") is not None:
                    text.append(delta["content"])
                if delta.get("reasoning_content") is not None:
                    reasoning.append(delta["reasoning_content"])
                if choice.get("finish_reason") is not None:
                    finish_reason = choice["finish_reason"]
        if usage is None:
            raise StreamInterrupted("Streamed response ended without usage")
        return {"choices": [{"message": {
            "content": "".join(text), "reasoning_content": "".join(reasoning)},
            "finish_reason": finish_reason}], "usage": usage}

    def _request(self, path: str, body: dict | None = None) -> dict:
        key = os.environ.get(self.config.key_env)
        if not key:
            raise ValueError(f"Set {self.config.key_env}")
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=self._headers(key),
        )
        measured = getattr(self, "measure_transport", False)
        if measured:
            self.transport_attempts, self.uncertain_attempts, self.backoff_seconds = 0, 0, 0
            self.response_latency_seconds = None
        http_retries = 0
        transport_retries = 0
        request_count = 0
        while True:
            request_count += 1
            try:
                if measured:
                    self.transport_attempts += 1
                    started = time.perf_counter()
                result = self._request_once(request)
                if measured:
                    self.response_latency_seconds = time.perf_counter() - started
                return result
            except HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504, 529}:
                    error.close()
                    raise
                if http_retries >= self.config.max_retries:
                    status = error.code
                    error.close()
                    raise ProviderUnavailable(
                        f"No response after {request_count} requests (HTTP {status})"
                    ) from None
                retry_after = error.headers.get("Retry-After")
                error.close()
                try:
                    delay = float(retry_after) if retry_after is not None else 2 ** http_retries
                except ValueError:
                    delay = 2 ** http_retries
                delay = max(0, min(delay, 60))
                http_retries += 1
                if measured:
                    self.backoff_seconds += delay
                time.sleep(delay)
            except (URLError, TimeoutError, HTTPException, ConnectionError) as error:
                if measured:
                    self.uncertain_attempts += 1
                if transport_retries >= self.config.transport_retries:
                    raise ProviderUnavailable(
                        f"No response after {request_count} requests ({type(error).__name__})"
                    ) from None
                delay = 2 ** transport_retries
                transport_retries += 1
                if measured:
                    self.backoff_seconds += delay
                time.sleep(delay)

    def models(self) -> list[str]:
        return [item["id"] for item in self._request("/models")["data"]]

    def send(self, model: str, messages: list[dict[str, str]]) -> Response:
        body = {"model": model, "messages": messages, **self.config.request_extra(model)}
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature
        start = time.perf_counter()
        data = self._request("/chat/completions", body)
        elapsed = time.perf_counter() - start
        usage = data["usage"]
        cached = usage.get("prompt_cache_hit_tokens",
                           (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        choice = data["choices"][0]
        if self.config.reasoning_mode.startswith("thinking"):
            reasoning = ((usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
                         or 0)
            return DeepSeekResponse(
                choice["message"].get("content") or "", usage["prompt_tokens"],
                usage["completion_tokens"], cached, elapsed, reasoning,
                choice.get("finish_reason", ""))
        return Response(choice["message"]["content"] or "",
                        usage["prompt_tokens"], usage["completion_tokens"], cached, elapsed)


class Muse(OpenAICompatible):
    def __init__(self, config, spend_limit=None):
        super().__init__(config)
        if spend_limit is not None and spend_limit <= 0:
            raise ValueError("spend_limit must be positive")
        self.spend_limit = spend_limit
        self.spent_upper_bound = 0

    def _request_once(self, request):
        if self.spend_limit is None or not request.full_url.endswith("/chat/completions"):
            return super()._request_once(request)
        body = json.loads(request.data)
        price = self.config.prices[body["model"]]
        maximum = price.cost(Response("", len(request.data) + 1024,
                                      body["max_completion_tokens"]))
        if self.spent_upper_bound + maximum > self.spend_limit:
            raise SpendLimitExceeded("Insufficient budget for another maximum-sized response")
        self.spent_upper_bound += maximum
        data = super()._request_once(request)
        usage = data["usage"]
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        actual = price.cost(Response("", usage["prompt_tokens"], usage["completion_tokens"], cached))
        self.spent_upper_bound += actual - maximum
        return data

    def send(self, model, messages):
        body = {"model": model, "messages": messages, **self.config.request_extra(model)}
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature
        if model == "muse-ember-1.0" and (
            len(json.dumps(body).encode()) + 1024 + body["max_completion_tokens"] >= 200_000
        ):
            raise ValueError("Muse Ember pricing is verified only for contexts below 200k tokens")
        start = time.perf_counter()
        data = self._request("/chat/completions", body)
        elapsed = time.perf_counter() - start
        usage = data["usage"]
        choice = data["choices"][0]
        reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        return MuseResponse(choice["message"]["content"] or "", usage["prompt_tokens"],
                            usage["completion_tokens"], cached, elapsed, reasoning,
                            choice.get("finish_reason", ""))


class Gemini(OpenAICompatible):
    def __init__(self, config, spend_limit=None):
        super().__init__(config)
        self.configure_run(datetime.now(UTC))
        if spend_limit is not None and spend_limit <= 0:
            raise ValueError("spend_limit must be positive")
        self.spend_limit = spend_limit
        self.spent_upper_bound = 0

    def configure_run(self, run_date, model=None, price=None):
        prices = {**self.config.prices, **gemini_prices(run_date)}
        if model is not None and price is not None:
            prices[model] = price
        self.config = replace(self.config, prices=prices)

    def _headers(self, key):
        return {"x-goog-api-key": key, "Content-Type": "application/json",
                "User-Agent": "p95bench/0.1.0"}

    @staticmethod
    def _usage(data):
        usage = data["usageMetadata"]
        thinking = usage.get("thoughtsTokenCount", 0) or 0
        return (usage["promptTokenCount"],
                (usage.get("candidatesTokenCount", 0) or 0) + thinking,
                usage.get("cachedContentTokenCount", 0) or 0, thinking)

    def _request_once(self, request):
        if self.spend_limit is None or not request.full_url.endswith(":generateContent"):
            return super()._request_once(request)
        model = urllib.parse.unquote(request.full_url.rsplit("/", 1)[1].split(":")[0])
        price = self.config.prices[model]
        body = json.loads(request.data)
        maximum = price.cost(Response("", len(request.data) + 1024,
                                      body["generationConfig"]["maxOutputTokens"]))
        if self.spent_upper_bound + maximum > self.spend_limit:
            raise SpendLimitExceeded("Insufficient budget for another maximum-sized response")
        self.spent_upper_bound += maximum
        data = super()._request_once(request)
        input_tokens, output_tokens, cached_tokens, _ = self._usage(data)
        actual = price.cost(Response("", input_tokens, output_tokens, cached_tokens))
        self.spent_upper_bound += actual - maximum
        return data

    def models(self):
        models, path = [], "/models"
        while True:
            data = self._request(path)
            models.extend(item["name"].removeprefix("models/") for item in data["models"])
            if not data.get("nextPageToken"):
                return models
            path = "/models?" + urllib.parse.urlencode({"pageToken": data["nextPageToken"]})

    def send(self, model, messages):
        config = self.config.request_extra(model)
        if self.config.temperature is not None:
            config["temperature"] = self.config.temperature
        body = {"contents": [{"role": "model" if m["role"] == "assistant" else "user",
                              "parts": [{"text": m["content"]}]}
                             for m in messages if m["role"] != "system"],
                "generationConfig": config}
        system = [m["content"] for m in messages if m["role"] == "system"]
        if system:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
        start = time.perf_counter()
        data = self._request("/models/" + urllib.parse.quote(model, safe="") + ":generateContent",
                             body)
        elapsed = time.perf_counter() - start
        candidate = (data.get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part["text"] for part in parts
                       if isinstance(part.get("text"), str) and not part.get("thought"))
        input_tokens, output_tokens, cached_tokens, thinking = self._usage(data)
        return GeminiResponse(text, input_tokens, output_tokens, cached_tokens, elapsed,
                              thinking, candidate.get("finishReason", ""))


class Anthropic(OpenAICompatible):
    def _stream_response(self, response):
        blocks, usage = {}, {}
        stopped = final_usage = False
        finish_reason = None
        for raw_line in response:
            try:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:].strip())
                kind = event.get("type")
                if kind == "error":
                    raise StreamInterrupted(f"Anthropic stream error: {event.get('error')}")
                if kind == "message_start":
                    usage.update(event["message"].get("usage", {}))
                elif kind == "content_block_start":
                    block = event["content_block"]
                    if block["type"] == "text":
                        blocks[event["index"]] = block.get("text", "")
                elif kind == "content_block_delta" and event["delta"]["type"] == "text_delta":
                    index = event["index"]
                    blocks[index] = blocks.get(index, "") + event["delta"]["text"]
                elif kind == "message_delta":
                    usage.update(event.get("usage", {}))
                    final_usage = "output_tokens" in event.get("usage", {})
                    finish_reason = event.get("delta", {}).get("stop_reason")
                elif kind == "message_stop":
                    stopped = True
                    break
            except (ValueError, UnicodeError, TypeError, KeyError, AttributeError) as error:
                raise StreamInterrupted(f"Malformed Anthropic stream event: {error}") from error
        if (not stopped or not final_usage
                or any(type(usage.get(key)) is not int or usage[key] < 0
                       for key in ("input_tokens", "output_tokens"))):
            raise StreamInterrupted("Anthropic stream ended without final usage or message_stop")
        return {"content": [{"type": "text", "text": blocks[key]} for key in sorted(blocks)],
                "usage": usage, "stop_reason": finish_reason}

    def _headers(self, key: str) -> dict[str, str]:
        return {"x-api-key": key, "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"}

    def models(self) -> list[str]:
        models = []
        path = "/models"
        while True:
            data = self._request(path)
            models.extend(item["id"] for item in data["data"])
            if not data.get("has_more", False):
                return models
            path = "/models?" + urllib.parse.urlencode({"after_id": data["last_id"]})

    def send(self, model: str, messages: list[dict[str, str]]) -> Response:
        body = {"model": model,
                "messages": [message for message in messages if message["role"] != "system"],
                **self.config.request_extra(model)}
        system = [message["content"] for message in messages if message["role"] == "system"]
        if system:
            body["system"] = "\n\n".join(system)
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature
        start = time.perf_counter()
        data = self._request("/messages", body)
        elapsed = time.perf_counter() - start
        text = "".join(block["text"] for block in data["content"] if block["type"] == "text")
        usage = data["usage"]
        return AnthropicResponse(text, usage["input_tokens"], usage["output_tokens"],
                                 latency_seconds=elapsed, finish_reason=data.get("stop_reason", ""))


def get_provider(name: str) -> Provider:
    adapter = {"anthropic": Anthropic, "gemini": Gemini, "meta": Muse}.get(name, OpenAICompatible)
    config = PROVIDERS[name]
    if name == "qwen":
        config = replace(config, base_url=os.environ.get("QWEN_BASE_URL", config.base_url))
    return adapter(config)
