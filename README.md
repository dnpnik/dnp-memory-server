# DNP Memory Server

Сервер внешней памяти для Custom GPT Actions.

## Запуск локально

pip install -r requirements.txt

uvicorn main:app --reload

## Основные адреса

/health  
/privacy  
/api/memory/search  
/api/memory/save  
/api/feedback/save  
/api/case-note/save