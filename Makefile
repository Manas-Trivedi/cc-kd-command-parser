SHELL := /bin/bash
CLUSTER   := kd
PY        := .venv/bin/python
PARSER_IMG := kd-parser:1.0
WORKER_IMG := kd-worker:1.0
CONFIG    ?= training/config.yaml
SUBSET    ?=
REPO      := Manas-Trivedi/cc-kd-command-parser

.PHONY: up down cluster secrets storage images seed deploy label clean-data train train-v2 \
        eval package upload-models release lint test loadtest venv

# ---------- whole system ----------
## make up: cluster + storage + seeded models + service. Needs docker, k3d, kubectl.
up: cluster secrets storage images seed deploy
	@echo "ready: curl -s -X POST localhost:8080/parse -H 'content-type: application/json' -d '{\"text\":\"set an alarm for seven am\"}'"

down:
	k3d cluster delete $(CLUSTER)

cluster:
	@k3d cluster list $(CLUSTER) >/dev/null 2>&1 || k3d cluster create --config cluster/k3d.yaml
	kubectl config use-context k3d-$(CLUSTER)
	kubectl wait --for=condition=Ready nodes --all --timeout=180s

secrets:
	./scripts/secrets.sh

storage:
	kubectl apply -f k8s/data/seaweedfs.yaml
	kubectl -n data rollout status deploy/seaweedfs --timeout=300s

images:
	docker build -t $(WORKER_IMG) -f worker/Dockerfile .
	docker build -t $(PARSER_IMG) -f service/Dockerfile .
	k3d image import -c $(CLUSTER) $(WORKER_IMG) $(PARSER_IMG)

seed:
	kubectl -n pipeline delete job seed-storage --ignore-not-found
	kubectl apply -f k8s/pipeline/seed-job.yaml
	kubectl -n pipeline wait --for=condition=complete job/seed-storage --timeout=600s
	kubectl -n pipeline logs job/seed-storage

deploy:
	kubectl apply -f k8s/app/parser.yaml
	kubectl -n app rollout status deploy/parser --timeout=600s

# ---------- Task 1: teacher labelling ----------
label:
	kubectl -n pipeline delete job teacher-label --ignore-not-found
	kubectl apply -f k8s/pipeline/label-job.yaml
	kubectl -n pipeline get pods -l job-name=teacher-label -o wide

clean-data: venv
	set -a; source .secrets/s3.env; S3_ENDPOINT=http://localhost:8333 $(PY) worker/clean.py | tee results/clean_counts.txt
	$(PY) scripts/s3.py get datasets/v1/train.jsonl data/v1/train.jsonl
	$(PY) scripts/s3.py get datasets/v1/train.sha256 data/v1/train.sha256

# ---------- Task 2: training and evaluation ----------
venv: $(PY)
$(PY):
	python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt

train: venv
	$(PY) training/train.py --config $(CONFIG) $(if $(SUBSET),--subset $(SUBSET) --output-dir artifacts/subset-$(SUBSET) --loss-file training/loss_subset$(SUBSET).csv)

train-v2: venv
	$(PY) training/train.py --config training/config_v2.yaml

eval: venv
	$(PY) training/evaluate.py --model artifacts/v1 --out results/student_v1_predictions.jsonl
	$(PY) tools/score.py results/student_v1_predictions.jsonl data/validation.jsonl --out results/student_v1_validation.json

## deterministic tarball + checksum for the release and object storage
package: venv
	$(PY) scripts/package_model.py v1 artifacts/v1
	$(PY) scripts/package_model.py v2 artifacts/v2

upload-models: venv
	$(PY) scripts/s3.py putdir artifacts/v1 models/v1
	$(PY) scripts/s3.py put artifacts/release/model-v1.sha256 models/v1/model.sha256
	$(PY) scripts/s3.py putdir artifacts/v2 models/v2
	$(PY) scripts/s3.py put artifacts/release/model-v2.sha256 models/v2/model.sha256

release:
	gh release create v1 artifacts/release/model-v1.tar.gz artifacts/release/model-v1.sha256 -R $(REPO) -t "Student model v1" -n "Pythia-70M distilled from phi3.5, 8 epochs, seed 42"
	gh release create v2 artifacts/release/model-v2.tar.gz artifacts/release/model-v2.sha256 -R $(REPO) -t "Student model v2" -n "Pythia-70M distilled from phi3.5, see training/config_v2.yaml"

# ---------- checks ----------
lint: venv
	$(PY) -m ruff check .

test: venv
	$(PY) -m pytest -q tests

loadtest: venv
	.venv/bin/locust -f tools/locustfile.py --headless --users 4 --spawn-rate 4 --run-time 2m \
	  --host http://localhost:8080 --csv results/loadtest
