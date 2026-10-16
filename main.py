# pip install -U aiogram aiohttp
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton, FSInputFile, BotCommand,
)

# ==================== НАСТРОЙКИ ====================
TOKEN = "8751218718:AAEeldWQQn_7tzPXAP8WFQoHCKZ_wrKKV_E"
WEB_HOST = "127.0.0.1"
WEB_PORT = 8080
MAX_CONFIGS = 10

BASE = Path(__file__).parent
CONFIGS_DIR = BASE / "vpn_configs"
CONFIGS_DIR.mkdir(exist_ok=True)
CACHE_FILE = BASE / "geo_cache.json"

# ==================== ТЕКСТЫ ====================
WELCOME_TEXT = """👋 <b>Добро пожаловать!</b>

Этот бот выдаёт <b>VPN-конфиги</b> для приложения <b>Amnezia VPN</b>.

<b>📌 Что тут есть:</b>
• Свежие конфиги для обхода блокировок
• Автоматическое определение страны по IP
• Поддержка WireGuard / AmneziaWG / OpenVPN

<b>🚀 Как получить конфиг:</b>
1. Нажми /vpn
2. Выбери нужную страну
3. Скачай файл
4. Открой Amnezia VPN → «+» → «Импорт из файла»
5. Подключись

<b>💬 Команды:</b>
/vpn — получить конфиг
/help — помощь
/about — о боте"""

VPN_TEXT = """🌐 <b>Доступные VPN-конфиги</b>

Выбери сервер. Страна определяется автоматически по IP-адресу сервера.

<b>📲 После выбора:</b>
1. Скачай .conf файл
2. Открой <b>Amnezia VPN</b>
3. «+» → «Импорт из файла»
4. Выбери скачанный файл
5. Нажми «Подключиться»

<b>⬇️ Выбирай сервер ниже:</b>"""

HELP_TEXT = """📖 <b>Помощь</b>

<b>Команды:</b>
/vpn — список конфигов
/help — это сообщение
/about — о боте

<b>Как подключиться:</b>
1. Установи <b>Amnezia VPN</b>
2. Получи конфиг через /vpn
3. Amnezia → «+» → «Импорт из файла»
4. Выбери .conf
5. Подключись

<b>Если не работает:</b>
• Попробуй другой сервер
• Перезапусти Amnezia
• Проверь интернет"""

ABOUT_TEXT = """ℹ️ <b>О боте</b>

<b>VPN Config Bot</b>
Раздаёт конфиги для Amnezia VPN и автоматически определяет страну сервера по IP.

<b>Технологии:</b>
• Python + aiogram 3
• aiohttp + ip-api.com

<b>Версия:</b> 2.0"""

# ==================== INIT ====================
bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

# кэш геоданных: ip -> {country, code, flag, city, isp}
GEO_CACHE: dict[str, dict] = {}
if CACHE_FILE.exists():
    try:
        GEO_CACHE = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        GEO_CACHE = {}


def save_geo_cache():
    CACHE_FILE.write_text(json.dumps(GEO_CACHE, ensure_ascii=False, indent=2), encoding="utf-8")


# ==================== ПАРСИНГ КОНФИГА ====================
# WireGuard / AmneziaWG:  Endpoint = 1.2.3.4:51820
# OpenVPN:                remote 1.2.3.4 1194
WG_RE = re.compile(r"^\s*Endpoint\s*=\s*([^\s:]+):(\d+)", re.M | re.I)
OVPN_RE = re.compile(r"^\s*remote\s+([^\s]+)\s+(\d+)", re.M | re.I)


