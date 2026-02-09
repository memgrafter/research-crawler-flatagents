# Decentralized Autoscaling Architecture Plan

This document outlines a plan for a decentralized and resilient autoscaling system based on self-registering worker machines. This architecture is composed of autonomous workers, a central registry for service discovery, and an ephemeral scaling utility.

---

### 1. Core Components

*   **Registration Backend:** A central database or cache (e.g., Redis, SQLite, or a simple REST API) that acts as a service registry. It will store a list of active workers, their unique IDs, status (`active`, `terminating`), and last heartbeat time.
*   **Work Queue:** A message queue (e.g., Redis List, RabbitMQ) that holds the list of papers or tasks to be processed. Workers pull tasks from this queue.
*   **Long-Running Worker Machine (`worker.yml`):** A modified `research-pipeline` machine designed to run indefinitely in a loop, processing one paper at a time. It is responsible for its own registration and graceful shutdown.
*   **Scaler Utility (`scaler.py`):** An ephemeral script or FlatMachine that is run on-demand to adjust the number of active workers in the fleet.

---

### 2. Worker Machine Logic (`worker.yml`)

The worker is designed as a persistent, looped process that polls for work and for termination signals.

1.  **Startup Hook (`on_machine_start`):**
    *   The machine generates a unique `worker_id`.
    *   It registers itself with the **Registration Backend** by creating an entry with its `worker_id` and setting its status to `active`.

2.  **Main Processing Loop (States):**
    *   **`get_work`:** Pull a paper ID from the **Work Queue**. If the queue is empty, transition to a `waiting` state for a defined period before trying again. If a paper is found, proceed to `analyze_paper`.
    *   **`analyze_paper`:** Execute the core single-paper analysis logic.
    *   **`check_status`:** After analysis is complete, the worker performs a "heartbeat" by calling the **Registration Backend** to check its own status.
        *   If its status is still `active`, it transitions back to the `get_work` state to pick up a new task.
        *   If an external process has changed its status to `terminating`, the machine transitions to a final `shutdown` state.

3.  **Shutdown Hook (`on_machine_end`):**
    *   When the machine enters its final `shutdown` state, this hook is triggered.
    *   It updates its entry in the **Registration Backend** to `terminated` or removes it completely.

---

### 3. Scaler Utility Logic (`scaler.py`)

The scaler is a stateless command-line tool that reconciles the fleet size with a desired target.

1.  **Input:** Takes a single command-line argument: `--target-workers <N>`.

2.  **Execution Steps:**
    *   Query the **Registration Backend** to get the `current_workers` count by fetching all workers with an `active` status.
    *   **Scale Up:** If `current_workers < target_workers`, launch `N` new instances of the `worker.yml` machine in a fire-and-forget manner, where `N` is the deficit. The new workers will handle their own registration.
    *   **Scale Down:** If `current_workers > target_workers`, select `N` surplus workers from the registry. For each surplus worker, update their status field in the registry to `terminating`. The active workers will detect this status change on their next loop and initiate their own graceful shutdown.
