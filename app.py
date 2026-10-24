import os
import uuid
import random
import math
import json
import tempfile
import gradio as gr
import pandas as pd
from upstash_redis import Redis

# ==========================================
# 1. CONSTANTS & GAME RULES
# ==========================================
CHOICES = {1: "Rock", 2: "Paper", 3: "Scissors", 4: "Lizard", 5: "Spock"}
ACTION_PHRASES = {
    (1, 3): "Rock crushes Scissors", (1, 4): "Rock crushes Lizard",
    (2, 1): "Paper covers Rock", (2, 5): "Paper disproves Spock",
    (3, 2): "Scissors cuts Paper", (3, 4): "Scissors decapitates Lizard",
    (4, 2): "Lizard eats Paper", (4, 5): "Lizard poisons Spock",
    (5, 1): "Spock vaporizes Rock", (5, 3): "Spock smashes Scissors"
}

# ==========================================
# 2. AUTO-SANITIZED REDIS DATABASE
# ==========================================
RAW_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "https://dummy.upstash.io")
RAW_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "dummy")

CLEAN_URL = RAW_URL.strip().strip('"').strip("'")
CLEAN_TOKEN = RAW_TOKEN.strip().strip('"').strip("'")

if not CLEAN_URL.startswith("http"): CLEAN_URL = "https://" + CLEAN_URL

redis_client = Redis(url=CLEAN_URL, token=CLEAN_TOKEN)

def get_room(room_code):
    try:
        data = redis_client.get(f"RG:{room_code}")
        return json.loads(data) if data else None
    except: return None

def save_room(room_code, room_data):
    key = f"RG:{room_code}"
    redis_client.set(key, json.dumps(room_data))
    redis_client.expire(key, 86400)

def room_exists(room_code):
    try: return redis_client.exists(f"RG:{room_code}") > 0
    except: return False

def generate_room_code():
    code = str(uuid.uuid4())[:5].upper()
    while room_exists(code): code = str(uuid.uuid4())[:5].upper()
    return code

def resolve_matchup(m1, m2):
    if m1 == m2: return "Tie"
    if (m1, m2) in ACTION_PHRASES: return "P1"
    return "P2"

# --- ALL LEADERBOARD HELPERS ---
def update_global_leaderboard(champ_name, mode, lobby_size):
    try:
        lb_str = redis_client.get("RG_GLOBAL_LEADERBOARD")
        lb = json.loads(lb_str) if lb_str else []
        lb.append([champ_name, mode, lobby_size, "🏆 Champion"])
        redis_client.set("RG_GLOBAL_LEADERBOARD", json.dumps(lb))
    except: pass

def update_offline_leaderboards(player_name, p_wins, c_wins, ties):
    try:
        if p_wins == 10:
            hof_str = redis_client.get("RG_OFFLINE_HOF")
            hof = json.loads(hof_str) if hof_str else []
            hof.insert(0, [player_name, "Perfect 10 Wins!", f"{p_wins}W - {c_wins}L - {ties}T"])
            redis_client.set("RG_OFFLINE_HOF", json.dumps(hof))
        else:
            lb_str = redis_client.get("RG_OFFLINE_LEADERBOARD")
            lb = json.loads(lb_str) if lb_str else []
            lb.append([player_name, p_wins, c_wins, ties, f"{(p_wins/10)*100:.0f}%"])
            lb = sorted(lb, key=lambda x: (-x[1], -x[3], str(x[0]).lower()))
            redis_client.set("RG_OFFLINE_LEADERBOARD", json.dumps(lb))
    except: pass

def update_offline_pvc_hof(champ_name, lobby_size):
    try:
        hof_str = redis_client.get("RG_OFFLINE_PVC_HOF")
        hof = json.loads(hof_str) if hof_str else []
        hof.append([champ_name, "PvC Offline Tournament (10 Rounds)", lobby_size, "🏆 Champion"])
        redis_client.set("RG_OFFLINE_PVC_HOF", json.dumps(hof))
    except: pass

def get_leaderboard_data():
    try:
        data = redis_client.get("RG_GLOBAL_LEADERBOARD")
        lb = json.loads(data) if data else []
        return lb[::-1]
    except: return []

def get_offline_hof_data():
    try:
        data = redis_client.get("RG_OFFLINE_HOF")
        return json.loads(data) if data else []
    except: return []

def get_offline_lb_data():
    try:
        data = redis_client.get("RG_OFFLINE_LEADERBOARD")
        return json.loads(data) if data else []
    except: return []

def get_offline_pvc_hof_data():
    try:
        data = redis_client.get("RG_OFFLINE_PVC_HOF")
        hof = json.loads(data) if data else []
        return hof[::-1]
    except: return []

def append_global_match_log(round_num, p1, m1, p2, m2, res):
    try:
        log_str = redis_client.get("RG_GLOBAL_MATCH_LOGS")
        logs = json.loads(log_str) if log_str else []
        logs.insert(0, [round_num, p1, m1, p2, m2, res])
        if len(logs) > 50: logs = logs[:50]
        redis_client.set("RG_GLOBAL_MATCH_LOGS", json.dumps(logs))
    except: pass

def get_global_match_logs():
    try:
        data = redis_client.get("RG_GLOBAL_MATCH_LOGS")
        return json.loads(data) if data else []
    except: return []

def wipe_all_leaderboards():
    try:
        redis_client.delete("RG_GLOBAL_LEADERBOARD")
        redis_client.delete("RG_OFFLINE_HOF")
        redis_client.delete("RG_OFFLINE_LEADERBOARD")
        redis_client.delete("RG_OFFLINE_PVC_HOF")
        redis_client.delete("RG_GLOBAL_MATCH_LOGS")
    except: pass
    return load_all_leaderboards_once()

