#ifndef TAYM_TAYM_AY_H
#define TAYM_TAYM_AY_H

/*
 * AY-3-8910 / YM2149: what TAYM targets mean on this chip (appendix A).
 *
 * taym_timers.h is chip-agnostic and reports raw target values. This is the
 * AY's half: R0..R13 are byte registers, R13 holds a 4-bit shape and restarts
 * the envelope on every write, and the sample amplitude (0x80) is combined with
 * the volume register of the same timer through the chip's DAC curve (S11.1).
 *
 * Another chip gets a file like this one; the engine does not change.
 */

#include <stddef.h>
#include <stdint.h>

#include "taym/taym.h"
#include "taym/taym_timers.h"

#ifdef __cplusplus
extern "C" {
#endif

/* R0..R13. */
#define TAYM_AY_REGISTERS 14u

/*
 * Turn the engine's target writes into AY register writes, in place: on return
 * the first N entries are register writes (`target` = R0..R13, `value` = the
 * byte to write), in the same order, and N is returned. It is never more than
 * `count`.
 *
 * Works an expiry at a time (`expiry_start`), so pass whole expiries:
 * everything one taym_timers_collect() call returned is fine.
 *
 *   - a target above R13 that is not the sample amplitude is dropped;
 *   - R13 keeps its low 4 bits;
 *   - the sample amplitude is never written itself. If its expiry also writes
 *     R8, R9 or R10, that write becomes the combined volume code.
 *
 * `variant` is CHIP.variant: TAYM_AY_VARIANT_AY or TAYM_AY_VARIANT_YM.
 */
size_t taym_ay_resolve(TaymTimerWrite *writes, size_t count, uint8_t variant);

/*
 * The combine of S11.1 for the AY/YM 16-step volume DAC: delog `volume_code`
 * through the chip's curve, scale by `amplitude / full_scale` (both linear),
 * and requantize to the nearest code.
 */
uint8_t taym_ay_combine_amplitude(uint8_t variant,
                                  uint8_t volume_code,
                                  uint32_t amplitude,
                                  uint32_t full_scale);

#ifdef __cplusplus
}
#endif

#endif
