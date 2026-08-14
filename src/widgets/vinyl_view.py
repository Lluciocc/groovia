# vinyl_view.py
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

import math
import time

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GObject, Gtk

from ..visuals import load_scaled_pixbuf


class VinylView(Gtk.DrawingArea):
    """A frame-synchronised record deck with a rotating centre label and smooth arm motion."""

    __gtype_name__ = "GrooviaVinylView"
    __gsignals__ = {
        "seek-requested": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "toggle-play": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_content_width(540)
        self.set_content_height(540)
        self.set_draw_func(self._draw)
        self.cover = None
        self._previous_cover = None
        self._cover_transition = 1.0
        self.angle = 0.0
        self._rotation_angle = 0.0
        self.progress = 0.0
        self.duration = 0.0
        self.arm_progress = 0.0
        self.is_playing = False
        self.rotation_velocity = 0.0
        self.accent = (0.80, 0.30, 0.22)
        self._last_frame = time.monotonic()
        self._drag_active = False
        self._drag_start_x = 0.0
        self._drag_start_y = 0.0
        self._drag_previous_angle = 0.0
        self._drag_total_angle = 0.0
        self._drag_start_position = 0.0
        self.set_focusable(True)
        self.add_tick_callback(self._tick)

        drag = Gtk.GestureDrag.new()
        drag.set_button(1)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        click = Gtk.GestureClick.new()
        click.set_button(1)
        click.connect("pressed", self._on_click)
        self.add_controller(click)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    def set_cover(self, path):
        previous = self.cover
        cover = None
        if path:
            try:
                # Load once at a bounded size; never paint a full-resolution cover per frame.
                cover = load_scaled_pixbuf(path, 512, 512)
            except Exception:
                pass
        self._previous_cover = previous if previous is not None and cover is not None else None
        self.cover = cover
        self._cover_transition = 0.0 if self._previous_cover is not None else 1.0
        self.queue_draw()

    def set_playing(self, playing):
        self.is_playing = playing

    def set_duration(self, duration):
        self.duration = max(0.0, float(duration))

    def set_progress(self, progress):
        self.progress = max(0.0, min(1.0, progress))
        self.queue_draw()

    def set_accent(self, color):
        self.accent = tuple(color)
        self.queue_draw()

    def _record_geometry(self):
        width, height = self.get_width(), self.get_height()
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 30
        return cx, cy, radius

    def _inside_record(self, x, y):
        cx, cy, radius = self._record_geometry()
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius**2

    @staticmethod
    def _angle(x, y, cx, cy):
        return math.atan2(y - cy, x - cx)

    @staticmethod
    def _angle_delta(current, previous):
        return (current - previous + math.pi) % math.tau - math.pi

    def _on_drag_begin(self, _gesture, x, y):
        self._drag_active = self._inside_record(x, y)
        if not self._drag_active:
            return
        cx, cy, _ = self._record_geometry()
        self._drag_start_x, self._drag_start_y = x, y
        self._drag_previous_angle = self._angle(x, y, cx, cy)
        self._drag_total_angle = 0.0
        self._drag_start_position = self.progress * self.duration

    def _on_drag_update(self, _gesture, offset_x, offset_y):
        if not self._drag_active:
            return
        x = self._drag_start_x + offset_x
        y = self._drag_start_y + offset_y
        cx, cy, _ = self._record_geometry()
        current_angle = self._angle(x, y, cx, cy)
        delta = self._angle_delta(current_angle, self._drag_previous_angle)
        self._drag_previous_angle = current_angle
        self._drag_total_angle += delta
        self._rotation_angle += delta
        self.angle = self._rotation_angle

        if self.duration > 0:
            # One full manual turn scrubs 45 seconds, in either direction.
            seconds = self._drag_start_position + self._drag_total_angle / math.tau * 45.0
            seconds = max(0.0, min(self.duration, seconds))
            self.progress = seconds / self.duration
            self.emit("seek-requested", seconds)
        self.queue_draw()

    def _on_drag_end(self, _gesture, _offset_x, _offset_y):
        self._drag_active = False

    def _on_click(self, _gesture, n_press, x, y):
        if n_press == 2 and self._inside_record(x, y):
            self.emit("toggle-play")

    def _on_scroll(self, _controller, _dx, dy):
        if self.duration <= 0:
            return False
        # A scroll notch moves the needle by five seconds.
        seconds = self.progress * self.duration - dy * 5.0
        self.emit("seek-requested", max(0.0, min(self.duration, seconds)))
        return True

    def _tick(self, _widget, _clock):
        now = time.monotonic()
        delta = min(0.1, now - self._last_frame)
        self._last_frame = now
        target_velocity = 1.18 if self.is_playing else 0.0
        self.rotation_velocity += (target_velocity - self.rotation_velocity) * min(1.0, delta * 5.5)
        self.arm_progress += (self.progress - self.arm_progress) * min(1.0, delta * 4.5)
        self._rotation_angle += delta * self.rotation_velocity
        self.angle = self._rotation_angle
        if self._cover_transition < 1.0:
            self._cover_transition = min(1.0, self._cover_transition + delta / 0.9)
            if self._cover_transition >= 1.0:
                self._previous_cover = None
        if (
            self.rotation_velocity > 0.002
            or abs(self.arm_progress - self.progress) > 0.001
            or self._cover_transition < 1.0
        ):
            self.queue_draw()
        return True

    def _draw(self, _area, cr, width, height):
        size = min(width, height) - 30
        cx, cy, radius = width / 2, height / 2, size / 2

        cr.set_source_rgba(0.0, 0.0, 0.0, 0.30)
        cr.arc(cx + 5, cy + 13, radius, 0, math.tau)
        cr.fill()
        cr.set_source_rgb(0.018, 0.020, 0.027)
        cr.arc(cx, cy, radius, 0, math.tau)
        cr.fill()

        # Grooves, centre label and artwork rotate as one record surface.
        cr.save()
        cr.translate(cx, cy)
        cr.rotate(self.angle)
        for ring in range(14, int(radius) - 4, 8):
            alpha = 0.035 + (ring % 16) / 520
            cr.set_source_rgba(0.74, 0.78, 0.86, alpha)
            cr.set_line_width(1)
            cr.arc(0, 0, ring, 0, math.tau)
            cr.stroke()
        label = radius * 0.43
        cr.set_source_rgb(*self.accent)
        cr.arc(0, 0, label, 0, math.tau)
        cr.fill()
        if self._previous_cover:
            self._paint_cover(cr, label, self._previous_cover, 1.0 - self._cover_transition)
        if self.cover:
            self._paint_cover(cr, label, self.cover, self._cover_transition)
        cr.set_source_rgb(0.055, 0.055, 0.065)
        cr.arc(0, 0, 7, 0, math.tau)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.65)
        cr.arc(-2, -2, 2, 0, math.tau)
        cr.fill()
        cr.restore()

        # Soft moving reflections that make the vinyl rotation visible
        # without looking like a bright painted line.
        cr.save()
        cr.translate(cx, cy)
        cr.rotate(self._rotation_angle * 0.72)

        # Main broad reflection
        cr.set_line_cap(1)
        cr.set_line_width(radius * 0.055)
        cr.set_source_rgba(0.92, 0.95, 1.0, 0.035)
        cr.arc(
            -radius * 0.12,
            -radius * 0.10,
            radius * 0.72,
            math.pi * 1.08,
            math.pi * 1.43,
        )
        cr.stroke()

        # Softer outer glow
        cr.set_line_width(radius * 0.095)
        cr.set_source_rgba(0.86, 0.91, 1.0, 0.018)
        cr.arc(
            -radius * 0.14,
            -radius * 0.12,
            radius * 0.76,
            math.pi * 1.06,
            math.pi * 1.45,
        )
        cr.stroke()

        # Thin highlight inside the reflection
        cr.set_line_width(radius * 0.012)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.075)
        cr.arc(
            -radius * 0.10,
            -radius * 0.08,
            radius * 0.68,
            math.pi * 1.11,
            math.pi * 1.37,
        )
        cr.stroke()

        # Smaller reflection on the opposite side
        cr.set_line_width(radius * 0.030)
        cr.set_source_rgba(0.90, 0.93, 1.0, 0.026)
        cr.arc(
            radius * 0.08,
            radius * 0.12,
            radius * 0.62,
            math.pi * 0.08,
            math.pi * 0.27,
        )
        cr.stroke()

        cr.restore()

        self._draw_tonearm(cr, cx, cy, radius)

    def _paint_cover(self, cr, label, cover, opacity=1.0):
        try:
            diameter = (label - 3) * 2
            cr.save()
            cr.arc(0, 0, label - 3, 0, math.tau)
            cr.clip()
            # Draw the already bounded Pixbuf into the exact label diameter.
            cr.translate(-diameter / 2, -diameter / 2)
            cr.scale(diameter / cover.get_width(), diameter / cover.get_height())
            Gdk.cairo_set_source_pixbuf(cr, cover, 0, 0)
            cr.paint_with_alpha(opacity)
            cr.restore()
        except Exception:
            pass

    def _draw_tonearm(self, cr, cx, cy, radius):
        arm_angle = -0.95 + self.arm_progress * 0.44
        arm_length = radius * 1.12
        ax, ay = (
            cx + math.cos(arm_angle) * radius * 0.92,
            cy + math.sin(arm_angle) * radius * 0.92,
        )
        ex, ey = (
            cx + math.cos(arm_angle) * arm_length,
            cy + math.sin(arm_angle) * arm_length,
        )
        cr.set_line_cap(1)
        cr.set_line_width(8)
        cr.set_source_rgba(0.04, 0.045, 0.06, 0.42)
        cr.move_to(ax + 3, ay + 4)
        cr.line_to(ex + 3, ey + 4)
        cr.stroke()
        cr.set_line_width(5)
        cr.set_source_rgb(0.76, 0.73, 0.70)
        cr.move_to(ax, ay)
        cr.line_to(ex, ey)
        cr.stroke()
        cr.set_source_rgb(0.34, 0.31, 0.29)
        cr.arc(ex, ey, 10, 0, math.tau)
        cr.fill()
        cr.set_source_rgb(0.86, 0.84, 0.80)
        cr.arc(ax, ay, 11, 0, math.tau)
        cr.fill()
