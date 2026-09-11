# AGENTS.md — YuE2-Studio

FastAPI (GPU) + Vite+React 前后端分离作曲工作台。单卡串行生成。

## 架构（文件名看不出的关键点）

- GPU 任务队列是进程内 `asyncio.Queue`（`backend/app/core/store.py` + `services/queue.py` 单 worker 串行消费）。
  **api 永远 `--workers 1`，永远单副本**——多 worker/多副本会让排队与显存互斥静默失效。
- 后端入口（仓库根运行）：`uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --workers 1`
- 配置全走环境变量（见 `.env.example`），`get_settings()` 带 `lru_cache`——测试里改 env 后必须调 `reload_settings()`。
- 模型导入是延迟的（`services/inference.py` 内 import yue2）：无 GPU/无依赖时服务照常启动，`/healthz` 报 `degraded`，`POST /api/generate` 报 503。
- 管理口 `/api/admin/*` 统一 `require_admin`：`ADMIN_TOKEN` 为空 = 全部 403；鉴权走 `Authorization: Bearer` 或 `X-Admin-Token`。
- 前端是 hash 路由（`#/` 创作，`#/admin` 管理），无 react-router。all-in-one 镜像里后端 serve `web/dist`；**改完前端必须在 `web/` 跑 `npm run build`，否则后端 `/` 报 500**。dev 用 `npm run dev`（5173，经 vite proxy 反代 127.0.0.1:8000）。
- compose 下浏览器只访问 `web:80`（nginx 反代 `/api /audio /healthz` 到 api）；api 默认不 publish 端口。
- 只挂载了 `outputs/audio/` 做静态 serving——永远不要把整个 `outputs/` 挂出去，`history.json` 会泄露。

## 数据与约束

- `outputs/audio/*.flac`、`outputs/artifacts/<id>/`、`outputs/history.json`（原子写；损坏自动备份 `*.bak.*`）。compose 用命名卷 `outputs`。
- `task_id` 恒为 8 位 hex；`audio_url` 恒为同源 `/audio/<id>.flac`（前后端都有格式校验，改动时保持）。
- 取消任务只对排队中有效，运行中返回 409（GPU 不可抢占）。

## 验证（无测试/CI/lint，一律手动）

```bash
python3 -m py_compile backend/app/main.py backend/app/core/*.py backend/app/routers/*.py backend/app/services/*.py
cd web && npm run build   # 含 tsc 类型检查
docker compose config     # 改编排后必跑；无 .env 也能过（required: false）
```

- 后端冒烟需 stub `yue2`（重依赖 dev 环境没有）：`sys.modules["yue2"]` 塞假 `YuE2Pipeline`，再用 `TestClient(backend.app.main:app)` 走 `POST /api/generate` → 轮询 `GET /api/tasks/{id}` 到 `succeeded`。
- 前后端联调：先起 api（8000），再 `web/npm run dev`，不要直接 `vite preview` 测 API。

## 卫生

- 只改源文件：`web/src/**/*.tsx`、`.py`。`web/src/**/*.js` 是历史误提交的构建产物，`__pycache__/` 同理——不要编辑，不要新增，顺手删掉无妨但别混进功能提交。
- `web/dist/`、`outputs/`、`node_modules/` 已 gitignore，音频/历史永远不入库。
