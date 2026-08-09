# Auto DJ

Auto DJ is Groovia's optional transition engine. It prepares the next track
already selected by the queue, studies both sides of the handoff, and chooses
an overlap that should feel musical rather than merely applying the same fixed
crossfade every time.

It is designed for albums, playlists, and ordinary music libraries. It is not
a recommender and it is not a replacement for the queue: Auto DJ never chooses
new tracks, reorders the queue, or creates duplicate queue entries.

## What Auto DJ does

For every current/next pair, Auto DJ can decide:

- where the outgoing part of the current track should begin fading;
- where the incoming track should begin playing;
- how long the overlap should last;
- whether the two tracks can be aligned by beat or tempo;
- whether the incoming intro should be shortened to reach a stronger entry;
- whether the transition should favour a vocal handoff, an instrumental blend,
  an energy change, or a more conservative fade;
- how much gain correction and bass reduction should be applied during the
  overlap.

The result is a plan for one specific pair of tracks. When the queue changes,
the previous plan is discarded and a new pair is analysed. A plan is also
checked against the current and next file paths before it reaches the player,
so an old background result cannot be applied to a different queue state.

## Getting started

1. Open **Preferences → Playback**.
2. Enable **Auto DJ**.
3. Keep a next track available in the queue, a playlist, or the current
   playback source.
4. Start playback and let Groovia prepare the next track in the background.

Analysis is asynchronous. The first transition for a track pair may use a
short safety handoff while analysis is still running; a complete musical plan
replaces that temporary plan as soon as it is ready. Playback remains usable if
analysis takes too long or fails.

Auto DJ is most visible when playback reaches the end of the current track.
Pressing **Next** can also use a prepared Auto DJ stream when the current queue
item still matches the prepared next track. If there is no valid next track,
there is nothing for Auto DJ to transition into.

## Preferences

The Auto DJ group is under **Preferences → Playback**.

| Setting | Default | Effect |
| --- | --- | --- |
| Enable Auto DJ | Off | Enables background planning and Auto DJ playback. |
| Transition style | Balanced | Controls the preferred musical length and intensity. |
| Transition length | Automatic | Lets the planner choose a duration from tempo, phrases, and available audio. Fixed values are 2, 4, 8, 12, or 15 seconds. |
| Beat matching | On | Allows beat alignment when both tracks have sufficiently reliable beat evidence. |
| Phrase matching | On | Prefers structural boundaries instead of switching at arbitrary timestamps. |
| Tempo matching | On | Allows conservative tempo changes when a pitch-preserving GStreamer element is available. |
| Smart EQ | On | Reduces bass buildup while both tracks are playing. |
| Silence detection | On | Makes intro and outro silence available as transition cues. |
| Artwork animation | On | Animates the player artwork during an Auto DJ handoff. |
| Show Auto DJ badge | On | Shows the small Auto DJ indicator while a transition is active. |

### Transition styles

The style is a preference, not a forced effect:

- **Subtle** favours short, restrained overlaps. With reliable beat data it
  generally targets one to two bars; without it, it prefers roughly 2–4.5
  seconds.
- **Balanced** is the default. With beat data it generally targets two to
  four bars; without it, it considers roughly 3–9.5 seconds.
- **Energetic** allows longer and more expressive overlaps. With beat data it
  generally targets four to eight bars; without it, it considers roughly
  6–14 seconds.

The planner may still select a shorter or longer candidate when the available
intro, outro, vocal timing, or track duration makes the preferred range unsafe.

## How a transition is prepared

### 1. The queue supplies the pair

Groovia first resolves the current track and the next queue item. In shuffle
mode, the queue's selected next item is still the only source used by Auto DJ.
When repeat-all reaches the end of a source, the next item is resolved from
that source according to the normal playback rules.

### 2. The next stream is preloaded

The player creates a second GStreamer `playbin` for the incoming track and
prerolls it in the paused state. The planned incoming timestamp is only sought
after preroll completes; this avoids starting a transition from an unreliable
or stale position.

While the analysis worker is running, the player can install a temporary
fallback plan. That fallback is deliberately short, uses no smart EQ, and is
replaced by the analysed plan when the pair is ready.

### 3. Both tracks are analysed in a worker

