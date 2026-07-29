# -*- coding: utf-8 -*-
import asyncio
import os
import time
import re
import sqlite3
import json
import logging
import uuid
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, MessageNotModified, AccessTokenInvalid, PeerIdInvalid, ChannelPrivate
from pyrogram.raw.types import InputChannel, InputPeerChannel
from pyrogram.raw.functions.channels import GetChannels

logging.basicConfig(level=logging.INFO)

# =========================================================================
# ⚙️ UNIVERSAL ENGINE CONTROL PANEL (V50.6 - ANTI-ACCESS DENIED PRO)
# =========================================================================
API_ID = int(os.environ.get("API_ID", 39199066))
API_HASH = os.environ.get("API_HASH", "95f9f5b87842e1ec2334f543c3dbfd0e")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TARGET_CHAT_ID = int(os.environ.get("TARGET_CHAT_ID", -1004321956195))
DEFAULT_WATERMARK = "         "

SESSION_1 = os.environ.get("SESSION_STRING_1", "").strip()
SESSION_2 = os.environ.get("SESSION_STRING_2", "").strip()

CUSTOM_REPLACEMENT_MAP = {
    "Guidely": "Demon Core",
    "Class -": "Lecture",
    "Live Mock": "Practice Test"
}
# =========================================================================

# Pattern to remove "returaj" variants (case-insensitive): "returaj", "returaj gaikwad", "returaj_gaikwad", "@returaj", "returajgaikwad"
UNWANTED_RETURAJ_PATTERN = re.compile(r'@?returaj(?:[_\s]*gaikwad)?|returajgaikwad', re.IGNORECASE)

ADMIN_USER_IDS = [5983880450]
CURRENT_BATCH_CANCEL = False
LOCAL_TEMP_PATH = "temp_media"
os.makedirs(LOCAL_TEMP_PATH, exist_ok=True)

BATCH_METRICS = {"total": 0, "videos": 0, "pdfs": 0, "skipped": 0, "bytes": 0}

# background batches registry
BATCH_TASKS = {}  # batch_id -> {"task": Task, "cancel": asyncio.Event()}

