sudo dfu-util -e -d 1209:7d00

ECM_IFACE=$(ls -A /sys/bus/usb/drivers/cdc_ether/*/net/); ECM_IFACE=${ECM_IFACE%/}
sudo ip address add 2001:db8::1/64 dev "$ECM_IFACE"
ping -c3 fe80::2%"$ECM_IFACE"


APP_VER=$(date +%s) \
  PROGRAMMER=dfu-util DFU="sudo dfu-util" SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768 \
  SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/mldsa-keys SUIT_KEY=mldsa65 SUIT_KEY_ALGO=ml-dsa-65 \
  BOARD=nrf52840dongle \
  make -C examples/advanced/suit_update clean all riotboot/flash-slot0

picocom -b 115200 /dev/ttyACM0

APP_VER=$(date +%s)
SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/mldsa-keys \
  SUIT_KEY=mldsa65 SUIT_KEY_ALGO=ml-dsa-65 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768 \
  BOARD=nrf52840dongle APP_VER=$APP_VER SUIT_COAP_SERVER=[2001:db8::1] \
  make -C examples/advanced/suit_update suit/publish

python3 examples/advanced/suit_update/manifest-encryption-mlkem/encrypt_manifest.py \
  --key ~/masterthesis/RIOT/examples/advanced/suit_update/mldsa-keys/device_mlkem768.pem \
  -o coaproot/fw/suit_update/nrf52840dongle/riot.suit.enc \
  coaproot/fw/suit_update/nrf52840dongle/riot.suit.latest.bin

SUIT_NOTIFY_MANIFEST=riot.suit.enc \
  SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%$ECM_IFACE] \
  BOARD=nrf52840dongle make -C examples/advanced/suit_update suit/notify

suit fetch coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc
