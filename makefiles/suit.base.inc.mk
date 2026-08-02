#
# path to suit-tool
SUIT_TOOL ?= $(RIOTBASE)/dist/tools/suit/suit-manifest-generator/bin/suit-tool

#
# SUIT encryption keys
#

# Specify key(s) to use.
# Will use $(SUIT_KEY_DIR)/$(SUIT_KEY).pem as combined private/public key
# files.
# Multiple keys can be specified, that means that the firmware will accept
# updates signed with either one of those keys.
# If the firmware accepts multiple keys, let the first key be the signing key.
SUIT_KEY ?= default
SUIT_KEY_SIGN ?= $(word 1, $(SUIT_KEY))
# Signature algorithm, e.g. ed25519 (default), es256 / es384 / es512 for
# ECDSA over the NIST curves, or ml-dsa-44 / ml-dsa-65 / ml-dsa-87 for
# post-quantum ML-DSA signatures (requires OpenSSL 3.5+). All keys in
# SUIT_KEY must use the same algorithm.
SUIT_KEY_ALGO ?= ed25519

# ECDSA is one OpenSSL algorithm ("ec") parameterized by a curve, unlike
# ed25519/ml-dsa-XX where the algorithm name is the whole story - so the name
# passed to `openssl genpkey -algorithm ...` is derived rather than being
# SUIT_KEY_ALGO verbatim.
SUIT_KEY_EC_CURVE = $(strip \
    $(if $(filter es256,$(SUIT_KEY_ALGO)),P-256) \
    $(if $(filter es384,$(SUIT_KEY_ALGO)),P-384) \
    $(if $(filter es512,$(SUIT_KEY_ALGO)),P-521))
SUIT_KEY_GENPKEY_ALGO ?= $(if $(SUIT_KEY_EC_CURVE),ec,$(SUIT_KEY_ALGO))

# OpenSSL 3.5+ defaults to writing ML-DSA private keys with both the FIPS 204
# seed *and* the expanded secret key in a combined ASN.1 CHOICE ("seed-priv"
# format). Python's `cryptography` (used by suit-tool) can't currently parse
# that combined form - force seed-only output, which it does support. This
# provparam is silently ignored for non-ML-DSA algorithms.
# For ECDSA, `-algorithm ec` needs the curve naming which parameter set to use;
# note ES512 uses P-521 (a 521-bit curve), not a "P-512" that doesn't exist.
SUIT_KEY_GENPKEY_ARGS ?= \
    $(if $(filter ml-dsa-%,$(SUIT_KEY_ALGO)),-provparam ml-dsa.output_formats=seed-only) \
    $(if $(SUIT_KEY_EC_CURVE),-pkeyopt ec_paramgen_curve:$(SUIT_KEY_EC_CURVE))

# ML-DSA needs on-device verification support (sys/suit's
# suit_algo_mldsa44/65/87 modules, wired to a wolfCrypt-backed libcose
# backend - the app Makefile must add `USEMODULE += suit_algo_mldsaXX`
# itself when SUIT_KEY_ALGO=ml-dsa-XX, since module selection here runs too
# late, after dependency resolution). The backend sources wolfCrypt's
# ML-DSA code from a local, pre-configured wolfSSL checkout (built with
# --enable-dilithium, which covers all three parameter sets) rather than
# RIOT's own pinned wolfssl pkg fetch, which predates wolfSSL's ML-DSA
# support - see examples/advanced/suit_update/CLAUDE.md for details/caveats.
# The local checkout is also needed for ML-KEM manifest encryption (the
# pinned wolfssl pkg predates wolfSSL's ML-KEM support too).
ifneq (,$(filter ml-dsa-%,$(SUIT_KEY_ALGO))$(filter ml-kem-%,$(SUIT_MANIFEST_ENCRYPT_ALGO)))
  export PKG_SOURCE_LOCAL_WOLFSSL ?= $(RIOTBASE)/dist/tools/suit/ml-dsa-example/wolfssl
endif

XDG_DATA_HOME ?= $(HOME)/.local/share

ifeq (1, $(RIOT_CI_BUILD))
  SUIT_KEY_DIR ?= $(BINDIR)
else
  SUIT_KEY_DIR ?= $(XDG_DATA_HOME)/RIOT/keys
endif

# we may accept multiple keys for the firmware
SUIT_SEC ?= $(foreach item,$(SUIT_KEY),$(SUIT_KEY_DIR)/$(item).pem)
# but there can only be one signing key
SUIT_SEC_SIGN ?= $(SUIT_KEY_DIR)/$(SUIT_KEY_SIGN).pem

# generate a list of the public keys
SUIT_PUBS ?= $(SUIT_SEC:.pem=.pem.pub)

SUIT_PUB_HDR = $(BINDIR)/riotbuild/public_key.h
SUIT_PUB_HDR_DIR = $(dir $(SUIT_PUB_HDR))
CFLAGS += -I$(SUIT_PUB_HDR_DIR)
BUILDDEPS += $(SUIT_PUB_HDR)

