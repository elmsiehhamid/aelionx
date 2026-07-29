import os
from flask import Flask, render_template, request, redirect, session, jsonify, send_from_directory
import sqlite3
import requests
import base64

app = Flask(__name__, template_folder='.') # Cherche index.html dans le même dossier
app.secret_key = "9664c42e441bbd49bdc5ac31dc550bead16d49bffb395e5e185e2f72085a638d"

# ================= CONFIGURATION =================
# Ton nom de domaine en ligne sur Render
DOMAIN_URL = "https://aelionx.onrender.com" 

# 1. DISCORD OAUTH2 (Connexion des joueurs)
DISCORD_CLIENT_ID = "1531436256129450124"
DISCORD_CLIENT_SECRET = "0gJTf_NObmpCY6aaCnptq6RnDO8uuK7t3"
DISCORD_REDIRECT_URI = f"{DOMAIN_URL}/callback"

# 2. PAYPAL API (Passe en "https://api-m.paypal.com" avec tes clés Live pour l'argent réel)
PAYPAL_CLIENT_ID = "AZnJoA4KhCvofBX4gnAEoszq7U8WMPZaFCvuxNBP0f6iEWKhBT19d71tewTNJ4mZhsBjkGtWuBSV5n0G"
PAYPAL_SECRET = "EF5L9PIRmbiP2QkwmXQOfj5js4hE52zPCPdCUXf0mXFA43rgIo10ahOhQ-qcNPNU4ejsQRgF8uEJiH5i"
PAYPAL_API_BASE = "https://api-m.sandbox.paypal.com" # Remplace par https://api-m.paypal.com pour le mode réel

# ================= BASE DE DONNÉES =================
def init_db():
    conn = sqlite3.connect('aelionx.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (user_id TEXT PRIMARY KEY, tokens REAL, elo INTEGER, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, epic_id TEXT)''')
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect('aelionx.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    if not user:
        c.execute("INSERT OR IGNORE INTO users (user_id, tokens, elo, wins, losses, epic_id) VALUES (?, ?, ?, ?, ?, ?)", (user_id, 0.0, 0, 0, 0, None))
        conn.commit()
        user = (user_id, 0.0, 0, 0, 0, None)
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

# --- 1. CONNEXION DISCORD ---
@app.route('/login')
def login():
    discord_auth_url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&redirect_uri={DISCORD_REDIRECT_URI}&response_type=code&scope=identify"
    return redirect(discord_auth_url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    data = {
        'client_id': DISCORD_CLIENT_ID,
        'client_secret': DISCORD_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': DISCORD_REDIRECT_URI
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    r = requests.post('https://discord.com/api/oauth2/token', data=data, headers=headers)
    token_info = r.json()
    
    headers = {'Authorization': f"Bearer {token_info.get('access_token')}"}
    user_info = requests.get('https://discord.com/api/users/@me', headers=headers).json()
    
    session['user_id'] = user_info['id']
    session['username'] = user_info['username']
    
    get_user(user_info['id'])
    
    return redirect('/')

# --- 2. LIAISON EPIC GAMES ---
@app.route('/link/epic', methods=['POST'])
@app.route('/api/link/epic', methods=['POST'])
@app.route('/link-epic', methods=['POST'])
def link_epic():
    try:
        if 'user_id' not in session:
            return jsonify({"error": "Non connecté à Discord"}), 403

        req_data = request.get_json(silent=True) or {}
        epic_id = req_data.get('epic_id')
        
        if not epic_id:
            return jsonify({"error": "Pseudo Epic manquant"}), 400
            
        user_id = session['user_id']
        get_user(user_id)
        
        conn = sqlite3.connect('aelionx.db')
        c = conn.cursor()
        c.execute("UPDATE users SET epic_id = ? WHERE user_id = ?", (epic_id, user_id))
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "message": f"Compte Epic {epic_id} lié avec succès !"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- 3. PAIEMENT PAYPAL ---
def get_paypal_token():
    auth = base64.b64encode(f"{PAYPAL_CLIENT_ID}:{PAYPAL_SECRET}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"}
    r = requests.post(f"{PAYPAL_API_BASE}/v1/oauth2/token", headers=headers, data="grant_type=client_credentials")
    res_json = r.json()
    if 'access_token' not in res_json:
        raise Exception(f"Erreur d'authentification PayPal : {res_json}")
    return res_json['access_token']

@app.route('/pay/create', methods=['POST'])
def create_payment():
    try:
        if 'user_id' not in session:
            return jsonify({"error": "Non connecté à Discord"}), 403

        req_data = request.get_json(silent=True) or {}
        amount = req_data.get('amount', 10.0)
        
        token = get_paypal_token()
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        
        order_data = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "amount": {"currency_code": "USD", "value": str(amount)},
                "description": f"{amount} Tokens ÆLIONX pour {session['username']}"
            }],
            "application_context": {
                "return_url": f"{DOMAIN_URL}/pay/success",
                "cancel_url": f"{DOMAIN_URL}/pay/cancel"
            }
        }
        
        r = requests.post(f"{PAYPAL_API_BASE}/v2/checkout/orders", headers=headers, json=order_data)
        order = r.json()
        
        for link in order.get('links', []):
            if link['rel'] == "approve":
                return jsonify({"payment_url": link['href']})
                
        return jsonify({"error": "Erreur PayPal", "details": order}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/pay/success')
def execute_payment():
    token = request.args.get('token')
    access_token = get_paypal_token()
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    
    r = requests.post(f"{PAYPAL_API_BASE}/v2/checkout/orders/{token}/capture", headers=headers)
    result = r.json()
    
    if result.get('status') == 'COMPLETED':
        amount_paid = float(result['purchase_units'][0]['payments']['captures'][0]['amount']['value'])
        user_id = session.get('user_id')
        
        if user_id:
            add_tokens(user_id, amount_paid)
        
        return "Paiement réussi ! Les tokens ont été ajoutés à ton compte ÆLIONX. <a href='/'>Retour au site</a>"
    else:
        return "Erreur lors du paiement. La transaction n'a pas pu être validée."

@app.route('/pay/cancel')
def cancel_payment():
    return "Paiement annulé. <a href='/'>Retour au site</a>"

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)