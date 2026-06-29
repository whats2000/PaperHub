# Launcher for the per-stage prompt-eval CLI (SRS §III-9). In-process — no live
# backend needed; needs an LLM API key in backend/.env. Examples:
#   scripts/run-eval.ps1 run --stage router --version v1 --corpus benchmark/agent/corpus/router.core.jsonl --model gemini/gemini-2.5-flash
#   scripts/run-eval.ps1 list --stage router
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $EvalArgs)
uv run python -m benchmark.agent.cli @EvalArgs
