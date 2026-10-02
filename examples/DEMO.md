# datapipe demo (about 5 minutes)

    pip install duckdb
    python examples/demo.py --pause          # presenting: waits for Enter between steps
    python examples/demo.py                  # or straight through (about 10 s)
    python examples/demo.py --review         # then opens the browser review UI

The script uses a temporary folder; nothing in the repository changes. It exits 1 if any step behaves differently from what is described below.

## Talk track

| # | Show | Say |
|---|---|---|
| 1 | `map` on a file with renamed columns | The matcher only *suggests*. Offline here, so nothing left the machine. Every suggestion is checked against the real values. |
| 2 | `approve-mapping` by a second person | One column was only 70% sure, so a human must accept it knowingly. The proposal is hash-sealed. |
| 3 | `run` with the approved schema | Same numbers as the original file. |
| 4 | **Regulated + dirty file: BLOCKED** | This is the point of the tool: 8 of 10 rows are bad, so it refuses and never repairs silently. |
| 5-7 | Sign-off: self-sign refused, second person accepted | Four-eyes is enforced by the tool. |
| 8-9 | `verify-audit`, then an edit to the log | Tamper-evident: one changed word is detected. |
| 10 | `--review`: the browser page | Reviewer sees evidence, exactly what was sent to the model, and decides per column. Try a manual remap. |

## Say honestly if asked

- Identity is self-asserted (`--actor`, `--reviewer`). A product needs real authentication.
- The audit log detects edits but not removal of its *tail*; store the printed head hash elsewhere.
- The Anthropic and local-model providers were tested against fake servers only.
- No independent security review. Prototype for a trusted machine.

## Using a local model in the demo (optional)

    ollama pull llama3.1
    python -m datapipe map examples/sales_renamed.csv --schema examples/schema_sales.json \
        --provider openai-compat --model llama3.1 --dry-run     # shows exactly what would be sent
