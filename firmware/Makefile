# Host tier: build and run the firmware core without an ESP32.
#
# The firmware's capture path is deliberately hardware-free (see
# components/imu_capture/include/imu_logger.h), so the same bytes that run on the
# board are compiled here and driven by a simulated sensor. `make test` is the
# gate; `make selftest` produces the acceptance capture.
#
#   make test       # the unit suite (no output files)
#   make selftest   # the 60 s bench capture + assertions, writes evidence/
#   make bench MODE=A SECONDS=60
#
# CC may be overridden; -Wall -Wextra -Werror is the contract for this tier
# because a warning here is a warning on the target with fewer eyes on it.

CC      ?= cc
CSTD    ?= -std=c11
WARN    ?= -Wall -Wextra -Werror -Wshadow -Wconversion -Wno-sign-conversion
OPT     ?= -O2
INC     := -Icomponents/imu_capture/include -Ihost
CFLAGS  ?= $(CSTD) $(WARN) $(OPT) $(INC) -D_POSIX_C_SOURCE=200809L
LDFLAGS ?=

BUILD   := build-host
CORE    := components/imu_capture/imu_contract.c components/imu_capture/imu_regs.c \
           components/imu_capture/imu_logger.c
HOSTLIB := host/fake_hal.c

MODE    ?= M
SECONDS ?= 60
OUTDIR  ?= evidence/bench

.PHONY: all test selftest bench clean

all: $(BUILD)/selftest $(BUILD)/maintest

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/selftest: $(CORE) $(HOSTLIB) host/selftest.c | $(BUILD)
	$(CC) $(CFLAGS) -o $@ $(CORE) $(HOSTLIB) host/selftest.c $(LDFLAGS)

$(BUILD)/maintest: $(CORE) $(HOSTLIB) host/test_imu.c | $(BUILD)
	$(CC) $(CFLAGS) -o $@ $(CORE) $(HOSTLIB) host/test_imu.c $(LDFLAGS)

# Green only when every binary built AND the suite exited 0.
test: $(BUILD)/maintest
	./$(BUILD)/maintest

selftest: $(BUILD)/selftest
	mkdir -p $(OUTDIR)
	./$(BUILD)/selftest $(OUTDIR) $(MODE) $(SECONDS)

bench: $(BUILD)/selftest
	mkdir -p $(OUTDIR)
	./$(BUILD)/selftest $(OUTDIR) $(MODE) $(SECONDS)

clean:
	rm -rf $(BUILD)
