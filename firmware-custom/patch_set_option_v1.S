/*
 * BK-B custom firmware patch v1.
 *
 * Reproduces the stock startup SET_FREQ sequence for runtime clock changes:
 * every selected host's four internal targets are programmed at selectors
 * 0..15. Selector zero consumes the requested clock-table index; selectors
 * 1..15 follow the stock sender's fixed register path.
 *
 * Destination zero remains board-specific. Destination one validates all
 * three hosts before issuing one bridge-global 12-target transaction.
 *
 * A host's bridge-status echo advances only if all 64 sender calls for that
 * host succeed. The command ACK reports success only if every selected host
 * succeeds. Clock code zero remains fan/temperature-only.
 */
    .syntax unified
    .cpu cortex-m4
    .thumb
    .section .text
    .global new_set_option
    .thumb_func
new_set_option:                 /* 0x08005000 */
    push    {r4, r5, r6, r7, r8, lr}
    sub     sp, #24
    mov     r4, r0                     /* r4 = host frame */
    ldrb    r5, [r4, #0]               /* r5 = reply miner_id */
    movs    r7, #0                     /* command-status ACK */
    str     r7, [sp, #16]              /* per-host failure bitmask */

    cmp     r5, #2
    bhi     .Lreply

    /* Select [start_host, end_host). */
    ldrb    r2, [r4, #3]
    cmp     r2, #0
    beq     .Lselect_individual
    cmp     r2, #1
    bne     .Lreply
    movs    r2, #0
    str     r2, [sp, #8]
    movs    r2, #3
    str     r2, [sp, #12]
    b       .Lvalidate_states

.Lselect_individual:
    str     r5, [sp, #8]
    adds    r2, r5, #1
    str     r2, [sp, #12]

    /* Validate every selected host before any side effect. */
.Lvalidate_states:
    ldr     r6, [sp, #8]
.Lstate_loop:
    movw    r2, #0x0E18
    movt    r2, #0x2000
    mov.w   r3, #0x374
    mla     r3, r3, r6, r2
    ldrb.w  r2, [r3, #0x320]
    subs    r2, #4
    cmp     r2, #1
    bhi     .Lreply
    adds    r6, #1
    ldr     r2, [sp, #12]
    cmp     r6, r2
    blo     .Lstate_loop

    /* Clock code zero intentionally skips readiness and clock-index checks. */
    ldrb    r8, [r4, #4]
    cmp     r8, #0
    beq     .Lapply_thermal
    cmp     r8, #8
    blo     .Lreply
    cmp     r8, #32
    bhi     .Lreply

    ldr     r6, [sp, #8]
.Lreadiness_loop:
    movw    r2, #0x0E18
    movt    r2, #0x2000
    mov.w   r3, #0x374
    mla     r3, r3, r6, r2
    ldrb.w  r2, [r3, #0x008]
    cmp     r2, #3
    bne     .Lreply
    ldrb.w  r2, [r3, #0x0C8]
    cmp     r2, #3
    bne     .Lreply
    ldrb.w  r2, [r3, #0x188]
    cmp     r2, #3
    bne     .Lreply
    ldrb.w  r2, [r3, #0x248]
    cmp     r2, #3
    bne     .Lreply
    adds    r6, #1
    ldr     r2, [sp, #12]
    cmp     r6, r2
    blo     .Lreadiness_loop

    /* Preserve stock cutoff-temperature and fan behavior. */
.Lapply_thermal:
    ldr     r6, [sp, #8]
.Lthermal_loop:
    movw    r2, #0x0E18
    movt    r2, #0x2000
    mov.w   r3, #0x374
    mla     r3, r3, r6, r2
    ldrb    r2, [r4, #6]
    cbz     r2, .Lfan
    strb.w  r2, [r3, #0x305]
.Lfan:
    ldrb    r1, [r4, #7]
    cmp     r1, #100
    bhi     .Lnext_thermal
    mov     r0, r6
    movw    r3, #0x0CB1                /* stock fan/TIM2 helper */
    movt    r3, #0x0800
    blx     r3
.Lnext_thermal:
    adds    r6, #1
    ldr     r2, [sp, #12]
    cmp     r6, r2
    blo     .Lthermal_loop

    movs    r7, #1
    cmp     r8, #0
    beq     .Lreply

    /* Exact stock SET_FREQ parameter bytes: {index,1,1,6,7,7,0,0}. */
    movs    r3, #0
    str     r3, [sp, #0]
    str     r3, [sp, #4]
    strb.w  r8, [sp, #0]
    movs    r3, #1
    strb    r3, [sp, #1]
    strb    r3, [sp, #2]
    movs    r3, #6
    strb    r3, [sp, #3]
    movs    r3, #7
    strb    r3, [sp, #4]
    strb    r3, [sp, #5]

    ldr     r6, [sp, #8]
    lsls    r6, r6, #2                 /* first target = start_host * 4 */

.Ltarget_loop:
    movs    r7, #0                     /* selector */
.Lselector_loop:
    /* Match stock work eligibility without changing slot state/counts. */
    lsrs    r0, r6, #2
    movw    r1, #0x0E18
    movt    r1, #0x2000
    mov.w   r2, #0x374
    mla     r1, r0, r2, r1
    and.w   r0, r6, #3
    movs    r2, #0xC0
    mla     r1, r0, r2, r1
    movs    r2, #12
    mla     r1, r7, r2, r1
    ldrb    r0, [r1, #8]
    subs    r0, #2
    cmp     r0, #1
    bhi     .Lselector_delay
    mov     r0, r6
    mov     r1, r7
    mov     r2, sp
    movw    r3, #0x1A65                /* stock sender 0x08001A64 | Thumb */
    movt    r3, #0x0800
    blx     r3
    cmp     r0, #0
    bne     .Lselector_delay

    /* Record failure against target / 4, but complete the stock sequence. */
    lsrs    r3, r6, #2
    movs    r2, #1
    lsls    r2, r2, r3
    ldr     r3, [sp, #16]
    orrs    r3, r2
    str     r3, [sp, #16]

.Lselector_delay:
    movs    r0, #1
    movw    r3, #0x06F1                /* stock counted-delay helper */
    movt    r3, #0x0800
    blx     r3
    adds    r7, #1
    cmp     r7, #16
    blo     .Lselector_loop

    adds    r6, #1
    ldr     r2, [sp, #12]
    lsls    r2, r2, #2                 /* last target = end_host * 4 */
    cmp     r6, r2
    blo     .Ltarget_loop

    /* Commit successful host echoes independently; ACK requires all hosts. */
    movs    r7, #1
    ldr     r6, [sp, #8]
.Lecho_loop:
    movs    r2, #1
    lsls    r2, r2, r6
    ldr     r3, [sp, #16]
    tst     r3, r2
    beq     .Lcommit_echo
    movs    r7, #0
    b       .Lnext_echo

.Lcommit_echo:
    movw    r2, #0x0E18
    movt    r2, #0x2000
    mov.w   r3, #0x374
    mla     r3, r3, r6, r2
    strb.w  r8, [r3, #0x302]

.Lnext_echo:
    adds    r6, #1
    ldr     r2, [sp, #12]
    cmp     r6, r2
    blo     .Lecho_loop

.Lreply:
    /* Exact stock SET_OPTION ACK shape. */
    movs    r3, #0
    str.w   r3, [r4, #0xD8]
    strb    r7, [r4, #3]
    movs    r3, #1
    strb    r5, [r4, #0]
    strb    r3, [r4, #2]
    mov     r0, r4
    add     sp, #24
    pop     {r4, r5, r6, r7, r8, lr}
    movw    r3, #0x05FD                /* stock reply sender */
    movt    r3, #0x0800
    bx      r3
