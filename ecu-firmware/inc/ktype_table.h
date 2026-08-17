/*
 * ktype_table.h - GENERATED FILE, do not edit by hand.
 *
 * Regenerate with:  python tools/gen_ktype_table.py
 *
 * Type-K thermocouple EMF as a function of temperature, from NIST's own
 * ITS-90 inverse function coefficients (NIST Standard Reference
 * Database 60 / Monograph 175, DOI 10.18434/T4S888). See
 * tools/gen_ktype_table.py for the full provenance, including why only
 * the inverse coefficient set is used and why this is a table rather
 * than a runtime polynomial.
 *
 * Index i corresponds to temperature KTYPE_T_MIN_C + i * KTYPE_T_STEP_C
 * degrees Celsius; the value is that junction's EMF in MICROVOLTS
 * referenced to a 0 C cold junction. The array is strictly monotonic,
 * which is what lets the same table serve both directions: forward for
 * cold-junction compensation, and reverse (binary search) for the
 * measurement itself.
 */
#ifndef KTYPE_TABLE_H
#define KTYPE_TABLE_H

#include <stdint.h>

#define KTYPE_T_MIN_C   (-40)
#define KTYPE_T_STEP_C  (10)
#define KTYPE_COUNT     (130)

static const int32_t KTYPE_EMF_UV[KTYPE_COUNT] = {
       -1527,    -1156,     -777,     -392,        0,      399,   /*   -40 C */
         799,     1203,     1611,     2022,     2436,     2851,   /*    20 C */
        3267,     3683,     4098,     4510,     4920,     5328,   /*    80 C */
        5734,     6137,     6539,     6940,     7340,     7739,   /*   140 C */
        8139,     8540,     8941,     9344,     9748,    10154,   /*   200 C */
       10561,    10970,    11381,    11794,    12208,    12623,   /*   260 C */
       13040,    13457,    13875,    14294,    14713,    15133,   /*   320 C */
       15554,    15975,    16397,    16820,    17243,    17667,   /*   380 C */
       18091,    18516,    18942,    19367,    19792,    20218,   /*   440 C */
       20646,    21071,    21497,    21923,    22349,    22775,   /*   500 C */
       23202,    23628,    24054,    24480,    24905,    25330,   /*   560 C */
       25755,    26179,    26603,    27026,    27448,    27870,   /*   620 C */
       28290,    28711,    29130,    29548,    29966,    30383,   /*   680 C */
       30799,    31214,    31628,    32041,    32453,    32864,   /*   740 C */
       33275,    33684,    34093,    34500,    34907,    35312,   /*   800 C */
       35717,    36120,    36523,    36925,    37325,    37725,   /*   860 C */
       38124,    38522,    38918,    39314,    39708,    40102,   /*   920 C */
       40495,    40886,    41276,    41666,    42054,    42441,   /*   980 C */
       42827,    43212,    43596,    43978,    44360,    44740,   /*  1040 C */
       45119,    45497,    45873,    46248,    46622,    46995,   /*  1100 C */
       47366,    47736,    48104,    48472,    48837,    49201,   /*  1160 C */
       49564,    49926,    50285,    50644,   /*  1220 C */
};

#endif /* KTYPE_TABLE_H */
