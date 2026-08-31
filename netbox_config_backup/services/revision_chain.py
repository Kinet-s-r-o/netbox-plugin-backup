from __future__ import annotations


def relink_kept_revisions(target, revisions, deleted_ids: set[int]) -> None:
    """Reconnect one target's revision chain after revisions are removed."""

    kept = sorted(
        (revision for revision in revisions if revision.pk not in deleted_ids),
        key=lambda revision: (revision.created, revision.pk),
    )
    previous = None
    for revision in kept:
        previous_id = previous.pk if previous else None
        if revision.previous_revision_id != previous_id:
            revision.previous_revision_id = previous_id
            revision.save(update_fields=("previous_revision", "last_updated"))
        previous = revision
    latest_id = kept[-1].pk if kept else None
    if target.last_revision_id != latest_id:
        target.last_revision_id = latest_id
        target.save(update_fields=("last_revision", "last_updated"))
