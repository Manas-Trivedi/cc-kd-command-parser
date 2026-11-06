"""Service tests with a stub model (no download, no weights)."""
import importlib
import os
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "service"))

GOOD = '{"intent": "weather_query", "date": null, "time": null, "place": "london"}'


def make_app(monkeypatch, raw_output, fault="0", loaded=True):
    env = {"MODEL_URI": "s3://models/v1/", "MODEL_VERSION": "v1", "S3_ENDPOINT": "http://none:8333",
           "MAX_NEW_TOKENS": "64", "FAULT_INJECT": fault, "POD_NAME": "pod-a",
           "SCHEMA_PATH": os.path.join(ROOT, "data", "schema.json")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import app as app_module
    app_module = importlib.reload(app_module)
    monkeypatch.setattr(app_module.inference, "generate", lambda *a, **k: raw_output)
    if loaded:
        app_module.state.update(tok=object(), model=object())
    return TestClient(app_module.app)  # not used as a context manager: no startup download


def test_parse_returns_object_and_headers(monkeypatch):
    c = make_app(monkeypatch, GOOD)
    r = c.post("/parse", json={"text": "what is the weather in london"})
    assert r.status_code == 200
    assert r.json() == {"intent": "weather_query", "date": None, "time": None, "place": "london"}
    assert r.headers["X-Model-Version"] == "v1"
    assert r.headers["X-Served-By"] == "pod-a"


@pytest.mark.parametrize("raw", [
    "not json",
    '{"intent": "weather_query", "date": null, "time": null}',                         # missing key
    '{"intent": "weather_query", "date": null, "time": null, "place": null, "x": 1}',  # extra key
    '{"intent": "dance", "date": null, "time": null, "place": null}',                  # bad intent
    '{"intent": "weather_query", "date": 5, "time": null, "place": null}',             # wrong type
    '{"intent": "weather_query", "date": "tomor',                                      # cut off
])
def test_invalid_output_is_500(monkeypatch, raw):
    c = make_app(monkeypatch, raw)
    r = c.post("/parse", json={"text": "x"})
    assert r.status_code == 500
    assert r.json() == {"error": "invalid_model_output"}


def test_fault_inject(monkeypatch):
    c = make_app(monkeypatch, GOOD, fault="1")
    r = c.post("/parse", json={"text": "what is the weather in london"})
    assert r.status_code == 500
    assert r.text == '{"error":"invalid_model_output"}'


def test_probes(monkeypatch):
    c = make_app(monkeypatch, GOOD, loaded=False)
    assert c.get("/healthz").status_code == 200
    assert c.get("/readyz").status_code == 503
    import app as app_module
    app_module.state.update(tok=object(), model=object())
    assert c.get("/readyz").status_code == 200