def extract_host(path: Path) -> str | None:
    """Возвращает IP/hostname сервера из .conf / .ovpn."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    m = WG_RE.search(text) or OVPN_RE.search(text)
    if m:
        host = m.group(1).strip()
        # если это домен — оставляем как есть, geoip сам разрезолвит
        return host
    return None


# ==================== ГЕОЛОКАЦИЯ ====================
def code_to_flag(code: str) -> str:
    """'NL' -> 🇳🇱"""
    if not code or len(code) != 2:
        return "🌐"
    code = code.upper()
    return chr(0x1F1E6 + ord(code[0]) - ord("A")) + chr(0x1F1E6 + ord(code[1]) - ord("A"))


async def geo_lookup(host: str) -> dict:
    """Возвращает {country, code, flag, city, isp}. Кэшируется."""
    if host in GEO_CACHE:
        return GEO_CACHE[host]

    result = {"country": "Неизвестно", "code": "", "flag": "🌐", "city": "", "isp": ""}
    try:
        url = f"http://ip-api.com/json/{host}?fields=status,country,countryCode,city,isp,query"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=8) as r:
                data = await r.json()
        if data.get("status") == "success":
            result = {
                "country": data.get("country", "Неизвестно"),
                "code":    data.get("countryCode", ""),
                "flag":    code_to_flag(data.get("countryCode", "")),
                "city":    data.get("city", ""),
                "isp":     data.get("isp", ""),
            }
    except Exception as e:
        result["country"] = f"Ошибка: {e}"

    GEO_CACHE[host] = result
    save_geo_cache()
    return result


async def get_config_info(path: Path) -> dict:
    """Собирает инфу о конфиге: имя, размер, host, geo."""
    host = extract_host(path)
    if not host:
        return {
            "name": path.name,
            "path": path,
            "host": "?",
            "country": "Неизвестно",
            "flag": "❓",
            "city": "",
            "isp": "",
        }
    geo = await geo_lookup(host)
    return {
        "name":    path.name,
        "path":    path,
        "host":    host,
        "country": geo["country"],
        "flag":    geo["flag"],
        "code":    geo["code"],
        "city":    geo["city"],
        "isp":     geo["isp"],
    }


# ==================== УТИЛИТЫ ====================
def list_configs() -> list[Path]:
    return sorted([f for f in CONFIGS_DIR.glob("*") if f.is_file()])


def human_size(n: int) -> str:
    for unit in ["Б", "КБ", "МБ"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ГБ"


def safe_path(name: str):
    p = (CONFIGS_DIR / name).resolve()
    if not str(p).startswith(str(CONFIGS_DIR.resolve())):
        return None
    return p


# ==================== HTML ====================
HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>VPN Configs</title>
<style>
body { font-family: -apple-system, sans-serif; max-width: 760px; margin: 40px auto;
       padding: 20px; background: #0f0f0f; color: #e5e5e5; }
h1 { color: #4ade80; margin-bottom: 6px; }
.sub { color: #777; font-size: 14px; margin-bottom: 24px; }
.box { background: #1a1a1a; padding: 20px; border-radius: 10px; margin-bottom: 20px; }
.config { background: #1a1a1a; padding: 14px 18px; border-radius: 8px;
          margin: 8px 0; display: flex; justify-content: space-between; align-items: center; }
.left { display: flex; flex-direction: column; }
.name { font-size: 15px; font-weight: 600; }
.meta { color: #777; font-size: 12px; margin-top: 3px; }
.country { font-size: 13px; color: #4ade80; margin-top: 4px; }
button { background: #ef4444; color: #fff; border: 0; padding: 8px 16px;
         border-radius: 6px; cursor: pointer; font-size: 13px; }
button.up { background: #4ade80; color: #000; }
button:hover { opacity: .85; }
input[type=file] { color: #ccc; font-size: 14px; margin-right: 10px; }
.msg { color: #4ade80; margin-top: 10px; display: inline-block; }
.empty { color: #777; text-align: center; padding: 30px; }
.count { color: #4ade80; }
</style>
</head>
<body>
<h1>🌐 VPN Configs</h1>
<div class="sub">Загрузи до 10 конфигов — они появятся в Telegram-боте по команде /vpn. Страна определяется автоматически.</div>

<div class="box">
    <form action="/upload" method="post" enctype="multipart/form-data">
        <input type="file" name="file" accept=".conf,.ovpn,.wg,.txt" required>
        <button type="submit" class="up">⬆ Загрузить</button>
    </form>
    __MSG__
</div>

<h3>Конфиги (<span class="count">__COUNT__</span>/__MAX__)</h3>
__LIST__
</body>
</html>"""


async def render_page(msg: str = "") -> str:
    files = list_configs()
    if files:
        rows = ""
        for f in files:
            info = await get_config_info(f)
            st = f.stat()
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%d.%m %H:%M")
            rows += (
                f'<div class="config">'
                f'<div class="left">'
                f'<div class="name">📄 {f.name}</div>'
                f'<div class="meta">{human_size(st.st_size)} · {mtime}</div>'
                f'<div class="country">{info["flag"]} {info["country"]}'
                + (f' · {info["city"]}' if info["city"] else "")
                + f' · <code>{info["host"]}</code></div>'
                f'</div>'
                f'<form action="/delete/{f.name}" method="post" style="margin:0;">'
                f'<button type="submit">Удалить</button></form></div>'
            )
    else:
        rows = '<div class="empty">Пока пусто. Загрузи первый конфиг выше.</div>'

    msg_html = f'<div class="msg">{msg}</div>' if msg else ""
    return (HTML_PAGE
            .replace("__MSG__", msg_html)
            .replace("__COUNT__", str(len(files)))
            .replace("__MAX__", str(MAX_CONFIGS))
            .replace("__LIST__", rows))


