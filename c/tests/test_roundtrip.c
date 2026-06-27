#include "taym/taym.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures = 0;

static void check(int condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", message);
        failures++;
    }
}

static uint8_t *read_all(const char *path, size_t *size)
{
    FILE *fp;
    long file_size;
    uint8_t *data;
    size_t got;

    fp = fopen(path, "rb");
    if (fp == NULL) {
        return NULL;
    }
    if (fseek(fp, 0, SEEK_END) != 0) {
        fclose(fp);
        return NULL;
    }
    file_size = ftell(fp);
    if (file_size < 0) {
        fclose(fp);
        return NULL;
    }
    if (fseek(fp, 0, SEEK_SET) != 0) {
        fclose(fp);
        return NULL;
    }
    data = (uint8_t *)malloc((size_t)file_size == 0 ? 1 : (size_t)file_size);
    if (data == NULL) {
        fclose(fp);
        return NULL;
    }
    got = fread(data, 1, (size_t)file_size, fp);
    fclose(fp);
    if (got != (size_t)file_size) {
        free(data);
        return NULL;
    }
    *size = (size_t)file_size;
    return data;
}

static void make_sample_model(Taym *taym)
{
    taym_init(taym);

    taym->trak.frame_rate = taym_to_fix16(50.0);
    taym->trak.frame_count = 2;
    taym->trak.loop_frame = TAYM_NO_LOOP;

    taym->chip_count = 1;
    taym->chips = (TaymChip *)calloc(taym->chip_count, sizeof(*taym->chips));
    taym->chips[0].clock_hz = 1773400;
    taym->chips[0].chip_type_id = TAYM_CHIP_TYPE_AY;
    taym->chips[0].variant = TAYM_AY_VARIANT_AY;
    memcpy(taym->chips[0].name, "AY", 2);
    taym->chips[0].config = TAYM_CHIP_CONFIG_DEFAULT;

    taym->timer_count = 1;
    taym->timers = (TaymTimr *)calloc(taym->timer_count, sizeof(*taym->timers));
    taym->timers[0].clock_divider = 16;
    taym->timers[0].chip_index = 0;
    taym->timers[0].clock_mode = TAYM_CLOCK_CHIP_PERIOD;

    taym->mod_count = 2;
    taym->mods = (TaymMods *)calloc(taym->mod_count, sizeof(*taym->mods));
    taym->mods[0].base_timer_value = 25;
    taym->mods[0].timer_lane_ref = 0;
    taym->mods[0].first_action = 0;
    taym->mods[0].action_count = 1;
    taym->mods[0].command = TAYM_CMD_START;
    taym->mods[1].base_timer_value = 1234;
    taym->mods[1].timer_lane_ref = 5678;
    taym->mods[1].first_action = 9;
    taym->mods[1].action_count = 8;
    taym->mods[1].command = TAYM_CMD_STOP;

    taym->action_count = 1;
    taym->actions = (TaymActn *)calloc(taym->action_count, sizeof(*taym->actions));
    taym->actions[0].operand = 0;
    taym->actions[0].target_id = 0x08;
    taym->actions[0].source_mode = TAYM_SRC_BIND_LANE;

    taym->lane_count = 1;
    taym->lanes = (TaymLane *)calloc(taym->lane_count, sizeof(*taym->lanes));
    taym->lanes[0].value_offset = 0;
    taym->lanes[0].length = 2;
    taym->lanes[0].loop_index = 0;
    taym->lanes[0].value_type = TAYM_VT_U8;

    taym->tlane_count = 1;
    taym->tlanes = (TaymTlan *)calloc(taym->tlane_count, sizeof(*taym->tlanes));
    taym->tlanes[0].value_offset = 0;
    taym->tlanes[0].length = 2;
    taym->tlanes[0].loop_index = 0;
    taym->tlanes[0].timing_mode = TAYM_TM_ABSOLUTE;

    taym->vu08_count = 2;
    taym->vu08 = (uint8_t *)calloc(taym->vu08_count, sizeof(*taym->vu08));
    taym->vu08[0] = 15;
    taym->vu08[1] = 0;

    taym->vu32_count = 2;
    taym->vu32 = (uint32_t *)calloc(taym->vu32_count, sizeof(*taym->vu32));
    taym->vu32[0] = 25;
    taym->vu32[1] = 75;
}

