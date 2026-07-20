#
# This file contains stuff related to SUIT manifest generation.
# It depends on SUIT key generation, which can be found in
# makefiles/suit.base.inc.mk
#
#

# Mandatory APP_VER, set to epoch by default
EPOCH = $(call memoized,EPOCH,$(shell date +%s))
APP_VER ?= $(EPOCH)

SUIT_VENDOR ?= "riot-os.org"
SUIT_SEQNR ?= $(APP_VER)
SUIT_CLASS ?= $(BOARD)

SUIT_COAP_BASEPATH ?= fw/$(APPLICATION)/$(BOARD)
SUIT_COAP_ROOT ?= coap://$(SUIT_COAP_SERVER)/$(SUIT_COAP_BASEPATH)
SUIT_COAP_FSROOT ?= $(RIOTBASE)/coaproot

BINDIR_SUIT = $(BINDIR)/suit_files
$(BINDIR_SUIT): $(CLEAN)
	$(Q)mkdir -p $(BINDIR_SUIT)

#
SUIT_MANIFEST_BASENAME ?= riot.suit
SUIT_MANIFEST ?= $(BINDIR_SUIT)/$(SUIT_MANIFEST_BASENAME)_unsigned.$(SUIT_SEQNR).bin
SUIT_MANIFEST_LATEST ?= $(BINDIR_SUIT)/$(SUIT_MANIFEST_BASENAME)_unsigned.latest.bin
SUIT_MANIFEST_SIGNED ?= $(BINDIR_SUIT)/$(SUIT_MANIFEST_BASENAME).$(SUIT_SEQNR).bin
SUIT_MANIFEST_SIGNED_LATEST ?= $(BINDIR_SUIT)/$(SUIT_MANIFEST_BASENAME).latest.bin

SUIT_NOTIFY_VERSION ?= latest
SUIT_NOTIFY_MANIFEST ?= $(SUIT_MANIFEST_BASENAME).$(SUIT_NOTIFY_VERSION).bin

# Long manifest names require more buffer space when parsing
export CFLAGS += -DCONFIG_SOCK_URLPATH_MAXLEN=128
export CFLAGS += -DSUIT_VENDOR_DOMAIN="\"$(SUIT_VENDOR)\""
export CFLAGS += -DSUIT_CLASS_ID="\"$(SUIT_CLASS)\""

SUIT_MANIFEST_PAYLOADS ?= $(SLOT0_RIOT_BIN) $(SLOT1_RIOT_BIN)
SUIT_MANIFEST_SLOTFILES ?= $(SLOT0_RIOT_BIN):$(SLOT0_OFFSET) \
                           $(SLOT1_RIOT_BIN):$(SLOT1_OFFSET)

# Firmware-payload encryption (FIRMWARE_ENCRYPTION_PLAN.md): when the
# firmware was built with the (default-on) suit_firmware_encrypt module,
# publish ChaCha20-Poly1305-encrypted payloads instead of the plaintext
# slot binaries. The manifest's image-digest/size stay computed over the
# plaintext (the device stores decrypted bytes); only the URI gets the
# .enc suffix. SUIT_ENC_SEC (the device key) comes from suit.base.inc.mk.
ifeq (1,$(SUIT_FIRMWARE_ENCRYPT))
  ifneq (,$(SUIT_ENC_SEC))
    SUIT_FW_ENCRYPT_TOOL ?= $(RIOTBASE)/examples/advanced/suit_update/firmware-encryption/encrypt_firmware.py
    SUIT_ENC_SUFFIX_ARGS = --enc-suffix .enc
    SUIT_PUBLISH_PAYLOADS = $(addsuffix .enc,$(SUIT_MANIFEST_PAYLOADS))

%.enc: % $(SUIT_ENC_SEC)
	$(Q)python3 $(SUIT_FW_ENCRYPT_TOOL) --no-headers \
	  --key $(SUIT_ENC_SEC) -o $@ $<
  endif
endif
SUIT_PUBLISH_PAYLOADS ?= $(SUIT_MANIFEST_PAYLOADS)

$(SUIT_MANIFEST): $(SUIT_MANIFEST_PAYLOADS) $(BINDIR_SUIT)
	$(Q)$(RIOTBASE)/dist/tools/suit/gen_manifest.py \
	  --urlroot $(SUIT_COAP_ROOT) \
	  --seqnr $(SUIT_SEQNR) \
	  --uuid-vendor $(SUIT_VENDOR) \
	  --uuid-class $(SUIT_CLASS) \
	  $(SUIT_ENC_SUFFIX_ARGS) \
	  -o $@.tmp \
	  $(SUIT_MANIFEST_SLOTFILES)

	$(Q)$(SUIT_TOOL) create -f suit -i $@.tmp -o $@

	$(Q)rm -f $@.tmp

$(SUIT_MANIFEST_SIGNED): $(SUIT_MANIFEST) $(SUIT_SEC)
	$(Q)(											\
	if grep -q ENCRYPTED $(SUIT_SEC_SIGN); then						\
		if [ -z "$(SUIT_SEC_PASSWORD)" ]; then						\
			printf "Enter encryption for key file $(SUIT_SEC_SIGN): ";		\
			read PASSWORD;								\
		else										\
			PASSWORD="$(SUIT_SEC_PASSWORD)";					\
		fi;										\
		$(SUIT_TOOL) sign -p "$$PASSWORD" -k $(SUIT_SEC_SIGN) -m $(SUIT_MANIFEST) -o $@;\
	else											\
		$(SUIT_TOOL) sign -k $(SUIT_SEC_SIGN) -m $(SUIT_MANIFEST) -o $@;		\
	fi											\
	)

$(SUIT_MANIFEST_LATEST): $(SUIT_MANIFEST)
	$(Q)ln -f -s $< $@

$(SUIT_MANIFEST_SIGNED_LATEST): $(SUIT_MANIFEST_SIGNED)
	$(Q)ln -f -s $< $@

SUIT_MANIFESTS := $(SUIT_MANIFEST_SIGNED) \
                  $(SUIT_MANIFEST_SIGNED_LATEST)

suit/manifest: $(SUIT_MANIFESTS)

suit/publish: $(SUIT_MANIFESTS) $(SUIT_PUBLISH_PAYLOADS)
	$(Q)mkdir -p $(SUIT_COAP_FSROOT)/$(SUIT_COAP_BASEPATH)
	$(Q)cp $^ $(SUIT_COAP_FSROOT)/$(SUIT_COAP_BASEPATH)
	$(Q)for file in $^; do \
		echo "published \"$$file\""; \
		echo "       as \"$(SUIT_COAP_ROOT)/$$(basename $$file)\""; \
	done

suit/notify: | $(filter suit/publish, $(MAKECMDGOALS))
	$(Q)test -n "$(SUIT_CLIENT)" || { echo "error: SUIT_CLIENT unset!"; false; }
	aiocoap-client -m POST "coap://$(SUIT_CLIENT)/suit/trigger" \
		--payload "$(SUIT_COAP_ROOT)/$(SUIT_NOTIFY_MANIFEST)" && \
		echo "Triggered $(SUIT_CLIENT) to update."
