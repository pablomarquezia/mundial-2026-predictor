#!/usr/bin/env python3
"""Modelo Poisson con ranking FIFA, momentum intra-torneo y ajuste secuencial"""

import json, math, urllib.request, os, random, re
from collections import defaultdict, deque
from datetime import date as dt_date

DATA_DIR = os.path.dirname(__file__)
WC_YEARS = [(2014, 0.5), (2018, 0.75), (2022, 1.0)]
API_BASE = "https://raw.githubusercontent.com/openfootball/worldcup.json/master"
API_2026 = API_BASE + "/2026/worldcup.json"

NAME_MAP = {
    "Canada": "Canadá", "Mexico": "México", "USA": "Estados Unidos",
    "Iran": "RI de Irán", "South Korea": "República de Corea",
    "Qatar": "Catar", "Saudi Arabia": "Arabia Saudí",
    "Cape Verde": "Cabo Verde", "Ivory Coast": "Costa de Marfil",
    "DR Congo": "RD Congo", "Bosnia & Herzegovina": "Bosnia y Herzegovina",
    "Czech Republic": "República Checa", "Netherlands": "Países Bajos",
    "New Zealand": "Nueva Zelanda", "Turkey": "Turquía",
}
HIST_MAP = {"Bosnia-Herzegovina": "Bosnia & Herzegovina", "Côte d'Ivoire": "Ivory Coast"}

conf_map = {
    "UEFA": ["Austria", "Belgium", "Bosnia & Herzegovina", "Croatia", "Czech Republic",
             "England", "France", "Germany", "Netherlands", "Norway", "Portugal",
             "Scotland", "Spain", "Sweden", "Switzerland", "Turkey"],
    "CONMEBOL": ["Argentina", "Brazil", "Colombia", "Ecuador", "Paraguay", "Uruguay"],
    "AFC": ["Australia", "Iran", "Japan", "Jordan", "South Korea", "Qatar",
            "Saudi Arabia", "Uzbekistan", "Iraq"],
    "CAF": ["Algeria", "Cape Verde", "Ivory Coast", "Egypt", "Ghana",
            "Morocco", "Senegal", "South Africa", "Tunisia", "DR Congo"],
    "CONCACAF": ["Canada", "Mexico", "USA", "Curaçao", "Haiti", "Panama"],
    "OFC": ["New Zealand"],
}

RANKING_FACTOR = {
    "France":1.2,"Spain":1.1994,"Argentina":1.1983,"England":1.1644,"Portugal":1.1214,
    "Brazil":1.1195,"Netherlands":1.1172,"Morocco":1.1159,"Belgium":1.1012,"Germany":1.0982,
    "Croatia":1.089,"Colombia":1.0724,"Senegal":1.0695,"Mexico":1.064,"USA":1.0585,
    "Uruguay":1.0585,"Japan":1.0497,"Switzerland":1.0421,"Iran":1.0185,"Turkey":1.0072,
    "Ecuador":1.0042,"Austria":1.0033,"South Korea":1.0,"Australia":0.9945,"Algeria":0.9831,
    "Egypt":0.9824,"Canada":0.9777,"Norway":0.9739,"Panama":0.9667,"Ivory Coast":0.9614,
    "Sweden":0.9488,"Paraguay":0.941,"Tunisia":0.9316,"Scotland":0.9212,"Czech Republic":0.9178,
    "Saudi Arabia":0.9109,"Ghana":0.9074,"DR Congo":0.897,"Qatar":0.8935,"South Africa":0.8901,
    "Iraq":0.8866,"Bosnia & Herzegovina":0.8831,"Cape Verde":0.8762,"Uzbekistan":0.8727,
    "Jordan":0.8693,"Haiti":0.8346,"Curaçao":0.8208,"New Zealand":0.8,
}

KO_FACTOR = {
    "Round of 32": 0.85, "Round of 16": 0.85,
    "Quarter-finals": 0.80, "Semi-finals": 0.75,
    "Final": 0.72, "Match for third place": 0.85,
}

# Dixon-Coles (1997): corrige Poisson para scores bajos
# rho negativo → 0-0, 1-1 más probables que el Poisson independiente
DC_RHO = -0.20  # Ajustado para 2026: más empates (70% partidos con gol de ambos)

