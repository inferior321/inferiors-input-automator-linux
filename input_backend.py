"""
Linux input backend for InFeRiOr's Input Automator.

Replaces the Win32 pieces of the original AutoHotkey v2 script:

    AHK / Win32                     ->  Linux equivalent used here
    ---------------------------------------------------------------
    Send / Click                    ->  evdev virtual devices via /dev/uinput
    SetCursorPos / MouseMove        ->  X11 XWarpPointer
    MouseGetPos                     ->  X11 QueryPointer
    F6:: / F7:: global hotkeys      ->  evdev read of every real keyboard
    SetSystemCursor + KeyWait       ->  X11 XGrabPointer with a crosshair cursor
    GetKeyVK (key validation)       ->  AHK_KEY_MAP lookup below

Injection goes through /dev/uinput rather than XTEST so that events are
indistinguishable from real hardware, which is what makes this work inside
games, Wine and Proton. Pointer *movement* still uses XWarpPointer because
that is the closest analogue to what the original does: AHK's SetCursorPos
teleports the cursor without injecting motion events.
"""

import os
import select
import threading
import time

import evdev
from evdev import UInput, ecodes as e

from Xlib import X, display, Xcursorfont

# Name given to the virtual devices. Also used to recognise (and ignore) our
# own devices in the hotkey listener, so injected keys never feed back in.
KBD_DEVICE_NAME = "Inferiors Input Automator Keyboard"
MOUSE_DEVICE_NAME = "Inferiors Input Automator Mouse"

# Time to let udev/libinput/X notice a newly created virtual device. Without
# this the first few injected events are silently dropped.
DEVICE_SETTLE_SECONDS = 0.35


# ==================== KEY NAME MAP ====================
# Maps AutoHotkey key names (lowercased) to evdev key codes. These are
# *physical* key positions, which is what a game wants: "a" means the key
# labelled A on a US layout, the same key AHK's Send("{a}") would hit.
#
# Verbatim text typing ("quoted literals") deliberately does NOT use this
# table -- see type_text(), which resolves characters through the live X
# keyboard mapping so it stays correct on non-US layouts.

AHK_KEY_MAP = {}


def _register(names, code):
    for n in names:
        AHK_KEY_MAP[n.lower()] = code


# Letters and digits
for _c in "abcdefghijklmnopqrstuvwxyz":
    _register([_c], getattr(e, "KEY_" + _c.upper()))
for _d in "0123456789":
    _register([_d], getattr(e, "KEY_" + _d))

# Function keys
for _i in range(1, 25):
    if hasattr(e, "KEY_F%d" % _i):
        _register(["f%d" % _i], getattr(e, "KEY_F%d" % _i))

# Modifiers. AHK treats the sided and unsided names as distinct key names but
# both are valid; unsided resolves to the left-hand key, matching AHK.
_register(["lcontrol", "lctrl", "control", "ctrl"], e.KEY_LEFTCTRL)
_register(["rcontrol", "rctrl"], e.KEY_RIGHTCTRL)
_register(["lshift", "shift"], e.KEY_LEFTSHIFT)
_register(["rshift"], e.KEY_RIGHTSHIFT)
_register(["lalt", "alt"], e.KEY_LEFTALT)
_register(["ralt", "altgr"], e.KEY_RIGHTALT)
_register(["lwin"], e.KEY_LEFTMETA)
_register(["rwin"], e.KEY_RIGHTMETA)

# Navigation / editing
_register(["space"], e.KEY_SPACE)
_register(["enter", "return"], e.KEY_ENTER)
_register(["escape", "esc"], e.KEY_ESC)
_register(["tab"], e.KEY_TAB)
_register(["backspace", "bs"], e.KEY_BACKSPACE)
_register(["delete", "del"], e.KEY_DELETE)
_register(["insert", "ins"], e.KEY_INSERT)
_register(["home"], e.KEY_HOME)
_register(["end"], e.KEY_END)
_register(["pgup", "pageup", "prior"], e.KEY_PAGEUP)
_register(["pgdn", "pagedown", "next"], e.KEY_PAGEDOWN)
_register(["up"], e.KEY_UP)
_register(["down"], e.KEY_DOWN)
_register(["left"], e.KEY_LEFT)
_register(["right"], e.KEY_RIGHT)

