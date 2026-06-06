// ══════════════════════════════════════════════
// CHECKLIST SHOLAT HARIAN — script.js (Upgraded)
// Jadwal Sholat: Tarakan, Kalimantan Utara (WITA)
// ══════════════════════════════════════════════

const prayers = ['subuh', 'dzuhur', 'ashar', 'maghrib', 'isya'];
const PRAYER_LABELS = { subuh: 'Subuh', dzuhur: 'Dzuhur', ashar: 'Ashar', maghrib: 'Maghrib', isya: 'Isya' };
const PRAYER_ICONS = { subuh: '🌙', dzuhur: '☀️', ashar: '🌤️', maghrib: '🌇', isya: '🌃' };

// Koordinat Tarakan, Kaltara (WITA = UTC+8)
const LAT = 3.3011;
const LON = 117.5765;

let prayerTimes = null;
let countdownInterval = null;

// ── Tanggal ──
(function () {
    const d = new Date();
    const days = ['Minggu', 'Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu'];
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Ags', 'Sep', 'Okt', 'Nov', 'Des'];
    const el = document.getElementById('tanggal-hari');
    if (el) el.textContent = days[d.getDay()] + ', ' + d.getDate() + ' ' + months[d.getMonth()] + ' ' + d.getFullYear();
})();

// ── Helper: waktu WITA ──
function getCurrentWITA() {
    const now = new Date();
    const utc = now.getTime() + now.getTimezoneOffset() * 60000;
    return new Date(utc + 8 * 3600000);
}

// ── Fetch Jadwal dari Aladhan API ──
async function fetchPrayerTimes() {
    try {
        const today = new Date();
        const dd = String(today.getDate()).padStart(2, '0');
        const mm = String(today.getMonth() + 1).padStart(2, '0');
        const yyyy = today.getFullYear();
        const url = `https://api.aladhan.com/v1/timings/${dd}-${mm}-${yyyy}?latitude=${LAT}&longitude=${LON}&method=11`;
        const res = await fetch(url);
        const json = await res.json();

        if (json.code === 200) {
            const t = json.data.timings;
            prayerTimes = {
                subuh: t.Fajr.substring(0, 5),
                dzuhur: t.Dhuhr.substring(0, 5),
                ashar: t.Asr.substring(0, 5),
                maghrib: t.Maghrib.substring(0, 5),
                isya: t.Isha.substring(0, 5)
            };
        }
    } catch (e) {
        // Fallback perkiraan waktu Tarakan
        prayerTimes = { subuh: '04:28', dzuhur: '12:06', ashar: '15:18', maghrib: '18:13', isya: '19:22' };
    }
    renderSchedule();
    updatePrayerTimeTexts();
    startCountdown();
}

// ── Render Grid Jadwal ──
function renderSchedule() {
    const container = document.getElementById('schedule-content');
    if (!container || !prayerTimes) return;

    const now = getCurrentWITA();
    const nowMins = now.getHours() * 60 + now.getMinutes();
    const timeMins = {};
    prayers.forEach(p => {
        const [h, m] = prayerTimes[p].split(':').map(Number);
        timeMins[p] = h * 60 + m;
    });

    let nextPrayer = null;
    let passed = [];
    for (let i = 0; i < prayers.length; i++) {
        if (timeMins[prayers[i]] > nowMins) {
            nextPrayer = prayers[i];
            passed = prayers.slice(0, i);
            break;
        }
    }
    if (!nextPrayer) passed = [...prayers];

    let html = '<div class="schedule-grid">';
    prayers.forEach(p => {
        const isNext = p === nextPrayer;
        const isPassed = passed.includes(p);
        let cls = 'sched-item';
        if (isNext) cls += ' active';
        if (isPassed) cls += ' passed';
        html += `<div class="${cls}">
            ${isNext ? '<span class="sched-badge">Selanjutnya</span>' : ''}
            <span class="sched-icon">${PRAYER_ICONS[p]}</span>
            <span class="sched-name">${PRAYER_LABELS[p]}</span>
            <span class="sched-time">${prayerTimes[p]}</span>
        </div>`;
    });
    html += '</div>';

    if (nextPrayer) {
        html += `<div class="countdown-bar" id="countdown-bar">
            <span class="countdown-label">Menuju <strong>${PRAYER_LABELS[nextPrayer]}</strong></span>
            <span class="countdown-time" id="countdown-time">--:--:--</span>
        </div>`;
    } else {
        html += `<div class="countdown-bar"><span class="countdown-label">Semua sholat hari ini selesai 🌟</span></div>`;
    }
    container.innerHTML = html;

    // Update header pill
    const pill = document.getElementById('next-prayer-label');
    if (pill) {
        pill.textContent = nextPrayer
            ? `${PRAYER_LABELS[nextPrayer]} ${prayerTimes[nextPrayer]}`
            : 'Alhamdulillah ✓';
    }
}

// ── Countdown Timer ──
function startCountdown() {
    if (countdownInterval) clearInterval(countdownInterval);
    countdownInterval = setInterval(updateCountdown, 1000);
    updateCountdown();
}

