#!/usr/bin/env python3
"""Servidor web - Modelo Poisson con ranking FIFA, momentum y actualizacion automatica"""

import json, urllib.request, os, threading, time, re
from collections import defaultdict
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from modelo import (
    es, fetch_json, NAME_MAP, HIST_MAP, conf_map,
    RANKING_FACTOR, KO_FACTOR, build_model, predict, probs,
    update_strengths, norm_team, fetch_market_odds
)

DATA_DIR = os.path.dirname(__file__)
API_2026 = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"
POLL_INTERVAL = 120
LOG_FILE = os.path.join(DATA_DIR, "log.json")

def fmt_ph(name):
    if name.startswith("W"):
        return f"Ganador {name[1:]}"
    if name.startswith("L"):
        return f"Perdedor {name[1:]}"
    if name.startswith(("1","2")):
        return f"{'1°' if name[0]=='1' else '2°'} {name[1:]}"
    if name.startswith("3"):
        return name
    return name

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def fetch_txt(url):
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        return resp.read().decode("utf-8")
    except:
        return None

TXT_API = "https://raw.githubusercontent.com/openfootball/worldcup/master/2026--usa/cup.txt"

def parse_cup_txt():
    """Fetch and parse cup.txt for live scores not yet in JSON."""
    data = fetch_txt(TXT_API)
    if not data:
        return {}, {}
    wc = set().union(*conf_map.values())
    # Build name map from TXT convention to model names
    txt_names = {"Korea Republic": "South Korea", "IR Iran": "Iran",
                 "Cabo Verde": "Cape Verde", "Congo DR": "DR Congo",
                 "Côte d'Ivoire": "Ivory Coast", "Czechia": "Czech Republic",
                 "Türkiye": "Turkey"}
    def fn(name):
        return txt_names.get(name, name)
    scores = {}   # (team1, team2) -> (g1, g2)
    fixtures = {} # (team1, team2) -> True
    for line in data.splitlines():
        line = line.rstrip()
        if not line or line.startswith(("#","=","▪","Group")):
            continue
        # Match line examples:
        # "  15:00 UTC-4     Canada   1-1 (0-1) Bosnia & Herzegovina    @ Toronto"
        # "  12:00 UTC-7     Switzerland  v Bosnia & Herzegovina   @ Los Angeles"
        parts = re.split(r"\s{3,}", line)
        if len(parts) < 3:
            continue
        if " v " in parts[1]:
            # Unplayed: "Switzerland  v Bosnia & Herzegovina"
            t1 = fn(parts[1].split(" v ")[0].strip())
            t2 = fn(parts[1].split(" v ")[1].strip())
            if t1 in wc and t2 in wc:
                fixtures[(t1, t2)] = True
        else:
            # Played: parts[1]="Canada", parts[2]="1-1 (0-1) Bosnia & Herzegovina"
            t1 = fn(parts[1].strip())
            sm = re.match(r"(\d+)-(\d+)", parts[2].strip())
            if sm:
                rest = parts[2][sm.end():].strip()
                # Remove halftime score "(0-1)"
                rest = re.sub(r"\(.*?\)", "", rest).strip()
                t2 = fn(rest) if rest else ""
                if t1 in wc and t2 in wc:
                    scores[(t1, t2)] = (int(sm.group(1)), int(sm.group(2)))
    return scores, fixtures

state = {
    "strengths": {}, "league_avg": 2.59, "matches": [], "log": [],
    "last_update": None, "correct_result": 0, "correct_exact": 0, "total_played": 0,
}
_bracket_cache = []
_bracket_dirty = True

_state_lock = threading.Lock()


