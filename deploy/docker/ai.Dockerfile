# AI 服务：FastAPI + 轻量网关。构建上下文是仓库根目录，案例和示例单据随镜像一起打包
FROM python:3.13.17-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AI_DATA_DIR=/data AI_CASES_DIR=/app/knowledge AI_SAMPLES_DIR=/app/samples AI_OPSDOCS_DIR=/app/opsdocs
COPY ai/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ai/app app
COPY ai/knowledge knowledge
# 运维知识库：本仓库自己的运维手册、架构说明、迁移手册（ai/app/opsdocs.py）
COPY docs/runbook.md docs/architecture.md docs/migrate-server.md opsdocs/
COPY public/ai/samples samples
# 非 root 运行；数据（费用账本、向量缓存）写在挂载的 /data
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
