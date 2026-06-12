#!/usr/bin/env python3
"""Modelo Poisson con ranking FIFA, momentum intra-torneo y ajuste secuencial"""

import json, math, urllib.request, os, random
from collections import defaultdict, deque

DATA_DIR = os.path.dirname(__file__)
WC_YEARS = [2014, 2018, 2022]
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

def es(name):
    return NAME_MAP.get(name, name)

def fetch_json(url):
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        return json.loads(resp.read())
    except:
        return None

def load_historical_matches():
    all_m = []
    for year in WC_YEARS:
        data = fetch_json(f"{API_BASE}/{year}/worldcup.json")
        if not data:
            continue
        for m in data["matches"]:
            if "score" not in m:
                continue
            t1 = HIST_MAP.get(m["team1"], m["team1"])
            t2 = HIST_MAP.get(m["team2"], m["team2"])
            all_m.append({"team1": t1, "team2": t2, "g1": m["score"]["ft"][0], "g2": m["score"]["ft"][1]})
    return all_m

def build_model():
    hist_matches = load_historical_matches()
    print(f"  Partidos historicos: {len(hist_matches)}")

    stats = defaultdict(lambda: {"gf": 0, "ga": 0, "pj": 0})
    for m in hist_matches:
        for t, gf, ga in [(m["team1"], m["g1"], m["g2"]), (m["team2"], m["g2"], m["g1"])]:
            stats[t]["gf"] += gf; stats[t]["ga"] += ga; stats[t]["pj"] += 1

    league_avg = sum(m["g1"]+m["g2"] for m in hist_matches) / len(hist_matches) / 2
    print(f"  Promedio goles/partido: {league_avg*2:.2f}")

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
        conf_avgs[conf] = {"attack": sum(atk)/len(atk) if atk else 1.0,
                           "defense": sum(dfn)/len(dfn) if dfn else 1.0}

    team_conf = {t: c for c, ct in conf_map.items() for t in ct}

    for team in all_teams:
        conf = team_conf.get(team, "UEFA")
        ca = conf_avgs[conf]
        rf = RANKING_FACTOR.get(team, 1.0)

        if team in strengths:
            s = strengths[team]
            s["attack_hist"] = s["attack"]
            s["defense_hist"] = s["defense"]
            s["estimated"] = False
        else:
            atk = ca["attack"] * rf
            defn = ca["defense"] / rf
            strengths[team] = {
                "attack": atk, "defense": defn,
                "attack_hist": atk, "defense_hist": defn,
                "pj_hist": 0, "pj": 0, "estimated": True,
            }

        s = strengths[team]
        s["attack_form"] = s["attack"]
        s["defense_form"] = s["defense"]
        s["form_obs"] = deque(maxlen=3)

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
    s = strengths[team]
    n_form = len(s["form_obs"])
    if n_form >= 2:
        w_hist = max(0.3, 0.7 - 0.05 * n_form)
    else:
        w_hist = 0.85
    atk = w_hist * s["attack_hist"] + (1 - w_hist) * s["attack_form"]
    dfn = w_hist * s["defense_hist"] + (1 - w_hist) * s["defense_form"]
    return atk, dfn

def predict(t1, t2, strengths, league_avg, is_knockout=False):
    atk1, def1 = get_combined_strength(t1, strengths)
    atk2, def2 = get_combined_strength(t2, strengths)
    e1 = league_avg * atk1 * def2
    e2 = league_avg * atk2 * def1
    if is_knockout:
        e1 *= 0.82
        e2 *= 0.82
    return e1, e2

def poisson(l, k):
    if l <= 0: return 1.0 if k == 0 else 0.0
    return math.exp(-l) * (l**k) / math.factorial(k)

def probs(e1, e2, mg=6):
    exact = {}
    for g1 in range(mg+1):
        for g2 in range(mg+1):
            exact[(g1,g2)] = poisson(e1,g1)*poisson(e2,g2)
    w1 = sum(p for (g1,g2),p in exact.items() if g1>g2)
    dr = sum(p for (g1,g2),p in exact.items() if g1==g2)
    w2 = sum(p for (g1,g2),p in exact.items() if g1<g2)
    ml = max(exact, key=exact.get)
    return {"w1": round(w1*100,1), "dr": round(dr*100,1), "w2": round(w2*100,1),
            "ml": list(ml), "pml": round(exact[ml]*100,1)}

