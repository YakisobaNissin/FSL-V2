# FSL-V2

A local webcam app for Filipino Sign Language fingerspelling (A–Z) and 20 conversational signs.

## Install and run

1. Clone or download the project and extract it to a writable folder. Keep
   `models/` and `export/` alongside the application files.
2. Install [64-bit CPython 3.12](https://www.python.org/downloads/). Connect
   a webcam to the computer that will run the app.
3. Open a terminal in the folder containing `launch_fsl.py`.

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy Bypass -File .\Launch-FSL.ps1
```

The wrapper looks for Python 3.12. For a custom installation, set `$env:FSL_PYTHON312` to its `python.exe` path. You can also run `py -3.12 launch_fsl.py` directly if the Python launcher lists 3.12.

**macOS and Linux**

```sh
python3.12 launch_fsl.py
```

The first run needs internet access to install packages. The launcher
creates `.venv-browser/` and `.venv-letters/` in the project folder and
installs the dependencies for each model. Subsequent runs reuse those
environments. When moving the project to another computer, recreate the
environments there by running the launcher.

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) if it does not open
automatically. Allow camera access for Python or the terminal when
prompted. Press **Ctrl+C** in the terminal to stop the server.

### Platform requirements

The pinned MediaPipe and OpenCV wheels overlap on **Windows x86-64**,
**Apple Silicon on macOS 13+**, and **x86-64 Linux with glibc 2.28+**.
On Linux, install Python's `venv` support if your distribution packages it
separately. Camera access depends on the host operating system and drivers.
[MediaPipe builds](https://pypi.org/project/mediapipe/0.10.35/#files) ·
[OpenCV builds](https://pypi.org/project/opencv-contrib-python/5.0.0.93/#files)

## Host and port

The default address, `127.0.0.1:5000`, is reachable from the computer
running the app. If port 5000 is in use, append `--port 8000` to the launch
command and open `http://127.0.0.1:8000`.

For a demo on a trusted local network, bind the app to the host's IPv4
interfaces.

**Windows**

```powershell
powershell -ExecutionPolicy Bypass -File .\Launch-FSL.ps1 --host 0.0.0.0 --port 8000 --no-browser
```

**macOS and Linux**

```sh
python3.12 launch_fsl.py --host 0.0.0.0 --port 8000 --no-browser
```

On the other device, open `http://HOST_LAN_IP:8000`. Replace
`HOST_LAN_IP` with the IP address of the computer running the app, such
as `192.168.1.25`. Both devices need to be on the same network. If prompted
by the host firewall, allow the chosen TCP port on the private network.

The webcam and recognition session belong to the host computer and are
shared across clients. Use one active browser at a time. The app serves
HTTP without sign-in, so keep LAN mode on a trusted network and leave the
port closed to the public internet.
[Flask development server guidance](https://flask.palletsprojects.com/en/stable/server/)

## Use the app

- **Fingerspelling.** Press **Start detection** and hold one letter steady.
  Lower your hand briefly to enter the same letter twice.
- **Signs.** Press **Record sign** and perform one sign after the countdown.
  Review the prediction and choose **Add sign** or **Record again**. The SVM
  margin measures the score gap between the predicted sign and its closest alternative.

Both modes write to the same editable draft, which is saved in your browser.

| Setting | Windows PowerShell | macOS or Linux |
| --- | --- | --- |
| Use the optional 105-sign bundle in `export/` | `$env:FSL_SIGNS_VOCAB = "full"` | `export FSL_SIGNS_VOCAB=full` |
| Try the next camera | `$env:FSL_CAMERA_INDEX = "1"` | `export FSL_CAMERA_INDEX=1` |

Set those variables before launching. The default model bundles are
`models/fingerspelling/` and `models/conversational/`. They use different
scikit-learn versions, so the launcher creates separate environments.