INJURIES_FILE = os.path.join(DATA_DIR, "injuries.json")

# Host nation boost (home advantage in 2026 group stage)
HOST_NATIONS = {"Mexico", "USA", "Canada"}
HOST_ATTACK_BOOST = 1.40
HOST_DEFENSE_BOOST = 0.80
USA_ATTACK_BOOST = 1.60
USA_DEFENSE_BOOST = 0.75

# Compression: reduce strength dispersion toward 1.0 (2026 has more parity)
STRENGTH_COMPRESSION = 0.80

# Odds de mercado (The Odds API)
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/odds"
ODDS_WEIGHT = 0.35
_odds_cache = {}

# Normalización de nombres entre openfootball y odds API
ODDS_NAME_MAP = {
    "bosnia & herzegovina": "bosnia and herzegovina",
    "curaçao": "curacao",
    "dr congo": "congo dr",
}

# ── Text parser (openfootball Football.TXT) ─────────────────────────

MONTHS_MAP = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
              "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}

_MATCH_RE = re.compile(
    r'^\s+'
    r'(.+?)\s+'
    r'(\d+)-(\d+)'
    r'\s+'
    r'(.+?)'
    r'(?:\s+@\s+(.+?))?'
    r'\s*$'
)
_DATE_LINE_RE = re.compile(r'^[A-Z][a-z]{2}\s+([A-Z][a-z]{2})\s+(\d{1,2})$')
_YEAR_RE = re.compile(r'(\d{4})\s*(?:\(|$)')

INTERNATIONALS_BASE = "https://raw.githubusercontent.com/openfootball/internationals/master"
INTERNATIONALS_URLS = [
    f"{INTERNATIONALS_BASE}/friendly/2022_friendly.txt",
    f"{INTERNATIONALS_BASE}/friendly/2023_friendly.txt",
    f"{INTERNATIONALS_BASE}/friendly/2024_friendly.txt",
    f"{INTERNATIONALS_BASE}/friendly/2025_friendly.txt",
    f"{INTERNATIONALS_BASE}/copa_america/2024_copa_america.txt",
    f"{INTERNATIONALS_BASE}/uefa_euro/2024_uefa_euro.txt",
    f"{INTERNATIONALS_BASE}/african_cup_of_nations/2024_african_cup_of_nations.txt",
    f"{INTERNATIONALS_BASE}/afc_asian_cup/2024_afc_asian_cup.txt",
]

def fetch_text(url):
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        return resp.read().decode("utf-8")
    except Exception:
        return None

def parse_openfootball_text(text, source=""):
    matches = []
    year = None
    current_date = None
    current_group = ""

    for line_raw in text.split("\n"):
        line = line_raw.rstrip("\r")
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("# Date") or stripped.startswith("# date"):
            y = _YEAR_RE.search(stripped)
            if y:
                year = int(y.group(1))

        elif stripped.startswith("\u25a2 ") or stripped.startswith("\u25ca ") or stripped.startswith("▪"):
            current_group = stripped.lstrip("▪ ◊").strip()

        elif _DATE_LINE_RE.match(stripped):
            m = _DATE_LINE_RE.match(stripped)
            month = MONTHS_MAP.get(m.group(1))
            day = int(m.group(2))
            if month and year:
                current_date = f"{year:04d}-{month:02d}-{day:02d}"

        else:
            m = _MATCH_RE.match(line)
            if m and current_date:
                t1 = m.group(1).strip()
                g1 = int(m.group(2))
                g2 = int(m.group(3))
                t2 = m.group(4).strip()
                matches.append({
                    "date": current_date,
                    "team1": t1,
                    "team2": t2,
                    "g1": g1,
                    "g2": g2,
                    "group": current_group,
                    "source": source,
                })

    return matches

