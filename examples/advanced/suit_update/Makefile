# name of your application
APPLICATION = suit_update

# If no BOARD is found in the environment, use this default:
BOARD ?= samr21-xpro

# This has to be the absolute path to the RIOT base directory:
RIOTBASE ?= $(CURDIR)/../../..

#
# Networking
#
# Include packages that pull up and auto-init the link layer.
# NOTE: 6LoWPAN will be included if IEEE802.15.4 devices are present

USEMODULE += auto_init_gnrc_netif
# Specify the mandatory networking modules for IPv6 and UDP
USEMODULE += gnrc_ipv6_default
USEMODULE += sock_udp
# Additional networking modules that can be dropped if not needed
USEMODULE += gnrc_icmpv6_echo

# include this for printing IP addresses
USEMODULE += shell
USEMODULE += shell_cmds_default
USEMODULE += shell_cmd_suit

# Set this to 1 to enable code in RIOT that does safety checking
# which is not needed in a production environment but helps in the
# development process:
DEVELHELP ?= 0

# Change this to 0 show compiler invocation lines by default:
QUIET ?= 1

#
# SUIT update specific stuff
#

USEMODULE += suit suit_transport_coap
USEMODULE += nanocoap_server_auto_init

# SUIT_KEY_ALGO=ml-dsa-44/-65/-87 selects on-device ML-DSA verification for
# that FIPS 204 parameter set (must be added to USEMODULE before dependency
# resolution runs in Makefile.include, so this can't live in the
# later-included makefiles/suit.base.inc.mk).
ifneq (,$(filter ml-dsa-%,$(SUIT_KEY_ALGO)))
  USEMODULE += suit_algo_mldsa$(subst ml-dsa-,,$(SUIT_KEY_ALGO))
  # sys/suit/transport/worker.c's manifest fetch buffer defaults to 640B,
  # sized for an Ed25519 manifest (64B signature). An ML-DSA manifest embeds
  # a much larger signature (44: 2420B, 65: 3309B, 87: 4627B), pushing the
  # whole CBOR envelope past the buffer - bump it or suit_handle_url() fails
  # with "error getting manifest" (nanocoap_get_blockwise_url_to_buf()/
  # vfs_file_to_buffer() both reject anything that doesn't fit). Sized per
  # level: signature + ~530B envelope overhead, rounded up, while saving
  # RAM over rounder values on 32KB-RAM boards.
  ifeq (ml-dsa-44,$(SUIT_KEY_ALGO))
    SUIT_MANIFEST_BUFSIZE = 3072
  else ifeq (ml-dsa-87,$(SUIT_KEY_ALGO))
    SUIT_MANIFEST_BUFSIZE = 5376
  else
    SUIT_MANIFEST_BUFSIZE = 3840
  endif
  # The ML-DSA verify work state lives in a static MlDsaKey (see
  # pkg/libcose's wolfcrypt_mldsa.c backend), not on the worker stack, so
  # the default 6KB stack suffices for the verify itself; shrink it to 4KB
  # to fit the ~10KB (ML-DSA-65) static key state within a 32KB RAM budget.
  # Constrained boards only: native's x86-64 stack frames overflow 4KB in
  # the deeper streaming-payload-decrypt call chain (worker → parse →
  # fetch → transport callback → ML-KEM decaps), silently corrupting
  # adjacent .bss — the AEAD state, seen as nondeterministic Poly1305 tags.
  # (Emitted once, after SUIT_MANIFEST_ENCRYPT_ALGO below has had a chance
  # to raise this further for ML-KEM - see there for why.)
  SUIT_WORKER_STACKSIZE ?= 4096
else
  SUIT_MANIFEST_BUFSIZE = 640
endif

