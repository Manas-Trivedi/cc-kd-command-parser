import json
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "worker"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import clean  # noqa: E402
import package_model  # noqa: E402
from worker import shard_lines  # noqa: E402


def rec(i, text, raw):
    return {"id": f"C{i:04d}", "text": text, "teacher_raw": raw}


GOOD = '{"intent": "alarm_set", "date": "tomorrow", "time": "seven am", "place": null}'


def test_shards_cover_every_line_exactly_once():
    lines = [f"line{i}" for i in range(3000)]
    for count in (2, 3, 7):
        shards = [shard_lines(lines, k, count) for k in range(count)]
        flat = [x for s in shards for x in s]
        assert sorted(flat) == sorted(lines)
        assert len(flat) == len(set(flat))
        assert shards[1][0] == "line1" and shards[1][1] == f"line{1 + count}"


def test_clean_reasons_and_counts():
    text = "set an alarm for seven am tomorrow"
    records = [
        rec(1, text, GOOD),
        rec(2, text, "Sure! here is the JSON"),                                            # (a)
        rec(3, text, '{"intent": "alarm_set", "date": null, "time": null}'),               # (b)
        rec(4, text, '{"intent": "alarm_delete", "date": null, "time": null, "place": null}'),  # (c)
        rec(5, text, '{"intent": "alarm_set", "date": "monday", "time": null, "place": null}'),  # (d)
        rec(6, text, GOOD),                                                                # (e) duplicate
        rec(7, "what is the weather in london",
            '{"intent": "weather_query", "date": null, "time": null, "place": "london"}'),
    ]
    kept, counts = clean.clean(list(reversed(records)))
    assert [r["id"] for r in kept] == ["C0001", "C0007"]        # id order, smallest id kept
    assert counts == {"input": 7, "a_invalid_json": 1, "b_wrong_keys": 1, "c_bad_intent": 1,
                      "d_slot_not_in_command": 1, "e_duplicates": 1, "kept": 2}
    assert json.loads(kept[0]["target"]) == json.loads(GOOD)


def test_clean_is_deterministic():
    records = [rec(i, f"wake me at {i} am", '{"intent": "alarm_set", "date": null, "time": "%d am", '
                   '"place": null}' % i) for i in range(1, 50)]
    a = clean.serialise(clean.clean(records)[0])
    b = clean.serialise(clean.clean(list(reversed(records)))[0])
    assert a == b


def test_package_is_reproducible(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "model.safetensors").write_bytes(b"\x00" * 100)
    assert package_model.pack("v1", tmp_path) == package_model.pack("v1", tmp_path)
