"""Command-parser inference service.

Endpoints
  POST /parse    {"text": "..."} -> the four-key JSON object (200), or {"error": "invalid_model_output"} (500)
  GET  /healthz  200 while the process is alive
  GET  /readyz   503 until the model is loaded, then 200

Configuration (environment only)
  MODEL_URI       s3://models/v1/          where the model and tokenizer live
  MODEL_VERSION   v1                       returned in X-Model-Version
  S3_ENDPOINT     http://seaweedfs.data.svc.cluster.local:8333
  MAX_NEW_TOKENS  64
  FAULT_INJECT    1 replaces the model output with "not json" (tests the validation)
  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY come from a Kubernetes Secret.
"""
import json
import logging
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import boto3
import jsonschema
import torch
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import inference

MODEL_URI = os.environ["MODEL_URI"]
MODEL_VERSION = os.environ["MODEL_VERSION"]
S3_ENDPOINT = os.environ["S3_ENDPOINT"]
MAX_NEW_TOKENS = int(os.environ["MAX_NEW_TOKENS"])
FAULT_INJECT = os.environ.get("FAULT_INJECT", "0") == "1"
POD_NAME = os.environ.get("POD_NAME") or socket.gethostname()
SCHEMA_PATH = os.environ.get("SCHEMA_PATH", str(Path(__file__).with_name("schema.json")))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("parser")

with open(SCHEMA_PATH, encoding="utf-8") as f:
    VALIDATOR = jsonschema.Draft7Validator(json.load(f)["json_schema"])

torch.set_num_threads(int(os.environ.get("TORCH_THREADS", "1")))
state = {"tok": None, "model": None, "error": None}
infer_lock = threading.Lock()
app = FastAPI()
HEADERS = {"X-Model-Version": MODEL_VERSION, "X-Served-By": POD_NAME}


def download_model(uri, dest):
    if not uri.startswith("s3://"):
        raise ValueError(f"MODEL_URI must start with s3://, got {uri}")
    bucket, _, prefix = uri[5:].partition("/")
    prefix = prefix.rstrip("/") + "/"
    s3 = boto3.client("s3", endpoint_url=S3_ENDPOINT, region_name="us-east-1")
    objs = s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
    if not objs:
        raise FileNotFoundError(f"nothing found at {uri}")
    total = 0
    for o in objs:
        rel = o["Key"][len(prefix):]
        if not rel or "/" in rel:
            continue
        s3.download_file(bucket, o["Key"], os.path.join(dest, rel))
        total += o["Size"]
        log.info("downloaded s3://%s/%s (%d bytes)", bucket, o["Key"], o["Size"])
    return total


def load_model():
    t0 = time.time()
    try:
        dest = tempfile.mkdtemp(prefix="model-")
        log.info("downloading model %s from %s", MODEL_URI, S3_ENDPOINT)
        size = download_model(MODEL_URI, dest)
        log.info("download finished: %.1f MB in %.1fs", size / 1e6, time.time() - t0)
        tok, model = inference.load(dest)
        inference.generate(tok, model, "set an alarm for seven am", 8)  # warm-up
        state["tok"], state["model"] = tok, model
        log.info("model %s ready after %.1fs", MODEL_VERSION, time.time() - t0)
    except Exception as e:  # readiness stays 503; liveness keeps passing so logs can be read
        state["error"] = repr(e)
        log.exception("model load failed")


@app.on_event("startup")
def startup():
    threading.Thread(target=load_model, daemon=True).start()


class ParseRequest(BaseModel):
    text: str


def validate(raw):
    """Return the parsed object if `raw` is valid per schema.json, else None."""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    errors = list(VALIDATOR.iter_errors(obj))
    return None if errors else obj


@app.post("/parse")
def parse(req: ParseRequest):
    if state["model"] is None:
        return JSONResponse({"error": "model_not_loaded"}, status_code=503, headers=HEADERS)
    with infer_lock:
        raw = inference.generate(state["tok"], state["model"], req.text, MAX_NEW_TOKENS)
    if FAULT_INJECT:
        raw = "not json"
    obj = validate(raw)
    if obj is None:
        log.warning("invalid model output for %r: %r", req.text, raw)
        return JSONResponse({"error": "invalid_model_output"}, status_code=500, headers=HEADERS)
    return JSONResponse(obj, status_code=200, headers=HEADERS)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    if state["model"] is None:
        return JSONResponse({"status": "loading", "error": state["error"]}, status_code=503)
    return {"status": "ready", "model_version": MODEL_VERSION}
