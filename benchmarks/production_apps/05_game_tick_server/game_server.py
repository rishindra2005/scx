#!/usr/bin/env python3
"""
Authoritative 120 FPS Multiplayer Game Simulation Server Benchmark
Simulates 500 connected clients, spatial grid collision, physics integration,
and tick pacing tracking P50/P95/P99 latency variance and frame drops.
"""

import sys
import time
import math
import random
import threading
import queue
import argparse
import json
from dataclasses import dataclass

TICK_RATE_HZ = 120
TICK_INTERVAL_NS = int(1_000_000_000 / TICK_RATE_HZ)  # 8,333,333 ns = 8.333 ms
DEFAULT_CLIENTS = 500
DEFAULT_DURATION_SEC = 30
GRID_SIZE = 64
CELL_SIZE = 10.0


@dataclass
class PlayerInput:
    player_id: int
    move_x: float
    move_y: float
    aim_yaw: float
    buttons: int
    client_seq: int
    timestamp_ns: int


@dataclass
class PlayerEntity:
    player_id: int
    pos_x: float
    pos_y: float
    pos_z: float
    vel_x: float
    vel_y: float
    vel_z: float
    health: int
    last_seq: int


class SpatialGrid:
    def __init__(self, size: int, cell_size: float):
        self.size = size
        self.cell_size = cell_size
        self.cells = [[] for _ in range(size * size)]

    def clear(self):
        for cell in self.cells:
            cell.clear()

    def get_cell_index(self, x: float, y: float) -> int:
        cx = int((x + 500.0) / self.cell_size) % self.size
        cy = int((y + 500.0) / self.cell_size) % self.size
        return cy * self.size + cx

    def insert(self, entity: PlayerEntity):
        idx = self.get_cell_index(entity.pos_x, entity.pos_y)
        self.cells[idx].append(entity)

    def query_radius(self, x: float, y: float, radius: float) -> int:
        idx = self.get_cell_index(x, y)
        nearby = self.cells[idx]
        collisions = 0
        r_sq = radius * radius
        for other in nearby:
            dx = other.pos_x - x
            dy = other.pos_y - y
            if (dx * dx + dy * dy) < r_sq:
                collisions += 1
        return collisions


