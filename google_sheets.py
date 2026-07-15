from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly", "https://www.googleapis.com/auth/drive.readonly"]


def _norm_header(value: Any) -> str:
    return str(value or "").strip().lower().replace("ё", "е")


def _norm_date(value: Any) -> str | None:
    text = str(value or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


class GoogleSheetsService:
    def __init__(self, service_account_json: str | None):
        self.service_account_json = service_account_json

    def _client(self):
        if not self.service_account_json:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not configured")
        info = json.loads(self.service_account_json)
        credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
        return gspread.authorize(credentials)

    def read_posts(self, sheet_url: str, target_date: date) -> list[dict[str, str]]:
        sheet = self._client().open_by_url(sheet_url).sheet1
        values = sheet.get_all_values()
        if not values:
            return []
        headers = [_norm_header(v) for v in values[0]]
        aliases = {"date": ["дата", "date"], "time": ["время", "time"], "text": ["ветка", "текст", "post"], "status": ["статус", "status"]}
        indices = {}
        for key, names in aliases.items():
            for name in names:
                if name in headers:
                    indices[key] = headers.index(name); break
        missing = [k for k in ("date", "text", "status") if k not in indices]
        if missing:
            raise RuntimeError("В таблице отсутствуют обязательные колонки: " + ", ".join(missing))
        result = []
        for row in values[1:]:
            def cell(key: str) -> str:
                idx = indices.get(key)
                return row[idx].strip() if idx is not None and idx < len(row) else ""
            if _norm_date(cell("date")) != target_date.isoformat():
                continue
            if cell("status").strip().lower() != "готово":
                continue
            text = cell("text")
            if text:
                result.append({"time": cell("time"), "text": text})
        result.sort(key=lambda x: x.get("time") or "")
        return result
