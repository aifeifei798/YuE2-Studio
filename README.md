# YuE2-Studio

FastAPI（GPU）+ Vite+React（Studio/Admin）的前后端分离作曲工作台。单机单卡可跑，compose 双容器可分离部署。

## 结构

- `backend/app/`：API 真身。`routers/public.py`（创作/查询/音频）+ `routers/admin.py`（队列/取消/磁盘/日志/配置，需 `ADMIN_TOKEN`）。
- `web/`：前端工程。`/` 创作页 + `#/admin` 管理页，一次构建。 dev 用 `npm run dev`（5173，反代 8000）。
- `docker-compose.yml`：`api`（GPU，workers=1）+ `web`（nginx 反代 /api、/audio、/healthz）。
- `Dockerfile`：单镜像 all-in-one（node 构建 web → python 同服）；分离部署用 `backend/Dockerfile` + `web/Dockerfile`。

## 本地开发

```bash
pip install -r requirements.txt          # 转发到 backend/requirements.txt
cp .env.example .env                    # 填 ADMIN_TOKEN（管理页登录用）；后端启动时自动加载仓库根 .env，真实环境变量优先
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --workers 1
cd web && npm install && npm run build  # 产物 web/dist 由后端同服（all-in-one 镜像内自动构建）
cd web && npm install && npm run dev    # 前端 dev：http://127.0.0.1:5173
```

> 必须 `--workers 1`：GPU 队列在进程内存中。

## 部署（docker compose）

架构：`api`（GPU 推理，`--workers 1` 单副本）+ `web`（nginx 静态，反代 `/api /audio /healthz`）。
浏览器只访问 `web:80`，api 默认不对外暴露端口。

### 前提

- 带 NVIDIA 显卡的 Linux 主机，已装 Docker（含 compose v2）与 [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/)。
- 验证 GPU 直通：`docker run --rm --gpus all nvidia/cuda:12.0-base-ubuntu22.04 nvidia-smi` 能打出显卡信息。

### 首次部署

```bash
cp .env.example .env   # 必做
# 编辑 .env：
#   ADMIN_TOKEN=...     # 设一个长随机串，否则管理页不可用（/api/admin/* 全 403）
#   CORS_ORIGINS=...    # 公网部署时改成你的域名，如 https://music.example.com
#   YUE2_MAX_QUEUE=10   # 按显存/并发意愿调
#   YUE2_SUBMIT_PER_HOUR=20        # 每 IP 每小时提交次数
#   YUE2_MAX_PENDING_PER_IP=2      # 每 IP 最大并存任务数（无账号体系下 IP 即用户）

docker compose up --build -d
docker compose ps                       # 两个容器都 healthy 才算好
curl http://127.0.0.1/healthz           # 经 web 反代，{"status":"ok",...} 即就绪（模型加载需几分钟，期间为 degraded）
```

然后浏览器打开 `http://<主机IP>/`，管理页在 `http://<主机IP>/#/admin`（用 `ADMIN_TOKEN` 登录）。

> 首次 `up` 会拉 `python:3.12-slim`、`node:20-slim`、`nginx:alpine` 并在容器内构建前端，耗时几分钟属正常。
> 模型权重（`YUE2_MODEL_REPO`）是运行时由 `yue2` 按需下载/加载的，不在镜像里——首次生成前确认磁盘够大。

### 日常运维

```bash
docker compose logs -f api            # 看推理日志（管理页 Logs 标签也能看近 500 条）
docker compose logs -f web
docker compose restart api            # 改 .env 后必须重启；改 ADMIN_TOKEN/CORS 都要重启
docker compose up --build -d          # 拉新代码后更新
docker compose down                   # 停服（outputs 卷保留，音频/历史不丢）
```