# Manifest confidentiality: on by default, opt out with
# SUIT_MANIFEST_ENCRYPT=0 (module selection must happen here, before
# dependency resolution; key handling lives in makefiles/suit.base.inc.mk,
# workflow in MANIFEST_ENCRYPTION_PLAN.md). SUIT_MANIFEST_ENCRYPT_ALGO
# picks the key-establishment scheme: x25519 (default, classical) or
# ml-kem-768 / ml-kem-1024 (post-quantum, MLKEM_ENCRYPTION_PLAN.md). The
# COSE_Encrypt wrapper grows the fetch buffer accordingly: ~130B for
# X25519 (32B ephemeral key + nonce + tag + framing), ~1.2/1.7KB for
# ML-KEM (the encapsulation ciphertext dominates).
SUIT_MANIFEST_ENCRYPT ?= 1
SUIT_MANIFEST_ENCRYPT_ALGO ?= x25519
ifeq (1,$(SUIT_MANIFEST_ENCRYPT))
  USEMODULE += suit_manifest_encrypt
  ifneq (,$(filter ml-kem-%,$(SUIT_MANIFEST_ENCRYPT_ALGO)))
    # wc_MlKemKey_Decapsulate()'s call chain (mlkemkey_decapsulate ->
    # mlkemkey_encapsulate, invoked internally for the FIPS 203
    # implicit-rejection check) peaks at ~8.5-9KB of stack (measured via
    # -fstack-usage: 1232+2072+4664+~600B nested), even after enabling
    # every wolfCrypt small-memory knob (WOLFSSL_NO_MALLOC +
    # MAKEKEY/ENCAPSULATE_SMALL_MEM in user_settings.h - without those,
    # it's worse: an unconditional ~6-10KB heap XMALLOC, invisible to
    # link-time RAM analysis, that fails outright on any newlib heap this
    # small - found as runtime "CEK derivation failed: -125" on real
    # samr21-xpro hardware). The pre-existing 4KB ML-DSA worker stack
    # (below) silently corrupts adjacent .bss on overflow - no MPU on
    # Cortex-M0+ - so force an explicit, measured-adequate size here
    # rather than let an ml-kem-* build "successfully" produce a
    # time-bomb image. This overflows the RAM budget outright on
    # anything but the most spacious combo, turning the danger into a
    # loud, honest link error instead: see FIRMWARE_ENCRYPTION_CHANGES.md
    # / MLKEM_ENCRYPTION_CHANGES.md for which combos (if any) still fit.
    # Applies regardless of SUIT_KEY_ALGO - an Ed25519 signing build with
    # ML-KEM manifest encryption needs this override just as much as an
    # ML-DSA one; without it, Ed25519 builds silently keep worker.c's
    # un-sized-for-this default (3 * THREAD_STACKSIZE_LARGE, ~6KB) instead.
    SUIT_WORKER_STACKSIZE := 9216
    USEMODULE += wolfcrypt_mlkem
    USEMODULE += wolfcrypt_mlkem$(subst ml-kem-,,$(SUIT_MANIFEST_ENCRYPT_ALGO))
    ifeq (ml-dsa-44/ml-kem-768,$(SUIT_KEY_ALGO)/$(SUIT_MANIFEST_ENCRYPT_ALGO))
      # The full-PQ combo is squeezed into samr21-xpro's 32KB RAM (together
      # with the suit_pq_scratch union, see sys/include/suit/pq_scratch.h).
      # The buffer must hold the encrypted manifest AS FETCHED (decryption
      # happens in place after the transfer completes) - undersizing it
      # doesn't fail loudly with a crypto error, it fails earlier with
      # "suit_worker: error getting manifest" from
      # nanocoap_get_blockwise_url_to_buf() refusing to exceed the buffer.
      # 3904B (2632B assumed ML-DSA-44 manifest + 1144B COSE_Encrypt/KEM
      # overhead + 128B margin) was measured against a short test path and
      # undershot: real `suit/publish` URIs are longer
      # (SUIT_COAP_BASEPATH=fw/$(APPLICATION)/$(BOARD), doubled across two
      # slot components), producing a 2853B signed manifest -> 3997B
      # encrypted - 93B over budget. Sized here from the real number with
      # a wider margin for URI/hostname variation across setups.
      SUIT_MANIFEST_BUFSIZE := 4160
      # Leftover RAM after static allocation IS the newlib heap, and stdio
      # needs ~1KB of it: at 964B free (this combo + firmware encryption,
      # original 3904B manifest buffer) the board booted with a MUTE SHELL
      # - network up, prompt echoing, every printf silently empty (no boot
      # banner). Reclaim from pktbuf: SUIT's 64B CoAP blocks don't need
      # 2KB; the trade-off is that frames >~1.15KB (e.g. full-MTU ethos
      # pings) no longer fit. The cut must track SUIT_MANIFEST_BUFSIZE
      # above it (both eat the same static RAM budget) - 592B for the
      # original buffer size verified restoring a working shell; the wider
      # buffer needs an extra 256B trimmed off pktbuf to keep the same
      # heap margin. Set as both make var (skips the generic 2000B default
      # below) and define.
      CONFIG_GNRC_PKTBUF_SIZE = 1152
      CFLAGS += -DCONFIG_GNRC_PKTBUF_SIZE=1152
    else ifeq (ml-kem-1024,$(SUIT_MANIFEST_ENCRYPT_ALGO))
      SUIT_MANIFEST_BUFSIZE := $(shell expr $(SUIT_MANIFEST_BUFSIZE) + 1696)
    else
      SUIT_MANIFEST_BUFSIZE := $(shell expr $(SUIT_MANIFEST_BUFSIZE) + 1216)
    endif
  else
    SUIT_MANIFEST_BUFSIZE := $(shell expr $(SUIT_MANIFEST_BUFSIZE) + 128)
  endif
