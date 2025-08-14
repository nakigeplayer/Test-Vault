import os
import json
import threading
from flask import Flask, send_from_directory, abort, render_template_string, redirect, request, session
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from datetime import datetime
import asyncio
import time
import uuid
import re
from flask import url_for



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
        try:
            with open(storage_path, "r") as f:
                data = json.load(f)
                for i in range(1, TOTAL_INSTANCES + 1):
                    data.setdefault(str(i), 0.0)
                return data
        except json.JSONDecodeError:
            return {str(i): 0.0 for i in range(1, TOTAL_INSTANCES + 1)}
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

async def notify_deletion(user_id: int, filename: str, size_mb: float):
    await bot_app_instance.send_message(
        user_id,
        f"🧽 Tu archivo `{filename}` fue eliminado manualmente desde el panel web."
    )
    await asyncio.sleep(1)
    await bot_app_instance.send_message(
        user_id,
        f"/decrement {INSTANCE} {size_mb:.2f}"
    )
    
# --- Web ---
web_app = Flask(__name__)
web_app.secret_key = os.getenv("SECRET_KEY", "clave_segura")

@web_app.route("/")
def home():
    return "🟢 Servicio en línea. Instancia Flask funcionando correctamente."

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
    os.makedirs(VAULT_FOLDER, exist_ok=True)  # Asegura que la carpeta exista

    users = sorted(os.listdir(VAULT_FOLDER), key=str.lower)
    links = [f"<li><a href='/vault/{uid}/'>{uid}</a></li>" for uid in users]

    create_form = """
    <h4>Crear carpeta de usuario</h4>
    <form method='POST' action='/vault/new'>
        <input type='text' name='user' placeholder='ID de usuario' required>
        <button type='submit'>📁 Crear carpeta</button>
    </form>
    """

    return render_template_string(f"""
        <h2>Instancia {INSTANCE}</h2>
        <ul>{''.join(links)}</ul>
        {create_form}
    """)

@web_app.route("/vault/new", methods=["POST"])
@login_required
def vault_new():
    user_id = request.form.get("user", "").strip()

    if not re.match(r"^[\w\-]+$", user_id):
        return "ID de usuario inválido.", 400

    user_folder = os.path.join(VAULT_FOLDER, user_id)
    os.makedirs(user_folder, exist_ok=True)

    return redirect(url_for("index"))
    

@web_app.route("/vault/<user>/")
@login_required
def user_files(user):
    path = os.path.join(VAULT_FOLDER, user)
    if not os.path.exists(path):
        return "Usuario no encontrado.", 404

    # Formulario para subir archivos
    upload_form = f"""
    <h4>Subir nuevo archivo</h4>
    <form method='POST' action='/vault/{user}/upload' enctype='multipart/form-data'>
        <input type='file' name='file' required>
        <button type='submit'>📤 Subir</button>
    </form>
    """

    # Lista de archivos
    files = os.listdir(path)
    items = []
    for f in files:
        fpath = os.path.join(path, f)
        size = os.path.getsize(fpath) / (1024 * 1024)
        items.append(f"""
        <li>
            {f} ({round(size,2)} MB)
            <a href='/vault/{user}/{f}'>Descargar</a>
            <form method='POST' action='/vault/{user}/{f}/delete' style='display:inline;'>
                <button type='submit'>🗑️ Borrar</button>
            </form>
        </li>
        """)

    return render_template_string(
        f"<h3>Archivos de {user}</h3>"
        + upload_form +
        "<ul>" + "".join(items) + "</ul>"
    )

@web_app.route("/vault/<user>/upload", methods=["POST"])
@login_required
def upload_to_vault(user):
    path = os.path.join(VAULT_FOLDER, user)
    if not os.path.exists(path):
        return "Usuario no encontrado.", 404

    file = request.files.get("file")
    if file and file.filename:
        save_path = os.path.join(path, file.filename)
        file.save(save_path)
        return redirect(f"/vault/{user}/")
    return "Archivo inválido.", 400
    
@web_app.route("/vault/<user>/<filename>")
def serve(user, filename):
    filename = secure_filename(filename)
    return send_from_directory(os.path.join(VAULT_FOLDER, user), filename)

@web_app.route("/vault/<user>/<filename>/delete", methods=["POST"])
@login_required
def delete_file(user, filename):
    filename = secure_filename(filename)
    path = os.path.join(VAULT_FOLDER, user, filename)

    if os.path.exists(path):
        try:
            size_mb = os.path.getsize(path) / (1024 * 1024)  # <== Aquí lo defines
            os.remove(path)

            folder = os.path.dirname(path)
            if not os.listdir(folder):
                os.rmdir(folder)

            asyncio.run(notify_deletion(int(user), filename, size_mb))

        except Exception as e:
            print(f"❌ Error al eliminar archivo desde web: {type(e).__name__} — {e}")
            return f"Error interno: {type(e).__name__} — {e}", 500

    return "✅ Archivo eliminado correctamente", 200
    
    
@web_app.errorhandler(404)
def not_found(e):
    return "🛑 Archivo no encontrado", 404

# --- Bots ---
session1 = uuid.uuid4().hex
session2 = uuid.uuid4().hex