# ==================== WEB ====================
async def web_index(request):
    msg = request.query.get("msg", "")
    html = await render_page(msg)
    return web.Response(text=html, content_type="text/html")


async def web_upload(request):
    if len(list_configs()) >= MAX_CONFIGS:
        return web.HTTPFound(f"/?msg=Лимит: {MAX_CONFIGS} файлов")

    reader = await request.multipart()
    field = await reader.next()
    if not field or field.name != "file" or not field.filename:
        return web.HTTPFound("/?msg=Файл не выбран")

    name = Path(field.filename).name
    target = safe_path(name)
    if not target:
        return web.HTTPFound("/?msg=Плохое имя файла")

    with open(target, "wb") as f:
        while True:
            chunk = await field.read_chunk()
            if not chunk:
                break
            f.write(chunk)

    # сразу пробуем определить страну
    host = extract_host(target)
    geo_msg = ""
    if host:
        geo = await geo_lookup(host)
        geo_msg = f" · {geo['flag']} {geo['country']}"

    return web.HTTPFound(f"/?msg=Загружено: {name}{geo_msg}")


async def web_delete(request):
    name = request.match_info["name"]
    p = safe_path(name)
    if p and p.exists():
        p.unlink()
    return web.HTTPFound("/?msg=Удалено")


async def start_web():
    app = web.Application()
    app.router.add_get("/", web_index)
    app.router.add_post("/upload", web_upload)
    app.router.add_post("/delete/{name}", web_delete)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_HOST, WEB_PORT)
    await site.start()
    print(f"🌐 Админка: http://{WEB_HOST}:{WEB_PORT}")


# ==================== TELEGRAM ====================
async def configs_kb() -> InlineKeyboardMarkup:
    rows = []
    for f in list_configs():
        info = await get_config_info(f)
        rows.append([InlineKeyboardButton(
            text=f"{info['flag']} {info['country']} — {f.name}",
            callback_data=f"get:{f.name}"
        )])
    if not rows:
        rows.append([InlineKeyboardButton(text="Нет конфигов", callback_data="none")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(WELCOME_TEXT)


@dp.message(Command("vpn"))
async def cmd_vpn(message: Message):
    files = list_configs()
    if not files:
        await message.answer("❌ Конфигов пока нет. Зайди позже.")
        return

    status = await message.answer("🔍 Определяю страны серверов...")
    kb = await configs_kb()
    try:
        await status.delete()
    except Exception:
        pass

    await message.answer(
        VPN_TEXT + f"\n\n<b>📦 Доступно: {len(files)}</b>",
        reply_markup=kb
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)


@dp.message(Command("about"))
async def cmd_about(message: Message):
    await message.answer(ABOUT_TEXT)


@dp.callback_query(F.data == "none")
async def cb_none(cb: CallbackQuery):
    await cb.answer("Конфигов нет.", show_alert=True)


@dp.callback_query(F.data.startswith("get:"))
async def cb_get(cb: CallbackQuery):
    name = cb.data.split(":", 1)[1]
    p = safe_path(name)
    if not p or not p.exists():
        await cb.answer("Файл не найден", show_alert=True)
        return

    await cb.answer("📤 Отправляю...")
    info = await get_config_info(p)

    caption = (
        f"{info['flag']} <b>{info['country']}</b>"
        + (f" · {info['city']}" if info['city'] else "")
        + f"\n📄 <code>{p.name}</code>\n"
        f"🌍 <code>{info['host']}</code>\n\n"
        "📲 <b>Как подключить:</b>\n"
        "1️⃣ Скачай файл\n"
        "2️⃣ Открой <b>Amnezia VPN</b>\n"
        "3️⃣ «+» → «Импорт из файла»\n"
        "4️⃣ Выбери скачанный файл\n"
        "5️⃣ Подключись\n\n"
        "⚡ Если не работает — попробуй другой сервер."
    )

    await bot.send_document(
        chat_id=cb.message.chat.id,
        document=FSInputFile(p, filename=p.name),
        caption=caption,
    )


# ==================== ЗАПУСК ====================
async def main():
    await start_web()
    await bot.set_my_commands([
        BotCommand(command="start", description="Добро пожаловать"),
        BotCommand(command="vpn",   description="Получить VPN-конфиг"),
        BotCommand(command="help",  description="Помощь"),
        BotCommand(command="about", description="О боте"),
    ])
    me = await bot.get_me()
    print(f"✅ Бот @{me.username} запущен")
    print(f"📁 Конфиги: {CONFIGS_DIR.absolute()}")
    print(f"🌐 Админка: http://{WEB_HOST}:{WEB_PORT}")
    print("📌 Жду...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())