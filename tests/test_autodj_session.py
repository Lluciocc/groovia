from types import SimpleNamespace

from src.autodj.session import (
    PlaybackContext,
    PlaybackEventDeduplicator,
    QueueProvenance,
    RecommendationGuard,
)


def item(path):
    return SimpleNamespace(path=path)


def test_recommendations_are_disabled_for_playlists_and_enabled_for_library_auto():
    assert PlaybackContext.LIBRARY_AUTOMATIC.allows_recommendations
    assert not PlaybackContext.PLAYLIST.allows_recommendations
    assert not PlaybackContext.ALBUM.allows_recommendations
    assert not PlaybackContext.ARTIST.allows_recommendations
    assert not PlaybackContext.MANUAL_QUEUE.allows_recommendations
    assert not PlaybackContext.DIRECT_TRACK.allows_recommendations


def test_manual_queue_items_are_always_inserted_before_recommendations():
    provenance = QueueProvenance()
    automatic_one = item("auto-1")
    automatic_two = item("auto-2")
    queue = [automatic_one, automatic_two]
    provenance.mark_automatic(automatic_one)
    provenance.mark_automatic(automatic_two)

    manual_one = item("manual-1")
    manual_two = item("manual-2")
    provenance.append_manual(queue, manual_one)
    provenance.append_manual(queue, manual_two)

    assert [track.path for track in queue] == [
        "manual-1",
        "manual-2",
        "auto-1",
        "auto-2",
    ]
    assert not provenance.is_automatic(manual_one)


def test_obsolete_async_result_token_is_rejected_after_queue_change():
    guard = RecommendationGuard()
    context = PlaybackContext.LIBRARY_AUTOMATIC
    original_queue = (("queued", False),)
    token = guard.issue("current", original_queue, context)
    assert guard.accepts(token, "current", original_queue, context)

    guard.invalidate()
    assert not guard.accepts(token, "current", original_queue, context)

    fresh = guard.issue("current", original_queue, context)
    changed_queue = (("manual", False), ("queued", False))
    assert not guard.accepts(fresh, "current", changed_queue, context)
    assert not guard.accepts(fresh, "current", original_queue, PlaybackContext.PLAYLIST)


def test_repeated_playback_signal_is_counted_only_once():
    deduplicator = PlaybackEventDeduplicator()
    marker = ("transition", 123, "song")
    assert deduplicator.accepts(marker)
    assert not deduplicator.accepts(marker)
    assert deduplicator.accepts(("transition", 456, "song"))
