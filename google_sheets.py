from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import gspread


class GoogleSheetsError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ThreadPost:
    time: str
    text: str


def _normalize_header(value: str) -> str:
    return " ".join(value.strip().lower().replace("ё", "е").split())


def _parse_date(value: str) -> date | None:
    raw = value.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _client() -> gspread.Client:
    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw_json:
        raise GoogleSheetsError("Не задан GOOGLE_SERVICE_ACCOUNT_JSON")
    try:
        credentials = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise GoogleSheetsError("GOOGLE_SERVICE_ACCOUNT_JSON содержит неверный JSON") from exc
    try:
        return gspread.service_account_from_dict(credentials)
    except Exception as exc:
        raise GoogleSheetsError("Не удалось авторизоваться в Google Sheets") from exc


def read_posts_for_date(
    sheet_url: str,
    target_date: date,
) -> list[ThreadPost]:
    try:
        worksheet = _client().open_by_url(sheet_url).get_worksheet(0)
        if worksheet is None:
            raise GoogleSheetsError("В таблице нет первого листа")
        values = worksheet.get_all_values()
    except GoogleSheetsError:
        raise
    except Exception as exc:
        raise GoogleSheetsError(
            "Не удалось открыть таблицу. Проверьте ссылку и доступ сервисного аккаунта."
        ) from exc

    if not values:
        return []

    headers = [_normalize_header(cell) for cell in values[0]]
    aliases = {
        "date": {"дата", "date"},
        "time": {"время", "time"},
        "text": {"ветка", "текст", "пост", "thread"},
        "status": {"статус", "status"},
    }
    indexes: dict[str, int] = {}
    for key, names in aliases.items():
        for idx, header in enumerate(headers):
            if header in names:
                indexes[key] = idx
                break
    missing = [key for key in ("date", "text", "status") if key not in indexes]
    if missing:
        raise GoogleSheetsError(
            "В первой строке таблицы нужны колонки: Дата, Время, Ветка, Статус."
        )

    def cell(row: list[str], key: str) -> str:
        idx = indexes.get(key)
        return row[idx].strip() if idx is not None and idx < len(row) else ""

    posts: list[ThreadPost] = []
    for row in values[1:]:
        if _parse_date(cell(row, "date")) != target_date:
            continue
        if cell(row, "status").strip().lower() != "готово":
            continue
        text = cell(row, "text")
        if text:
            posts.append(ThreadPost(time=cell(row, "time"), text=text))
    posts.sort(key=lambda item: item.time)
    return posts


def read_today_posts(sheet_url: str, timezone_name: str | None = None) -> list[ThreadPost]:
    tz = ZoneInfo(timezone_name or os.getenv("TIMEZONE", "Europe/Moscow"))
    return read_posts_for_date(sheet_url, datetime.now(tz).date())
