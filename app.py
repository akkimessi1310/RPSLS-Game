import os
import uuid
import random
import math
import json
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
# 2. REDIS DATABASE CONNECTION
# ==========================================
redis_client = Redis(
    url=os.environ.get("UPSTASH_REDIS_REST_URL", ""),
    token=os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
)

def get_room(room_code):
    # Appends RG: prefix behind the scenes
    data = redis_client.get(f"RG:{room_code}")
    return json.loads(data) if data else None

def save_room(room_code, room_data):
    # Appends RG: prefix behind the scenes
    redis_client.setex(f"RG:{room_code}", 86400, json.dumps(room_data))

def room_exists(room_code):
    # Appends RG: prefix behind the scenes
    return redis_client.exists(f"RG:{room_code}") > 0

def generate_room_code():
    code = str(uuid.uuid4())[:5].upper()
    while room_exists(code):
        code = str(uuid.uuid4())[:5].upper()
    return code

def resolve_matchup(m1, m2):
    if m1 == m2: return "Tie"
    if (m1, m2) in ACTION_PHRASES: return "P1"
    return "P2"

# ==========================================
# 3. TOURNAMENT BRACKET ENGINE
# ==========================================
def generate_bracket(players):
    p_ids = list(players.keys())
    random.shuffle(p_ids)
    
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
    if not room or room["status"] == "finished": return
    
    all_done = all(m["winner"] is not None for m in room["bracket"])
    if not all_done: return

    winners = [m["winner"] for m in room["bracket"]]
    
    if len(winners) == 1:
        room["status"] = "finished"
        room["champion"] = winners[0]
        return
    
    new_matches = []
    for i in range(0, len(winners), 2):
        new_matches.append({
            "p1": winners[i], "p2": winners[i+1], 
            "p1_score": 0, "p2_score": 0, "p1_move": None, "p2_move": None, "winner": None,
            "p1_pvc_stats": {"w":0, "l":0, "t":0, "played":0}, 
            "p2_pvc_stats": {"w":0, "l":0, "t":0, "played":0}
        })
    room["bracket"] = new_matches
    room["round"] += 1

# ==========================================
# 4. ONLINE LOBBY FUNCTIONS (Redis Enabled)
# ==========================================
def host_room(client_id, player_name, mode, best_of):
    if not player_name.strip(): player_name = "Host"
    code = generate_room_code()
    target_score = 2 if best_of == "Best of 3" else 3
    
    room_data = {
        "host": client_id,
        "mode": mode,
        "target_score": target_score,
        "status": "lobby",
        "players": {client_id: {"name": player_name}},
        "bracket": [],
        "round": 1,
        "log": []
    }
    save_room(code, room_data)
    return code, gr.update(visible=False), gr.update(visible=True)

def join_room(client_id, player_name, room_code):
    code = room_code.upper().strip()
    room = get_room(code)
    
    if not room: return "❌ Room not found.", gr.update(), gr.update()
    if room["status"] != "lobby": return "❌ Game already in progress.", gr.update(), gr.update()
    if room["mode"] == "Casual 1v1" and len(room["players"]) >= 2: return "❌ Casual room is full.", gr.update(), gr.update()
    if len(room["players"]) >= 16: return "❌ Tournament is at max capacity (16).", gr.update(), gr.update()
        
    room["players"][client_id] = {"name": player_name if player_name.strip() else "Guest"}
    save_room(code, room)
    return f"Joined {code}!", gr.update(visible=False), gr.update(visible=True)

def start_game(client_id, room_code):
    room = get_room(room_code)
    if not room or room["host"] != client_id or len(room["players"]) < 2: return
    
    room["status"] = "playing"
    room["bracket"] = generate_bracket(room["players"])
    save_room(room_code, room)

