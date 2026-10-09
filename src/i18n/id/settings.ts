import type { Dict } from "../types";

/**
 * Slice bahasa Indonesia untuk halaman Setelan: akun, sandi, manajemen
 * pengguna, tampilan, koneksi backend, dan fakta sistem.
 *
 * Nilai milik deployment (versi, tanggal build, id zona waktu, token status
 * layanan) tetap dirender sebagai data, bukan sebagai salinan.
 */
const settings: Dict = {
  "settings.title": "Setelan",
  "settings.subtitle": "Konfigurasi sistem, kontrol akses, dan status deployment",

  // Akun & Keamanan
  "settings.account.title": "Akun & Keamanan",
  "settings.account.signedIn": "Masuk sebagai",
  "settings.account.signedInTail": "({role}) melalui Autentikasi Lokal",
  "settings.account.changePassword": "Ubah Sandi Akun",
  "settings.account.currentPassword": "Sandi Saat Ini",
  "settings.account.newPassword": "Sandi Baru (min 8 karakter)",
  "settings.account.confirmPassword": "Konfirmasi Sandi Baru",
  "settings.account.updatePassword": "Perbarui Sandi",
  "settings.account.updating": "Memperbarui...",
  "settings.password.minLength": "Sandi baru minimal 8 karakter.",
  "settings.password.mismatch": "Sandi baru tidak cocok.",
  "settings.password.unchanged": "Sandi baru harus berbeda dari sandi saat ini.",
  "settings.password.changed": "Kata sandi diubah.",
  "settings.password.changeFailed": "Gagal mengubah sandi",

  // Manajemen Pengguna (admin)
  "settings.users.title": "Manajemen Pengguna",
  "settings.users.description": "Kelola peran, akses, dan kredensial pengguna",
  "settings.users.createTitle": "Buat Pengguna Baru",
  "settings.users.email": "Email",
  "settings.users.emailPlaceholder": "user@example.com",
  "settings.users.name": "Nama",
  "settings.users.namePlaceholder": "Nama Lengkap",
  "settings.users.tempPassword": "Sandi Sementara (min 8)",
  "settings.users.role": "Peran",
  "settings.users.add": "Tambah Pengguna",
  "settings.users.save": "Simpan Pengguna",
  "settings.users.saving": "Membuat...",
  "settings.users.minLength": "Sandi minimal 8 karakter.",
  "settings.users.created": "Pengguna {email} dibuat.",
  "settings.users.enabled": "Pengguna {email} diaktifkan.",
  "settings.users.disabled": "Pengguna {email} dinonaktifkan.",
  "settings.users.roleUpdated": "Peran untuk {email} diperbarui menjadi {role}.",
  "settings.users.passwordReset": "Sandi diatur ulang dan sesi dicabut untuk {email}.",
  "settings.users.sessionsRevoked": "Sesi untuk {email} dicabut.",
  "settings.users.loadFailed": "Gagal memuat daftar pengguna",
  "settings.users.createFailed": "Gagal membuat pengguna",
  "settings.users.statusFailed": "Gagal memperbarui status pengguna",
  "settings.users.roleFailed": "Gagal memperbarui peran pengguna",
  "settings.users.resetFailed": "Gagal mengatur ulang sandi",
  "settings.users.revokeFailed": "Gagal mencabut sesi",
  "settings.users.resetTitle": "Atur Ulang Sandi untuk {email}",
  "settings.users.resetPasswordLabel": "Sandi Sementara Baru (min 8 karakter)",
  "settings.users.applyReset": "Terapkan Reset Sandi",
  "settings.users.resetting": "Mengatur ulang...",
  "settings.users.tableStatus": "Status",
  "settings.users.mustChangePass": "Harus Ganti Sandi",
  "settings.users.tableActions": "Aksi",
  "settings.users.loading": "Memuat pengguna...",
  "settings.users.empty": "Tidak ada pengguna.",
  "settings.users.stateActive": "Aktif",
  "settings.users.stateDisabled": "Nonaktif",
  "settings.users.yes": "Ya",
  "settings.users.no": "Tidak",
  "settings.users.enable": "Aktifkan",
  "settings.users.disable": "Nonaktifkan",
  "settings.users.resetPass": "Reset Sandi",
  "settings.users.revoke": "Cabut",
  "settings.users.cancel": "Batal",

  // Tampilan
  "settings.appearance.title": "Tampilan",
  "settings.appearance.theme": "Tema",
  "settings.appearance.light": "Terang",
  "settings.appearance.dark": "Gelap",
  "settings.appearance.system": "Sistem",

  // Suara alarm
  "settings.alarmSound.title": "Suara Alarm",
  "settings.alarmSound.label": "Peringatan bersuara",
  "settings.alarmSound.description": "Memutar nada singkat untuk alarm tingkat tinggi dan disaster di browser ini.",
  "settings.alarmSound.on": "Aktif",
  "settings.alarmSound.off": "Nonaktif",
  "settings.alarmSound.test": "Uji",

  // Zona waktu
  "settings.timezone.title": "Zona Waktu",
  "settings.timezone.display": "Zona Waktu Tampilan",
  "settings.timezone.fixed": "Zona waktu tetap untuk deployment ini.",

  // Koneksi backend
  "settings.integrations.title": "Koneksi Backend",
  "settings.integrations.service": "Layanan",
  "settings.integrations.status": "Status",
  "settings.integrations.mqtt": "Kolektor MQTT",
  "settings.integrations.zabbixApi": "Zabbix API",
  "settings.integrations.zabbixSender": "Zabbix Sender",
  "settings.integrations.sse": "SSE",

  // Sistem
  "settings.system.title": "Sistem",
  "settings.system.version": "Versi NMS",
  "settings.system.buildDate": "Tanggal Build",
  "settings.system.deployment": "Deployment",
};

export default settings;
