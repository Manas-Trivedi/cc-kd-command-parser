"""Prompt format and greedy generation shared by training, evaluation and the service."""
import torch
from transformers import StoppingCriteria, StoppingCriteriaList

PROMPT = "Command: {text}\nJSON:"


def build_prompt(text):
    return PROMPT.format(text=text.strip())


def load(model_dir):
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForCausalLM.from_pretrained(model_dir, torch_dtype=torch.float32)
    model.eval()
    return tok, model


class StopAtLineEnd(StoppingCriteria):
    """Stop once the answer has some text and then a newline (the reply is one line)."""

    def __init__(self, tok, start):
        self.tok, self.start = tok, start

    def __call__(self, input_ids, scores, **kwargs):
        text = self.tok.decode(input_ids[0, self.start:], skip_special_tokens=True).lstrip()
        return "\n" in text


@torch.inference_mode()
def generate(tok, model, text, max_new_tokens=64):
    """Return the model's one-line answer after 'JSON:' (ends at EOS, a newline, or max_new_tokens)."""
    ids = tok(build_prompt(text), return_tensors="pt")
    start = ids["input_ids"].shape[1]
    out = model.generate(
        **ids,
        max_new_tokens=max_new_tokens,
        do_sample=False,
        eos_token_id=tok.eos_token_id,
        pad_token_id=tok.eos_token_id,
        stopping_criteria=StoppingCriteriaList([StopAtLineEnd(tok, start)]),
    )
    raw = tok.decode(out[0, start:], skip_special_tokens=True).strip()
    return raw.split("\n", 1)[0].strip()
