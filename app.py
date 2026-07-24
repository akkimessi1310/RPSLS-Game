import os
import tempfile
import random
import gradio as gr
import pandas as pd

CHOICES = {1: "Rock", 2: "Paper", 3: "Scissors", 4: "Lizard", 5: "Spock"}
ACTION_PHRASES = {
    (1, 3): "Rock crushes Scissors", (1, 4): "Rock crushes Lizard",
    (2, 1): "Paper covers Rock", (2, 5): "Paper disproves Spock",
    (3, 2): "Scissors cuts Paper", (3, 4): "Scissors decapitates Lizard",
    (4, 2): "Lizard eats Paper", (4, 5): "Lizard poisons Spock",
    (5, 1): "Spock vaporizes Rock", (5, 3): "Spock smashes Scissors"
}
WRITTEN_RULES_TEXT = "### 📖 Game Rules\n* Scissors cuts Paper | Paper covers Rock\n* Rock crushes Lizard | Lizard poisons Spock\n* Spock smashes Scissors | Scissors decapitates Lizard\n* Lizard eats Paper | Paper disproves Spock\n* Spock vaporizes Rock | Rock crushes Scissors"

def play_round(player_choice_num, n_wins, n_losses, n_ties, n_log, t_wins, t_losses, t_ties, t_log, tourney_active, current_round, t_p_wins, t_cpu_wins, player_name, leaderboard_list):
    cpu_choice_num = random.randint(1, 5)
    p_name, c_name = CHOICES[player_choice_num], CHOICES[cpu_choice_num]

    if player_choice_num == cpu_choice_num:
        outcome_msg, win_tag = "🤝 It's a Tie!", "Tie"
    elif (player_choice_num, cpu_choice_num) in ACTION_PHRASES:
        outcome_msg, win_tag = f"🎉 {player_name} Wins! {ACTION_PHRASES[(player_choice_num, cpu_choice_num)]}.", "Player"
    else:
        outcome_msg, win_tag = f"😢 CPU Wins! {ACTION_PHRASES[(cpu_choice_num, player_choice_num)]}.", "CPU"

    # Branch 1: Normal Practice Mode Processing
    if not tourney_active:
        if win_tag == "Tie": n_ties += 1
        elif win_tag == "Player": n_wins += 1
        else: n_losses += 1

        n_log.append([len(n_log) + 1, p_name, c_name, "Tie" if win_tag == "Tie" else player_name if win_tag == "Player" else "CPU"])
        total_n = n_wins + n_losses + n_ties
        stats_summary = f"📊 NORMAL MODE ANALYTICS:\nTotal Casual Matches: {total_n}\nPlayer Win Rate: {(n_wins*100/total_n if total_n>0 else 0):.1f}%\nCPU Win Rate: {(n_losses*100/total_n if total_n>0 else 0):.1f}%\nTie Ratio: {(n_ties*100/total_n if total_n>0 else 0):.1f}%"

        return (p_name, c_name, outcome_msg, stats_summary,
                f"🏆 {n_wins}", f"🤖 {n_losses}", f"🤝 {n_ties}", n_log, n_log, t_log,
                n_wins, n_losses, n_ties, t_wins, t_losses, t_ties, tourney_active, current_round, t_p_wins, t_cpu_wins,
                "🛑 Normal Practice Session Active.", leaderboard_list, leaderboard_list)

    # Branch 2: Tournament Ranked Match Processing
    # Track the active tournament ties sub-metric count explicitly
    t_round_is_tie = 0
    if win_tag == "Tie":
        t_ties += 1
        t_round_is_tie = 1
    elif win_tag == "Player":
        t_wins += 1
        t_p_wins += 1
    else:
        t_losses += 1
        t_cpu_wins += 1

    t_log.append([len(t_log) + 1, p_name, c_name, player_name if win_tag == "Player" else "CPU" if win_tag == "CPU" else "Tie"])
    current_round += 1

    if current_round <= 10:
        current_session_ties = (current_round - 1) - (t_p_wins + t_cpu_wins)
        tourney_banner = f"⚔️ Tournament Status: Round {current_round} / 10 | Standings -> {player_name}: {t_p_wins} | CPU: {t_cpu_wins} | Ties: {current_session_ties}"
    else:
        tourney_active = False
        outcome_msg = f"🏆 TOURNAMENT OVER! {player_name} scored {t_p_wins} wins out of 10 rounds!"
        tourney_banner = "🏁 Tournament Completed! Click 'Reset / Stop Game' to clear space for a new challenger."

        # Calculate ties from this individual 10-round tournament run
        t_session_ties = 10 - (t_p_wins + t_cpu_wins)

        # FEATURE UPDATE: Injected the individual session tie counter index value directly into the leaderboard state list
        leaderboard_list.append([player_name, int(t_p_wins), int(t_session_ties), int(t_cpu_wins), f"{t_p_wins * 10}%"])
        
        # FIX: Added x[2] to the lambda key tuple so it sorts by Wins first, then Ties second
        leaderboard_list = sorted(leaderboard_list, key=lambda x: (x[1], x[2]), reverse=True)

    total_t = t_wins + t_losses + t_ties
    stats_summary = f"🏆 TOURNAMENT MODE ANALYTICS:\nTotal Ranked Matches: {total_t}\nOverall Player Win Rate: {(t_wins*100/total_t if total_t>0 else 0):.1f}%\nOverall CPU Win Rate: {(t_losses*100/total_t if total_t>0 else 0):.1f}%\nTie Ratio: {(t_ties*100/total_t if total_t>0 else 0):.1f}%"

    return (p_name, c_name, outcome_msg, stats_summary,
            f"🏆 {t_wins}", f"🤖 {t_losses}", f"🤝 {t_ties}", n_log, n_log, t_log,
            n_wins, n_losses, n_ties, t_wins, t_losses, t_ties, tourney_active, current_round, t_p_wins, t_cpu_wins,
            tourney_banner, leaderboard_list, leaderboard_list)
    
