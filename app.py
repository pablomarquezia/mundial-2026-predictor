#!/usr/bin/env python3
"""Servidor web - Modelo Poisson con ranking FIFA, momentum y actualizacion automatica"""

import json, math, urllib.request, os, threading, time
from collections import defaultdict, deque
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

DATA_DIR = os.path.dirname(__file__)
API_2026 = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"
WC_YEARS = [2014, 2018, 2022]
HIST_API = "https://raw.githubusercontent.com/openfootball/worldcup.json/master"
POLL_INTERVAL = 120

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

def es(t): return NAME_MAP.get(t, t)

def fetch_json(url):
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        return json.loads(resp.read())
    except:
        return None

state = {
    "strengths": {}, "league_avg": 2.59, "matches": [], "log": [],
    "last_update": None, "correct_result": 0, "correct_exact": 0, "total_played": 0,
}

def init_model():
    all_m = []
    for year in WC_YEARS:
        data = fetch_json(f"{HIST_API}/{year}/worldcup.json")
        if not data:
            continue
        for m in data["matches"]:
            if "score" not in m:
                continue
            t1 = HIST_MAP.get(m["team1"], m["team1"])
            t2 = HIST_MAP.get(m["team2"], m["team2"])
            all_m.append({"team1": t1, "team2": t2, "g1": m["score"]["ft"][0], "g2": m["score"]["ft"][1]})

    stats = defaultdict(lambda: {"gf": 0, "ga": 0, "pj": 0})
    for m in all_m:
        for t, gf, ga in [(m["team1"], m["g1"], m["g2"]), (m["team2"], m["g2"], m["g1"])]:
            stats[t]["gf"] += gf; stats[t]["ga"] += ga; stats[t]["pj"] += 1

    league_avg = sum(m["g1"]+m["g2"] for m in all_m) / len(all_m) / 2 if all_m else 1
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

def update_form(team, obs_a, obs_d):
    s = state["strengths"][team]
    s["form_obs"].append((obs_a, obs_d))
    if s["form_obs"]:
        s["attack_form"] = sum(o[0] for o in s["form_obs"]) / len(s["form_obs"])
        s["defense_form"] = sum(o[1] for o in s["form_obs"]) / len(s["form_obs"])

def combined(team):
    s = state["strengths"].get(team, {})
    nf = len(s.get("form_obs", []))
    w_hist = 0.85 if nf < 2 else max(0.3, 0.7 - 0.05 * nf)
    atk = w_hist * s["attack_hist"] + (1-w_hist) * s["attack_form"]
    dfn = w_hist * s["defense_hist"] + (1-w_hist) * s["defense_form"]
    return atk, dfn

def predict(t1, t2, ko=False):
    s = state["strengths"]
    la = state["league_avg"]
    a1, d1 = combined(t1)
    a2, d2 = combined(t2)
    e1 = la * a1 * d2
    e2 = la * a2 * d1
    if ko:
        e1 *= 0.82; e2 *= 0.82
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

def update(t1, t2, g1, g2):
    s = state["strengths"]
    la = state["league_avg"]
    for team, opp, gf, ga in [(t1,t2,g1,g2), (t2,t1,g2,g1)]:
        st, op = s[team], s[opp]
        lr = min(0.12, 1.0/(1.0+st["pj_hist"]))
        obs_a = gf / (la * op["defense"]) if op["defense"] > 0 else 1.0
        obs_d = ga / (la * op["attack"]) if op["attack"] > 0 else 1.0
        st["attack_hist"] = (1-lr)*st["attack_hist"] + lr*obs_a
        st["defense_hist"] = (1-lr)*st["defense_hist"] + lr*obs_d
        st["attack"] = st["attack_hist"]
        st["defense"] = st["defense_hist"]
        st["pj"] += 1
        st["pj_hist"] += 1
        update_form(team, obs_a, obs_d)

