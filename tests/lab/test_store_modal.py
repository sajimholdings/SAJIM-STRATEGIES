"""store_modal.ModalStore with a fake modal.Dict / modal.Volume (offline).

The store-agnostic tests of tests/lab/test_ledger.py are imported below and re-run against
ModalStore through this module's `store` fixture. The two LocalDirStore-only tests (they delete
slot FILES) are re-stated here with a Dict entry expiring instead.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

from sajim_quant.lab import ledger as L
from sajim_quant.lab import store_modal as STM
from sajim_quant.lab.interfaces import (
    LedgerStore, derive_request_id, holdout_sha_key, holdout_touch_key, slot_filename, trial_key,
)

sys.path.insert(0, str(Path(__file__).parent))
from _modal_fakes import FakeDict, FakeVolume  # noqa: E402

# Store-conformance: the same test functions, collected again here with the ModalStore fixture.
from test_ledger import (  # noqa: E402,F401
    test_concurrent_holdout_claims_have_one_winner, test_crash_after_claiming_the_last_touch_is_recovered,
    test_crash_between_claim_and_record_is_recovered_by_the_same_request, test_dev_pass_lookups,
    test_families_have_separate_budgets_and_global_count, test_fourth_dev_trial_is_refused_429,
    test_holdout_touches_burn_after_three, test_make_and_put_record, test_protected_paths_are_case_and_alias_insensitive,
    test_second_holdout_for_same_sha_is_409, test_write_once_and_protected_paths,
)

FAM = "lab_test_family"
SHA_A, SHA_B = "a" * 64, "b" * 64


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def parts(tmp_path):
    return FakeVolume(), FakeDict(), Clock()


@pytest.fixture
def store(tmp_path, parts):
    vol, slots, clock = parts
    return STM.ModalStore(vol, slots, root=tmp_path / "results", reload_min_interval_s=0.0, clock=clock)


def test_modal_store_is_a_ledger_store(store):
    assert isinstance(store, LedgerStore)


def test_slots_live_in_the_dict_not_on_the_volume(store, parts):
    _, slots, _ = parts
    key = holdout_sha_key(SHA_A)
    assert store.read_slot(key) is None
    assert store.claim_slot(key, b'{"request_id": "r1"}') is True
    assert store.claim_slot(key, b'{"request_id": "r2"}') is False          # first writer wins
    assert slots.data[key] == b'{"request_id": "r1"}' and store.read_slot(key) == b'{"request_id": "r1"}'
    assert store.listdir("lab/_slots") == []                                 # never a file
    for bad in ("trial:lab_x:0", "holdout_sha:xyz", "nope", "trial:LAB_X:1"):
        with pytest.raises(ValueError):
            store.claim_slot(bad, b"{}")
    with pytest.raises(TypeError):
        store.claim_slot(trial_key(FAM, 1), "not bytes")


def test_claim_slot_requires_a_true_created_flag(store, parts):
    _, slots, _ = parts
    slots.put = lambda key, value, skip_if_exists=False: 1                  # truthy but not True
    assert store.claim_slot(trial_key(FAM, 1), b"{}") is False


class LostResponseDict(FakeDict):
    """modal's stub retries DictUpdate on transient errors: the first attempt lands, its response is lost,
    and the retry answers created=False for OUR OWN entry."""

    def __init__(self):
        super().__init__()
        self.lose_next = True

    def put(self, key, value, *, skip_if_exists=False):
        created = super().put(key, value, skip_if_exists=skip_if_exists)
        if created and self.lose_next:
            self.lose_next = False
            return super().put(key, value, skip_if_exists=skip_if_exists)       # the retry: False
        return created


def test_claim_slot_survives_a_retried_put_whose_first_attempt_landed(tmp_path, parts):
    vol, _, clock = parts
    slots = LostResponseDict()
    st = STM.ModalStore(vol, slots, root=tmp_path / "results", reload_min_interval_s=0.0, clock=clock)
    assert st.claim_slot(trial_key(FAM, 1), b'{"request_id": "r1"}') is True       # our bytes are there
    slots.lose_next = True
    assert st.claim_slot(trial_key(FAM, 1), b'{"request_id": "r2"}') is False      # someone else's stays lost


def test_retried_put_does_not_burn_a_second_dev_trial(tmp_path, parts):
    vol, _, clock = parts
    st = STM.ModalStore(vol, LostResponseDict(), root=tmp_path / "results", reload_min_interval_s=0.0, clock=clock)
    rid = derive_request_id(FAM, SHA_A, "dev")
    assert L.claim_dev_trial(st, FAM, lab_id=FAM, request_id=rid, code_sha256=SHA_A) == 1
    assert L.count_dev_trials(st, FAM) == 1


def test_retried_put_does_not_turn_a_first_holdout_into_409(tmp_path, parts):
    vol, _, clock = parts
    st = STM.ModalStore(vol, LostResponseDict(), root=tmp_path / "results", reload_min_interval_s=0.0, clock=clock)
    rid = derive_request_id(FAM, SHA_A, "holdout")
    assert L.claim_holdout(st, FAM, SHA_A, lab_id=FAM, request_id=rid) == 1


def test_non_bytes_dict_value_counts_as_taken(store, parts):
    _, slots, _ = parts
    slots.data[trial_key(FAM, 1)] = {"weird": True}
    assert store.read_slot(trial_key(FAM, 1)) == b""
    assert L.is_slot_taken(store, trial_key(FAM, 1)) is True


def test_writes_commit_and_listdir_reloads(store, parts):
    vol, _, _ = parts
    assert store.write_once("lab/x/ledger/r.json", b"1") is True
    assert vol.commits == 1
    assert store.write_once("lab/x/ledger/r.json", b"2") is False
    assert vol.commits == 1                                                  # nothing written, no commit
    store.write("lab/x/abc/strategy.py", b"code")
    assert vol.commits == 2
    before = vol.reloads
    assert store.listdir("lab/x") == ["abc", "ledger"]
    assert vol.reloads == before + 1


def test_read_miss_reloads_once_and_retries(store, parts, tmp_path):
    vol, _, _ = parts
    path = tmp_path / "results" / "lab" / "y" / "dev_scorecard.json"

    def other_container_commits():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{}")

    vol.reload = lambda: other_container_commits()
    assert store.read("lab/y/dev_scorecard.json") == b"{}"


def test_reload_is_throttled(tmp_path, parts):
    vol, slots, clock = parts
    st = STM.ModalStore(vol, slots, root=tmp_path / "r", reload_min_interval_s=5.0, clock=clock)
    st.listdir("lab")
    st.listdir("lab")
    assert vol.reloads == 1
    clock.t += 5.0
    st.listdir("lab")
    assert vol.reloads == 2
    st.write("lab/x/a/b.json", b"1")                                         # commit counts as a fresh view
    st.listdir("lab")
    assert vol.reloads == 2
    assert st.refresh(force=True) is True and vol.reloads == 3


def test_reload_failure_keeps_the_local_view(tmp_path):
    vol = FakeVolume(reload_exc=RuntimeError("there are open files preventing the operation"))
    st = STM.ModalStore(vol, FakeDict(), root=tmp_path / "r", reload_min_interval_s=0.0)
    st.write("lab/x/a/b.json", b"1")
    assert st.listdir("lab/x") == ["a"] and st.read("lab/x/a/b.json") == b"1"
    assert st.read("lab/x/a/missing.json") is None


@pytest.mark.parametrize("alias", [
    "lab/x/ledger/r.json", "lab/x/LEDGER/r.json", "LAB/x/ledger/r.json", "lab/x/ledger./r.json",
    "lab/x/ledger /r.json", "lab/x/Ledger. ./r.json", "lab/_slots/a.json", "lab/_SLOTS/a.json",
    "Lab/_slots./a.json", "lab/_slots /x/y.json",
])
def test_write_refuses_protected_aliases_before_touching_the_volume(store, parts, alias):
    vol, _, _ = parts
    with pytest.raises(ValueError):
        store.write(alias, b"overwritten")
    assert vol.commits == 0
    assert L._is_protected(alias)                                            # the same rule as ledger


def test_bad_paths_are_refused_before_any_volume_call(store, parts):
    vol, _, _ = parts
    for bad in ("../escape.json", "/abs.json", "lab/../x", "lab\\x", "C:/x", ""):
        with pytest.raises(ValueError):
            store.write_once(bad, b"x")
        with pytest.raises(ValueError):
            store.write(bad, b"x")
    assert (vol.commits, vol.reloads) == (0, 0)


def test_expired_dict_entry_cannot_reset_the_budget(store, parts):
    _, slots, _ = parts
    rid = derive_request_id(FAM, SHA_A, "dev")
    assert L.claim_dev_trial(store, FAM, lab_id=FAM, request_id=rid, code_sha256=SHA_A) == 1
    slots.expire(trial_key(FAM, 1))                                          # 7 idle days later
    assert L.is_slot_taken(store, trial_key(FAM, 1)) is True                 # the ledger record still holds it
    assert L.claim_dev_trial(store, FAM, lab_id=FAM, request_id=derive_request_id(FAM, SHA_B, "dev"),
                             code_sha256=SHA_B) == 2


def test_expired_holdout_touch_entry_keeps_the_touch(store, parts):
    _, slots, _ = parts
    rid = derive_request_id(FAM, SHA_A, "holdout")
    assert L.claim_holdout(store, FAM, SHA_A, lab_id=FAM, request_id=rid) == 1
    kinds = sorted(r["payload"]["kind"] for r in L.list_records(store, FAM, record_type="budget_claim"))
    assert kinds == ["holdout_sha", "holdout_touch"]
    slots.expire(holdout_touch_key(FAM, 1))
    slots.expire(holdout_sha_key(SHA_A))
    assert L.count_holdout_touches(store, FAM) == 1
    assert L.is_slot_taken(store, holdout_sha_key(SHA_A)) is True


def test_concurrent_dev_claims_get_distinct_trials(store):
    results, barrier = [], threading.Barrier(6)
    shas = [f"{i:x}" * 64 for i in range(1, 7)]

    def go(sha):
        barrier.wait()
        results.append(L.claim_dev_trial(store, FAM, lab_id=FAM, request_id=derive_request_id(FAM, sha, "dev"),
                                         code_sha256=sha))
    threads = [threading.Thread(target=go, args=(s,)) for s in shas]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted((r for r in results if r is not None)) == [1, 2, 3]
    assert results.count(None) == 3


def test_record_values_are_json_slots(store, parts):
    _, slots, _ = parts
    rid = derive_request_id(FAM, SHA_A, "dev")
    L.claim_dev_trial(store, FAM, lab_id=FAM, request_id=rid, code_sha256=SHA_A)
    value = json.loads(slots.data[trial_key(FAM, 1)].decode())
    assert value["request_id"] == rid and value["key"] == trial_key(FAM, 1)
    assert slot_filename(trial_key(FAM, 1)) == "trial__lab_test_family__1.json"


def test_from_modal_looks_up_the_named_dict_and_volume(monkeypatch, tmp_path):
    import modal

    seen = {}

    def dict_from_name(name, **kw):
        seen["dict"] = (name, kw)
        return FakeDict()

    def vol_from_name(name, **kw):
        seen["vol"] = (name, kw)
        return FakeVolume()

    monkeypatch.setattr(modal.Dict, "from_name", staticmethod(dict_from_name))
    monkeypatch.setattr(modal.Volume, "from_name", staticmethod(vol_from_name))
    st = STM.ModalStore.from_modal(root=tmp_path / "r")
    assert seen == {"dict": ("sajim-lab", {"create_if_missing": True}),
                    "vol": ("sajim-results", {"create_if_missing": True})}
    assert isinstance(st, LedgerStore) and st.root == tmp_path / "r"
    given = FakeVolume()
    assert STM.ModalStore.from_modal(volume=given, root=tmp_path / "r2").volume is given