# ==========================================
# 3. ONLINE ENGINE (BRACKET & LOBBY)
# ==========================================
def generate_bracket(players, shuffle=True):
    p_ids = list(players.keys())
    if shuffle: random.shuffle(p_ids)
    
    n = len(p_ids)
    next_power_of_2 = 2 ** math.ceil(math.log2(n)) if n > 0 else 0
    byes = next_power_of_2 - n
    first_round_players = n - byes
    
    matches = []
    for i in range(0, first_round_players, 2):
        matches.append({"p1": p_ids[i], "p2": p_ids[i+1], "p1_score": 0, "p2_score": 0, "ties": 0,
                        "rounds_played": 0,
                        "p1_move": None, "p2_move": None, "winner": None,
                        "p1_pvc_stats": {"w":0, "l":0, "t":0, "played":0}, 
                        "p2_pvc_stats": {"w":0, "l":0, "t":0, "played":0}})
    for i in range(first_round_players, n):
        matches.append({"p1": p_ids[i], "p2": None, "p1_score": 1, "p2_score": 0, "ties": 0,
                        "rounds_played": 0,
                        "p1_move": None, "p2_move": None, "winner": p_ids[i]})
    return matches

def advance_tournament(room):
    all_done = all(m["winner"] is not None for m in room["bracket"])
    if not all_done: return

    winners = [m["winner"] for m in room["bracket"]]
    if len(winners) == 1:
        room["status"] = "finished"
        room["champion"] = winners[0]
        champ_name = room["players"][winners[0]]["name"]
        update_global_leaderboard(champ_name, room["mode"], len(room["players"]))
        return
    
    new_matches = []
    for i in range(0, len(winners), 2):
        new_matches.append({
            "p1": winners[i], "p2": winners[i+1], "p1_score": 0, "p2_score": 0, "ties": 0,
            "rounds_played": 0,
            "p1_move": None, "p2_move": None, "winner": None,
            "p1_pvc_stats": {"w":0, "l":0, "t":0, "played":0}, 
            "p2_pvc_stats": {"w":0, "l":0, "t":0, "played":0}
        })
    room["bracket"] = new_matches
    room["round"] += 1

def host_room(client_id, player_name, mode, best_of):
    try:
        if not player_name or not str(player_name).strip(): player_name = "Host"
        code = generate_room_code()
        target_score = 2 if best_of == "Best of 3" else 3
        
        room_data = {
            "host": client_id, "mode": mode, "target_score": target_score,
            "status": "lobby", "players": {client_id: {"name": player_name}},
            "bracket": [], "round": 1
        }
        save_room(code, room_data)
        return code, "", gr.update(visible=False), gr.update(visible=True)
    except Exception as e:
        return "", f"❌ DB Error: {str(e)}", gr.update(visible=True), gr.update(visible=False)

def join_room(client_id, player_name, room_code):
    try:
        if not room_code or not str(room_code).strip(): return "", "❌ Enter a code.", gr.update(), gr.update()
        code = str(room_code).upper().strip()
        room = get_room(code)
        
        if not room: return "", "❌ Room not found.", gr.update(), gr.update()
        if room["status"] != "lobby": return "", "❌ Game in progress.", gr.update(), gr.update()
        if room["mode"] == "Casual 1v1" and len(room["players"]) >= 2: return "", "❌ Casual room is full.", gr.update(), gr.update()
        if len(room["players"]) >= 16: return "", "❌ Tournament max capacity.", gr.update(), gr.update()
        
        if not player_name or not str(player_name).strip(): player_name = "Guest"
        room["players"][client_id] = {"name": player_name}
        save_room(code, room)
        return code, f"Joined {code}!", gr.update(visible=False), gr.update(visible=True)
    except Exception as e:
        return "", f"❌ DB Error: {str(e)}", gr.update(), gr.update()

def start_game(client_id, room_code):
    try:
        room = get_room(room_code)
        if not room or room["host"] != client_id or len(room["players"]) < 2: return
        room["status"] = "playing"
        should_shuffle = (room["mode"] != "Casual 1v1")
        room["bracket"] = generate_bracket(room["players"], shuffle=should_shuffle)
        save_room(room_code, room)
    except: pass

def make_move_online(client_id, room_code, move):
    try:
        room = get_room(room_code)
        if not room or room["status"] != "playing": return
        
        match = next((m for m in room["bracket"] if m["winner"] is None and (m["p1"] == client_id or m["p2"] == client_id)), None)
        if not match: return
        
        is_p1 = (match["p1"] == client_id)
        
        if room["mode"] == "PvC Tournament (10 Rounds)":
            stats = match["p1_pvc_stats"] if is_p1 else match["p2_pvc_stats"]
            if stats["played"] < 10:
                cpu_move = random.randint(1, 5)
                res = resolve_matchup(move, cpu_move)
                stats["played"] += 1
                if res == "P1": stats["w"] += 1
                elif res == "P2": stats["l"] += 1
                else: stats["t"] += 1
                
                p_name = room["players"][client_id]["name"]
                
                log_round = f"Stage {room['round']} (Rd {stats['played']})"
                append_global_match_log(log_round, p_name, CHOICES[move], "CPU", CHOICES[cpu_move], res)
                
                if match["p1_pvc_stats"]["played"] == 10 and match["p2_pvc_stats"]["played"] == 10:
                    s1, s2 = match["p1_pvc_stats"], match["p2_pvc_stats"]
                    if s1["w"] > s2["w"]: match["winner"] = match["p1"]
                    elif s2["w"] > s1["w"]: match["winner"] = match["p2"]
                    elif s1["t"] > s2["t"]: match["winner"] = match["p1"]
                    elif s2["t"] > s1["t"]: match["winner"] = match["p2"]
                    else: match["winner"] = random.choice([match["p1"], match["p2"]])
                    advance_tournament(room)
        else:
            if is_p1: match["p1_move"] = move
            else: match["p2_move"] = move
            
            if match["p1_move"] and match["p2_move"]:
                match["rounds_played"] = match.get("rounds_played", 0) + 1
                res = resolve_matchup(match["p1_move"], match["p2_move"])
                p1_name, p2_name = room["players"][match["p1"]]["name"], room["players"][match["p2"]]["name"]
                m1_name, m2_name = CHOICES[match["p1_move"]], CHOICES[match["p2_move"]]
                
                log_round = f"Round {match['rounds_played']}" if room["mode"] == "Casual 1v1" else f"Stage {room['round']} (Rd {match['rounds_played']})"
                append_global_match_log(log_round, p1_name, m1_name, p2_name, m2_name, res)
                
                if res == "P1": match["p1_score"] += 1
                elif res == "P2": match["p2_score"] += 1
                else: match["ties"] = match.get("ties", 0) + 1
                    
                match["p1_move"], match["p2_move"] = None, None
                if match["p1_score"] == room["target_score"]: match["winner"] = match["p1"]
                elif match["p2_score"] == room["target_score"]: match["winner"] = match["p2"]
                
                if match["winner"]: advance_tournament(room)
        save_room(room_code, room)
    except: pass

