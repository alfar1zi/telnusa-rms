import type { Dict } from "../types";

/**
 * Slice bahasa Indonesia untuk layar autentikasi dan akses jarak jauh.
 *
 * `auth.*` mencakup keadaan layar masuk dan layar wajib ganti kata sandi;
 * `remote.*` mencakup kartu akses jarak jauh, halaman Web UI jarak jauh, dan
 * halaman terminal jarak jauh. Bagian statis halaman masuk (merek, label
 * kolom, aksi utama) memakai kunci `login.*` pada core, dan dua pesan
 * kegagalan ganti sandi memakai `auth.passwordChangeFailed*` di core; slice
 * ini hanya menambah yang belum dimiliki core.
 *
 * Nilai milik server (alamat surel akun, id perangkat, isi pesan galat dari
 * API) tetap dirender sebagai data, bukan salinan. Baris "BLOCKED: ..."
 * adalah kosakata status konsol dan tokennya tetap berbahasa Inggris di kedua
 * bahasa, sama seperti "NO DATA" di core.
 */
const auth: Dict = {
  // Keadaan formulir masuk
  "auth.showPassword": "Tampilkan kata sandi",
  "auth.hidePassword": "Sembunyikan kata sandi",
  "auth.invalidCredentials": "Kredensial tidak valid.",
  "auth.tooManyAttempts": "Terlalu banyak percobaan. Coba lagi nanti.",
  "auth.signInFailed": "Gagal masuk. Coba lagi.",
  "auth.serverUnreachable": "Server tidak dapat dijangkau. Periksa koneksi jaringan.",
  "auth.emailPlaceholder": "name@example.com",
  "auth.emailPlaceholderMock": "admin@telnusa.dev",
  "auth.submitMock": "Masuk (Mode Mock)",
  "auth.mockModeNotice": "Mode mock pengembangan: tidak perlu kredensial",

  // Wajib ganti kata sandi
  "auth.securityRequirement": "Persyaratan Keamanan",
  "auth.changeRequiredTitle": "Wajib Ubah Kata Sandi",
  "auth.changeRequiredPrefix": "Akun ",
  "auth.changeRequiredSuffix": " perlu diperbarui kata sandinya sebelum mengakses TELNUSA NMS.",
  "auth.currentPassword": "Kata Sandi Saat Ini",
  "auth.newPassword": "Kata Sandi Baru (min 8 karakter)",
  "auth.confirmPassword": "Konfirmasi Kata Sandi Baru",
  "auth.passwordTooShort": "Kata sandi baru harus minimal 8 karakter.",
  "auth.passwordUnchanged": "Kata sandi baru harus berbeda dari kata sandi saat ini.",
  "auth.passwordMismatch": "Kata sandi baru dan konfirmasinya tidak sama.",
  "auth.passwordUpdated": "Kata sandi diperbarui.",
  "auth.redirecting": "Mengalihkan ke dashboard...",
  "auth.updatingPassword": "Memperbarui Kata Sandi...",
  "auth.updatePasswordAndContinue": "Perbarui Kata Sandi & Lanjutkan",
  "auth.signOut": "Keluar",

  // Kartu akses jarak jauh
  "remote.webUi": "Web UI",
  "remote.webUiDesc": "WebUI bawaan Teltonika RUT906",
  "remote.terminal": "Terminal",
  "remote.terminalDesc": "SSH melalui Apache Guacamole",
  "remote.zabbix": "Zabbix",
  "remote.zabbixDesc": "Monitoring dan riwayat",
  "remote.zabbixInternalOnly": "Akses Zabbix langsung hanya untuk internal",
  "remote.notConfigured": "Belum Dikonfigurasi",
  "remote.open": "Buka",

  // Halaman Web UI dan terminal jarak jauh
  "remote.deviceFallback": "Perangkat",
  "remote.webUiTitle": "Web UI Jarak Jauh: {device}",
  "remote.webUiSubtitle": "Antarmuka Manajemen Web Teltonika RUT906 Bawaan",
  "remote.webUiBlocked": "BLOCKED: MISSING EXTERNAL SERVICE (proksi WebUI RUT / ZeroTier belum dikonfigurasi).",
  "remote.webUiFailed": "Gagal membuka koneksi Web UI jarak jauh.",
  "remote.terminalTitle": "Terminal Jarak Jauh: {device}",
  "remote.terminalSubtitle": "Terminal SSH Aman melalui Apache Guacamole",
  "remote.terminalBlocked": "BLOCKED: MISSING EXTERNAL SERVICE (Guacamole / tunnel SSH belum dikonfigurasi).",
  "remote.terminalFailed": "Gagal membuka koneksi terminal jarak jauh.",
};

export default auth;
