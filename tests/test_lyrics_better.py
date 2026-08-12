import io
import threading
import urllib.error
from types import SimpleNamespace

import pytest

from src.lyrics.better_lyrics import BetterLyricsClient, BetterLyricsResult
from src.lyrics.lrclib import LyricsResult
from src.lyrics.parser import parse_lyrics, parse_ttml
from src.lyrics.service import LyricsService

TTML = """<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml"
    xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
    xmlns:itunes="http://music.apple.com/lyric-ttml-internal"
    xml:lang="ar" itunes:timing="Word">
  <head><metadata>
    <ttm:agent type="person" xml:id="voice1"><ttm:name>Lead</ttm:name></ttm:agent>
    <ttm:agent type="person" xml:id="voice2"><ttm:name>Guest</ttm:name></ttm:agent>
    <transliterations><transliteration xml:lang="ar-Latn">
      <text for="L1"><span begin="1:02.500" end="62.750">marhaban</span></text>
    </transliteration></transliterations>
  </metadata></head>
  <body><div>
    <p begin="00:00:01.000" end="00:04.000" ttm:agent="voice1" itunes:key="L1">
      <span begin="00:00:01.000" end="00:02.000">مرحبا</span> <span ttm:role="x-bg">
        <span begin="1:02.500" end="62.750">خلفية</span>
      </span>
    </p>
    <p begin="62.750" end="65.000" ttm:agent="voice2"><span>line only</span></p>
  </div></body>
</tt>"""


def test_ttml_preserves_raw_source_and_word_line_metadata():
    timeline = parse_ttml(TTML, provider="betterlyrics")
    assert timeline.raw_source == TTML
    assert timeline.source_format == "ttml"
    assert timeline.rtl is True
    assert timeline.agents == {"voice1": "Lead", "voice2": "Guest"}
    assert timeline.lines[0].text.strip().startswith("مرحبا")
    assert timeline.lines[0].words[1].background_vocal is True
    assert timeline.lines[0].transliteration == "marhaban"
    assert timeline.lines[1].word_synchronized is False
    assert timeline.line_view().word_synchronized is False


def test_ttml_time_formats_and_current_lookup():
    timeline = parse_ttml(TTML)
    assert timeline.lines[0].start_time_ms == 1000
    assert timeline.lines[0].words[1].start_time_ms == 62500
    assert timeline.current_index(63_000) == 1
    assert timeline.current_word_index(0, 1_500) == 0

    timeline.apply_offset(500)
    assert timeline.current_index(1_200) == 0
    assert timeline.current_word_index(0, 1_200) == -1


def test_ttml_malformed_document_is_rejected():
    try:
        parse_ttml("<tt><body><p begin='bad'>oops")
    except ValueError:
        pass
    else:
        raise AssertionError("malformed TTML should fail")


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.headers = {"X-Cache-Status": "HIT", "X-Provider": "ttml"}
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._payload


def test_better_lyrics_client_builds_metadata_request(monkeypatch):
    calls = []

    def open_request(request, timeout):
        calls.append((request.full_url, timeout))
        return FakeResponse(b'{"ttml":"<tt><body><p begin=\\"1\\">Hi</p></body></tt>","score":97}')

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    result = BetterLyricsClient(base_url="https://example.test").get_lyrics(
        "A song", "An artist", "An album", 181
    )
    assert result and result.score == 97
    assert "s=A+song" in calls[0][0] and "d=181" in calls[0][0]


def test_better_lyrics_401_is_a_normal_miss(monkeypatch):
    def open_request(_request, timeout=None):
        raise urllib.error.HTTPError("https://example.test", 401, "key required", {}, io.BytesIO())

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    client = BetterLyricsClient(base_url="https://example.test")
    assert client.get_lyrics("Song", "Artist") is None
    assert client.last_status == 401


def test_better_lyrics_http_misses_and_bad_json_are_safe(monkeypatch):
    for status in (404, 422, 429):

        def open_request(_request, timeout=None, status=status):
            raise urllib.error.HTTPError(
                "https://example.test", status, "provider miss", {}, io.BytesIO()
            )

        monkeypatch.setattr("urllib.request.urlopen", open_request)
        client = BetterLyricsClient(base_url="https://example.test")
        assert client.get_lyrics("Song", "Artist") is None
        assert client.last_status == status

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse(b"not json")
    )
    assert BetterLyricsClient(base_url="https://example.test").get_lyrics("Song", "Artist") is None


