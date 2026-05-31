use anyhow::{anyhow, Context, Result};
use chrono::{Datelike, Local, NaiveDate, NaiveDateTime, Timelike};
use cosmic_text::{
    fontdb::Database, Attrs, Buffer, Color, Family, FontSystem, Metrics, Shaping, SwashCache,
    Weight, Wrap,
};
use image::{imageops, ImageFormat, ImageReader, Rgb, RgbImage, RgbaImage};
use reqwest::blocking::Client;
use reqwest::header::{HeaderMap, HeaderValue, USER_AGENT};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::env;
use std::fs;
use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::thread;
use std::time::Instant;

const SCALE: f32 = 2.0;
const WIDTH: u32 = 820;
const CONTENT_LEFT: f32 = 20.0;
const CONTENT_RIGHT: f32 = 800.0;
const CONTENT_WIDTH: f32 = CONTENT_RIGHT - CONTENT_LEFT;
const BODY_TOP: f32 = 64.0;
const BODY_QUOTE_GAP: f32 = 17.0;
const MAIN_NAME_SIZE: f32 = 15.0;
const MAIN_HANDLE_SIZE: f32 = 15.0;
const MAIN_TEXT_SIZE: f32 = 17.0;
const MAIN_LINE_HEIGHT: f32 = 24.0;
const META_TEXT_SIZE: f32 = 15.0;
const QUOTE_NAME_SIZE: f32 = 15.0;
const QUOTE_META_SIZE: f32 = 15.0;
const QUOTE_TEXT_SIZE: f32 = 15.0;
const QUOTE_LINE_HEIGHT: f32 = 20.0;
const QUOTE_TEXT_TOP: f32 = 39.0;
const QUOTE_TEXT_MEDIA_GAP: f32 = 14.0;
const MAX_IMAGE_BYTES: usize = 8 * 1024 * 1024;

const BG: Rgb<u8> = Rgb([0x00, 0x00, 0x00]);
const BORDER: Rgb<u8> = Rgb([0x2f, 0x33, 0x36]);
const PRIMARY: Rgb<u8> = Rgb([0xe7, 0xe9, 0xea]);
const SECONDARY: Rgb<u8> = Rgb([0x71, 0x76, 0x7b]);
const ACCENT: Rgb<u8> = Rgb([0x1d, 0x9b, 0xf0]);
const WHITE: Rgb<u8> = Rgb([0xff, 0xff, 0xff]);
const SUBSCRIBE_BG: Rgb<u8> = Rgb([0xef, 0xf3, 0xf4]);
const SUBSCRIBE_TEXT: Rgb<u8> = Rgb([0x0f, 0x14, 0x19]);

#[derive(Clone)]
struct Span {
    text: String,
    color: Color,
    weight: Weight,
}

#[derive(Clone, Default)]
struct AuthorInfo {
    name: String,
    handle: String,
    avatar: String,
    verified: bool,
    subscribe: bool,
}

#[derive(Clone, Deserialize)]
struct DirectMedia {
    url: String,
    #[serde(default)]
    info: Value,
}

#[derive(Clone, Deserialize)]
struct QuotedPost {
    #[serde(default)]
    text: String,
    #[serde(default)]
    info: Value,
    #[serde(default)]
    media: Vec<DirectMedia>,
}

#[derive(Clone, Deserialize)]
struct RenderRequest {
    id: u64,
    tweet_text: String,
    temp_dir: String,
    tweet_identifier: String,
    #[serde(default)]
    tweet_info: Value,
    #[serde(default)]
    quoted_post: Option<QuotedPost>,
}

#[derive(Serialize)]
struct RenderResponse {
    id: u64,
    ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    render_s: f64,
}

struct Renderer {
    font_system: FontSystem,
    cache: SwashCache,
    verified_badge: Option<RgbaImage>,
    http: Client,
}

struct Assets {
    main_avatar: Option<RgbaImage>,
    quote_avatar: Option<RgbaImage>,
    quote_media: Option<RgbaImage>,
}

