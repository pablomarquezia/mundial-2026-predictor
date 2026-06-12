#!/usr/bin/env python3
"""World Cup 2026 Prediction System"""

import json
import os
import sys
from datetime import datetime
from urllib.request import urlopen

API_URL = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"
DATA_FILE = os.path.join(os.path.dirname(__file__), "worldcup_data.json")
PRED_FILE = os.path.join(os.path.dirname(__file__), "predictions.json")

TEAMS_BY_CONF = {
    "Anfitrionas": ["Canadá", "México", "Estados Unidos"],
    "AFC": ["Australia", "RI de Irán", "Japón", "Jordania", "República de Corea", "Catar", "Arabia Saudí", "Uzbekistán", "Irak"],
    "CAF": ["Argelia", "Cabo Verde", "Costa de Marfil", "Egipto", "Ghana", "Marruecos", "Senegal", "Sudáfrica", "Túnez", "RD Congo"],
    "CONCACAF": ["Curazao", "Haití", "Panamá"],
    "CONMEBOL": ["Argentina", "Brasil", "Colombia", "Ecuador", "Paraguay", "Uruguay"],
    "OFC": ["Nueva Zelanda"],
    "UEFA": ["Austria", "Bélgica", "Bosnia y Herzegovina", "Croacia", "República Checa", "Inglaterra", "Francia", "Alemania", "Países Bajos", "Noruega", "Portugal", "Escocia", "España", "Suecia", "Suiza", "Turquía"],
}

NAME_MAP = {
    "Canadá": "Canada", "México": "Mexico", "Estados Unidos": "USA",
    "RI de Irán": "Iran", "República de Corea": "South Korea",
    "Catar": "Qatar", "Arabia Saudí": "Saudi Arabia",
    "Cabo Verde": "Cape Verde", "Costa de Marfil": "Ivory Coast",
    "RD Congo": "DR Congo", "Bosnia y Herzegovina": "Bosnia & Herzegovina",
    "República Checa": "Czech Republic", "Países Bajos": "Netherlands",
    "Nueva Zelanda": "New Zealand",
}

NAME_MAP_REV = {v: k for k, v in NAME_MAP.items()}

def eng_to_spanish(name):
    return NAME_MAP_REV.get(name, name)

def spanish_to_eng(name):
    return NAME_MAP.get(name, name)

def fetch_data():
    print("Descargando datos del Mundial 2026...")
    try:
        resp = urlopen(API_URL, timeout=10)
        data = json.loads(resp.read())
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
        print("OK - datos guardados")
        return data
    except Exception as e:
        print(f"Error al descargar: {e}")
        if os.path.exists(DATA_FILE):
            print("Usando datos locales...")
            with open(DATA_FILE) as f:
                return json.load(f)
        return None

def load_local_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return None

def list_teams():
    print("\n=== PAÍSES PARTICIPANTES - MUNDIAL 2026 ===\n")
    for conf, equipos in TEAMS_BY_CONF.items():
        print(f"  {conf}:")
        for e in equipos:
            print(f"    - {e}")
        print()

def list_groups(data):
    matches = data["matches"]
    groups = {}
    for m in matches:
        g = m.get("group")
        if g:
            groups.setdefault(g, set()).add(m["team1"])
            groups.setdefault(g, set()).add(m["team2"])

    print("\n=== GRUPOS DEL MUNDIAL 2026 ===\n")
    for g in sorted(groups.keys()):
        print(f"  {g}:")
        for t in sorted(groups[g]):
            es = eng_to_spanish(t)
            print(f"    - {es} ({t})")
        print()

def list_matches(data, filtro=None):
    matches = data["matches"]
    print("\n=== PARTIDOS ===\n")
    for m in matches:
        g = m.get("group", "Eliminación")
        if filtro and filtro not in g and filtro not in m["team1"] and filtro not in m["team2"]:
            continue
        t1 = f"{eng_to_spanish(m['team1'])} ({m['team1']})"
        t2 = f"{eng_to_spanish(m['team2'])} ({m['team2']})"
        score = ""
        if "score" in m:
            s = m["score"]
            score = f" {s['ft'][0]}-{s['ft'][1]}"
        fecha = f"{m['date']} {m.get('time','')}"
        print(f"  [{g}] {fecha} | {t1} vs {t2}{score}")

def load_predictions():
    if os.path.exists(PRED_FILE):
        with open(PRED_FILE) as f:
            return json.load(f)
    return {}

def save_predictions(preds):
    with open(PRED_FILE, "w") as f:
        json.dump(preds, f, indent=2)

def make_prediction(data):
    matches = [m for m in data["matches"] if "score" not in m and m.get("group")]
    if not matches:
        print("\nNo hay partidos futuros en fase de grupos.")
        return

    print("\n=== PARTIDOS DISPONIBLES PARA PREDECIR ===\n")
    for i, m in enumerate(matches, 1):
        t1 = eng_to_spanish(m["team1"])
        t2 = eng_to_spanish(m["team2"])
        print(f"  {i}. [{m['group']}] {t1} vs {t2} - {m['date']}")

    print("\n(Si el partido no está en la lista, se puede ingresar manualmente)")

    preds = load_predictions()

    while True:
        try:
            idx = input("\nN° del partido (0 para salir): ").strip()
            if not idx:
                continue
            idx = int(idx)
            if idx == 0:
                break
            if 1 <= idx <= len(matches):
                m = matches[idx - 1]
                key = f"{m['date']}|{m['team1']}|{m['team2']}"
                print(f"\n  {eng_to_spanish(m['team1'])} vs {eng_to_spanish(m['team2'])}")
                g1 = int(input("  Goles equipo 1: "))
                g2 = int(input("  Goles equipo 2: "))
                preds[key] = {"team1": m["team1"], "team2": m["team2"], "pred": [g1, g2], "date": m["date"]}
                save_predictions(preds)
                print("  ✓ Predicción guardada!")
            else:
                print("  Número inválido")
        except ValueError:
            print("  Ingresá un número válido")