def resolve_bracket(raw_matches):
    groups = defaultdict(dict)
    for m in raw_matches:
        if "Matchday" not in m.get("round", ""):
            continue
        g = m["group"]
        for t in [m["team1"], m["team2"]]:
            if t not in groups[g]:
                groups[g][t] = {"pts": 0, "gd": 0, "gf": 0, "gp": 0}
        if "score" not in m:
            continue
        t1, t2 = m["team1"], m["team2"]
        g1, g2 = m["score"]["ft"]
        for team, gf, ga in [(t1, g1, g2), (t2, g2, g1)]:
            p = groups[g][team]
            p["gp"] += 1
            p["pts"] += 3 if gf > ga else (1 if gf == ga else 0)
            p["gd"] += gf - ga
            p["gf"] += gf

    pos_map = {}
    for grp, teams in groups.items():
        played = any(v["gp"] > 0 for v in teams.values())
        ranked = sorted(teams.items(), key=lambda x: (-x[1]["pts"], -x[1]["gd"], -x[1]["gf"]))
        for rk, (tm, _) in enumerate(ranked, 1):
            if played:
                pos_map[f"{rk}{grp.replace('Group ','')}"] = tm

    third = []
    for grp, teams in groups.items():
        played = any(v["gp"] > 0 for v in teams.values())
        if not played:
            continue
        ranked = sorted(teams.items(), key=lambda x: (-x[1]["pts"], -x[1]["gd"], -x[1]["gf"]))
        tm, st = ranked[2]
        third.append((tm, st["pts"], st["gd"], st["gf"], grp.replace("Group ", "")))
    third.sort(key=lambda x: (-x[1], -x[2], -x[3]))
    qual = {t[4] for t in third[:8]}

    third_slots = [
        (["A","B","C","D","F"], 2), (["C","D","F","G","H"], 5),
        (["C","E","F","H","I"], 7), (["E","H","I","J","K"], 8),
        (["B","E","F","I","J"], 9), (["A","E","H","I","J"], 10),
        (["E","F","G","I","J"], 13),(["D","E","I","J","L"], 15),
    ]
    used = set()
    for slot_groups, _ in sorted(third_slots, key=lambda x: len([g for g in x[0] if g in qual])):
        for tm, _, _, _, grp in third[:8]:
            if grp in slot_groups and grp not in used:
                pos_map[f"3_{_}"] = tm
                used.add(grp)
                break

    def ph(name):
        if name in pos_map:
            return pos_map[name]
        parts = name.split("/")
        for tm, _, _, _, grp in third[:8]:
            if grp in parts and grp in qual:
                return tm
        return name

    resolved = []
    for m in raw_matches:
        t1, t2 = m["team1"], m["team2"]
        r = dict(m)
        r["team1"] = ph(t1)
        r["team2"] = ph(t2)
        resolved.append(r)
    return resolved, pos_map

def save_log():
    try:
        with open(LOG_FILE, "w") as f:
            json.dump(state["log"], f)
    except:
        pass

def load_log():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE) as f:
                state["log"] = json.load(f)
        except:
            pass

def fetch_and_update():
    global _bracket_cache, _bracket_dirty
    data = fetch_json(API_2026)
    if not data:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    matches_all = []
    for m in data["matches"]:
        matches_all.append(m)

    with _state_lock:
        state["matches"] = sorted(matches_all, key=lambda x: (x["date"], x.get("time", "")))

        # TXT scores como respaldo (openfootball/worldcup se actualiza antes que worldcup.json)
        txt_scores, _ = parse_cup_txt()
        for m in state["matches"]:
            if "score" not in m:
                key = (m["team1"], m["team2"])
                if key in txt_scores:
                    m["score"] = {"ft": list(txt_scores[key])}

        known_keys = {x["key"] for x in state["log"]}
        new_results = []
        for m in state["matches"]:
            if "score" not in m:
                continue
            t1, t2, g1, g2 = m["team1"], m["team2"], m["score"]["ft"][0], m["score"]["ft"][1]
            lk = f"{m['date']}|{t1}|{t2}"
            if lk not in known_keys:
                new_results.append(m)
                state["log"].append({"key": lk, "date": m["date"], "team1": t1, "team2": t2, "g1": g1, "g2": g2})

        market_odds = fetch_market_odds()
        for m in new_results:
            t1, t2, g1, g2 = m["team1"], m["team2"], m["score"]["ft"][0], m["score"]["ft"][1]
            rnd = m.get("round", m.get("group", ""))
            e1, e2 = predict(t1, t2, state["strengths"], state["league_avg"], rnd)
            mo = market_odds.get((norm_team(t1), norm_team(t2)))
            if not mo:
                mo_rev = market_odds.get((norm_team(t2), norm_team(t1)))
                if mo_rev:
                    mo = (mo_rev[2], mo_rev[1], mo_rev[0])
            p = probs(e1, e2, market_odds=mo)
            pw = 0 if p["ml"][0] > p["ml"][1] else (2 if p["ml"][0] < p["ml"][1] else 1)
            rw = 0 if g1 > g2 else (2 if g1 < g2 else 1)
            r_ok = pw == rw
            e_ok = p["ml"][0]==g1 and p["ml"][1]==g2
            if r_ok: state["correct_result"] += 1
            if e_ok: state["correct_exact"] += 1
            state["total_played"] += 1
            state["league_avg"] = update_strengths({"team1": t1, "team2": t2, "g1": g1, "g2": g2}, state["strengths"], state["league_avg"])

            lk = f"{m['date']}|{t1}|{t2}"
            for entry in state["log"]:
                if entry["key"] == lk:
                    entry.update({"pred": list(p["ml"]), "result_ok": r_ok, "exact_ok": e_ok})
                    break

        # Limitar tamaño del log
        if len(state["log"]) > 500:
            state["log"] = state["log"][-250:]

        if new_results:
            _bracket_dirty = True
            log(f"Procesados {len(new_results)} nuevos resultados")

        state["last_update"] = now
    save_log()

    # Recalcular aciertos desde el log completo (por si hubo reinicio)
    state["correct_result"] = sum(1 for x in state["log"] if x.get("result_ok"))
    state["correct_exact"] = sum(1 for x in state["log"] if x.get("exact_ok"))
    state["total_played"] = len([x for x in state["log"] if x.get("result_ok") is not None])

