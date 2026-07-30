# main.py
# UNIVERSAL ENGINE CONTROL PANEL (V50.6)
# -----------------------
# IMPORTANT (security):
# - Do NOT hardcode API_ID, API_HASH, BOT_TOKEN, or SESSION strings in this file.
# - Set them as environment variables on your machine before running:
#     API_ID, API_HASH, BOT_TOKEN (or SESSION_STRING_1 / SESSION_STRING_2)
# - Example (PowerShell):
#     $env:API_ID="12345"
#     $env:API_HASH="abcdef..."
#     $env:BOT_TOKEN="123:ABC..."
#     python main.py
#
# This file intentionally reads credentials from environment variables and
# validates session strings to avoid crashes from malformed session strings.

from __future__ import annotations
import asyncio
import os
import sys
import time
import re
import sqlite3
import json
import logging
import uuid
import base64
import binascii
import socket
from typing import Optional

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.raw.types import InputChannel
from pyrogram.raw.functions.channels import GetChannels
from aiohttp import web

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

# -----------------------
# Environment / configuration
def required_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        logger.error("Required environment variable %s is not set. Exiting.", name)
        sys.exit(1)
    return v

def optional_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)

def _validate_session_string(s: Optional[str]) -> str:
    if not s:
        return ""
    s_clean = s.strip()
    try:
        padding = (-len(s_clean)) % 4
        base64.urlsafe_b64decode((s_clean + "=" * padding).encode())
        return s_clean
    except Exception:
        logger.warning("SESSION_STRING looks invalid or truncated; ignoring it to avoid startup crash.")
        return ""

# Read required credentials (do NOT hardcode in repo)
API_ID_RAW = optional_env("API_ID", "").strip()
API_HASH = optional_env("API_HASH", "").strip()
BOT_TOKEN = optional_env("BOT_TOKEN", "").strip()

if not API_ID_RAW or not API_HASH:
    logger.error("API_ID or API_HASH missing. Please set environment variables API_ID and API_HASH.")
    sys.exit(1)

try:
    API_ID = int(API_ID_RAW)
except ValueError:
    logger.error("API_ID must be an integer. Got: %s", API_ID_RAW)
    sys.exit(1)

# Session strings are optional — validated
SESSION_1 = _validate_session_string(optional_env("SESSION_STRING_1", "").strip())
SESSION_2 = _validate_session_string(optional_env("SESSION_STRING_2", "").strip())

# If there's no BOT_TOKEN and no sessions, we cannot run
if not BOT_TOKEN and not (SESSION_1 or SESSION_2):
    logger.error("No BOT_TOKEN and no SESSION strings provided. Set BOT_TOKEN or at least one SESSION_STRING_X.")
    sys.exit(1)

TARGET_CHAT_ID_RAW = optional_env("TARGET_CHAT_ID", "").strip()
if TARGET_CHAT_ID_RAW:
    try:
        TARGET_CHAT_ID = int(TARGET_CHAT_ID_RAW)
    except ValueError:
        TARGET_CHAT_ID = TARGET_CHAT_ID_RAW  # allow username-like targets
else:
    TARGET_CHAT_ID = None  # user must set a target or modify behavior in the UI

DEFAULT_WATERMARK = optional_env("SINGLE_WATERMARK", " ").strip() or " "

# Optional extras (kept for compatibility; not required)
AUTO_RESTART = optional_env("AUTO_RESTART", "ffmpeg")
LOOP_TIMER = int(optional_env("LOOP_TIMER", "0"))
MAX_WORKERS = int(optional_env("MAX_WORKERS", "4"))
SLEEP_THRESHOLD = int(optional_env("SLEEP_THRESHOLD", "60"))
STREAM_DOWNLOAD = optional_env("STREAM_DOWNLOAD", "True").lower() in ("1", "true", "yes")

LOCAL_TEMP_PATH = "temp_media"
os.makedirs(LOCAL_TEMP_PATH, exist_ok=True)

BATCH_METRICS = {"total": 0, "videos": 0, "pdfs": 0, "skipped": 0, "bytes": 0}
BATCH_TASKS = {}