def start_tournament(player_name, t_wins, t_losses, t_ties):
    if not player_name.strip(): player_name = "Player 1"
    banner = f"⚔️ Tournament Status: Round 1 / 10 | Standings -> {player_name}: 0 | CPU: 0 | Ties: 0"
    return True, 1, 0, 0, banner, gr.update(interactive=False, value=player_name), f"🏆 {t_wins}", f"🤖 {t_losses}", f"🤝 {t_ties}", "Tournament Mode Activated. Practice scores paused."

def reset_and_unlock(n_wins, n_losses, n_ties, n_log, t_wins, t_losses, t_ties, t_log, tourney_active, current_round, t_p_wins, t_cpu_wins):
    # Rollback logic: If a tournament is active and we played at least 1 round, we are aborting midway.
    if tourney_active and current_round > 1:
        rounds_played = current_round - 1
        session_ties = rounds_played - (t_p_wins + t_cpu_wins)
        
        # Deduct the partial tournament stats from the global tracking
        t_wins -= t_p_wins
        t_losses -= t_cpu_wins
        t_ties -= session_ties
        
        # Remove the partial matches from the tournament log
        if rounds_played > 0:
            t_log = t_log[:-rounds_played]

    return (
        "", "", "", "No analytical data recorded.", 
        False, 1, 0, 0, 
        "No tournament bracket active currently. Normal Mode Active.", 
        gr.update(interactive=True), 
        f"🏆 {n_wins}", f"🤖 {n_losses}", f"🤝 {n_ties}", n_log,
        t_wins, t_losses, t_ties, t_log # Return the updated global tournament states
    )
    
def clear_all_data():
    return ("", "", "", "No analytical data recorded.", "🏆 0", "🤖 0", "🤝 0", [], [], [], 0, 0, 0, 0, 0, 0, False, 1, 0, 0, "No tournament bracket active currently. Normal Mode Active.", gr.update(interactive=True, value=" "), [], [])

