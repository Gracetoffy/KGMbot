from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram import BotCommandScopeChat, BotCommandScopeDefault
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler
)
from telegram.request import HTTPXRequest
from telegram.error import TimedOut, NetworkError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore
from pymongo import MongoClient
from aiohttp import web
import logging
import os
import json
import dateparser
from datetime import datetime
from dotenv import load_dotenv

# ── setup ─────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN  = os.getenv("BOT_TOKEN")
MONGO_URL  = os.getenv("MONGO_URL")
ADMIN_IDS  = [5864151718]
os.environ["TZ"] = "Africa/Lagos"

logging.basicConfig(level=logging.INFO)

# ── scheduler ─────────────────────────────────────────────────
scheduler = AsyncIOScheduler(
    jobstores={
        "default": MongoDBJobStore(
            client=MongoClient(MONGO_URL),
            database="kgmbot",
            collection="jobs"
        )
    },
    job_defaults={"misfire_grace_time": 300}
)

# ── request ───────────────────────────────────────────────────
request = HTTPXRequest(
    connect_timeout=30,
    read_timeout=30,
    write_timeout=30,
    pool_timeout=30,
)

# ── states ────────────────────────────────────────────────────
(
    WAITING_FOR_MESSAGE,
    WAITING_FOR_GROUP_SELECTION,
    WAITING_FOR_REMINDER_COUNT,
    WAITING_FOR_ANNOUNCEMENT,
    WAITING_FOR_ANNOUNCEMENT_TIME,
    WAITING_FOR_REMINDER_TARGET,
) = range(1, 7)

# ── mongodb client ────────────────────────────────────────────
mongo_client = MongoClient(MONGO_URL)
db           = mongo_client["kgmbot"]

users_col  = db["users"]
groups_col = db["groups"]

admins_col = db["admins"]

def load_admins():
    admins = [a["_id"] for a in admins_col.find()]
    if not admins:
        # your ID is always the default super admin
        admins_col.insert_one({"_id": 5864151718})
        return [5864151718]
    return admins

def save_admin(uid: int):
    admins_col.update_one({"_id": uid}, {"$set": {"_id": uid}}, upsert=True)

def remove_admin(uid: int):
    admins_col.delete_one({"_id": uid})

ADMIN_IDS = load_admins()

# ── user helpers ──────────────────────────────────────────────
def load_users():
    return {u["_id"]: {"name": u["name"]} for u in users_col.find()}

def save_user(uid: str, name: str):
    users_col.update_one(
        {"_id": uid},
        {"$set": {"name": name}},
        upsert=True
    )

def get_all_user_ids():
    return [u["_id"] for u in users_col.find()]


# ── group helpers ─────────────────────────────────────────────
def load_groups():
    return {g["_id"]: g["name"] for g in groups_col.find()}

def save_group(gid: str, name: str):
    groups_col.update_one(
        {"_id": gid},
        {"$set": {"name": name}},
        upsert=True
    )

def get_all_group_ids():
    return [g["_id"] for g in groups_col.find()]


# ── load on startup ───────────────────────────────────────────
users  = load_users()
groups = load_groups()


# ── reusable keyboards ────────────────────────────────────────
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Broadcast Message",        callback_data="broadcast")],
        [InlineKeyboardButton("⏰ Set Meeting Reminders",     callback_data="set_reminders")],
        [InlineKeyboardButton("📋 View Scheduled Reminders",  callback_data="view_reminders")],
        [InlineKeyboardButton("👥 List Groups",               callback_data="list_groups")],
        [InlineKeyboardButton("👤 Manage Admins",             callback_data="manage_admins")]

    ])


def back_button():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Back to Menu", callback_data="back_to_menu")]
    ])


# ── health check web server ───────────────────────────────────
async def health_check(request):
    return web.Response(text="✅ KGM Bot is running!")


async def start_web_server():
    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"✅ Web server running on port {port}")


