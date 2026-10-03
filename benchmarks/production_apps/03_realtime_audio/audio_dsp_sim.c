#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <time.h>
#include <math.h>
#include <sched.h>
#include <pthread.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <unistd.h>
#include <getopt.h>
#include <xmmintrin.h>
#include <pmmintrin.h>

#define DEFAULT_SAMPLE_RATE    48000
#define DEFAULT_BUFFER_SIZE    64
#define DEFAULT_TOTAL_FRAMES   30000
#define DEFAULT_NUM_CHANNELS   8
#define DEFAULT_DSP_LOAD       16  /* Number of cascaded biquad/waveshaper stages */

typedef struct {
    double b0, b1, b2, a1, a2;
    double x1, x2, y1, y2;
} BiquadFilter;

static inline void init_biquad(BiquadFilter *f, double fc, double fs, double Q) {
    double omega = 2.0 * M_PI * fc / fs;
    double alpha = sin(omega) / (2.0 * Q);
    double cos_w = cos(omega);

    double a0 = 1.0 + alpha;
    f->b0 = ((1.0 - cos_w) / 2.0) / a0;
    f->b1 = (1.0 - cos_w) / a0;
    f->b2 = ((1.0 - cos_w) / 2.0) / a0;
    f->a1 = (-2.0 * cos_w) / a0;
    f->a2 = (1.0 - alpha) / a0;

    f->x1 = f->x2 = f->y1 = f->y2 = 0.0;
}

static inline double process_biquad(BiquadFilter *f, double in) {
    double out = f->b0 * in + f->b1 * f->x1 + f->b2 * f->x2 - f->a1 * f->y1 - f->a2 * f->y2;
    f->x2 = f->x1;
    f->x1 = in;
    f->y2 = f->y1;
    f->y1 = out;
    return out;
}

static inline double process_saturator(double in) {
    /* Fast polynomial soft-clipping saturation simulating analog console circuitry */
    if (in > 1.5) return 1.0;
    if (in < -1.5) return -1.0;
    return in - (in * in * in) / 6.75;
}

static inline uint64_t timespec_to_ns(const struct timespec *ts) {
    return (uint64_t)ts->tv_sec * 1000000000ULL + (uint64_t)ts->tv_nsec;
}

static inline struct timespec ns_to_timespec(uint64_t ns) {
    struct timespec ts;
    ts.tv_sec = (time_t)(ns / 1000000000ULL);
    ts.tv_nsec = (long)(ns % 1000000000ULL);
    return ts;
}

static int compare_u64(const void *a, const void *b) {
    uint64_t v1 = *(const uint64_t *)a;
    uint64_t v2 = *(const uint64_t *)b;
    if (v1 < v2) return -1;
    if (v1 > v2) return 1;
    return 0;
}

typedef struct {
    uint64_t *proc_times_ns;
    uint64_t *turnaround_times_ns;
    uint64_t *wake_jitters_ns;
    uint32_t total_frames;
    uint32_t xruns;
    uint64_t deadline_ns;
} BenchmarkMetrics;

void print_percentile_row(const char *name, uint64_t *arr, uint32_t n, uint64_t deadline_ns) {
    (void)deadline_ns;
    qsort(arr, n, sizeof(uint64_t), compare_u64);

    double min_us  = arr[0] / 1000.0;
    double p50_us  = arr[(uint32_t)(n * 0.50)] / 1000.0;
    double p95_us  = arr[(uint32_t)(n * 0.95)] / 1000.0;
    double p99_us  = arr[(uint32_t)(n * 0.99)] / 1000.0;
    double p999_us = arr[(uint32_t)(n * 0.999)] / 1000.0;
    double max_us  = arr[n - 1] / 1000.0;

    double sum = 0.0;
    for (uint32_t i = 0; i < n; i++) {
        sum += (double)arr[i];
    }
    double avg_us = (sum / n) / 1000.0;

    printf("| %-22s | %8.2f | %8.2f | %8.2f | %8.2f | %8.2f | %8.2f | %8.2f |\n",
           name, min_us, avg_us, p50_us, p95_us, p99_us, p999_us, max_us);
}

