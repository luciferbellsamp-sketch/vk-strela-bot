import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from vkbottle import Bot, Keyboard, KeyboardButtonColor, GroupEventType, Callback, GroupTypes
from vkbottle.bot import Message

# =========================================================
# CONFIG
# Railway Variables:
# TOKEN=vk token
# CHAT_ID=466              # optional, id беседы без 2000000000
# MODERATOR_IDS=1,2,3      # id модеров через запятую
# DB_PATH=strela_bot.db    # optional
# =========================================================
TOKEN = os.getenv("TOKEN", "")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
MODERATOR_IDS = {
    int(x.strip()) for x in os.getenv("MODERATOR_IDS", "").split(",") if x.strip().isdigit()
}
DB_PATH = os.getenv("DB_PATH", "strela_bot.db")

if not TOKEN:
    raise RuntimeError("TOKEN не найден в переменных окружения")

bot = Bot(token=TOKEN)
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row


# =========================================================
# DB
# =========================================================
def init_db() -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS strels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            peer_id INTEGER NOT NULL,
            creator_id INTEGER NOT NULL,
            count_slots INTEGER NOT NULL,
            map_name TEXT NOT NULL,
            event_time TEXT NOT NULL,
            comment TEXT DEFAULT '',
            message_id INTEGER,
            created_at INTEGER NOT NULL,
            event_date TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS strel_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strel_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            slot_type TEXT NOT NULL CHECK(slot_type IN ('main', 'reserve')),
            position INTEGER NOT NULL,
            UNIQUE(strel_id, user_id),
            UNIQUE(strel_id, slot_type, position)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mutes (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            until_ts INTEGER NOT NULL,
            PRIMARY KEY(user_id, chat_id)
        )
        """
    )
    conn.commit()


init_db()


# =========================================================
# HELPERS
# =========================================================
@dataclass
class StrelData:
    count_slots: int
    map_name: str
    event_time: str
    comment: str


MENTION_RE = re.compile(r"\[id(\d+)\|[^\]]+\]|@id(\d+)|\[club(\d+)\|[^\]]+\]|@club(\d+)")


def is_moderator(user_id: int) -> bool:
    return user_id in MODERATOR_IDS


def get_today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def extract_user_id(raw: str) -> Optional[int]:
    m = MENTION_RE.search(raw)
    if not m:
        return None
    for group in m.groups():
        if group and group.isdigit():
            return int(group)
    return None


def parse_strela_command(text: str) -> Optional[StrelData]:
    # /strela 4 Mirage 17:00 дигл шот рифла
    parts = text.strip().split(maxsplit=4)
    if len(parts) < 4 or parts[0].lower() != "/strela":
        return None
    if not parts[1].isdigit():
        return None
    count_slots = int(parts[1])
    map_name = parts[2]
    event_time = parts[3]
    comment = parts[4] if len(parts) > 4 else ""
    if count_slots < 1 or count_slots > 20:
        return None
    if not re.match(r"^\d{1,2}:\d{2}$", event_time):
        return None
    return StrelData(count_slots=count_slots, map_name=map_name, event_time=event_time, comment=comment)


def build_strel_keyboard(strel_id: int) -> str:
    return (
        Keyboard(inline=True)
        .add(Callback("✅ Взять слот", payload={"cmd": "join_strel", "strel_id": strel_id}), color=KeyboardButtonColor.POSITIVE)
        .add(Callback("❌ Покинуть слот", payload={"cmd": "leave_strel", "strel_id": strel_id}), color=KeyboardButtonColor.NEGATIVE)
        .row()
        .add(Callback("🔄 Обновить", payload={"cmd": "refresh_strel", "strel_id": strel_id}), color=KeyboardButtonColor.SECONDARY)
        .get_json()
    )


def fetch_strel(strel_id: int):
    cur = conn.cursor()
    cur.execute("SELECT * FROM strels WHERE id = ?", (strel_id,))
    return cur.fetchone()


def fetch_player_entry(strel_id: int, user_id: int):
    cur = conn.cursor()
    cur.execute("SELECT * FROM strel_players WHERE strel_id = ? AND user_id = ?", (strel_id, user_id))
    return cur.fetchone()


def fetch_strel_players(strel_id: int):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM strel_players
        WHERE strel_id = ?
        ORDER BY CASE slot_type WHEN 'main' THEN 0 ELSE 1 END, position ASC
        """,
        (strel_id,),
    )
    return cur.fetchall()


