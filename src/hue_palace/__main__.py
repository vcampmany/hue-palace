"""Command line entry point: `hue-palace run | list-devices | pair`."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config

log = logging.getLogger("hue_palace")

DEFAULT_CONFIG = os.environ.get("HUE_PALACE_CONFIG", "/etc/hue-palace/config.yaml")


async def _run(config_path: str) -> int:
    from .hue import HueController
    from .keyboards import KeyboardManager
    from .state import AppState
    from .web import serve

    config = load_config(config_path)
    app_key = config.bridge.resolve_app_key()

    state = AppState(config)
    hue = HueController(config.bridge.host, app_key, state)
    keyboards = KeyboardManager(config.keyboards, state, hue.submit)
    log.info(
        "hue-palace %s: %d keyboard(s) configured, bridge %s",
        __version__, len(config.keyboards), config.bridge.host,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(hue.run(), name="hue"),
        asyncio.create_task(keyboards.run(), name="keyboards"),
        asyncio.create_task(serve(state, config.web), name="web"),
        asyncio.create_task(state.run_publisher(), name="publisher"),
    ]
    if config.ble_buttons:  # only touch Bluetooth LE when BLE buttons are configured
        from .ble import BleManager

        ble = BleManager(config.ble_buttons, state, hue.submit, adapter=config.ble.adapter)
        tasks.append(asyncio.create_task(ble.run(), name="ble"))
    stopper = asyncio.create_task(stop.wait(), name="stop")
    done, _ = await asyncio.wait([*tasks, stopper], return_when=asyncio.FIRST_COMPLETED)

    exit_code = 0
    for task in done:
        if task is not stopper and not task.cancelled() and task.exception():
            log.error("task %s crashed", task.get_name(), exc_info=task.exception())
            exit_code = 1  # let systemd / Docker restart us
    for task in [*tasks, stopper]:
        task.cancel()
    await asyncio.gather(*tasks, stopper, return_exceptions=True)
    log.info("stopped")
    return exit_code


def cmd_run(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(_run(args.config))
    except ConfigError as err:
        log.error("%s", err)
        return 2


def cmd_list_devices(args: argparse.Namespace) -> int:
    import evdev
    from evdev import ecodes

    from .keyboards import key_names

    devices = []
    for path in sorted(evdev.list_devices(), key=lambda p: int(p.rsplit("event", 1)[-1] or 0)):
        try:
            devices.append(evdev.InputDevice(path))
        except OSError as err:
            print(f"{path}: cannot open ({err})")
    if not devices:
        print("No readable input devices. Are you root or in the 'input' group?")
        return 1
    for d in devices:
        keys = "keys" if ecodes.EV_KEY in d.capabilities() else "no keys"
        print(f"{d.path:<20} {d.name!r:<45} MAC={d.uniq or '-':<18} {keys}  phys={d.phys or '-'}")

    if not args.watch:
        return 0
    print("\nPress keys (Ctrl+C to stop). Use these names in config.yaml:\n")

    async def watch(device: evdev.InputDevice) -> None:
        with contextlib.suppress(OSError):
            async for event in device.async_read_loop():
                if event.type == ecodes.EV_KEY and event.value == 1:
                    print(f"{device.name!r} [{device.uniq or device.path}]: {' / '.join(key_names(event.code))}")

    async def main() -> None:
        await asyncio.gather(*(watch(d) for d in devices if ecodes.EV_KEY in d.capabilities()))

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
    return 0


def cmd_list_ble(args: argparse.Namespace) -> int:
    from .ble import Deduplicator, make_scanner
    from .bthome import SERVICE_UUID, BTHomeError, parse

    seen: set[str] = set()
    dedup = Deduplicator()

    def on_adv(device, adv) -> None:
        data = adv.service_data.get(SERVICE_UUID)
        if data is None:
            return
        mac = device.address.lower()
        name = adv.local_name or device.name or ""
        try:
            packet = parse(data, mac)
        except BTHomeError as err:
            print(f"{mac}  {name!r}: invalid BTHome packet ({err})")
            return
        if mac not in seen:
            seen.add(mac)
            print(f"found {mac.upper()}  {name!r}  RSSI {adv.rssi} dBm"
                  f"{'  [encrypted]' if packet.encrypted else ''}")
        if packet.encrypted or not dedup.is_new(mac, packet):
            return
        parts = [f"button{n}:{e}" for n, e in packet.events]
        if packet.battery is not None:
            parts.append(f"battery {packet.battery}%")
        if parts:
            print(f"  {mac.upper()}: {', '.join(parts)}")

    async def main() -> None:
        scanner = make_scanner(on_adv, args.adapter)
        await scanner.start()
        try:
            await asyncio.sleep(args.seconds)
        finally:
            await scanner.stop()

    print(f"Listening for BTHome devices for {args.seconds}s; press the buttons now (Ctrl+C to stop)…")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as err:  # noqa: BLE001 - BlueZ / D-Bus errors
        print(f"BLE scan failed: {type(err).__name__}: {err}", file=sys.stderr)
        return 1
    if not seen:
        print("No BTHome devices heard. Is the button in Bluetooth (not Zigbee) mode? Press a button.")
    return 0


def cmd_pair(args: argparse.Namespace) -> int:
    from aiohue import create_app_key

    input(f"Press the link button on the bridge at {args.host}, then press Enter… ")
    try:
        key = asyncio.run(create_app_key(args.host, f"hue-palace#{socket.gethostname()[:19]}"))
    except Exception as err:  # noqa: BLE001
        print(f"Pairing failed: {err}", file=sys.stderr)
        return 1
    if args.save:
        path = Path(args.save)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600, exist_ok=True)
        path.write_text(key + "\n")
        print(f"App key saved to {path}")
    else:
        print(f"App key: {key}")
    print("It does not expire; keep it out of version control.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hue-palace", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="run the service (default)")
    p_run.add_argument("-c", "--config", default=DEFAULT_CONFIG,
                       help=f"config file (default: {DEFAULT_CONFIG}, or $HUE_PALACE_CONFIG)")
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser("list-devices", help="show input devices, to write the config")
    p_list.add_argument("-w", "--watch", action="store_true", help="also print key names as you press them")
    p_list.set_defaults(func=cmd_list_devices)

    p_ble = sub.add_parser("list-ble", help="show BTHome BLE buttons/sensors and their events")
    p_ble.add_argument("-s", "--seconds", type=float, default=30, help="how long to listen (default 30)")
    p_ble.add_argument("--adapter", help="Bluetooth adapter, e.g. hci0")
    p_ble.set_defaults(func=cmd_list_ble)

    p_pair = sub.add_parser("pair", help="get an app key from the bridge (press its link button)")
    p_pair.add_argument("host", help="bridge IP or hostname")
    p_pair.add_argument("--save", metavar="FILE", help="write the key to FILE instead of printing it")
    p_pair.set_defaults(func=cmd_pair)

    args = parser.parse_args(argv)
    if args.command is None:  # `hue-palace` alone == `hue-palace run`
        args = parser.parse_args([*(argv if argv is not None else sys.argv[1:]), "run"])

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if not args.debug:
        logging.getLogger("aiohue").setLevel(logging.WARNING)
        logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