def leave_online_room():
    return "", gr.update(visible=True), gr.update(visible=False)

# --- STRICT PANDAS DATAFRAMES ---
df_empty_bracket = pd.DataFrame(columns=["Player 1", "Score/Stats", " ", "Player 2", "Score/Stats", "Status"])
df_empty_history = pd.DataFrame(columns=["Round", "Player 1", "Move 1", "Player 2", "Move 2", "Result"])
df_empty_lb = pd.DataFrame(columns=["Champion Name", "Tournament Mode", "Lobby Size", "Status"])
df_empty_off_hof = pd.DataFrame(columns=["Player Name", "Achievement", "Final Record"])
df_empty_off_lb = pd.DataFrame(columns=["Player Name", "Wins", "Losses", "Ties", "Win Rate"])
df_empty_off_pvc_hof = pd.DataFrame(columns=["Champion Name", "Tournament Mode", "Lobby Size", "Status"])

def load_all_leaderboards_once():
    lb_data = get_leaderboard_data()
    df_lb = pd.DataFrame(lb_data, columns=["Champion Name", "Tournament Mode", "Lobby Size", "Status"]) if lb_data else df_empty_lb
    
    hof_data = get_offline_hof_data()
    df_hof = pd.DataFrame(hof_data, columns=["Player Name", "Achievement", "Final Record"]) if hof_data else df_empty_off_hof
    
    off_lb_data = get_offline_lb_data()
    df_off_lb = pd.DataFrame(off_lb_data, columns=["Player Name", "Wins", "Losses", "Ties", "Win Rate"]) if off_lb_data else df_empty_off_lb

    off_pvc_data = get_offline_pvc_hof_data()
    df_off_pvc = pd.DataFrame(off_pvc_data, columns=["Champion Name", "Tournament Mode", "Lobby Size", "Status"]) if off_pvc_data else df_empty_off_pvc_hof
    
    return df_lb, df_hof, df_off_lb, df_off_pvc

def sync_ui(client_id, room_code):
    if not room_code: return "<h3>Welcome to the Arena</h3>", "Waiting...", df_empty_bracket, df_empty_history, gr.update(visible=False)
    
    room = get_room(room_code)
    if not room: return "<h3>Room closed or expired</h3>", "Waiting...", df_empty_bracket, df_empty_history, gr.update(visible=False)
    
    logs_data = get_global_match_logs()
    df_logs = pd.DataFrame(logs_data, columns=["Round", "Player 1", "Move 1", "Player 2", "Move 2", "Result"]) if logs_data else df_empty_history
    
    if room["status"] == "lobby":
        players_list = ", ".join([p["name"] for p in room["players"].values()])
        html = f"<h3>Room: {room_code} | Mode: {room['mode']}</h3><p>Players ({len(room['players'])}): {players_list}</p>"
        if room["host"] == client_id: html += "<p><em>You are host. Click Start when ready!</em></p>"
        return html, "Waiting in lobby...", df_empty_bracket, df_logs, gr.update(visible=False)
        
    if room["status"] == "finished":
        champ_name = room["players"][room["champion"]]["name"]
        export_btn_state = gr.update(visible=True) if room["host"] == client_id else gr.update(visible=False)
        return f"<h2>🏆 TOURNAMENT OVER!</h2><h3>Champion: {champ_name}</h3>", "Game Over", df_empty_bracket, df_logs, export_btn_state

    html = f"<h3>🕹️ Live Round: {room['round']} | {room['mode']}</h3>"
    status = "Waiting..."
    bracket_data = []
    my_match = None

    for m in room["bracket"]:
        p1n = room["players"][m["p1"]]["name"]
        p2n = room["players"][m["p2"]]["name"] if m["p2"] else "BYE"
        m_stat = "Finished" if m["winner"] else "Playing"
        
        if room["mode"] == "PvC Tournament (10 Rounds)":
            p1_inf = f"{m['p1_pvc_stats']['w']}W-{m['p1_pvc_stats']['l']}L-{m['p1_pvc_stats']['t']}T (Rd {m['p1_pvc_stats']['played']}/10)"
            p2_inf = f"{m['p2_pvc_stats']['w']}W-{m['p2_pvc_stats']['l']}L-{m['p2_pvc_stats']['t']}T (Rd {m['p2_pvc_stats']['played']}/10)" if m["p2"] else "-"
            bracket_data.append([p1n, p1_inf, "vs", p2n, p2_inf, m_stat])
            
            if not m["winner"] and (m["p1"] == client_id or m["p2"] == client_id):
                my_match = m
                is_p1 = (m["p1"] == client_id)
                played = m['p1_pvc_stats']['played'] if is_p1 else m['p2_pvc_stats']['played']
                if played < 10: status = f"🎮 Your Turn: Play CPU! (Round {played+1}/10)"
                else: status = "Finished your 10 rounds. Waiting for opponent..."
        else:
            ties_count = m.get("ties", 0)
            p1_inf = f"{m['p1_score']}W-{m['p2_score']}L-{ties_count}T" if m["p2"] else "1W-0L-0T (BYE)"
            p2_inf = f"{m['p2_score']}W-{m['p1_score']}L-{ties_count}T" if m["p2"] else "-"
            bracket_data.append([p1n, p1_inf, "vs", p2n, p2_inf, m_stat])
            
            if not m["winner"] and (m["p1"] == client_id or m["p2"] == client_id):
                my_match = m
                is_p1 = (m["p1"] == client_id)
                my_move = m["p1_move"] if is_p1 else m["p2_move"]
                if my_move is None: status = "🟢 YOUR TURN - Make a move!"
                else: status = "⏳ Waiting for opponent to move..."

    if not my_match: status = "You have a Bye, or you were eliminated. Spectating..."
    df_bracket = pd.DataFrame(bracket_data, columns=["Player 1", "Score/Stats", " ", "Player 2", "Score/Stats", "Status"]) if bracket_data else df_empty_bracket
    return html, status, df_bracket, df_logs, gr.update(visible=False)

