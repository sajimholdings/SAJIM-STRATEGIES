"""ledger.py: immutable per-record files, first-writer-wins slot claims, budget semantics."""

import json
import re
import threading
from datetime import datetime, timezone

import pytest

from sajim_quant.lab import gates, ledger as L
from sajim_quant.lab.interfaces import (
    HTTP_BUDGET_EXHAUSTED, HTTP_HOLDOUT_ALREADY_RUN, BudgetExhausted, HoldoutAlreadyRun, LedgerStore,
    derive_request_id, holdout_sha_key, slot_filename, trial_key,
)

FAM = "lab_test_family"
SHA_A, SHA_B, SHA_C, SHA_D = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)


@pytest.fixture
def store(tmp_path):
    return L.LocalDirStore(tmp_path / "results")


def dev_rid(sha, family=FAM):
    return derive_request_id(family, sha, "dev")


def hold_rid(sha, family=FAM):
    return derive_request_id(family, sha, "holdout")


# ── store ───────────────────────────────────────────────────────────────────

def test_local_store_is_a_ledger_store(store):
    assert isinstance(store, LedgerStore)


def test_write_once_and_protected_paths(store):
    assert store.write_once("lab/x/ledger/r.json", b"1") is True
    assert store.write_once("lab/x/ledger/r.json", b"2") is False
    assert store.read("lab/x/ledger/r.json") == b"1"
    with pytest.raises(ValueError):
        store.write("lab/x/ledger/r.json", b"3")                 # records are immutable
    with pytest.raises(ValueError):
        store.write(f"lab/_slots/{slot_filename(trial_key(FAM, 1))}", b"x")
    store.write("lab/x/abc/dev_scorecard.json", b"a")
    store.write("lab/x/abc/dev_scorecard.json", b"b")          # artefacts may be replaced
    assert store.read("lab/x/abc/dev_scorecard.json") == b"b"
    for bad in ("../escape.json", "/abs.json", "lab/../x", "lab\\x", "C:/x", ""):
        with pytest.raises(ValueError):
            store.write_once(bad, b"x")
    assert store.listdir("lab/x") == ["abc", "ledger"]
    assert store.listdir("nope") == [] and store.read("nope/x") is None


def test_slot_files_are_windows_safe(store, tmp_path):
    assert store.claim_slot(holdout_sha_key(SHA_A), b"{}") is True
    assert store.claim_slot(holdout_sha_key(SHA_A), b"{}") is False
    names = store.listdir("lab/_slots")
    assert names == [f"holdout_sha__{SHA_A}.json"] and ":" not in names[0]


# ── records ─────────────────────────────────────────────────────────────────

def test_make_and_put_record(store):
    rec = L.make_record("check", family=FAM, lab_id=FAM, request_id="0123456789abcdef", actor="api",
                        payload={"passed": True, "reasons": [], "checks": {}}, code_sha256=SHA_A, stage="dev")
    assert rec["schema"] == "sajim.lab.ledger/1" and rec["gates_version"] == gates.GATES_VERSION
    rel = L.put_record(store, rec)
    assert re.fullmatch(rf"lab/{FAM}/ledger/\d{{8}}T\d{{12}}Z_0123456789abcdef_check\.json", rel)
    assert L.put_record(store, rec) == rel                    # existing file kept, never overwritten
    assert json.loads(store.read(rel)) == rec
    assert L.list_records(store, FAM, record_type="check") == [rec]
    assert L.list_records(store, FAM, code_sha256=SHA_B) == []
    for bad in (dict(record_type="nope"), dict(actor="hacker"), dict(request_id="x"), dict(stage="purge"),
                dict(code_sha256="xyz")):
        kw = dict(record_type="check", family=FAM, lab_id=FAM, request_id="0123456789abcdef", actor="api",
                  payload={})
        kw.update(bad)
        rt = kw.pop("record_type")
        with pytest.raises(ValueError):
            L.make_record(rt, **kw)


# ── DEV trial budget ────────────────────────────────────────────────────────

def test_fourth_dev_trial_is_refused_429(store):
    for n, sha in enumerate((SHA_A, SHA_B, SHA_C), start=1):
        assert L.claim_dev_trial(store, FAM, lab_id=FAM, request_id=dev_rid(sha), code_sha256=sha) == n
    assert L.claim_dev_trial(store, FAM, lab_id=FAM, request_id=dev_rid(SHA_D), code_sha256=SHA_D) is None
    with pytest.raises(BudgetExhausted) as exc:
        L.require_dev_trial(store, FAM, lab_id=FAM, request_id=dev_rid(SHA_D), code_sha256=SHA_D)
    assert exc.value.status == HTTP_BUDGET_EXHAUSTED == 429
    assert L.count_dev_trials(store, FAM) == 3
    # a retried request is deduplicated: same trial, no new slot
    assert L.claim_dev_trial(store, FAM, lab_id=FAM, request_id=dev_rid(SHA_B), code_sha256=SHA_B) == 2
    claims = L.list_records(store, FAM, record_type="budget_claim")
    assert [c["payload"]["slot"] for c in claims] == [1, 2, 3]
    info = L.budget_info(store, FAM, trial=3)
    assert (info.n_trials_family, info.budget_exhausted, info.holdout_burned) == (3, True, False)
    assert L.budget_info(store, FAM, trial=1).budget_exhausted is False


