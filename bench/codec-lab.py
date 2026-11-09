#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=11"]
# ///
"""Offline codec comparison with generated or explicitly supplied video, never screen capture."""

import argparse
import html
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import time

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]


def run(command, log):
    start = time.perf_counter()
    with log.open("w") as stream:
        subprocess.run(command, check=True, stdout=stream, stderr=stream)
    return time.perf_counter() - start


def font(size):
    return ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", size)


def generate(path, width, height, fps, seconds):
    """Scrolling text, hard cuts, moving colored shapes, then a stationary page."""
    scale = width / 1920
    line_h = max(12, round(40 * scale))
    face = font(max(9, round(26 * scale)))
    pages = []
    for light in (False, True):
        bg = (239, 241, 245) if light else (18, 23, 33)
        page = Image.new("RGB", (width, height * 3), bg)
        draw = ImageDraw.Draw(page)
        for line in range(height * 3 // line_h):
            color = [(70, 180, 240), (210, 105, 190), (110, 180, 85), (210, 150, 70)][
                line % 4
            ]
            text = f"{line:04d}  const result_{line} = await render({{ id: {line * 37}, codec: 'synthetic' }}); // AaBb 0123456789"
            draw.text((round(35 * scale), line * line_h), text, font=face, fill=color)
        pages.append(page)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgb24",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(fps),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264rgb",
        "-crf",
        "0",
        "-preset",
        "veryfast",
        str(path),
    ]
    with path.with_suffix(".generation.log").open("w") as log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=log)
        try:
            for frame in range(round(fps * seconds)):
                t = frame / fps
                phase = min(int(t / (seconds / 4)), 3)
                if phase in (0, 2, 3):
                    page = pages[int(phase >= 2)]
                    offset = (
                        int((t % (seconds / 4)) * height * 0.7) if phase != 3 else 0
                    )
                    offset = min(offset, height * 2)
                    image = page.crop((0, offset, width, offset + height))
                else:
                    image = Image.new("RGB", (width, height), (35, 35, 55))
                    draw = ImageDraw.Draw(image)
                    cell = max(20, width // 16)
                    shift = int(t * width * 0.4) % cell
                    for y in range(-cell, height + cell, cell):
                        for x in range(-cell, width + cell, cell):
                            color = (
                                (x * 13 + y * 7) % 220 + 25,
                                (x * 3 + y * 11) % 220 + 25,
                                (x * 17 + y * 5) % 220 + 25,
                            )
                            draw.rectangle(
                                (x + shift, y, x + shift + cell - 3, y + cell - 3),
                                fill=color,
                            )
                    draw.text(
                        (30, 30),
                        "GENERATED MOTION / HARD SCENE CUT",
                        font=face,
                        fill="white",
                        stroke_width=2,
                        stroke_fill="black",
                    )
                # Keep final quarter truly stationary: no changing frame counter.
                if phase != 3:
                    draw = ImageDraw.Draw(image)
                    draw.rectangle((0, height - line_h, width, height), fill=(0, 0, 0))
                    draw.text(
                        (20, height - line_h),
                        f"SYNTHETIC INPUT   frame {frame:04d}   {t:.3f}s",
                        font=face,
                        fill="white",
                    )
                process.stdin.write(image.tobytes())
        finally:
            process.stdin.close()
            code = process.wait()
        if code:
            raise RuntimeError(f"source generation failed; see {log.name}")


def probe(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def packet_sizes(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_packets",
            "-show_entries",
            "packet=size",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [int(row["size"]) for row in json.loads(result.stdout)["packets"]]


def percentile(values, percent):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * percent / 100) - 1)]