int main(int argc, char **argv) {
    uint32_t sample_rate = DEFAULT_SAMPLE_RATE;
    uint32_t buffer_size = DEFAULT_BUFFER_SIZE;
    uint32_t total_frames = DEFAULT_TOTAL_FRAMES;
    uint32_t num_channels = DEFAULT_NUM_CHANNELS;
    uint32_t dsp_load_stages = DEFAULT_DSP_LOAD;
    const char *json_output_path = NULL;
    bool enable_rt_priority = true;

    static struct option long_options[] = {
        {"sample-rate",   required_argument, 0, 'r'},
        {"buffer-size",   required_argument, 0, 'b'},
        {"frames",        required_argument, 0, 'n'},
        {"channels",      required_argument, 0, 'c'},
        {"load-stages",   required_argument, 0, 'l'},
        {"json",          required_argument, 0, 'j'},
        {"no-rt",         no_argument,       0, 'u'},
        {"help",          no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };

    int opt;
    while ((opt = getopt_long(argc, argv, "r:b:n:c:l:j:uh", long_options, NULL)) != -1) {
        switch (opt) {
            case 'r': sample_rate = atoi(optarg); break;
            case 'b': buffer_size = atoi(optarg); break;
            case 'n': total_frames = atoi(optarg); break;
            case 'c': num_channels = atoi(optarg); break;
            case 'l': dsp_load_stages = atoi(optarg); break;
            case 'j': json_output_path = optarg; break;
            case 'u': enable_rt_priority = false; break;
            case 'h':
            default:
                printf("Usage: %s [options]\n", argv[0]);
                printf("  -r, --sample-rate <Hz>   Audio sample rate (default: %d)\n", DEFAULT_SAMPLE_RATE);
                printf("  -b, --buffer-size <smp>  Buffer size in samples (default: %d)\n", DEFAULT_BUFFER_SIZE);
                printf("  -n, --frames <count>     Total audio buffer frames to simulate (default: %d)\n", DEFAULT_TOTAL_FRAMES);
                printf("  -c, --channels <count>   Audio channels (default: %d)\n", DEFAULT_NUM_CHANNELS);
                printf("  -l, --load-stages <stg>  DSP filter stages per channel (default: %d)\n", DEFAULT_DSP_LOAD);
                printf("  -j, --json <path>        Write JSON results to file\n");
                printf("  -u, --no-rt              Do not attempt SCHED_FIFO priority\n");
                printf("  -h, --help               Show this help\n");
                return (opt == 'h' ? 0 : 1);
        }
    }

    /* Compute buffer frame deadline: (buffer_size / sample_rate) seconds */
    double frame_period_sec = (double)buffer_size / (double)sample_rate;
    uint64_t frame_period_ns = (uint64_t)(frame_period_sec * 1e9);
    double frame_period_us = (double)frame_period_ns / 1000.0;
    double frame_period_ms = frame_period_us / 1000.0;

    printf("========================================================================\n");
    printf(" Pro-Audio DSP Real-Time Buffer Scheduling Benchmark\n");
    printf(" Real-world DAW (Bitwig/Ableton) & PipeWire/JACK Engine Emulation\n");
    printf("========================================================================\n");
    printf(" Audio Configuration:\n");
    printf("   Sample Rate:      %u Hz\n", sample_rate);
    printf("   Buffer Size:      %u samples\n", buffer_size);
    printf("   Frame Deadline:   %.3f ms (%.1f us / %lu ns)\n", frame_period_ms, frame_period_us, frame_period_ns);
    printf("   Total Frames:     %u (%.2f seconds of streaming audio)\n",
           total_frames, (double)total_frames * frame_period_sec);
    printf("   Channels:         %u\n", num_channels);
    printf("   DSP Complexity:   %u cascaded biquad/saturator stages per channel\n", dsp_load_stages);

    /* 1. Prevent paging delays via mlockall (DAW standard practice) */
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0) {
        perror("[-] Notice: mlockall failed (requires RLIMIT_MEMLOCK or CAP_IPC_LOCK)");
    } else {
        printf(" [+] Memory locked into physical RAM via mlockall()\n");
    }

    /* 2. Configure x86 CPU Denormals-Are-Zero (DAZ) and Flush-To-Zero (FTZ) */
    _MM_SET_FLUSH_ZERO_MODE(_MM_FLUSH_ZERO_ON);
    _MM_SET_DENORMALS_ZERO_MODE(_MM_DENORMALS_ZERO_ON);
    printf(" [+] x86 SSE/AVX Denormals Flush-To-Zero (FTZ) & DAZ activated\n");

    /* 3. Real-Time Scheduling Priority (SCHED_FIFO 90) */
    if (enable_rt_priority) {
        struct sched_param sp;
        sp.sched_priority = 90;
        if (sched_setscheduler(0, SCHED_FIFO, &sp) == 0) {
            printf(" [+] Acquired POSIX Real-Time SCHED_FIFO (Priority 90)\n");
        } else {
            perror(" [-] Notice: Could not acquire SCHED_FIFO (running under standard CFS/sched_ext)");
        }
    } else {
        printf(" [*] Running under standard scheduler priority\n");
    }
    printf("========================================================================\n\n");

    /* Allocate latency tracking buffers */
    uint64_t *proc_times_ns       = malloc(sizeof(uint64_t) * total_frames);
    uint64_t *turnaround_times_ns = malloc(sizeof(uint64_t) * total_frames);
    uint64_t *wake_jitters_ns     = malloc(sizeof(uint64_t) * total_frames);
    if (!proc_times_ns || !turnaround_times_ns || !wake_jitters_ns) {
        fprintf(stderr, "Fatal: Failed to allocate memory for metrics\n");
        return 1;
    }

    /* Allocate and initialize audio sample buffers */
    double **audio_buffers = malloc(sizeof(double *) * num_channels);
    BiquadFilter **filters = malloc(sizeof(BiquadFilter *) * num_channels);
    for (uint32_t c = 0; c < num_channels; c++) {
        audio_buffers[c] = malloc(sizeof(double) * buffer_size);
        filters[c] = malloc(sizeof(BiquadFilter) * dsp_load_stages);
        for (uint32_t s = 0; s < dsp_load_stages; s++) {
            double cutoff = 200.0 + (s * 350.0);
            if (cutoff > 18000.0) cutoff = 18000.0;
            init_biquad(&filters[c][s], cutoff, sample_rate, 0.707);
        }
        /* Fill initial synthetic audio wave (swept sine + noise) */
        for (uint32_t i = 0; i < buffer_size; i++) {
            audio_buffers[c][i] = sin(2.0 * M_PI * 440.0 * (double)i / sample_rate) * 0.5;
        }
    }

    /* Warm-up run */
    for (uint32_t c = 0; c < num_channels; c++) {
        for (uint32_t s = 0; s < dsp_load_stages; s++) {
            for (uint32_t i = 0; i < buffer_size; i++) {
                audio_buffers[c][i] = process_biquad(&filters[c][s], audio_buffers[c][i]);
            }
        }
    }

    /* Main High-Precision Real-Time Audio DSP Loop */
    struct timespec ts_now;
    clock_gettime(CLOCK_MONOTONIC, &ts_now);
    uint64_t next_deadline_ns = timespec_to_ns(&ts_now) + frame_period_ns;

    uint32_t xrun_count = 0;
    uint32_t consecutive_xruns = 0;
    uint32_t max_consecutive_xruns = 0;

    printf("[*] Streaming %u buffer cycles...\n", total_frames);

    for (uint32_t frame = 0; frame < total_frames; frame++) {
        uint64_t target_sched_ns = next_deadline_ns - frame_period_ns;
        struct timespec sleep_target = ns_to_timespec(target_sched_ns);

        /* Accurate high-resolution nanosleep to target frame boundary */
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &sleep_target, NULL);

        struct timespec ts_wake;
        clock_gettime(CLOCK_MONOTONIC, &ts_wake);
        uint64_t wake_ns = timespec_to_ns(&ts_wake);

        /* Wake-up jitter from expected frame start */
        int64_t jitter = (int64_t)wake_ns - (int64_t)target_sched_ns;
        wake_jitters_ns[frame] = (jitter > 0) ? (uint64_t)jitter : 0;

        /* Execute DSP Workload across all channels and filter stages */
        for (uint32_t c = 0; c < num_channels; c++) {
            double *buf = audio_buffers[c];
            for (uint32_t s = 0; s < dsp_load_stages; s++) {
                BiquadFilter *filt = &filters[c][s];
                for (uint32_t i = 0; i < buffer_size; i++) {
                    double sample = buf[i];
                    sample = process_biquad(filt, sample);
                    if ((s & 3) == 0) {
                        sample = process_saturator(sample);
                    }
                    buf[i] = sample;
                }
            }
        }

        struct timespec ts_done;
        clock_gettime(CLOCK_MONOTONIC, &ts_done);
        uint64_t done_ns = timespec_to_ns(&ts_done);

        uint64_t proc_ns = done_ns - wake_ns;
        uint64_t turnaround_ns = done_ns - target_sched_ns;

        proc_times_ns[frame] = proc_ns;
        turnaround_times_ns[frame] = turnaround_ns;

        /* Check for audio xrun (deadline miss) */
        if (done_ns > next_deadline_ns) {
            xrun_count++;
            consecutive_xruns++;
            if (consecutive_xruns > max_consecutive_xruns) {
                max_consecutive_xruns = consecutive_xruns;
            }
            /* Fast-forward next deadline to catch up if severely delayed */
            if (done_ns > next_deadline_ns + frame_period_ns) {
                next_deadline_ns = done_ns + frame_period_ns;
            } else {
                next_deadline_ns += frame_period_ns;
            }
        } else {
            consecutive_xruns = 0;
            next_deadline_ns += frame_period_ns;
        }
    }

    printf("[+] Simulation finished. Analyzing %u buffer frames...\n\n", total_frames);

    /* Formatted Latency Report Table */
    printf("+------------------------+----------+----------+----------+----------+----------+----------+----------+\n");
    printf("| Metric                 | Min (us) | Avg (us) | P50 (us) | P95 (us) | P99 (us) | 99.9(us) | Max (us) |\n");
    printf("+------------------------+----------+----------+----------+----------+----------+----------+----------+\n");

    print_percentile_row("Buffer Processing Time", proc_times_ns, total_frames, frame_period_ns);
    print_percentile_row("Total Turnaround Time ", turnaround_times_ns, total_frames, frame_period_ns);
    print_percentile_row("Wake-up Jitter Latency", wake_jitters_ns, total_frames, frame_period_ns);

    printf("+------------------------+----------+----------+----------+----------+----------+----------+----------+\n\n");

    /* Audio Quality Summary */
    double xrun_pct = ((double)xrun_count / (double)total_frames) * 100.0;
    printf("========================================================================\n");
    printf(" Audio Integrity & Buffer Scheduling Scorecard:\n");
    printf("   Total Buffer Frames:       %u\n", total_frames);
    printf("   Buffer Deadline Budget:    %.2f us\n", frame_period_us);
    printf("   Total Audio Xruns:         %u (%.3f%% failure rate)\n", xrun_count, xrun_pct);
    printf("   Max Consecutive Xruns:     %u\n", max_consecutive_xruns);
    if (xrun_count == 0) {
        printf("   Pro-Audio Health:          PERFECT (Zero glitches / 100%% glitch-free audio)\n");
    } else if (xrun_pct < 0.1) {
        printf("   Pro-Audio Health:          ACCEPTABLE (Occasional transient drops)\n");
    } else {
        printf("   Pro-Audio Health:          DEGRADED (Severe audible clicks and dropouts)\n");
    }
    printf("========================================================================\n");

    /* Optional JSON Output */
    if (json_output_path) {
        FILE *jf = fopen(json_output_path, "w");
        if (jf) {
            qsort(proc_times_ns, total_frames, sizeof(uint64_t), compare_u64);
            qsort(turnaround_times_ns, total_frames, sizeof(uint64_t), compare_u64);
            qsort(wake_jitters_ns, total_frames, sizeof(uint64_t), compare_u64);

            fprintf(jf, "{\n");
            fprintf(jf, "  \"sample_rate\": %u,\n", sample_rate);
            fprintf(jf, "  \"buffer_size\": %u,\n", buffer_size);
            fprintf(jf, "  \"frame_deadline_us\": %.3f,\n", frame_period_us);
            fprintf(jf, "  \"total_frames\": %u,\n", total_frames);
            fprintf(jf, "  \"xruns\": %u,\n", xrun_count);
            fprintf(jf, "  \"xrun_rate_pct\": %.4f,\n", xrun_pct);
            fprintf(jf, "  \"max_consecutive_xruns\": %u,\n", max_consecutive_xruns);
            fprintf(jf, "  \"processing_time_us\": {\n");
            fprintf(jf, "    \"p50\": %.2f,\n", proc_times_ns[(uint32_t)(total_frames * 0.50)] / 1000.0);
            fprintf(jf, "    \"p95\": %.2f,\n", proc_times_ns[(uint32_t)(total_frames * 0.95)] / 1000.0);
            fprintf(jf, "    \"p99\": %.2f,\n", proc_times_ns[(uint32_t)(total_frames * 0.99)] / 1000.0);
            fprintf(jf, "    \"max\": %.2f\n", proc_times_ns[total_frames - 1] / 1000.0);
            fprintf(jf, "  },\n");
            fprintf(jf, "  \"turnaround_time_us\": {\n");
            fprintf(jf, "    \"p50\": %.2f,\n", turnaround_times_ns[(uint32_t)(total_frames * 0.50)] / 1000.0);
            fprintf(jf, "    \"p95\": %.2f,\n", turnaround_times_ns[(uint32_t)(total_frames * 0.95)] / 1000.0);
            fprintf(jf, "    \"p99\": %.2f,\n", turnaround_times_ns[(uint32_t)(total_frames * 0.99)] / 1000.0);
            fprintf(jf, "    \"max\": %.2f\n", turnaround_times_ns[total_frames - 1] / 1000.0);
            fprintf(jf, "  },\n");
            fprintf(jf, "  \"wake_jitter_us\": {\n");
            fprintf(jf, "    \"p50\": %.2f,\n", wake_jitters_ns[(uint32_t)(total_frames * 0.50)] / 1000.0);
            fprintf(jf, "    \"p95\": %.2f,\n", wake_jitters_ns[(uint32_t)(total_frames * 0.95)] / 1000.0);
            fprintf(jf, "    \"p99\": %.2f,\n", wake_jitters_ns[(uint32_t)(total_frames * 0.99)] / 1000.0);
            fprintf(jf, "    \"max\": %.2f\n", wake_jitters_ns[total_frames - 1] / 1000.0);
            fprintf(jf, "  }\n");
            fprintf(jf, "}\n");
            fclose(jf);
            printf("[+] Machine-readable metrics written to %s\n", json_output_path);
        }
    }

    /* Cleanup */
    for (uint32_t c = 0; c < num_channels; c++) {
        free(audio_buffers[c]);
        free(filters[c]);
    }
    free(audio_buffers);
    free(filters);
    free(proc_times_ns);
    free(turnaround_times_ns);
    free(wake_jitters_ns);

    return 0;
}