def update_strengths(m, strengths, league_avg):
    t1, t2, g1, g2 = m["team1"], m["team2"], m["g1"], m["g2"]
    for team, opp, gf, ga in [(t1,t2,g1,g2), (t2,t1,g2,g1)]:
        s, op = strengths[team], strengths[opp]
        lr = min(0.12, 1.0/(1.0+s["pj_hist"]))
        obs_a = gf / (league_avg * op["defense"]) if op["defense"] > 0 else 1.0
        obs_d = ga / (league_avg * op["attack"]) if op["attack"] > 0 else 1.0

        s["attack_hist"] = (1-lr)*s["attack_hist"] + lr*obs_a
        s["defense_hist"] = (1-lr)*s["defense_hist"] + lr*obs_d
        s["attack"] = s["attack_hist"]
        s["defense"] = s["defense_hist"]
        s["pj"] += 1
        s["pj_hist"] += 1

        update_form(team, obs_a, obs_d, strengths)

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
    print(f"{'Equipo':25s} {'Ataque':>8s} {'Defensa':>8s} {'PJ':>4s} {'RankF':>6s} {'Fuente'}")
    print("-" * 55)
    for team in sorted(all_teams):
        s = strengths[team]
        rf = RANKING_FACTOR.get(team, 1.0)
        src = "Estimado" if s.get("estimated",False) else "Real"
        print(f"{es(team):25s} {s['attack']:>8.2f} {s['defense']:>8.2f} {s['pj_hist']:>4d} {rf:>5.2f} {src}")

    matches = load_2026()
    if not matches:
        print("Error: no se pudieron cargar los datos 2026")
        return

    print(f"\n{'='*60}")
    print("  SIMULACION SECUENCIAL CON MOMENTUM")
    print(f"{'='*60}\n")

    log = []
    c_result, c_exact, total = 0, 0, 0

    for m in matches:
        t1, t2 = m["team1"], m["team2"]
        is_ko = "Round" in m.get("group","") or "Quarter" in m.get("group","") or "Semi" in m.get("group","") or "Final" in m.get("group","") or not m.get("group","").startswith("Group")
        knockout = not m.get("group","").startswith("Group") and m.get("group","") != ""

        e1, e2 = predict(t1, t2, strengths, league_avg, knockout)
        p = probs(e1, e2)
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
            print(f"    Pred: {p['ml'][0]}-{p['ml'][1]}  {es(t1)}:{p['w1']:.0f}% Emp:{p['dr']:.0f}% {es(t2)}:{p['w2']:.0f}%   (lr={min(0.12,1.0/(1.0+strengths[t1]['pj_hist'])):.3f})")

            update_strengths({"team1":t1,"team2":t2,"g1":g1,"g2":g2}, strengths, league_avg)

            # Show updated strength
            s1, s2 = strengths[t1], strengths[t2]
            print(f"    → {es(t1)}: atk={s1['attack_hist']:.2f} def={s1['defense_hist']:.2f} form={s1['attack_form']:.2f}/{s1['defense_form']:.2f}")
            print(f"    → {es(t2)}: atk={s2['attack_hist']:.2f} def={s2['defense_hist']:.2f} form={s2['attack_form']:.2f}/{s2['defense_form']:.2f}")
            print()

            log.append({"date":m["date"],"group":m["group"],"t1":t1,"t2":t2,
                       "pred":list(p["ml"]),"real":[g1,g2],"r_ok":r_ok,"e_ok":e_ok})
        else:
            print(f"  {m['date']} | {m['group']}")
            print(f"     {es(t1):25s} vs {es(t2)}")
            print(f"    Pred: {p['ml'][0]}-{p['ml'][1]}  {es(t1)}:{p['w1']:.0f}% Emp:{p['dr']:.0f}% {es(t2)}:{p['w2']:.0f}%")
            print()

    print("="*60)
    if total > 0:
        print(f"\n  RESULTADOS: {c_result}/{total} resultado ({c_result/total*100:.1f}%) | {c_exact}/{total} exacto ({c_exact/total*100:.1f}%)")
    print()

    print("=== FUERZA FINAL ===\n")
    print(f"{'Equipo':25s} {'Ataque':>8s} {'Defensa':>8s} {'FormAtk':>8s} {'FormDef':>8s} {'PJ':>4s}")
    print("-" * 60)
    for team in sorted(all_teams):
        s = strengths[team]
        nf = len(s.get("form_obs", []))
        marca = ""
        if nf > 0 and s["pj_hist"] > 3:
            if abs(s["attack_form"] - s["attack_hist"]) > 0.15:
                marca = " ⚡"
        print(f"{es(team):25s} {s['attack_hist']:>8.2f} {s['defense_hist']:>8.2f} {s['attack_form']:>8.2f} {s['defense_form']:>8.2f} {s['pj']:>4d}{marca}")

if __name__ == "__main__":
    run_simulation()
