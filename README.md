# hue-palace

Turn Bluetooth (or USB) keyboards, and BLE broadcast buttons such as the Shelly BLU RC Button 4, into Philips Hue remotes. Built to run headless on a
Raspberry Pi Zero 2 W: it starts at boot, listens to several keyboards, each with its own
key map, and serves a status page showing what every keyboard is doing.

- **Actions per key:** recall a scene; turn a light, room or zone on, off or toggle it; step brightness up or down.
- **Keyboards are matched by Bluetooth MAC or name**, not by `/dev/input` path, so they keep
  working after sleeping, reconnecting or getting a new event number.
- **Exclusive grab:** key presses don't leak into the Pi's console.
- **Status page** (`http://<pi>:8080`): bridge connection, each keyboard's connection and key
  map (rows flash live on press), config problems such as a misspelled room, light and room
  states, an activity log, and unconfigured input devices with their name and MAC.
- **One persistent bridge connection** with automatic reconnect. Commands are queued and sent
  one at a time so the bridge's rate limit isn't hit.

## Configuration

Copy `config.example.yaml` and edit it; it documents every option. In short:

```yaml
bridge:
  host: 192.168.1.10
  app_key_file: /config/appkey
keyboards:
  - name: Living room remote
    match: { mac: "AA:BB:CC:DD:EE:FF" }
    keys:
      KEY_Q:  { action: scene, scene: Sunset, room: Living room }
      KEY_W:  { action: toggle, light: Wheel }
      KEY_UP: { action: brightness_up, room: Living room, step: 15 }
```

Helper commands:

