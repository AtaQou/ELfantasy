"""Retained official non-EuroLeague boxes for live context only.

Provider identities are resolved to existing players; unmatched opponents are
retained with null canonical IDs, never inserted as duplicate players.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import re

from src.data.fantasy_identity import normalize_person_name
from .database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from .identity_resolution import official_name_variants
from .ids import stable_id
from .ingestion import CanonicalIngestor
from .raw_store import describe_existing_file


class BoxTables(HTMLParser):
    """Small HTML table reader; no additional scraping dependency."""
    def __init__(self, text):
        super().__init__()
        self.tables, self.table, self.row, self.cell = [], None, None, None
        self.starter_rows = set()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if self.row is not None and any('playerStarterIcon' in value for _, value in attrs if value):
            self.starter_rows.add(id(self.row))
        if tag == 'table':
            self.table = []
            self.tables.append(self.table)
        elif tag == 'tr':
            self.row = []
        elif tag in ('td', 'th'):
            if self.cell is not None:
                self.handle_endtag('td')
            self.cell = []

    def handle_data(self, text):
        if self.cell is not None:
            self.cell.append(text)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            if self.row is not None:
                self.row.append(' '.join(' '.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.table is not None and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == 'table':
            self.table = None


def minutes(value):
    if value is None or str(value).strip() in ('', '-', 'DNP', 'NPJ'):
        return None
    value = str(value).strip()
    if value.startswith('PT'):
        match = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?', value)
        if not match:
            raise ValueError(f'Invalid duration: {value}')
        h, m, s = (float(x or 0) for x in match.groups())
        return h * 60 + m + s / 60
    if ':' in value:
        m, s = value.split(':')
        return float(m) + float(s) / 60
    return float(value)


def number(value):
    return None if value is None or str(value).strip() in ('', '-', '—', '–', 'N/A') else int(value)


def shots(row, value, prefix):
    if '/' in value:
        made, attempted = value.split('/')
        row[prefix + '_made'], row[prefix + '_attempted'] = number(made), number(attempted)


def parse_html(text, spec):
    parsed = BoxTables(text)
    tables = parsed.tables
    acb = spec['parser'] == 'acb'
    boxes = [t for t in tables if any('Jugador' in r if acb else 'PIR' in r and 'Player' in r for r in t)]
    if len(boxes) < 2:
        raise ValueError('Full player box tables missing')
    result = []
    for side, table in zip(('home', 'away'), boxes[:2]):
        for values in table:
            if acb:
                if len(values) != 22 or not re.match(r'^\d+\s', values[0]):
                    continue
                name = re.sub(r'^\d+\s+', '', values[0])
                row = {'player_name_raw': name, 'source_player_id': name, 'minutes': minutes(values[1])}
                row['starter'] = id(values) in parsed.starter_rows
                positions = {'points':2,'offensive_rebounds':9,'defensive_rebounds':10,'total_rebounds':11,'assists':12,'turnovers':13,'steals':14,'blocks':15,'blocks_received':16,'fouls_committed':18,'fouls_drawn':19,'plus_minus':20,'pir':21}
                for index, prefix in [(3,'two_points'),(5,'three_points'),(7,'free_throws')]:
                    shots(row, values[index], prefix)
            else:
                if len(values) != 18 or not values[0].isdigit():
                    continue
                name = values[1]
                row = {'player_name_raw':name,'source_player_id':name,'minutes':minutes(values[2])}
                positions = {'points':3,'offensive_rebounds':7,'defensive_rebounds':8,'total_rebounds':9,'assists':10,'steals':11,'turnovers':12,'blocks':13,'blocks_received':14,'fouls_committed':15,'fouls_drawn':16,'pir':17}
                for index, prefix in [(4,'two_points'),(5,'three_points'),(6,'free_throws')]:
                    shots(row, values[index], prefix)
            row.update({k:number(values[v]) for k,v in positions.items()})
            row['team_name_raw'] = spec[side]
            row['team_code'] = spec.get(side+'_code')
            # No starter inference from display order or minutes.
            result.append(row)
    return result


def next_data(text):
    return json.loads(re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S).group(1))['props']['pageProps']


def parse_bbl(text, spec):
    data = next_data(text)['initialGameStats']
    if data['status'] != 'OFFICIAL':
        raise ValueError('BBL game is not official/final')
    result = []
    fields = {'points':'points','two_points_made':'twoPointShotsMade','two_points_attempted':'twoPointShotsAttempted','three_points_made':'threePointShotsMade','three_points_attempted':'threePointShotsAttempted','free_throws_made':'freeThrowsMade','free_throws_attempted':'freeThrowsAttempted','offensive_rebounds':'offensiveRebounds','defensive_rebounds':'defensiveRebounds','total_rebounds':'totalRebounds','assists':'assists','steals':'steals','turnovers':'turnovers','blocks':'blocks','fouls_committed':'foulsCommitted','fouls_drawn':'foulsReceived','efficiency':'efficiency','plus_minus':'plusMinus'}
    for side, key in [('home','homeTeam'),('away','guestTeam')]:
        for p in data[key]['playerStats']:
            person = p['seasonPlayer']
            row = {k:p.get(v) for k,v in fields.items()}
            row.update(player_name_raw=person['firstName']+' '+person['lastName'],source_player_id=str(person['playerId']),minutes=p['secondsPlayed']/60,starter=p.get('isStartingFive'),team_name_raw=spec[side],team_code=spec.get(side+'_code'))
            result.append(row)
    return result


def parse_vtb(text, spec):
    data = json.loads(text)
    result = []
    fields = {'points':'Points','two_points_made':'Goal2','two_points_attempted':'Shot2','three_points_made':'Goal3','three_points_attempted':'Shot3','free_throws_made':'Goal1','free_throws_attempted':'Shot1','offensive_rebounds':'OffRebound','defensive_rebounds':'DefRebound','total_rebounds':'Rebound','assists':'Assist','steals':'Steal','turnovers':'Turnover','blocks':'Blocks','fouls_committed':'Foul','fouls_drawn':'OpponentFoul','plus_minus':'PlusMinus'}
    for side, team in zip(('home','away'), data['GameTeams']):
        for p in team['Players']:
            row = {k:p.get(v) for k,v in fields.items()}
            # Explicit shooting strings preserve published zero makes.
            for n, prefix in [(1,'free_throws'),(2,'two_points'),(3,'three_points')]:
                shots(row,p.get(f'Shots{n}') or '',prefix)
            row.update(player_name_raw=p['FirstNameEn']+' '+p['LastNameEn'],source_player_id=str(p['PersonID']),minutes=p['Seconds']/60 if p.get('Seconds') is not None else None,starter=p.get('IsStart'),team_name_raw=spec[side],team_code=spec.get(side+'_code'))
            # The final VTB feed uses null for zero counting events. Keep DNPs
            # missing; for participants derive standard EFF, not EuroLeague PIR.
            if (p.get('Seconds') or 0) > 0:
                for key in fields:
                    if key != 'plus_minus':
                        row[key] = row[key] or 0
                row['efficiency'] = (
                    sum(row[k] for k in ('points','total_rebounds','assists','steals','blocks'))
                    - sum(row[k+'_attempted']-row[k+'_made'] for k in ('two_points','three_points','free_throws'))
                    - row['turnovers']
                )
            result.append(row)
    return result


def parse_lnb(text, spec):
    data = json.loads(text)['data']['statistics']['data']['base']
    fields = {'points':'points','two_points_made':'pointsTwoMade','two_points_attempted':'pointsTwoAttempted','three_points_made':'pointsThreeMade','three_points_attempted':'pointsThreeAttempted','free_throws_made':'freeThrowsMade','free_throws_attempted':'freeThrowsAttempted','offensive_rebounds':'reboundsOffensive','defensive_rebounds':'reboundsDefensive','total_rebounds':'rebounds','assists':'assists','steals':'steals','turnovers':'turnovers','blocks':'blocks','blocks_received':'blocksReceived','fouls_committed':'foulsTotal','efficiency':'efficiency','plus_minus':'plusMinus'}
    result = []
    for side in ('home','away'):
        for p in data[side]['persons'][0]['rows']:
            stat=p['statistics'];row={k:stat.get(v) for k,v in fields.items()}
            row.update(player_name_raw=p['personName'],source_player_id=p['personId'],minutes=minutes(stat.get('minutes')),starter=p.get('starter'),team_name_raw=spec[side],team_code=spec.get(side+'_code'))
            result.append(row)
    return result


def parse_lkl(text, spec):
    data = json.loads(text)['boxscore']
    result = []
    fields = {'points':'points','offensive_rebounds':'offensive_rebounds','defensive_rebounds':'defensive_rebounds','total_rebounds':'total_rebounds','assists':'assists','steals':'steals','turnovers':'turnovers','blocks':'blocks','blocks_received':'blocks_received','fouls_committed':'fouls','fouls_drawn':'fouls_on','efficiency':'efficiency','plus_minus':'plus_minus'}
    for side in ('home','away'):
        for p in data[side]['players']:
            row = {k:number(p.get(v,{}).get('value')) for k,v in fields.items()}
            for key,prefix in [('fg2','two_points'),('fg3','three_points'),('ft','free_throws')]:
                shots(row,str(p.get(key,{}).get('value','')),prefix)
            row.update(player_name_raw=p['name']['value'],source_player_id=p['slug'],minutes=minutes(p['time']['value']),starter=p.get('is_starter'),team_name_raw=spec[side],team_code=spec.get(side+'_code'))
            result.append(row)
    return result


def parse_lba(text, spec):
    data=json.loads(text)['scores']
    result=[]
    fields={'points':'pun','two_points_made':'t2_r','two_points_attempted':'t2_t','three_points_made':'t3_r','three_points_attempted':'t3_t','free_throws_made':'tl_r','free_throws_attempted':'tl_t','offensive_rebounds':'rimbalzi_o','defensive_rebounds':'rimbalzi_d','total_rebounds':'rimbalzi_t','assists':'ass','steals':'palle_r','turnovers':'palle_p','blocks':'stoppate_dat','blocks_received':'stoppate_sub','fouls_committed':'falli_c','fouls_drawn':'falli_sf','efficiency':'val_lega','plus_minus':'plus_minus'}
    for side,key in [('home','ht'),('away','vt')]:
        for p in data[key]['rows']:
            if not p.get('player_id'):
                continue
            row={k:p.get(v) for k,v in fields.items()}
            row.update(player_name_raw=p['player_name']+' '+p['player_surname'],source_player_id=str(p['player_id']),minutes=p['sec']/60,starter=str(p.get('sf'))=='1',team_name_raw=spec[side],team_code=spec.get(side+'_code'))
            result.append(row)
    return result


def parse_lnb_pre(text, spec):
    result=[]
    boxes=[t for t in BoxTables(text).tables if any('Joueur' in r for r in t)]
    for side,table in zip(('home','away'),boxes):
        has_starters = any(len(p) == 20 and p[2] == 'x' for p in table)
        for p in table:
            if len(p)!=20 or not p[0].isdigit():
                continue
            row={'player_name_raw':p[1],'source_player_id':p[1],
                 'minutes':minutes(p[3]),'starter':(p[2]=='x') if has_starters else None,
                 'team_name_raw':spec[side],'team_code':spec.get(side+'_code')}
            fields={'points':4,'offensive_rebounds':9,'defensive_rebounds':10,'total_rebounds':11,'assists':12,'fouls_committed':13,'fouls_drawn':14,'steals':15,'turnovers':16,'blocks':17,'blocks_received':18,'efficiency':19}
            row.update({k:number(p[v]) for k,v in fields.items()})
            fg=[int(x.strip()) for x in p[5].split(' - ')]
            three=[int(x.strip()) for x in p[7].split(' - ')]
            shots(row,p[7].replace(' - ','/'),'three_points')
            shots(row,p[8].replace(' - ','/'),'free_throws')
            row['two_points_made']=fg[0]-three[0]
            row['two_points_attempted']=fg[1]-three[1]
            result.append(row)
    return result


def parse_givemestats(text, spec):
    """Parse the public GIVEMESTATS player box tables.

    Its first shooting column is labelled FG but contains two-point makes and
    attempts (the displayed percentage and points totals confirm this).
    Missing cells remain null throughout.
    """
    boxes = [
        table for table in BoxTables(text).tables
        if any('Player' in row and 'MIN' in row and 'PTS' in row for row in table)
    ]
    if len(boxes) < 2:
        raise ValueError('GIVEMESTATS full player box tables missing')
    result = []
    for side, table in zip(('home', 'away'), boxes[:2]):
        for values in table:
            if len(values) != 19 or values[1] in ('Player', 'TOTAL', 'Team', 'Team Totals'):
                continue
            row = {
                'player_name_raw': values[1],
                'source_player_id': values[1],
                'minutes': minutes(values[2]),
                'starter': None,
                'team_name_raw': spec[side],
                'team_code': spec.get(side + '_code'),
            }
            fields = {
                'points': 3, 'offensive_rebounds': 8,
                'defensive_rebounds': 9, 'total_rebounds': 10,
                'assists': 11, 'steals': 12, 'blocks': 13,
                'blocks_received': 14, 'turnovers': 15,
                'fouls_committed': 16, 'fouls_drawn': 17,
                'efficiency': 18,
            }
            row.update({key: number(values[index]) for key, index in fields.items()})
            shots(row, values[4], 'two_points')
            shots(row, values[5], 'three_points')
            shots(row, values[6], 'free_throws')
            # A few source pages contain an impossible attempts typo (for
            # example 2/0). Preserve the published makes and leave attempts
            # unknown instead of fabricating a correction.
            for prefix in ('two_points', 'three_points', 'free_throws'):
                if (row.get(prefix + '_made') is not None
                        and row.get(prefix + '_attempted') is not None
                        and row[prefix + '_made'] > row[prefix + '_attempted']):
                    row[prefix + '_attempted'] = None
            result.append(row)
    return result


def parse_fiba(text, spec):
    data = json.loads(text)
    result = []
    fields = {
        'points': 'sPoints',
        'two_points_made': 'sTwoPointersMade',
        'two_points_attempted': 'sTwoPointersAttempted',
        'three_points_made': 'sThreePointersMade',
        'three_points_attempted': 'sThreePointersAttempted',
        'free_throws_made': 'sFreeThrowsMade',
        'free_throws_attempted': 'sFreeThrowsAttempted',
        'offensive_rebounds': 'sReboundsOffensive',
        'defensive_rebounds': 'sReboundsDefensive',
        'total_rebounds': 'sReboundsTotal',
        'assists': 'sAssists', 'steals': 'sSteals',
        'turnovers': 'sTurnovers', 'blocks': 'sBlocks',
        'blocks_received': 'sBlocksReceived',
        'fouls_committed': 'sFoulsPersonal',
        'fouls_drawn': 'sFoulsOn', 'plus_minus': 'sPlusMinusPoints',
        'efficiency': 'eff_1',
    }
    team_order = spec.get('team_order', {'1': 'home', '2': 'away'})
    for team_key, side in team_order.items():
        for source_id, player in data['tm'][team_key]['pl'].items():
            name = ' '.join(filter(None, (
                player.get('internationalFirstName') or player.get('firstName'),
                player.get('internationalFamilyName') or player.get('familyName'),
            )))
            row = {key: player.get(source) for key, source in fields.items()}
            row.update(
                player_name_raw=name,
                source_player_id=str(source_id),
                minutes=minutes(player.get('sMinutes')),
                starter=bool(player['starter']) if player.get('starter') is not None else None,
                team_name_raw=spec[side],
                team_code=spec.get(side + '_code'),
            )
            result.append(row)
    return result


def parse_israel(text, spec):
    boxes = [
        table for table in BoxTables(text).tables
        if any('Player Name' in row and 'Min' in row and 'VAL' in row for row in table)
    ]
    if len(boxes) < 2:
        raise ValueError('Israeli official full player box tables missing')
    result = []
    for side, table in zip(('home', 'away'), boxes[:2]):
        for values in table:
            if len(values) != 23 or not values[0].isdigit() or values[1] == 'Team':
                continue
            row = {
                'player_name_raw': values[1],
                'source_player_id': values[1],
                'minutes': minutes(values[3]),
                'starter': values[2] == '*',
                'team_name_raw': spec[side],
                'team_code': spec.get(side + '_code'),
            }
            fields = {
                'points': 4, 'defensive_rebounds': 11,
                'offensive_rebounds': 12, 'total_rebounds': 13,
                'fouls_committed': 14, 'fouls_drawn': 15,
                'steals': 16, 'turnovers': 17, 'assists': 18,
                'blocks': 19, 'blocks_received': 20,
                'efficiency': 21, 'plus_minus': 22,
            }
            row.update({key: number(values[index]) for key, index in fields.items()})
            for index, prefix in ((5, 'two_points'), (7, 'three_points'), (9, 'free_throws')):
                shots(row, values[index], prefix)
            result.append(row)
    return result


def parse_rincon(text, spec):
    boxes = [
        table for table in BoxTables(text).tables
        if any('Jugador' in row and 'Min' in row and 'Val' in row for row in table)
    ]
    if len(boxes) < 2:
        raise ValueError('Rincon full player box tables missing')
    result = []
    for side, table in zip(('home', 'away'), boxes[:2]):
        for values in table:
            if len(values) != 15 or values[0] in ('Jugador', 'Totales'):
                continue
            row = {
                'player_name_raw': values[0], 'source_player_id': values[0],
                'minutes': minutes(values[1]), 'starter': None,
                'points': number(values[2]), 'total_rebounds': number(values[6]),
                'assists': number(values[8]), 'turnovers': number(values[9]),
                'steals': number(values[10]), 'plus_minus': number(values[13]),
                'efficiency': number(values[14]),
                'team_name_raw': spec[side], 'team_code': spec.get(side + '_code'),
            }
            for index, prefix in ((3, 'two_points'), (4, 'three_points'), (5, 'free_throws')):
                shots(row, values[index], prefix)
            pairs = (
                (7, 'offensive_rebounds', 'defensive_rebounds'),
                (11, 'blocks', 'blocks_received'),
                (12, 'fouls_committed', 'fouls_drawn'),
            )
            for index, first, second in pairs:
                if '/' in values[index]:
                    one, two = values[index].split('/', 1)
                    row[first], row[second] = number(one), number(two)
            result.append(row)
    return result


def parse_flashscore(text, spec):
    """Parse Flashscore's public basketball player-statistics feed."""
    result = []
    seen = set()
    sides = {
        spec['home_source_code']: 'home',
        spec['away_source_code']: 'away',
    }
    fields = (
        'points', 'total_rebounds', 'assists', 'minutes',
        None, None,  # Overall field goals are redundant with 2P + 3P.
        'two_points_made', 'two_points_attempted',
        'three_points_made', 'three_points_attempted',
        'free_throws_made', 'free_throws_attempted', 'plus_minus',
        'offensive_rebounds', 'defensive_rebounds', 'fouls_committed',
        'steals', 'turnovers', 'blocks', 'blocks_received',
        None,  # Technical fouls have no retained preparation-stat column.
    )
    for block in text.split('¬~'):
        published = dict(
            part.split('÷', 1) for part in block.split('¬') if '÷' in part
        )
        if not {'PJ', 'PK', 'PN', 'PC'} <= published.keys():
            continue
        side = sides.get(published['PN'])
        values = published['PC'].split('|')
        if side is None or len(values) != len(fields):
            continue
        source_player_id = published['PK'].strip('/').split('/')[-1]
        if source_player_id in seen:
            continue
        seen.add(source_player_id)

        # The visible name is surname + initial; the public player URL supplies
        # the unabbreviated given name(s), so retain a useful full raw name.
        display_surname = published['PJ'].rsplit(' ', 1)[0]
        slug = published['PK'].strip('/').split('/')[-2]
        surname_slug = re.sub(r'[^a-z0-9-]', '', display_surname.lower())
        given_slug = slug[len(surname_slug) + 1:] if slug.startswith(surname_slug + '-') else ''
        player_name = (
            f"{given_slug.replace('-', ' ').title()} {display_surname}"
            if given_slug else published['PJ']
        )
        row = {
            'player_name_raw': player_name,
            'source_player_id': source_player_id,
            'starter': None,
            'team_name_raw': spec[side],
            'team_code': spec.get(side + '_code'),
        }
        for key, value in zip(fields, values):
            if key == 'minutes':
                row[key] = minutes(value)
            elif key is not None:
                # This feed renders published counting-stat zeroes as "-".
                # Unpublished fields (starter, fouls drawn, PIR) stay absent.
                row[key] = 0 if value.strip() == '-' else number(value)
        result.append(row)
    return result


