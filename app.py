import os
from flask import Flask, render_template, request, redirect, session, jsonify, send_from_directory
import sqlite3
import requests
import base64

app = Flask(__name__, template_folder='.')
app.secret_key = "9664c42e441bbd49bdc5ac31dc550bead16d49bffb395e5e185e2f72085a638d"

# ================= CONFIGURATION =================
DOMAIN_URL = "https://aelionx.onrender.com" 

# 1. EPIC GAMES OAUTH2 (Vraie Connexion)
# Il faudra que tu crées une application sur dev.epicgames.com pour avoir ces clés
EPIC_CLIENT_ID = "xyza7891k9NrmSzmyZc1wPaDM8H2G1zu"
EPIC_CLIENT_SECRET = "utdW2eU78ctM1QGwtEaTpCZBUVAPTx8E3v8JEP18vs0"
EPIC_REDIRECT_URI = f"{DOMAIN_URL}/callback/epic"

# 2. PAYPAL API
PAYPAL_CLIENT_ID = "AZnJoA4KhCvofBX4gnAEoszq7U8WMPZaFCvuxNBP0f6iEWKhBT19d71tewTNJ4mZhsBjkGtWuBSV5n0G"
PAYPAL_SECRET = "EF5L9PIRmbiP2QkwmXQOfj5js4hE52zPCPdCUXf0mXFA43rgIo10ahOhQ-qcNPNU4ejsQRgF8uEJiH5i"
PAYPAL_API_BASE = "https://api-m.sandbox.paypal.com" # Remplace par api-m.paypal.com en réel

