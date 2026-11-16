# 生产镜像：只含代码 + Python 依赖，不含知识库数据/模型/配置。
# 构建上下文必须是仓库根目录；数据/模型/配置在运行时用 volume 挂载。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 离线 wheel 由 deploy/wheels 提供；构建机/服务器都不需要外网
COPY deploy/requirements-server.txt /app/requirements-server.txt
COPY deploy/wheels/ /app/wheels/
# jieba 只有 sdist：先装 setuptools/wheel，再用 --no-build-isolation 离线构建
RUN pip install --no-cache-dir --no-index --find-links=/app/wheels setuptools wheel \
    && pip install --no-cache-dir --no-index --no-build-isolation \
       --find-links=/app/wheels -r /app/requirements-server.txt \
    && rm -rf /app/wheels

# 代码（.dockerignore 已排除 app/data、app/config.json、models、index、demo、docs）
COPY app/ /app/app/
COPY *.py /app/

# 非 root 运行；宿主机数据卷请 chown 1000:1000
RUN useradd --create-home --uid 1000 rag && mkdir -p /app/logs && chown -R rag:rag /app /app/logs
USER rag

EXPOSE 8000
CMD ["python", "app/server.py", "--port", "8000"]
