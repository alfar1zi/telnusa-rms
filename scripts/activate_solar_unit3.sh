#!/bin/sh
# activate_solar_unit3.sh
# Skrip terkontrol untuk menambahkan Unit ID 3 ke modbus_client & data_sender RUT906
# Harus dieksekusi dengan privilege root resmi pada RUT906 setelah bench test fisik sukses.

set -e

DATE=$(date +%Y%m%d_%H%M%S)

echo "=== 1. Backup Konfigurasi Aktif ==="
cp /etc/config/modbus_client "/etc/config/modbus_client.bak_${DATE}"
cp /etc/config/data_sender "/etc/config/data_sender.bak_${DATE}"
echo "Backup tersimpan di .bak_${DATE}"

echo "=== 2. Menerapkan Konfigurasi Unit ID 3 (modbus_client) ==="
uci set modbus_client.solar='rtu_server'
uci set modbus_client.solar.name='solar-radiation'
uci set modbus_client.solar.rtu_device='1'
uci set modbus_client.solar.frequency='period'
uci set modbus_client.solar.skip_on_many_tmos='0'
uci set modbus_client.solar.enabled='1'
uci set modbus_client.solar.period='20'
uci set modbus_client.solar.timeout='1'
uci set modbus_client.solar.server_id='3'

uci set modbus_client.req_solar='request_solar'
uci set modbus_client.req_solar.first_reg='0'
uci set modbus_client.req_solar.store_on_change_only='0'
uci set modbus_client.req_solar.no_brackets='1'
uci set modbus_client.req_solar.broadcast='0'
uci set modbus_client.req_solar.reg_count='1'
uci set modbus_client.req_solar.function='3'
uci set modbus_client.req_solar.name='solar_radiation'
uci set modbus_client.req_solar.enabled='1'
uci set modbus_client.req_solar.data_type='16bit_uint_hi_first'

uci commit modbus_client

echo "=== 3. Menerapkan Tag Acquisition pada data_sender ==="
# Menambahkan solar_radiation ke collection input data sender jika belum ada
uci add_list data_sender.5.input='solar_radiation' || true
uci commit data_sender

echo "=== 4. Reload Daemon ==="
/etc/init.d/modbus_client reload
/etc/init.d/data_sender reload

echo "Aktivasi Unit ID 3 selesai. Periksa logread dan /var/run/modbus_client/modbus.db."
