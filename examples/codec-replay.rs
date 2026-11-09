//! Offline access-unit replay using the same x264 wrapper as the server.
//! No capture, sockets, credentials, or live server state are used.
#[path = "../src/encoder.rs"]
mod encoder;

use encoder::{Encoder, EncoderConfig, Planes};
use std::fs::File;
use std::io::{BufReader, BufWriter, Read, Write};
use std::time::Instant;
use voxl as _; // Carry the library's build.rs native-link metadata into this example.

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() != 10 {
        return Err("usage: codec-replay INPUT.yuv WIDTH HEIGHT FPS CRF WORKER_KBPS PRESET VBV_FRAMES OUTPUT.h264 METRICS.jsonl".into());
    }
    let width: u32 = args[1].parse()?;
    let height: u32 = args[2].parse()?;
    let fps: u32 = args[3].parse()?;
    let crf: f32 = args[4].parse()?;
    let cap: u32 = args[5].parse()?;
    let vbv_frames: f64 = args[7].parse()?;
    if width == 0
        || height == 0
        || width % 2 != 0
        || height % 2 != 0
        || fps == 0
        || cap == 0
        || !vbv_frames.is_finite()
        || vbv_frames < 1.25
    {
        return Err("need positive even dimensions, fps/cap > 0, and VBV >= 1.25 frames".into());
    }
    let pixels = width as usize * height as usize;
    let frame_size = pixels * 3 / 2;
    let input = File::open(&args[0])?;
    let metadata = input.metadata()?;
    if metadata.is_file() && metadata.len() % frame_size as u64 != 0 {
        return Err("input contains an incomplete I420 frame".into());
    }
    let mut input = BufReader::new(input);
    let mut output = BufWriter::new(File::create(&args[8])?);
    let mut metrics = BufWriter::new(File::create(&args[9])?);
    let threads = (std::thread::available_parallelism()?.get() as u32 / 4).clamp(1, 4);
    let mut encoder = Encoder::new(&EncoderConfig {
        width,
        height,
        fps,
        crf,
        max_kbps: cap,
        preset: args[6].clone(),
        vbv_frames,
        threads,
    })?;
    let mut frame = vec![0u8; frame_size];
    let mut count = 0u64;
    loop {
        // EOF between frames is normal; read_exact rejects a truncated pipe frame.
        if input.read(&mut frame[..1])? == 0 {
            break;
        }
        input.read_exact(&mut frame[1..])?;
        let key = count == 0;
        if key {
            // Same IDR burst policy as video.rs::stripe_worker.
            let boost = cap.saturating_mul(4).max(16_000);
            encoder.set_rate_buf(crf, boost, boost / fps * 8);
        } else if count == 1 {
            encoder.set_rate(crf, cap);
        }
        let planes = Planes {
            y: &frame[..pixels],
            u: &frame[pixels..pixels * 5 / 4],
            v: &frame[pixels * 5 / 4..],
            stride_y: width as usize,
            stride_uv: width as usize / 2,
        };
        let mut encoded = Vec::new();
        let start = Instant::now();
        let result = encoder.encode(
            &planes,
            (count * 1000 / fps as u64) as i64,
            key,
            None,
            None,
            &mut encoded,
        )?;
        let encode_us = start.elapsed().as_micros();
        output.write_all(&encoded)?;
        writeln!(
            metrics,
            "{}",
            serde_json::json!({
                "frame": count, "bytes": encoded.len(), "encode_us": encode_us,
                "keyframe": result.keyframe, "qp": result.qp, "crf_avg": result.crf_avg,
                "threads": threads,
            })
        )?;
        count += 1;
    }
    output.flush()?;
    metrics.flush()?;
    Ok(())
}
