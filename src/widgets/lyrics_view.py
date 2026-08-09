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

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango, PangoCairo

from ..platform_compat import iter_gtk_children


class WordSyncedLyricsRenderer(Gtk.DrawingArea):
    """Draw one word-synced line with a single, canonical Pango layout.

    The line is intentionally rendered as one layout.  The base pass draws
    the whole line in the same muted color as an inactive lyric, and the
    highlight pass draws that same layout through cached glyph regions.
    """

    def __init__(self, text, words, style_source, offset_ms):
        super().__init__()
        self._text = text or ""
        self._words = words
        self._style_source = style_source
        self._offset_ms = int(offset_ms)
        self._layout = None
        self._layout_width = None
        self._word_ranges = []
        self._word_regions = []
        self._scale = 1.0
        self._active = False
        self._position_ms = 0
        self._word_index = -1
        self.set_hexpand(True)
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)
        self._rebuild_layout()

    @staticmethod
    def _byte_length(text):
        return len(text.encode("utf-8"))

    def _rebuild_word_ranges(self):
        """Map parsed word text to UTF-8 ranges in the canonical line text."""
        self._word_ranges = []
        cursor = 0
        for word in self._words:
            word_text = word.text or ""
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
        # Build from the existing line button's style context so font family,
        # size and inherited application styling stay identical to labels.
        self._layout = self._style_source.create_pango_layout(self._text)
        self._layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        self._layout.set_alignment(Pango.Alignment.CENTER)
        self._apply_font_attributes()
        self._rebuild_word_ranges()
        self._set_layout_width(self.get_width())
        self.queue_resize()
        self.queue_draw()

    def _apply_font_attributes(self):
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_scale_new(float(self._scale)))
        if self._active:
            attrs.insert(Pango.attr_weight_new(Pango.Weight.ULTRABOLD))
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

    def _build_word_regions(self):
        """Cache rectangles covering each word's glyphs, including wrapping."""
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
        self._apply_font_attributes()
        self.queue_resize()

    def set_position(self, position_ms, word_index):
        self._position_ms = int(position_ms)
        self._word_index = int(word_index)
        self.queue_draw()

    def _progress(self, word):
        start = word.start_time_ms + self._offset_ms
        end = word.end_time_ms
        if end is not None:
            end += self._offset_ms
        if end is None or end <= start:
            return 1.0 if self._position_ms >= start else 0.0
        return max(0.0, min(1.0, (self._position_ms - start) / (end - start)))

    def _clip_regions(self, context, regions, progress=1.0, inflate=0.0):
        for x, y, width, height in regions:
            context.rectangle(
                x - inflate,
                y - inflate,
                max(1.0, width * progress + inflate * 2),
                height + inflate * 2,
            )
        context.clip()

    def _draw_layout(self, context, color, alpha):
        context.set_source_rgba(color.red, color.green, color.blue, alpha)
        PangoCairo.show_layout(context, self._layout)

    def _draw(self, _area, context, width, _height):
        if self._layout is None:
            return
        self._set_layout_width(width)
        color = self._style_source.get_color()

        # Upcoming words remain visible in the same translucent gray used by
        # inactive synchronized lines.
        self._draw_layout(context, color, 0.58)

        if not self._active or not self._word_regions:
            return

        current_progress = 0.0
        if 0 <= self._word_index < len(self._words):
            current_progress = self._progress(self._words[self._word_index])

        completed = [
            region
            for index, regions in enumerate(self._word_regions)
            if index < self._word_index
            for region in regions
        ]
        current = (
            self._word_regions[self._word_index]
            if 0 <= self._word_index < len(self._word_regions)
            else []
        )

        if current and current_progress > 0.0:
            glow_alpha = 0.11 * (current_progress * (1.0 - current_progress) * 4.0)
            if glow_alpha > 0.0:
                context.save()
                self._clip_regions(context, current, current_progress, inflate=2.0)
                for x_offset, y_offset, alpha in (
                    (-1.0, 0.0, glow_alpha * 0.45),
                    (1.0, 0.0, glow_alpha * 0.45),
                    (0.0, -1.0, glow_alpha * 0.55),
                    (0.0, 1.0, glow_alpha * 0.55),
                ):
                    context.save()
                    context.translate(x_offset, y_offset)
                    self._draw_layout(context, color, alpha)
                    context.restore()
                context.restore()

        if completed or (current and current_progress > 0.0):
            context.save()
            for region in completed:
                context.rectangle(region[0], region[1], region[2], region[3])
            if current and current_progress > 0.0:
                self._clip_regions(context, current, current_progress)
            else:
                context.clip()
            self._draw_layout(context, color, 1.0)
            context.restore()


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
        self._programmatic_scroll = False
        self._animations = {}
        self._scroll_animation = None
        self._animations_enabled = self._read_animation_preference()
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
        try:
            schema_source = Gio.SettingsSchemaSource.get_default()
            schema = (
                schema_source.lookup("io.github.Lluciocc.Groovia", True)
                if schema_source
                else None
            )
            if schema is not None:
                self._app_settings = Gio.Settings.new_full(schema, None, None)
                app_enabled = self._app_settings.get_boolean("animations")
                self._app_settings.connect(
                    "changed::animations", self._on_animation_setting_changed
                )
        except (GLib.Error, TypeError):
            pass
        return system_enabled and app_enabled

    def _on_animation_setting_changed(self, *_args):
        gtk_settings = Gtk.Settings.get_default()
        system_enabled = (
            True
            if gtk_settings is None
            else bool(gtk_settings.get_property("gtk-enable-animations"))
        )
        app_enabled = (
            True
            if self._app_settings is None
            else self._app_settings.get_boolean("animations")
        )
        self._animations_enabled = system_enabled and app_enabled
        if not self._animations_enabled:
            for animation in self._animations.values():
                animation.skip()
            self._animations.clear()
            if self._scroll_animation:
                self._scroll_animation.skip()
                self._scroll_animation = None

    @staticmethod
    def _label_for(widget):
        return widget.get_child() if isinstance(widget, Gtk.Button) else widget

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
        self._set_scale(index, scale)
        self._buttons[index].set_opacity(opacity)

    def _animate_line(self, index, from_scale, to_scale, from_opacity, to_opacity):
        if not 0 <= index < len(self._buttons):
            return
        previous = self._animations.pop(index, None)
        if previous is not None:
            previous.skip()
        if not self._animations_enabled:
            self._set_line_visuals(index, to_scale, to_opacity)
            return

        widget = self._buttons[index]

        def update(value):
            progress = float(value)
            scale = from_scale + (to_scale - from_scale) * progress
            opacity = from_opacity + (to_opacity - from_opacity) * progress
            self._set_line_visuals(index, scale, opacity)

        target = Adw.CallbackAnimationTarget.new(update)
        animation = Adw.TimedAnimation.new(self, 0.0, 1.0, 320, target)
        animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._animations[index] = animation
        animation.play()

    def _scroll_to_index(self, index, animated=True):
        if not 0 <= index < len(self._buttons):
            return GLib.SOURCE_REMOVE
        adjustment = self.get_vadjustment()
        allocation = self._buttons[index].get_allocation()
        target = max(0.0, allocation.y - adjustment.get_page_size() * 0.42)
        target = min(
            target, max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        )
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
            # The active line's font scale changes its allocation. Recalculate after
            # GTK has laid out the line, then animate the adjustment to it.
            GLib.idle_add(self._scroll_to_index, index, True)

    def _transition_to(self, index):
        previous = self._active_index
        old_upcoming = self._upcoming_index

        if 0 <= previous < len(self._buttons):
            self._buttons[previous].remove_css_class("lyrics-current")
            renderer = self._word_renderers.get(previous)
            if renderer is not None:
                renderer.set_active(False)
            self._animate_line(previous, 1.08, 1.0, 1.0, 0.58)

        if 0 <= old_upcoming < len(self._buttons) and old_upcoming not in (
            previous,
            index,
        ):
            self._animate_line(old_upcoming, 1.0, 1.0, 0.70, 0.58)

        self._active_index = index
        if 0 <= index < len(self._buttons):
            self._buttons[index].add_css_class("lyrics-current")
            renderer = self._word_renderers.get(index)
            if renderer is not None:
                renderer.set_active(True)
            self._animate_line(index, 1.0, 1.08, 0.58, 1.0)

        self._upcoming_index = index + 1 if index + 1 < len(self._buttons) else -1
        if self._upcoming_index >= 0:
            self._animate_line(self._upcoming_index, 1.0, 1.0, 0.58, 0.70)
        self._follow_index(index)

    def _on_scroll(self, _controller, _dx, _dy):
        if not self._programmatic_scroll:
            self._auto_follow = False
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
        for animation in self._animations.values():
            animation.skip()
        self._animations.clear()
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
        for index, line in enumerate(document.lines):
            if document.synchronized:
                if line.words:
                    # Word-synced lines use the same single line item as the
                    # normal synchronized view. Only its active-line text
                    # renderer differs, so wrapping and line transitions stay
                    # on the existing path.
                    button = Gtk.Button(has_frame=False, focusable=True)
                    button.add_css_class("lyrics-line")
                    renderer = WordSyncedLyricsRenderer(
                        line.text,
                        line.words,
                        button,
                        document.offset_ms,
                    )
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
                    self._set_line_visuals(index, 1.0, 0.58)
                    self._content.append(button)
                else:
                    button = Gtk.Button(has_frame=False, focusable=True)
                    text = line.text or ""
                    if text.strip():
                        content = Gtk.Label(
                            label=text, wrap=True, justify=Gtk.Justification.CENTER
                        )
                    else:
                        content = Gtk.Image.new_from_icon_name(
                            "audio-x-generic-symbolic"
                        )
                        content.set_pixel_size(self._music_icon_size)
                        content.add_css_class("lyrics-music-icon")
                    button.set_child(content)
                    button.add_css_class("lyrics-line")
                    button.set_tooltip_text("Seek to this lyric line")
                    button.connect(
                        "clicked",
                        lambda _button, line=line: self.emit(
                            "seek-requested",
                            (line.start_time_ms + document.offset_ms) / 1000.0,
                        ),
                    )
                    self._buttons.append(button)
                    self._set_line_visuals(index, 1.0, 0.58)
                    self._content.append(button)

            else:
                label = Gtk.Label(
                    label=line.text,
                    wrap=True,
                    justify=Gtk.Justification.CENTER,
                )
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
        return tuple(
            mode for mode in ("line", "word", "plain") if mode in self._documents
        )

    @property
    def selected_row(self):
        return self._document_rows.get(self._mode)

    @property
    def variant_rows(self):
        return tuple(
            row
            for row in self._document_rows.values()
            if row and row.get("id") is not None
        )
