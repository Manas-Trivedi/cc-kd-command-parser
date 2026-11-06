"""Tiny S3 helper for the laptop side (talks to SeaweedFS on localhost:8333).

  python scripts/s3.py mb BUCKET
  python scripts/s3.py put LOCAL_FILE BUCKET/KEY
  python scripts/s3.py get BUCKET/KEY LOCAL_FILE
  python scripts/s3.py putdir LOCAL_DIR BUCKET/PREFIX
  python scripts/s3.py getdir BUCKET/PREFIX LOCAL_DIR
  python scripts/s3.py ls BUCKET[/PREFIX]

Credentials come from the environment or from .secrets/s3.env (git-ignored).
"""
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent


def client():
    env_file = ROOT / ".secrets" / "s3.env"
    if "AWS_ACCESS_KEY_ID" not in os.environ and env_file.exists():
        for line in env_file.read_text().splitlines():
            k, _, v = line.partition("=")
            os.environ[k] = v
    return boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT", "http://localhost:8333"),
                        region_name="us-east-1")


def split(path):
    bucket, _, key = path.partition("/")
    return bucket, key


def main():
    cmd, *args = sys.argv[1:]
    s3 = client()
    if cmd == "mb":
        try:
            s3.create_bucket(Bucket=args[0])
            print(f"created bucket {args[0]}")
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
                raise
            print(f"bucket {args[0]} already exists")
    elif cmd == "put":
        b, k = split(args[1])
        s3.upload_file(args[0], b, k)
        print(f"uploaded {args[0]} -> s3://{b}/{k}")
    elif cmd == "get":
        b, k = split(args[0])
        Path(args[1]).parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(b, k, args[1])
        print(f"downloaded s3://{b}/{k} -> {args[1]}")
    elif cmd == "putdir":
        b, prefix = split(args[1])
        for f in sorted(Path(args[0]).rglob("*")):
            if f.is_file():
                key = f"{prefix.rstrip('/')}/{f.relative_to(args[0])}"
                s3.upload_file(str(f), b, key)
                print(f"uploaded {f} -> s3://{b}/{key}")
    elif cmd == "getdir":
        b, prefix = split(args[0])
        for o in s3.list_objects_v2(Bucket=b, Prefix=prefix).get("Contents", []):
            dest = Path(args[1]) / o["Key"][len(prefix):].lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(b, o["Key"], str(dest))
            print(f"downloaded s3://{b}/{o['Key']} -> {dest}")
    elif cmd == "ls":
        b, prefix = split(args[0])
        for o in s3.list_objects_v2(Bucket=b, Prefix=prefix).get("Contents", []):
            print(f"{o['Size']:>12}  s3://{b}/{o['Key']}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
