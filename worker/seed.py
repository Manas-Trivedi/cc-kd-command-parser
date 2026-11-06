"""Seed object storage (runs in-cluster as a Job during `make up`).

1. Create the buckets `datasets` and `models`.
2. Upload commands_pool.jsonl to s3://datasets/commands_pool.jsonl.
3. If SEED_MODELS is set (e.g. "v1,v2"), download model-<v>.tar.gz from the GitHub Release,
   check it against model-<v>.sha256 from the same release, and unpack it to s3://models/<v>/.
"""
import hashlib
import io
import os
import tarfile
import urllib.request

import boto3
from botocore.exceptions import ClientError

RELEASE_URL = os.environ.get(
    "RELEASE_URL", "https://github.com/Manas-Trivedi/cc-kd-command-parser/releases/download")


def fetch(url):
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read()


def main():
    s3 = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"], region_name="us-east-1")
    for b in ("datasets", "models"):
        try:
            s3.create_bucket(Bucket=b)
            print(f"created bucket {b}")
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
                raise
            print(f"bucket {b} exists")
    s3.upload_file("/app/commands_pool.jsonl", "datasets", "commands_pool.jsonl")
    print("uploaded s3://datasets/commands_pool.jsonl")

    for v in filter(None, os.environ.get("SEED_MODELS", "").split(",")):
        blob = fetch(f"{RELEASE_URL}/{v}/model-{v}.tar.gz")
        expected = fetch(f"{RELEASE_URL}/{v}/model-{v}.sha256").decode().split()[0]
        actual = hashlib.sha256(blob).hexdigest()
        if actual != expected:
            raise SystemExit(f"checksum mismatch for model-{v}.tar.gz: {actual} != {expected}")
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            for m in tar.getmembers():
                if not m.isfile():
                    continue
                name = os.path.basename(m.name)
                s3.put_object(Bucket="models", Key=f"{v}/{name}", Body=tar.extractfile(m).read())
                print(f"uploaded s3://models/{v}/{name}")
        s3.put_object(Bucket="models", Key=f"{v}/model.sha256", Body=f"{actual}  model-{v}.tar.gz\n".encode())
        print(f"model {v} seeded, sha256 {actual}")


if __name__ == "__main__":
    main()
