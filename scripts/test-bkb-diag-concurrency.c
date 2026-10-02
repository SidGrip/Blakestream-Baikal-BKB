#include "baikal-diag.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>

static struct baikal_diag_trace ring;

static int check_record(const struct baikal_diag_trace_view *v, void *unused)
{
    unsigned i;
    (void)unused;
    assert(v->tx_len == 512 && v->rx_len == 128);
    assert(v->generation == 123 && v->board == 2);
    for (i = 0; i < v->tx_len; i++) assert(v->tx[i] == (unsigned char)i);
    for (i = 0; i < v->rx_len; i++) assert(v->rx[i] == (unsigned char)(255 - i));
    return 0;
}

static void *produce(void *unused)
{
    struct baikal_diag_trace_rec record;
    unsigned char tx[512], rx[128];
    unsigned i;
    (void)unused;
    for (i = 0; i < sizeof(tx); i++) tx[i] = (unsigned char)i;
    for (i = 0; i < sizeof(rx); i++) rx[i] = (unsigned char)(255 - i);
    memset(&record, 0, sizeof(record));
    record.tx = tx;
    record.tx_len = sizeof(tx);
    record.rx = rx;
    record.rx_len = sizeof(rx);
    record.generation = 123;
    record.board = 2;
    for (i = 0; i < 100000; i++) {
        record.mono_us = i;
        assert(baikal_diag_trace_append(&ring, &record) == 0);
    }
    return NULL;
}

int main(void)
{
    unsigned char wire[512], data[252], decoded[252];
    unsigned char miner, cmd, param, dest;
    unsigned i, n;
    int size, dlen, bad = 0;
    pthread_t producer;

    for (i = 0; i < sizeof(data); i++) data[i] = (unsigned char)i;
    for (n = 0; n <= sizeof(data); n++) {
        assert(baikal_diag_encode_tx(2, 4, 199, 0, data, n,
                                    wire, sizeof(wire), &size) == 0);
        assert(size == (int)(7 + 2 * n));
        assert(baikal_diag_decode_rx(wire, size, &miner, &cmd, &param,
                                     &dest, decoded, sizeof(decoded), &dlen) == size);
        assert(dlen == (int)n && memcmp(data, decoded, n) == 0);
        assert(miner == 2 && cmd == 4 && param == 199 && dest == 0);
    }
    for (n = 8; n <= 128; n += 2) {
        memset(wire, 0, sizeof(wire));
        wire[0] = ':';
        wire[n - 2] = '\r';
        wire[n - 1] = '\n';
        if (baikal_diag_decode_rx(wire, n, &miner, &cmd, &param, &dest,
                                  decoded, sizeof(decoded), &dlen) >= 0) bad++;
    }
    printf("Malformed half-payload frames accepted: %d (expected 0)\n", bad);

    assert(baikal_diag_trace_init(&ring) == 0);
    assert(baikal_diag_trace_open(&ring, BAIKAL_DIAG_TRACE_BYTES, 1) == 0);
    assert(pthread_create(&producer, NULL, produce, NULL) == 0);
    for (i = 0; i < 200; i++) {
        unsigned char *copy = NULL;
        uint32_t len = 0, live = 0, capacity = 0;
        uint64_t overwritten = 0, dropped = 0, recorded = 0;
        int enabled;

        if (i % 17 == 0) baikal_diag_trace_clear(&ring);
        assert(baikal_diag_trace_set(&ring, i % 3 != 0) == 0);
        assert(baikal_diag_trace_copy(&ring, &copy, &len, &live,
                                      &overwritten, &dropped, &recorded,
                                      &capacity, &enabled) == 0);
        assert(capacity <= BAIKAL_DIAG_TRACE_BYTES && len <= capacity);
        assert(recorded == overwritten + live && dropped == 0);
        assert(baikal_diag_trace_walk(copy, len, check_record, NULL) == 0);
        free(copy);
    }
    assert(pthread_join(producer, NULL) == 0);
    baikal_diag_trace_free(&ring);
    puts("Concurrent trace snapshots: PASS");
    return bad ? 1 : 0;
}
