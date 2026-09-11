# Reusable Make targets for the generic test framework.
# Runtime state is always kept under the project running Make.

TESTKIT_MAKEFILE := $(lastword $(MAKEFILE_LIST))
TESTKIT_DIR ?= $(patsubst %/,%,$(dir $(abspath $(TESTKIT_MAKEFILE))))
TESTKIT_PROJECT_ROOT := $(CURDIR)
TESTKIT_STATE_DIR := $(TESTKIT_PROJECT_ROOT)/.testkit
TESTKIT_CACHE_DIR := $(TESTKIT_STATE_DIR)/.cache
TESTKIT_CONFIG := $(TESTKIT_STATE_DIR)/.config/settings.conf
export TESTKIT_CACHE_DIR TESTKIT_DIR

.PHONY: test debug template menuconfig help

test:
	@"$(TESTKIT_DIR)/bin/menuconfig.sh" select test "$(TESTKIT_PROJECT_ROOT)" "$(TESTKIT_CONFIG)" "$(SUITE)" "$(SOURCE)"

debug:
	@"$(TESTKIT_DIR)/bin/menuconfig.sh" select debug "$(TESTKIT_PROJECT_ROOT)" "$(TESTKIT_CONFIG)" "$(SUITE)" "$(SOURCE)"

template:
	@"$(TESTKIT_DIR)/bin/menuconfig.sh" template "$(TESTKIT_PROJECT_ROOT)" "$(TESTKIT_CONFIG)" "$(SUITE)" "$(FORCE)"

menuconfig:
	@"$(TESTKIT_DIR)/bin/menuconfig.sh" configure "$(TESTKIT_PROJECT_ROOT)" "$(TESTKIT_CONFIG)"

help:
	@echo "make test        Select a test suite/source, then test"
	@echo "make debug       Select a test suite/source, then debug with GDB/PDB"
	@echo "make template    Select a test suite, then create test templates"
	@echo "make menuconfig  Configure search paths, file patterns, and sorting"
	@echo "Pass SUITE=... SOURCE=... to skip the corresponding selection screens"
	@echo "Pass SUITE=... FORCE=1 to replace existing template files"
