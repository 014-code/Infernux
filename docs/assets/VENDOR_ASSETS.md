# Website vendor assets

These files are committed so the GitHub Pages experience does not depend on third-party font or icon CDNs at runtime.

| Local file | Upstream | Version/source | SHA-256 |
|---|---|---|---|
| `fonts/source-han-sans-sc-subset.woff2` | Google Noto Sans SC (Source Han Sans family) | `NotoSansSC-VF.ttf`; website character subset | `98038ca3eba66feb91ddec25f83bf0115e54a021b1e1a2f480b173f012b5ed8c` |
| `fonts/inter-latin.woff2` | Google Fonts / Inter | `fonts.gstatic.com/s/inter/v20` | `3100e775e8616cd2611beecfa23a4263d7037586789b43f035236a2e6fbd4c62` |
| `fonts/jetbrains-mono-latin.woff2` | Google Fonts / JetBrains Mono | `fonts.gstatic.com/s/jetbrainsmono/v24` | `83c005d49d8a6a50474c73a5a36ac0468076e9c4a29da7bdb14995d80560a5be` |
| `fonts/space-grotesk-latin.woff2` | Google Fonts / Space Grotesk | `fonts.gstatic.com/s/spacegrotesk/v22` | `0640890476fc1198ab4de571fb658de443c4d85b66466ec09534a8737ab1ce9d` |
| `fonts/fa-solid-subset-900.woff2` | Font Awesome Free | `6.4.0`; 31-glyph site subset | `1ab0dea7613a56456bd30de51fee7d0fccb6def013fe1f46862e2eb204fba343` |
| `fonts/fa-brands-subset-400.woff2` | Font Awesome Free | `6.4.0`; GitHub + Python subset | `7d7c0b8449df96bbfdc8b4e6c6740ce2337af2c90363a5213713977df3e7ae76` |

Font license texts are preserved in `vendor-licenses/`. When any asset changes, update its version/source, checksum, license, and the static-site verifier in the same change.

The `source-han-sans-sc-subset.woff2` file is a character subset of Google Noto Sans SC, a Source Han Sans family font, distributed under SIL OFL 1.1; its license is preserved in `vendor-licenses/Noto-Sans-SC-OFL.txt`.

## Project-authored visual assets

`infernux-social-card-0.3.4.jpg` is the reviewed 1200×630 Open Graph/X card for release 0.3.4. It is a center-cropped derivative of the original 1920×1032 editor capture (`demo.png`, since retired from the repository) rather than separate promotional artwork. Its SHA-256 is `c1bb18887d484776433a14f43bf2d77dde6a9fe4f8eabc077e6fbb541c273159`. The site verifier locks its format, dimensions, release-scoped filename, and reviewed content hash.

The original `demo.png` capture and its `demo-runtime.webp` / `demo-runtime.avif` homepage derivatives of the 65,536-object voxel continent were retired with the homepage redesign. The homepage shows the demo reel below.

### Demo reel

The homepage reel is cut from the author's own Bilibili video `BV1538P6jELT`, part 2 ("熔炉0.3.4演示Demo纯净版"), downloaded as the 852×480 H.264 stream. Each clip is cropped to 852×388 to drop the title and subtitle bands (and the channel watermark), resampled with Lanczos to the listed width, re-timed to 24 fps and encoded with ffmpeg 7.1 (imageio-ffmpeg): `libx264 -preset veryslow -crf 30 -profile:v high -pix_fmt yuv420p -an -movflags +faststart`. Posters are single frames saved with Pillow 12.2.0 as WebP (quality 68; the feature poster 72). Clips carry `preload="none"`, a `data-src` and a poster; `js/fx-hud.js` attaches the source and plays a clip only while it is visible, and never under reduced motion unless the visitor presses play.

