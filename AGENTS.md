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

- `outputs/audio/*.flac`、`outputs/artifacts/<id>/`、`outputs/history.db`（SQLite；老 `history.json` 首次启动自动导入并改名 `.migrated`）。compose 用命名卷 `outputs`。
- `task_id` 恒为 8 位 hex；`audio_url` 恒为同源 `/audio/<id>.flac`（前后端都有格式校验，改动时保持）。
- 取消任务只对排队中有效，运行中返回 409（GPU 不可抢占）。
- 删除歌曲只有管理端（`DELETE /api/admin/history`，Studio 页登录后才显示按钮）；公开 `/healthz` 无敏感字段，模型错误原文只在 `GET /api/admin/health`。
- `POST /api/generate` 按 IP 限流（`YUE2_SUBMIT_PER_HOUR`，nginx 透传 `X-Forwarded-For`）；另有每 IP 并存任务上限 `YUE2_MAX_PENDING_PER_IP`（pending+running，无账号体系下 IP 即用户）；两者都可在管理页 config 热更新。
- 用户体系是 API Key（`api_keys` 表，只存 SHA256，明文仅创建时返回一次）：凭证格式 `X-API-Key: 用户名:secret`；配额按**成功生成数**计（失败不计），`used + 在途 >= quota` 即 429；`REQUIRE_API_KEY=true` 时匿名 401；歌曲归属记 `records.owner`，删 Key 不删歌。
- 排队快照 `outputs/pending.json`，重启自动恢复 pending 任务。

## 验证（pytest + tsc + compose config，CI 同款）

```bash
python -m pytest backend/tests -q   # 需先 pip install -r requirements-dev.txt；stub yue2，无需 GPU
cd web && npm run build             # 含 tsc 类型检查；改完前端必跑，否则后端 / 报 500
docker compose config               # 改编排后必跑；无 .env 也能过（required: false）
```

- 测试 fixture 说明：`asyncio.Queue` 会绑定首次触碰它的 event loop，所以全 session 只用**一个** `TestClient`（见 `conftest.py` 注释），逐用例只清数据不清 client——不要改成每用例开关 client。
- `get_settings()` 带 `lru_cache`：测试里改 env 或运行时配置后必须调 `reload_settings()`（fixture 每次自动还原）。
- 前后端联调：先起 api（8000），再 `web/npm run dev`，不要直接 `vite preview` 测 API。

## 卫生

- 只改源文件：`web/src/**/*.tsx`、`.py`。`web/src/**/*.js` 是历史误提交的构建产物，`__pycache__/` 同理——不要编辑，不要新增，顺手删掉无妨但别混进功能提交。
- `web/dist/`、`outputs/`、`node_modules/` 已 gitignore，音频/历史永远不入库。