# ── post init ─────────────────────────────────────────────────
async def post_init(application):
    await start_web_server()
    scheduler.start()

    if os.path.exists("users.json"):
        with open("users.json","r") as f :
            local_users = json.load(f)
        for uid, info in local_users.items():
            save_user(uid, info["name"])
        print(f"✅ Loaded {len(local_users)} users from local JSON file.")


    if os.path.exists("groups.json"): 
        with open("groups.json","r") as f :
            local_groups = json.load(f)
        for gid, name in local_groups.items():
            save_group(gid, name)
        print(f"Synced {len(local_groups)} groups from local JSON file to MongoDB.")
    global users,groups
    users = load_users()
    groups = load_groups()
    jobs = scheduler.get_jobs()
    if jobs:
        print(f"✅ Reloaded {len(jobs)} scheduled job(s)")
        for job in jobs:
            print(f"  • {job.id} — next run: {job.next_run_time}")
    else:
        print("No scheduled jobs found.")
   
    await application.bot.set_my_commands([], scope=BotCommandScopeDefault())
    await application.bot.set_my_commands(
        commands=[
            ("start",  "🏠 Start the bot"),
            ("cancel", "❌ Cancel current operation"),
        ],
        scope=BotCommandScopeChat(chat_id=5864151718)
    )


# ── /start ────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid  = str(user.id)
    name = user.full_name or "Unknown"

    if uid not in users:
        users[uid] = {"name": name}
        save_user(uid, name)
        print(f"New user registered: {name} (ID: {uid})")

    if user.id in ADMIN_IDS:
        await update.message.reply_text(
            f"Welcome {name}! You have admin access.",
            reply_markup=main_menu()
        )
    else:
        await update.message.reply_text(
            f"Welcome {name}! You are connected to receive reminders and "
            f"messages from us here at KGM! God bless you."
        )


# ── broadcast message ─────────────────────────────────────────
async def receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text
    target  = context.user_data.get("target", "ALL")

    if target == "ALL":
        chat_ids = [int(gid) for gid in get_all_group_ids()] 
    elif target == "DM":
        chat_ids = [int(uid) for uid in get_all_user_ids()]
    else:
        chat_ids = [int(target)]

    sent = failed = 0
    for chat_id in chat_ids:
        try:
            await context.bot.send_message(chat_id=chat_id, text=message)
            sent += 1
        except Exception as e:
            failed += 1
            print(f"Failed to send to {chat_id}: {e}")

    await update.message.reply_text(
        f"✅ Message broadcasted to {sent} chat(s)!" +
        (f"\n⚠️ Failed: {failed}." if failed else ""),
        reply_markup=back_button()
    )
    return ConversationHandler.END


# ── auto register group ───────────────────────────────────────
async def bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_status = update.my_chat_member.new_chat_member.status
    chat       = update.effective_chat

    if new_status not in ("member", "administrator"):
        return

    groups[str(chat.id)] = chat.title
    save_group(str(chat.id), chat.title)
    print(f"New group registered: {chat.title} ({chat.id})")

    await context.bot.send_message(
        chat_id=chat.id,
        text="✅ KGM bot activated successfully."
    )


# ── /list_groups command ──────────────────────────────────────
async def list_groups_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return
    if not groups:
        await update.message.reply_text("No groups registered yet.")
        return
    lines = [f"• {name}  (ID: {gid})" for gid, name in groups.items()]
    await update.message.reply_text("👥 Registered groups:\n\n" + "\n".join(lines))


    