# ==========================================
# 4. OFFLINE LOGIC (Endless, 10-Rd vs Comp, PvC Offline Tournament)
# ==========================================
def format_offline_bracket(pvc_state):
    if not pvc_state or not pvc_state.get("bracket"):
        return df_empty_bracket
    rows = []
    for m in pvc_state["bracket"]:
        p1n = m["p1"]
        p2n = m["p2"] if m["p2"] else "BYE"
        m_stat = f"Winner: {m['winner']}" if m["winner"] else "Playing"
        p1_inf = f"{m['p1_stats']['w']}W-{m['p1_stats']['l']}L-{m['p1_stats']['t']}T (Rd {m['p1_stats']['played']}/10)"
        p2_inf = f"{m['p2_stats']['w']}W-{m['p2_stats']['l']}L-{m['p2_stats']['t']}T (Rd {m['p2_stats']['played']}/10)" if m["p2"] else "-"
        rows.append([p1n, p1_inf, "vs", p2n, p2_inf, m_stat])
    return pd.DataFrame(rows, columns=["Player 1", "Score/Stats", " ", "Player 2", "Score/Stats", "Status"])

def get_next_offline_pvc_prompt(pvc_state):
    if not pvc_state or not pvc_state.get("active"):
        return "Tournament Inactive"
    for m in pvc_state["bracket"]:
        if m["winner"] is None:
            if m["p1_stats"]["played"] < 10:
                return f"🕹️ Stage {pvc_state['stage']} | 🎮 {m['p1']}'s Turn vs CPU (Round {m['p1_stats']['played'] + 1}/10)"
            elif m["p2"] and m["p2_stats"]["played"] < 10:
                return f"🕹️ Stage {pvc_state['stage']} | 🎮 {m['p2']}'s Turn vs CPU (Round {m['p2_stats']['played'] + 1}/10)"
    return "Tournament Completed"

def toggle_offline_mode(mode):
    if mode == "Endless Practice":
        return gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), "Endless Practice Active"
    elif mode == "10-Round vs Comp":
        return gr.update(visible=True, value="⚔️ Start 10-Round vs Comp"), gr.update(visible=False), gr.update(visible=False), "Click 'Start 10-Round vs Comp' to begin!"
    else:
        return gr.update(visible=True, value="🏆 Start PvC Offline Tournament"), gr.update(visible=True), gr.update(visible=True), "Enter 3-16 players and click Start!"

def start_offline_mode(mode, player_name, participants_str):
    if not player_name or not str(player_name).strip(): player_name = "Player 1"
    
    if mode == "10-Round vs Comp":
        return True, 1, 0, 0, 0, {"active": False}, f"⚔️ 10-Round vs Comp Started! Round 1/10 | {player_name}: 0 | CPU: 0 | Ties: 0", df_empty_bracket
    
    elif mode == "PvC Offline Tournament (10 Rounds)":
        raw_names = [x.strip() for x in str(participants_str).split(",") if x.strip()]
        if player_name not in raw_names:
            raw_names.insert(0, player_name)
        
        seen = set()
        players_list = []
        for n in raw_names:
            if n not in seen:
                seen.add(n)
                players_list.append(n)
                
        while len(players_list) < 3:
            bot_name = f"Challenger {len(players_list)}"
            if bot_name not in seen:
                seen.add(bot_name)
                players_list.append(bot_name)
        if len(players_list) > 16:
            players_list = players_list[:16]
            
        lobby_size = len(players_list)
        random.shuffle(players_list)
        
        n = len(players_list)
        next_pow2 = 2 ** math.ceil(math.log2(n))
        byes = next_pow2 - n
        first_round_count = n - byes
        
        matches = []
        for i in range(0, first_round_count, 2):
            matches.append({
                "p1": players_list[i], "p2": players_list[i+1],
                "p1_stats": {"w": 0, "l": 0, "t": 0, "played": 0},
                "p2_stats": {"w": 0, "l": 0, "t": 0, "played": 0},
                "winner": None
            })
        for i in range(first_round_count, n):
            matches.append({
                "p1": players_list[i], "p2": None,
                "p1_stats": {"w": 0, "l": 0, "t": 0, "played": 0},
                "p2_stats": {"w": 0, "l": 0, "t": 0, "played": 0},
                "winner": players_list[i]
            })
            
        pvc_state = {
            "active": True,
            "stage": 1,
            "lobby_size": lobby_size,
            "bracket": matches,
            "champion": None
        }
        status = get_next_offline_pvc_prompt(pvc_state)
        return False, 1, 0, 0, 0, pvc_state, status, format_offline_bracket(pvc_state)
        
    return False, 1, 0, 0, 0, {"active": False}, "Endless Practice Active", df_empty_bracket

