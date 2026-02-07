# OpenRouter Free Worker Sizing (v2)

This note is for choosing `-w/--workers` when both models are on OpenRouter free tier.

## Constraint

OpenRouter free requests are shared across both models in this pipeline:
- `openrouter/openai/gpt-oss-120b:free`
- `openrouter/openrouter/pony-alpha`

Assume a **combined cap of 20 RPM** total free requests.

## Calls per paper (current machine)

Baseline PASS path = **7 calls/paper**:
- gpt-oss: 4 calls (`key_outcome`, `limits_confidence`, `report_assembler`, `judge`)
- pony-alpha: 3 calls (`why`, `reproduction`, `open_questions`)

If repair triggers once, total becomes **9 calls/paper** (repair + re-judge).

## Observed runtime (recent v2 logs)

Recent full-format runs were mostly in the ~4–6 minute range, with slower outliers:
- `single_worker_20260206_213826.log` → 4.32 min
- `single_worker_20260206_214336.log` → 4.63 min
- `single_worker_20260206_215309.log` → 5.27 min
- `single_worker_20260206_220302.log` → 8.75 min (outlier)

Planning range used below: **5–9 min/paper**.

## Throughput math

Per-worker RPM:

- Baseline: `7 / minutes_per_paper`
- Repair-heavy: `9 / minutes_per_paper`

So at 5–9 min/paper:
- Baseline per-worker RPM: **1.4 .. 0.78**
- Repair per-worker RPM: **1.8 .. 1.0**

Total RPM estimate:

`total_rpm ~= workers * per_worker_rpm`

## Worker limits under 20 RPM cap

- Baseline fast case (5 min): `floor(20 / 1.4) = 14`
- Repair-heavy fast case (5 min): `floor(20 / 1.8) = 11`

## Recommended operating range

For free-tier stability and headroom:
- **Start: 5 workers**
- **Normal: 6–8 workers**
- **Upper practical limit: ~10 workers** (expect higher 429 risk)

If you see sustained 429s, step down by 1–2 workers.