# ================= BASE DE DONNÉES =================
def init_db():
    conn = sqlite3.connect('aelionx.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (user_id TEXT PRIMARY KEY, tokens REAL, elo INTEGER, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, epic_id TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS withdrawals 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, amount REAL, paypal_email TEXT, status TEXT DEFAULT 'PENDING')''')
    c.execute('''CREATE TABLE IF NOT EXISTS matches 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, creator_id TEXT, region TEXT, platform TEXT, gamemode TEXT, entry_fee REAL, prize REAL, status TEXT DEFAULT 'WAITING')''')
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect('aelionx.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    if not user:
        c.execute("INSERT OR IGNORE INTO users (user_id, tokens, elo, wins, losses, epic_id) VALUES (?, ?, ?, ?, ?, ?)", (user_id, 0.0, 0, 0, 0, user_id))
        conn.commit()
        user = (user_id, 0.0, 0, 0, 0, user_id)
    conn.close()
    return user

def add_tokens(user_id, amount):
    conn = sqlite3.connect('aelionx.db')
    c = conn.cursor()
    c.execute("UPDATE users SET tokens = tokens + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

# ================= ROUTES DU SITE =================

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/<path:filename>')
def serve_file(filename):
    return send_from_directory('.', filename)

@app.route('/api/me')
def api_me():
    if 'user_id' in session:
        user = get_user(session['user_id'])
        return jsonify({"logged_in": True, "balance": user[1], "username": session['username']})
    return jsonify({"logged_in": False})

# --- 1. CONNEXION OFFICIELLE EPIC GAMES ---
@app.route('/login/epic')
def login_epic():
    # Redirige le joueur vers la vraie page de connexion d'Epic Games
    epic_auth_url = f"https://www.epicgames.com/id/authorize?client_id={EPIC_CLIENT_ID}&response_type=code&scope=basic_profile&redirect_uri={EPIC_REDIRECT_URI}"
    return redirect(epic_auth_url)

@app.route('/callback/epic')
def callback_epic():
    code = request.args.get('code')
    if not code:
        return "Erreur : Code d'autorisation manquant.", 400
        
    # On échange le code secret contre les informations du joueur
    token_url = "https://api.epicgames.dev/epic/oauth/v2/token"
    auth_str = f"{EPIC_CLIENT_ID}:{EPIC_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    
    headers = {
        "Authorization": f"Basic {b64_auth}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": EPIC_REDIRECT_URI
    }
    
    try:
        r = requests.post(token_url, headers=headers, data=data)
        token_data = r.json()
        
        if "access_token" not in token_data:
            return f"Erreur de connexion Epic (Clés invalides ?) : {token_data}", 400
            
        epic_account_id = token_data.get("account_id")
        epic_display_name = token_data.get("displayName", epic_account_id)
        
        # Le joueur est connecté avec succès !
        session['user_id'] = epic_account_id
        session['username'] = epic_display_name
        
        get_user(epic_account_id) # Initialise en base de données
        
        return redirect('/?epic_success=true')
    except Exception as e:
        return f"Erreur serveur avec Epic Games : {str(e)}", 500


# --- 2. MATCHS ---
@app.route('/api/matches', methods=['GET'])
def get_matches():
    conn = sqlite3.connect('aelionx.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM matches WHERE status = 'WAITING' ORDER BY id DESC")
    matches = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify({"matches": matches})

@app.route('/api/matches/create', methods=['POST'])
def create_match():
    if 'user_id' not in session: return jsonify({"error": "Veuillez vous connecter avec Epic Games d'abord."}), 403
    req = request.get_json(silent=True) or {}
    fee = float(req.get('entry_fee', 0))
    if fee <= 0: return jsonify({"error": "Mise invalide"}), 400
    
    user_id = session['user_id']
    user = get_user(user_id)
    if user[1] < fee: return jsonify({"error": "Fonds insuffisants sur le compte."}), 400
    
    prize = fee * 1.9 
    add_tokens(user_id, -fee)
    new_user_data = get_user(user_id)
    
    conn = sqlite3.connect('aelionx.db')
    c = conn.cursor()
    c.execute("INSERT INTO matches (creator_id, region, platform, gamemode, entry_fee, prize) VALUES (?, ?, ?, ?, ?, ?)", 
              (user_id, req.get('region'), req.get('platform'), req.get('gamemode'), fee, prize))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "new_balance": new_user_data[1]})

@app.route('/api/matches/join', methods=['POST'])
def join_match():
    if 'user_id' not in session: return jsonify({"error": "Veuillez vous connecter avec Epic Games d'abord."}), 403
    req = request.get_json(silent=True) or {}
    match_id = req.get('match_id')
    user_id = session['user_id']
    
    conn = sqlite3.connect('aelionx.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM matches WHERE id = ? AND status = 'WAITING'", (match_id,))
    match = c.fetchone()
    if not match:
        conn.close()
        return jsonify({"error": "Match introuvable ou déjà plein"}), 400
        
    if match['creator_id'] == user_id:
        conn.close()
        return jsonify({"error": "Vous ne pouvez pas rejoindre votre propre match !"}), 400
        
    user = get_user(user_id)
    fee = match['entry_fee']
    if user[1] < fee:
        conn.close()
        return jsonify({"error": "Fonds insuffisants"}), 400
        
    add_tokens(user_id, -fee)
    new_user_data = get_user(user_id)
    
    c.execute("UPDATE matches SET status = 'OCCUPIED' WHERE id = ?", (match_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "new_balance": new_user_data[1]})

# --- 3. RETRAITS ---
@app.route('/api/withdraw', methods=['POST'])
def withdraw():
    if 'user_id' not in session: return jsonify({"error": "Veuillez vous connecter avec Epic Games d'abord."}), 403
    req_data = request.get_json(silent=True) or {}
    amount = float(req_data.get('amount', 0))
    paypal_email = req_data.get('email', '')

    if amount < 5.0: return jsonify({"error": "Le retrait minimum est de 5 Tokens."}), 400
    if not paypal_email or "@" not in paypal_email: return jsonify({"error": "Email PayPal invalide."}), 400

    user_id = session['user_id']
    user = get_user(user_id)
    if user[1] < amount: return jsonify({"error": "Fonds insuffisants."}), 400

    add_tokens(user_id, -amount)
    new_user_data = get_user(user_id)
    
    conn = sqlite3.connect('aelionx.db')
    c = conn.cursor()
    c.execute("INSERT INTO withdrawals (user_id, amount, paypal_email) VALUES (?, ?, ?)", (user_id, amount, paypal_email))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": f"Retrait de ${amount} demandé !", "new_balance": new_user_data[1]})

# --- 4. PAYPAL ---
def get_paypal_token():
    auth = base64.b64encode(f"{PAYPAL_CLIENT_ID}:{PAYPAL_SECRET}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"}
    r = requests.post(f"{PAYPAL_API_BASE}/v1/oauth2/token", headers=headers, data="grant_type=client_credentials")
    return r.json()['access_token']

@app.route('/pay/create', methods=['POST'])
def create_payment():
    try:
        if 'user_id' not in session: return jsonify({"error": "Veuillez vous connecter avec Epic Games d'abord."}), 403
        req_data = request.get_json(silent=True) or {}
        amount = req_data.get('amount', 10.0)
        token = get_paypal_token()
        
        order_data = {
            "intent": "CAPTURE",
            "purchase_units": [{"amount": {"currency_code": "USD", "value": str(amount)}, "description": f"{amount} Tokens ÆLIONX"}],
            "application_context": { "return_url": f"{DOMAIN_URL}/pay/success", "cancel_url": f"{DOMAIN_URL}/pay/cancel" }
        }
        r = requests.post(f"{PAYPAL_API_BASE}/v2/checkout/orders", headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"}, json=order_data)
        for link in r.json().get('links', []):
            if link['rel'] == "approve": return jsonify({"payment_url": link['href']})
        return jsonify({"error": "Erreur PayPal"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/pay/success')
def execute_payment():
    token = request.args.get('token')
    r = requests.post(f"{PAYPAL_API_BASE}/v2/checkout/orders/{token}/capture", headers={"Content-Type": "application/json", "Authorization": f"Bearer {get_paypal_token()}"})
    if r.json().get('status') == 'COMPLETED':
        amount_paid = float(r.json()['purchase_units'][0]['payments']['captures'][0]['amount']['value'])
        user_id = session.get('user_id')
        if user_id: add_tokens(user_id, amount_paid)
        return "Paiement réussi ! Les tokens ont été ajoutés à ton compte ÆLIONX. <a href='/'>Retour au site</a>"
    return "Erreur lors du paiement."

@app.route('/pay/cancel')
def cancel_payment():
    return "Paiement annulé. <a href='/'>Retour au site</a>"

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)