# Create Pyrogram clients
user_app_1 = Client(
    "user_backend_1",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_1 if SESSION_1 else None,
    workers=4,
)
user_app_2 = Client(
    "user_backend_2",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_2 if SESSION_2 else None,
    workers=4,
)
bot_app = Client(
    "quantix_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN if BOT_TOKEN else None,
    workers=8,
)

# Utility functions
def get_watermark() -> str:
    return DEFAULT_WATERMARK

def run_db(query: str, params=()):
    try:
        with sqlite3.connect("quantix_recovery.db", timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if query.strip().upper().startswith("SELECT"):
                return cursor.fetchone()
            conn.commit()
    except Exception:
        logger.exception("DB operation failed")
    return None

def format_size(bytes_size: int) -> str:
    if bytes_size >= 1073741824:
        return f"{bytes_size / 1073741824:.2f} GB"
    return f"{bytes_size / 1048576:.2f} MB"

UNWANTED_RETURAJ_PATTERN = re.compile(r'@?returaj(?:[_\s]*gaikwad)?|returajgaikwad', re.IGNORECASE)
CUSTOM_REPLACEMENT_MAP = {
    "Guidely": "Demon Core",
    "Class -": "Lecture",
    "Live Mock": "Practice Test"
}

def global_text_cleaner(text_input: Optional[str]) -> str:
    if not text_input:
        return ""
    clean = text_input.replace("_", " ")
    for target_word, replacement in CUSTOM_REPLACEMENT_MAP.items():
        clean = re.compile(re.escape(target_word), re.IGNORECASE).sub(replacement, clean)
    clean = re.sub(r'(?i)\bfree[_\s]*batches\b', '', clean)
    clean = UNWANTED_RETURAJ_PATTERN.sub('', clean)
    clean = re.sub(r"[^a-zA-Z0-9\s]", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean

def build_forward_watermark_caption(old_caption: Optional[str]) -> str:
    watermark = get_watermark()
    if not old_caption:
        return watermark
    clean_lines = []
    for line in old_caption.splitlines():
        if not line.strip():
            continue
        for target_word, replacement in CUSTOM_REPLACEMENT_MAP.items():
            line = re.compile(re.escape(target_word), re.IGNORECASE).sub(replacement, line)
        line = re.sub(r'@[a-zA-Z0-9_]+', '', line).strip()
        line = UNWANTED_RETURAJ_PATTERN.sub('', line).strip()
        if line:
            clean_lines.append(line)
    final_text = "\n".join(clean_lines).strip()
    return f"{final_text}\n\n{watermark}" if watermark else final_text

def clean_and_build_caption(old_caption: Optional[str], fallback_name: str = "") -> str:
    watermark = get_watermark()
    title_val = ""
    if old_caption:
        lines = [l.strip() for l in old_caption.splitlines() if l.strip()]
        for line in lines:
            ttl_m = re.search(r'(?i)(Title|Subject|Lesson|Topic|Content|Lecture)\s*:\s*(.*)', line)
            if ttl_m:
                title_val = global_text_cleaner(ttl_m.group(2).strip())
                break
        if not title_val and lines:
            for potential_line in lines:
                if not re.search(r'(?i)(Index|Sr\s*No|S\.No|Sl\s*No)', potential_line):
                    title_val = global_text_cleaner(potential_line.strip())
                    break
    if not title_val and fallback_name:
        title_val = global_text_cleaner(fallback_name.rsplit('.', 1)[0])
    title_val = re.sub(r'(?i)@|\[|\.pdf', '', title_val).strip()
    caption = f" **Title:** {title_val}" if title_val else " **Title:** Extra Asset"
    if watermark:
        caption += f"\n\n{watermark}"
    return caption

# Video helpers
async def get_video_metadata_async(video_path: str):
    for ff_binary in ['static_ffprobe', 'ffprobe', '.venv/bin/static_ffprobe']:
        try:
            proc = await asyncio.create_subprocess_exec(
                ff_binary, '-v', 'quiet', '-print_format', 'json', '-show_streams', '-show_format', video_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            data = json.loads(stdout.decode() or "{}")
            duration = int(float(data.get('format', {}).get('duration', 0) or 0))
            width = height = 0
            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'video':
                    width = int(stream.get('width', 0) or 0)
                    height = int(stream.get('height', 0) or 0)
                    break
            return width, height, duration
        except Exception:
            continue
    return 1280, 720, 3600

async def generate_instant_thumb_async(video_path: str):
    thumb_path = video_path + ".jpg"
    for ff_binary in ['static_ffmpeg', 'ffmpeg', '.venv/bin/static_ffmpeg']:
        try:
            proc = await asyncio.create_subprocess_exec(
                ff_binary, '-ss', '00:04:00', '-i', video_path, '-vf', "select='gt(scene,0.01)',scale=320:-1", '-vframes', '1', '-q:v', '2', thumb_path, '-y',
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(proc.communicate(), timeout=12)
            if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 100:
                return thumb_path
        except Exception:
            continue
    return None

# Async safe wrappers
async def safe_api(func, *args, **kwargs):
    for i in range(3):
        try:
            return await func(*args, **kwargs)
        except FloodWait as e:
            await asyncio.sleep(e.value + 2)
        except Exception:
            if i == 2:
                raise
            await asyncio.sleep(2)

async def safe_edit_text(msg, text):
    try:
        await msg.edit_text(text)
    except MessageNotModified:
        pass
    except Exception:
        logger.exception("Failed to edit message")

# Peer resolution and message fetch helpers
async def force_sync_peer_async(client_app: Client, raw_chat_id):
    try:
        clean_cid = int(str(raw_chat_id).replace("-100", ""))
        input_channel = InputChannel(channel_id=clean_cid, access_hash=0)
        await client_app.invoke(GetChannels(id=[input_channel]))
    except Exception:
        try:
            await client_app.get_chat(raw_chat_id)
        except Exception:
            pass

async def fetch_message_dual_nodes(raw_chat_id, msg_id):
    try:
        await force_sync_peer_async(user_app_1, raw_chat_id)
        messages = await user_app_1.get_messages(raw_chat_id, msg_id)
        if messages and (messages.media or messages.text):
            return messages, user_app_1
    except Exception:
        pass
    try:
        await force_sync_peer_async(user_app_2, raw_chat_id)
        messages = await user_app_2.get_messages(raw_chat_id, msg_id)
        if messages and (messages.media or messages.text):
            return messages, user_app_2
    except Exception:
        pass
    return None, None

# Core processing function
async def process_nitro_restricted(cid, msg_id_list, status_msg, topic_id, prefix="", force_forward_mode=False, single_mode=False):
    path = None
    msg = None
    worker_client = None
    for mid in msg_id_list:
        msg, worker_client = await fetch_message_dual_nodes(cid, mid)
        if msg:
            break

    if not msg or not worker_client:
        BATCH_METRICS["skipped"] += 1
        if status_msg:
            await safe_edit_text(status_msg, f"{prefix}❌ Access Denied: Channel not joined on either account.")
        return

    try:
        raw_filename = getattr(msg, "document", None) and msg.document.file_name or (getattr(msg, "video", None) and msg.video.file_name) or "file"
        is_pdf_doc = getattr(msg, "document", None) and msg.document.file_name.lower().endswith('.pdf')
        ext = ".pdf" if is_pdf_doc else ".mp4"
        file_size_bytes = getattr(msg, "document", None) and msg.document.file_size or (getattr(msg, "video", None) and msg.video.file_size) or 0
        BATCH_METRICS["bytes"] += file_size_bytes

        caption = build_forward_watermark_caption(msg.caption) if force_forward_mode else clean_and_build_caption(msg.caption, fallback_name=raw_filename)
        final_save_path = os.path.join(LOCAL_TEMP_PATH, f"nitro_{msg.id}{ext}")
        if status_msg:
            await safe_edit_text(status_msg, f"{prefix}🚀 **Downloading Data Blocks...**")

        start_time_down = time.time()
        p_bar_down = lambda c, t: bot_app.loop.create_task(progress_bar(c, t, "DOWNLOADING", status_msg, start_time_down, False))
        path = await safe_api(worker_client.download_media, msg, file_name=final_save_path, progress=p_bar_down)

        if not path or not os.path.exists(path):
            BATCH_METRICS["skipped"] += 1
            if status_msg:
                await safe_edit_text(status_msg, f"{prefix}❌ Disk IO Error.")
            return

        if status_msg:
            await safe_edit_text(status_msg, f"{prefix}📤 **Injecting Pipeline Core...**")
        start_time_up = time.time()
        p_bar_up = lambda c, t: bot_app.loop.create_task(progress_bar(c, t, "UPLOADING", status_msg, start_time_up, False))
        send_kwargs = {"reply_to_message_id": topic_id} if topic_id else {}

        if getattr(msg, "video", None) or (getattr(msg, "document", None) and raw_filename.lower().endswith(('.mp4', '.mkv', '.avi')) and not is_pdf_doc):
            v_width, v_height, v_duration = await get_video_metadata_async(path)
            generated_thumb = await generate_instant_thumb_async(path)
            await safe_api(
                bot_app.send_video,
                TARGET_CHAT_ID or 0,
                video=path,
                thumb=generated_thumb,
                width=v_width,
                height=v_height,
                duration=v_duration,
                caption=caption,
                supports_streaming=True,
                progress=p_bar_up,
                **send_kwargs
            )
            if generated_thumb and os.path.exists(generated_thumb):
                os.remove(generated_thumb)
            BATCH_METRICS["videos"] += 1
        else:
            clean_file_name = global_text_cleaner(raw_filename.rsplit('.', 1)[0]) + ext
            await safe_api(bot_app.send_document, TARGET_CHAT_ID or 0, document=path, file_name=clean_file_name, caption=caption, progress=p_bar_up, **send_kwargs)
            BATCH_METRICS["pdfs"] += 1

        if status_msg:
            await status_msg.delete()

    except Exception as e:
        BATCH_METRICS["skipped"] += 1
        if status_msg:
            await safe_edit_text(status_msg, f"{prefix}❌ Fault Node: `{str(e)}`")
        logger.exception("Error processing message")
    finally:
        if path and os.path.exists(path):
            os.remove(path)

# Progress bar and admin helpers
last_edit = {}
async def progress_bar(current, total, action, msg, start, compression_active=False):
    if now_cancelled():
        raise Exception("Stopped!")
    now = time.time()
    try:
        display_total = total if total and total >= current else current
        pct = (current * 100 / display_total) if display_total else 0
        spd = current / (now - start) if (now - start) > 0 else 0
        bar = "[{0}{1}]".format('=' * int(pct/10), ' ' * (10 - int(pct/10)))
        comp_status = "OFF"
        dashboard = (
            f"⚡ DEMON ENGINE ACTIVE\n"
            f"<code>{bar} {pct:.2f}%</code>\n\n"
            f"📊 Core Engine: `{action}`\n"
            f"🗜️ Compression: `{comp_status}`\n"
            f"📁 Size: `{current/1048576:.2f} / {display_total/1048576:.2f} MB`\n"
            f"🚀 Speed: `{spd/1048576:.2f} MB/s`"
        )
        await safe_edit_text(msg, dashboard)
    except Exception:
        pass

def now_cancelled() -> bool:
    # global cancellation check (simple placeholder)
    return False

def is_admin(_, __, message):
    return message.from_user and message.from_user.id in [5983880450]

# Bot commands
@bot_app.on_message(filters.command(["start", "help"]) & filters.create(is_admin))
async def start_cmd(c, m):
    await m.reply("DEMON CORE ACTIVE\n\nCommands:\n`/batch [Topic_ID] L1 L2` (bulk)\n`/q [Topic_ID] link` (single)")

@bot_app.on_message(filters.command(["w", "f", "p", "q"]) & filters.create(is_admin))
async def forward_watermark_cmd(c, m):
    if len(m.command) < 2:
        return await m.reply("❌ Usage: `/q [Topic_ID] link`")
    try:
        words = m.text.split()
        topic_id = None
        link_to_parse = words[1]
        if len(words) > 2 and words[1].isdigit():
            topic_id = int(words[1]); link_to_parse = words[2]
        chat_id, msg_id = parse_link_advanced(link_to_parse)
        status = await m.reply("⚡ Bypassing asset...")
        await process_nitro_restricted(chat_id, [msg_id], status, topic_id, force_forward_mode=False, single_mode=True)
    except Exception as e:
        await m.reply(f"❌ Single Bypass Fault: `{e}`")

def parse_link_advanced(text_arg: str):
    clean_text = "".join(text_arg.split()).split("?")[0]
    all_numbers = re.findall(r'\d+', clean_text)
    if not all_numbers or (len(all_numbers) < 2 and "t.me/c/" in clean_text):
        raise ValueError("Could not extract standard numerical configurations from the URL string.")
    msg_id = int(all_numbers[-1])
    if "t.me/c/" in clean_text:
        chat_candidate = all_numbers[0]
        if len(chat_candidate) < 5 and len(all_numbers) > 2:
            chat_candidate = all_numbers[1]
        chat_id = int(f"-100{chat_candidate}")
    else:
        parts = [p for p in clean_text.split('/') if p.strip()]
        chat_str = parts[-2]
        chat_id = int(f"-100{chat_str}") if chat_str.isdigit() else chat_str
    return chat_id, msg_id

# Batch worker simplified (keeps behavior similar to earlier)
async def _batch_worker(batch_id, cid, all_msg_ids, topic_id, master_panel, cancel_event):
    try:
        total_tasks = len(all_msg_ids)
        for idx, mid in enumerate(all_msg_ids, 1):
            if cancel_event.is_set():
                await master_panel.edit_text(f"🛑 Batch `{batch_id}` cancelled by admin.")
                break
            await master_panel.edit_text(f"⏳ Batch `{batch_id}` — Processing [{idx}/{total_tasks}] msg_id `{mid}` ...")
            try:
                await asyncio.wait_for(
                    process_nitro_restricted(cid, [mid], master_panel, topic_id, f"Slot [{idx}/{total_tasks}] ", force_forward_mode=False, single_mode=False),
                    timeout=900
                )
            except asyncio.TimeoutError:
                BATCH_METRICS["skipped"] += 1
                await master_panel.edit_text(f"❌ Batch `{batch_id}` — msg `{mid}` timed out, skipping...")
            except Exception:
                BATCH_METRICS["skipped"] += 1
                await master_panel.edit_text(f"❌ Batch `{batch_id}` — Fault while processing `{mid}` — skipping...")
            finally:
                await asyncio.sleep(0.5)
        readable_size = format_size(BATCH_METRICS["bytes"])
        summary_report = (
            f"☠️ BATCH {batch_id} COMPLETE\n\n"
            f"🎬 Videos: `{BATCH_METRICS['videos']}`\n"
            f"📄 PDFs: `{BATCH_METRICS['pdfs']}`\n"
            f"❌ Skipped: `{BATCH_METRICS['skipped']}`\n"
            f"📦 Volume: `{readable_size}`"
        )
        await master_panel.edit_text(summary_report)
    finally:
        BATCH_TASKS.pop(batch_id, None)

# Health server + startup
async def _health(request):
    return web.Response(text="OK")

async def main():
    run_db('''CREATE TABLE IF NOT EXISTS batch_history (chat_id TEXT, msg_id INTEGER, status TEXT, PRIMARY KEY (chat_id, msg_id))''')
    # start user sessions only if provided
    if SESSION_1:
        await user_app_1.start()
    if SESSION_2:
        await user_app_2.start()
    # bot_app start (works even if bot token is None when using user sessions)
    await bot_app.start()

    port = int(optional_env("PORT", "10000"))
    web_app = web.Application()
    web_app.router.add_get("/health", _health)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)

    port_in_use = False
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("0.0.0.0", port))
        probe.close()
    except OSError:
        port_in_use = True

    if port_in_use:
        logger.warning("Port %s already in use; skipping embedded aiohttp health server.", port)
    else:
        try:
            await site.start()
            print(f" CORE ENGINE V50.6 UP & ROUTING! (health at /health on port {port})")
        except Exception as e:
            logger.exception("Failed to start health server: %s", e)

    # keep running
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception:
        logger.exception("Fatal error on startup")