def load_broad_data():
    """Load matches from all sources (worldcup.json + internationals) sorted by date."""
    matches = []
    for year, weight in WC_YEARS:
        data = fetch_json(f"{API_BASE}/{year}/worldcup.json")
        if data:
            for m in data.get("matches", []):
                if "score" not in m or "date" not in m:
                    continue
                t1 = HIST_MAP.get(m["team1"], m["team1"])
                t2 = HIST_MAP.get(m["team2"], m["team2"])
                matches.append({
                    "date": m["date"],
                    "team1": t1, "team2": t2,
                    "g1": m["score"]["ft"][0], "g2": m["score"]["ft"][1],
                    "source": "worldcup",
                })
    for url in INTERNATIONALS_URLS:
        text = fetch_text(url)
        if text:
            source = url.rsplit("/", 1)[-1].replace(".txt", "")
            matches.extend(parse_openfootball_text(text, source))
    matches.sort(key=lambda m: m["date"])
    return matches

# ── ELO rating system ──────────────────────────────────────────────

ELO_BASE = 1500
ELO_K_DEFAULT = 25
ELO_K_BY_SOURCE = {
    "friendly": 20,
    "worldcup": 40,
    "copa_america": 30,
    "uefa_euro": 30,
    "afcon": 30,
    "asian_cup": 30,
    "gold_cup": 30,
}

def compute_elo_ratings(broad_matches):
    elo = defaultdict(lambda: ELO_BASE)
    for m in broad_matches:
        t1, t2, g1, g2 = m["team1"], m["team2"], m["g1"], m["g2"]
        k = ELO_K_BY_SOURCE.get(m.get("source", ""), ELO_K_DEFAULT)
        r1, r2 = elo[t1], elo[t2]
        e1 = 1.0 / (1.0 + 10.0 ** ((r2 - r1) / 400.0))
        e2 = 1.0 / (1.0 + 10.0 ** ((r1 - r2) / 400.0))
        if g1 > g2:
            s1, s2 = 1.0, 0.0
        elif g1 < g2:
            s1, s2 = 0.0, 1.0
        else:
            s1, s2 = 0.5, 0.5
        elo[t1] = r1 + k * (s1 - e1)
        elo[t2] = r2 + k * (s2 - e2)
    return dict(elo)

def elo_to_factor(elo_rating):
    return 1.0 + (elo_rating - ELO_BASE) / 500.0

# ── Injury system ──────────────────────────────────────────────────

