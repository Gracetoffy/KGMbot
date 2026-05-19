from multiprocessing import context
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler,ConversationHandler, MessageHandler, filters, ContextTypes,ChatMemberHandler
from telegram.request import HTTPXRequest
from telegram import BotCommand, BotCommandScopeChat
import logging
from telegram import Bot
from telegram.error import TimedOut, NetworkError
import os
from dotenv import load_dotenv
from datetime import datetime, time as dt_time
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import json
import dateparser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from aiohttp import web




request = HTTPXRequest(
    connect_timeout=30,
    read_timeout=30,
    write_timeout=30,
    pool_timeout=30,
)

load_dotenv()

BOT_TOKEN=os.getenv("BOT_TOKEN")

WAITING_FOR_MESSAGE = 1
WAITING_FOR_GROUP_SELECTION = 2
WAITING_FOR_REMINDER_COUNT = 3
WAITING_FOR_ANNOUNCEMENT = 4
WAITING_FOR_ANNOUNCEMENT_TIME = 5
WAITING_FOR_REMINDER_TARGET = 6


from apscheduler.jobstores.mongodb import MongoDBJobStore
from pymongo import MongoClient

MONGO_URL = os.getenv("MONGO_URL")

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



USER_FILE = "users.json"
def load_users():
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "r") as f:
            return json.load(f)
    return {}
def save_users(users):
    with open(USER_FILE, "w") as f:
        json.dump(users, f,indent=2)
users= load_users()


GROUP_DATA_FILE = "groups.json"
def load_groups():
    if os.path.exists(GROUP_DATA_FILE):
        with open(GROUP_DATA_FILE, "r") as f:
            return json.load(f)
    return {}
def save_groups(groups):
    with open(GROUP_DATA_FILE, "w") as f:
        json.dump(groups, f,indent=2)

groups = load_groups()


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







async def post_init(application):
    await start_web_server() 
    scheduler.start()
      # ✅ set the command menu
    await application.bot.set_my_commands([
        ("start",       "🏠 Start the bot"),
        ("cancel",      "❌ Cancel current operation"),
        ("list_groups", "👥 List all registered groups"),
    ])
    await application.bot.set_my_commands(
        commands=[
            ("start",       "🏠 Start the bot"),
            ("cancel",      "❌ Cancel current operation"),
            ("list_groups", "👥 List all registered groups"),
        ],
        scope=BotCommandScopeChat(chat_id=5864151718)  # your admin ID
    )


#start command handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user= update.effective_user
    uid= str(user.id)
    name= user.full_name or "Unknown"

    if uid not in users:
        users[uid]={"name": name}
        save_users(users)
        print(f"New user registered: {name} (Id:{uid})")
    
    if user.id in [5864151718]:

        keyboard = [
        [InlineKeyboardButton("📢 Broadcast Message",       callback_data="broadcast")],
        [InlineKeyboardButton("⏰ Set Meeting Reminders",    callback_data="set_reminders")],
        [InlineKeyboardButton("📋 View Scheduled Reminders", callback_data="view_reminders")],
        [InlineKeyboardButton("👥 List Groups",              callback_data="list_groups")],
    ]
        await update.message.reply_text(f"Welcome {name}! You have admin access.", reply_markup=InlineKeyboardMarkup(keyboard))


    else:
        await update.message.reply_text(f"Welcome! {name},You are connected to receive reminders and messages from us here at KGM! ,God bless you ")



#message handler to receive the message to broadcast
async def receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message.text
    target= context.user_data.get("target","ALL")

    if target=="ALL":
        chat_ids = [5864151718] +[int(gid) for gid in groups.keys()] 
    elif target =="DM":
        chat_ids = [int(uid) for uid in users.keys()]
    else:
        chat_ids = [int(target)]
    sent=failed= 0 
    for chat_id in chat_ids:
        try:
            await context.bot.send_message(chat_id=chat_id, text=message)
            sent+=1
        except Exception as e:
            failed+=1
            print(f"Failed to send message to {chat_id}: {e}")
    await update.message.reply_text(
    f"✅ Message broadcasted successfully to {sent} chat(s)!" +
    (f"\n⚠️ Failed: {failed}." if failed else ""),
    reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Back to Menu", callback_data="back_to_menu")]
    ])
)
    return ConversationHandler.END


