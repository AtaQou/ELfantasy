"""Download the remaining public 2026-27 preparation player boxes.

This is intentionally a small, idempotent manifest updater. GIVEMESTATS is
used only where an original public full box was not available.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/preparation/2026-27"
MANIFEST = RAW / "manifest.json"

TEAM_CODES = {
    "Anadolu Efes Istanbul": "IST", "Baskonia Vitoria": "BAS",
    "Crvena Zvezda MTS Belgrade": "RED", "BC Dubai": "DUB",
    "FC Barcelona Lassa": "BAR", "Fenerbahce Istanbul": "ULK",
    "Hapoel Tel Aviv": "HTA", "Maccabi Tel Aviv": "TEL",
    "AX Armani Exchange Milan": "MIL", "Olympiacos Piraeus": "OLY",
    "Panathinaikos Athens": "PAN", "Paris Basketball": "PRS",
    "Partizan NIS Belgrade": "PAR", "Real Madrid": "MAD",
    "Valencia Basket": "PAM", "Zalgiris Kaunas": "ZAL",
    "FC Bayern Munich": "MUN", "Besiktas Istanbul": "BES",
    "LDLC Asvel Villeurbanne": "ASV",
}

# id, home, away, game type. Dates come from the YYYYMMDD prefix.
GMS_GAMES = [
    ("2026082811", "Saint Chamond Andrezieux", "LDLC Asvel Villeurbanne", "FRIENDLY"),
    ("2026082911", "Rio Breogan", "Real Madrid", "FRIENDLY"),
    ("2026082912", "Zalgiris Kaunas", "Zelli Riga", "FRIENDLY"),
    ("2026082915", "Balkan Botevgrad", "Hapoel Tel Aviv", "FRIENDLY"),
    ("2026082916", "Besiktas Istanbul", "Tofas Bursa", "FRIENDLY"),
    ("2026090211", "Zalgiris Kaunas", "Juventus Utena", "FRIENDLY"),
    ("2026090411", "Olympiacos Piraeus", "Fenerbahce Istanbul", "FRIENDLY"),
    ("2026090611", "BC Dubai", "Besiktas Istanbul", "FRIENDLY"),
    ("2026090612", "AX Armani Exchange Milan", "Fenerbahce Istanbul", "FRIENDLY"),
    ("2026090613", "Crvena Zvezda MTS Belgrade", "Olympiacos Piraeus", "FRIENDLY"),
    ("2026090711", "Anadolu Efes Istanbul", "Trabzonspor", "FRIENDLY"),
    ("2026090931", "Bahcesehir Koleji", "BC Dubai", "FRIENDLY"),
    ("2026091111", "Baskonia Vitoria", "Alba Berlin", "FRIENDLY"),
    ("2026091112", "Obradoiro CAB", "Valencia Basket", "FRIENDLY"),
    ("2026091113", "Partizan NIS Belgrade", "Fuenlabrada", "FRIENDLY"),
    ("2026091114", "Tenerife CB Canarias", "Real Madrid", "FRIENDLY"),
    ("2026091115", "BC Dubai", "Anadolu Efes Istanbul", "FRIENDLY"),
    ("2026091117", "Olympiacos Piraeus", "Crvena Zvezda MTS Belgrade", "FRIENDLY"),
    ("2026091211", "Fuenlabrada", "Besiktas Istanbul", "FRIENDLY"),
    ("2026091212", "Valencia Basket", "Baskonia Vitoria", "FRIENDLY"),
    ("2026091213", "Unicaja Malaga", "Real Madrid", "FRIENDLY"),
    ("2026091311", "Partizan NIS Belgrade", "Besiktas Istanbul", "FRIENDLY"),
    ("2026091313", "Anadolu Efes Istanbul", "U-BT Cluj-Napoca", "FRIENDLY"),
    ("2026091314", "Hapoel Tel Aviv", "FC Bayern Munich", "FRIENDLY"),
    ("2026091315", "FC Barcelona Lassa", "Lleida", "FRIENDLY"),
    ("2026091511", "Baskonia Vitoria", "Bilbao Basket", "FRIENDLY"),
    ("2026091812", "BC Dubai", "Real Madrid", "SUPERCUP"),
    ("2026091813", "Crvena Zvezda MTS Belgrade", "Aris Thessaloniki", "FRIENDLY"),
    ("2026091814", "PAOK Thessaloniki", "Partizan NIS Belgrade", "FRIENDLY"),
    ("2026091815", "Panathinaikos Athens", "JL Bourg Basket", "FRIENDLY"),
    ("2026091911", "Fenerbahce Istanbul", "BC Dubai", "SUPERCUP"),
    ("2026091981", "Paris Basketball", "Nanterre 92", "SUPERCUP"),
]

RINCON_GAMES = [
    ("13", "2026-09-05T12:00:00Z", "Real Madrid", "Baskonia", "MAD", "BAS", "FRIENDLY"),
    ("18", "2026-09-06T12:00:00Z", "MoraBanc Andorra", "Barça", None, "BAR", "FRIENDLY"),
    ("24", "2026-09-07T18:00:00Z", "Valencia Basket", "Casademont Zaragoza", "PAM", None, "FRIENDLY"),
    ("28", "2026-09-10T19:00:00Z", "Barça", "MoraBanc Andorra", "BAR", None, "PRESEASON_TOURNAMENT"),
    ("35", "2026-09-11T19:00:00Z", "BAXI Manresa", "Barça", None, "BAR", "PRESEASON_TOURNAMENT"),
]


def fetch(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 ELfantasy preparation importer"})
    with urlopen(request, timeout=30) as response:
        return response.read()


def add_spec(manifest, spec):
    key = (spec["source"], spec["id"])
    if key not in {(row["source"], row["id"]) for row in manifest}:
        manifest.append(spec)


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text())
    for game_id, home, away, game_type in GMS_GAMES:
        path = RAW / f"givemestats-{game_id}.html"
        url = f"https://givemestats.com/game-boxscore/?id={game_id}"
        if not path.exists():
            path.write_bytes(fetch(url))
        date = f"{game_id[:4]}-{game_id[4:6]}-{game_id[6:8]}T12:00:00Z"
        if game_id == "2026091812":
            date = "2026-09-18T17:00:00Z"
        elif game_id == "2026091911":
            date = "2026-09-19T14:00:00Z"
        add_spec(manifest, {
            "parser": "givemestats", "source": "givemestats", "id": game_id,
            "date": date, "home": home, "away": away,
            "home_code": TEAM_CODES.get(home), "away_code": TEAM_CODES.get(away),
            "url": url, "path": path.name, "game_type": game_type,
        })

    for game_id, date, home, away, home_code, away_code, game_type in RINCON_GAMES:
        path = RAW / f"rincon-{game_id}.html"
        url = f"https://www.rincondelmanager.com/smgr/pretemporada_partido.php?p={game_id}"
        if not path.exists():
            path.write_bytes(fetch(url))
        add_spec(manifest, {
            "parser": "rincon", "source": "rincon_supermanager", "id": game_id,
            "date": date, "home": home, "away": away,
            "home_code": home_code, "away_code": away_code,
            "url": url, "path": path.name, "game_type": game_type,
        })

    official = [
        {
            "parser": "fiba", "source": "fiba_livestats", "id": "2885582",
            "date": "2026-09-11T15:00:00Z", "home": "Maccabi Tel Aviv",
            "away": "Aris Thessaloniki", "home_code": "TEL", "away_code": None,
            "team_order": {"1": "away", "2": "home"},
            "url": "https://fibalivestats.dcd.shared.geniussports.com/data/2885582/data.json",
            "path": "fiba-2885582.json", "game_type": "FRIENDLY",
        },
        {
            "parser": "fiba", "source": "fiba_livestats", "id": "2885587",
            "date": "2026-09-13T15:00:00Z", "home": "Maccabi Tel Aviv",
            "away": "Olympiacos Piraeus", "home_code": "TEL", "away_code": "OLY",
            "team_order": {"1": "away", "2": "home"},
            "url": "https://fibalivestats.dcd.shared.geniussports.com/data/2885587/data.json",
            "path": "fiba-2885587.json", "game_type": "FRIENDLY",
        },
        {
            "parser": "israel", "source": "israel_bsl", "id": "26633",
            "date": "2026-09-17T17:55:00Z", "home": "Maccabi Tel Aviv",
            "away": "Hapoel Tel Aviv", "home_code": "TEL", "away_code": "HTA",
            "url": "https://basket.co.il/game-zone.asp?GameId=26633&lang=en",
            "path": "israel-26633.html", "game_type": "SUPERCUP",
        },
    ]
    for spec in official:
        path = RAW / spec["path"]
        if not path.exists():
            path.write_bytes(fetch(spec["url"]))
        add_spec(manifest, spec)

    image_specs = [
        {
            "parser": "transcribed_box", "source": "israel_bsl_image", "id": "20260905-partizan-hapoel",
            "date": "2026-09-05T17:00:00Z", "home": "Partizan Mozzart Bet",
            "away": "Hapoel IBI Tel Aviv", "home_code": "PAR", "away_code": "HTA",
            "url": "https://basket.co.il/pics/2026-2027/%D7%9E%D7%A9%D7%97%D7%A7%D7%99%20%D7%90%D7%99%D7%9E%D7%95%D7%9F/WhatsApp%20Image%202026-09-05%20at%2021_50_44.jpeg",
            "path": "israel-image-partizan-hapoel.json", "artifact_path": "israel-image-partizan-hapoel.jpeg",
            "game_type": "FRIENDLY",
        },
        {
            "parser": "transcribed_box", "source": "israel_bsl_image", "id": "20260906-fmp-hapoel",
            "date": "2026-09-06T13:15:00Z", "home": "KK FMP", "away": "Hapoel IBI Tel Aviv",
            "home_code": None, "away_code": "HTA",
            "url": "https://basket.co.il/pics/2026-2027/%D7%A9%D7%95%D7%A0%D7%95%D7%AA/WhatsApp%20Image%202026-09-07%20at%2000_16_07.jpeg",
            "path": "israel-image-hapoel-fmp.json", "artifact_path": "israel-image-hapoel-fmp.jpeg",
            "game_type": "FRIENDLY",
        },
        {
            "parser": "transcribed_box", "source": "israel_bsl_image", "id": "20260912-hapoel-roma",
            "date": "2026-09-12T16:00:00Z", "home": "Hapoel IBI Tel Aviv", "away": "Maxima Roma",
            "home_code": "HTA", "away_code": None,
            "url": "https://basket.co.il/pics/2026-2027/%D7%9E%D7%A9%D7%97%D7%A7%D7%99%20%D7%90%D7%99%D7%9E%D7%95%D7%9F/WhatsApp%20Image%202026-09-13%20at%2000_53_36.jpeg",
            "path": "israel-image-hapoel-roma.json", "artifact_path": "israel-image-hapoel-roma.jpeg",
            "game_type": "PRESEASON_TOURNAMENT",
        },
    ]
    for spec in image_specs:
        artifact_path = RAW / spec["artifact_path"]
        if not artifact_path.exists():
            artifact_path.write_bytes(fetch(spec["url"]))
        add_spec(manifest, spec)

    article_specs = [
        {
            "parser": "transcribed_box", "source": "olympiacos_official", "id": "19681",
            "date": "2026-08-30T12:00:00Z", "home": "Olympiacos Piraeus", "away": "Promitheas Patras",
            "home_code": "OLY", "away_code": None,
            "url": "https://www.olympiacosbc.gr/el/nea/game-news/19681-filiko-olympiacos-promitheas.html",
            "path": "official-olympiacos-promitheas.json", "artifact_path": "official-olympiacos-promitheas.html",
            "game_type": "FRIENDLY",
        },
        {
            "parser": "transcribed_box", "source": "crvena_zvezda_official", "id": "20260904-milano-crvena",
            "date": "2026-09-04T15:30:00Z", "home": "Olimpia Milano", "away": "Crvena Zvezda",
            "home_code": "MIL", "away_code": "RED",
            "url": "https://kkcrvenazvezda.rs/news/krit-crvena-zvezda-nakon-preokreta-do-pobede-nad-milanom",
            "path": "official-milano-crvena.json", "artifact_path": "official-milano-crvena.html",
            "game_type": "PRESEASON_TOURNAMENT",
        },
        {
            "parser": "transcribed_box", "source": "crvena_zvezda_official", "id": "20260913-crvena-aris",
            "date": "2026-09-13T15:00:00Z", "home": "Crvena Zvezda", "away": "Aris Thessaloniki",
            "home_code": "RED", "away_code": None,
            "url": "https://kkcrvenazvezda.rs/news/utakmica-za-zabprav-poraz-od-arisa",
            "path": "official-crvena-aris.json", "artifact_path": "official-crvena-aris.html",
            "game_type": "PRESEASON_TOURNAMENT",
        },
        {
            "parser": "transcribed_box", "source": "virtus_official", "id": "20260903-rimini-virtus",
            "date": "2026-09-03T18:00:00Z", "home": "Rimini", "away": "Virtus Bologna",
            "home_code": None, "away_code": "VIR",
            "url": "https://www.emiliaromagnasport.com/news/dole-rimini-virtus-pallacannestro-bologna-79-83-13-27-36-45-63-55",
            "path": "official-rimini-virtus.json", "artifact_path": "official-rimini-virtus.html",
            "game_type": "FRIENDLY",
        },
        {
            "parser": "transcribed_box", "source": "virtus_official", "id": "20260909-virtus-tortona",
            "date": "2026-09-09T18:00:00Z", "home": "Virtus Bologna", "away": "Derthona Tortona",
            "home_code": "VIR", "away_code": None,
            "url": "https://www.virtus.it/news/memorial-bertolazzi-una-virtus-sugli-scudi-batte-tortona-88-73/",
            "path": "official-virtus-tortona.json", "artifact_path": "official-virtus-tortona.html",
            "game_type": "FRIENDLY",
        },
        {
            "parser": "transcribed_box", "source": "virtus_official", "id": "20260911-pesaro-virtus",
            "date": "2026-09-11T18:00:00Z", "home": "VL Pesaro", "away": "Virtus Bologna",
            "home_code": None, "away_code": "VIR",
            "url": "https://www.virtus.it/news/memorial-alphonso-ford-una-bella-virtus-bologna-batte-la-vl-pesaro-67-110/",
            "path": "official-pesaro-virtus.json", "artifact_path": "official-pesaro-virtus.html",
            "game_type": "FRIENDLY",
        },
        {
            "parser": "transcribed_box", "source": "virtus_official", "id": "20260916-virtus-roma",
            "date": "2026-09-16T18:00:00Z", "home": "Virtus Bologna", "away": "Maxima Roma",
            "home_code": "VIR", "away_code": None,
            "url": "https://www.virtus.it/news/la-virtus-bologna-vince-lultima-amichevole-prestagione-contro-la-maxima-roma-97-80/",
            "path": "official-virtus-roma.json", "artifact_path": "official-virtus-roma.html",
            "game_type": "FRIENDLY",
        },
    ]
    for spec in article_specs:
        artifact_path = RAW / spec["artifact_path"]
        if not artifact_path.exists():
            artifact_path.write_bytes(fetch(spec["url"]))
        add_spec(manifest, spec)

    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"manifest_games": len(manifest), "new_specs": len(manifest) - 19}))


if __name__ == "__main__":
    main()
