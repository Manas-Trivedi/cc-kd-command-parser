FROM python:3.12.8-slim
WORKDIR /app
COPY worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker/worker.py worker/clean.py worker/seed.py ./
COPY prompts/teacher_prompt.txt ./teacher_prompt.txt
COPY data/commands_pool.jsonl ./commands_pool.jsonl
USER 1000
CMD ["python", "worker.py"]