fn repo_root() -> PathBuf {
    if let Ok(path) = env::var("OIKURA_RENDER_REPO_ROOT") {
        return PathBuf::from(path);
    }
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

fn rgb_to_color(color: Rgb<u8>) -> Color {
    Color::rgb(color.0[0], color.0[1], color.0[2])
}

fn load_font_system() -> FontSystem {
    let mut db = Database::new();
    db.load_system_fonts();
    db.load_fonts_dir(repo_root().join("assets/fonts"));
    FontSystem::new_with_locale_and_db("en-US".into(), db)
}

fn build_http_client() -> Result<Client> {
    let mut headers = HeaderMap::new();
    headers.insert(
        USER_AGENT,
        HeaderValue::from_static(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 \
             (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        ),
    );
    Client::builder()
        .default_headers(headers)
        .redirect(reqwest::redirect::Policy::limited(10))
        .timeout(std::time::Duration::from_secs(4))
        .build()
        .context("build HTTP client")
}

fn blend(dst: &mut Rgb<u8>, color: Rgb<u8>, alpha: u8) {
    let a = alpha as u16;
    let inv = 255 - a;
    for i in 0..3 {
        dst.0[i] = ((color.0[i] as u16 * a + dst.0[i] as u16 * inv) / 255) as u8;
    }
}

fn blend_cosmic_pixel(dst: &mut Rgb<u8>, color: Color) {
    blend(dst, Rgb([color.r(), color.g(), color.b()]), color.a());
}

fn fill_rect(img: &mut RgbImage, x: i32, y: i32, w: u32, h: u32, color: Color) {
    if color.a() == 0 {
        return;
    }
    for yy in y.max(0) as u32..(y + h as i32).min(img.height() as i32).max(0) as u32 {
        for xx in x.max(0) as u32..(x + w as i32).min(img.width() as i32).max(0) as u32 {
            blend_cosmic_pixel(img.get_pixel_mut(xx, yy), color);
        }
    }
}

fn draw_line(img: &mut RgbImage, x1: f32, y1: f32, x2: f32, y2: f32, color: Rgb<u8>) {
    let mut x = (x1 * SCALE).round() as i32;
    let mut y = (y1 * SCALE).round() as i32;
    let x2 = (x2 * SCALE).round() as i32;
    let y2 = (y2 * SCALE).round() as i32;
    let dx = (x2 - x).abs();
    let dy = -(y2 - y).abs();
    let sx = if x < x2 { 1 } else { -1 };
    let sy = if y < y2 { 1 } else { -1 };
    let mut err = dx + dy;
    loop {
        if x >= 0 && y >= 0 && x < img.width() as i32 && y < img.height() as i32 {
            img.put_pixel(x as u32, y as u32, color);
        }
        if x == x2 && y == y2 {
            break;
        }
        let e2 = 2 * err;
        if e2 >= dy {
            err += dy;
            x += sx;
        }
        if e2 <= dx {
            err += dx;
            y += sy;
        }
    }
}

fn draw_circle(img: &mut RgbImage, cx: f32, cy: f32, r: f32, color: Rgb<u8>) {
    let cx = cx * SCALE;
    let cy = cy * SCALE;
    let r = r * SCALE;
    let min_x = (cx - r - 1.0).floor().max(0.0) as u32;
    let max_x = (cx + r + 1.0).ceil().min(img.width() as f32 - 1.0) as u32;
    let min_y = (cy - r - 1.0).floor().max(0.0) as u32;
    let max_y = (cy + r + 1.0).ceil().min(img.height() as f32 - 1.0) as u32;
    for y in min_y..=max_y {
        for x in min_x..=max_x {
            let dx = x as f32 + 0.5 - cx;
            let dy = y as f32 + 0.5 - cy;
            let dist = (dx * dx + dy * dy).sqrt();
            let alpha = (r + 0.75 - dist).clamp(0.0, 1.0);
            if alpha > 0.0 {
                blend(img.get_pixel_mut(x, y), color, (alpha * 255.0) as u8);
            }
        }
    }
}

fn in_rounded_rect(px: f32, py: f32, x: f32, y: f32, w: f32, h: f32, r: f32) -> bool {
    if px < x || py < y || px >= x + w || py >= y + h {
        return false;
    }
    let cx = if px < x + r {
        x + r
    } else if px >= x + w - r {
        x + w - r - 1.0
    } else {
        return true;
    };
    let cy = if py < y + r {
        y + r
    } else if py >= y + h - r {
        y + h - r - 1.0
    } else {
        return true;
    };
    let dx = px - cx;
    let dy = py - cy;
    dx * dx + dy * dy <= r * r
}

fn fill_rounded_rect(img: &mut RgbImage, x: f32, y: f32, w: f32, h: f32, r: f32, color: Rgb<u8>) {
    let sx = (x * SCALE).round() as i32;
    let sy = (y * SCALE).round() as i32;
    let sw = (w * SCALE).round() as i32;
    let sh = (h * SCALE).round() as i32;
    let sr = r * SCALE;
    for yy in sy.max(0)..(sy + sh).min(img.height() as i32) {
        for xx in sx.max(0)..(sx + sw).min(img.width() as i32) {
            let alpha = if in_rounded_rect(
                xx as f32 + 0.5,
                yy as f32 + 0.5,
                sx as f32,
                sy as f32,
                sw as f32,
                sh as f32,
                sr,
            ) {
                255
            } else {
                0
            };
            if alpha > 0 {
                blend(img.get_pixel_mut(xx as u32, yy as u32), color, alpha);
            }
        }
    }
}

fn base_attrs(color: Color, weight: Weight) -> Attrs<'static> {
    Attrs::new()
        .family(Family::Name("TwitterChirp"))
        .color(color)
        .weight(weight)
}

fn text_width(font_system: &mut FontSystem, text: &str, size: f32, weight: Weight) -> f32 {
    let mut buffer = Buffer::new(font_system, Metrics::new(size * SCALE, size * 1.25 * SCALE));
    let mut buffer = buffer.borrow_with(font_system);
    buffer.set_wrap(Wrap::None);
    buffer.set_size(Some(10_000.0), Some(size * 2.0 * SCALE));
    buffer.set_text(text, &base_attrs(rgb_to_color(PRIMARY), weight), Shaping::Advanced, None);
    buffer
        .layout_runs()
        .map(|run| run.line_w)
        .fold(0.0f32, f32::max)
        / SCALE
}

fn fit_text(
    font_system: &mut FontSystem,
    text: &str,
    size: f32,
    weight: Weight,
    max_width: f32,
) -> String {
    if text_width(font_system, text, size, weight) <= max_width {
        return text.to_string();
    }
    let mut output = text.to_string();
    while !output.is_empty()
        && text_width(font_system, &(output.clone() + "..."), size, weight) > max_width
    {
        output.pop();
    }
    if output.is_empty() {
        "...".to_string()
    } else {
        output.trim_end().to_string() + "..."
    }
}

fn draw_spans(
    img: &mut RgbImage,
    font_system: &mut FontSystem,
    cache: &mut SwashCache,
    x: f32,
    y: f32,
    width: f32,
    height: f32,
    size: f32,
    line_height: f32,
    spans: &[Span],
    wrap: Wrap,
) {
    let metrics = Metrics::new(size * SCALE, line_height * SCALE);
    let mut buffer = Buffer::new(font_system, metrics);
    let mut buffer = buffer.borrow_with(font_system);
    buffer.set_wrap(wrap);
    buffer.set_size(Some(width * SCALE), Some(height * SCALE));
    let default_attrs = base_attrs(rgb_to_color(PRIMARY), Weight::NORMAL);
    buffer.set_rich_text(
        spans
            .iter()
            .map(|span| (span.text.as_str(), base_attrs(span.color, span.weight))),
        &default_attrs,
        Shaping::Advanced,
        None,
    );
    let offset_x = (x * SCALE).round() as i32;
    let offset_y = (y * SCALE).round() as i32;
    buffer.draw(cache, rgb_to_color(PRIMARY), |px, py, w, h, color| {
        fill_rect(img, offset_x + px, offset_y + py, w, h, color);
    });
}

fn build_buffer(
    font_system: &mut FontSystem,
    spans: &[Span],
    width: f32,
    height: f32,
    size: f32,
    line_height: f32,
) -> Buffer {
    let metrics = Metrics::new(size * SCALE, line_height * SCALE);
    let mut buffer = Buffer::new(font_system, metrics);
    {
        let mut buffer = buffer.borrow_with(font_system);
        buffer.set_wrap(Wrap::Word);
        buffer.set_size(Some(width * SCALE), Some(height * SCALE));
        let default_attrs = base_attrs(rgb_to_color(PRIMARY), Weight::NORMAL);
        buffer.set_rich_text(
            spans
                .iter()
                .map(|span| (span.text.as_str(), base_attrs(span.color, span.weight))),
            &default_attrs,
            Shaping::Advanced,
            None,
        );
    }
    buffer
}

fn buffer_line_count(font_system: &mut FontSystem, buffer: &mut Buffer) -> usize {
    let mut buffer = buffer.borrow_with(font_system);
    buffer.layout_runs().count().max(1)
}

fn draw_buffer_at(
    img: &mut RgbImage,
    font_system: &mut FontSystem,
    cache: &mut SwashCache,
    buffer: &mut Buffer,
    x: f32,
    y: f32,
) {
    let offset_x = (x * SCALE).round() as i32;
    let offset_y = (y * SCALE).round() as i32;
    let mut buffer = buffer.borrow_with(font_system);
    buffer.draw(cache, rgb_to_color(PRIMARY), |px, py, w, h, color| {
        fill_rect(img, offset_x + px, offset_y + py, w, h, color);
    });
}

fn draw_text(
    img: &mut RgbImage,
    font_system: &mut FontSystem,
    cache: &mut SwashCache,
    x: f32,
    y: f32,
    width: f32,
    text: &str,
    size: f32,
    weight: Weight,
    color: Rgb<u8>,
) {
    if text.is_empty() {
        return;
    }
    let span = Span {
        text: text.to_string(),
        color: rgb_to_color(color),
        weight,
    };
    draw_spans(
        img,
        font_system,
        cache,
        x,
        y,
        width,
        size * 1.8,
        size,
        size * 1.25,
        &[span],
        Wrap::None,
    );
}

fn token_color(token: &str) -> Color {
    let stripped = token.trim();
    if stripped.starts_with('@')
        || stripped.starts_with('#')
        || stripped.starts_with("http://")
        || stripped.starts_with("https://")
        || stripped.starts_with("x.com/")
        || stripped.starts_with("twitter.com/")
    {
        rgb_to_color(ACCENT)
    } else {
        rgb_to_color(PRIMARY)
    }
}

fn body_spans(text: &str) -> Vec<Span> {
    let mut spans = Vec::new();
    for (line_index, line) in text.lines().enumerate() {
        if line_index > 0 {
            spans.push(Span {
                text: "\n".to_string(),
                color: rgb_to_color(PRIMARY),
                weight: Weight::NORMAL,
            });
        }
        for (index, word) in line.split_whitespace().enumerate() {
            if index > 0 {
                spans.push(Span {
                    text: " ".to_string(),
                    color: rgb_to_color(PRIMARY),
                    weight: Weight::NORMAL,
                });
            }
            spans.push(Span {
                text: word.to_string(),
                color: token_color(word),
                weight: Weight::NORMAL,
            });
        }
    }
    if spans.is_empty() {
        spans.push(Span {
            text: "".to_string(),
            color: rgb_to_color(PRIMARY),
            weight: Weight::NORMAL,
        });
    }
    spans
}

fn load_verified_badge(size: f32) -> Option<RgbaImage> {
    let size_px = (size * SCALE).round().max(1.0) as u32;
    if let Some(icon) = load_verified_badge_svg(size_px) {
        return Some(icon);
    }

    let path = repo_root().join("assets/icons/twitter_verified.png");
    let reader = ImageReader::open(path).ok()?;
    let icon = reader.decode().ok()?;
    Some(imageops::resize(&icon.to_rgba8(), size_px, size_px, imageops::Lanczos3))
}

fn load_verified_badge_svg(size_px: u32) -> Option<RgbaImage> {
    let path = repo_root().join("assets/icons/checkmark.svg");
    let raw_svg = fs::read_to_string(path).ok()?;
    let svg = if raw_svg.trim_start().starts_with("<svg") {
        raw_svg
    } else {
        format!(
            r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 22 22"><g fill="#1d9bf0">{raw_svg}</g></svg>"##
        )
    };

    let mut options = usvg::Options::default();
    options.fontdb_mut().load_system_fonts();
    let tree = usvg::Tree::from_str(&svg, &options).ok()?;
    let mut pixmap = tiny_skia::Pixmap::new(size_px, size_px)?;
    let scale_x = size_px as f32 / tree.size().width();
    let scale_y = size_px as f32 / tree.size().height();
    resvg::render(
        &tree,
        tiny_skia::Transform::from_scale(scale_x, scale_y),
        &mut pixmap.as_mut(),
    );

    let mut image = RgbaImage::new(size_px, size_px);
    for y in 0..size_px {
        for x in 0..size_px {
            let pixel = pixmap.pixel(x, y)?;
            image.put_pixel(x, y, image::Rgba([pixel.red(), pixel.green(), pixel.blue(), pixel.alpha()]));
        }
    }
    Some(image)
}

fn paste_rgba(img: &mut RgbImage, src: &RgbaImage, x: f32, y: f32) {
    let x = (x * SCALE).round() as i32;
    let y = (y * SCALE).round() as i32;
    for iy in 0..src.height() {
        for ix in 0..src.width() {
            let px = x + ix as i32;
            let py = y + iy as i32;
            if px < 0 || py < 0 || px >= img.width() as i32 || py >= img.height() as i32 {
                continue;
            }
            let pixel = src.get_pixel(ix, iy);
            blend(
                img.get_pixel_mut(px as u32, py as u32),
                Rgb([pixel.0[0], pixel.0[1], pixel.0[2]]),
                pixel.0[3],
            );
        }
    }
}

fn paste_verified_badge(img: &mut RgbImage, badge: Option<&RgbaImage>, x: f32, y: f32) {
    if let Some(icon) = badge {
        paste_rgba(img, icon, x, y);
    } else {
        draw_circle(img, x + 9.0, y + 9.0, 9.0, ACCENT);
    }
}

fn fetch_image(client: &Client, url: &str) -> Option<RgbaImage> {
    if url.trim().is_empty() {
        return None;
    }
    for _ in 0..3 {
        let mut response = client.get(url).send().ok()?;
        if !response.status().is_success() {
            continue;
        }
        let mut bytes = Vec::new();
        response.copy_to(&mut bytes).ok()?;
        if bytes.len() > MAX_IMAGE_BYTES {
            return None;
        }
        if let Ok(image) = image::load_from_memory(&bytes) {
            return Some(image.to_rgba8());
        }
    }
    None
}

fn cover_resize(source: &RgbaImage, width: u32, height: u32) -> RgbaImage {
    if source.width() == 0 || source.height() == 0 {
        return RgbaImage::new(width, height);
    }
    let source_ratio = source.width() as f32 / source.height() as f32;
    let target_ratio = width as f32 / height as f32;
    let cropped = if source_ratio > target_ratio {
        let crop_width = (source.height() as f32 * target_ratio).round() as u32;
        let left = (source.width().saturating_sub(crop_width)) / 2;
        imageops::crop_imm(source, left, 0, crop_width, source.height()).to_image()
    } else {
        let crop_height = (source.width() as f32 / target_ratio).round() as u32;
        let top = (source.height().saturating_sub(crop_height)) / 2;
        imageops::crop_imm(source, 0, top, source.width(), crop_height).to_image()
    };
    imageops::resize(&cropped, width, height, imageops::Lanczos3)
}

fn paste_circle_image(img: &mut RgbImage, source: &RgbaImage, x: f32, y: f32, size: f32) {
    let size_px = (size * SCALE).round() as u32;
    let avatar = cover_resize(source, size_px, size_px);
    let dx = (x * SCALE).round() as i32;
    let dy = (y * SCALE).round() as i32;
    let r = size_px as f32 / 2.0;
    for iy in 0..size_px {
        for ix in 0..size_px {
            let px = dx + ix as i32;
            let py = dy + iy as i32;
            if px < 0 || py < 0 || px >= img.width() as i32 || py >= img.height() as i32 {
                continue;
            }
            let cx = ix as f32 + 0.5 - r;
            let cy = iy as f32 + 0.5 - r;
            let edge = (r + 0.75 - (cx * cx + cy * cy).sqrt()).clamp(0.0, 1.0);
            if edge <= 0.0 {
                continue;
            }
            let pixel = avatar.get_pixel(ix, iy);
            let alpha = ((pixel.0[3] as f32) * edge) as u8;
            blend(
                img.get_pixel_mut(px as u32, py as u32),
                Rgb([pixel.0[0], pixel.0[1], pixel.0[2]]),
                alpha,
            );
        }
    }
}

fn initials(author: &AuthorInfo) -> String {
    let base = if author.name.is_empty() {
        author.handle.as_str()
    } else {
        author.name.as_str()
    };
    let mut out = String::new();
    for part in base.split_whitespace().take(2) {
        if let Some(ch) = part.chars().next() {
            out.extend(ch.to_uppercase());
        }
    }
    if out.is_empty() {
        "X".to_string()
    } else {
        out
    }
}

fn draw_avatar(
    img: &mut RgbImage,
    font_system: &mut FontSystem,
    cache: &mut SwashCache,
    x: f32,
    y: f32,
    size: f32,
    avatar: Option<&RgbaImage>,
    author: &AuthorInfo,
    font_size: f32,
) {
    if let Some(avatar) = avatar {
        paste_circle_image(img, avatar, x, y, size);
        return;
    }
    draw_circle(img, x + size / 2.0, y + size / 2.0, size / 2.0, ACCENT);
    let text = initials(author);
    let width = text_width(font_system, &text, font_size, Weight::BOLD);
    draw_text(
        img,
        font_system,
        cache,
        x + size / 2.0 - width / 2.0,
        y + size / 2.0 - font_size * 0.72,
        size,
        &text,
        font_size,
        Weight::BOLD,
        WHITE,
    );
}

fn value_string(value: &Value, key: &str) -> String {
    match value.get(key) {
        Some(Value::String(text)) => text.trim().to_string(),
        Some(Value::Number(number)) => number.to_string(),
        _ => String::new(),
    }
}

fn value_bool(value: &Value, key: &str) -> bool {
    match value.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::Number(number)) => number.as_i64().unwrap_or(0) != 0,
        Some(Value::String(text)) => matches!(text.as_str(), "true" | "True" | "1"),
        _ => false,
    }
}

