#!/usr/bin/env bash
# Task 3 evidence: run the image with 1 CPU / 1 GiB, check the API, send 50 sequential
# requests while sampling `docker stats`, then check FAULT_INJECT=1.
# Needs SeaweedFS reachable on localhost:8333 (the k3d NodePort) and .secrets/s3.env.
set -euo pipefail
cd "$(dirname "$0")/.."
IMG=kd-parser:1.0
OUT=results/task3
mkdir -p $OUT
run() {  # $1 = name, $2 = FAULT_INJECT
  docker rm -f "$1" >/dev/null 2>&1 || true
  docker run -d --name "$1" -p 127.0.0.1:8000:8000 --cpus 1 --memory 1g \
    --env-file .secrets/s3.env -e MODEL_URI=s3://models/v1/ -e MODEL_VERSION=v1 \
    -e S3_ENDPOINT=http://host.docker.internal:8333 -e MAX_NEW_TOKENS=64 -e FAULT_INJECT="$2" $IMG >/dev/null
}
post() { curl -s -D - -X POST localhost:8000/parse -H 'content-type: application/json' -d "{\"text\": \"$1\"}"; echo; }

{
echo "## image"
docker images $IMG --format '{{.Repository}}:{{.Tag}}  size {{.Size}}'
echo "weight files inside the image (expect none):"
docker run --rm --entrypoint sh $IMG -c 'find / -xdev \( -name "*.safetensors" -o -name "pytorch_model*.bin" \) 2>/dev/null | wc -l'

echo; echo "## start with --cpus 1 --memory 1g"
run parser-test 0
echo "readyz while loading: HTTP $(curl -s -o /dev/null -w '%{http_code}' localhost:8000/readyz || echo down)"
until curl -sf localhost:8000/readyz >/dev/null; do sleep 0.5; done
echo "readyz after load:    HTTP $(curl -s -o /dev/null -w '%{http_code}' localhost:8000/readyz)"
echo "healthz:              HTTP $(curl -s -o /dev/null -w '%{http_code}' localhost:8000/healthz)"
docker logs parser-test 2>&1 | grep -E 'downloading|download finished|ready after'
docker inspect parser-test --format 'limits: NanoCpus={{.HostConfig.NanoCpus}} Memory={{.HostConfig.Memory}}'

echo; echo "## one request with headers"
post "set an alarm for seven thirty am tomorrow"

echo "## 50 sequential requests (validation commands), sampling docker stats"
(while true; do docker stats --no-stream --format '{{.MemUsage}} {{.CPUPerc}}' parser-test; done) > $OUT/stats_samples.txt 2>/dev/null &
sampler=$!
python3 - <<'EOF'
import json, time, urllib.request, collections
texts = [json.loads(l)["text"] for l in open("data/validation.jsonl")][:50]
codes, lat = collections.Counter(), []
for t in texts:
    req = urllib.request.Request("http://localhost:8000/parse", data=json.dumps({"text": t}).encode(),
                                 headers={"content-type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            codes[r.status] += 1
    except urllib.error.HTTPError as e:
        codes[e.code] += 1
    lat.append(time.time() - t0)
lat.sort()
print(f"status codes: {dict(codes)}; latency median {lat[len(lat)//2]*1000:.0f} ms, max {lat[-1]*1000:.0f} ms")
EOF
kill $sampler
python3 - <<'EOF'
import re
vals = []
for line in open("results/task3/stats_samples.txt"):
    m = re.match(r"([\d.]+)(MiB|GiB)", line.strip())
    if m:
        vals.append(float(m.group(1)) * (1024 if m.group(2) == "GiB" else 1))
print(f"docker stats samples: {len(vals)}; peak memory {max(vals):.1f} MiB of 1024 MiB limit")
EOF
echo "cgroup memory.peak (exact peak since start): $(docker exec parser-test cat /sys/fs/cgroup/memory.peak | awk '{printf "%.1f MiB", $1/1048576}')"
echo "process peak RSS (VmHWM of uvicorn, PID 1): $(docker exec parser-test grep VmHWM /proc/1/status | awk '{printf "%.1f MiB", $2/1024}')"
echo "  (memory.peak above also counts the page cache of the downloaded model files, which the kernel can reclaim)"
echo "OOM killed: $(docker inspect parser-test --format '{{.State.OOMKilled}}'), restarts: $(docker inspect parser-test --format '{{.RestartCount}}'), running: $(docker inspect parser-test --format '{{.State.Running}}')"

echo; echo "## FAULT_INJECT=1"
docker rm -f parser-test >/dev/null 2>&1 || true
run parser-fault 1
until curl -sf localhost:8000/readyz >/dev/null; do sleep 0.5; done
post "what is the weather in london"
docker rm -f parser-fault >/dev/null
} 2>&1 | tee $OUT/container_test.txt
