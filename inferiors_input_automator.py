#!/usr/bin/env python3
"""
InFeRiOr's Input Automator v3.6.0 -- Linux port of the AutoHotkey v2 original
(v3.5.1); the Linux-only changes since are listed under the deviations below.

15 General + 15 Repeat (First) + 15 Repeat (Second) + 15 Repeat (Third)
timers across four scrollable tabs. All settings adjustable in real time
without restarting.

The Repeat tabs were called "Position Timers" in the AHK original. They are
named for the fixed interval they fire on rather than for a click position,
because the position is now optional: a row left at the 0,0 default clicks
wherever the pointer already is.

Layout constants below are copied verbatim from the AHK script so every timer
control sits at its original coordinate. Two things differ:

  * Width. The four tab labels render 432px wide at DejaVu Sans 9, so the tab
    control is 432px and the window 452px, instead of 365/385. The tab strip
    therefore ends flush with the notebook's right edge. Every control inside
    the tabs keeps its original x/y.
  * The bottom block (status line, START/RESET, footers) spans the full 432px
    content width rather than the original 365px, so it stays balanced under
    the wider tab strip. Y coordinates and heights are unchanged.

Deliberate deviations from the AHK original (all agreed up front):
  1. Modifier combos are held for the WHOLE hold duration. AHK's
     Send("^{v down}") auto-releases Ctrl immediately, so the modifier was not
     actually down during the hold. See send_key_press().
  2. General-tab clicks re-read the cursor position per click instead of
     pinning to the position captured at the start of the 50ms tick, so the
     clicker follows your mouse. See run_general_timers().
  3. The "Get Pos" click is swallowed rather than passed through, so picking a
     spot cannot land a stray click in the app underneath.
  4. Scrolling clips partially-visible rows instead of hiding them outright,
     and there is a scrollbar. The original is wheel-only with pop-in.
  5. A position of 0,0 means "click at the current cursor" instead of "skip
     this timer". It resolves to the pointer as it was before that tab moved
     it, so 0,0 means the user's real mouse on every tab rather than whatever
     an earlier row warped to. See run_repeat_timer_set().
  6. The "Get Pos" button becomes "Clear" once a position is captured, so a
     row can be returned to cursor-following without the global RESET.
  7. General timers take a position too, so the two kinds of tab differ only
     in WHEN they fire: General loops back-to-back, a Repeat tab fires on its
     interval. That forces different cursor-return schedules -- per click on
     General, per set on a Repeat tab (see run_general_timers()) -- but 0,0
     still resolves the same way on both, per deviation 5.
  8. Quoted text is typed one paced character at a time, with Hold as the
     per-character time, instead of all at once; a text row at Hold 0 types
     nothing. Caps Lock is compensated so text types as written. See
     type_text().
Everything else -- timings, spacing, run semantics, Hold=0 behaviour, interval
measurement -- matches the original exactly.
"""

import atexit
import signal
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

import input_backend as ib

# ==================== CONFIGURATION ====================
# Ported 1:1 from the AHK script.

TIMERS = {
    "numBasic": 15,
    "numRepeat1": 15,
    "numRepeat2": 15,
    "numRepeat3": 15,
    "loopInterval": 50,   # ms between main loop ticks
}
TOTAL_TIMERS = (TIMERS["numBasic"] + TIMERS["numRepeat1"]
                + TIMERS["numRepeat2"] + TIMERS["numRepeat3"])
NUM_TABS = 4

# Window width widened 385 -> 452 purely so the tab strip fits; height and all
# inner timer coordinates are unchanged.
#
# 452 is measured, not estimated: probing Notebook.index("@x,y") across an
# over-wide notebook puts the four rendered tabs at x 0..431 at DejaVu Sans 9
# (102 + 110 + 110 + 110), so the notebook is exactly 432px and the window
# 10 + 432 + 10. Sizing the notebook to the tab strip is what makes the last
# tab end flush with the window's right margin instead of leaving a gap.
WINDOW = {"width": 452, "height": 707, "marginX": 10}
# Full-width band used by the header, status line, buttons and footers.
WINDOW["contentWidth"] = WINDOW["width"] - 2 * WINDOW["marginX"]   # 432

HEADER = {"y": 10, "height": 30}

TAB_LAYOUT = {
    "x": 10,
    "y": 42,
    "width": 432,        # was 365 in AHK; widened to fit the four tab labels
                         # exactly, so the strip ends flush on the right
    "height": 500,
    "contentTop": 72,
    "contentBottom": 532,
    "scrollStep": 40,    # pixels per scroll wheel notch
    "clipPadding": 5,
    "contentInset": 5,
}
TAB_VISIBLE_HEIGHT = TAB_LAYOUT["contentBottom"] - TAB_LAYOUT["contentTop"]

LAYOUT = {
    "sectionHeaderH": 20,
    "sectionHeaderX": 20,
    "sectionHeaderW": 345,
    "editWidth": 95,
    "labelWidth": 160,
    "contentLeft": 30,
    "editLeft": 195,
}

# afterWait is the gap between two rows of one timer; afterPos is the larger
# gap to the next timer's header. Both tabs now carry a position row, so both
# spacing tables need an afterPos.
GEN_SPACING = {"afterHeader": 28, "afterKey": 28, "afterClick": 35,
               "afterWait": 35, "afterPos": 49}
REPEAT_SPACING = {"afterHeader": 23, "afterKey": 23, "afterClick": 28,
                  "afterWait": 28, "afterPos": 32}

INTERVAL_LAYOUT = {"labelW": 190, "editW": 85, "after": 35}

POS_BTN = {"x": 298, "w": 60, "h": 24, "yOffset": -2,
           "getText": "Get Pos", "clearText": "Clear"}
MODE_DDL = {"x": 195, "w": 72, "yOffset": -2}