fn value_i64(value: &Value, key: &str) -> i64 {
    match value.get(key) {
        Some(Value::Number(number)) => number.as_i64().unwrap_or(0),
        Some(Value::String(text)) => text.parse().unwrap_or(0),
        _ => 0,
    }
}

fn author_info(tweet_info: &Value) -> AuthorInfo {
    let author = tweet_info
        .get("author")
        .or_else(|| tweet_info.get("user"))
        .filter(|value| value.is_object())
        .unwrap_or(&Value::Null);
    let handle = value_string(author, "name").trim_start_matches('@').to_string();
    let name = {
        let nick = value_string(author, "nick");
        if nick.is_empty() {
            handle.clone()
        } else {
            nick
        }
    };
    let professional_type = value_string(author, "professional_type").to_lowercase();
    AuthorInfo {
        name: if name.is_empty() { "X User".to_string() } else { name },
        handle,
        avatar: value_string(author, "profile_image"),
        verified: value_bool(author, "verified") || value_bool(author, "blue_verified"),
        subscribe: professional_type == "creator",
    }
}

fn compact_count(value: i64) -> String {
    if value >= 1_000_000 {
        format!("{:.1}M", value as f64 / 1_000_000.0).replace(".0", "")
    } else if value >= 1_000 {
        format!("{:.1}K", value as f64 / 1_000.0).replace(".0", "")
    } else if value > 0 {
        value.to_string()
    } else {
        String::new()
    }
}