def parse_transcribed_box(text, spec):
    """Read rows transcribed from a public official box-score image."""
    result = []
    for source_index, published in enumerate(json.loads(text)['rows']):
        row = dict(published)
        side = row.pop('side')
        row['minutes'] = minutes(row.get('minutes'))
        row.update(
            source_player_id=row.get('source_player_id') or f"{row['player_name_raw']}:{source_index}",
            team_name_raw=spec[side], team_code=spec.get(side + '_code'),
        )
        result.append(row)
    return result


PARSERS = {
    'acb': parse_html, 'olympiacos': parse_html, 'bbl': parse_bbl,
    'vtb': parse_vtb, 'lnb': parse_lnb, 'lkl': parse_lkl,
    'lba': parse_lba, 'lnb_pre': parse_lnb_pre,
    'givemestats': parse_givemestats, 'fiba': parse_fiba,
    'israel': parse_israel, 'rincon': parse_rincon,
    'flashscore': parse_flashscore,
    'transcribed_box': parse_transcribed_box,
}


def ingest_preparation_manifest(manifest_path, database_path=DEFAULT_DATABASE_PATH):
    manifest_path = Path(manifest_path)
    specs = json.loads(manifest_path.read_text())
    initialize_database(database_path)
    counts = {'games_added':0,'player_rows_added':0,'mapped_rows':0,'unmapped':[]}
    with connect_database(database_path) as c:
        ing = CanonicalIngestor(c, source='official_preparation', command='ingest_preparation_manifest', season_code='E2026')
        names = defaultdict(set)
        for pid, name in c.execute('SELECT canonical_player_id,canonical_name FROM players UNION SELECT canonical_player_id,alias_value FROM player_aliases').fetchall():
            for variant in official_name_variants(name):
                names[variant].add(pid)
                # Exact token reordering handles documented surname-first boxes.
                names[' '.join(sorted(variant.split()))].add(pid)
        teams = dict(c.execute('SELECT official_team_code,canonical_team_id FROM teams').fetchall())
        roster = defaultdict(set)
        for pid,tid in c.execute("SELECT DISTINCT canonical_player_id,canonical_team_id FROM player_team_memberships JOIN seasons USING(season_id) WHERE season_code='E2026'").fetchall():
            roster[tid].add(pid)
        for pid,tid in c.execute("SELECT canonical_player_id,canonical_team_id FROM current_live_roster WHERE season_code='E2026' AND roster_status='CURRENT_ROSTER'").fetchall():
            roster[tid].add(pid)
        for pid,tid in c.execute("""
            SELECT x.canonical_player_id,m.canonical_team_id
            FROM fantasy_market_snapshots m JOIN fantasy_player_crosswalk x USING(fantasy_entity_id)
            WHERE m.season_code='E2026' AND x.season_code='E2026'
              AND x.mapping_status='MATCHED' AND x.valid_to IS NULL
              AND m.snapshot_batch_id=(SELECT snapshot_batch_id FROM fantasy_market_snapshots
                WHERE season_code='E2026' ORDER BY observed_at DESC,snapshot_batch_id DESC LIMIT 1)
        """).fetchall():
            roster[tid].add(pid)
        try:
            c.begin()
            for spec in specs:
                path = manifest_path.parent / spec['path']
                rows = PARSERS[spec['parser']](path.read_text(errors='replace'),spec)
                if not rows:
                    raise ValueError(f"Empty player boxes: {path}")
                artifact_path = manifest_path.parent / spec.get('artifact_path', spec['path'])
                artifact = ing.register_artifact(describe_existing_file(artifact_path.resolve()),source=spec['source'],endpoint='preparation_boxscore',source_url=spec['url'],competition_code=spec['game_type'],season_code='E2026',fetched_at=datetime.fromtimestamp(artifact_path.stat().st_mtime,UTC),media_type='application/json' if artifact_path.suffix=='.json' else ('text/html' if artifact_path.suffix in ('.html', '.htm') else 'image/jpeg'))
                gid=stable_id('preparation_game',spec['source'],spec['id'])
                counts['games_added'] += ing.insert_ignore('preparation_games',{'preparation_game_id':gid,'season_code':'E2026','game_date':spec['date'],'game_type':spec['game_type'],'source':spec['source'],'source_game_id':spec['id'],'home_team_name':spec['home'],'away_team_name':spec['away'],'source_artifact_id':artifact,'ingestion_run_id':ing.run_id})
                c.execute('UPDATE preparation_games SET game_date=? WHERE preparation_game_id=?', [spec['date'], gid])
                for row in rows:
                    for key in set(row) - {'player_name_raw','source_player_id','team_name_raw','team_code','minutes','starter'}:
                        row[key] = number(row[key])
                    tid=teams.get(row.pop('team_code',None))
                    name=normalize_person_name(row['player_name_raw'])
                    matches=names.get(name,set()) | names.get(' '.join(sorted(name.split())),set())
                    method='EXACT_NAME_VARIANT'
                    if len(matches)>1 and tid:
                        matches &= roster[tid]
                    if not matches and tid:
                        # Initial + surname only when unique within current team.
                        tokens=name.split()
                        if tokens and len(tokens[0])<=2:
                            matches={pid for variant,pids in names.items() if variant.split() and variant.split()[0].startswith(tokens[0]) and variant.split()[1:]==tokens[1:] for pid in pids if pid in roster[tid]}
                            method='UNIQUE_TEAM_INITIAL_SURNAME'
                    pid=next(iter(matches)) if len(matches)==1 else None
                    if pid:
                        ing.ensure_player_alias(pid,row['player_name_raw'],source=spec['source'],season_code='E2026',match_method=method)
                        counts['mapped_rows']+=1
                    else:
                        counts['unmapped'].append([spec['id'],row['team_name_raw'],row['player_name_raw']])
                    row.update(preparation_player_stat_id=stable_id('preparation_stat',gid,row['team_name_raw'],row['source_player_id']),preparation_game_id=gid,canonical_player_id=pid,canonical_team_id=tid,mapping_method=method if pid else 'UNRESOLVED',source_artifact_id=artifact,ingestion_run_id=ing.run_id)
                    for prefix in ('two_points','three_points','free_throws'):
                        made, attempted = row.get(prefix+'_made'),row.get(prefix+'_attempted')
                        if made is not None and attempted is not None and not 0<=made<=attempted:
                            raise ValueError(f'Invalid shooting: {row}')
                    added = ing.insert_ignore('preparation_player_stats',row)
                    counts['player_rows_added'] += added
                    if not added and 'starter' in row:
                        c.execute('UPDATE preparation_player_stats SET starter=? WHERE preparation_player_stat_id=?', [row['starter'], row['preparation_player_stat_id']])
                    if not added and spec['parser'] == 'vtb':
                        stats = {k:v for k,v in row.items() if k in {'points','assists','steals','turnovers','blocks','fouls_committed','fouls_drawn','offensive_rebounds','defensive_rebounds','total_rebounds','efficiency','two_points_made','two_points_attempted','three_points_made','three_points_attempted','free_throws_made','free_throws_attempted'}}
                        c.execute('UPDATE preparation_player_stats SET '+','.join(k+'=?' for k in stats)+' WHERE preparation_player_stat_id=?', [*stats.values(),row['preparation_player_stat_id']])
                    if not added and pid:
                        c.execute('UPDATE preparation_player_stats SET canonical_player_id=?,canonical_team_id=?,mapping_method=? WHERE preparation_player_stat_id=? AND canonical_player_id IS NULL',[pid,tid,method,row['preparation_player_stat_id']])
            c.commit()
            ing.finish()
        except Exception as error:
            c.rollback()
            ing.fail(error)
            raise
    return counts
