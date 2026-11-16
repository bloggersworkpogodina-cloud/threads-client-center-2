from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

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


def _clean_cell(value: Any) -> str:
    # Copy/paste from chat and documents can leave invisible format characters.
    text = unicodedata.normalize("NFKC", str(value if value is not None else ""))
    return "".join(c for c in text if unicodedata.category(c) != "Cf").strip()


def _norm_header(value: Any) -> str:
    return " ".join(_clean_cell(value).lower().replace("ё", "е").split())


def _is_ready(value: Any) -> bool:
    # Exact status only: never accept «не готово» or «почти готово».
    return _norm_header(value).strip(" ✅☑✔\ufe0f") == "готово"


@dataclass
class SheetReadResult:
    posts: list[dict[str, str | int]]
    diagnostics: dict

    def empty_message(self) -> str:
        d = self.diagnostics
        message = (
            f"Ветки для отправки не найдены на {d['target_date']}.\n"
            f"Таблица: {d['spreadsheet_title']} · лист: {d['worksheet_title']}\n"
            f"Колонки: дата — {d['columns']['date']}, текст — {d['columns']['text']}, "
            f"статус — {d['columns']['status']}.\n"
            f"Строк на эту дату: {d['matching_date']}; "
            f"со статусом «Готово»: {d['ready_today']}; "
            f"из них без текста: {d['empty_text_today']}."
        )
        if d['statuses_today']:
            statuses = ", ".join(f"«{key}»: {count}" for key, count in d['statuses_today'].items())
            message += f"\nСтатусы на эту дату: {statuses}."
        if d['date_range']:
            message += f"\nРаспознанные даты в таблице: {d['date_range']}."
        if d['invalid_date_rows']:
            message += "\nНе удалось прочитать дату в строках: " + ", ".join(map(str, d['invalid_date_rows'])) + "."
        return message


def _column_letter(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _norm_date(value: Any) -> str | None:
    text = re.sub(r"\s*([./-])\s*", r"\1", _clean_cell(value))
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _validate_sheet_url(sheet_url: str) -> str:
    url = (sheet_url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "docs.google.com":
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
            "image": ["изображение", "картинка", "фото", "image", "photo"],
            "status": ["статус", "status"],
        }
        indices: dict[str, int] = {}
        for key, names in aliases.items():
            matches = [i for i, header in enumerate(headers) if header in names]
            if len(matches) > 1:
                raise SheetConfigurationError(
                    "Несколько колонок одного типа: " + ", ".join(_column_letter(i) for i in matches)
                    + f" ({names[0]}). Нельзя однозначно выбрать колонку."
                )
            if matches:
                indices[key] = matches[0]
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

    def _open_sheet(self, sheet_url: str):
        url = _validate_sheet_url(sheet_url)
        parsed = urlparse(url)
        gid = parse_qs(parsed.fragment).get("gid", parse_qs(parsed.query).get("gid", [None]))[0]
        if gid is not None and not gid.isdigit():
            raise SheetConfigurationError("В ссылке некорректный идентификатор листа (gid).")
        try:
            spreadsheet = self._client().open_by_url(url)
            worksheet = spreadsheet.sheet1 if gid is None else spreadsheet.get_worksheet_by_id(int(gid))
            return spreadsheet, worksheet
        except gspread.exceptions.WorksheetNotFound as exc:
            raise SheetConfigurationError("Лист из ссылки не найден. Проверьте ссылку на контент-план.") from exc
        except (gspread.exceptions.SpreadsheetNotFound, PermissionError) as exc:
            email = self.service_account_email()
            hint = f" Откройте доступ для {email}." if email else ""
            raise SheetAccessError("Нет доступа к таблице." + hint) from exc
        except gspread.exceptions.APIError as exc:
            raise SheetAccessError("Google Sheets вернул ошибку доступа. Проверьте ссылку и права.") from exc

    def _open_values(self, sheet_url: str) -> list[list[str]]:
        _, worksheet = self._open_sheet(sheet_url)
        return worksheet.get_all_values()

    def _read_posts_with_diagnostics_sync(self, sheet_url: str, target_date: date) -> SheetReadResult:
        spreadsheet, worksheet = self._open_sheet(sheet_url)
        values = worksheet.get_all_values()
        indices = self._column_indices(values)
        result: list[dict[str, str | int]] = []
        diagnostics = {
            "spreadsheet_title": str(spreadsheet.title)[:160],
            "worksheet_title": str(worksheet.title)[:100],
            "worksheet_id": worksheet.id,
            "target_date": target_date.strftime("%d.%m.%Y"),
            "columns": {key: _column_letter(index) for key, index in indices.items()},
            "matching_date": 0, "ready_today": 0, "empty_text_today": 0,
            "invalid_date_rows": [], "statuses_today": {}, "date_range": "",
        }
        dates = []
        statuses = Counter()
        for source_row, row in enumerate(values[1:], start=2):
            def cell(key: str) -> str:
                idx = indices.get(key)
                return str(row[idx]).strip() if idx is not None and idx < len(row) else ""

            raw_date = cell("date")
            row_date = _norm_date(raw_date)
            if row_date:
                dates.append(row_date)
            elif raw_date or cell("text"):
                if len(diagnostics["invalid_date_rows"]) < 10:
                    diagnostics["invalid_date_rows"].append(source_row)
            if row_date != target_date.isoformat():
                continue
            diagnostics["matching_date"] += 1
            statuses[_norm_header(cell("status"))[:60] or "пусто"] += 1
            if not _is_ready(cell("status")):
                continue
            diagnostics["ready_today"] += 1
            text = cell("text")
            if not _clean_cell(text):
                diagnostics["empty_text_today"] += 1
                continue
            result.append({
                "time": cell("time"), "text": text,
                "image_url": cell("image"), "source_row": source_row,
            })
        if dates:
            diagnostics["date_range"] = " — ".join(
                date.fromisoformat(day).strftime("%d.%m.%Y") for day in (min(dates), max(dates))
            )
        diagnostics["statuses_today"] = dict(statuses.most_common(10))
        result.sort(key=lambda item: str(item.get("time") or "").zfill(5))
        return SheetReadResult(result, diagnostics)

    def _read_posts_sync(self, sheet_url: str, target_date: date) -> list[dict[str, str | int]]:
        return self._read_posts_with_diagnostics_sync(sheet_url, target_date).posts

    async def read_posts_with_diagnostics(self, sheet_url: str, target_date: date) -> SheetReadResult:
        return await asyncio.to_thread(self._read_posts_with_diagnostics_sync, sheet_url, target_date)

    async def validate(self, sheet_url: str) -> dict[str, str | int | None]:
        def read():
            _, worksheet = self._open_sheet(sheet_url)
            return worksheet.title, worksheet.get_all_values()
        title, values = await asyncio.to_thread(read)
        self._column_indices(values)
        return {
            "title": title,
            "rows": max(len(values) - 1, 0),
            "service_account_email": self.service_account_email(),
        }

    async def read_posts(self, sheet_url: str, target_date: date) -> list[dict[str, str | int]]:
        return await asyncio.to_thread(self._read_posts_sync, sheet_url, target_date)
