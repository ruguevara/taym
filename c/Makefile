CC ?= cc
AR ?= ar
PYTHON ?= python3
CFLAGS ?= -std=c99 -Wall -Wextra -pedantic -Iinclude
LDLIBS ?= -lm
BUILD_DIR ?= build

LIB := $(BUILD_DIR)/libtaym.a
OBJS := $(BUILD_DIR)/taym.o $(BUILD_DIR)/taym_timers.o $(BUILD_DIR)/taym_ay.o $(BUILD_DIR)/taym_player.o
TEST_BIN := $(BUILD_DIR)/test_roundtrip
TIMERS_BIN := $(BUILD_DIR)/test_timers
TIMELINE_BIN := $(BUILD_DIR)/taym_timeline
SAMPLE := $(BUILD_DIR)/sample.taym
AUDIO := $(BUILD_DIR)/audio.taym
ENGINE := $(BUILD_DIR)/engine.taym
EXAMPLES := $(wildcard ../examples/*.taym)

.PHONY: all test clean

all: $(LIB) $(TIMELINE_BIN)

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/taym.o: src/taym.c include/taym/taym.h | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c -o $@ src/taym.c

$(BUILD_DIR)/taym_timers.o: src/taym_timers.c include/taym/taym_timers.h include/taym/taym.h | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c -o $@ src/taym_timers.c

$(BUILD_DIR)/taym_ay.o: src/taym_ay.c include/taym/taym_ay.h include/taym/taym_timers.h include/taym/taym.h | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c -o $@ src/taym_ay.c

$(BUILD_DIR)/taym_player.o: src/taym_player.c include/taym/taym_player.h include/taym/taym_ay.h include/taym/taym_timers.h include/taym/taym.h | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c -o $@ src/taym_player.c

$(LIB): $(OBJS)
	$(AR) rcs $@ $^

$(TEST_BIN): tests/test_roundtrip.c $(LIB) include/taym/taym.h | $(BUILD_DIR)
	$(CC) $(CFLAGS) -o $@ tests/test_roundtrip.c $(LIB) $(LDLIBS)

$(TIMERS_BIN): tests/test_timers.c $(LIB) include/taym/taym_timers.h include/taym/taym_ay.h | $(BUILD_DIR)
	$(CC) $(CFLAGS) -o $@ tests/test_timers.c $(LIB) $(LDLIBS)

$(TIMELINE_BIN): tests/taym_timeline.c $(LIB) include/taym/taym_player.h | $(BUILD_DIR)
	$(CC) $(CFLAGS) -o $@ tests/taym_timeline.c $(LIB) $(LDLIBS)

$(SAMPLE): | $(BUILD_DIR)
	cd ../python && PYTHONPATH=src $(PYTHON) -m taym sample ../c/$(SAMPLE)

$(AUDIO): | $(BUILD_DIR)
	cd ../python && PYTHONPATH=src $(PYTHON) -m taym sample --audio ../c/$(AUDIO)

$(ENGINE): | $(BUILD_DIR)
	cd ../python && PYTHONPATH=src $(PYTHON) -m taym sample --engine ../c/$(ENGINE)

# Structural round trip, the timer engine's own checks, and the cross-language
# diff: the C write timeline must match the Python oracle's byte for byte.
# The diff runs on the generated fixtures and on every tune in ../examples.
test: $(TEST_BIN) $(TIMERS_BIN) $(TIMELINE_BIN) $(SAMPLE) $(AUDIO) $(ENGINE)
	$(TEST_BIN) $(SAMPLE)
	$(TIMERS_BIN)
	@for f in $(SAMPLE) $(AUDIO) $(ENGINE) $(EXAMPLES); do \
		echo "timeline diff: $$f"; \
		$(TIMELINE_BIN) $$f > $(BUILD_DIR)/c_writes.txt || exit 1; \
		(cd ../python && PYTHONPATH=src $(PYTHON) -m taym writes ../c/$$f) > $(BUILD_DIR)/py_writes.txt || exit 1; \
		diff -u $(BUILD_DIR)/py_writes.txt $(BUILD_DIR)/c_writes.txt || exit 1; \
	done
	@echo "all C tests passed"

clean:
	rm -rf $(BUILD_DIR)
