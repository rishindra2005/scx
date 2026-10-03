#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <time.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <getopt.h>
#include <unistd.h>
#include <sys/mman.h>

#define DEFAULT_NUM_ORDERS       250000
#define RING_BUFFER_CAPACITY     262144  /* Power of 2: 2^18 (8 MB buffer) */
#define RING_BUFFER_MASK         (RING_BUFFER_CAPACITY - 1)
#define MAX_PRICE_TICKS          20000   /* Price ladder ticks ($0.01 to $200.00) */
#define BASE_MID_PRICE           10000   /* $100.00 base price */

typedef enum {
    SIDE_BUY  = 0,
    SIDE_SELL = 1
} Side;

typedef enum {
    TYPE_LIMIT  = 0,
    TYPE_MARKET = 1,
    TYPE_CANCEL = 2
} OrderType;

typedef struct OrderNode {
    uint64_t order_id;
    uint32_t price;
    uint32_t qty;
    uint8_t side;
    uint64_t ingress_ts_ns;
    struct OrderNode *prev;
    struct OrderNode *next;
} OrderNode;

typedef struct {
    uint32_t price;
    uint64_t total_volume;
    uint32_t order_count;
    OrderNode *head;
    OrderNode *tail;
} PriceLevel;

typedef struct {
    PriceLevel levels[MAX_PRICE_TICKS];
    uint32_t best_bid;
    uint32_t best_ask;
    uint64_t total_trades;
    uint64_t total_volume_matched;
} LimitOrderBook;

/* Ring Buffer Event Structure (Cache-aligned) */
typedef struct {
    uint64_t order_id;
    uint32_t price;
    uint32_t qty;
    uint8_t side;
    uint8_t type;
    uint64_t ingress_ts_ns;
} OrderEvent;

typedef struct {
    _Alignas(64) _Atomic uint64_t head;
    uint8_t pad1[64 - sizeof(_Atomic uint64_t)];
    _Alignas(64) _Atomic uint64_t tail;
    uint8_t pad2[64 - sizeof(_Atomic uint64_t)];
    OrderEvent *events;
} RingBuffer;

static inline uint64_t get_time_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static inline bool ring_buffer_init(RingBuffer *rb) {
    atomic_init(&rb->head, 0);
    atomic_init(&rb->tail, 0);
    int ret = posix_memalign((void **)&rb->events, 64, sizeof(OrderEvent) * RING_BUFFER_CAPACITY);
    if (ret != 0 || !rb->events) {
        rb->events = malloc(sizeof(OrderEvent) * RING_BUFFER_CAPACITY);
    }
    if (!rb->events) return false;
    memset(rb->events, 0, sizeof(OrderEvent) * RING_BUFFER_CAPACITY);
    return true;
}

static inline void ring_buffer_free(RingBuffer *rb) {
    free(rb->events);
}

static inline bool ring_buffer_push(RingBuffer *rb, const OrderEvent *ev) {
    uint64_t current_tail = atomic_load_explicit(&rb->tail, memory_order_relaxed);
    uint64_t current_head = atomic_load_explicit(&rb->head, memory_order_acquire);

    if (current_tail - current_head >= RING_BUFFER_CAPACITY) {
        return false; /* Buffer full */
    }

    rb->events[current_tail & RING_BUFFER_MASK] = *ev;
    atomic_store_explicit(&rb->tail, current_tail + 1, memory_order_release);
    return true;
}

static inline bool ring_buffer_pop(RingBuffer *rb, OrderEvent *ev) {
    uint64_t current_head = atomic_load_explicit(&rb->head, memory_order_relaxed);
    uint64_t current_tail = atomic_load_explicit(&rb->tail, memory_order_acquire);

    if (current_head == current_tail) {
        return false; /* Buffer empty */
    }

    *ev = rb->events[current_head & RING_BUFFER_MASK];
    atomic_store_explicit(&rb->head, current_head + 1, memory_order_release);
    return true;
}

/* Fast Order Book Memory Pool */
typedef struct {
    OrderNode *nodes;
    OrderNode **order_lookup; /* O(1) order lookup by order_id */
    uint32_t capacity;
    uint32_t allocated_count;
} OrderPool;

static inline bool order_pool_init(OrderPool *pool, uint32_t max_orders) {
    pool->capacity = max_orders + 1024;
    pool->nodes = calloc(pool->capacity, sizeof(OrderNode));
    pool->order_lookup = calloc(pool->capacity, sizeof(OrderNode *));
    pool->allocated_count = 0;
    return (pool->nodes && pool->order_lookup);
}

