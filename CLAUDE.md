# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`hue-palace`: an asyncio Python service, run as a boot-time service on a Raspberry Pi Zero 2 W,
that turns Bluetooth/USB keyboards into Philips Hue remotes. Key maps come from a YAML config,
and a read-only web status page shows each keyboard's state. User-facing docs, including Pi and
Bluetooth setup, are in `README.md`.

## Commands

```
.venv/bin/pip install -e '.[dev]'                     # deps are declared in pyproject.toml
.venv/bin/pytest                                      # all tests
.venv/bin/pytest tests/test_hue.py::test_toggle_light # one test
.venv/bin/hue-palace --debug run -c config.yaml       # run locally (--debug goes before the subcommand)
.venv/bin/hue-palace list-devices --watch             # see device names/MACs and key names
```

Tests use fakes for the bridge and for evdev devices, so they need neither hardware nor a bridge.
`asyncio_mode = "auto"` means async tests need no decorator.

## Architecture (`src/hue_palace/`)

Everything runs in one event loop started by `__main__._run`: the Hue controller, the keyboard
manager, the web server and the state publisher are sibling tasks. If any one crashes the
process exits non-zero, and systemd/Docker restarts it.

- `config.py`: pydantic models (frozen, `extra="forbid"`). `parse_config` turns
  `ValidationError` into `ConfigError` with readable paths. Key names are checked against
  `evdev.ecodes`. YAML's bare `on`/`off` booleans are coerced back to action names.
  `HUE_APP_KEY` / `HUE_BRIDGE_HOST` env vars override the file.
- `keyboards.py`: `KeyboardManager` polls `evdev.list_devices()` every 2 s, matches each new
  node to a `KeyboardConfig` by MAC (`uniq`) or name, grabs it and starts a reader task. One
  logical keyboard can have several event nodes. Paths are never trusted across reconnects:
  `_detach` drops the path from `_known` so a reused `eventN` is inspected again. `close()`
  releases devices whose reader tasks were cancelled before they started.
- `hue.py`: `resolve()` / `execute()` are pure functions over a bridge-like object (which is
  what the tests fake). `HueController` holds the single `HueBridgeV2`, retries the initial
  connect with backoff (aiohue's event stream reconnects itself after that), and runs actions
  through a bounded queue, one at a time. Room/zone actions go through the group's
  `grouped_light`. Names are re-resolved on every press, so renames in the Hue app take effect
  without a restart.
- `state.py`: `AppState` is the single source for the UI. Mutate it, then call `changed()`.
  `run_publisher` coalesces changes into `status` snapshots; `record_key` also publishes an
  immediate `key` event.
- `web.py` + `static/index.html`: aiohttp (already a dependency of aiohue) serves `/`,
  `/api/status` and `/api/events` (SSE). The page is a single vanilla-JS file with no build step.

## Deployment

`Dockerfile` + `compose.yaml` (host network, `/dev/input` bind-mounted, `device_cgroup_rules`
for input major 13), built for `linux/arm64` by `deploy/push-to-pi.sh`. Alternatively
`deploy/install-systemd.sh` + `deploy/hue-palace.service`. Bluetooth pairing is done on the
host with `bluetoothctl`, never in the container.

## Secrets

The bridge app key must not be committed: it lives in `config/appkey` or
`/etc/hue-palace/appkey`, or in `HUE_APP_KEY`. `config.yaml`, `config/` and `appkey` are
git-ignored. Only `config.example.yaml` is tracked.
