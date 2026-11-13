/*
 * AY-3-8910 / YM2149 target rules (appendix A, S11.1). The engine in
 * taym_timers.c reports raw target values; this makes register writes of them.
 */

#include "taym/taym_ay.h"

#include <math.h>

/* AY/YM 16-step volume DAC curves (appendix A.3), used only by the 0x80
   sample-amplitude combine. Both chips pick the volume level from a 4-bit
   curve; the YM's is closer to a true exponential. The YM's 32-step table is
   the *envelope* curve, not this one. */
static const double taym_ay_dac[16] = {
    0.0,
    0.00999465934234,
    0.0144502937362,
    0.0210574502174,
    0.0307011520562,
    0.0455481803616,
    0.0644998855573,
    0.107362478065,
    0.126588845655,
    0.20498970016,
    0.292210269322,
    0.372838941024,
    0.492530708782,
    0.635324635691,
    0.805584802014,
    1.0
};

static const double taym_ym_dac[16] = {
    0.0,
    0.00772106507973,
    0.0139620050355,
    0.0200198367285,
    0.029694056611,
    0.0403906309606,
    0.0583352407111,
    0.0777752346075,
    0.111085679408,
    0.148485542077,
    0.211551079576,
    0.281101701381,
    0.400427252613,
    0.53443198291,
    0.75800717174,
    1.0
};

static int taym_ay_is_volume_reg(uint8_t target)
{
    return target == 0x08u || target == 0x09u || target == 0x0Au;
}

static const double *taym_dac_table(uint8_t variant)
{
    return variant == TAYM_AY_VARIANT_YM ? taym_ym_dac : taym_ay_dac;
}

uint8_t taym_ay_combine_amplitude(uint8_t variant,
                                  uint8_t volume_code,
                                  uint32_t amplitude,
                                  uint32_t full_scale)
{
    const double *table = taym_dac_table(variant);
    double linear;
    double best_distance;
    unsigned best_code;
    unsigned code;

    linear = table[volume_code & 0x0Fu];
    if (full_scale != 0u) {
        linear *= (double) amplitude / (double) full_scale;
    } else {
        linear = 0.0;
    }

    best_code = 0u;
    best_distance = fabs(table[0] - linear);
    for (code = 0u; code < 16u; code++) {
        const double distance = fabs(table[code] - linear);
        /* <= so a tie prefers the higher code, as the oracle does. */
        if (distance <= best_distance) {
            best_code = code;
            best_distance = distance;
        }
    }
    return (uint8_t) best_code;
}

/* Full scale of an amplitude by where it came from: a lane's element type, or
   a byte for an inline operand. */
static uint32_t taym_ay_full_scale(uint8_t value_type)
{
    switch (value_type) {
        case TAYM_VT_U16:
            return 0xFFFFu;
        case TAYM_VT_U32:
            return 0xFFFFFFFFu;
        default:
            return 0xFFu;
    }
}

size_t taym_ay_resolve(TaymTimerWrite *writes, size_t count, uint8_t variant)
{
    size_t out = 0u;
    size_t begin = 0u;

    if (writes == NULL) {
        return 0u;
    }

    while (begin < count) {
        size_t end = begin + 1u;
        size_t i;
        int has_amplitude = 0;
        uint32_t amplitude = 0u;
        uint32_t full_scale = 0xFFu;

        /* One expiry: up to the next write that starts another. */
        while (end < count && !writes[end].expiry_start) {
            end++;
        }

        /* S11.1: the amplitude first, so the volume register of the same
           expiry can combine it. */
        for (i = begin; i < end; i++) {
            if (writes[i].target == TAYM_TGT_SAMPLE_AMPLITUDE) {
                has_amplitude = 1;
                amplitude = writes[i].value;
                full_scale = taym_ay_full_scale(writes[i].value_type);
            }
        }

        for (i = begin; i < end; i++) {
            TaymTimerWrite write = writes[i];
            uint8_t byte;

            if (write.target > TAYM_AY_TARGET_MAX) {
                continue; /* the amplitude, or nothing this chip has */
            }
            byte = (uint8_t) (write.value & 0xFFu);
            if (write.target == TAYM_AY_R13_SHAPE) {
                byte = (uint8_t) (byte & 0x0Fu);
            } else if (has_amplitude && taym_ay_is_volume_reg(write.target)) {
                byte = taym_ay_combine_amplitude(variant, byte, amplitude, full_scale);
            }
            write.value = byte;
            writes[out++] = write;
        }
        begin = end;
    }
    return out;
}
