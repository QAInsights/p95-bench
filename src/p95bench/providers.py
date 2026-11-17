"""Direct provider APIs and explicit per-million-token pricing."""

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Response:
    text: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    latency_seconds: float = 0


class Provider(Protocol):
    def send(self, model: str, messages: list[dict[str, str]]) -> Response: ...


@dataclass(frozen=True)
class Price:
    input: float
    cached: float
    output: float

    def cost(self, response: Response) -> float:
        return ((response.input_tokens - response.cached_tokens) * self.input
                + response.cached_tokens * self.cached
                + response.output_tokens * self.output) / 1_000_000


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    key_env: str
    prices: dict[str, Price]
    temperature: float | None = 0
    extra: dict = field(default_factory=dict)


PROVIDERS = {
    "deepseek": ProviderConfig(
        "https://api.deepseek.com", "DEEPSEEK_API_KEY",
        {"deepseek-flash": Price(0.30, 0.006, 1.20),
         "deepseek-v4-pro": Price(1.32, 0.044, 3.96)},
        extra={"thinking": {"type": "disabled"}, "max_tokens": 8192},
    ),
    "anthropic": ProviderConfig(
        "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY",
        {"claude-sonnet-5-5": Price(2, 2, 10),
         "claude-opus-5-5": Price(4, 4, 20)},
        temperature=None,
        extra={"max_tokens": 8192},
    ),
    "thinkingmachines": ProviderConfig(
        "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1", "TINKER_API_KEY",
        {"thinkingmachines/Inkling": Price(1.87, 0.374, 4.68),
         "thinkingmachines/Inkling-Small": Price(0.58, 0.116, 1.44),
         "thinkingmachines/Inkling-2-Small": Price(0.30, 0.03, 1.00)},
        extra={"max_tokens": 8192},
    ),
}


class OpenAICompatible:
    def __init__(self, config: ProviderConfig):
        self.config = config

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _request(self, path: str, body: dict | None = None) -> dict:
        key = os.environ.get(self.config.key_env)
        if not key:
            raise ValueError(f"Set {self.config.key_env}")
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=self._headers(key),
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.load(response)

    def models(self) -> list[str]:
        return [item["id"] for item in self._request("/models")["data"]]

    def send(self, model: str, messages: list[dict[str, str]]) -> Response:
        body = {"model": model, "messages": messages, **self.config.extra}
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature
        start = time.perf_counter()
        data = self._request("/chat/completions", body)
        elapsed = time.perf_counter() - start
        usage = data["usage"]
        cached = usage.get("prompt_cache_hit_tokens",
                           usage.get("prompt_tokens_details", {}).get("cached_tokens", 0))
        return Response(data["choices"][0]["message"]["content"] or "",
                        usage["prompt_tokens"], usage["completion_tokens"], cached, elapsed)


class Anthropic(OpenAICompatible):
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
                **self.config.extra}
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
        return Response(text, usage["input_tokens"], usage["output_tokens"],
                        latency_seconds=elapsed)


def get_provider(name: str) -> Provider:
    adapter = Anthropic if name == "anthropic" else OpenAICompatible
    return adapter(PROVIDERS[name])