def save_everything_to_file(n_log, t_log, leaderboard_list, n_w, n_l, n_t, t_w, t_l, t_t):
    if not n_log and not t_log and not leaderboard_list: return None
    df_normal = pd.DataFrame(n_log, columns=["Round ID", "Player Move Choice", "CPU Move Choice", "Winner Label"])
    df_tourney = pd.DataFrame(t_log, columns=["Ranked Match ID", "Player Move Choice", "CPU Move Choice", "Winner Label"])

    # FEATURE UPDATE: Sync exporter header parsing array with new leaderboard dataframe configuration mapping properties
    df_leaderboard = pd.DataFrame(leaderboard_list, columns=["Challenger Name", "Wins Summary", "Ties Summary", "CPU Defeats", "Tournament Win Rate"])

    tot_n = n_w + n_l + n_t
    tot_t = t_w + t_l + t_t

    df_stats = pd.DataFrame({
        "Analytical Category Summary": ["Total Rounds Played", "Total Player Wins", "Total CPU Victories", "Total Match Draws"],
        "Normal Practice Mode Count": [tot_n, n_w, n_l, n_t],
        "Normal Mode Percentage Yield": ["100.0%", f"{(n_w*100/tot_n if tot_n>0 else 0):.1f}%", f"{(n_l*100/tot_n if tot_n>0 else 0):.1f}%", f"{(n_t*100/tot_n if tot_n>0 else 0):.1f}%"],
        "Tournament Mode Count": [tot_t, t_w, t_l, t_t],
        "Tournament Mode Percentage Yield": ["100.0%", f"{(t_w*100/tot_t if tot_t>0 else 0):.1f}%", f"{(t_l*100/tot_t if tot_t>0 else 0):.1f}%", f"{(t_t*100/tot_t if tot_t>0 else 0):.1f}%"]
    })
    temp_dir = tempfile.gettempdir()
    file_path = os.path.join(temp_dir, "rpsls_isolated_analytics_report.xlsx")
    with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
        sheet = "Consolidated Analytics"
        current_row = 0
        
        # Write Stats
        df_stats.to_excel(writer, sheet_name=sheet, startrow=current_row, index=False)
        current_row += len(df_stats.index) + 3 
        
        # Write Leaderboard (with a title row)
        pd.DataFrame([["--- Hall of Fame Registry ---"]]).to_excel(writer, sheet_name=sheet, startrow=current_row, index=False, header=False)
        current_row += 1
        df_leaderboard.to_excel(writer, sheet_name=sheet, startrow=current_row, index=False)
        current_row += len(df_leaderboard.index) + 3
        
        # Write Normal Logs (with a title row)
        pd.DataFrame([["--- Normal Mode Logs ---"]]).to_excel(writer, sheet_name=sheet, startrow=current_row, index=False, header=False)
        current_row += 1
        df_normal.to_excel(writer, sheet_name=sheet, startrow=current_row, index=False)
        current_row += len(df_normal.index) + 3

        # Write Tourney Logs (with a title row)
        pd.DataFrame([["--- Tournament Mode Logs ---"]]).to_excel(writer, sheet_name=sheet, startrow=current_row, index=False, header=False)
        current_row += 1
        df_tourney.to_excel(writer, sheet_name=sheet, startrow=current_row, index=False)

    return file_path
