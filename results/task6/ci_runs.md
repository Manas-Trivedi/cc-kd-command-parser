# CI runs

Passing run (student v1 from Release, gate passes): https://github.com/Manas-Trivedi/cc-kd-command-parser/actions/runs/42317803924
Deliberately bad model (untrained Pythia-70M, workflow_dispatch model=untrained), fails at the quality gate: https://github.com/Manas-Trivedi/cc-kd-command-parser/actions/runs/42318098565

## passing run

✓ main ci · 42317803924
Triggered via push about 5 minutes ago

JOBS
✓ lint in 8s (ID 128234655745)
✓ secret-scan in 6s (ID 128234656310)
✓ unit-tests in 52s (ID 128234710139)
✓ evaluate in 1m17s (ID 128235014055)
✓ quality-gate in 8s (ID 128235460333)

ANNOTATIONS
Download model from the GitHub Release and check its checksum	2026-11-07T00:23:58.9689529Z model-v1.tar.gz: OK
Score	2026-11-07T00:24:34.8433944Z   "json_validity": 100.0,
Score	2026-11-07T00:24:34.8435169Z   "full_frame": 45.0,
score.py --min-validity 95	2026-11-07T00:24:44.9487138Z   "json_validity": 100.0,
score.py --min-validity 95	2026-11-07T00:24:44.9491749Z   "full_frame": 45.0,

## failing run

X main ci · 42318098565
Triggered via workflow_dispatch about 2 minutes ago

JOBS
✓ secret-scan in 6s (ID 128235654064)
✓ lint in 5s (ID 128235654369)
✓ unit-tests in 34s (ID 128235696901)
✓ evaluate in 1m20s (ID 128235901340)
X quality-gate in 4s (ID 128236361670)
  ✓ Set up job
  ✓ Run actions/checkout@v4
score.py --min-validity 95	2026-11-07T00:27:21.6434789Z QUALITY GATE FAILED: json_validity 0.0 < 95.0
score.py --min-validity 95	2026-11-07T00:27:21.6437444Z   "json_validity": 0.0,
