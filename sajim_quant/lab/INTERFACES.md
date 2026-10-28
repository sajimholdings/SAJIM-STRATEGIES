# Strategy Lab — P1 frozen interfaces

Version `p1.2` (`sajim_quant/lab/interfaces.py: INTERFACES_VERSION`). Written 2026-10-12;
p1.2 changes 2026-10-13.

**What changed in p1.2** (P1 part 2, protocol gaps from the part-1 review):
- §5.2 **adaptive causality cuts**: up to 6 more truncation cuts placed 1..3 bars after bars
  where `full1` itself emitted an event, run as the second sandbox job (§8). A check is now 2
  jobs, DEV is still 2 jobs, a holdout is 2 jobs. Holdout reasons never name a bar index.
- §2.1 **one per-run limit**: `PER_RUN_TIMEOUT_S` is 14 s (derived), not 60 s, in every job;
  a kill because the job budget ran out is kind `budget`, distinct from a per-run `timeout`.
  Neighbours stopped for time make G7 *not computable* with a runtime reason (§6.3).
- Every anchored id pattern is used with `re.fullmatch`; forbidden labels are refused in
  lab ids, families, manifest names and PARAMS names (§3.1); `http://` API URLs only for
  loopback (§10); the simulator clamps MFE on an SL bar (§3.3); the holdout failure path
  copies `dev_hard_gates_passed` (§6.5); `put_record` warns on a differing collision (§7.3);
  run.py records "spec changed since approval" (§11).

Every P1 builder codes against this file. Authority order when sources disagree:
`docs/STRATEGY_LAB_PLAN.md` "P0 results" and "Decisions" > `CONTRACT.md` > `gates.py` /
`splits.py` > this file > older plan text. If this file is wrong, **say so in your
report**. Do not quietly diverge from it.

Builders run in parallel and cannot see each other's work. Three rules follow from that:

1. **Shared types live in `sajim_quant/lab/interfaces.py`**, which already exists and is
   frozen. Import dataclasses, protocols, limits, key helpers and error classes from
   there. Never import them from another builder's module, and never edit
   `interfaces.py`. A change goes through the integrator and bumps `INTERFACES_VERSION`.
2. **Tests import only your own modules, `interfaces.py` and pre-existing code.** Where you
   need another builder's module, use a fake built from the signatures below. For an
   end-to-end test, use `pytest.importorskip("sajim_quant.lab.<other>")` so the test
   skips until that module lands; the integrator makes sure it runs.
3. **Cross-builder imports in library code go inside functions**, so your module and its
   tests still import before the other module exists. The one exception is
   `evaluate.py`, which is integration code.

Global rules:
- Run Python as `python -X utf8`.
- Use logging, not print, in library code. `cli.py` and `run.py` print to stdout.
- Write dataclasses with type hints and short docstrings.
- Unit tests use synthetic OHLC only. Never load `data/**`.
- Never use `adapters/python/backtrader_adapter.py`, because it charges zero cost.
- Windows are half-open naive UTC. Slice data only through `splits.loader_bounds()` and
  `splits.window_mask()`.

---

## 1. Module map and ownership

| Path | Owner | One-line job |
|---|---|---|
| `sajim_quant/lab/interfaces.py` | interface author (frozen) | shared types/limits/keys/errors |
| `sajim_quant/lab/contract.py` | **A engine** | AST lint, PARAMS parsing, manifest validation, lint CLI |
| `sajim_quant/lab/indicators.py` | **A engine** | causal indicators. Self-contained (numpy/pandas/math/typing only) because it is vendored into the sandbox |
| `sajim_quant/lab/simulator.py` | **A engine** | `SignalSimulator`, `to_backtest_result` |
| `sajim_quant/lab/sandbox_io.py` | **B sandbox** | trusted encode (frame→parquet, plan.json), strict non-pickle decode + validation, `events_equal` |
| `sajim_quant/lab/sandbox_runner.py` | **B sandbox** | the script run INSIDE the sandbox: stdlib + numpy + pandas + pyarrow only, **no `sajim_quant` import** |
| `sajim_quant/lab/sandbox_local.py` | **B sandbox** | `LocalSubprocessExecutor` (tests/dev; NOT a security boundary) |
| `sajim_quant/lab/causality.py` | **B sandbox** | integrity plan (cuts, scramble, determinism) + `assess_check` |
| `sajim_quant/lab/robustness.py` | **C judging** | metrics behind every gate rule: performance, stress, folds, null, plateau, holdout |
| `sajim_quant/lab/scorecard.py` | **C judging** | `evaluate_gates`, `verdict`, scorecard JSON build/validate, `builder_view` |
| `sajim_quant/lab/ledger.py` | **C judging** | records, `LocalDirStore`, budget slot claims |
| `sajim_quant/lab/evaluate.py` | **C judging** | trusted orchestration of one check / dev / holdout evaluation |
| `sajim_quant/lab/cli.py` | **D laptop** | `lab.cli` subcommands + `LabClient` HTTP client |
| `sajim_quant/lab/run.py` | **D laptop** | orchestrator: claude sessions, approval, holdout, noise, critic, README |
| `.claude/skills/lab-build/**`, `.claude/skills/lab-review/**`, `.claude/agents/strategy-critic.md`, `.claude/lab/settings.json`, `.claude/lab/hooks/*.py`, the `## Strategy Lab` section of `CLAUDE.md` | **E claude layer** | skills, critic agent, lab-only settings and hooks |
| `sajim_quant/lab/sandbox_modal.py`, `sajim_quant/lab/store_modal.py`, `/lab/*` routes + `lab_job` function in `sajim_quant/cloud/modal_runner.py` | **integrator (later)** | prod executor, Modal store, HTTP API |

Tests live in `tests/lab/test_<module>.py`, one file or more per owned module:
- A: `test_contract.py`, `test_indicators.py`, `test_simulator.py`
- B: `test_sandbox_io.py`, `test_sandbox_runner.py`, `test_causality.py`
- C: `test_robustness.py`, `test_scorecard.py`, `test_ledger.py`, `test_evaluate.py`
- D: `test_cli.py`, `test_run.py`
- E: `test_hooks.py`, `test_claude_layer.py`

`test_interfaces.py` belongs to the interface author. Before reporting, every builder runs
the whole suite with `python -X utf8 -m pytest tests/lab -q`.

`core/instruments.py` is **not** edited in P1 (see 3.4). Nobody edits `sim/**`, `data/**`,
the existing `cloud/*.py`, or `~/.claude`.

---

## 2. Sandbox: executor protocol, file layout, limits, decoder

### 2.1 Types (in `interfaces.py`)

```python
@dataclass(frozen=True) class ScrambleSpec: start: int; seed: int            # rows [start, n) replaced, start >= 1
@dataclass(frozen=True) class SandboxRun:  run_id: str; params: Mapping[str, int|float]; cut: int|None = None; scramble: ScrambleSpec|None = None
@dataclass(frozen=True) class SandboxJob:  frame_parquet: bytes; n_bars: int; strategy_py: bytes; indicators_py: bytes;
                                           runs: tuple[SandboxRun, ...]; allow_short: bool;
                                           per_run_timeout_s: float = PER_RUN_TIMEOUT_S (14.0); job_timeout_s: float = 300.0; label: str = ""
@dataclass(frozen=True) class SandboxResult: files: Mapping[str, bytes]; exit_code: int|None; timed_out: bool; wall_s: float; stderr_tail: str = ""
class SandboxExecutor(Protocol):            def run(self, job: SandboxJob) -> SandboxResult
@dataclass(frozen=True) class RunError:    run_id: str|None; kind: str; message: str
@dataclass(frozen=True) class DecodedJob:  events: Mapping[str, np.ndarray]; errors: tuple[RunError, ...]; timings; versions; sensitive_env_names
                                           .ok(run_id) -> bool ; .job_ok -> bool
```

`run_id` fully matches `[a-z0-9_]{1,32}` (checked with `re.fullmatch`, so a trailing newline
never passes). A run has a cut or a scramble, never both. The run ids that are frozen:
- check runs: `cut20 cut40 cut60 cut80 cut95 scramble full1 full2`, in that order (§5.2);
- adaptive cuts: `acut1`, `acut2`, … in ascending cut order (§5.2, p1.2);
- plateau neighbours: `nb00`, `nb01`, … (§6.3).

Limits (`interfaces.py`):