The analysis runs outside GTK's main loop, one pair at a time. It does not
block the interface or playback. If a newer pair is requested, an older result
is cancelled logically and is not delivered to the player.

The analyser combines the following evidence when it is available:

- embedded duration and BPM metadata through `ffprobe`;
- decoded mono PCM through FFmpeg;
- onset peaks and a beat timeline;
- downbeats and larger phrase boundaries;
- tempo candidates, including half-time and double-time interpretations;
- beat confidence and tempo stability;
- intro and outro silence;
- energy and dynamic-range curves;
- a broad key estimate with a confidence score;
- vocal activity, vocal entry points, and vocal exit points;
- synchronized lyric timing when a synchronized lyric document is available;
- loudness and peak values for gain correction.

The PCM analysis is intentionally bounded. It is decoded as mono audio at
11,025 Hz and is limited to the first 25 minutes of a very long recording.
Normal songs and mixes are analysed in full.

### 4. Candidates are scored

The planner generates several possible outgoing/incoming pairs, then scores
them instead of committing to the first beat it finds. The score considers:

- beat and downbeat alignment;
- phrase-boundary alignment;
- tempo compatibility and accumulated beat drift;
- energy continuity or a deliberate energy rise;
- key compatibility when both key estimates are reliable;
- the quality of the incoming musical entry;
- vocal overlap and the possibility of a clean vocal handoff;
- silence at the end of the current track or beginning of the next one;
- whether the chosen transition strategy is appropriate for the evidence.

When synchronized lyrics are present, their line or word timings provide
particularly useful vocal entry and exit points. Without lyrics, Auto DJ can
still estimate broad vocal sections from the audio spectrum.

### 5. The best plan is applied conservatively

Plans below the confidence threshold are rejected in favour of the fallback
path. A successful plan can use one of several strategies internally:

- clean blend;
- instrumental blend;
- vocal handoff;
- bass swap;
- filter or echo-out;
- reverb-out;
- beat-repeat-out;
- hard cut at a very strong structural entry.

These are decisions made by the planner, not extra modes that need to be
selected manually. If the required GStreamer effect is unavailable, the player
falls back to a clean blend rather than failing playback.

## Beat and tempo matching

Auto DJ distinguishes beat alignment from tempo stretching:

- Beat alignment requires BPM evidence, a beat timeline, and a minimum beat
  confidence for both tracks.
- Differences of about one percent or less can be aligned without changing
  playback speed.
- With tempo matching enabled, the planner permits a conservative range of up
  to approximately four percent.
- A larger difference is rejected as musically incompatible.
- If the tracks need more than the no-stretch range but no pitch-preserving
  element is installed, Auto DJ keeps the original tempo and uses a
  phrase-aware blend instead.

Changing tempo must not change pitch. For that reason, tempo matching is only
used when one of the supported GStreamer elements is found:

- `rubberband`;
- `pitch`;
- `scaletempo`.

The player checks these elements at startup and logs the one it finds. The
availability of one of them improves transitions between tracks with slightly
different BPMs, but it is not required for Auto DJ itself.

## Vocal and structural handoffs

Auto DJ tries not to place two dense vocal sections on top of each other. It
can instead fade out near the end of a vocal section and bring in the next
track at a lyric entry or another strong structural boundary.

The planner also recognises situations where the first part of a track is a
long, low-energy instrumental intro. Depending on the score, it may start the
incoming track later to reach a meaningful vocal or energy entry. It will not
do this when the available evidence is weak or when doing so would damage the
shape of the transition.

Some material is intentionally treated conservatively. If the current or
next track is labelled as a podcast, audiobook, or spoken word recording,
Auto DJ disables beat and bass-oriented decisions for that pair. It also
preserves material labelled as classical, live, continuous, a DJ mix, or a
mixtape instead of trying to impose a club-style transition on it.

## Audio effects used during the overlap

The player builds a temporary audio filter chain for the incoming stream. It
may contain:

- an optional tempo filter;
- `equalizer-3bands` for bass-aware mixing;
- `audioecho` for selected echo-out strategies;
- `freeverb` for selected reverb-out strategies.

Effects are enabled only for the transition and are reset when it completes or
is cancelled. Smart EQ is intentionally conservative: it reduces low
frequencies during the overlap and restores the normal signal afterward.