def play_offline_round(move, off_mode, n_wins, n_losses, n_ties, match_log, player_name, t_active, t_round, t_p_wins, t_c_wins, t_ties, pvc_state):
    if not player_name or not str(player_name).strip(): player_name = "Player 1"
    
    # 1. PvC Offline Tournament (10 Rounds)
    if off_mode == "PvC Offline Tournament (10 Rounds)":
        if not pvc_state or not pvc_state.get("active"):
            df_off = pd.DataFrame(match_log, columns=["Round", "Player 1", "Move 1", "Player 2", "Move 2", "Result"]) if match_log else df_empty_history
            return "⚠️ Click 'Start PvC Offline Tournament' first!", f"🏆 Wins: {n_wins} | 🤖 Losses: {n_losses} | 🤝 Ties: {n_ties}", df_off, n_wins, n_losses, n_ties, t_active, t_round, t_p_wins, t_c_wins, t_ties, pvc_state, "Click Start above to begin!", format_offline_bracket(pvc_state)
        
        active_match = None
        is_p1 = True
        for m in pvc_state["bracket"]:
            if m["winner"] is None:
                active_match = m
                if m["p1_stats"]["played"] < 10:
                    is_p1 = True
                else:
                    is_p1 = False
                break
                
        if not active_match:
            df_off = pd.DataFrame(match_log, columns=["Round", "Player 1", "Move 1", "Player 2", "Move 2", "Result"]) if match_log else df_empty_history
            return "Tournament Over!", f"🏆 Wins: {n_wins} | 🤖 Losses: {n_losses} | 🤝 Ties: {n_ties}", df_off, n_wins, n_losses, n_ties, t_active, t_round, t_p_wins, t_c_wins, t_ties, pvc_state, "Tournament Over", format_offline_bracket(pvc_state)

        curr_player = active_match["p1"] if is_p1 else active_match["p2"]
        stats = active_match["p1_stats"] if is_p1 else active_match["p2_stats"]
        
        cpu_move = random.randint(1, 5)
        m_name, c_name = CHOICES[move], CHOICES[cpu_move]
        res = resolve_matchup(move, cpu_move)
        
        stats["played"] += 1
        if res == "P1": stats["w"] += 1
        elif res == "P2": stats["l"] += 1
        else: stats["t"] += 1
        
        log_round = f"Stage {pvc_state['stage']} (Rd {stats['played']})"
        match_log.insert(0, [log_round, curr_player, m_name, "CPU", c_name, res])
        df_off = pd.DataFrame(match_log, columns=["Round", "Player 1", "Move 1", "Player 2", "Move 2", "Result"])
        
        msg = f"[{curr_player}] {'🎉 Wins!' if res == 'P1' else '😢 CPU Wins!' if res == 'P2' else '🤝 Tie!'} ({m_name} vs {c_name})"
        
        if active_match["p1_stats"]["played"] == 10 and active_match["p2_stats"]["played"] == 10:
            s1, s2 = active_match["p1_stats"], active_match["p2_stats"]
            if s1["w"] > s2["w"]: active_match["winner"] = active_match["p1"]
            elif s2["w"] > s1["w"]: active_match["winner"] = active_match["p2"]
            elif s1["t"] > s2["t"]: active_match["winner"] = active_match["p1"]
            elif s2["t"] > s1["t"]: active_match["winner"] = active_match["p2"]
            else: active_match["winner"] = random.choice([active_match["p1"], active_match["p2"]])
            
            if all(m["winner"] is not None for m in pvc_state["bracket"]):
                winners = [m["winner"] for m in pvc_state["bracket"]]
                if len(winners) == 1:
                    pvc_state["active"] = False
                    pvc_state["champion"] = winners[0]
                    update_offline_pvc_hof(winners[0], pvc_state["lobby_size"])
                    status = f"🏆 OFFLINE TOURNAMENT OVER! Champion: {winners[0]}!"
                else:
                    new_matches = []
                    for i in range(0, len(winners), 2):
                        new_matches.append({
                            "p1": winners[i], "p2": winners[i+1],
                            "p1_stats": {"w": 0, "l": 0, "t": 0, "played": 0},
                            "p2_stats": {"w": 0, "l": 0, "t": 0, "played": 0},
                            "winner": None
                        })
                    pvc_state["bracket"] = new_matches
                    pvc_state["stage"] += 1
                    status = get_next_offline_pvc_prompt(pvc_state)
            else:
                status = get_next_offline_pvc_prompt(pvc_state)
        else:
            status = get_next_offline_pvc_prompt(pvc_state)
            
        stats_str = f"🏆 Wins: {n_wins} | 🤖 Losses: {n_losses} | 🤝 Ties: {n_ties}"
        return msg, stats_str, df_off, n_wins, n_losses, n_ties, t_active, t_round, t_p_wins, t_c_wins, t_ties, pvc_state, status, format_offline_bracket(pvc_state)

    # 2. 10-Round vs Comp
    elif off_mode == "10-Round vs Comp":
        if not t_active:
            df_off = pd.DataFrame(match_log, columns=["Round", "Player 1", "Move 1", "Player 2", "Move 2", "Result"]) if match_log else df_empty_history
            return "⚠️ Click 'Start 10-Round vs Comp' first!", f"🏆 Wins: {n_wins} | 🤖 Losses: {n_losses} | 🤝 Ties: {n_ties}", df_off, n_wins, n_losses, n_ties, t_active, t_round, t_p_wins, t_c_wins, t_ties, pvc_state, "Click 'Start 10-Round vs Comp' to begin!", df_empty_bracket

        cpu_move = random.randint(1, 5)
        m_name, c_name = CHOICES[move], CHOICES[cpu_move]
        res = resolve_matchup(move, cpu_move)
        
        log_round = f"10-Rd (Rd {t_round})"
        match_log.insert(0, [log_round, player_name, m_name, "CPU", c_name, res])
        df_off = pd.DataFrame(match_log, columns=["Round", "Player 1", "Move 1", "Player 2", "Move 2", "Result"])

        if res == "P1": t_p_wins += 1
        elif res == "P2": t_c_wins += 1
        else: t_ties += 1

        if t_round < 10:
            status = f"⚔️ Round {t_round + 1}/10 | {player_name}: {t_p_wins} | CPU: {t_c_wins} | Ties: {t_ties}"
            t_round += 1
        else:
            if t_p_wins > t_c_wins: status = f"🏆 10-ROUND VS COMP OVER! {player_name} WINS {t_p_wins}W-{t_c_wins}L-{t_ties}T!"
            elif t_c_wins > t_p_wins: status = f"💀 10-ROUND VS COMP OVER! CPU WINS {t_c_wins}W-{t_p_wins}L-{t_ties}T!"
            else: status = f"⚖️ 10-ROUND VS COMP OVER! IT'S A DRAW {t_p_wins}W-{t_c_wins}L-{t_ties}T!"
            
            update_offline_leaderboards(player_name, t_p_wins, t_c_wins, t_ties)
            t_active = False

        msg = f"{'🎉 You Win!' if res == 'P1' else '😢 CPU Wins!' if res == 'P2' else '🤝 Tie!'} {m_name} vs {c_name}"
        stats_str = f"🏆 Wins: {n_wins} | 🤖 Losses: {n_losses} | 🤝 Ties: {n_ties}"
        return msg, stats_str, df_off, n_wins, n_losses, n_ties, t_active, t_round, t_p_wins, t_c_wins, t_ties, pvc_state, status, df_empty_bracket

    # 3. Endless Practice
    else:
        cpu_move = random.randint(1, 5)
        m_name, c_name = CHOICES[move], CHOICES[cpu_move]
        res = resolve_matchup(move, cpu_move)
        
        if res == "P1": n_wins += 1
        elif res == "P2": n_losses += 1
        else: n_ties += 1
        
        total_endless = n_wins + n_losses + n_ties
        log_round = f"Round {total_endless}"
        match_log.insert(0, [log_round, player_name, m_name, "CPU", c_name, res])
        df_off = pd.DataFrame(match_log, columns=["Round", "Player 1", "Move 1", "Player 2", "Move 2", "Result"])
        
        msg = f"{'🎉 You Win!' if res == 'P1' else '😢 CPU Wins!' if res == 'P2' else '🤝 Tie!'} {m_name} vs {c_name}"
        stats_str = f"🏆 Wins: {n_wins} | 🤖 Losses: {n_losses} | 🤝 Ties: {n_ties}"
        return msg, stats_str, df_off, n_wins, n_losses, n_ties, t_active, t_round, t_p_wins, t_c_wins, t_ties, pvc_state, "Endless Practice Active", df_empty_bracket

