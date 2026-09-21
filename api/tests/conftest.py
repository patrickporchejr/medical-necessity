import json
from unittest.mock import MagicMock

import pytest

from app import observability
from app.config import Settings


class RecordingSession:
    """A stand-in for the HTTP session under the LangSmith client: keeps every request body."""

    headers: dict = {}

    def __init__(self):
        self.requests: list[tuple[str, str, dict]] = []

    def request(self, method, url, **kwargs):
        data = kwargs.get("data")
        if isinstance(data, (bytes, bytearray)):
            try:
                self.requests.append((method, url, json.loads(bytes(data))))
            except ValueError:
                pass
        response = MagicMock(status_code=200, text="{}", content=b"{}", headers={})
        response.json.return_value = {}
        return response

    def close(self):
        pass

    def mount(self, *args, **kwargs):
        pass


class Captured:
    """The runs LangSmith would have received, create and update calls merged per run."""

    def __init__(self, session: RecordingSession):
        self._session = session

    @property
    def runs(self) -> list[dict]:
        merged: dict[str, dict] = {}
        for method, url, body in self._session.requests:
            if not url.rstrip("/").split("/runs")[-1].strip("/") and method == "POST":
                merged[body["id"]] = dict(body)
            elif "/runs/" in url and method == "PATCH":
                merged.setdefault(body["id"], {}).update(body)
        return list(merged.values())

    def named(self, name: str) -> dict:
        [run] = [r for r in self.runs if r["name"] == name]
        return run

    @staticmethod
    def metadata(run: dict) -> dict:
        return run.get("extra", {}).get("metadata", {})

    def clear(self) -> None:
        self._session.requests.clear()


@pytest.fixture
def ls(monkeypatch):
    """Tracing on, through the real metadata-only client, with nothing leaving the process."""
    session = RecordingSession()
    client = observability.build_client(
        Settings(_env_file=None, langsmith_api_key="test-key"), session=session, auto_batch_tracing=False
    )
    monkeypatch.setattr(observability, "_client", client)
    monkeypatch.setattr(observability, "_project", "test")
    with observability.tracing():
        yield Captured(session)