fn parse_date(value: &Value) -> Option<NaiveDateTime> {
    let text = match value {
        Value::String(text) => text.trim(),
        _ => return None,
    };
    if text.is_empty() {
        return None;
    }
    if let Ok(parsed) = chrono::DateTime::parse_from_rfc3339(text) {
        return Some(parsed.naive_utc());
    }
    if let Ok(parsed) = NaiveDateTime::parse_from_str(text, "%Y-%m-%dT%H:%M:%S%.f") {
        return Some(parsed);
    }
    if let Ok(parsed) = NaiveDateTime::parse_from_str(text, "%Y-%m-%d %H:%M:%S") {
        return Some(parsed);
    }
    if let Ok(parsed) = NaiveDate::parse_from_str(text, "%Y-%m-%d") {
        return parsed.and_hms_opt(0, 0, 0);
    }
    None
}

fn format_timestamp(value: &Value) -> String {
    let Some(date) = parse_date(value) else {
        return String::new();
    };
    let hour = date.hour() % 12;
    let hour = if hour == 0 { 12 } else { hour };
    let am_pm = if date.hour() < 12 { "AM" } else { "PM" };
    format!(
        "{}:{:02} {} · {} {}, {}",
        hour,
        date.minute(),
        am_pm,
        date.format("%b"),
        date.day(),
        date.year()
    )
}

