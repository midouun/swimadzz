import asyncio
import sqlite3
import time
import csv
import io
import html
import os
from dotenv import load_dotenv  # مكتبة قراءة الملفات السرية
from pyrogram import Client, filters, idle, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.raw import functions, types as raw_types

# --- تحميل المتغيرات السرية ---
load_dotenv()

# قراءة المعلومات من البيئة أو ملف .env
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
# تحويل نص الآيديات إلى قائمة أرقام
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# --- قاعدة البيانات ---
conn = sqlite3.connect('swima.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER, session_name TEXT, start_time INTEGER, is_active INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS attendance (user_id INTEGER, user_name TEXT, session_id INTEGER, duration_seconds INTEGER, UNIQUE(user_id, session_id))''')
c.execute('''CREATE TABLE IF NOT EXISTS groups (group_id INTEGER PRIMARY KEY, title TEXT)''')
conn.commit()

# --- المتغيرات والعملاء ---
active_trackers = {}
user_states = {}
user_app = Client("swima_user", api_id=API_ID, api_hash=API_HASH)
bot_app = Client("swima_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

def format_time(seconds):
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"

# --- دالة متطورة لجلب الاسم والآيدي معاً ---
async def get_full_participants(client, chat_id):
    try:
        peer = await client.resolve_peer(chat_id)
        chat_full = await client.invoke(functions.channels.GetFullChannel(channel=peer))
        
        if not chat_full.full_chat.call:
            return []

        call_record = chat_full.full_chat.call
        input_call = raw_types.InputGroupCall(id=call_record.id, access_hash=call_record.access_hash)
        
        final_results = []
        user_map = {}

        # المحاولة 1: Snapshot
        try:
            req_state = functions.phone.GetGroupCall(call=input_call, limit=100)
            res_state = await client.invoke(req_state)
            for u in res_state.users:
                user_map[u.id] = u.first_name or "Unknown"
            for p in res_state.participants:
                uid = getattr(p, 'user_id', None) or getattr(getattr(p, 'peer', None), 'user_id', None)
                if uid:
                    name = user_map.get(uid, "مستخدم")
                    final_results.append({'id': uid, 'name': name})
        except: pass 

        # المحاولة 2: List
        if not final_results:
            try:
                req_list = functions.phone.GetGroupCallParticipants(
                    call=input_call, ids=[], sources=[], offset="", limit=100
                )
                res_list = await client.invoke(req_list)
                for u in res_list.users:
                    user_map[u.id] = u.first_name or "Unknown"
                for p in res_list.participants:
                    uid = getattr(p, 'user_id', None) or getattr(getattr(p, 'peer', None), 'user_id', None)
                    if uid:
                        name = user_map.get(uid, "مستخدم")
                        final_results.append({'id': uid, 'name': name})
            except: pass

        seen = set()
        unique = []
        for item in final_results:
            if item['id'] not in seen:
                seen.add(item['id'])
                unique.append(item)

        return unique

    except Exception as e:
        print(f"❌ خطأ في الجلب: {e}")
        return []

# --- حلقة التتبع ---
async def track_voice_chat(client, group_id, session_id):
    print(f"📡 [نظام] بدء الجلسة {session_id}...")
    while session_id in active_trackers.values():
        try:
            people = await get_full_participants(client, group_id)
            if people:
                print(f"✅ [مراقبة] {len(people)} حاضر.")
                for person in people:
                    uid = person['id']
                    u_name = person['name']
                    try:
                        c.execute("""
                            INSERT INTO attendance (user_id, user_name, session_id, duration_seconds)
                            VALUES (?, ?, ?, 10)
                            ON CONFLICT(user_id, session_id) 
                            DO UPDATE SET duration_seconds = duration_seconds + 10, user_name = ?
                        """, (uid, u_name, session_id, u_name))
                    except: pass
                conn.commit()
            else:
                pass 
        except Exception as e:
            print(f"❌ خطأ: {e}")
        await asyncio.sleep(10)

# --- الواجهة ---
def get_main_menu():
    btns = [
        [InlineKeyboardButton("🆕 بدء مراجعة جديدة", callback_data="start_flow")],
        [InlineKeyboardButton("📂 الأرشيف", callback_data="list_sessions")],
        [InlineKeyboardButton("⚙️ المجموعات", callback_data="manage_groups")],
        [InlineKeyboardButton("♻️ تحديث", callback_data="refresh")]
    ]
    for gid, sid in active_trackers.items():
        try:
            c.execute("SELECT title FROM groups WHERE group_id=?", (gid,))
            t = c.fetchone()
            name = t[0] if t else str(gid)
        except: name = str(gid)
        btns.insert(0, [InlineKeyboardButton(f"🛑 إيقاف: {name}", callback_data=f"stop_{gid}")])
    return InlineKeyboardMarkup(btns)

@bot_app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    if message.from_user.id in ADMIN_IDS:
        await message.reply("👋 **نظام SWIMA - GitHub Version**", reply_markup=get_main_menu())

@bot_app.on_callback_query()
async def callback(client, q):
    if q.from_user.id not in ADMIN_IDS: return
    data = q.data
    uid = q.from_user.id
    
    if data == "refresh" or data == "main_menu":
        user_states.pop(uid, None)
        await q.message.edit_text("القائمة الرئيسية:", reply_markup=get_main_menu())

    elif data == "manage_groups":
        c.execute("SELECT group_id, title FROM groups")
        grps = c.fetchall()
        txt = "المجموعات:\n" + "\n".join([f"- {g[1]}" for g in grps])
        await q.message.edit_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ إضافة", callback_data="add_grp"), InlineKeyboardButton("رجوع", callback_data="main_menu")]]))

    elif data == "add_grp":
        user_states[uid] = "wait_gid"
        await q.message.edit_text("أرسل آيدي المجموعة الآن:")

    elif data == "start_flow":
        c.execute("SELECT group_id, title FROM groups")
        grps = c.fetchall()
        if not grps: return await q.answer("أضف مجموعة أولاً", show_alert=True)
        if len(grps) == 1:
            gid = grps[0][0]
            if gid in active_trackers: return await q.answer("الجلسة تعمل!", show_alert=True)
            user_states[uid] = {"state": "wait_name", "gid": gid}
            await q.message.edit_text(f"📝 بدء في: **{grps[0][1]}**\nأرسل اسم المحاضرة:")
        else:
            btns = []
            for g in grps:
                if g[0] not in active_trackers: btns.append([InlineKeyboardButton(g[1], callback_data=f"sel_{g[0]}")])
            btns.append([InlineKeyboardButton("رجوع", callback_data="main_menu")])
            await q.message.edit_text("اختر المجموعة:", reply_markup=InlineKeyboardMarkup(btns))

    elif data.startswith("sel_"):
        gid = int(data.split("_")[1])
        user_states[uid] = {"state": "wait_name", "gid": gid}
        await q.message.edit_text("أرسل اسم المحاضرة:")

    elif data.startswith("stop_"):
        gid = int(data.split("_")[1])
        if gid in active_trackers:
            sid = active_trackers.pop(gid)
            c.execute("UPDATE sessions SET is_active=0 WHERE id=?", (sid,))
            conn.commit()
            btns = [
                [InlineKeyboardButton("📂 تحميل Excel", callback_data=f"rep_{sid}")],
                [InlineKeyboardButton("📜 القائمة النصية", callback_data=f"txt_{sid}")],
                [InlineKeyboardButton("🏠 الرئيسية", callback_data="main_menu")]
            ]
            await q.message.edit_text(f"🛑 تم إيقاف الجلسة.\nاختر طريقة عرض التقرير:", reply_markup=InlineKeyboardMarkup(btns))

    elif data == "list_sessions":
        c.execute("SELECT id, session_name FROM sessions ORDER BY id DESC LIMIT 5")
        btns = [[InlineKeyboardButton(r[1], callback_data=f"sess_{r[0]}")] for r in c.fetchall()]
        btns.append([InlineKeyboardButton("رجوع", callback_data="main_menu")])
        await q.message.edit_text("📂 الأرشيف:", reply_markup=InlineKeyboardMarkup(btns))

    elif data.startswith("sess_"):
        sid = int(data.split("_")[1])
        c.execute("SELECT session_name FROM sessions WHERE id=?", (sid,))
        sname = c.fetchone()[0]
        btns = [
            [InlineKeyboardButton("📂 تحميل Excel", callback_data=f"rep_{sid}")],
            [InlineKeyboardButton("📜 القائمة النصية", callback_data=f"txt_{sid}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="list_sessions")]
        ]
        await q.message.edit_text(f"📊 خيارات التقرير لـ: {sname}", reply_markup=InlineKeyboardMarkup(btns))

    elif data.startswith("rep_"):
        sid = int(data.split("_")[1])
        await send_excel_report(client, q.message.chat.id, sid)
        await q.answer()

    elif data.startswith("txt_"):
        sid = int(data.split("_")[1])
        await send_text_list(client, q.message.chat.id, sid)
        await q.answer()

@bot_app.on_message(filters.text & filters.private)
async def msg(client, message):
    if message.from_user.id not in ADMIN_IDS: return
    st = user_states.get(message.from_user.id)
    if not st: return

    if st == "wait_gid":
        try:
            gid = int(message.text)
            chat = await user_app.get_chat(gid)
            c.execute("INSERT OR REPLACE INTO groups VALUES (?,?)", (gid, chat.title))
            conn.commit()
            user_states.pop(message.from_user.id)
            await message.reply(f"✅ تم: {chat.title}", reply_markup=get_main_menu())
        except Exception as e: await message.reply(f"❌ خطأ: {e}")

    elif isinstance(st, dict) and st["state"] == "wait_name":
        nm = message.text
        gid = st["gid"]
        user_states.pop(message.from_user.id)
        tm = int(time.time())
        c.execute("INSERT INTO sessions (group_id, session_name, start_time, is_active) VALUES (?,?,?,1)", (gid, nm, tm))
        conn.commit()
        sid = c.lastrowid
        active_trackers[gid] = sid
        asyncio.create_task(track_voice_chat(user_app, gid, sid))
        await message.reply(f"✅ بدأ: {nm}", reply_markup=get_main_menu())

async def send_excel_report(client, chat_id, sid):
    c.execute("SELECT user_name, user_id, duration_seconds FROM attendance WHERE session_id=? ORDER BY duration_seconds DESC", (sid,))
    data = c.fetchall()
    if not data: return await client.send_message(chat_id, "القائمة فارغة")

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['Name', 'ID', 'Minutes', 'Time'])
    for row in data:
        writer.writerow([row[0], row[1], round(row[2]/60, 2), format_time(row[2])])
    
    out.seek(0)
    f = io.BytesIO(out.getvalue().encode('utf-8-sig'))
    f.name = f"Report_{sid}.csv"
    await client.send_document(chat_id, document=f, caption=f"📊 تقرير Excel: {len(data)} مشارك")

async def send_text_list(client, chat_id, sid):
    c.execute("SELECT user_name, user_id, duration_seconds FROM attendance WHERE session_id=? ORDER BY duration_seconds DESC", (sid,))
    data = c.fetchall()
    c.execute("SELECT session_name FROM sessions WHERE id=?", (sid,))
    sname = c.fetchone()[0]

    if not data:
        await client.send_message(chat_id, "القائمة فارغة.")
        return

    chunk = f"📋 **قائمة الحضور: {sname}**\n\n"
    
    for i, (name, uid, secs) in enumerate(data, 1):
        clean_name = " ".join(name.split())
        if not clean_name: clean_name = "مستخدم"
        safe_name = html.escape(clean_name)
        line = f'{i}. <a href="tg://user?id={uid}">{safe_name}</a> \u200E- ⏱ <b>{format_time(secs)}</b>\n'
        
        if len(chunk) + len(line) > 4000:
            await client.send_message(chat_id, chunk, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
            chunk = ""
        chunk += line
    
    if chunk:
        await client.send_message(chat_id, chunk, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)

async def main():
    print("--- SWIMA SYSTEM STARTED ---")
    await user_app.start()
    try:
        async for _ in user_app.get_dialogs(limit=50): pass
    except: pass
    await bot_app.start()
    print("READY.")
    await idle()
    await user_app.stop()
    await bot_app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