# OpenSSL leaves an empty file if key generation fails - remove it manually
# see https://github.com/openssl/openssl/issues/25440
$(SUIT_SEC): | $(CLEAN)
	$(Q)echo suit: generating key in $(SUIT_KEY_DIR)
	$(Q)mkdir -p $(SUIT_KEY_DIR)
	$(Q)(										\
	printf "0) none\n";								\
	printf "1) aes-256-cbc\n";							\
	printf "Choose encryption for key file $@: ";					\
	if [ -z "$(RIOT_CI_BUILD)" ]; then read encryption; else encryption=0; fi;	\
	case $$encryption in								\
		0)									\
			openssl genpkey -algorithm $(SUIT_KEY_GENPKEY_ALGO) $(SUIT_KEY_GENPKEY_ARGS) -out $@;		\
			;;								\
		1)									\
			openssl genpkey -algorithm $(SUIT_KEY_GENPKEY_ALGO) $(SUIT_KEY_GENPKEY_ARGS) -aes-256-cbc -out $@ || :;	\
			;;								\
		*)									\
			echo "Invalid choice";						\
			exit 1;								\
			;;								\
	esac;										\
	)
	$(Q)if [ ! -s $@ ]; then rm $@; fi

# Allow to disable auto-generating public key from private key, e.g. if only the
# public key is available at build time and manifest is created elsewhere.
SUIT_GEN_PUBKEY ?= 1
ifeq (1, $(SUIT_GEN_PUBKEY))
  _PUB_FROM_SEC := %.pem
endif
%.pem.pub: $(_PUB_FROM_SEC)
	$(Q)openssl pkey -inform pem -in $< -outform pem -pubout -out $@

# Convert public keys to C headers, using the raw key material regardless of
# algorithm (Ed25519, ML-DSA, ...). All SUIT_KEY entries must share the same
# algorithm/key size.
#
# set FORCE so switching between keys using "SUIT_KEY=foo make ..."
# triggers a rebuild even if the new key would otherwise not (because the other
# key's mtime is too far back).
$(SUIT_PUB_HDR): $(SUIT_PUBS) FORCE | $(CLEAN)
	$(Q)mkdir -p $(SUIT_PUB_HDR_DIR)
	$(Q)$(RIOTBASE)/dist/tools/suit/pubkey_to_header.py $(SUIT_PUBS) | \
		'$(LAZYSPONGE)' $(LAZYSPONGE_FLAGS) '$@'

suit/genkey: $(SUIT_SEC)

#
# SUIT manifest encryption (confidentiality) - see
# examples/advanced/suit_update/MANIFEST_ENCRYPTION_PLAN.md.
#
# On by default; opt out with SUIT_MANIFEST_ENCRYPT=0. Like the ML-DSA
# modules, the suit_manifest_encrypt module itself must be selected in the
# app Makefile (this file is included after dependency resolution). Here we
# manage the device's static X25519 keypair and embed its private key into
# the firmware via a generated header (prototype-grade key storage: the key
# ends up in the image).
export SUIT_MANIFEST_ENCRYPT ?= 1
# Firmware-payload confidentiality (suit_firmware_encrypt module, selected
# by the app Makefile like the modules above): reuses the same device key
# (SUIT_ENC_SEC) and recipient scheme, so no extra key handling here. The
# host-side payload encryption is a manual step, like manifest encryption:
# examples/advanced/suit_update/firmware-encryption/encrypt_firmware.py
# --no-headers --key $(SUIT_ENC_SEC) -o <payload>.enc <payload>, with the
# manifest generated via gen_manifest.py --enc-suffix .enc.
export SUIT_FIRMWARE_ENCRYPT ?= 1
# Key-establishment scheme: x25519 (default) or ml-kem-768 / ml-kem-1024
# (post-quantum; needs OpenSSL 3.5+ for key generation and the local
# wolfssl checkout on-device — see MLKEM_ENCRYPTION_PLAN.md)
export SUIT_MANIFEST_ENCRYPT_ALGO ?= x25519
# OpenSSL writes ML-KEM keys in combined seed+expanded form by default;
# `cryptography` can only parse seed-only (same story as ML-DSA above)
SUIT_ENC_GENPKEY_ARGS ?= $(if $(filter ml-kem-%,$(SUIT_MANIFEST_ENCRYPT_ALGO)),-provparam ml-kem.output_formats=seed-only)

ifneq (,$(filter suit_manifest_encrypt,$(USEMODULE)))
  # device_x25519 / device_mlkem768 / device_mlkem1024
  SUIT_ENC_KEY ?= device_$(subst -,,$(SUIT_MANIFEST_ENCRYPT_ALGO))
  SUIT_ENC_SEC ?= $(SUIT_KEY_DIR)/$(SUIT_ENC_KEY).pem
  SUIT_ENC_HDR = $(SUIT_PUB_HDR_DIR)suit_enc_seckey.h
  BUILDDEPS += $(SUIT_ENC_HDR)

  $(SUIT_ENC_SEC): | $(CLEAN)
	$(Q)echo suit: generating manifest-encryption key in $(SUIT_KEY_DIR)
	$(Q)mkdir -p $(SUIT_KEY_DIR)
	$(Q)openssl genpkey -algorithm $(SUIT_MANIFEST_ENCRYPT_ALGO) $(SUIT_ENC_GENPKEY_ARGS) -out $@

  # FORCE for the same key-switching reason as SUIT_PUB_HDR above
  $(SUIT_ENC_HDR): $(SUIT_ENC_SEC) FORCE | $(CLEAN)
	$(Q)mkdir -p $(SUIT_PUB_HDR_DIR)
	$(Q)$(RIOTBASE)/dist/tools/suit/enckey_to_header.py $< | \
		'$(LAZYSPONGE)' $(LAZYSPONGE_FLAGS) '$@'

suit/genenckey: $(SUIT_ENC_SEC)
endif