bot_app = Client(session1, api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
bot_app_instance = Client(session2, api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

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
    usage[str(target)] = usage.get(str(target), 0.0) + size_mb
    save_storage_map(usage)
    msg = f"/up {target} {FILE_DURATION_MIN} {user_id}"
    await message.reply(msg, quote=True)

@bot_app_instance.on_message(filters.command("up"))
async def handle_up_command(client: Client, message: Message):
    if not message.reply_to_message or not message.reply_to_message.media:
        await message.reply("❌ Este comando debe responder a un archivo.")
        return
    sender = message.from_user
    admin_first = os.getenv("ADMIN_USER", "").lower()
    sender_first = (sender.first_name or "").lower()

    if not sender.is_self and sender_first != admin_first:
        await message.reply("Este comando es automanejado por el bot.")
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
    active_files[fid] = {
        "fname": fname,
        "user_id": user_id,
        "size_mb": size_mb,
        "timestamp": datetime.now().timestamp(),
        "duration": mins
    }
    usage = load_storage_map()
    usage[str(INSTANCE)] += size_mb
    save_storage_map(usage)
    link = f"{BASE_URL}/vault/{user_id}/{secure_filename(fname)}"
    await client.send_message(int(user_id), f"✅ Tu archivo está en Instancia {INSTANCE}. Descárgalo aquí:\n{link}")

@bot_app.on_message(filters.command("decrement"))
async def handle_decrement(client, message):
    try:
        _, instance_str, mb_str = message.text.strip().split()
        instance = str(instance_str)
        mb = float(mb_str)
        usage = load_storage_map()
        usage[instance] = max(0.0, usage.get(instance, 0.0) - mb)
        save_storage_map(usage)
    except Exception as e:
        print(f"❌ Error al procesar /decrement: {e}")

@bot_app.on_message(filters.command("status"))
async def show_vault(client, message):
    usage = load_storage_map()
    report = []
    for i in range(1, TOTAL_INSTANCES + 1):
        freed = round(float(usage.get(str(i), 0.0)), 2)
        report.append(f"🗂 Instancia {i}: {freed} MB / {STORAGE_LIMIT_MB} MB")
    msg = "\n".join(report)
    await message.reply(f"📁Estado del almacenamiento por instancia:\n{msg}")
    
@bot_app.on_message(filters.command("clear"))
async def clear_manager(client, message):
    usage = load_storage_map()
    report = []
    for i in range(1, TOTAL_INSTANCES + 1):
        freed = round(float(usage.get(str(i), 0.0)), 2)
        report.append(f"🗂 Instancia {i}: {freed} MB liberados")
    if os.path.exists(storage_path):
        os.remove(storage_path)
    with open(storage_path, "w") as f:
        json.dump({}, f)
    msg = "\n".join(report)
    await message.reply(f"🧹 Estado del almacenamiento por instancia:\n{msg}")

@bot_app_instance.on_message(filters.command("clear"))
async def clear_uploader(client, message):
    user_id = str(message.from_user.id)
    folder = os.path.join(VAULT_FOLDER, user_id)
    if not os.path.exists(folder):
        return
    freed = 0.0
    for f in os.listdir(folder):
        fpath = os.path.join(folder, f)
        try:
            freed += os.path.getsize(fpath) / (1024 * 1024)
            os.remove(fpath)
        except:
            continue
    try:
        os.rmdir(folder)
    except:
        pass
    usage = load_storage_map()
    usage[str(INSTANCE)] = max(0.0, usage.get(str(INSTANCE), 0.0) - freed)
    save_storage_map(usage)
    
def start_expiration_checker():
    async def check_files():
        while True:
            now = datetime.now().timestamp()
            expired = []
            for fid, data in list(active_files.items()):
                age = (now - data["timestamp"]) / 60  # minutos
                duration = int(data.get("duration", FILE_DURATION_MIN))
                if age >= duration:
                    path = os.path.join(VAULT_FOLDER, data["user_id"], secure_filename(data["fname"]))
                    if os.path.exists(path):
                        os.remove(path)
                        print(f"⏳ Archivo expirado: {data['fname']}")
                        try: os.rmdir(os.path.dirname(path))
                        except: pass
                    usage = load_storage_map()
                    usage[str(INSTANCE)] = max(0.0, usage.get(str(INSTANCE), 0.0) - data["size_mb"])
                    save_storage_map(usage)
                    expired.append(fid)
                    await bot_app_instance.send_message(
                        int(data["user_id"]),
                        f"🗑️ Tu archivo `{data['fname']}` fue eliminado tras {duration} minutos."
                    )
                    await asyncio.sleep(1)
                    await bot_app_instance.send_message(
                        int(data["user_id"]),
                        f"/decrement {INSTANCE} {data['size_mb']:.2f}"
                    )
            for fid in expired:
                active_files.pop(fid, None)
            await asyncio.sleep(60)  # revisar cada minuto

    threading.Thread(target=lambda: asyncio.run(check_files()), daemon=True).start()

def run_flask():
    web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

def start_bot(bot_instance, label):
    print(f"🟢 [{label}] Iniciando...")
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        bot_instance.run()
    except Exception as e:
        print(f"❌ [{label}] Error: {e}")
from pyrogram.errors import FloodWait

def wait_for_bot(client):
    try:
        client.run()
    except FloodWait as e:
        wait_time = e.value
        print(f"⏳ Esperando {wait_time} segundos para iniciar el bot...")
        for i in range(wait_time):
            print(f"\r⌛ Esperando... {wait_time - i} segundos", end="")
            time.sleep(1)
        print("\r✅ Retomando ejecución...               ")
        client.run()

if __name__ == "__main__":
    TYPE_SERVICE = os.getenv("TYPE_SERVICE", "Manager").lower()
    print(f"🔧 Tipo de servicio: {TYPE_SERVICE}")

    if TYPE_SERVICE == "uploader":
        print("🌐 Iniciando Flask (Uploader)...")
        threading.Thread(target=run_flask, daemon=True).start()
        start_expiration_checker()
        wait_for_bot(bot_app_instance)

    elif TYPE_SERVICE == "manager":
        print("🤖 Iniciando Bot Manager...")
        wait_for_bot(bot_app)

    else:
        print("⚠️ Tipo desconocido, usando Manager por defecto.")
        wait_for_bot(bot_app)
