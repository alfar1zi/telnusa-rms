import type { Dict } from "../types";

/**
 * Chrome bersama dalam Bahasa Indonesia. Istilah baku NOC (device, monitoring,
 * telemetry, dashboard) tetap dipakai supaya operator tidak perlu menerjemahkan
 * dua kosakata; token status ("NO DATA", "Loading") sengaja sama di kedua bahasa.
 */
const core: Dict = {
  // Navigasi dan kerangka halaman
  "nav.navigation": "NAVIGASI",
  "nav.dashboard": "Dasbor",
  "nav.devices": "Perangkat",
  "nav.monitoring": "Pemantauan",
  "nav.alarms": "Alarm",
  "nav.reports": "Laporan",
  "nav.settings": "Pengaturan",
  "brand.name": "TELNUSA NMS",
  "brand.tagline": "OPERASI NOC",
  "header.title.dashboard": "Dasbor Ikhtisar",
  "header.title.devices": "Daftar Perangkat",
  "header.title.monitoring": "Analitik Telemetri",
  "header.title.alarms": "Alarm & Keamanan",
  "header.title.reports": "Laporan Historis",
  "header.title.settings": "Pengaturan Sistem",
  "header.title.deviceDetail": "Telemetri & Kendali Perangkat",
  "header.title.remote": "Akses Jarak Jauh Aman",
  "header.toggleTheme": "Ganti tema",
  "header.theme.light": "Terang (Linen)",
  "header.theme.dark": "Gelap (Charcoal)",
  "header.theme.system": "Ikut Sistem",
  "header.userMenu": "Menu pengguna",
  "header.language": "Bahasa",
  "header.signOut": "Keluar",
  "header.sidebarToggle": "Buka/tutup navigasi",

  // Aksi dan status umum
  "common.loading": "Loading",
  "common.retry": "Coba lagi",
  "common.cancel": "Batal",
  "common.save": "Simpan",
  "common.close": "Tutup",
  "common.contactAdmin": "Jika masalah berlanjut, hubungi administrator.",
  "common.expandSidebar": "Buka navigasi",
  "outage.title": "Layanan Tidak Tersedia",
  "outage.message": "Backend TELNUSA tidak dapat dijangkau, sehingga halaman ini tidak bisa dimuat. Sesi Anda tidak ditolak.",

  // Semantik kesehatan router
  "state.connected": "Terhubung",
  "state.degraded": "Menurun",
  "state.disconnected": "Terputus",
  "state.unknown": "Tidak diketahui",
  "state.unknownUpper": "TIDAK DIKETAHUI",
  "state.noDataLabel": "Tanpa Data",
  "state.noData": "NO DATA",
  "state.unassigned": "BELUM DITETAPKAN",
  "state.healthy": "SEHAT",
  "state.warning": "PERINGATAN",
  "state.critical": "KRITIS",
  "state.notIntegrated": "BELUM TERINTEGRASI",

  // Status metrik
  "metricState.data": "DATA TERSEDIA",
  "metricState.noDataInRange": "TIDAK ADA DATA PADA RENTANG INI",
  "metricState.notIntegrated": "BELUM TERINTEGRASI",
  "metricState.error": "ERROR",
  "metricState.empty.notIntegrated": "Metrik ini belum punya sumber data.",
  "metricState.empty.error": "Metrik ini gagal dibaca dari sumbernya.",
  "metricState.empty.noDataInRange": "Sumbernya terintegrasi tetapi tidak mengirim apa pun pada rentang ini.",
  "metricState.empty.data": "Data tersedia.",

  // Status sumber kesehatan
  "healthState.available": "DATA TERSEDIA",
  "healthState.noData": "NO DATA",
  "healthState.notIntegrated": "BELUM TERINTEGRASI",
  "healthState.error": "ERROR",

  // Jenis event
  "event.alarmCreated": "Alarm Dibuat",
  "event.alarmResolved": "Alarm Selesai",
  "event.statusChange": "Perubahan Status",
  "event.configChange": "Perubahan Konfigurasi",
  "event.reboot": "Reboot",
  "event.unknown": "Event Tidak Dikenal",
  "event.unnamed": "Event tanpa nama",

  // Badge status dan sensor
  "badge.status.online": "Online",
  "badge.status.offline": "Offline",
  "badge.status.stale": "Telemetri Basi",
  "badge.status.clockSkew": "Jam Tidak Sinkron",
  "badge.status.sensorFault": "Sensor Bermasalah",
  "badge.status.partial": "Data Sebagian",
  "badge.status.degraded": "Perlu Perhatian",
  "badge.status.noData": "Tanpa Data",
  "badge.status.unknown": "Tidak Diketahui",
  "badge.status.allOk": "Normal",
  "badge.status.notConnected": "Tidak Terhubung",
  "badge.severity.disaster": "DISASTER",
  "badge.severity.high": "TINGGI",
  "badge.severity.average": "SEDANG",
  "badge.severity.warning": "PERINGATAN",
  "badge.severity.information": "INFO",
  "badge.sensor.allOk": "Normal",
  "badge.sensor.ok": "Normal",
  "badge.sensor.partial": "Data Sebagian",
  "badge.sensor.sensorFault": "Sensor Bermasalah",
  "badge.sensor.warning": "Perhatian",
  "badge.sensor.noData": "Tanpa Data",
  "badge.sensor.notInstalled": "Belum Terpasang",
  "badge.sensor.notIntegrated": "Belum Terintegrasi",

  // Autentikasi
  "auth.passwordChangeFailed": "Kata sandi gagal diubah. Periksa kata sandi saat ini.",
  "auth.passwordChangeFailedGeneric": "Gagal mengubah kata sandi.",

  // Rentang waktu
  "range.15m": "15 menit terakhir",
  "range.1h": "1 jam terakhir",
  "range.6h": "6 jam terakhir",
  "range.12h": "12 jam terakhir",
  "range.24h": "24 jam terakhir",
  "range.7d": "7 hari terakhir",
  "range.30d": "30 hari terakhir",

  // Kerangka chart telemetri
  "chart.loadFailed": "Gagal memuat data telemetri.",
  "chart.partial": "sebagian",

  // Login
  "login.brand": "Network Management System",
  "login.organizedBy": "Diselenggarakan oleh Telnusa Intrakom",
  "login.product": "NETWORK MANAGEMENT SYSTEM",
  "login.welcome": "Selamat datang di Network Management System",
  "login.subtitle": "Masuk untuk melanjutkan ke konsol NMS.",
  "login.email": "Email",
  "login.password": "Kata sandi",
  "login.submit": "Masuk",
  "login.submitting": "Sedang masuk...",
  "login.signInRequired": "Masuk untuk melanjutkan.",
  "login.sessionExpired": "Sesi berakhir. Silakan masuk lagi.",

  // Deskripsi chart: satu per metrik (canvas satu seri)
  "chart.metric.mppt.pvInputPower": "Berapa besar daya DC yang dihasilkan panel saat ini, dihitung dari volt x ampere tiap sampel.",
  "chart.metric.mppt.outputPower": "Berapa besar daya DC yang dikeluarkan MPPT saat ini, dihitung dari volt x ampere tiap sampel.",
  "chart.metric.mppt.pvVoltage": "Tegangan dari panel yang masuk ke MPPT; turun ke 0 V saat panel tidak berproduksi.",
  "chart.metric.mppt.pvCurrent": "Arus yang disuplai panel; mengikuti kurva sinar matahari harian.",
  "chart.metric.mppt.outputVoltage": "Tegangan yang dikeluarkan MPPT di terminal keluaran.",
  "chart.metric.mppt.outputCurrent": "Arus yang dikeluarkan MPPT; 0 A selama sisi keluaran menganggur.",
  "chart.metric.pzem.phaseAVoltage": "Tegangan fasa A pada suplai site.",
  "chart.metric.pzem.phaseBVoltage": "Tegangan fasa B pada suplai site.",
  "chart.metric.pzem.phaseCVoltage": "Tegangan fasa C pada suplai site.",
  "chart.metric.pzem.phaseACurrent": "Arus beban pada fasa A; 0 A berarti fasa itu tidak berbeban.",
  "chart.metric.pzem.phaseBCurrent": "Arus beban pada fasa B; 0 A berarti fasa itu tidak berbeban.",
  "chart.metric.pzem.phaseCCurrent": "Arus beban pada fasa C; 0 A berarti fasa itu tidak berbeban.",
  "chart.metric.pzem.energy": "Berapa banyak energi yang sudah dipakai site secara total, dari register kumulatif meter itu sendiri.",
  "chart.metric.solar.radiation": "Berapa banyak sinar matahari yang diterima panel, diukur pada bidang panel.",
  "chart.metric.system.temperature": "Seberapa panas router itu sendiri, sesuai laporan perangkat.",
  "chart.metric.modem.signal": "Seberapa kuat sinyal seluler; makin dekat ke 0 dBm makin kuat.",
  "chart.metric.modem.temperature": "Seberapa panas modul modem, sesuai laporan perangkat.",

  // Deskripsi chart: satu per canvas perbandingan
  "chart.scope.power.dc": "Berapa besar daya DC yang dihasilkan panel dibanding yang dikeluarkan MPPT, dihitung per sampel.",
  "chart.scope.voltage.dc": "Tegangan panel yang masuk ke MPPT dibanding tegangan yang dikeluarkan MPPT.",
  "chart.scope.voltage.ac": "Tegangan saluran pada ketiga fasa AC yang menyuplai site.",
  "chart.scope.current.ac": "Berapa besar arus yang ditarik tiap fasa AC; 0 A berarti fasa itu tidak berbeban.",

  // Alamat tidak dikenal
  "notFound.title": "Halaman tidak ditemukan",
  "notFound.subtitle": "Alamat yang dibuka tidak cocok dengan halaman konsol mana pun.",
  "notFound.message": "Halaman ini tidak ada, atau tautan yang membawa ke sini sudah kedaluwarsa.",
  "notFound.action": "Kembali ke dashboard",
};

export default core;