# ==========================================
# 5. ONLINE GAMEPLAY LOGIC (Redis Enabled)
# ==========================================
def make_move_online(client_id, room_code, move):
    room = get_room(room_code)
    if not room or room["status"] != "playing": return
    
    match = None
    is_p1 = False
    for m in room["bracket"]:
        if m["winner"] is None and (m["p1"] == client_id or m["p2"] == client_id):
            match = m
            is_p1 = (m["p1"] == client_id)
            break
            
    if not match: return
    
    if room["mode"] == "PvC Tournament (10 Rounds)":
        stats = match["p1_pvc_stats"] if is_p1 else match["p2_pvc_stats"]
        if stats["played"] < 10:
            cpu_move = random.randint(1, 5)
            res = resolve_matchup(move, cpu_move)
            stats["played"] += 1
            if res == "P1": stats["w"] += 1
            elif res == "P2": stats["l"] += 1
            else: stats["t"] += 1
            
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
            
            if res == "P1": 
                match["p1_score"] += 1
                room["log"].insert(0, f"{p1_name} won round! {m1_name} vs {m2_name}")
            elif res == "P2": 
                match["p2_score"] += 1
                room["log"].insert(0, f"{p2_name} won round! {m2_name} vs {m1_name}")
            else:
                room["log"].insert(0, f"Tie! Both chose {m1_name}")
                
            match["p1_move"], match["p2_move"] = None, None
            
            if match["p1_score"] == room["target_score"]: match["winner"] = match["p1"]
            elif match["p2_score"] == room["target_score"]: match["winner"] = match["p2"]
            
            if match["winner"]: advance_tournament(room)
            
    save_room(room_code, room)

# ==========================================
# 6. UI SYNCHRONIZATION (Polling from Redis)
# ==========================================
def sync_ui(client_id, room_code):
    if not room_code: return gr.update(value="Not in a room"), "", pd.DataFrame()
    room = get_room(room_code)
    if not room: return gr.update(value="Room closed or expired"), "", pd.DataFrame()
    
    players_list = ", ".join([p["name"] for p in room["players"].values()])
    
    if room["status"] == "lobby":
        html = f"<h3>Room: {room_code} | Mode: {room['mode']}</h3><p>Players ({len(room['players'])}): {players_list}</p>"
        if room["host"] == client_id: html += "<p><em>You are host. Click Start when ready!</em></p>"
        return html, "Waiting in lobby...", pd.DataFrame()
        
    if room["status"] == "finished":
        champ_name = room["players"][room["champion"]]["name"]
        return f"<h2>🏆 TOURNAMENT OVER!</h2><h3>Champion: {champ_name}</h3>", "Game Over", pd.DataFrame()

    html = f"<h3>Round {room['round']} | {room['mode']}</h3>"
    status = "Waiting..."
    bracket_data = []
    my_match = None

    for m in room["bracket"]:
        p1n = room["players"][m["p1"]]["name"]
        p2n = room["players"][m["p2"]]["name"] if m["p2"] else "BYE"
        m_stat = "Finished" if m["winner"] else "Playing"
        
        if room["mode"] == "PvC Tournament (10 Rounds)":
            p1_inf = f"{m['p1_pvc_stats']['w']}W-{m['p1_pvc_stats']['l']}L (Played {m['p1_pvc_stats']['played']}/10)"
            p2_inf = f"{m['p2_pvc_stats']['w']}W-{m['p2_pvc_stats']['l']}L (Played {m['p2_pvc_stats']['played']}/10)" if m["p2"] else "-"
            bracket_data.append([p1n, p1_inf, "vs", p2n, p2_inf, m_stat])
            
            if not m["winner"] and (m["p1"] == client_id or m["p2"] == client_id):
                my_match = m
                is_p1 = (m["p1"] == client_id)
                played = m['p1_pvc_stats']['played'] if is_p1 else m['p2_pvc_stats']['played']
                if played < 10: status = f"Your Turn: Play against CPU! ({played}/10 complete)"
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
    df = pd.DataFrame(bracket_data, columns=["Player 1", "Score/Stats", " ", "Player 2", "Score/Stats", "Status"])
    return html, status, df

