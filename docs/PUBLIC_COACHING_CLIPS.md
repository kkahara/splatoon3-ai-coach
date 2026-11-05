# Public coaching clips

The public result page offers a playable MP4 for each indexed coaching moment,
using the existing still as its poster and fallback. No autoplay or upfront video
download is requested. Social sharing continues to use JPEG previews.

After `coaching_index.json` is available, the worker extracts from the original
source recording, not the analysis cadence frames. Each clip spans five recording
seconds before the moment anchor and three after. Death-episode anchors are death
video times. At file boundaries the clip is shorter; no padding or invented
frames are added. This is presentation media, not new analysis or LLM evidence.

Clips are H.264/yuv420p MP4 with fast-start metadata, constrained to 1280×720
without upscaling, preserving source cadence up to 60 fps. A 30 fps source stays
30 fps. Source audio, when present, is encoded as AAC. The existing FFmpeg installation needs libx264.

Files are atomically stored at `analysis/<submission>/public_clips/<safe_id>.mp4`.
Guest token and authenticated owner endpoints mirror still-image access checks;
FileResponse supports byte-range playback. Result expiry removes the entire
analysis directory, including clips and stills.

Source deletion follows successful clip extraction. Failed clips retain the
source for a retry when the result is requested, or deletion at normal expiry.
The result still completes and can show its still if clip extraction fails.
Development mode retains sources as before. Historical results whose sources
have already been deleted cannot acquire clips and continue to show stills.

The existing `frames` operational stage includes both clip and still generation;
its duration therefore includes video encoding. No telemetry schema or analysis,
scenario, coaching, or evidence contract is changed.
