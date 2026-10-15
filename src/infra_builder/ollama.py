"""Optional Ollama-backed configuration assistant for validated configuration drafts."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from pydantic import ValidationError

from infra_builder.models import InfraConfig

DEFAULT_MODEL = "qwen2.5:1.5b"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"


class OllamaError(RuntimeError):
    """Raised when the optional local Ollama service cannot provide a valid config."""


class OllamaAssistant:
    """Generate a validated InfraConfig from a natural-language prompt."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _build_prompt(self, prompt: str) -> str:
        return (
            "You are generating a configuration for the infra-builder project. "
            "Return only a JSON object that validates as an InfraConfig. "
            "Use only lowercase field names and values that match the schema. "
            "Do not include markdown fences or explanatory text. "
            "User request: "
            f"{prompt}"
        )

    def _call(self, prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": self._build_prompt(prompt),
            "stream": False,
            "format": "json",
        }
        url = f"{self.base_url}/api/generate"
        req = request.Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except error.URLError as exc:
            raise OllamaError(
                f"Ollama is unavailable at '{self.base_url}': {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise OllamaError(
                "Ollama request to "
                f"'{self.base_url}' timed out after {self.timeout} seconds"
            ) from exc

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise OllamaError(
                "Ollama returned invalid JSON instead of a config draft"
            ) from exc

        response_text = data.get("response")
        if not isinstance(response_text, str) or not response_text.strip():
            raise OllamaError("Ollama did not return a config draft")
        return response_text

    def generate_config(self, prompt: str) -> InfraConfig:
        """Ask Ollama for a config draft and validate it against the project schema."""
        response_text = self._call(prompt)
        try:
            candidate = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise OllamaError(
                "Ollama returned invalid JSON instead of a config draft"
            ) from exc

        if not isinstance(candidate, dict):
            raise OllamaError("Ollama response must be a JSON object")

        try:
            return InfraConfig.model_validate(candidate)
        except ValidationError as exc:
            details = "\n".join(
                f"  - {'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                for item in exc.errors()
            )
            raise OllamaError(f"AI config draft failed validation:\n{details}") from exc


def generate_config_from_prompt(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 30.0,
) -> InfraConfig:
    """Convenience wrapper for a one-shot Ollama-backed configuration generation."""
    return OllamaAssistant(
        model=model,
        base_url=base_url,
        timeout=timeout,
    ).generate_config(prompt)


__all__ = ["OllamaAssistant", "OllamaError", "generate_config_from_prompt"]
