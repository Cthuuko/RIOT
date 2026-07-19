ifneq (,$(filter suit_transport_%,$(USEMODULE)))
  DIRS += transport
endif

ifneq (,$(filter suit_storage_%,$(USEMODULE)))
  DIRS += storage
endif

ifneq (,$(filter suit_manifest_encrypt,$(USEMODULE)))
  DIRS += encrypt
endif

include $(RIOTBASE)/Makefile.base
