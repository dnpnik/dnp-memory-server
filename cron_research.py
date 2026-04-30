import os
from datetime import datetime

import psycopg2
import psycopg2.extras
from tavily import TavilyClient


DATABASE_URL = os.getenv("DATABASE_URL")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")


TOPICS = [
    "догазификация согласие основного абонента судебная практика",
    "платность согласия основного абонента догазификация",
    "статья 159 УК РФ гражданско-правовой спор Верховный Суд",
    "догазификация бесплатное подключение судебная практика",
    "ПП РФ 1547 согласие основного абонента",
]


def normalize_database_url(url: str) -> str:
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    return psycopg2.connect(
        normalize_database_url(DATABASE_URL),
        cursor_factory=psycopg2.extras.RealDictCursor
    )


def init_db():
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

    conn.commit()
    cur.close()
    conn.close()


def save_memory(title: str, content: str, project: str, tags: str, importance: str = "normal"):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO memories (title, content, project, tags, importance, type, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        title,
        content,
        project,
        tags,
        importance,
        "cron_research",
        datetime.utcnow()
    ))

    row = cur.fetchone()
    conn.commit()

    cur.close()
    conn.close()

    return row["id"]


def run_search():
    if not TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY is not set")

    client = TavilyClient(api_key=TAVILY_API_KEY)

    init_db()

    saved_count = 0

    for topic in TOPICS:
        print(f"Searching topic: {topic}")

        response = client.search(
            query=topic,
            max_results=5,
            search_depth="advanced"
        )

        results = response.get("results", [])

        if not results:
            print(f"No results for topic: {topic}")
            continue

        content_parts = []
        content_parts.append(f"ТЕМА ПОИСКА: {topic}")
        content_parts.append(f"ДАТА ПОИСКА UTC: {datetime.utcnow().isoformat()}")
        content_parts.append("")
        content_parts.append("НАЙДЕННЫЕ ИСТОЧНИКИ:")

        for idx, item in enumerate(results, start=1):
            title = item.get("title", "")
            url = item.get("url", "")
            snippet = item.get("content", "")

            content_parts.append("")
            content_parts.append(f"{idx}. {title}")
            content_parts.append(f"Источник: {url}")
            content_parts.append(f"Кратко: {snippet}")

        content_parts.append("")
        content_parts.append("ПРЕДВАРИТЕЛЬНЫЙ ВЫВОД:")
        content_parts.append(
            "Эта запись создана автоматически cron-поиском. "
            "Перед использованием в суде проверить источник, дату, актуальность и применимость к делу."
        )

        memory_title = f"Автопоиск: {topic}"

        memory_content = "\n".join(content_parts)

        save_memory(
            title=memory_title,
            content=memory_content,
            project="legal_work",
            tags="cron,autosearch,dogazification,legal",
            importance="normal"
        )

        saved_count += 1

    print(f"Done. Saved records: {saved_count}")


if __name__ == "__main__":
    run_search()