| Local file | Source time | Geometry | Bytes | SHA-256 |
|---|---|---|---:|---|
| `reel/space-battle.mp4` | 01:24.5, 4.5 s | 852×388 | 175,117 | `30dec9c1672fb13316b54240787167bd2078d35f5324a24f983abeae2c2a1af8` |
| `reel/npr-pipeline.mp4` | 00:19.0, 4 s | 640×292 | 111,851 | `ba8095dbdc583e0da7c58cd95d3608af3178d5b0b3cd3710d3c2fea0999c3453` |
| `reel/rigid-coins.mp4` | 00:33.5, 4 s | 640×292 | 290,880 | `5471e528ceac863eb59f2f2e156b51a6a47e48e6e3f8ebe9807b4d599da51bb8` |
| `reel/animated-cats.mp4` | 00:58.0, 4 s | 640×292 | 98,641 | `a6e4349dc2167253124ec1d1440c96fa81ad722c6786c9fd1dbaced199803856` |

`docs/tools/check-image-variants.mjs` locks every clip and poster by content hash, checks the MP4 `ftyp`/`moov`-before-`mdat` layout and poster dimensions, enforces per-clip and total size limits, and verifies the lazy `<video>` markup. The performance budget counts reel posters in the homepage first view while excluding the clips themselves.

### Install and touch icons

The engine emblem is a project-authored hollow red tapered monolith with rising diagonal stripes. The inset and stripe gaps are transparent, not painted white. The top is 80 units wide and the base 129.442 units wide (a ratio of approximately 1:1.618), with a 224-unit height for a slender silhouette. Its editable source is `python/infernux/resources/icons/icon.svg`. The geometry uses filled paths without clipping, filters or embedded images so browser and Qt rendering agree.

Run `python scripts/maintenance/generate_brand_assets.py` in the `infernux` environment to regenerate the complete set. `logo.svg` is a byte-for-byte copy of the engine source and is used directly by website navigation, the API header, the offline page and both READMEs. `logo-wordmark.svg` adds outlined Space Grotesk Bold lettering and a red rule; the second word inherits `currentColor` for light or dark surfaces. Its font source and SIL OFL license are preserved under `packaging/resources/fonts/`.

The raster assets are rendered from that SVG with Qt SVG at four times their target size and downsampled with Pillow Lanczos. Desktop icons retain a transparent background. Install icons are composited over the site background `#0a0c11` and saved as optimized 256-color opaque PNGs. The maskable emblem remains inside the Web App Manifest safe-zone circle (radius 40% of the canvas).

| Local file | Role and geometry |
|---|---|
| `logo.png` | Structured-data organisation logo; 256×256, transparent; identical to the engine and Hub PNG |
| `favicon-64.png` | Browser favicon; 64×64, transparent |
| `infernux-icon-192.png` | Chromium install icon; 192×192, opaque |
| `infernux-icon-512.png` | Chromium install/splash icon; 512×512, opaque |
| `infernux-icon-maskable-512.png` | Adaptive launcher icon; 512×512, opaque, safe-zone padded |
| `infernux-apple-touch-icon.png` | Apple home-screen icon; 180×180, opaque |

The same command updates `packaging/resources/icon.png` for Hub and installer windows, plus the engine `icon.png`, the `scene.png` asset emblem and multi-resolution `icon.ico` for Editor, Player and Windows executable resources. The scene emblem is identical to the application PNG, so scene assets follow the same brand. ICO sizes are 16, 24, 32, 48, 64, 128 and 256 pixels.

`docs/tools/check-pwa-assets.mjs` checks PNG signatures, dimensions, opacity, the maskable safe zone, manifest roles, HTML links, provenance and Service Worker loading. Regenerate and visually review the complete set together when the emblem changes.

## Font Awesome subsetting

The two Font Awesome files are generated from the official 6.4.0 `webfonts` files with the `pyftsubset` executable provided by the repository's `infernux` environment. The solid subset contains the code points declared by the first `unicode-range` in `css/fontawesome-subset.css`; the brand subset contains `U+F09B` (GitHub) and `U+F3E2` (Python). When a new icon class is introduced, regenerate the matching WOFF2, update its `unicode-range` and checksum, then run the website verifier. The verifier rejects missing CSS mappings, unexpected font hashes, and Font Awesome files large enough to indicate that a complete upstream font was restored.