def test_expired_slot_claim_cannot_reset_the_budget(store, tmp_path):
    L.claim_dev_trial(store, FAM, lab_id=FAM, request_id=dev_rid(SHA_A), code_sha256=SHA_A,
                      ignored_client_fields=["start_date"])
    # simulate a Modal Dict entry expiring after 7 idle days: the slot claim vanishes, the record stays
    (store.root / "lab" / "_slots" / slot_filename(trial_key(FAM, 1))).unlink()
    assert L.is_slot_taken(store, trial_key(FAM, 1)) is True
    assert L.claim_dev_trial(store, FAM, lab_id=FAM, request_id=dev_rid(SHA_B), code_sha256=SHA_B) == 2
    rec = L.find_claim(store, FAM, dev_rid(SHA_A))
    assert rec["payload"]["ignored_client_fields"] == ["start_date"]


def test_crash_between_claim_and_record_is_recovered_by_the_same_request(store):
    key = trial_key(FAM, 1)
    store.claim_slot(key, json.dumps({"request_id": dev_rid(SHA_A)}).encode())   # claimed, record never written
    assert L.claim_dev_trial(store, FAM, lab_id=FAM, request_id=dev_rid(SHA_B), code_sha256=SHA_B) == 2
    assert L.claim_dev_trial(store, FAM, lab_id=FAM, request_id=dev_rid(SHA_A), code_sha256=SHA_A) == 1
    assert L.count_dev_trials(store, FAM) == 2


def test_families_have_separate_budgets_and_global_count(store):
    other = "lab_other_family"
    for sha in (SHA_A, SHA_B, SHA_C):
        L.claim_dev_trial(store, FAM, lab_id=FAM, request_id=dev_rid(sha), code_sha256=sha)
    assert L.claim_dev_trial(store, other, lab_id=other, request_id=dev_rid(SHA_A, other), code_sha256=SHA_A) == 1
    assert L.count_global_trials(store) == 4
    assert L.list_families(store) == sorted([FAM, other])


# ── holdout ─────────────────────────────────────────────────────────────────

def test_second_holdout_for_same_sha_is_409(store):
    assert L.claim_holdout(store, FAM, SHA_A, lab_id=FAM, request_id=hold_rid(SHA_A)) == 1
    assert L.claim_holdout(store, FAM, SHA_A, lab_id=FAM, request_id=hold_rid(SHA_A)) == 1   # dedup retry
    with pytest.raises(HoldoutAlreadyRun) as exc:
        L.claim_holdout(store, FAM, SHA_A, lab_id=FAM, request_id="another_request_id_1")
    assert exc.value.status == HTTP_HOLDOUT_ALREADY_RUN == 409
    # the sha lock holds across families too (once per sha)
    with pytest.raises(HoldoutAlreadyRun):
        L.claim_holdout(store, "lab_other_family", SHA_A, lab_id="lab_other_family",
                        request_id=hold_rid(SHA_A, "lab_other_family"))
    kinds = [r["payload"]["kind"] for r in L.list_records(store, FAM, record_type="budget_claim")]
    assert kinds == ["holdout_sha", "holdout_touch"]


def test_holdout_touches_burn_after_three(store):
    for n, sha in enumerate((SHA_A, SHA_B, SHA_C), start=1):
        assert L.claim_holdout(store, FAM, sha, lab_id=FAM, request_id=hold_rid(sha)) == n
    assert L.count_holdout_touches(store, FAM) == 3
    with pytest.raises(BudgetExhausted) as exc:
        L.claim_holdout(store, FAM, SHA_D, lab_id=FAM, request_id=hold_rid(SHA_D))
    assert exc.value.code == "holdout_burned" and exc.value.status == 429
    assert L.is_slot_taken(store, holdout_sha_key(SHA_D)) is False      # refused before claiming the sha
    assert L.budget_info(store, FAM).holdout_burned is True


