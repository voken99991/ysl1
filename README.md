# YSL Whole Page Editor

A complete visual editor for the YSL homepage.

## What can be edited

- Every text element and HTML block
- Buttons and links
- Cards, squares, decorative objects and sections
- Text colour, background colour and border colour
- Font size, weight, alignment, spacing and line height
- Padding, margin, width, height, radius and opacity
- Shadows and animations
- Images and background images
- Section order
- Global theme colours and fonts
- New text, buttons, cards, images and sections

Changes are saved on the server in `data/site.json`, so they are visible to every visitor.

## Login

The default editor password is:

```text
adminson
```

It is read from `ADMIN_PASSWORD` in the environment, not hardcoded in the page JavaScript.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Open:

```text
http://localhost:8000
```

Click **Admin** in the bottom-right corner.

## Production start command

```bash
gunicorn -b 0.0.0.0:$PORT app:app
```

## Important security note

Change both `ADMIN_PASSWORD` and `YSL_SECRET_KEY` before making the site public. Use HTTPS and set:

```env
COOKIE_SECURE=true
```

## Persistence

The editor stores content in `data/site.json` and uploaded media in `static/uploads/`. Your host must provide persistent storage for those folders.