static void test_read_write_python_sample(const char *fixture_path)
{
    uint8_t *expected = NULL;
    uint8_t *actual = NULL;
    size_t expected_size = 0;
    size_t actual_size = 0;
    Taym taym;
    TaymResult r;

    expected = read_all(fixture_path, &expected_size);
    check(expected != NULL, "read Python sample fixture");
    if (expected == NULL) {
        return;
    }

    r = taym_read_bytes(expected, expected_size, &taym);
    check(r == TAYM_OK, "C reads Python sample");
    if (r != TAYM_OK) {
        fprintf(stderr, "read result: %s\n", taym_result_string(r));
        free(expected);
        return;
    }

    check(taym.trak.frame_rate == taym_to_fix16(50.0), "frame_rate");
    check(taym.trak.frame_count == 2, "frame_count");
    check(taym.chip_count == 1, "chip_count");
    check(taym.timer_count == 1, "timer_count");
    check(taym.mod_count == 2, "mod_count");
    check(taym.action_count == 1, "action_count");
    check(taym.lane_count == 1, "lane_count");
    check(taym.tlane_count == 1, "tlane_count");
    check(taym.vu08_count == 2 && taym.vu08[0] == 15 && taym.vu08[1] == 0, "VU08");
    check(taym.vu32_count == 2 && taym.vu32[0] == 25 && taym.vu32[1] == 75, "VU32");

    r = taym_write_bytes(&taym, &actual, &actual_size);
    check(r == TAYM_OK, "C writes parsed sample");
    check(actual_size == expected_size, "round-trip size");
    check(actual_size == expected_size && memcmp(actual, expected, expected_size) == 0,
          "round-trip bytes");

    taym_free_bytes(actual);
    taym_free(&taym);
    free(expected);
}

static void test_manual_model_matches_python_sample(const char *fixture_path)
{
    uint8_t *expected = NULL;
    uint8_t *actual = NULL;
    size_t expected_size = 0;
    size_t actual_size = 0;
    Taym taym;
    TaymResult r;

    expected = read_all(fixture_path, &expected_size);
    check(expected != NULL, "read Python sample fixture for manual model");
    if (expected == NULL) {
        return;
    }

    make_sample_model(&taym);
    r = taym_write_bytes(&taym, &actual, &actual_size);
    check(r == TAYM_OK, "manual C sample writes");
    check(actual_size == expected_size, "manual C sample size");
    check(actual_size == expected_size && memcmp(actual, expected, expected_size) == 0,
          "manual C sample bytes");

    taym_free_bytes(actual);
    taym_free(&taym);
    free(expected);
}

static void test_bad_magic_rejected(const char *fixture_path)
{
    uint8_t *data = NULL;
    size_t size = 0;
    Taym taym;
    TaymResult r;

    data = read_all(fixture_path, &size);
    check(data != NULL, "read Python sample fixture for bad magic");
    if (data == NULL) {
        return;
    }
    data[0] = 'X';
    r = taym_read_bytes(data, size, &taym);
    check(r == TAYM_ERROR_FORMAT, "bad magic rejected");
    free(data);
}

static void test_frame_data_chunk_round_trip(void)
{
    static const char psg0[4] = {'P', 'S', 'G', '0'};
    static const uint8_t payload[] = {'P', 'S', 'G', 0x1A, 0xFD};
    Taym taym;
    Taym read_back;
    TaymResult r;
    uint8_t *bytes = NULL;
    size_t size = 0;
    const TaymChunk *chunk;

    make_sample_model(&taym);
    memcpy(taym.chips[0].frame_data_tag, psg0, 4);
    taym.chunk_count = 1;
    taym.chunks = (TaymChunk *)calloc(1, sizeof(*taym.chunks));
    memcpy(taym.chunks[0].tag, psg0, 4);
    taym.chunks[0].size = sizeof(payload);
    taym.chunks[0].data = (uint8_t *)calloc(sizeof(payload), 1);
    memcpy(taym.chunks[0].data, payload, sizeof(payload));

    r = taym_write_bytes(&taym, &bytes, &size);
    check(r == TAYM_OK, "frame-data model writes");
    r = taym_read_bytes(bytes, size, &read_back);
    check(r == TAYM_OK, "frame-data model reads");
    chunk = taym_find_chunk(&read_back, psg0);
    check(chunk != NULL, "PSG0 chunk preserved");
    check(chunk != NULL && chunk->size == sizeof(payload), "PSG0 chunk size");
    check(chunk != NULL && memcmp(chunk->data, payload, sizeof(payload)) == 0,
          "PSG0 chunk payload");

    taym_free(&read_back);
    taym_free_bytes(bytes);
    taym_free(&taym);
}

int main(int argc, char **argv)
{
    if (argc != 2) {
        fprintf(stderr, "usage: %s sample.taym\n", argv[0]);
        return 2;
    }

    test_read_write_python_sample(argv[1]);
    test_manual_model_matches_python_sample(argv[1]);
    test_bad_magic_rejected(argv[1]);
    test_frame_data_chunk_round_trip();

    if (failures != 0) {
        fprintf(stderr, "%d failure(s)\n", failures);
        return 1;
    }
    printf("C TAYM tests passed\n");
    return 0;
}