endif

ifneq (640,$(SUIT_MANIFEST_BUFSIZE))
  CFLAGS += -DSUIT_MANIFEST_BUFSIZE=$(SUIT_MANIFEST_BUFSIZE)
endif

# Emitted once both the ML-DSA (SUIT_WORKER_STACKSIZE ?= 4096) and ML-KEM
# (SUIT_WORKER_STACKSIZE := 9216, wins if both apply) blocks above have had
# their say; unset (pure Ed25519 + X25519/off) leaves worker.c's own
# built-in default untouched. Non-native only - see the ML-DSA block for
# why native needs the opposite treatment (its default is too *small*
# there, not too large).
ifneq (,$(SUIT_WORKER_STACKSIZE))
  ifeq (,$(filter native native32 native64,$(BOARD)))
    CFLAGS += -DSUIT_WORKER_STACKSIZE=$(SUIT_WORKER_STACKSIZE)
  endif
endif

# Firmware-payload confidentiality: on by default, opt out with
# SUIT_FIRMWARE_ENCRYPT=0. Streams the detached-ciphertext COSE_Encrypt
# payload container through an in-place ChaCha20-Poly1305 decrypt during
# the fetch (FIRMWARE_ENCRYPTION_PLAN.md); shares the device key and
# recipient scheme (x25519 / ml-kem-*) with manifest encryption, which it
# therefore implies (sys/suit/Makefile.dep). Plaintext payloads still pass
# through. Host side: encrypt payloads with
# firmware-encryption/encrypt_firmware.py and point the manifest URI at
# the .enc via gen_manifest.py --enc-suffix (manual, like the manifest
# encrypt-after-sign step). No SUIT_MANIFEST_BUFSIZE impact: the payload
# streams past the manifest buffer; the decryptor keeps its own small
# static header buffer (192 B x25519, 1216/1696 B ml-kem).
SUIT_FIRMWARE_ENCRYPT ?= 1
ifeq (1,$(SUIT_FIRMWARE_ENCRYPT))
  USEMODULE += suit_firmware_encrypt
endif

# enable VFS transport (only works on boards with external storage)
USEMODULE += suit_transport_vfs
USEMODULE += vfs_default

# Display a progress bar during firmware download. Set PROGRESS_BAR=0 to drop
# it: it reprints on every received block, which is the single largest source
# of console traffic during an update. On the nRF52840 dongle that traffic goes
# through USB CDC-ACM, where a stalled (attached but not draining) host makes
# printf() block indefinitely -- see the buffer-size comment in the dongle
# block above. Turning it off makes an OTA independent of the console.
PROGRESS_BAR ?= 1
ifeq (1,$(PROGRESS_BAR))
  USEMODULE += progress_bar
endif

# Optional feature to trigger suit update through gpio callback
FEATURES_OPTIONAL += periph_gpio_irq

# Default COAP manifest resource location when fetched through gpio trigger
CFLAGS += -DSUIT_MANIFEST_RESOURCE=\"$(SUIT_COAP_ROOT)/$(SUIT_NOTIFY_MANIFEST)\"

# Enable test_utils_interactive_sync, only used when running automatic test
DEFAULT_MODULE += test_utils_interactive_sync

ifneq (,$(filter native native32 native64,$(BOARD)))
  USE_ETHOS ?= 0
  IFACE ?= tapbr0
  # Configure two RAM regions with 2K each
  CFLAGS += -DCONFIG_SUIT_STORAGE_RAM_REGIONS=2 -DCONFIG_SUIT_STORAGE_RAM_SIZE=2048
endif

