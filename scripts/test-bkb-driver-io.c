/* Compile against the fully patched source with function/data sections and
 * --gc-sections. USB, logging and the process clock are mocked; driver paths
 * and mutex operations are real. The lab hook is compiled in so the test can
 * prove it cannot replace a computed clock index. */
#define BAIKAL_RAW_CLOCK_EXPERIMENT 1
#include "driver-baikalu.c"
#include <assert.h>
#include <sys/wait.h>

bool opt_debug, use_syslog, opt_log_output;
int opt_log_level;
pthread_mutex_t hash_lock = PTHREAD_MUTEX_INITIALIZER;
pthread_mutex_t stats_lock = PTHREAD_MUTEX_INITIALIZER;
struct thr_info **mining_thr;
int mining_threads, total_pools;
struct pool **pools;
static struct cgpu_info *snapshot_cg;
static int inject_reset;
static int tx_status, tx_count = -1, rx_status, rx_count;
static uint8_t reply[128];
static int writes, reads;
static int capture_tx, captured_tx_n;
static uint8_t captured_tx[8][64];
static int captured_tx_len[8];
static int script_n, script_i;
static uint8_t script_buf[8][160];
static int script_len[8];
static struct cgpu_info *hook_cg;
static struct baikal_info *hook_info;

bool opt_baikal_experimental_clock_index;
char *opt_baikal_board_clock_index;
int opt_baikal_board_clock_indices[3];
char *opt_baikal_clock_mode;
bool opt_baikal_bridge_global;

int to_baikal_algorithm(algorithm_type_t type)
{
    (void)type;
    return 1;
}

void _quit(int status) { exit(status); }

void cgtime(struct timeval *tv)
{
    tv->tv_sec = 1000;
    tv->tv_usec = 0;
    if (inject_reset) {
        inject_reset = 0;
        baikal_zero_stats(snapshot_cg);
    }
}

static void *observe_repeatedly(void *arg)
{
    struct cgpu_info *cg = arg;
    struct baikal_info *info = cg->device_data;
    uint64_t previous = 0;
    int i;
    for (i = 0; i < 1000; i++) {
        baikal_diag_observe_all(cg);
        pthread_mutex_lock(&info->diag_lock);
        assert(info->miners[0].diag.snap_mono_us >= previous);
        previous = info->miners[0].diag.snap_mono_us;
        assert(info->miners[0].diag_activity.share_valid == 1);
        assert(info->miners[0].diag_activity.stat_sample.diff_accepted ==
               32.0 * info->miners[0].diag_activity.stat_sample.accepted);
        pthread_mutex_unlock(&info->diag_lock);
    }
    return NULL;
}

static void check_snapshots(struct cgpu_info *cg)
{
    struct baikal_info *info = cg->device_data;
    struct thr_info thr, *threads[1];
    struct baikal_diag_activity *activity = &info->miners[0].diag_activity;
    struct device_drv drv = { .drv_id = DRIVER_baikalu };
    pthread_t readers[2];
    uint64_t epoch;
    int i;
    memset(&thr, 0, sizeof(thr));
    thr.cgpu = cg;
    threads[0] = &thr;
    mining_thr = threads;
    mining_threads = 1;
    info->miner_count = 1;
    info->miners[0].thr_id = 0;
    info->diag_ready = 1;
    info->diag_start_mono_us = baikal_diag_mono_us() - 10000000;
    cg->miner_id = 0;
    cg->drv = &drv;
    snapshot_cg = cg;
    baikal_diag_observe_all(cg);
    assert(activity->share_valid && activity->elapsed_valid);
    assert(activity->device_elapsed_sec >= 10.0 && activity->device_elapsed_sec < 12.0);
    cg->dev_start_tv.tv_sec = 100;
    cg->accepted = 1;
    cg->diff_accepted = 32;
    cg->effective_pool_no = -1;
    baikal_diag_observe_all(cg);
    assert(activity->share_valid && activity->elapsed_valid);
    assert(activity->device_elapsed_sec == 900.0);
    epoch = activity->stat_epoch;
    inject_reset = 1;
    baikal_diag_observe_all(cg);
    assert(activity->stat_epoch == epoch + 1);
    assert(!activity->share_valid && !activity->elapsed_valid);
    baikal_diag_observe_all(cg);
    assert(activity->share_valid && activity->elapsed_valid);
    for (i = 0; i < 2; i++)
        assert(pthread_create(&readers[i], NULL, observe_repeatedly, cg) == 0);
    for (i = 2; i < 1000; i++) {
        pthread_mutex_lock(&hash_lock);
        pthread_mutex_lock(&stats_lock);
        cg->accepted = i;
        cg->diff_accepted = 32.0 * i;
        pthread_mutex_unlock(&stats_lock);
        pthread_mutex_unlock(&hash_lock);
    }
    for (i = 0; i < 2; i++) assert(pthread_join(readers[i], NULL) == 0);
    mining_thr = NULL;
    mining_threads = 0;
}

