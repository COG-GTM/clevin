"""Probe 01 - Memory Store lifecycle through the public API alone.

Primitive: `client.beta.memory_stores` (create/retrieve/update/list/archive/delete).
Question: which lifecycle actions are declarative/API-driven and which still need the
Console? What does renaming do to the mount slug? What happens to an archived store?

Run: uv run --project runtime python experiments/E/probe_01_store_lifecycle.py
"""

from __future__ import annotations

from harness import Probe, client, memory_map, slug_for, temp_name

EXPERIMENT_TAG = {"experiment": "clevin-native-primitives", "swarm_workstream": "E"}


def main() -> None:
    api = client()
    probe = Probe("probe_01_store_lifecycle")

    name = temp_name("lifecycle")
    store = api.beta.memory_stores.create(
        name=name,
        description="Temporary store for workstream E lifecycle probing.",
        metadata=EXPERIMENT_TAG,
    )
    probe.record(
        "created",
        store_id=store.id,
        name=store.name,
        derived_slug_guess=slug_for(store.name),
        fields=sorted(store.model_dump().keys()),
        description=store.description,
        archived_at=store.archived_at,
    )

    retrieved = api.beta.memory_stores.retrieve(store.id)
    probe.record("retrieved", store_id=retrieved.id, equal_to_created=retrieved.name == store.name)

    # Does the store carry any scoping/selection field beyond name/description/metadata?
    probe.record(
        "declarative_surface",
        create_params=["name", "description", "metadata"],
        update_params=["name", "description", "metadata"],
        no_scope_field=True,
        note="No repo/task scope, no auto-selection predicate, no per-path ACL in the store object.",
    )

    renamed = api.beta.memory_stores.update(
        store.id,
        name=f"{name}-renamed",
        description="Renamed during probe 01.",
        metadata={"probe": "01"},
    )
    probe.record(
        "renamed",
        store_id=renamed.id,
        new_name=renamed.name,
        new_slug_guess=slug_for(renamed.name),
        metadata_after_patch=renamed.metadata,
        note="Metadata patch semantics: keys upserted, omitted keys preserved, null deletes.",
    )

    # Metadata delete-by-null.
    patched = api.beta.memory_stores.update(store.id, metadata={"probe": None})
    probe.record("metadata_key_deleted", metadata=patched.metadata)

    # Restore experiment tags so the temp resource stays attributable until cleanup.
    api.beta.memory_stores.update(store.id, metadata=EXPERIMENT_TAG)

    listed_ids = [item.id for item in api.beta.memory_stores.list(limit=100)]
    probe.record(
        "listed_active",
        contains_probe_store=store.id in listed_ids,
        active_store_count_first_page=len(listed_ids),
    )

    api.beta.memory_stores.memories.create(store.id, path="/probe01/note.md", content="pre-archive content")
    probe.record("wrote_memory_before_archive", paths=sorted(memory_map(api, store.id)))

    archived = api.beta.memory_stores.archive(store.id)
    probe.record("archived", store_id=archived.id, archived_at=archived.archived_at)

    excluded = [item.id for item in api.beta.memory_stores.list(limit=100)]
    included = [item.id for item in api.beta.memory_stores.list(limit=100, include_archived=True)]
    probe.record(
        "archive_visibility",
        hidden_from_default_list=store.id not in excluded,
        visible_with_include_archived=store.id in included,
    )

    probe.attempt(
        "read_memories_after_archive",
        lambda: memory_map(api, store.id),
    )
    probe.expect_error(
        "write_memory_after_archive",
        lambda: api.beta.memory_stores.memories.create(
            store.id, path="/probe01/post-archive.md", content="should this be allowed?"
        ),
    )
    probe.expect_error(
        "update_archived_store",
        lambda: api.beta.memory_stores.update(store.id, description="post-archive description"),
    )
    probe.expect_error(
        "unarchive_via_update",
        lambda: api.beta.memory_stores.update(store.id, name=f"{name}-unarchived"),
    )

    deleted = probe.attempt("deleted", lambda: api.beta.memory_stores.delete(store.id))
    probe.add_cleanup(
        f"memory_store {store.id} ({name})",
        "archive + delete",
        "deleted" if deleted is not None else "delete failed - see observations",
    )
    probe.expect_error(
        "retrieve_after_delete",
        lambda: api.beta.memory_stores.retrieve(store.id),
    )

    probe.write()


if __name__ == "__main__":
    main()
