# OpsPilot Indirect Prompt Injection Defense Report

- **Run UTC**: `2026-11-22T10:00:01.754042+00:00`
- **Gateway `/v1/scan` enabled**: `False`
- **Test Corpus**: `30` adversarial injected log lines + `50` clean operational log lines

| Defense Tier | Intercepted / 30 | Interception Rate | Unmitigated Exposure | Clean Log FPR |
|---|---:|---:|---:|---:|
| `none` | 0/30 | 0.0% | 100.0% | 0.0% |
| `delimiter` | 0/30 | 0.0% | 100.0% | 0.0% |
| `delimiter_and_scanner` | 30/30 | 100.0% | 0.0% | 0.0% |
