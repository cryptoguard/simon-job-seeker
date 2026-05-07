"""Sacrée Soirée contract availability monitor.

Logs in, checks the two listing pages via the JSON API, diffs against
seen.json, and pushes a Telegram alert for every new contract ID.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

PORTAL = "https://portail.sacreesoiree.com"
LOGIN_URL = f"{PORTAL}/login.php"
SASOKEY_PAGE = f"{PORTAL}/placement/ponctuels.php"
API_URL = f"{PORTAL}/pigiste/api/api.php"

PAGES = {
    "placement": {
        "request": "get/contrats/placement/excludebooke",
        "label": "Ponctuels",
        "url": f"{PORTAL}/placement/ponctuels.php",
    },
    "eventss": {
        "request": "get/contrats/eventss/excludebooke",
        "label": "Événements",
        "url": f"{PORTAL}/eventss/contrats.php",
    },
}

TZ = ZoneInfo("America/Toronto")
WINDOW_START_HOUR = 8
WINDOW_END_HOUR = 18  # exclusive — last allowed minute is 17:59
SEEN_PATH = Path(__file__).parent / "seen.json"
SEEN_CAP = 1000

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0"


def in_window(now: datetime) -> bool:
    return WINDOW_START_HOUR <= now.hour < WINDOW_END_HOUR


def login(session: requests.Session, email: str, password: str) -> None:
    session.headers["User-Agent"] = UA
    # Prime PHPSESSID
    session.get(LOGIN_URL, timeout=20)
    r = session.post(
        LOGIN_URL,
        data={"email": email, "password": password, "erlang": ""},
        timeout=20,
        allow_redirects=True,
    )
    r.raise_for_status()
    if 'name="password"' in r.text:
        raise RuntimeError("Login failed — login form still present in response")


def get_sasokey(session: requests.Session) -> str:
    r = session.get(SASOKEY_PAGE, timeout=20)
    r.raise_for_status()
    m = re.search(r'var\s+sasokey\s*=\s*"([a-f0-9]+)"', r.text)
    if not m:
        raise RuntimeError("Could not extract sasokey from page HTML")
    return m.group(1)


def fetch_contracts(session: requests.Session, key: str, request_value: str) -> list[dict]:
    # Use multipart so it matches the captured cURL exactly.
    files = {
        "key": (None, key),
        "request": (None, request_value),
    }
    r = session.post(API_URL, files=files, timeout=20)
    r.raise_for_status()
    # The API returns JSON prefixed with a UTF-8 BOM, which trips r.json().
    text = r.content.decode("utf-8-sig")
    try:
        data = json.loads(text)
    except ValueError as e:
        raise RuntimeError(f"API did not return JSON: {text[:300]}") from e

    # Response shape unknown ahead of first run. Accept either a top-level
    # list, or a dict with a list under a common key.
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for k in ("contrats", "data", "results", "items"):
            v = data.get(k)
            if isinstance(v, list):
                items = v
                break
        else:
            raise RuntimeError(f"Unexpected JSON shape: keys={list(data.keys())}")
    else:
        raise RuntimeError(f"Unexpected JSON type: {type(data).__name__}")

    return items


def contract_id(item: dict) -> str:
    for field in ("id", "id_contrat", "idcontrat", "contrat_id"):
        if field in item and item[field] is not None:
            return str(item[field])
    raise RuntimeError(f"No id field in contract entry; keys={list(item.keys())}")


def format_alert(label: str, page_url: str, item: dict) -> str:
    poste = item.get("poste") or item.get("titre") or item.get("nom") or "?"
    adresse = item.get("adresse") or item.get("lieu") or ""
    date_parts = [
        str(item.get(k, "")).strip()
        for k in ("jour", "date", "heure")
        if item.get(k)
    ]
    when = " ".join(p for p in date_parts if p)

    lines = [f"🆕 Nouveau contrat — {label}", f"{poste}"]
    if when:
        lines.append(when)
    if adresse:
        lines.append(adresse)
    lines.append(page_url)
    return "\n".join(lines)


def telegram_send(token: str, chat_id: str, text: str) -> None:
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"},
        timeout=20,
    )
    r.raise_for_status()


def load_seen() -> dict[str, list[str]]:
    if not SEEN_PATH.exists():
        return {key: [] for key in PAGES}
    state = json.loads(SEEN_PATH.read_text())
    for key in PAGES:
        state.setdefault(key, [])
    return state


def save_seen(state: dict[str, list[str]]) -> None:
    SEEN_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def main() -> int:
    now = datetime.now(TZ)
    force = os.environ.get("FORCE_RUN") == "1"
    if not in_window(now) and not force:
        print(f"Outside 8-18 ET window (now={now.isoformat()}), skipping.")
        return 0
    if force:
        print(f"FORCE_RUN=1: bypassing time-window check (now={now.isoformat()}).")

    email = env("PORTAL_EMAIL")
    password = env("PORTAL_PASSWORD")
    tg_token = env("TELEGRAM_BOT_TOKEN")
    tg_chat = env("TELEGRAM_CHAT_ID")

    seen = load_seen()
    session = requests.Session()
    login(session, email, password)
    key = get_sasokey(session)

    total_new = 0
    for page_key, cfg in PAGES.items():
        items = fetch_contracts(session, key, cfg["request"])
        current_ids = [contract_id(it) for it in items]
        seen_set = set(seen[page_key])
        new_items = [it for it, cid in zip(items, current_ids) if cid not in seen_set]
        print(f"[{page_key}] {len(items)} listed, {len(new_items)} new")

        for it in new_items:
            cid = contract_id(it)
            text = format_alert(cfg["label"], cfg["url"], it)
            try:
                telegram_send(tg_token, tg_chat, text)
            except Exception as e:
                print(f"  ! Telegram send failed for {cid}: {e}", file=sys.stderr)
                # Don't mark as seen so we retry next run.
                continue
            seen[page_key].append(cid)
            total_new += 1

        # Cap list size, keeping most recent.
        if len(seen[page_key]) > SEEN_CAP:
            seen[page_key] = seen[page_key][-SEEN_CAP:]

    save_seen(seen)
    print(f"Done. {total_new} new contract(s) alerted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
