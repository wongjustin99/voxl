# Benchmarks

Tools used to compare voxl with selkies, KasmVNC, Neko, noVNC and Sunshine. Everything runs on a separate Xvfb display
(`:2` by default, `BENCH_DISPLAY` to change it), so a live desktop isn't disturbed.

## Setup

```sh
cc -O2 -o loadgen loadgen.c -lX11 -lXext      # 60 Hz animation with a frame/time marker
cc -O2 -o rootlat rootlat.c -lX11             # marker latency on the root window
env/xvfb.sh & sleep 1; env/openbox.sh &       # 2560x1600 @ 144 DPI, like production
env/voxl.sh                                   # voxl on :6902 (VOXL_BIN to override)
env/selkies.sh                                # selkies (own Xvfb) :3, port 6912 (SELKIES_IMAGE to override)
BENCH_DISPLAY=:3 env/openbox.sh &
KASMVNC=/path/to/kasmvncserver XKB_DIR=/path/to/xkb env/kasmvnc.sh &  # KasmVNC (own X server) :5, port 6922
sleep 1; BENCH_DISPLAY=:5 env/openbox.sh &
```

`env/selkies.sh` runs selkies and the XLibre Xvfb from selkies' base image, since
selkies reads the screen in place only from its own X server; `docker logs selkies-bench`
is its log. CBR from 30 Mbps, with congestion control down to 2 Mbps.

`KASMVNC` is the monorepo's `nix/packages/kasmvnc` build; `XKB_DIR` is
`xkeyboard_config`'s `share/X11/xkb`. `env/kasmvnc.sh` passes the flags Tembo's
`kasmvnc.yaml` expands to.

`*.mjs` need Playwright (`PLAYWRIGHT_NODE_MODULES`) and Chromium (`CHROMIUM`).
`py.sh` runs Python with PyAV and websockets through nix-shell.

## Measuring

- `nclient.py voxl|selkies host:port secs [x y]`: native client (FFmpeg decode)
  that reads the marker at (x, y). Reports unique FPS, latency and Mbps.
- `kclient.py host:port secs [x y]`: native KasmVNC client. Speaks RFB over its
  WebSocket the way KasmVNC's web client does (same pixel format and encodings,
  including JPEG, WebP and CopyRect, continuous updates and fences), keeps a full
  framebuffer, decodes images in parallel and reads the marker after each update.
  Same JSON as `nclient.py`.
- `nkclient.py host:port secs [x y]`: native Neko client. Logs in and signals over
  Neko's WebSocket API like its web client, receives the WebRTC video with aiortc and
  reads the marker from every decoded frame. Needs aiortc (`pip install aiortc`). Same JSON.
- `suite2.sh`: noVNC, Neko and tuned Neko through the same workloads and links as
  `suite.sh`; `cpusuite2.sh`: their server CPU. Start the servers with
  `env/novnc.sh` (x11vnc + websockify + noVNC, as E2B ships them; `X11VNC`, `NOVNC`,
  `WEBSOCKIFY`), `env/neko.sh` (Neko's official image against the benchmark display;
  `NEKO_SHAPE=mbps/rtt` shapes its UDP media with `tc netem` in its own container
  network) or `env/neko-tuned.sh` (60 FPS x264).
- `mlclient.py host secs [x y]`: native Moonlight client for Sunshine. Streams with
  moonlight-embedded built by `moonlight/` (`nix-build moonlight`; its patch makes
  `-platform fake` hand each frame over as it arrives), decodes with FFmpeg and reads the
  marker. `MOONLIGHT` (the binary), `ML_HOME` (its pairing keys), `ML_BITRATE`. Same JSON.
- `suite3.sh`: Sunshine through the same workloads and links (`CONFIGS`: `sunshine`,
  `sunshine_tuned`; `REPS`), restarting it per run with `env/sunshine.sh` (`SUNSHINE`:
  the binary; `SUNSHINE_SHAPE=mbps/rtt` runs it in its own container network shaped
  with `tc netem`, like Neko). Pair once as `env/sunshine.sh` describes. `cpusuite2.sh`
  measures its CPU with `SERVERS="sunshine sunshine_tuned"`.
- `daytona_bench.py up|run|down`: a Daytona sandbox (default size, 2560x1600 desktop
  with x11vnc + noVNC) with the workloads in it, measured with `kclient.py` through
  Daytona's signed preview proxy (`KC_URL`, `KC_HEADERS`; `KC_CONNECT` to go through
  `shaper.py`). Needs the `daytona` SDK and `DAYTONA_API_KEY`.
- `suite.sh`: every config (`CONFIGS`: selkies with and without picom, voxl and
  KasmVNC with and without picom) x workload
  (Chromium scroll, 1080p and full-screen animation) x link (LAN, 10 Mbps/40 ms,
  5 Mbps/60 ms) via `shaper.py`, a userspace bottleneck proxy.
