#!/usr/bin/env bash
#
# Installer for InFeRiOr's Input Automator (Linux port).
#
# Everything your own user can do runs automatically: creating the virtualenv,
# installing Python dependencies, and adding the application menu entry.
#
# Anything needing root -- apt packages, the /dev/uinput udev rule, adding you
# to the "input" group -- is never run silently. Each such step prints the exact
# command and waits for a y/N. Decline any of them and it is collected into a
# summary you can run later.
#
# The script is idempotent and safe to re-run: every step detects whether it is
# already done and skips it.
#
#   ./install.sh           install (asks before each privileged step)
#   ./install.sh --check    diagnose only, change absolutely nothing
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
APPDIR="$HOME/.local/share/applications"
DESKTOP="$APPDIR/inferiors-input-automator.desktop"
UDEV_RULE="/etc/udev/rules.d/99-uinput.rules"

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

if [[ -t 1 ]]; then
    B=$'\e[1m'; GRN=$'\e[32m'; YEL=$'\e[33m'; RED=$'\e[31m'; RST=$'\e[0m'
else
    B=""; GRN=""; YEL=""; RED=""; RST=""
fi

ok()      { printf "  ${GRN}[ ok ]${RST} %s\n" "$1"; }
miss()    { printf "  ${YEL}[todo]${RST} %s\n" "$1"; }
fail()    { printf "  ${RED}[fail]${RST} %s\n" "$1"; }
note()    { printf "         %s\n" "$1"; }
section() { printf "\n${B}%s${RST}\n" "$1"; }

PENDING=()          # privileged commands that were skipped or declined
PROBLEMS=0

# Run a privileged command after explicit confirmation.
#   confirm_run "<description>" "<shell command>"
confirm_run() {
    local desc="$1" cmd="$2"
    if (( CHECK_ONLY )); then
        miss "$desc"
        PENDING+=("$cmd")
        return 1
    fi
    printf "\n  This step needs administrator rights:\n"
    printf "    ${B}%s${RST}\n" "$cmd"
    local reply
    read -r -p "  Run it now? [y/N] " reply
    if [[ "$reply" =~ ^[Yy] ]]; then
        if sudo bash -c "$cmd"; then
            ok "$desc"
            return 0
        fi
        fail "$desc - the command failed"
        PENDING+=("$cmd")
        return 1
    fi
    miss "$desc - skipped"
    PENDING+=("$cmd")
    return 1
}

printf "${B}InFeRiOr's Input Automator - installer${RST}\n"
printf "Project: %s\n" "$HERE"
# Note the %s form: with colors disabled the literal would begin with "--",
# which printf would try to parse as an option.
(( CHECK_ONLY )) && printf "%s--check: diagnosing only, nothing will be modified.%s\n" "$YEL" "$RST"

# ---------------------------------------------------------------- 1. platform
section "1. Platform"

if [[ "$(uname -s)" != "Linux" ]]; then
    fail "This port targets Linux; detected $(uname -s)."
    exit 1
fi
ok "Linux $(uname -r)"

SESSION="${XDG_SESSION_TYPE:-unknown}"
if [[ "$SESSION" == "wayland" ]]; then
    fail "Wayland session detected."
    note "This app needs X11: it uses XWarpPointer for cursor movement and an"
    note "X pointer grab for 'Get Pos'. Log in with an Xorg/X11 session."
    PROBLEMS=$((PROBLEMS + 1))
elif [[ "$SESSION" == "x11" ]]; then
    ok "X11 session"
else
    miss "Could not determine session type (XDG_SESSION_TYPE=$SESSION)"
    note "The app requires X11."
fi

# ------------------------------------------------------------ 2. system python
section "2. System Python packages"

if ! command -v python3 >/dev/null 2>&1; then
    fail "python3 not found"
    confirm_run "install python3" "apt-get install -y python3"
else
    ok "python3 $(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
fi

if python3 -m venv --help >/dev/null 2>&1; then
    ok "python3-venv available"
else
    miss "python3-venv missing (cannot create a virtualenv without it)"
    confirm_run "install python3-venv" "apt-get install -y python3-venv"
fi

# tkinter ships with CPython but Debian/Mint split it into python3-tk.
if python3 -c "import tkinter" >/dev/null 2>&1; then
    ok "tkinter available (python3-tk)"
else
    miss "tkinter missing - the GUI cannot start"
    confirm_run "install python3-tk" "apt-get install -y python3-tk"
fi

# --------------------------------------------------------------- 3. virtualenv
section "3. Virtualenv and Python dependencies"

if (( CHECK_ONLY )); then
    if [[ -x "$VENV/bin/python" ]]; then
        ok "virtualenv present at .venv"
        for mod in evdev Xlib tkinter; do
            if "$VENV/bin/python" -c "import $mod" >/dev/null 2>&1; then
                ok "  import $mod"
            else
                miss "  import $mod FAILED"
                PROBLEMS=$((PROBLEMS + 1))
            fi
        done
    else
        miss "no virtualenv yet (run ./install.sh to create it)"
    fi
