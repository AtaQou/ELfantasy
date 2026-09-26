"""Conservative current-season context, never historical training features."""
from __future__ import annotations

import hashlib
import numpy as np
import pandas as pd

PREPARATION_CONTEXT_VERSION = 'preparation_context_v4_50pct'
GAME_WEIGHTS = {'FRIENDLY':0.20, 'PRESEASON_TOURNAMENT':0.30,
                'SUPERCUP':0.65, 'DOMESTIC_OFFICIAL':0.75}


def attach_preparation_context(connection, frame, season_code, cutoff):
    out = frame.copy()
    defaults = {'preparation_games':0,'preparation_weight':0.0,
                'preparation_minutes':np.nan,'preparation_starter_rate':np.nan,
                'preparation_pir_per_minute':np.nan,
                'preparation_fp_per_minute':np.nan,
                'preparation_latest_game':None,'preparation_evidence_fingerprint':None}
    for key,value in defaults.items():
        out[key]=value
    out['preparation_context_version']=PREPARATION_CONTEXT_VERSION
    if out.empty:
        return out
    history=connection.execute('''
        WITH game_scores AS (
          SELECT g.preparation_game_id,
                 sum(s.points) FILTER (WHERE s.team_name_raw=g.home_team_name) AS home_score,
                 sum(s.points) FILTER (WHERE s.team_name_raw=g.away_team_name) AS away_score,
                 count(*) FILTER (WHERE s.team_name_raw=g.home_team_name) AS home_rows,
                 count(s.points) FILTER (WHERE s.team_name_raw=g.home_team_name) AS home_point_rows,
                 count(*) FILTER (WHERE s.team_name_raw=g.away_team_name) AS away_rows,
                 count(s.points) FILTER (WHERE s.team_name_raw=g.away_team_name) AS away_point_rows
          FROM preparation_games g JOIN preparation_player_stats s USING(preparation_game_id)
          GROUP BY g.preparation_game_id
        )
        SELECT s.*,g.game_date,g.game_type,g.season_code,
               score.home_score,score.away_score,score.home_rows,score.home_point_rows,
               score.away_rows,score.away_point_rows,g.home_team_name,g.away_team_name
        FROM preparation_player_stats s JOIN preparation_games g USING(preparation_game_id)
        JOIN game_scores score USING(preparation_game_id)
        JOIN raw_artifacts a ON a.artifact_id=s.source_artifact_id
        WHERE g.season_code=? AND g.game_date < ?
          AND g.game_date >= ?::TIMESTAMPTZ - INTERVAL 60 DAY
          AND a.fetched_at <= ? AND s.canonical_player_id IS NOT NULL
          AND s.canonical_team_id IS NOT NULL
          AND (s.minutes > 0 OR s.starter IS NOT NULL)
        QUALIFY row_number() OVER (
          PARTITION BY s.canonical_player_id,s.canonical_team_id
          ORDER BY g.game_date DESC,g.preparation_game_id DESC
        ) <= 5
    ''',[season_code,cutoff,cutoff,cutoff]).df()
    if history.empty:
        return out
    scoring_inputs = (
        'points','total_rebounds','assists','steals','blocks','fouls_drawn',
        'turnovers','blocks_received','fouls_committed','two_points_made',
        'two_points_attempted','three_points_made','three_points_attempted',
        'free_throws_made','free_throws_attempted',
    )
    complete = history[list(scoring_inputs)].notna().all(axis=1)
    score_known = (
        history.home_rows.gt(0) & history.away_rows.gt(0)
        & history.home_rows.eq(history.home_point_rows)
        & history.away_rows.eq(history.away_point_rows)
    )
    home = history.team_name_raw.eq(history.home_team_name)
    away = history.team_name_raw.eq(history.away_team_name)
    side_known = home | away
    base = (
        history.points + history.total_rebounds + history.assists
        + history.steals + history.blocks + history.fouls_drawn
        - history.turnovers - history.blocks_received - history.fouls_committed
        - (history.two_points_attempted-history.two_points_made)
        - (history.three_points_attempted-history.three_points_made)
        - (history.free_throws_attempted-history.free_throws_made)
    )
    won = (home & history.home_score.gt(history.away_score)) | (
        away & history.away_score.gt(history.home_score)
    )
    valid_fp = complete & score_known & side_known
    history['preparation_fantasy_points'] = np.where(
        valid_fp, base + np.where(won, base.abs()*.1, 0), np.nan,
    )
    history['weight']=history.game_type.map(GAME_WEIGHTS).astype(float)
    age=(pd.Timestamp(cutoff)-pd.to_datetime(history.game_date,utc=True)).dt.total_seconds()/86400
    history['weight'] *= np.exp2(-age/21.0)
    contexts={}
    for (pid,tid), group in history.groupby(['canonical_player_id','canonical_team_id']):
        total=group.weight.sum()
        def avg(column):
            valid=group[column].notna()
            return np.average(group.loc[valid,column].astype(float),weights=group.loc[valid,'weight']) if valid.any() else np.nan
        valid=group.preparation_fantasy_points.notna() & group.minutes.ge(5)
        fp_rate=(np.average(group.loc[valid,'preparation_fantasy_points']/group.loc[valid,'minutes'],weights=group.loc[valid,'weight']) if valid.any() else np.nan)
        contexts[(pid,tid)]={'preparation_games':len(group),
            'preparation_weight':min(.50,.50*total),
            'preparation_minutes':avg('minutes'),'preparation_starter_rate':avg('starter'),
            'preparation_pir_per_minute':fp_rate,
            'preparation_fp_per_minute':fp_rate,
            'preparation_latest_game':group.game_date.max().isoformat(),
            'preparation_evidence_fingerprint':hashlib.sha256(group[['preparation_player_stat_id','source_artifact_id']].to_json().encode()).hexdigest()}
    for index,row in out.iterrows():
        context=contexts.get((row.player_id,row.team_id))
        if context:
            for key,value in context.items():
                out.at[index,key]=value
    return out


