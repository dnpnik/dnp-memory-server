import io
import os
from datetime import datetime
from typing import List, Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.auth.transport.requests import AuthorizedSession

from pypdf import PdfReader


app = FastAPI(
    title="DNP Self Learning Memory API",
    version="1.2.4",
    description="External PostgreSQL memory server with Google Drive search/read endpoints for Custom GPT Actions."
)

DATABASE_URL = os.getenv("DATABASE_URL")
API_KEY = os.getenv("MEMORY_API_KEY", "change-me")

GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "service_account.json"
)

GOOGLE_DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly"
]


# =========================
# COMMON HELPERS
# =========================

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
    Checks x-api-key header from GPT Action request.
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


def get_drive_service():
    """
    Creates Google Drive API service using service_account.json.

    On Render recommended:
    GOOGLE_SERVICE_ACCOUNT_FILE=/etc/secrets/service_account.json
    """
    if not os.path.exists(GOOGLE_SERVICE_ACCOUNT_FILE):
        raise HTTPException(
            status_code=500,
            detail=f"Google service account file not found: {GOOGLE_SERVICE_ACCOUNT_FILE}"
        )

    credentials = service_account.Credentials.from_service_account_file(
        GOOGLE_SERVICE_ACCOUNT_FILE,
        scopes=GOOGLE_DRIVE_SCOPES
    )

    return build("drive", "v3", credentials=credentials)