- 备份：`outputs` 是命名卷（确切名称用 `docker volume ls | grep outputs` 确认，一般为 `yue2-studio_outputs`），直接快照它即可：`docker run --rm -v yue2-studio_outputs:/data -v $(pwd):/bak alpine tar czf /bak/outputs-$(date +%F).tgz -C /data .`。
- 磁盘：管理页 Disk 标签看 `audio/artifacts` 占用；删歌用管理页（`DELETE /api/admin/history/{id}`，会连音频一起删；Studio 页登录管理后才显示删除按钮）。
- api 永远单副本：不要 `docker compose up --scale api=2`，内存队列会让第二个副本的排队静默失效。

### 公网部署追加项

1. `.env` 里 `CORS_ORIGINS` 改为你的真实域名（逗号分隔），不要用 `*`。直连 api 调试时浏览器预检需要 `PATCH` + `X-API-Key` 头（已在 CORS 放行）。
2. 前面再架一层反代（Caddy/Nginx/云 LB）做 HTTPS，把 443 转到本机 80；不要把 api 的 8000 直接暴露到公网。
3. 默认已强制登录（`REQUIRE_API_KEY=true`）：匿名一律 401，必须登录才能生成。登录用户走 Key 配额 + 每用户并存上限，不再受每 IP 并存数限制。

### 单镜像（单机最省，不用 compose）

```bash
docker build -f Dockerfile -t yue2-allinone .
docker run -d --gpus all -p 8000:8000 -v outputs:/app/outputs --env-file .env --name yue2 yue2-allinone
```

### 用预构建镜像（免构建）

Actions 里手动跑 `Docker 镜像构建` 工作流会推到 GHCR（见 `.github/workflows/docker-build.yml`），之后部署机在 `.env` 里加两行即可免构建部署：

```bash
YUE2_API_IMAGE=ghcr.io/<owner>/yue2-api:<tag>
YUE2_WEB_IMAGE=ghcr.io/<owner>/yue2-web:<tag>
docker compose pull && docker compose up -d   # 注意：不要再加 --build
```

> 注意：GHCR 的 `yue2-api` 镜像**不含** `yue2`/`torch` 重依赖（体积原因，Dockerfile 里是注释说明的）。
> 用它部署时，需按 YuE2 官方指引把模型依赖装进镜像（`backend/Dockerfile` 里加一行 `RUN pip install`），
> 否则服务只能启动、`/healthz` 报 `degraded`，生成报 503。

管理页：`http://<host>/#/admin`，请求头 `Authorization: Bearer $ADMIN_TOKEN`。`ADMIN_TOKEN` 为空则 `/api/admin/*` 直接 403（默认安全）。

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/`、`/#/admin` | 新版前端（`web/dist`，缺失则 404 提示先构建 / 访问 web 服务） |
| GET | `/healthz` | 公开健康（`model_loaded / worker_alive / queue_pending / history_count`，无敏感明细） |
| POST | `/api/generate` | 入队 202 `{task_id, queue_position, seed}`，轮询任务；每 IP 每小时限 `YUE2_SUBMIT_PER_HOUR` 次（配额/IP 挡掉的不计数） |
| GET | `/api/config` | 公开配置（长度上限 `max_title/style/lyrics_len`、`require_api_key`，前端动态取） |
| GET | `/api/tasks/{id}` | `pending / running / succeeded(+record) / failed(+error)` |
| GET | `/api/history?limit=&offset=&q=` | 无 `limit` 兼容老数组；有则 `{total, items}` |
| GET | `/audio/{id}.flac` | 仅音频子目录，`history.json` 永不静态暴露 |
| GET | `/api/admin/health` | 模型/ worker 详情（含 `model_error` 原文，需管理鉴权） |
| GET | `/api/admin/queue` | 排队+运行中（需管理鉴权） |
| POST | `/api/admin/tasks/{id}/cancel` | 取消排队中任务（运行中 409） |
| GET | `/api/admin/tasks?status=&limit=` | 全量任务表 |
| DELETE | `/api/admin/history/{id}` | 管理删档（含文件；运行中 409，防 worker 复活） |
| GET | `/api/admin/disk` | 音频数/字节/磁盘余量/排队 |
| GET | `/api/admin/logs?tail=` | 内存日志环（500 条） |
| GET/PUT | `/api/admin/config` | 查看/热更新 `max_queue, submit_per_hour, max_pending_per_ip, require_api_key, log_level`（重启后以 env 为准） |
| GET | `/api/admin/keys` | Key 列表（只显示前缀，不含哈希与明文） |
| POST | `/api/admin/keys` | 新建 Key `{name, quota_total, note}`，明文 `api_key` 只返回一次 |
| POST | `/api/admin/keys/{id}/reset` | 换 Key：旧 Key 立即失效，明文只返回一次，配额/历史保留 |
| PATCH/DELETE | `/api/admin/keys/{id}` | 改配额/启停/备注；删 Key（其历史歌曲保留归属名） |
| GET | `/api/admin/keys/{id}/history` | 该 Key 的歌 |
| POST | `/api/auth/login` | 用户登录 `{username, key}`，返回配额视图 |
| GET | `/api/auth/me`、`GET /api/auth/history` | 自查配额 / 只看我的歌（请求头 `X-API-Key: 用户名:secret`） |
| DELETE | `/api/auth/history/{id}` | 用户删自己的歌（别人的 403，运行中 409） |