function updateCountdown() {
    if (!prayerTimes) return;
    const now = getCurrentWITA();
    const nowMins = now.getHours() * 60 + now.getMinutes();
    const nowSecs = nowMins * 60 + now.getSeconds();

    let nextMins = null;
    let nextPrayer = null;
    for (let i = 0; i < prayers.length; i++) {
        const [h, m] = prayerTimes[prayers[i]].split(':').map(Number);
        if (h * 60 + m > nowMins) {
            nextPrayer = prayers[i];
            nextMins = h * 60 + m;
            break;
        }
    }

    const el = document.getElementById('countdown-time');
    if (!el || !nextPrayer) return;

    let diff = nextMins * 60 - nowSecs;
    if (diff < 0) diff = 0;
    const h = Math.floor(diff / 3600);
    const m = Math.floor((diff % 3600) / 60);
    const s = diff % 60;
    el.textContent = String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');

    if (s === 0 && m === 0) renderSchedule(); // refresh saat ganti sholat
}

// ── Update teks waktu di checklist ──
function updatePrayerTimeTexts() {
    if (!prayerTimes) return;
    prayers.forEach(p => {
        const el = document.getElementById('pt-' + p);
        if (el) el.textContent = prayerTimes[p] + ' WITA';
    });
}

// ── Toggle checklist item ──
function toggleItem(id) {
    const cb = document.getElementById(id);
    const item = document.getElementById('item-' + id);
    cb.checked = !cb.checked;
    item.classList.toggle('checked', cb.checked);
    item.classList.add('pulse-anim');
    setTimeout(() => item.classList.remove('pulse-anim'), 300);
    saveChecklist();
    updateProgress();
}

// ── Update progress bar ──
function updateProgress() {
    const checked = prayers.filter(p => document.getElementById(p).checked).length;
    const numEl = document.getElementById('count-num');
    const fillEl = document.getElementById('progress-fill');
    if (numEl) numEl.textContent = checked;
    if (fillEl) fillEl.style.width = (checked / 5 * 100) + '%';
}

// ── Simpan ke localStorage ──
function saveChecklist() {
    const data = {};
    prayers.forEach(p => data[p] = document.getElementById(p).checked);
    try { localStorage.setItem('sholatChecklist', JSON.stringify(data)); } catch (e) { }
}

// ── Load dari localStorage ──
function loadChecklist() {
    try {
        const saved = JSON.parse(localStorage.getItem('sholatChecklist') || 'null');
        if (saved) {
            prayers.forEach(p => {
                if (saved[p]) {
                    document.getElementById(p).checked = true;
                    document.getElementById('item-' + p).classList.add('checked');
                }
            });
        }
        const nama = localStorage.getItem('sholatNama') || '';
        const namaEl = document.getElementById('nama');
        if (nama && namaEl) namaEl.value = nama;
    } catch (e) { }
    updateProgress();
}

// ── Form Submit ──
function handleSubmit(e) {
    e.preventDefault();
    const nama = document.getElementById('nama').value.trim();

    if (!nama) { showMsg('❌ Nama harus diisi dulu ya!', 'error'); return; }
    if (nama.length < 3) { showMsg('❌ Nama minimal 3 karakter!', 'error'); return; }

    const status = {};
    prayers.forEach(p => status[p] = document.getElementById(p).checked);
    const total = Object.values(status).filter(Boolean).length;
    const dataKirim = { nama, ...status, totalSholat: total };

    try { localStorage.setItem('sholatNama', nama); } catch (e) { }

    const btn = document.getElementById('btn-submit');
    const origHTML = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '⏳ Mengirim...';
    showMsg('⏳ Menghubungi server...', '');

    fetch('https://reve.pythonanywhere.com/sholat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(dataKirim)
    })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success' || data.status === 'sukses') {
                showMsg('✅ Laporan diterima! ' + (data.message || '') + ` (${total}/5 sholat)`, 'success');
                document.querySelectorAll('#sholatForm input[type=checkbox]').forEach(cb => cb.checked = false);
                prayers.forEach(p => document.getElementById('item-' + p).classList.remove('checked'));
                try { localStorage.removeItem('sholatChecklist'); } catch (e) { }
                updateProgress();
                setTimeout(() => document.getElementById('pesan-server').classList.add('hidden'), 5000);
            } else {
                showMsg('❌ Gagal: ' + (data.message || 'Server menolak data'), 'error');
            }
            resetBtn();
        })
        .catch(() => {
            showMsg('❌ Koneksi gagal! Cek koneksi internetmu.', 'error');
            resetBtn();
        });

    function resetBtn() { btn.disabled = false; btn.innerHTML = origHTML; }
}

function showMsg(text, type) {
    const d = document.getElementById('pesan-server');
    d.textContent = text;
    d.className = type || '';
    d.classList.remove('hidden');
}

// ── Init ──
loadChecklist();
fetchPrayerTimes();
