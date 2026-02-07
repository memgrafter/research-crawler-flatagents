# OpenRouter Free Tier Budget Planning (v2)

## Constraint

OpenRouter free requests are shared across both models:
- `openrouter/openai/gpt-oss-120b:free` (cheap/structured)
- `openrouter/openrouter/pony-alpha` (expensive/reasoning)

**Daily budget: ~1000 requests.** RPM cap: ~20.

## Calls per paper (two-machine split)

### Prep machine (1 cheap request)
| Agent | Model | Requests |
|---|---|---|
| key_outcome_writer | cheap | 1 |
| **Total** | | **1** |

### Analysis machine (6–8 requests)
| Agent | Model | Requests |
|---|---|---|
| why_hypothesis_writer | expensive | 1 |
| reproduction_writer | expensive | 1 |
| limits_confidence_writer | cheap | 1 |
| open_questions_writer | expensive | 1 |
| report_assembler | cheap | 1 |
| completeness_judge | cheap | 1 |
| targeted_repair (if needed) | cheap | 0–1 |
| re-judge (if repaired) | cheap | 0–1 |
| **Total** | | **6–8** |

### Combined: 7–9 requests/paper

## Daily capacity at 1000 req budget

### Scenario A: Buffer already full (steady state)
All budget goes to analysis:
- 1000 ÷ 7 ≈ **142 papers/day** (best case)
- 1000 ÷ 9 ≈ **111 papers/day** (repair-heavy)

### Scenario B: Cold start (need to build buffer)
- Prep 40 papers: 40 cheap requests
- Remaining: 960 ÷ 7 ≈ **137 papers analyzed**
- End of day: prep more with leftover budget

### Scenario C: Expensive model intermittent
- Prep runs whenever cheap model is available (high concurrency)
- Analysis runs in bursts when expensive model responds
- Budget split depends on expensive model uptime

## Worker sizing (RPM-limited)

With 20 RPM shared cap and ~5 min/paper for analysis:

| Workers | Est. RPM | Risk |
|---|---|---|
| 3 | ~4 | Safe |
| 5 | ~7 | Comfortable |
| 8 | ~11 | Normal |
| 10 | ~14 | Upper limit |

For prep-only runs (1 req/paper, fast): can safely use 10+ workers since
each prep only makes 1 API call.

## Recommended operating modes

```bash
# Normal operation: balanced prep + analysis
python run.py --workers 5 --daemon

# Fill buffer (cheap, high concurrency)
python run.py --workers 10 --prep-only --daemon

# Conservative (expensive model flaky)
python run.py --workers 3 --daemon

# End-of-day budget burn for tomorrow's buffer
python run.py --workers 10 --prep-only --budget 200
```

## Budget tracking

The runner tracks daily usage in `data/v2_executions.sqlite` (`daily_usage` table).
Currently uses fixed estimates per machine run. Check actual usage:

```sql
SELECT * FROM daily_usage ORDER BY date DESC LIMIT 5;
```
