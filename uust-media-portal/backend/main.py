import json
import sqlite3
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List

from scraper import fetch_live_uust_news
from moderator_algo import inspect_article_non_ai

DB_PATH = Path(__file__).parent / "uust_media.db"

app = FastAPI(
    title="УУНиТ.Медиа API",
    description="Бэкенд новостного портала УУНиТ с системой скинов и 3-этапной параллельной модерацией без ИИ",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                preferred_skin TEXT NOT NULL DEFAULT 'mombo'
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS limits_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                title_min INTEGER NOT NULL,
                title_max INTEGER NOT NULL,
                body_min INTEGER NOT NULL,
                body_max INTEGER NOT NULL,
                max_files INTEGER NOT NULL,
                max_file_size_mb INTEGER NOT NULL,
                stop_words TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                body TEXT NOT NULL,
                source_url TEXT NOT NULL,
                author TEXT NOT NULL,
                author_username TEXT NOT NULL,
                media_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                spell_status TEXT NOT NULL DEFAULT 'pending',
                content_status TEXT NOT NULL DEFAULT 'pending',
                fact_status TEXT NOT NULL DEFAULT 'pending',
                revision_reason TEXT NOT NULL DEFAULT ''
            )
        """)
        cur.execute("SELECT COUNT(*) FROM limits_config")
        if cur.fetchone()[0] == 0:
            cur.execute("""
                INSERT INTO limits_config VALUES (1, 15, 120, 100, 3000, 4, 5, 'шок,сенсация,срочно в номер,кликбейт,фейк')
            """)
        conn.commit()


@app.on_event("startup")
def on_startup():
    init_db()


class ArticleCreatePayload(BaseModel):
    title: str
    category: str
    body: str
    source_url: str
    author: str
    author_username: str
    media: List[str]


class ModerationDecisionPayload(BaseModel):
    moderator_role: str  # mod_spell | mod_content | mod_fact | admin
    stage: str           # spell | content | fact
    decision: str        # approved | revision
    comment: str = ""


@app.get("/api/uust-live")
async def get_live_uust(limit: int = 30):
    items = await fetch_live_uust_news(limit=limit)
    return {"source": "https://uust.ru/news/", "count": len(items), "items": items}


@app.post("/api/articles")
def submit_article(payload: ArticleCreatePayload):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT title_min, title_max, body_min, body_max, max_files, stop_words FROM limits_config WHERE id = 1")
        t_min, t_max, b_min, b_max, m_max, stop_words_csv = cur.fetchone()

        if not (t_min <= len(payload.title.strip()) <= t_max):
            raise HTTPException(status_code=400, detail=f"Заголовок должен быть от {t_min} до {t_max} символов")
        if not (b_min <= len(payload.body.strip()) <= b_max):
            raise HTTPException(status_code=400, detail=f"Текст должен быть от {b_min} до {b_max} символов")
        if not (0 <= len(payload.media) <= m_max):
            raise HTTPException(status_code=400, detail=f"Количество медиафайлов: от 1 до {m_max}")

        algo_report = inspect_article_non_ai(payload.title, payload.body, stop_words_csv.split(","))
        cur.execute("""
            INSERT INTO articles (title, category, body, source_url, author, author_username, media_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            payload.title.strip(),
            payload.category,
            payload.body.strip(),
            payload.source_url,
            payload.author,
            payload.author_username,
            json.dumps(payload.media, ensure_ascii=False)
        ))
        conn.commit()
        return {"id": cur.lastrowid, "status": "pending", "algo_report": algo_report}


@app.post("/api/articles/{article_id}/moderate")
def moderate_article(article_id: int, payload: ModerationDecisionPayload):
    role_to_stage = {
        "mod_spell": "spell",
        "mod_content": "content",
        "mod_fact": "fact",
    }
    if payload.moderator_role != "admin" and role_to_stage.get(payload.moderator_role) != payload.stage:
        raise HTTPException(status_code=403, detail="Роль модератора не соответствует выбранному этапу проверки")

    column_map = {
        "spell": "spell_status",
        "content": "content_status",
        "fact": "fact_status"
    }
    col = column_map.get(payload.stage)
    if not col:
        raise HTTPException(status_code=400, detail="Некорректный этап")

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        if payload.decision == "revision":
            cur.execute(
                f"UPDATE articles SET {col} = 'revision', status = 'revision', revision_reason = ? WHERE id = ?",
                (payload.comment, article_id)
            )
        else:
            cur.execute(f"UPDATE articles SET {col} = 'approved' WHERE id = ?", (article_id,))
            cur.execute("SELECT spell_status, content_status, fact_status FROM articles WHERE id = ?", (article_id,))
            row = cur.fetchone()
            if row and all(s == "approved" for s in row):
                cur.execute("UPDATE articles SET status = 'published', revision_reason = '' WHERE id = ?", (article_id,))
        conn.commit()
        return {"article_id": article_id, "updated_stage": payload.stage, "decision": payload.decision}


# Умный поиск index.html (ищет в папке frontend или рядом с main.py)
BASE_DIR = Path(__file__).resolve().parent
POSSIBLE_PATHS = [
    BASE_DIR / "index.html",
    BASE_DIR / "frontend" / "index.html",
    BASE_DIR.parent / "frontend" / "index.html",
]

@app.get("/")
def serve_Root():
    for path in POSSIBLE_PATHS:
        if path.exists():
            return FileResponse(str(path))
    return {
        "error": "Файл index.html не найден!",
        "checked_paths": [str(p) for p in POSSIBLE_PATHS]
    }