void _applog(int priority, const char *message, bool force)
{
    (void)priority; (void)message; (void)force;
}

int _usb_write(struct cgpu_info *cg, int interface, int ep, char *buf,
               size_t size, int *amount, int timeout, enum usb_cmds cmd)
{
    (void)cg; (void)interface; (void)ep; (void)timeout; (void)cmd;
    writes++;
    if (capture_tx) {
        assert(captured_tx_n < 8);
        assert(size <= sizeof(captured_tx[0]));
        memcpy(captured_tx[captured_tx_n], buf, size);
        captured_tx_len[captured_tx_n] = (int)size;
        captured_tx_n++;
    }
    *amount = tx_count < 0 ? (int)size : tx_count;
    return tx_status;
}

int _usb_read(struct cgpu_info *cg, int interface, int ep, char *buf,
              size_t size, int *amount, int timeout, const char *end,
              enum usb_cmds cmd, bool once, bool cancellable)
{
    (void)cg; (void)interface; (void)ep; (void)timeout; (void)end;
    (void)cmd; (void)once; (void)cancellable;
    reads++;
    if (script_n > 0) {
        assert(script_i < script_n);
        assert(script_len[script_i] <= (int)size);
        memcpy(buf, script_buf[script_i], script_len[script_i]);
        *amount = script_len[script_i];
        script_i++;
        if (script_i >= script_n) {
            script_n = 0;
            script_i = 0;
        }
        return rx_status;
    }
    assert(rx_count <= (int)size);
    memcpy(buf, reply, rx_count);
    *amount = rx_count;
    return rx_status;
}

static void make_reply_dest(int board, int command, int param, int dest,
                            const uint8_t *data, int len)
{
    int i;
    reply[0] = ':';
    reply[1] = board;
    reply[2] = command;
    reply[3] = param;
    reply[4] = dest;
    for (i = 0; i < len; i++) {
        reply[5 + i * 2] = 0;
        reply[6 + i * 2] = data[i];
    }
    reply[5 + len * 2] = '\r';
    reply[6 + len * 2] = '\n';
    rx_count = 7 + 2 * len;
}

static void make_reply(int board, int command, int param,
                       const uint8_t *data, int len)
{
    make_reply_dest(board, command, param, 0, data, len);
}

static void push_reply(void)
{
    assert(script_n < 8);
    assert(rx_count > 0 && rx_count <= (int)sizeof(script_buf[0]));
    memcpy(script_buf[script_n], reply, rx_count);
    script_len[script_n] = rx_count;
    script_n++;
    script_i = 0;
}

static void expect_quit(void (*fn)(void))
{
    pid_t pid = fork();
    int status = 0;

    assert(pid >= 0);
    if (pid == 0) {
        fn();
        _exit(0);
    }
    assert(waitpid(pid, &status, 0) == pid);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 1);
}

static int trace_count(const struct baikal_diag_trace_view *v, void *count)
{
    (*(int *)count)++;
    assert(v->board == 2);
    assert(v->tx_intended == 7);
    if (v->tx_status < 0) assert(v->tx_transferred == 3);
    if (v->rx_status == -7) assert(v->rx_transferred == 3);
    return 0;
}