# ── button click handler ──────────────────────────────────────
async def button_clicked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "broadcast":
        keyboard = []
        for gid, name in groups.items():
            keyboard.append([InlineKeyboardButton(f"👥 {name}", callback_data=f"group_{gid}")])
        keyboard.append([InlineKeyboardButton("📣 All Groups",  callback_data="group_ALL")])
        keyboard.append([InlineKeyboardButton("👤 Send to DMs", callback_data="group_DM")])
        await query.edit_message_text(
            "Who do you want to broadcast to?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return WAITING_FOR_GROUP_SELECTION
    elif query.data == "manage_admins":
    # only super admin can access this
        if update.effective_user.id != 5864151718:
            await query.edit_message_text(
                "⛔ Only the super admin can manage admins.",
                reply_markup=back_button()
            )
            return ConversationHandler.END

        all_users = list(users_col.find())
        print(f"Loaded {len(all_users)} users for admin management.")
        if not all_users:
            await query.edit_message_text(
                "No users registered yet.",
                reply_markup=back_button()
        )
            return ConversationHandler.END

        keyboard = []
        for u in all_users:
            uid  = u["_id"]
            name = u["name"]
            if uid == 5864151718:
                continue  # skip yourself
            if int(uid) in ADMIN_IDS:
                keyboard.append([InlineKeyboardButton(
                    f"❌ Remove {name} as admin",
                    callback_data=f"removeadmin_{uid}"
                )])
            else:
                keyboard.append([InlineKeyboardButton(
                    f"➕ Make {name} admin",
                    callback_data=f"addadmin_{uid}"
                )])

        keyboard.append([InlineKeyboardButton("🏠 Back to Menu", callback_data="back_to_menu")])

        lines = []
        for u in all_users:
            is_admin = int(u["_id"]) in ADMIN_IDS
            lines.append(f"{'✅' if is_admin else '👤'} {u['name']} (ID: {u['_id']})")

        await query.edit_message_text(
            f"👤 Manage Admins:\n\n" + "\n".join(lines) + "\n\nTap to add or remove:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    elif query.data.startswith("addadmin_"):
        uid  = int(query.data.replace("addadmin_", ""))
        name = users.get(str(uid), {}).get("name", "Unknown")
        if uid not in ADMIN_IDS:
            ADMIN_IDS.append(uid)
            save_admin(uid)
        await query.edit_message_text(
            f"✅ {name} is now an admin!",
            reply_markup=back_button()
        )
        return ConversationHandler.END

    elif query.data.startswith("removeadmin_"):
        uid  = int(query.data.replace("removeadmin_", ""))
        name = users.get(str(uid), {}).get("name", "Unknown")
        if uid in ADMIN_IDS:
            ADMIN_IDS.remove(uid)
            remove_admin(uid)
        await query.edit_message_text(
            f"✅ {name} has been removed as admin.",
            reply_markup=back_button()
        )
        return ConversationHandler.END  

       
    elif query.data.startswith("group_"):
        target = query.data.replace("group_", "")
        context.user_data["target"] = target
        await query.edit_message_text("✏️ Enter the message you want to broadcast:")
        return WAITING_FOR_MESSAGE

    elif query.data == "list_groups":
        if update.effective_user.id not in ADMIN_IDS:
            await query.edit_message_text("⛔ You are not authorized.")
            return ConversationHandler.END
        if not groups:
            await query.edit_message_text("No groups registered yet.", reply_markup=back_button())
            return ConversationHandler.END
        lines = [f"• {name}  (ID: {gid})" for gid, name in groups.items()]
        await query.edit_message_text(
            f"👥 Registered Groups ({len(groups)}):\n\n" + "\n".join(lines),
            reply_markup=back_button()
        )
        return ConversationHandler.END

    elif query.data == "set_reminders":
        await query.edit_message_text("🔢 How many announcements do you want to send?")
        return WAITING_FOR_REMINDER_COUNT

    elif query.data == "view_reminders":
        jobs = scheduler.get_jobs()
        if not jobs:
            await query.edit_message_text("No scheduled reminders.", reply_markup=back_button())
            return ConversationHandler.END
        keyboard = []
        for job in jobs:
            text     = job.args[1][:30]
            run_time = job.next_run_time.strftime("%b %d at %H:%M")
            keyboard.append([
                InlineKeyboardButton(
                    f"❌ {run_time} — {text}",
                    callback_data=f"canceljob_{job.id}"
                )
            ])
        keyboard.append([InlineKeyboardButton("🏠 Back to Menu", callback_data="back_to_menu")])
        await query.edit_message_text(
            "📋 Scheduled announcements:\n\nTap one to cancel it:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    

    elif query.data.startswith("canceljob_"):
        job_id = query.data.replace("canceljob_", "")
        job    = scheduler.get_job(job_id)
        if job:
            job.remove()
            await query.edit_message_text(
                "✅ Reminder cancelled!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 View Remaining", callback_data="view_reminders")],
                    [InlineKeyboardButton("🏠 Back to Menu",   callback_data="back_to_menu")],
                ])
            )
        else:
            await query.edit_message_text(
                "⚠️ Reminder not found — already sent or cancelled.",
                reply_markup=back_button()
            )
        return ConversationHandler.END

    elif query.data == "back_to_menu":
        await query.edit_message_text(
            "What would you like to do?",
            reply_markup=main_menu()
        )
        return ConversationHandler.END
    