def load_injuries():
    try:
        with open(INJURIES_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def apply_injuries(strengths, injuries):
    if not injuries:
        return strengths
    for team, adj in injuries.items():
        if team in strengths:
            atk_mul = adj.get("attack", 1.0)
            dfn_mul = adj.get("defense", 1.0)
            s = strengths[team]
            s["attack"] *= atk_mul
            s["defense"] *= dfn_mul
            s["attack_hist"] *= atk_mul
            s["defense_hist"] *= dfn_mul
    return strengths

def es(name):
    return NAME_MAP.get(name, name)

def fetch_json(url):
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        return json.loads(resp.read())
    except:
        return None

def norm_team(name):
    n = name.lower().replace("'", "").replace("é", "e").replace("ç", "c").replace("&", "and").strip()
    return ODDS_NAME_MAP.get(n, n)

def fetch_market_odds():
    if not ODDS_API_KEY:
        return {}
    today_s = dt_date.today().isoformat()
    if today_s in _odds_cache:
        return _odds_cache[today_s]
    print("  📊 Consultando odds de mercado...")
    url = f"{ODDS_API_URL}?regions=eu,us&markets=h2h&oddsFormat=decimal&apiKey={ODDS_API_KEY}"
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        data = json.loads(resp.read())
    except Exception as e:
        print(f"  ⚠ Error al obtener odds: {e}")
        return {}
    result = {}
    for match in data:
        ht, at = match["home_team"], match["away_team"]
        key = (norm_team(ht), norm_team(at))
        for bk in match.get("bookmakers", []):
            for market in bk.get("markets", []):
                if market["key"] != "h2h":
                    continue
                odds = {o["name"]: o["price"] for o in market["outcomes"]}
                if ht in odds and "Draw" in odds and at in odds:
                    imp = (1/odds[ht], 1/odds["Draw"], 1/odds[at])
                    if bk["key"] == "pinnacle" or key not in result:
                        result[key] = imp
                    break
    if result:
        print(f"  ✓ Odds obtenidos para {len(result)} partidos")
    _odds_cache[today_s] = result
    return result

def load_historical_matches():
    all_m = []
    for year, weight in WC_YEARS:
        data = fetch_json(f"{API_BASE}/{year}/worldcup.json")
        if not data:
            continue
        for m in data["matches"]:
            if "score" not in m:
                continue
            t1 = HIST_MAP.get(m["team1"], m["team1"])
            t2 = HIST_MAP.get(m["team2"], m["team2"])
            all_m.append({"team1": t1, "team2": t2, "g1": m["score"]["ft"][0], "g2": m["score"]["ft"][1], "w": weight})
    return all_m

def build_model():
    hist_matches = load_historical_matches()
    print(f"  Partidos historicos (Mundiales): {len(hist_matches)}")

    stats = defaultdict(lambda: {"gf": 0, "ga": 0, "pj": 0})
    for m in hist_matches:
        w = m["w"]
        for t, gf, ga in [(m["team1"], m["g1"], m["g2"]), (m["team2"], m["g2"], m["g1"])]:
            stats[t]["gf"] += gf * w
            stats[t]["ga"] += ga * w
            stats[t]["pj"] += w

    league_avg = sum((m["g1"]+m["g2"]) * m["w"] for m in hist_matches) / sum(m["w"] for m in hist_matches) / 2 if hist_matches else 1

    all_teams = [t for c in conf_map.values() for t in c]

    strengths = {}
    for team, s in stats.items():
        if s["pj"] > 0:
            strengths[team] = {
                "attack": (s["gf"]/s["pj"])/league_avg,
                "defense": (s["ga"]/s["pj"])/league_avg,
                "pj_hist": s["pj"], "pj": s["pj"],
            }

    conf_avgs = {}
    for conf, cteams in conf_map.items():
        atk = [strengths[t]["attack"] for t in cteams if t in strengths]
        dfn = [strengths[t]["defense"] for t in cteams if t in strengths]
        conf_avgs[conf] = {"attack": sum(atk)/len(atk) if atk else 1.0, "defense": sum(dfn)/len(dfn) if dfn else 1.0}

    team_conf = {t: c for c, ct in conf_map.items() for t in ct}

    # ── ELO: compute from broad data and convert to factor ──
    print("  📡 Cargando datos ampliados para ELO...")
    broad_matches = load_broad_data()
    print(f"  Partidos totales para ELO: {len(broad_matches)}")
    elo_ratings = compute_elo_ratings(broad_matches)
    elo_factors = {team: elo_to_factor(elo_ratings.get(team, ELO_BASE)) for team in all_teams}

    # Blend league_avg with recent scoring rate from broad data
    if broad_matches:
        recent = [m for m in broad_matches if m["date"] >= "2023-01-01"]
        if recent:
            broad_avg = sum(m["g1"] + m["g2"] for m in recent) / len(recent) / 2.0
            league_avg = 0.3 * league_avg + 0.7 * broad_avg
    # 48-team format adjustment: 10% boost for known higher scoring in expanded WC
    league_avg *= 1.10
    print(f"  Promedio goles/partido: {league_avg*2:.2f} (ponderado)")

    # Apply ELO factor to base strengths (stronger ELO → higher attack, lower defense)
    for team in all_teams:
        conf = team_conf.get(team, "UEFA")
        ca = conf_avgs[conf]
        rf = RANKING_FACTOR.get(team, 1.0)
        ef = elo_factors.get(team, 1.0)

        if team in strengths:
            s = strengths[team]
            s["attack"] *= ef
            s["defense"] /= ef
            s["attack_hist"] = s["attack"]
            s["defense_hist"] = s["defense"]
            s["estimated"] = False
        else:
            # Use ELO factor instead of static ranking for debutants
            rf_adj = ef ** 1.5
            conf_w = 0.6
            atk = (ca["attack"] * conf_w + 1.0 * (1 - conf_w)) * rf_adj
            defn = (ca["defense"] * conf_w + 1.0 * (1 - conf_w)) / rf_adj
            strengths[team] = {
                "attack": atk, "defense": defn,
                "attack_hist": atk, "defense_hist": defn,
                "pj_hist": 0, "pj": 0, "estimated": True,
            }

    # Shrink extreme strengths for teams with few WC matches
    for team in all_teams:
        if team in strengths:
            s = strengths[team]
            if s["pj_hist"] < 10:
                conf = team_conf.get(team, "UEFA")
                ca = conf_avgs[conf]
                ef = elo_factors.get(team, 1.0)
                w = s["pj_hist"] / 10.0
                conf_atk = ca["attack"] * ef
                conf_def = ca["defense"] / ef
                s["attack"] = w * s["attack"] + (1 - w) * conf_atk
                s["defense"] = w * s["defense"] + (1 - w) * conf_def

    # Compress extreme strengths toward 1.0 (2026 has more parity than historical data suggests)
    for team in all_teams:
        if team in strengths:
            s = strengths[team]
            s["attack"] = 1.0 + (s["attack"] - 1.0) * STRENGTH_COMPRESSION
            s["defense"] = 1.0 + (s["defense"] - 1.0) * STRENGTH_COMPRESSION

        s = strengths[team]
        # Host nation home advantage boost
        if team in HOST_NATIONS:
            if team == "USA":
                s["attack"] *= USA_ATTACK_BOOST
                s["defense"] *= USA_DEFENSE_BOOST
            else:
                s["attack"] *= HOST_ATTACK_BOOST
                s["defense"] *= HOST_DEFENSE_BOOST
            s["attack_hist"] = s["attack"]
            s["defense_hist"] = s["defense"]
        s["elo_factor"] = ef
        s["elo_rating"] = int(elo_ratings.get(team, ELO_BASE))
        s["attack_form"] = s["attack"]
        s["defense_form"] = s["defense"]
        s["form_obs"] = deque(maxlen=3)

    # ── Apply injuries ──
    injuries = load_injuries()
    if injuries:
        print(f"  🩹 Aplicando ajustes por lesiones ({len(injuries)} equipos)")
        apply_injuries(strengths, injuries)

    return strengths, league_avg, all_teams

def update_form(team, obs_attack, obs_defense, strengths):
    s = strengths[team]
    s["form_obs"].append((obs_attack, obs_defense))
    if s["form_obs"]:
        atk = sum(o[0] for o in s["form_obs"]) / len(s["form_obs"])
        dfn = sum(o[1] for o in s["form_obs"]) / len(s["form_obs"])
        s["attack_form"] = atk
        s["defense_form"] = dfn

def get_combined_strength(team, strengths):
    s = strengths.get(team, {})
    nf = len(s.get("form_obs", []))
    w_hist = 0.85 if nf < 2 else max(0.3, 0.7 - 0.05 * nf)
    atk = w_hist * s.get("attack_hist", 1.0) + (1-w_hist) * s.get("attack_form", 1.0)
    dfn = w_hist * s.get("defense_hist", 1.0) + (1-w_hist) * s.get("defense_form", 1.0)
    return atk, dfn

def predict(t1, t2, strengths, league_avg, ronda=""):
    a1, d1 = get_combined_strength(t1, strengths)
    a2, d2 = get_combined_strength(t2, strengths)
    e1 = league_avg * a1 * d2
    e2 = league_avg * a2 * d1
    if ronda and "Matchday" not in ronda and "Group " not in ronda:
        for key, val in KO_FACTOR.items():
            if key in ronda:
                f = val
                break
        else:
            f = 0.82
        e1 *= f
        e2 *= f
    return e1, e2

def poisson(l, k):
    if l <= 0: return 1.0 if k == 0 else 0.0
    return math.exp(-l) * (l**k) / math.factorial(k)

def dixon_coles_tau(x, y, lam, mu, rho):
    # 0-0 gets no boost: only 1/20 matches in 2026 WC
    if x == 0 and y == 0:
        return 1.0
    if x == 0 and y == 1:
        return 1 + lam * rho
    if x == 1 and y == 0:
        return 1 + mu * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0

def probs(e1, e2, mg=6, market_odds=None, rho=DC_RHO):
    exact = {}
    total = 0.0
    for g1 in range(mg+1):
        for g2 in range(mg+1):
            p = poisson(e1, g1) * poisson(e2, g2)
            if rho != 0:
                p *= dixon_coles_tau(g1, g2, e1, e2, rho)
            exact[(g1,g2)] = p
            total += p
    if total > 0 and abs(total - 1.0) > 1e-9:
        for k in exact:
            exact[k] /= total
    w1 = sum(p for (g1,g2),p in exact.items() if g1>g2)
    dr = sum(p for (g1,g2),p in exact.items() if g1==g2)
    w2 = sum(p for (g1,g2),p in exact.items() if g1<g2)
    if market_odds:
        m_w1, m_dr, m_w2 = market_odds
        tot = m_w1 + m_dr + m_w2
        m_w1, m_dr, m_w2 = m_w1/tot, m_dr/tot, m_w2/tot
        w1 = (1 - ODDS_WEIGHT) * w1 + ODDS_WEIGHT * m_w1
        dr = (1 - ODDS_WEIGHT) * dr + ODDS_WEIGHT * m_dr
        w2 = (1 - ODDS_WEIGHT) * w2 + ODDS_WEIGHT * m_w2
    def ev(s):
        p_out = w1 if s[0] > s[1] else (dr if s[0] == s[1] else w2)
        return exact[s] + p_out
    best = max(exact, key=ev)
    return {"w1": round(w1*100,1), "dr": round(dr*100,1), "w2": round(w2*100,1),
            "ml": list(best), "pml": round(exact[best]*100,1)}

def update_strengths(m, strengths, league_avg, decay=0.15):
    t1, t2, g1, g2 = m["team1"], m["team2"], m["g1"], m["g2"]
    for team, opp, gf, ga in [(t1,t2,g1,g2), (t2,t1,g2,g1)]:
        s, op = strengths[team], strengths[opp]
        lr = min(0.15, 1.0/(1.0+s["pj_hist"]))
        obs_a = gf / (league_avg * op["defense"]) if op["defense"] > 0 else 1.0
        obs_d = ga / (league_avg * op["attack"]) if op["attack"] > 0 else 1.0
        obs_a = max(0.3, min(2.5, obs_a))
        obs_d = max(0.3, min(2.5, obs_d))

        s["attack_hist"] = (1-lr)*s["attack_hist"] + lr*obs_a
        s["defense_hist"] = (1-lr)*s["defense_hist"] + lr*obs_d
        s["attack"] = s["attack_hist"]
        s["defense"] = s["defense_hist"]
        s["pj"] += 1
        s["pj_hist"] += 1

        update_form(team, obs_a, obs_d, strengths)

    new_league_avg = (1 - decay) * league_avg + decay * (g1 + g2) / 2.0
    return new_league_avg

def load_2026():
    data = fetch_json(API_2026)
    if not data:
        f = os.path.join(DATA_DIR, "worldcup_data.json")
        if os.path.exists(f):
            with open(f) as fp: data = json.load(fp)
        else:
            return None
    matches = []
    for m in data["matches"]:
        if not m.get("group"):
            continue
        entry = {"date": m["date"], "team1": m["team1"], "team2": m["team2"],
                 "group": m["group"], "time": m.get("time","")}
        if "score" in m:
            entry["score"] = m["score"]["ft"]
        matches.append(entry)
    matches.sort(key=lambda x: (x["date"], x["time"]))
    return matches

def run_simulation():
    print("=== MODELO MEJORADO: Ranking + Momentum + Ajuste secuencial ===\n")
    strengths, league_avg, all_teams = build_model()

    print("\n=== FUERZA INICIAL ===\n")
    print(f"{'Equipo':25s} {'Ataque':>8s} {'Defensa':>8s} {'PJ':>4s} {'ELO':>5s} {'ELOf':>6s} {'Fuente'}")
    print("-" * 65)
    for team in sorted(all_teams):
        s = strengths[team]
        ef = s.get("elo_factor", 1.0)
        elo_r = s.get("elo_rating", 1500)
        src = "Estimado" if s.get("estimated",False) else "Real"
        print(f"{es(team):25s} {s['attack']:>8.2f} {s['defense']:>8.2f} {s['pj_hist']:>4.0f} {elo_r:>5d} {ef:>5.2f} {src}")

    matches = load_2026()
    if not matches:
        print("Error: no se pudieron cargar los datos 2026")
        return

    print(f"\n{'='*60}")
    print("  SIMULACION SECUENCIAL CON MOMENTUM")
    print(f"{'='*60}\n")

    market_odds = fetch_market_odds()

    log = []
    c_result, c_exact, total = 0, 0, 0

    for m in matches:
        t1, t2 = m["team1"], m["team2"]

        e1, e2 = predict(t1, t2, strengths, league_avg, m.get("group", ""))
        mo = market_odds.get((norm_team(t1), norm_team(t2)))
        has_odds = mo is not None
        if not has_odds:
            mo_rev = market_odds.get((norm_team(t2), norm_team(t1)))
            if mo_rev:
                mo = (mo_rev[2], mo_rev[1], mo_rev[0])
                has_odds = True
        p = probs(e1, e2, market_odds=mo)
        played = "score" in m

        if played:
            g1, g2 = m["score"]
            pw = 0 if p["ml"][0] > p["ml"][1] else (2 if p["ml"][0] < p["ml"][1] else 1)
            rw = 0 if g1 > g2 else (2 if g1 < g2 else 1)
            r_ok = pw == rw
            e_ok = p["ml"][0]==g1 and p["ml"][1]==g2
            if r_ok: c_result += 1
            if e_ok: c_exact += 1
            total += 1

            marca = "✓" if r_ok else "✗"
            exact_m = " ✓" if e_ok else " "
            print(f"  {m['date']} | {m['group']}")
            print(f"  {marca} {es(t1):25s} {g1}-{g2:<3d} {es(t2)}{exact_m} 🔄")
            print(f"    Pred: {p['ml'][0]}-{p['ml'][1]}  {es(t1)}:{p['w1']:.0f}% Emp:{p['dr']:.0f}% {es(t2)}:{p['w2']:.0f}%   (lr={min(0.12,1.0/(1.0+strengths[t1]['pj_hist'])):.3f}){' 📊' if has_odds else ''}")

            league_avg = update_strengths({"team1":t1,"team2":t2,"g1":g1,"g2":g2}, strengths, league_avg)

            # Show updated strength
            s1, s2 = strengths[t1], strengths[t2]
            print(f"    → {es(t1)}: atk={s1['attack_hist']:.2f} def={s1['defense_hist']:.2f} form={s1['attack_form']:.2f}/{s1['defense_form']:.2f}")
            print(f"    → {es(t2)}: atk={s2['attack_hist']:.2f} def={s2['defense_hist']:.2f} form={s2['attack_form']:.2f}/{s2['defense_form']:.2f}")
            print()

            log.append({"date":m["date"],"group":m["group"],"t1":t1,"t2":t2,
                       "pred":list(p["ml"]),"real":[g1,g2],"r_ok":r_ok,"e_ok":e_ok,
                       "has_odds": has_odds})
        else:
            print(f"  {m['date']} | {m['group']}")
            print(f"     {es(t1):25s} vs {es(t2)}")
            print(f"    Pred: {p['ml'][0]}-{p['ml'][1]}  {es(t1)}:{p['w1']:.0f}% Emp:{p['dr']:.0f}% {es(t2)}:{p['w2']:.0f}%{' 📊' if has_odds else ' 📡'}")
            print()

    print("="*60)
    if total > 0:
        print(f"\n  RESULTADOS: {c_result}/{total} resultado ({c_result/total*100:.1f}%) | {c_exact}/{total} exacto ({c_exact/total*100:.1f}%)")
    print()

    print("=== FUERZA FINAL ===\n")
    print(f"{'Equipo':25s} {'Ataque':>8s} {'Defensa':>8s} {'FormAtk':>8s} {'FormDef':>8s} {'ELO':>5s} {'PJ':>4s}")
    print("-" * 70)
    for team in sorted(all_teams):
        s = strengths[team]
        nf = len(s.get("form_obs", []))
        marca = ""
        if nf > 0 and s["pj_hist"] > 3:
            if abs(s["attack_form"] - s["attack_hist"]) > 0.15:
                marca = " ⚡"
        elo_r = s.get("elo_rating", 1500)
        print(f"{es(team):25s} {s['attack_hist']:>8.2f} {s['defense_hist']:>8.2f} {s['attack_form']:>8.2f} {s['defense_form']:>8.2f} {elo_r:>5d} {s['pj']:>4.0f}{marca}")

if __name__ == "__main__":
    run_simulation()
