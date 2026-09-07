import random
from types import SimpleNamespace

import pytest

from src.autodj.planner import harmonic_compatibility
from src.autodj.recommender import (
    AutoDJRecommender,
    bpm_compatibility,
    genre_compatibility,
    normalize_text,
    split_genres,
)
from src.models import Track


def track(
    path,
    *,
    artist="Artist",
    album="Album",
    genre="House",
    track_number=1,
    play_count=0,
):
    return Track(
        None,
        path,
        artist,
        album,
        artist,
        "2026",
        genre,
        track_number,
        1,
        180.0,
        path,
        play_count=play_count,
    )


def analysis(*, bpm=120.0, key="C major", confidence=0.8, start=0.5, end=0.5):
    return SimpleNamespace(
        bpm=bpm,
        key=key,
        key_confidence=confidence,
        energy=0.5,
        energy_curve=(start,) * 5 + (end,) * 5,
    )


def test_genre_normalization_ignores_case_accents_and_punctuation():
    assert normalize_text("ÉLECTRO-Pop!") == "electro pop"
    assert genre_compatibility("Électro", "electro") == pytest.approx(1.0)


def test_multiple_genres_are_split_and_shared_genres_score_highly():
    assert split_genres("House / Funk; Disco, Soul & Pop") == {
        "house",
        "funk",
        "disco",
        "soul",
        "pop",
    }
    assert genre_compatibility("House / Disco", "Techno; DISCO") > 0.75


def test_bpm_compatibility_supports_normal_and_half_double_time():
    assert bpm_compatibility(120, 122) > 0.9
    assert bpm_compatibility(70, 140) == pytest.approx(1.0)
    assert bpm_compatibility(140, 70) == pytest.approx(1.0)
    assert bpm_compatibility(None, 120) is None


def test_camelot_key_compatibility_and_confidence():
    assert harmonic_compatibility("C major", "C major") == 1.0
    assert harmonic_compatibility("C major", "A minor") >= 0.9
    assert harmonic_compatibility("C major", "G major") > 0.8
    assert harmonic_compatibility("C major", "F# major") < 0.5
    assert harmonic_compatibility("8B", "9B") > 0.8


def test_coherent_entry_energy_is_preferred():
    current = track("current")
    smooth = track("smooth", artist="Two", album="Two")
    abrupt = track("abrupt", artist="Three", album="Three")
    analyses = {
        "current": analysis(end=0.55),
        "smooth": analysis(start=0.59),
        "abrupt": analysis(start=0.98),
    }
    ranked = AutoDJRecommender(random.Random(1)).rank(
        current, [], [], [smooth, abrupt], analyses=analyses
    )
    assert ranked[0].track is smooth


def test_current_and_queued_tracks_are_absolute_exclusions():
    current = track("current")
    queued = track("queued")
    available = track("available")
    ranked = AutoDJRecommender(random.Random(1)).rank(
        current, [], [queued], [current, queued, available]
    )
    assert [item.track.path for item in ranked] == ["available"]


def test_immediate_history_is_excluded_when_another_track_exists():
    current = track("current")
    recent = track("recent")
    fresh = track("fresh")
    ranked = AutoDJRecommender(random.Random(2)).rank(current, [recent], [], [recent, fresh])
    assert [item.track.path for item in ranked] == ["fresh"]


def test_recent_artist_repetition_is_penalized():
    current = track("current", artist="Opening")
    repeated = track("repeated", artist="Repeated", album="Other")
    diverse = track("diverse", artist="Fresh", album="Other")
    history = [track(f"old-{index}", artist="Repeated") for index in range(3)]
    ranked = AutoDJRecommender(random.Random(3)).rank(current, history, [], [repeated, diverse])
    assert ranked[0].track is diverse


def test_better_transition_confidence_is_preferred():
    current = track("current")
    weak = track("weak", artist="Two", album="Two")
    strong = track("strong", artist="Three", album="Three")
    plans = {
        "weak": SimpleNamespace(confidence=0.1),
        "strong": SimpleNamespace(confidence=0.95),
    }
    ranked = AutoDJRecommender(random.Random(4)).rank(
        current, [], [], [weak, strong], transition_plans=plans
    )
    assert ranked[0].track is strong
    assert ranked[0].transition_score == 0.95


def test_missing_metadata_and_tiny_library_use_safe_fallbacks():
    current = track("current", artist="", album="", genre="Unknown")
    only_other = track("other", artist="", album="", genre="")
    recommender = AutoDJRecommender(random.Random(5))
    ranked = recommender.rank(current, [only_other], [], [current, only_other])
    assert [item.track.path for item in ranked] == ["other"]
    assert recommender.rank(current, [], [], [current]) == []


def test_seeded_rng_makes_ranking_deterministic_and_favours_lower_play_count():
    current = track("current")
    heard = track("heard", artist="Two", album="Two", play_count=50)
    discovery = track("discovery", artist="Three", album="Three", play_count=0)
    first = AutoDJRecommender(random.Random(42)).rank(current, [], [], [heard, discovery])
    second = AutoDJRecommender(random.Random(42)).rank(current, [], [], [heard, discovery])
    assert [(item.track.path, item.score) for item in first] == [
        (item.track.path, item.score) for item in second
    ]
    assert first[0].track is discovery
