# Quest2Wiimote

Use Meta Quest 2 Touch controllers as Wii Remotes for [Dolphin](https://dolphin-emu.org/) on Windows. The right controller becomes a Wii Remote with calibrated virtual IR pointing; the left becomes its Nunchuk. Hand tracking works as a second input mode, and game rumble comes back to the controllers.

The headset sits off your head on a shelf, facing your controllers. No SteamVR, no Meta Quest Link, no Android Studio, no Unity.

## How it works

- A native C++ Quest app (OpenXR) streams controller poses over a paired TCP stream tunneled through ADB — USB cable first, wireless after.
- The Windows tracking station (native Qt/OpenGL app; its Python/Qt runtime ships inside the EXE folder) converts the poses into Dolphin **DSU** motion at up to 100 Hz and sends two simulated sensor-bar lights into Dolphin's **Point (Passthrough)** input, giving 15-bit virtual IR precision.
- Dolphin sees a normal Wiimote + Nunchuk over UDP 26760. Rumble loopback uses UDP 26762; the telemetry tunnel uses TCP 26761.

## Requirements

- Windows 10/11 x64
- Meta Quest 2 (or similar) with Developer Mode enabled and a USB data cable for initial pairing
- Android `adb` on your PATH (the release EXE does not bundle platform-tools; a dev checkout bundles it under `tools/adb`)
- Your own [Dolphin](https://dolphin-emu.org/download/) installation
- Optional, for rumble: Visual Studio 2022 C compiler + Windows 10 SDK (see [Rumble](#rumble))

## Install (Windows release)

1. Download `Quest2Wiimote-windows-x64.zip` from the releases page and extract the `Quest2Wiimote` folder anywhere you will keep it. The app reads configuration from `data/` next to the EXE.
2. Edit `data\desktop.json` next to the EXE:
   - `dolphin_dir` — folder containing `Dolphin.exe`
   - `dolphin_user` — the Dolphin user folder containing `Config/` (usually `%LOCALAPPDATA%\Dolphin Emulator`)
3. Run `Quest2Wiimote.exe`. Windows SmartScreen may flag the unsigned build: **More info → Run**.
4. Launching a second copy only raises the existing window. Closing the window stops the tracking station.

## First run

1. Start the EXE. Power on the Quest, connect the **USB data cable**, and accept **Allow USB debugging** on the headset (Developer Mode must be on). Select the authorized device in the sidebar.
2. Click **Install & start**. This installs the bundled APK, pairs it with this desktop, reverses TCP 26761 through ADB, and requests off-head keep-awake. Later launches can use **Start existing app**.
3. Rest the headset securely on a surface with all four external cameras unobstructed, facing your controller play area. Wake both controllers and keep the Quest app focused.
4. Calibrate the virtual screen (right panel): enter your display size and the distance from the right controller to the screen, then choose **Calibrate by aiming at center** — aim at the center of the transparent guides that appear over your monitor, hold steady, release, and pull the right trigger once. Alternatively choose **Capture three physical corners** if all three top corners are reachable.
5. Click **Open configured Dolphin**. The app backs up your Dolphin configuration, installs the Quest profiles into `<dolphin_user>\Config\Profiles\Wiimote`, enables DSU Client, and launches Dolphin with matching graphics aspect overrides. Unrelated settings and saves are preserved.
6. Load a game. The right controller is the Wii Remote; the left is its Nunchuk.

## Configuration

All runtime state lives next to the EXE under `data/` (repo root when running from source):

| File | Purpose |
| --- | --- |
| `desktop.json` | `dolphin_dir`, `dolphin_user`, `passthrough`, `hand_tracking`, `stick_deadzones` |
| `screen.json` | Saved virtual-screen calibration, in fixed workspace coordinates |
| `view.json` | Saved 3D view orbit/pan/zoom |
| `dolphin-config-backups/` | Backup of your Dolphin `Config/` before profiles were first applied |

The pairing token is a random local value written to `work/pairing-token.txt` on first pairing — do not share or commit it.

Network services: DSU UDP **26760**, telemetry TCP **26761** (ADB tunnel; direct-LAN mode is plaintext and trusted-LAN only), rumble UDP **26762**.

## Button mapping

| Touch control | Wii input |
| --- | --- |
| Right B | A |
| Right trigger | B |
| Right A | 1 |
| Right grip | 2 |
| Right stick click | + |
| Right stick directions | D-pad |
| Left menu | − |
| Left Y | Home |
| Left grip / trigger | Nunchuk C / Z |
| Left stick | Nunchuk stick |
| Left stick click | Recenter gyro pointing / aim at screen center |

All Quest profiles explicitly set **Point → Vertical Offset = 10 cm**. For two players, load **Quest - Right Wiimote** for Wii Remote 1 and **Quest - Left Wiimote** for Wii Remote 2. Profiles are generated from `desktop/profiles.py` and installed into the configured Dolphin user folder; reapplying them preserves your existing button, D-pad, hotkey and Nunchuk bindings.

## Hand tracking

Enable **Hand Tracking** next to **Use Wiimote/Nunchuk Models**, install the APK once with **Install & start**, enable hand tracking in Quest settings, and put the controllers down.

| Gesture | Right hand | Left hand |
| --- | --- | --- |
| Thumb + index pinch | A | C |
| Thumb + middle pinch | B | Z |
| Thumb + ring pinch | 1 | — |
| Thumb + pinky pinch | 2 | — |
| Thumbs up | + | — |
| Closed fist, moved up/down/left/right | D-pad | D-pad |

Buttons stay pressed while the gesture is held. Closing a fist sets its neutral position; move about 4 cm to press a direction, return within 2.5 cm to release, or open the hand to reset. Fists take priority over pinches. Both hands use OpenXR joint tracking and the runtime's hand aim pose; keep your hands visible to the cameras.

## Rumble

Game-triggered Wii Remote rumble is sent back to the matching Touch controller. Stock Dolphin DSU has no output channel, so the app can install a small app-local `xinput1_4.dll` adapter beside your `Dolphin.exe`: XInput slots 2 and 3 become neutral output devices for Quest right and left, while slots 0 and 1 still forward to Windows XInput. No Windows driver or system DLL is changed — delete the file next to `Dolphin.exe` to remove it.

The release zip does not include the prebuilt adapter. To build it on your machine:

```
python tools/bootstrap.py
python tools/build_rumble.py
```

without the adapter everything else works; rumble simply stays silent.

## Motion and limits

- Acceleration is **estimated**, not raw IMU data: tracked linear velocity is differentiated (35 ms low-pass, ±8 g clamp), gravity is added, and the result is mapped into the controller frame. This supports tilt, swing and shake approximately; optical smoothing and differentiation affect sharp movements.
- Gyro pointing is integrated by Dolphin, can drift, and needs recentering (left stick click).
- Off-head keep-awake uses Meta's documented developer ADB broadcasts. Whether the runtime keeps the session focused while unworn depends on firmware and does not survive a reboot; if ignored, use Meta Quest Developer Hub's proximity-sensor option.
- When a controller is dropped or loses tracking, the desktop holds its last tracked pose (20 s for the remote, 15 s for a lone Nunchuk) so the pointer does not jump.
- The left controller disconnects after 15 s without physical movement; any button press reconnects it.

## Wireless ADB

With an authorized USB connection, click **Connect wireless ADB**: the app reads the headset's IP, enables ADB TCP on port 5555 using the existing debugging authorization, and the motion stream then flows over Wi-Fi. No inbound Windows firewall rule is needed. Unplug the cable when it succeeds; **Disable wireless ADB** (or a headset reboot) restores USB mode. A changed Wi-Fi IP requires re-pairing over USB.

## Build from source

```
python tools/bootstrap.py      # download NDK, build tools, JDK, OpenXR, scrcpy into tools/
python tools/build.py          # compile + sign the Quest APK  -> dist/Quest2Wiimote.apk
python tools/build_rumble.py   # optional rumble adapter (VS C compiler + Win 10 SDK)
python tools/build_desktop.py  # PyInstaller onedir  -> dist/Quest2Wiimote/
python -m unittest discover -s tests
```

`bootstrap.py` downloads portable toolchains under `tools/` (no Gradle/Maven caches, no system installs). `build.py` compiles arm64 native code with the NDK, packages with aapt2, and signs with a local development keystore. `build_desktop.py` uses an isolated venv at `tools/desktop-venv`; dependency versions are pinned in `tools/desktop-requirements.lock.txt`. Keep the built EXE beside its `_internal` folder.

## Project layout

- `desktop/` — Windows tracking station (Python, native Qt/OpenGL): `native.py` UI, `preview.py` renderer, `screen.py` calibration/ray math, `motion.py` pose→DSU conversion, `dsu.py` Dolphin DSU client, `server.py` telemetry/ADB, `profiles.py` Dolphin profile generation
- `quest/` — native C++ Quest sender (OpenXR)
- `tests/` — unittest suite
- `releases/Quest2Wiimote/` — built Windows release (EXE + runtime + APK)
- `assets/` — controller meshes (Valve SteamVR content), motion sensor and headset models

## Sources

- [Dolphin DSU implementation and input conventions](https://github.com/dolphin-emu/dolphin/blob/master/Source/Core/InputCommon/ControllerInterface/DualShockUDPClient/DualShockUDPClient.cpp)
- [Cemuhook DSU protocol](https://github.com/v1993/cemuhook-protocol)
- [Meta off-head ADB testing](https://developers.meta.com/vr/documentation/unity/meta-xr-operator/quest/)
- [Meta Passthrough Camera API device support](https://developers.meta.com/vr/documentation/unity/unity-pca-overview/)
- [Khronos hand tracking](https://registry.khronos.org/OpenXR/specs/1.0/man/html/XR_EXT_hand_tracking.html)
- [Khronos OpenXR SDK](https://github.com/KhronosGroup/OpenXR-SDK)
- [scrcpy](https://github.com/Genymobile/scrcpy)
- [Official Dolphin downloads](https://dolphin-emu.org/download/)

## License

MIT — see `LICENSE`. Bundled third-party components retain their own licenses (Three.js MIT, OpenXR loader/headers and scrcpy Apache-2.0, Dolphin GPL-2.0-or-later, controller models from Valve's SteamVR content).
