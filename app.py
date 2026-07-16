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
import re
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


def notify_all(temperature, threshold, raw_payload=None, device_name=None,
                rule_name=None, reading_text=None):
    """Kirim WhatsApp ke semua nomor terdaftar, catat event."""
    numbers = get_numbers()

    temp_line = f"Suhu terdeteksi: {temperature:.1f} C" if temperature is not None \
        else f"Reading: {reading_text or 'N/A'}"

    message_lines = [
        "ALERT Smart Monitoring System",
    ]
    if device_name:
        message_lines.append(f"Device: {device_name}")
    if rule_name:
        message_lines.append(f"Rule: {rule_name}")
    message_lines.append(temp_line)
    if threshold is not None:
        message_lines.append(f"Batas aman: {threshold:.1f} C")
    message_lines.append(f"Waktu: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}")
    message_lines.append("Segera periksa unit penyimpanan.")
    message = "\n".join(message_lines)

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
        "device_name": device_name,
        "rule_name": rule_name,
        "reading_text": reading_text,
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
    Struktur JSON iMonnit yang sebenarnya (Rule Webhook Contents), field lowercase:

        {
          "subject": "...", "reading": "Temperature: 28.4 F", "rule": "...",
          "date": "...", "time": "...", "readingDate": "...", "readingTime": "...",
          "originalReadingDate": "...", "originalReadingTime": "...",
          "originalReading": "...", "acknowledgeURL": "...", "parentAccount": "...",
          "deviceID": "...", "name": "...", "networkID": "...", "network": "...",
          "accountID": "...", "accountNumber": "...", "companyName": "..."
        }

    PENTING: webhook ini HANYA dipanggil iMonnit kalau rule-nya sendiri sudah
    terpicu (kondisi ambang batas sudah dicek di sisi iMonnit). Jadi begitu
    endpoint ini menerima request, kita anggap itu ALERT dan langsung kirim
    WhatsApp — bukan mengevaluasi ulang threshold dari nol. Angka suhu tetap
    kita ekstrak dari field "reading" (formatnya string, misal "Temperature: 28.4 F"),
    supaya bisa ditampilkan di dashboard dan pesan WhatsApp.
    """
    payload = request.get_json(silent=True) or {}
    app.logger.info(f"iMonnit webhook payload diterima: {payload}")

    reading_text = payload.get("reading") or payload.get("originalReading") or ""
    temperature = extract_temperature(reading_text)
    threshold = get_threshold()

    device_name = payload.get("name") or payload.get("deviceID") or "Unknown Device"
    rule_name = payload.get("rule") or payload.get("subject") or "Unknown Rule"

    # Webhook dipanggil = rule sudah terpicu di iMonnit -> selalu kirim alert
    results = notify_all(
        temperature=temperature,
        threshold=threshold,
        raw_payload=payload,
        device_name=device_name,
        rule_name=rule_name,
        reading_text=reading_text,
    )

    return jsonify({
        "status": "alert_sent",
        "temperature": temperature,
        "reading_text": reading_text,
        "device_name": device_name,
        "rule_name": rule_name,
        "notified": results,
    }), 200


def extract_temperature(reading_text):
    """
    Field 'reading' dari iMonnit berbentuk string seperti:
      "Temperature: 28.4 F"  atau  "Temp: -18.2 C"  atau  "Battery: 10%"
    Kita ambil angka pertama (termasuk desimal & minus) dari string tsb.
    """
    if not reading_text:
        return None
    match = re.search(r"-?\d+(\.\d+)?", reading_text)
    if match:
        try:
            return float(match.group())
        except (ValueError, TypeError):
            return None
    return None


# ============================================================
# ROUTE — TEST MANUAL (untuk simulasi tanpa sensor fisik / iMonnit asli)
# ============================================================
@app.route("/api/test-trigger", methods=["POST"])
def test_trigger():
    """
    Endpoint bantu untuk simulasi manual saat latihan/demo sebelum sensor asli
    tersambung. Berbeda dengan /webhook/imonnit, di sini kita MEMANG mengecek
    threshold sendiri (karena tidak ada rule iMonnit yang sudah memvalidasi).
    """
    data = request.get_json(force=True)
    temperature = float(data.get("temperature", 28.0))
    threshold = get_threshold()

    if temperature >= threshold:
        results = notify_all(
            temperature=temperature,
            threshold=threshold,
            raw_payload={"source": "manual_test"},
            device_name="Uji Manual",
            rule_name="Manual Test Trigger",
            reading_text=f"Temperature: {temperature} C",
        )
        return jsonify({"status": "alert_sent", "temperature": temperature, "notified": results})
    else:
        add_event({
            "timestamp": datetime.now().isoformat(),
            "temperature": temperature,
            "threshold": threshold,
            "status": "NORMAL",
            "notified": [],
            "device_name": "Uji Manual",
            "rule_name": "Manual Test Trigger",
            "reading_text": f"Temperature: {temperature} C",
            "raw_payload": {"source": "manual_test"},
        })
        return jsonify({"status": "normal", "temperature": temperature})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
