# Auto DJ

Auto DJ is an opt-in playback enhancement.

It analyzes the current and next tracks in a background worker and uses the
existing queue as its only source of tracks.

Auto DJ never reorders or duplicates queue entries.

## Analysis cache

Analysis results are cached under:

```text
$XDG_DATA_HOME/groovia/autodj/analysis.json
```

## Playback behavior

Auto DJ preloads one next stream.

When a pitch-preserving GStreamer element is available, it can use tempo
matching for transitions.

Supported elements may include:

- Rubber Band;
- `pitch`;
- `scaletempo`.

If no suitable pitch-preserving element is available, Auto DJ remains usable
and falls back to phrase-aware transitions without tempo matching.

## Disabled behavior

When Auto DJ is disabled, Groovia keeps the original crossfade path unchanged.
