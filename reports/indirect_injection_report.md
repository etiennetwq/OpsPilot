# OpsPilot Independent Indirect Prompt Injection Report

- **Run UTC**: `2026-11-22T13:05:14.793978+00:00`
- **Attack Source**: Public `Lakera/gandalf_ignore_instructions` + `deepset/prompt-injections` embedded in log templates (`n=60`)
- **Clean Source**: Real BGL operational log lines (`n=60`)
- **Live Gateway `/v1/scan`**: `http://127.0.0.1:8000`

| Defense Mode | Intercepted | Attack Recall | Clean Log False Alarms | Clean Log FPR |
|---|---:|---:|---:|---:|
| `none` | 0/60 | 0.0% | 0/60 | 0.0% |
| `local_regex` | 11/60 | 18.3% | 0/60 | 0.0% |
| `gateway_cascade` | 51/60 | 85.0% | 2/60 | 3.3% |
