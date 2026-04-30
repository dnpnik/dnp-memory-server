import os
import psycopg2
import psycopg2.extras
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel


app = FastAPI(
    title="DNP Self Learning Memory API",
    version="1.0.0",
    description="External PostgreSQL memory server for Custom GPT Actions."
)

DATABASE_URL = os.getenv("DATABASE_URL")
API_KEY = os.getenv("MEMORY_API_KEY", "change-me")


def normalize_database_url(url: str) -> str:
    """
    Some services use postgres:// instead of postgresql://.
    psycopg2 works better when the prefix is postgresql://.
    """
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def get_conn():
    """
    Creates a PostgreSQL connection using DATABASE_URL from environment variables.
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    return psycopg2.connect(
        normalize_database_url(DATABASE_URL),
        cursor_factory=psycopg2.extras.RealDictCursor
    )


def check_api_key(x_api_key: Optional[str]):
    """
    Checks X-API-Key header from GPT Action request.
    """
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def init_db():
    """
    Creates required tables if they do not exist.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS memories (
        id SERIAL PRIMARY KEY,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        project TEXT,
        tags TEXT,
        importance TEXT DEFAULT 'normal',
        type TEXT DEFAULT 'memory',
        created_at TIMESTAMP NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS feedback (
        id SERIAL PRIMARY KEY,
        feedback TEXT NOT NULL,
        bad_answer TEXT,
        corrected_answer TEXT,
        project TEXT,
        tags TEXT,
        created_at TIMESTAMP NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS case_notes (
        id SERIAL PRIMARY KEY,
        case_name TEXT,
        note TEXT NOT NULL,
        category TEXT DEFAULT 'other',
        importance TEXT DEFAULT 'normal',
        tags TEXT,
        created_at TIMESTAMP NOT NULL
    )
    """)

    conn.commit()
    cur.close()
    conn.close()


@app.on_event("startup")
def startup_event():
    init_db()


class SearchRequest(BaseModel):
    query: str
    project: Optional[str] = None
    limit: int = 5


class MemorySaveRequest(BaseModel):
    title: str
    content: str
    project: Optional[str] = None
    tags: Optional[List[str]] = []
    importance: Optional[str] = "normal"


class FeedbackSaveRequest(BaseModel):
    feedback: str
    bad_answer: Optional[str] = None
    corrected_answer: Optional[str] = None
    project: Optional[str] = None
    tags: Optional[List[str]] = []


class CaseNoteSaveRequest(BaseModel):
    case_name: Optional[str] = None
    note: str
    category: Optional[str] = "other"
    importance: Optional[str] = "normal"
    tags: Optional[List[str]] = []


@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "DNP Memory Server with PostgreSQL is running"
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "database": "postgresql"
    }


@app.get("/privacy", response_class=HTMLResponse)
def privacy_policy():
    return """
    <html>
      <head>
        <title>Privacy Policy</title>
      </head>
      <body>
        <h1>Политика конфиденциальности</h1>
        <p>Этот сервер используется для сохранения и поиска пользовательских заметок, правил, судебных позиций, шаблонов и обратной связи.</p>

        <h2>Какие данные могут сохраняться</h2>
        <ul>
          <li>текстовые заметки пользователя;</li>
          <li>рабочие правила;</li>
          <li>проектные выводы;</li>
          <li>судебные формулировки;</li>
          <li>шаблоны документов;</li>
          <li>обратная связь по качеству ответов.</li>
        </ul>

        <h2>Какие данные не следует сохранять без отдельного разрешения</h2>
        <ul>
          <li>пароли;</li>
          <li>паспортные данные;</li>
          <li>банковские данные;</li>
          <li>точный адрес проживания;</li>
          <li>медицинские диагнозы;</li>
          <li>иные чувствительные персональные сведения.</li>
        </ul>

        <p>Данные используются только для улучшения качества ответов пользовательского GPT.</p>
      </body>
    </html>
    """


@app.post("/api/memory/search")
def search_memory(request: SearchRequest, x_api_key: Optional[str] = Header(None)):
    check_api_key(x_api_key)

    query_like = f"%{request.query}%"
    conn = get_conn()
    cur = conn.cursor()

    if request.project:
        cur.execute("""
            SELECT id, title, content, project, tags, importance, type, created_at
            FROM memories
            WHERE project = %s
              AND (title ILIKE %s OR content ILIKE %s OR tags ILIKE %s)
            ORDER BY id DESC
            LIMIT %s
        """, (
            request.project,
            query_like,
            query_like,
            query_like,
            request.limit
        ))
    else:
        cur.execute("""
            SELECT id, title, content, project, tags, importance, type, created_at
            FROM memories
            WHERE title ILIKE %s OR content ILIKE %s OR tags ILIKE %s
            ORDER BY id DESC
            LIMIT %s
        """, (
            query_like,
            query_like,
            query_like,
            request.limit
        ))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    results = []
    for row in rows:
        results.append({
            "id": str(row["id"]),
            "title": row["title"],
            "content": row["content"],
            "project": row["project"],
            "tags": row["tags"].split(",") if row["tags"] else [],
            "importance": row["importance"],
            "type": row["type"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None
        })

    return {
        "results": results
    }


@app.post("/api/memory/save")
def save_memory(request: MemorySaveRequest, x_api_key: Optional[str] = Header(None)):
    check_api_key(x_api_key)

    tags = ",".join(request.tags or [])
    created_at = datetime.utcnow()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO memories (title, content, project, tags, importance, type, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        request.title,
        request.content,
        request.project,
        tags,
        request.importance,
        "memory",
        created_at
    ))

    row = cur.fetchone()
    conn.commit()

    cur.close()
    conn.close()

    return {
        "saved": True,
        "id": str(row["id"])
    }


@app.post("/api/feedback/save")
def save_feedback(request: FeedbackSaveRequest, x_api_key: Optional[str] = Header(None)):
    check_api_key(x_api_key)

    tags = ",".join(request.tags or [])
    created_at = datetime.utcnow()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO feedback (feedback, bad_answer, corrected_answer, project, tags, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        request.feedback,
        request.bad_answer,
        request.corrected_answer,
        request.project,
        tags,
        created_at
    ))

    feedback_row = cur.fetchone()

    title = "Исправление пользователя"
    content = f"Ошибка/обратная связь: {request.feedback}\n"

    if request.bad_answer:
        content += f"Плохой вариант: {request.bad_answer}\n"

    if request.corrected_answer:
        content += f"Правильный вариант: {request.corrected_answer}\n"

    cur.execute("""
        INSERT INTO memories (title, content, project, tags, importance, type, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        title,
        content,
        request.project,
        tags,
        "high",
        "feedback",
        created_at
    ))

    conn.commit()

    cur.close()
    conn.close()

    return {
        "saved": True,
        "id": str(feedback_row["id"])
    }


@app.post("/api/case-note/save")
def save_case_note(request: CaseNoteSaveRequest, x_api_key: Optional[str] = Header(None)):
    check_api_key(x_api_key)

    tags = ",".join(request.tags or [])
    created_at = datetime.utcnow()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO case_notes (case_name, note, category, importance, tags, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        request.case_name,
        request.note,
        request.category,
        request.importance,
        tags,
        created_at
    ))

    case_row = cur.fetchone()

    title = f"Судебная заметка: {request.category}"
    content = request.note

    cur.execute("""
        INSERT INTO memories (title, content, project, tags, importance, type, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        title,
        content,
        request.case_name,
        tags,
        request.importance,
        "case_note",
        created_at
    ))

    conn.commit()

    cur.close()
    conn.close()

    return {
        "saved": True,
        "id": str(case_row["id"])
    }
