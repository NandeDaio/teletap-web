import time
import random
import requests
import threading
import json
import os
from datetime import datetime, timezone
from flask import Flask, jsonify, request
from flask_cors import CORS

from supabase import create_client, Client

from dotenv import load_dotenv

# Cargar variables de entorno (.env)
load_dotenv()

app = Flask(__name__)
# En producción, restringe los orígenes permitidos por seguridad
CORS(app, resources={r"/api/*": {"origins": "*"}})

# --- CONFIGURACIÓN SUPABASE ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_KEY or not SUPABASE_URL:
    print("❌ ERROR CRÍTICO: SUPABASE_URL o SUPABASE_KEY no encontrada en variables de entorno.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# --- CACHE EN MEMORIA PARA RENDIMIENTO ---
# Estructura: { "email": { (datos del usuario de Supabase) } }
local_cache = {}

def sync_from_db(email):
    """Obtiene los datos más recientes de Supabase para un usuario."""
    try:
        res = supabase.table("users").select("*").eq("email", email).execute()
        if res.data:
            local_cache[email] = res.data[0]
            return local_cache[email]
    except Exception as e:
        print(f"Error syncing from DB: {e}")
    return local_cache.get(email)

def save_to_db(email, data, force=True):
    """Guarda cambios del cache local en Supabase. Actualiza cache siempre, DB solo si force=True."""
    # 1. Actualizar cache local SIEMPRE para que la UI sea reactiva
    user = local_cache.get(email)
    if user and isinstance(user, dict):
        user.update(data)
    
    # 2. Si no es forzado, no hacemos nada más (throttling)
    if not force:
        return

    # 3. Guardar en Supabase
    try:
        supabase.table("users").update(data).eq("email", email).execute()
    except Exception as e:
        print(f"Error saving to DB: {e}")


def log_message(email, bot_type, message):
    user = local_cache.get(email)
    if not user or not isinstance(user, dict): return
    
    log_key = f"{bot_type}_logs"
    logs = user.get(log_key, [])
    if not isinstance(logs, list): logs = []
    
    timestamp = datetime.now().strftime("%H:%M:%S")
    msg_formatted = f"[{timestamp}] {message}"
    logs.append(msg_formatted)
    
    # Debugging: Imprimir en consola para ver actividad en Render Logs
    print(f"DEBUG: [{email}] [{bot_type}] {message}")
    
    logs = logs[-20:] # Mantener últimos 20
    
    # Guardar en Cache SIEMPRE, en DB solo cada pocos segundos para evitar [Errno 11]
    user[log_key] = logs
    
    # Throttling: Guardar logs en Supabase solo si pasaron 5 segundos desde el último log guardado
    last_log_save = user.get(f"last_log_save_{bot_type}", 0)
    if time.time() - last_log_save > 5:
        save_to_db(email, {log_key: logs})
        user[f"last_log_save_{bot_type}"] = time.time()


# --- REGISTRO DE HILOS ---
# Estructura: { "email_chainer": Thread, "email_roller": Thread }
active_bots = {}


# =========================================================================
# 🤖 LÓGICA DE LOS BOTS (MULTIUSUARIO)
# =========================================================================

# --- MAPA DE VIDA DEL BOSS ---
BOSS_HP_MAP = {
    "level1": 0, "level2": 2000, "level3": 200000, "level4": 2000000,
    "level5": 200000000, "level6": 2000000000, "level7": 20000000000,
    "level8": 200000000000, "level9": 2000000000000, "level10": 20000000000000
}

def calculate_boss_hp(source):
    # Intentar obtener el nivel de varias fuentes posibles
    level_code = (source.get("profileProgressionsCode") or 
                  source.get("level") or 
                  "level5")
    
    # Intentar obtener el daño realizado
    damage_done = int(source.get("bossDamageForCurrentLevel") or 
                      source.get("damage") or 0)
    
    # Normalizar el level_code a minúsculas para el mapa
    level_key = str(level_code).lower()
    max_hp = BOSS_HP_MAP.get(level_key, 200000000)
    
    # Debug para ver qué está pasando
    # print(f"DEBUG_HP: level={level_key}, damage={damage_done}, max_hp={max_hp}")
    
    return int(max(0, max_hp - damage_done))

# --- NIVELES ROLLERBOT ---
ROLLER_LEVEL_TARGETS = {
    "level1": 1000,
    "level2": 100000,
    "level3": 1000000,
    "level4": 10000000,
    "level5": 100000000,
    "level6": 1000000000,
    "level7": 10000000000,
    "level8": 100000000000,
    "level9": 1000000000000,
    "level10": 1000000000000
}

def parse_recharge_time(date_val):
    if not date_val: return 0
    try:
        # Si es un timestamp numérico (ms o s)
        if isinstance(date_val, (int, float)):
            if date_val > 2000000000: date_val /= 1000 # de ms a s
            diff = date_val - time.time()
            return int(max(0, diff))
            
        # Si es un string ISO
        if isinstance(date_val, str):
            clean_date = date_val.replace("Z", "+00:00")
            target = datetime.fromisoformat(clean_date)
            now = datetime.now(timezone.utc)
            diff = (target - now).total_seconds()
            return int(max(0, diff))
    except:
        pass
    return 0

def chainer_sync_loop(email, headers, profile_url, balance_url):
    while local_cache.get(email, {}).get("chainer_running"):
        try:
            resp_p = requests.get(profile_url, headers=headers, timeout=10)
            if resp_p.status_code == 200:
                p_data = resp_p.json().get("data", {})
                source = p_data.get("gameProfile", p_data)
                
                val_total = (source.get("totalEnergyCount") or source.get("energyLimit") or source.get("maxEnergy") or
                             p_data.get("userData", {}).get("energyLimit") or p_data.get("userData", {}).get("totalEnergyCount") or
                             p_data.get("totalEnergyCount") or 0)
                
                update_data = {
                    "chainer_energy": int(source.get("activeEnergyCount") or 0),
                    "chainer_max_energy": int(val_total),
                    "chainer_energy_per_tap": int(source.get("energyPerTap") or 1),
                    "chainer_recharges": int(
                        (source.get("totalEnergyRechargeCount", 6) - (source.get("energyRechargeCount") or 0)) if "energyRechargeCount" in source else
                        (source.get("dailyEnergyRechargeLimit", 6) - (source.get("dailyEnergyRechargeUsed") or 0)) if "dailyEnergyRechargeUsed" in source else
                        source.get("energyRechargeCount") or source.get("rechargeEnergyCount") or source.get("recharges") or 
                        source.get("dailyRechargeCount") or p_data.get("userData", {}).get("energyRechargeCount") or 0
                    ),
                    "chainer_recharge_at": int(time.time() + parse_recharge_time(source.get("nextEnergyRechargeDate") or source.get("rechargeEnergyAt") or source.get("rechargeAt"))),
                    "chainer_level": (lambda val: int(str(val).replace("level", "")) if val else 1)(
                                        source.get("profileProgressionsCode") or p_data.get("profileProgressionsCode") or 
                                        p_data.get("userData", {}).get("profileProgressionsCode") or
                                        p_data.get("playerLevel") or p_data.get("userData", {}).get("playerLevel") or 
                                        source.get("playerLevel") or p_data.get("level") or source.get("level")
                                    ),
                    "chainer_boss_hp": calculate_boss_hp(source)
                }
                save_to_db(email, update_data)

            elif resp_p.status_code in [401, 403]:
                log_message(email, "chainer", f"❌ ERROR {resp_p.status_code}: Fin sesión.")
                save_to_db(email, {"chainer_running": False})
                break

            resp_b = requests.get(balance_url, headers=headers, timeout=10)
            if resp_b.status_code == 200:
                save_to_db(email, {"chainer_balance": resp_b.json().get("data", {}).get("balance", 0)})
            
            time.sleep(1.5)
        except Exception as e:
            time.sleep(2)

def chainer_tap_loop(email, headers, collect_url, recharge_url):
    while True:
        user = local_cache.get(email)
        if not user or not user.get("chainer_running"):
            break
            
        try:
            curr_energy = int(user.get("chainer_energy", 0))
            total_energy = int(user.get("chainer_max_energy", 0))
            energy_per_tap = int(user.get("chainer_energy_per_tap", 1))
            recharge_at = user.get("chainer_recharge_at", 0)
            recharge_ready = int(user.get("chainer_recharges", 0)) > 0 or (recharge_at > 0 and time.time() >= recharge_at)
            rest_until = float(user.get("chainer_rest_until", 0))

            if time.time() < rest_until:
                time.sleep(5); continue

            # Verificar si energía está baja
            if curr_energy < 20:
                # PRIORIDAD: Antes de descansar, verificar si hay recarga disponible (Sincronizar para estar seguros)
                user = sync_from_db(email)
                recharges = int(user.get("chainer_recharges", 0))
                recharge_at = int(user.get("chainer_recharge_at", 0))
                recharge_ready = recharges > 0 or (recharge_at > 0 and time.time() >= recharge_at)

                if recharge_ready:
                    log_message(email, "chainer", "⚡ Recarga disponible. Priorizando antes de descansar.")
                    if requests.post(recharge_url, json={}, headers=headers, timeout=10).status_code <= 201:
                        user["chainer_recharges_done"] = user.get("chainer_recharges_done", 0) + 1
                        save_to_db(email, {"chainer_recharges_done": user["chainer_recharges_done"]})
                    time.sleep(2); continue
                
                # Si no hay recargos, ahora sí descansar
                if time.time() > rest_until:
                    target_rest = time.time() + 600
                    save_to_db(email, {"chainer_rest_until": target_rest})
                    log_message(email, "chainer", "💤 Energía baja. Descansando 10 min...")
                    time.sleep(5); continue

            
            if curr_energy < 100 and recharge_ready:
                log_message(email, "chainer", "⚡ Auto-Recarga activada.")
                if requests.post(recharge_url, json={}, headers=headers, timeout=10).status_code <= 201:
                    user["chainer_recharges_done"] = user.get("chainer_recharges_done", 0) + 1
                    save_to_db(email, {"chainer_recharges_done": user["chainer_recharges_done"]})
                time.sleep(2); continue

            # 2. Taps asíncronos
            if curr_energy >= energy_per_tap:
                resp = requests.post(collect_url, json={"tapsCount": energy_per_tap}, headers=headers, timeout=10)
                if resp.status_code == 200:
                    new_energy = max(0, curr_energy - energy_per_tap)
                    is_turbo = curr_energy > (total_energy * 0.3) if total_energy > 0 else False
                    
                    # Throttling inteligente: Guardar en DB solo cada 5 segundos si está en Turbo
                    last_save = user.get("last_db_save_chainer", 0)
                    should_save_db = (not is_turbo) or (time.time() - last_save > 5)
                    
                    save_to_db(email, {"chainer_energy": new_energy}, force=should_save_db)
                    if should_save_db: user["last_db_save_chainer"] = time.time()
                    
                    log_message(email, "chainer", f"{'🚀 [TURBO]' if is_turbo else '✅'} Taps enviados ({energy_per_tap})")
                
                wait = random.uniform(1.5, 3.5)
                if total_energy > 0 and curr_energy > (total_energy * 0.3): wait *= 0.1
                time.sleep(wait)
            else:
                time.sleep(2)
        except Exception as e:
            time.sleep(5)

def chainer_worker(email, token):
    log_message(email, "chainer", "🚀 Iniciando hilos...")
    final_token = token.replace("Bearer ", "").strip()
    headers = {
        "Authorization": f"Bearer {final_token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0",
        "Origin": "https://tapp.chainers.app",
        "Referer": "https://tapp.chainers.app/"
    }
    
    p_url = "https://tapp.chainers.app/api/auth/user-data"
    b_url = "https://tapp.chainers.app/api/game/balance"
    c_url = "https://tapp.chainers.app/api/game/collect-token"
    r_url = "https://tapp.chainers.app/api/game/recharge-energy"

    # Evitar duplicados
    if f"{email}_chainer" in active_bots:
        return
    
    t1 = threading.Thread(target=chainer_sync_loop, args=(email, headers, p_url, b_url), daemon=True)
    t2 = threading.Thread(target=chainer_tap_loop, args=(email, headers, c_url, r_url), daemon=True)
    
    active_bots[f"{email}_chainer"] = t1
    t1.start()
    t2.start()


def roller_sync_loop(email, headers, profile_url, balance_url):
    while local_cache.get(email, {}).get("roller_running"):
        try:
            resp_p = requests.get(profile_url, headers=headers, timeout=10)
            if resp_p.status_code == 200:
                p_data = resp_p.json().get("data", {})
                source = p_data.get("gameProfile", p_data)
                
                val_total = (source.get("totalEnergyCount") or source.get("energyLimit") or source.get("maxEnergy") or
                             p_data.get("userData", {}).get("energyLimit") or p_data.get("userData", {}).get("totalEnergyCount") or
                             p_data.get("totalEnergyCount") or 0)
                
                update_data = {
                    "roller_energy": int(source.get("activeEnergyCount") or 0),
                    "roller_max_energy": int(val_total),
                    "roller_energy_per_tap": int(source.get("energyPerTap") or 1),
                    "roller_recharges": source.get("energyRechargeCount") or source.get("rechargeEnergyCount") or source.get("recharges") or source.get("dailyRechargeCount") or 0,
                    "roller_recharge_at": int(time.time() + parse_recharge_time(source.get("nextEnergyRechargeDate") or source.get("energyRechargeDate") or source.get("rechargeAt") or source.get("next_recharge_at"))),
                    "roller_level_progress": source.get("levelProgress", 0)
                }
                
                # Extraer nivel y HP del boss
                level_code = source.get("profileProgressionsCode") or p_data.get("profileProgressionsCode") or p_data.get("userData", {}).get("profileProgressionsCode") or "level1"
                update_data["roller_level_required"] = ROLLER_LEVEL_TARGETS.get(level_code, "-")
                update_data["roller_level"] = int(level_code.replace("level", "")) if "level" in str(level_code) else 1
                update_data["roller_boss_hp"] = calculate_boss_hp(source)
                
                save_to_db(email, update_data)
                
            elif resp_p.status_code in [401, 403]:
                log_message(email, "roller", f"❌ ERROR {resp_p.status_code}: Fin sesión Roller.")
                save_to_db(email, {"roller_running": False})
                break

            resp_b = requests.get(balance_url, headers=headers, timeout=10)
            if resp_b.status_code == 200:
                save_to_db(email, {"roller_balance": resp_b.json().get("data", {}).get("balance", 0)})

            time.sleep(1.5)
        except:
            time.sleep(2)

def roller_tap_loop(email, headers, collect_url, recharge_url):
    while True:
        user = local_cache.get(email)
        if not user or not user.get("roller_running"):
            break

        try:
            curr_energy = int(user.get("roller_energy", 0))
            total_energy = int(user.get("roller_max_energy", 0))
            energy_per_tap = int(user.get("roller_energy_per_tap", 1))
            recharge_at = user.get("roller_recharge_at", 0)
            recharge_ready = int(user.get("roller_recharges", 0)) > 0 or (recharge_at > 0 and time.time() >= recharge_at)
            rest_until = float(user.get("roller_rest_until", 0))

            if time.time() < rest_until:
                time.sleep(5); continue

            if curr_energy < 20:
                # PRIORIDAD: Antes de descansar, verificar si hay recarga disponible
                user = sync_from_db(email)
                recharges = int(user.get("roller_recharges", 0))
                recharge_at = int(user.get("roller_recharge_at", 0))
                recharge_ready = recharges > 0 or (recharge_at > 0 and time.time() >= recharge_at)

                if recharge_ready:
                    log_message(email, "roller", "⚡ Recarga disponible. Priorizando antes de descansar.")
                    if requests.post(recharge_url, json={}, headers=headers, timeout=10).status_code <= 201:
                        user["roller_recharges_done"] = user.get("roller_recharges_done", 0) + 1
                        save_to_db(email, {"roller_recharges_done": user["roller_recharges_done"]})
                    time.sleep(2); continue
                
                if time.time() > rest_until:
                    target_rest = time.time() + 600
                    save_to_db(email, {"roller_rest_until": target_rest})
                    log_message(email, "roller", "💤 Energía baja. Descansando 10 min...")
                    time.sleep(5); continue


            if curr_energy >= energy_per_tap:
                resp = requests.post(collect_url, json={"tapsCount": energy_per_tap}, headers=headers, timeout=10)
                if resp.status_code == 200:
                    new_energy = max(0, curr_energy - energy_per_tap)
                    is_turbo = curr_energy > (total_energy * 0.3) if total_energy > 0 else False
                    
                    # Throttling inteligente: Guardar en DB solo cada 5 segundos si está en Turbo
                    last_save = user.get("last_db_save_roller", 0)
                    should_save_db = (not is_turbo) or (time.time() - last_save > 5)
                    
                    save_to_db(email, {"roller_energy": new_energy}, force=should_save_db)
                    if should_save_db: user["last_db_save_roller"] = time.time()
                    
                    log_message(email, "roller", f"{'🚀 [TURBO]' if is_turbo else '✅'} Taps enviados ({energy_per_tap})")
                
                wait = random.uniform(1.5, 3.5)
                if total_energy > 0 and curr_energy > (total_energy * 0.3): wait *= 0.1
                time.sleep(wait)
            else:
                time.sleep(2)
        except Exception as e:
            time.sleep(5)

def roller_worker(email, token):
    log_message(email, "roller", "🚀 Iniciando hilos...")
    final_token = token.replace("Bearer ", "").strip()
    headers = {
        "Authorization": f"Bearer {final_token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0",
        "Origin": "https://tapp.rlr.app",
        "Referer": "https://tapp.rlr.app/"
    }
    
    p_url = "https://tapp.rlr.app/api/auth/user-data"
    b_url = "https://tapp.rlr.app/api/game/balance"
    c_url = "https://tapp.rlr.app/api/game/collect-token"
    r_url = "https://tapp.rlr.app/api/game/recharge-energy"

    # Evitar duplicados
    if f"{email}_roller" in active_bots:
        return

    t1 = threading.Thread(target=roller_sync_loop, args=(email, headers, p_url, b_url), daemon=True)
    t2 = threading.Thread(target=roller_tap_loop, args=(email, headers, c_url, r_url), daemon=True)
    
    active_bots[f"{email}_roller"] = t1
    t1.start()
    t2.start()


# =========================================================================
# 🌐 API ENDPOINTS
# =========================================================================

@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    email = data.get("email")
    password = data.get("password")
    
    user = sync_from_db(email)
    if user and user["password"] == password:
        return jsonify({
            "success": True,
            "user": {
                "email": email,
                "sub_chainer": user["sub_chainer"],
                "sub_roller": user["sub_roller"],
                "token_chainer": user["token_chainer"],
                "token_roller": user["token_roller"],
                "chainer_running": user["chainer_running"],
                "roller_running": user["roller_running"]
            }
        })
    return jsonify({"success": False, "message": "Credenciales inválidas"}), 401

@app.route('/api/update_token', methods=['POST'])
def update_token():
    data = request.json
    email = data.get("email")
    bot_type = data.get("bot_type")
    token = data.get("token")
    
    user = local_cache.get(email)
    if user:
        token_key = f"token_{bot_type}"
        save_to_db(email, {token_key: token})
        log_message(email, bot_type, "✅ Token actualizado y guardado.")
        return jsonify({"success": True, "message": "Token actualizado"})
    return jsonify({"success": False, "message": "Usuario no encontrado"}), 404

@app.route("/api/toggle_bot", methods=["POST"])
def toggle_bot():
    data = request.json
    email = data.get("email")
    bot_type = data.get("type")
    
    user = sync_from_db(email)
    if not user: return jsonify({"success": False}), 404
    
    if bot_type == "chainer" and not user["sub_chainer"]:
        return jsonify({"success": False, "message": "Requiere suscripción Chainers"}), 403
    if bot_type == "roller" and not user["sub_roller"]:
        return jsonify({"success": False, "message": "Requiere suscripción Roller"}), 403

    field = f"{bot_type}_running"
    state_to_set = not user[field]
    
    update_data = {field: state_to_set}
    if state_to_set:
        update_data[f"{bot_type}_start_time"] = time.time()
        update_data[f"{bot_type}_rest_until"] = 0
        
        token = user[f"token_{bot_type}"]
        if not token or "PONER_AQUÍ" in token:
            log_message(email, bot_type, "⚠️ ERROR: Token vacío o de ejemplo")
            return jsonify({"success": False, "message": "Debes poner un token real"}), 400
            
        save_to_db(email, update_data)
        worker_func = chainer_worker if bot_type == "chainer" else roller_worker
        threading.Thread(target=worker_func, args=(email, token), daemon=True).start()
    else:
        update_data = {f"{bot_type}_running": False}
        save_to_db(email, update_data)
        
        # ELIMINAR DE ACTIVE_BOTS PARA PERMITIR REINICIO
        if f"{email}_{bot_type}" in active_bots:
            del active_bots[f"{email}_{bot_type}"]
            
        log_message(email, bot_type, "⏹️ Deteniendo bot...")
        
    return jsonify({"success": True, "running": state_to_set})

@app.route("/api/status", methods=["GET"])
def get_user_status():
    email = request.args.get("email")
    user = local_cache.get(email) or sync_from_db(email)
    if user:
        return jsonify({
            "success": True,
            "chainer_running": user.get("chainer_running"),
            "roller_running": user.get("roller_running"),
            "chainer_balance": user.get("chainer_balance", 0),
            "chainer_energy": user.get("chainer_energy", 0),
            "chainer_max_energy": user.get("chainer_max_energy", "-"),
            "chainer_recharges": user.get("chainer_recharges", 0),
            "chainer_recharge_at": user.get("chainer_recharge_at", 0),
            "roller_balance": user.get("roller_balance", 0),
            "roller_energy": user.get("roller_energy", 0),
            "roller_max_energy": user.get("roller_max_energy", "-"),
            "roller_recharges": user.get("roller_recharges", 0),
            "roller_recharge_at": user.get("roller_recharge_at", 0),
            "roller_level_progress": user.get("roller_level_progress", 0),
            "roller_level_required": user.get("roller_level_required", "-"),
            "sub_chainer": user.get("sub_chainer"),
            "sub_roller": user.get("sub_roller"),
            "chainer_logs": user.get("chainer_logs", []),
            "roller_logs": user.get("roller_logs", []),
            "chainer_start_time": user.get("chainer_start_time", 0),
            "roller_start_time": user.get("roller_start_time", 0),
            "chainer_rest_until": user.get("chainer_rest_until", 0),
            "roller_rest_until": user.get("roller_rest_until", 0),
            "chainer_level": user.get("chainer_level", 1),
            "roller_level": user.get("roller_level", 1),
            "chainer_boss_hp": user.get("chainer_boss_hp", 0),
            "roller_boss_hp": user.get("roller_boss_hp", 0)
        })
    return jsonify({"success": False}), 404

@app.route("/api/submit_payment", methods=["POST"])
def submit_payment():
    data = request.json
    email = data.get("email")
    plan = data.get("plan")
    txid = data.get("txid")
    
    if email in local_cache or sync_from_db(email):
        try:
            supabase.table("payments").insert({
                "email": email,
                "plan": plan,
                "txid": txid,
                "status": "pending"
            }).execute()
            log_message(email, "chainer", f"💰 Pago enviado para verificación (TXID: {txid[:10]}...)")
            return jsonify({"success": True, "message": "Pago enviado para verificación"})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({"success": False}), 404

@app.route("/api/buy_sub", methods=["POST"])
def buy_sub():
    data = request.json
    email = data.get("email")
    plan = data.get("plan")
    
    user = local_cache.get(email)
    if user:
        update_fields = {}
        if plan == "chainer": update_fields["sub_chainer"] = True
        elif plan == "roller": update_fields["sub_roller"] = True
        elif plan == "both":
            update_fields["sub_chainer"] = True
            update_fields["sub_roller"] = True
        
        save_to_db(email, update_fields)
        return jsonify({"success": True})
    return jsonify({"success": False}), 404

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "time": time.time()})

def resume_active_bots():
    """Busca usuarios que tenían bots encendidos y los reinicia al arrancar el servidor."""
    print("🔍 Buscando bots activos para reanudar...")
    try:
        # Consultar usuarios con chainer o roller corriendo
        res = supabase.table("users").select("*").or_("chainer_running.eq.true,roller_running.eq.true").execute()
        if res.data:
            print(f"📦 Reanudando {len(res.data)} sesiones de bots...")
            for user in res.data:
                email = user["email"]
                local_cache[email] = user
                
                # Reanudar Chainer
                if user.get("chainer_running") and user.get("token_chainer"):
                    threading.Thread(target=chainer_worker, args=(email, user["token_chainer"]), daemon=True).start()
                
                # Reanudar Roller
                if user.get("roller_running") and user.get("token_roller"):
                    threading.Thread(target=roller_worker, args=(email, user["token_roller"]), daemon=True).start()
        else:
            print("✅ No hay bots activos para reanudar.")
    except Exception as e:
        print(f"❌ Error al reanudar bots: {e}")

if __name__ == "__main__":
    # Iniciar la reanudación de bots en un hilo aparte para no bloquear el arranque
    threading.Thread(target=resume_active_bots, daemon=True).start()
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

