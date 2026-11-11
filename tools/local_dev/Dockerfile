FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUTF8=1

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.115,<1" \
    "uvicorn>=0.30,<1" \
    "httpx>=0.27,<1"

COPY mock_rag.py demo_bff.py ./
COPY demo_docs ./demo_docs

EXPOSE 9390 8020

# 默认启动假 RAG；BFF 在 compose 里覆盖 command
CMD ["python", "mock_rag.py", "--host", "0.0.0.0", "--port", "9390"]
