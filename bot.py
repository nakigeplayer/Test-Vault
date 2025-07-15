import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
STORAGE_LIMIT_MB = 1000

# Variables globales
total_storage_usage = 0.0  # en MB
active_files = {}  # file_id: (user_id, file_size_mb)

# Inicializa el bot
app = Client("vault_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Extrae el tamaño del archivo según el tipo de media
def get_file_size_mb(message: Message) -> float:
    media = None
    if message.document:
        media = message.document
    elif message.photo:
        media = message.photo
    elif message.audio:
        media = message.audio
    elif message.video:
        media = message.video
    elif message.voice:
        media = message.voice
    elif message.animation:
        media = message.animation
    elif message.sticker and message.sticker.file_size:
        media = message.sticker
    else:
        return 0.0
    return media.file_size / (1024 * 1024) if media.file_size else 0.0

# Extrae el file_id según el tipo de media
def extract_file_id(message: Message) -> str:
    if message.document:
        return message.document.file_id
    elif message.photo:
        return message.photo.file_id
    elif message.audio:
        return message.audio.file_id
    elif message.video:
        return message.video.file_id
    elif message.voice:
        return message.voice.file_id
    elif message.animation:
        return message.animation.file_id
    elif message.sticker and message.sticker.file_id:
        return message.sticker.file_id
    else:
        return "unknown_file_id"

# Maneja mensajes multimedia
@app.on_message(filters.media)
async def handle_media(client: Client, message: Message):
    global total_storage_usage

    user_id = str(message.from_user.id)
    file_size_mb = get_file_size_mb(message)

    if total_storage_usage + file_size_mb > STORAGE_LIMIT_MB:
        await message.reply("No puedo almacenar más archivos ahora")
        return

    total_storage_usage += file_size_mb
    file_id = extract_file_id(message)
    file_path = f"vault/{user_id}/{file_id}"

    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    await client.download_media(message, file_path)

    active_files[file_id] = (user_id, file_size_mb)
    await message.reply(f"Archivo guardado temporalmente: `{file_path}`", quote=True)

    # Programar la eliminación del archivo en 20 minutos
    asyncio.create_task(remove_file_later(client, message, file_id, file_path))

# Elimina el archivo y libera espacio
async def remove_file_later(client: Client, message: Message, file_id: str, path: str):
    global total_storage_usage

    await asyncio.sleep(1200)  # 20 minutos
    if os.path.exists(path):
        os.remove(path)

    _, file_size_mb = active_files.pop(file_id, (None, 0.0))
    total_storage_usage = max(0.0, total_storage_usage - file_size_mb)

    await message.reply("archivo borrado", quote=True)

# Ejecutar el bot
app.run()