def report(root, results, settings):
    rows = []
    options = []
    curves = []
    colors = {
        "voxl-current": "#ff9977",
        "voxl-vbv5": "#cc99ff",
        "vt-h264": "#88ccff",
        "vt-hevc-main": "#99ee99",
    }
    for result in results:
        name = html.escape(result["name"])
        if "error" in result:
            rows.append(
                f"<tr><td>{name}</td><td colspan='6'>Unavailable: see {name}.log</td></tr>"
            )
            continue
        options.append(f"<option value='{name}.review.mp4'>{name}</option>")
        rows.append(
            f"<tr><td>{name}</td><td>{result['mbps']:.2f}</td><td>{result['max_frame_kb']:.1f}</td><td>{result['max_serialization_ms_at_10mbps']:.0f} ms</td><td>{result['ssim']:.5f}</td><td>{result['elapsed_seconds']:.2f}</td><td><a href='{name}.mp4'>Encoded original</a></td></tr>"
        )
        if name in colors:
            scores = result["ssim_per_frame"]
            points = " ".join(
                f"{i * 1000 / max(len(scores) - 1, 1):.1f},{(1 - score) * 500:.1f}"
                for i, score in enumerate(scores)
            )
            curves.append(
                f"<polyline points='{points}' fill='none' stroke='{colors[name]}' stroke-width='2'/>"
            )
    page = """<!doctype html><meta charset='utf-8'><title>Voxl Codec Lab</title>
<style>body{font:16px system-ui;background:#15171c;color:#eee;margin:24px}select,button{font:inherit;padding:8px;margin:8px}a{color:#8bd}section{display:grid;grid-template-columns:1fr 1fr;gap:16px}video{width:100%;background:#000}table{border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #555;text-align:left}p{max-width:1000px}input{width:90%}</style>
<h1>Voxl Codec Lab</h1>
<p>Generated content only (unless an explicit input was supplied). All candidates receive identical I420 frames. Playback below uses near-lossless H.264 review copies of each decoded output so browser HEVC/AV1 support cannot change which candidate you can inspect. Encoded originals are linked below.</p>
<p><b>This is encoder-only replay, not a live Voxl session.</b> It excludes network adaptation, MotionQuality correction, damage maps, paint-over scheduling, WSS, iOS decoding and display. Encode throughput is not end-to-end latency. Fixed worker budget: BUDGET kbps; current single-stripe server allocation is 2x its adaptive target. New encoder candidates are not bitrate-identical: inspect measured Mbps and burst sizes.</p>
<button id='play'>Play / pause both</button><button id='restart'>Restart</button><button id='step'>Next frame</button><label>Speed <select id='speed'><option>1</option><option>0.5</option><option>0.25</option></select></label>
<section><div><h2>A <select id='aSelect'>OPTIONS</select></h2><video id='a' muted playsinline controls preload='metadata'></video></div><div><h2>B <select id='bSelect'>OPTIONS</select></h2><video id='b' muted playsinline controls preload='metadata'></video></div></section>
<p>Try current versus veryfast, then current versus hardware HEVC. Pause immediately after the cuts at CUT1 and CUT2 seconds; step frame by frame. Compare fine colored text and blocks while moving. Independent video elements are only approximately synchronized; frame-step pauses/seeks both to the same nominal timestamp.</p>
<h2>Per-frame quality: dips mean more distortion</h2><p style='color:#ff9977'>Current H.264 <span style='color:#cc99ff'> / H.264 with 5-frame VBV</span><span style='color:#88ccff'> / hardware H.264</span><span style='color:#99ee99'> / hardware HEVC</span></p>
<svg viewBox='0 -5 1000 160' style='width:100%;max-width:1200px;background:#222'><path d='M250 0V150M500 0V150M750 0V150' stroke='#555' stroke-dasharray='4 4'/>CURVES</svg><p>Left to right: the whole clip. Top is SSIM 1.0; bottom is 0.70. Dashed lines mark quarter boundaries (scene changes in generated input).</p>
<table><tr><th>Candidate</th><th>Actual Mbps</th><th>Largest frame KB</th><th>Its minimum transfer time at 10 Mbps</th><th>Mean SSIM</th><th>Encode wall seconds</th><th>Download</th></tr>ROWS</table>
<p>SSIM is measured against the shared 4:2:0 input, not original RGB. It does not measure lost source chroma or guarantee small-text readability. Review copies add a tiny second-generation error. CPU/hardware load, rate-control policy and startup affect encode wall time. No frame reordering is requested. Hardware candidates disallow software fallback. The transfer-time column is bytes divided by a hypothetical 10 Mbps link, not measured network or end-to-end delay. The FFmpeg hardware path uses realtime/no-reordering hints, not Apple's specialized EnableLowLatencyRateControl mode. Its burst policy differs from x264; AV1 uses low-delay CBR. AV1 and HEVC 4:4:4 are explicitly software probes, not proposed A15 playback defaults. The 4:4:4 probe upsamples the common I420 input and cannot recover original RGB detail.</p>
<p>Raw settings, commands, per-frame SSIM and packet/encode statistics: <a href='results.json'>results.json</a>. Source: <a href='source.review.mp4'>source video</a>. Keep this page at its native display scale or fullscreen a video to inspect text.</p>
<script>
const a=document.querySelector('#a'),b=document.querySelector('#b');
function load(v,s){let t=a.currentTime;a.pause();b.pause();v.src=s.value;v.addEventListener('loadedmetadata',()=>{v.currentTime=Math.min(t,v.duration)},{once:true})}
for(const [v,s] of [[a,aSelect],[b,bSelect]])s.onchange=()=>load(v,s);
aSelect.selectedIndex=0;bSelect.selectedIndex=Math.min(1,bSelect.options.length-1);load(a,aSelect);load(b,bSelect);
play.onclick=()=>{if(a.paused){b.currentTime=a.currentTime;Promise.all([a.play(),b.play()]).catch(console.error)}else{a.pause();b.pause()}};
restart.onclick=()=>{a.pause();b.pause();a.currentTime=b.currentTime=0};
step.onclick=()=>{a.pause();b.pause();const t=Math.min(a.currentTime+1/FPS,a.duration);a.currentTime=b.currentTime=t};
speed.onchange=()=>{a.playbackRate=b.playbackRate=Number(speed.value)};
a.onseeked=()=>{if(Math.abs(a.currentTime-b.currentTime)>0.08)b.currentTime=a.currentTime};
</script>"""
    page = (
        page.replace("BUDGET", str(settings["worker_kbps"]))
        .replace("OPTIONS", "".join(options))
        .replace("ROWS", "".join(rows))
        .replace("CUT1", str(settings["seconds"] / 4))
        .replace("CUT2", str(settings["seconds"] / 2))
        .replace("FPS", str(settings["fps"]))
        .replace("CURVES", "".join(curves))
    )
    (root / "index.html").write_text(page)
    (root / "results.json").write_text(
        json.dumps({"settings": settings, "results": results}, indent=2)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new output directory; never overwrites a previous run",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="optional video you explicitly choose; otherwise generate synthetic desktop video",
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--seconds", type=float, default=8)
    parser.add_argument(
        "--worker-kbps",
        type=int,
        default=20000,
        help="actual encoder cap, not server adaptive target (default 2 x 10000)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="320x180, 30 fps, two seconds; excludes software codec probes",
    )
    args = parser.parse_args()
    if args.quick:
        args.width, args.height, args.fps, args.seconds = 320, 180, 30, 2
    if (
        any(
            value <= 0
            for value in (
                args.width,
                args.height,
                args.fps,
                args.seconds,
                args.worker_kbps,
            )
        )
        or args.width % 2
        or args.height % 2
    ):
        parser.error(
            "positive dimensions/fps/duration/budget and even dimensions required"
        )
    if args.output.exists():
        parser.error("output directory already exists; choose a fresh path")
    if shutil.disk_usage(args.output.parent).free < 400 * 1024**2:
        parser.error(
            "need at least 400 MiB free; frames stream through pipes but outputs still use space"
        )
    args.output.mkdir(parents=True)
    root = args.output.resolve()
    source = root / "source.mkv"
    if args.input:
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(args.input.resolve()),
                "-t",
                str(args.seconds),
                "-vf",
                f"scale={args.width}:{args.height}:force_original_aspect_ratio=decrease,pad={args.width}:{args.height}:(ow-iw)/2:(oh-ih)/2,fps={args.fps}",
                "-an",
                "-c:v",
                "libx264rgb",
                "-crf",
                "0",
                "-preset",
                "veryfast",
                str(source),
            ],
            root / "source.log",
        )
    else:
        generate(source, args.width, args.height, args.fps, args.seconds)
    binary = REPO / "target/release/examples/codec-replay"
    run(
        [
            "cargo",
            "build",
            "--release",
            "--example",
            "codec-replay",
            "--manifest-path",
            str(REPO / "Cargo.toml"),
        ],
        root / "build.log",
    )
    variants = [
        ("voxl-current", "ultrafast", 24, 1.25),
        ("voxl-veryfast", "veryfast", 24, 1.25),
        ("voxl-crf20", "ultrafast", 20, 1.25),
        ("voxl-vbv5", "ultrafast", 24, 5),
        ("vt-h264", None, None, None),
        ("vt-hevc-main", None, None, None),
    ]
    if not args.quick:
        variants += [
            ("software-av1", None, None, None),
            ("software-hevc444", None, None, None),
        ]
    results = []
    for name, preset, crf, vbv in variants:
        if shutil.disk_usage(root).free < 350 * 1024**2:
            raise SystemExit(
                f"Stopping before disk fills; partial report: {root / 'index.html'}"
            )
        print(f"Encoding {name}...", flush=True)
        target, log = root / f"{name}.mp4", root / f"{name}.log"
        result = {"name": name}
        try:
            if preset:
                raw, stats = root / f"{name}.h264", root / f"{name}.jsonl"
                decode = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(source),
                    "-pix_fmt",
                    "yuv420p",
                    "-f",
                    "rawvideo",
                    "pipe:1",
                ]
                command = [
                    str(binary),
                    "/dev/stdin",
                    str(args.width),
                    str(args.height),
                    str(args.fps),
                    str(crf),
                    str(args.worker_kbps),
                    preset,
                    str(vbv),
                    str(raw),
                    str(stats),
                ]
                start = time.perf_counter()
                with log.open("w") as stream:
                    decoder = subprocess.Popen(
                        decode, stdout=subprocess.PIPE, stderr=stream
                    )
                    try:
                        subprocess.run(
                            command,
                            stdin=decoder.stdout,
                            stdout=stream,
                            stderr=stream,
                            check=True,
                        )
                    finally:
                        decoder.stdout.close()
                        decoder_code = decoder.wait()
                    if decoder_code:
                        raise RuntimeError("source decoder failed")
                result["elapsed_seconds"] = time.perf_counter() - start
                result["command"] = command
                records = [json.loads(row) for row in stats.read_text().splitlines()]
                result["encode_ms_p50"] = percentile(
                    [row["encode_us"] / 1000 for row in records], 50
                )
                result["encode_ms_p95"] = percentile(
                    [row["encode_us"] / 1000 for row in records], 95
                )
                run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-r",
                        str(args.fps),
                        "-i",
                        str(raw),
                        "-c:v",
                        "copy",
                        "-movflags",
                        "+faststart",
                        str(target),
                    ],
                    root / f"{name}.remux.log",
                )
                raw.unlink()
            else:
                command = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "info",
                    "-y",
                    "-i",
                    str(source),
                    "-an",
                    "-pix_fmt",
                    "yuv420p",
                ]
                if name.startswith("vt-"):
                    codec = (
                        "h264_videotoolbox"
                        if name == "vt-h264"
                        else "hevc_videotoolbox"
                    )
                    command += [
                        "-c:v",
                        codec,
                        "-allow_sw",
                        "0",
                        "-realtime",
                        "1",
                        "-prio_speed",
                        "1",
                        "-bf",
                        "0",
                        "-g",
                        "999999",
                        "-b:v",
                        f"{args.worker_kbps}k",
                        "-maxrate",
                        f"{args.worker_kbps}k",
                        "-bufsize",
                        f"{math.ceil(args.worker_kbps * 1.25 / args.fps)}k",
                        "-profile:v",
                        "high" if name == "vt-h264" else "main",
                    ]
                elif name == "software-av1":
                    command += [
                        "-c:v",
                        "libsvtav1",
                        "-preset",
                        "8",
                        "-b:v",
                        f"{args.worker_kbps}k",
                        "-maxrate",
                        f"{args.worker_kbps}k",
                        "-bufsize",
                        f"{args.worker_kbps}k",
                        "-g",
                        "999999",
                        "-svtav1-params",
                        "lp=4:pred-struct=1:rc=2:lookahead=0:enable-tf=0",
                    ]
                else:
                    # Upsampled I420 intentionally: codec compatibility probe, not a true 4:4:4 text-quality test.
                    command += [
                        "-vf",
                        "format=yuv420p,format=yuv444p",
                        "-pix_fmt",
                        "yuv444p",
                        "-c:v",
                        "libx265",
                        "-preset",
                        "ultrafast",
                        "-tune",
                        "zerolatency",
                        "-b:v",
                        f"{args.worker_kbps}k",
                        "-x265-params",
                        "pools=4:frame-threads=1:keyint=999999:scenecut=0:log-level=error",
                    ]
                if "hevc" in name:
                    command += ["-tag:v", "hvc1"]
                command += ["-movflags", "+faststart", str(target)]
                result["command"] = command
                result["elapsed_seconds"] = run(command, log)
            info = probe(target)
            stream = info["streams"][0]
            frames = packet_sizes(target)
            expected = round(args.seconds * args.fps)
            if len(frames) != expected:
                raise RuntimeError(
                    f"expected {expected} access units, got {len(frames)}"
                )
            result.update(
                {
                    "codec": stream["codec_name"],
                    "profile": stream.get("profile"),
                    "pix_fmt": stream["pix_fmt"],
                    "frames": len(frames),
                    "mbps": sum(frames) * 8 / args.seconds / 1e6,
                    "max_frame_kb": max(frames) / 1000,
                    "max_serialization_ms_at_10mbps": max(frames) / 1250,
                    "packet_bytes": frames,
                }
            )
            stats = root / f"{name}.ssim"
            filters = f"[0:v]format=yuv420p,settb=1/{args.fps},setpts=N[ref];[1:v]format=yuv420p,settb=1/{args.fps},setpts=N[dist];[dist][ref]ssim=stats_file={stats}[out]"
            run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-i",
                    str(source),
                    "-i",
                    str(target),
                    "-filter_complex",
                    filters,
                    "-map",
                    "[out]",
                    "-f",
                    "null",
                    "-",
                ],
                root / f"{name}.quality.log",
            )
            values = [
                float(line.split("All:")[1].split()[0])
                for line in stats.read_text().splitlines()
            ]
            result["ssim_per_frame"] = values
            result["ssim"] = sum(values) / len(values)
            result["ssim_min"] = min(values)
            run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(target),
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "1",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(root / f"{name}.review.mp4"),
                ],
                root / f"{name}.review.log",
            )
            print(
                f"  {result['mbps']:.2f} Mbps, SSIM {result['ssim']:.5f}, wall {result['elapsed_seconds']:.2f}s",
                flush=True,
            )
        except (subprocess.CalledProcessError, RuntimeError) as error:
            result["error"] = str(error)
            print(f"  Unavailable: {error}; see {log}", flush=True)
        results.append(result)
        settings = {
            "width": args.width,
            "height": args.height,
            "fps": args.fps,
            "seconds": args.seconds,
            "worker_kbps": args.worker_kbps,
            "input": "explicit user video" if args.input else "generated",
            "scope": "encoder-only, no network/capture/adaptation/MotionQuality/paint-over",
            "source_commit": subprocess.check_output(
                ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True
            ).strip(),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "ffmpeg_version": subprocess.check_output(
                ["ffmpeg", "-version"], text=True
            ).splitlines()[0],
            "cpu": subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip(),
        }
        report(root, results, settings)
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(root / "source.review.mp4"),
        ],
        root / "source.review.log",
    )
    print(f"Report: {root / 'index.html'}", flush=True)
    if all("error" in result for result in results):
        raise SystemExit("No candidate succeeded")


if __name__ == "__main__":
    main()