The exact result depends on the GStreamer plugins installed on the system. A
missing optional effect changes the chosen strategy, not the availability of
the music player.

## Dependencies

### NumPy and SciPy

NumPy and SciPy are required Auto DJ dependencies. Meson checks that both can
be imported during configuration, so a source build must install them before
running `meson setup`.

On Debian or Ubuntu, the package names are usually:

```sh
sudo apt install python3-numpy python3-scipy
```

Other distributions may use different package names. See
[`docs/linux-development.md`](linux-development.md) for the rest of the GNOME,
GStreamer, and FFmpeg requirements.

### FFmpeg and FFprobe

FFmpeg decodes the bounded analysis stream. FFprobe supplies metadata and
duration information. On Linux they must be available on `PATH`; the native
Windows package supplies the required tools in its private bundle.

If either tool cannot analyse a track, Groovia keeps playback available and
uses the evidence that remains. If there is not enough evidence for a musical
plan, the player uses the safe fallback transition.

### GStreamer

Basic GStreamer playback is required. The optional `pitch` element is supplied
by GStreamer Bad Plug-ins on many Linux distributions; `rubberband` and
`scaletempo` are accepted alternatives. The optional effects are discovered at
runtime, so the application does not need every effect to be installed.

For the packaged Windows build, the available plugins are determined by the
MSYS2 environment used to create the bundle. The build's smoke test reports
whether NumPy, SciPy, GStreamer, and a pitch-preserving element are available.

## Analysis cache

Analyses are persisted so opening the same library again does not require
decoding every track from scratch. The default cache location is:

| Platform | Location |
| --- | --- |
| Linux | `$XDG_DATA_HOME/groovia/autodj/analysis.json` or `~/.local/share/groovia/autodj/analysis.json` |
| Windows | `%LOCALAPPDATA%\\Groovia\\autodj\\analysis.json` |

Each entry is keyed by the resolved file path, modification timestamp, file
size, and the current analysis schema version. Editing or replacing a music
file therefore causes a fresh analysis. The cache is bounded to 500 entries;
old entries are removed when it grows beyond that limit.

To rebuild the cache, close Groovia and remove only `analysis.json`. The file
will be recreated as pairs are analysed. Deleting the cache does not delete
music, playlists, lyrics, or other Groovia data.

## Troubleshooting

### Auto DJ is enabled but no transition is heard

Check that:

1. a different next track is actually available;
2. the current track is playing far enough for its planned outgoing point to be
   reached;
3. the next track is not the same file as the current track;
4. the normal GStreamer playback path works for both files;
5. NumPy, SciPy, FFmpeg, and FFprobe are installed or bundled correctly.

The queue can still hand off normally when Auto DJ has no usable plan.

### Transitions are always conservative

This usually means the analyser lacks reliable BPM or beat evidence, the
tracks differ by more than the supported tempo range, or the best candidate
did not reach the confidence threshold. It can also be expected for spoken,
classical, live, or continuous material.

Try **Balanced** with **Beat matching** and **Phrase matching** enabled. If the
tracks have different BPMs, install a supported pitch-preserving GStreamer
element and keep **Tempo matching** enabled.

### Auto DJ works but tempo never changes

Inspect the startup log for the detected tempo filter. If it reports `none`,
the player can still align tracks with compatible BPMs, but it will not stretch
one track to match another. Install `rubberband`, `pitch`, or `scaletempo`, then
restart Groovia so the element is detected.

### Analysis seems stuck or repeatedly starts again

Run Groovia from a terminal and look for messages beginning with
`[Groovia Auto DJ]` or `AutoDJ`. They identify cache hits, missing tools,
analysis failures, tempo decisions, selected candidates, and fallback reasons.

If the cache is unreadable, Groovia logs a warning and rebuilds it. If a track
has moved or changed while it is being analysed, the old result is discarded
and the new path/signature is analysed instead.

## When Auto DJ is disabled

Turning Auto DJ off cancels pending analysis, removes the prepared Auto DJ
stream, resets temporary EQ/effect/tempo changes, and returns playback to
Groovia's normal queue and crossfade path. The analysis cache is retained so
that enabling Auto DJ again does not necessarily require rebuilding previous
results.