static void hook_env_mismatch(void)
{
    setenv("BAIKAL_RAW_CLOCK_CODE", "15", 1);
    baikal_experiment_clock_code(30, 400);
}

static void hook_env_invalid(void)
{
    setenv("BAIKAL_RAW_CLOCK_CODE", "30abc", 1);
    baikal_experiment_clock_code(30, 400);
}

static void hook_env_on_send(void)
{
    setenv("BAIKAL_RAW_CLOCK_CODE", "15", 1);
    baikal_setoption_for_miner_locked(hook_cg, 0, 400, 1, 55, 40);
}

static void install_out_of_range(void)
{
    opt_baikal_experimental_clock_index = true;
    opt_baikal_board_clock_indices[0] = 7;
    opt_baikal_board_clock_indices[1] = 30;
    opt_baikal_board_clock_indices[2] = 30;
    hook_info->clock_mode_global = false;
    baikal_install_experimental_clock_indices(hook_info);
}

static void script_clock_readback(int miner, int wire)
{
    uint8_t data[7];

    memset(data, 0, sizeof(data));
    data[0] = 12;
    data[1] = 1;
    data[3] = (uint8_t)wire;
    data[4] = 64;
    data[5] = 56;
    data[6] = 2;
    script_n = 0;
    script_i = 0;
    make_reply_dest(miner, BAIKAL_SET_OPTION, 1, 1, NULL, 0);
    push_reply();
    make_reply_dest(miner, BAIKAL_GET_INFO, 0, 0, data, 7);
    push_reply();
}

static int tx_byte(int frame, int offset)
{
    assert(frame < captured_tx_n);
    assert(offset < captured_tx_len[frame]);
    return captured_tx[frame][offset];
}