MOUSE_BTN = {
    "label": "Mouse Button:",
    "keyLabel": "Key Name:",
    "options": ["Left", "Right", "Middle"],
}

# The two buttons are re-proportioned to span the full content width while
# keeping the original 5px gap between them (AHK: 178 + 5 + 182 across 365).
# All Y coordinates and heights are unchanged from the original.
_BTN_GAP = 5
_BTN_LEFT_W = (WINDOW["contentWidth"] - _BTN_GAP) // 2

BOTTOM_UI = {
    "statusY": 547, "statusH": 25,
    "btnY": 575, "btnH": 40,
    "btnLeftW": _BTN_LEFT_W,
    "btnRightX": WINDOW["marginX"] + _BTN_LEFT_W + _BTN_GAP,
    "btnRightW": WINDOW["contentWidth"] - _BTN_LEFT_W - _BTN_GAP,
    "footerLine1Y": 625, "footerLine1bY": 647, "footerLine2Y": 669,
}

# AHK's "s9" / "s10 bold" / "s12 bold" on DejaVu Sans, which measures slightly
# narrower than Segoe UI at this display's 92 DPI, so every original box width
# still fits its text.
FONT_FAMILY = "DejaVu Sans"
FONTS = {"default": 9, "status": 10, "btn": 12}

CLICK_TIMING = {
    "preDelay": 20,        # ms before mouse down
    "postDelay": 20,       # ms after a position click before the next
    "cursorReturn1": 20,   # ms after the warp on return
    "cursorReturn2": 20,   # ms after the second warp on return
}

TOOLTIP_TIMEOUT = 2000
INVALID_KEY_TOOLTIP_TIMEOUT = 3000

DEFAULTS = {"seconds": "0", "clickMs": "0", "waitMs": "0",
            "position": "0,0", "key": ""}

# AHK colour names used in the original.
COLORS = {"blue": "#0000FF", "red": "#FF0000", "green": "#008000",
          "black": "#000000"}

SCROLLBAR_W = 12


def now_ms():
    return time.monotonic() * 1000.0


# ==================== PER-TIMER STATE ====================
# Mirrors the AHK timerObj. The cached_* fields exist for the same reason they
# do in the original: the runtime hot path must never touch a widget. Here it
# matters even more, because the engine runs on a worker thread and Tk widgets
# may only be touched from the main thread.

class Timer:
    def __init__(self):
        self.click_edit = None
        self.wait_edit = None
        self.mode_ddl = None
        self.button_ddl = None
        self.key_edit = None
        self.shared_label = None
        self.pos_edit = None
        self.pos_label = None
        self.pos_button = None

        self.current_mode = "Click"
        self.cached_click_ms = 0
        self.cached_wait_ms = 0
        self.cached_key = ""
        self.cached_key_valid = False
        self.cached_click_button = "Left"
        self.cached_position = "0,0"

        # tk variables backing the widgets
        self.click_var = None
        self.wait_var = None
        self.key_var = None
        self.mode_var = None
        self.button_var = None
        self.pos_var = None

    def refresh_cache(self, *_):
        """Port of RefreshTimerCache."""
        click_val = self.click_var.get() if self.click_var else ""
        wait_val = self.wait_var.get() if self.wait_var else ""
        self.cached_click_ms = 0 if click_val == "" else int(click_val)
        self.cached_wait_ms = 0 if wait_val == "" else int(wait_val)
        self.cached_key = self.key_var.get().strip() if self.key_var else ""
        self.cached_key_valid = ib.is_valid_key(self.cached_key)
        if self.button_var is not None:
            self.cached_click_button = self.button_var.get()
        if self.pos_var is not None:
            self.cached_position = self.pos_var.get()


class RepeatSet:
    """Port of CreatePositionSet. Named "repeat" here because what defines
    these sets is the fixed interval they fire on, not the click position."""

    def __init__(self):
        self.timers = []
        self.seconds_var = None
        self.cached_interval_ms = 0


# ==================== SCROLLABLE TAB ====================
# Replaces the AHK scroll machinery (WH_MOUSE_LL hook, RepositionControls,
# per-control Visible toggling). A Canvas gives real clipping for free, so the
# hide-if-not-fully-visible logic is unnecessary here.

class ScrollTab:
    def __init__(self, parent, x, y, width, height):
        self.offset = 0
        self.content_height = 0
        self.view_height = height

        self.canvas = tk.Canvas(parent, width=width, height=height,
                                highlightthickness=0, bd=0,
                                background=parent.cget("background"))
        self.canvas.place(x=x, y=y, width=width, height=height)

        self.inner = tk.Frame(self.canvas, background=parent.cget("background"))
        self.window_id = self.canvas.create_window(0, 0, window=self.inner,
                                                   anchor="nw", width=width)

        self.scrollbar = ttk.Scrollbar(parent, orient="vertical",
                                       command=self._on_scrollbar)
        self.scrollbar.place(x=x + width + 2, y=y,
                             width=SCROLLBAR_W, height=height)

        self.canvas.bind("<Button-4>", lambda ev: self.scroll_by(-1))
        self.canvas.bind("<Button-5>", lambda ev: self.scroll_by(1))

    def bind_wheel_recursive(self, widget):
        """The AHK version uses a low-level mouse hook so the wheel scrolls the
        tab even when the cursor sits over an edit box. Binding every child
        achieves the same thing without touching the X input stream."""
        widget.bind("<Button-4>", lambda ev: self.scroll_by(-1), add="+")
        widget.bind("<Button-5>", lambda ev: self.scroll_by(1), add="+")
        for child in widget.winfo_children():
            self.bind_wheel_recursive(child)

    def set_content_height(self, height):
        self.content_height = height
        self.inner.configure(height=height)
        self.canvas.itemconfigure(self.window_id, height=height)
        self._apply()

    def max_scroll(self):
        return max(0, self.content_height - self.view_height)

    def scroll_by(self, notches):
        """One notch == TabLayout.scrollStep (40px), as in the original."""
        self.offset = max(0, min(self.max_scroll(),
                                 self.offset + notches * TAB_LAYOUT["scrollStep"]))
        self._apply()
        return "break"

    def _on_scrollbar(self, action, value, units=None):
        if action == "moveto":
            self.offset = int(float(value) * max(1, self.content_height))
        elif action == "scroll":
            step = TAB_LAYOUT["scrollStep"] if units == "units" else self.view_height
            self.offset += int(value) * step
        self.offset = max(0, min(self.max_scroll(), self.offset))
        self._apply()

    def reset(self):
        self.offset = 0
        self._apply()

    def _apply(self):
        self.canvas.coords(self.window_id, 0, -self.offset)
        total = max(1, self.content_height)
        self.scrollbar.set(self.offset / total,
                           (self.offset + self.view_height) / total)

    def lift(self):
        # tk.Canvas.lift is an alias for tag_raise (canvas ITEMS), so the
        # window-stacking version has to be called through Misc explicitly.
        tk.Misc.lift(self.canvas)
        tk.Misc.lift(self.scrollbar)

    def lower(self):
        tk.Misc.lower(self.canvas)
        tk.Misc.lower(self.scrollbar)