def reset_offline():
    return "Practice Mode Reset.", "🏆 Wins: 0 | 🤖 Losses: 0 | 🤝 Ties: 0", df_empty_history, 0, 0, 0, [], False, 1, 0, 0, 0, {"active": False}, "Endless Practice Active", df_empty_bracket

def hard_reset_all():
    return (
        "", gr.update(visible=True), gr.update(visible=False), 
        "App Fully Reset.", "🏆 Wins: 0 | 🤖 Losses: 0 | 🤝 Ties: 0", df_empty_history, 0, 0, 0, [], 
        False, 1, 0, 0, 0, {"active": False}, "Endless Practice Active", df_empty_bracket
    )

# ==========================================
# 5. UNIVERSAL EXCEL EXPORT
# ==========================================
def export_all_data(offline_log):
    temp_dir = tempfile.gettempdir()
    file_path = os.path.join(temp_dir, f"RPSLS_Arcade_Data.xlsx")
    
    with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
        df_off = pd.DataFrame(offline_log, columns=["Round", "Player 1", "Move 1", "Player 2", "Move 2", "Result"]) if offline_log else df_empty_history
        df_off.to_excel(writer, sheet_name="Offline Round History", index=False)
        
        lb_data = get_leaderboard_data()
        df_lb = pd.DataFrame(lb_data, columns=["Champion Name", "Tournament Mode", "Lobby Size", "Status"]) if lb_data else df_empty_lb
        df_lb.to_excel(writer, sheet_name="Online Hall of Fame", index=False)
        
        hof_data = get_offline_hof_data()
        df_hof = pd.DataFrame(hof_data, columns=["Player Name", "Achievement", "Final Record"]) if hof_data else df_empty_off_hof
        df_hof.to_excel(writer, sheet_name="Offline Perfect 10 HOF", index=False)
        
        off_lb_data = get_offline_lb_data()
        df_off_lb = pd.DataFrame(off_lb_data, columns=["Player Name", "Wins", "Losses", "Ties", "Win Rate"]) if off_lb_data else df_empty_off_lb
        df_off_lb.to_excel(writer, sheet_name="10-Round vs Comp LB", index=False)

        off_pvc_data = get_offline_pvc_hof_data()
        df_off_pvc = pd.DataFrame(off_pvc_data, columns=["Champion Name", "Tournament Mode", "Lobby Size", "Status"]) if off_pvc_data else df_empty_off_pvc_hof
        df_off_pvc.to_excel(writer, sheet_name="Offline Hall of Fame", index=False)

        logs_data = get_global_match_logs()
        df_on = pd.DataFrame(logs_data, columns=["Round", "Player 1", "Move 1", "Player 2", "Move 2", "Result"]) if logs_data else df_empty_history
        df_on.to_excel(writer, sheet_name="Global Online Match History", index=False)
    return file_path

# ==========================================
# 6. UI HELPER FUNCTION (DYNAMIC MATCH LENGTH)
# ==========================================
def toggle_match_length(mode):
    if mode == "PvC Tournament (10 Rounds)": return gr.update(visible=False)
    return gr.update(visible=True)

