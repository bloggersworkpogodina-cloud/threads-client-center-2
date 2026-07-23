import hashlib, json, os, re
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

def extract_sheet_id(url):
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url or "")
    return m.group(1) if m else None

def _service():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON не задан")
    info = json.loads(raw)
    credentials = Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)

def _normalize_date(value):
    value = (value or "").strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None

def read_posts_for_date(sheet_url, target_date):
    sheet_id = extract_sheet_id(sheet_url)
    if not sheet_id:
        raise ValueError("Некорректная ссылка Google Таблицы")
    service = _service()
    meta = service.spreadsheets().get(spreadsheetId=sheet_id, fields="sheets.properties.title").execute()
    if not meta.get("sheets"):
        return []
    title = meta["sheets"][0]["properties"]["title"]
    rows = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{title}'!A:D"
    ).execute().get("values", [])
    if not rows:
        return []
    headers = [str(v).strip().lower() for v in rows[0]]
    required = ["дата", "время", "ветка", "статус"]
    for name in required:
        if name not in headers:
            raise ValueError("Нужны колонки: Дата | Время | Ветка | Статус")
    idx = {name: headers.index(name) for name in required}
    posts = []
    for row in rows[1:]:
        def cell(name):
            i = idx[name]
            return str(row[i]).strip() if i < len(row) else ""
        if _normalize_date(cell("дата")) != target_date:
            continue
        if cell("статус").lower() != "готово":
            continue
        text = cell("ветка")
        if not text:
            continue
        time_value = cell("время")
        fp = hashlib.sha256(f"{target_date.isoformat()}|{time_value}|{text}".encode()).hexdigest()
        posts.append({"time": time_value, "text": text, "fingerprint": fp})
    posts.sort(key=lambda x: x["time"])
    return posts