| Constant | Value |
|---|---|
| `MAX_RUNS_PER_JOB` | 16 |
| `MAX_FRAME_BARS` / `MAX_FRAME_BYTES` | 400 000 / 32 MiB |
| `MAX_EVENTS_FILE_BYTES` | 8 MiB per run |
| `MAX_STATUS_BYTES` | 256 KiB |
| `MAX_TOTAL_OUT_BYTES` | 64 MiB |
| `MAX_ERRORS` | 64 |
| `MAX_ERROR_MESSAGE_CHARS` | 2000 |
| `JOB_TIMEOUT_S` | `gates.LAB_SIGNALS_TIMEOUT_S` (300) |
| `RUNNER_BUDGET_MARGIN_S` | 45 (the runner stops starting runs at `job_timeout_s − 45`) |
| `JOB_BUDGET_S` | `JOB_TIMEOUT_S − RUNNER_BUDGET_MARGIN_S` (255) |
| `RUNNER_PER_RUN_OVERHEAD_S` | 1.0 (parent work per run outside the child's timeout) |
| `CHILD_STARTUP_ALLOWANCE_S` | 3.0 (child start-up, inside the per-run timeout) |
| `PER_RUN_TIMEOUT_S` | `min(MAX_SIGNAL_RUNTIME_S, floor(JOB_BUDGET_S / MAX_RUNS_PER_JOB − RUNNER_PER_RUN_OVERHEAD_S))` = **14** |
| `RUN_TIMEOUT_KINDS` | `("timeout", "budget")` (a run stopped for time) |

**Runtime budget (p1.2).** p1.1 let each run take `MAX_SIGNAL_RUNTIME_S` = 60 s, but a job
must start all of its runs inside `JOB_BUDGET_S` = 255 s, and a job holds up to 16 runs, each
with about 1-3 s of child start-up (python, numpy/pandas import, frame read). The real
per-call limit was therefore about 40 s in the check job and about 23 s in the neighbour job,
and runs past the budget failed as `budget` without telling the builder why. p1.2 uses **one**
per-run limit in **every** job: `PER_RUN_TIMEOUT_S` = 14 s, the fair share of the largest job
(16 × (14 + 1) = 240 ≤ 255), so a job whose runs all use their whole limit still starts every
run and a budget kill cannot happen in normal operation. The limit includes child start-up,
so `generate_signals` itself gets about 11 s on the full frame (DEV M5 is about 105k bars;
vectorised numpy/pandas code needs well under 1 s). `gates.MAX_SIGNAL_RUNTIME_S` (60) stays
CONTRACT's outer cap; the sandbox enforces the stricter 14 s, and the same limit applies to
the free check, so a check pass predicts that DEV will not fail for time. Local tests that run
several jobs at once on a laptop may pass a larger `per_run_timeout_s` to `SandboxJob`; the
trusted evaluation code never does.

### 2.2 In-sandbox file layout

The root is `/tmp` on Modal, or a fresh temp dir for the local executor.

```
<root>/in/frame.parquet        trusted: the stage frame (below)
<root>/in/strategy.py          the exact submitted bytes
<root>/in/indicators.py        exact bytes of the TRUSTED sajim_quant/lab/indicators.py (never from the request)
<root>/in/plan.json            the run plan (below)
<root>/in/sandbox_runner.py    exact bytes of the trusted sajim_quant/lab/sandbox_runner.py
<root>/work/                   empty, writable scratch (per-run frames)
<root>/out/                    empty; runner writes <run_id>.events.npy and status.json
```

`sandbox_io.encode_job(job) -> dict[str, bytes]` returns exactly these five keys:
`"in/frame.parquet"`, `"in/strategy.py"`, `"in/indicators.py"`, `"in/plan.json"`,
`"in/sandbox_runner.py"`. The executor writes them and creates the empty `work/` and `out/`
directories. It then runs exactly
`interfaces.runner_argv(root)` = `python -I -X utf8 <root>/in/sandbox_runner.py <root>/in <root>/out`.

**frame.parquet** comes from `sandbox_io.frame_to_parquet(frame)`:
- `frame.reset_index()` with columns exactly `time, open, high, low, close, volume`;
- `time` is pyarrow `timestamp("us")` with no tz; OHLCV are `float64`;
- rows ascending and unique, written with `preserve_index=False`;
- `len == job.n_bars`.

The runner rebuilds the DataFrame the strategy receives:
- a `DatetimeIndex` named `time`, converted to `datetime64[ns]`, naive UTC;
- columns `open, high, low, close, volume` as float64.

That matches CONTRACT.md §3.

**plan.json** (`interfaces.PLAN_SCHEMA`):
```json
{"schema": "sajim.lab.sandbox_plan/1", "n_bars": 104211, "allow_short": false,
 "per_run_timeout_s": 14, "job_budget_s": 255, "max_events_per_run": 104211,
 "runs": [{"run_id": "cut20", "params": {"range_end_h": 6, "rr": 2.0}, "cut": 20842, "scramble": null},
          {"run_id": "scramble", "params": {...}, "cut": null, "scramble": {"start": 52105, "seed": 81236...}}]}
```
- `params` values are JSON ints for `int` params and JSON floats for `float` params.
- `job_budget_s = job_timeout_s − RUNNER_BUDGET_MARGIN_S`.
- `max_events_per_run = min(n_bars, sandbox_io.MAX_EVENTS_PER_RUN)`, where
  `MAX_EVENTS_PER_RUN = (MAX_EVENTS_FILE_BYTES − 4096) // 26` = 322 477 (what fits in one
  events file; part 1 deviation B, now written down).

**out/<run_id>.events.npy** is written with `np.save(..., allow_pickle=False)`, using dtype
`interfaces.EVENTS_DTYPE`:

```
[('bar','<i8'), ('signal','i1'), ('stop_dist','<f8'), ('tp_dist','<f8'), ('exit','?')]   # packed, itemsize 26
```

It holds one record per row where `signal != 0 or exit`, with strictly increasing `bar`.
`bar` is the row index in the frame that run saw.

**out/status.json** (`interfaces.STATUS_SCHEMA`):
```json
{"schema": "sajim.lab.sandbox_status/1", "ok": false,
 "runs": {"cut20": {"ok": true, "n_events": 31, "seconds": 0.84}},
 "errors": [{"run_id": "full1", "kind": "exception", "message": "ZeroDivisionError: ... (strategy.py line 41)"}],
 "timings": {"cut20": 0.84, "_total": 12.3},
 "versions": {"python": "3.12.7", "numpy": "2.1.3", "pandas": "2.2.3", "pyarrow": "18.1.0"},
 "sensitive_env_names": []}
```
- `kind` is one of `interfaces.ERROR_KINDS`.
- `sensitive_env_names` lists the **names** of environment variables matching
  `^(SAJIM|MODAL|ANTHROPIC|CLAUDE)_`. Values are never included. This list backs the P1
  test "lab_signals has no SAJIM_* env vars".

### 2.3 What `sandbox_runner.py` does (untrusted side, B)

The parent is invoked as `sandbox_runner.py <in_dir> <out_dir>`:
1. Read `plan.json` and load `frame.parquet` into memory. Check that the row count equals
   `n_bars`, otherwise raise a job-level `internal` error. Then **delete `frame.parquet`**
   before any strategy code runs.
2. Run each plan entry **in list order**, each in a **fresh child process**:
   `[sys.executable, "-I", "-X", "utf8", <this file>, "--child", run_id, in_dir, out_dir, <work frame path>]`.
   - Just before each child starts, the parent writes that run's frame to
     `work/<run_id>/frame.parquet`: the cut prefix `[0, cut)`, the scrambled frame, or the
     full frame. It deletes the file after the child exits.
   - Each child gets a timeout of `per_run_timeout_s` (it includes the child's start-up).
     When it fires, the child is killed and the run gets error kind `timeout`, message
     "generate_signals did not finish within the per-run limit of 14 s (process start-up
     included); the run was killed".
   - Once elapsed time passes `job_budget_s`, the parent starts no more runs. Each
     remaining run gets kind `budget` ("not started: the job time budget ... was used up").
   - Near the end of the job the parent may cut a run's timeout below `per_run_timeout_s`
     so `status.json` is still written; a run killed by that shorter timeout gets kind
     `budget` ("stopped ... because the job time budget ran out"), never `timeout`. With
     the p1.2 numbers neither budget case happens in normal operation.
   - Because runs execute in a fixed order, in separate processes, and the full frame
     never sits on disk while a cut run executes, a strategy cannot cache future bars from
     an earlier run.
3. Write `status.json` last, always, even after errors. If a child exits non-zero without
   writing its error file, the run gets kind `crash`.

The child, `--child`:
1. Installs the vendored indicators as module `sajim_quant.lab.indicators`. The parent
   packages `sajim_quant` and `sajim_quant.lab` are empty `types.ModuleType` entries in
   `sys.modules`, so `from sajim_quant.lab import indicators as ind` works.
2. Execs `strategy.py` into a fresh module named `lab_strategy`, compiled with filename
   `strategy.py`. A failure gets kind `import`. A missing or non-dict `PARAMS` gets kind
   `params`.
3. Calls `generate_signals(df.copy(), dict(params))`. An exception gets kind `exception`;
   the message is the last 2000 chars of the formatted traceback.
4. Validates the dense output:
   - It must be a DataFrame, or the run gets kind `schema`.
   - `index.equals(df.index)`, or kind `index`.
   - Required columns are `signal`, `stop_dist` and `tp_dist`. `exit` is optional (all
     False when absent). Extra columns are ignored.
   - `signal` must have an integer dtype of any width (float or object gets kind `dtype`,
     with the message "use .astype('int8')"). Its values must be in {-1, 0, 1}, otherwise
     kind `value`.
   - `stop_dist` and `tp_dist` must be real numeric; they are cast to f8.
   - `exit` must be a bool dtype, otherwise kind `dtype`.
   - Where `signal != 0`: `stop_dist` must be finite and > 0, and `tp_dist` must be NaN or
     finite and > 0. Otherwise kind `value`.
   - A `-1` signal when `allow_short` is false gets kind `value`.
   - More than `max_events_per_run` events gets kind `too_many_events`.
5. Converts to sparse events. `stop_dist` and `tp_dist` are set to NaN on rows where
   `signal == 0`. It writes `out/<run_id>.events.npy`, or on failure
   `out/<run_id>.error.json` = `{"kind", "message"}`, which the parent folds into
   `status.json`.

`sandbox_runner.py` also exposes `scramble_frame(df, start, seed) -> pd.DataFrame` at module
level. That is the frozen algorithm (§5.2). Tests import the runner as a module; the `__main__`
guard stops it doing work on import.

### 2.4 Executors

```python
# sajim_quant/lab/sandbox_local.py (B)
class LocalSubprocessExecutor:
    def __init__(self, python_exe: str | None = None, *, base_dir: Path | None = None, keep_dirs: bool = False): ...
    def run(self, job: SandboxJob) -> SandboxResult: ...
```
- Creates a fresh temp root with `in/`, `work/` and `out/`.
- Runs `runner_argv(root)`, with `"python"` replaced by `python_exe or sys.executable`.
- Uses `cwd=<root>/work`, `stdin=DEVNULL` and `timeout=job.job_timeout_s`. On timeout it
  kills the process tree and sets `timed_out=True`.
- Gives the process a minimal env: `SYSTEMROOT`, `PATH` (the python dir only), `TEMP` and
  `TMP` set to `<root>/work`, and `PYTHONUTF8=1`. There are **no** `SAJIM_*` or other
  inherited variables.
- Reads back only `sandbox_io.expected_output_names(job)`, which is `status.json` plus each
  `<run_id>.events.npy`. Each file is read with a bound of its limit + 1 bytes, so the
  decoder can see that it is oversize.
- Deletes the root unless `keep_dirs`.
- It is **not** a security boundary. The docstring has to say so.

```python
# sajim_quant/lab/sandbox_modal.py (integrator, later) — signature frozen now
class ModalSandboxExecutor:
    def __init__(self, app: "modal.App", image: "modal.Image", *, cpu: float = 2.0,
                 memory: int = 4096, timeout: int = gates.LAB_SIGNALS_TIMEOUT_S): ...
    def run(self, job: SandboxJob) -> SandboxResult: ...
```
- Creates a **fresh** `modal.Sandbox.create(app=app, image=image, block_network=True,
  cpu=2, memory=4096, timeout=300)` per call, with **no** `secrets`, **no** `volumes` and
  no `SAJIM_*` env.
- The image is `debian_slim(python 3.12)` with pinned numpy/pandas/pyarrow only: no
  `modal`, no `sajim_quant`.
- Writes `encode_job` files under `/tmp` with `sb.filesystem.write_bytes`, then calls
  `sb.exec(*runner_argv("/tmp"), timeout=job.job_timeout_s)` and waits.
- Reads only the expected names, never more than the limit + 1 bytes each. If the SDK
  cannot bound a read, the integrator documents and tests the fallback. This is an open
  issue, see §13.
- Calls `sb.terminate()` in `finally`.

### 2.5 Trusted decoder (`sandbox_io.py`, B)

```python
def frame_to_parquet(frame: pd.DataFrame) -> bytes
def plan_json(job: SandboxJob) -> bytes
def runner_source() -> bytes                       # bytes of sandbox_runner.py next to this module
def indicators_source() -> bytes                   # bytes of sajim_quant/lab/indicators.py (read lazily)
def encode_job(job: SandboxJob) -> dict[str, bytes]
def expected_output_names(job: SandboxJob) -> list[str]
def decode_events(raw: bytes, n_bars: int, *, allow_short: bool) -> np.ndarray   # raises SandboxDecodeError
def decode_status(raw: bytes) -> dict                                            # raises SandboxDecodeError
def decode_result(job: SandboxJob, result: SandboxResult) -> DecodedJob          # never raises on bad untrusted data
def events_equal(a: np.ndarray, b: np.ndarray, *, atol: float = gates.CAUSALITY_ATOL) -> tuple[bool, int | None]
class SandboxDecodeError(ValueError): kind: str   # "decode" | "status_invalid"
```

`decode_events` rules, all of which must hold. On any failure it raises
`SandboxDecodeError` and the run counts as failed with kind `decode`. It never calls pickle
or `eval` on anything:
1. `len(raw) <= MAX_EVENTS_FILE_BYTES`.
2. `np.lib.format.read_magic` gives version (1,0) or (2,0). `read_array_header_1_0/2_0` is
   called with `max_header_size=10000`.
3. The header requires `dtype == EVENTS_DTYPE` exactly (same names, order and offsets),
   `fortran_order is False`, shape `(k,)` and `k <= n_bars`.
4. `header_len + k*26 == len(raw)`: no trailing bytes.
5. `np.load(io.BytesIO(raw), allow_pickle=False)`, then `np.array(..., copy=True)`.
6. `exit` raw bytes (`view(uint8)`) are all 0 or 1. `0 <= bar < n_bars` and `bar` is
   strictly increasing. `signal` is in {-1, 0, 1}. Every record has `signal != 0 or exit`.
7. Where `signal != 0`: `stop_dist` is finite and > 0, and `tp_dist` is NaN or finite and > 0.
   `inf` is rejected.
8. When `allow_short` is false, there are no `-1` signals.
9. Normalise: `stop_dist` and `tp_dist` are set to NaN where `signal == 0`.

`decode_status`:
- Size `<= MAX_STATUS_BYTES`; `json.loads` with `parse_constant` rejecting NaN/Infinity.
- `schema` must match.
- `errors` has at most `MAX_ERRORS` entries.
- An unknown `kind` becomes `internal`.
- Messages are truncated to 2000 chars, and characters outside printable ASCII plus `\n`/`\t`
  are replaced with `?`. These messages are untrusted text that reaches the builder.
- Malformed `timings`/`versions` entries are dropped.
- `sensitive_env_names` entries must match `^[A-Z0-9_]{1,64}$`, with at most 64 of them.

`decode_result`:
- A missing or invalid `status.json` gives the job-level error `status_invalid` (`run_id=None`).
- `timed_out` gives a job-level `timeout`. A non-zero `exit_code` with no status gives
  `sandbox`.
- Each run is decoded with `n_bars = run.n_bars(job.n_bars)`. A missing events file with no
  reported error gives `missing_output`.
- `status.ok` is advisory and ignored. The trusted side recomputes everything.

`events_equal` compares `bar`, `signal` and `exit` exactly, and `stop_dist`/`tp_dist`
NaN-aware with `abs(a−b) <= atol`. It returns `(equal, first differing bar or None)`. When
the arrays differ in length, the first differing bar is the smallest bar present in only one
of them.

---

## 3. Engine (A): contract, indicators, simulator

### 3.1 `contract.py`

```python
class ContractError(ValueError): ...
@dataclass(frozen=True)
class LintIssue:
    line: int; col: int; code: str; message: str
    def format(self, filename: str) -> str          # "strategy.py:12:5: LOOKAHEAD .shift(-1) reads the future"
def lint_source(source: str | bytes) -> list[LintIssue]          # CONTRACT.md §6 incl. PARAMS rules, size, UTF-8, syntax
def parse_params(source: str | bytes) -> dict[str, dict]         # PARAMS literal, insertion order kept; raises ContractError
def default_params(params_spec: Mapping[str, Mapping]) -> dict[str, int | float]
def clip_params(params_spec, values: Mapping[str, float]) -> dict[str, int | float]   # ints rounded half-away-from-zero then clipped
def validate_manifest(manifest: Mapping, *, strategy_py: bytes | None = None,
                      spec_md: bytes | None = None, require_shas: bool = True) -> list[str]
def check_universe(symbol: str, timeframe: str) -> list[str]     # splits.ALLOWED_* + interfaces.SHORT_COVERAGE_REFUSED
def lint_path(path: Path) -> list[str]                           # strategy.py -> lint; manifest.json -> validate_manifest(require_shas=False); else []
def main(argv: list[str] | None = None) -> int                   # `python -X utf8 -m sajim_quant.lab.contract lint <path>...`
```
- Issue codes are uppercase: `SYNTAX`, `ENCODING`, `SIZE`, `IMPORT`, `CALL`, `DUNDER`,
  `IO`, `LOOKAHEAD`, `LITERAL`, `MODULE`, `RANDOM`, `PARAMS`.
- `lint_source` returns `[]` when the file is clean.
- `main` prints one formatted issue per line. Exit codes: 0 clean, 1 issues found, 2 usage
  error or unreadable file.
- `validate_manifest`:
  - Rejects unknown keys and any `risk_pct`, balance, cost or date field. Checks every rule
    in CONTRACT.md §4, including `check_universe`.
  - `id` and `family` must fully match `gates.LAB_ID_PATTERN` (`re.fullmatch`).
  - p1.2: `id`, `family` and `name` must not contain a `gates.FORBIDDEN_LABELS` entry, read
    with `_`/`-` as spaces (`contract.forbidden_label_in`), so `lab_elite_x`, `lab_tier_1_x`
    and `"OPTIMAL breakout"` are refused before any budget claim (422). A multi-word label
    written as one word (`lab_tier1_x`, `EdgeFound`) is refused too; single-word labels are
    not matched across joined words (`range_lite` is allowed). `run.py` uses the same
    function for its id/family preflight and the derived-id slug.
  - With `require_shas=True`, `code_sha256` must equal `sha256(strategy_py)` and
    `spec_sha256` must equal `sha256(spec_md)`; both must be passed.
  - With `require_shas=False` (the local hook), a sha may be `""` or any 64-hex value.
    Claude cannot hash; `lab.cli` stamps the shas (§8).
- `n_params` for G1 is `len(parse_params(source))`.
- p1.2: a PARAMS name containing a forbidden label (`optimal_len`, `EliteFast`,
  `tier_1_mult`) is a `PARAMS` lint issue, and `parse_params` raises `ContractError` for it,
  because PARAMS names appear in the scorecard's `params` and `detail.plateau`.

### 3.2 `indicators.py`

Self-contained: imports only `numpy`, `pandas`, `math` and `typing`. No relative or
`sajim_quant` imports. Every function is causal: row *i* uses rows ≤ *i* only. All of them
pass the truncation test in `test_indicators.py`. Outputs are aligned to the input index.

```python
INDICATORS_VERSION = "1"
def sma(x: pd.Series, n: int) -> pd.Series
def ema(x: pd.Series, n: int) -> pd.Series                         # adjust=False, min_periods=n
def atr(df: pd.DataFrame, n: int = 14) -> pd.Series                # Wilder; NaN for the first n bars
def rsi(x: pd.Series, n: int = 14) -> pd.Series                    # Wilder
def donchian(df: pd.DataFrame, n: int) -> pd.DataFrame             # columns upper, lower, mid; window INCLUDES the current bar (use .shift(1) for "prior n")
def zscore(x: pd.Series, n: int) -> pd.Series                      # rolling mean / rolling std (ddof=1)
def session_mask(index: pd.DatetimeIndex, start_h: int, end_h: int) -> pd.Series      # bool; start_h <= hour < end_h, wraps midnight if start_h > end_h
def session_range(df: pd.DataFrame, start_h: int, end_h: int) -> pd.DataFrame         # columns high, low of the most recent COMPLETED session; visible from the first bar with open time >= session end; NaN before
def htf(df: pd.DataFrame, rule: str, base_minutes: int | None = None) -> pd.DataFrame  # open/high/low/close of the most recent CLOSED higher-TF bar; rule in {"30min","1h","4h","1D"}
```

In `htf`, a higher-TF bar `[T, T+R)` becomes visible on the first row whose bar end
(`open time + base_minutes`) is ≥ `T+R`. `base_minutes` defaults to the smallest positive
index step. These are the only legitimate route to a higher-TF view; the linter flags
`resample()` anywhere in a strategy.

### 3.3 `simulator.py`

```python
class SignalSimulator:
    def __init__(self, spec: InstrumentSpec, *, initial_balance: float = gates.INITIAL_BALANCE,
                 risk_pct: float = gates.RISK_PCT, cost_mult: float = 1.0, delay_bars: int = 0,
                 max_hold_bars: int, allow_short: bool, window: tuple[int, int] | None = None,
                 strategy_name: str = "lab", timeframe: str = ""): ...
    def run(self, frame: pd.DataFrame, events: np.ndarray,
            quote_rate: np.ndarray | None = None) -> interfaces.SimResult: ...

def to_backtest_result(sim: interfaces.SimResult, *, lab_id: str, symbol: str, timeframe: str,
                       stage: str, code_sha256: str, params: Mapping, fx_desc: str) -> BacktestResult
```

Constructor:
- `risk_pct` is clamped to `gates.MAX_RISK_PCT`, with a logged warning.
- `window=None` means `(0, len(frame))`.
- `quote_rate=None` is allowed only when `spec.quote_currency == "USD"`; otherwise it raises
  `ValueError`. When given, it is a float64 array of `len(frame)`, finite and > 0.
- Events must have `dtype == EVENTS_DTYPE`, with bars in range. They are validated cheaply
  and a violation raises `ValueError`.
- `frame` has `open, high, low, close` and a DatetimeIndex.

Execution semantics follow CONTRACT.md §5, frozen exactly as below. Let `[ws, we)` be the
window, `d` the delay, and `s` = +1 for long, −1 for short.

1. **Eligible signal.** An event with `signal != 0` on bar `i` has fill bar `f = i + 1 + d`.
   It counts in `n_signals` only if `ws <= f < we`. Signals whose `f` falls outside the
   window are ignored entirely: no position is carried in.
2. **Entry** happens only when flat at bar `i`'s close (`n_entry_attempts += 1`).
   - Fill = `open[f]` and `rate = quote_rate[f]`.
   - `lots = spec.lots_for_risk(equity*risk_pct, stop_dist, rate, entry_price=fill, equity=equity)`.
   - `lots == 0` means `untradable_stop += 1` and the signal is skipped.
   - `SL = fill − s*stop_dist`, `TP = fill + s*tp_dist`; `TP = None` if `tp_dist` is NaN.
   - With `d > 0`, signals on bars `i+1 … f−1` are ignored, because an entry is pending.
3. **Bars `j = f, f+1, …`** are processed in this order; the entry bar's own high and low
   are checked:
   - a. **SL/TP.**
     - SL is hit when `low[j] <= SL` for a long, or `high[j] >= SL` for a short. TP is hit
       when `high[j] >= TP` for a long, or `low[j] <= TP` for a short.
     - If both are touched, book **SL**.
     - An SL gap (`open[j]` beyond SL) fills at `open[j]`, which is worse than the level.
     - **A TP gap fills at the TP level**, never at the better open. This is a pessimistic
       choice; CONTRACT's "worse than the level" only constrains the SL case.
     - Reasons: `SL`, `TP`.
   - b. **Hold limit.** If `j == f + max_hold_bars − 1`, close at `close[j]` with reason
     `TIMEOUT`. So `max_hold_bars=1` exits at the entry bar's close.
   - c. **Window end.** If `j == we − 1`, close at `close[j]` with reason `TIMEOUT`
     (`window_end_closes += 1`).
   - d. **Signal exit.** If `exit[j]` is true or `signal[j] == −s`, close at `open[j+1]` with
     reason `SIGNAL`. `j+1 < we` is guaranteed by (c). This does **not** open a reversal.
     Same-direction signals and `exit` events while in a position are otherwise ignored.
4. **After an exit.** After an SL/TP/TIMEOUT exit on bar `j`, the strategy is flat from
   `j`'s close, so a signal on bar `j` itself may enter at `open[j+1+d]`. After a `SIGNAL`
   exit at `open[j+1]`, signals on bars ≥ `j+1` may enter. When flat, `exit` flags are
   ignored.
5. **P&L**, all at the entry rate:
   - `gross = spec.gross_pnl(fill, exit_price, lots, is_long, rate)`
   - `cost = spec.round_trip_cost(fill, lots, rate) * cost_mult`
   - `net = gross − cost`
   - `equity += net`
   - Equity compounds (sizing uses the current equity). Values are not rounded.
6. **MAE/MFE** are in account currency (`mae <= 0`, `mfe >= 0`). They span bars `f..exit`.
   A `SIGNAL` exit uses bars `f..j` plus `open[j+1]`. On the exit bar of an SL/TP exit the
   intrabar order is unknown, so (p1.2, both sides, long and short):
   - SL bar: the adverse extreme is clamped at the stop fill; the favourable extreme is
     clamped at `max(open[j], fill)` for a long (`min` for a short), so a target touch on a
     bar that booked the stop never inflates MFE;
   - TP bar: the favourable extreme is clamped at the TP level; the adverse extreme is kept
     whole (pessimistic MAE, which feeds the intrabar drawdown).
7. **Ruin.** After any close, if `equity <= gates.RUIN_FLOOR * initial_balance`, that
   trade's `exit_reason` becomes `STOP_OUT` (its price, time and P&L are unchanged) and
   `custom_fields["original_exit_reason"]` keeps the old reason. The run halts with
   `halted=True`.
8. **`Trade` fields:**
   - `trade_id=f"{strategy_name}_{k:05d}"`, `engine=gates.ENGINE`, `symbol=spec.symbol`.
   - `entry_time=frame.index[f].isoformat()`, naive.
   - `exit_time` is `index[j]` for SL/TP/TIMEOUT/STOP_OUT, or `index[j+1]` for `SIGNAL`.
   - `position_size=lots`, `commission=cost`, `swap_cost=0`,
     `return_pct=net/equity_before`, `mae`, `mfe`.
   - `custom_fields={"signal_bar": i, "entry_bar": f, "exit_bar": j_exit, "stop_dist": x,
     "tp_dist": x|None, "quote_rate": rate, "cost_mult": m}`, where `j_exit` is `j+1` for
     `SIGNAL`. Robustness (null hold distribution) depends on `entry_bar`/`exit_bar`.
9. **`SimResult`** (in `interfaces.py`):
   - `equity` is a float Series. Its first point is `initial_balance` at `index[ws]`,
     followed by one point per exit at `exit_time`.
   - `notes` is a JSON-safe dict with `cost_mult, delay_bars, risk_pct, initial_balance,
     max_hold_bars, allow_short, window: {start_bar, stop_bar, first_bar, last_bar}` (ISO
     for the times), plus `untradable_stop, n_signals, n_entry_attempts,
     window_end_closes, halted, ruin_floor`.
10. **Performance.** `run()` must be O(events + exposed bars), not a Python loop over every
    bar. Target: a 100 000-bar frame with 300 trades runs in < 50 ms, because robustness
    calls it about 330 times per DEV evaluation.

`to_backtest_result` fills `RunMetadata`:
- `strategy_name=lab_id`, `engine="python:signal"`, `symbol`, `timeframe`;
- `start_date` / `end_date` = ISO of the window's first and last bar;
- `initial_balance`, `currency="USD"`, `spread_model="constant"`, `spread_pips=None`;
- `commission_model=gates.COMMISSION_MODEL` ("instruments_v1");
- `commission_rate = cost_assumptions(symbol)["commission_rate"] * cost_mult`;
- `slippage_pips = cost_assumptions(symbol)["slippage_pips"] * cost_mult`;
- `execution_latency_ms=0`.

`notes = json.dumps(..., sort_keys=True)` of:
```json
{"schema": "sajim.lab.run_notes/1", "code_sha256": "...", "params": {...}, "stage": "dev",
 "window": {"start": "2024-09-01T00:00:00", "stop": "2026-01-01T00:00:00", "first_bar": "...", "last_bar": "..."},
 "cost_mult": 1.0, "delay": 0, "risk_pct": 0.01, "untradable_stop": 3, "n_signals": 240,
 "n_entry_attempts": 214, "window_end_closes": 1, "halted": false, "fx_desc": "quote currency USD (no conversion)",
 "gates_version": "v1", "protocol_version": "v1", "engine_version": "signal_sim/1"}
```

### 3.4 Cost multiplier: decision

The multiplier is applied to the **output** of `spec.round_trip_cost(...)`, which is
CONTRACT's literal formula `net = gross − round_trip_cost × cost_mult`. The spec itself is
**not** replaced. `round_trip_cost` is linear in both `round_trip_bps` and
`slippage_points`, so this equals a `dataclasses.replace` copy for the cost charged. It also
keeps sizing, `min_tradable_stop()` and leverage caps identical across stress runs, so cost
stress measures cost and nothing else. No `instruments.py` edit is needed.

Test this per trade: at 2x, each trade with identical lots has exactly twice the
`commission`. A whole-run comparison is not exact, because compounding changes later lots.

FX: `evaluate.py` (C) computes
`rates, fx_desc = core.fx.quote_rate_series(spec.quote_currency, timeframe, sframe.frame.index, loader=loader)`
and passes `rates.to_numpy()`. The simulator never loads data.

---

## 4. Frames and windows (C, `evaluate.py`)

```python
def prepare_frame(raw: pd.DataFrame, stage: str, warmup_bars: int,
                  reference_end: datetime | None = None) -> interfaces.StageFrame
```
- `raw` is `DataLoader().load(symbol, tf, start_date=None, end_date=splits.loader_bounds(stage)[1])`.
- In-window rows come from `splits.window_mask(raw.index, stage)`. Never compare against
  `*_END` directly. The first in-window row is `w0`.
- `frame = raw.iloc[max(0, w0 − warmup_bars) : last_in_window + 1]`, with
  `start_bar = w0 − offset`, `stop_bar = len(frame)` and `stage = splits.normalize_stage(stage)`.
- It raises `ContractRefused(code="coverage")` when there are no in-window rows, or when
  `splits.has_min_coverage(stage, first, last, reference_end)` is false.
- DEV has no pre-DEV data, so DEV warmup comes out of DEV itself and `start_bar` may be 0.
  HOLDOUT warmup comes from PURGE and late DEV bars, which are never scored.

---

## 5. Integrity (B, `causality.py`)

### 5.1 API

```python
SCRAMBLE_FRACTION = 0.5
CHECK_RUN_IDS = ("cut20", "cut40", "cut60", "cut80", "cut95", "scramble", "full1", "full2")
def cut_index(frac: float, n_bars: int) -> int            # max(1, floor(frac * n_bars))
def scramble_seed(code_sha256: str) -> int                # int.from_bytes(sha256(b"sajim.lab.scramble/1|" + sha.encode()).digest()[:8], "big") & (2**63 - 1)
def build_check_plan(n_bars: int, params: Mapping[str, int | float], code_sha256: str) -> tuple[SandboxRun, ...]
def assess_check(plan: Sequence[SandboxRun], decoded: DecodedJob, n_bars: int, *,
                 reveal_bars: bool = True) -> CheckReport

# p1.2 adaptive cuts
ADAPTIVE_CUTS = 6
ADAPTIVE_MAX_OFFSET = 3
def adaptive_seed(code_sha256: str, stage: str) -> int   # sha256(f"sajim.lab.adaptive/1|{sha}|{stage}")[:8], & (2**63 - 1)
def is_adaptive_run(run_id: str) -> bool                 # fullmatch acut<1-2 digits>
def build_adaptive_plan(base_events: np.ndarray | None, n_bars: int, params, code_sha256: str, stage: str, *,
                        k: int = ADAPTIVE_CUTS, min_bar: int = 0) -> tuple[SandboxRun, ...]
def assess_adaptive(report: CheckReport, plan: Sequence[SandboxRun], decoded: DecodedJob, *,
                    reveal_bars: bool = True) -> CheckReport
```

### 5.2 Plan and checks

The plan is these 8 runs in this exact order, all with the default params (clipped):
- `cut20` … `cut95`, where `cut = cut_index(f, n)` for `f` in `gates.CAUSALITY_CUTS`;
- `scramble`, with `ScrambleSpec(start=cut_index(0.5, n), seed=scramble_seed(sha))`;
- `full1`, `full2`.

Frozen scramble algorithm (`sandbox_runner.scramble_frame`):
```
rng = np.random.default_rng(seed); n = len(df); src = rng.permutation(np.arange(start, n))
prev = close[src - 1]                                    # original previous close of each source bar
r_o, r_h, r_l, r_c = open[src]/prev, high[src]/prev, low[src]/prev, close[src]/prev
new_close = close[start-1] * cumprod(r_c); new_prev = concat([close[start-1]], new_close[:-1])
open/high/low/close[start:] = new_prev * (r_o, r_h, r_l, r_c); volume[start:] = volume[src]; index unchanged
```

`assess_check` returns a `CheckReport`. It sets `checks["lint"]` and `checks["manifest"]` to
True, because `evaluate.check` overwrites them. The other checks:
- `sandbox` is False if the job has an error with `run_id=None`, or any run has kind
  `import`, `params`, `exception`, `timeout`, `crash` or `budget`.
- `schema` is False if any run has kind `schema`, `index`, `dtype`, `value`,
  `too_many_events`, `decode` or `missing_output`.
- `determinism`: `events_equal(full1, full2)`.
- `causality`: for each cut `c`, `events_equal(full1[full1.bar < c], cut_run)`.
- `scramble`: `events_equal(full1[bar < start], scramble[bar < start])`.
- `min_signals`: `count(full1.signal != 0) >= gates.MIN_SIGNALS`.

Reasons are short builder-facing strings and carry no money figures. For example:
- `"causality: outputs before the 40% cut changed when later bars were removed (first differing bar 41522)"`
- `"determinism: two identical runs differed (first differing bar 1203)"`
- `"sandbox: full1 exception: KeyError: 'close_' (strategy.py line 22)"`

`base_events` holds `full1`'s events when it decoded, and is used as the DEV/holdout base
run. `passed` = all `CHECK_KEYS` True.

**Adaptive cuts (p1.2).** The five fixed cuts only see look-ahead that changes an event within
a few bars before a cut. A sparse strategy (about 200 signals in 20k bars) filtered on the
close 3 bars ahead, written as a positive-offset array slice that the linter cannot see,
passed the fixed checks in 37 of 40 seeds. So, after the check job passed (on the holdout:
passed apart from a zero signal count), a **second sandbox job** runs up to `ADAPTIVE_CUTS`
cuts placed just after `full1`'s own event bars:
- Sampler (frozen), `rng = np.random.default_rng(adaptive_seed(sha, stage))`, with `stage` the
  frame's stage (`dev` for checks and DEV, `holdout` for the holdout):
  - eligible events: `bar + 1 < n_bars` and `bar >= min_bar` (the scored window's
    `start_bar`; if none is, every `bar + 1 < n_bars`). Signal events and exit-only events
    are separate pools.
  - `k_exit = min(#exit, k // 3)`, `k_sig = min(#signal, k − k_exit)`,
    `k_exit = min(#exit, k − k_sig)`.
  - draws, in this order: `rng.choice(signal pool, k_sig, replace=False)`,
    `rng.choice(exit pool, k_exit, replace=False)`,
    `offsets = rng.permutation(np.resize(np.arange(1, ADAPTIVE_MAX_OFFSET + 1), k_sig + k_exit))`.
  - `cut = min(bar + offset, n_bars − 1)`; a duplicate cut keeps the first. Run ids
    `acut1..` in ascending cut order, default params, full stage frame as the source.
- Check: for each adaptive cut `c`, `events_equal(full1[bar < c], acut[bar < c])`. A peek of
  `p` bars at a sampled event bar `e` is seen by every cut with offset ≤ `p` (the event's
  inputs past the cut are gone), and the offsets repeat 1, 2, 3, so with 6 cuts a peek of
  1-3 bars is caught deterministically; offsets above 1 also cover events the peek would
  create in the bars before the cut.
- A difference sets `causality` False. A failed adaptive run sets `sandbox` / `schema` False
  as for the fixed runs; a job-level error of that job sets `sandbox` False. Each is
  G0 → REJECT. Errors of plateau neighbours in the same job (DEV) are not integrity errors.
- Reasons name the kind of cut: DEV/check `"causality: outputs before an adaptive cut (2
  bar(s) after a signal on bar 1203) changed when later bars were removed (first differing
  bar 1203); the strategy reads bars after the bar it decides on"`.
- **Holdout (`reveal_bars=False`)**: no reason names a bar index. Trusted text drops
  "(first differing bar N)", the scramble reason says "the scramble point", the adaptive reason
  says "placed just after one of the strategy's own event bars", and every digit run in an
  untrusted sandbox message is replaced by `#`. Holdout reasons only reach the judge-scope
  holdout scorecard and README, but they stay free of holdout bar facts anyway.

Canaries, B's tests via `LocalSubprocessExecutor` with a stub `indicators.py`: `shift(-1)`,
`rolling(..., center=True)` and an unshifted H4 `resample().last()` reindexed to the base TF
must each give `passed=False`. In addition, `contract.lint_source` must flag all three (A's
tests).

Threat model, stated in the module docstring: these checks catch **accidental** look-ahead
by a model. The real boundary is the sandbox. A deliberately adversarial strategy may defeat
in-sandbox checks. That only costs the honesty of its own scorecard and gives no access to
the trusted side.

---

## 6. Robustness and scorecard (C)

### 6.1 Metric → function table (every `gates.GATES` rule metric)

| Gate | Metric(s) | Computed by |
|---|---|---|
| G0 | `integrity_checks_passed`, `cost_model_checks_passed`, `slippage_points`, `untradable_stop_frac`, `n_signals` | `robustness.integrity_metrics` |
| G1 | `n_params` | `robustness.integrity_metrics` (`n_params` arg = `len(contract.parse_params(src))`) |
| G2 | `dev_trades`, `dev_months_with_trades_frac` / `holdout_trades` | `robustness.sample_metrics` |
| G3 | `pf`, `expectancy`, `max_dd_frac`, `top5_profit_share`, `max_month_profit_share`, `min_side_pf` | `robustness.performance_metrics` |
| G4a | `net_minus_null_pctl` | `robustness.null_metrics` |
| G4b | `psr`, `dsr` | Phase 2 (absent → not evaluated) |
| G5 | `positive_folds`, `min_fold_pf` | `robustness.fold_metrics` |
| G6 | `net_at_1_5x_cost`, `pf_at_2x_cost`, `pf_delay_1` | `robustness.stress_metrics` |
| G7 | `plateau_positive_frac`, `plateau_median_pf` | `robustness.plateau_metrics` |
| G8 | `holdout_net`, `holdout_pf`, `holdout_pf_at_1_5x_cost`, `holdout_sharpe_margin`, `holdout_dd_margin` | `robustness.holdout_metrics` |
| G9 | `mc_*` | Phase 2 |
| G10 | `profitable_nodes_beyond_primary` | Phase 2 |
| G11 | — (report only) | `performance_metrics` → scorecard `context` |

Each function returns a flat `dict[str, float | None]`. Extra keys are allowed, such as
`detail` (a nested dict, server-only). **Absent key = rule not evaluated** (`passed=None`).
**Present `None` = evaluated and not computable**, which fails.

### 6.2 Shared definitions

```python
def profit_factor(pnls: Sequence[float]) -> float | None
```
- No trades → `None`.
- No losses and some wins → `float("inf")`.
- No losses and no wins → `None`.
- Otherwise `gross_profit / gross_loss`.
- `inf` serialises to JSON `null`; the rule's `passed` was already computed.
- Where an aggregate needs a number (`min_fold_pf`, `plateau_median_pf`, `min_side_pf`),
  `None` counts as `0.0`.

```python
def performance_metrics(result: BacktestResult, *, months: Sequence[str], first_bar: datetime,
                        last_bar: datetime) -> dict
```
Keys:
- `trades`, `net`, `return_pct` (%), `pf`
- `expectancy` (= `calculate_metrics(result).expectancy`)
- `win_rate` (fraction 0..1)
- `max_dd_pct` (= `calculate_metrics(result).max_drawdown_pct`, which includes intra-trade
  MAE) and `max_dd_frac` (= `max_dd_pct/100`)
- `daily_sharpe`
- `top5_profit_share`: sum of the 5 largest `net_pnl` / net, or `None` if net ≤ 0
- `max_month_profit_share`: max over **exit** months of month net / net, or `None` if
  net ≤ 0
- `long_trades`, `long_net`, `long_pf`, `short_trades`, `short_net`, `short_pf`
- `min_side_pf`: min PF over sides with ≥ 1 trade, or `None` if there are no trades
- `months_with_trades_frac`: distinct **entry** months / `len(months)`
- `median_hold_days`
- `rollover_entry_share`: share of entries with `21 <= hour < 24`, from
  `gates.ROLLOVER_FLAG_UTC_HOURS`

Reuse `analytics.metrics.calculate_metrics`. Do not reimplement drawdown.

```python
def daily_sharpe(trades, *, first_bar, last_bar, initial_balance) -> float | None
```
- Uses business days `pd.bdate_range(first_bar.date(), last_bar.date())`, zero-filled.
- A trade exiting on Saturday or Sunday counts on the next business day, clamped to the
  last business day.
- `r_d` = that day's summed `net_pnl` / equity at the start of the day.
- `mean(r)/std(r, ddof=1) * sqrt(252)`.
- `None` if there are fewer than 2 business days; `0.0` if std is 0.

### 6.3 Functions

```python
def integrity_metrics(check: CheckReport, sim: SimResult | None, *, result: BacktestResult | None,
                      fx_desc: str, spec: InstrumentSpec, n_params: int) -> dict
```
- `integrity_checks_passed = 1.0 if check.passed else 0.0`
- `cost_model_checks_passed = 1.0` iff `result.metadata.commission_model != "zero"` and
  `not fx_desc.startswith(gates.UNCONVERTED_FX_PREFIX)` and
  `result.metadata.engine == gates.ENGINE`
- `slippage_points = spec.slippage_points`
- `untradable_stop_frac = sim.untradable_stop / max(1, sim.n_entry_attempts)`
- `n_signals = sim.n_signals`
- `n_params`
- When `sim` is `None` (integrity failed), only `integrity_checks_passed`, `n_params` and
  `slippage_points` are present.

```python
def sample_metrics(sim: SimResult, stage: str, *, months: Sequence[str]) -> dict
```
- dev → `dev_trades`, `dev_months_with_trades_frac`, using entry months against
  `splits.months("dev")`.
- holdout → `holdout_trades`.

```python
def stress_metrics(simulate: SimulateFn, events: np.ndarray) -> dict
```
- `net_at_1_5x_cost` uses `SimOverrides(cost_mult=gates.COST_STRESS_NET_MULT)`.
- `pf_at_2x_cost` uses `cost_mult=gates.COST_STRESS_PF_MULT`.
- `pf_delay_1` uses `delay_bars=gates.DELAY_BARS`.
- It also returns `breakeven_cost_mult` and `breakeven_flag` from `breakeven_cost_mult()`
  (reported, not gated).

```python
def breakeven_cost_mult(simulate, events, lo=0.5, hi=4.0, tol=0.01, max_iter=12) -> tuple[float, str]
```
- If `net(lo) <= 0` → `(lo, "below_range")`.
- If `net(hi) > 0` → `(hi, "above_range")`.
- Otherwise bisect on `net(m) > 0` until `hi − lo <= tol` or `max_iter`, and return
  `(round((lo+hi)/2, 2), "in_range")`. The bounds are `gates.BREAKEVEN_MULT_RANGE`.

```python
def fold_bounds(window: tuple[int, int], n_folds: int = gates.N_FOLDS) -> list[tuple[int, int]]
```
- 4 equal **bar-count** folds:
  `edges = [ws + (k * (we − ws)) // n_folds for k in 0..n_folds]`, folds `[edges[k], edges[k+1])`.

```python
def fold_metrics(simulate, events, window) -> dict
```
- Each fold is simulated with `SimOverrides(window=fold)`: fresh `initial_balance`,
  positions open at the fold end close as TIMEOUT, and pre-fold signals are ignored.
- `positive_folds = count(net > 0)`.
- `min_fold_pf = min(pf or 0.0)`; a fold with no trades has PF 0.0.
- `detail = {"folds": [{"start_bar", "stop_bar", "trades", "net", "pf"}]}`.

```python
def null_seed(code_sha256: str, stage: str, r: int) -> int
    # int.from_bytes(sha256(f"sajim.lab.null/1|{code_sha256}|{stage}|{r}".encode("ascii")).digest()[:8], "big")
def null_events(base: SimResult, window: tuple[int, int], rng: np.random.Generator) -> np.ndarray
def null_metrics(simulate, base: SimResult, window, *, code_sha256: str, stage: str,
                 runs: int = gates.NULL_RUNS) -> dict
```

Frozen null construction, run `r`, `rng = np.random.default_rng(null_seed(sha, stage, r))`:
1. Take `K = len(base.trades)`. For each trade *k* use `dir_k`, `stop_k`, `tp_k` (from
   `custom_fields`) and `hold_k = exit_bar − entry_bar + 1`. If `K == 0`, return
   `{"net_minus_null_pctl": None, ...}`.
2. The bar span is `[a, we)` with `a = max(ws, 1)` and `B = we − a`. Slot length is
   `L_k = hold_k + 1`. Set `free = max(0, B − Σ L_k)`.
3. Draw the random numbers in this order:
   - `cuts = np.sort(rng.integers(0, free + 1, size=K))`
   - `gaps = np.diff(np.concatenate([[0], cuts, [free]]))`, giving K+1 gaps
   - `perm = rng.permutation(K)`
4. Lay out the slots sequentially from `a`: gap₀, slot₀, gap₁, slot₁, …. Slot *m* uses trade
   `perm[m]`'s `(dir, stop, tp, hold)`, with `fill_m` = the slot start.
5. Emit events:
   - `(bar=fill_m − 1, signal=dir, stop, tp, exit=False)`;
   - `(bar=fill_m + hold − 1, signal=0, NaN, NaN, exit=True)`.
   - Drop any slot whose `fill_m >= we`.
6. Simulate with `SimOverrides()`: cost 1, delay 0, the base window. Record the net.

Outputs:
- `null_pctl_95 = np.percentile(nets, gates.NULL_PERCENTILE)` (numpy default "linear")
- `net_minus_null_pctl = base.net − null_pctl_95`
- `null_rank_pct = 100 * mean(nets < base.net)`
- `null_mean_trades`
- `detail.null = {runs, pctl_95, rank_pct, mean_trades, seed_scheme: "sajim.lab.null/1"}`

```python
@dataclass(frozen=True)
class Neighbour: run_id: str; param: str; factor: float; params: Mapping[str, int | float]
def plateau_neighbours(params_spec: Mapping[str, Mapping], defaults: Mapping[str, int | float]) -> list[Neighbour]
def plateau_metrics(simulate, neighbour_events: Mapping[str, np.ndarray | None],
                    neighbours: Sequence[Neighbour]) -> dict
```

Neighbours are taken in PARAMS insertion order × `gates.PLATEAU_FACTORS` order:
- `v = default * factor`.
- An `int` is rounded; if it still equals the default, it moves by `−1` for factor < 1 and
  `+1` for factor > 1 (`gates.PLATEAU_MIN_INT_STEP`).
- The value is clipped to `[min, max]`. If it equals the default after clipping, the
  neighbour is **skipped** and does not consume an index. Duplicates are skipped too.
- `run_id = f"nb{idx:02d}"`. There are at most 10, because of `MAX_PARAMS × 2`.

Metrics:
- A neighbour whose events are `None` (its run failed) counts as net ≤ 0 with PF 0.0.
- p1.2, `evaluate.py`: when any neighbour run was **stopped for time** (kind in
  `RUN_TIMEOUT_KINDS`; a job-level timeout of that job counts only when the job holds no
  adaptive cuts, because a job-level error of the shared DEV second job is an integrity
  failure, G0 -> REJECT, §5.2), `plateau_positive_frac` and
  `plateau_median_pf` are set to `None` (evaluated, not computable → G7 fails with value
  `n/a`), and the scorecard's **first** reason is `"G7 plateau not computable: K of N
  neighbour runs were stopped for time (each run gets 14 s including process start-up);
  this is a runtime problem, not a plateau result: make generate_signals faster"`. Timing
  can therefore never pass or fail the plateau on numbers, and a slow strategy is told why.
  Neighbours that failed otherwise still count as losing neighbours, with a trailing reason
  `"plateau: K of N neighbour runs failed (<kinds>) and count as losing neighbours"`.
  `detail.plateau` gains `timed_out: [run_id]` and `failed: {run_id: kind}`.
- `plateau_positive_frac = count(net > 0)/n`; `plateau_median_pf = median(pf or 0.0)`;
  `plateau_n = n`.
- If `n == 0`, both metrics are `None` (fail).
- Per-neighbour numbers go only in `detail.plateau.neighbours`, which is server-only.

```python
def holdout_metrics(base: SimResult, stressed: SimResult, result: BacktestResult, *,
                    dev_headline: Mapping, first_bar, last_bar) -> dict
```
- `holdout_net`, `holdout_pf`, `holdout_pf_at_1_5x_cost`.
- `holdout_sharpe` (daily) and `holdout_max_dd_frac`.
- `holdout_sharpe_margin = holdout_sharpe − gates.MIN_HOLDOUT_SHARPE_RATIO × dev_headline["daily_sharpe"]`.
- `holdout_dd_margin = holdout_max_dd_frac − gates.MAX_HOLDOUT_DD_RATIO × dev_headline["max_dd_pct"]/100`.
  - Either margin is `None` if an input is `None`.
- `h_a` and `h_b` = `{trades, net, pf}` over trades whose **entry** time falls in
  `splits.window_mask(…, "h_a"/"h_b")`.

### 6.4 `scorecard.py`

```python
LAB_PHASE = interfaces.LAB_PHASE              # 1
def evaluate_gates(metrics: Mapping[str, float | None], *, phase: int = LAB_PHASE) -> list[GateResult]
def decisive_rule(g: GateResult) -> RuleResult | None      # first failing evaluated rule, else first evaluated rule
def verdict(gate_results: Sequence[GateResult], stage: str, budget_exhausted: bool, *,
            holdout_burned: bool = False) -> VerdictOutcome
def build_scorecard(*, submission: Submission, stage: str, sframe: StageFrame, params: Mapping,
                    gate_results: Sequence[GateResult], outcome: VerdictOutcome, budget: BudgetInfo,
                    perf: Mapping, holdout_split: Mapping | None, cost_model: Mapping,
                    context: Mapping, detail: Mapping, result_file: str, deploy_id: str = "",
                    warnings: Sequence[str] = (), now: datetime | None = None,
                    dev_hard_gates_passed: bool | None = None) -> dict   # p1.2: holdout passes the DEV value
def builder_view(scorecard: Mapping) -> dict              # DEV only (ValueError otherwise): drops "detail"
def to_json_bytes(scorecard: Mapping) -> bytes            # NaN/inf -> null, indent=2, UTF-8
def validate_scorecard(d: Mapping) -> list[str]           # [] when it matches §6.5
```

`evaluate_gates` covers every gate in `gates.GATES`:
- `active = g.phase <= phase`.
- A rule's `passed` is `None` if its metric key is absent, otherwise `Rule.passes(value)`.
- A gate's `passed` is `None` if it is inactive or no rule was evaluated. Otherwise it is
  `all(evaluated rules passed)`.
- G11 is always `passed=None`.

`verdict`, frozen precedence (Phase 1). "Failed" means `active and hard and passed is False`.
1. G0 or G1 failed → `REJECT`, `final=True`.
2. G2's DEV rules (`dev_trades` or `dev_months_with_trades_frac`) failed → `NOT_PROVEN`,
   `final=budget_exhausted`. Too few trades means not proven; DEV performance gates are not
   used to REJECT in this case.
3. Any of G3, G4a, G5, G6, G7 failed (plus G4b/G9/G10 once active) →
   `gates.BUDGET_EXHAUSTED_VERDICT` if `budget_exhausted`, else `REJECT`, with
   `final=budget_exhausted`. Both are REJECT today; `final` tells the builder whether a
   revision is still allowed.
4. `stage == "dev"`, all DEV hard gates passed → `NOT_PROVEN`, `final=holdout_burned`. The
   reasons are `"DEV gates passed (IN-SAMPLE); sealed holdout not yet run"` or
   `"holdout burned"`.
5. `stage == "holdout"`. The holdout scorecard's `gates` are the DEV gate results copied
   from the DEV scorecard, plus G2's `holdout_trades` rule and G8 evaluated now.
   - G2's `holdout_trades` rule failed → `NOT_PROVEN`, `final=True`. This is the
     "G2 → NOT_PROVEN" path; G8 cannot REJECT with fewer than `MIN_HOLDOUT_TRADES` trades.
   - Otherwise, G8 failed → `REJECT`, `final=True`.
   - Otherwise → `CANDIDATE`, `final=True`.
   - A negative H-B net adds the warning `"H-B (after the model cutoff) net is negative"`.
     It caps the verdict only from Phase 2.

When the build session ends without a holdout, the runner calls
`verdict(dev_gates, "dev", budget_exhausted=True, holdout_burned=...)`. The critic applies
afterwards via `gates.apply_downgrade`.

Reasons and warnings must not contain `gates.FORBIDDEN_LABELS`. `to_json_bytes` asserts this.

### 6.5 Scorecard JSON — `sajim.lab.scorecard/1` (exact keys, in this order)

```json
{
  "schema": "sajim.lab.scorecard/1",
  "id": "lab_asian_range_breakout_xauusd", "family": "lab_asian_range_breakout_xauusd",
  "code_sha256": "<64 hex>", "spec_sha256": "<64 hex>", "sha12": "<12 hex>",
  "gates_version": "v1", "protocol_version": "v1", "lab_phase": 1,
  "stage": "dev", "label": "IN-SAMPLE",
  "symbol": "XAUUSD", "timeframe": "M15",
  "window": {"start": "2024-09-01T00:00:00", "stop": "2026-01-01T00:00:00", "first_bar": "...", "last_bar": "...",
             "n_bars": 33000, "warmup_bars": 200},
  "params": {"range_end_h": 6, "rr": 2.0},
  "trial": 2, "max_trials": 3, "n_trials_family": 2, "n_trials_global": 7,
  "holdout_touches": 0, "max_holdout_touches": 3, "holdout_burned": false,
  "cost_model": {"spread_model": "constant", "commission_model": "instruments_v1", "commission_rate": 0.0003,
                 "slippage_pips": 0.5, "round_trip_bps": 3.0, "slippage_points": 5.0,
                 "fx_desc": "quote currency USD (no conversion)", "breakeven_cost_mult": 1.84,
                 "breakeven_flag": "in_range"},
  "headline": {"trades": 212, "net": 1234.5, "return_pct": 12.3, "pf": 1.31, "expectancy": 5.82,
               "win_rate": 0.41, "max_dd_pct": 7.9, "daily_sharpe": 1.12},
  "sides": {"long": {"trades": 212, "net": 1234.5, "pf": 1.31}, "short": {"trades": 0, "net": 0.0, "pf": null}},
  "holdout_split": null,
  "gates": [{"id": "G0", "name": "Integrity", "hard": true, "phase": 1, "active": true, "passed": true,
             "value": 0.04, "threshold": "untradable_stop_frac <= 0.3", "note": "...",
             "rules": [{"metric": "untradable_stop_frac", "op": "<=", "threshold": 0.3, "value": 0.04, "passed": true}]}],
  "dev_hard_gates_passed": false,
  "verdict": "REJECT", "final": false, "reasons": ["G3 DEV after base costs: pf 1.08 >= 1.2 failed"],
  "warnings": [],
  "context": {"median_hold_days": 0.2, "financing_flag": false, "rollover_entry_share": 0.0, "rollover_flag": false,
              "window_end_closes": 1, "untradable_stop": 3, "n_signals": 240, "halted": false},
  "critic": {"decision": null, "to": null, "reason": "", "notes": ""},
  "detail": {"folds": [...], "null": {...}, "plateau": {"n": 6, "neighbours": [...]}, "stress": {...}},
  "seal_note": "<splits.SEAL_NOTE>",
  "result_file": "lab_asian_range_breakout_xauusd_XAUUSD_M15_dev_result.json",
  "deploy_id": "...", "created_at": "2026-10-12T22:15:30+00:00"
}
```
- `label` is `splits.IN_SAMPLE_LABEL` for dev and `"HOLDOUT"` for holdout.
- `holdout_split` is `{"H_A": {trades, net, pf}, "H_B": {...}}` for holdout, and `null` for
  dev.
- `trial` on a holdout scorecard is the DEV trial number of that sha.
- Gate-level `value`/`threshold` come from `decisive_rule` (`threshold` = `Rule.describe()`).
  Both are `null` when no rule was evaluated.
- `dev_hard_gates_passed`:
  - on dev: no active hard gate failed among G0, G1, G3–G7 and G2's DEV rules;
  - on holdout: copied from the DEV scorecard, on every path including the holdout
    integrity-failure path (p1.2: `evaluate_holdout` passes it to `build_scorecard`; part 1
    recomputed it from gates that included the new failed G0).
- `context.financing_flag = median_hold_days > gates.FINANCING_FLAG_MEDIAN_HOLD_DAYS`.
- `context.rollover_flag = rollover_entry_share > 0`.
- `detail` is server-only. It never reaches the builder; `builder_view` drops it.

---

## 7. Ledger and budgets (C, `ledger.py`)

### 7.1 Store

The `interfaces.LedgerStore` protocol is a dumb byte store with POSIX paths relative to its
root:

```python
class LocalDirStore:                    # tests + local dev
    def __init__(self, root: Path): ...
    # write_once: os.open(path, O_CREAT|O_EXCL|O_WRONLY|getattr(os,"O_BINARY",0)) -> False on FileExistsError
    # claim_slot: same O_EXCL on lab/_slots/<interfaces.slot_filename(key)>
```

The integrator's `ModalStore` (`store_modal.py`) works like this:
- Root is `/results` on the `sajim-results` volume.
- `claim_slot` uses `modal.Dict("sajim-lab").put(key, value, skip_if_exists=True)`.
- `write_once` checks existence, then writes and calls `RESULTS_VOLUME.commit()`.

Layout under the store root:
```
lab/<family>/ledger/<utc_ts>_<request_id>_<record_type>.json      immutable records (write_once only)
lab/<family>/<sha12>/strategy.py | manifest.json | spec.md        code snapshot (write)
lab/<family>/<sha12>/check.json | dev_scorecard.json | holdout_scorecard.json   (write)
lab/_slots/<key with ':' -> '__'>.json                            slot claims (claim_slot only)
```

`utc_ts` is `interfaces.utc_stamp()`, for example `20261012T221530123456Z`.

The task text asked for `<utc_ts>_<request_id>.json`. This contract appends
`_<record_type>`, because one request writes several records (`budget_claim` + `dev_trial`)
under the same request_id.

### 7.2 Record (`sajim.lab.ledger/1`)

```json
{"schema": "sajim.lab.ledger/1", "record_type": "dev_trial", "family": "lab_x", "id": "lab_x",
 "request_id": "47ab16a0fcac6267e37f225bc660e411", "actor": "api", "code_sha256": "<hex>|null",
 "spec_sha256": "<hex>|null", "stage": "dev|holdout|null", "created_at": "2026-10-12T22:15:30.123456+00:00",
 "deploy_id": "...", "gates_version": "v1", "protocol_version": "v1", "payload": {...}}
```

`payload` by `record_type`:
- `check`: `{passed, reasons[], checks{...}}`
- `budget_claim`: `{key, kind: "trial"|"holdout_sha"|"holdout_touch", slot: int|null, job_id: str|null, ignored_client_fields: []}`
- `dev_trial`: `{trial, job_id, status: "done"|"failed", verdict, final, dev_hard_gates_passed, scorecard_path, result_file, error: str|null}`
- `holdout`: `{touch, job_id, status, verdict, holdout_trades, scorecard_path, result_file, error}`
- `review`: `{decision: "KEEP"|"DOWNGRADE", to, reason, verdict_before, verdict_after}`
- `verdict`: `{verdict, final: true, source: "scorecard"|"critic"|"budget", trial, holdout_touch}`

`actor` is one of `interfaces.ACTORS`.

### 7.3 Functions

```python
def make_record(record_type: str, *, family: str, lab_id: str, request_id: str, actor: str, payload: Mapping,
                code_sha256: str | None = None, spec_sha256: str | None = None, stage: str | None = None,
                deploy_id: str = "", now: datetime | None = None) -> dict
def put_record(store: LedgerStore, record: Mapping) -> str             # returns relpath; FileExistsError never escapes (returns existing path)
    # p1.2: when the existing file's bytes differ, the existing file is kept and a WARNING is logged
    # (a collision on <utc_ts>_<request_id>_<record_type> once hid a lost budget claim)
def list_records(store, family: str, *, record_type: str | None = None, code_sha256: str | None = None,
                 stage: str | None = None, request_id: str | None = None) -> list[dict]   # sorted by file name
def list_families(store) -> list[str]
def count_dev_trials(store, family) -> int            # distinct trial slots taken (slot OR budget_claim record)
def count_holdout_touches(store, family) -> int
def count_global_trials(store) -> int                 # sum of count_dev_trials over list_families()
def is_slot_taken(store, key: str) -> bool            # read_slot(key) is not None OR a budget_claim record with payload.key == key
def find_claim(store, family, request_id) -> dict | None              # dedup lookup
def claim_dev_trial(store, family, *, lab_id, request_id, code_sha256, actor="api", now=None) -> int | None
def claim_holdout(store, family, code_sha256, *, lab_id, request_id, actor="api", now=None) -> int
def budget_info(store, family, *, trial: int | None = None) -> BudgetInfo
```

`claim_dev_trial` (first-writer-wins). The ledger is the truth, so a Dict entry that expired
after 7 days cannot reset a budget:
```
if (c := find_claim(store, family, request_id)) is not None: return c["payload"]["slot"]   # dedup, no new trial
for n in 1..MAX_DEV_TRIALS_PER_FAMILY:
    key = trial_key(family, n)
    if is_slot_taken(store, key): continue
    if store.claim_slot(key, json bytes): put_record(budget_claim, key=key, slot=n); return n
return None                                    # API maps to 429 BudgetExhausted
```

`claim_holdout`:
1. Dedup first. If `find_claim` finds this request, return its touch.
2. `count_holdout_touches >= MAX_HOLDOUT_TOUCHES_PER_FAMILY` → raise
   `BudgetExhausted(code="holdout_burned")` (429).
3. `claim_slot(holdout_sha_key(sha))` lost, or `is_slot_taken` → raise `HoldoutAlreadyRun`
   (409).
4. Claim the first free `holdout_touch_key(family, n)`. If none is free, raise
   `BudgetExhausted` (the sha slot stays burned, which is harmless).
5. Write two `budget_claim` records (sha + touch) and return `n`.

The trial is claimed and recorded **before** the job is spawned (G1 note).

---

## 8. Orchestration of one evaluation (C, `evaluate.py`)

```python
def make_simulate(sframe: StageFrame, spec: InstrumentSpec, quote_rate: np.ndarray, *, max_hold_bars: int,
                  allow_short: bool, strategy_name: str, timeframe: str) -> SimulateFn
def check(sub: Submission, sframe: StageFrame, executor: SandboxExecutor, *,
          indicators_py: bytes | None = None) -> CheckReport
def evaluate_dev(sub: Submission, sframe: StageFrame, executor: SandboxExecutor, *, quote_rate: np.ndarray,
                 fx_desc: str, budget: BudgetInfo, deploy_id: str = "", indicators_py: bytes | None = None) -> Evaluation
def evaluate_holdout(sub: Submission, sframe: StageFrame, executor: SandboxExecutor, *, quote_rate: np.ndarray,
                     fx_desc: str, budget: BudgetInfo, dev_scorecard: Mapping, deploy_id: str = "",
                     indicators_py: bytes | None = None) -> Evaluation
def result_payload(result: BacktestResult) -> dict     # {"metadata", "total_trades", "metrics", "trades"}, json-safe, same shape as run_cloud_strategy
```

`check` runs these steps:
1. `contract.lint_source` and `contract.validate_manifest(require_shas=True)`. On issues it
   returns a failed report with `checks["lint"/"manifest"]=False` and no sandbox call.
2. Builds params with `contract.default_params(contract.parse_params(src))`.
3. Builds the plan with `causality.build_check_plan(len(frame), params, sha)`.
4. Runs the first sandbox job (`label="check"`):
   `executor.run(SandboxJob(frame_parquet=sandbox_io.frame_to_parquet(sframe.frame), ...))`.
5. Calls `sandbox_io.decode_result`, then `causality.assess_check(reveal_bars=stage != holdout)`,
   and merges the lint/manifest flags.
6. p1.2: if that passed (a zero signal count aside), runs a **second** job (`label="adaptive"`)
   with `causality.build_adaptive_plan(full1 events, n, params, sha, stage,
   min_bar=sframe.start_bar)` and folds it in with `causality.assess_adaptive`. DEV draws the
   same cuts (same sha, stage and window), so a free check predicts DEV's G0.

`evaluate_dev`:
1. Runs `check` steps 1-5 only (the first job, `label="check"`); the adaptive cuts move into
   the second job (step 3), so DEV stays at two jobs. If the check fails, every non-G0/G1
   metric is absent, the verdict comes from G0, and `result` is `None`.
2. **Base run:** `base_events = check.base_events`. That is `full1`, reused; there is no
   extra sandbox run. It is simulated with `simulate(base_events, SimOverrides())`, then
   `to_backtest_result(...)` and `performance_metrics`.
3. **Second job (p1.2):** `plateau_neighbours`, then ONE more sandbox job
   (`label="adaptive_neighbours"`): first the adaptive cuts
   (`k = min(ADAPTIVE_CUTS, MAX_RUNS_PER_JOB − #neighbours)`, so 6 with up to 10
   neighbours), then the neighbours (full frame), ≤ 16 runs. `assess_adaptive` runs first;
   if it fails, the evaluation takes the integrity-failure path (G0 → REJECT, no
   simulation). A failed neighbour run gets `None` events; neighbours stopped for time make
   G7 not computable (§6.3).
4. Simulator-only work: `stress_metrics`, `fold_metrics(window=sframe.window)` and
   `null_metrics(stage="dev")`.
5. Merges all metric dicts, then runs `evaluate_gates` → `verdict(…, "dev",
   budget.budget_exhausted, holdout_burned=budget.holdout_burned)` → `build_scorecard`.
   `budget.budget_exhausted` is `budget.trial >= MAX_DEV_TRIALS_PER_FAMILY`.

`evaluate_holdout`:
1. Runs `check` on the holdout frame: the check job (`full1` is the base) and, when it passed
   apart from a zero signal count, the adaptive-cut job (`stage="holdout"` seeds the sampler;
   reasons carry no bar index). A failure of either is the integrity-failure path: G0 fails,
   REJECT, `dev_hard_gates_passed` copied from DEV.
2. Simulates the base and 1.5x cost runs.
3. Computes `sample_metrics("holdout")` and
   `holdout_metrics(dev_headline=dev_scorecard["headline"])`.
4. DEV gate results are copied from `dev_scorecard["gates"]`; G2's `holdout_trades` and G8
   are evaluated now.
5. Runs `verdict(…, "holdout", ...)`.

There are at most 2 sandbox jobs per check, per DEV evaluation and per holdout, each at most
300 s (p1.2: the holdout's second job holds only the adaptive cuts; a check's second job
likewise). Every run in every job gets `PER_RUN_TIMEOUT_S` = 14 s including start-up (§2.1).
A `generate_signals` slower than that on the stage frame fails G0 already in the free check
(`sandbox: full1 timeout: ...`), and `lab.cli` prints a `RUNTIME:` hint whenever a reason says
a run was stopped for time.

The Modal `lab_job(job: dict) -> dict` function belongs to the integrator. It:
- loads data with `DataLoader(parquet_root=DATA_MOUNT)` and `splits.loader_bounds`;
- runs `prepare_frame` and `quote_rate_series`;
- builds a `ModalSandboxExecutor`;
- calls the `evaluate.*` function;
- writes `lab/<family>/<sha12>/*` and `results/<result_file>` (`result_payload`);
- writes the ledger `dev_trial` / `holdout` / `check` record.

It holds **no** API secret. It returns the job result shapes given in §9.

---

## 9. HTTP API (integrator adds to `api()` in `modal_runner.py`)

### 9.1 Auth and middleware

These secret keys are added to the `sajim-api` secret, which is attached to `api()` only:
`SAJIM_LAB_TOKEN` and `SAJIM_JUDGE_TOKEN`. Scopes are compared with `hmac.compare_digest`
on bytes, as `_token_ok` does:

| Header | Token | Scope |
|---|---|---|
| `X-Sajim-Token` (or `Authorization: Bearer`) | `SAJIM_API_TOKEN` | `read` (dashboard, run.py results/noise) |
| `X-Lab-Token` | `SAJIM_LAB_TOKEN` | `builder` |
| `X-Judge-Token` | `SAJIM_JUDGE_TOKEN` | `judge` (superset of builder) |

Middleware change:
- Paths starting with `/lab/` skip the global `X-Sajim-Token` check.
- They are capped at `interfaces.LAB_MAX_BODY_BYTES` (256 KiB). Anything over returns 413.
- Each lab route calls `_lab_scope(request) -> str`. With no valid token of any kind it
  returns 401.

Why: the builder session must never hold `SAJIM_API_TOKEN`. That token can read every
result file, including other families' holdout result JSONs. This is also why the lab CLI
**never** reads `sim/.env.local`.

### 9.2 Routes

Every lab error body is `{"detail": {"code": str, "message": str, "reasons": [str]}}`
(`LabError.detail()`).

| Route | Scopes | Body / query | 200 response |
|---|---|---|---|
| `POST /lab/check` | builder, judge | Submission body (below) | `{"job_id", "status": "queued", "kind": "lab_check", "request_id", "code_sha256", "deduplicated": bool}` |
| `POST /lab/evaluate` | `stage=dev`: builder, judge · `stage=holdout`: **judge only** | Submission body + `"stage"` | `{"job_id", "status": "queued", "kind": "lab_dev"\|"lab_holdout", "request_id", "code_sha256", "trial": int\|null, "holdout_touch": int\|null, "deduplicated": bool}` |
| `GET /lab/job/{job_id}` | read, builder, judge | — | `{"job_id", "status": "running"\|"done"\|"failed"\|"expired", "kind", "error"?, "result"?}` |
| `GET /lab/{family}` | read, builder, judge | — | family summary (below) |
| `GET /lab/{family}/scorecard/{sha12}` | read, builder (dev only), judge | `?stage=dev\|holdout` (default dev) | scorecard JSON (builder: `builder_view`) |
| `POST /lab/finalize` | judge | `{"lab_id", "family", "code_sha256", "request_id", "verdict", "source", "review": review.json\|null}` | `{"ok": true, "records": [relpath, ...], "verdict"}` (synchronous) |

`/lab/job/{job_id}` is declared **before** `/lab/{family}`; `job` cannot match
`LAB_ID_PATTERN`. It wraps the existing `_poll()` and `JOBS` entry, so the existing
`GET /job/{id}` keeps working for `X-Sajim-Token` callers. The builder cannot use
`GET /job/{id}` because it holds only the lab token. This is an intended deviation from
"poll the existing GET /job/{id}".

**Submission body** (check / evaluate):
```json
{"lab_id": "lab_x", "family": "lab_x", "stage": "dev", "request_id": "<see below>",
 "strategy_py": "<exact UTF-8 text of strategy.py>", "spec_md": "<exact UTF-8 text of spec.md>",
 "manifest": {...manifest.json object...}, "client": {"cli_version": "1"}}
```

Server validation runs in this order, and every check happens **before any budget claim**:
1. JSON body ≤ 256 KiB, else 413. `strategy_py` encoded as UTF-8 must be ≤
   `gates.MAX_SOURCE_BYTES`, else 413.
2. `lab_id`/`family` must match `LAB_ID_PATTERN`, `stage` must be in `splits.SCORED_STAGES`,
   and `request_id` must match `REQUEST_ID_RE`. Otherwise 400.
3. `code_sha256 = sha256(strategy_py.encode("utf-8"))`. It must equal
   `manifest.code_sha256`, and `spec_md` must likewise match. Otherwise 422 `sha_mismatch`.
4. `contract.lint_source` and `contract.validate_manifest(require_shas=True)`, which includes
   `check_universe` and therefore `SHORT_COVERAGE_REFUSED`. Failure gives 422
   `contract_refused` with `reasons`.
5. Keys such as `start_date`, `end_date`, `window`, `dates` or `risk_pct` are **ignored**.
   They are logged with `logger.warning` and listed in the claim record's
   `ignored_client_fields`. Windows always come from `splits`.
6. Scope check:
   - a read token on any POST → 403;
   - a builder token with `stage=holdout` → 403 `wrong_scope`;
   - a builder token reading a holdout scorecard or a `lab_holdout` job → 403.
7. dev: `request_id` must equal `derive_request_id(family, sha, "dev")`, else 400. Then
   `claim_dev_trial`; `None` → 429 `budget_exhausted`.
8. holdout: `request_id` must equal `derive_request_id(family, sha, "holdout")`. The store
   must hold `lab/<family>/<sha12>/dev_scorecard.json` with `dev_hard_gates_passed == true`
   and the same `code_sha256`; otherwise 412 `dev_not_passed`. Then `claim_holdout`, which
   raises 409 or 429.
9. check: `request_id` is a client `uuid4().hex`. Checks are free; dedup only covers retries
   of the same POST.

Dedup (`request_id`):
- The existing `_dedup` / `_remember` (`JOBS["req-<rid>"]`) returns the same `job_id` with
  `deduplicated: true`, and no new claim is made.
- If the remembered job is `failed`/`expired` and no `dev_trial`/`holdout` record with
  `status: "done"` exists for that request, the API **respawns** the job under the same
  claim. An infrastructure failure must not burn a trial twice.

Server defences beyond the steps above (p1.2, `sajim_quant/cloud/lab_api.py`; all before any
claim unless noted):
- **Family binding.** A `lab_id` is bound to the family of its first DEV/holdout claim
  (`lab/_bindings/<lab_id>.json`, first writer wins, with an atomic `JOBS["labbind-<lab_id>"]`
  entry). A later body naming another family gets 422 `family_mismatch`.
- **DEV-time snapshot.** A DEV job writes `lab/<family>/<sha12>/{strategy.py, spec.md,
  manifest.json}`. A holdout whose manifest differs in `id, family, code_sha256, symbol,
  timeframe, max_hold_bars, allow_short, warmup_bars, breadth_symbols` or whose `spec_sha256`
  differs from DEV gets 412 `dev_snapshot_mismatch` (no touch spent).
- **One attempt at a time, one configuration per claim.** Each attempt `n` of a request is a
  first-writer-wins spawn lock `JOBS["labspawn-<kind>-<family>-<sha>-<rid>-<n>"]` holding
  `{state: pending|spawned|spawn_failed, at, manifest_sha256, job_id?}`. A concurrent POST of
  the same request gets 503 (retry; it is then deduplicated). A respawn of a DEV/holdout
  request must carry the same `manifest_sha256` (canonical JSON of the manifest) as its first
  attempt, else 409 `request_conflict`. After `MAX_SPAWN_ATTEMPTS` (8) failed attempts the
  request gets 409 `attempts_exhausted` (not retried). An attempt that already wrote its
  scorecard is never run again: 409 `already_evaluated`.
- **Finalize.** 404 when no DEV scorecard exists for the sha, 422 `verdict_refused` for a
  verdict better than the scorecards support, 409 `already_finalized` for a second, different
  verdict; a retry with the same verdict returns the existing records.
- A check never takes over a `request_id` that already names another request's job (400).
- `GET /lab/{family}` returns at most the newest 500 ledger records (`records_truncated`).

**Job result** (`GET /lab/job/{id}` → `result`):
```json
{"success": true, "kind": "lab_check", "code_sha256": "...", "passed": false,
 "reasons": ["causality: ..."], "checks": {"lint": true, "manifest": true, "sandbox": true, "schema": true,
 "determinism": true, "causality": false, "scramble": true, "min_signals": true}, "deploy_id": "..."}

{"success": true, "kind": "lab_dev" | "lab_holdout", "code_sha256": "...", "stage": "dev",
 "scorecard": {...}, "result_file": "lab_x_XAUUSD_M15_dev_result.json", "deploy_id": "..."}
```
- `success: false` + `error` means an infrastructure failure. A failed gate is still
  `success: true`.
- Builder scope gets `scorecard = builder_view(...)`. For `lab_holdout` jobs it gets 403.

**Family summary** (`GET /lab/{family}`):
```json
{"family": "lab_x", "ids": ["lab_x"], "n_trials": 2, "max_trials": 3, "holdout_touches": 0,
 "max_holdout_touches": 3, "holdout_burned": false, "n_trials_global": 7,
 "scorecards": [{"sha12", "stage", "verdict", "final", "trial", "created_at"}],
 "records": [<ledger records>]}
```
In builder scope:
- holdout scorecard entries keep only `{sha12, stage, created_at}`;
- `holdout`, `review` and `verdict` records keep only `{record_type, created_at, payload: {touch}}`.

Status codes, all lab routes:

| Code | Meaning |
|---|---|
| 400 | malformed |
| 401 | no valid token |
| 403 | wrong scope |
| 404 | unknown family / sha / job |
| 409 | holdout already run for this sha; `request_conflict`, `attempts_exhausted`, `already_evaluated`, `already_finalized` (see above) |
| 412 | holdout without a passing DEV scorecard for this sha; `dev_snapshot_mismatch` |
| 413 | too large |
| 422 | contract / sha / universe refusal, `family_mismatch`, `verdict_refused` (no budget used) |
| 429 | DEV trials or holdout touches exhausted |
| 502/503/504 | retryable |

**429 is never retried** by lab clients.

---

## 10. Laptop CLI (D, `cli.py`)

`python -X utf8 -m sajim_quant.lab.cli <subcommand>`. The lab folder is
`strategies/generated/$SAJIM_LAB_ID/` under the repo root (`Path(__file__).parents[2]`).
`SAJIM_LAB_ID` is required; if it is missing, exit 2.

Config:
- `SAJIM_API_URL`, falling back to `SAJIM_MODAL_API_URL`. p1.2: it must be `https://`;
  `http://` is accepted only for `127.0.0.1` and `localhost` (a local test server); URLs with
  credentials are refused. Otherwise `load_config` raises `LabApiError(code="not_configured")`
  (exit 2) without echoing the URL.
- `SAJIM_LAB_TOKEN`, falling back to the contents of `%USERPROFILE%\.sajim\lab_token`.
- It **never** reads `judge_token`, `SAJIM_API_TOKEN` or `sim/.env.local`.
- Token values are never printed.

```python
class LabApiError(Exception): status: int; code: str; detail: dict
class LabClient:
    def __init__(self, base_url: str, *, lab_token: str | None = None, judge_token: str | None = None,
                 timeout: float = 60, retries: int = 4): ...   # sends exactly one of X-Judge-Token / X-Lab-Token (judge wins)
    def check(self, body: dict) -> dict
    def evaluate(self, body: dict) -> dict
    def job(self, job_id: str) -> dict                       # GET /lab/job/{id}
    def wait(self, job_id: str, *, timeout: float = 900, interval: float = 3, on_change=None) -> dict
    def family(self, family: str) -> dict
    def scorecard(self, family: str, sha12: str, stage: str = "dev") -> dict
    def finalize(self, body: dict) -> dict
def load_config() -> tuple[str, str]                         # (api_url, lab_token); raises LabApiError(code="not_configured")
def api_url_problem(url: str) -> str | None                  # p1.2: None for https:// or http://127.0.0.1|localhost
def runtime_hint(reasons) -> str | None                      # p1.2: the RUNTIME: line, or None
def stamp_manifest(folder: Path) -> dict                     # recompute spec/code sha256, rewrite manifest.json in place (key order kept)
def build_body(folder: Path, *, stage: str | None, kind: str) -> dict
def render_gate_table(scorecard: Mapping, *, trial_line: bool = True) -> str
def main(argv: list[str] | None = None) -> int
```

Retries happen only on network errors and 502/503/504, with backoff `min(30, 2**attempt)`.
Other statuses raise `LabApiError` with `detail.code`.

| Subcommand (exact argv) | Does | Exit codes |
|---|---|---|
| `check` | local `contract.lint_path` on strategy.py + manifest.json → `stamp_manifest` → POST /lab/check (uuid4 request_id) → `wait` → prints `CHECK PASS sha <sha12>` or `CHECK FAIL sha <sha12>` + one `- reason` per line | 0 pass · 1 fail · 2 local lint/manifest/usage · 3 network/timeout · 4 server refusal (4xx) · 5 job infra failure |
| `dev` | lint → stamp → POST /lab/evaluate stage=dev (`derive_request_id`) → record `pending` in build_log → `wait` → save `scorecards/dev_<sha12>.json` (builder view) → print gate table | 0 all hard DEV gates passed · 1 trial done, gates failed / NOT_PROVEN · 2 · 3 (resume with `status`) · 4 (429 budget, 403, 412, 422) · 5 |
| `summary` / `summary --sha <sha12>` | prints the gate table of the newest (or given) local DEV scorecard + a `GET /lab/{family}` counts line (local fallback if offline) | 0 · 2 no scorecard |
| `done --final <sha12 or sha64>` | re-lints locally (PostToolUse cannot undo a write), verifies the sha prefix matches current strategy.py, sets `build_log.final_sha` | 0 · 2 lint fails / sha mismatch |
| `review --file review.json` | validates `<lab folder>/review.json` (`sajim.lab.review/1`, below), prints `REVIEW OK` or the problems | 0 valid · 2 invalid |
| `status` | resumes `build_log.pending` (GET /lab/job), finishing exactly as the original command would | same as the original command |

Gate table, printed by `dev` and `summary`. It is the only performance view the builder
gets, and it never shows per-trade, per-neighbour, per-fold or holdout numbers:
```
SAJIM LAB  lab_x  sha 1a2b3c4d5e6f  DEV trial 2/3  [IN-SAMPLE]
family holdout touches 0/3 | global lab trials 7
trades 212 | net +1234.50 | PF 1.31 | max DD 7.9% | daily Sharpe 1.12   (IN-SAMPLE)
GATE  NAME                      RESULT  DECISIVE
G0    Integrity                 PASS    untradable_stop_frac 0.04 <= 0.3
G3    DEV after base costs      FAIL    pf 1.08 >= 1.2
G4b   PSR / DSR                 --      phase 2
G8    Sealed holdout            --      runner only (judge token)
VERDICT (provisional, IN-SAMPLE): REJECT - revisions left: 1 (spec-fix or bug-fix only)
```
- There is one row per gate in `gates.GATES` order. RESULT is `PASS`, `FAIL` or `--`.
- p1.2: when any reason says a sandbox run was stopped for time (`... timeout: ...`,
  `... budget: ...`, "stopped for time"), `check` and the gate table end with one
  `RUNTIME:` line (`cli.runtime_hint`) that states the 14 s per-run limit and what to do.
- Numbers use `:g`, or `n/a` for `null`.
- The verdict line reads `final` when `final` is true.

Files written in the lab folder. The tool writes these; Claude does not, and the hooks
govern only Claude's tools:
- `manifest.json`: the sha fields, written by `stamp_manifest`.
- `scorecards/dev_<sha12>.json`.
- `build_log.json`, `sajim.lab.build_log/1`. It is rewritten atomically (temp file +
  `os.replace`). Writers are the cli and run.py, never concurrently:
  ```json
  {"schema": "sajim.lab.build_log/1", "lab_id": "...", "family": "...", "final_sha": null,
   "pending": {"kind": "lab_dev", "job_id": "fc-...", "request_id": "...", "code_sha256": "...", "started_at": "..."},
   "events": [{"ts": "...", "source": "cli" | "runner", "event": "check" | "dev" | "done" | "session" | "holdout" | "critic" | "finalize", "...": "..."}]}
  ```

`review.json`, `sajim.lab.review/1`, is written by the critic with the Write tool. It is the
only file the critic may write:
```json
{"schema": "sajim.lab.review/1", "lab_id": "lab_x", "code_sha256": "<64 hex>", "decision": "KEEP" | "DOWNGRADE",
 "to": null | "NOT_PROVEN" | "REJECT", "reason": "<= 1000 chars", "mismatches": ["<= 10 items"],
 "smells": ["<= 10"], "cost_sensitivity": "<= 500 chars", "next_tests": ["<= 3"]}
```
- `to` is required iff `decision == "DOWNGRADE"`.
- Any `gates.contains_forbidden_label` hit is invalid.

---

## 11. Runner (D, `run.py`)

```
python -X utf8 -m sajim_quant.lab.run "<idea>" SYMBOL TF [--id lab_x] [--family lab_f] [--yes]
                                       [--no-holdout] [--resume lab_x]
```

```python
def find_claude_exe() -> Path
    # env SAJIM_CLAUDE_EXE, else shutil.which("claude.exe"), else %USERPROFILE%\.local\bin\claude.exe; refuse .cmd/.bat/.ps1
def child_env(base: Mapping[str, str], *, lab_id: str, stage: str, lab_token: str, api_url: str) -> dict[str, str]
    # only interfaces.CHILD_ENV_ALLOWLIST keys; PYTHONUTF8=1; asserts none of CHILD_ENV_FORBIDDEN
def claude_argv(exe: Path, prompt: str, *, settings: Path, max_turns: int) -> list[str]
def preflight(exe: Path, env: Mapping[str, str]) -> None      # raises PreflightError
def parse_stream_line(line: str) -> dict | None
@dataclass class SessionOutcome: subtype: str; is_error: bool; num_turns: int; duration_ms: int; api_key_source: str;
                                 rate_limited: bool; notional_cost_usd: float | None
def run_session(argv: list[str], env: Mapping[str, str], cwd: Path, *, wall_timeout_s: int = 1500,
                on_event=None) -> SessionOutcome
def main(argv: list[str] | None = None) -> int
def gate_results_from_json(gates_json) -> list[GateResult]   # p1.2: delegates to scorecard.gate_results_from_json
def spec_changed_since_approval(request: Mapping, spec_raw: bytes | None) -> str   # p1.2: "yes" | "no" | "unknown"
```

The exact claude argv (P0), with `cwd` = repo root, `stdin=DEVNULL` and `env=child_env(...)`:
```
[<claude.exe>, "-p", "/lab-build <id> --stage spec"   |   "/lab-build <id> --stage code"   |   "/lab-review <id>",
 "--settings", "<repo>\\.claude\\lab\\settings.json",
 "--tools", "Read,Edit,Write,Glob,Grep,Bash,Skill", "--strict-mcp-config",
 "--permission-mode", "dontAsk", "--permission-prompts", "none",
 "--max-turns", "20" | "40" | "15",
 "--output-format", "stream-json", "--verbose"]
```
- The child env sets `SAJIM_LAB_STAGE` to `spec`, `code` or `review`, and `SAJIM_LAB_ID`.
- The process is spawned directly, never through `cmd.exe` or `claude.cmd`.
- On wall-clock timeout (`SESSION_WALL_TIMEOUT_S`), kill **only that PID tree**.

Preflight (exit 2 on any failure):
- `claude.exe --version` must be ≥ `MIN_CLAUDE_VERSION`.
- `claude.exe auth status --json` must give `loggedIn` true, `authMethod == "claude.ai"` and
  `subscriptionType == "max"`. Read **only** those three keys; never log `email`/`orgId` or
  the raw JSON.
- No other lab run may be active: the lock file `%TEMP%\sajim_lab_run.lock` uses O_EXCL.
- Fewer than `MAX_BUILDS_PER_DAY` `request.json` files may have today's UTC `created_at`.
- The lab token is read; the judge token file is checked to exist. The judge token is read
  only at the holdout and finalize steps.

stream-json events parsed:
- `{"type": "system", "subtype": "init"}`:
  - `apiKeySource` must be `"none"`, `mcp_servers` must be `[]`, and `set(tools)` must be a
    subset of the 7 tools. Otherwise kill immediately with exit 2.
- `{"type": "rate_limit_event"}` records `rate_limited` and its status.
- `{"type": "result"}` records `subtype`:
  - `"success"`;
  - **`"error_max_turns"`** is its own outcome, `max_turns`;
  - `"error_during_execution"`;
  - It also records `num_turns`, `duration_ms` and `total_cost_usd` (labelled notional; it
    is not a Max usage meter).
- Assistant and user message content is **not** logged.

Steps:
1. Validate SYMBOL/TF with `contract.check_universe`. Derive the id:
   `"lab_" + slug(first 4 words of the idea) + "_" + symbol.lower()`, truncated to 44
   chars, with `_2`, `_3`, … if the folder exists. p1.2: the slug skips any word that would
   complete a forbidden label, and `--id` / `--family` containing one are refused (exit 2).
2. Write `request.json`:
   ```json
   {"schema": "sajim.lab.request/1", "lab_id", "family", "description", "symbol", "timeframe", "created_at",
    "revision_notes": [], "approved_spec_sha256": null, "runner_version": "1"}
   ```
   The description is stored as data.
3. Run the spec session. Print `spec.md`, then prompt
   `Approve / Revise <note> / Cancel [a/r/c]:` with plain `input()`.
   - `--yes` approves.
   - Revise appends the note to `revision_notes` and reruns the spec session, at most 3
     times.
   - Approval stores `approved_spec_sha256` and (p1.2) `approved_spec_head_sha256`, the sha256
     of spec.md above the `## 8. Trials` heading (the whole file when there is none; same
     heading regex as `guard_paths.py`).
4. Run the code session.
5. **Final sha:** `build_log.final_sha`, else the newest local DEV scorecard. Re-lint
   locally. Fetch the full DEV scorecard with the judge token
   (`GET /lab/{family}/scorecard/{sha12}?stage=dev`).
6. **Critic, before the holdout:** first (p1.2) the runner records
   `spec_changed_since_approval` = `"yes" | "no" | "unknown"` (spec.md above `## 8. Trials`
   now vs `approved_spec_head_sha256`, with a BOM and CRLF normalised away as guard_paths does;
   an older request.json without it can only prove "no" when the whole file is unchanged) in `request.json` and as a `spec_check` build_log event,
   both of which the critic reads. Then the `/lab-review <id>` session with
   `SAJIM_LAB_STAGE=review` writes `review.json`. The critic therefore never sees a holdout number (P1 acceptance:
   "transcripts contain no holdout numbers").
7. **Holdout**, judge token, only if all of these hold:
   - `dev_hard_gates_passed`;
   - the critic did not downgrade to REJECT;
   - the family is not burned;
   - `--no-holdout` was not given.

   It POSTs `/lab/evaluate` with stage holdout, waits, and saves the full scorecard to
   `scorecards/holdout_<sha12>.json`.
8. **Noise**, report only. Failures are logged and never change the verdict.
   - GET `/results/<file>` for the dev and holdout result files with the API token from
     run.py's **own** environment (`cloud.client._config`), then `cloud.client.save_result`.
   - Run `python -X utf8 -m sajim_quant.cloud.client noise --result data/results/<file>` as
     a subprocess with run.py's own env, never the child env.
9. **Final verdict:**
   - With a holdout: the holdout scorecard verdict.
   - Without one: `scorecard.verdict(dev gates, "dev", budget_exhausted=True, holdout_burned=…)`.
   - In both cases, then `gates.apply_downgrade(…, critic.to)`.
   - Send `POST /lab/finalize` (judge) with `derive_request_id(family, sha, "finalize")`.
10. Write `README.md`, in this section order:
    - `# <name> — <VERDICT>`;
    - a meaning line (CANDIDATE = `gates.CANDIDATE_MEANING`);
    - `Trial k of 3 · holdout touches n/3 · global lab trials N`;
    - the id line, then `spec changed since approval (spec.md above '## 8. Trials'): yes|no|unknown`;
    - `## Idea (verbatim)`;
    - `## DEV (IN-SAMPLE) vs HOLDOUT`: a table with rows trades / net / return % / PF /
      expectancy / win rate / max DD % / daily Sharpe and columns DEV (IN-SAMPLE) / HOLDOUT
      / H-A / H-B;
    - `## Gates`;
    - `## Cost model` (echo + break-even multiplier);
    - `## Critic`;
    - `## Context (report only)` (noise summary if available);
    - `## Protocol` (versions + `splits.SEAL_NOTE`);
    - `## Files`.

    The README never contains `FORBIDDEN_LABELS`.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | a verdict was reached and written |
| 2 | preflight / usage refusal (auth, version, env, lock, daily cap, universe) |
| 3 | Claude session failed with no DEV scorecard (error, `max_turns`, rate limit — never retried in a loop) |
| 4 | cancelled at approval |
| 5 | server/network failure after retries (resume with `--resume <id>`) |
| 6 | budget refusal (429/409) when no verdict can be formed |

`--resume <id>` skips any step that `build_log.json` shows as complete.

---

## 12. Claude layer (E)

### 12.1 Files

| File | Content |
|---|---|
| `.claude/lab/settings.json` | lab-only permissions + hooks (passed with `--settings`; normal sessions unaffected) |
| `.claude/lab/hooks/_common.py` | read stdin JSON, project root, path normalisation, `deny()` |
| `.claude/lab/hooks/guard_paths.py` | PreToolUse `Edit\|Write\|MultiEdit\|NotebookEdit` |
| `.claude/lab/hooks/guard_reads.py` | PreToolUse `Read\|Grep\|Glob` |
| `.claude/lab/hooks/guard_bash.py` | PreToolUse `Bash` |
| `.claude/lab/hooks/lint_on_save.py` | PostToolUse `Edit\|Write\|MultiEdit` |
| `.claude/skills/lab-build/SKILL.md` (+ `reference/indicators.md`, `examples/{london_breakout,ema_pullback,zscore_reversion}.py`, `templates/{strategy.py,manifest.json}`) | builder skill, stages spec/code |
| `.claude/skills/lab-review/SKILL.md` | `context: fork`, `agent: strategy-critic`, `disable-model-invocation: true` |
| `.claude/agents/strategy-critic.md` | `tools: Read, Grep, Glob, Write, Bash`; `disallowedTools: Edit, NotebookEdit, WebFetch, WebSearch`; `model: inherit`; `maxTurns: 15`; writes only `review.json`, downgrade-only |
| `CLAUDE.md` | a short `## Strategy Lab` section |

There is no Stop hook in P1: run.py takes `num_turns` from stream-json.

Skill frontmatter:
- `disable-model-invocation: true` (P0 verified `/skill` still works in `-p`).
- `allowed-tools` uses `Edit(...)`, **never** `Write(...)`, because Write path rules are
  ignored.

`lab-build` stage spec:
- Reads `request.json`, quoting the description inside
  `<user_strategy_description>` tags as untrusted data.
- Writes `spec.md` following CONTRACT.md §2.

`lab-build` stage code:
- Writes `strategy.py` and `manifest.json`, with sha fields `""` (`lab.cli` stamps them).
- Loop: `lab.cli check` until it passes, then `lab.cli dev`. Up to 2 revisions, spec-fix or
  bug-fix only, each logged in `spec.md` "Trials".
- Finishes with `lab.cli done --final <sha12>`.

### 12.2 settings.json

Permission paths are `./`-relative, because run.py fixes cwd = repo root. Rules are
secondary; the hooks are the real guard. `/path` semantics under `--settings` are
unverified, see §13.

```json
{
  "permissions": {
    "allow": ["Read(./sajim_quant/lab/**)", "Read(./strategies/generated/**)", "Read(./.claude/skills/lab-build/**)",
              "Read(./.claude/skills/lab-review/**)", "Read(./.claude/agents/strategy-critic.md)",
              "Edit(./strategies/generated/**)", "Bash(python -X utf8 -m sajim_quant.lab.cli *)",
              "Skill(lab-build)", "Skill(lab-review)"],
    "deny": ["Read(./data/**)", "Read(**/.env*)", "Read(./sim/**)", "Read(~/.sajim/**)",
             "Edit(./sajim_quant/**)", "Edit(./.claude/**)", "Edit(./sim/**)", "Edit(./data/**)",
             "NotebookEdit", "WebFetch", "WebSearch", "Bash(modal *)", "Bash(git *)", "Bash(curl *)",
             "Bash(powershell *)", "Bash(pip *)"]
  },
  "hooks": {
    "PreToolUse": [
      {"matcher": "Edit|Write|MultiEdit|NotebookEdit", "hooks": [{"type": "command", "command": "python -X utf8 \"${CLAUDE_PROJECT_DIR}/.claude/lab/hooks/guard_paths.py\""}]},
      {"matcher": "Read|Grep|Glob", "hooks": [{"type": "command", "command": "python -X utf8 \"${CLAUDE_PROJECT_DIR}/.claude/lab/hooks/guard_reads.py\""}]},
      {"matcher": "Bash", "hooks": [{"type": "command", "command": "python -X utf8 \"${CLAUDE_PROJECT_DIR}/.claude/lab/hooks/guard_bash.py\""}]}
    ],
    "PostToolUse": [
      {"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": "python -X utf8 \"${CLAUDE_PROJECT_DIR}/.claude/lab/hooks/lint_on_save.py\""}]}
    ]
  }
}
```

### 12.3 Hook I/O (P0-verified fields)

**stdin** is one JSON object:
```json
{"session_id": "...", "transcript_path": "...", "cwd": "F:\\Tete\\SAJIM STRATEGIES", "permission_mode": "dontAsk",
 "hook_event_name": "PreToolUse", "tool_name": "Edit",
 "tool_input": {"file_path": "F:\\Tete\\SAJIM STRATEGIES\\strategies\\generated\\lab_x\\strategy.py", "...": "..."}}
```

The path fields in `tool_input`:

| Tool | Field |
|---|---|
| Read / Edit / Write / MultiEdit | `file_path` |
| NotebookEdit | `notebook_path` |
| Grep | `path` (optional) |
| Glob | `path` (optional) |
| Bash | `command` |

PostToolUse adds `tool_response`.

Env:
- `CLAUDE_PROJECT_DIR` arrives with **forward slashes** (`F:/Tete/SAJIM STRATEGIES`).
- `SAJIM_LAB_ID` and `SAJIM_LAB_STAGE` come from run.py.

Project root = `Path(__file__).resolve().parents[3]`; for `.claude/lab/hooks/x.py` that is
the repo. p1.2: every hook sets `sys.dont_write_bytecode = True` before importing `_common`
(and lint_on_save before importing `sajim_quant`), so no `__pycache__` lands in
`.claude/lab/hooks`; the test helpers do the same for skills and examples they load.
`SAJIM_LAB_ID` is checked with `LAB_ID_RE.fullmatch`. If `CLAUDE_PROJECT_DIR` is set and disagrees after `resolve()` + `normcase`, deny.

Path normalisation:
- A relative path is joined to stdin `cwd`.
- Then `Path(p).resolve(strict=False)`.
- Comparisons use `os.path.normcase(str(...))` and `is_relative_to`. This is
  case-insensitive on Windows and follows junctions.

**Deny** (PreToolUse) prints exactly this one line to stdout and exits 0:
```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "<why>"}}
```
- **Allow** prints nothing and exits 0.
- A hook **never** emits `"allow"`, because that would bypass the permission rules.
- Malformed stdin, an unknown tool or an internal exception → deny (fail closed).

**PostToolUse lint** (`lint_on_save.py`):
- It runs only for `strategy.py` / `manifest.json` in the lab folder.
- It adds the repo root to `sys.path` and calls `sajim_quant.lab.contract.lint_path`.
- Violations go to stderr, one per line (`LintIssue.format`), with exit **2**.
- A clean file exits 0 silently.

Rules:
- **guard_paths:**
  - Missing `SAJIM_LAB_ID`/`SAJIM_LAB_STAGE` → deny.
  - Allow only when the resolved parent is `<root>/strategies/generated/<SAJIM_LAB_ID>` and
    the name is in `interfaces.LAB_WRITABLE[stage]`. Spec stage: `spec.md`. Code stage:
    `spec.md`, `strategy.py`, `manifest.json`. Review stage: `review.json`.
- **guard_reads:** default deny. Allowed roots are:
  - `<root>/sajim_quant/lab/`
  - `<root>/.claude/skills/lab-build/`
  - `<root>/.claude/skills/lab-review/`
  - `<root>/.claude/agents/strategy-critic.md`
  - `<root>/strategies/generated/<SAJIM_LAB_ID>/`

  Always denied, even inside those roots:
  - any path component starting with `.env`;
  - `~/.sajim/**`, `data/**`, `sim/**`;
  - other strategy folders;
  - `scorecards/holdout_*`, `README.md`.

  Grep/Glob without `path` resolves to `cwd`, which is the repo root, and is denied. A
  Grep/Glob path that is the lab folder, or an ancestor of `scorecards/`, is denied when
  any `scorecards/holdout_*` file exists.
- **guard_bash:**
  - Deny if the command contains any of `; | & > < \` $ ( )`, a newline or a CR.
  - Otherwise, after `strip()`, it must fully match one of the stage patterns:
    - spec stage: none;
    - code stage: `^python -X utf8 -m sajim_quant\.lab\.cli (check|dev|status|summary( --sha [0-9a-f]{12})?|done --final [0-9a-f]{12}(?:[0-9a-f]{52})?)$`;
    - review stage: `^python -X utf8 -m sajim_quant\.lab\.cli (summary( --sha [0-9a-f]{12})?|review --file review\.json)$`.

E's tests (`test_hooks.py`) pipe sample events through each hook with `subprocess` and check
the following. Every case runs with a forward-slash `CLAUDE_PROJECT_DIR` and a repo path that
contains a space:
- a write outside the folder is denied;
- `../` escapes are denied;
- case-variant paths are handled;
- `.env` reads are denied;
- `data/parquet` reads are denied;
- holdout scorecard reads are denied;
- `modal deploy` is denied;
- `python -X utf8 -m sajim_quant.lab.cli check; whoami` is denied;
- lint exits 2 on `shift(-1)`;
- malformed JSON is denied.

---

## 13. Open issues for the integrator / acceptance (not blockers for builders)

1. `ModalSandboxExecutor` must cap bytes read per output file. If modal 1.6
   `sb.filesystem.read_bytes` cannot bound a read, the integrator must prove that a 1 GB
   `out/full1.events.npy` cannot OOM the trusted job, for example by checking the size from
   inside a second fresh process before reading.
2. Permission-rule path anchors (`./x` vs `/x`) under `--settings` are unverified. The hooks
   are authoritative, so a wrong anchor fails safe (deny), but a too-broad user-level allow
   rule would be merged. Consider `--setting-sources project,local` in the acceptance run;
   it is not frozen here because P0 did not verify it.
3. A slot counts as taken when either the Dict entry or a `budget_claim` record exists, so
   the Dict's 7-day idle expiry cannot reset a budget. One narrow gap remains: if the API
   crashes between `claim_slot` and `put_record`, the slot has no record. It stays burned
   only until its Dict entry expires, after which it could be claimed again.
   `claim_dev_trial` and `claim_holdout` write the record immediately after the claim and
   before spawning the job. The integrator accepts this window.
4. Sandbox image versions (numpy/pandas/pyarrow) should be pinned to the same versions as
   the trusted image so determinism holds across checks. `status.versions` records them.
5. p1.2: measure child start-up inside the Modal sandbox (`status.timings` per run) in the
   acceptance run. `PER_RUN_TIMEOUT_S` = 14 s assumes about 1-3 s; if Modal start-up is much
   slower, lower `MAX_RUNS_PER_JOB` for the DEV second job (fewer adaptive cuts) rather than
   raising the per-run limit past the job budget.
