from aiohue import HueBridgeV2
from aiohue.v2.models.scene import Scene
from aiohue.v2.models.smart_scene import SmartScene
from aiohue.v2.controllers.scenes import ScenesController


async def discover_devices(bridge: HueBridgeV2) -> list[tuple[str, str]]:
    devices: list[tuple[str, str]] = []
    for item in bridge.devices:
        devices.append((item.metadata.name, item.id))
    return devices

async def discover_lights_per_device(bridge: HueBridgeV2) -> dict[str, list[str]]:
    lights: dict[str, list[str]] = {}
    for item in bridge.lights:
        lights[item.owner.rid] = list(item.id)
    return lights

async def discover_scenes(host: str, appkey: str) -> dict[str, Scene | SmartScene]:
    async with HueBridgeV2(host, appkey) as bridge:
        scenes: dict[str, Scene | SmartScene] = {}
        for item in bridge.scenes:
            scenes[item.metadata.name] = item
        return scenes

async def turn_light_on(host: str, appkey: str, light_id: str):
    async with HueBridgeV2(host, appkey) as bridge:
        await bridge.lights.turn_on(light_id, 1)

async def toggle_light_state(host: str, appkey: str, light_id: str):
    async with HueBridgeV2(host, appkey) as bridge:
        if bridge.lights.get(light_id).is_on:
            await bridge.lights.turn_off(light_id, 1)
        else:
            await bridge.lights.turn_on(light_id, 1)

async def activate_scene(host: str, appkey: str, scene_id: str):
    async with HueBridgeV2(host, appkey) as bridge:
        await bridge.scenes.recall(scene_id)

#
# async def is_light_on(host: str, appkey: str, light_id: str):
#     async with HueBridgeV2(host, appkey) as bridge:
#         return await bridge.lights.get_light(light_id).is_on()