fn relative_age(value: &Value) -> String {
    let Some(date) = parse_date(value) else {
        return String::new();
    };
    let now = Local::now().naive_utc();
    let seconds = (now - date).num_seconds().max(0);
    if seconds < 60 {
        format!("{seconds}s")
    } else if seconds < 3600 {
        format!("{}m", seconds / 60)
    } else if seconds < 86400 {
        format!("{}h", seconds / 3600)
    } else if seconds < 604800 {
        format!("{}d", seconds / 86400)
    } else {
        date.format("%b %d").to_string()
    }
}

fn draw_main_author(
    img: &mut RgbImage,
    font_system: &mut FontSystem,
    cache: &mut SwashCache,
    badge: Option<&RgbaImage>,
    tweet_info: &Value,
    avatar: Option<&RgbaImage>,
) {
    let author = author_info(tweet_info);
    draw_avatar(img, font_system, cache, CONTENT_LEFT, 14.0, 40.0, avatar, &author, 15.0);
    let name_x = 68.0;
    let name_y = 16.0;
    let button_x = CONTENT_RIGHT - 123.0;
    let max_name_width = if author.subscribe {
        button_x - name_x - 28.0
    } else {
        CONTENT_RIGHT - name_x
    };
    let name_text = fit_text(font_system, &author.name, MAIN_NAME_SIZE, Weight::BOLD, max_name_width);
    draw_text(
        img,
        font_system,
        cache,
        name_x,
        name_y,
        430.0,
        &name_text,
        MAIN_NAME_SIZE,
        Weight::BOLD,
        PRIMARY,
    );
    let name_w = text_width(font_system, &name_text, MAIN_NAME_SIZE, Weight::BOLD);
    if author.verified {
        paste_verified_badge(img, badge, name_x + name_w + 6.0, name_y + 6.0);
    }
    if !author.handle.is_empty() {
        let handle = fit_text(
            font_system,
            &format!("@{}", author.handle),
            MAIN_HANDLE_SIZE,
            Weight::NORMAL,
            450.0,
        );
        draw_text(
            img,
            font_system,
            cache,
            name_x,
            36.0,
            450.0,
            &handle,
            MAIN_HANDLE_SIZE,
            Weight::NORMAL,
            SECONDARY,
        );
    }
    if author.subscribe {
        fill_rounded_rect(img, button_x, 20.0, 123.0, 39.0, 20.0, SUBSCRIBE_BG);
        let label = "Subscribe";
        let width = text_width(font_system, label, 16.0, Weight::BOLD);
        draw_text(
            img,
            font_system,
            cache,
            button_x + (123.0 - width) / 2.0,
            30.0,
            123.0,
            label,
            16.0,
            Weight::BOLD,
            SUBSCRIBE_TEXT,
        );
    }
}