static inline void order_pool_free(OrderPool *pool) {
    free(pool->nodes);
    free(pool->order_lookup);
}

static inline OrderNode *order_pool_alloc(OrderPool *pool, uint64_t order_id) {
    if (pool->allocated_count >= pool->capacity) return NULL;
    OrderNode *node = &pool->nodes[pool->allocated_count++];
    node->order_id = order_id;
    node->prev = NULL;
    node->next = NULL;
    if (order_id < pool->capacity) {
        pool->order_lookup[order_id] = node;
    }
    return node;
}

static inline void order_pool_release(OrderPool *pool, uint64_t order_id) {
    if (order_id < pool->capacity) {
        pool->order_lookup[order_id] = NULL;
    }
}

/* LOB Mechanics */
static void init_book(LimitOrderBook *book) {
    memset(book, 0, sizeof(LimitOrderBook));
    book->best_bid = 0;
    book->best_ask = MAX_PRICE_TICKS - 1;
    for (uint32_t i = 0; i < MAX_PRICE_TICKS; i++) {
        book->levels[i].price = i;
    }
}

static inline void add_order_to_level(PriceLevel *lvl, OrderNode *node) {
    node->prev = lvl->tail;
    node->next = NULL;
    if (lvl->tail) {
        lvl->tail->next = node;
    } else {
        lvl->head = node;
    }
    lvl->tail = node;
    lvl->total_volume += node->qty;
    lvl->order_count++;
}

static inline void remove_order_from_level(PriceLevel *lvl, OrderNode *node) {
    if (node->prev) {
        node->prev->next = node->next;
    } else {
        lvl->head = node->next;
    }
    if (node->next) {
        node->next->prev = node->prev;
    } else {
        lvl->tail = node->prev;
    }
    lvl->total_volume -= node->qty;
    lvl->order_count--;
}

/* Process Order Event on Matching Engine Thread */
static inline void process_order_event(LimitOrderBook *book, OrderPool *pool, const OrderEvent *ev) {
    if (ev->type == TYPE_CANCEL) {
        if (ev->order_id < pool->capacity) {
            OrderNode *target = pool->order_lookup[ev->order_id];
            if (target && target->qty > 0) {
                PriceLevel *lvl = &book->levels[target->price];
                remove_order_from_level(lvl, target);
                order_pool_release(pool, target->order_id);

                if (target->side == SIDE_BUY && target->price == book->best_bid && lvl->order_count == 0) {
                    while (book->best_bid > 0 && book->levels[book->best_bid].order_count == 0) {
                        book->best_bid--;
                    }
                } else if (target->side == SIDE_SELL && target->price == book->best_ask && lvl->order_count == 0) {
                    while (book->best_ask < MAX_PRICE_TICKS - 1 && book->levels[book->best_ask].order_count == 0) {
                        book->best_ask++;
                    }
                }
            }
        }
        return;
    }

    uint32_t remaining_qty = ev->qty;

    if (ev->side == SIDE_BUY) {
        /* Match against Best Ask */
        while (remaining_qty > 0 && book->best_ask <= ev->price && book->best_ask < MAX_PRICE_TICKS - 1) {
            PriceLevel *ask_lvl = &book->levels[book->best_ask];
            while (remaining_qty > 0 && ask_lvl->head != NULL) {
                OrderNode *resting = ask_lvl->head;
                uint32_t trade_qty = (remaining_qty < resting->qty) ? remaining_qty : resting->qty;

                resting->qty -= trade_qty;
                remaining_qty -= trade_qty;
                ask_lvl->total_volume -= trade_qty;
                book->total_trades++;
                book->total_volume_matched += trade_qty;

                if (resting->qty == 0) {
                    remove_order_from_level(ask_lvl, resting);
                    order_pool_release(pool, resting->order_id);
                }
            }
            if (ask_lvl->order_count == 0) {
                book->best_ask++;
                while (book->best_ask < MAX_PRICE_TICKS - 1 && book->levels[book->best_ask].order_count == 0) {
                    book->best_ask++;
                }
            }
        }

        /* Post unfilled limit order to Bid Book */
        if (remaining_qty > 0 && ev->type == TYPE_LIMIT && ev->price < MAX_PRICE_TICKS) {
            OrderNode *new_node = order_pool_alloc(pool, ev->order_id);
            if (new_node) {
                new_node->price = ev->price;
                new_node->qty = remaining_qty;
                new_node->side = SIDE_BUY;
                new_node->ingress_ts_ns = ev->ingress_ts_ns;
                add_order_to_level(&book->levels[ev->price], new_node);
                if (ev->price > book->best_bid) {
                    book->best_bid = ev->price;
                }
            }
        }
    } else {
        /* SIDE_SELL: Match against Best Bid */
        while (remaining_qty > 0 && book->best_bid >= ev->price && book->best_bid > 0) {
            PriceLevel *bid_lvl = &book->levels[book->best_bid];
            while (remaining_qty > 0 && bid_lvl->head != NULL) {
                OrderNode *resting = bid_lvl->head;
                uint32_t trade_qty = (remaining_qty < resting->qty) ? remaining_qty : resting->qty;

                resting->qty -= trade_qty;
                remaining_qty -= trade_qty;
                bid_lvl->total_volume -= trade_qty;
                book->total_trades++;
                book->total_volume_matched += trade_qty;

                if (resting->qty == 0) {
                    remove_order_from_level(bid_lvl, resting);
                    order_pool_release(pool, resting->order_id);
                }
            }
            if (bid_lvl->order_count == 0) {
                if (book->best_bid > 0) {
                    book->best_bid--;
                    while (book->best_bid > 0 && book->levels[book->best_bid].order_count == 0) {
                        book->best_bid--;
                    }
                }
            }
        }

        /* Post unfilled limit order to Ask Book */
        if (remaining_qty > 0 && ev->type == TYPE_LIMIT && ev->price < MAX_PRICE_TICKS) {
            OrderNode *new_node = order_pool_alloc(pool, ev->order_id);
            if (new_node) {
                new_node->price = ev->price;
                new_node->qty = remaining_qty;
                new_node->side = SIDE_SELL;
                new_node->ingress_ts_ns = ev->ingress_ts_ns;
                add_order_to_level(&book->levels[ev->price], new_node);
                if (ev->price < book->best_ask) {
                    book->best_ask = ev->price;
                }
            }
        }
    }
}

