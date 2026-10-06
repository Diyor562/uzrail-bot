#!/usr/bin/env python3
"""Быстрая диагностика: что eticket.railway.uz реально отдаёт на неделю вперёд.
Запуск: python debug_search.py
"""
import json, re, uuid, urllib.request
from datetime import datetime, timedelta

BASE = "https://eticket.railway.uz"
XSRF = str(uuid.uuid4())
HEADERS = {
    "Content-Type": "application/json", "Accept": "application/json",
    "device-type": "BROWSER", "Accept-Language": "uz",
    "X-XSRF-TOKEN": XSRF, "Cookie": f"XSRF-TOKEN={XSRF}",
    "Origin": BASE, "Referer": f"{BASE}/uz/home",
    "User-Agent": "Mozilla/5.0 (compatible; debug)",
}
ROUTES = [("Ташкент→Самарканд", "2900000", "2900700"),
          ("Самарканд→Ташкент", "2900700", "2900000")]

def search(date, fc, tc):
    payload = {"directions": {"forward": {"date": date, "depStationCode": fc, "arvStationCode": tc}}}
    req = urllib.request.Request(BASE + "/api/v3/handbook/trains/list",
                                 data=json.dumps(payload).encode(), headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def free_seats(car):
    n = int(car.get("freeSeats") or 0)
    d = car.get("seatDetail") or {}
    n += sum(int(d.get(k) or 0) for k in ("undef", "down", "lateralDn"))
    for t in car.get("tariffs") or []:
        n += int(t.get("freeSeats") or 0)
    return n

today = datetime.now()
for i in range(7):
    d = (today + timedelta(days=i)).strftime("%Y-%m-%d")
    for name, fc, tc in ROUTES:
        try:
            data = search(d, fc, tc)
        except Exception as e:
            print(f"{d} {name}: ОШИБКА {e}")
            continue
        trains = (data.get("data", {}).get("directions", {}).get("forward", {}) or {}).get("trains", [])
        if not trains:
            print(f"{d} {name}: 0 поездов")
            continue
        parts = []
        for t in trains:
            total = sum(free_seats(c) for c in t.get("cars", []))
            parts.append(f"{t.get('number')}[{t.get('brand') or t.get('type')}]={total}")
        print(f"{d} {name}: " + "; ".join(parts))