def fetch_and_update():
    data = fetch_json(API_2026)
    if not data:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    matches_2026 = {}
    for m in data["matches"]:
        if not m.get("group"):
            continue
        matches_2026[(m["date"], m["team1"], m["team2"])] = m

    state["matches"] = sorted(matches_2026.values(), key=lambda x: (x["date"], x.get("time", "")))

    new_results = []
    for m in state["matches"]:
        if "score" not in m:
            continue
        t1, t2, g1, g2 = m["team1"], m["team2"], m["score"]["ft"][0], m["score"]["ft"][1]
        lk = f"{m['date']}|{t1}|{t2}"
        if lk not in {x["key"] for x in state["log"]}:
            new_results.append(m)
            state["log"].append({"key": lk, "date": m["date"], "team1": t1, "team2": t2, "g1": g1, "g2": g2})

    for m in new_results:
        t1, t2, g1, g2 = m["team1"], m["team2"], m["score"]["ft"][0], m["score"]["ft"][1]
        ko = not m["group"].startswith("Group")
        e1, e2 = predict(t1, t2, ko)
        p = probs(e1, e2)
        pw = 0 if p["ml"][0] > p["ml"][1] else (2 if p["ml"][0] < p["ml"][1] else 1)
        rw = 0 if g1 > g2 else (2 if g1 < g2 else 1)
        r_ok = pw == rw
        e_ok = p["ml"][0]==g1 and p["ml"][1]==g2
        if r_ok: state["correct_result"] += 1
        if e_ok: state["correct_exact"] += 1
        state["total_played"] += 1
        update(t1, t2, g1, g2)

        lk = f"{m['date']}|{t1}|{t2}"
        for entry in state["log"]:
            if entry["key"] == lk:
                entry.update({"pred": list(p["ml"]), "result_ok": r_ok, "exact_ok": e_ok})
                break

    state["last_update"] = now

def bg_loop():
    while True:
        try:
            fetch_and_update()
        except:
            pass
        time.sleep(POLL_INTERVAL)

HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mundial 2026 - Predicciones</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a1a;color:#e0e0e0;padding:20px}
h1{color:#fff;font-size:1.5em;margin-bottom:5px}
.sub{color:#888;margin-bottom:20px;font-size:0.9em}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(400px,1fr));gap:12px}
.card{background:#141428;border-radius:10px;padding:14px;border:1px solid #2a2a4a}
.card.played{border-color:#3a5a3a;background:#121a12}
.card.pending{border-color:#2a2a4a}
.ko{background:#1a1428;border-color:#4a2a6a}
.matchup{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px}
.team{flex:1;font-weight:600;font-size:0.95em}
.team.left{text-align:right}
.team.right{text-align:left}
.score{font-size:1.3em;font-weight:700;padding:0 10px;color:#fff;text-align:center;min-width:50px}
.vs{color:#555;font-size:0.8em;padding:0 5px}
.meta{font-size:0.75em;color:#666;margin-bottom:6px}
.bar-cont{display:flex;height:6px;border-radius:3px;overflow:hidden;margin:6px 0}
.bar-win{background:#22c55e;transition:width 0.5s}
.bar-draw{background:#a855f7;transition:width 0.5s}
.bar-loss{background:#ef4444;transition:width 0.5s}
.pct-row{display:flex;justify-content:space-between;font-size:0.75em;color:#888;margin-bottom:4px}
.pct-row .pct{font-weight:600}
.pct-win{color:#22c55e}
.pct-draw{color:#a855f7}
.pct-loss{color:#ef4444}
.pred{font-size:0.8em;color:#555;margin-top:4px}
.pred .ml{color:#facc15;font-weight:600}
.momentum{font-size:0.7em;color:#22c55e;margin-left:4px}
.status{display:inline-block;font-size:0.7em;padding:2px 8px;border-radius:10px;margin-left:6px}
.ok{background:#166534;color:#86efac}
.err{background:#7f1d1d;color:#fca5a5}
.update{color:#666;font-size:0.8em;margin-top:12px;text-align:center}
.stats-row{display:flex;gap:20px;margin-bottom:16px;flex-wrap:wrap}
.stat{background:#141428;border-radius:8px;padding:10px 16px;border:1px solid #2a2a4a;text-align:center;min-width:100px}
.stat .num{font-size:1.4em;font-weight:700;color:#facc15}
.stat .lbl{font-size:0.75em;color:#666}
.tag{display:inline-block;font-size:0.65em;padding:1px 6px;border-radius:4px;margin-left:4px}
.tag-ko{background:#4a2a6a;color:#c4a0e8}
.tag-group{background:#1a3a2a;color:#86efac}
</style>
</head>
<body>
<h1>🏆 Mundial 2026 - Predicciones</h1>
<div class="sub">Modelo Poisson + Ranking FIFA + Momentum intra-torneo</div>
<div class="stats-row" id="stats">Cargando...</div>
<div class="grid" id="cards">Cargando...</div>
<div class="update" id="update"></div>
<script>
async function load(){
try{
const r=await fetch('/api');
const d=await r.json();
const s=d.stats, cards=d.cards;
document.getElementById('stats').innerHTML=
'<div class=stat><div class=num>'+s.correct_result+'/'+s.total_played+'</div><div class=lbl>Acertados</div></div>'+
'<div class=stat><div class=num>'+(s.total_played?(s.correct_result/s.total_played*100|0):'-')+'%</div><div class=lbl>Precisión</div></div>'+
'<div class=stat><div class=num>'+s.pending+'</div><div class=lbl>Pendientes</div></div>'+
'<div class=stat><div class=num>'+s.total_played+'</div><div class=lbl>Jugados</div></div>';
let html='';
cards.forEach(c=>{
let cls=c.played?'played':'pending';
if(!c.group?.startsWith('Group')) cls+=' ko';
let sc='';
if(c.played){
let ok=c.result_ok?'ok':'err';
let em=c.exact_ok?'✓':' ';
sc='<span class="status '+ok+'">'+em+' '+c.real[0]+'-'+c.real[1]+'</span>';
}
let tag='<span class="tag tag-group">Grupo</span>';
if(!c.group?.startsWith('Group')) tag='<span class="tag tag-ko">Elim.</span>';
let mom=c.momentum?'<span class="momentum">⚡</span>':'';
html+=`
<div class="card ${cls}">
<div class="meta">${c.date} ${tag}</div>
<div class=matchup>
<div class="team left">${c.t1}</div>
${c.played?`<div class=score>${c.real[0]}-${c.real[1]}</div>`:'<div class=vs>vs</div>'}
<div class="team right">${c.t2}${mom}</div>
</div>
<div class=pct-row>
<span class="pct-win">${c.w1}%</span>
<span class="pct-draw">${c.dr}%</span>
<span class="pct-loss">${c.w2}%</span>
</div>
<div class=bar-cont><div class=bar-win style=width:${c.w1}%></div><div class=bar-draw style=width:${c.dr}%></div><div class=bar-loss style=width:${c.w2}%></div></div>
<div class=pred>Pred: <span class=ml>${c.ml[0]}-${c.ml[1]}</span> (${c.pml}%)${sc}</div>
</div>`;
});
document.getElementById('cards').innerHTML=html;
document.getElementById('update').textContent='Ultima actualizacion: '+d.last_update+' (cada 2min)';
}catch(e){}
}
load();
setInterval(load,30000);
</script>
</body>
</html>"""

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            cards = []
            for m in state["matches"]:
                t1, t2 = m["team1"], m["team2"]
                ko = not m["group"].startswith("Group")
                e1, e2 = predict(t1, t2, ko)
                p = probs(e1, e2)
                card = {
                    "date": m["date"], "group": m["group"],
                    "t1": es(t1), "t2": es(t2),
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
                card["momentum"] = nf >= 1
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
    print(f"Servidor: http://0.0.0.0:{port}")
    server.serve_forever()

if __name__ == "__main__":
    import sys
    print("Inicializando modelo mejorado...")
    s, la, _ = init_model()
    state["strengths"] = s
    state["league_avg"] = la
    fetch_and_update()
    t = threading.Thread(target=bg_loop, daemon=True)
    t.start()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run(port)
