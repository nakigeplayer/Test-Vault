import os
import json
import threading
from flask import Flask, send_from_directory, abort, render_template_string, redirect, request, session
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

# --- Configuración ---
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
STORAGE_LIMIT_MB = int(os.getenv("STORAGE_LIMIT_MB", 1000))
FILE_DURATION_MIN = int(os.getenv("FILE_DURATION_MIN", 20))
INSTANCE = int(os.getenv("INSTANCE", 1))
TOTAL_INSTANCES = int(os.getenv("TOTAL_INSTANCES", 1))
RENDER_APP_NAME = os.getenv("RENDER_APP_NAME", "tu_app")
BASE_URL = f"https://{RENDER_APP_NAME}.onrender.com"

VAULT_FOLDER = "vault"
storage_path = "storage_map.json"
active_files = {}

# --- Utilidades ---
def load_storage_map():
    if os.path.exists(storage_path):
        with open(storage_path, "r") as f:
            return json.load(f)
    return {str(i): 0.0 for i in range(1, TOTAL_INSTANCES + 1)}

def save_storage_map(data):
    with open(storage_path, "w") as f:
        json.dump(data, f)

def decide_instance(size_mb):
    usage = load_storage_map()
    for i in range(1, TOTAL_INSTANCES + 1):
        if usage.get(str(i), 0.0) + size_mb <= STORAGE_LIMIT_MB:
            return i
    return 1

def get_info(msg: Message):
    media = next((m for m in [msg.document, msg.photo, msg.audio, msg.video, msg.voice, msg.animation, msg.sticker] if m), None)
    fname = getattr(media, "file_name", None) or media.file_id if media else None
    fid = media.file_id if media else None
    size = getattr(media, "file_size", 0) / (1024 * 1024) if media else 0.0
    return fname, fid, size

# --- Web ---
web_app = Flask(__name__)
web_app.secret_key = os.getenv("SECRET_KEY", "clave_segura")

def login_required(f):
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect("/login")
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

@web_app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username")
        p = request.form.get("password")
        if u == os.getenv("ADMIN_USER") and p == os.getenv("ADMIN_PASS"):
            session["logged_in"] = True
            return redirect("/vault/")
        return "Credenciales incorrectas", 403
    return render_template_string("""
    <form method="post">
      <input name="username" placeholder="Usuario"><br>
      <input type="password" name="password" placeholder="Contraseña"><br>
      <input type="submit" value="Ingresar">
    </form>
    """)

@web_app.route("/vault/")
@login_required
def index():
    try:
        users = os.listdir(VAULT_FOLDER)
        links = [f"<li><a href='/vault/{uid}/'>{uid}</a></li>" for uid in users]
        return render_template_string(f"<h2>Instancia {INSTANCE}</h2><ul>" + "".join(links) + "</ul>")
    except FileNotFoundError:
        return "No hay archivos almacenados."

@web_app.route("/vault/<user>/")
@login_required
def user_files(user):
    path = os.path.join(VAULT_FOLDER, user)
    if not os.path.exists(path):
        return "Usuario no encontrado.", 404
    files = os.listdir(path)
    items = []
    for f in files:
        fpath = os.path.join(path, f)
        size = os.path.getsize(fpath) / (1024 * 1024)
        items.append(f"<li>{f} ({round(size,2)} MB) <a href='/vault/{user}/{f}'>Descargar</a></li>")
    return render_template_string(f"<h3>Archivos de {user}</h3><ul>" + "".join(items) + "</ul>")

@web_app.route("/vault/<user>/<filename>")
def serve(user, filename):
    filename = secure_filename(filename)
    return send_from_directory(os.path.join(VAULT_FOLDER, user), filename)

@web_app.errorhandler(404)
def not_found(e):
    return "🛑 Archivo no encontrado", 404

# --- Bots ---
bot_app = Client("vault", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
bot_app_instance = Client("vault_instance", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@bot_app.on_message(filters.media)
async def receive_media(client, message):
    if INSTANCE != 1:
        return
    user_id = message.from_user.id
    fname, fid, size_mb = get_info(message)
    if not fname:
        await message.reply("No pude identificar el archivo.")
        return
    target = decide_instance(size_mb)
    usage = load_storage_map()
    usage[str(target)] += size_mb
    save_storage_map(usage)
    msg = f"/up {target} {FILE_DURATION_MIN} {user_id}"
    await message.reply(msg, quote=True)

@bot_app_instance.on_message(filters.command("up"))
async def handle_up_command(client: Client, message: Message):
    if not message.reply_to_message or not message.reply_to_message.media:
        await message.reply("❌ Este comando debe responder a un archivo.")
        return
    try:
        _, inst, mins, uid = message.text.split()
        inst = int(inst)
        mins = int(mins)
        user_id = uid
    except ValueError:
        await message.reply("⚠️ Uso incorrecto. Formato: /up <instancia> <minutos> <user_id>")
        return
    if inst != INSTANCE:
        return
    fname, fid, size_mb = get_info(message.reply_to_message)
    path = os.path.join(VAULT_FOLDER, user_id, secure_filename(fname))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    await client.download_media(message.reply_to_message, path)
    active_files[fid] = (fname, user_id, size_mb)
    usage = load_storage_map()
    usage[str(INSTANCE)] += size_mb
    save_storage_map(usage)
    link = f"{BASE_URL}/vault/{user_id}/{secure_filename(fname)}"
    await client.send_message(int(user_id), f"✅ Tu archivo está en Instancia {INSTANCE}. Descárgalo aquí:\n{link}")

@bot_app_instance.on_message(filters.command("clear"))
async def clear(client, message):
    user_id = str(message.from_user.id)
    folder = os.path.join(VAULT_FOLDER, user_id)
    if not os.path.exists(folder):
        await message.reply("No tienes archivos.")
        return
    freed = 0.0
    for f in os.listdir(folder):
        fpath = os.path.join(folder, f)
        try:
            freed += os.path.getsize(fpath) / (1024 * 1024)
            os.remove(fpath)
        except: continue
    try: os.rmdir(folder)
    except: pass
    usage = load_storage_map()
    usage[str(INSTANCE)] = max(0.0, usage.get(str(INSTANCE), 0.0) - freed)
    save_storage_map(usage)
    await message.reply(f"🧹 {round(freed,2)} MB borrados en la Instancia {INSTANCE}")

# --- Ejecutar ---
def run_flask():
    web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

import asyncio
import threading

def run_bot(bot_instance):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_instance.run()

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    threading.Thread(target=lambda: run_bot(bot_app)).start()
    threading.Thread(target=lambda: run_bot(bot_app_instance)).start()
