#!/usr/bin/env sh
# Container entrypoint: bring the schema up to date, make sure the clinic's
# doctors exist, then serve. Migrations run here (not in a separate pre-deploy
# step) so the same image works on any host, including Render's free tier.
set -eu

alembic upgrade head
python -m app.seed
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"