# Locks and system keys
_register(["capslock"], e.KEY_CAPSLOCK)
_register(["numlock"], e.KEY_NUMLOCK)
_register(["scrolllock"], e.KEY_SCROLLLOCK)
_register(["printscreen", "prtsc"], e.KEY_SYSRQ)
_register(["pause", "break"], e.KEY_PAUSE)
_register(["appskey", "menu"], e.KEY_COMPOSE)

# Numpad
for _i in range(10):
    _register(["numpad%d" % _i], getattr(e, "KEY_KP%d" % _i))
_register(["numpaddot", "numpaddel"], e.KEY_KPDOT)
_register(["numpaddiv"], e.KEY_KPSLASH)
_register(["numpadmult"], e.KEY_KPASTERISK)
_register(["numpadsub"], e.KEY_KPMINUS)
_register(["numpadadd"], e.KEY_KPPLUS)
_register(["numpadenter"], e.KEY_KPENTER)

# Punctuation, by both AHK symbol and common name
_register(["-", "minus"], e.KEY_MINUS)
_register(["=", "equals"], e.KEY_EQUAL)
_register(["[", "openbracket"], e.KEY_LEFTBRACE)
_register(["]", "closebracket"], e.KEY_RIGHTBRACE)
_register(["\\", "backslash"], e.KEY_BACKSLASH)
_register([";", "semicolon"], e.KEY_SEMICOLON)
_register(["'", "quote"], e.KEY_APOSTROPHE)
_register(["`", "backtick"], e.KEY_GRAVE)
_register([",", "comma"], e.KEY_COMMA)
_register([".", "period", "dot"], e.KEY_DOT)
_register(["/", "slash"], e.KEY_SLASH)

# Mouse buttons are valid AHK key names too (GetKeyVK("LButton") is non-zero),
# so Send("{LButton down}") is legal in the original. Mirror that.
MOUSE_BUTTON_KEYS = {
    "lbutton": e.BTN_LEFT,
    "rbutton": e.BTN_RIGHT,
    "mbutton": e.BTN_MIDDLE,
}

# Mouse Button dropdown values -> evdev button codes.
CLICK_BUTTONS = {
    "Left": e.BTN_LEFT,
    "Right": e.BTN_RIGHT,
    "Middle": e.BTN_MIDDLE,
}

# Modifier names, used when a combo has no non-modifier key.
MODIFIER_NAMES = {
    "ctrl", "lctrl", "rctrl", "control", "lcontrol", "rcontrol",
    "alt", "lalt", "ralt", "shift", "lshift", "rshift", "lwin", "rwin",
}


def is_quoted_literal(s):
    """Port of IsQuotedLiteral: a double-quote-wrapped literal, min length 2."""
    return len(s) >= 2 and s[0] == '"' and s[-1] == '"'


def is_valid_key(key_name):
    """Port of IsValidKey. Accepts a single key, a '+'-separated combo, a bare
    '+', or a quoted literal (whose contents are typed verbatim)."""
    if key_name == "":
        return False
    if is_quoted_literal(key_name):
        return True
    if key_name.strip() == "+":
        return True
    for part in key_name.split("+"):
        part = part.strip()
        if part == "":
            return False
        low = part.lower()
        if low not in AHK_KEY_MAP and low not in MOUSE_BUTTON_KEYS:
            return False
    return True


class BackendError(RuntimeError):
    """Raised when the virtual devices or the X connection cannot be set up."""


