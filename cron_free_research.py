import os
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime


API_BASE_URL = os.getenv("API_BASE_URL", "https://dnp-memory-server.onrender.com/api")
MEMORY_API_KEY = os.getenv("MEMORY_API_KEY")


TOPICS = [
    "догазификация согласие основного абонента судебная практика",
    "платность согласия основного абонента догазификация",
    "статья 159 УК РФ гражданско-правовой спор Верховный Суд",
    "догазификация бесплатное подключение судебная практика",
    "ПП РФ 1547 согласие основного абонента",
]


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
    channel = root.find("channel")

    items = []

    if channel is None:
        return items

    for item in channel.findall("item")[:limit]:
        items.append({
            "title": item.findtext("title", default=""),
            "link": item.findtext("link", default=""),
            "pub_date": item.findtext("pubDate", default=""),
            "description": item.findtext("description", default="")
        })

    return items


def save_memory_via_api(title: str, content: str):
    if not MEMORY_API_KEY:
        raise RuntimeError("MEMORY_API_KEY is not set")

    url = f"{API_BASE_URL}/memory/save"

    payload = {
        "title": title,
        "content": content,
        "project": "legal_work",
        "tags": [
            "github_actions",
            "free_research",
            "dogazification",
            "legal"
        ],
        "importance": "normal"
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": MEMORY_API_KEY
        }
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        response_body = response.read().decode("utf-8")
        return response.status, response_body


def run():
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
        parts.append("АВТОПОИСК GITHUB ACTIONS")
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
            "Запись создана автоматически бесплатным поиском через GitHub Actions и RSS. "
            "Перед использованием в суде обязательно проверить источник, дату, текст судебного акта или нормативного документа."
        )

        title = f"Автопоиск GitHub Actions: {topic}"
        content = "\n".join(parts)

        try:
            status, response_body = save_memory_via_api(title, content)
            print(f"Saved via API. Status: {status}. Response: {response_body}")
            total_saved += 1
        except Exception as e:
            print(f"Save failed for {topic}: {e}")

    print(f"Done. Total saved: {total_saved}")


if __name__ == "__main__":
    run()