def test_service_falls_back_to_lrclib_after_better_lyrics_miss(tmp_path):
    from tests.test_lyrics import FakeDatabase

    database = FakeDatabase()
    service = LyricsService(database, scanner=SimpleNamespace(), data_dir=tmp_path)
    service.better_lyrics = SimpleNamespace(get_lyrics=lambda *args: None)
    service.lrclib = SimpleNamespace(
        get_lyrics=lambda *args: LyricsResult(12, synced_lyrics="[00:01.00]Hello")
    )
    track = SimpleNamespace(
        id=1, title="Song", artist="Artist", album="Album", duration=180, path="song.mp3"
    )
    bundle = service.fetch_online(track)
    assert bundle and bundle.line and bundle.line.provider == "lrclib"


def test_service_falls_back_when_better_lyrics_only_returns_plain_text(tmp_path):
    from tests.test_lyrics import FakeDatabase

    database = FakeDatabase()
    service = LyricsService(database, scanner=SimpleNamespace(), data_dir=tmp_path)
    service.better_lyrics = SimpleNamespace(
        get_lyrics=lambda *args: BetterLyricsResult("No timing available")
    )
    service.lrclib = SimpleNamespace(
        get_lyrics=lambda *args: LyricsResult(13, synced_lyrics="[00:01.00]Synced")
    )
    track = SimpleNamespace(
        id=2, title="Song", artist="Artist", album="Album", duration=180, path="song.mp3"
    )

    bundle = service.fetch_online(track)

    assert bundle and bundle.line and bundle.line.provider == "lrclib"
    assert service.find(track)[0].synchronized


def test_plain_provider_cache_does_not_block_synchronized_enrichment(tmp_path):
    from tests.test_lyrics import FakeDatabase

    database = FakeDatabase()
    service = LyricsService(database, scanner=SimpleNamespace(), data_dir=tmp_path)
    track = SimpleNamespace(
        id=3, title="Song", artist="Artist", album="Album", duration=180, path="song.mp3"
    )
    service.ingest_content(track, "Only text", provider="betterlyrics")

    assert service._has_cached_lyrics(track) is False


def test_better_lyrics_is_persisted_as_native_ttml(tmp_path):
    from tests.test_lyrics import FakeDatabase

    database = FakeDatabase()
    service = LyricsService(database, scanner=SimpleNamespace(), data_dir=tmp_path)
    service.better_lyrics = SimpleNamespace(
        get_lyrics=lambda *args: BetterLyricsResult(TTML, score=99, provider="ttml")
    )
    track = SimpleNamespace(
        id=9, title="Song", artist="Artist", album="Album", duration=180, path="song.mp3"
    )
    bundle = service.fetch_better_lyrics(track)
    rows = database.lyrics_for_track(track.id)
    assert bundle and bundle.word and bundle.line
    assert rows[0]["provider"] == "betterlyrics"
    assert rows[0]["file_path"].endswith(".ttml")
    assert rows[0]["content"] == TTML


def test_lrc_and_plain_compatibility_remains():
    assert parse_lyrics("[00:01.00]<00:01.00>Hello <00:01.50>world").word_synchronized
    assert parse_lyrics("first\nsecond").source_format == "plain"


class FakeArtwork:
    def __init__(self, cached=False, result=None, error=None):
        self.cached_value = cached
        self.result = result
        self.error = error
        self.fetch_calls = []

    def cached(self, *_args):
        return self.cached_value

    def unavailable_cached(self, *_args):
        return False

    def fetch(self, *args):
        self.fetch_calls.append(args)
        if self.error:
            raise self.error
        return self.result


def _enrichment_track(track_id=1):
    return SimpleNamespace(
        id=track_id,
        title=f"Song {track_id}",
        artist="Artist",
        album="Album",
        duration=180,
        path=f"song-{track_id}.mp3",
    )