# ── reminder count ────────────────────────────────────────────
async def receive_reminder_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count_text = update.message.text.strip()
    if not count_text.isdigit() or int(count_text) < 1:
        await update.message.reply_text("❌ Please enter a valid number greater than 0.")
        return WAITING_FOR_REMINDER_COUNT

    context.user_data["reminder_count"]         = int(count_text)
    context.user_data["current_reminder_index"] = 0
    context.user_data["announcements"]          = []

    await update.message.reply_text(
        f"✅ Got it — {count_text} announcement(s).\n\n"
        f"✏️ Enter announcement 1:"
    )
    return WAITING_FOR_ANNOUNCEMENT


# ── announcement text ─────────────────────────────────────────
async def receive_announcement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    index = context.user_data["current_reminder_index"]
    context.user_data["current_announcement_text"] = update.message.text

    await update.message.reply_text(
        f"🕐 When should announcement {index + 1} be sent?\n\n"
        "Use 24hr time or specify AM/PM:\n"
        "• Tomorrow 14:00\n"
        "• June 5th at 9:00 AM\n"
        "• 25/12/2025 21:00"
    )
    return WAITING_FOR_ANNOUNCEMENT_TIME


# ── announcement time ─────────────────────────────────────────
async def receive_announcement_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw         = update.message.text.strip()
    parsed_time = dateparser.parse(raw, settings={
        "TIMEZONE":                "Africa/Lagos",
        "RETURN_AS_TIMEZONE_AWARE": False,
        "PREFER_DATES_FROM":       "future",
    })

    if not parsed_time:
        await update.message.reply_text(
            "❌ Couldn't understand that. Try again:\n\n"
            "Examples: Tomorrow 14:00, June 5th 9:00 AM, 25/12/2025 21:00"
        )
        return WAITING_FOR_ANNOUNCEMENT_TIME

    if parsed_time < datetime.now():
        await update.message.reply_text(
            f"❌ {parsed_time.strftime('%b %d at %H:%M')} is in the past. Enter a future time:"
        )
        return WAITING_FOR_ANNOUNCEMENT_TIME

    time_str    = parsed_time.strftime("%Y-%m-%d %H:%M")
    display_str = parsed_time.strftime("%b %d at %H:%M")
    index       = context.user_data["current_reminder_index"]
    count       = context.user_data["reminder_count"]
    text        = context.user_data["current_announcement_text"]

    context.user_data["announcements"].append({"text": text, "time": time_str, "display": display_str})
    context.user_data["current_reminder_index"] = index + 1

    if index + 1 < count:
        await update.message.reply_text(
            f"✅ Saved! Announcement {index + 1} set for {display_str}\n\n"
            f"✏️ Enter announcement {index + 2}:"
        )
        return WAITING_FOR_ANNOUNCEMENT

    summary = "\n".join(
        f"• {a['display']} → {a['text'][:40]}{'...' if len(a['text']) > 40 else ''}"
        for a in context.user_data["announcements"]
    )
    await update.message.reply_text(
        f"✅ All {count} announcements saved!\n\n"
        f"📋 Summary:\n{summary}\n\n"
        f"👥 Who should receive these?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📣 All Groups",  callback_data="rtarget_ALL")],
            [InlineKeyboardButton("👤 Send to DMs", callback_data="rtarget_DM")],
            [InlineKeyboardButton("📣👤 Both",       callback_data="rtarget_EVERYONE")],
        ])
    )
    return WAITING_FOR_REMINDER_TARGET


