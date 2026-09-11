import os
import json
import uuid
import random
import datetime
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from yue2 import YuE2Pipeline

# ----------------- 目录与持久化 -----------------
MODEL_REPO = "m-a-p/YuE2-3B"
OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = OUTPUT_DIR / "history.json"

app = FastAPI(title="YuE2 Music Studio")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/audio", StaticFiles(directory=str(OUTPUT_DIR)), name="audio")
gpu_lock = threading.Lock()

print("⏳ 正在加载 YuE2 模型到显存，请稍候...")
pipe = YuE2Pipeline.from_pretrained(MODEL_REPO, device="cuda")
print("✅ 模型加载成功，服务就绪！")


def get_all_history():
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def add_history_record(record: dict):
    history = get_all_history()
    history.insert(0, record)
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class GenerateRequest(BaseModel):
    title: Optional[str] = "未命名歌曲"
    style: str
    lyrics: str
    cot: Optional[str] = "full"
    seed: Optional[int] = None  # 默认 None，表示随机


@app.get("/")
def read_root():
    return FileResponse("index.html")


@app.get("/api/history")
def fetch_history():
    return get_all_history()


@app.post("/api/generate")
def generate_music(req: GenerateRequest):
    if not gpu_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=429, detail="当前显卡正在生成其他音乐，请稍等前一个任务完成！"
        )

    try:
        task_id = uuid.uuid4().hex[:8]
        filename = f"{task_id}.flac"
        file_path = OUTPUT_DIR / filename
        artifacts_dir = OUTPUT_DIR / f"{task_id}_artifacts"

        title = req.title.strip() if req.title and req.title.strip() else "未命名歌曲"

        # 处理种子：若未填写或小于0，则生成一个确定的随机种子并落库
        if req.seed is None or req.seed < 0:
            actual_seed = random.randint(1, 2**31 - 1)
        else:
            actual_seed = req.seed

        print(f"🎵 开始生成 [{task_id}] - 歌名: {title} | Seed: {actual_seed}")

        # 调用 Pipeline
        song = pipe(style=req.style, lyrics=req.lyrics, cot=req.cot, seed=actual_seed)

        song.save(str(file_path))
        song.save_artifacts(str(artifacts_dir))

        # 完整记录制作信息
        record = {
            "task_id": task_id,
            "title": title,
            "style": req.style,
            "lyrics": req.lyrics,
            "seed": actual_seed,
            "cot": req.cot,
            "audio_url": f"/audio/{filename}",
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        add_history_record(record)

        return {"status": "success", **record}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        gpu_lock.release()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