class InputBackend:
    """Virtual keyboard + mouse over /dev/uinput, plus X11 pointer control."""

    def __init__(self):
        if not os.access("/dev/uinput", os.W_OK):
            raise BackendError(
                "/dev/uinput is not writable.\n\n"
                "Add yourself to the 'input' group and create a udev rule:\n"
                "  sudo usermod -aG input $USER\n"
                "  echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\"' | "
                "sudo tee /etc/udev/rules.d/99-uinput.rules\n"
                "  sudo modprobe uinput && sudo udevadm control --reload-rules\n"
                "then log out and back in."
            )

        # Keyboard device advertises every code we can possibly send, so the
        # capability set never has to change after creation.
        key_codes = sorted(set(AHK_KEY_MAP.values()))
        # Also advertise the full printable range for verbatim text typing,
        # which resolves keycodes dynamically from the X layout.
        key_codes = sorted(set(key_codes) | set(range(1, 128)))

        try:
            self._kbd = UInput({e.EV_KEY: key_codes}, name=KBD_DEVICE_NAME)
            self._mouse = UInput(
                {
                    e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
                    # REL axes are declared so the kernel classifies this as a
                    # real mouse. We never emit motion -- movement is done with
                    # XWarpPointer, mirroring AHK's SetCursorPos.
                    e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL],
                },
                name=MOUSE_DEVICE_NAME,
            )
        except OSError as exc:
            raise BackendError("Could not create virtual input devices: %s" % exc)

        try:
            self._display = display.Display()
            self._root = self._display.screen().root
        except Exception as exc:
            self._kbd.close()
            self._mouse.close()
            raise BackendError("Could not open the X display: %s" % exc)

        # Tracks what we currently hold down so release_all() can be exact.
        self._held_keys = set()
        self._held_buttons = set()
        self._lock = threading.Lock()

        time.sleep(DEVICE_SETTLE_SECONDS)

    # ---------- keyboard ----------

    def _emit_key(self, code, value):
        with self._lock:
            self._kbd.write(e.EV_KEY, code, value)
            self._kbd.syn()
            if value:
                self._held_keys.add(code)
            else:
                self._held_keys.discard(code)

    def key_down(self, ahk_name):
        code = self._resolve(ahk_name)
        if code is None:
            return
        if code in CLICK_BUTTONS.values():
            self.mouse_down_code(code)
        else:
            self._emit_key(code, 1)

    def key_up(self, ahk_name):
        code = self._resolve(ahk_name)
        if code is None:
            return
        if code in CLICK_BUTTONS.values():
            self.mouse_up_code(code)
        else:
            self._emit_key(code, 0)

    def key_tap(self, ahk_name):
        self.key_down(ahk_name)
        self.key_up(ahk_name)

    @staticmethod
    def _resolve(ahk_name):
        low = ahk_name.strip().lower()
        if low in MOUSE_BUTTON_KEYS:
            return MOUSE_BUTTON_KEYS[low]
        return AHK_KEY_MAP.get(low)

    def type_text(self, text):
        """Type a string verbatim -- the port of AHK's SendText.

        Characters are resolved through the *live X keyboard mapping* rather
        than a hardcoded US table, so this stays correct on any layout. Each
        character is looked up to find its keycode and whether Shift is
        required to reach it.
        """
        for ch in text:
            resolved = self._char_to_keycode(ch)
            if resolved is None:
                continue
            code, needs_shift = resolved
            if needs_shift:
                self._emit_key(e.KEY_LEFTSHIFT, 1)
            self._emit_key(code, 1)
            self._emit_key(code, 0)
            if needs_shift:
                self._emit_key(e.KEY_LEFTSHIFT, 0)

    def char_down(self, ch):
        """Press the key that produces `ch` (with Shift if the layout needs
        it), leaving it held. Used for holding a character that has no AHK key
        name of its own, such as a bare '+'."""
        resolved = self._char_to_keycode(ch)
        if resolved is None:
            return False
        code, needs_shift = resolved
        if needs_shift:
            self._emit_key(e.KEY_LEFTSHIFT, 1)
        self._emit_key(code, 1)
        return True

    def char_up(self, ch):
        resolved = self._char_to_keycode(ch)
        if resolved is None:
            return
        code, needs_shift = resolved
        self._emit_key(code, 0)
        if needs_shift:
            self._emit_key(e.KEY_LEFTSHIFT, 0)

    def _char_to_keycode(self, ch):
        """Return (evdev_code, needs_shift) for a character, or None."""
        codepoint = ord(ch)
        # Latin-1 maps to keysyms 1:1; everything else uses the Unicode range.
        keysym = codepoint if codepoint < 0x100 else 0x01000000 + codepoint
        x_keycode = self._display.keysym_to_keycode(keysym)
        if not x_keycode:
            return None
        mapping = self._display.get_keyboard_mapping(x_keycode, 1)[0]
        needs_shift = False
        for level, sym in enumerate(mapping[:2]):
            if sym == keysym:
                needs_shift = (level == 1)
                break
        # evdev codes sit 8 below X keycodes.
        return x_keycode - 8, needs_shift

    # ---------- mouse ----------

    def mouse_down_code(self, code):
        with self._lock:
            self._mouse.write(e.EV_KEY, code, 1)
            self._mouse.syn()
            self._held_buttons.add(code)

    def mouse_up_code(self, code):
        with self._lock:
            self._mouse.write(e.EV_KEY, code, 0)
            self._mouse.syn()
            self._held_buttons.discard(code)

    def mouse_down(self, button_name):
        self.mouse_down_code(CLICK_BUTTONS.get(button_name, e.BTN_LEFT))

    def mouse_up(self, button_name):
        self.mouse_up_code(CLICK_BUTTONS.get(button_name, e.BTN_LEFT))

    # ---------- pointer ----------

    def warp(self, x, y):
        """Port of SetCursorPos / MouseMove: teleport the cursor."""
        self._root.warp_pointer(int(x), int(y))
        self._display.sync()

    def get_pointer(self):
        """Port of MouseGetPos, in screen (root) coordinates."""
        p = self._root.query_pointer()
        return p.root_x, p.root_y

    # ---------- cleanup ----------

    def release_all(self):
        """Port of ReleaseAllKeys.

        Releases every modifier and all three mouse buttons unconditionally
        (as the original does), plus anything else we still hold, so nothing
        can get stuck after a stop, an exit or a crash.
        """
        for name in ("lcontrol", "rcontrol", "lshift", "rshift",
                     "lalt", "ralt", "lwin", "rwin"):
            code = AHK_KEY_MAP.get(name)
            if code is not None:
                self._emit_key(code, 0)
        for code in list(self._held_keys):
            self._emit_key(code, 0)
        for code in (e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE):
            self.mouse_up_code(code)

    def close(self):
        try:
            self.release_all()
        except Exception:
            pass
        for dev in (self._kbd, self._mouse):
            try:
                dev.close()
            except Exception:
                pass
        try:
            self._display.close()
        except Exception:
            pass


