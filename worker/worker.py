"""Teacher labelling worker.

Takes every command whose line number (from 0) % SHARD_COUNT == SHARD_INDEX,
asks the teacher (phi3.5 on Ollama) for the JSON, and writes the raw replies to
s3://datasets/teacher/shard-<SHARD_INDEX>.jsonl.

The shard object is re-uploaded every CHECKPOINT_EVERY commands, and on start-up
any ids already in it are skipped, so a restarted pod carries on where it stopped.
"""
import json
import os
import sys
import time

import boto3
import requests
from botocore.exceptions import ClientError

BUCKET = "datasets"
POOL_KEY = "commands_pool.jsonl"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
MODEL = os.environ.get("TEACHER_MODEL", "phi3.5")
PROMPT_FILE = os.environ.get("PROMPT_FILE", "/app/teacher_prompt.txt")
CHECKPOINT_EVERY = int(os.environ.get("CHECKPOINT_EVERY", "20"))
OPTIONS = {"temperature": 0, "seed": 42, "num_predict": 96}


def shard_lines(lines, index, count):
    """Lines whose 0-based position leaves remainder `index` when divided by `count`."""
    return [line for i, line in enumerate(lines) if i % count == index]


def ask_teacher(prompt_template, text, session):
    prompt = prompt_template.replace("{command}", text)
    for attempt in range(5):
        try:
            r = session.post(f"{OLLAMA_URL}/api/generate", timeout=300, json={
                "model": MODEL, "prompt": prompt, "stream": False, "format": "json", "options": OPTIONS,
            })
        except requests.ConnectionError as e:
            err = e
        else:
            if r.status_code == 200:
                return r.json()["response"]
            if 400 <= r.status_code < 500:
                r.raise_for_status()  # bad request will not fix itself
            err = RuntimeError(f"ollama HTTP {r.status_code}: {r.text[:200]}")
        wait = 2 ** attempt
        print(f"teacher call failed ({err}), retry in {wait}s", flush=True)
        time.sleep(wait)
    raise RuntimeError(f"teacher unreachable after 5 attempts: {err}")


def s3_client():
    return boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"], region_name="us-east-1")


def load_existing(s3, key):
    try:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return []
        raise
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def main():
    index = int(os.environ.get("SHARD_INDEX", os.environ.get("JOB_COMPLETION_INDEX", "")))
    count = int(os.environ["SHARD_COUNT"])
    if not 0 <= index < count:
        sys.exit(f"SHARD_INDEX {index} must be in [0, {count})")
    with open(PROMPT_FILE, encoding="utf-8") as f:
        template = f.read()

    s3 = s3_client()
    pool = s3.get_object(Bucket=BUCKET, Key=POOL_KEY)["Body"].read().decode().splitlines()
    pool = [line for line in pool if line.strip()]
    mine = [json.loads(line) for line in shard_lines(pool, index, count)]

    key = f"teacher/shard-{index}.jsonl"
    out = load_existing(s3, key)
    done = {r["id"] for r in out}
    print(f"shard {index}/{count}: {len(mine)} commands, {len(done)} already done", flush=True)

    def flush():
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out)
        s3.put_object(Bucket=BUCKET, Key=key, Body=body.encode())

    session = requests.Session()
    start = time.time()
    todo = [c for c in mine if c["id"] not in done]
    for n, cmd in enumerate(todo, 1):
        raw = ask_teacher(template, cmd["text"], session)
        out.append({"id": cmd["id"], "text": cmd["text"], "teacher_raw": raw})
        if n % CHECKPOINT_EVERY == 0:
            flush()
            rate = n / (time.time() - start)
            print(f"shard {index}: {len(out)}/{len(mine)} ({rate:.2f}/s)", flush=True)
    flush()
    print(f"shard {index}: finished, {len(out)} records in s3://{BUCKET}/{key}", flush=True)


if __name__ == "__main__":
    main()
