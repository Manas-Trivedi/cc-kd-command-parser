#!/usr/bin/env bash
# Task 4 / Task 7 experiments. Each one writes its evidence to results/.
#   scripts/k8s_tests.sh probes|hpa|rollout|recovery|coldstart|balance|perf
set -euo pipefail
cd "$(dirname "$0")/.."
NS=app
LOCUST=.venv/bin/locust
OUT=results/task4
mkdir -p "$OUT"

ts() { date '+%H:%M:%S'; }

locust_run() {  # $1 = csv prefix, $2 = run time
  $LOCUST -f tools/locustfile.py --headless --users 4 --spawn-rate 4 --run-time "$2" \
    --host http://localhost:8080 --csv "$OUT/$1" --only-summary \
    > "$OUT/$1_locust.log" 2>&1 || true   # locust exits 1 if any request failed; read the numbers
}
headers_env() { export LOADTEST_HEADERS_OUT="$OUT/$1_headers.json"; }

wait_ready() {  # $1 = replica count
  until [ "$(kubectl -n $NS get deploy parser -o jsonpath='{.status.readyReplicas}')" = "$1" ]; do sleep 2; done
}

pin_two() {  # keep exactly two replicas for tests that need them
  kubectl -n $NS patch hpa parser -p '{"spec":{"minReplicas":2}}' >/dev/null
  kubectl -n $NS scale deploy parser --replicas=2 >/dev/null
  wait_ready 2
}
unpin() { kubectl -n $NS patch hpa parser -p '{"spec":{"minReplicas":1}}' >/dev/null; }

case "${1:-}" in
probes)
  # The Service must have no ready endpoints while the model is loading.
  # Scale to 0, then to 1, and poll every ~0.3 s until the new pod is Ready.
  f=$OUT/probes.txt
  {
    echo "# $(ts) scale to 0 replicas"
    kubectl -n $NS scale deploy parser --replicas=0
    kubectl -n $NS wait --for=delete pod -l app=parser --timeout=120s >/dev/null 2>&1 || true
    echo "# $(ts) scale to 1 replica and watch"
    kubectl -n $NS scale deploy parser --replicas=1
    for _ in $(seq 1 200); do
      pod=$(kubectl -n $NS get pods -l app=parser -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
      ready=$(kubectl -n $NS get pods -l app=parser -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null || true)
      eps=$(kubectl -n $NS get endpointslices -l kubernetes.io/service-name=parser -o jsonpath='{range .items[*].endpoints[*]}{.targetRef.name}={.conditions.ready} {end}' || true)
      readyz=$(kubectl -n $NS exec "$pod" -- python -c "import urllib.request as u
try: print(u.urlopen('http://localhost:8000/readyz').status)
except Exception as e: print(getattr(e,'code','down'))" 2>/dev/null || echo "n/a")
      code=$(curl -s -o /dev/null -w '%{http_code}' -X POST localhost:8080/parse -H 'content-type: application/json' -d '{"text":"set an alarm for six am"}')
      echo "$(ts) pod=$pod podReady=${ready:-none} /readyz=$readyz endpoints=[${eps}] ingress/parse=HTTP $code"
      [ "$ready" = "true" ] && [ "$code" = "200" ] && break
      sleep 0.3
    done
    echo "# pod log:"
    kubectl -n $NS logs "$pod" | grep -E 'downloading|download finished|ready after|readyz'
  } 2>&1 | tee "$f"
  kubectl -n $NS scale deploy parser --replicas=2 >/dev/null
  ;;

hpa)
  # Scale 1 -> 2 under load and back to 1 afterwards.
  f=$OUT/hpa.txt
  unpin
  echo "# $(ts) waiting for the HPA to settle at 1 replica (idle)" | tee "$f"
  until [ "$(kubectl -n $NS get deploy parser -o jsonpath='{.spec.replicas}')" = "1" ]; do sleep 5; done
  kubectl -n $NS get hpa parser | tee -a "$f"
  (while true; do echo "$(ts) $(kubectl -n $NS get hpa parser --no-headers)"; kubectl -n $NS top pods --no-headers 2>/dev/null | sed 's/^/        top: /'; sleep 10; done) >> "$f" &
  watcher=$!
  headers_env hpa
  echo "# $(ts) load test 4 min" >> "$f"
  locust_run hpa 4m
  echo "# $(ts) load finished, waiting for scale down" >> "$f"
  until [ "$(kubectl -n $NS get deploy parser -o jsonpath='{.spec.replicas}')" = "1" ]; do sleep 5; done
  sleep 15
  kill $watcher
  echo "# $(ts) back to 1 replica" >> "$f"
  kubectl -n $NS describe hpa parser | sed -n '/Events:/,$p' >> "$f"
  cat "$f"
  ;;

rollout)
  # v1 -> v2 rolling update under load, then kubectl rollout undo.
  f=$OUT/rollout.txt
  pin_two
  headers_env rollout
  locust_run rollout 5m &
  lpid=$!
  {
    sleep 30
    echo "# $(ts) set v2"
    kubectl -n $NS set env deploy/parser MODEL_VERSION=v2 MODEL_URI=s3://models/v2/
    kubectl -n $NS rollout status deploy/parser --timeout=300s
    kubectl -n $NS rollout history deploy/parser
    sleep 60
    echo "# $(ts) rollback"
    kubectl -n $NS rollout undo deploy/parser
    kubectl -n $NS rollout status deploy/parser --timeout=300s
    kubectl -n $NS get deploy parser -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}' | grep MODEL_
  } 2>&1 | tee "$f"
  wait $lpid
  {
    echo "# $(ts) after rollback, 5 requests through the ingress:"
    for _ in 1 2 3 4 5; do
      curl -s -D - -o /dev/null -X POST localhost:8080/parse -H 'content-type: application/json' \
        -d '{"text":"what is the weather in london"}' | grep -iE '^(HTTP|x-model-version|x-served-by)'
    done
    echo "# locust summary"; tail -25 "$OUT/rollout_locust.log"
  } | tee -a "$f"
  unpin
  ;;

