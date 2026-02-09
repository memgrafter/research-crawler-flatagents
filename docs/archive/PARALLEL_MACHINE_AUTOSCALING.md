# Parallel Machine Autoscaling Plan


## 645 AM Idea Trent


 I don't think this is a good architecture. It seems like it may be better to register the machines when they launch, and mark them unregistered when they terminate.  The machines can self register into the registration backend (which is optional).  So that's a new backend if we don't have on in flatagents-runtimes.d.ts.  We can then use more of a launcher script in main to launch all the machines, or a launcher flatmachine if there is value in using a flatmachine. Either way it may just be a for loop, but we let the launcher machine terminate, so then just the scaled state machines are left.  They are all in the registry working away in parallel, basically each one is what we have now, a sequential worker going over one paper at a time.  If we want to scale, we need to send a message to the system, which will launch a scaling machine, could be same as the initial machine... or we could suspend the initial machine although we need to clean it up then.


## 6 AM Gemini 2.5 Pro

This document outlines a plan for an orchestration system that manages parallel analysis machines and allows for graceful scaling based on a target parallelism.

The system has two main components:
1.  **The Worker Machine**: A configurable FlatMachine that runs the core analysis.
2.  **The Orchestrator App**: A Python application that manages a fleet of Worker Machines.

---

### Part 1: Create a Configurable Worker Machine

The goal is to make the `research-pipeline` machine's internal parallelism adjustable for each run via an input parameter.

1.  **Modify `machine.yml`:**
    *   **Add Input Parameter:** Add `parallelism: "{{ input.parallelism | default(1) }}"` to the machine's `context` to accept a parallelism value at runtime.
    *   **Parallelize Analysis State:** Convert the `analyze` state to use a `foreach` loop. This loop will iterate based on the `input.parallelism` value, running multiple `analyzer` sub-machines concurrently.
    *   **Add Synthesizer State:** Create a new `synthesize` state immediately following the parallel `analyze` state. This state will use a new agent to merge the multiple analysis results into a single, coherent result before the workflow proceeds to the `refine` step.

---

### Part 2: Build a Python Orchestrator Application

The goal is to manage a fleet of worker machines to meet a target total parallelism, gracefully draining old workers and gradually launching new ones as the target changes.

1.  **Define Core State Variables:** The orchestrator application will manage:
    *   **`target_parallelism`**: The desired total number of concurrent analysis tasks (e.g., 20). This value should be externally configurable (e.g., via a config file or API).
    *   **`active_workers`**: A list or dictionary of currently running worker processes or threads, tracking their state and current parallelism.
    *   **`work_queue`**: A thread-safe queue holding the upcoming papers or tasks to be analyzed.

2.  **Implement the Reconciliation Loop:** The orchestrator's main logic is a continuous loop that reconciles the current state with the target state.
    *   **Calculate Current Parallelism:** Sum the `parallelism` of all `active_workers` at the start of each loop.
    *   **Compare to Target:** Check if the current total parallelism matches the `target_parallelism`.
    *   **Scale Down (Drain):** If the current value is higher than the target, mark surplus workers for a graceful shutdown. These workers will finish their current task, exit cleanly, and will not be replaced. This constitutes the "draining" process.
    *   **Scale Up (Launch):** If the current value is lower than the target, calculate the deficit. Launch one or more new worker instances with the appropriate `parallelism` input value to meet the target. This provides a "gradual launch" to scale up the fleet.


