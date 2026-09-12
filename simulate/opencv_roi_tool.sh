# start UI for general use
# use a picker
python3 tools/roi_calibrate.py --name specials_loaded
# specify a file name
python3 tools/roi_calibrate.py --name inkblot_art_academy 


# preview to union regions for map ink only
python tools/map_ink_roi_preview.py --stage inkblot_art_academy \
  calibration/reference/paint_map/recon/inkblot_art_academy.png

# Ad-hoc boxes (omit image path → native file picker)
python tools/roi_visualize.py --roi '[0.44,0.06,0.73,0.28]' --name R01
python tools/roi_visualize.py \
  --roi R01='[0.44,0.06,0.73,0.28]' \
  --roi R02='[0.50,0.20,0.80,0.40]' \
  -o /tmp/rois.jpg

python tools/roi_visualize.py --roi '[0.396875, 0.337037, 0.600000, 0.607407]' -o ./calibration/reference/reverse_roi.jpg
python tools/roi_visualize.py --roi '[0.743229, 0.905556, 0.998437, 0.975000]' -o ./calibration/reference/stage_roi.jpg


# Stage pack (ink-map workflow) — also omit path to pick a frame
python tools/roi_visualize.py --stage inkblot_art_academy
python tools/roi_visualize.py --geometry configs/stage_maps/inkblot_art_academy/default.yaml
