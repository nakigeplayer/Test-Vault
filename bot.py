import os
import asyncio
import threading
from flask import Flask, request, send_from_directory, render_template_string, redirect, abort
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv

load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
STORAGE_LIMIT_MB = 1000
VAULT_FOLDER = "vault"
FILE_DURATION_MIN = int(os.getenv("FILE_DURATION_MIN", 20))
RENDER_APP_NAME = os.getenv("RENDER_APP_NAME", "tu_app")
PASSWORD = os.getenv("PASSWORD", "admin")
BASE_URL = f"https://{RENDER_APP_NAME}.onrender.com"

total_storage_usage = 0.0
active_files = {}           # file_id: (filename, user_id, file_size_mb)
download_counter = 1        # 000001 - 999999
download_map = {}           # code: (user_id, filename)
authorized_ips = set()      # IPs que ya pasaron login

# --- Flask ---
web_app = Flask(__name__)

def human_size(path):
    try:
        size = os.path.getsize(path)
        return f"{round(size / 1024 / 1024, 2)} MB"
    except:
        return "?"

@web_app.route("/login/", methods=["GET", "POST"])
def login():
    ip = request.remote_addr
    if request.method == "POST":
        if request.form.get("password") == PASSWORD:
            authorized_ips.add(ip)
            return redirect("/vault/")
        return render_template_string("<h3>❌ Contraseña incorrecta</h3><a href='/login/'>Intentar de nuevo</a>")
    return render_template_string("""
        <h2>🔐 Acceso requerido</h2>
        <form method='post'>
        <input type='password' name='password' placeholder='Contraseña'>
        <button type='submit'>Entrar</button>
        </form>
    """)

def require_auth():
    if request.remote_addr not in authorized_ips:
        return redirect("/login/")

@web_app.route("/vault/")
def vault_index():
    auth = require_auth()
    if auth: return auth
    try:
        users = os.listdir(VAULT_FOLDER)
        links = [f"<li><a href='/vault/{uid}/'>{uid}</a></li>" for uid in users]
        return render_template_string("<h2>Usuarios disponibles:</h2><ul>" + "".join(links) + "</ul>")
    except FileNotFoundError:
        return "No hay archivos almacenados."

@web_app.route("/vault/<user_id>/")
def user_vault(user_id):
    auth = require_auth()
    if auth: return auth
    user_path = os.path.join(VAULT_FOLDER, user_id)
    if not os.path.exists(user_path):
        abort(404)

    files = os.listdir(user_path)
    links = ""
    for f in files:
        file_path = os.path.join(user_path, f)
        size = human_size(file_path)
        links += f"<li>{f} ({size}) &nbsp; <a href='/vault/{user_id}/{f}'>Descargar</a> &nbsp; <a href='/vault/{user_id}/{f}/delete'>🗑️ Borrar</a></li>"

    return render_template_string(f"<h2>Archivos de usuario {user_id}:</h2><ul>{links}</ul>")

@web_app.route("/vault/<user_id>/<filename>")
def serve_file(user_id, filename):
    auth = require_auth()
    if auth: return auth
    return send_from_directory(os.path.join(VAULT_FOLDER, user_id), filename)

@web_app.route("/vault/<user_id>/<filename>/delete")
def delete_file(user_id, filename):
    auth = require_auth()
    if auth: return auth
    path = os.path.join(VAULT_FOLDER, user_id, filename)
    try:
        size_mb = os.path.getsize(path) / (1024 * 1024)
        os.remove(path)
        global total_storage_usage
        total_storage_usage = max(0.0, total_storage_usage - size_mb)
        return redirect(f"/vault/{user_id}/")
    except:
        return "No se pudo borrar el archivo.", 500

