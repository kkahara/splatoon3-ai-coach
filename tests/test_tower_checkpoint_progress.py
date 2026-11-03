"""Development measurements of operator-supplied ring candidates."""

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.tower_checkpoint_progress import (
    measure_checkpoint_ring_candidate,
)

DEVELOPMENT = PROJECT_ROOT / "analysis/development/tower_control"


@pytest.mark.parametrize(
    ("name", "top", "expected"),
    [
        ("makomart_112_5.png", 105, "full_or_nearly_full"),
        ("makomart_113_5.png", 105, "three_quarters_remaining"),
        ("makomart_candidate_168_5.png", 95, "half_remaining"),
        ("makomart_candidate_169_5.png", 95, "one_quarter_remaining"),
        ("makomart_candidate_170_5.png", 95, "unknown"),
    ],
)
def test_development_ring_candidates(name: str, top: int, expected: str) -> None:
    """Known center and color distinguish coarse development appearances."""
    crop = cv2.imread(str(DEVELOPMENT / name))
    assert crop is not None
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    image[top : top + crop.shape[0], 450:1470] = crop
    result = measure_checkpoint_ring_candidate(image, (1062 / 1920, 155 / 1080), 134)
    assert result.progress == expected