/* Worker Thread Contexts */
typedef struct {
    RingBuffer *rb;
    uint32_t total_orders;
    _Atomic bool producer_done;
} ThreadContext;

static int compare_u64(const void *a, const void *b) {
    uint64_t v1 = *(const uint64_t *)a;
    uint64_t v2 = *(const uint64_t *)b;
    if (v1 < v2) return -1;
    if (v1 > v2) return 1;
    return 0;
}

static void *producer_worker(void *arg) {
    ThreadContext *ctx = (ThreadContext *)arg;
    RingBuffer *rb = ctx->rb;
    uint32_t n = ctx->total_orders;

    unsigned int seed = 1337;
    for (uint32_t i = 1; i <= n; i++) {
        OrderEvent ev;
        ev.order_id = i;

        int r = rand_r(&seed) % 100;
        if (r < 65) {
            /* 65% Limit Orders */
            ev.type = TYPE_LIMIT;
            ev.side = (rand_r(&seed) % 2 == 0) ? SIDE_BUY : SIDE_SELL;
            int offset = (rand_r(&seed) % 40) - 20; /* Spread around mid */
            int p = (int)BASE_MID_PRICE + offset;
            if (p < 1) p = 1;
            if (p >= MAX_PRICE_TICKS) p = MAX_PRICE_TICKS - 1;
            ev.price = (uint32_t)p;
            ev.qty = (rand_r(&seed) % 10 + 1) * 10;
        } else if (r < 85) {
            /* 20% Cancels */
            ev.type = TYPE_CANCEL;
            /* Attempt to cancel a recently placed order */
            uint32_t cancel_id = (i > 50) ? (i - (rand_r(&seed) % 45 + 1)) : 1;
            ev.order_id = cancel_id;
            ev.price = 0;
            ev.qty = 0;
            ev.side = 0;
        } else {
            /* 15% Market Orders */
            ev.type = TYPE_MARKET;
            ev.side = (rand_r(&seed) % 2 == 0) ? SIDE_BUY : SIDE_SELL;
            ev.price = (ev.side == SIDE_BUY) ? MAX_PRICE_TICKS - 1 : 1;
            ev.qty = (rand_r(&seed) % 5 + 1) * 10;
        }

        /* Ingress timestamp taken right before pushing to lock-free ring buffer */
        ev.ingress_ts_ns = get_time_ns();

        while (!ring_buffer_push(rb, &ev)) {
            __builtin_ia32_pause();
        }
    }

    atomic_store_explicit(&ctx->producer_done, true, memory_order_release);
    return NULL;
}

