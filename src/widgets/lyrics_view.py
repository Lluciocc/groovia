"""Native, keyboard-accessible lyrics timeline widget."""

from __future__ import annotations

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango


class LyricsView(Gtk.ScrolledWindow):
    __gsignals__ = {
        "seek-requested": (GObject.SignalFlags.RUN_LAST, None, (float,)),
        "manual-scroll": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self):
        super().__init__(vexpand=True, hexpand=True)
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._document = None
        self._buttons = []
        self._word_buttons = {}
        self._active_word = (-1, -1)
        self._active_index = -1
        self._upcoming_index = -1
        self._music_icon_size = 20
        self._auto_follow = True
        self._programmatic_scroll = False
        self._animations = {}
        self._word_animation = None
        self._scroll_animation = None
        self._animations_enabled = self._read_animation_preference()
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self._content.set_margin_top(36); self._content.set_margin_bottom(36)
        self._content.set_margin_start(24); self._content.set_margin_end(24)
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
            gtk_settings.connect("notify::gtk-enable-animations", self._on_animation_setting_changed)
        self._app_settings = None
        app_enabled = True
        try:
            schema_source = Gio.SettingsSchemaSource.get_default()
            schema = schema_source.lookup("io.github.Lluciocc.Groovia", True) if schema_source else None
            if schema is not None:
                self._app_settings = Gio.Settings.new_full(schema, None, None)
                app_enabled = self._app_settings.get_boolean("animations")
                self._app_settings.connect("changed::animations", self._on_animation_setting_changed)
        except (GLib.Error, TypeError):
            pass
        return system_enabled and app_enabled

    def _on_animation_setting_changed(self, *_args):
        gtk_settings = Gtk.Settings.get_default()
        system_enabled = True if gtk_settings is None else bool(
            gtk_settings.get_property("gtk-enable-animations")
        )
        app_enabled = True if self._app_settings is None else self._app_settings.get_boolean("animations")
        self._animations_enabled = system_enabled and app_enabled
        if not self._animations_enabled:
            for animation in self._animations.values():
                animation.skip()
            self._animations.clear()
            if self._word_animation:
                self._word_animation.skip()
                self._word_animation = None
            if self._scroll_animation:
                self._scroll_animation.skip()
                self._scroll_animation = None

    @staticmethod
    def _label_for(widget):
        return widget.get_child() if isinstance(widget, Gtk.Button) else widget

    def _set_scale(self, index, scale):
        if not 0 <= index < len(self._buttons):
            return

        if index in self._word_buttons:
            for word_button in self._word_buttons[index]:
                child = word_button.get_child()
                if isinstance(child, Gtk.Label):
                    attrs = Pango.AttrList()
                    attrs.insert(Pango.attr_scale_new(float(scale)))
                    child.set_attributes(attrs)
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
        target = max(0.0, allocation.y - adjustment.get_page_size() * .42)
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

        animation = Adw.TimedAnimation.new(self, 0.0, 1.0, 360, Adw.CallbackAnimationTarget.new(update))
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
            self._animate_line(previous, 1.08, 1.0, 1.0, .58)

        if 0 <= old_upcoming < len(self._buttons) and old_upcoming not in (previous, index):
            self._animate_line(old_upcoming, 1.0, 1.0, .70, .58)

        self._active_index = index
        if 0 <= index < len(self._buttons):
            self._buttons[index].add_css_class("lyrics-current")
            self._animate_line(index, 1.0, 1.08, .58, 1.0)

        self._upcoming_index = index + 1 if index + 1 < len(self._buttons) else -1
        if self._upcoming_index >= 0:
            self._animate_line(self._upcoming_index, 1.0, 1.0, .58, .70)
        self._follow_index(index)

    def _on_scroll(self, _controller, _dx, _dy):
        if not self._programmatic_scroll:
            self._auto_follow = False
            self.emit("manual-scroll")
        return False

    def set_document(self, document):
        self._document = document
        self._active_index = -1
        self._upcoming_index = -1
        self._auto_follow = True
        for animation in self._animations.values():
            animation.skip()
        self._animations.clear()
        if self._word_animation:
            self._word_animation.skip()
            self._word_animation = None
        if self._scroll_animation:
            self._scroll_animation.skip()
            self._scroll_animation = None
        for child in list(self._content):
            self._content.remove(child)
        self._buttons = []
        self._word_buttons = {}
        self._active_word = (-1, -1)
        if not document:
            return
        for index, line in enumerate(document.lines):
            if document.synchronized:
                if line.words:
                    # Word-synced lines use one native, keyboard-focusable
                    # button per word. The line container remains a single
                    # item for scrolling and line-level animation.
                    container = Gtk.FlowBox()
                    container.set_selection_mode(Gtk.SelectionMode.NONE)
                    container.set_halign(Gtk.Align.CENTER)
                    container.set_valign(Gtk.Align.CENTER)
                    container.set_row_spacing(2)
                    container.set_column_spacing(1)
                    container.add_css_class("lyrics-line")
                    container.add_css_class("lyrics-word-line")
                    words = []
                    for word_index, word in enumerate(line.words):
                        button = Gtk.Button(label=word.text, has_frame=False, focusable=True)
                        button.add_css_class("lyrics-word")
                        button.set_tooltip_text("Seek to this lyric word")
                        button.connect(
                            "clicked",
                            lambda _button, word=word: self.emit(
                                "seek-requested",
                                (word.start_time_ms + document.offset_ms) / 1000.0,
                            ),
                        )
                        words.append(button)
                        container.insert(button, -1)
                    self._word_buttons[index] = words
                    button = container
                    self._buttons.append(button)
                    self._set_line_visuals(index, 1.0, .58)
                    self._content.append(button)
                else:
                    button = Gtk.Button(has_frame=False, focusable=True)
                    text = line.text or ""
                    if text.strip():
                        content = Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.CENTER)
                    else:
                        content = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
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
                    self._set_line_visuals(index, 1.0, .58)
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

    def update_position(self, position_ms: int):
        if not self._document or not self._document.synchronized or not self._buttons:
            return
        index = self._document.current_index(position_ms)
        if index != self._active_index:
            self._transition_to(index)
        if self._document.word_synchronized:
            self._update_word(position_ms)

    def _update_word(self, position_ms: int):
        line_index = self._active_index
        words = self._word_buttons.get(line_index, [])
        if not words:
            return
        word_index = self._document.current_word_index(line_index, position_ms)
        previous_line, previous_word = self._active_word
        if (previous_line, previous_word) != (line_index, word_index):
            if 0 <= previous_line and previous_line in self._word_buttons and 0 <= previous_word < len(self._word_buttons[previous_line]):
                old = self._word_buttons[previous_line][previous_word]
                old.remove_css_class("lyrics-word-current")
                old.add_css_class("lyrics-word-previous")
            if 0 <= word_index < len(words):
                current = words[word_index]
                current.remove_css_class("lyrics-word-upcoming")
                current.add_css_class("lyrics-word-current")
                self._animate_word(current, 1.08, 1.107, .82, 1.0)
            self._active_word = (line_index, word_index)

        for index, button in enumerate(words):
            self._set_word_scale(button, 1.08)
            button.remove_css_class("lyrics-word-previous")
            button.remove_css_class("lyrics-word-upcoming")
            if index < word_index:
                button.add_css_class("lyrics-word-previous")
            elif index > word_index:
                button.add_css_class("lyrics-word-upcoming")

        if 0 <= word_index < len(words):
            word = self._document.lines[line_index].words[word_index]
            start = word.start_time_ms + self._document.offset_ms
            end = word.end_time_ms
            if end is None:
                end = self._document.lines[line_index].end_time_ms
            end = (end if end is not None else word.start_time_ms + 500) + self._document.offset_ms
            progress = 1.0 if end <= start else max(0.0, min(1.0, (position_ms - start) / (end - start)))
            button = words[word_index]
            button.set_opacity(.82 + .18 * progress)
            self._set_word_scale(button, 1.08 * (1.0 + .025 * progress))

    def _set_word_scale(self, button, scale):
        child = button.get_child()
        if isinstance(child, Gtk.Label):
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_scale_new(float(scale)))
            child.set_attributes(attrs)

    def _animate_word(self, button, from_scale, to_scale, from_opacity, to_opacity):
        if self._word_animation is not None:
            self._word_animation.skip()
            self._word_animation = None
        if not self._animations_enabled:
            self._set_word_scale(button, to_scale)
            button.set_opacity(to_opacity)
            return
        def update(value):
            progress = float(value)
            self._set_word_scale(button, from_scale + (to_scale - from_scale) * progress)
            button.set_opacity(from_opacity + (to_opacity - from_opacity) * progress)
        animation = Adw.TimedAnimation.new(
            self, 0.0, 1.0, 240, Adw.CallbackAnimationTarget.new(update)
        )
        animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._word_animation = animation
        animation.play()

    def return_to_current(self):
        self._auto_follow = True
        if 0 <= self._active_index < len(self._buttons):
            self._scroll_to_index(self._active_index, animated=True)

    @property
    def synchronized(self):
        return bool(self._document and self._document.synchronized)
