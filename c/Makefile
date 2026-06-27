CC ?= cc
AR ?= ar
PYTHON ?= python3
CFLAGS ?= -std=c99 -Wall -Wextra -pedantic -Iinclude
BUILD_DIR ?= build

LIB := $(BUILD_DIR)/libtaym.a
OBJ := $(BUILD_DIR)/taym.o
TEST_BIN := $(BUILD_DIR)/test_roundtrip
SAMPLE := $(BUILD_DIR)/sample.taym

.PHONY: all test clean

all: $(LIB)

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(OBJ): src/taym.c include/taym/taym.h | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c -o $@ src/taym.c

$(LIB): $(OBJ)
	$(AR) rcs $@ $^

$(TEST_BIN): tests/test_roundtrip.c $(LIB) include/taym/taym.h | $(BUILD_DIR)
	$(CC) $(CFLAGS) -o $@ tests/test_roundtrip.c $(LIB)

$(SAMPLE): | $(BUILD_DIR)
	cd ../python && PYTHONPATH=src $(PYTHON) -m taym sample ../c/$(SAMPLE)

test: $(TEST_BIN) $(SAMPLE)
	$(TEST_BIN) $(SAMPLE)

clean:
	rm -rf $(BUILD_DIR)