def escape_drive_query(value: str) -> str:
    """
    Escapes single quotes and backslashes for Google Drive query syntax.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def limit_text(text: str, max_chars: int = 120000) -> str:
    """
    Limits extracted text so GPT Action response is not too huge.
    """
    if not text:
        return ""

    if len(text) <= max_chars:
        return text

    return text[:max_chars] + "\n\n...[TEXT TRUNCATED]..."


def download_drive_file_to_bytes(
    service,
    file_id: str,
    download_url: Optional[str] = None
) -> bytes:
    """
    Downloads binary file content from Google Drive into bytes.

    Strategy:
    1. First tries Drive API get_media.
    2. If get_media fails and download_url is provided, tries authorized webContentLink download.
    """
    try:
        request = service.files().get_media(fileId=file_id)

        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()

        return buffer.getvalue()

    except Exception as api_error:
        if not download_url:
            raise HTTPException(
                status_code=500,
                detail=f"Drive get_media failed and no download_url provided: {api_error}"
            )

        try:
            # googleapiclient service created by build() usually keeps authorized http here
            credentials = service._http.credentials
            session = AuthorizedSession(credentials)

            response = session.get(download_url)
            response.raise_for_status()

            return response.content

        except Exception as link_error:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Drive download failed. "
                    f"get_media error: {api_error}; "
                    f"webContentLink error: {link_error}"
                )
            )


def export_google_file_to_bytes(service, file_id: str, export_mime_type: str) -> bytes:
    """
    Exports Google Docs/Sheets/Slides to bytes.
    Kept for future use.
    """
    request = service.files().export_media(
        fileId=file_id,
        mimeType=export_mime_type
    )

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    return buffer.getvalue()


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """
    Extracts text from PDF using pypdf.
    Works for text PDFs.
    For scanned PDFs, OCR is needed separately.
    """
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages_text = []

        for index, page in enumerate(reader.pages, start=1):
            try:
                page_text = page.extract_text() or ""
            except Exception as page_error:
                page_text = f"[Page {index}: text extraction error: {page_error}]"

            if page_text.strip():
                pages_text.append(f"\n\n--- PAGE {index} ---\n{page_text}")

        extracted = "\n".join(pages_text).strip()

        if not extracted:
            return (
                "PDF file was downloaded, but no text was extracted. "
                "Most likely this is a scanned/image PDF and OCR is required."
            )

        return extracted

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF text extraction error: {e}")


# =========================
# STARTUP
# =========================

@app.on_event("startup")
def startup_event():
    init_db()


# =========================
# MODELS
# =========================

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


# =========================
# BASIC ROUTES
# =========================

@app.get("/")
def root():
    return {
        "ok": True,
        "status": "ok",
        "message": "DNP Memory Server with PostgreSQL and Google Drive is running"
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "status": "ok",
        "database": "postgresql",
        "drive": "google_drive_readonly"
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


# =========================
# MEMORY ROUTES
# =========================

@app.post("/api/memory/search")
def search_memory(request: SearchRequest, x_api_key: Optional[str] = Header(None)):
    check_api_key(x_api_key)

    query_like = f"%{request.query}%"
    limit = max(1, min(request.limit, 50))

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
            limit
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
            limit
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
        "ok": True,
        "count": len(results),
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
        "ok": True,
        "saved": True,
        "id": str(row["id"]),
        "title": request.title,
        "project": request.project,
        "tags": request.tags or [],
        "importance": request.importance,
        "created_at": created_at.isoformat()
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
        "ok": True,
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
        "ok": True,
        "saved": True,
        "id": str(case_row["id"])
    }


# =========================
# GOOGLE DRIVE ROUTES
# =========================

@app.get("/drive/search")
def search_drive_files(
    q: str = Query(..., description="Search query, file name, phrase, or keyword"),
    folder_id: Optional[str] = Query(None, description="Optional Google Drive folder ID"),
    limit: int = Query(10, description="Maximum number of files to return"),
    x_api_key: Optional[str] = Header(None),
):
    """
    Searches Google Drive by file name or indexed full text.
    Requires service account access to target folders/files.
    """
    check_api_key(x_api_key)

    try:
        service = get_drive_service()

        safe_query = escape_drive_query(q)

        query_parts = [
            "trashed = false",
            f"(name contains '{safe_query}' or fullText contains '{safe_query}')"
        ]

        if folder_id:
            safe_folder_id = escape_drive_query(folder_id)
            query_parts.append(f"'{safe_folder_id}' in parents")

        drive_query = " and ".join(query_parts)

        response = service.files().list(
            q=drive_query,
            pageSize=max(1, min(limit, 50)),
            fields="files(id,name,mimeType,webViewLink,webContentLink,modifiedTime,size)"
        ).execute()

        files = response.get("files", [])

        return {
            "ok": True,
            "query": q,
            "folder_id": folder_id,
            "count": len(files),
            "files": [
                {
                    "file_id": item.get("id"),
                    "name": item.get("name"),
                    "mimeType": item.get("mimeType"),
                    "webViewLink": item.get("webViewLink") or f"https://drive.google.com/file/d/{item.get('id')}/view",
                    "downloadUrl": item.get("webContentLink"),
                    "modifiedTime": item.get("modifiedTime"),
                    "size": item.get("size")
                }
                for item in files
            ]
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/drive/read")
def read_drive_file(
    file_id: str = Query(..., description="Google Drive file ID"),
    max_chars: int = Query(120000, description="Maximum extracted text characters"),
    name: Optional[str] = Query(None, description="Optional file name from search result"),
    mime_type: Optional[str] = Query(None, description="Optional mime type from search result"),
    download_url: Optional[str] = Query(None, description="Optional webContentLink from search result"),
    x_api_key: Optional[str] = Header(None),
):
    """
    Reads text from a Google Drive file by file_id.

    Important:
    This version does NOT call files().get metadata because some Drive API
    configurations return HttpError 400 on metadata fields.

    Strategy:
    1. Try Drive API get_media.
    2. If it fails, try authorized download_url / webContentLink.
    """
    check_api_key(x_api_key)

    try:
        service = get_drive_service()

        file_name = name or ""
        file_mime_type = mime_type or ""

        raw_bytes = download_drive_file_to_bytes(
            service,
            file_id,
            download_url=download_url
        )

        text = ""

        # PDF files or unknown binary that starts as PDF
        if (
            file_mime_type == "application/pdf"
            or file_name.lower().endswith(".pdf")
            or raw_bytes[:4] == b"%PDF"
        ):
            text = extract_pdf_text(raw_bytes)

        # Text-like files
        elif (
            file_mime_type in [
                "text/plain",
                "text/html",
                "text/csv",
                "application/json",
                "application/xml",
                "text/xml",
                "text/markdown"
            ]
            or file_name.lower().endswith((
                ".txt",
                ".md",
                ".csv",
                ".json",
                ".xml",
                ".html",
                ".htm"
            ))
        ):
            text = raw_bytes.decode("utf-8", errors="replace")

        # Fallback: try UTF-8 decode
        else:
            try:
                text = raw_bytes.decode("utf-8", errors="replace")
            except Exception:
                text = (
                    "File was downloaded, but direct text extraction is not supported "
                    "for this file type. For DOCX add python-docx; for scanned PDF add OCR."
                )

        return {
            "ok": True,
            "file_id": file_id,
            "name": file_name,
            "mimeType": file_mime_type,
            "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
            "downloadUrlUsed": bool(download_url),
            "modifiedTime": None,
            "size": len(raw_bytes),
            "text": limit_text(text, max_chars=max_chars)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))