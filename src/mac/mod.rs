//! macOS backend: the same modules as the X11 one (capture, desktop, input, audio,
//! screen) on Apple's APIs, so the rest of voxl (encoder, flow control, server, web
//! client) runs unchanged.
//!
//! - capture: ScreenCaptureKit, NV12 frames with dirty rectangles
//! - input: CGEvent (pixel scrolling, keys resolved against the current layout)
//! - desktop: cursor images from NSCursor, clipboard sync through NSPasteboard
//! - audio: system audio from ScreenCaptureKit
//! - screen: dynamic resolution by switching display modes
//! - mic: browser microphone played into a loopback output device

pub mod audio;
pub mod capture;
pub mod desktop;
pub mod ffi;
pub mod input;
pub mod mic;
pub mod screen;
pub mod stream;

use ffi::*;
use parking_lot::Mutex;
use std::collections::HashMap;

/// The captured display and how its points map to the desktop pixels voxl streams.
#[derive(Clone, Copy, Debug)]
pub struct Geometry {
    pub display: u32,
    /// Display bounds in global points.
    pub bounds: CGRect,
    /// Streamed size in pixels.
    pub width: u32,
}

impl Geometry {
    /// Desktop pixels per point.
    pub fn scale(&self) -> f64 {
        self.width as f64 / self.bounds.size.width.max(1.0)
    }
}

/// Set by the capturer whenever it (re)starts; read by input to map coordinates.
pub static GEOMETRY: Mutex<Option<Geometry>> = Mutex::new(None);

struct Settings {
    display: u32,
    /// Fixed pixels per point (--capture-scale), or None for automatic.
    scale: Option<f64>,
    automatic_scale: f64,
    resize_scale: Option<f64>,
    max_pixels: f64,
    fps: u32,
    show_cursor: bool,
}

impl Settings {
    fn switch_display(&mut self, display: u32, points: (f64, f64), native: f64) {
        self.display = display;
        self.automatic_scale = auto_scale(points.0, points.1, native, self.max_pixels);
    }

    fn resize_scale(&self) -> f64 {
        self.resize_scale
            .or(self.scale)
            .unwrap_or(self.automatic_scale)
    }
}

static SETTINGS: Mutex<Settings> = Mutex::new(Settings {
    display: 0,
    scale: None,
    automatic_scale: 1.0,
    resize_scale: None,
    max_pixels: 2560.0 * 1600.0,
    fps: 60,
    show_cursor: false,
});

/// Reads the macOS options and checks the privacy permissions voxl needs.
pub fn init(a: &HashMap<String, String>) {
    let display = pick_display(a.get("display").map(|s| s.as_str()));
    let read_only = crate::truthy(a.get("read-only"), false);
    let show_cursor = crate::truthy(a.get("show-cursor"), read_only);
    {
        let mut s = SETTINGS.lock();
        s.display = display;
        s.show_cursor = show_cursor;
        s.scale = a
            .get("capture-scale")
            .and_then(|v| v.parse().ok())
            .filter(|&v: &f64| v > 0.0);
        s.resize_scale = a
            .get("resize-scale")
            .and_then(|v| v.rsplit('-').next()?.trim().parse().ok());
        if let Some(px) = a.get("max-capture-pixels").and_then(|v| v.parse().ok()) {
            s.max_pixels = px;
        }
        // Selkies-style ranges ("60-60"): the upper bound.
        if let Some(f) = a
            .get("framerate")
            .and_then(|v| v.rsplit('-').next()?.trim().parse::<f64>().ok())
        {
            s.fps = (f as u32).clamp(1, 240);
        }
    }
    unsafe {
        if !CGPreflightScreenCaptureAccess() {
            CGRequestScreenCaptureAccess();
            log::error!(
                "Screen Recording permission is missing: allow voxl (or the terminal running it) \
                 in System Settings > Privacy & Security > Screen Recording, then restart it"
            );
        }
        if !AXIsProcessTrusted() {
            prompt_accessibility();
            log::warn!(
                "Accessibility permission is missing, so input is ignored: allow voxl (or the \
                 terminal running it) in System Settings > Privacy & Security > Accessibility"
            );
        }
    }
    {
        let b = unsafe { CGDisplayBounds(display) };
        let mut s = SETTINGS.lock();
        s.switch_display(
            display,
            (b.size.width, b.size.height),
            native_scale(display),
        );
    }
    let (w, h) = capture_size(display);
    log::info!(
        "display {display}: {}x{} points, streaming {w}x{h}",
        unsafe { CGDisplayBounds(display) }.size.width,
        unsafe { CGDisplayBounds(display) }.size.height
    );
}

/// `--display` as an index into the active displays (0 = main) or a display ID.
fn pick_display(arg: Option<&str>) -> u32 {
    let ids = active_displays();
    for (i, id) in ids.iter().enumerate() {
        let b = unsafe { CGDisplayBounds(*id) };
        log::info!(
            "display {i}: id {id}, {}x{} points{}",
            b.size.width,
            b.size.height,
            if unsafe { CGDisplayIsMain(*id) } != 0 {
                " (main)"
            } else {
                ""
            }
        );
    }
    let main = unsafe { CGMainDisplayID() };
    match arg.and_then(|s| s.trim_start_matches(':').parse::<u32>().ok()) {
        Some(n) if (n as usize) < ids.len() => ids[n as usize],
        Some(n) if ids.contains(&n) => n,
        Some(n) => {
            log::warn!("no display {n}; using the main display");
            main
        }
        None => main,
    }
}