# ── reminder target ───────────────────────────────────────────
async def receive_reminder_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    target        = query.data.replace("rtarget_", "")
    announcements = context.user_data.get("announcements", [])
    scheduled     = []

    if target == "ALL":
        chat_ids = [int(gid) for gid in get_all_group_ids()]
    elif target == "DM":
        chat_ids = [int(uid) for uid in get_all_user_ids()]
    else:
        chat_ids = [int(gid) for gid in get_all_group_ids()] + \
                   [int(uid) for uid in get_all_user_ids()]

    for i, a in enumerate(announcements):
        run_time = dateparser.parse(
            a["time"],
            settings={"TIMEZONE": "Africa/Lagos", "RETURN_AS_TIMEZONE_AWARE": False}
        )
        if not run_time:
            continue

        scheduler.add_job(
            send_announcement,
            trigger="date",
            run_date=run_time,
            id=f"announcement_{run_time.strftime('%Y%m%d%H%M')}_{i}",
            replace_existing=True,
            args=[chat_ids, a["text"]]
        )
        scheduled.append(
            f"• {a['display']} → {a['text'][:40]}{'...' if len(a['text']) > 40 else ''}"
        )

    target_label = {"ALL": "All Groups", "DM": "DMs", "EVERYONE": "Groups and DMs"}.get(target, target)
    await query.edit_message_text(
        f"✅ {len(scheduled)} announcement(s) scheduled for {target_label}!\n\n"
        f"📋 Schedule:\n" + "\n".join(scheduled),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 View Scheduled Reminders", callback_data="view_reminders")],
            [InlineKeyboardButton("🏠 Back to Menu",             callback_data="back_to_menu")],
        ])
    )
    return ConversationHandler.END


# ── send announcement job ─────────────────────────────────────
async def send_announcement(chat_ids: list, text: str):
    bot = Bot(token=BOT_TOKEN)
    async with bot:
        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:
                print(f"Failed to send to {chat_id}: {e}")


# ── cancel ────────────────────────────────────────────────────
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operation cancelled.", reply_markup=main_menu())
    return ConversationHandler.END


# ── error handler ─────────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    if isinstance(error, TimedOut):
        print("⚠️ Timed out — retrying automatically...")
        return
    if isinstance(error, NetworkError):
        print("⚠️ Network error — retrying automatically...")
        return
    print(f"❌ Unexpected error: {error}")


# ── conversation handler ──────────────────────────────────────
conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(button_clicked)],
    states={
        WAITING_FOR_GROUP_SELECTION:   [CallbackQueryHandler(button_clicked)],
        WAITING_FOR_MESSAGE:           [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_message)],
        WAITING_FOR_REMINDER_COUNT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reminder_count)],
        WAITING_FOR_ANNOUNCEMENT:      [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_announcement)],
        WAITING_FOR_ANNOUNCEMENT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_announcement_time)],
        WAITING_FOR_REMINDER_TARGET:   [CallbackQueryHandler(receive_reminder_target, pattern="^rtarget_")],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    allow_reentry=True
)

# ── app ───────────────────────────────────────────────────────
app = ApplicationBuilder()\
    .token(BOT_TOKEN)\
    .request(request)\
    .post_init(post_init)\
    .build()

app.add_error_handler(error_handler)
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("cancel", cancel))
app.add_handler(CommandHandler("list_groups", list_groups_cmd))
app.add_handler(ChatMemberHandler(bot_added, ChatMemberHandler.MY_CHAT_MEMBER))
app.add_handler(conv_handler)

app.run_polling(
    poll_interval=3,
    timeout=30,
    drop_pending_updates=True
)
