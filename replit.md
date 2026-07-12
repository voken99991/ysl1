# YSL Whole Page Editor

A Flask-based visual editor for the YSL (Youth Soccer League) homepage. Every
text block, button, card, image, and theme color on the page can be edited
in-place after logging in as admin.

## Stack

- Python 3.11, Flask, gunicorn (production), python-dotenv
- Content persists to `data/site.json`; uploaded media goes to `static/uploads/`
- No database — file-based storage only

## Running on Replit

- Dev: the `Start application` workflow runs `python app.py`, reading `PORT`
  from `.env` (set to `5000` for the Replit webview).
- Production: `gunicorn -b 0.0.0.0:$PORT app:app`
- Config comes from `.env` (copied from `.env.example`): `ADMIN_PASSWORD`,
  `YSL_SECRET_KEY`, `PORT`, `COOKIE_SECURE`, `FLASK_DEBUG`.
- Admin login: click **Admin** (bottom-right corner) and use `ADMIN_PASSWORD`
  (defaults to `adminson`, kept as imported — not yet changed to a secure
  value).

## User preferences

- Imported as a zip; user asked to get it running as-is on Replit without
  changing existing structure, stack, or default credentials.

## Known follow-ups

- Default `ADMIN_PASSWORD` (`adminson`) and placeholder `YSL_SECRET_KEY` are
  still in use — should be replaced with strong secrets before making the
  site public, per the project's own README.
