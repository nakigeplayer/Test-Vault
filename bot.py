import os
import asyncio
import threading
from flask import Flask, send_from_directory
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv

# --- Configuración .env ---
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
STORAGE_LIMIT_MB = 1000
VAULT_FOLDER = "vault"

# --- Flask para servir archivos ---
web_app = Flask(__name__)

@web_app.route("/")
def index():
    return "🚀 Vault activo. Archivos temporales disponibles por 20 minutos."

@web_app.route("/vault/<user_id>/<file_id>")
def serve_file(user_id, file_id):
    dir_path = os.path.join(VAULT_FOLDER, user_id)
    return send_from_directory(dir_path, file_id)

def run_flask():
    web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

# --- Pyrogram Bot ---
total_storage_usage = 0.0  # Global en MB
active_files = {}  # file_id: (user_id, file_size)

bot_app = Client("vault_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

def get_file_size_mb(message: Message) -> float:
    media = None
    if message.document: media = message.document
    elif message.photo: media = message.photo
    elif message.audio: media = message.audio
    elif message.video: media = message.video
    elif message.voice: media = message.voice
    elif message.animation: media = message.animation
    elif message.sticker and message.sticker.file_size: media = message.sticker
    else: return 0.0
    return media.file_size / (1024 * 1024) if media.file_size else 0.0

def extract_file_id(message: Message) -> str:
    if message.document: return message.document.file_id
    elif message.photo: return message.photo.file_id
    elif message.audio: return message.audio.file_id
    elif message.video: return message.video.file_id
    elif message.voice: return message.voice.file_id
    elif message.animation: return message.animation.file_id
    elif message.sticker and message.sticker.file_id: return message.sticker.file_id
    return "unknown_file_id"

@bot_app.on_message(filters.media)
async def handle_media(client: Client, message: Message):
    global total_storage_usage

    user_id = str(message.from_user.id)
    file_size_mb = get_file_size_mb(message)
    if total_storage_usage + file_size_mb > STORAGE_LIMIT_MB:
        await message.reply("No puedo almacenar más archivos ahora")
        return

    file_id = extract_file_id(message)
    file_path = os.path.join(VAULT_FOLDER, user_id, file_id)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    await client.download_media(message, file_path)
    total_storage_usage += file_size_mb
    active_files[file_id] = (user_id, file_size_mb)

    public_link = f"https://auto-resend-ctns.onrender.com/vault/{user_id}/{file_id}"
    await message.reply(f"Archivo guardado por 20 minutos: [Acceder al archivo]({public_link})", disable_web_page_preview=True)

    asyncio.create_task(remove_file_later(client, message, file_id, file_path))

async def remove_file_later(client: Client, message: Message, file_id: str, path: str):
    global total_storage_usage
    await asyncio.sleep(1200)  # 20 minutos

    if os.path.exists(path):
        os.remove(path)

    _, size_mb = active_files.pop(file_id, (None, 0.0))
    total_storage_usage = max(0.0, total_storage_usage - size_mb)
    await message.reply("archivo borrado", quote=True)

# --- Lanzar ambos servicios ---
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    bot_app.run()
