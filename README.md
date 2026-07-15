# THREADS CLIENT CENTER 2.0

## Railway
1. Mount Volume at `/data`.
2. Set `DB_PATH=/data/bot.db`.
3. Add BOT_TOKEN, ADMIN_ID, WORK_GROUP_ID, GOOGLE_SERVICE_ACCOUNT_JSON, TIMEZONE.
4. Add bot to forum supergroup as admin with topic permissions.
5. Deploy. Migrations run before polling.

## Google Sheets
First sheet headers: `Дата`, `Время`, `Ветка`, `Статус`. Only `Готово` rows for today are sent.

## Local test
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. python tests/smoke_test.py
python -m compileall .
```

## Important
This build includes the agreed architecture and core flows. Telegram forum permissions, real Google credentials, Railway Volume persistence and scheduled delivery must still be verified in the live environment.
