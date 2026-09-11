# 单机单镜像（all-in-one）：先构建 web 静态，再装后端同服。
# 用法：docker build -f Dockerfile -t yue2-allinone . && docker run --gpus all -p 8000:8000 -v outputs:/app/outputs yue2-allinone
# 需要前后端分离请用 docker-compose.yml（api + web 两个容器）。
FROM node:20-slim AS webbuild
WORKDIR /build
COPY web/package.json ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
# YuE2 / torch 请按官方文档另行安装（体积大，默认镜像不内置）

COPY backend/ ./backend/
COPY --from=webbuild /build/dist/ ./web/dist/

RUN mkdir -p /app/outputs/audio /app/outputs/artifacts
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1
# 必须单 worker：GPU 队列在进程内存中
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
