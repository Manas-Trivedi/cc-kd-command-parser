"""Sequence-level distillation: fine-tune Pythia-70M on the teacher's JSON replies.

  python training/train.py [--config training/config.yaml] [--subset N] [--output-dir DIR] [--loss-file FILE]

Each example is
    Command: <command>
    JSON: <teacher's JSON reply><eos>
and the loss is computed only on the reply part (" <JSON><eos>"), so the model learns
to produce the teacher's output given the command. Runs on the CPU only.
"""
import argparse
import csv
import json
import math
import os
import random
import sys
import time

import numpy as np
import torch
import yaml
from transformers import AutoModelForCausalLM, AutoTokenizer, get_linear_schedule_with_warmup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "service"))
from inference import build_prompt  # noqa: E402


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


def encode(tok, rec, max_len):
    prompt_ids = tok(build_prompt(rec["text"]))["input_ids"]
    target_ids = tok(" " + rec["target"])["input_ids"] + [tok.eos_token_id]
    ids = (prompt_ids + target_ids)[:max_len]
    labels = ([-100] * len(prompt_ids) + target_ids)[:max_len]
    return ids, labels


def batches(examples, size, pad_id, rng):
    order = list(range(len(examples)))
    rng.shuffle(order)
    for i in range(0, len(order), size):
        chunk = [examples[j] for j in order[i:i + size]]
        width = max(len(ids) for ids, _ in chunk)
        input_ids = torch.full((len(chunk), width), pad_id)
        labels = torch.full((len(chunk), width), -100)
        mask = torch.zeros((len(chunk), width), dtype=torch.long)
        for r, (ids, lab) in enumerate(chunk):
            input_ids[r, :len(ids)] = torch.tensor(ids)
            labels[r, :len(lab)] = torch.tensor(lab)
            mask[r, :len(ids)] = 1
        yield input_ids, mask, labels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="training/config.yaml")
    ap.add_argument("--subset", type=int, help="train on the first N records only (smoke test)")
    ap.add_argument("--output-dir", help="override output_dir from the config")
    ap.add_argument("--loss-file", help="override loss_file from the config")
    a = ap.parse_args()

    with open(a.config) as f:
        cfg = yaml.safe_load(f)
    set_seed(cfg["seed"])
    torch.set_num_threads(max(1, os.cpu_count() or 1))
    device = torch.device("cpu")

    with open(cfg["train_file"], encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    records = records[:cfg["train_records"]]
    if a.subset:
        records = records[:a.subset]
    out_dir = a.output_dir or cfg["output_dir"]
    loss_file = a.loss_file or cfg["loss_file"]
    print(f"training on {len(records)} records, {cfg['epochs']} epochs, output {out_dir}", flush=True)

    tok = AutoTokenizer.from_pretrained(cfg["base_model"], revision=cfg["base_model_revision"])
    model = AutoModelForCausalLM.from_pretrained(cfg["base_model"], revision=cfg["base_model_revision"],
                                                 torch_dtype=torch.float32).to(device)
    examples = [encode(tok, r, cfg["max_seq_length"]) for r in records]

    opt = torch.optim.AdamW(model.parameters(), lr=cfg["learning_rate"], weight_decay=cfg["weight_decay"])
    steps = cfg["epochs"] * math.ceil(len(examples) / cfg["batch_size"])
    sched = get_linear_schedule_with_warmup(opt, int(cfg["warmup_ratio"] * steps), steps)
    rng = random.Random(cfg["seed"])

    os.makedirs(os.path.dirname(loss_file) or ".", exist_ok=True)
    rows = []
    model.train()
    for epoch in range(1, cfg["epochs"] + 1):
        t0, total, n = time.time(), 0.0, 0
        for input_ids, mask, labels in batches(examples, cfg["batch_size"], tok.eos_token_id, rng):
            loss = model(input_ids=input_ids, attention_mask=mask, labels=labels).loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad()
            total += loss.item()
            n += 1
        avg = total / n
        rows.append((epoch, round(avg, 6)))
        print(f"epoch {epoch}: loss {avg:.4f} ({time.time() - t0:.0f}s)", flush=True)

    with open(loss_file, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["epoch", "train_loss"])
        w.writerows(rows)

    model.save_pretrained(out_dir, safe_serialization=True)
    tok.save_pretrained(out_dir)
    with open(os.path.join(out_dir, "training_info.json"), "w") as f:
        json.dump({"config": cfg, "records": len(records), "loss": rows}, f, indent=2)
    print(f"saved model and tokenizer to {out_dir}")


if __name__ == "__main__":
    main()
