"""crop_at.py <video> <gate:intro|callout> <t> <out.png>"""
import sys, cv2
from pathlib import Path
from loguru import logger; logger.remove()
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.media.video import VideoLoader
from splatoon3_ai_coach.vision.roi import crop_roi
from splatoon3_ai_coach.vision.game_language import GameLanguageProbe
cfg = load_config(default_config_path()); probe = GameLanguageProbe(cfg.vision)
video, gate, t, out = Path(sys.argv[1]), sys.argv[2], float(sys.argv[3]), Path(sys.argv[4])
roi = cfg.vision.match_intro.battle_mode_roi if gate == "intro" else cfg.vision.game_language.roi
m = probe.intro_matcher if gate == "intro" else probe.callout_matcher
with VideoLoader(video, max_width=1920, max_height=1080) as loader:
    for f in loader.frames():
        if f.timestamp >= t:
            r = m.observe(f.image)
            print(video.name, f.timestamp, f.frame_index, r.candidate_language, r.cue_id, r.score, r.runner_up_score, r.accepted)
            cv2.imwrite(str(out), crop_roi(f.image, roi)); break