recovery)
  f=$OUT/recovery.txt
  pin_two
  headers_env recovery
  locust_run recovery 3m &
  lpid=$!
  {
    sleep 30
    victim=$(kubectl -n $NS get pods -l app=parser -o jsonpath='{.items[0].metadata.name}')
    echo "# $(ts) deleting $victim"
    kubectl -n $NS delete pod "$victim" --wait=false
    for _ in $(seq 1 60); do
      echo "$(ts) $(kubectl -n $NS get deploy parser --no-headers)"
      sleep 2
      [ "$(kubectl -n $NS get deploy parser -o jsonpath='{.status.readyReplicas}')" = "2" ] && \
        [ -z "$(kubectl -n $NS get pod "$victim" --ignore-not-found -o name)" ] && break
    done
    echo "# $(ts) replica count restored"
    kubectl -n $NS get pods -l app=parser -o wide
  } 2>&1 | tee "$f"
  wait $lpid
  tail -25 "$OUT/recovery_locust.log" | tee -a "$f"
  unpin
  ;;

coldstart)
  # Delete all pods 3 times; time from pod creation to Ready; show the download in the logs.
  f=$OUT/coldstart.txt
  pin_two
  : > "$f"
  for run in 1 2 3; do
    old=$(kubectl -n $NS get pods -l app=parser -o jsonpath='{.items[*].metadata.name}')
    echo "# run $run $(ts): deleting all pods: $old" | tee -a "$f"
    kubectl -n $NS delete pod -l app=parser --wait=false >/dev/null
    sleep 3
    until n=$(kubectl -n $NS get pods -l app=parser -o json | python3 -c '
import json,sys
old=set(sys.argv[1].split()); p=[x for x in json.load(sys.stdin)["items"] if x["metadata"]["name"] not in old]
r=[x for x in p if any(c["type"]=="Ready" and c["status"]=="True" for c in x["status"].get("conditions",[]))]
print(len(r))' "$old") && [ "$n" -ge 2 ]; do sleep 2; done
    kubectl -n $NS get pods -l app=parser -o json | python3 -c '
import json,sys
from datetime import datetime
old=set(sys.argv[1].split())
for x in json.load(sys.stdin)["items"]:
    if x["metadata"]["name"] in old: continue
    c=datetime.fromisoformat(x["metadata"]["creationTimestamp"].replace("Z","+00:00"))
    r=[datetime.fromisoformat(k["lastTransitionTime"].replace("Z","+00:00")) for k in x["status"]["conditions"] if k["type"]=="Ready"][0]
    name=x["metadata"]["name"]
    print(f"COLDSTART {name} created {c:%H:%M:%S} ready {r:%H:%M:%S} seconds {(r-c).total_seconds():.0f}")' "$old" | tee -a "$f"
    pod=$(kubectl -n $NS get pods -l app=parser -o jsonpath='{.items[0].metadata.name}')
    echo "# logs of $pod:" >> "$f"
    kubectl -n $NS logs "$pod" | grep -E 'download|ready after' >> "$f"
    sleep 5
  done
  python3 - "$f" <<'EOF' | tee -a "$f"
import re, statistics, sys
t = [int(x) for x in re.findall(r"COLDSTART .* seconds (\d+)", open(sys.argv[1]).read())]
print(f"cold-start times (s): {t}; median {statistics.median(t)} s")
EOF
  unpin
  ;;

balance)
  f=$OUT/balance.txt
  pin_two
  kubectl -n $NS get pods -l app=parser | tee "$f"
  headers_env balance
  locust_run balance 2m
  { tail -25 "$OUT/balance_locust.log"; cat "$OUT/balance_headers.json"; } | tee -a "$f"
  python3 -c '
import json; d=json.load(open("results/task4/balance_headers.json"))["per_replica"]; t=sum(d.values())
for k,v in d.items(): print(f"{k}: {v} ({100*v/t:.1f}%)")' | tee -a "$f"
  unpin
  ;;

perf)
  # Task 7: the standard run, with kubectl top sampled every 10 s.
  f=$OUT/perf_top.txt
  : > "$f"
  (while true; do echo "--- $(ts)"; kubectl -n $NS top pods --no-headers 2>/dev/null; sleep 10; done) >> "$f" &
  watcher=$!
  export LOADTEST_HEADERS_OUT=$OUT/loadtest_headers.json
  $LOCUST -f tools/locustfile.py --headless --users 4 --spawn-rate 4 --run-time 2m \
    --host http://localhost:8080 --csv $OUT/loadtest 2>&1 | tee $OUT/loadtest_locust.log || true
  kill $watcher
  cat "$f"
  ;;

*)
  echo "usage: $0 probes|hpa|rollout|recovery|coldstart|balance|perf"; exit 1 ;;
esac