class GameSimulationServer:
    def __init__(self, num_clients: int, tick_rate: int, duration_sec: int, json_path: str = None):
        self.num_clients = num_clients
        self.tick_rate = tick_rate
        self.tick_interval_ns = int(1_000_000_000 / tick_rate)
        self.tick_deadline_ms = self.tick_interval_ns / 1_000_000.0
        self.total_ticks = tick_rate * duration_sec
        self.duration_sec = duration_sec
        self.json_path = json_path

        self.input_queue = queue.Queue(maxsize=100_000)
        self.stop_event = threading.Event()

        # Initialize player entities
        self.entities = {}
        for pid in range(self.num_clients):
            self.entities[pid] = PlayerEntity(
                player_id=pid,
                pos_x=random.uniform(-400.0, 400.0),
                pos_y=random.uniform(-400.0, 400.0),
                pos_z=0.0,
                vel_x=0.0,
                vel_y=0.0,
                vel_z=0.0,
                health=100,
                last_seq=0
            )

        self.spatial_grid = SpatialGrid(GRID_SIZE, CELL_SIZE)

    def client_traffic_worker(self):
        """Simulates network thread receiving packets from connected clients at 120 Hz."""
        seq_counters = [0] * self.num_clients
        packet_interval_s = 1.0 / self.tick_rate

        while not self.stop_event.is_set():
            t_start = time.perf_counter()
            now_ns = time.perf_counter_ns()

            # Push a packet batch for random active clients
            for pid in range(self.num_clients):
                seq_counters[pid] += 1
                inp = PlayerInput(
                    player_id=pid,
                    move_x=math.cos(seq_counters[pid] * 0.05),
                    move_y=math.sin(seq_counters[pid] * 0.05),
                    aim_yaw=(seq_counters[pid] % 360) * 1.0,
                    buttons=1 if (seq_counters[pid] % 30 == 0) else 0,
                    client_seq=seq_counters[pid],
                    timestamp_ns=now_ns
                )
                try:
                    self.input_queue.put_nowait(inp)
                except queue.Full:
                    break

            elapsed = time.perf_counter() - t_start
            sleep_time = packet_interval_s - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def run_simulation(self):
        print("========================================================================")
        print(" Authoritative 120 FPS Multiplayer Game Simulation Server")
        print(" Real-world Esports Server Engine (Valorant 128-tick / CS2 Subtick Model)")
        print("========================================================================")
        print(" Engine Configuration:")
        print(f"   Target Tick Rate:       {self.tick_rate} Hz ({self.tick_deadline_ms:.3f} ms / {self.tick_interval_ns} ns)")
        print(f"   Connected Clients:      {self.num_clients} concurrent player sessions")
        print(f"   Duration:               {self.duration_sec} seconds ({self.total_ticks} authoritative ticks)")
        print(f"   Spatial Hash Grid:      {GRID_SIZE}x{GRID_SIZE} cells (Cell Size: {CELL_SIZE}m)")
        print("========================================================================\n")

        # Start network client worker thread
        client_thread = threading.Thread(target=self.client_traffic_worker, daemon=True)
        client_thread.start()

        # Allow client inputs to queue up
        time.sleep(0.05)

        tick_durations_ns = []
        tick_intervals_ns = []
        deadline_misses = 0
        consecutive_misses = 0
        max_consecutive_misses = 0

        dt = 1.0 / self.tick_rate
        drag_coeff = 0.92

        next_tick_ns = time.perf_counter_ns() + self.tick_interval_ns
        prev_tick_start_ns = time.perf_counter_ns()

        print(f"[*] Running authoritative simulation loop for {self.total_ticks} ticks...")

        for tick in range(self.total_ticks):
            # Precision tick boundary synchronization
            now_ns = time.perf_counter_ns()
            sleep_ns = next_tick_ns - now_ns
            if sleep_ns > 1_500_000:
                time.sleep((sleep_ns - 1_000_000) / 1e9)
            while time.perf_counter_ns() < next_tick_ns:
                pass  # Spin wait for sub-millisecond precision

            tick_start_ns = time.perf_counter_ns()
            interval_ns = tick_start_ns - prev_tick_start_ns
            prev_tick_start_ns = tick_start_ns

            if tick > 0:
                tick_intervals_ns.append(interval_ns)

            # 1. Drain client input buffer
            inputs_processed = 0
            while not self.input_queue.empty():
                try:
                    inp = self.input_queue.get_nowait()
                    ent = self.entities.get(inp.player_id)
                    if ent and inp.client_seq > ent.last_seq:
                        ent.last_seq = inp.client_seq
                        ent.vel_x += inp.move_x * 2.5
                        ent.vel_y += inp.move_y * 2.5
                    inputs_processed += 1
                    if inputs_processed >= self.num_clients * 2:
                        break
                except queue.Empty:
                    break

            # 2. Authoritative Physics Integration & Kinematics
            self.spatial_grid.clear()
            for ent in self.entities.values():
                # Apply velocity and drag
                ent.pos_x += ent.vel_x * dt
                ent.pos_y += ent.vel_y * dt
                ent.vel_x *= drag_coeff
                ent.vel_y *= drag_coeff

                # Boundary bounce
                if ent.pos_x < -450.0 or ent.pos_x > 450.0:
                    ent.vel_x *= -1.0
                if ent.pos_y < -450.0 or ent.pos_y > 450.0:
                    ent.vel_y *= -1.0

                # Insert into spatial partitioning structure
                self.spatial_grid.insert(ent)

            # 3. Collision Resolution & Area of Interest Broadphase
            total_collisions = 0
            for ent in self.entities.values():
                total_collisions += self.spatial_grid.query_radius(ent.pos_x, ent.pos_y, 2.5)

            # 4. State Delta Serialization & Snapshotting
            # Simulates packing snapshot delta into packet payload
            snapshot_bytes = len(self.entities) * 24

            tick_end_ns = time.perf_counter_ns()
            exec_duration_ns = tick_end_ns - tick_start_ns
            tick_durations_ns.append(exec_duration_ns)

            # Check deadline miss (>8.333 ms execution or interval spike)
            if exec_duration_ns > self.tick_interval_ns or interval_ns > (self.tick_interval_ns * 1.25):
                deadline_misses += 1
                consecutive_misses += 1
                if consecutive_misses > max_consecutive_misses:
                    max_consecutive_misses = consecutive_misses
            else:
                consecutive_misses = 0

            next_tick_ns += self.tick_interval_ns
            if time.perf_counter_ns() > next_tick_ns:
                next_tick_ns = time.perf_counter_ns() + self.tick_interval_ns

        self.stop_event.set()
        client_thread.join(timeout=1.0)

        # Statistics & Percentiles
        tick_durations_ns.sort()
        tick_intervals_ns.sort()

        n_dur = len(tick_durations_ns)
        n_int = len(tick_intervals_ns)

        def pct(arr, p):
            return arr[int(len(arr) * p)] / 1000.0  # to microseconds

        dur_p50 = pct(tick_durations_ns, 0.50)
        dur_p90 = pct(tick_durations_ns, 0.90)
        dur_p95 = pct(tick_durations_ns, 0.95)
        dur_p99 = pct(tick_durations_ns, 0.99)
        dur_max = tick_durations_ns[-1] / 1000.0
        dur_avg = (sum(tick_durations_ns) / n_dur) / 1000.0

        int_p50 = pct(tick_intervals_ns, 0.50)
        int_p90 = pct(tick_intervals_ns, 0.90)
        int_p95 = pct(tick_intervals_ns, 0.95)
        int_p99 = pct(tick_intervals_ns, 0.99)
        int_max = tick_intervals_ns[-1] / 1000.0
        int_avg = (sum(tick_intervals_ns) / n_int) / 1000.0

        # Calculate interval jitter variance (|interval - 8.333ms|)
        target_us = self.tick_deadline_ms * 1000.0
        jitters_us = [abs((val / 1000.0) - target_us) for val in tick_intervals_ns]
        jitters_us.sort()
        jitter_p50 = jitters_us[int(len(jitters_us) * 0.50)]
        jitter_p95 = jitters_us[int(len(jitters_us) * 0.95)]
        jitter_p99 = jitters_us[int(len(jitters_us) * 0.99)]
        jitter_max = jitters_us[-1]

        drop_rate = (deadline_misses / self.total_ticks) * 100.0

        print("[+] Simulation complete. Pacing and tick analysis:")
        print("+------------------------------------------------------------------------------------------------+")
        print("|                              TICK SIMULATION PERFORMANCE STATS                                 |")
        print("+---------------------------+----------+----------+----------+----------+----------+-------------+")
        print("| Metric                    | P50 (us) | P90 (us) | P95 (us) | P99 (us) | Max (us) | Target (us) |")
        print("+---------------------------+----------+----------+----------+----------+----------+-------------+")
        print(f"| Tick Execution Duration   | {dur_p50:8.2f} | {dur_p90:8.2f} | {dur_p95:8.2f} | {dur_p99:8.2f} | {dur_max:8.2f} | {target_us:11.2f} |")
        print(f"| Tick Interval Pacing      | {int_p50:8.2f} | {int_p90:8.2f} | {int_p95:8.2f} | {int_p99:8.2f} | {int_max:8.2f} | {target_us:11.2f} |")
        print(f"| Tick-to-Tick Jitter       | {jitter_p50:8.2f} | {jitters_us[int(len(jitters_us)*0.90)]:8.2f} | {jitter_p95:8.2f} | {jitter_p99:8.2f} | {jitter_max:8.2f} |        0.00 |")
        print("+---------------------------+----------+----------+----------+----------+----------+-------------+\n")

        print("========================================================================")
        print(" Game Simulation Pacing & Tick Health Scorecard:")
        print(f"   Total Authoritative Ticks: {self.total_ticks}")
        print(f"   Target Tick Interval:      {self.tick_deadline_ms:.3f} ms ({target_us:.1f} us)")
        print(f"   Frame Drops / Misses:      {deadline_misses} ({drop_rate:.3f}% drop rate)")
        print(f"   Max Consecutive Drops:     {max_consecutive_misses}")
        print(f"   Mean Execution Duration:   {dur_avg:.2f} us ({dur_avg / target_us * 100:.1f}% frame budget)")
        print(f"   P99 Tick Execution:        {dur_p99:.2f} us")
        print(f"   P99 Tick Jitter:           {jitter_p99:.2f} us")
        if deadline_misses == 0 and jitter_p99 < 500.0:
            print("   Esports Tick Grade:        FLAWLESS (Tournament-grade 120 FPS consistency)")
        elif drop_rate < 1.0:
            print("   Esports Tick Grade:        GOOD (Minor transient hitching)")
        else:
            print("   Esports Tick Grade:        UNACCEPTABLE (Severe rubber-banding and desync)")
        print("========================================================================")

        if self.json_path:
            out_data = {
                "tick_rate_hz": self.tick_rate,
                "tick_interval_ms": self.tick_deadline_ms,
                "num_clients": self.num_clients,
                "total_ticks": self.total_ticks,
                "frame_drops": deadline_misses,
                "frame_drop_pct": drop_rate,
                "max_consecutive_drops": max_consecutive_misses,
                "tick_duration_us": {
                    "avg": dur_avg,
                    "p50": dur_p50,
                    "p90": dur_p90,
                    "p95": dur_p95,
                    "p99": dur_p99,
                    "max": dur_max
                },
                "tick_interval_us": {
                    "avg": int_avg,
                    "p50": int_p50,
                    "p90": int_p90,
                    "p95": int_p95,
                    "p99": int_p99,
                    "max": int_max
                },
                "tick_jitter_us": {
                    "p50": jitter_p50,
                    "p95": jitter_p95,
                    "p99": jitter_p99,
                    "max": jitter_max
                }
            }
            with open(self.json_path, "w") as f:
                json.dump(out_data, f, indent=2)
            print(f"[+] Exported JSON telemetry to {self.json_path}")


def main():
    parser = argparse.ArgumentParser(description="120 FPS Multiplayer Game Simulation Server")
    parser.add_argument("-c", "--clients", type=int, default=DEFAULT_CLIENTS, help=f"Concurrent connected clients (default: {DEFAULT_CLIENTS})")
    parser.add_argument("-r", "--tick-rate", type=int, default=TICK_RATE_HZ, help=f"Server tick rate in Hz (default: {TICK_RATE_HZ})")
    parser.add_argument("-d", "--duration", type=int, default=DEFAULT_DURATION_SEC, help=f"Simulation duration in seconds (default: {DEFAULT_DURATION_SEC})")
    parser.add_argument("-j", "--json", type=str, default=None, help="Save metrics to JSON file")

    args = parser.parse_args()
    server = GameSimulationServer(
        num_clients=args.clients,
        tick_rate=args.tick_rate,
        duration_sec=args.duration,
        json_path=args.json
    )
    server.run_simulation()


if __name__ == "__main__":
    main()
