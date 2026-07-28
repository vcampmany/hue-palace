from aiohue import HueBridgeV2

async def discover_devices(bridge: HueBridgeV2) -> list[tuple[str, str]]:
    devices: list[tuple[str, str]] = []
    for item in bridge.devices:
        devices.append((item.metadata.name, item.id))
    return devices

async def discover_lights(bridge: HueBridgeV2) -> list[str]:
    lights: list[str] = []
    for item in bridge.lights:
        lights.append(item.id)
    return lights

async def discover_scenes(bridge: HueBridgeV2) -> dict[str, str]:
    scenes: dict[str, str] = {}
    for item in bridge.scenes:
        scenes[item.id] = item.metadata.name
    return scenes

async def turn_light_on(host: str, appkey: str, light_id: str):
    async with HueBridgeV2(host, appkey) as bridge:
        await bridge.lights.turn_on(light_id, 1)

async def turn_light_off(host: str, appkey: str, light_id: str):
    async with HueBridgeV2(host, appkey) as bridge:
        await bridge.lights.turn_off(light_id, 1)