fn draw_timestamp(
    img: &mut RgbImage,
    font_system: &mut FontSystem,
    cache: &mut SwashCache,
    tweet_info: &Value,
    y: f32,
) {
    let timestamp = format_timestamp(tweet_info.get("date").unwrap_or(&Value::Null));
    let views = compact_count(value_i64(tweet_info, "view_count"));
    if timestamp.is_empty() && views.is_empty() {
        return;
    }
    let mut x = CONTENT_LEFT;
    if !timestamp.is_empty() {
        let text = if views.is_empty() {
            timestamp
        } else {
            format!("{timestamp} · ")
        };
        draw_text(img, font_system, cache, x, y, 500.0, &text, META_TEXT_SIZE, Weight::NORMAL, SECONDARY);
        x += text_width(font_system, &text, META_TEXT_SIZE, Weight::NORMAL);
    }
    if !views.is_empty() {
        draw_text(img, font_system, cache, x, y, 100.0, &views, META_TEXT_SIZE, Weight::BOLD, PRIMARY);
        x += text_width(font_system, &views, META_TEXT_SIZE, Weight::BOLD);
        draw_text(img, font_system, cache, x, y, 80.0, " Views", META_TEXT_SIZE, Weight::NORMAL, SECONDARY);
    }
}

fn quote_media_url(quoted: &QuotedPost) -> String {
    for media in &quoted.media {
        let media_type = value_string(&media.info, "type").to_lowercase();
        let extension = value_string(&media.info, "extension").to_lowercase();
        if !(media_type == "photo"
            || matches!(extension.as_str(), "jpg" | "jpeg" | "png" | "webp"))
        {
            continue;
        }
        if !media.url.is_empty() {
            return media.url.clone();
        }
        if let Some(fallbacks) = media.info.get("_fallback").and_then(Value::as_array) {
            if let Some(url) = fallbacks.iter().find_map(Value::as_str) {
                return url.to_string();
            }
        }
    }
    String::new()
}

fn truncated_quote_text(
    font_system: &mut FontSystem,
    text: &str,
    max_lines: usize,
    width: f32,
) -> (String, usize) {
    fn line_count_for(font_system: &mut FontSystem, text: &str, width: f32) -> usize {
        let spans = body_spans(text);
        let mut buffer = build_buffer(
            font_system,
            &spans,
            width,
            10_000.0,
            QUOTE_TEXT_SIZE,
            QUOTE_LINE_HEIGHT,
        );
        buffer_line_count(font_system, &mut buffer)
    }

    let trimmed = text.trim();
    let full_lines = line_count_for(font_system, trimmed, width);
    if full_lines <= max_lines {
        return (trimmed.to_string(), full_lines);
    }

    let chars: Vec<char> = trimmed.chars().collect();
    let mut lo = 0usize;
    let mut hi = chars.len();
    while lo < hi {
        let mid = (lo + hi).div_ceil(2);
        let mut candidate = chars[..mid].iter().collect::<String>();
        candidate = candidate.trim_end().to_string();
        if !candidate.is_empty() {
            candidate.push_str("...");
        }
        if line_count_for(font_system, &candidate, width) <= max_lines {
            lo = mid;
        } else {
            hi = mid - 1;
        }
    }

    let mut output = chars[..lo].iter().collect::<String>();
    output = output.trim_end().to_string();
    output.push_str("...");
    let lines = line_count_for(font_system, &output, width);
    (output, lines.min(max_lines).max(1))
}

