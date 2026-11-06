"""Write a predictions file for tools/score.py.

  python training/evaluate.py --model artifacts/v1 --out results/student_v1_predictions.jsonl
  python training/evaluate.py --model EleutherAI/pythia-70m --out results/untrained_predictions.jsonl
  python training/evaluate.py --teacher --out results/teacher_predictions.jsonl

--model runs a (local or Hugging Face) causal LM with the student prompt format.
--teacher asks phi3.5 on Ollama with prompts/teacher_prompt.txt and the Task 1 settings.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "service"))


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--model")
    src.add_argument("--teacher", action="store_true")
    ap.add_argument("--data", default="data/validation.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-new-tokens", type=int, default=64)
    a = ap.parse_args()

    with open(a.data, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    if a.teacher:
        import requests
        with open("prompts/teacher_prompt.txt", encoding="utf-8") as f:
            template = f.read()
        url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
        s = requests.Session()

        def predict(text):
            r = s.post(f"{url}/api/generate", timeout=300, json={
                "model": "phi3.5", "prompt": template.replace("{command}", text), "stream": False, "format": "json",
                "options": {"temperature": 0, "seed": 42, "num_predict": 96}})
            r.raise_for_status()
            return r.json()["response"]
    else:
        import torch
        from inference import generate, load
        torch.manual_seed(42)
        tok, model = load(a.model)

        def predict(text):
            return generate(tok, model, text, a.max_new_tokens)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    t0 = time.time()
    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"id": r["id"], "prediction": predict(r["text"])}, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} predictions to {a.out} in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
