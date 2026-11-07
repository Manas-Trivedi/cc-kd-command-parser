# AI Usage Disclosure — KD Command Parser

## 1. AI tools used

### Ollama — Phi-3.5
Used as a teacher model to generate structured intent and slot annotations for the command dataset.

### EleutherAI Pythia-70M
Used as the student language model, fine-tuned on the cleaned command dataset to generate JSON predictions for the command parser.

### Claude Code (Anthropic, model Claude Opus 5.5)
An AI coding agent running in VS Code on my Mac. It could read and write files, run terminal commands, and use my logged-in GitHub CLI.

## 2. Dataset preparation

Teacher-generated annotations were not accepted blindly. A cleaning pipeline checked:
- JSON validity and required keys.
- Intent values against the allowed schema.
- Slot values against the original command text.
- Duplicate commands.

Invalid or inconsistent records were discarded. The cleaned v1 dataset contains 2,772 examples; the student was trained on the first 1,500.

## 3. Human oversight

The project developer made the final decisions about:
- Dataset cleaning and schema requirements.
- Model training configuration and model selection.
- API validation and deployment configuration.
- Evaluation, testing, and interpretation of results.

AI-generated suggestions and annotations were treated as assistance, not as authoritative results.

## 4. Evaluation and limitations

The production v1 model achieved 100% JSON validity on the validation set. Its full-frame accuracy was 45%, compared with 54% for the Phi-3.5 teacher and 0% for untrained Pythia-70M. Per-field accuracy: intent 86%, date 78%, time 85%, place 82%.

Every validation output was valid JSON, but slot values were often wrong (full-frame accuracy is only 45%). The API still validates each output against the schema and returns an error rather than silently accepting an invalid prediction.

The model is a small experimental model and should not be treated as a reliable general-purpose language understanding system.

## 5. Privacy and security

The project uses command examples for training and evaluation. No credentials, API keys, or private user information should be included in training data or prompts.

S3 keys are generated randomly at `make up` and stored as Kubernetes Secrets; none are in the repo (gitleaks scan clean).

## 6. Final responsibility

The project developer is responsible for reviewing AI-assisted work, verifying test results, documenting limitations, and ensuring that the submitted implementation behaves as reported.