# The nRF52840 dongle has no UART-to-USB bridge; its only host link is the
# nRF52840's native USB. ethos rides on a hardware UART, so an ethos build
# enumerates nothing on the dongle's USB. Network it over USB CDC-ECM
# (USB Ethernet) with the shell on CDC-ACM instead -- or, with
# DONGLE_NETIF=radio, over the nRF52840's own IEEE 802.15.4 radio behind a
# 6LoWPAN border router, keeping only the shell on USB (DEVICE_802154_PI.md).
ifeq (nrf52840dongle,$(BOARD))
  USE_ETHOS ?= 0
  DONGLE_NETIF ?= cdc-ecm
  ifeq (,$(filter cdc-ecm radio,$(DONGLE_NETIF)))
    $(error DONGLE_NETIF must be `cdc-ecm` or `radio`, got `$(DONGLE_NETIF)`)
  endif

  # Everything in this block is required whatever the dongle is networked
  # over, which is why it lives here rather than in the CDC-ECM arm of the
  # USE_ETHOS selector below.

  # USB is the dongle's only console: with no UART-to-USB bridge, a build
  # without stdio_cdc_acm enumerates and runs but prints nothing at all --
  # network up, prompt echoing, every printf silently empty. A DONGLE_NETIF=
  # radio build still needs it (and needs USB plugged in) even though its
  # update traffic never touches USB.
  USEMODULE += auto_init_usbus
  USEMODULE += stdio_cdc_acm

  # Enlarge the CDC-ACM stdio ring buffer well past its 128B default
  # (sys/include/usb/usbus/cdc/acm.h). This is not just about dropped
  # characters: sys/usb/usbus/cdc/acm/cdc_acm_stdio.c's _write() loops
  # `while (len) { n = usbus_cdc_acm_submit(...); len -= n; }`, and submit()
  # returns 0 once the ring buffer is full unless the line state is
  # DISCONNECTED -- so a host that is attached but not draining (a dead
  # terminal that never dropped DTR, say) makes printf() spin forever and
  # hangs the device mid-update. A single `ifconfig` already emits ~600B, and
  # progress_bar prints on every downloaded block. 1KB costs nothing on this
  # board's 256KB and keeps bursty output from wedging an OTA.
  CFLAGS += -DCONFIG_USBUS_CDC_ACM_STDIO_BUF_SIZE=1024

  # The dongle's stock Nordic USB DFU bootloader cannot install riotboot (it
  # protects the MBR at 0x0, where plain riotboot links), so SUIT OTA is flashed
  # via RIOT's own riotboot_dfu bootloader instead (see
  # HARDWARE_NRF52840_DONGLE_WSL.md). usbus_dfu is required on the app side for
  # two reasons: it bumps RIOTBOOT_LEN so the slots line up with riotboot_dfu
  # (SLOT0_OFFSET 0x4000, not the plain-riotboot 0x2000), and it gives the
  # running firmware a DFU-runtime interface so dfu-util can re-flash a slot
  # without re-entering the Nordic bootloader. Making it unconditional here keeps
  # every build/publish slot-aligned (forgetting it on suit/publish would
  # silently produce images the bootloader can't boot).
  USEMODULE += usbus_dfu

  # Pin the Nordic-bootloader flash geometry regardless of PROGRAMMER. The board
  # file only sets ROM_OFFSET=0x1000 (clear the MBR) and ROM_LEN=0xdf000
  # (preserve the top 128K Open bootloader) inside its `ifeq (nrfutil,PROGRAMMER)`
  # block. But riotboot_dfu keeps the Nordic bootloader in place, and its slots
  # are flashed with PROGRAMMER=dfu-util -- which would otherwise fall back to the
  # CPU default ROM_LEN=0x100000 (full 1MB) and compute SLOT1_OFFSET=0x82000,
  # while the default-PROGRAMMER `suit/publish` uses 0xdf000 -> SLOT1_OFFSET=
  # 0x71800. The mismatch makes a fetched update fail the slot-offset condition
  # (`offset does not match`, suit_parse res=-4). Pinning here forces publish and
  # dfu-util flash to agree (and stops slot 1 overrunning the Open bootloader).
  ROM_OFFSET ?= 0x1000
  ROM_LEN ?= 0xdf000
endif

# Change this to 0 to not use ethos
USE_ETHOS ?= 1

