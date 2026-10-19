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

def wipe_all_leaderboards():
    try:
        redis_client.delete("RG_GLOBAL_LEADERBOARD")
        redis_client.delete("RG_OFFLINE_HOF")
        redis_client.delete("RG_OFFLINE_LEADERBOARD")
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
        matches.append({"p1": p_ids[i], "p2": p_ids[i+1], "p1_score": 0, "p2_score": 0, 
                        "p1_move": None, "p2_move": None, "winner": None,
                        "p1_pvc_stats": {"w":0, "l":0, "t":0, "played":0}, 
                        "p2_pvc_stats": {"w":0, "l":0, "t":0, "played":0}})
    for i in range(first_round_players, n):
        matches.append({"p1": p_ids[i], "p2": None, "p1_score": 1, "p2_score": 0, 
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
            "p1": winners[i], "p2": winners[i+1], "p1_score": 0, "p2_score": 0, 
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
            "bracket": [], "round": 1, "all_match_logs": [] 
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
                room["all_match_logs"].insert(0, [room["round"], p_name, CHOICES[move], "CPU", CHOICES[cpu_move], res])
                
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
                res = resolve_matchup(match["p1_move"], match["p2_move"])
                p1_name, p2_name = room["players"][match["p1"]]["name"], room["players"][match["p2"]]["name"]
                m1_name, m2_name = CHOICES[match["p1_move"]], CHOICES[match["p2_move"]]
                
                room["all_match_logs"].insert(0, [room["round"], p1_name, m1_name, p2_name, m2_name, res])
                if res == "P1": match["p1_score"] += 1
                elif res == "P2": match["p2_score"] += 1
                    
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
df_empty_off = pd.DataFrame(columns=["Round ID", "Player Move", "CPU Move", "Winner"])
df_empty_off_hof = pd.DataFrame(columns=["Player Name", "Achievement", "Final Record"])
df_empty_off_lb = pd.DataFrame(columns=["Player Name", "Wins", "Losses", "Ties", "Win Rate"])

def load_all_leaderboards_once():
    lb_data = get_leaderboard_data()
    df_lb = pd.DataFrame(lb_data, columns=["Champion Name", "Tournament Mode", "Lobby Size", "Status"]) if lb_data else df_empty_lb
    
    hof_data = get_offline_hof_data()
    df_hof = pd.DataFrame(hof_data, columns=["Player Name", "Achievement", "Final Record"]) if hof_data else df_empty_off_hof
    
    off_lb_data = get_offline_lb_data()
    df_off_lb = pd.DataFrame(off_lb_data, columns=["Player Name", "Wins", "Losses", "Ties", "Win Rate"]) if off_lb_data else df_empty_off_lb
    
    return df_lb, df_hof, df_off_lb

def sync_ui(client_id, room_code):
    if not room_code: return "<h3>Welcome to the Arena</h3>", "Waiting...", df_empty_bracket, df_empty_history, gr.update(visible=False)
    
    room = get_room(room_code)
    if not room: return "<h3>Room closed or expired</h3>", "Waiting...", df_empty_bracket, df_empty_history, gr.update(visible=False)
    
    logs_data = room.get("all_match_logs", [])
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
            p1_inf = f"{m['p1_pvc_stats']['w']}W-{m['p1_pvc_stats']['l']}L (Round {m['p1_pvc_stats']['played']}/10)"
            p2_inf = f"{m['p2_pvc_stats']['w']}W-{m['p2_pvc_stats']['l']}L (Round {m['p2_pvc_stats']['played']}/10)" if m["p2"] else "-"
            bracket_data.append([p1n, p1_inf, "vs", p2n, p2_inf, m_stat])
            
            if not m["winner"] and (m["p1"] == client_id or m["p2"] == client_id):
                my_match = m
                is_p1 = (m["p1"] == client_id)
                played = m['p1_pvc_stats']['played'] if is_p1 else m['p2_pvc_stats']['played']
                if played < 10: status = f"🎮 Your Turn: Play CPU! (Round {played+1}/10)"
                else: status = "Finished your 10 rounds. Waiting for opponent..."
        else:
            bracket_data.append([p1n, m["p1_score"], "vs", p2n, m["p2_score"] if m["p2"] else "-", m_stat])
            
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
# 4. RESTORED OFFLINE LOGIC (Practice + Tournament)
# ==========================================
def start_offline_tournament(player_name):
    if not player_name or not str(player_name).strip(): player_name = "Player 1"
    return True, 1, 0, 0, 0, f"⚔️ Tournament Started! Round 1/10 | {player_name}: 0 | CPU: 0 | Ties: 0"

def play_offline_round(move, n_wins, n_losses, n_ties, match_log, player_name, t_active, t_round, t_p_wins, t_c_wins, t_ties):
    if not player_name or not str(player_name).strip(): player_name = "Player 1"
    cpu_move = random.randint(1, 5)
    m_name, c_name = CHOICES[move], CHOICES[cpu_move]
    
    res = resolve_matchup(move, cpu_move)
    winner_name = player_name if res == "P1" else "CPU" if res == "P2" else "Tie"
    match_log.insert(0, [len(match_log) + 1, m_name, c_name, winner_name])
    df_off = pd.DataFrame(match_log, columns=["Round ID", "Player Move", "CPU Move", "Winner"])

    if not t_active:
        if res == "P1": n_wins += 1
        elif res == "P2": n_losses += 1
        else: n_ties += 1
        msg = f"{'🎉 You Win!' if res == 'P1' else '😢 CPU Wins!' if res == 'P2' else '🤝 Tie!'} {m_name} vs {c_name}"
        stats = f"🏆 Wins: {n_wins} | 🤖 Losses: {n_losses} | 🤝 Ties: {n_ties}"
        return msg, stats, df_off, n_wins, n_losses, n_ties, t_active, t_round, t_p_wins, t_c_wins, t_ties, "Endless Practice Active"
    
    else:
        if res == "P1": t_p_wins += 1
        elif res == "P2": t_c_wins += 1
        else: t_ties += 1

        if t_round < 10:
            status = f"⚔️ Round {t_round + 1}/10 | {player_name}: {t_p_wins} | CPU: {t_c_wins} | Ties: {t_ties}"
            t_round += 1
        else:
            if t_p_wins > t_c_wins: status = f"🏆 TOURNAMENT OVER! {player_name} WINS {t_p_wins}-{t_c_wins}!"
            elif t_c_wins > t_p_wins: status = f"💀 TOURNAMENT OVER! CPU WINS {t_c_wins}-{t_p_wins}!"
            else: status = f"⚖️ TOURNAMENT OVER! IT'S A DRAW {t_p_wins}-{t_c_wins}!"
            
            update_offline_leaderboards(player_name, t_p_wins, t_c_wins, t_ties)
            t_active = False

        msg = f"{'🎉 You Win!' if res == 'P1' else '😢 CPU Wins!' if res == 'P2' else '🤝 Tie!'} {m_name} vs {c_name}"
        stats = f"🏆 Wins: {n_wins} | 🤖 Losses: {n_losses} | 🤝 Ties: {n_ties}"
        return msg, stats, df_off, n_wins, n_losses, n_ties, t_active, t_round, t_p_wins, t_c_wins, t_ties, status

def reset_offline():
    return "Practice Mode Reset.", "🏆 Wins: 0 | 🤖 Losses: 0 | 🤝 Ties: 0", df_empty_off, 0, 0, 0, [], False, 1, 0, 0, 0, "Endless Practice Active"

def hard_reset_all():
    return (
        "", gr.update(visible=True), gr.update(visible=False), 
        "App Fully Reset.", "🏆 Wins: 0 | 🤖 Losses: 0 | 🤝 Ties: 0", df_empty_off, 0, 0, 0, [], 
        False, 1, 0, 0, 0, "Endless Practice Active"
    )

# ==========================================
# 5. UNIVERSAL EXCEL EXPORT
# ==========================================
def export_all_data(room_code, offline_log):
    temp_dir = tempfile.gettempdir()
    file_path = os.path.join(temp_dir, f"RPSLS_Arcade_Data.xlsx")
    
    with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
        df_off = pd.DataFrame(offline_log, columns=["Round ID", "Player Move", "CPU Move", "Winner"]) if offline_log else df_empty_off
        df_off.to_excel(writer, sheet_name="Offline Practice Logs", index=False)
        
        lb_data = get_leaderboard_data()
        df_lb = pd.DataFrame(lb_data, columns=["Champion Name", "Tournament Mode", "Lobby Size", "Status"]) if lb_data else df_empty_lb
        df_lb.to_excel(writer, sheet_name="Online Hall of Fame", index=False)
        
        hof_data = get_offline_hof_data()
        df_hof = pd.DataFrame(hof_data, columns=["Player Name", "Achievement", "Final Record"]) if hof_data else df_empty_off_hof
        df_hof.to_excel(writer, sheet_name="Offline Perfect 10 HOF", index=False)
        
        off_lb_data = get_offline_lb_data()
        df_off_lb = pd.DataFrame(off_lb_data, columns=["Player Name", "Wins", "Losses", "Ties", "Win Rate"]) if off_lb_data else df_empty_off_lb
        df_off_lb.to_excel(writer, sheet_name="Offline Leaderboard", index=False)

        if room_code:
            room = get_room(room_code)
            if room:
                logs_data = room.get("all_match_logs", [])
                df_on = pd.DataFrame(logs_data, columns=["Round", "Player 1", "Move 1", "Player 2", "Move 2", "Result"]) if logs_data else df_empty_history
                df_on.to_excel(writer, sheet_name="Online Tournament Logs", index=False)
    return file_path

# ==========================================
# 6. GRADIO UI LAYOUT (Flat Page Architecture)
# ==========================================
with gr.Blocks(theme=gr.themes.Soft(primary_hue="indigo")) as demo:
    client_id = gr.State(lambda: str(uuid.uuid4()))
    active_room = gr.State("")
    
    off_w, off_c, off_t, off_log = gr.State(0), gr.State(0), gr.State(0), gr.State([])
    off_t_active, off_t_round = gr.State(False), gr.State(1)
    off_t_p, off_t_c, off_t_ties = gr.State(0), gr.State(0), gr.State(0)

    with gr.Row():
        gr.Markdown("# 🕹️ RPSLS Arcade Arena")
        btn_theme = gr.Button("🌓 Toggle Dark Mode", variant="secondary")
        btn_hard_reset = gr.Button("🛑 HARD RESET APP", variant="stop")
        
    gr.Markdown("### Welcome! Enter your name below to play Online or Offline.")
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
        btn_export_online = gr.Button("💾 Host: Unlock Full Data Export", visible=False, variant="primary")

    gr.Markdown("---")
    gr.Markdown("## 🤖 OFFLINE PRACTICE")
    with gr.Row():
        btn_start_off_tourney = gr.Button("⚔️ Start 10-Round Offline Tournament", variant="primary")
        btn_reset_off = gr.Button("🔄 Reset Offline Data", variant="secondary")
    
    off_tourney_status = gr.Textbox(label="Tournament Status", value="Endless Practice Active", interactive=False)
    
    with gr.Row():
        with gr.Column():
            with gr.Row():
                off_r, off_p, off_s, off_l, off_k = [gr.Button("🪨"), gr.Button("📄"), gr.Button("✂️"), gr.Button("🦎"), gr.Button("🖖")]
        with gr.Column():
            off_msg = gr.Textbox(label="Match Result", interactive=False)
            off_stats = gr.Textbox(label="Endless Session Analytics", value="🏆 Wins: 0 | 🤖 Losses: 0 | 🤝 Ties: 0", interactive=False)
            
    gr.Markdown("### 🕒 Offline Round History Tracking")
    off_history_view = gr.Dataframe(headers=["Round ID", "Player Move", "CPU Move", "Winner"], interactive=False)

    gr.Markdown("---")
    gr.Markdown("## 🏆 HALL OF FAME & LEADERBOARDS")
    
    with gr.Row():
        btn_refresh_lb = gr.Button("🔄 Refresh All Leaderboards", variant="primary")
        btn_wipe_lb = gr.Button("🗑️ Wipe Global Leaderboards", variant="stop")
    
    with gr.Accordion("🌐 Online Global Hall of Fame (All Modes)", open=True):
        lb_view_setup = gr.Dataframe(headers=["Champion Name", "Tournament Mode", "Lobby Size", "Status"], interactive=False)
        
    with gr.Accordion("👑 Offline Perfect 10 Hall of Fame", open=False):
        off_hof_view = gr.Dataframe(headers=["Player Name", "Achievement", "Final Record"], interactive=False)
        
    with gr.Accordion("📊 Offline Tournament Leaderboard", open=False):
        off_lb_view = gr.Dataframe(headers=["Player Name", "Wins", "Losses", "Ties", "Win Rate"], interactive=False)
    
    gr.Markdown("Export all Offline Practice stats, Global Leaderboards, and completed Online Tournament logs to Excel.")
    btn_export_all = gr.Button("💾 Generate Excel Spreadsheets", variant="secondary")
    download_file = gr.File(label="Download Excel Document", interactive=False, visible=False)

    # --- EVENT TRIGGERS ---
    timer = gr.Timer(value=1)
    btn_theme.click(fn=None, js="() => { document.documentElement.classList.toggle('dark'); }")
    demo.load(load_all_leaderboards_once, inputs=[], outputs=[lb_view_setup, off_hof_view, off_lb_view])
    
    btn_refresh_lb.click(load_all_leaderboards_once, inputs=[], outputs=[lb_view_setup, off_hof_view, off_lb_view])
    btn_wipe_lb.click(wipe_all_leaderboards, inputs=[], outputs=[lb_view_setup, off_hof_view, off_lb_view])
    
    # Online
    btn_host.click(host_room, inputs=[client_id, my_name, host_mode, host_bo], outputs=[active_room, sys_msg, screen_setup, screen_game])
    btn_join.click(join_room, inputs=[client_id, my_name, join_code], outputs=[active_room, sys_msg, screen_setup, screen_game])
    btn_start.click(start_game, inputs=[client_id, active_room], outputs=[])
    btn_leave.click(leave_online_room, inputs=[], outputs=[active_room, screen_setup, screen_game])
    timer.tick(sync_ui, inputs=[client_id, active_room], outputs=[top_banner, match_status, bracket_view, history_view, btn_export_online])
    
    for btn, val in [(btn_r, 1), (btn_p, 2), (btn_s, 3), (btn_l, 4), (btn_k, 5)]:
        btn.click(make_move_online, inputs=[client_id, active_room, gr.State(val)], outputs=[])

    # Offline 
    btn_start_off_tourney.click(start_offline_tournament, inputs=[my_name], outputs=[off_t_active, off_t_round, off_t_p, off_t_c, off_t_ties, off_tourney_status])
    for btn, val in [(off_r, 1), (off_p, 2), (off_s, 3), (off_l, 4), (off_k, 5)]:
        btn.click(play_offline_round, 
                  inputs=[gr.State(val), off_w, off_c, off_t, off_log, my_name, off_t_active, off_t_round, off_t_p, off_t_c, off_t_ties], 
                  outputs=[off_msg, off_stats, off_history_view, off_w, off_c, off_t, off_t_active, off_t_round, off_t_p, off_t_c, off_t_ties, off_tourney_status])
    btn_reset_off.click(reset_offline, inputs=[], outputs=[off_msg, off_stats, off_history_view, off_w, off_c, off_t, off_log, off_t_active, off_t_round, off_t_p, off_t_c, off_t_ties, off_tourney_status])

    # Resets & Exports
    btn_hard_reset.click(hard_reset_all, inputs=[], outputs=[active_room, screen_setup, screen_game, off_msg, off_stats, off_history_view, off_w, off_c, off_t, off_log, off_t_active, off_t_round, off_t_p, off_t_c, off_t_ties, off_tourney_status])
    btn_export_all.click(export_all_data, inputs=[active_room, off_log], outputs=[download_file]).then(lambda: gr.update(visible=True), outputs=[download_file])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    demo.launch(server_name="0.0.0.0", server_port=port)