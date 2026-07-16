const POLL_INTERVAL_MS = 3000;

const els = {
  connDot: document.querySelector('.conn-dot'),
  connLabel: document.getElementById('connLabel'),
  readoutValue: document.getElementById('readoutValue'),
  readoutStatus: document.getElementById('readoutStatus'),
  thresholdInput: document.getElementById('thresholdInput'),
  saveThreshold: document.getElementById('saveThreshold'),
  testTemp: document.getElementById('testTemp'),
  testTrigger: document.getElementById('testTrigger'),
  registerForm: document.getElementById('registerForm'),
  nameInput: document.getElementById('nameInput'),
  phoneInput: document.getElementById('phoneInput'),
  apiKeyInput: document.getElementById('apiKeyInput'),
  numbersList: document.getElementById('numbersList'),
  logConsole: document.getElementById('logConsole'),
  webhookUrl: document.getElementById('webhookUrl'),
};

let renderedEventIds = new Set();

// Tampilkan URL webhook lengkap (domain saat ini + path)
els.webhookUrl.textContent = `${window.location.origin}/webhook/imonnit`;

function setConnected(ok) {
  els.connDot.classList.toggle('online', ok);
  els.connLabel.textContent = ok ? 'Terhubung' : 'Terputus';
}

// ============================================================
// THRESHOLD
// ============================================================
async function loadThreshold() {
  try {
    const res = await fetch('/api/threshold');
    const data = await res.json();
    els.thresholdInput.value = data.value;
  } catch (e) { /* silent */ }
}

els.saveThreshold.addEventListener('click', async () => {
  const value = parseFloat(els.thresholdInput.value);
  if (isNaN(value)) return;
  els.saveThreshold.textContent = 'Menyimpan…';
  try {
    await fetch('/api/threshold', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    });
    els.saveThreshold.textContent = 'Tersimpan';
  } catch (e) {
    els.saveThreshold.textContent = 'Gagal';
  }
  setTimeout(() => (els.saveThreshold.textContent = 'Simpan'), 1500);
});

// ============================================================
// TEST TRIGGER (uji manual)
// ============================================================
els.testTrigger.addEventListener('click', async () => {
  const temperature = parseFloat(els.testTemp.value);
  if (isNaN(temperature)) return;
  els.testTrigger.textContent = 'Mengirim…';
  els.testTrigger.disabled = true;
  try {
    await fetch('/api/test-trigger', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ temperature }),
    });
    await refreshEvents();
  } catch (e) { /* silent */ }
  els.testTrigger.textContent = 'Kirim Uji Suhu';
  els.testTrigger.disabled = false;
});

// ============================================================
// REGISTRASI NOMOR
// ============================================================
els.registerForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const payload = {
    name: els.nameInput.value.trim(),
    phone: els.phoneInput.value.trim(),
    api_key: els.apiKeyInput.value.trim(),
  };
  const submitBtn = els.registerForm.querySelector('button[type="submit"]');
  submitBtn.textContent = 'Mendaftarkan…';
  submitBtn.disabled = true;

  try {
    const res = await fetch('/api/numbers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      els.registerForm.reset();
      await refreshNumbers();
    }
  } catch (e) { /* silent */ }

  submitBtn.textContent = 'Daftarkan Nomor';
  submitBtn.disabled = false;
});

async function refreshNumbers() {
  try {
    const res = await fetch('/api/numbers');
    const numbers = await res.json();
    if (numbers.length === 0) {
      els.numbersList.innerHTML = '<div class="empty-state">Belum ada nomor terdaftar.</div>';
      return;
    }
    els.numbersList.innerHTML = numbers.map(n => `
      <div class="number-card" data-id="${n.id}">
        <div class="number-card-info">
          <span class="number-card-name">${escapeHtml(n.name)}</span>
          <span class="number-card-phone">${escapeHtml(n.phone)}</span>
        </div>
        <button class="number-card-remove" data-id="${n.id}" title="Hapus">&times;</button>
      </div>
    `).join('');

    els.numbersList.querySelectorAll('.number-card-remove').forEach(btn => {
      btn.addEventListener('click', async () => {
        await fetch(`/api/numbers/${btn.dataset.id}`, { method: 'DELETE' });
        await refreshNumbers();
      });
    });
  } catch (e) { /* silent */ }
}

// ============================================================
// LIVE READOUT + EVENT LOG (polling)
// ============================================================
async function refreshEvents() {
  try {
    const res = await fetch('/api/events');
    const events = await res.json();
    setConnected(true);

    if (events.length > 0) {
      const latest = events[0];
      updateReadout(latest);
    }

    // render log terbaru yang belum ditampilkan
    const newOnes = events.filter(ev => !renderedEventIds.has(ev.timestamp));
    if (newOnes.length > 0) {
      if (els.logConsole.querySelector('.log-empty')) {
        els.logConsole.innerHTML = '';
      }
      newOnes.reverse().forEach(ev => {
        renderedEventIds.add(ev.timestamp);
        const entry = document.createElement('div');
        const isAlert = ev.status === 'ALERT';
        entry.className = `log-entry ${isAlert ? 'alert' : 'normal'}`;
        const time = new Date(ev.timestamp).toLocaleTimeString('id-ID');
        const tempTxt = ev.temperature !== null && ev.temperature !== undefined
          ? `${ev.temperature.toFixed?.(1) ?? ev.temperature}°C` : 'N/A';
        const deviceTxt = ev.device_name ? ` [${ev.device_name}]` : '';
        const ruleTxt = ev.rule_name ? ` — rule: ${ev.rule_name}` : '';
        const msg = isAlert
          ? `ALERT${deviceTxt} — ${tempTxt}${ruleTxt} — WhatsApp terkirim ke ${ev.notified.length} nomor`
          : ev.status === 'PAYLOAD TIDAK DIKENALI'
            ? `Payload diterima tapi format tidak dikenali — cek log server`
            : `Normal${deviceTxt} — ${tempTxt} (batas ${ev.threshold}°C)`;
        entry.innerHTML = `<span class="log-time">${time}</span><span class="log-msg">${msg}</span>`;
        els.logConsole.prepend(entry);
      });
    }
  } catch (e) {
    setConnected(false);
  }
}

function updateReadout(latest) {
  const isAlert = latest.status === 'ALERT';
  if (latest.temperature !== null && latest.temperature !== undefined) {
    els.readoutValue.textContent = Number(latest.temperature).toFixed(1);
  }
  els.readoutValue.classList.toggle('alert', isAlert);
  els.readoutStatus.classList.toggle('alert', isAlert);
  els.readoutStatus.classList.toggle('normal', !isAlert);
  els.readoutStatus.textContent = isAlert
    ? `Suhu melebihi batas aman — alert terkirim`
    : `Suhu dalam batas aman`;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ============================================================
// INIT
// ============================================================
async function init() {
  await loadThreshold();
  await refreshNumbers();
  await refreshEvents();
  setInterval(refreshEvents, POLL_INTERVAL_MS);
}

init();
