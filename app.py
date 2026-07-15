"""
Smart Monitoring System — Production Backend
==============================================
Alur:
  1. iMonnit mengirim POST JSON ke /webhook/imonnit saat rule terpicu (suhu > threshold)
  2. Backend parse payload, cek suhu, kirim WhatsApp ke semua nomor terdaftar via CallMeBot
  3. Frontend (index.html) polling /api/events dan /api/numbers untuk live update
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, jsonify, request, render_template

app = Flask(__name__)

# ============================================================
# STORAGE (file JSON sederhana — cukup untuk demo/produksi skala kecil)
# Untuk skala lebih besar, ganti ke SQLite/Postgres.
# ============================================================
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
NUMBERS_FILE = DATA_DIR / "numbers.json"
EVENTS_FILE = DATA_DIR / "events.json"
THRESHOLD_FILE = DATA_DIR / "threshold.json"

DEFAULT_THRESHOLD = 27.0


def _load(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default
    return default


def _save(path, data):
    path.write_text(json.dumps(data, indent=2, default=str))


def get_numbers():
    return _load(NUMBERS_FILE, [])


def get_events():
    return _load(EVENTS_FILE, [])


def get_threshold():
    return _load(THRESHOLD_FILE, {"value": DEFAULT_THRESHOLD})["value"]


def add_event(event):
    events = get_events()
    events.insert(0, event)  # terbaru di atas
    events = events[:100]  # simpan max 100 event terakhir
    _save(EVENTS_FILE, events)


# ============================================================
# WHATSAPP SENDER (CallMeBot)
# ============================================================
def send_whatsapp(phone, api_key, message):
    url = "https://api.callmebot.com/whatsapp.php"
    params = {"phone": phone, "text": message, "apikey": api_key}
    try:
        r = requests.get(url, params=params, timeout=10)
        return r.status_code == 200, r.text
    except Exception as e:
        return False, str(e)


def notify_all(temperature, threshold, raw_payload=None):
    """Kirim WhatsApp ke semua nomor terdaftar, catat event."""
    numbers = get_numbers()
    message = (
        f"ALERT Smart Monitoring System\n"
        f"Suhu terdeteksi: {temperature:.1f} C\n"
        f"Batas aman: {threshold:.1f} C\n"
        f"Waktu: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}\n"
        f"Segera periksa unit penyimpanan."
    )

    results = []
    for n in numbers:
        ok, resp = send_whatsapp(n["phone"], n["api_key"], message)
        results.append({"name": n["name"], "phone": n["phone"], "success": ok})

    add_event({
        "timestamp": datetime.now().isoformat(),
        "temperature": temperature,
        "threshold": threshold,
        "status": "ALERT",
        "notified": results,
        "raw_payload": raw_payload,
    })
    return results


# ============================================================
# ROUTES — FRONTEND
# ============================================================
@app.route("/")
def index():
    return render_template("index.html")


# ============================================================
# ROUTES — API: REGISTRASI NOMOR
# ============================================================
@app.route("/api/numbers", methods=["GET"])
def api_get_numbers():
    # jangan expose api_key penuh ke frontend
    numbers = get_numbers()
    safe = [{"id": n["id"], "name": n["name"], "phone": n["phone"]} for n in numbers]
    return jsonify(safe)


@app.route("/api/numbers", methods=["POST"])
def api_add_number():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    api_key = (data.get("api_key") or "").strip()

    if not name or not phone or not api_key:
        return jsonify({"error": "Nama, nomor, dan API key wajib diisi."}), 400

    numbers = get_numbers()
    new_entry = {
        "id": int(time.time() * 1000),
        "name": name,
        "phone": phone,
        "api_key": api_key,
    }
    numbers.append(new_entry)
    _save(NUMBERS_FILE, numbers)
    return jsonify({"id": new_entry["id"], "name": name, "phone": phone}), 201


@app.route("/api/numbers/<int:number_id>", methods=["DELETE"])
def api_delete_number(number_id):
    numbers = get_numbers()
    numbers = [n for n in numbers if n["id"] != number_id]
    _save(NUMBERS_FILE, numbers)
    return jsonify({"deleted": number_id})


# ============================================================
# ROUTES — API: THRESHOLD
# ============================================================
@app.route("/api/threshold", methods=["GET"])
def api_get_threshold():
    return jsonify({"value": get_threshold()})


@app.route("/api/threshold", methods=["POST"])
def api_set_threshold():
    data = request.get_json(force=True)
    value = float(data.get("value", DEFAULT_THRESHOLD))
    _save(THRESHOLD_FILE, {"value": value})
    return jsonify({"value": value})


# ============================================================
# ROUTES — API: EVENTS (untuk live log di frontend)
# ============================================================
@app.route("/api/events", methods=["GET"])
def api_events():
    return jsonify(get_events())


# ============================================================
# ROUTE — WEBHOOK PENERIMA DARI IMONNIT
# ============================================================
@app.route("/webhook/imonnit", methods=["POST"])
def webhook_imonnit():
    """
    PENTING: Struktur JSON iMonnit bisa bervariasi tergantung template rule
    yang kamu pilih di dashboard iMonnit (Rule > Webhook > Payload format).

    Kode di bawah ini mencoba beberapa kemungkinan field yang umum dipakai.
    Setelah webhook pertama kali masuk, cek log mentah di /api/events atau
    log server untuk melihat struktur JSON asli, lalu sesuaikan fungsi
    extract_temperature() di bawah jika perlu.
    """
    payload = request.get_json(silent=True) or {}

    # Log payload mentah dulu — supaya kalau parsing gagal, kita tetap
    # punya data untuk debug & sesuaikan.
    app.logger.info(f"iMonnit webhook payload diterima: {payload}")

    temperature = extract_temperature(payload)
    threshold = get_threshold()

    if temperature is None:
        # Tetap catat event supaya kelihatan di log, tapi tidak kirim alert
        add_event({
            "timestamp": datetime.now().isoformat(),
            "temperature": None,
            "threshold": threshold,
            "status": "PAYLOAD TIDAK DIKENALI",
            "notified": [],
            "raw_payload": payload,
        })
        return jsonify({"status": "received_but_unparsed", "payload": payload}), 200

    if temperature >= threshold:
        results = notify_all(temperature, threshold, raw_payload=payload)
        return jsonify({"status": "alert_sent", "temperature": temperature, "notified": results}), 200
    else:
        add_event({
            "timestamp": datetime.now().isoformat(),
            "temperature": temperature,
            "threshold": threshold,
            "status": "NORMAL",
            "notified": [],
            "raw_payload": payload,
        })
        return jsonify({"status": "normal", "temperature": temperature}), 200


def extract_temperature(payload):
    """Coba beberapa struktur umum payload webhook iMonnit."""
    candidates = [
        payload.get("Value"),
        payload.get("value"),
        payload.get("CurrentReading"),
        payload.get("SensorValue"),
        payload.get("Reading"),
    ]

    # Kadang datanya nested, misal payload["Readings"][0]["Value"]
    readings = payload.get("Readings") or payload.get("readings")
    if isinstance(readings, list) and readings:
        candidates.append(readings[0].get("Value") or readings[0].get("value"))

    for c in candidates:
        if c is not None:
            try:
                return float(c)
            except (ValueError, TypeError):
                continue
    return None


# ============================================================
# ROUTE — TEST MANUAL (untuk simulasi tanpa sensor fisik / iMonnit asli)
# ============================================================
@app.route("/api/test-trigger", methods=["POST"])
def test_trigger():
    """Endpoint bantu untuk simulasi manual saat latihan sebelum demo."""
    data = request.get_json(force=True)
    temperature = float(data.get("temperature", 28.0))
    threshold = get_threshold()

    if temperature >= threshold:
        results = notify_all(temperature, threshold, raw_payload={"source": "manual_test"})
        return jsonify({"status": "alert_sent", "temperature": temperature, "notified": results})
    else:
        add_event({
            "timestamp": datetime.now().isoformat(),
            "temperature": temperature,
            "threshold": threshold,
            "status": "NORMAL",
            "notified": [],
            "raw_payload": {"source": "manual_test"},
        })
        return jsonify({"status": "normal", "temperature": temperature})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)