# ==================== GLOBAL HOTKEY LISTENER ====================
# Replaces AHK's "F6::" / "F7::" global hotkeys. Reads every real keyboard
# directly, so the hotkeys work even when a fullscreen game has focus.
#
# The key is NOT swallowed: the focused application still receives it. Doing
# otherwise would require an exclusive grab of the whole keyboard.
#
# Only a *bare* F6/F7 fires -- if any modifier is held the event is ignored,
# so Cinnamon's Alt+F7 ("move window") never triggers the exit hotkey.

_MODIFIER_CODES = {
    e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL, e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT,
    e.KEY_LEFTALT, e.KEY_RIGHTALT, e.KEY_LEFTMETA, e.KEY_RIGHTMETA,
}


# How often to re-scan for keyboards that appeared after startup. Without
# this, a keyboard plugged in later -- or a wireless one that reconnects --
# would never be monitored and the hotkeys would silently stop working on it.
DEVICE_RESCAN_SECONDS = 3.0


class HotkeyListener(threading.Thread):
    def __init__(self, on_f6, on_f7):
        super().__init__(daemon=True)
        self._on_f6 = on_f6
        self._on_f7 = on_f7
        self._stop = threading.Event()
        self._held_mods = set()
        self.devices = self._find_keyboards()

    @staticmethod
    def _find_keyboards(exclude_paths=()):
        found = []
        for path in evdev.list_devices():
            if path in exclude_paths:
                continue
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue
            # Never listen to our own virtual devices, or injected keys would
            # feed straight back into the hotkey handler.
            if dev.name in (KBD_DEVICE_NAME, MOUSE_DEVICE_NAME):
                dev.close()
                continue
            keys = dev.capabilities().get(e.EV_KEY, [])
            if e.KEY_F6 in keys or e.KEY_F7 in keys:
                found.append(dev)
            else:
                dev.close()
        return found

    def stop(self):
        self._stop.set()

    def run(self):
        fd_map = {dev.fd: dev for dev in self.devices}
        next_rescan = time.monotonic() + DEVICE_RESCAN_SECONDS

        while not self._stop.is_set():
            if time.monotonic() >= next_rescan:
                next_rescan = time.monotonic() + DEVICE_RESCAN_SECONDS
                known = {d.path for d in fd_map.values()}
                for dev in self._find_keyboards(exclude_paths=known):
                    fd_map[dev.fd] = dev
                self.devices = list(fd_map.values())

            if not fd_map:
                self._stop.wait(0.2)
                continue

            try:
                ready, _, _ = select.select(list(fd_map), [], [], 0.2)
            except (OSError, ValueError):
                # A device vanished mid-select; drop the dead ones and retry.
                for fd, dev in list(fd_map.items()):
                    try:
                        os.fstat(fd)
                    except OSError:
                        fd_map.pop(fd, None)
                continue

            for fd in ready:
                dev = fd_map.get(fd)
                if dev is None:
                    continue
                try:
                    for event in dev.read():
                        self._handle(event)
                except OSError:
                    fd_map.pop(fd, None)

    def _handle(self, event):
        if event.type != e.EV_KEY:
            return
        code, value = event.code, event.value
        if code in _MODIFIER_CODES:
            if value:
                self._held_mods.add(code)
            else:
                self._held_mods.discard(code)
            return
        # value 1 == key down (2 == autorepeat, which we ignore)
        if value != 1 or self._held_mods:
            return
        if code == e.KEY_F6:
            self._on_f6()
        elif code == e.KEY_F7:
            self._on_f7()