static void check_experimental_clock_index(struct cgpu_info *cg)
{
    struct baikal_info *info = cg->device_data;
    struct baikal_info installed;
    int parsed[3];
    int code;
    int before;
    static const int stock_echo[25] = {
        260, 270, 280, 290, 300, 310, 320, 330, 340, 350,
        360, 370, 380, 390, 400, 410, 420, 430, 440, 450,
        460, 470, 480, 490, 500
    };
    const char *rejected[] = {
        NULL, "", "08:30:30", "29:30:30 ", " 29:30:30", "+29:30:30",
        "29.0:30:30", "29:30", "29:30:30:31", "7:30:30", "33:30:30",
        "029:30:30", "29:30:30\n", "8:09:32", "30:30:30:", ":30:30:30"
    };
    size_t i;

    unsetenv("BAIKAL_RAW_CLOCK_CODE");
    assert(pthread_mutex_init(&info->clock_lock, NULL) == 0);
    info->miner_count = 3;
    info->clock = 400;
    info->cutofftemp = 55;
    info->fanspeed = 40;

    for (code = 0; code < 25; code++) {
        assert(baikal_bridge_echo_from_command_code(8 + code) == stock_echo[code]);
    }
    assert(baikal_bridge_echo_from_command_code(0) == 0);
    assert(baikal_bridge_echo_from_command_code(7) == 0);
    assert(baikal_bridge_echo_from_command_code(33) == 0);
    assert(baikal_bridge_echo_from_command_code(22) == 400);
    assert(baikal_bridge_echo_from_command_code(29) == 470);
    assert(baikal_bridge_echo_from_command_code(30) == 480);

    assert(baikal_parse_board_clock_indices("29:30:30", parsed) == NULL);
    assert(parsed[0] == 29 && parsed[1] == 30 && parsed[2] == 30);
    assert(baikal_parse_board_clock_indices("8:9:32", parsed) == NULL);
    assert(parsed[0] == 8 && parsed[1] == 9 && parsed[2] == 32);
    for (i = 0; i < sizeof(rejected) / sizeof(rejected[0]); i++)
        assert(baikal_parse_board_clock_indices(rejected[i], parsed) != NULL);
    assert(baikal_parse_board_clock_indices("29:30:30", NULL) != NULL);

    opt_baikal_experimental_clock_index = false;
    opt_baikal_board_clock_index = NULL;
    opt_baikal_clock_mode = NULL;
    memset(opt_baikal_board_clock_indices, 0, sizeof(opt_baikal_board_clock_indices));
    assert(baikal_experimental_clock_index_config_error() == NULL);
    opt_baikal_experimental_clock_index = true;
    assert(baikal_experimental_clock_index_config_error() != NULL);
    opt_baikal_experimental_clock_index = false;
    opt_baikal_board_clock_index = "29:30:30";
    assert(baikal_experimental_clock_index_config_error() != NULL);
    opt_baikal_board_clock_index = "";
    assert(baikal_experimental_clock_index_config_error() != NULL);
    opt_baikal_experimental_clock_index = true;
    opt_baikal_board_clock_index = "29:30:30";
    opt_baikal_clock_mode = "global";
    opt_baikal_board_clock_indices[0] = 29;
    opt_baikal_board_clock_indices[1] = 30;
    opt_baikal_board_clock_indices[2] = 30;
    assert(baikal_experimental_clock_index_config_error() != NULL);
    opt_baikal_clock_mode = NULL;
    assert(baikal_experimental_clock_index_config_error() != NULL);
    opt_baikal_clock_mode = "individual";
    opt_baikal_board_clock_indices[2] = 31;
    assert(baikal_experimental_clock_index_config_error() != NULL);
    opt_baikal_board_clock_indices[2] = 30;
    assert(baikal_experimental_clock_index_config_error() == NULL);

    memset(&installed, 0, sizeof(installed));
    installed.clock = 400;
    installed.clock_mode_global = true;
    installed.board_clock_indices[0] = 11;
    opt_baikal_experimental_clock_index = true;
    baikal_install_experimental_clock_indices(&installed);
    assert(!installed.experimental_clock_index);
    assert(installed.board_clock_indices[0] == 0 && installed.board_clock_indices[1] == 0 &&
           installed.board_clock_indices[2] == 0);
    installed.clock_mode_global = false;
    opt_baikal_experimental_clock_index = false;
    installed.board_clock_indices[0] = 11;
    baikal_install_experimental_clock_indices(&installed);
    assert(!installed.experimental_clock_index && installed.board_clock_indices[0] == 0);
    opt_baikal_experimental_clock_index = true;
    opt_baikal_board_clock_indices[0] = 29;
    opt_baikal_board_clock_indices[1] = 30;
    opt_baikal_board_clock_indices[2] = 8;
    baikal_install_experimental_clock_indices(&installed);
    assert(installed.experimental_clock_index);
    assert(installed.board_clock_indices[0] == 29 && installed.board_clock_indices[1] == 30 &&
           installed.board_clock_indices[2] == 8);
    hook_info = &installed;
    expect_quit(install_out_of_range);
    assert(installed.experimental_clock_index);
    assert(installed.board_clock_indices[0] == 29);

    info->experimental_clock_index = false;
    info->clock_mode_global = false;
    info->board_clock_indices[0] = 8;
    info->board_clock_indices[1] = 8;
    info->board_clock_indices[2] = 8;
    opt_baikal_experimental_clock_index = true;
    opt_baikal_board_clock_indices[0] = 29;
    capture_tx = 1;
    captured_tx_n = 0;
    make_reply_dest(0, BAIKAL_SET_OPTION, 1, 1, NULL, 0);
    assert(baikal_setoption_for_miner_locked(cg, 0, 400, 1, 55, 40));
    assert(tx_byte(0, 1) == 0 && tx_byte(0, 2) == BAIKAL_SET_OPTION);
    assert(tx_byte(0, 4) == 0 && tx_byte(0, 6) == 30);
    /* Normal mode: the sent code stays 30 for 400. RequestedClockIndex is 0. */
    assert(info->miners[0].sent_clock_code == 30);
    assert(!info->experimental_clock_index);
    assert(baikal_requested_clock_index(info, 0) == 0);
    captured_tx_n = 0;
    make_reply_dest(0, BAIKAL_SET_OPTION, 1, 1, NULL, 0);
    assert(baikal_setoption_for_miner_locked(cg, 0, 260, 1, 55, 40));
    assert(tx_byte(0, 6) == 30);
    info->miners[0].sent_clock_code = 30;
    captured_tx_n = 0;
    setenv("BAIKAL_RAW_CLOCK_CODE", "15", 1);
    make_reply_dest(0, BAIKAL_SET_OPTION, 1, 1, NULL, 0);
    assert(baikal_setoption_for_miner_locked(cg, 0, 0, 1, 55, 40));
    assert(tx_byte(0, 6) == 0);
    assert(info->miners[0].sent_clock_code == 30);
    unsetenv("BAIKAL_RAW_CLOCK_CODE");

    info->experimental_clock_index = true;
    info->clock_mode_global = false;
    info->board_clock_indices[0] = 29;
    info->board_clock_indices[1] = 30;
    info->board_clock_indices[2] = 8;
    info->miners[0].sent_clock_code = 0;
    info->miners[1].sent_clock_code = 0;
    info->miners[2].sent_clock_code = 0;
    captured_tx_n = 0;
    make_reply_dest(0, BAIKAL_SET_OPTION, 1, 1, NULL, 0);
    assert(baikal_setoption_for_miner_locked(cg, 0, 400, 1, 55, 40));
    assert(tx_byte(0, 6) == 29 && tx_byte(0, 4) == 0 && tx_byte(0, 1) == 0);
    assert(info->miners[0].sent_clock_code == 29);
    assert(info->miners[1].sent_clock_code == 0);
    captured_tx_n = 0;
    make_reply_dest(1, BAIKAL_SET_OPTION, 1, 1, NULL, 0);
    assert(baikal_setoption_for_miner_locked(cg, 1, 400, 1, 55, 40));
    assert(tx_byte(0, 6) == 30 && tx_byte(0, 1) == 1);
    assert(info->miners[0].sent_clock_code == 29);
    assert(info->miners[1].sent_clock_code == 30);
    captured_tx_n = 0;
    make_reply_dest(2, BAIKAL_SET_OPTION, 1, 1, NULL, 0);
    assert(baikal_setoption_for_miner_locked(cg, 2, 260, 1, 55, 40));
    assert(tx_byte(0, 6) == 8 && tx_byte(0, 1) == 2);
    assert(info->miners[2].sent_clock_code == 8);
    assert(info->miners[0].sent_clock_code == 29);
    assert(info->miners[1].sent_clock_code == 30);
    assert(baikal_requested_clock_index(info, 0) == 29);
    assert(baikal_requested_clock_index(info, 1) == 30);
    assert(baikal_requested_clock_index(info, 2) == 8);

    info->miners[0].sent_clock_code = 29;
    captured_tx_n = 0;
    setenv("BAIKAL_RAW_CLOCK_CODE", "15", 1);
    make_reply_dest(0, BAIKAL_SET_OPTION, 1, 1, NULL, 0);
    assert(baikal_setoption_for_miner_locked(cg, 0, 0, 1, 55, 40));
    assert(tx_byte(0, 6) == 0);
    assert(info->miners[0].sent_clock_code == 29);
    unsetenv("BAIKAL_RAW_CLOCK_CODE");

    info->miners[0].working = true;
    info->miners[0].clock_retry_pending = true;
    info->miners[0].clock_retry_done = false;
    info->miners[0].clock_retry_failures = 0;
    info->miners[0].clock_retry_last_time = 0;
    info->miners[0].command_verified = false;
    captured_tx_n = 0;
    script_clock_readback(0, 240);
    baikal_apply_clock_retry(cg, 0);
    assert(tx_byte(0, 6) == 29);
    assert(!info->miners[0].command_verified);
    assert(info->miners[0].clock_retry_failures == 1);
    assert(info->miners[0].reported_clock == 480);
    info->miners[0].clock_retry_pending = true;
    info->miners[0].clock_retry_done = false;
    info->miners[0].clock_retry_failures = 0;
    info->miners[0].clock_retry_last_time = 0;
    captured_tx_n = 0;
    script_clock_readback(0, 235);
    baikal_apply_clock_retry(cg, 0);
    assert(tx_byte(0, 6) == 29);
    assert(info->miners[0].command_verified);
    assert(info->miners[0].clock_retry_done);
    assert(info->miners[0].reported_clock == 470);
    assert(info->miners[0].sent_clock_code == 29);

    info->miners[0].working = true;
    info->miners[0].clock_retry_pending = false;
    info->miners[0].command_verified = false;
    baikal_queue_clock_reapply(info, &info->miners[0], 400);
    captured_tx_n = 0;
    script_clock_readback(0, 235);
    baikal_apply_pending_clock_reapply(cg, 0);
    assert(tx_byte(0, 6) == 29);
    assert(info->miners[0].command_verified);
    assert(info->miners[0].reported_clock == 470);
    assert(!info->miners[0].pending_clock_reapply_valid);

    info->miner_count = 5;
    before = writes;
    captured_tx_n = 0;
    assert(!baikal_setoption_for_miner_locked(cg, 3, 400, 1, 55, 40));
    assert(writes == before && captured_tx_n == 0);
    assert(baikal_requested_clock_index(info, 3) == 0);
    assert(baikal_requested_clock_index(info, 0) == 29);
    info->experimental_clock_index = false;
    assert(baikal_requested_clock_index(info, 0) == 0);
    info->experimental_clock_index = true;
    info->miner_count = 3;

    before = writes;
    assert(!baikal_setoption_global_locked(cg, 400, 1, 55, 40));
    assert(writes == before);
    info->experimental_clock_index = false;
    opt_baikal_experimental_clock_index = false;
    info->clock_mode_global = true;
    info->miner_count = 3;
    captured_tx_n = 0;
    make_reply_dest(0, BAIKAL_SET_OPTION, 1, 1, NULL, 0);
    assert(baikal_setoption_global_locked(cg, 400, 1, 55, 40));
    assert(tx_byte(0, 4) == 1 && tx_byte(0, 6) == 30);
    assert(info->miners[0].sent_clock_code == 30);
    assert(info->miners[1].sent_clock_code == 30);
    assert(info->miners[2].sent_clock_code == 30);

    info->clock_mode_global = true;
    info->experimental_clock_index = true;
    opt_baikal_experimental_clock_index = true;
    info->global_clock_pending = true;
    info->global_clock_inflight = false;
    baikal_apply_global_clock_generation(cg);
    assert(!info->global_clock_pending);
    assert(!info->global_clock_inflight);

    assert(baikal_experiment_clock_code(30, 400) == 30);
    setenv("BAIKAL_RAW_CLOCK_CODE", "30", 1);
    assert(baikal_experiment_clock_code(30, 400) == 30);
    unsetenv("BAIKAL_RAW_CLOCK_CODE");
    setenv("BAIKAL_RAW_CLOCK_CODE", "15", 1);
    assert(baikal_experiment_clock_code(0, 0) == 0);
    unsetenv("BAIKAL_RAW_CLOCK_CODE");
    expect_quit(hook_env_mismatch);
    expect_quit(hook_env_invalid);
    hook_cg = cg;
    info->experimental_clock_index = true;
    info->clock_mode_global = false;
    info->board_clock_indices[0] = 30;
    info->miner_count = 3;
    before = writes;
    expect_quit(hook_env_on_send);
    assert(writes == before);
    unsetenv("BAIKAL_RAW_CLOCK_CODE");
    capture_tx = 0;
    script_n = 0;
    pthread_mutex_destroy(&info->clock_lock);
}

