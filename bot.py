import os
import asyncio
import threading
import json
import re
from flask import Flask, send_from_directory, abort, render_template_string, redirect, request, session
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv

load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

STORAGE_LIMIT_MB = int(os.getenv("STORAGE_LIMIT_MB", 1000))
FILE_DURATION_MIN = int(os.getenv("FILE_DURATION_MIN", 20))
VAULT_FOLDER = "vault"
RENDER_APP_NAME = os.getenv("RENDER_APP_NAME", "tu_app")
BASE_URL = f"https://{RENDER_APP_NAME}.onrender.com"

INSTANCE = int(os.getenv("INSTANCE", 1))
TOTAL_INSTANCES = int(os.getenv("TOTAL_INSTANCES", 1))

total_storage_usage = 0.0
active_files = {}
download_counter = 1
download_map = {}

instance_storage_path = "storage_map.json"

def load_storage_map():
    if os.path.exists(instance_storage_path):
        with open(instance_storage_path, "r") as f:
            return json.load(f)
    return {str(i): 0.0 for i in range(1, TOTAL_INSTANCES + 1)}

def save_storage_map(map_data):
    with open(instance_storage_path, "w") as f:
        json.dump(map_data, f)

# --- Flask ---
web_app = Flask(__name__)
web_app.secret_key = os.getenv("SECRET_KEY", "clave_super_segura")

@web_app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == os.getenv("ADMIN_USER") and password == os.getenv("ADMIN_PASS"):
            session["logged_in"] = True
            return redirect("/vault/")
        else:
            return "Credenciales incorrectas.", 403
    return render_template_string("""
    <form method="post">
      <input name="username" placeholder="Usuario"><br>
      <input type="password" name="password" placeholder="Contraseña"><br>
      <input type="submit" value="Ingresar">
    </form>
    """)

def login_required(f):
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect("/login")
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

@web_app.route("/vault/")
@login_required
def vault_index():
    try:
        users = os.listdir(VAULT_FOLDER)
        links = [f"<li><a href='/vault/{uid}/'>{uid}</a></li>" for uid in users]
        return render_template_string("<h2>Usuarios disponibles:</h2><ul>" + "".join(links) + "</ul>")
    except FileNotFoundError:
        return "No hay archivos almacenados."

@web_app.route("/vault/<user_id>/")
@login_required
def user_vault(user_id):
    user_path = os.path.join(VAULT_FOLDER, user_id)
    if not os.path.exists(user_path):
        abort(404)
    files = os.listdir(user_path)
    items = []
    for f in files:
        file_path = os.path.join(user_path, f)
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        items.append(f"<li>{f} - {round(size_mb, 2)} MB "
                     f"<a href='/vault/{user_id}/{f}'>[Descargar]</a> "
                     f"<a href='/delete/{user_id}/{f}'>🗑️ Eliminar</a></li>")
    return render_template_string(f"<h2>Archivos de usuario {user_id}:</h2><ul>" + "".join(items) + "</ul>")

@web_app.route("/vault/<user_id>/<filename>")
def serve_file(user_id, filename):
    dir_path = os.path.join(VAULT_FOLDER, user_id)
    return send_from_directory(dir_path, filename)

@web_app.errorhandler(404)
def not_found_error(e):
    return "🛑 El archivo no existe o fue eliminado.", 404

@login_required
@web_app.route("/delete/<user_id>/<filename>")
def delete_file(user_id, filename):
    try:
        file_path = os.path.join(VAULT_FOLDER, user_id, filename)
        if os.path.exists(file_path):
            os.remove(file_path)

        file_id = next((fid for fid, (fname, uid, _) in active_files.items()
                        if fname == filename and uid == user_id), None)

        if file_id:
            _, _, size_mb = active_files.pop(file_id, (None, None, 0.0))
            global total_storage_usage
            total_storage_usage = max(0.0, total_storage_usage - size_mb)

        safe_notify(int(user_id), f"🗑️ Archivo '{filename}' eliminado manualmente.")
        return "✅ Archivo eliminado satisfactoriamente."
    except Exception as e:
        print("Error al eliminar:", e)
        return "✅ Archivo eliminado.", 200

def safe_notify(chat_id, msg):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(bot_app.send_message(chat_id=chat_id, text=msg))
        else:
            loop.run_until_complete(bot_app.send_message(chat_id=chat_id, text=msg))
    except Exception as e:
        print("No se pudo notificar al usuario:", e)

# --- Pyrogram ---
bot_app = Client("vault_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

def get_file_info(message: Message):
    media = next((m for m in [message.document, message.photo, message.audio,
                              message.video, message.voice, message.animation,
                              message.sticker] if m), None)
    filename = getattr(media, "file_name", None) or media.file_id if media else None
    size_mb = getattr(media, "file_size", 0) / (1024 * 1024) if media else 0.0
    return filename, media.file_id if media else None, size_mb

def decide_instance(size_mb):
    usage_map = load_storage_map()
    for i in range(1, TOTAL_INSTANCES + 1):
        if usage_map.get(str(i), 0.0) + size_mb <= STORAGE_LIMIT_MB:
            return i
    return 1

@bot_app.on_message(filters.media)
async def handle_media(client: Client, message: Message):
    if INSTANCE != 1:
        return

    filename, file_id, size_mb = get_file_info(message)
    if not filename:
        await message.reply("Archivo no identificado.")
        return

    target_instance = decide_instance(size_mb)
    usage_map = load_storage_map()
    usage_map[str(target_instance)] += size_mb
    save_storage_map(usage_map)

    reply_msg = f"Subiendo a la Instancia {target_instance} durante {FILE_DURATION_MIN} minutos"
    await message.reply(reply_msg, quote=True)

@bot_app.on_message(filters.text & filters.incoming)
async def handle_instance_message(client: Client, message: Message):
    match = re.search(r"Instancia (\d+)", message.text)
    if not match or int(match.group(1)) != INSTANCE:
        return

    original = message.reply_to_message
    if not original or not original.media:
        return

    filename, file_id, size_mb = get_file_info(original)
    user_id = str(original.from_user.id)
    file_path = os.path.join(VAULT_FOLDER, user_id, filename)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    await client.download_media(original, file_path)
    total_storage_usage += size_mb
    active_files[file_id] = (filename, user_id, size_mb)

@bot_app.on_message(filters.command("clear"))
async def clear_files(client: Client, message: Message):
    user_id = str(message.from_user.id)
    path = os.path.join(VAULT_FOLDER, user_id)

    if not os.path.exists(path):
        await message.reply("No tienes archivos.")
        return

    freed = 0.0
    for fname in os.listdir(path):
        fpath = os.path.join(path, fname)
        try:
            freed += os.path.getsize(fpath) / (1024 * 1024)
            os.remove(fpath)
        except: pass
    try:
        os.rmdir(path)
    except: pass

    usage_map = load_storage_map()
    usage_map[str(INSTANCE)] = max(0.0, usage_map.get(str(INSTANCE), 0.0) - freed)
    save_storage_map(usage_map)
    await message.reply(f"🧹 Archivos eliminados. Espacio liberado: {round(freed, 2)} MB en la Instancia {INSTANCE}")

# --- Ejecutar servicios ---
def run_flask():
    web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

if __name__ == "__main__":
    if INSTANCE == 1:
        threading.Thread(target=run_flask).start()
    bot_app.run()