def get_cached_bracket():
    global _bracket_cache, _bracket_dirty
    if _bracket_dirty or not _bracket_cache:
        _bracket_cache, pm = resolve_bracket(state["matches"])
        _bracket_dirty = False
    return _bracket_cache

def bg_loop():
    while True:
        try:
            fetch_and_update()
        except Exception as e:
            log(f"Error en bg: {e}")
        time.sleep(POLL_INTERVAL)

with open(os.path.join(DATA_DIR, "template.html")) as f:
    HTML = f.read()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with _state_lock:
                cards = []
                market_odds = fetch_market_odds()
                resolved = get_cached_bracket()
                for m in resolved:
                    t1, t2 = m["team1"], m["team2"]
                    rnd = m.get("round", m.get("group", ""))
                    known = t1 in state["strengths"] and t2 in state["strengths"]
                    if known:
                        e1, e2 = predict(t1, t2, state["strengths"], state["league_avg"], rnd)
                        mo = market_odds.get((norm_team(t1), norm_team(t2)))
                        if not mo:
                            mo_rev = market_odds.get((norm_team(t2), norm_team(t1)))
                            if mo_rev:
                                mo = (mo_rev[2], mo_rev[1], mo_rev[0])
                        p = probs(e1, e2, market_odds=mo)
                    else:
                        p = {"w1":0,"dr":0,"w2":0,"ml":[0,0],"pml":0}
                    card = {
                        "date": m["date"], "group": m.get("group", ""), "round": rnd,
                        "t1": fmt_ph(t1) if t1 not in state["strengths"] else es(t1),
                        "t2": fmt_ph(t2) if t2 not in state["strengths"] else es(t2),
                        "w1": p["w1"], "dr": p["dr"], "w2": p["w2"],
                        "ml": list(p["ml"]), "pml": p["pml"],
                        "played": "score" in m,
                    }
                    if "score" in m:
                        card["real"] = m["score"]["ft"]
                        for l in reversed(state["log"]):
                            if l["team1"]==t1 and l["team2"]==t2 and l["date"]==m["date"]:
                                card["result_ok"] = l.get("result_ok", False)
                                card["exact_ok"] = l.get("exact_ok", False)
                                break
                    s = state["strengths"].get(t1, {})
                    nf = len(s.get("form_obs", []))
                    card["momentum"] = nf >= 2
                    cards.append(card)
                pending = sum(1 for c in cards if not c["played"])
                self.wfile.write(json.dumps({
                    "stats": {
                        "correct_result": state["correct_result"],
                        "correct_exact": state["correct_exact"],
                        "total_played": state["total_played"],
                        "pending": pending,
                    },
                    "cards": cards,
                    "last_update": state["last_update"] or "Nunca",
                }).encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode("utf-8"))

def run(port=8080):
    server = HTTPServer(("0.0.0.0", port), Handler)
    log(f"Servidor iniciado en puerto {port}")
    server.serve_forever()

if __name__ == "__main__":
    import sys
    log("Inicializando modelo...")
    s, la, _ = build_model()
    state["strengths"] = s
    state["league_avg"] = la
    load_log()
    fetch_and_update()
    t = threading.Thread(target=bg_loop, daemon=True)
    t.start()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run(port)