# ==================== POSITION PICKER ====================
# Replaces SetSystemCursor(crosshair) + KeyWait("LButton", "D").
#
# Grabs the pointer on a dedicated X connection with owner_events=False, so
# the confirming click is delivered to us and NOT to whatever is underneath.
# That is the one deliberate deviation here: it stops a stray click landing in
# your game while you are picking a spot.
#
# Right-click or middle-click cancels, and the grab times out on its own, so
# the pointer can never stay captured.

PICKER_TIMEOUT_SECONDS = 30


class PositionPicker:
    def __init__(self):
        self._display = display.Display()
        self._root = self._display.screen().root
        font = self._display.open_font("cursor")
        self._cursor = font.create_glyph_cursor(
            font,
            Xcursorfont.crosshair,
            Xcursorfont.crosshair + 1,
            (0, 0, 0),
            (65535, 65535, 65535),
        )
        self._active = False
        self._deadline = 0.0

    def start(self):
        """Begin capture. Returns True if the pointer grab succeeded."""
        result = self._root.grab_pointer(
            False,                      # owner_events -> swallow the click
            X.ButtonPressMask,
            X.GrabModeAsync,
            X.GrabModeAsync,
            X.NONE,
            self._cursor,
            X.CurrentTime,
        )
        self._active = (result == X.GrabSuccess)
        self._deadline = time.monotonic() + PICKER_TIMEOUT_SECONDS
        return self._active

    def poll(self):
        """Non-blocking check, driven from the Tk event loop.

        Returns (x, y) once a left-click lands, the string "cancelled" if the
        user right/middle-clicked or the grab timed out, or None while waiting.
        """
        if not self._active:
            return "cancelled"
        if time.monotonic() > self._deadline:
            self.finish()
            return "cancelled"
        while self._display.pending_events():
            event = self._display.next_event()
            if event.type != X.ButtonPress:
                continue
            if event.detail == 1:
                pos = (event.root_x, event.root_y)
                self.finish()
                return pos
            if event.detail in (2, 3):
                self.finish()
                return "cancelled"
        return None

    def finish(self):
        if self._active:
            self._display.ungrab_pointer(X.CurrentTime)
            self._display.sync()
            self._active = False

    def close(self):
        self.finish()
        try:
            self._display.close()
        except Exception:
            pass