# registers every group the bot is added to and saves it in a json file for future use
async def bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    group_id = chat.id
    group_name = chat.title
    groups[str(group_id)] = group_name
    save_groups(groups)
    print(f"New group registered: {group_name} ({group_id})")
    print(f"All groups: {groups}")
    await context.bot.send_message(
        chat_id=group_id,
        text="KGM bot activated successfully."
    )

#registers grup that already have the bot and saves it in a json file for future use
async def register_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat= update.effective_chat

    if chat.type not in ("group","supergroup"):
        await update.message.reply_text("This command will only work in group chats")
        return
    groups[str(chat.id)]=chat.title
    save_groups(groups)
    await update.message.reply_text(f"Group '{chat.title}' registered successfully!")


async def list_groups(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != 5864151718:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if not groups:
        await update.message.reply_text("No groups registered yet.")
        return
    lines = [f"{name} (Id:{gid})"for gid, name in groups.items()]
    await update.message.reply_text("Registered groups:\n" + "\n".join(lines), reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Back to Menu", callback_data="back_to_menu") ]
    ]))




#button click handler
async def button_clicked(update:Update, context:ContextTypes.DEFAULT_TYPE)->None:
     query= update.callback_query
     await query.answer()
     if query.data=="broadcast":
         if not groups:
            await query.message.reply_text("No groups registered yet.")
            return ConversationHandler.END
         
         keyboard=[]
         for gid,name in groups.items():
              keyboard.append([InlineKeyboardButton(f"👥 {name}", callback_data=f"group_{gid}")])

        # bottom options
         keyboard.append([InlineKeyboardButton("📣 All Groups",  callback_data="group_ALL")])
         keyboard.append([InlineKeyboardButton("👤 Send to DMs", callback_data="group_DM")])

         await query.edit_message_text(
            "Who do you want to broadcast to?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
         return WAITING_FOR_GROUP_SELECTION
    
    
     elif query.data.startswith("group_"):
         target= query.data.replace("group_","")
         context.user_data["target"]= target
         await query.edit_message_text("Please enter the message you want to broadcast:")
         return WAITING_FOR_MESSAGE
    
    
     elif query.data=="list_groups":
        if update.effective_user.id != 5864151718:
            await query.message.reply_text("You are not authorized to use this command.")
            return ConversationHandler.END
        if not groups:
            await query.message.reply_text("No groups registered yet.")
            return
        lines = [f"{name} (Id:{gid})"for gid, name in groups.items()]
        await query.edit_message_text("Registered groups:\n" + "\n".join(lines))    
        return ConversationHandler.END
     
     elif query.data=="set_reminders":
         await query.edit_message_text("How many times do you want to send the reminder?")
         return WAITING_FOR_REMINDER_COUNT
     
    
     elif query.data=="view_reminders":
         jobs = scheduler.get_jobs()
         if not jobs:
            await query.edit_message_text("No scheduled reminders.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Back to Menu", callback_data="back_to_menu")]
            ]))
            return ConversationHandler.END
         keyboard=[]
         for job in jobs:
             text = job.args[1][:30]
             run_time = job.next_run_time.strftime("%Y-%m-%d %H:%M")
             keyboard.append([
                 InlineKeyboardButton(f"{text} at {run_time}", callback_data=f"canceljob_{job.id}")])
         keyboard.append([
             InlineKeyboardButton("back", callback_data="back_to_menu") 
         ])
         await query.edit_message_text(
             "Scheduled announcement :\n\n Tap one to cancel it:", reply_markup=InlineKeyboardMarkup(keyboard)
         )
         return ConversationHandler.END
     elif query.data.startswith("canceljob_"):
         job_id = query.data.replace("canceljob_", "")
         job    = scheduler.get_job(job_id)

         if job:
            job.remove()
            await query.edit_message_text(
                f"✅ Reminder cancelled successfully!\n\n"
                f"What would you like to do?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 View Remaining", callback_data="view_reminders")],
                    [InlineKeyboardButton("🏠 Back to Menu",   callback_data="back_to_menu")],
                ])
            )
         else:
            await query.edit_message_text(
                "⚠️ Reminder not found — it may have already been sent or cancelled.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Back to Menu", callback_data="back_to_menu")]
                ])
            )
         return ConversationHandler.END

     elif query.data == "back_to_menu":
        keyboard = [
        [InlineKeyboardButton("📢 Broadcast Message",       callback_data="broadcast")],
        [InlineKeyboardButton("⏰ Set Meeting Reminders",    callback_data="set_reminders")],
        [InlineKeyboardButton("📋 View Scheduled Reminders", callback_data="view_reminders")],
        [InlineKeyboardButton("👥 List Groups",              callback_data="list_groups")],
    ]
        await query.edit_message_text(
        "What would you like to do?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
     return ConversationHandler.END

async def receive_reminder_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count_text = update.message.text
    if not count_text.isdigit() or int(count_text)<1:
        await update.message.reply_text("Please enter a valid number greater than 0.")
        return WAITING_FOR_REMINDER_COUNT
    context.user_data["reminder_count"] = int(count_text)
    context.user_data["current_reminder_index"] = 0
    context.user_data["announcements"] =[]
    await update.message.reply_text(
        f"Got it — {count_text} announcement(s).\n\n"
        f"Enter announcement 1:"
    )
    return WAITING_FOR_ANNOUNCEMENT

async def receive_announcement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    index = context.user_data["current_reminder_index"]
    context.user_data["current_announcement_text"] = update.message.text
    await update.message.reply_text(
        f"Enter time for announcement {index+1} to be sent ? \n\n"
        "⚠️ Use 24hr time — 14:00 or indicate wheter AM or PM — 2:00 PM. You can also specify a date like 'tomorrow at 9am' or 'June 5th at 14:00'."
                                    )
    return WAITING_FOR_ANNOUNCEMENT_TIME


async def receive_announcement_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw= update.message.text.strip()
    parsed_time = dateparser.parse(raw,
        settings={"TIMEZONE": "Africa/Lagos", 
                  "RETURN_AS_TIMEZONE_AWARE": False,
                  "PREFER_DATES_FROM": "future",})
    if not parsed_time:
        await update.message.reply_text("Sorry, I couldn't understand that time. Please try again.")
        return WAITING_FOR_ANNOUNCEMENT_TIME
    
    if parsed_time < datetime.now():
        await update.message.reply_text("The time you entered has already passed. Please enter a future time.")
        return WAITING_FOR_ANNOUNCEMENT_TIME

    time_str= parsed_time.strftime("%Y-%m-%d %H:%M")
    display_str  = parsed_time.strftime("%b %d at %H:%M")
    index = context.user_data["current_reminder_index"]
    count= context.user_data["reminder_count"]
    announcement_text = context.user_data["current_announcement_text"]
    
    context.user_data["announcements"].append(
        {"text": announcement_text, "time": time_str}
    )
    context.user_data["current_reminder_index"] = index + 1
    if index+1 < count:
        await update.message.reply_text(
            f"Announcement {index+1} scheduled for {time_str}.\n\n"
            f"Enter announcement {index+2}:"
        )
        return WAITING_FOR_ANNOUNCEMENT
    keyboard=[
        [InlineKeyboardButton("All Groups",callback_data="rtarget_ALL")],
        [InlineKeyboardButton("Send to DMs",callback_data="rtarget_DM")],
        [InlineKeyboardButton("Both DM and Groups",callback_data="rtarget_EVERYONE")]
    ]
    summary = "\n\n".join(
        [f"{a['text'][:40]} at {a['time']}{'...' if len(a['text'])>40 else ''}"
        for i,a in enumerate(context.user_data["announcements"])]
        )
    await update.message.reply_text(
        f"All announcements scheduled:\n\n{summary}\n\n"
        "Where do you want to send these reminders?", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_FOR_REMINDER_TARGET

async def receive_reminder_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    target = query.data.replace("rtarget_","")
    announcements= context.user_data.get("announcements",[])
    
    if target=="ALL":
        chat_ids = [5864151718] +[int(gid) for gid in groups.keys()]
    elif target=="DM":
        chat_ids = [int(uid) for uid in users.keys()]
    else:
        chat_ids = [5864151718] +[int(gid) for gid in groups.keys()] + [int(uid) for uid in users.keys()]

    scheduled = []


    for a in announcements:
        run_time = dateparser.parse(
            a['time'],
            settings={"TIMEZONE": "Africa/Lagos", "RETURN_AS_TIMEZONE_AWARE": False, "PREFER_DATES_FROM": "future"}
        )
        if  not run_time:
            continue
        
        scheduler.add_job(
                send_announcement,
                trigger = "date",
                run_date = run_time,
                id=f"announcement_{run_time.strftime('%Y%m%d%H%M%S')}_{len(scheduled)}",
                replace_existing=True,
                args=[chat_ids, a['text']])
            
        scheduled.append(f"{a['text'][:40]} at {a['time']} to {len(chat_ids)} targets{'...' if len(a['text'])>40 else ''}")
        target_label ={"ALL": "All Groups", "DM": "DMs", "EVERYONE": "Groups and DMs"}.get(target,target)
    keyboard = [
     [InlineKeyboardButton("📋 View Scheduled Reminders", callback_data="view_reminders")],
     [InlineKeyboardButton("🏠 Back to Menu",             callback_data="back_to_menu")],
]
    await query.edit_message_text(
        f"Scheduled {len(announcements)} announcement(s) to be sent to {target_label}:\n\n" + "\n\n".join(scheduled), reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return ConversationHandler.END
async def send_announcement(chat_ids: list, text: str):
    bot= Bot(token=BOT_TOKEN)
    async with bot:
        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:
                print(f"Failed to send to {chat_id}: {e}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END



#error handler
logging.basicConfig(level=logging.INFO)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error

    if isinstance(error, TimedOut):
        print("⚠️ Timed out — retrying automatically...")
        return  # bot keeps running, no crash

    if isinstance(error, NetworkError):
        print("⚠️ Network error — retrying automatically...")
        return

    # log anything else
    print(f"❌ Unexpected error: {error}")





conv_handler= ConversationHandler(
    entry_points=[CallbackQueryHandler(button_clicked)],
    states={
        WAITING_FOR_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_message)],
        WAITING_FOR_GROUP_SELECTION: [CallbackQueryHandler(button_clicked)],  
        WAITING_FOR_REMINDER_COUNT:[MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reminder_count)],
        WAITING_FOR_ANNOUNCEMENT:[MessageHandler(filters.TEXT & ~filters.COMMAND, receive_announcement)],
        WAITING_FOR_ANNOUNCEMENT_TIME:[MessageHandler(filters.TEXT & ~filters.COMMAND, receive_announcement_time)],
        WAITING_FOR_REMINDER_TARGET:[CallbackQueryHandler(receive_reminder_target, pattern="^rtarget_")],
    },
    fallbacks=[CommandHandler("cancel", cancel)]
)

    
app = ApplicationBuilder()\
    .token(BOT_TOKEN)\
    .request(request)\
    .post_init(post_init)\
    .build()

app.add_error_handler(error_handler)
app.add_handler(CommandHandler("start", start))
app.add_handler(
    ChatMemberHandler(
        bot_added,
        ChatMemberHandler.MY_CHAT_MEMBER
    )
)
app.add_handler(conv_handler)
app.add_handler(CommandHandler("register", register_group))
app.add_handler(CommandHandler("list_groups", list_groups))
app.run_polling(
    poll_interval= 3,
    timeout= 30,
    drop_pending_updates= True
)       
        