def test_concurrent_holdout_claims_have_one_winner(store):
    results, barrier = [], threading.Barrier(10)

    def go(i):
        barrier.wait()
        try:
            results.append(L.claim_holdout(store, FAM, SHA_A, lab_id=FAM, request_id=f"concurrent_req_{i:02d}"))
        except HoldoutAlreadyRun:
            results.append("409")
    threads = [threading.Thread(target=go, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results, key=str) == [1] + ["409"] * 9


def test_first_holdout_claim_records_both_sha_and_touch(store):
    # Regression: the sha and touch budget_claim records share record_type, request_id and timestamp,
    # so the touch record collided on its file name and was silently dropped on the FIRST claim.
    from sajim_quant.lab.interfaces import holdout_touch_key
    assert L.claim_holdout(store, FAM, SHA_A, lab_id=FAM, request_id=hold_rid(SHA_A)) == 1
    kinds = [r["payload"]["kind"] for r in L.list_records(store, FAM, record_type="budget_claim")]
    assert sorted(kinds) == ["holdout_sha", "holdout_touch"]
    # a Modal Dict entry expiring must not give the family its holdout touch back
    (store.root / "lab" / "_slots" / slot_filename(holdout_touch_key(FAM, 1))).unlink()
    assert L.count_holdout_touches(store, FAM) == 1
    assert L.find_claim(store, FAM, hold_rid(SHA_A), kinds=("holdout_touch",)) is not None


def test_crash_after_claiming_the_last_touch_is_recovered(store):
    from sajim_quant.lab.interfaces import holdout_touch_key
    for sha in (SHA_A, SHA_B):
        L.claim_holdout(store, FAM, sha, lab_id=FAM, request_id=hold_rid(sha))
    rid = hold_rid(SHA_C)
    for key in (holdout_sha_key(SHA_C), holdout_touch_key(FAM, 3)):      # claimed, records never written
        store.claim_slot(key, json.dumps({"request_id": rid}).encode())
    assert L.claim_holdout(store, FAM, SHA_C, lab_id=FAM, request_id=rid) == 3
    kinds = sorted(r["payload"]["kind"] for r in L.list_records(store, FAM, record_type="budget_claim",
                                                                 request_id=rid))
    assert kinds == ["holdout_sha", "holdout_touch"]                      # both records written on recovery
    with pytest.raises(BudgetExhausted):
        L.claim_holdout(store, FAM, SHA_D, lab_id=FAM, request_id=hold_rid(SHA_D))


def test_protected_paths_are_case_and_alias_insensitive(store):
    # NTFS is case-insensitive and drops trailing dots/spaces: these all alias a ledger record or slot.
    assert store.write_once("lab/x/ledger/r.json", b"1") is True
    for alias in ("lab/x/LEDGER/r.json", "LAB/x/ledger/r.json", "lab/x/ledger./r.json", "lab/x/ledger /r.json",
                  f"lab/_SLOTS/{slot_filename(trial_key(FAM, 1))}", f"Lab/_slots/{slot_filename(trial_key(FAM, 1))}"):
        with pytest.raises(ValueError):
            store.write(alias, b"overwritten")
    assert store.read("lab/x/ledger/r.json") == b"1"


def test_dev_pass_lookups(store):
    assert L.dev_pass_record(store, FAM, SHA_A) is None
    for k, passed in enumerate((False, True)):
        L.put_record(store, L.make_record(
            "dev_trial", family=FAM, lab_id=FAM, request_id=dev_rid(SHA_A), actor="api", code_sha256=SHA_A,
            now=datetime(2026, 10, 12, 22, 15, k, tzinfo=timezone.utc),
            stage="dev", payload={"trial": 1, "job_id": "j", "status": "done", "verdict": "NOT_PROVEN",
                                  "final": False, "dev_hard_gates_passed": passed,
                                  "scorecard_path": L.dev_scorecard_relpath(FAM, SHA_A), "result_file": "f",
                                  "error": None}))
    assert L.dev_pass_record(store, FAM, SHA_A)["payload"]["dev_hard_gates_passed"] is True
    assert L.dev_pass_record(store, FAM, SHA_B) is None
    store.write(L.dev_scorecard_relpath(FAM, SHA_A), json.dumps({"code_sha256": SHA_A}).encode())
    assert L.load_dev_scorecard(store, FAM, SHA_A) == {"code_sha256": SHA_A}
    assert L.load_dev_scorecard(store, FAM, SHA_B) is None


def test_put_record_warns_when_a_different_record_collides(tmp_path, caplog):
    import logging
    from datetime import datetime, timezone
    from sajim_quant.lab import ledger as LG
    store = LG.LocalDirStore(tmp_path)
    when = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)
    kw = dict(family="lab_warn_test", lab_id="lab_warn_test", request_id="r" * 32, actor="api", now=when)
    a = LG.make_record("check", payload={"passed": True, "reasons": [], "checks": {}}, **kw)
    b = LG.make_record("check", payload={"passed": False, "reasons": ["x"], "checks": {}}, **kw)
    rel = LG.put_record(store, a)
    with caplog.at_level(logging.INFO, logger=LG.__name__):
        assert LG.put_record(store, a) == rel                     # identical retry: info only
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert LG.put_record(store, b) == rel                     # collision with other bytes: warning
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1 and "DIFFERENT content" in warned[0].getMessage()
    assert json.loads(store.read(rel))["payload"]["passed"] is True       # the first record stays
    with pytest.raises(ValueError):
        LG.make_record("check", payload={}, **dict(kw, family="lab_warn_test\n"))