def apply_preparation_context(frame):
    """Apply once after EuroLeague calibration, before availability redistribution.

    At most 50% of minutes and scoring rate comes from external games. The
    evidence weight still reflects competition strength and recency, with
    bounded per-minute differences. Fantasy form is calculated only when every
    verified scoring input and the game result are available; missing box-score
    values and source-specific efficiency fields are not zero-filled.
    """
    out=frame.copy()
    baseline=pd.to_numeric(out['baseline_expected_minutes'],errors='coerce')
    recent=pd.to_numeric(out.get('preparation_minutes',pd.Series(np.nan,index=out.index)),errors='coerce')
    weight=pd.to_numeric(out.get('preparation_weight',pd.Series(0.,index=out.index)),errors='coerce').fillna(0)
    eligible=baseline.notna() & recent.notna()
    weight=weight.where(eligible,0).clip(0,.50)
    adjusted=(baseline+weight*(recent-baseline).fillna(0)).clip(0,40)
    out['preparation_baseline_minutes']=baseline
    out['preparation_minutes_delta']=adjusted-baseline
    out['baseline_expected_minutes']=adjusted
    starter = pd.to_numeric(out.get('preparation_starter_rate', pd.Series(np.nan, index=out.index)), errors='coerce')
    prior_starter = pd.to_numeric(out.get('rot_last5_starter_rate', pd.Series(np.nan, index=out.index)), errors='coerce')
    out['preparation_expected_starter_rate'] = prior_starter
    known_starter = starter.notna() & weight.gt(0)
    out.loc[known_starter, 'preparation_expected_starter_rate'] = (
        (1-weight[known_starter])*prior_starter[known_starter].fillna(.5)
        + weight[known_starter]*starter[known_starter]
    )
    rate=pd.to_numeric(out['expected_fp_per_min'],errors='coerce')
    recent_rate=pd.to_numeric(out.get(
        'preparation_fp_per_minute',
        out.get('preparation_pir_per_minute',pd.Series(np.nan,index=out.index)),
    ),errors='coerce')
    form=(recent_rate-rate).clip(-.5,.5).fillna(0)*weight*adjusted
    out['preparation_form_fp_delta']=form.fillna(0)
    out['preparation_fp_delta']=((adjusted-baseline)*rate+out.preparation_form_fp_delta).fillna(0).clip(-5,5)
    out['preparation_role_change']=np.select(
        [out.preparation_minutes_delta.ge(.5),out.preparation_minutes_delta.le(-.5)],
        ['TRENDING UP','TRENDING DOWN'],default='STABLE')
    return out
