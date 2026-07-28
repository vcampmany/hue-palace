import argparse
import asyncio
import contextlib
import logging

import evdev
from aiohue import HueBridgeV2
from evdev import categorize, ecodes

from controls import discover_devices, discover_lights, discover_scenes


async def main(args):
    """Run Main execution."""
    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)-15s %(levelname)-5s %(name)s -- %(message)s",
        )

    async with HueBridgeV2(args.host, args.appkey) as bridge:
        logging.info(f"Connected to bridge at {args.host}, bridge id {bridge.bridge_id}")
        devices = await discover_devices(bridge)
        lights = await discover_lights(bridge)
        scenes = await discover_scenes(bridge)
        print(devices)
        print(lights)
        print(scenes)

        print("found devices:")
        light = None
        for item in bridge.devices:
            print(item.metadata.name)
            print(item.id)
            if item.metadata.name == "Blob":
                light = list(item.lights)[0]

        cycle_scene_id = None
        for scene in bridge.scenes:
            print(scene.metadata.name)
            print(scene.id)
            if scene.metadata.name == "cycle":
                cycle_scene_id = scene.id


        await bridge.lights.turn_off(light, 1)


LIST_OF_KBS = [evdev.InputDevice('/dev/input/by-id/usb-Dell_Dell_Universal_Receiver-event-kbd')]

async def print_events(device, args):
    async for event in device.async_read_loop():
        # print(device.path, evdev.categorize(event), sep=': ')
        if event.type == ecodes.EV_KEY:
            key = categorize(event)
            if key.keystate == key.key_down:
                if key.keycode == 'KEY_K':
                    await turn_light_on(args.host, args.appkey)
                print(f"[{device}] {key.keycode}")

async def create_kb_listener(args):
    for kb in LIST_OF_KBS:
        asyncio.create_task(print_events(kb, args))

    await asyncio.Event().wait()  # run forever


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="hue-palace")
    parser.add_argument("host", help="hostname of Hue bridge")
    parser.add_argument("appkey", help="appkey for Hue bridge")
    parser.add_argument("--debug", help="enable debug logging", action="store_true")

    with contextlib.suppress(KeyboardInterrupt):
        # asyncio.run(create_kb_listener(parser.parse_args()))
        asyncio.run(main(parser.parse_args()))
