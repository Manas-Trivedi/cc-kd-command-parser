"""Clean the teacher shard outputs into datasets/v1/train.jsonl.

Every record goes through the checks below in this order; the first one it fails
is the reason it is counted under:
  (a) reply is not valid JSON
  (b) reply is not an object with exactly the keys intent, date, time, place
  (c) intent is not one of the eight
  (d) a slot is neither null nor text that appears in the command
Records that pass are then de-duplicated on the command text, keeping the smallest id (e).
The kept records are written in id order. The output is byte-for-byte deterministic,
so its SHA-256 checksum (train.sha256) can be reproduced by re-running this script.

Usage
  python worker/clean.py                         read s3://datasets/teacher/shard-*.jsonl, write s3://datasets/v1/
  python worker/clean.py --local DIR [--out DIR] read DIR/shard-*.jsonl, write to --out (default DIR)
"""
import argparse
import glob
import hashlib
import json
import os
import sys

KEYS = ["intent", "date", "time", "place"]
SLOTS = ["date", "time", "place"]
INTENTS = {
    "alarm_set", "alarm_query", "calendar_set", "calendar_query",
    "calendar_remove", "weather_query", "datetime_query", "recommendation_events",
}
BUCKET = "datasets"


def norm(s):
    return " ".join(s.lower().split())


def check(record):
    """Return (reason, parsed). reason is None when the record is good."""
    try:
        obj = json.loads(record["teacher_raw"].strip())
    except json.JSONDecodeError:
        return "a_invalid_json", None
    if not isinstance(obj, dict) or set(obj) != set(KEYS):
        return "b_wrong_keys", None
    if not isinstance(obj["intent"], str) or obj["intent"] not in INTENTS:
        return "c_bad_intent", None
    command = norm(record["text"])
    for k in SLOTS:
        v = obj[k]
        if v is None:
            continue
        if not isinstance(v, str) or not v.strip() or norm(v) not in command:
            return "d_slot_not_in_command", None
    return None, {k: obj[k] for k in KEYS}


def clean(records):
    """Return (kept_records, counts). `records` is every shard line, in any order."""
    counts = {"input": len(records), "a_invalid_json": 0, "b_wrong_keys": 0,
              "c_bad_intent": 0, "d_slot_not_in_command": 0, "e_duplicates": 0, "kept": 0}
    seen = set()
    kept = []
    for r in sorted(records, key=lambda r: r["id"]):
        reason, obj = check(r)
        if reason:
            counts[reason] += 1
            continue
        t = norm(r["text"])
        if t in seen:
            counts["e_duplicates"] += 1
            continue
        seen.add(t)
        kept.append({"id": r["id"], "text": r["text"], "teacher_raw": r["teacher_raw"],
                     "target": json.dumps(obj, ensure_ascii=False)})
    counts["kept"] = len(kept)
    return kept, counts


def serialise(kept):
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in kept).encode()


def parse_lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--local", help="directory holding shard-*.jsonl instead of S3")
    ap.add_argument("--out", help="output directory for --local mode (default: same as --local)")
    a = ap.parse_args()

    if a.local:
        records = []
        files = sorted(glob.glob(os.path.join(a.local, "shard-*.jsonl")))
        for path in files:
            with open(path, encoding="utf-8") as f:
                records += parse_lines(f.read())
    else:
        import boto3
        s3 = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"], region_name="us-east-1")
        listing = s3.list_objects_v2(Bucket=BUCKET, Prefix="teacher/shard-")
        files = sorted(o["Key"] for o in listing.get("Contents", []))
        records = []
        for key in files:
            records += parse_lines(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode())
    if not files:
        sys.exit("no shard files found")

    kept, counts = clean(records)
    data = serialise(kept)
    digest = hashlib.sha256(data).hexdigest()
    sha_line = f"{digest}  train.jsonl\n".encode()

    if a.local:
        out = a.out or a.local
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "train.jsonl"), "wb") as f:
            f.write(data)
        with open(os.path.join(out, "train.sha256"), "wb") as f:
            f.write(sha_line)
    else:
        s3.put_object(Bucket=BUCKET, Key="v1/train.jsonl", Body=data)
        s3.put_object(Bucket=BUCKET, Key="v1/train.sha256", Body=sha_line)

    print(f"shard files: {', '.join(os.path.basename(f) for f in files)}")
    print(json.dumps(counts, indent=2))
    rejected = sum(v for k, v in counts.items() if k not in ("input", "kept"))
    assert counts["input"] == rejected + counts["kept"]
    print(f"sha256 {digest}")


if __name__ == "__main__":
    main()