ifeq (1,$(USE_ETHOS))
  USEMODULE += stdio_ethos
  USEMODULE += gnrc_uhcpc

  # ethos baudrate can be configured from make command
  ETHOS_BAUDRATE ?= 115200
  CFLAGS += -DETHOS_BAUDRATE=$(ETHOS_BAUDRATE)

  # make sure ethos and uhcpd are built
  TERMDEPS += host-tools

  # For local testing, run
  #
  #     $ cd dist/tools/ethos; sudo ./setup_network.sh riot0 2001:db8::0/64
  #
  #... in another shell and keep it running.
  IFACE ?= riot0
  TERMPROG = $(RIOTTOOLS)/ethos/ethos
  TERMFLAGS = $(IFACE) $(PORT)
else ifeq (nrf52840dongle/cdc-ecm,$(BOARD)/$(DONGLE_NETIF))
  # USB CDC-ECM (USB Ethernet) networking over the dongle's native USB, with
  # the shell on CDC-ACM. The host side is driven by usb-cdc-ecm/start_network.sh
  # (host fe80::1, device fe80::2, uhcpd pushes the prefix) instead of ethos.
  # (auto_init_usbus / stdio_cdc_acm / usbus_dfu / ROM geometry are selected
  # unconditionally in the nrf52840dongle block further up -- they are not
  # CDC-ECM-specific.)
  USEMODULE += usbus_cdc_ecm
  USEMODULE += gnrc_uhcpc

  # IPv6 prefix delegated to the device over the CDC-ECM link
  IPV6_PREFIX ?= 2001:db8::/64
  TERMDEPS += host-tools
  TERMPROG ?= sudo sh $(RIOTTOOLS)/usb-cdc-ecm/start_network.sh
  TERMFLAGS ?= $(IPV6_PREFIX)
else
  # Wireless node behind a 6LoWPAN border router: the board's own radio
  # (samr21-xpro's at86rf233, the dongle's nrf802154 with DONGLE_NETIF=radio).
  # No gnrc_uhcpc here on purpose -- a node gets its prefix from the border
  # router's router advertisements, not over UHCP. See DEVICE_802154_PI.md.
  USEMODULE += netdev_default
endif

# Ensure both slot bin files are always generated and linked to avoid compiling
# during the test. This ensures that "BUILD_IN_DOCKER=1 make test"
# can rely on them being present without having to trigger re-compilation.
BUILD_FILES += $(SLOT_RIOT_ELFS:%.elf=%.bin)

# The test needs the linked slot binaries without header in order to be able to
# create final binaries with specific APP_VER values. The CI RasPi test workers
# don't compile themselves and re-create signed images, thus add the required
# files here so they will be submitted along with the test jobs.
TEST_EXTRA_FILES += $(SLOT_RIOT_ELFS) $(SUIT_SEC) $(SUIT_PUB)
# For now `examples/%/tests" still rely on the test applicaton being reset after
# a terminal is opened to synchronize.
TESTRUNNER_RESET_AFTER_TERM ?= 1

# This can be removed as soon as the Pi-fleet runners support an Openssl version
# with ed25519 support.
TEST_ON_CI_BLACKLIST = all

# Include global Makefile.include first to apply board aliasses
include $(RIOTBASE)/Makefile.include

# Add custom SUIT targets
include $(CURDIR)/Makefile.suit.custom
include $(CURDIR)/Makefile.thesis.suit.manifest

# export IFACE for test
$(call target-export-variables,test-with-config test-with-config/check-config,IFACE)
# export BOARD
$(call target-export-variables,test-with-config test-with-config/check-config,BOARD)

# allow to use large blocks to utilize large MTUs (802.15.4g, Ethernet, WiFi)
LARGE_BLOCKS ?= 0
ifeq (1, $(LARGE_BLOCKS))
  CFLAGS += -DCONFIG_NANOCOAP_BLOCKSIZE_DEFAULT=COAP_BLOCKSIZE_1024
else
# lower pktbuf size to something sufficient for this application
# Set GNRC_PKTBUF_SIZE via CFLAGS if not being set via Kconfig.
ifndef CONFIG_GNRC_PKTBUF_SIZE
  CFLAGS += -DCONFIG_GNRC_PKTBUF_SIZE=2000
endif
endif

.PHONY: host-tools

host-tools:
	$(Q)env -u CC -u CFLAGS $(MAKE) -C $(RIOTTOOLS)

include $(RIOTMAKE)/default-radio-settings.inc.mk