int main(int argc, char **argv) {
    uint32_t total_orders = DEFAULT_NUM_ORDERS;
    const char *json_path = NULL;

    static struct option long_opts[] = {
        {"orders", required_argument, 0, 'n'},
        {"json",   required_argument, 0, 'j'},
        {"help",   no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };

    int opt;
    while ((opt = getopt_long(argc, argv, "n:j:h", long_opts, NULL)) != -1) {
        switch (opt) {
            case 'n': total_orders = atoi(optarg); break;
            case 'j': json_path = optarg; break;
            case 'h':
            default:
                printf("Usage: %s [options]\n", argv[0]);
                printf("  -n, --orders <count>  Total order events to process (default: %d)\n", DEFAULT_NUM_ORDERS);
                printf("  -j, --json <path>     Save JSON metrics output\n");
                printf("  -h, --help            Show help\n");
                return (opt == 'h' ? 0 : 1);
        }
    }

    printf("========================================================================\n");
    printf(" High-Frequency Trading (HFT) Limit Order Book & Matching Engine\n");
    printf(" Architecture: LMAX Disruptor Ring-Buffer + Direct-Indexed Price Ladder\n");
    printf("========================================================================\n");
    printf(" Workload Parameters:\n");
    printf("   Synthetic Orders:      %u events\n", total_orders);
    printf("   Ring Buffer Size:      %u slots (Lock-Free Cache-Aligned SPSC)\n", RING_BUFFER_CAPACITY);
    printf("   Base Mid-Market:       $%u.%02u\n", BASE_MID_PRICE / 100, BASE_MID_PRICE % 100);
    printf("   Order Mix:             65%% Limit Orders | 20%% Cancels | 15%% Market Orders\n");

    RingBuffer rb;
    if (!ring_buffer_init(&rb)) {
        fprintf(stderr, "Fatal: Cannot allocate ring buffer\n");
        return 1;
    }

    LimitOrderBook *book = calloc(1, sizeof(LimitOrderBook));
    if (!book) {
        fprintf(stderr, "Fatal: Cannot allocate LimitOrderBook\n");
        return 1;
    }
    init_book(book);

    OrderPool pool;
    if (!order_pool_init(&pool, total_orders)) {
        fprintf(stderr, "Fatal: Cannot allocate OrderPool\n");
        return 1;
    }

    uint64_t *latencies_ns = malloc(sizeof(uint64_t) * total_orders);
    if (!latencies_ns) {
        fprintf(stderr, "Fatal: Cannot allocate latency array\n");
        return 1;
    }

    /* Lock current memory into RAM to avoid minor page faults if permitted */
    if (mlockall(MCL_CURRENT) == 0) {
        printf(" [+] Memory locked into physical RAM via mlockall(MCL_CURRENT)\n");
    }

    ThreadContext ctx;
    ctx.rb = &rb;
    ctx.total_orders = total_orders;
    atomic_init(&ctx.producer_done, false);

    printf(" [*] Launching Gateway Producer and Matching Engine Core threads...\n");

    uint64_t benchmark_start_ns = get_time_ns();

    pthread_t prod_thread;
    if (pthread_create(&prod_thread, NULL, producer_worker, &ctx) != 0) {
        perror("Fatal: Failed to create producer thread");
        return 1;
    }

    /* Consumer: Matching Engine Processing Loop */
    uint32_t processed_count = 0;
    OrderEvent ev;

    while (processed_count < total_orders) {
        if (ring_buffer_pop(&rb, &ev)) {
            process_order_event(book, &pool, &ev);
            uint64_t complete_ns = get_time_ns();

            uint64_t turnaround_ns = (complete_ns >= ev.ingress_ts_ns) ? (complete_ns - ev.ingress_ts_ns) : 0;
            latencies_ns[processed_count++] = turnaround_ns;
        } else {
            if (atomic_load_explicit(&ctx.producer_done, memory_order_acquire) &&
                atomic_load_explicit(&rb.head, memory_order_relaxed) == atomic_load_explicit(&rb.tail, memory_order_relaxed)) {
                break;
            }
            __builtin_ia32_pause();
        }
    }

    pthread_join(prod_thread, NULL);
    uint64_t benchmark_end_ns = get_time_ns();
    double total_wall_time_sec = (double)(benchmark_end_ns - benchmark_start_ns) / 1e9;
    if (total_wall_time_sec <= 0.0) total_wall_time_sec = 0.000001;
    double throughput_ops = (double)processed_count / total_wall_time_sec;

    printf("[+] Processed %u order events in %.4f seconds (%.0f orders/sec)\n\n",
           processed_count, total_wall_time_sec, throughput_ops);

    /* Compute Tail Latencies */
    qsort(latencies_ns, processed_count, sizeof(uint64_t), compare_u64);

    double min_us   = (double)latencies_ns[0] / 1000.0;
    double p50_us   = (double)latencies_ns[(uint32_t)(processed_count * 0.50)] / 1000.0;
    double p90_us   = (double)latencies_ns[(uint32_t)(processed_count * 0.90)] / 1000.0;
    double p95_us   = (double)latencies_ns[(uint32_t)(processed_count * 0.95)] / 1000.0;
    double p99_us   = (double)latencies_ns[(uint32_t)(processed_count * 0.99)] / 1000.0;
    double p999_us  = (double)latencies_ns[(uint32_t)(processed_count * 0.999)] / 1000.0;
    double max_us   = (double)latencies_ns[processed_count - 1] / 1000.0;

    double sum_ns = 0.0;
    for (uint32_t i = 0; i < processed_count; i++) sum_ns += (double)latencies_ns[i];
    double avg_us = (sum_ns / processed_count) / 1000.0;

    printf("+-----------------------------------------------------------------------------------------+\n");
    printf("|                              ORDER TURNAROUND LATENCY STATS                             |\n");
    printf("+------------------------+----------+----------+----------+----------+----------+---------+\n");
    printf("| Metric                 | P50 (us) | P90 (us) | P95 (us) | P99 (us) | 99.9(us) | Max(us) |\n");
    printf("+------------------------+----------+----------+----------+----------+----------+---------+\n");
    printf("| Turnaround Latency     | %8.2f | %8.2f | %8.2f | %8.2f | %8.2f | %7.2f |\n",
           p50_us, p90_us, p95_us, p99_us, p999_us, max_us);
    printf("+------------------------+----------+----------+----------+----------+----------+---------+\n\n");

    printf("========================================================================\n");
    printf(" Execution Summary & Book Health:\n");
    printf("   Total Processed Orders:    %u\n", processed_count);
    printf("   Throughput:                %.2f orders/sec (%.2f ns/order)\n",
           throughput_ops, (total_wall_time_sec * 1e9) / (processed_count > 0 ? processed_count : 1));
    printf("   Total Executed Trades:     %lu\n", book->total_trades);
    printf("   Total Volume Traded:       %lu shares\n", book->total_volume_matched);
    printf("   Final Inside Market:       Best Bid $%u.%02u | Best Ask $%u.%02u\n",
           book->best_bid / 100, book->best_bid % 100, book->best_ask / 100, book->best_ask % 100);
    printf("   Mean Turnaround Latency:   %.2f us (Min: %.2f us)\n", avg_us, min_us);
    printf("   Tail Risk (P99 / P50):     %.2fx ratio\n", p99_us / (p50_us > 0.01 ? p50_us : 0.01));
    printf("========================================================================\n");

    if (json_path) {
        FILE *jf = fopen(json_path, "w");
        if (jf) {
            fprintf(jf, "{\n");
            fprintf(jf, "  \"total_orders\": %u,\n", processed_count);
            fprintf(jf, "  \"wall_time_sec\": %.4f,\n", total_wall_time_sec);
            fprintf(jf, "  \"throughput_ops\": %.2f,\n", throughput_ops);
            fprintf(jf, "  \"trades_executed\": %lu,\n", book->total_trades);
            fprintf(jf, "  \"volume_matched\": %lu,\n", book->total_volume_matched);
            fprintf(jf, "  \"latency_turnaround_us\": {\n");
            fprintf(jf, "    \"min\": %.2f,\n", min_us);
            fprintf(jf, "    \"avg\": %.2f,\n", avg_us);
            fprintf(jf, "    \"p50\": %.2f,\n", p50_us);
            fprintf(jf, "    \"p90\": %.2f,\n", p90_us);
            fprintf(jf, "    \"p95\": %.2f,\n", p95_us);
            fprintf(jf, "    \"p99\": %.2f,\n", p99_us);
            fprintf(jf, "    \"p999\": %.2f,\n", p999_us);
            fprintf(jf, "    \"max\": %.2f\n", max_us);
            fprintf(jf, "  }\n");
            fprintf(jf, "}\n");
            fclose(jf);
            printf("[+] JSON output exported to %s\n", json_path);
        }
    }

    ring_buffer_free(&rb);
    order_pool_free(&pool);
    free(book);
    free(latencies_ns);

    return 0;
}
