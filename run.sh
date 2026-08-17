#!/usr/bin/env bash
# Launcher for InFeRiOr's Input Automator (Linux port).
# Resolves its own location, so it works from any working directory and from
# the Cinnamon menu.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"

if [[ ! -x "$VENV/bin/python" ]]; then
    echo "Virtualenv missing. Creating it at $VENV ..." >&2
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$HERE/requirements.txt"
fi

# The app needs write access to /dev/uinput to create its virtual devices.
if [[ ! -w /dev/uinput ]]; then
    cat >&2 <<'EOF'
WARNING: /dev/uinput is not writable, so input injection will fail.

Fix with:
  sudo modprobe uinput
  sudo usermod -aG input "$USER"
  echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' \
      | sudo tee /etc/udev/rules.d/99-uinput.rules
  sudo udevadm control --reload-rules && sudo udevadm trigger
Then log out and back in.
EOF
fi

exec "$VENV/bin/python" "$HERE/inferiors_input_automator.py" "$@"