def create_strel(chat_id: int, peer_id: int, creator_id: int, data: StrelData) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO strels (chat_id, peer_id, creator_id, count_slots, map_name, event_time, comment, created_at, event_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (chat_id, peer_id, creator_id, data.count_slots, data.map_name, data.event_time, data.comment, int(time.time()), get_today_str()),
    )
    conn.commit()
    return cur.lastrowid


def set_strel_message(strel_id: int, message_id: int) -> None:
    cur = conn.cursor()
    cur.execute("UPDATE strels SET message_id = ? WHERE id = ?", (message_id, strel_id))
    conn.commit()


def list_today_strels(chat_id: int):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM strels
        WHERE chat_id = ? AND event_date = ? AND is_active = 1
        ORDER BY created_at ASC
        """,
        (chat_id, get_today_str()),
    )
    return cur.fetchall()


def get_next_free_position(strel_id: int, slot_type: str, limit: int) -> Optional[int]:
    cur = conn.cursor()
    cur.execute(
        "SELECT position FROM strel_players WHERE strel_id = ? AND slot_type = ? ORDER BY position ASC",
        (strel_id, slot_type),
    )
    busy = {row[0] for row in cur.fetchall()}
    for pos in range(1, limit + 1):
        if pos not in busy:
            return pos
    return None


def add_user_to_strel(strel_id: int, user_id: int) -> tuple[bool, str]:
    strel = fetch_strel(strel_id)
    if not strel or not strel["is_active"]:
        return False, "Стрела не найдена или уже закрыта."
    if fetch_player_entry(strel_id, user_id):
        return False, "Ты уже записан в эту стрелу."

    limit = strel["count_slots"]
    main_pos = get_next_free_position(strel_id, "main", limit)
    cur = conn.cursor()

    if main_pos is not None:
        cur.execute(
            "INSERT INTO strel_players (strel_id, user_id, slot_type, position) VALUES (?, ?, 'main', ?)",
            (strel_id, user_id, main_pos),
        )
        conn.commit()
        return True, f"Ты записан в основу #{main_pos}."

    reserve_pos = get_next_free_position(strel_id, "reserve", limit)
    if reserve_pos is not None:
        cur.execute(
            "INSERT INTO strel_players (strel_id, user_id, slot_type, position) VALUES (?, ?, 'reserve', ?)",
            (strel_id, user_id, reserve_pos),
        )
        conn.commit()
        return True, f"Основа занята. Ты записан в резерв #{reserve_pos}."

    return False, "Свободных мест нет."


def rebalance_strel(strel_id: int) -> None:
    strel = fetch_strel(strel_id)
    if not strel:
        return

    cur = conn.cursor()
    cur.execute("SELECT user_id FROM strel_players WHERE strel_id = ? AND slot_type = 'main' ORDER BY position ASC", (strel_id,))
    mains = [row[0] for row in cur.fetchall()]
    cur.execute("SELECT user_id FROM strel_players WHERE strel_id = ? AND slot_type = 'reserve' ORDER BY position ASC", (strel_id,))
    reserves = [row[0] for row in cur.fetchall()]

    combined = mains + reserves
    cur.execute("DELETE FROM strel_players WHERE strel_id = ?", (strel_id,))

    limit = strel["count_slots"]
    for idx, user_id in enumerate(combined[:limit], start=1):
        cur.execute(
            "INSERT INTO strel_players (strel_id, user_id, slot_type, position) VALUES (?, ?, 'main', ?)",
            (strel_id, user_id, idx),
        )
    for idx, user_id in enumerate(combined[limit: limit * 2], start=1):
        cur.execute(
            "INSERT INTO strel_players (strel_id, user_id, slot_type, position) VALUES (?, ?, 'reserve', ?)",
            (strel_id, user_id, idx),
        )
    conn.commit()


def remove_user_from_strel(strel_id: int, user_id: int) -> tuple[bool, str]:
    if not fetch_player_entry(strel_id, user_id):
        return False, "Тебя нет в списке этой стрелы."
    cur = conn.cursor()
    cur.execute("DELETE FROM strel_players WHERE strel_id = ? AND user_id = ?", (strel_id, user_id))
    conn.commit()
    rebalance_strel(strel_id)
    return True, "Ты удален из слотов."


def set_mute(chat_id: int, user_id: int, minutes: int) -> int:
    until_ts = int((datetime.now() + timedelta(minutes=minutes)).timestamp())
    cur = conn.cursor()
    cur.execute("REPLACE INTO mutes (user_id, chat_id, until_ts) VALUES (?, ?, ?)", (user_id, chat_id, until_ts))
    conn.commit()
    return until_ts


def get_active_mute(chat_id: int, user_id: int) -> Optional[int]:
    cur = conn.cursor()
    cur.execute("SELECT until_ts FROM mutes WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
    row = cur.fetchone()
    if not row:
        return None
    if row["until_ts"] <= int(time.time()):
        cur.execute("DELETE FROM mutes WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
        conn.commit()
        return None
    return row["until_ts"]


async def build_strel_text(strel_id: int) -> str:
    strel = fetch_strel(strel_id)
    if not strel:
        return "Стрела не найдена."

    players = fetch_strel_players(strel_id)
    main_map = {row["position"]: row["user_id"] for row in players if row["slot_type"] == "main"}
    reserve_map = {row["position"]: row["user_id"] for row in players if row["slot_type"] == "reserve"}

    title = f"{strel['count_slots']}x{strel['count_slots']} {strel['map_name']} {strel['event_time']}"
    if strel["comment"]:
        title += f" {strel['comment']}"

    lines = ["⚔️ Сбор на стрелу", "", title, "", "Основа:"]
    for i in range(1, strel["count_slots"] + 1):
        lines.append(f"{i}) [id{main_map[i]}|Игрок]" if i in main_map else f"{i})")

    lines.extend(["", "Резерв:"])
    for i in range(1, strel["count_slots"] + 1):
        lines.append(f"{i}. [id{reserve_map[i]}|Игрок]" if i in reserve_map else f"{i}.")

    lines.extend(["", f"ID стрелы: {strel_id}"])
    return "\n".join(lines)


async def update_strel_message(strel_id: int) -> None:
    strel = fetch_strel(strel_id)
    if not strel or not strel["message_id"]:
        return

    await bot.api.messages.edit(
        peer_id=strel["peer_id"],
        conversation_message_id=strel["message_id"],
        message=await build_strel_text(strel_id),
        keyboard=build_strel_keyboard(strel_id),
    )


# =========================================================
# COMMANDS
# =========================================================
@bot.on.message(text=["/ping", "ping"])
async def ping_handler(message: Message):
    await message.answer("Бот работает ✅")

@bot.on.message(text="/myid")
async def myid_handler(message: Message):
    await message.answer(f"Твой ID: {message.from_id}")


@bot.on.message(text="/help")
async def help_handler(message: Message):
    await message.answer(
        "Команды:\n"
        "/ping\n"
        "/strela 4 Mirage 17:00 дигл шот\n"
        "/strels\n"
        "/streledit ID новый_текст\n"
        "/slotadd ID @user main|reserve\n"
        "/slotdel ID @user\n"
        "/mute @user 10|30|60\n"
        "/вызов текст"
    )


@bot.on.message(text="/strela <raw>")
async def strela_handler(message: Message, raw: str):
    if message.from_id is None or message.peer_id is None:
        return
    if message.peer_id < 2_000_000_000:
        await message.answer("Эта команда работает только в беседе.")
        return

    chat_id = message.peer_id - 2_000_000_000
    if CHAT_ID and chat_id != CHAT_ID:
        await message.answer("Бот настроен для другой беседы.")
        return
    if not is_moderator(message.from_id):
        await message.answer("У тебя нет прав на эту команду.")
        return

    parsed = parse_strela_command(f"/strela {raw}")
    if not parsed:
        await message.answer("Формат: /strela 4 Mirage 17:00 дигл шот рифла")
        return

    strel_id = create_strel(chat_id, message.peer_id, message.from_id, parsed)
    text = f"@all\n\n{await build_strel_text(strel_id)}"
       sent_message_id = await bot.api.messages.send(
        peer_id=message.peer_id,
        random_id=0,
        message=text,
        keyboard=build_strel_keyboard(strel_id),
    )

    msg_info = await bot.api.messages.get_by_id(message_ids=[sent_message_id])
    conversation_message_id = msg_info.items[0].conversation_message_id

    set_strel_message(strel_id, conversation_message_id)


@bot.on.message(text="/strels")
async def strels_handler(message: Message):
    if message.peer_id is None or message.peer_id < 2_000_000_000:
        return
    chat_id = message.peer_id - 2_000_000_000
    rows = list_today_strels(chat_id)
    if not rows:
        await message.answer("Стрел на сегодня нет, отдыхай пока что.")
        return

    lines = ["Стрелы на сегодня:", ""]
    for idx, row in enumerate(rows, start=1):
        extra = f" {row['comment']}" if row['comment'] else ""
        lines.append(f"{idx}) ID {row['id']} — {row['count_slots']}x{row['count_slots']} {row['map_name']} {row['event_time']}{extra}")
    await message.answer("\n".join(lines))


@bot.on.message(text="/streledit <strel_id> <new_text>")
async def streledit_handler(message: Message, strel_id: str, new_text: str):
    if message.from_id is None or not is_moderator(message.from_id):
        await message.answer("У тебя нет прав на эту команду.")
        return
    if not strel_id.isdigit():
        await message.answer("Укажи корректный ID стрелы.")
        return
    row = fetch_strel(int(strel_id))
    if not row:
        await message.answer("Стрела не найдена.")
        return

    cur = conn.cursor()
    cur.execute("UPDATE strels SET comment = ? WHERE id = ?", (new_text, int(strel_id)))
    conn.commit()
    await update_strel_message(int(strel_id))
    await message.answer("Текст стрелы обновлен.")


@bot.on.message(text="/slotadd <strel_id> <target> <slot_type>")
async def slotadd_handler(message: Message, strel_id: str, target: str, slot_type: str):
    if message.from_id is None or not is_moderator(message.from_id):
        await message.answer("У тебя нет прав на эту команду.")
        return
    if not strel_id.isdigit():
        await message.answer("Укажи корректный ID стрелы.")
        return
    user_id = extract_user_id(target)
    if not user_id:
        await message.answer("Не смог определить пользователя.")
        return
    if slot_type not in {"main", "reserve"}:
        await message.answer("slot_type должен быть main или reserve.")
        return

    row = fetch_strel(int(strel_id))
    if not row:
        await message.answer("Стрела не найдена.")
        return
    if fetch_player_entry(int(strel_id), user_id):
        await message.answer("Пользователь уже записан.")
        return

    pos = get_next_free_position(int(strel_id), slot_type, row["count_slots"])
    if pos is None:
        await message.answer("Свободных мест в этом списке нет.")
        return

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO strel_players (strel_id, user_id, slot_type, position) VALUES (?, ?, ?, ?)",
        (int(strel_id), user_id, slot_type, pos),
    )
    conn.commit()
    rebalance_strel(int(strel_id))
    await update_strel_message(int(strel_id))
    await message.answer("Игрок добавлен.")


@bot.on.message(text="/slotdel <strel_id> <target>")
async def slotdel_handler(message: Message, strel_id: str, target: str):
    if message.from_id is None or not is_moderator(message.from_id):
        await message.answer("У тебя нет прав на эту команду.")
        return
    if not strel_id.isdigit():
        await message.answer("Укажи корректный ID стрелы.")
        return
    user_id = extract_user_id(target)
    if not user_id:
        await message.answer("Не смог определить пользователя.")
        return

    ok, text = remove_user_from_strel(int(strel_id), user_id)
    if ok:
        await update_strel_message(int(strel_id))
    await message.answer(text)


@bot.on.message(text="/вызов <text>")
async def call_handler(message: Message, text: str):
    if message.from_id is None or not is_moderator(message.from_id):
        await message.answer("У тебя нет прав на эту команду.")
        return
    await message.answer(f"@all\n{text}")


@bot.on.message(text="/mute <target> <minutes>")
async def mute_handler(message: Message, target: str, minutes: str):
    if message.from_id is None or message.peer_id is None:
        return
    if message.peer_id < 2_000_000_000:
        await message.answer("Мут работает только в беседе.")
        return
    if not is_moderator(message.from_id):
        await message.answer("У тебя нет прав на эту команду.")
        return

    user_id = extract_user_id(target)
    if not user_id:
        await message.answer("Не смог определить пользователя. Укажи через упоминание.")
        return
    if minutes not in {"10", "30", "60"}:
        await message.answer("Доступны муты только на 10, 30 или 60 минут.")
        return

    chat_id = message.peer_id - 2_000_000_000
    until_ts = set_mute(chat_id, user_id, int(minutes))
    until_str = datetime.fromtimestamp(until_ts).strftime("%H:%M")
    await message.answer(f"[id{user_id}|Пользователь] получил мут на {minutes} минут. До {until_str}.")


# =========================================================
# CALLBACKS
# =========================================================
@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=GroupTypes.MessageEvent)
async def handle_message_event(event: GroupTypes.MessageEvent):
    payload = event.object.payload or {}
    cmd = payload.get("cmd")
    strel_id = payload.get("strel_id")

    if not strel_id:
        await bot.api.messages.send_message_event_answer(
            event_id=event.object.event_id,
            user_id=event.object.user_id,
            peer_id=event.object.peer_id,
            event_data={"type": "show_snackbar", "text": "Нет ID стрелы."},
        )
        return

    strel_id = int(strel_id)
    user_id = event.object.user_id
    peer_id = event.object.peer_id

    if cmd == "join_strel":
        _, text = add_user_to_strel(strel_id, user_id)
        await update_strel_message(strel_id)
    elif cmd == "leave_strel":
        _, text = remove_user_from_strel(strel_id, user_id)
        await update_strel_message(strel_id)
    elif cmd == "refresh_strel":
        text = "Список обновлен."
        await update_strel_message(strel_id)
    else:
        text = "Неизвестная команда."

    await bot.api.messages.send_message_event_answer(
        event_id=event.object.event_id,
        user_id=user_id,
        peer_id=peer_id,
        event_data={"type": "show_snackbar", "text": text},
    )


@bot.on.message()
async def mute_guard(message: Message):
    if message.from_id is None or message.peer_id is None:
        return
    if message.peer_id < 2_000_000_000:
        return
    chat_id = message.peer_id - 2_000_000_000
    until_ts = get_active_mute(chat_id, message.from_id)
    if until_ts:
        try:
            await bot.api.messages.delete(
                peer_id=message.peer_id,
                cmids=[message.conversation_message_id],
                delete_for_all=True,
            )
        except Exception:
            pass

# =========================================================
# START
# =========================================================
print("Bot started")
bot.run_forever()