def test_imported_track_hook_starts_enrichment_for_every_downloaded_track():
    pytest.importorskip("gi")
    from src.downloads.service import SpotDLService

    service = SpotDLService.__new__(SpotDLService)
    tracks = [_enrichment_track(1), _enrichment_track(2), _enrichment_track(3)]
    calls = []
    events = []
    service._contexts = {}
    service.lyrics = SimpleNamespace(
        enrich_tracks_async=lambda imported, callback: calls.append((imported, callback))
    )
    service._emit = lambda event, _job, _payload: events.append(event)
    job = SimpleNamespace(state="finished", job_type="sync")

    service._import_event("import-finished", job, {"tracks": tracks})

    assert calls and calls[0][0] == tracks
    assert events == ["completed"]


def test_single_track_enrichment_fetches_lyrics_and_artwork(tmp_path):
    from tests.test_lyrics import FakeDatabase

    database = FakeDatabase()
    service = LyricsService(database, scanner=SimpleNamespace(), data_dir=tmp_path)
    service.better_lyrics = SimpleNamespace(
        get_lyrics=lambda *args: BetterLyricsResult(TTML, score=99, provider="ttml")
    )
    artwork_path = tmp_path / "art.gif"
    artwork_path.write_bytes(b"GIF89a")
    artwork = FakeArtwork(result=artwork_path)
    service.artwork = artwork
    finished = threading.Event()
    results = []

    assert service.enrich_track_async(
        _enrichment_track(), lambda result: (results.append(result), finished.set())
    )
    assert finished.wait(2)
    assert results[0].bundle and results[0].bundle.word
    assert artwork.fetch_calls == [("Song 1", "Artist", "Album")]
    assert database.lyrics_for_track(1)[0]["provider"] == "betterlyrics"


def test_playlist_enrichment_starts_one_worker_per_imported_track(tmp_path):
    from tests.test_lyrics import FakeDatabase

    service = LyricsService(FakeDatabase(), scanner=SimpleNamespace(), data_dir=tmp_path)
    lyric_calls = []
    service.fetch_online = lambda track: lyric_calls.append(track) or None
    service.artwork = FakeArtwork(result=None)
    finished = threading.Event()
    results = []
    tracks = [_enrichment_track(index) for index in range(1, 4)]

    assert (
        service.enrich_tracks_async(
            tracks,
            lambda result: (results.append(result), finished.set() if len(results) == 3 else None),
        )
        == 3
    )
    assert finished.wait(2)
    assert {track.id for track in lyric_calls} == {1, 2, 3}
    assert len(results) == 3


def test_cached_track_does_not_refetch_enrichment(tmp_path):
    from tests.test_lyrics import FakeDatabase

    database = FakeDatabase()
    service = LyricsService(database, scanner=SimpleNamespace(), data_dir=tmp_path)
    track = _enrichment_track()
    service.ingest_content(track, "[00:01.00]Cached", provider="betterlyrics")
    service.fetch_online = lambda *_args: pytest.fail("cached lyrics must not refetch")
    service.artwork = FakeArtwork(cached=tmp_path / "cached.gif")

    assert service.enrich_track_async(track) is False


def test_enrichment_failure_is_reported_without_raising(tmp_path):
    from tests.test_lyrics import FakeDatabase

    service = LyricsService(FakeDatabase(), scanner=SimpleNamespace(), data_dir=tmp_path)
    service.fetch_online = lambda *_args: (_ for _ in ()).throw(RuntimeError("provider down"))
    service.artwork = FakeArtwork(error=RuntimeError("artwork down"))
    finished = threading.Event()
    results = []

    assert service.enrich_track_async(
        _enrichment_track(), lambda result: (results.append(result), finished.set())
    )
    assert finished.wait(2)
    assert "lyrics: provider down" in results[0].error
    assert "artwork: artwork down" in results[0].error
    assert service.enrich_track_async(_enrichment_track()) is False


def test_active_lyrics_view_refreshes_after_enrichment():
    pytest.importorskip("gi")
    from src.window import GrooviaWindow

    track = _enrichment_track()
    refreshed = []
    window = GrooviaWindow.__new__(GrooviaWindow)
    window.current = track
    window.stack = SimpleNamespace(get_visible_child_name=lambda: "lyrics")
    window.download_service = SimpleNamespace(
        lyrics=SimpleNamespace(find=lambda _track: (object(), {}))
    )
    window._show_lyrics = lambda refreshed_track: refreshed.append(refreshed_track)

    window._download_event(
        "lyrics-enriched", None, {"track": track, "bundle": object(), "artwork": None}
    )

    assert refreshed == [track]
