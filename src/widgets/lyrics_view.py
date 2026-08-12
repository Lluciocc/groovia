# lyrics_view.py
#
# Copyright 2026 Lluciocc (llucio.cc00@gmail.com)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import math
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango, PangoCairo

from ..platform_compat import iter_gtk_children


class WordSyncedLyricsRenderer(Gtk.DrawingArea):
    """Render one natural Pango line with Better Lyrics-style illumination.

    Timing spans remain a data concern: the widget has one layout for the
    complete sentence and only clips cached glyph regions while drawing. This
    keeps TTML syllables visually continuous, including when a span wraps.
    """

    _BASE_ALPHA = {
        "future": 0.24,
        "past_pending": 0.25,
        "past": 0.22,
        "active": 0.25,
    }

    def __init__(self, text, words, style_source, offset_ms, rtl=False):
        super().__init__()
        self._text = text or ""
        self._words = words or []
        self._style_source = style_source
        self._offset_ms = int(offset_ms)
        self._rtl = bool(rtl)
        self._layout = None
        self._layout_width = None
        self._word_ranges = []
        self._word_regions = []
        self._word_timing = []
        self._background_regions = []
        self._shaped_clusters = []
        self._scale = 1.0
        self._active = False
        self._playing = False
        self._tick_id = None
        self._position_ms = 0
        self._position_anchor = time.monotonic()
        self._word_index = -1
        self._focus_state = "future"
        self._focus_opacity = 1.0
        self._blur_radius = 0.0
        self._wave_enabled = True
        self._glow_enabled = True
        self._wave_x = None
        self._wave_target_x = 0.0
        self._wave_strength = 0.0
        self._wave_target_strength = 0.0
        self._wave_last_frame_time = None
        self._wave_radius = 96.0
        self._wave_leading_radius = 86.0
        self._wave_trailing_radius = 110.0
        self._wave_lift = 3.0
        self._wave_glow_radius = 2.4
        self.set_hexpand(True)
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)
        self._rebuild_layout()

    @staticmethod
    def _byte_length(text):
        return len(text.encode("utf-8"))

    def _rebuild_word_ranges(self):
        """Map parsed span text to exact UTF-8 ranges in the line layout."""
        self._word_ranges = []
        self._word_timing = []
        cursor = 0
        for index, word in enumerate(self._words):
            word_text = word.text or ""
            start_time = word.start_time_ms + self._offset_ms
            end_time = word.end_time_ms
            if end_time is not None:
                end_time += self._offset_ms
            if end_time is None or end_time <= start_time:
                next_start = (
                    self._words[index + 1].start_time_ms + self._offset_ms
                    if index + 1 < len(self._words)
                    else start_time + 300
                )
                end_time = max(start_time + 1, next_start)
            self._word_timing.append((start_time, end_time))
            start = self._text.find(word_text, cursor)
            if start < 0:
                self._word_ranges.append(None)
                continue
            end = start + len(word_text)
            self._word_ranges.append(
                (
                    self._byte_length(self._text[:start]),
                    self._byte_length(self._text[:end]),
                )
            )
            cursor = end

    def _rebuild_layout(self):
        # The style source supplies Groovia's system font. Never change the
        # layout's size or weight when a line becomes active: Spotlight's
        # focus comes from contrast, not a bouncing/scaling line.
        self._layout = self._style_source.create_pango_layout(self._text)
        self._layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        self._layout.set_alignment(Pango.Alignment.CENTER)
        self._layout.set_auto_dir(True)
        self._rebuild_word_ranges()
        self._apply_font_attributes()
        self._set_layout_width(self.get_width())
        self.queue_resize()
        self.queue_draw()

    def _apply_font_attributes(self):
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_scale_new(float(self._scale)))
        attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
        self._layout.set_attributes(attrs)
        self._layout_width = None
        self._set_layout_width(self.get_width())
        self.queue_draw()

    def _set_layout_width(self, width):
        width = int(width) if width and width > 0 else -1
        if self._layout_width == width:
            return
        self._layout_width = width
        self._layout.set_width(width * Pango.SCALE if width > 0 else -1)
        self._word_regions = self._build_word_regions()
        self._shaped_clusters = self._build_shaped_clusters()
        self._update_wave_typography()
        self._background_regions = [
            regions
            for word, regions in zip(self._words, self._word_regions)
            if word.background_vocal
        ]

    def _build_word_regions(self):
        """Cache glyph rectangles for every UTF-8 span, including wrapping."""
        if self._layout is None:
            return []
        lines = self._layout.get_lines_readonly()
        text_length = self._byte_length(self._text)
        regions = []
        for word_range in self._word_ranges:
            word_regions = []
            if word_range is not None:
                word_start, word_end = word_range
                for line_index, line in enumerate(lines):
                    line_start = line.get_start_index()
                    line_end = (
                        lines[line_index + 1].get_start_index()
                        if line_index + 1 < len(lines)
                        else text_length
                    )
                    start = max(word_start, line_start)
                    end = min(word_end, line_end)
                    if start >= end:
                        continue
                    line_origin = self._layout.index_to_pos(line_start)
                    x_origin = line_origin.x / Pango.SCALE
                    x_start = x_origin + line.index_to_x(start, False) / Pango.SCALE
                    x_end = x_origin + line.index_to_x(end, True) / Pango.SCALE
                    _ink, logical = line.get_pixel_extents()
                    word_regions.append(
                        (
                            min(x_start, x_end),
                            line_origin.y / Pango.SCALE,
                            max(1.0, abs(x_end - x_start)),
                            max(1.0, logical.height),
                        )
                    )
            regions.append(word_regions)
        return regions

    def do_measure(self, orientation, for_size):
        if self._layout is None:
            return (0, 0, -1, -1)
        self._set_layout_width(for_size if for_size > 0 else -1)
        width, height = self._layout.get_pixel_size()
        if orientation == Gtk.Orientation.HORIZONTAL:
            return (width, width, -1, -1)
        return (height, height, -1, -1)

    def set_scale(self, scale):
        # Kept for callers from the old renderer; Spotlight does not animate
        # line scale, but a non-default scale remains a valid layout setting.
        scale = float(scale)
        if abs(self._scale - scale) < 0.0001:
            return
        self._scale = scale
        self._apply_font_attributes()
        self.queue_resize()

    def set_active(self, active):
        active = bool(active)
        if self._active == active:
            return
        self._active = active
        if not active:
            self._wave_last_frame_time = None
        if active:
            if self._words and self._tick_id is None:
                self._tick_id = self.add_tick_callback(self._tick)
        elif self._tick_id is not None:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = None
        self.queue_draw()

    def set_playing(self, playing):
        self._playing = bool(playing)
        self._position_anchor = time.monotonic()
        self._wave_last_frame_time = None
        self.queue_draw()

    def set_focus(self, state, *, opacity=1.0, blur=0.0):
        state = state if state in self._BASE_ALPHA else "future"
        opacity = max(0.0, min(1.0, float(opacity)))
        blur = max(0.0, min(2.5, float(blur)))
        if (
            self._focus_state == state
            and abs(self._focus_opacity - opacity) < 0.001
            and abs(self._blur_radius - blur) < 0.01
        ):
            return
        self._focus_state = state
        self._focus_opacity = opacity
        self._blur_radius = blur
        self.queue_draw()

    def set_position(self, position_ms, word_index):
        previous_position = self._position_ms
        self._position_ms = int(position_ms)
        self._position_anchor = time.monotonic()
        self._word_index = int(word_index)
        # Player updates are normally close together. A larger discontinuity
        # is a seek, and the emphasis field must relocate immediately instead
        # of visibly travelling across the rest of the sentence.
        if self._wave_x is None or abs(self._position_ms - previous_position) > 600:
            target_x, target_strength = self._wave_state_for_position(self._position_ms)
            self._wave_x = target_x
            self._wave_target_x = target_x
            self._wave_strength = target_strength
            self._wave_target_strength = target_strength
        self._wave_last_frame_time = None
        self.queue_draw()

    def set_wave_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled and not self._wave_enabled:
            target_x, target_strength = self._wave_state_for_position(self._draw_position_ms)
            self._wave_x = target_x
            self._wave_target_x = target_x
            self._wave_strength = target_strength
            self._wave_target_strength = target_strength
            self._wave_last_frame_time = None
        self._wave_enabled = enabled
        self.queue_draw()

    def set_glow_enabled(self, enabled):
        self._glow_enabled = bool(enabled)
        self.queue_draw()

    def _tick(self, _widget, _frame_clock):
        if self._active and self._playing:
            if self._wave_enabled or self._glow_enabled:
                frame_time = _frame_clock.get_frame_time() / 1_000_000.0
                if self._wave_last_frame_time is None:
                    delta_time = 1.0 / 60.0
                else:
                    delta_time = max(0.0, frame_time - self._wave_last_frame_time)
                    delta_time = min(delta_time, 0.1)
                self._wave_last_frame_time = frame_time
                self._update_wave_motion(self._draw_position_ms, delta_time)
            self.queue_draw()
        return True

    @property
    def _draw_position_ms(self):
        if not self._active or not self._playing:
            return self._position_ms
        # AudioPlayer publishes every 200 ms. Interpolating only until the
        # next expected sample removes stepped karaoke without running ahead
        # indefinitely when playback is paused or loses its clock.
        elapsed = min(220.0, max(0.0, (time.monotonic() - self._position_anchor) * 1000.0))
        return self._position_ms + elapsed

    def _progress(self, word, position_ms=None, index=None):
        if position_ms is None:
            position_ms = self._draw_position_ms
        if index is not None and 0 <= index < len(self._word_timing):
            start, end = self._word_timing[index]
        else:
            start = word.start_time_ms + self._offset_ms
            end = word.end_time_ms
            if end is not None:
                end += self._offset_ms
        if end is None or end <= start:
            return 1.0 if position_ms >= start else 0.0
        return max(0.0, min(1.0, (position_ms - start) / (end - start)))

    def _clip_regions(self, context, regions, progress=1.0):
        """Clip a span in visual order, respecting RTL and wrapped regions."""
        if not regions or progress <= 0:
            return False
        total_width = sum(region[2] for region in regions)
        remaining = total_width * min(1.0, progress)
        context.new_path()
        for x, y, width, height in regions:
            amount = min(width, max(0.0, remaining))
            if amount > 0:
                clip_x = x + width - amount if self._rtl else x
                context.rectangle(clip_x, y, amount, height)
            remaining -= width
            if remaining <= 0:
                break
        context.clip()
        return True

    def _draw_layout(self, context, color, alpha):
        context.set_source_rgba(color.red, color.green, color.blue, max(0.0, alpha))
        PangoCairo.show_layout(context, self._layout)

    def _draw_base(self, context, color, alpha):
        if self._blur_radius <= 0.01:
            self._draw_layout(context, color, alpha)
            return
        # Cairo has no portable GTK blur primitive. A compact, cached Pango
        # layout plus a few low-alpha passes gives the same restrained 2.5px
        # focus falloff without rasterizing or rebuilding the line each tick.
        radius = self._blur_radius
        context.save()
        for dx, dy, weight in (
            (-radius, 0.0, 0.10),
            (radius, 0.0, 0.10),
            (0.0, -radius, 0.10),
            (0.0, radius, 0.10),
            (-radius * 0.6, -radius * 0.6, 0.07),
            (radius * 0.6, -radius * 0.6, 0.07),
            (-radius * 0.6, radius * 0.6, 0.07),
            (radius * 0.6, radius * 0.6, 0.07),
        ):
            context.save()
            context.translate(dx, dy)
            self._draw_layout(context, color, alpha * weight)
            context.restore()
        self._draw_layout(context, color, alpha * 0.38)
        context.restore()

    def _draw_regions(self, context, color, regions, alpha, progress=1.0):
        if not regions or progress <= 0:
            return
        context.save()
        if self._clip_regions(context, regions, progress):
            self._draw_layout(context, color, alpha)
        context.restore()

    def _draw_background_overlay(self, context, opacity):
        if not self._background_regions:
            return
        context.save()
        for regions in self._background_regions:
            for x, y, region_width, height in regions:
                context.rectangle(x, y, region_width, height)
        context.set_source_rgba(0.0, 0.0, 0.0, 0.26 * opacity)
        context.fill()
        context.restore()

    @staticmethod
    def _utf8_boundaries(text, start, end):
        """Return character boundaries relative to a UTF-8 byte range."""
        encoded = text.encode("utf-8")
        boundaries = []
        for index in range(start + 1, end):
            # Continuation bytes cannot be valid Pango split points. Avoiding
            # them also keeps PyGObject/Pango builds from emitting warnings.
            if (encoded[index] & 0xC0) != 0x80:
                boundaries.append(index - start)
        return boundaries

    def _cluster_word_index(self, start, end):
        for index, word_range in enumerate(self._word_ranges):
            if word_range is None:
                continue
            word_start, word_end = word_range
            if start < word_end and end > word_start:
                return index
        return -1

    def _build_shaped_clusters(self):
        """Cache complete Pango-shaped clusters for the active line.

        The old implementation rasterized the full line and replayed it
        through narrow strips. That necessarily re-sampled antialiased glyph
        edges. Instead, ask Pango for its shaped runs and split each run only
        at boundaries Pango itself accepts. Each resulting GlyphItem is a
        complete shaped cluster and is painted once per frame.
        """
        if self._layout is None:
            return []

        clusters = []
        lines = self._layout.get_lines_readonly()
        first_line_start = lines[0].get_start_index() if lines else 0
        first_origin = self._layout.index_to_pos(first_line_start)
        baseline_offset = self._layout.get_baseline() / Pango.SCALE - (first_origin.y / Pango.SCALE)

        for line_index, line in enumerate(lines):
            line_start = line.get_start_index()
            line_origin = self._layout.index_to_pos(line_start)
            _line_ink, line_logical = line.get_pixel_extents()
            line_x = line_origin.x / Pango.SCALE
            if line.get_resolved_direction() == Pango.Direction.RTL:
                # For a fully RTL line Pango reports index_to_x() from the
                # line's visual right edge, while index_to_pos() is that same
                # logical edge in the aligned layout. Convert it to the
                # visual left origin before placing the shaped item.
                line_x -= line_logical.width
            line_y = line_origin.y / Pango.SCALE
            _ink, logical = line.get_pixel_extents()
            top = line_y + logical.y
            height = max(1.0, float(logical.height))
            baseline = line_y + baseline_offset

            for run in line.runs:
                run_start = run.item.offset
                run_end = run_start + run.item.length
                if run_end <= run_start:
                    continue

                # GlyphItem.split() returns the shaped prefix and moves the
                # original copy to the suffix. The returned item length is
                # therefore the actual shaping/ligature boundary, not merely
                # a Python character boundary.
                boundaries = {run_start, run_end}
                for relative in self._utf8_boundaries(self._text, run_start, run_end):
                    prefix = run.copy().split(self._text, relative)
                    if prefix is not None:
                        boundaries.add(prefix.item.offset + prefix.item.length)
                boundaries = sorted(boundaries)

                raw = []
                for start, end in zip(boundaries, boundaries[1:]):
                    if end <= start:
                        continue
                    x_start = line_x + line.index_to_x(start, False) / Pango.SCALE
                    x_end = line_x + line.index_to_x(end, True) / Pango.SCALE
                    raw.append((start, end, x_start, x_end))

                # Combining marks can be represented by several accepted
                # split points while occupying one visual cell. Merge those
                # ranges before creating the GlyphItem.
                merged = []
                for start, end, x_start, x_end in raw:
                    if merged and abs(x_start - merged[-1][2]) < 0.001:
                        previous = merged[-1]
                        merged[-1] = (previous[0], end, previous[2], x_end)
                    else:
                        merged.append((start, end, x_start, x_end))

                rtl = bool(run.item.analysis.level % 2)
                for start, end, x_start, x_end in merged:
                    relative_start = start - run_start
                    relative_length = end - start
                    shaped_tail = run.copy()
                    if relative_start:
                        shaped_tail.split(self._text, relative_start)
                    shaped = (
                        shaped_tail
                        if end == run_end
                        else shaped_tail.split(self._text, relative_length)
                    )
                    if shaped is None:
                        continue

                    glyph_width = shaped.glyphs.get_width() / Pango.SCALE
                    draw_x = x_start
                    if rtl:
                        # A RTL GlyphItem is drawn leftwards from its logical
                        # start edge; this keeps its origin identical to the
                        # canonical Pango layout.
                        draw_x -= glyph_width
                    visual_left = min(x_start, x_end)
                    visual_right = max(x_start, x_end)
                    visual_width = max(0.0, visual_right - visual_left)
                    clusters.append(
                        {
                            "item": shaped,
                            "start": start,
                            "end": end,
                            "x": draw_x,
                            "left": visual_left,
                            "width": visual_width,
                            "top": top,
                            "height": height,
                            "baseline": baseline,
                            "line": line_index,
                            "word": self._cluster_word_index(start, end),
                            "rtl": rtl,
                        }
                    )

        # Build a stable visual fill order per timed span. It is used only
        # for the coincident bright-color pass; geometry remains Pango's.
        by_word = {}
        for cluster in clusters:
            word_index = cluster["word"]
            if word_index >= 0:
                by_word.setdefault(word_index, []).append(cluster)
        for word_clusters in by_word.values():
            word_clusters.sort(
                key=lambda cluster: (
                    cluster["line"],
                    -cluster["left"] if self._rtl else cluster["left"],
                )
            )
            total = sum(cluster["width"] for cluster in word_clusters)
            cursor = 0.0
            for cluster in word_clusters:
                cluster["fill_start"] = cursor
                cluster["fill_width"] = cluster["width"]
                cluster["fill_total"] = total
                cursor += cluster["width"]
        return clusters

    def _cursor_x_for_span(self, index, progress):
        regions = self._word_regions[index]
        if not regions:
            return 0.0
        total_width = sum(region[2] for region in regions)
        remaining = total_width * max(0.0, min(1.0, progress))
        for x, _y, width, _height in regions:
            amount = min(width, max(0.0, remaining))
            if remaining <= width:
                return x + width - amount if self._rtl else x + amount
            remaining -= width
        x, _y, width, _height = regions[-1]
        return x if self._rtl else x + width

    def _visual_cursor_x(self, position_ms):
        """Map timing to the target X of the shared emphasis field."""
        return self._wave_state_for_position(position_ms)[0]

    @staticmethod
    def _smoothstep(value):
        value = max(0.0, min(1.0, value))
        return value * value * (3.0 - 2.0 * value)

    def _wave_state_for_position(self, position_ms):
        """Return a visual target and strength for the current timing.

        The exact TTML position still controls the WBW color pass. The wave
        gets a separate spatial target so a short gap can travel between span
        endpoints, while a longer silence lets the field settle away.
        """
        if not self._words or not self._word_timing:
            return 0.0, 0.0

        first_start = self._word_timing[0][0]
        if position_ms < first_start:
            return self._cursor_x_for_span(0, 0.0), 0.0

        for index, (start, end) in enumerate(self._word_timing):
            if position_ms <= end:
                progress = (position_ms - start) / max(1, end - start)
                return self._cursor_x_for_span(index, progress), 1.0

            if index + 1 >= len(self._word_timing):
                break
            next_start = self._word_timing[index + 1][0]
            if position_ms >= next_start:
                continue

            previous_x = self._cursor_x_for_span(index, 1.0)
            next_x = self._cursor_x_for_span(index + 1, 0.0)
            gap = max(0, next_start - end)
            elapsed = max(0, position_ms - end)
            if gap <= 350:
                progress = self._smoothstep(elapsed / max(1, gap))
                # Keep a little vocal energy through a short consonant or
                # breath gap, then enter the next span without a bump.
                strength = 0.72 + 0.28 * progress
                return (
                    previous_x + (next_x - previous_x) * progress,
                    strength,
                )

            # A real pause stays near the last sung location and gently
            # releases the deformation instead of freezing a large bump.
            decay = self._smoothstep(min(1.0, elapsed / 520.0))
            return previous_x, 0.72 * (1.0 - decay)

        last_index = len(self._word_timing) - 1
        last_end = self._word_timing[last_index][1]
        elapsed = max(0, position_ms - last_end)
        decay = self._smoothstep(min(1.0, elapsed / 520.0))
        return self._cursor_x_for_span(last_index, 1.0), 0.72 * (1.0 - decay)

    def _update_wave_motion(self, position_ms, delta_time):
        target_x, target_strength = self._wave_state_for_position(position_ms)
        self._wave_target_x = target_x
        self._wave_target_strength = target_strength
        if self._wave_x is None:
            self._wave_x = target_x
            self._wave_strength = target_strength
            return

        # Exponential convergence is independent of refresh rate and is just
        # enough to absorb player samples arriving every ~200 ms.
        alpha = 1.0 - math.exp(-max(0.0, delta_time) / 0.075)
        self._wave_x += (target_x - self._wave_x) * alpha
        self._wave_strength += (target_strength - self._wave_strength) * alpha

    def _update_wave_typography(self):
        widths = [
            cluster["width"]
            for cluster in self._shaped_clusters
            if cluster["width"] > 1.0 and cluster["word"] >= 0
        ]
        heights = [cluster["height"] for cluster in self._shaped_clusters]
        average_width = sum(widths) / len(widths) if widths else 24.0
        font_height = max(heights, default=42.0)
        self._wave_radius = max(average_width * 3.5, font_height * 1.4)
        self._wave_leading_radius = self._wave_radius * 0.9
        self._wave_trailing_radius = self._wave_radius * 1.18
        self._wave_lift = max(2.0, min(4.0, font_height * 0.06))
        self._wave_glow_radius = max(2.0, min(3.0, font_height * 0.055))

    def _wave_influence(self, left, center, right, wave_x):
        """Sample one broad, asymmetric spatial wave over a cluster."""
        if self._rtl:
            trailing = center > wave_x
        else:
            trailing = center < wave_x
        radius = self._wave_trailing_radius if trailing else self._wave_leading_radius

        def sample(x):
            distance = abs(x - wave_x)
            normalized = min(1.0, distance / max(1.0, radius))
            if normalized >= 1.0:
                return 0.0
            envelope = 0.5 + 0.5 * math.cos(math.pi * normalized)
            return envelope * envelope

        return (sample(left) + 2.0 * sample(center) + sample(right)) / 4.0

    def _cluster_fill_fraction(self, cluster, progress):
        total = cluster.get("fill_total", 0.0)
        width = cluster.get("fill_width", 0.0)
        if total <= 0.0 or width <= 0.0:
            return 1.0 if progress >= 1.0 else 0.0
        amount = total * max(0.0, min(1.0, progress))
        return max(
            0.0,
            min(1.0, (amount - cluster["fill_start"]) / width),
        )

    def _draw_shaped_cluster(
        self,
        context,
        color,
        cluster,
        dim_alpha,
        bright_alpha,
        scale_x,
        scale_y,
        lift_y=0.0,
        glow_alpha=0.0,
        glow_radius=0.0,
        bright_fraction=0.0,
    ):
        """Paint one complete shaped cluster with no raster overlap."""
        if dim_alpha <= 0.0 and bright_alpha <= 0.0:
            return
        center_x = cluster["left"] + cluster["width"] * 0.5
        baseline = cluster["baseline"]

        def paint(source_alpha, fraction=None, offset_x=0.0, offset_y=0.0):
            if source_alpha <= 0.0:
                return
            context.save()
            # Move the pivot itself before scaling so lift_y remains a real
            # device-space displacement rather than being multiplied by sy.
            context.translate(center_x, baseline + lift_y)
            context.scale(scale_x, scale_y)
            context.translate(-center_x, -baseline)
            if fraction is not None and fraction < 1.0:
                amount = cluster["width"] * max(0.0, fraction)
                if amount <= 0.0:
                    context.restore()
                    return
                clip_x = (
                    cluster["left"] + cluster["width"] - amount if self._rtl else cluster["left"]
                )
                context.rectangle(
                    clip_x,
                    cluster["top"] - cluster["height"],
                    amount,
                    cluster["height"] * 3.0,
                )
                context.clip()
            # The GlyphItem's coordinates are relative to the run origin.
            # Apply that canonical origin only after the fill clip has been
            # established in line coordinates.
            context.translate(
                cluster["x"] + offset_x / max(scale_x, 0.001),
                baseline + offset_y / max(scale_y, 0.001),
            )
            context.set_source_rgba(
                color.red,
                color.green,
                color.blue,
                max(0.0, min(1.0, source_alpha)),
            )
            PangoCairo.show_glyph_item(context, self._text, cluster["item"])
            context.restore()

        if glow_alpha > 0.0 and glow_radius > 0.0:
            # Cairo/Pango has no portable small-radius text blur primitive.
            # The alpha is intentionally per diffuse sample rather than
            # divided across the whole ring: one sample may be the only one
            # outside a glyph edge. Keep each sample low enough that the
            # combined result remains a soft bloom.
            glow_pass_alpha = min(0.045, glow_alpha * 0.38)
            diagonal = glow_radius * 0.707
            for offset_x, offset_y in (
                (-glow_radius, 0.0),
                (glow_radius, 0.0),
                (0.0, -glow_radius),
                (0.0, glow_radius),
                (-diagonal, -diagonal),
                (diagonal, -diagonal),
                (-diagonal, diagonal),
                (diagonal, diagonal),
            ):
                paint(glow_pass_alpha, offset_x=offset_x, offset_y=offset_y)

        # A completed span has one final color pass. A partially sung span
        # gets a coincident bright pass over its dim mask; geometry and
        # antialiasing are identical, so no second displaced edge is created.
        if bright_fraction >= 1.0:
            paint(bright_alpha)
        else:
            paint(dim_alpha)
            paint(bright_alpha, bright_fraction)

    def _draw_shaped_active_line(self, context, color, position_ms, progresses):
        target_x, target_strength = self._wave_state_for_position(position_ms)
        if self._wave_x is None:
            self._wave_x = target_x
            self._wave_target_x = target_x
            self._wave_strength = target_strength
            self._wave_target_strength = target_strength
        cursor_x = self._wave_x
        wave_strength = self._wave_strength
        # A draw can happen before the first tick after a position update.
        # Keep the target current without advancing the smoothed state here.
        self._wave_target_x = target_x
        self._wave_target_strength = target_strength
        for cluster in self._shaped_clusters:
            word_index = cluster["word"]
            word = self._words[word_index] if word_index >= 0 else None
            progress = progresses[word_index] if word is not None else 0.0
            left = cluster["left"]
            right = left + cluster["width"]
            center = (left + right) * 0.5
            influence = self._wave_influence(left, center, right, cursor_x)
            influence *= wave_strength
            soft = influence * influence * (3.0 - 2.0 * influence)
            if self._wave_enabled:
                scale_x = 1.0 + soft * 0.018
                scale_y = 1.0 + soft * 0.105
                lift_y = -self._wave_lift * soft
            else:
                scale_x = 1.0
                scale_y = 1.0
                lift_y = 0.0
            glow_strength = max(0.0, min(1.0, influence)) ** 1.5 if self._glow_enabled else 0.0
            background_factor = 0.55 if word is not None and word.background_vocal else 1.0
            if progress >= 1.0:
                bright_fraction = 1.0
            elif progress > 0.0:
                bright_fraction = self._cluster_fill_fraction(cluster, progress)
            else:
                bright_fraction = 0.0
            self._draw_shaped_cluster(
                context,
                color,
                cluster,
                dim_alpha=self._BASE_ALPHA[self._focus_state]
                * self._focus_opacity
                * background_factor,
                bright_alpha=self._focus_opacity * background_factor,
                scale_x=scale_x,
                scale_y=scale_y,
                lift_y=lift_y,
                glow_alpha=self._focus_opacity * background_factor * 0.15 * glow_strength,
                glow_radius=self._wave_glow_radius,
                bright_fraction=bright_fraction,
            )

    def _draw(self, _area, context, width, _height):
        if self._layout is None:
            return
        self._set_layout_width(width)
        color = self._style_source.get_color()
        focus_alpha = self._focus_opacity
        position_ms = self._draw_position_ms
        base_alpha = (
            1.0
            if self._focus_state == "active" and not self._words
            else self._BASE_ALPHA[self._focus_state]
        ) * focus_alpha

        if not self._active or not self._word_regions:
            self._draw_base(context, color, base_alpha)
            self._draw_background_overlay(context, focus_alpha)
            return

        completed_lead = []
        completed_background = []
        current_lead = []
        current_background = []
        progresses = []
        current_lead_progress = 0.0
        current_background_progress = 0.0
        for index, (word, regions) in enumerate(zip(self._words, self._word_regions)):
            progress = self._progress(word, position_ms, index)
            progresses.append(progress)
            if progress >= 1.0:
                (completed_background if word.background_vocal else completed_lead).extend(regions)
            elif progress > 0.0:
                if word.background_vocal:
                    current_background.extend(regions)
                    current_background_progress = max(current_background_progress, progress)
                else:
                    current_lead.extend(regions)
                    current_lead_progress = max(current_lead_progress, progress)

        if (self._wave_enabled or self._glow_enabled) and self._words and self._shaped_clusters:
            # Paint each complete shaped Pango cluster once. There is no
            # canonical full-line copy underneath and no raster strip to
            # re-sample antialiased edges.
            self._draw_shaped_active_line(context, color, position_ms, progresses)
            return

        # Reduced motion keeps the normal WBW color passes but does not run
        # the visual deformation.
        self._draw_base(context, color, base_alpha)
        self._draw_background_overlay(context, focus_alpha)
        self._draw_regions(context, color, completed_lead, focus_alpha, 1.0)
        self._draw_regions(context, color, current_lead, focus_alpha, current_lead_progress)
        self._draw_regions(context, color, completed_background, focus_alpha * 0.55, 1.0)
        self._draw_regions(
            context,
            color,
            current_background,
            focus_alpha * 0.55,
            current_background_progress,
        )


