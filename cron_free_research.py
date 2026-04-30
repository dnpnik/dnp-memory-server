import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

import psycopg2
import psycopg2.extras


DATABASE_URL = os.getenv("DATABASE_URL")

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


def save_memory(title: str, content: str):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO memories (title, content, project, tags, importance, type, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        title,
        content,
        "legal_work",
        "github_actions,free_research,dogazification,legal",
        "normal",
        "github_actions_research",
        datetime.utcnow()
    ))

    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return row["id"]


def google_news_rss_search(query: str, limit: int = 10):
    encoded_query = urllib.parse.quote_plus(query)
    url = (
        "https://news.google.com/rss/search?"
        f"q={encoded_query}&hl=ru&gl=RU&ceid=RU:ru"
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read()

    root = ET.fromstring(data)

    items = []
    channel = root.find("channel")

    if channel is None:
        return items

    for item in channel.findall("item")[:limit]:
        title = item.findtext("title", default="")
        link = item.findtext("link", default="")
        pub_date = item.findtext("pubDate", default="")
        description = item.findtext("description", default="")

        items.append({
            "title": title,
            "link": link,
            "pub_date": pub_date,
            "description": description
        })

    return items


def run():
    init_db()

    total_saved = 0

    for topic in TOPICS:
        print(f"Searching: {topic}")

        try:
            results = google_news_rss_search(topic, limit=10)
        except Exception as e:
            print(f"Search failed for {topic}: {e}")
            continue

        if not results:
            print(f"No results for: {topic}")
            continue

        parts = []
        parts.append(f"АВТОПОИСК GITHUB ACTIONS")
        parts.append(f"Тема: {topic}")
        parts.append(f"Дата поиска UTC: {datetime.utcnow().isoformat()}")
        parts.append("")
        parts.append("Найденные источники:")

        for i, item in enumerate(results, start=1):
            parts.append("")
            parts.append(f"{i}. {item['title']}")
            parts.append(f"Дата публикации: {item['pub_date']}")
            parts.append(f"Ссылка: {item['link']}")
            parts.append(f"Описание: {item['description']}")

        parts.append("")
        parts.append("ВНИМАНИЕ:")
        parts.append(
            "Запись создана автоматически бесплатным поиском через RSS. "
            "Перед использованием в суде обязательно проверить источник, дату, текст судебного акта или нормативного документа."
        )

        memory_id = save_memory(
            title=f"Автопоиск GitHub Actions: {topic}",
            content="\n".join(parts)
        )

        print(f"Saved memory id: {memory_id}")
        total_saved += 1

    print(f"Done. Total saved: {total_saved}")


if __name__ == "__main__":
    run()