# ==========================================
# 7. GRADIO UI LAYOUT
# ==========================================
with gr.Blocks(theme=gr.themes.Soft(primary_hue="purple")) as demo:
    client_id = gr.State(lambda: str(uuid.uuid4()))
    active_room = gr.State("")

    gr.Markdown("# 🎮 RPSLS Arena: Online Edition")
    
    with gr.Tabs():
        with gr.Tab("🌐 Online Arena"):
            with gr.Column(visible=True) as screen_setup:
                gr.Markdown("### Welcome to the Multiplayer Hub!")
                my_name = gr.Textbox(label="Your Name", value="Player")
                with gr.Row():
                    with gr.Group():
                        gr.Markdown("#### Host a Game")
                        host_mode = gr.Dropdown(["Casual 1v1", "PvP Tournament (3-16)", "PvC Tournament (10 Rounds)"], value="Casual 1v1", label="Game Mode")
                        host_bo = gr.Radio(["Best of 3", "Best of 5"], value="Best of 3", label="Match Length")
                        btn_host = gr.Button("Create Room", variant="primary")
                    with gr.Group():
                        gr.Markdown("#### Join a Game")
                        join_code = gr.Textbox(label="Enter 5-Letter Room Code")
                        btn_join = gr.Button("Join Room")
                        sys_msg = gr.Markdown("")

            with gr.Column(visible=False) as screen_game:
                top_banner = gr.HTML("Loading room...")
                btn_start = gr.Button("🚀 Host: Start Tournament / Match", variant="primary")
                with gr.Row():
                    with gr.Column(scale=1):
                        match_status = gr.Textbox(label="Action Required", interactive=False)
                        with gr.Row():
                            btn_r, btn_p, btn_s, btn_l, btn_k = [gr.Button("🪨"), gr.Button("📄"), gr.Button("✂️"), gr.Button("🦎"), gr.Button("🖖")]
                    with gr.Column(scale=2):
                        bracket_view = gr.Dataframe(headers=["Player 1", "Score/Stats", " ", "Player 2", "Score/Stats", "Status"], interactive=False)

            timer = gr.Timer(value=1)
            
            btn_host.click(host_room, inputs=[client_id, my_name, host_mode, host_bo], outputs=[active_room, screen_setup, screen_game])
            btn_join.click(join_room, inputs=[client_id, my_name, join_code], outputs=[sys_msg, screen_setup, screen_game]).then(
                lambda code: code, inputs=join_code, outputs=active_room
            )
            btn_start.click(start_game, inputs=[client_id, active_room], outputs=[])
            timer.tick(sync_ui, inputs=[client_id, active_room], outputs=[top_banner, match_status, bracket_view])
            for btn, val in [(btn_r, 1), (btn_p, 2), (btn_s, 3), (btn_l, 4), (btn_k, 5)]:
                btn.click(make_move_online, inputs=[client_id, active_room, gr.State(val)], outputs=[])

        with gr.Tab("🤖 Offline Practice"):
            gr.Markdown("Test your skills locally against the AI.")
            with gr.Row():
                with gr.Column():
                    l_r, l_p, l_s, l_l, l_k = gr.Button("🪨"), gr.Button("📄"), gr.Button("✂️"), gr.Button("🦎"), gr.Button("🖖")
                with gr.Column():
                    out_res = gr.Textbox(label="Result")
                    
            def play_local(move):
                c_move = random.randint(1,5)
                if move == c_move: return f"Tie! Both chose {CHOICES[move]}"
                elif (move, c_move) in ACTION_PHRASES: return f"You Win! {ACTION_PHRASES[(move, c_move)]}"
                else: return f"CPU Wins! {ACTION_PHRASES[(c_move, move)]}"
                
            for btn, val in [(l_r, 1), (l_p, 2), (l_s, 3), (l_l, 4), (l_k, 5)]:
                btn.click(play_local, inputs=[gr.State(val)], outputs=out_res)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    demo.launch(server_name="0.0.0.0", server_port=port)