class LyricsView(Gtk.ScrolledWindow):
    __gsignals__ = {
        "seek-requested": (GObject.SignalFlags.RUN_LAST, None, (float,)),
        "manual-scroll": (GObject.SignalFlags.RUN_LAST, None, ()),
        "mode-changed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self):
        super().__init__(vexpand=True, hexpand=True)
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._document = None
        self._documents = {}
        self._document_rows = {}
        self._mode = None
        self._setting_documents = False
        self._last_position_ms = 0
        self._buttons = []
        self._word_renderers = {}
        self._active_word = (-1, -1)
        self._active_index = -1
        self._upcoming_index = -1
        self._music_icon_size = 20
        self._auto_follow = True
        self._playing = False
        self._programmatic_scroll = False
        self._focus_timers = {}
        self._focus_animations = {}
        self._scroll_animation = None
        self._animations_enabled = self._read_animation_preference()
        self._wave_preference = getattr(self, "_wave_preference", True)
        self._glow_preference = getattr(self, "_glow_preference", True)
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self._content.set_margin_top(36)
        self._content.set_margin_bottom(36)
        self._content.set_margin_start(24)
        self._content.set_margin_end(24)
        self._content.set_halign(Gtk.Align.CENTER)
        self._content.set_valign(Gtk.Align.START)
        self._content.set_size_request(360, -1)
        self.set_child(self._content)
        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    def _read_animation_preference(self):
        """Follow both GTK's reduced-motion setting and Groovia's preference."""
        gtk_settings = Gtk.Settings.get_default()
        system_enabled = True
        if gtk_settings is not None:
            system_enabled = bool(gtk_settings.get_property("gtk-enable-animations"))
            gtk_settings.connect(
                "notify::gtk-enable-animations", self._on_animation_setting_changed
            )
        self._app_settings = None
        app_enabled = True
        self._wave_preference = True
        self._glow_preference = True
        try:
            schema_source = Gio.SettingsSchemaSource.get_default()
            schema = (
                schema_source.lookup("io.github.Lluciocc.Groovia", True) if schema_source else None
            )
            if schema is not None:
                self._app_settings = Gio.Settings.new_full(schema, None, None)
                app_enabled = self._app_settings.get_boolean("animations")
                self._app_settings.connect(
                    "changed::animations", self._on_animation_setting_changed
                )
                self._wave_preference = self._read_boolean_setting("lyrics-wave", True)
                self._glow_preference = self._read_boolean_setting("lyrics-glow", True)
                self._app_settings.connect(
                    "changed::lyrics-wave", self._on_animation_setting_changed
                )
                self._app_settings.connect(
                    "changed::lyrics-glow", self._on_animation_setting_changed
                )
        except (GLib.Error, TypeError):
            pass
        return system_enabled and app_enabled

    def _read_boolean_setting(self, key, default):
        if self._app_settings is None:
            return default
        try:
            return bool(self._app_settings.get_boolean(key))
        except (GLib.Error, TypeError):
            return default

    def _on_animation_setting_changed(self, *_args):
        gtk_settings = Gtk.Settings.get_default()
        system_enabled = (
            True
            if gtk_settings is None
            else bool(gtk_settings.get_property("gtk-enable-animations"))
        )
        app_enabled = (
            True if self._app_settings is None else self._app_settings.get_boolean("animations")
        )
        self._animations_enabled = system_enabled and app_enabled
        self._wave_preference = self._read_boolean_setting("lyrics-wave", True)
        self._glow_preference = self._read_boolean_setting("lyrics-glow", True)
        for renderer in self._word_renderers.values():
            renderer.set_wave_enabled(self._animations_enabled and self._wave_preference)
            renderer.set_glow_enabled(self._animations_enabled and self._glow_preference)
        if not self._animations_enabled:
            self._clear_focus_transitions()
            for line_index in range(max(0, self._active_index)):
                self._set_focus(line_index, "past", opacity=0.5, blur=2.5)
            if self._scroll_animation:
                self._scroll_animation.skip()
                self._scroll_animation = None

    @staticmethod
    def _label_for(widget):
        return widget.get_child() if isinstance(widget, Gtk.Button) else widget

    def _clear_focus_transitions(self):
        for source_id in self._focus_timers.values():
            GLib.source_remove(source_id)
        self._focus_timers.clear()
        for animation in self._focus_animations.values():
            animation.skip()
        self._focus_animations.clear()

    def _cancel_focus_transition(self, index):
        source_id = self._focus_timers.pop(index, None)
        if source_id is not None:
            GLib.source_remove(source_id)
        animation = self._focus_animations.pop(index, None)
        if animation is not None:
            animation.skip()

    def _set_scale(self, index, scale):
        if not 0 <= index < len(self._buttons):
            return

        renderer = self._word_renderers.get(index)
        if renderer is not None:
            renderer.set_scale(scale)
            return

        child = self._label_for(self._buttons[index])

        if isinstance(child, Gtk.Label):
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_scale_new(float(scale)))
            child.set_attributes(attrs)

        elif isinstance(child, Gtk.Image):
            child.set_pixel_size(round(self._music_icon_size * float(scale)))

    def _set_line_visuals(self, index, scale, opacity):
        if not 0 <= index < len(self._buttons):
            return
        # Keep line geometry stable. Spotlight focuses through contrast and
        # blur, never through the old active-line zoom.
        if self._word_renderers.get(index) is None:
            self._buttons[index].set_opacity(opacity)

    def _set_focus(self, index, state, *, opacity=1.0, blur=0.0):
        if not 0 <= index < len(self._buttons):
            return
        renderer = self._word_renderers.get(index)
        if renderer is not None:
            renderer.set_focus(state, opacity=opacity, blur=blur)
        else:
            self._buttons[index].set_opacity(opacity)

    def _begin_past_falloff(self, index):
        self._focus_timers.pop(index, None)
        if not 0 <= index < len(self._buttons):
            return GLib.SOURCE_REMOVE
        if not self._animations_enabled:
            self._set_focus(index, "past", opacity=0.5, blur=2.5)
            return GLib.SOURCE_REMOVE

        def update(value):
            progress = float(value)
            self._set_focus(
                index,
                "past",
                opacity=0.82 - 0.32 * progress,
                blur=2.5 * progress,
            )

        animation = Adw.TimedAnimation.new(
            self, 0.0, 1.0, 500, Adw.CallbackAnimationTarget.new(update)
        )
        animation.set_easing(Adw.Easing.EASE_IN_OUT_CUBIC)
        self._focus_animations[index] = animation
        GLib.timeout_add(520, self._drop_focus_animation, index, animation)
        animation.play()
        return GLib.SOURCE_REMOVE

    def _animate_active_focus(self, index):
        self._cancel_focus_transition(index)
        if not self._animations_enabled:
            self._set_focus(index, "active", opacity=1.0, blur=0.0)
            return

        def update(value):
            self._set_focus(index, "active", opacity=0.78 + 0.22 * float(value), blur=0.0)

        animation = Adw.TimedAnimation.new(
            self, 0.0, 1.0, 166, Adw.CallbackAnimationTarget.new(update)
        )
        animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._focus_animations[index] = animation
        GLib.timeout_add(190, self._drop_focus_animation, index, animation)
        animation.play()

    def _drop_focus_animation(self, index, animation):
        if self._focus_animations.get(index) is animation:
            self._focus_animations.pop(index, None)
        return GLib.SOURCE_REMOVE

    def _schedule_past_falloff(self, index, delay=350):
        self._cancel_focus_transition(index)
        if not 0 <= index < len(self._buttons):
            return
        if not self._animations_enabled or delay <= 0:
            self._begin_past_falloff(index)
            return
        self._focus_timers[index] = GLib.timeout_add(delay, self._begin_past_falloff, index)

    def _scroll_to_index(self, index, animated=True):
        if not 0 <= index < len(self._buttons):
            return GLib.SOURCE_REMOVE
        adjustment = self.get_vadjustment()
        allocation = self._buttons[index].get_allocation()
        target = max(0.0, allocation.y - adjustment.get_page_size() * 0.42)
        target = min(target, max(0.0, adjustment.get_upper() - adjustment.get_page_size()))
        current = adjustment.get_value()
        if not animated or not self._animations_enabled or abs(target - current) < 1:
            adjustment.set_value(target)
            return GLib.SOURCE_REMOVE

        if self._scroll_animation is not None:
            self._scroll_animation.skip()
        holder = {}

        def update(value):
            adjustment.set_value(current + (target - current) * float(value))

        animation = Adw.TimedAnimation.new(
            self, 0.0, 1.0, 360, Adw.CallbackAnimationTarget.new(update)
        )
        animation.set_easing(Adw.Easing.EASE_IN_OUT_CUBIC)
        holder["animation"] = animation
        self._scroll_animation = animation
        animation.play()
        return GLib.SOURCE_REMOVE

    def _follow_index(self, index):
        if self._auto_follow:
            # Recalculate after GTK has laid out the line, then softly move the
            # focus zone. Word ticks never call this method.
            GLib.idle_add(self._scroll_to_index, index, True)

    def _transition_to(self, index):
        previous = self._active_index
        if 0 <= previous < len(self._buttons) and previous != index:
            self._buttons[previous].remove_css_class("lyrics-current")
            renderer = self._word_renderers.get(previous)
            if renderer is not None:
                renderer.set_active(False)
            # Spotlight keeps the just-finished line sharp for 350 ms before
            # its 500 ms opacity/blur falloff.
            if self._auto_follow:
                self._set_focus(previous, "past_pending", opacity=0.82, blur=0.0)
                self._schedule_past_falloff(previous)
            else:
                self._set_focus(previous, "past_pending", opacity=0.78, blur=0.0)

        self._active_index = index
        if 0 <= index < len(self._buttons):
            self._buttons[index].add_css_class("lyrics-current")
            renderer = self._word_renderers.get(index)
            if renderer is not None:
                renderer.set_active(True)
                renderer.set_playing(self._playing)
            self._set_focus(index, "active", opacity=0.78, blur=0.0)
            self._animate_active_focus(index)

        # Seeking backward or jumping across a document must restore the
        # explicit PAST/FUTURE states instead of leaving stale blurred lines.
        for line_index in range(len(self._buttons)):
            if line_index == index or line_index == previous:
                continue
            self._cancel_focus_transition(line_index)
            if line_index < index:
                if self._auto_follow:
                    self._set_focus(line_index, "past", opacity=0.5, blur=2.5)
                else:
                    self._set_focus(line_index, "past_pending", opacity=0.78, blur=0.0)
            else:
                self._set_focus(line_index, "future", opacity=1.0, blur=0.0)

        self._upcoming_index = index + 1 if index + 1 < len(self._buttons) else -1
        if self._upcoming_index >= 0:
            self._set_focus(self._upcoming_index, "future", opacity=1.0, blur=0.0)
        self._follow_index(index)

    def _on_scroll(self, _controller, _dx, _dy):
        if not self._programmatic_scroll:
            self._auto_follow = False
            for line_index in range(self._active_index):
                self._cancel_focus_transition(line_index)
                self._set_focus(line_index, "past_pending", opacity=0.78, blur=0.0)
            self.emit("manual-scroll")
        return False

    def set_document(self, document):
        if not self._setting_documents:
            self._documents = {}
            self._document_rows = {}
            self._mode = self._mode_for_document(document)
        self._document = document
        self._active_index = -1
        self._upcoming_index = -1
        self._auto_follow = True
        self._clear_focus_transitions()
        if self._scroll_animation:
            self._scroll_animation.skip()
            self._scroll_animation = None
        for child in iter_gtk_children(self._content):
            self._content.remove(child)
        self._buttons = []
        self._word_renderers = {}
        self._active_word = (-1, -1)
        if not document:
            return
        primary_agent = next(
            (line.speaker_agent for line in document.lines if line.speaker_agent), None
        )
        for index, line in enumerate(document.lines):
            if document.synchronized:
                if line.text.strip():
                    # Both word-synced and line-only documents use the same
                    # renderer. The latter simply has no timing clips, which
                    # keeps Spotlight identical in Lines and Words modes.
                    button = Gtk.Button(has_frame=False, focusable=True)
                    button.add_css_class("lyrics-line")
                    if line.speaker_agent and line.speaker_agent != primary_agent:
                        button.add_css_class("lyrics-alt-speaker")
                    if line.background_vocals:
                        button.add_css_class("lyrics-group-vocal")
                    renderer = WordSyncedLyricsRenderer(
                        line.text,
                        line.words,
                        button,
                        document.offset_ms,
                        document.rtl,
                    )
                    renderer.set_wave_enabled(self._animations_enabled and self._wave_preference)
                    renderer.set_glow_enabled(self._animations_enabled and self._glow_preference)
                    button.set_child(renderer)
                    self._word_renderers[index] = renderer
                    button.set_tooltip_text("Seek to this lyric line")
                    button.connect(
                        "clicked",
                        lambda _button, line=line: self.emit(
                            "seek-requested",
                            (line.start_time_ms + document.offset_ms) / 1000.0,
                        ),
                    )
                    self._buttons.append(button)
                    renderer.set_focus("future", opacity=1.0, blur=0.0)
                    self._content.append(button)
                else:
                    button = Gtk.Button(has_frame=False, focusable=True)
                    content = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
                    content.set_pixel_size(self._music_icon_size)
                    content.add_css_class("lyrics-music-icon")
                    button.set_child(content)
                    button.add_css_class("lyrics-line")
                    if line.speaker_agent and line.speaker_agent != primary_agent:
                        button.add_css_class("lyrics-alt-speaker")
                    if line.background_vocals:
                        button.add_css_class("lyrics-group-vocal")
                    button.set_tooltip_text("Seek to this lyric line")
                    button.connect(
                        "clicked",
                        lambda _button, line=line: self.emit(
                            "seek-requested",
                            (line.start_time_ms + document.offset_ms) / 1000.0,
                        ),
                    )
                    self._buttons.append(button)
                    self._set_line_visuals(index, 1.0, 1.0)
                    self._content.append(button)

            else:
                label = Gtk.Label(
                    label=line.text,
                    wrap=True,
                    justify=Gtk.Justification.CENTER,
                )
                if document.rtl:
                    label.set_direction(Gtk.TextDirection.RTL)
                label.add_css_class("lyrics-line")
                self._buttons.append(label)
                self._set_line_visuals(index, 1.0, 1.0)
                self._content.append(label)

    @staticmethod
    def _mode_for_document(document):
        if not document:
            return None
        if document.word_synchronized:
            return "word"
        if document.synchronized:
            return "line"
        return "plain"

    def set_documents(self, variants, preferred_mode="line"):
        """Install available lyric variants and select line mode by default."""
        self._documents = {}
        self._document_rows = {}
        for timeline, row in variants or ():
            mode = self._mode_for_document(timeline)
            if mode and mode not in self._documents:
                self._documents[mode] = timeline
                self._document_rows[mode] = row
        if not self._documents:
            self._mode = None
            self.set_document(None)
            return
        mode = (
            preferred_mode
            if preferred_mode in self._documents
            else next(
                (
                    candidate
                    for candidate in ("line", "word", "plain")
                    if candidate in self._documents
                ),
                None,
            )
        )
        self._mode = mode
        self._setting_documents = True
        try:
            self.set_document(self._documents[mode])
        finally:
            self._setting_documents = False
        self.update_position(self._last_position_ms)

    def set_mode(self, mode):
        if mode not in self._documents or mode == self._mode:
            return False
        self._mode = mode
        self._setting_documents = True
        try:
            self.set_document(self._documents[mode])
        finally:
            self._setting_documents = False
        self.update_position(self._last_position_ms)
        self.emit("mode-changed", mode)
        return True

    def update_position(self, position_ms: int):
        self._last_position_ms = int(position_ms)
        if not self._document or not self._document.synchronized or not self._buttons:
            return
        index = self._document.current_index(position_ms)
        if index != self._active_index:
            self._transition_to(index)
        if self._document.word_synchronized:
            self._update_word(position_ms)

    def set_playing(self, playing):
        """Allow the active renderer to interpolate between 200ms samples."""
        self._playing = bool(playing)
        renderer = self._word_renderers.get(self._active_index)
        if renderer is not None:
            renderer.set_playing(self._playing)

    def _update_word(self, position_ms: int):
        line_index = self._active_index
        renderer = self._word_renderers.get(line_index)
        if renderer is None:
            return
        word_index = self._document.current_word_index(line_index, position_ms)
        previous_line, previous_word = self._active_word
        if (previous_line, previous_word) != (line_index, word_index):
            self._active_word = (line_index, word_index)
        renderer.set_position(position_ms, word_index)

    def return_to_current(self):
        self._auto_follow = True
        for line_index in range(max(0, self._active_index)):
            self._set_focus(line_index, "past_pending", opacity=0.78, blur=0.0)
            self._schedule_past_falloff(line_index, delay=0)
        if 0 <= self._active_index < len(self._buttons):
            self._scroll_to_index(self._active_index, animated=True)

    @property
    def synchronized(self):
        return bool(self._document and self._document.synchronized)

    @property
    def word_synchronized(self):
        return bool(self._document and self._document.word_synchronized)

    @property
    def document(self):
        return self._document

    @property
    def mode(self):
        return self._mode

    @property
    def available_modes(self):
        return tuple(mode for mode in ("line", "word", "plain") if mode in self._documents)

    @property
    def selected_row(self):
        return self._document_rows.get(self._mode)

    @property
    def variant_rows(self):
        return tuple(
            row for row in self._document_rows.values() if row and row.get("id") is not None
        )