pub fn active_displays() -> Vec<u32> {
    let mut ids = [0u32; 16];
    let mut n = 0u32;
    unsafe { CGGetActiveDisplayList(16, ids.as_mut_ptr(), &mut n) };
    let mut v = ids[..n as usize].to_vec();
    // Main display first, so index 0 is the main one.
    let main = unsafe { CGMainDisplayID() };
    v.sort_by_key(|&id| id != main);
    v
}

pub fn display() -> u32 {
    SETTINGS.lock().display
}

fn select_active_display(selected: u32, active: &[u32]) -> Option<u32> {
    if active.contains(&selected) {
        Some(selected)
    } else {
        active.first().copied()
    }
}

/// Keep the selected monitor when available, otherwise use the current main one.
pub fn refresh_display() -> Result<u32, String> {
    let active = active_displays();
    let mut settings = SETTINGS.lock();
    let next = select_active_display(settings.display, &active).ok_or("no active displays")?;
    if next != settings.display {
        log::info!(
            "display {} is unavailable; capturing display {next}",
            settings.display
        );
        let bounds = unsafe { CGDisplayBounds(next) };
        settings.switch_display(
            next,
            (bounds.size.width, bounds.size.height),
            native_scale(next),
        );
    }
    Ok(next)
}

pub fn fps() -> u32 {
    SETTINGS.lock().fps
}

pub fn show_cursor() -> bool {
    SETTINGS.lock().show_cursor
}

/// Pixels per point for a `w`x`h`-point desktop whose panel has `native` pixels per
/// point: native, reduced so a frame stays within --max-capture-pixels (x264's cost
/// grows with area).
fn auto_scale(w: f64, h: f64, native: f64, max_pixels: f64) -> f64 {
    let native = if native > 0.0 { native } else { 1.0 };
    native.min((max_pixels / (w * h).max(1.0)).sqrt()).max(0.25)
}

/// Desktop pixels per point, fixed per monitor so resolution changes preserve the
/// mapping from the viewer's window to points.
fn scale() -> f64 {
    let settings = SETTINGS.lock();
    settings.scale.unwrap_or(settings.automatic_scale)
}

pub fn resize_scale() -> f64 {
    SETTINGS.lock().resize_scale()
}

/// Most pixels a frame should have (--max-capture-pixels).
pub fn max_pixels() -> f64 {
    SETTINGS.lock().max_pixels
}

/// Streamed size (even, for 4:2:0) of a desktop of `w`x`h` points.
pub fn size_for(w: f64, h: f64) -> (u32, u32) {
    let k = scale();
    (((w * k).round() as u32) & !1, ((h * k).round() as u32) & !1)
}

fn native_scale(display: u32) -> f64 {
    unsafe {
        let m = CGDisplayCopyDisplayMode(display);
        if m.is_null() {
            return 1.0;
        }
        let k = CGDisplayModeGetPixelWidth(m) as f64 / CGDisplayModeGetWidth(m).max(1) as f64;
        CGDisplayModeRelease(m);
        k
    }
}

/// Desktop pixels per point as streamed.
pub fn capture_scale() -> f64 {
    scale()
}

/// Streamed size of `display`.
pub fn capture_size(display: u32) -> (u32, u32) {
    let b = unsafe { CGDisplayBounds(display) };
    size_for(b.size.width, b.size.height)
}

fn prompt_accessibility() {
    use objc2_core_foundation::{CFBoolean, CFDictionary, CFString};
    unsafe {
        let key = CFString::from_static_str("AXTrustedCheckOptionPrompt");
        let opts = CFDictionary::from_slices(&[&*key], &[CFBoolean::new(true)]);
        AXIsProcessTrustedWithOptions(&*opts as *const _ as *const std::ffi::c_void);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn removed_display_uses_current_main_display() {
        assert_eq!(select_active_display(4, &[43]), Some(43));
    }

    #[test]
    fn selected_display_is_preserved_when_still_connected() {
        assert_eq!(select_active_display(4, &[43, 4]), Some(4));
    }

    #[test]
    fn no_displays_can_recover_to_returning_or_replacement_monitor() {
        assert_eq!(select_active_display(4, &[]), None);
        assert_eq!(select_active_display(4, &[4]), Some(4));
        assert_eq!(select_active_display(4, &[43]), Some(43));
    }

    #[test]
    fn replacement_monitor_recalculates_auto_scale_but_preserves_explicit_scale() {
        for scale in [None, Some(1.0)] {
            let mut settings = Settings {
                display: 4,
                scale,
                automatic_scale: 1.0,
                resize_scale: None,
                max_pixels: 2_073_600.0,
                fps: 60,
                show_cursor: false,
            };
            settings.switch_display(43, (3840.0, 2160.0), 2.0);
            assert_eq!(settings.display, 43);
            assert_eq!(
                settings.scale.unwrap_or(settings.automatic_scale),
                scale.unwrap_or(0.5)
            );
            assert_eq!(settings.resize_scale(), scale.unwrap_or(0.5));
            settings.resize_scale = Some(0.75);
            assert_eq!(settings.resize_scale(), 0.75);
        }
    }

    #[test]
    fn show_cursor_default_and_flags() {
        let mut a = HashMap::new();
        init(&a);
        assert!(!show_cursor());

        a.insert("show-cursor".into(), "true".into());
        init(&a);
        assert!(show_cursor());

        a.clear();
        a.insert("read-only".into(), "true".into());
        init(&a);
        assert!(show_cursor());

        a.insert("show-cursor".into(), "false".into());
        init(&a);
        assert!(!show_cursor());
    }
}
