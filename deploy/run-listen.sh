#!/usr/bin/env bash
# Launch the attendance bot's listen mode under a virtual display.
#
# The SVKM portal needs a *visible* browser (headless Chromium fails to render
# its WebDynpro app), so on a headless server we wrap the process in xvfb, which
# provides an in-memory X display. The bot idles cheaply polling Telegram and
# only launches Chromium (into this display) when you send /check.
set -euo pipefail

# Resolve the project root (this script lives in <root>/deploy/).
cd "$(dirname "$(readlink -f "$0")")/.."

# uv is usually installed to one of these; adjust if `which uv` differs.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"

exec xvfb-run -a --server-args="-screen 0 1280x1024x24" \
    uv run python -m attendance listen
