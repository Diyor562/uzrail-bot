#!/usr/bin/env python3
"""
UzRailway Afrosiyob Bot v2 — мультипользовательский мониторинг через Telegram.

Команды в Telegram:
  /start [ts|st|both]   — запустить мониторинг (по умолчанию оба направления)
  /stop                 — остановить
  /status               — статус и текущие настройки
  /route ts|st|both     — сменить маршрут без остановки
  /check                — проверить наличие прямо сейчас
  /help                 — справка

Маршруты:
  ts = Ташкент → Самарканд
  st = Самарканд → Ташкент

Даты: всегда сегодня + 6 дней вперёд, список обновляется каждый день сам.

Токен бота: переменная окружения TELEGRAM_BOT_TOKEN или config.json -> telegram_bot_token.
Опционально в config.json: {"poll_seconds": 60, "allowed_user_ids": ["123"]}
allowed_user_ids — если задан, бот отвечает только этим пользователям.
По умолчанию бот открыт для всех.
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
TASHKENT = "2900000"
SAMARKAND = "2900700"
TZ_TASHKENT = timezone(timedelta(hours=5))  # время Узбекистана

ROUTES = {
    "ts": ("Ташкент", "Самарканд", TASHKENT, SAMARKAND),
    "st": ("Самарканд", "Ташкент", SAMARKAND, TASHKENT),
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
    "User-Agent": "Mozilla/5.0 (compatible; UzRailAfrosiyobBot/2.0)",
}

LOCK = threading.Lock()
USERS: Dict[str, Dict[str, Any]] = {}


# ---------- config ----------

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
    raise SystemExit("Задайте TELEGRAM_BOT_TOKEN (переменная окружения) или telegram_bot_token в config.json")

POLL_SECONDS = int(get_cfg("poll_seconds", "POLL_SECONDS", "60"))
ALLOWED = CFG.get("allowed_user_ids") or None  # None = открыт для всех

USERS = load_json(USERS_PATH, {})


# ---------- uzrailway api ----------

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


# ---------- telegram ----------

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


def week_dates() -> List[str]:
    today = datetime.now(TZ_TASHKENT)
    return [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]


# ---------- users ----------

def allowed(uid: str) -> bool:
    return ALLOWED is None or uid in [str(x) for x in ALLOWED]


def ensure_user(uid: str) -> Dict[str, Any]:
    with LOCK:
        u = USERS.setdefault(uid, {"active": False, "routes": ["ts", "st"], "notified": {}})
        u.setdefault("routes", ["ts", "st"])
        u.setdefault("notified", {})
        u.setdefault("active", False)
        save_json(USERS_PATH, USERS)
        return u


def routes_names(routes: List[str]) -> str:
    return ", ".join(f"{ROUTES[r][0]} → {ROUTES[r][1]}" for r in routes if r in ROUTES)


HELP_TEXT = (
    "🚄 <b>Мониторинг билетов Afrosiyob</b>\n\n"
    "/start — запустить мониторинг (можно сразу с маршрутом: /start ts)\n"
    "/stop — остановить\n"
    "/status — текущий статус\n"
    "/route ts|st|both — сменить маршрут\n"
    "/check — проверить наличие прямо сейчас\n"
    "/help — эта справка\n\n"
    "Маршруты:\n"
    "• ts — Ташкент → Самарканд\n"
    "• st — Самарканд → Ташкент\n"
    "• both — оба направления\n\n"
    "Проверяются даты: сегодня + 6 дней. Уведомление придёт, когда появятся места."
)


def handle_message(uid: str, text: str) -> None:
    if not allowed(uid):
        send(uid, "⛔ Бот доступен только разрешённым пользователям.")
        return
    parts = text.strip().split()
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1].lower() if len(parts) > 1 else ""

    if cmd in ("/start", "/run"):
        u = ensure_user(uid)
        if arg in ("ts", "st"):
            u["routes"] = [arg]
        elif arg in ("both", "оба", "все"):
            u["routes"] = ["ts", "st"]
        with LOCK:
            u["active"] = True
            u["notified"] = {}  # сбрасываем, чтобы можно было снова получить уведомления
            save_json(USERS_PATH, USERS)
        send(uid,
             "✅ <b>Мониторинг запущен.</b>\n"
             f"Маршруты: {routes_names(u['routes'])}\n"
             f"Проверка каждые {POLL_SECONDS} сек.\n"
             "Остановить: /stop\n\n"
             "🔍 Сейчас проверю наличие мест...")
        # Сразу проверяем и отправляем результаты
        try:
            result = check_now(u["routes"])
            send(uid, result)
            # Запускаем один цикл монитора, чтобы отправить красивые уведомления и поставить notified
            monitor_cycle()
        except Exception as e:
            send(uid, f"Ошибка при проверке: {e}")

    elif cmd in ("/stop", "/off"):
        u = ensure_user(uid)
        with LOCK:
            u["active"] = False
            u["notified"] = {}
            save_json(USERS_PATH, USERS)
        send(uid, "⏹ <b>Мониторинг остановлен.</b>\nЗапустить снова: /start")

    elif cmd in ("/status", "/stat"):
        u = ensure_user(uid)
        state = "🟢 работает" if u.get("active") else "🔴 остановлен"
        send(uid,
             f"Статус: {state}\n"
             f"Маршруты: {routes_names(u.get('routes', [])) or 'не заданы'}\n"
             f"Проверка каждые {POLL_SECONDS} сек\n"
             f"Даты: сегодня + 6 дней")

    elif cmd == "/route":
        u = ensure_user(uid)
        if arg in ("ts", "st"):
            u["routes"] = [arg]
        elif arg in ("both", "оба", "все"):
            u["routes"] = ["ts", "st"]
        else:
            send(uid, "Укажите маршрут: /route ts, /route st или /route both")
            return
        with LOCK:
            u["notified"] = {}
            save_json(USERS_PATH, USERS)
        send(uid, f"🧭 Маршрут изменён: {routes_names(u['routes'])}")

    elif cmd == "/check":
        routes = [arg] if arg in ("ts", "st") else ["ts", "st"]
        send(uid, "🔍 Проверяю прямо сейчас...\n" + check_now(routes))

    elif cmd == "/help":
        send(uid, HELP_TEXT)

    else:
        send(uid, "Не понял команду. Список команд: /help")


def check_now(routes: List[str]) -> str:
    """Ручная проверка: что сайт реально отдаёт на неделю вперёд."""
    dates = week_dates()
    out_lines: List[str] = []
    for r in routes:
        if r not in ROUTES:
            continue
        from_name, to_name, fc, tc = ROUTES[r]
        out_lines.append(f"<b>{from_name} → {to_name}</b>")
        for d in dates:
            try:
                trains = search_trains(d, fc, tc)
            except Exception as e:
                out_lines.append(f"  {d}: ошибка запроса: {e}")
                continue
            if not trains:
                out_lines.append(f"  {d}: сайт вернул 0 поездов")
                continue
            afr = [t for t in trains if is_afrosiyob(t)]
            if not afr:
                nums = ", ".join(str(t.get("number")) for t in trains[:8])
                out_lines.append(f"  {d}: {len(trains)} поездов, Afrosiyob НЕ найден: {nums}")
                continue
            parts = []
            for t in afr:
                total, _ = availability_summary(t)
                parts.append(f"{t.get('number')} — {total} мест")
            out_lines.append(f"  {d}: " + "; ".join(parts))
    return "\n".join(out_lines)


# ---------- monitor ----------

def monitor_cycle() -> None:
    with LOCK:
        snapshot = {uid: {"routes": list(u.get("routes", ["ts", "st"]))}
                    for uid, u in USERS.items() if u.get("active")}
    if not snapshot:
        return

    dates = week_dates()
    route_users: Dict[str, List[str]] = {}
    for uid, data in snapshot.items():
        for r in data["routes"]:
            if r in ROUTES:
                route_users.setdefault(r, []).append(uid)

    for route, uids in route_users.items():
        from_name, to_name, from_code, to_code = ROUTES[route]
        for date_iso in dates:
            try:
                trains = search_trains(date_iso, from_code, to_code)
                afr_n = sum(1 for t in trains if is_afrosiyob(t))
                print(f"[CHECK] {route} {date_iso}: поездов={len(trains)} afrosiyob={afr_n}", flush=True)
            except Exception as e:
                print(f"[ERROR] search {route} {date_iso}: {e}", file=sys.stderr, flush=True)
                continue

            found = []
            for train in trains:
                if not is_afrosiyob(train):
                    continue
                total, lines = availability_summary(train)
                if total > 0:
                    found.append((train, total, lines))

            for uid in uids:
                if not found:
                    prefix = f"{date_iso}|{route}|"
                    with LOCK:
                        u = USERS.get(uid)
                        if u:
                            for k in list(u.get("notified", {})):
                                if k.startswith(prefix):
                                    u["notified"][k] = False
                            save_json(USERS_PATH, USERS)
                    continue
                for train, total, lines in found:
                    key = f"{date_iso}|{route}|{train.get('number')}"
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
                         f"Поезд: {train.get('number', '?')} {train.get('brand') or train.get('type') or ''}\n"
                         f"Отправление: {train.get('departureDate', '?')}\n"
                         f"Прибытие: {train.get('arrivalDate', '?')}\n"
                         f"Свободно мест: {total}\n" + "\n".join(lines[:8]))


def monitor_loop() -> None:
    while True:
        try:
            monitor_cycle()
        except Exception as e:
            print(f"[ERROR] monitor: {e}", file=sys.stderr, flush=True)
        time.sleep(POLL_SECONDS)


# ---------- health endpoint (для keep-alive на Render) ----------

def run_health_server() -> None:
    port = int(os.environ.get("PORT", "8080"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *args):
            pass

    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


# ---------- main ----------

def telegram_loop() -> None:
    try:
        tg_api("deleteWebhook")  # на случай, если вебхук был настроен ранее
    except Exception:
        pass
    offset = 0
    while True:
        try:
            res = tg_api("getUpdates", {"offset": offset, "timeout": 30})
            if not res.get("ok"):
                print(f"[ERROR] getUpdates: {res}", file=sys.stderr, flush=True)
                time.sleep(5)
                continue
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                text = msg.get("text")
                uid = str(msg.get("from", {}).get("id") or msg.get("chat", {}).get("id") or "")
                if text and uid:
                    handle_message(uid, text)
        except Exception as e:
            print(f"[ERROR] telegram loop: {e}", file=sys.stderr, flush=True)
            time.sleep(5)


def main() -> None:
    threading.Thread(target=run_health_server, daemon=True).start()
    threading.Thread(target=monitor_loop, daemon=True).start()
    print(f"[INFO] Бот запущен. Пользователей в базе: {len(USERS)}. POLL_SECONDS={POLL_SECONDS}", flush=True)
    telegram_loop()


if __name__ == "__main__":
    main()