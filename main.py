import argparse
import asyncio
import contextlib
import logging

import aiohue
import evdev
from aiohue import HueBridgeV2
from evdev import categorize, ecodes

from controls import discover_devices, discover_lights_per_device, discover_scenes
import controls


# async def light_id_to_name(host: str, app_key: str):
#     async with HueBridgeV2(host, app_key) as bridge:
#         devices = await discover_devices(bridge)
#         aa = await discover_lights_per_device(bridge)
#         name_to_id: dict[str, str] = {}
#         for item in devices:
#             name_to_id[item[0]] = list(item[1].lights)[0]
#
#         return name_to_id


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
        # lights = await discover_lights(bridge)
        # scenes = await discover_scenes(bridge)
        aiohue.v2.ScenesController() ['cycle'].recall()
        print("deviceeeeeeeeeeeeeeeeees")
        print(devices)
        print("lightsssssssssssssssssssss")
        # print(lights)
        print(scenes)

        print("found devices:")
        light = None
        for item in bridge.devices:
            print(item.metadata.name)
            print(item.id)
            if item.metadata.name == "Wheel":
                light = list(item.lights)[0]

        cycle_scene_id = None
        for scene in bridge.scenes:
            print(scene.metadata.name)
            print(scene.id)
            if scene.metadata.name == "cycle":
                cycle_scene_id = scene.id


        await bridge.lights.turn_on(light, 1)


LIST_OF_KBS = [evdev.InputDevice('/dev/input/by-id/usb-Dell_Dell_Universal_Receiver-event-kbd'),
               evdev.InputDevice('/dev/input/by-id/usb-PCsensor_MK321BT-event-kbd')]

async def print_events(device, args):
    async for event in device.async_read_loop():
        # print(device.path, evdev.categorize(event), sep=': ')
        if event.type == ecodes.EV_KEY:
            key = categorize(event)
            if key.keystate == key.key_down:
                if key.keycode == 'KEY_Q':
                    # await controls.toggle_light_state(HOST, APPKEY, "ff697577-f010-43a4-b14d-18c9e76408fc")
                    scenes = await discover_scenes(HOST, APPKEY)
                    await controls.activate_scene(HOST, APPKEY, scenes['Sunsetqweqweq'].id)
                elif key.keycode == 'KEY_W':
                    scenes = await discover_scenes(HOST, APPKEY)
                    await controls.activate_scene(HOST, APPKEY, scenes['cycle'].id)
                elif key.keycode == 'KEY_E':
                    scenes = await discover_scenes(HOST, APPKEY)
                    await controls.activate_scene(HOST, APPKEY, scenes['Workshop'].id)

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

    args = parser.parse_args()
    HOST = args.host
    APPKEY = args.appkey

    # DEVICE_TO_ID = asyncio.run(light_id_to_name(HOST, APPKEY))
    # scenes = asyncio.run(discover_scenes(HOST, APPKEY))

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(create_kb_listener(args))
        # asyncio.run(main(args))