static void check_global_clock_rejection(struct cgpu_info *cg)
{
    struct baikal_info *info = cg->device_data;
    struct thr_info threads[3], *thread_ptrs[3];
    uint8_t data[] = {22, 145, 7, 240, 64, 64, 145};
    int bridge_global, attempt, i, before;

    memset(threads, 0, sizeof(threads));
    assert(pthread_mutex_init(&info->clock_lock, NULL) == 0);
    info->clock_mode_global = true;
    info->experimental_clock_index = false;
    opt_baikal_experimental_clock_index = false;
    info->miner_count = 3;
    info->clock = 400;
    cg->deven = DEV_ENABLED;
    cg->primary_pool_no = cg->effective_pool_no = 0;
    for (i = 0; i < 3; i++) {
        threads[i].cgpu = cg;
        thread_ptrs[i] = &threads[i];
        info->miners[i].thr_id = i;
        info->miners[i].working = true;
        info->miners[i].clock_ready = true;
    }
    mining_thr = thread_ptrs;
    mining_threads = 3;
    for (bridge_global = 0; bridge_global < 2; bridge_global++) {
        opt_baikal_bridge_global = bridge_global;
        info->global_clock_pending = false;
        info->global_clock_inflight = false;
        info->clock_verified_generation = 0;
        for (i = 0; i < 3; i++) info->miners[i].sent_clock_code = 0;
        baikal_queue_global_clock_generation(info);
        for (attempt = 0; attempt < 3; attempt++) {
            script_n = script_i = 0;
            if (bridge_global) {
                make_reply_dest(0, BAIKAL_SET_OPTION, 1, 0, NULL, 0);
                push_reply();
            } else {
                for (i = 0; i < 3; i++) {
                    make_reply_dest(i, BAIKAL_SET_OPTION, 1, i == 0 ? 0 : 1, NULL, 0);
                    push_reply();
                }
            }
            for (i = 0; i < 3; i++) {
                data[5] = i == 0 ? 56 : 64;
                make_reply_dest(i, BAIKAL_GET_INFO, 0, 1, data, sizeof(data));
                push_reply();
            }
            info->global_clock_last_time = 0;
            baikal_apply_global_clock_generation(cg);
            assert(script_n == 0);
            assert(info->global_clock_failures == attempt + 1);
            assert(info->global_clock_pending == (attempt < 2));
            assert(info->clock_verified_generation == 0);
            for (i = 0; i < 3; i++) {
                assert(!info->miners[i].command_verified);
                assert(info->miners[i].reported_clock == 480);
                assert(info->miners[i].sent_clock_code == ((!bridge_global && i > 0) ? 30 : 0));
            }
        }
        before = writes;
        baikal_apply_global_clock_generation(cg);
        assert(writes == before);
    }
    opt_baikal_bridge_global = false;
    mining_thr = NULL;
    mining_threads = 0;
    pthread_mutex_destroy(&info->clock_lock);
}

