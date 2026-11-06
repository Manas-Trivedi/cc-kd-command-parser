# Distilled command parser on a local k3d cloud

CS3081 Cloud Computing mini project — Manas Trivedi (230101073).

Repository: https://github.com/Manas-Trivedi/cc-kd-command-parser
Report: [`report/report.pdf`](report/report.pdf) · Model releases: [v1](https://github.com/Manas-Trivedi/cc-kd-command-parser/releases/tag/v1), [v2](https://github.com/Manas-Trivedi/cc-kd-command-parser/releases/tag/v2)

A teacher model (phi3.5, 3.8B, on Ollama) labels 3,000 voice-assistant commands with a four-key JSON
(`intent`, `date`, `time`, `place`). A 70M-parameter student (Pythia-70M) is fine-tuned on those
replies (sequence-level knowledge distillation) and served by a FastAPI service on a local
Kubernetes cluster (k3d), with model weights kept in SeaweedFS object storage.

```
laptop ── Ollama (phi3.5) ◄── pipeline/teacher-label (Indexed Job, 3 shards)
                                   │ raw replies
                                   ▼
            data/seaweedfs (S3): datasets/  models/v1  models/v2
                                   │ download at start-up
                                   ▼
  :8080 ─► Traefik Ingress /parse ─► app/parser Service ─► parser Pods (2, HPA 1–2)
```

## Prerequisites

Docker (Docker Desktop or OrbStack), [k3d](https://k3d.io) ≥ 5.6, kubectl, make, openssl, Python 3.10+.
For Task 1 only: [Ollama](https://ollama.com) with `ollama pull phi3.5`.

## Build and deploy (fresh clone, three commands)

```bash
git clone https://github.com/Manas-Trivedi/cc-kd-command-parser.git
cd cc-kd-command-parser
make up
```

`make up` creates the cluster from `cluster/k3d.yaml`, generates S3 credentials into Kubernetes
Secrets (never into the repo), deploys SeaweedFS, builds and imports both images, runs a seed Job
that creates the buckets, uploads `commands_pool.jsonl` and copies the released models v1/v2
(checksums verified) into `s3://models/`, then deploys the service. Try it:

```bash
curl -s -X POST localhost:8080/parse -H 'content-type: application/json' \
     -d '{"text": "set an alarm for seven thirty am tomorrow"}'
```

## Tear down

```bash
make down        # deletes the k3d cluster
```

## Test

| What | Command |
|------|---------|
| Lint | `make lint` |
| Unit tests | `make test` |
| Training smoke test | `make train SUBSET=50` |
| Full training (v1 / v2) | `make train` / `make train-v2` |
| Evaluate student v1 | `make eval` |
| Score any predictions | `python tools/score.py PRED.jsonl data/validation.jsonl` |
| NACL check | `python tools/nacl_sim.py network/nacl.json network/packets_sample.jsonl --check` |
| Load test | `make loadtest` (standard 4-user, 2-minute Locust run against `localhost:8080`) |
| Kubernetes experiments | `scripts/k8s_tests.sh probes\|hpa\|rollout\|recovery\|coldstart\|balance\|perf` |

## Task 1 pipeline (teacher labelling)

```bash
ollama serve &  ollama pull phi3.5         # teacher on the laptop
make cluster secrets storage images seed   # (or make up)
make label                                 # 3 workers in namespace pipeline
make clean-data                            # -> s3://datasets/v1/train.jsonl + train.sha256
```

## Layout

| Path | What |
|------|------|
| `cluster/k3d.yaml` | cluster definition |
| `k8s/data`, `k8s/pipeline`, `k8s/app` | SeaweedFS; labelling and seed Jobs; Deployment, Service, Ingress, HPA |
| `prompts/teacher_prompt.txt` | teacher prompt |
| `worker/` | shard worker, `clean.py`, seed script, image |
| `training/` | `config.yaml`, `train.py`, `evaluate.py`, `loss.csv` |
| `service/` | FastAPI app, shared inference code, image |
| `network/` | `subnets.md`, `nacl.json` |
| `tests/` | unit tests |
| `results/` | all evidence: score reports, raw predictions, Locust output, command logs |
| `.github/workflows/ci.yml` | lint → unit tests → evaluation from the Release → quality gate; gitleaks |