else
    if [[ ! -x "$VENV/bin/python" ]]; then
        printf "  creating virtualenv ...\n"
        if python3 -m venv "$VENV"; then
            ok "created .venv"
        else
            fail "could not create the virtualenv"
            PROBLEMS=$((PROBLEMS + 1))
        fi
    else
        ok "virtualenv already present"
    fi

    if [[ -x "$VENV/bin/python" ]]; then
        printf "  installing dependencies from requirements.txt ...\n"
        "$VENV/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1
        if "$VENV/bin/pip" install --quiet -r "$HERE/requirements.txt"; then
            ok "dependencies installed"
        else
            fail "pip install failed"
            PROBLEMS=$((PROBLEMS + 1))
        fi
        for mod in evdev Xlib tkinter; do
            if "$VENV/bin/python" -c "import $mod" >/dev/null 2>&1; then
                ok "import $mod"
            else
                fail "import $mod FAILED"
                PROBLEMS=$((PROBLEMS + 1))
            fi
        done
    fi
fi

# ------------------------------------------------------------- 4. uinput access
section "4. Input device access (/dev/uinput)"
# The app creates a virtual keyboard and mouse here. Without write access there
# is no input injection at all, so this is the step that actually matters.

if [[ -e /dev/uinput ]]; then
    ok "/dev/uinput exists"
else
    miss "/dev/uinput missing - the uinput kernel module is not loaded"
    confirm_run "load the uinput module now" "modprobe uinput"
    confirm_run "load uinput automatically at boot" \
        "echo uinput > /etc/modules-load.d/uinput.conf"
fi

if [[ -w /dev/uinput ]]; then
    ok "/dev/uinput is writable - injection will work"
else
    miss "/dev/uinput is NOT writable - injection will fail"
    PROBLEMS=$((PROBLEMS + 1))
    if [[ ! -f "$UDEV_RULE" ]]; then
        confirm_run "add a udev rule granting the 'input' group access" \
            "echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\"' > $UDEV_RULE && udevadm control --reload-rules && udevadm trigger"
    else
        ok "udev rule already present at $UDEV_RULE"
    fi
fi

if id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
    ok "user '$USER' is in the 'input' group"
else
    miss "user '$USER' is NOT in the 'input' group"
    PROBLEMS=$((PROBLEMS + 1))
    confirm_run "add $USER to the 'input' group" "usermod -aG input $USER"
    note "You must log out and back in for a new group to take effect."
fi

# Global F6/F7 hotkeys need to READ the real keyboards.
READABLE=0
TOTAL=0
for dev in /dev/input/event*; do
    [[ -e "$dev" ]] || continue
    TOTAL=$((TOTAL + 1))
    [[ -r "$dev" ]] && READABLE=$((READABLE + 1))
done
if (( TOTAL > 0 && READABLE == TOTAL )); then
    ok "all $TOTAL input devices readable - global F6/F7 hotkeys will work"
elif (( READABLE > 0 )); then
    miss "only $READABLE of $TOTAL input devices readable - hotkeys may miss some keyboards"
else
    miss "no input devices readable - global F6/F7 hotkeys will NOT work"
    note "This is the same 'input' group membership as above."
fi

# ------------------------------------------------------------- 5. menu entry
section "5. Application menu entry"

if (( CHECK_ONLY )); then
    if [[ -f "$DESKTOP" ]]; then
        ok "menu entry installed at $DESKTOP"
    else
        miss "menu entry not installed"
    fi
else
    chmod +x "$HERE/run.sh" 2>/dev/null
    mkdir -p "$APPDIR"
    # Generated here rather than copied, so the Exec path is always correct
    # even if the project directory is moved or renamed.
    cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=InFeRiOr's Input Automator
GenericName=Input Automator
Comment=Configurable click and keypress automation with 60 timers
Exec="$HERE/run.sh"
Path=$HERE
Icon=input-mouse
Terminal=false
Categories=Utility;
Keywords=autoclicker;automation;macro;input;
StartupNotify=false
EOF
    if command -v desktop-file-validate >/dev/null 2>&1 \
       && ! desktop-file-validate "$DESKTOP" 2>/dev/null; then
        fail "generated .desktop failed validation"
        PROBLEMS=$((PROBLEMS + 1))
    else
        ok "menu entry installed at $DESKTOP"
    fi
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database "$APPDIR" >/dev/null 2>&1
fi

# ------------------------------------------------------------------ 6. summary
section "Summary"

if (( ${#PENDING[@]} > 0 )); then
    printf "  ${YEL}These privileged steps were not applied:${RST}\n\n"
    for cmd in "${PENDING[@]}"; do
        # ${cmd@Q} re-quotes the command so it survives copy-paste. The udev
        # rule contains single quotes of its own, which would otherwise
        # terminate the wrapper quoting and produce a broken command.
        printf "    sudo bash -c %s\n" "${cmd@Q}"
    done
    printf "\n  Re-run ./install.sh to be prompted again, or paste the above.\n"
fi

if (( PROBLEMS == 0 && ${#PENDING[@]} == 0 )); then
    printf "  ${GRN}Everything is set up.${RST}\n\n"
    printf "  Start it with:  ${B}./run.sh${RST}\n"
    printf "  or from the application menu: \"InFeRiOr's Input Automator\"\n"
    printf "  Hotkeys: F6 = Start/Stop, F7 = Exit\n"
else
    printf "\n  ${YEL}Setup is incomplete.${RST} Re-check at any time with:\n"
    printf "    ./install.sh --check\n"
fi
printf "\n"

exit 0
