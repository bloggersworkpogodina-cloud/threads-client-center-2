from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


class SheetConfigurationError(RuntimeError):
    pass


class SheetAccessError(RuntimeError):
    pass


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


def _validate_sheet_url(sheet_url: str) -> str:
    url = (sheet_url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or "docs.google.com" not in parsed.netloc:
        raise SheetConfigurationError("Нужна полная ссылка Google Sheets.")
    if "/spreadsheets/" not in parsed.path:
        raise SheetConfigurationError("Ссылка должна вести именно на Google-таблицу.")
    return url


class GoogleSheetsService:
    def __init__(self, service_account_json: str | None):
        self.service_account_json = service_account_json

    def service_account_email(self) -> str | None:
        if not self.service_account_json:
            return None
        try:
            return str(json.loads(self.service_account_json).get("client_email") or "") or None
        except (json.JSONDecodeError, TypeError):
            return None

    def _client(self):
        if not self.service_account_json:
            raise SheetConfigurationError(
                "В Railway не добавлена переменная GOOGLE_SERVICE_ACCOUNT_JSON."
            )
        try:
            info = json.loads(self.service_account_json)
            credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            raise SheetConfigurationError(
                "GOOGLE_SERVICE_ACCOUNT_JSON заполнена неверно."
            ) from exc
        return gspread.authorize(credentials)

    @staticmethod
    def _column_indices(values: list[list[str]]) -> dict[str, int]:
        if not values:
            raise SheetConfigurationError("Таблица пустая.")
        headers = [_norm_header(v) for v in values[0]]
        aliases = {
            "date": ["дата", "date"],
            "time": ["время", "time"],
            "text": ["ветка", "текст", "post"],
            "status": ["статус", "status"],
        }
        indices: dict[str, int] = {}
        for key, names in aliases.items():
            for name in names:
                if name in headers:
                    indices[key] = headers.index(name)
                    break
        missing_names = {
            "date": "Дата",
            "text": "Ветка",
            "status": "Статус",
        }
        missing = [label for key, label in missing_names.items() if key not in indices]
        if missing:
            raise SheetConfigurationError(
                "В первой строке не хватает колонок: " + ", ".join(missing)
            )
        return indices

    def _open_values(self, sheet_url: str) -> list[list[str]]:
        url = _validate_sheet_url(sheet_url)
        try:
            return self._client().open_by_url(url).sheet1.get_all_values()
        except gspread.exceptions.SpreadsheetNotFound as exc:
            email = self.service_account_email()
            hint = f" Откройте доступ для {email}." if email else ""
            raise SheetAccessError("Нет доступа к таблице." + hint) from exc
        except gspread.exceptions.APIError as exc:
            raise SheetAccessError("Google Sheets вернул ошибку доступа. Проверьте ссылку и права.") from exc

    def _read_posts_sync(self, sheet_url: str, target_date: date) -> list[dict[str, str | int]]:
        values = self._open_values(sheet_url)
        indices = self._column_indices(values)
        result: list[dict[str, str | int]] = []
        for source_row, row in enumerate(values[1:], start=2):
            def cell(key: str) -> str:
                idx = indices.get(key)
                return row[idx].strip() if idx is not None and idx < len(row) else ""

            if _norm_date(cell("date")) != target_date.isoformat():
                continue
            if cell("status").strip().lower() != "готово":
                continue
            text = cell("text")
            if text:
                result.append(
                    {
                        "time": cell("time"),
                        "text": text,
                        "source_row": source_row,
                    }
                )
        result.sort(key=lambda item: str(item.get("time") or ""))
        return result

    async def validate(self, sheet_url: str) -> dict[str, str | int | None]:
        values = await asyncio.to_thread(self._open_values, sheet_url)
        self._column_indices(values)
        return {
            "title": "Первый лист",
            "rows": max(len(values) - 1, 0),
            "service_account_email": self.service_account_email(),
        }

    async def read_posts(self, sheet_url: str, target_date: date) -> list[dict[str, str | int]]:
        return await asyncio.to_thread(self._read_posts_sync, sheet_url, target_date)
