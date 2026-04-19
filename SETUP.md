# TetherLink — Setup Guide

## Python Server Setup

### Requirements
- Python 3.8 or newer
- pip

### Install dependencies

```bash
cd server
python -m venv .venv

# Activate the virtual environment
# Linux / macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

pip install -r requirements.txt
```

### Run the server

```bash
# Linux / macOS
python tetherlink_server.py

# Windows (uses mss for faster capture)
python tetherlink_server_windows.py
```

You should see:
```
12:00:00 [INFO] TetherLink server listening on 0.0.0.0:8080
12:00:00 [INFO] Connect your Android tablet via USB Tethering, then open the app.
```

---

## Android Studio Setup

1. Open Android Studio (Hedgehog 2023.1.1 or newer recommended).
2. Choose **File → Open** and select the `android/` folder.
3. Wait for the initial Gradle sync to complete.
4. If prompted to update AGP, **decline** — versions are pinned intentionally.
5. Connect your tablet via USB and enable **USB Debugging** in Developer Options.
6. Select your device from the run target dropdown and press **Run ▶**.

---

## Finding Your PC's USB Tethering IP

When your Android device has USB Tethering enabled, it creates a virtual network adapter on your PC. The PC's address on that adapter is typically `192.168.42.129`.

### Linux
```bash
ip addr show | grep "192.168.42"
# Look for: inet 192.168.42.129/...
```

### macOS
```bash
ifconfig | grep "192.168.42"
```

### Windows (Command Prompt)
```
ipconfig
# Look for an adapter named "Remote NDIS" or "USB Ethernet"
# with an IPv4 Address of 192.168.42.xxx
```

---

## Updating the Server IP in the App

Open:
```
android/app/src/main/java/com/tetherlink/MainActivity.kt
```

Change the `SERVER_IP` constant:
```kotlin
private val SERVER_IP = "192.168.42.129"  // ← replace with your PC's IP
```

Rebuild and redeploy the app.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| App shows "Connection error" | Wrong IP or server not running | Verify IP with `ip addr`; ensure server is started |
| Black screen on tablet | Connected but no frames sent | Check Python console for errors |
| Slow / choppy stream | USB cable quality, CPU load | Use a USB 3.0 cable; lower `JPEG_QUALITY` in server |
| Gradle sync fails | Android Studio version mismatch | Use Android Studio Hedgehog 2023.1.1+ |
| `ModuleNotFoundError: PIL` | venv not activated | Run `source .venv/bin/activate` first |
| Port 8080 already in use | Another service is using the port | Kill it with `lsof -i :8080` then `kill <PID>` |

---

## Firewall Notes

Make sure port **8080 TCP** is allowed outbound on your PC's firewall for the USB Tethering network adapter. On Windows, you may need to add a Windows Defender Firewall inbound rule for port 8080.

---

## Packaging for Release

### Windows (.exe)
To create a standalone Windows executable with the application icon:
1. Install PyInstaller: `pip install pyinstaller`
2. Run the build: `pyinstaller tetherlink.spec`
3. The executable will be in the `dist/TetherLink` folder.

### Linux (.deb)
To package for Debian/Ubuntu (requires `stdeb`):
1. Install stdeb: `pip install stdeb`
2. Run: `python setup.py --command-packages=stdeb.command bdist_deb`
   *(Ensure you have a `setup.py` configured with metadata)*

Alternatively, you can manually layout the files:
- Executable: `/usr/bin/tetherlink` -> `server/tetherlink_server.py`
- Icon: `/usr/share/icons/hicolor/512x512/apps/tetherlink.png`
- Desktop entry: `/usr/share/applications/tetherlink.desktop`
