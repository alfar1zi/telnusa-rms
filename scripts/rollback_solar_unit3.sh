#!/bin/sh
# rollback_solar_unit3.sh
# Skrip rollback ke baseline dua perangkat (PZEM Unit ID 1 & MPPT Unit ID 2)
# Menghapus konfigurasi solar dari modbus_client & data_sender RUT906

set -e

echo "=== 1. Rollback modbus_client ==="
# Hapus section solar jika ada
uci delete modbus_client.solar 2>/dev/null || true
uci delete modbus_client.req_solar 2>/dev/null || true
uci commit modbus_client

echo "=== 2. Rollback data_sender ==="
uci del_list data_sender.5.input='solar_radiation' 2>/dev/null || true
uci commit data_sender

echo "=== 3. Reload Daemon ==="
/etc/init.d/modbus_client reload
/etc/init.d/data_sender reload

echo "Rollback ke baseline 2 perangkat selesai. Verifikasi PZEM dan MPPT."
