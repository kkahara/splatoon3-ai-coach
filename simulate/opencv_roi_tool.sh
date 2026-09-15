# start UI for general use
# use a picker
python3 tools/roi_calibrate.py --name specials_loaded
# specify a file name
python3 tools/roi_calibrate.py --name ready 

python3 tools/roi_calibrate.py --name low_ink 


# preview to union regions for map ink only
python tools/map_ink_roi_preview.py --stage inkblot_art_academy \
  calibration/reference/paint_map/recon/inkblot_art_academy.png

# Ad-hoc boxes (omit image path → native file picker)
python tools/roi_visualize.py --roi '[0.44,0.06,0.73,0.28]' --name R01
python tools/roi_visualize.py \
  --roi R01='[0.44,0.06,0.73,0.28]' \
  --roi R02='[0.50,0.20,0.80,0.40]' \
  -o /tmp/rois.jpg

python tools/roi_visualize.py --roi '[0.30, 0.28, 0.70, 0.52]' -o ./calibration/reference/reverse_roi2.jpg
python tools/roi_visualize.py --roi '[0.743229, 0.905556, 0.998437, 0.975000]' -o ./calibration/reference/stage_roi.jpg

[0.385417, 0.461111, 0.614062, 0.570370]
[0.30, 0.28, 0.70, 0.52]

# Stage pack (ink-map workflow) — also omit path to pick a frame
python tools/roi_visualize.py --stage inkblot_art_academy
python tools/roi_visualize.py --geometry configs/stage_maps/inkblot_art_academy/default.yaml

# Playable-stage polygon mask (normalized). Click vertices on a real map frame.
# Do not invent production coordinates — calibrate visually, then preview.
python tools/stage_mask_calibrate.py --stage mahi_mahi_resort path/to/map_overlay_frame.jpg
python tools/stage_mask_calibrate.py --stage mahi_mahi_resort --view \
  configs/stage_maps/mahi_mahi_resort/stage_mask.yaml path/to/map_overlay_frame.jpg

# Manta / Museum calibrate frames (prep under analysis/map_ink_validation/stage_mask_calibrate/)
# Prefer *_game.png + verified video frames (do not invent vertices).
python tools/stage_mask_calibrate.py --stage manta_maria \
  analysis/map_ink_validation/stage_mask_calibrate/manta_maria_game.png
python tools/stage_mask_calibrate.py --stage museum_dalfonsino \
  analysis/map_ink_validation/stage_mask_calibrate/museum_dalfonsino_game.png

# Mahi-Mahi (Sep-10 map overlay frame)
python tools/stage_mask_calibrate.py --stage mahi_mahi_resort \
  --overlay analysis/map_ink_validation/stage_mask_calibrate/mahi_mask_overlay.jpg \
  analysis/map_ink_validation/stage_mask_calibrate/mahi_mahi_resort_game.jpg
# or: ./analysis/map_ink_validation/stage_mask_calibrate/CALIBRATE_MAHI.sh

# After YAML exists: paint % A/B (mask vs ROI union) + dual diagnostics
python tools/stage_mask_ab_compare.py --stage manta_maria \
  analysis/map_ink_validation/stage_mask_calibrate/manta_maria_game.png
python tools/stage_mask_ab_compare.py --stage museum_dalfonsino \
  analysis/map_ink_validation/stage_mask_calibrate/museum_dalfonsino_game.png
python tools/stage_mask_ab_compare.py --stage mahi_mahi_resort \
  analysis/map_ink_validation/stage_mask_calibrate/mahi_mahi_resort_game.jpg

bash analysis/map_ink_validation/stage_mask_calibrate/CALIBRATE_HAMMERHEAD.sh

cd /Users/kenjikahara/splatoon3-ai-coach
source .venv/bin/activate

python tools/stage_mask_calibrate.py --stage undertow_spillway \
  --overlay analysis/map_ink_validation/stage_mask_calibrate/undertow_mask_overlay.jpg \
  analysis/map_ink_validation/stage_mask_calibrate/undertow_spillway_game.jpg