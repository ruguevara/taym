/*
 * taym_timeline -- print a TAYM file's register-write timeline.
 *
 * One line per write: "<frame> <cycle> <reg> <value>", decimal, cycle-ordered
 * within each frame. The Python oracle prints the same with
 * `python -m taym timeline`, and `make test` diffs the two; keep the formats
 * identical.
 *
 *     taym_timeline song.taym [chip_index]
 */

#include "taym/taym_player.h"

#include <stdio.h>
#include <stdlib.h>

/* A fast timer makes many expiries per frame: 5 kHz at 50 fps is 100, and each
   may write several targets. Generous, since this is an offline tool. */
#define SCRATCH_WRITES 8192u

static void print_writes(void *context,
                         uint32_t frame,
                         const TaymTimerWrite *writes,
                         size_t count)
{
    size_t i;

    (void) context;
    for (i = 0u; i < count; i++) {
        printf("%u %llu %u %u\n",
               (unsigned) frame,
               (unsigned long long) writes[i].cycle,
               (unsigned) writes[i].reg,
               (unsigned) writes[i].value);
    }
}

int main(int argc, char **argv)
{
    Taym taym;
    TaymTimerWrite *scratch;
    size_t chip_index = 0u;
    TaymResult result;

    if (argc < 2) {
        fprintf(stderr, "usage: %s <file.taym> [chip_index]\n", argv[0]);
        return 2;
    }
    if (argc >= 3) {
        chip_index = (size_t) strtoul(argv[2], NULL, 10);
    }

    taym_init(&taym);
    result = taym_read_file(argv[1], &taym);
    if (result != TAYM_OK) {
        fprintf(stderr, "read %s: %s\n", argv[1], taym_result_string(result));
        taym_free(&taym);
        return 1;
    }

    scratch = (TaymTimerWrite *) malloc(SCRATCH_WRITES * sizeof(*scratch));
    if (scratch == NULL) {
        fprintf(stderr, "out of memory\n");
        taym_free(&taym);
        return 1;
    }

    result = taym_player_run(&taym, chip_index, scratch, SCRATCH_WRITES,
                             print_writes, NULL);
    if (result != TAYM_OK) {
        fprintf(stderr, "play: %s\n", taym_result_string(result));
    }

    free(scratch);
    taym_free(&taym);
    return result == TAYM_OK ? 0 : 1;
}