def evaluate_predictions(data):
    preds = load_predictions()
    if not preds:
        print("\nNo hay predicciones guardadas.")
        return

    matches = {f"{m['date']}|{m['team1']}|{m['team2']}": m for m in data["matches"]}

    print("\n=== EVALUACIÓN DE PREDICCIONES ===\n")
    pts_total = 0
    pts_posibles = 0
    for key, p in sorted(preds.items()):
        m = matches.get(key)
        if not m or "score" not in m:
            status = "Pendiente" if not m else "Sin resultado"
            print(f"  {p['date']} | {eng_to_spanish(p['team1'])} vs {eng_to_spanish(p['team2'])} | {status}")
            continue

        s = m["score"]["ft"]
        pred = p["pred"]
        t1, t2 = p["team1"], p["team2"]
        es1, es2 = eng_to_spanish(t1), eng_to_spanish(t2)

        pts = 0
        if pred[0] == s[0] and pred[1] == s[1]:
            pts = 3
            marca = "✓ EXACTO (+3)"
        elif (pred[0] - pred[1]) * (s[0] - s[1]) > 0:
            pts = 1
            marca = "~ RESULTADO (+1)"
        elif pred[0] == pred[1] and s[0] == s[1]:
            pts = 1
            marca = "~ EMPATE (+1)"
        else:
            marca = "✗ EQUIVOCADO (+0)"

        pts_total += pts
        pts_posibles += 3
        print(f"  {p['date']} | {es1} vs {es2}")
        print(f"    Pred: {pred[0]}-{pred[1]} | Real: {s[0]}-{s[1]} | {marca}")

    if pts_posibles > 0:
        pct = (pts_total / pts_posibles) * 100
        print(f"\n  Puntaje: {pts_total}/{pts_posibles} ({pct:.1f}%)")
    else:
        print("\n  Sin resultados para evaluar aún.")

def show_standings(data):
    matches = data["matches"]
    groups = {}
    for m in matches:
        g = m.get("group")
        if not g:
            continue
        groups.setdefault(g, {})
        for t in [m["team1"], m["team2"]]:
            groups[g].setdefault(t, {"pts": 0, "gf": 0, "ga": 0, "pj": 0})
        if "score" in m:
            s = m["score"]["ft"]
            t1, t2 = m["team1"], m["team2"]
            groups[g][t1]["pj"] += 1
            groups[g][t2]["pj"] += 1
            groups[g][t1]["gf"] += s[0]
            groups[g][t1]["ga"] += s[1]
            groups[g][t2]["gf"] += s[1]
            groups[g][t2]["ga"] += s[0]
            if s[0] > s[1]:
                groups[g][t1]["pts"] += 3
            elif s[1] > s[0]:
                groups[g][t2]["pts"] += 3
            else:
                groups[g][t1]["pts"] += 1
                groups[g][t2]["pts"] += 1

    print("\n=== TABLA DE POSICIONES ===\n")
    for g in sorted(groups.keys()):
        print(f"  {g}:")
        sorted_teams = sorted(groups[g].items(), key=lambda x: (-x[1]["pts"], -(x[1]["gf"] - x[1]["ga"]), -x[1]["gf"]))
        for i, (t, d) in enumerate(sorted_teams, 1):
            es = eng_to_spanish(t)
            print(f"    {i}. {es:25s} {d['pj']} PJ  {d['pts']} pts  GD: {d['gf']-d['ga']:+d}  GF:{d['gf']}  GA:{d['ga']}")
        print()

def main():
    data = load_local_data()
    if data is None:
        data = fetch_data()
    if data is None:
        print("No se pudieron cargar los datos.")
        sys.exit(1)

    while True:
        print("\n" + "=" * 50)
        print("  MUNDIAL 2026 - SISTEMA DE PREDICCIONES")
        print("  by Mercado Pago Challenge 💰 $50.000")
        print("=" * 50)
        print("  1. Listar países participantes")
        print("  2. Ver grupos")
        print("  3. Ver todos los partidos")
        print("  4. Tabla de posiciones")
        print("  5. Hacer predicción")
        print("  6. Evaluar predicciones")
        print("  7. Recargar datos desde API")
        print("  0. Salir")
        op = input("\n  Opción: ").strip()

        if op == "1":
            list_teams()
        elif op == "2":
            list_groups(data)
        elif op == "3":
            list_matches(data)
        elif op == "4":
            show_standings(data)
        elif op == "5":
            make_prediction(data)
        elif op == "6":
            evaluate_predictions(data)
        elif op == "7":
            data = fetch_data()
        elif op == "0":
            print("  ¡Suerte en la competencia!")
            break

if __name__ == "__main__":
    main()