@web_app.route("/download/<code>")
def direct_download(code):
    entry = download_map.get(code)
    if not entry:
        return "⚠️ Código inválido o expirado.", 404
    user_id, filename = entry
    file_path = os.path.join(VAULT_FOLDER, user_id, filename)
    if not os.path.exists(file_path):
        return "⚠️ Archivo no encontrado.", 404
    return send_from_directory(os.path.join(VAULT_FOLDER, user_id), filename, as_attachment=True, download_name=filename)

def run_flask():
    web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

# --- Pyrogram bot ---
bot_app = Client("vault_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

def get_file_info(message: Message):
    media, filename = None, None
    if message.document:
        media = message.document
        filename = media.file_name or media.file_id
    elif message.photo:
        media = message.photo
        filename = media.file_id
    elif message.audio:
        media = message.audio
        filename = media.file_name or media.file_id
    elif message.video:
        media = message.video
        filename = media.file_name or media.file_id
    elif message.voice:
        media = message.voice
        filename = media.file_id
    elif message.animation:
        media = message.animation
        filename = media.file_id
    elif message.sticker and message.sticker.file_size:
        media = message.sticker
        filename = media.file_id
    else:
        return None, None, 0.0

    size_mb = media.file_size / (1024 * 1024) if media.file_size else 0.0
    return filename, media.file_id, size_mb

def next_download_code():
    global download_counter
    code = f"{download_counter:06d}"
    download_counter = 1 if download_counter >= 999999 else download_counter + 1
    return code

@bot_app.on_message(filters.media)
async def handle_media(client: Client, message: Message):
    global total_storage_usage
    user_id = str(message.from_user.id)
    filename, file_id, file_size_mb = get_file_info(message)
    if not filename:
        await message.reply("No pude identificar el archivo.")
        return
    if total_storage_usage + file_size_mb > STORAGE_LIMIT_MB:
        await message.reply("No puedo almacenar más archivos ahora")
        return

    file_path = os.path.join(VAULT_FOLDER, user_id, filename)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    await client.download_media(message, file_path)
    total_storage_usage += file_size_mb
    active_files[file_id] = (filename, user_id, file_size_mb)

    code = next_download_code()
    download_map[code] = (user_id, filename)

    mirror_link = f"{BASE_URL}/download/{code}"
    await message.reply(f"Archivo guardado por {FILE_DURATION_MIN} minutos: [Descargar]({mirror_link})", disable_web_page_preview=True)

    asyncio.create_task(remove_file_later(client, message, file_id, file_path, code))

async def remove_file_later(client: Client, message: Message, file_id: str, path: str, code: str):
    global total_storage_usage
    await asyncio.sleep(FILE_DURATION_MIN * 60)
    if not os.path.exists(path):
        return
    try:
        os.remove(path)
    except:
        pass
    _, _, size_mb = active_files.pop(file_id, (None, None, 0.0))
    download_map.pop(code, None)
    total_storage_usage = max(0.0, total_storage_usage - size_mb)
    await message.reply("archivo borrado", quote=True)

@bot_app.on_message(filters.command("clear"))
async def clear_user_files(client: Client, message: Message):
    global total_storage_usage

    user_id = str(message.from_user.id)
    user_path = os.path.join(VAULT_FOLDER, user_id)

    if not os.path.exists(user_path):
        await message.reply("No tienes archivos almacenados.")
        return

    freed = 0.0
    for filename in os.listdir(user_path):
        try:
            path = os.path.join(user_path, filename)
            size_mb = os.path.getsize(path) / (1024 * 1024)
            os.remove(path)
            freed += size_mb
        except:
            continue

    try:
        os.rmdir(user_path)
    except:
        pass

    # Eliminar códigos de descarga vinculados a este usuario
    to_remove = [code for code, (uid, _) in download_map.items() if uid == user_id]
    for code in to_remove:
        download_map.pop(code)

    total_storage_usage = max(0.0, total_storage_usage - freed)
    await message.reply(f"🧹 Archivos eliminados. Espacio liberado: {round(freed, 2)} MB")
    