with gr.Blocks(theme=gr.themes.Soft(primary_hue="purple", secondary_hue="indigo")) as demo:
    n_score_p, n_score_c, n_score_t, n_history_state = gr.State(0), gr.State(0), gr.State(0), gr.State([])
    t_score_p, t_score_c, t_score_t, t_history_state = gr.State(0), gr.State(0), gr.State(0), gr.State([])
    tourney_active_state, tourney_round_counter = gr.State(False), gr.State(1)
    tourney_p_wins, tourney_c_wins, leaderboard_state = gr.State(0), gr.State(0), gr.State([])

    with gr.Row():
        gr.Markdown("# 🎮 Rock Paper Scissors Lizard Spock (Arcade Edition)")
        btn_theme = gr.Button("🌓 Toggle Dark Mode", variant="secondary")
    with gr.Row():
        card_p = gr.Label(value="🏆 0", label="Active Mode Player Wins")
        card_c = gr.Label(value="🤖 0", label="Active Mode CPU Wins")
        card_t = gr.Label(value="🤝 0", label="Active Mode Draw Ties")
    with gr.Group():
        gr.Markdown("### 🕹️ Arcade Challenge Entry Zone")
        with gr.Row():
            in_player_name = gr.Textbox(value=" ", label="Enter Your Name", interactive=True)
            btn_start_tourney = gr.Button("⚔️ Start 10-Round Tournament", variant="primary")
            btn_reset = gr.Button("🔄 Reset / Stop Game", variant="stop")
        out_tourney_status = gr.Textbox(value="No tournament bracket active currently. Normal Mode Active.", label="Status Tracking", interactive=False)
    with gr.Row():
        with gr.Column(scale=3):
            gr.Markdown("### Cast Your Move Inputs Below:")
            with gr.Row():
                btn_rock, btn_paper, btn_scissors, btn_lizard, btn_spock = gr.Button("🪨 Rock"), gr.Button("📄 Paper"), gr.Button("✂️ Scissors"), gr.Button("🦎 Lizard"), gr.Button("🖖 Spock")
            with gr.Row():
                out_player = gr.Textbox(label="Your Hand Gesture", interactive=False)
                out_cpu = gr.Textbox(label="CPU Hand Gesture", interactive=False)
            out_result = gr.Textbox(label="Last Match Result Feedback", interactive=False)
            out_stats = gr.Textbox(label="Analytical Platform Summary Table", lines=4, interactive=False, value="No analytical data recorded.")
            with gr.Row():
                btn_save = gr.Button("💾 Export Consolidated 1-Sheet Excel Document", variant="secondary")
                btn_clear_data = gr.Button("🧹 Reset Leaderboard & Analytics Data", variant="stop")
            download_file_target = gr.File(label="Spreadsheet Output Window File", interactive=False)
        with gr.Column(scale=2):
            gr.Markdown(WRITTEN_RULES_TEXT)
    with gr.Row():
        with gr.Column():
            gr.Markdown("### 🕒 Live Round Match Log Table Feed Grid View (Normal Mode)")
            history_table = gr.Dataframe(headers=["Round ID", "Player Move Choice", "CPU Move Choice", "Winner Label"], datatype=["number", "str", "str", "str"], value=[], interactive=False)
        with gr.Column():
            gr.Markdown("### 🏆 Hall of Fame Leaderboard Rankings")

            # FEATURE UPDATE: Appended "Ties Summary" to the layout schema configurations list array matrix
            leaderboard_table = gr.Dataframe(headers=["Challenger Name", "Wins Summary", "Ties Summary", "CPU Defeats", "Tournament Win Rate"], datatype=["str", "number", "number", "number", "str"], value=[], interactive=False)

    btn_theme.click(fn=None, js="() => { document.body.classList.toggle('dark'); }")
    btn_start_tourney.click(fn=start_tournament, inputs=[in_player_name, t_score_p, t_score_c, t_score_t], outputs=[tourney_active_state, tourney_round_counter, tourney_p_wins, tourney_c_wins, out_tourney_status, in_player_name, card_p, card_c, card_t, out_stats])
    btn_reset.click(
        fn=reset_and_unlock, 
        inputs=[
            n_score_p, n_score_c, n_score_t, n_history_state,
            t_score_p, t_score_c, t_score_t, t_history_state,
            tourney_active_state, tourney_round_counter, tourney_p_wins, tourney_c_wins
        ], 
        outputs=[
            out_player, out_cpu, out_result, out_stats, 
            tourney_active_state, tourney_round_counter, tourney_p_wins, tourney_c_wins, 
            out_tourney_status, in_player_name, 
            card_p, card_c, card_t, history_table,
            t_score_p, t_score_c, t_score_t, t_history_state
        ]
    )
    btn_clear_data.click(fn=clear_all_data, inputs=[], outputs=[out_player, out_cpu, out_result, out_stats, card_p, card_c, card_t, history_table, n_history_state, t_history_state, n_score_p, n_score_c, n_score_t, t_score_p, t_score_c, t_score_t, tourney_active_state, tourney_round_counter, tourney_p_wins, tourney_c_wins, out_tourney_status, in_player_name, leaderboard_table, leaderboard_state])

    game_buttons = [(btn_rock, 1), (btn_paper, 2), (btn_scissors, 3), (btn_lizard, 4), (btn_spock, 5)]
    for btn, choice_val in game_buttons:
        btn.click(fn=play_round, inputs=[gr.State(choice_val), n_score_p, n_score_c, n_score_t, n_history_state, t_score_p, t_score_c, t_score_t, t_history_state, tourney_active_state, tourney_round_counter, tourney_p_wins, tourney_c_wins, in_player_name, leaderboard_state], outputs=[out_player, out_cpu, out_result, out_stats, card_p, card_c, card_t, history_table, n_history_state, t_history_state, n_score_p, n_score_c, n_score_t, t_score_p, t_score_c, t_score_t, tourney_active_state, tourney_round_counter, tourney_p_wins, tourney_c_wins, out_tourney_status, leaderboard_table, leaderboard_state])

    btn_save.click(fn=save_everything_to_file, inputs=[n_history_state, t_history_state, leaderboard_state, n_score_p, n_score_c, n_score_t, t_score_p, t_score_c, t_score_t], outputs=[download_file_target])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    
    demo.launch(
        server_name="0.0.0.0", 
        server_port=port,
        ssr_mode=False,
        theme=gr.themes.Soft(primary_hue="purple", secondary_hue="indigo")
    )