`POST /api/generate`：`title(≤100) / style(1~2000) / lyrics(1~10000) / cot(full|none) / seed(0~2^31-1, null=随机)`（上限取后端配置，前端经 `/api/config` 动态取，不再硬编码）。
配额（429 时看返回文案区分）：全局排队上限 `YUE2_MAX_QUEUE`；每 IP 每小时提交数 `YUE2_SUBMIT_PER_HOUR`（0=不限，配额/IP 挡掉的不计数）；
匿名每 IP 并存任务数 `YUE2_MAX_PENDING_PER_IP`（pending+running，0=不限；登录用户走 Key 配额，不再叠加 IP 限制）；每用户并存任务数 `YUE2_MAX_PENDING_PER_KEY`（pending+running，0=不限，防一人塞满队列）；Key 配额（成功生成的首数，`/me` 的 `quota_left` 已扣在途）。
前三者 + `REQUIRE_API_KEY` 都可在管理页 config 标签热更新。默认 `REQUIRE_API_KEY=true`（强制登录），匿名一律 401。

## 用户体系（API Key）

无账号密码体系，凭“用户名 + Key”登录（Studio 页历史列表上方；用户名禁 `:`/空白）：

1. 管理员在 `#/admin` → keys 标签新建 Key：填用户名、配额（可生成首数，0=不限）、备注，明文 Key 只显示一次，立即复制发给用户。
2. 用户登录后：生成自动记到名下并扣配额（**只有成功生成的才计**，失败/取消不计；在途占用配额，`/me` 的 `quota_left` 已扣在途），历史列表可切“只看我的”，配额条实时显示已用/总量。
3. 管理员可随时改配额、停用、删除 Key，看每个 Key 的注册时间、最后使用时间、做了几首歌、歌曲列表。删 Key 不删歌（归属名保留）。
4. 库里只存 Key 的 SHA256 哈希，明文无法找回；用户丢 Key 点“换Key”发个新的（旧 Key 立即失效，配额与历史不变）。
5. 默认 `REQUIRE_API_KEY=true`（强制登录）：匿名一律 401，必须登录才能生成；内网自用可在管理页关掉。

## 数据

`outputs/audio/*.flac`、`outputs/artifacts/<id>/`、`outputs/history.db`（SQLite，历史记录）、`outputs/pending.json`（排队快照）。
老 `history.json` 首次启动自动一次性导入 SQLite 并改名 `history.json.migrated`（原文件保留可查）。
compose 用命名卷 `outputs`，备份请直接快照该卷。