# ==========================================
# 7. GRADIO UI LAYOUT (Flat Page Architecture)
# ==========================================
with gr.Blocks(theme=gr.themes.Soft(primary_hue="indigo")) as demo:
    client_id = gr.State(lambda: str(uuid.uuid4()))
    active_room = gr.State("")
    
    off_w, off_c, off_t, off_log = gr.State(0), gr.State(0), gr.State(0), gr.State([])
    off_t_active, off_t_round = gr.State(False), gr.State(1)
    off_t_p, off_t_c, off_t_ties = gr.State(0), gr.State(0), gr.State(0)
    off_pvc_state = gr.State({"active": False})

    with gr.Row():
        gr.Markdown("# 🕹️ RPSLS Arcade Arena")
        btn_theme = gr.Button("🌓 Toggle Dark Mode", variant="secondary")
        btn_hard_reset = gr.Button("🛑 HARD RESET APP", variant="stop")
        
    gr.Markdown("### Welcome! Enter your name below to play Online or Offline.")

    with gr.Accordion("📜 Game Rules & Modes Explained (Click to Expand)", open=False):
        gr.Markdown("""
        **THE RULES OF COMBAT:**
        * ✂️ **Scissors** cuts **Paper** and decapitates **Lizard**
        * 📄 **Paper** covers **Rock** and disproves **Spock**
        * 🪨 **Rock** crushes **Scissors** and crushes **Lizard**
        * 🦎 **Lizard** poisons **Spock** and eats **Paper**
        * 🖖 **Spock** smashes **Scissors** and vaporizes **Rock**
        
        **GAME MODES:**
        * **Casual 1v1:** Play a standard Best-of-3 or Best-of-5 match against a friend online.
        * **PvP Tournament:** A bracket-style elimination tournament for 3 to 16 players. Players without an opponent get an automatic "Bye" to the next round.
        * **PvC Tournament (Online & Offline):** Compete in a 3-16 player bracket by playing 10 rounds against the CPU! Whoever beats the CPU more across 10 rounds advances to the next stage (ties break deadlocks).
        * **10-Round vs Comp (Offline):** Play a 10-round solo match against the CPU. Score 10/10 to reach the Perfect 10 Hall of Fame, or rank on the 10-Round vs Comp Leaderboard!
        """)

    my_name = gr.Textbox(label="Player Name", value="Player 1")

    gr.Markdown("---")
    gr.Markdown("## 🌐 ONLINE MULTIPLAYER")
    with gr.Column(visible=True) as screen_setup:
        with gr.Row():
            with gr.Group():
                gr.Markdown("#### 🕹️ HOST GAME")
                host_mode = gr.Dropdown(["Casual 1v1", "PvP Tournament (3-16)", "PvC Tournament (10 Rounds)"], value="Casual 1v1", label="Mode Select")
                host_bo = gr.Radio(["Best of 3", "Best of 5"], value="Best of 3", label="Match Length")
                btn_host = gr.Button("CREATE ROOM", variant="primary")
            with gr.Group():
                gr.Markdown("#### 🔌 JOIN GAME")
                join_code = gr.Textbox(label="Enter Room Code")
                btn_join = gr.Button("JOIN ROOM")
        sys_msg = gr.Markdown("")

    with gr.Column(visible=False) as screen_game:
        top_banner = gr.HTML("Loading Arcade Data...")
        with gr.Row():
            btn_start = gr.Button("🚀 START GAME (Host Only)", variant="primary")
            btn_leave = gr.Button("🚪 Leave / Reset Online Room", variant="stop")
        
        with gr.Row():
            with gr.Column(scale=1):
                match_status = gr.Textbox(label="Action Required", interactive=False)
                with gr.Row():
                    btn_r, btn_p, btn_s, btn_l, btn_k = [gr.Button("🪨"), gr.Button("📄"), gr.Button("✂️"), gr.Button("🦎"), gr.Button("🖖")]
            with gr.Column(scale=2):
                gr.Markdown("### 📡 Live Match Bracket")
                bracket_view = gr.Dataframe(headers=["Player 1", "Score/Stats", " ", "Player 2", "Score/Stats", "Status"], interactive=False)
        
        gr.Markdown("### 🕒 Online Match History Tracking")
        history_view = gr.Dataframe(headers=["Round", "Player 1", "Move 1", "Player 2", "Move 2", "Result"], interactive=False)

    gr.Markdown("---")
    gr.Markdown("## 🤖 OFFLINE PRACTICE")
    with gr.Row():
        off_mode_select = gr.Dropdown(
            ["Endless Practice", "10-Round vs Comp", "PvC Offline Tournament (10 Rounds)"],
            value="Endless Practice",
            label="Mode Select"
        )
        off_participants = gr.Textbox(
            label="Tournament Players (Comma-separated, 3-16 players)",
            value="Player 1, Challenger 1, Challenger 2, Challenger 3",
            visible=False
        )
    
    with gr.Row():
        btn_start_off_mode = gr.Button("⚔️ Start Mode", variant="primary", visible=False)
        btn_reset_off = gr.Button("🔄 Reset Offline Data", variant="secondary")
    
    off_tourney_status = gr.Textbox(label="Mode / Tournament Status", value="Endless Practice Active", interactive=False)
    
    with gr.Column(visible=False) as off_bracket_col:
        gr.Markdown("### 📡 Live Offline Match Bracket")
        off_bracket_view = gr.Dataframe(headers=["Player 1", "Score/Stats", " ", "Player 2", "Score/Stats", "Status"], interactive=False)

    with gr.Row():
        with gr.Column():
            with gr.Row():
                off_r, off_p, off_s, off_l, off_k = [gr.Button("🪨"), gr.Button("📄"), gr.Button("✂️"), gr.Button("🦎"), gr.Button("🖖")]
        with gr.Column():
            off_msg = gr.Textbox(label="Match Result", interactive=False)
            off_stats = gr.Textbox(label="Endless Session Analytics", value="🏆 Wins: 0 | 🤖 Losses: 0 | 🤝 Ties: 0", interactive=False)
            
    gr.Markdown("### 🕒 Offline Round History Tracking")
    off_history_view = gr.Dataframe(headers=["Round", "Player 1", "Move 1", "Player 2", "Move 2", "Result"], interactive=False)

    gr.Markdown("---")
    gr.Markdown("## 🏆 HALL OF FAME & LEADERBOARDS")
    
    with gr.Row():
        btn_refresh_lb = gr.Button("🔄 Refresh All Leaderboards", variant="primary")
        btn_wipe_lb = gr.Button("🗑️ Wipe Global Leaderboards", variant="stop")
    
    with gr.Accordion("🌐 Online Global Hall of Fame (All Modes)", open=True):
        lb_view_setup = gr.Dataframe(headers=["Champion Name", "Tournament Mode", "Lobby Size", "Status"], interactive=False)
        
    with gr.Accordion("👑 Offline Perfect 10 Hall of Fame", open=False):
        off_hof_view = gr.Dataframe(headers=["Player Name", "Achievement", "Final Record"], interactive=False)
        
    with gr.Accordion("📊 10-Round vs Comp Leaderboard", open=False):
        off_lb_view = gr.Dataframe(headers=["Player Name", "Wins", "Losses", "Ties", "Win Rate"], interactive=False)

    with gr.Accordion("🏅 Offline Hall of Fame (PvC Offline Tournament)", open=False):
        off_pvc_hof_view = gr.Dataframe(headers=["Champion Name", "Tournament Mode", "Lobby Size", "Status"], interactive=False)
    
    gr.Markdown("Export all Offline Practice stats, Global Leaderboards, and completed Online Tournament logs to Excel.")
    btn_export_all = gr.Button("💾 Generate Excel Spreadsheets", variant="secondary")
    download_file = gr.File(label="Download Excel Document", interactive=False, visible=False)

    # --- EVENT TRIGGERS ---
    timer = gr.Timer(value=1)
    btn_theme.click(fn=None, js="() => { document.documentElement.classList.toggle('dark'); }")
    demo.load(load_all_leaderboards_once, inputs=[], outputs=[lb_view_setup, off_hof_view, off_lb_view, off_pvc_hof_view])
    
    host_mode.change(toggle_match_length, inputs=[host_mode], outputs=[host_bo])
    off_mode_select.change(toggle_offline_mode, inputs=[off_mode_select], outputs=[btn_start_off_mode, off_participants, off_bracket_col, off_tourney_status])
    
    btn_refresh_lb.click(load_all_leaderboards_once, inputs=[], outputs=[lb_view_setup, off_hof_view, off_lb_view, off_pvc_hof_view])
    btn_wipe_lb.click(wipe_all_leaderboards, inputs=[], outputs=[lb_view_setup, off_hof_view, off_lb_view, off_pvc_hof_view])
    
    # Online
    btn_host.click(host_room, inputs=[client_id, my_name, host_mode, host_bo], outputs=[active_room, sys_msg, screen_setup, screen_game])
    btn_join.click(join_room, inputs=[client_id, my_name, join_code], outputs=[active_room, sys_msg, screen_setup, screen_game])
    btn_start.click(start_game, inputs=[client_id, active_room], outputs=[])
    btn_leave.click(leave_online_room, inputs=[], outputs=[active_room, screen_setup, screen_game])
    timer.tick(sync_ui, inputs=[client_id, active_room], outputs=[top_banner, match_status, bracket_view, history_view])
    
    for btn, val in [(btn_r, 1), (btn_p, 2), (btn_s, 3), (btn_l, 4), (btn_k, 5)]:
        btn.click(make_move_online, inputs=[client_id, active_room, gr.State(val)], outputs=[])

    # Offline 
    btn_start_off_mode.click(
        start_offline_mode,
        inputs=[off_mode_select, my_name, off_participants],
        outputs=[off_t_active, off_t_round, off_t_p, off_t_c, off_t_ties, off_pvc_state, off_tourney_status, off_bracket_view]
    )
    for btn, val in [(off_r, 1), (off_p, 2), (off_s, 3), (off_l, 4), (off_k, 5)]:
        btn.click(
            play_offline_round, 
            inputs=[gr.State(val), off_mode_select, off_w, off_c, off_t, off_log, my_name, off_t_active, off_t_round, off_t_p, off_t_c, off_t_ties, off_pvc_state], 
            outputs=[off_msg, off_stats, off_history_view, off_w, off_c, off_t, off_t_active, off_t_round, off_t_p, off_t_c, off_t_ties, off_pvc_state, off_tourney_status, off_bracket_view]
        ).then(
            load_all_leaderboards_once, inputs=[], outputs=[lb_view_setup, off_hof_view, off_lb_view, off_pvc_hof_view]
        )
    btn_reset_off.click(
        reset_offline,
        inputs=[],
        outputs=[off_msg, off_stats, off_history_view, off_w, off_c, off_t, off_log, off_t_active, off_t_round, off_t_p, off_t_c, off_t_ties, off_pvc_state, off_tourney_status, off_bracket_view]
    )

    # Resets & Exports
    btn_hard_reset.click(
        hard_reset_all,
        inputs=[],
        outputs=[active_room, screen_setup, screen_game, off_msg, off_stats, off_history_view, off_w, off_c, off_t, off_log, off_t_active, off_t_round, off_t_p, off_t_c, off_t_ties, off_pvc_state, off_tourney_status, off_bracket_view]
    )
    
    btn_export_all.click(export_all_data, inputs=[off_log], outputs=[download_file]).then(lambda: gr.update(visible=True), outputs=[download_file])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    demo.launch(server_name="0.0.0.0", server_port=port)