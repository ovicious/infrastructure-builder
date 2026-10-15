import io
import json
from urllib import error

from typer.testing import CliRunner

from infra_builder.cli import app
from infra_builder.ollama import OllamaAssistant, OllamaError

runner = CliRunner()


def test_ollama_assistant_generates_valid_config(monkeypatch) -> None:
    payload = {
        "project": {"name": "demo-api", "output_dir": "./generated/demo-api"},
        "runtime": {"type": "docker_compose"},
        "application": {
            "type": "python",
            "name": "api",
            "port": 8080,
            "image": "python:3.12",
        },
        "services": {"postgres": {"enabled": True}},
        "observability": {"metrics": True},
    }

    class FakeResponse:
        def __init__(self, payload: str) -> None:
            self._stream = io.BytesIO(payload.encode("utf-8"))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self._stream.close()
            return False

        def read(self) -> bytes:
            return self._stream.read()

    def fake_urlopen(request, timeout):
        assert request.full_url.endswith("/api/generate")
        assert json.loads(request.data.decode("utf-8"))["format"] == "json"
        return FakeResponse(json.dumps({"response": json.dumps(payload)}))

    monkeypatch.setattr("infra_builder.ollama.request.urlopen", fake_urlopen)

    config = OllamaAssistant(model="demo-model").generate_config("build me a demo")

    assert config.project.name == "demo-api"
    assert config.services.postgres.enabled is True


def test_ollama_missing_service_reports_actionable_error(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise error.URLError("not running")

    monkeypatch.setattr("infra_builder.ollama.request.urlopen", fake_urlopen)

    try:
        OllamaAssistant().generate_config("build me a demo")
    except OllamaError as exc:
        assert "Ollama is unavailable" in str(exc)
    else:
        raise AssertionError("OllamaError was not raised")


def test_cli_ai_configure_handles_invalid_json(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, payload: str) -> None:
            self._stream = io.BytesIO(payload.encode("utf-8"))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self._stream.close()
            return False

        def read(self) -> bytes:
            return self._stream.read()

    def fake_urlopen(request, timeout):
        return FakeResponse(json.dumps({"response": "not json"}))

    monkeypatch.setattr("infra_builder.ollama.request.urlopen", fake_urlopen)

    result = runner.invoke(
        app,
        ["ai-configure", "build me a demo", "--output", "infra.yaml"],
    )

    assert result.exit_code == 3
    assert "invalid json" in result.stderr.lower()
