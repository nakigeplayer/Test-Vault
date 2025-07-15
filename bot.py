import os
import asyncio
import threading
from flask import Flask, send_from_directory, abort, render_template_string
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv

# Cargar configuración del entorno
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
STORAGE_LIMIT_MB = 1000
VAULT_FOLDER = "vault"

# Variables globales
total_storage_usage = 0.0
active_files = {}  # file_id: (filename, user_id, file_size_mb)

# --- Servidor Flask ---
web_app = Flask(__name__)

@web_app.route("/")
def index():
    return "🚀 Vault activo. Archivos temporales disponibles por 20 minutos."

@web_app.route("/vault/")
def vault_index():
    try:
        users = os.listdir(VAULT_FOLDER)
        links = [f"<li><a href='/vault/{uid}/'>{uid}</a></li>" for uid in users]
        return render_template_string("<h2>Usuarios disponibles:</h2><ul>" + "".join(links) + "</ul>")
    except FileNotFoundError:
        return "No hay archivos almacenados."

@web_app.route("/vault/<user_id>/")
def user_vault(user_id):
    user_path = os.path.join(VAULT_FOLDER, user_id)
    if not os.path.exists(user_path):
        abort(404)
    files = os.listdir(user_path)
    links = [
        f"<li><a href='/vault/{user_id}/{f}'>{f}</a></li>"
        for f in files
    ]
    return render_template_string(f"<h2>Archivos de usuario {user_id}:</h2><ul>" + "".join(links) + "</ul>")

@web_app.route("/vault/<user_id>/<file_name>")
def serve_file(user_id, file_name):
    dir_path = os.path.join(VAULT_FOLDER, user_id)
    return send_from_directory(dir_path, file_name)

def run_flask():
    web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

# --- Pyrogram Bot ---
bot_app = Client("vault_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

def get_file_info(message: Message):
    media = None
    filename = None
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

    public_link = f"https://auto-resend-ctns.onrender.com/vault/{user_id}/{filename}"
    await message.reply(f"Archivo guardado temporalmente: [Abrir]({public_link})", disable_web_page_preview=True)

    asyncio.create_task(remove_file_later(client, message, file_id, file_path))

async def remove_file_later(client: Client, message: Message, file_id: str, path: str):
    global total_storage_usage

    await asyncio.sleep(1200)
    if os.path.exists(path):
        os.remove(path)

    _, _, size_mb = active_files.pop(file_id, (None, None, 0.0))
    total_storage_usage = max(0.0, total_storage_usage - size_mb)
    await message.reply("archivo borrado", quote=True)

# --- Comando /clear ---
@bot_app.on_message(filters.command("clear"))
async def clear_user_files(client: Client, message: Message):
    global total_storage_usage

    user_id = str(message.from_user.id)
    user_path = os.path.join(VAULT_FOLDER, user_id)

    if not os.path.exists(user_path):
        await message.reply("No tienes archivos almacenados.")
        return

    total_freed = 0.0
    for filename in os.listdir(user_path):
        file_path = os.path.join(user_path, filename)
        try:
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            os.remove(file_path)
            total_freed += size_mb
        except:
            continue

    try:
        os.rmdir(user_path)
    except:
        pass

    total_storage_usage = max(0.0, total_storage_usage - total_freed)
    await message.reply(f"🧹 Archivos eliminados. Espacio liberado: {round(total_freed, 2)} MB")

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    bot_app.run()