fn draw_quote_card(
    img: &mut RgbImage,
    font_system: &mut FontSystem,
    cache: &mut SwashCache,
    badge: Option<&RgbaImage>,
    quoted: &QuotedPost,
    avatar: Option<&RgbaImage>,
    media: Option<&RgbaImage>,
    top: f32,
) -> f32 {
    let max_lines = 8;
    let (quote_text, line_count) =
        truncated_quote_text(font_system, &quoted.text, max_lines, CONTENT_WIDTH - 30.0);
    let text_height = line_count as f32 * QUOTE_LINE_HEIGHT;
    let media_height = media
        .map(|image| 180.0f32.max((CONTENT_WIDTH - 2.0) * image.height() as f32 / image.width().max(1) as f32))
        .unwrap_or(0.0);
    let card_height = QUOTE_TEXT_TOP + text_height
        + if media.is_some() {
            QUOTE_TEXT_MEDIA_GAP + media_height + 1.0
        } else {
            16.0
        };
    let x = CONTENT_LEFT;

    fill_rounded_rect(img, x, top, CONTENT_WIDTH, card_height, 22.0, BORDER);
    fill_rounded_rect(img, x + 1.0, top + 1.0, CONTENT_WIDTH - 2.0, card_height - 2.0, 21.0, BG);

    let author = author_info(&quoted.info);
    draw_avatar(img, font_system, cache, x + 15.0, top + 16.0, 32.0, avatar, &author, 13.0);
    let name_text = fit_text(font_system, &author.name, QUOTE_NAME_SIZE, Weight::BOLD, 170.0);
    draw_text(
        img,
        font_system,
        cache,
        x + 55.0,
        top + 16.0,
        180.0,
        &name_text,
        QUOTE_NAME_SIZE,
        Weight::BOLD,
        PRIMARY,
    );
    let mut cursor_x = x + 55.0 + text_width(font_system, &name_text, QUOTE_NAME_SIZE, Weight::BOLD) + 5.0;
    if author.verified {
        paste_verified_badge(img, badge, cursor_x, top + 20.0);
        cursor_x += 22.0;
    }

    let mut meta = Vec::new();
    if !author.handle.is_empty() {
        meta.push(format!("@{}", author.handle));
    }
    let age = relative_age(quoted.info.get("date").unwrap_or(&Value::Null));
    if !age.is_empty() {
        meta.push(age);
    }
    if !meta.is_empty() {
        let meta = fit_text(
            font_system,
            &meta.join(" · "),
            QUOTE_META_SIZE,
            Weight::NORMAL,
            x + CONTENT_WIDTH - cursor_x - 14.0,
        );
        draw_text(
            img,
            font_system,
            cache,
            cursor_x,
            top + 17.0,
            500.0,
            &meta,
            QUOTE_META_SIZE,
            Weight::NORMAL,
            SECONDARY,
        );
    }

    let text_spans = body_spans(&quote_text);
    let mut quote_buffer = build_buffer(
        font_system,
        &text_spans,
        CONTENT_WIDTH - 30.0,
        text_height + QUOTE_LINE_HEIGHT,
        QUOTE_TEXT_SIZE,
        QUOTE_LINE_HEIGHT,
    );
    draw_buffer_at(img, font_system, cache, &mut quote_buffer, x + 15.0, top + QUOTE_TEXT_TOP);

    if let Some(media) = media {
        let media_top = top + QUOTE_TEXT_TOP + text_height + QUOTE_TEXT_MEDIA_GAP;
        let media_width = ((CONTENT_WIDTH - 2.0) * SCALE).round() as u32;
        let media_height_px = (media_height * SCALE).round() as u32;
        let resized = cover_resize(media, media_width, media_height_px);
        let dx = ((x + 1.0) * SCALE).round() as i32;
        let dy = (media_top * SCALE).round() as i32;
        for iy in 0..resized.height() {
            for ix in 0..resized.width() {
                let px = dx + ix as i32;
                let py = dy + iy as i32;
                if px < 0 || py < 0 || px >= img.width() as i32 || py >= img.height() as i32 {
                    continue;
                }
                if !in_rounded_rect(
                    px as f32 + 0.5,
                    py as f32 + 0.5,
                    ((x + 1.0) * SCALE).round(),
                    ((top + 1.0) * SCALE).round(),
                    ((CONTENT_WIDTH - 2.0) * SCALE).round(),
                    ((card_height - 2.0) * SCALE).round(),
                    21.0 * SCALE,
                ) {
                    continue;
                }
                let pixel = resized.get_pixel(ix, iy);
                blend(
                    img.get_pixel_mut(px as u32, py as u32),
                    Rgb([pixel.0[0], pixel.0[1], pixel.0[2]]),
                    pixel.0[3],
                );
            }
        }
        draw_line(img, x + 1.0, media_top, x + CONTENT_WIDTH - 2.0, media_top, Rgb([0x16, 0x18, 0x1c]));
    }
    card_height
}

fn load_assets(http: &Client, req: &RenderRequest) -> Assets {
    let main_author = author_info(&req.tweet_info);
    let quote_author = req
        .quoted_post
        .as_ref()
        .map(|quoted| author_info(&quoted.info))
        .unwrap_or_default();
    let quote_media_url = req
        .quoted_post
        .as_ref()
        .map(quote_media_url)
        .unwrap_or_default();

    thread::scope(|scope| {
        let main_client = http.clone();
        let main_url = main_author.avatar.clone();
        let main = scope.spawn(move || fetch_image(&main_client, &main_url));

        let quote_client = http.clone();
        let quote_url = quote_author.avatar.clone();
        let quote_avatar = scope.spawn(move || fetch_image(&quote_client, &quote_url));

        let media_client = http.clone();
        let media = scope.spawn(move || fetch_image(&media_client, &quote_media_url));

        Assets {
            main_avatar: main.join().ok().flatten(),
            quote_avatar: quote_avatar.join().ok().flatten(),
            quote_media: media.join().ok().flatten(),
        }
    })
}

impl Renderer {
    fn new() -> Result<Self> {
        Ok(Self {
            font_system: load_font_system(),
            cache: SwashCache::new(),
            verified_badge: load_verified_badge(18.0),
            http: build_http_client()?,
        })
    }