| Command | What it does |
|---|---|
| `hue-palace pair <bridge-ip> [--save FILE]` | Get an app key (press the bridge's link button first) |
| `hue-palace list-devices` | List input devices with their name and MAC, to fill in `match:` |
| `hue-palace list-devices --watch` | Print each key's name as you press it, to fill in `keys:` |
| `hue-palace list-ble` | List BTHome BLE buttons with their MAC and press events, to fill in `ble_buttons:` |
| `hue-palace run -c config.yaml` | Run the service (the default command) |

The config is read once at startup, so restart the service after editing it.

## Raspberry Pi setup

1. Flash **Raspberry Pi OS Lite (64-bit)**. In Raspberry Pi Imager, set Wi-Fi, a hostname
   (e.g. `hue-palace`) and SSH.
2. Pair each keyboard once, on the host (not in Docker):
   ```
   sudo bluetoothctl
   scan on                 # put the keyboard in pairing mode, wait for its MAC
   pair AA:BB:CC:DD:EE:FF  # type the PIN on the keyboard if asked
   trust AA:BB:CC:DD:EE:FF # lets it reconnect by itself after sleeping or a reboot
   connect AA:BB:CC:DD:EE:FF
   ```
   The keyboard then appears as `/dev/input/eventN`, and `hue-palace list-devices` shows its MAC.
3. Deploy with one of the two options below.

### Option A: Docker (recommended if you already use Docker)

The Zero 2 W runs Docker fine. The daemon costs about 60–100 MB of the 512 MB RAM, which still
leaves plenty for this app (around 40 MB). Bluetooth stays on the host. The container only gets
`/dev/input`, with a cgroup rule so keyboards that connect later are visible too.

```
# on the Pi, once
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER

# on your computer: build for arm64 and ship it (building on the Zero itself works but is slow)
deploy/push-to-pi.sh pi@hue-palace.local

# on the Pi
cd ~/hue-palace
cp <repo>/config.example.yaml config/config.yaml   # then edit it
docker run --rm -it --network host -v ./config:/config hue-palace pair <bridge-ip> --save /config/appkey
docker run --rm -it -v /dev/input:/dev/input --privileged hue-palace list-devices --watch
docker compose up -d --no-build
```

`restart: unless-stopped` plus the Docker service starting at boot brings it back after every
reboot. Check logs with `docker compose logs -f`.

### Option B: systemd + virtualenv (lighter)

No Docker daemon and no device passthrough. Updating means `git pull` and re-running the script.

```
git clone <repo> ~/hue-palace && cd ~/hue-palace
sudo deploy/install-systemd.sh               # venv in /opt/hue-palace, config in /etc/hue-palace
sudo hue-palace pair <bridge-ip> --save /etc/hue-palace/appkey
sudo chgrp hue-palace /etc/hue-palace/appkey && sudo chmod 640 /etc/hue-palace/appkey
sudo nano /etc/hue-palace/config.yaml
sudo systemctl restart hue-palace && journalctl -u hue-palace -f
```

#### Managing the service

| Task | Command |
|---|---|
| Is it running? Recent log lines | `systemctl status hue-palace` |
| Follow the logs live | `journalctl -u hue-palace -f` |
| Logs since the last boot | `journalctl -u hue-palace -b` |
| Apply config changes | `sudo systemctl restart hue-palace` |
| Stop it / start it | `sudo systemctl stop hue-palace` / `start` |
| Don't start at boot any more | `sudo systemctl disable hue-palace` |
| Show the unit file systemd is using | `systemctl cat hue-palace` |
| Rate the sandboxing | `systemd-analyze security hue-palace` |
| Update to a new version | `git pull && sudo deploy/install-systemd.sh` |

### Which to choose?

Both are fully supported, and the app doesn't depend on either. Docker gives you a
self-contained image and an easy rollback. systemd uses less RAM and SD-card I/O and has fewer
moving parts on a 512 MB board. Either way, consider enabling the **overlay (read-only) file
system** in `raspi-config → Performance` once everything works, so power cuts can't corrupt the
SD card.

### BLE buttons (Shelly BLU RC Button 4 and other BTHome devices)

Some battery buttons don't connect like a keyboard. Instead they **broadcast** each press as
a Bluetooth LE advertisement in the [BTHome v2](https://bthome.io) format; Shelly's BLU
buttons are an example. hue-palace scans for those broadcasts and maps
*button × press type* to actions.

1. **Use Bluetooth mode, and don't pair it.** The *ZB* model also speaks Zigbee: pressing a
   button 5 times starts Zigbee pairing, so avoid that. Nothing needs pairing in `bluetoothctl`;
   the Pi just listens.
2. **Find its MAC and event names.** Stop the service first, then run
   `sudo hue-palace list-ble` and press each button in each way. It prints lines such as
   `7C:C6:B6:61:2E:9A: button2:double_press`. Unconfigured BTHome devices also appear on the
   status page under **Other BTHome devices**.
3. **Add it to the config:**
   ```yaml
   ble_buttons:
     - name: Bedroom button
       mac: "7C:C6:B6:61:2E:9A"
       buttons:
         1:
           press:        { action: toggle, room: Bedroom }
           double_press: { action: scene, scene: Relax, room: Bedroom }
           long_press:   { action: off, zone: Upstairs }
         2:
           press:        { action: brightness_up, room: Bedroom, step: 20 }
   ```
   Press types: `press`, `double_press`, `triple_press`, `long_press`, `long_double_press`,
   `long_triple_press`, `hold_press`, `hold_release`. Which of these a device sends depends on
   the device.
4. Restart the service. The button gets a card on the status page showing last heard, signal
   strength, battery and its mappings.

**Encryption (optional).** Encryption is off by default, which means anyone within Bluetooth
range could fake a press. To turn it on, enable encryption for the button in the **Shelly
Smart Control** app and copy the key into the config as `bindkey: "<32 hex characters>"`.
hue-palace then rejects packets that fail to decrypt or that replay an older counter. Changing
the passkey generates a new key, and turning encryption off again requires a factory reset of
the button.

**How it runs.** The BLE scan only starts when `ble_buttons` is configured. It goes through
bluetoothd, so it needs D-Bus access: the systemd unit adds the `bluetooth` group, and
`compose.yaml` mounts `/run/dbus`. Each press is repeated on air for about a second, and
duplicates are filtered, so one press triggers one action.

### Bluetooth notes

- The Zero 2 W has 2.4 GHz-only Wi-Fi on the same radio as Bluetooth. A few keyboards are
  fine, but if presses lag, improve the Wi-Fi signal or move the router to a quieter channel.
- BLE button scanning runs continuously on that same radio. If keyboards or Wi-Fi get
  laggy after adding BLE buttons, this is the first thing to suspect.
- Keyboards that sleep reconnect when you press a key. That first key press may be lost while
  the link comes back up.

## Development

```
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/hue-palace --debug run -c config.yaml
```