# ==================== TOOLTIP ====================
# Port of AHK's ToolTip(): a small borderless window near the cursor that
# disappears after a timeout.

class Tooltip:
    def __init__(self, root):
        self.root = root
        self.window = None
        self.after_id = None

    def show(self, text, timeout=TOOLTIP_TIMEOUT):
        self.hide()
        self.window = tk.Toplevel(self.root)
        self.window.wm_overrideredirect(True)
        self.window.attributes("-topmost", True)
        label = tk.Label(self.window, text=text, justify="left",
                         background="#FFFFE1", foreground="#000000",
                         relief="solid", borderwidth=1,
                         font=(FONT_FAMILY, FONTS["default"]),
                         padx=4, pady=2)
        label.pack()
        x = self.root.winfo_pointerx() + 12
        y = self.root.winfo_pointery() + 20
        self.window.wm_geometry("+%d+%d" % (x, y))
        self.after_id = self.root.after(timeout, self.hide)

    def hide(self):
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None


# ==================== APPLICATION ====================

class InputAutomator:
    def __init__(self, root, backend):
        self.root = root
        self.backend = backend

        # Runtime state (port of AppState)
        self.is_running = False
        self.stop_event = threading.Event()
        self.stop_event.set()          # set == not running
        self.worker = None
        self.last_repeat_run = [0.0, 0.0, 0.0]

        self.general_timers = []
        self.repeat_sets = [RepeatSet(), RepeatSet(), RepeatSet()]
        self.scroll_tabs = []

        self.tooltip = Tooltip(root)
        self.picker = None
        self.picker_target = None

        self._build_fonts()
        self._build_gui()

        self.hotkeys = ib.HotkeyListener(
            on_f6=lambda: self.root.after(0, self.toggle_script),
            on_f7=lambda: self.root.after(0, self.exit_app),
        )
        self.hotkeys.start()

    # ---------- fonts ----------

    def _build_fonts(self):
        self.font_default = tkfont.Font(family=FONT_FAMILY, size=FONTS["default"])
        self.font_bold = tkfont.Font(family=FONT_FAMILY, size=FONTS["default"],
                                     weight="bold")
        self.font_status = tkfont.Font(family=FONT_FAMILY, size=FONTS["status"],
                                       weight="bold")
        self.font_btn = tkfont.Font(family=FONT_FAMILY, size=FONTS["btn"],
                                    weight="bold")

    # ---------- widget helpers ----------

    def _label(self, parent, x, y, w, text, color=COLORS["black"],
               bold=False, anchor="w"):
        lbl = tk.Label(parent, text=text, foreground=color, anchor=anchor,
                       font=self.font_bold if bold else self.font_default,
                       background=parent.cget("background"))
        lbl.place(x=x, y=y, width=w)
        return lbl

    def _number_entry(self, parent, x, y, w, var):
        vcmd = (self.root.register(lambda p: p == "" or p.isdigit()), "%P")
        ent = tk.Entry(parent, textvariable=var, font=self.font_default,
                       validate="key", validatecommand=vcmd,
                       relief="sunken", borderwidth=1)
        ent.place(x=x, y=y, width=w, height=21)
        return ent

    def _text_entry(self, parent, x, y, w, var, readonly=False):
        ent = tk.Entry(parent, textvariable=var, font=self.font_default,
                       relief="sunken", borderwidth=1)
        if readonly:
            ent.configure(state="readonly")
        ent.place(x=x, y=y, width=w, height=21)
        return ent

    def _combo(self, parent, x, y, w, values, var):
        cb = ttk.Combobox(parent, values=values, textvariable=var,
                          state="readonly", font=self.font_default)
        cb.place(x=x, y=y, width=w, height=21)
        return cb

    # ---------- GUI construction ----------

    def _build_gui(self):
        root = self.root
        root.title("InFeRiOr's Input Automator v3.6.0")
        root.geometry("%dx%d" % (WINDOW["width"], WINDOW["height"]))
        root.resizable(False, False)
        root.attributes("-topmost", True)          # AHK's +AlwaysOnTop

        # Instructions at top
        self._label(root, WINDOW["marginX"], HEADER["y"], WINDOW["contentWidth"],
                    "Configure up to %d click and/or keypress cycles." % TOTAL_TIMERS,
                    anchor="center")

        # Tab control. Pages are left empty: exactly as in AHK, the scrolling
        # content lives on the window itself and is shown/hidden per tab.
        style = ttk.Style()
        style.configure("TNotebook.Tab", font=(FONT_FAMILY, FONTS["default"]))
        self.notebook = ttk.Notebook(root)
        self.notebook.place(x=TAB_LAYOUT["x"], y=TAB_LAYOUT["y"],
                            width=TAB_LAYOUT["width"], height=TAB_LAYOUT["height"])
        for title in ("General Timers", "Repeat Timers 1",
                      "Repeat Timers 2", "Repeat Timers 3"):
            self.notebook.add(tk.Frame(self.notebook), text=title)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Scroll viewports, placed at the original clip band.
        canvas_x = TAB_LAYOUT["x"] + 2
        canvas_y = TAB_LAYOUT["contentTop"] + TAB_LAYOUT["clipPadding"]
        canvas_h = (TAB_LAYOUT["contentBottom"] - TAB_LAYOUT["clipPadding"]) - canvas_y
        canvas_w = (TAB_LAYOUT["x"] + TAB_LAYOUT["width"] - 4) - canvas_x - SCROLLBAR_W - 2
        self.canvas_origin = (canvas_x, canvas_y)

        for _ in range(NUM_TABS):
            self.scroll_tabs.append(
                ScrollTab(root, canvas_x, canvas_y, canvas_w, canvas_h))

        # --- Tab 1: General timers ---
        content_y = (TAB_LAYOUT["contentTop"] + TAB_LAYOUT["clipPadding"]
                     + TAB_LAYOUT["contentInset"])
        end_y = self._create_timer_rows(0, content_y, TIMERS["numBasic"],
                                        "General", GEN_SPACING,
                                        self.general_timers)
        self.scroll_tabs[0].set_content_height(end_y - TAB_LAYOUT["contentTop"])

        # --- Tabs 2-4: Repeat timers ---
        for set_index in range(3):
            self._build_repeat_tab(set_index + 1, set_index,
                                   TIMERS["numRepeat%d" % (set_index + 1)])

        for st in self.scroll_tabs:
            st.bind_wheel_recursive(st.inner)

        # --- Bottom controls (outside the tabs) ---
        self.status_label = tk.Label(
            root, text="Status: Stopped", font=self.font_status,
            foreground=COLORS["black"], anchor="center",
            background=root.cget("background"))
        self.status_label.place(x=WINDOW["marginX"], y=BOTTOM_UI["statusY"],
                                width=WINDOW["contentWidth"],
                                height=BOTTOM_UI["statusH"])

        self.start_stop_btn = tk.Button(root, text="START (F6)",
                                        font=self.font_btn,
                                        command=self.toggle_script)
        self.start_stop_btn.place(x=WINDOW["marginX"], y=BOTTOM_UI["btnY"],
                                  width=BOTTOM_UI["btnLeftW"],
                                  height=BOTTOM_UI["btnH"])

        self.reset_btn = tk.Button(root, text="RESET", font=self.font_btn,
                                   command=self.reset_all)
        self.reset_btn.place(x=BOTTOM_UI["btnRightX"], y=BOTTOM_UI["btnY"],
                             width=BOTTOM_UI["btnRightW"], height=BOTTOM_UI["btnH"])

        self._label(root, WINDOW["marginX"], BOTTOM_UI["footerLine1Y"],
                    WINDOW["contentWidth"],
                    "Hotkeys: F6 = Start/Stop | F7 = Exit", anchor="center")
        self._label(root, WINDOW["marginX"], BOTTOM_UI["footerLine1bY"],
                    WINDOW["contentWidth"],
                    'Key Name: + = combo | "text" = type verbatim', anchor="center")
        self._label(root, WINDOW["marginX"], BOTTOM_UI["footerLine2Y"],
                    WINDOW["contentWidth"],
                    "Each Repeat tab has its own interval. "
                    "Position 0,0 = click at cursor.",
                    color=COLORS["red"], anchor="center")

        self._on_tab_changed()

    def _build_repeat_tab(self, tab_index, set_index, num_timers):
        """Port of BuildPositionTab."""
        repeat_set = self.repeat_sets[set_index]
        parent = self.scroll_tabs[tab_index].inner
        _, canvas_y = self.canvas_origin
        canvas_x = self.canvas_origin[0]

        content_y = (TAB_LAYOUT["contentTop"] + TAB_LAYOUT["clipPadding"]
                     + TAB_LAYOUT["contentInset"])

        # In-tab interval control (port of AddIntervalControl)
        self._label(parent, LAYOUT["sectionHeaderX"] - canvas_x,
                    content_y - canvas_y, INTERVAL_LAYOUT["labelW"],
                    "Execution Interval (ms):", color=COLORS["red"], bold=True)
        repeat_set.seconds_var = tk.StringVar(value=DEFAULTS["seconds"])
        self._number_entry(parent, LAYOUT["editLeft"] - canvas_x,
                           content_y - 3 - canvas_y, LAYOUT["editWidth"],
                           repeat_set.seconds_var)
        repeat_set.cached_interval_ms = int(DEFAULTS["seconds"])

        def on_interval_change(*_a, _s=repeat_set):
            val = _s.seconds_var.get()
            _s.cached_interval_ms = 0 if val == "" else int(val)

        repeat_set.seconds_var.trace_add("write", on_interval_change)
        content_y += INTERVAL_LAYOUT["after"]

        end_y = self._create_timer_rows(tab_index, content_y, num_timers,
                                        "Repeat", REPEAT_SPACING,
                                        repeat_set.timers)
        self.scroll_tabs[tab_index].set_content_height(
            end_y - TAB_LAYOUT["contentTop"])

    def _create_timer_rows(self, tab_index, start_y, num_timers, timer_type,
                           spacing, timer_array):
        """Port of CreateTimerRows."""
        parent = self.scroll_tabs[tab_index].inner
        cx, cy = self.canvas_origin
        content_y = start_y

        for i in range(1, num_timers + 1):
            t = Timer()

            # Section header
            self._label(parent, LAYOUT["sectionHeaderX"] - cx, content_y - cy,
                        MODE_DDL["x"] - LAYOUT["sectionHeaderX"] - 5,
                        "═══ Timer %d (%s) ═══" % (i, timer_type),
                        color=COLORS["blue"])

            key_row_y = content_y + spacing["afterHeader"]
            self._add_mode_and_key_controls(parent, content_y, key_row_y, t)
            content_y += spacing["afterHeader"] + spacing["afterKey"]

            # Hold (ms)
            self._label(parent, LAYOUT["contentLeft"] - cx, content_y - cy,
                        LAYOUT["labelWidth"], "Hold (ms):")
            t.click_var = tk.StringVar(value=DEFAULTS["clickMs"])
            t.click_edit = self._number_entry(parent, LAYOUT["editLeft"] - cx,
                                              content_y - cy, LAYOUT["editWidth"],
                                              t.click_var)
            t.click_var.trace_add("write", t.refresh_cache)
            content_y += spacing["afterClick"]

            # Wait (ms)
            self._label(parent, LAYOUT["contentLeft"] - cx, content_y - cy,
                        LAYOUT["labelWidth"], "Wait (ms):")
            t.wait_var = tk.StringVar(value=DEFAULTS["waitMs"])
            t.wait_edit = self._number_entry(parent, LAYOUT["editLeft"] - cx,
                                             content_y - cy, LAYOUT["editWidth"],
                                             t.wait_var)
            t.wait_var.trace_add("write", t.refresh_cache)
            content_y += spacing["afterWait"]

            # Position row. Present on every tab: a General timer with a
            # position clicks there and puts the pointer straight back.
            content_y = self._add_position_row(parent, content_y, t, spacing)

            t.refresh_cache()
            timer_array.append(t)

        return content_y

    def _add_mode_and_key_controls(self, parent, header_y, key_row_y, t):
        """Port of AddModeAndKeyControls."""
        cx, cy = self.canvas_origin

        t.mode_var = tk.StringVar(value="Click")
        t.mode_ddl = self._combo(parent, MODE_DDL["x"] - cx,
                                 header_y + MODE_DDL["yOffset"] - cy,
                                 MODE_DDL["w"], ["Click", "KeyPress"], t.mode_var)
        t.mode_ddl.bind("<<ComboboxSelected>>",
                        lambda ev, _t=t: self._on_mode_change(_t))

        # Shared label: always visible, text flips with the mode.
        t.shared_label = self._label(parent, LAYOUT["contentLeft"] - cx,
                                     key_row_y - cy, LAYOUT["labelWidth"],
                                     MOUSE_BTN["label"])

        # The Mouse Button dropdown and the Key Name edit share one slot;
        # exactly one is visible at a time, driven by the mode.
        t.button_var = tk.StringVar(value="Left")
        t.button_ddl = self._combo(parent, LAYOUT["editLeft"] - cx,
                                   key_row_y - cy, LAYOUT["editWidth"],
                                   MOUSE_BTN["options"], t.button_var)
        t.button_var.trace_add("write", t.refresh_cache)

        t.key_var = tk.StringVar(value=DEFAULTS["key"])
        t.key_edit = self._text_entry(parent, LAYOUT["editLeft"] - cx,
                                      key_row_y - cy, LAYOUT["editWidth"],
                                      t.key_var)
        t.key_var.trace_add("write", t.refresh_cache)
        t.key_edit.place_forget()          # Click is the default mode

        t._slot_place = {"x": LAYOUT["editLeft"] - cx, "y": key_row_y - cy,
                         "width": LAYOUT["editWidth"], "height": 21}

    def _add_position_row(self, parent, content_y, t, spacing):
        """Port of AddPositionRow."""
        cx, cy = self.canvas_origin

        t.pos_label = self._label(parent, LAYOUT["contentLeft"] - cx,
                                  content_y - cy, LAYOUT["labelWidth"],
                                  "Position (X,Y):")
        t.pos_var = tk.StringVar(value=DEFAULTS["position"])
        t.pos_edit = self._text_entry(parent, LAYOUT["editLeft"] - cx,
                                      content_y - cy, LAYOUT["editWidth"],
                                      t.pos_var, readonly=True)
        t.pos_var.trace_add("write", t.refresh_cache)

        t.pos_button = tk.Button(
            parent, text=POS_BTN["getText"], font=self.font_default,
            command=lambda _t=t: self.toggle_position(_t))
        # One trace keeps the button label honest no matter what moved the
        # field: a capture, a Clear, or the global RESET.
        t.pos_var.trace_add("write", lambda *_a, _t=t: self._sync_pos_button(_t))
        t.pos_button.place(x=POS_BTN["x"] - cx,
                           y=content_y + POS_BTN["yOffset"] - cy,
                           width=POS_BTN["w"], height=POS_BTN["h"])

        t._pos_place = {
            "label": {"x": LAYOUT["contentLeft"] - cx, "y": content_y - cy,
                      "width": LAYOUT["labelWidth"]},
            "edit": {"x": LAYOUT["editLeft"] - cx, "y": content_y - cy,
                     "width": LAYOUT["editWidth"], "height": 21},
            "button": {"x": POS_BTN["x"] - cx,
                       "y": content_y + POS_BTN["yOffset"] - cy,
                       "width": POS_BTN["w"], "height": POS_BTN["h"]},
        }

        return content_y + spacing["afterPos"]

    # ---------- mode switching ----------

    def _on_mode_change(self, t):
        """Port of OnModeChange."""
        t.current_mode = t.mode_var.get()

        if t.current_mode == "KeyPress":
            t.shared_label.configure(text=MOUSE_BTN["keyLabel"])
            t.button_var.set("Left")            # reset to Left, as in AHK
            t.button_ddl.place_forget()
            t.key_edit.place(**t._slot_place)
            if t.pos_edit is not None:
                t.pos_label.place_forget()
                t.pos_edit.place_forget()
                t.pos_button.place_forget()
        else:
            t.shared_label.configure(text=MOUSE_BTN["label"])
            t.key_var.set(DEFAULTS["key"])      # clear typed key, as in AHK
            t.key_edit.place_forget()
            t.button_ddl.place(**t._slot_place)
            if t.pos_edit is not None:
                t.pos_label.place(**t._pos_place["label"])
                t.pos_edit.place(**t._pos_place["edit"])
                t.pos_button.place(**t._pos_place["button"])

        t.refresh_cache()

    def _on_tab_changed(self, _event=None):
        active = self.notebook.index("current")
        for i, st in enumerate(self.scroll_tabs):
            st.lift() if i == active else st.lower()

    # ---------- position capture ----------

    def _sync_pos_button(self, t):
        """The button is a two-state control: it captures a position while the
        field is unset, and clears it back to the default once one is set."""
        if t.pos_button is None:
            return
        unset = (t.pos_var.get() == DEFAULTS["position"])
        t.pos_button.configure(
            text=POS_BTN["getText"] if unset else POS_BTN["clearText"])

    def toggle_position(self, t):
        """Capture a position, or clear one that was already captured.

        Clearing matters now that 0,0 means "click at the cursor" -- without it
        the only way back to that behaviour would be the global RESET, which
        wipes every timer in the app.
        """
        if t.pos_var.get() != DEFAULTS["position"]:
            t.pos_var.set(DEFAULTS["position"])
            t.refresh_cache()
            self.tooltip.show("Position cleared - clicks at the cursor.")
            return
        self.get_position(t)

    def get_position(self, t):
        """Port of GetPosition, using an X pointer grab instead of
        SetSystemCursor + KeyWait. The confirming click is swallowed."""
        if self.picker is not None:
            return
        self.picker = ib.PositionPicker()
        if not self.picker.start():
            self.picker.close()
            self.picker = None
            self.tooltip.show("Could not grab the pointer.")
            return
        self.picker_target = t
        self._poll_picker()

    def _poll_picker(self):
        result = self.picker.poll()
        if result is None:
            self.root.after(10, self._poll_picker)
            return

        t = self.picker_target
        self.picker.close()
        self.picker = None
        self.picker_target = None

        if result == "cancelled":
            self.tooltip.show("Position capture cancelled.")
            return

        x, y = result
        t.pos_var.set("%d,%d" % (x, y))
        t.refresh_cache()
        self.tooltip.show("Position captured: %d,%d" % (x, y))

    # ---------- validation ----------

    def _validate_timer_key(self, t):
        """Port of ValidateTimerKey."""
        if t.current_mode != "KeyPress":
            return True
        if t.cached_key != "" and not t.cached_key_valid:
            self.tooltip.show(
                "Invalid key name: '%s'\nExamples: a, Space, F1, LControl+V, "
                "Ctrl+Shift+Escape" % t.cached_key,
                INVALID_KEY_TOOLTIP_TIMEOUT)
            return False
        return True

    def _validate_all_key_timers(self):
        """Port of ValidateAllKeyTimers."""
        for t in self.general_timers:
            if not self._validate_timer_key(t):
                return False
        for ps in self.repeat_sets:
            for t in ps.timers:
                if not self._validate_timer_key(t):
                    return False
        return True

    # ---------- start / stop / reset ----------

    def toggle_script(self):
        """Port of ToggleScript."""
        if self.is_running:
            self.is_running = False
            self.stop_event.set()
            if self.worker is not None:
                self.worker.join(timeout=1.0)
                self.worker = None
            self.backend.release_all()
            self.start_stop_btn.configure(text="START (F6)")
            self.status_label.configure(text="Status: Stopped",
                                        foreground=COLORS["black"])
        else:
            if not self._validate_all_key_timers():
                return
            self.is_running = True
            self.stop_event.clear()
            start = now_ms()
            self.last_repeat_run = [start, start, start]
            self.start_stop_btn.configure(text="STOP (F6)")
            self.status_label.configure(text="Status: Running",
                                        foreground=COLORS["green"])
            self.worker = threading.Thread(target=self._main_loop, daemon=True)
            self.worker.start()

    def reset_all(self):
        """Port of ResetAll."""
        if self.is_running:
            self.toggle_script()

        for ps in self.repeat_sets:
            ps.seconds_var.set(DEFAULTS["seconds"])
            ps.cached_interval_ms = int(DEFAULTS["seconds"])

        for t in self.general_timers:
            self._reset_timer(t)

        for ps in self.repeat_sets:
            for t in ps.timers:
                self._reset_timer(t)

        for st in self.scroll_tabs:
            st.reset()

        self.tooltip.show("All values reset!")

    def _reset_timer(self, t):
        t.pos_var.set(DEFAULTS["position"])
        t.click_var.set(DEFAULTS["clickMs"])
        t.wait_var.set(DEFAULTS["waitMs"])
        t.mode_var.set("Click")
        t.key_var.set(DEFAULTS["key"])
        t.button_var.set("Left")
        t.shared_label.configure(text=MOUSE_BTN["label"])
        t.current_mode = "Click"
        self._on_mode_change(t)

    # ==================== RUNTIME ENGINE ====================
    # Runs on a worker thread. It only ever reads cached_* fields, never
    # widgets, which is both the AHK design and a hard requirement for Tk
    # thread safety.

    def interruptible_sleep(self, duration_ms):
        """Port of InterruptibleSleep. Returns True if the full duration
        elapsed, False if a stop interrupted it.

        The original polls isRunning every 50ms; an Event makes the stop
        instant instead of up to 50ms late. Same semantics, better latency.
        """
        if duration_ms <= 0:
            return not self.stop_event.is_set()
        return not self.stop_event.wait(duration_ms / 1000.0)

    def click_at_position(self, x, y, hold_duration, click_btn):
        """Port of ClickAtPosition."""
        self.backend.warp(x, y)
        if not self.interruptible_sleep(CLICK_TIMING["preDelay"]):
            return False
        self.backend.mouse_down(click_btn)
        if not self.interruptible_sleep(hold_duration):
            self.backend.mouse_up(click_btn)   # release even if interrupted
            return False
        self.backend.mouse_up(click_btn)
        return True

    def type_text(self, text, per_char_ms):
        """Type `text` verbatim, one paced keystroke at a time.

        DEVIATION (agreed): Hold is the per-character time. Each key is held
        down for Hold ms, then there is a gap of Hold ms before the next one:

            [Shift down] key down -- Hold -- key up [Shift up] -- Hold -- next

        The original sent the whole string at once, which Windows buffers
        safely. Here it arrived in ~1.5 ms, inside a single frame, so games
        that read input once per frame (Sober/Roblox at 60 fps) missed letters,
        and the row's Wait started long before the game had actually taken the
        text in -- letting the next row's Enter cut the message off. Pacing
        makes the typing take real time here, so Wait now starts only after
        the last character has gone in.

        Hold must be non-zero: at Hold 0 a text row types nothing, on every
        tab. Returns False if a stop interrupted it; the key in flight and
        Shift are always released first, so nothing is left held down.
        """
        if per_char_ms <= 0:
            return True
        for ch in text:
            stroke = self.backend.char_keystroke(ch)
            if stroke is None:
                continue
            self.backend.press_keystroke(stroke)
            held = self.interruptible_sleep(per_char_ms)
            self.backend.release_keystroke(stroke)
            if not held or not self.interruptible_sleep(per_char_ms):
                return False
        return True

    def send_key_press(self, key_string, hold_duration):
        """Port of SendKeyPress.

        DEVIATION (agreed): modifiers stay held for the entire hold duration.
        AHK's Send("^{v down}") releases Ctrl the instant V goes down, so
        "hold Ctrl+V for 500ms" never actually held Ctrl.
        """
        # Quoted literal -> type verbatim, paced by Hold (see type_text).
        if ib.is_quoted_literal(key_string):
            return self.type_text(key_string[1:-1], hold_duration)

        # Special case: a literal "+" on its own. Resolved through the live
        # keyboard layout rather than assuming shift+=, so it stays correct on
        # non-US layouts.
        if key_string.strip() == "+":
            stroke = self.backend.char_keystroke("+")
            if stroke is None:
                return True
            self.backend.press_keystroke(stroke)
            ok = self.interruptible_sleep(hold_duration)
            self.backend.release_keystroke(stroke)
            return ok

        keys = [k.strip() for k in key_string.split("+")]
        mods = [k for k in keys if k.lower() in ib.MODIFIER_NAMES]
        finals = [k for k in keys if k.lower() not in ib.MODIFIER_NAMES]
        final_key = finals[-1] if finals else ""

        # No non-modifier key: hold every modifier for the duration.
        if final_key == "":
            if hold_duration > 0:
                for k in keys:
                    self.backend.key_down(k)
                ok = self.interruptible_sleep(hold_duration)
                for k in reversed(keys):
                    self.backend.key_up(k)
                return ok
            for k in keys:                     # zero hold: tap each, as in AHK
                self.backend.key_tap(k)
            return True

        for m in mods:
            self.backend.key_down(m)
        if hold_duration > 0:
            self.backend.key_down(final_key)
            ok = self.interruptible_sleep(hold_duration)
            self.backend.key_up(final_key)
            for m in reversed(mods):
                self.backend.key_up(m)
            return ok
        self.backend.key_tap(final_key)
        for m in reversed(mods):
            self.backend.key_up(m)
        return True

    def return_cursor(self, x, y):
        """Port of ReturnCursor."""
        self.backend.warp(x, y)
        time.sleep(CLICK_TIMING["cursorReturn1"] / 1000.0)
        self.backend.warp(x, y)
        time.sleep(CLICK_TIMING["cursorReturn2"] / 1000.0)

    def run_repeat_timer_set(self, timers):
        """Port of RunPositionTimerSet.

        An unset (0,0) row resolves against the pointer as it was BEFORE this
        set moved it, not against the live pointer. Reading it live would make
        the answer depend on what earlier rows warped to, which is exactly the
        thing that made 0,0 mean something different here than on the General
        tab. Snapshotting it once keeps 0,0 meaning "the user's real mouse"
        everywhere, whatever each tab's cursor-return schedule happens to be.
        """
        cursor_x, cursor_y = self.backend.get_pointer()

        for t in timers:
            if self.stop_event.is_set():
                return False

            click_duration = t.cached_click_ms
            wait_duration = t.cached_wait_ms
            position_str = t.cached_position
            mode = t.current_mode

            if click_duration == 0 and wait_duration == 0:
                continue

            if mode == "KeyPress":
                key_name = t.cached_key
                if key_name == "" or not t.cached_key_valid:
                    continue
                if not self.send_key_press(key_name, click_duration):
                    return False
                if wait_duration > 0:
                    if not self.interruptible_sleep(wait_duration):
                        return False
                continue

            # Click mode. A position left at the 0,0 default means "click at
            # the cursor" rather than "skip me", using the snapshot taken
            # before this set ran. To click one spot twice, put the same
            # coordinates in both rows.
            use_cursor = (position_str == "0,0")
            if use_cursor:
                x_pos, y_pos = cursor_x, cursor_y
            else:
                parts = position_str.split(",")
                if len(parts) != 2:
                    continue
                try:
                    x_pos, y_pos = int(parts[0]), int(parts[1])
                except ValueError:
                    continue

            if click_duration > 0:
                if not self.click_at_position(x_pos, y_pos, click_duration,
                                              t.cached_click_button):
                    return False
                if not self.interruptible_sleep(CLICK_TIMING["postDelay"]):
                    return False
            elif not use_cursor:
                # Hold=0 in Click mode means "move there, don't click" -- the
                # original behaviour, deliberately preserved. With no position
                # set there is nowhere to move to, so this row just waits.
                self.backend.warp(x_pos, y_pos)

            if wait_duration > 0:
                if not self.interruptible_sleep(wait_duration):
                    return False
        return True

    def try_run_repeat_sets(self):
        """Port of TryRunPositionSets.

        The interval is measured from the END of the previous run, exactly as
        in the original, so a slow set never overlaps itself. The timestamp is
        therefore taken before the cursor is handed back, which is cleanup
        rather than part of the run.

        The cursor is returned per SET rather than once per tick, so two tabs
        coming due on the same tick cannot hand each other a moved pointer.
        """
        current_time = now_ms()
        any_ran = False

        for idx, ps in enumerate(self.repeat_sets):
            target_ms = ps.cached_interval_ms
            is_due = (target_ms > 0
                      and current_time - self.last_repeat_run[idx] >= target_ms)
            if is_due and not self.stop_event.is_set():
                saved_x, saved_y = self.backend.get_pointer()
                self.run_repeat_timer_set(ps.timers)
                self.last_repeat_run[idx] = now_ms()
                # Returned even when a stop interrupted the set, so stopping
                # never strands the pointer at a timer's target.
                self.return_cursor(saved_x, saved_y)
                any_ran = True

        return any_ran

    def run_general_timers(self):
        """Port of RunGeneralTimers.

        DEVIATION (agreed): the cursor position is re-read for each click
        instead of being pinned to the position captured at the start of the
        tick, so the clicker follows the mouse and can be retargeted mid-run.

        Positions here return the cursor after EVERY click, unlike a repeat
        set, which returns it once the whole set has run. The difference is
        forced by the timing model: this tab has no interval, so it loops
        back-to-back and Wait is the only way to pace it. Returning per set
        would leave the pointer parked at the target for the whole of every
        Wait, i.e. permanently.

        The pointer is therefore always back under the user's hand by the time
        the next row runs, so reading it live here means the same thing as the
        pre-set snapshot a repeat set uses -- and it still lets the clicker be
        retargeted mid-run, per deviation 2.
        """
        for t in self.general_timers:
            if self.stop_event.is_set():
                break

            click_duration = t.cached_click_ms
            wait_duration = t.cached_wait_ms
            mode = t.current_mode

            if click_duration == 0 and wait_duration == 0:
                continue

            if mode == "KeyPress":
                key_name = t.cached_key
                if key_name == "" or not t.cached_key_valid:
                    continue
                # Hold=0 sends nothing here -- the original behaviour, kept.
                if click_duration > 0:
                    if not self.send_key_press(key_name, click_duration):
                        break
            elif click_duration > 0:
                # Hold=0 still does nothing on this tab. A repeat timer treats
                # it as "move there, don't click", but with the cursor handed
                # straight back that would be a no-op here.
                position_str = t.cached_position
                if position_str == "0,0":
                    cx, cy = self.backend.get_pointer()
                    if not self.click_at_position(cx, cy, click_duration,
                                                  t.cached_click_button):
                        break
                else:
                    parts = position_str.split(",")
                    if len(parts) != 2:
                        continue
                    try:
                        x_pos, y_pos = int(parts[0]), int(parts[1])
                    except ValueError:
                        continue
                    saved_x, saved_y = self.backend.get_pointer()
                    ok = self.click_at_position(x_pos, y_pos, click_duration,
                                                t.cached_click_button)
                    if ok:
                        # The same guard a repeat set gets after its clicks:
                        # let the target app see the release before the
                        # pointer leaves, so a click is not read as the start
                        # of a drag. Only meaningful now that this tab warps.
                        ok = self.interruptible_sleep(CLICK_TIMING["postDelay"])
                    # Hand the pointer back even on an interrupt, so stopping
                    # never leaves it stranded at the target.
                    self.return_cursor(saved_x, saved_y)
                    if not ok:
                        break

            if wait_duration > 0:
                if not self.interruptible_sleep(wait_duration):
                    break

    def _main_loop(self):
        """Port of MainClickLoop, driven as a worker thread rather than
        SetTimer. Priority order is unchanged: repeat sets beat general
        timers, and general timers are skipped on any tick where a set ran.

        Cursor bookkeeping lives in the two run_* methods now: a repeat set
        returns the pointer when the set ends, a general timer when its click
        ends. Doing it here instead would span every set that came due.
        """
        interval = TIMERS["loopInterval"] / 1000.0
        while not self.stop_event.is_set():
            tick_start = time.monotonic()

            any_repeat_ran = self.try_run_repeat_sets()

            if not any_repeat_ran and not self.stop_event.is_set():
                self.run_general_timers()

            elapsed = time.monotonic() - tick_start
            self.stop_event.wait(max(0.0, interval - elapsed))

    # ---------- shutdown ----------

    def exit_app(self):
        """Port of the F7 hotkey and the GUI Close handler."""
        self.is_running = False
        self.stop_event.set()
        if self.worker is not None:
            self.worker.join(timeout=1.0)
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        if self.picker is not None:
            self.picker.close()
        try:
            self.backend.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


def main():
    root = tk.Tk()

    try:
        backend = ib.InputBackend()
    except ib.BackendError as exc:
        root.withdraw()
        messagebox.showerror("Input Automator - backend unavailable", str(exc))
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1

    app = InputAutomator(root, backend)

    # Mirror of AHK's OnExit hook: release everything on any exit path.
    atexit.register(lambda: backend.close())
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_a: app.exit_app())

    root.protocol("WM_DELETE_WINDOW", app.exit_app)

    # Tk's mainloop blocks in C, where Python signal handlers never get a
    # chance to run. This idle tick hands control back periodically so Ctrl+C
    # and SIGTERM are actually delivered.
    def _signal_tick():
        root.after(200, _signal_tick)

    root.after(200, _signal_tick)

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