user_app_1 = Client("user_backend_1", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_1 if SESSION_1 else None, workers=4)
user_app_2 = Client("user_backend_2", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_2 if SESSION_2 else None, workers=4)
bot_app = Client("quantix_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN if BOT_TOKEN else None, workers=8)
DB_NAME = "quantix_recovery.db"

def get_watermark():
    return os.environ.get("SINGLE_WATERMARK", DEFAULT_WATERMARK)

def run_db(query, params=()):
    try:
        with sqlite3.connect(DB_NAME, timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if "SELECT" in query: return cursor.fetchone()
            else: conn.commit()
    except Exception:
        pass
    return None

def format_size(bytes_size):
    if bytes_size >= 1073741824:
        return f"{bytes_size / 1073741824:.2f} GB"
    return f"{bytes_size / 1048576:.2f} MB"

def direct_watermark_cleaner(old_text):
    watermark = get_watermark()
    if not old_text:
        return f"{watermark}" if watermark else ""
    clean = old_text
    # existing removals
    clean = re.sub(r'(?i)@LioBankingPro', '', clean)
    clean = re.sub(r'(?i)Extracted by\s*:\s*', '', clean)
    # also remove returaj variants
    clean = UNWANTED_RETURAJ_PATTERN.sub('', clean)
    clean = re.sub(r'\n\s*\n+', '\n\n', clean).strip()
    return f"{clean}\n\n{watermark}" if watermark else clean

def global_text_cleaner(text_input):
    if not text_input:
        return ""
    clean = text_input
    clean = clean.replace("_", " ")

    for target_word, replacement in CUSTOM_REPLACEMENT_MAP.items():
        pattern = re.compile(re.escape(target_word), re.IGNORECASE)
        clean = pattern.sub(replacement, clean)

    # Free_Batches removal
    clean = re.sub(r'(?i)\bfree[_\s]*batches\b', '', clean)

    # Remove returaj / returaj gaikwad variants (keep this in addition to other removals)
    clean = UNWANTED_RETURAJ_PATTERN.sub('', clean)

    # Existing unwanted chars removal
    clean = re.sub(r"[^a-zA-Z0-9\s]", "", clean)
    clean = re.sub(r"\s+", " ", clean)
    clean = clean.strip()
    return clean

def build_forward_watermark_caption(old_caption):
    watermark = get_watermark()
    if not old_caption: return f"{watermark}"
    clean_lines = []
    for line in old_caption.split('\n'):
        if line.strip():
            for target_word, replacement in CUSTOM_REPLACEMENT_MAP.items():
                pattern = re.compile(re.escape(target_word), re.IGNORECASE)
                line = pattern.sub(replacement, line)
            # preserve and keep existing removal rules
            line = re.sub(r'(?i)\bluciferbanker\s*x\s*bhaiyaji\b|\bluciferbanker\b|\bbhaiyaji\b|\bparinda\b|\bfree_batches\b', '', line).strip()
            line = re.sub(r'(?i)\bjetha\s*banker\b|\bjetha_banker\b|\bjethabanker\b|@jethabanker|\blioBankingPro\b|\blio\b|\bjethalal\b|\bjetha\b|\bfree_batches\b', '', line).strip()
            line = re.sub(r'(?i)\bluciferbanker\s*x\s*bhaiyaji\b|\bluciferbanker\b|\bbhaiyaji\b|\bparinda\b', '', line).strip()
            line = re.sub(r'(?i)\bjetha\s*banker\b|\bjetha_banker\b|\bjethabanker\b|@jethabanker|\blioBankingPro\b|\blio\b|\bjethalal\b|\bjetha', '', line).strip()
            # remove @usernames
            line = re.sub(r'@[a-zA-Z0-9_]+', '', line).strip()
            # remove common suffix markers
            line = re.sub(r'\s*\(1\)', '', line)
            # also remove returaj variants explicitly (in addition to other rules)
            line = UNWANTED_RETURAJ_PATTERN.sub('', line).strip()
            clean_lines.append(line)
    final_text = "\n".join([l for l in clean_lines if l.strip()]).strip()
    return f"{final_text}\n\n{watermark}" if watermark else final_text

def clean_and_build_caption(old_caption, fallback_name=""):
    watermark = get_watermark()
    title_val = ""
    if old_caption:
        teacher_found = ""
        teacher_match = re.search(r'(?i)([a-zA-Z]+\s*(?:Sir|Mam|Maam|Madam))', old_caption)
        if teacher_match:
            teacher_found = teacher_match.group(1).strip()
        lines = [line.strip() for line in old_caption.split('\n') if line.strip()]
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
    if old_caption and 'teacher_found' in locals() and teacher_found and teacher_found.lower() not in title_val.lower():
        title_val = f"{title_val} by {teacher_found}"
    caption = f" **Title:** {title_val}" if title_val else " **Title:** Extra Asset"
    if watermark: caption += f"\n\n{watermark}"
    return caption

async def get_video_metadata_async(video_path):
    for ff_binary in ['static_ffprobe', 'ffprobe', '.venv/bin/static_ffprobe']:
        try:
            proc = await asyncio.create_subprocess_exec(
                ff_binary, '-v', 'quiet', '-print_format', 'json', '-show_streams', '-show_format', video_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            data = json.loads(stdout.decode())
            duration = int(float(data.get('format', {}).get('duration', 0)))
            width, height = 0, 0
            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'video':
                    width, height = int(stream.get('width', 0)), int(stream.get('height', 0))
                    break
            return width, height, duration
        except Exception:
            continue
    return 1280, 720, 3600

async def generate_instant_thumb_async(video_path):
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

async def safe_edit_text(msg, text):
    try:
        await msg.edit_text(text)
    except MessageNotModified:
        pass
    except Exception:
        pass

last_edit = {}
async def progress_bar(current, total, action, msg, start, compression_active=False):
    if CURRENT_BATCH_CANCEL:
        raise Exception("Stopped!")
    now = time.time()
    if now - last_edit.get(msg.id, 0) > 1.5 or current == total:
        last_edit[msg.id] = now
        try:
            display_total = total if total and total >= current else current
            pct = (current * 100 / display_total) if display_total else 0
            spd = current / (now - start) if (now - start) > 0 else 0
            bar = "[{0}{1}]".format('🟩' * int(pct/10), '⬜' * (10 - int(pct/10)))
            comp_status = "OFF"
            dashboard = (
                f"⚡ **DEMON PROTECTED HIGH-SPEED ENGINE ACTIVE**\n"
                f"<code>{bar} {pct:.2f}%</code>\n\n"
                f"📊 **Core Engine:** `{action}`\n"
                f"🗜️ **Compression Dynamic:** `{comp_status}`\n"
                f"📁 **Size Engine:** `{current/1048576:.2f} / {display_total/1048576:.2f} MB`\n"
                f"🚀 **Burst Speed:** `{spd/1048576:.2f} MB/s`"
            )
            await safe_edit_text(msg, dashboard)
        except Exception:
            pass

def parse_link_advanced(text_arg):
    clean_text = "".join(text_arg.split()).split("?")[0]
    all_numbers = re.findall(r'\d+', clean_text)

    # Yeh line public (username) aur private dono links ko bina error ke pass hone degi
    if not all_numbers or (len(all_numbers) < 2 and "t.me/c/" in clean_text):
        raise ValueError("Could not extract standard numerical configurations from the URL string.")

    msg_id = int(all_numbers[-1])

    # Agar private link hai (/c/ wala)
    if "t.me/c/" in clean_text:
        chat_candidate = all_numbers[0]
        if len(chat_candidate) < 5 and len(all_numbers) > 2:
            chat_candidate = all_numbers[1]
        chat_id = int(f"-100{chat_candidate}")
    # Agar username wala link ya koi aur format hai
    else:
        parts = [p for p in clean_text.split('/') if p.strip()]
        chat_str = parts[-2]
        chat_id = int(f"-100{chat_str}") if chat_str.isdigit() else chat_str

    return chat_id, msg_id

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

# 🔥 FORCE PEER RESOLVER LOGIC
async def force_sync_peer_async(client_app, raw_chat_id):
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
    # Try with Client Node 1
    try:
        await force_sync_peer_async(user_app_1, raw_chat_id)
        messages = await user_app_1.get_messages(raw_chat_id, msg_id)
        if messages and (messages.media or messages.text):
            return messages, user_app_1
    except Exception:
        pass

    # Try with Client Node 2
    try:
        await force_sync_peer_async(user_app_2, raw_chat_id)
        messages = await user_app_2.get_messages(raw_chat_id, msg_id)
        if messages and (messages.media or messages.text):
            return messages, user_app_2
    except Exception:
        pass

    return None, None

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
        await safe_edit_text(status_msg, f"{prefix}❌ Access Denied: Channel not joined on either account.")
        return

    try:
        raw_filename = msg.document.file_name if msg.document else (msg.video.file_name if msg.video else "file")
        is_pdf_doc = msg.document and msg.document.file_name.lower().endswith('.pdf')
        ext = ".pdf" if is_pdf_doc else ".mp4"
        file_size_bytes = msg.document.file_size if msg.document else (msg.video.file_size if msg.video else 0)
        BATCH_METRICS["bytes"] += file_size_bytes

        caption = build_forward_watermark_caption(msg.caption) if force_forward_mode else clean_and_build_caption(msg.caption, fallback_name=raw_filename)
        final_save_path = os.path.join(LOCAL_TEMP_PATH, f"nitro_{msg.id}{ext}")
        await safe_edit_text(status_msg, f"{prefix}🚀 **Downloading Data Blocks...**")

        start_time_down = time.time()
        p_bar_down = lambda c, t: bot_app.loop.create_task(progress_bar(c, t, "DOWNLOADING", status_msg, start_time_down, False))
        path = await safe_api(worker_client.download_media, msg, file_name=final_save_path, progress=p_bar_down)

        if not path or not os.path.exists(path):
            BATCH_METRICS["skipped"] += 1
            await safe_edit_text(status_msg, f"{prefix}❌ Disk IO Error.")
            return

        await safe_edit_text(status_msg, f"{prefix}📤 **Injecting Pipeline Core...**")
        start_time_up = time.time()
        p_bar_up = lambda c, t: bot_app.loop.create_task(progress_bar(c, t, "UPLOADING", status_msg, start_time_up, False))
        send_kwargs = {"reply_to_message_id": topic_id} if topic_id else {}

        if msg.video or (msg.document and raw_filename.lower().endswith(('.mp4', '.mkv', '.avi')) and not is_pdf_doc):
            v_width, v_height, v_duration = await get_video_metadata_async(path)
            generated_thumb = await generate_instant_thumb_async(path)
            await safe_api(bot_app.send_video, TARGET_CHAT_ID, video=path, thumb=generated_thumb, width=v_width, height=v_height, duration=v_duration, caption=caption, supports_streaming=True, progress=p_bar_up, **send_kwargs)
            if generated_thumb and os.path.exists(generated_thumb):
                os.remove(generated_thumb)
            BATCH_METRICS["videos"] += 1
        else:
            clean_file_name = global_text_cleaner(raw_filename.rsplit('.', 1)[0]) + ext
            await safe_api(bot_app.send_document, TARGET_CHAT_ID, document=path, file_name=clean_file_name, caption=caption, progress=p_bar_up, **send_kwargs)
            BATCH_METRICS["pdfs"] += 1

        await status_msg.delete()

    except Exception as e:
        BATCH_METRICS["skipped"] += 1
        await safe_edit_text(status_msg, f"{prefix}❌ Fault Node: `{str(e)}`")
    finally:
        if path and os.path.exists(path):
            os.remove(path)

def is_admin(_, __, message):
    return message.from_user and message.from_user.id in ADMIN_USER_IDS

@bot_app.on_message(filters.command(["start", "help"]) & filters.create(is_admin))
async def start_cmd(c, m):
    await m.reply("☠️ **DEMON ALL-IN-ONE HYBRID CORE (V50.6)** ☠️\n\n"
                  "📥 **Download Methods (New Files):**\n"
                  "`/batch [Topic_ID] L1 L2` (Bulk)\n"
                  "`/q [Topic_ID] link` (Single Quality)\n\n"
                  "📝 **Note:** Compression and direct-edit features have been removed in this build.")

# enhanced cancel: supports /cancel <batch_id> or /cancel (all)
@bot_app.on_message(filters.command(["cancel", "stop"]) & filters.create(is_admin))
async def stop_cmd(c, m):
    args = m.command
    # If specific batch id provided -> cancel that one
    if len(args) > 1:
        batch_id = args[1]
        entry = BATCH_TASKS.get(batch_id)
        if entry:
            entry["cancel"].set()
            await m.reply(f"🛑 Batch `{batch_id}` cancellation requested.")
        else:
            await m.reply(f"❌ No active batch with id `{batch_id}`.")
        return

    # No id -> cancel all (legacy behavior)
    any_cancelled = False
    for bid, entry in list(BATCH_TASKS.items()):
        entry["cancel"].set()
        any_cancelled = True
    # also set global flag so in-progress progress bars can notice (existing code checks CURRENT_BATCH_CANCEL)
    global CURRENT_BATCH_CANCEL
    CURRENT_BATCH_CANCEL = True
    await m.reply("🛑 Cancellation requested for all active batches." if any_cancelled else "ℹ️ No active batches to cancel.")

@bot_app.on_message(filters.command(["w", "f", "p", "q"]) & filters.create(is_admin))
async def forward_watermark_cmd(c, m):
    global CURRENT_BATCH_CANCEL, BATCH_METRICS
    CURRENT_BATCH_CANCEL = False
    if len(m.command) < 2:
        return await m.reply("❌ Usage: `/q [Topic_ID] link`")
    try:
        BATCH_METRICS = {"total": 1, "videos": 0, "pdfs": 0, "skipped": 0, "bytes": 0}
        words = m.text.split()
        topic_id = None
        link_to_parse = words[1]
        if len(words) > 2 and words[1].isdigit():
            topic_id = int(words[1])
            link_to_parse = words[2]
        chat_id, msg_id = parse_link_advanced(link_to_parse)
        status = await m.reply("⚡ Bypassing asset...")
        await process_nitro_restricted(chat_id, [msg_id], status, topic_id, force_forward_mode=False, single_mode=True)
    except Exception as e:
        await m.reply(f"❌ Single Bypass Fault: `{e}`")

# Start batch -> spawn background worker
@bot_app.on_message(filters.command("batch") & filters.create(is_admin))
async def batch_cmd(c, m):
    try:
        topic_id, links = parse_topic_and_links(m.text)
        if len(links) != 2:
            return await m.reply("❌ Usage: `/batch [Topic_ID] L1 L2`")
        cid, s_id = parse_link_advanced(links[0])
        _, e_id = parse_link_advanced(links[1])
        start, end = min(s_id, e_id), max(s_id, e_id)
        all_msg_ids = list(range(start, end + 1))

        total_tasks = len(all_msg_ids)
        batch_id = uuid.uuid4().hex[:8]

        master_panel = await m.reply(f"🚀 **Batch `{batch_id}` started.** Tracking `{total_tasks}` elements silently...")
        cancel_event = asyncio.Event()

        # start background worker
        task = asyncio.create_task(_batch_worker(batch_id, cid, all_msg_ids, topic_id, master_panel, cancel_event))

        # register
        BATCH_TASKS[batch_id] = {"task": task, "cancel": cancel_event}
        await m.reply(f"✅ Started batch `{batch_id}` — use `/cancel {batch_id}` to stop it.")
    except Exception as e:
        logging.exception("Batch start failed")
        await m.reply(f"❌ Batch start failed: `{e}`")

# helper status proxy (so process_nitro_restricted can call edit_text/delete safely)
class _StatusProxy:
    def __init__(self, panel):
        self._panel = panel
    async def edit_text(self, text):
        try:
            await self._panel.edit_text(text)
        except Exception:
            logging.exception("Failed editing master panel")
    async def delete(self):
        # do not delete the master panel per-item
        return

async def _batch_worker(batch_id, cid, all_msg_ids, topic_id, master_panel, cancel_event):
    try:
        total_tasks = len(all_msg_ids)
        for idx, mid in enumerate(all_msg_ids, 1):
            # stop if requested for this batch
            if cancel_event.is_set():
                await master_panel.edit_text(f"🛑 Batch `{batch_id}` cancelled by admin.")
                break

            await master_panel.edit_text(f"⏳ Batch `{batch_id}` — Processing slot [{idx}/{total_tasks}] — msg_id `{mid}` ...")
            status_proxy = _StatusProxy(master_panel)

            try:
                # per-item timeout to avoid stuck items (900s = 15 minutes)
                await asyncio.wait_for(
                    process_nitro_restricted(cid, [mid], status_proxy, topic_id, f"Slot [{idx}/{total_tasks}] ", force_forward_mode=False, single_mode=False),
                    timeout=900
                )
            except asyncio.TimeoutError:
                BATCH_METRICS["skipped"] += 1
                logging.exception(f"Timeout while processing msg {mid} in batch {batch_id}")
                await master_panel.edit_text(f"❌ Batch `{batch_id}` — Slot [{idx}/{total_tasks}] msg `{mid}` timed out, skipping...")
            except Exception as e:
                BATCH_METRICS["skipped"] += 1
                logging.exception(f"Error processing msg {mid} in batch {batch_id}: {e}")
                await master_panel.edit_text(f"❌ Batch `{batch_id}` — Slot [{idx}/{total_tasks}] Fault: `{str(e)}` — skipping...")
            finally:
                await asyncio.sleep(0.5)

        # final summary for this batch (uses global BATCH_METRICS; can be adapted to per-batch metrics later)
        readable_size = format_size(BATCH_METRICS["bytes"])
        summary_report = (
            f"☠️ **BATCH {batch_id} COMPLETE**\n\n"
            f"📊 **Summary:**\n"
            f"🎬 Videos Extracted: `{BATCH_METRICS['videos']}`\n"
            f"📄 PDFs Extracted: `{BATCH_METRICS['pdfs']}`\n"
            f"❌ Skipped/Failed: `{BATCH_METRICS['skipped']}`\n"
            f"📦 Data Volume: `{readable_size}`"
        )
        await master_panel.edit_text(summary_report)
    except Exception:
        logging.exception(f"Unexpected error in batch worker {batch_id}")
        try:
            await master_panel.edit_text(f"❌ Batch `{batch_id}` terminated with error.")
        except Exception:
            pass
    finally:
        # cleanup: unregister batch
        BATCH_TASKS.pop(batch_id, None)

def parse_topic_and_links(message_text):
    links = re.findall(r'https://t\.me/[^\s]+', message_text)
    words = message_text.split()
    topic_id = None
    for word in words:
        if word.isdigit() and len(word) < 8:
            topic_id = int(word)
            break
    return topic_id, links

async def main():
    global DB_NAME
    DB_NAME = "quantix_recovery.db"
    run_db('''CREATE TABLE IF NOT EXISTS batch_history (chat_id TEXT, msg_id INTEGER, status TEXT, PRIMARY KEY (chat_id, msg_id))''')
    if SESSION_1:
        await user_app_1.start()
    if SESSION_2:
        await user_app_2.start()
    await bot_app.start()
    print(" CORE ENGINE V50.6 UP & ROUTING!")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