- `ab.sh <workload> <reps>`: interleaved voxl vs selkies runs.
- `cpusuite.sh`: server CPU (X server + streamer) per workload with one client connected.
- `scroll.html`: the long page `scrolltarget.mjs` smooth-scrolls, with a marker drawn every frame.
- `cpu.sh <pid> <secs>`, `rate.sh <secs>` (voxl `/stats`), `srcfps.py` (true
  source frame rate from the Xvfb framebuffer), `measure.mjs` (in-browser unique FPS; `VIEWPORT=WxH`).
- `inputtest.mjs`, `keytest.sh`: input and clipboard checks through the web client.

## Feature tests

`tests/` drives the web client with Playwright against a running voxl
(`VOXL_PORT`, default 6903, started with `--enable-basic-auth=false`) on a test display
(`BENCH_DISPLAY`, default `:3`). The tests that start their own voxl say so below.
For smooth scrolling, start that display with the patched server:
`XVFB=$(nix-build -E 'with import <nixpkgs> {}; callPackage ../xvfb.nix {}')/bin/Xvfb BENCH_DISPLAY=:3 env/xvfb.sh`.

- `smoothtest.mjs <display> <port>`: precise scroll steps reach Chromium pixel by pixel.
- `featuretest.mjs`: wheel/trackpad scrolling, tap, two-finger scroll, long press,
  touch toolbar (uses `target.html`; `SCROLL_PAGE` for the scroll page).
- `cliptest.mjs`, `cliptext.mjs`: PNG and text clipboard in both directions
  (`TEST_PNG`, needs `xclip`).
- `fstest.mjs`, `fsiframe.mjs`: fullscreen toolbar and Keyboard Lock, top level and embedded.
- `resizetest.mjs`: re-fit in a resizable sidebar iframe and at several tab sizes.
- `filestest.mjs`: file panel, uploads (button and drag and drop) and downloads; starts its own voxl.
- `foldertest.mjs`: folders download as zips (checked byte for byte) and upload through
  the API and the folder picker, merged and confined to the root; starts its own voxl.
- `resizedyn.mjs`: dynamic resolution end to end with its own Xvfb (`XVFB`) and voxl:
  desktop follows the window, clamping, decoded size, pointer mapping, zero-copy capture.
- `mictest.mjs`: microphone end to end with a fake 440 Hz microphone, a private PulseAudio
  pipe source and its own voxl (`PULSEAUDIO`: the pulseaudio package).

## Offline codec comparison (macOS)

`codec-lab.py` generates an eight-second 1080p60 text/scroll/scene-cut video and
compares the server's actual x264 wrapper with tuning variants, VideoToolbox
H.264/HEVC, and software AV1/HEVC 4:4:4 probes. It never captures a display or
connects to a running server. Requires FFmpeg (with those encoders), Rust, and uv.

```sh
uv run bench/codec-lab.py --output ~/Movies/voxl-codec-lab
open ~/Movies/voxl-codec-lab/index.html
# Smaller smoke test, or a video you explicitly select:
uv run bench/codec-lab.py --quick --output /tmp/voxl-codec-smoke
uv run bench/codec-lab.py --input /path/to/video.mp4 --output ~/Movies/voxl-video-test
cargo build --release --example codec-replay
python3 bench/tests/codec-replay.py
uv run bench/tests/codec-lab.py
```

The page has paired players, frame stepping, per-frame SSIM curves, actual bitrate,
frame burst sizes, and downloadable encoded originals. Browser playback uses
near-lossless H.264 review copies. Output directories must be new; frames stream
through pipes and the script stops between candidates if storage is low.

This measures encoder quality/throughput, not end-to-end latency or the full server:
no adaptive rate controller, MotionQuality correction, damage maps, paint-over,
WebSocket, or iOS decoder. Default worker cap is 20 Mbps, matching the current
single-stripe allocation at a 10 Mbps adaptive target, not a strict wire cap.
VideoToolbox uses FFmpeg realtime/no-reordering settings, not Apple's specialized
low-latency rate-control mode. Rate-control policies and actual bitrates differ;
compare measured bytes before attributing a quality improvement to the codec.
The 4:4:4 probe upsamples common I420 input; it cannot test true RGB text detail or
establish A15 hardware support. An explicit input video must cover `--seconds`.

## macOS

`macos/` tests voxl on a Mac against a running instance (`PORT`, default 6960, started
with `--enable-basic-auth=false`), as raw WebSocket clients plus Playwright
(`playwright-core` and Google Chrome) where a page is the target. They act on the real
desktop (pointer, keyboard, clipboard), so run them when nobody is using the Mac.

- `features.js`: hello, keyframe, cursor image, clipboard text both ways, system audio,
  typing and mouse in a scratch TextEdit document and, with `--enable-resize`, a
  display mode switch and back.
- `input.js`: clicks, typing (symbols, accents, CJK, emoji), Command shortcuts, drag
  selection, pixel and wheel scrolling, cursor shape, on a page in a temporary Chrome.
- `clipimage.js`: clipboard images both ways.
- `mic.js`: a 440 Hz tone through the microphone path (see voxl's log for drops).
- `perf.js`: full-screen animation; viewer FPS, pipeline and encode times.
