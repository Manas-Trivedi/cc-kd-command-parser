# Inference service image. No model weights inside: they are downloaded from S3 at start-up.
FROM python:3.12.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
WORKDIR /app

COPY service/requirements.txt .
# CPU-only PyTorch wheel (the default wheel pulls in ~3 GB of CUDA libraries)
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.5.1 \
 && pip install --no-cache-dir -r requirements.txt

COPY service/app.py service/inference.py ./
COPY data/schema.json ./schema.json

RUN useradd --uid 1000 --create-home app
USER 1000
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
