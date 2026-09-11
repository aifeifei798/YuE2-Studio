FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
# YuE2 / torch 请按官方文档另行安装（体积大，默认镜像不内置）

# 构建上下文为仓库根：docker build -f backend/Dockerfile .
# api 容器不内置前端（前端由 web 服务提供），/ 仅返回 500 提示。
COPY backend/ ./backend/

RUN mkdir -p /app/outputs/audio /app/outputs/artifacts
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

# 必须单 worker：GPU 队列在进程内存中
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
