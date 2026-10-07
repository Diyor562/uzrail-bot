#!/usr/bin/env python3
"""
UzRailway Afrosiyob Bot v3.1 — свободный выбор + короткие коды.

Примеры:
  /start tn          → Ташкент → Навои
  /start sb          → Самарканд → Бухара
  /start ташкент бухара
  /start both
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
import threading
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE = "https://eticket.railway.uz"
TZ_TASHKENT = timezone(timedelta(hours=5))

# ===== ГОРОДА =====
CITIES = {
    "ташкент": ("Ташкент", "2900000"),
    "таш": ("Ташкент", "2900000"),
    "tashkent": ("Ташкент", "2900000"),
    "toshkent": ("Ташкент", "2900000"),
    "t": ("Ташкент", "2900000"),

    "самарканд": ("Самарканд", "2900700"),
    "сам": ("Самарканд", "2900700"),
    "samarkand": ("Самарканд", "2900700"),
    "samarqand": ("Самарканд", "2900700"),
    "s": ("Самарканд", "2900700"),

    "бухара": ("Бухара", "2900800"),
    "бух": ("Бухара", "2900800"),
    "bukhara": ("Бухара", "2900800"),
    "buxoro": ("Бухара", "2900800"),
    "b": ("Бухара", "2900800"),

    "навои": ("Навои", "2900930"),
    "navoi": ("Навои", "2900930"),
    "navoiy": ("Навои", "2900930"),
    "n": ("Навои", "2900930"),
}

# Короткие коды направлений
SHORT_ROUTES = {
    "ts": [("Ташкент", "Самарканд", "2900000", "2900700")],
    "st": [("Самарканд", "Ташкент", "2900700", "2900000")],
    "tb": [("Ташкент", "Бухара", "2900000", "2900800")],
    "bt": [("Бухара", "Ташкент", "2900800", "2900000")],
    "tn": [("Ташкент", "Навои", "2900000", "2900930")],
    "nt": [("Навои", "Ташкент", "2900930", "2900000")],
    "sb": [("Самарканд", "Бухара", "2900700", "2900800")],
    "bs": [("Бухара", "Самарканд", "2900800", "2900700")],
    "sn": [("Самарканд", "Навои", "2900700", "2900930")],
    "ns": [("Навои", "Самарканд", "2900930", "2900700")],
    "bn": [("Бухара", "Навои", "2900800", "2900930")],
    "nb": [("Навои", "Бухара", "2900930", "2900800")],
    "both": [
        ("Ташкент", "Самарканд", "2900000", "2900700"),
        ("Самарканд", "Ташкент", "2900700", "2900000"),
    ],
}

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
USERS_PATH = SCRIPT_DIR / "users.json"

XSRF = str(uuid.uuid4())
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "device-type": "BROWSER",
    "Accept-Language": "uz",
    "X-XSRF-TOKEN": XSRF,
    "Cookie": f"XSRF-TOKEN={XSRF}",
    "Origin": BASE,
    "Referer": f"{BASE}/uz/home",
    "User-Agent": "Mozilla/5.0 (compatible; UzRailAfrosiyobBot/3.1)",
}

LOCK = threading.Lock()
USERS: Dict[str, Dict[str, Any]] = {}


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


CFG = load_json(CONFIG_PATH, {})


def get_cfg(key: str, env_name: str, default: Optional[str] = None) -> Optional[str]:
    val = CFG.get(key)
    if val is None or str(val).strip() == "":
        val = os.getenv(env_name)
    if val is None or str(val).strip() == "":
        return default
    return str(val).strip()


BOT_TOKEN = get_cfg("telegram_bot_token", "TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("Задайте TELEGRAM_BOT_TOKEN")

POLL_SECONDS = int(get_cfg("poll_seconds", "POLL_SECONDS", "60"))
ALLOWED = CFG.get("allowed_user_ids") or None

USERS = load_json(USERS_PATH, {})


def post_json(path: str, payload: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=HEADERS,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="replace")
        return json.loads(text) if text else {}


def search_trains(date_iso: str, from_code: str, to_code: str) -> List[Dict[str, Any]]:
    payload = {"directions": {"forward": {
        "date": date_iso,
        "depStationCode": from_code,
        "arvStationCode": to_code,
    }}}
    data = post_json("/api/v3/handbook/trains/list", payload)
    return (
        data.get("data", {}).get("directions", {}).get("forward", {}).get("trains", []) or []
    )


def norm_text(*parts: Any) -> str:
    return " ".join(str(p or "") for p in parts).lower()


def is_afrosiyob(train: Dict[str, Any]) -> bool:
    text = norm_text(train.get("brand"), train.get("type"), train.get("number"))
    if "afros" in text or "афрос" in text:
        return True
    return bool(re.search(r"(?<!\d)7[5-8]\d(?!\d)", str(train.get("number") or "")))


def seats_in_car(car: Dict[str, Any]) -> int:
    total = 0
    try:
        total += int(car.get("freeSeats") or 0)
    except Exception:
        pass
    detail = car.get("seatDetail") or {}
    for key in ("undef", "down", "lateralDn"):
        try:
            total += int(detail.get(key) or 0)
        except Exception:
            pass
    for t in car.get("tariffs") or []:
        try:
            total += int(t.get("freeSeats") or 0)
        except Exception:
            pass
    return total


def availability_summary(train: Dict[str, Any]) -> Tuple[int, List[str]]:
    total, lines = 0, []
    for car in train.get("cars") or []:
        n = seats_in_car(car)
        if n <= 0:
            continue
        total += n
        tariffs = []
        for t in car.get("tariffs") or []:
            cls = t.get("classServiceType") or "место"
            try:
                price = int(t.get("tariff") or 0)
            except Exception:
                price = 0
            tariffs.append(f"{cls}: {t.get('freeSeats', '?')} мест, {price:,} сум".replace(",", " "))
        lines.append(f"• {car.get('type') or 'вагон'} — {n} мест. " + "; ".join(tariffs))
    return total, lines


def tg_api(method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = json.dumps(params).encode("utf-8") if params is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def send(chat_id: str, text: str) -> None:
    try:
        tg_api("sendMessage", {
            "chat_id": chat_id, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        })
    except Exception as e:
        print(f"[ERROR] send -> {chat_id}: {e}", file=sys.stderr, flush=True)


def set_bot_commands() -> None:
    commands = [
        {"command": "start", "description": "Запустить (tn, sb, ташкент бухара...)"},
        {"command": "stop", "description": "Остановить"},
        {"command": "status", "description": "Статус"},
        {"command": "route", "description": "Сменить направление"},
        {"command": "check", "description": "Проверить сейчас"},
        {"command": "help", "description": "Справка"},
    ]
    try:
        tg_api("setMyCommands", {"commands": commands})
    except Exception:
        pass


def week_dates() -> List[str]:
    today = datetime.now(TZ_TASHKENT)
    return [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]


def parse_city(text: str) -> Optional[Tuple[str, str]]:
    key = text.strip().lower()
    return CITIES.get(key)


def parse_direction(args: List[str]) -> Optional[List[Tuple[str, str, str, str]]]:
    if not args:
        return SHORT_ROUTES["both"]

    text = " ".join(args).lower().strip()

    # Короткие коды (tn, sb, both и т.д.)
    if text in SHORT_ROUTES:
        return SHORT_ROUTES[text]

    # Попытка разобрать два города
    words = re.findall(r"[а-яёa-z]+", text)
    found = []
    for w in words:
        city = parse_city(w)
        if city and (not found or city[1] != found[-1][1]):
            found.append(city)
        if len(found) == 2:
            break

    if len(found) == 2:
        (n1, c1), (n2, c2) = found
        return [(n1, n2, c1, c2)]

    return None


def allowed(uid: str) -> bool:
    return ALLOWED is None or uid in [str(x) for x in ALLOWED]


def ensure_user(uid: str) -> Dict[str, Any]:
    with LOCK:
        u = USERS.setdefault(uid, {
            "active": False,
            "routes": [],
            "notified": {}
        })
        u.setdefault("routes", [])
        u.setdefault("notified", {})
        u.setdefault("active", False)
        save_json(USERS_PATH, USERS)
        return u


def routes_names(routes: List[Dict]) -> str:
    if not routes:
        return "не заданы"
    return ", ".join(f"{r['from_name']} → {r['to_name']}" for r in routes)


HELP_TEXT = (
    "🚄 <b>Мониторинг Afrosiyob</b>\n\n"
    "<b>Короткие коды:</b>\n"
    "ts / st — Ташкент ↔ Самарканд\n"
    "tb / bt — Ташкент ↔ Бухара\n"
    "tn / nt — Ташкент ↔ Навои\n"
    "sb / bs — Самарканд ↔ Бухара\n"
    "sn / ns — Самарканд ↔ Навои\n"
    "bn / nb — Бухара ↔ Навои\n"
    "both — оба направления Ташкент-Самарканд\n\n"
    "Также можно писать полностью:\n"
    "/start ташкент навои\n"
    "/start самарканд бухара\n\n"
    "/stop — остановить\n"
    "/status — статус\n"
    "/check tn — проверить сейчас\n"
    "/help — справка"
)


def handle_message(uid: str, text: str) -> None:
    if not allowed(uid):
        send(uid, "⛔ Бот доступен только разрешённым пользователям.")
        return

    parts = text.strip().split()
    cmd = parts[0].lower().split("@")[0]
    args = parts[1:]

    if cmd in ("/start", "/run"):
        directions = parse_direction(args)
        if not directions:
            send(uid, "Не понял направление.\n\n" + HELP_TEXT)
            return

        u = ensure_user(uid)
        routes = [{
            "from_name": fn, "to_name": tn,
            "from_code": fc, "to_code": tc
        } for fn, tn, fc, tc in directions]

        with LOCK:
            u["active"] = True
            u["routes"] = routes
            u["notified"] = {}
            save_json(USERS_PATH, USERS)

        send(uid,
             "✅ <b>Мониторинг запущен.</b>\n"
             f"Маршруты: {routes_names(routes)}\n"
             f"Проверка каждые {POLL_SECONDS} сек.\n"
             "Остановить: /stop\n\n🔍 Сейчас проверю...")
        try:
            result = check_now(routes)
            send(uid, result)
            monitor_cycle()
        except Exception as e:
            send(uid, f"Ошибка: {e}")

    elif cmd in ("/stop", "/off"):
        u = ensure_user(uid)
        with LOCK:
            u["active"] = False
            u["notified"] = {}
            save_json(USERS_PATH, USERS)
        send(uid, "⏹ Мониторинг остановлен.\nЗапустить: /start")

    elif cmd in ("/status", "/stat"):
        u = ensure_user(uid)
        state = "🟢 работает" if u.get("active") else "🔴 остановлен"
        send(uid,
             f"Статус: {state}\n"
             f"Маршруты: {routes_names(u.get('routes', []))}\n"
             f"Проверка каждые {POLL_SECONDS} сек")

    elif cmd == "/route":
        directions = parse_direction(args)
        if not directions:
            send(uid, "Пример: /route tn   или   /route самарканд бухара")
            return
        u = ensure_user(uid)
        routes = [{
            "from_name": fn, "to_name": tn,
            "from_code": fc, "to_code": tc
        } for fn, tn, fc, tc in directions]
        with LOCK:
            u["routes"] = routes
            u["notified"] = {}
            save_json(USERS_PATH, USERS)
        send(uid, f"🧭 Маршрут: {routes_names(routes)}")

    elif cmd == "/check":
        directions = parse_direction(args)
        if directions:
            routes = [{
                "from_name": fn, "to_name": tn,
                "from_code": fc, "to_code": tc
            } for fn, tn, fc, tc in directions]
        else:
            u = ensure_user(uid)
            routes = u.get("routes") or []
            if not routes:
                send(uid, "Сначала сделай /start или укажи направление")
                return
        send(uid, "🔍 Проверяю...\n" + check_now(routes))

    elif cmd == "/help":
        send(uid, HELP_TEXT)

    else:
        send(uid, "Не понял.\n\n" + HELP_TEXT)


def check_now(routes: List[Dict]) -> str:
    dates = week_dates()
    out = []
    for r in routes:
        out.append(f"<b>{r['from_name']} → {r['to_name']}</b>")
        for d in dates:
            try:
                trains = search_trains(d, r["from_code"], r["to_code"])
            except Exception as e:
                out.append(f"  {d}: ошибка")
                continue
            afr = [t for t in trains if is_afrosiyob(t)]
            if not afr:
                out.append(f"  {d}: нет Afrosiyob")
                continue
            parts = [f"{t.get('number')} — {availability_summary(t)[0]} мест" for t in afr]
            out.append(f"  {d}: " + "; ".join(parts))
    return "\n".join(out)


def monitor_cycle() -> None:
    with LOCK:
        snapshot = {
            uid: list(u.get("routes", []))
            for uid, u in USERS.items() if u.get("active") and u.get("routes")
        }
    if not snapshot:
        return

    dates = week_dates()
    dir_users: Dict[str, List[str]] = {}
    for uid, routes in snapshot.items():
        for r in routes:
            key = f"{r['from_code']}->{r['to_code']}"
            dir_users.setdefault(key, []).append(uid)

    for dir_key, uids in dir_users.items():
        fc, tc = dir_key.split("->")
        sample = next((r for routes in snapshot.values() for r in routes
                       if r["from_code"] == fc and r["to_code"] == tc), None)
        from_name = sample["from_name"] if sample else fc
        to_name = sample["to_name"] if sample else tc

        for date_iso in dates:
            try:
                trains = search_trains(date_iso, fc, tc)
            except Exception as e:
                print(f"[ERROR] {dir_key} {date_iso}: {e}", flush=True)
                continue

            found = []
            for train in trains:
                if is_afrosiyob(train):
                    total, lines = availability_summary(train)
                    if total > 0:
                        found.append((train, total, lines))

            for uid in uids:
                if not found:
                    prefix = f"{date_iso}|{dir_key}|"
                    with LOCK:
                        u = USERS.get(uid)
                        if u:
                            for k in list(u.get("notified", {})):
                                if k.startswith(prefix):
                                    u["notified"][k] = False
                            save_json(USERS_PATH, USERS)
                    continue

                for train, total, lines in found:
                    key = f"{date_iso}|{dir_key}|{train.get('number')}"
                    with LOCK:
                        u = USERS.get(uid)
                        if not u or u.get("notified", {}).get(key):
                            continue
                        u["notified"][key] = True
                        save_json(USERS_PATH, USERS)
                    send(uid,
                         "🚄 <b>Найдены билеты Afrosiyob!</b>\n"
                         f"Маршрут: {from_name} → {to_name}\n"
                         f"Дата: {date_iso}\n"
                         f"Поезд: {train.get('number')} {train.get('brand') or ''}\n"
                         f"Отправление: {train.get('departureDate', '?')}\n"
                         f"Прибытие: {train.get('arrivalDate', '?')}\n"
                         f"Свободно мест: {total}\n" + "\n".join(lines[:8]))


def monitor_loop() -> None:
    while True:
        try:
            monitor_cycle()
        except Exception as e:
            print(f"[ERROR] monitor: {e}", flush=True)
        time.sleep(POLL_SECONDS)


def run_health_server() -> None:
    port = int(os.environ.get("PORT", "8080"))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *args): pass
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


def telegram_loop() -> None:
    try:
        tg_api("deleteWebhook")
    except Exception:
        pass
    offset = 0
    while True:
        try:
            res = tg_api("getUpdates", {"offset": offset, "timeout": 30})
            if not res.get("ok"):
                time.sleep(5)
                continue
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                text = msg.get("text")
                uid = str(msg.get("from", {}).get("id") or "")
                if text and uid:
                    handle_message(uid, text)
        except Exception as e:
            print(f"[ERROR] telegram: {e}", flush=True)
            time.sleep(5)


def main() -> None:
    set_bot_commands()
    threading.Thread(target=run_health_server, daemon=True).start()
    threading.Thread(target=monitor_loop, daemon=True).start()
    print(f"[INFO] Бот v3.1 запущен. Пользователей: {len(USERS)}", flush=True)
    telegram_loop()


if __name__ == "__main__":
    main()
