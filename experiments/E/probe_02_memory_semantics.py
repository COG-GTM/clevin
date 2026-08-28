"""Probe 02 - memory-level semantics of the native store.

Primitive: `client.beta.memory_stores.memories` (create/retrieve/update/list/delete)
plus the `content_sha256` precondition.

Questions: what path shapes does the store accept; is listing hierarchical enough to
support structural scoping (depth / prefix rollups); do preconditions give safe
concurrent curation; what is the practical size and count ceiling?

Run: uv run --project runtime python experiments/E/probe_02_memory_semantics.py
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from harness import Probe, client, memory_map, sha256, temp_name

BULK_COUNT = 260
BULK_PARALLEL = 12


def main() -> None:
    api = client()
    probe = Probe("probe_02_memory_semantics")
    memories = api.beta.memory_stores.memories

    name = temp_name("semantics")
    store = api.beta.memory_stores.create(
        name=name,
        description="Temporary store for workstream E memory-semantics probing.",
        metadata={"experiment": "clevin-native-primitives", "swarm_workstream": "E"},
    )
    store_id = store.id
    probe.record("store_created", store_id=store_id, name=name)

    try:
        # --- 1. Path validation matrix -------------------------------------------------
        accepted_paths = [
            "/repos/COG-GTM/clevin/setup.md",
            "/repos/COG-GTM/clevin/tests.md",
            "/repos/COG-GTM/clevin/failures/mypy.md",
            "/repos/COG-GTM/other-repo/setup.md",
            "/global/conventions.md",
            "/Repos/CASE-Sensitive.md",
            "/no-extension",
            "/a" * 1,
        ]
        for path in accepted_paths:
            probe.attempt(f"accept_path {path}", lambda p=path: memories.create(store_id, path=p, content=f"content for {p}"), path=path)

        rejected_paths = {
            "relative": "repos/clevin/setup.md",
            "empty_segment": "/repos//setup.md",
            "dot_segment": "/repos/./setup.md",
            "dotdot_segment": "/repos/../setup.md",
            "trailing_slash": "/repos/clevin/",
            "control_char": "/repos/clev\u0001in.md",
            "non_nfc": "/repos/cafe\u0301.md",
            "too_long": "/" + ("x" * 1100),
            "root": "/",
        }
        for label, path in rejected_paths.items():
            probe.expect_error(f"path_rejected? {label}", lambda p=path: memories.create(store_id, path=p, content="x"), path=path)

        probe.expect_error(
            "duplicate_path_create",
            lambda: memories.create(store_id, path="/global/conventions.md", content="second writer"),
        )

        # --- 2. Views, hierarchy, rollups ---------------------------------------------
        basic = list(memories.list(store_id, limit=100))
        probe.record(
            "list_basic",
            item_types=sorted({item.type for item in basic}),
            content_is_none=all(getattr(item, "content", None) is None for item in basic if item.type == "memory"),
            count=len(basic),
        )
        full = list(memories.list(store_id, limit=100, view="full"))
        probe.record(
            "list_full",
            returned=len(full),
            limit_capped_at_20=len(full) <= 20,
            content_populated=any(getattr(item, "content", None) for item in full if item.type == "memory"),
        )
        depth1 = list(memories.list(store_id, depth=1))
        probe.record(
            "list_depth1_root",
            entries=[{"type": item.type, "path": getattr(item, "path", None)} for item in depth1],
            note="depth=1 behaves like ls: deeper entries roll up as memory_prefix items.",
        )
        prefixed = list(memories.list(store_id, path_prefix="/repos/COG-GTM/clevin/", depth=1))
        probe.record(
            "list_prefix_depth1",
            entries=[{"type": item.type, "path": getattr(item, "path", None)} for item in prefixed],
        )
        probe.expect_error(
            "prefix_without_trailing_slash",
            lambda: list(memories.list(store_id, path_prefix="/repos/COG-GTM/clevin")),
        )
        probe.record(
            "case_sensitivity",
            distinct_paths_coexist=sorted(
                p for p in memory_map(api, store_id) if p.lower() == "/repos/case-sensitive.md"
            ),
        )

        # --- 3. Rename keeps identity, precondition semantics -------------------------
        target = memory_map(api, store_id, full=True)["/repos/COG-GTM/clevin/setup.md"]
        renamed = memories.update(
            target["id"], memory_store_id=store_id, path="/repos/COG-GTM/clevin/setup-v2.md"
        )
        probe.record(
            "rename_preserves_id",
            same_id=renamed.id == target["id"],
            new_path=renamed.path,
            content_unchanged=renamed.content_sha256 == target["sha256"],
            new_version_id_differs=renamed.memory_version_id != target["memory_version_id"],
        )

        precondition_ok = memories.update(
            renamed.id,
            memory_store_id=store_id,
            content="updated under a matching precondition",
            precondition={"type": "content_sha256", "content_sha256": renamed.content_sha256},
        )
        probe.record("precondition_match_applies", new_sha=precondition_ok.content_sha256)

        probe.expect_error(
            "precondition_stale_rejected",
            lambda: memories.update(
                renamed.id,
                memory_store_id=store_id,
                content="lost-update attempt",
                precondition={"type": "content_sha256", "content_sha256": renamed.content_sha256},
            ),
        )
        probe.attempt(
            "precondition_stale_but_state_already_matches",
            lambda: memories.update(
                renamed.id,
                memory_store_id=store_id,
                content="updated under a matching precondition",
                precondition={"type": "content_sha256", "content_sha256": renamed.content_sha256},
            ),
            note="Docs claim 200 rather than 409 when the requested state already equals stored state.",
        )

        # Concurrent last-writer-wins vs preconditioned curation.
        head = memories.retrieve(renamed.id, memory_store_id=store_id, view="full")
        base_sha = head.content_sha256

        def racer(index: int) -> str:
            try:
                memories.update(
                    renamed.id,
                    memory_store_id=store_id,
                    content=f"writer {index} won",
                    precondition={"type": "content_sha256", "content_sha256": base_sha},
                )
            except Exception as error:  # noqa: BLE001
                return f"rejected:{type(error).__name__}"
            return "applied"

        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(racer, range(6)))
        probe.record(
            "preconditioned_race",
            outcomes=outcomes,
            applied=outcomes.count("applied"),
            note="Exactly one write should apply if the precondition is a real CAS.",
        )

        def unconditional(index: int) -> str:
            memories.update(renamed.id, memory_store_id=store_id, content=f"unconditional {index}")
            return "applied"

        with ThreadPoolExecutor(max_workers=6) as pool:
            uncond = list(pool.map(unconditional, range(6)))
        probe.record("unconditional_race", applied=uncond.count("applied"), note="Last writer wins; silent lost updates.")

        # --- 4. Delete semantics ------------------------------------------------------
        doomed = memories.create(store_id, path="/probe02/doomed.md", content="delete me")
        probe.expect_error(
            "delete_with_wrong_expected_sha",
            lambda: memories.delete(
                doomed.id, memory_store_id=store_id, expected_content_sha256=sha256("something else")
            ),
        )
        probe.attempt(
            "delete_with_correct_expected_sha",
            lambda: memories.delete(
                doomed.id, memory_store_id=store_id, expected_content_sha256=doomed.content_sha256
            ),
        )
        probe.expect_error(
            "retrieve_after_delete",
            lambda: memories.retrieve(doomed.id, memory_store_id=store_id),
        )
        probe.attempt(
            "recreate_deleted_path",
            lambda: memories.create(store_id, path="/probe02/doomed.md", content="new occupant"),
        )

        # --- 5. Content ceiling -------------------------------------------------------
        probe.attempt(
            "content_100kb_exact",
            lambda: memories.create(store_id, path="/probe02/big-100kb.md", content="a" * 102_400),
        )
        probe.expect_error(
            "content_over_100kb",
            lambda: memories.create(store_id, path="/probe02/big-over.md", content="a" * 102_401),
        )
        probe.attempt(
            "empty_content_allowed",
            lambda: memories.create(store_id, path="/probe02/empty.md", content=""),
        )
        probe.expect_error(
            "null_content_rejected",
            lambda: memories.create(store_id, path="/probe02/null.md", content=None),
        )

        # --- 6. Count ceiling / bulk write throughput ---------------------------------
        started = time.monotonic()
        failures: list[str] = []

        def bulk(index: int) -> None:
            try:
                memories.create(
                    store_id,
                    path=f"/bulk/shard{index % 13}/entry-{index:04d}.md",
                    content=f"bulk entry {index}\n" + ("filler " * 40),
                )
            except Exception as error:  # noqa: BLE001
                failures.append(f"{index}:{type(error).__name__}:{str(error)[:180]}")

        with ThreadPoolExecutor(max_workers=BULK_PARALLEL) as pool:
            list(pool.map(bulk, range(BULK_COUNT)))
        elapsed = time.monotonic() - started
        all_paths = memory_map(api, store_id)
        probe.record(
            "bulk_write",
            attempted=BULK_COUNT,
            failures=failures[:10],
            failure_count=len(failures),
            elapsed_seconds=round(elapsed, 1),
            writes_per_second=round(BULK_COUNT / elapsed, 2),
            total_memories_in_store=len(all_paths),
        )
        page = api.beta.memory_stores.memories.list(store_id, limit=100)
        pages = 0
        seen = 0
        for _ in page:
            seen += 1
        probe.record("pagination_walks_full_store", auto_paginated_items=seen, pages_hint=pages)
        rollup = list(memories.list(store_id, path_prefix="/bulk/", depth=1))
        probe.record(
            "bulk_rollup_depth1",
            entries=[{"type": item.type, "path": getattr(item, "path", None)} for item in rollup][:20],
            note="Structural scoping depends on these prefix rollups being cheap to enumerate.",
        )
    finally:
        purged = 0
        try:
            for entry in memory_map(api, store_id).values():
                try:
                    api.beta.memory_stores.memories.delete(entry["id"], memory_store_id=store_id)
                    purged += 1
                except Exception:  # noqa: BLE001, S110
                    pass
        finally:
            try:
                api.beta.memory_stores.archive(store_id)
                api.beta.memory_stores.delete(store_id)
                result = f"deleted (after purging {purged} memories)"
            except Exception as error:  # noqa: BLE001
                result = f"cleanup failed: {error}"
            probe.add_cleanup(f"memory_store {store_id} ({name})", "purge memories + archive + delete", result)
        probe.write()


if __name__ == "__main__":
    main()