    fn render(&mut self, req: &RenderRequest) -> Result<PathBuf> {
        let assets = load_assets(&self.http, req);
        let body = body_spans(&req.tweet_text);
        let mut body_buffer = build_buffer(
            &mut self.font_system,
            &body,
            CONTENT_WIDTH,
            100_000.0,
            MAIN_TEXT_SIZE,
            MAIN_LINE_HEIGHT,
        );
        let body_lines = buffer_line_count(&mut self.font_system, &mut body_buffer);
        let body_height = body_lines as f32 * MAIN_LINE_HEIGHT;

        let quote_card_height = if let Some(quoted) = &req.quoted_post {
            let max_lines = 8;
            let (_, line_count) = truncated_quote_text(
                &mut self.font_system,
                &quoted.text,
                max_lines,
                CONTENT_WIDTH - 30.0,
            );
            let text_height = line_count as f32 * QUOTE_LINE_HEIGHT;
            let media_height = assets
                .quote_media
                .as_ref()
                .map(|image| 180.0f32.max((CONTENT_WIDTH - 2.0) * image.height() as f32 / image.width().max(1) as f32))
                .unwrap_or(0.0);
            Some(
                QUOTE_TEXT_TOP + text_height
                    + if assets.quote_media.is_some() {
                        QUOTE_TEXT_MEDIA_GAP + media_height + 1.0
                    } else {
                        16.0
                    },
            )
        } else {
            None
        };

        let quote_top = BODY_TOP + body_height + BODY_QUOTE_GAP;
        let timestamp_y = if let Some(card_height) = quote_card_height {
            quote_top + card_height + 17.0
        } else {
            BODY_TOP + body_height + 19.0
        };
        let height = (timestamp_y + 46.0).ceil() as u32;
        let mut img = RgbImage::from_pixel(
            (WIDTH as f32 * SCALE) as u32,
            (height as f32 * SCALE) as u32,
            BG,
        );

        draw_line(&mut img, 0.0, 0.0, 0.0, height as f32, BORDER);
        draw_line(&mut img, WIDTH as f32 - 1.0, 0.0, WIDTH as f32 - 1.0, height as f32, BORDER);
        draw_main_author(
            &mut img,
            &mut self.font_system,
            &mut self.cache,
            self.verified_badge.as_ref(),
            &req.tweet_info,
            assets.main_avatar.as_ref(),
        );

        {
            let mut body_buffer_ref = body_buffer.borrow_with(&mut self.font_system);
            body_buffer_ref.set_size(
                Some(CONTENT_WIDTH * SCALE),
                Some((body_height + MAIN_LINE_HEIGHT) * SCALE),
            );
        }
        draw_buffer_at(
            &mut img,
            &mut self.font_system,
            &mut self.cache,
            &mut body_buffer,
            CONTENT_LEFT,
            BODY_TOP,
        );

        if let Some(quoted) = &req.quoted_post {
            draw_quote_card(
                &mut img,
                &mut self.font_system,
                &mut self.cache,
                self.verified_badge.as_ref(),
                quoted,
                assets.quote_avatar.as_ref(),
                assets.quote_media.as_ref(),
                quote_top,
            );
        }

        draw_timestamp(
            &mut img,
            &mut self.font_system,
            &mut self.cache,
            &req.tweet_info,
            timestamp_y,
        );

        let output = Path::new(&req.temp_dir).join(format!("{}_text.png", req.tweet_identifier));
        img.save_with_format(&output, ImageFormat::Png)
            .with_context(|| format!("save {}", output.display()))?;
        Ok(output)
    }
}

fn run_server() -> Result<()> {
    let stdin = io::stdin();
    let mut stdout = io::stdout().lock();
    let mut renderer = Renderer::new()?;
    eprintln!("oikura-rust-render ready");
    for line in stdin.lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let start = Instant::now();
        let request: RenderRequest = match serde_json::from_str(&line) {
            Ok(request) => request,
            Err(error) => {
                let response = RenderResponse {
                    id: 0,
                    ok: false,
                    path: None,
                    error: Some(format!("decode request: {error}")),
                    render_s: start.elapsed().as_secs_f64(),
                };
                writeln!(stdout, "{}", serde_json::to_string(&response)?)?;
                stdout.flush()?;
                continue;
            }
        };
        let id = request.id;
        let response = match renderer.render(&request) {
            Ok(path) => RenderResponse {
                id,
                ok: true,
                path: Some(path.to_string_lossy().to_string()),
                error: None,
                render_s: start.elapsed().as_secs_f64(),
            },
            Err(error) => RenderResponse {
                id,
                ok: false,
                path: None,
                error: Some(format!("{error:#}")),
                render_s: start.elapsed().as_secs_f64(),
            },
        };
        writeln!(stdout, "{}", serde_json::to_string(&response)?)?;
        stdout.flush()?;
    }
    Ok(())
}

fn run_cli() -> Result<()> {
    let mut output: Option<PathBuf> = None;
    let mut text_file: Option<PathBuf> = None;
    let mut runs: usize = 1;
    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--serve" => return run_server(),
            "--output" => output = args.next().map(PathBuf::from),
            "--text-file" => text_file = args.next().map(PathBuf::from),
            "--runs" => runs = args.next().unwrap_or_else(|| "1".into()).parse().unwrap_or(1),
            _ => {}
        }
    }

    let output = output.context("--output is required")?;
    let text_file = text_file.context("--text-file is required")?;
    let text =
        fs::read_to_string(&text_file).with_context(|| format!("read {}", text_file.display()))?;
    let temp_dir = output
        .parent()
        .ok_or_else(|| anyhow!("output path has no parent"))?
        .to_string_lossy()
        .to_string();
    let stem = output
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("rust_render")
        .trim_end_matches("_text")
        .to_string();
    let request = RenderRequest {
        id: 1,
        tweet_text: text,
        temp_dir,
        tweet_identifier: stem,
        tweet_info: serde_json::json!({
            "date": "2026-05-30T12:00:00",
            "view_count": 123000,
            "author": {
                "name": "example",
                "nick": "Example User",
                "blue_verified": true
            }
        }),
        quoted_post: None,
    };
    let mut renderer = Renderer::new()?;
    let mut timings = Vec::new();
    for _ in 0..runs {
        let start = Instant::now();
        let actual = renderer.render(&request)?;
        if actual != output {
            fs::copy(&actual, &output)?;
        }
        timings.push(start.elapsed().as_secs_f64());
    }
    let mean = timings.iter().sum::<f64>() / timings.len() as f64;
    let mut sorted = timings.clone();
    sorted.sort_by(|a, b| a.total_cmp(b));
    let median = sorted[sorted.len() / 2];
    println!(
        "runs={} median={:.6}s mean={:.6}s all={}",
        runs,
        median,
        mean,
        timings
            .iter()
            .map(|value| format!("{value:.6}"))
            .collect::<Vec<_>>()
            .join("|")
    );
    Ok(())
}

fn main() -> Result<()> {
    run_cli()
}