int main(void)
{
    struct baikal_info info;
    struct cgpu_info cg;
    struct miner_info *miner;
    baikal_msg msg;
    uint8_t data[] = {12, 1, 0, 200, 64, 56, 2};
    uint8_t *trace = NULL;
    uint32_t bytes, live, capacity;
    uint64_t overwritten, dropped, recorded;
    int enabled, count = 0, before;
    char host_hash[65];
    pthread_mutex_t usb_mutex = PTHREAD_MUTEX_INITIALIZER;

    unsetenv("BAIKAL_RAW_CLOCK_CODE");
    baikal_diag_hash_executable(host_hash);
    assert(strlen(host_hash) == 64);
    printf("SELF_SHA256 %s\n", host_hash);

    memset(&info, 0, sizeof(info));
    memset(&cg, 0, sizeof(cg));
    memset(&msg, 0, sizeof(msg));
    cg.device_data = &info;
    cg.mutex = &usb_mutex;
    cg.miner_id = 2;
    info.miner_count = 3;
    info.clock = 400;
    info.clock_mode_global = true;
    assert(pthread_mutex_init(&info.diag_lock, NULL) == 0);
    assert(pthread_mutex_init(&info.diag_sample_lock, NULL) == 0);
    assert(baikal_diag_trace_init(&info.trace) == 0);
    assert(baikal_diag_trace_set(&info.trace, 1) == 0);
    miner = &info.miners[2];
    baikal_diag_counters_init(&miner->diag);

    msg.miner_id = 2;
    msg.cmd = BAIKAL_SEND_WORK;
    assert(baikal_sendmsg(&cg, &msg) == 7);
    make_reply(2, BAIKAL_SEND_WORK, 199, NULL, 0);
    assert(baikal_readmsg(&cg, &msg, 7) == 7);
    assert(msg.param == 199); /* Preserve the ACK byte, not a clock value. */
    assert(miner->diag.work_sends == 1);

    tx_count = 3;
    assert(baikal_sendmsg(&cg, &msg) < 0);
    tx_status = -7;
    assert(baikal_sendmsg(&cg, &msg) == -7);
    assert(miner->diag.work_sends == 1);
    tx_count = -1;
    tx_status = 0;

    assert(baikal_sendmsg(&cg, &msg) > 0);
    rx_count = 0;
    assert(baikal_readmsg(&cg, &msg, 7) < 0);
    assert(miner->diag.work_sends == 2); /* A sent frame with no ACK. */
    assert(baikal_sendmsg(&cg, &msg) > 0);
    make_reply(1, BAIKAL_SEND_WORK, 199, NULL, 0);
    assert(baikal_readmsg(&cg, &msg, 7) < 0);

    msg.miner_id = 2;
    assert(baikal_sendmsg(&cg, &msg) > 0);
    make_reply(2, BAIKAL_GET_INFO, 0, NULL, 0);
    assert(baikal_readmsg(&cg, &msg, 7) < 0);

    msg.cmd = BAIKAL_SEND_WORK;
    assert(baikal_sendmsg(&cg, &msg) > 0);
    rx_status = -7;
    rx_count = 3;
    assert(baikal_readmsg(&cg, &msg, 7) == -7);
    rx_status = 0;

    make_reply(2, BAIKAL_GET_INFO, 0, data, sizeof(data));
    assert(baikal_getinfo_for_miner_locked(&cg, 2));
    assert(miner->asic_count == 64 && miner->asic_count_r == 56);
    assert(miner->asic_ver == 2 && miner->reported_clock == 400);
    assert(miner->diag.info_valid == 1 && miner->diag.last_info_ok == 1);

    tx_status = -7;
    tx_count = 3;
    before = reads;
    assert(!baikal_getinfo_for_miner_locked(&cg, 2));
    assert(reads == before);
    assert(miner->diag.info_valid == 1 && miner->diag.last_info_ok == 0);
    assert(miner->asic_count_r == 56 && miner->reported_clock == 400);
    tx_status = 0;
    tx_count = -1;

    make_reply(2, BAIKAL_GET_INFO, 0, data, 6);
    assert(!baikal_getinfo_for_miner_locked(&cg, 2));
    assert(miner->diag.last_info_ok == 0 && miner->asic_count_r == 56);
    make_reply(0, BAIKAL_GET_INFO, 0, data, sizeof(data));
    assert(!baikal_getinfo_for_miner_locked(&cg, 2));
    assert(miner->diag.last_info_ok == 0 && miner->asic_count_r == 56);

    assert(baikal_diag_trace_copy(&info.trace, &trace, &bytes, &live,
           &overwritten, &dropped, &recorded, &capacity, &enabled) == 0);
    assert(live == (unsigned)writes && recorded == live);
    assert(baikal_diag_trace_walk(trace, bytes, trace_count, &count) == 0);
    assert(count == writes && overwritten == 0 && dropped == 0);
    free(trace);
    make_reply(2, BAIKAL_RESET, 3, NULL, 0);
    assert(baikal_reset(&cg));
    assert(info.miner_count == 3);
    make_reply(0, BAIKAL_RESET, 1, NULL, 0);
    assert(baikal_reset(&cg));
    assert(info.miner_count == 1);
    make_reply(BAIKAL_MAXMINERS, BAIKAL_RESET, 3, NULL, 0);
    assert(!baikal_reset(&cg));
    make_reply(2, BAIKAL_RESET, 0, NULL, 0);
    assert(!baikal_reset(&cg));
    make_reply(2, BAIKAL_RESET, BAIKAL_MAXMINERS + 1, NULL, 0);
    assert(!baikal_reset(&cg));
    make_reply(2, BAIKAL_GET_INFO, 3, NULL, 0);
    assert(!baikal_reset(&cg));
    check_snapshots(&cg);
    check_experimental_clock_index(&cg);
    check_global_clock_rejection(&cg);
    baikal_diag_trace_free(&info.trace);
    pthread_mutex_destroy(&info.diag_sample_lock);
    pthread_mutex_destroy(&info.diag_lock);
    puts("Real driver USB/GET_INFO and concurrent snapshot paths: PASS");
    return 0;
}
