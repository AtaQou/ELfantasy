from __future__ import annotations

import pandas as pd
import numpy as np

from src.live.minutes import calibrate_cold_start_minutes, expected_role_label


def test_expected_role_label_uses_existing_minutes_role_boundaries() -> None:
    assert expected_role_label(28.0) == "STAR / PRIMARY"
    assert expected_role_label(22.0) == "STARTER"
    assert expected_role_label(14.0) == "ROTATION"
    assert expected_role_label(13.9) == "BENCH / LIMITED"


def test_cold_start_blend_only_changes_returners_with_zero_current_games() -> None:
    frame = pd.DataFrame({
        "player_id": ["star", "bench", "played", "new"],
        "baseline_expected_minutes": [24.0, 16.0, 24.0, 18.0],
        "previous_season_minutes_avg": [30.0, 10.0, 30.0, float("nan")],
        "previous_season_games": [30, 25, 30, 0],
        "season_games_before": [0, 0, 1, 0],
    })

    result = calibrate_cold_start_minutes(frame)

    assert np.allclose(
        result["baseline_expected_minutes"], [25.8, 14.2, 24.0, 18.0]
    )
    assert result["uncalibrated_expected_minutes"].tolist() == [24.0, 16.0, 24.0, 18.0]
    assert result["minutes_calibration_weight"].tolist() == [0.3, 0.3, 0.0, 0.0]
