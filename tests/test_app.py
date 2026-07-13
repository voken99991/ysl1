import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app


def test_home_contains_editor_assets():
    client = app.app.test_client()
    response = client.get('/')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert '/static/css/site.css?v=' in html
    assert '/static/js/site.js?v=' in html
    assert '/static/js/editor.js?v=' in html
