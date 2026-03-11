import os
import re
import sqlite3
import time
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from vkbottle import Bot, Keyboard, KeyboardButtonColor, GroupEventType, Callback, GroupTypes
from vkbottle.bot import Message

# =========================================================
# CONFIG
# Railway Variables:
# TOKEN=vk token
# CHAT_ID=1                      # optional, id беседы без 2000000000
# MODERATOR_IDS=1,2,3            # id модеров через запятую
# DB_PATH=strela_bot.db          # optional
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

SERVER_MAP = {
    1: "phoenix", 2: "tucson", 3: "scottdale", 4: "chandler", 5: "brainburg",
    6: "saint rose", 7: "mesa", 8: "red-rock", 9: "yuma", 10: "surprise",
    11: "prescott", 12: "glendale", 13: "kingman", 14: "winslow", 15: "payson",
    16: "gilbert", 17: "showlow", 18: "casa granda", 19: "page", 20: "sun city",
    21: "queen creek", 22: "sedona", 23: "holiday", 24: "wednesday", 25: "yava",
    26: "faraway", 27: "bumblebee", 28: "christmas", 29: "mirage", 30: "love",
    31: "drake", 32: "space"
}

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
            server_name TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_time TEXT NOT NULL,
            comment TEXT DEFAULT '',
            message_id INTEGER,
            created_at INTEGER NOT NULL,
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
        CREATE TABLE IF NOT EXISTS bizwars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            war_date TEXT NOT NULL,
            war_time TEXT NOT NULL,
            enemy TEXT NOT NULL,
            server_num INTEGER NOT NULL,
            player_count INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            notified INTEGER NOT NULL DEFAULT 0
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
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS members (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            added_at INTEGER NOT NULL,
            PRIMARY KEY(user_id, chat_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            created_at INTEGER NOT NULL
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
    server_name: str
    event_date: str
    event_time: str
    comment: str


MENTION_RE = re.compile(r"\[id(\d+)\|[^\]]+\]|@id(\d+)|\[club(\d+)\|[^\]]+\]|@club(\d+)")
MSK = timezone(timedelta(hours=3))


def now_ts() -> int:
    return int(time.time())


def now():
    return datetime.now(MSK)


def today_str() -> str:
    return now().strftime("%d.%m")


def is_moderator(user_id: int) -> bool:
    return user_id in MODERATOR_IDS


def extract_user_id(raw: str) -> Optional[int]:
    m = MENTION_RE.search(raw)
    if not m:
        return None
    for group in m.groups():
        if group and group.isdigit():
            return int(group)
    return None


def parse_count(value: str) -> Optional[int]:
    value = value.lower().replace("х", "x")
    if "x" in value:
        left = value.split("x", 1)[0]
        return int(left) if left.isdigit() else None
    return int(value) if value.isdigit() else None


def parse_strela_command(text: str) -> Optional[StrelData]:
    # !strela 4x4 Mirage 10.03 17:00 Дигл шот
    parts = text.strip().split(maxsplit=5)
    if len(parts) < 5:
        return None

    cmd, count_raw, server_name, event_date, event_time = parts[:5]
    if cmd.lower() not in {"!strela", "/strela"}:
        return None

    count_slots = parse_count(count_raw)
    if not count_slots or count_slots < 1 or count_slots > 20:
        return None

    if not re.match(r"^\d{2}\.\d{2}$", event_date):
        return None

    if not re.match(r"^\d{1,2}:\d{2}$", event_time):
        return None

    comment = parts[5] if len(parts) > 5 else ""
    return StrelData(
        count_slots=count_slots,
        server_name=server_name,
        event_date=event_date,
        event_time=event_time,
        comment=comment,
    )


def build_strel_keyboard(strel_id: int):
    return (
        Keyboard(inline=True)
        .add(
            Callback("✅ Взять слот", payload={"cmd": "join_strel", "strel_id": strel_id}),
            color=KeyboardButtonColor.POSITIVE,
        )
        .add(
            Callback("❌ Покинуть слот", payload={"cmd": "leave_strel", "strel_id": strel_id}),
            color=KeyboardButtonColor.NEGATIVE,
        )
        .row()
        .add(
            Callback("🔄 Обновить", payload={"cmd": "refresh_strel", "strel_id": strel_id}),
            color=KeyboardButtonColor.SECONDARY,
        )
        .get_json()
    )


def fetch_strel(strel_id: int):
    cur = conn.cursor()
    cur.execute("SELECT * FROM strels WHERE id = ?", (strel_id,))
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


def fetch_player_entry(strel_id: int, user_id: int):
    cur = conn.cursor()
    cur.execute("SELECT * FROM strel_players WHERE strel_id = ? AND user_id = ?", (strel_id, user_id))
    return cur.fetchone()


async def get_user_names(user_ids: list[int]) -> dict[int, str]:
    if not user_ids:
        return {}

    user_ids = list(set(user_ids))

    try:
        users = await bot.api.users.get(user_ids=user_ids)
    except Exception:
        return {}

    result = {}
    for user in users:
        first_name = getattr(user, "first_name", "") or ""
        last_name = getattr(user, "last_name", "") or ""
        full_name = f"{first_name} {last_name}".strip()
        result[user.id] = full_name if full_name else f"id{user.id}"

    return result


def create_strel(chat_id: int, peer_id: int, creator_id: int, data: StrelData) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO strels (chat_id, peer_id, creator_id, count_slots, server_name, event_date, event_time, comment, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chat_id,
            peer_id,
            creator_id,
            data.count_slots,
            data.server_name,
            data.event_date,
            data.event_time,
            data.comment,
            now_ts(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def set_strel_message_id(strel_id: int, message_id: int) -> None:
    print("SAVE MESSAGE ID:", message_id)
    cur = conn.cursor()
    cur.execute("UPDATE strels SET message_id = ? WHERE id = ?", (message_id, strel_id))
    conn.commit()


def get_next_free_position(strel_id: int, slot_type: str, limit: int) -> Optional[int]:
    cur = conn.cursor()
    cur.execute(
        "SELECT position FROM strel_players WHERE strel_id = ? AND slot_type = ? ORDER BY position ASC",
        (strel_id, slot_type),
    )
    busy = {row[0] for row in cur.fetchall()}
    for i in range(1, limit + 1):
        if i not in busy:
            return i
    return None


def log_activity(chat_id: int, user_id: int, action: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO activity (user_id, chat_id, action, created_at) VALUES (?, ?, ?, ?)",
        (user_id, chat_id, action, now_ts()),
    )
    conn.commit()


def rebalance_strel(strel_id: int) -> None:
    strel = fetch_strel(strel_id)
    if not strel:
        return

    players = fetch_strel_players(strel_id)
    all_users = [row["user_id"] for row in players]

    cur = conn.cursor()
    cur.execute("DELETE FROM strel_players WHERE strel_id = ?", (strel_id,))

    main_limit = strel["count_slots"]
    reserve_limit = strel["count_slots"]

    main_users = all_users[:main_limit]
    reserve_users = all_users[main_limit:main_limit + reserve_limit]

    for idx, user_id in enumerate(main_users, start=1):
        cur.execute(
            "INSERT INTO strel_players (strel_id, user_id, slot_type, position) VALUES (?, ?, 'main', ?)",
            (strel_id, user_id, idx),
        )
    for idx, user_id in enumerate(reserve_users, start=1):
        cur.execute(
            "INSERT INTO strel_players (strel_id, user_id, slot_type, position) VALUES (?, ?, 'reserve', ?)",
            (strel_id, user_id, idx),
        )

    conn.commit()


def add_user_to_strel(strel_id: int, user_id: int) -> tuple[bool, str]:
    strel = fetch_strel(strel_id)
    if not strel or not strel["is_active"]:
        return False, "Стрела не найдена или уже закрыта."

    if fetch_player_entry(strel_id, user_id):
        return False, "Ты уже записан."

    limit = strel["count_slots"]
    preferred = "main" if is_moderator(user_id) else "reserve"
    fallback = "reserve" if preferred == "main" else None

    cur = conn.cursor()

    preferred_pos = get_next_free_position(strel_id, preferred, limit)
    if preferred_pos is not None:
        cur.execute(
            "INSERT INTO strel_players (strel_id, user_id, slot_type, position) VALUES (?, ?, ?, ?)",
            (strel_id, user_id, preferred, preferred_pos),
        )
        conn.commit()
        log_activity(strel["chat_id"], user_id, "join")
        if preferred == "main":
            return True, f"Ты записан в основу #{preferred_pos}."
        return True, f"Ты записан в резерв #{preferred_pos}."

    if fallback:
        fallback_pos = get_next_free_position(strel_id, fallback, limit)
        if fallback_pos is not None:
            cur.execute(
                "INSERT INTO strel_players (strel_id, user_id, slot_type, position) VALUES (?, ?, ?, ?)",
                (strel_id, user_id, fallback, fallback_pos),
            )
            conn.commit()
            log_activity(strel["chat_id"], user_id, "join")
            return True, f"Основа занята. Ты записан в резерв #{fallback_pos}."

    return False, "Свободных мест нет."


def remove_user_from_strel(strel_id: int, user_id: int) -> tuple[bool, str]:
    entry = fetch_player_entry(strel_id, user_id)
    if not entry:
        return False, "Тебя нет в списке этой стрелы."

    cur = conn.cursor()
    cur.execute("DELETE FROM strel_players WHERE strel_id = ? AND user_id = ?", (strel_id, user_id))
    conn.commit()

    rebalance_strel(strel_id)

    strel = fetch_strel(strel_id)
    if strel:
        log_activity(strel["chat_id"], user_id, "leave")

    return True, "Ты удален из слотов."


def add_member(chat_id: int, user_id: int) -> None:
    cur = conn.cursor()
    cur.execute(
        "REPLACE INTO members (user_id, chat_id, added_at) VALUES (?, ?, ?)",
        (user_id, chat_id, now_ts()),
    )
    conn.commit()


def remove_member(chat_id: int, user_id: int) -> None:
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM members WHERE user_id = ? AND chat_id = ?",
        (user_id, chat_id),
    )
    conn.commit()


def list_members(chat_id: int):
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id FROM members WHERE chat_id = ? ORDER BY user_id ASC",
        (chat_id,),
    )
    return [row["user_id"] for row in cur.fetchall()]


def get_last_activity_ts(chat_id: int, user_id: int) -> Optional[int]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT created_at
        FROM activity
        WHERE chat_id = ? AND user_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (chat_id, user_id),
    )
    row = cur.fetchone()
    return row["created_at"] if row else None


def get_week_activity(chat_id: int, days: int = 7):
    since_ts = now_ts() - days * 86400
    cur = conn.cursor()
    cur.execute(
        """
        SELECT user_id, COUNT(*) as cnt
        FROM activity
        WHERE chat_id = ? AND action = 'join' AND created_at >= ?
        GROUP BY user_id
        ORDER BY cnt DESC, user_id ASC
        """
        ,
        (chat_id, since_ts),
    )
    return cur.fetchall()


def get_inactive_members(chat_id: int, inactive_days: int = 7) -> list[int]:
    threshold = now_ts() - inactive_days * 86400
    members = list_members(chat_id)
    inactive = []

    for user_id in members:
        last_ts = get_last_activity_ts(chat_id, user_id)
        if last_ts is None or last_ts < threshold:
            inactive.append(user_id)

    return inactive


def set_mute(chat_id: int, user_id: int, minutes: int) -> int:
    until_ts = int((now() + timedelta(minutes=minutes)).timestamp())
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
    if row["until_ts"] <= now_ts():
        cur.execute("DELETE FROM mutes WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
        conn.commit()
        return None
    return row["until_ts"]


def add_bizwar(chat_id: int, war_time: str, enemy: str, server_num: int, player_count: int, war_date: Optional[str] = None) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO bizwars (chat_id, war_date, war_time, enemy, server_num, player_count, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (chat_id, war_date or today_str(), war_time, enemy.lower(), server_num, player_count, now_ts()),
    )
    conn.commit()


def list_today_bizwars(chat_id: int):
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM bizwars WHERE chat_id = ? AND war_date = ? ORDER BY war_time ASC, server_num ASC",
        (chat_id, today_str()),
    )
    return cur.fetchall()


def cleanup_old_bizwars() -> None:
    cur = conn.cursor()
    current_hm = now().strftime("%H:%M")
    cur.execute("DELETE FROM bizwars WHERE war_date < ?", (today_str(),))
    cur.execute("DELETE FROM bizwars WHERE war_date = ? AND war_time < ?", (today_str(), current_hm))
    if now().hour >= 22:
        cur.execute("DELETE FROM bizwars WHERE war_date = ?", (today_str(),))
    conn.commit()


def get_top(chat_id: int, days: int):
    since_ts = now_ts() - days * 86400
    cur = conn.cursor()
    cur.execute(
        """
        SELECT user_id, COUNT(*) as cnt
        FROM activity
        WHERE chat_id = ? AND action = 'join' AND created_at >= ?
        GROUP BY user_id
        ORDER BY cnt DESC, user_id ASC
        LIMIT 20
        """,
        (chat_id, since_ts),
    )
    return cur.fetchall()


async def build_strel_text(strel_id: int) -> str:
    strel = fetch_strel(strel_id)
    if not strel:
        return "Стрела не найдена."

    players = fetch_strel_players(strel_id)

    all_user_ids = [row["user_id"] for row in players]
    user_names = await get_user_names(all_user_ids)

    main_map = {row["position"]: row["user_id"] for row in players if row["slot_type"] == "main"}
    reserve_map = {row["position"]: row["user_id"] for row in players if row["slot_type"] == "reserve"}

    title = f"{strel['count_slots']}x{strel['count_slots']} {strel['server_name']} {strel['event_date']} {strel['event_time']}"
    if strel["comment"]:
        title += f" {strel['comment']}"

    lines = ["⚔️ Сбор на стрелу", "", title, "", "Основа:"]

    for i in range(1, strel["count_slots"] + 1):
        if i in main_map:
            uid = main_map[i]
            name = user_names.get(uid, f"id{uid}")
            lines.append(f"{i}) [id{uid}|{name}]")
        else:
            lines.append(f"{i})")

    lines.extend(["", "Резерв:"])

    for i in range(1, strel["count_slots"] + 1):
        if i in reserve_map:
            uid = reserve_map[i]
            name = user_names.get(uid, f"id{uid}")
            lines.append(f"{i}. [id{uid}|{name}]")
        else:
            lines.append(f"{i}.")

    lines.extend(["", f"ID стрелы: {strel_id}"])
    return "\n".join(lines)


async def resend_strel_message(strel_id: int) -> None:
    strel = fetch_strel(strel_id)
    if not strel:
        return

    text = await build_strel_text(strel_id)

    old_message_id = strel["message_id"]
    if old_message_id:
        try:
            await bot.api.messages.delete(
                message_ids=[old_message_id],
                delete_for_all=True,
            )
        except Exception:
            pass

    new_message_id = await bot.api.messages.send(
        peer_id=strel["peer_id"],
        random_id=int(time.time() * 1000),
        message=text,
        keyboard=build_strel_keyboard(strel_id),
    )

    set_strel_message_id(strel_id, int(new_message_id))


async def update_strel_message(strel_id: int) -> None:
    strel = fetch_strel(strel_id)
    if not strel:
        print(f"EDIT ERROR: strel {strel_id} not found")
        return

    if not strel["message_id"]:
        print(f"EDIT ERROR: no message_id for strel {strel_id}")
        return

    text = await build_strel_text(strel_id)

    try:
        result = await bot.api.messages.edit(
            peer_id=strel["peer_id"],
            message_id=int(strel["message_id"]),
            message=text,
            keyboard=build_strel_keyboard(strel_id),
        )
        print("EDIT RESULT:", result)
    except Exception as e:
        print("EDIT ERROR:", e)


async def send_activity_report() -> None:
    inactive_days = 7

    if CHAT_ID == 0:
        print("REPORT ERROR: CHAT_ID не задан")
        return

    rows = get_week_activity(CHAT_ID, 7)
    inactive_ids = get_inactive_members(CHAT_ID, inactive_days)

    active_user_ids = [row["user_id"] for row in rows]
    all_user_ids = list(set(active_user_ids + inactive_ids))
    user_names = await get_user_names(all_user_ids)

    text_lines = ["Отчёт по активности", ""]

    text_lines.append("Активность за 7 дней:")
    if rows:
        for idx, row in enumerate(rows, start=1):
            uid = row["user_id"]
            name = user_names.get(uid, f"id{uid}")
            text_lines.append(f"{idx}) [id{uid}|{name}] — {row['cnt']}")
    else:
        text_lines.append("За неделю активности не было.")

    text_lines.extend(["", f"Неактивные более {inactive_days} дней:"])
    if inactive_ids:
        for idx, uid in enumerate(inactive_ids, start=1):
            name = user_names.get(uid, f"id{uid}")
            text_lines.append(f"{idx}) [id{uid}|{name}]")
    else:
        text_lines.append("Неактивных нет.")

    report_text = "\n".join(text_lines)

    for moderator_id in MODERATOR_IDS:
        try:
            await bot.api.messages.send(
                peer_id=moderator_id,
                random_id=int(time.time() * 1000),
                message=report_text,
            )
        except Exception as e:
            print(f"REPORT SEND ERROR to {moderator_id}: {e}")


async def scheduler_loop() -> None:
    while True:
        try:
            cleanup_old_bizwars()

            rows = list_today_bizwars(CHAT_ID) if CHAT_ID else []
            now_obj = now()

            print("DEBUG CHAT_ID:", CHAT_ID)
            print("DEBUG NOW:", now_obj.strftime("%d.%m %H:%M:%S"))
            print("DEBUG ROWS COUNT:", len(rows))

            for row in rows:
                war_dt = datetime.strptime(
                    f"{row['war_date']} {row['war_time']}",
                    "%d.%m %H:%M"
                ).replace(year=now_obj.year, tzinfo=MSK)

                delta = (war_dt - now_obj).total_seconds()

                print("DEBUG BIZWAR:", dict(row))
                print("DEBUG DELTA:", delta, "NOTIFIED:", row["notified"])

                if 0 <= delta <= 1800 and not row["notified"]:
                    try:
                        await bot.api.messages.send(
                            peer_id=2000000000 + row["chat_id"],
                            random_id=int(time.time() * 1000),
                            message=(
                                f"@all\n"
                                f"Через 30 минут стрела: {row['war_time']} "
                                f"({SERVER_MAP.get(row['server_num'], row['server_num'])}) "
                                f"[{row['player_count']}x{row['player_count']}]"
                            ),
                        )
                        print("DEBUG REMINDER SENT OK")
                    except Exception as e:
                        print("DEBUG REMINDER SEND ERROR:", e)

                    cur = conn.cursor()
                    cur.execute("UPDATE bizwars SET notified = 1 WHERE id = ?", (row["id"],))
                    conn.commit()

        except Exception as e:
            print(f"SCHEDULER ERROR: {e}")

        await asyncio.sleep(60)


# =========================================================
# COMMANDS
# =========================================================
@bot.on.message(text=["/ping", "!ping", "ping"])
async def ping_handler(message: Message):
    await message.answer("Бот работает ✅")


@bot.on.message(text=["!time", "/time"])
async def time_handler(message: Message):
    await message.answer(now().strftime("Время бота: %d.%m %H:%M"))


@bot.on.message(text=["/myid", "!myid"])
async def myid_handler(message: Message):
    await message.answer(f"Твой ID: {message.from_id}")


@bot.on.message(text=["/chatid", "!chatid"])
async def chatid_handler(message: Message):
    if message.peer_id and message.peer_id > 2_000_000_000:
        await message.answer(f"Chat ID: {message.peer_id - 2_000_000_000}")
    else:
        await message.answer("Команда работает только в беседе.")


@bot.on.message(text=["!memberadd <target>", "/memberadd <target>"])
async def memberadd_handler(message: Message, target: str):
    if message.from_id is None or message.peer_id is None:
        return
    if message.peer_id < 2_000_000_000:
        await message.answer("Команда работает только в беседе.")
        return
    if not is_moderator(message.from_id):
        await message.answer("У тебя нет прав на эту команду.")
        return

    user_id = extract_user_id(target)
    if not user_id:
        await message.answer("Не смог определить пользователя.")
        return

    chat_id = message.peer_id - 2_000_000_000
    add_member(chat_id, user_id)
    await message.answer(f"[id{user_id}|Пользователь] добавлен в список участников для отчётов.")


@bot.on.message(text=["!memberdel <target>", "/memberdel <target>"])
async def memberdel_handler(message: Message, target: str):
    if message.from_id is None or message.peer_id is None:
        return
    if message.peer_id < 2_000_000_000:
        await message.answer("Команда работает только в беседе.")
        return
    if not is_moderator(message.from_id):
        await message.answer("У тебя нет прав на эту команду.")
        return

    user_id = extract_user_id(target)
    if not user_id:
        await message.answer("Не смог определить пользователя.")
        return

    chat_id = message.peer_id - 2_000_000_000
    remove_member(chat_id, user_id)
    await message.answer(f"[id{user_id}|Пользователь] удалён из списка участников для отчётов.")


@bot.on.message(text=["+ <strel_id>"])
async def plus_handler(message: Message, strel_id: str):
    if message.from_id is None or message.peer_id is None:
        return

    if message.peer_id < 2_000_000_000:
        return

    if not strel_id.isdigit():
        await message.answer("Укажи ID стрелы. Пример: + 5")
        return

    strel = fetch_strel(int(strel_id))
    if not strel or not strel["is_active"]:
        await message.answer("Стрела не найдена.")
        return

    ok, text = add_user_to_strel(int(strel_id), message.from_id)
    if ok:
        await update_strel_message(int(strel_id))

    try:
        await bot.api.messages.delete(
            peer_id=message.peer_id,
            cmids=[message.conversation_message_id],
            delete_for_all=True,
        )
    except Exception:
        pass

    await message.answer(text)


@bot.on.message(text=["- <strel_id>"])
async def minus_handler(message: Message, strel_id: str):
    if message.from_id is None or message.peer_id is None:
        return

    if message.peer_id < 2_000_000_000:
        return

    if not strel_id.isdigit():
        await message.answer("Укажи ID стрелы. Пример: - 5")
        return

    strel = fetch_strel(int(strel_id))
    if not strel or not strel["is_active"]:
        await message.answer("Стрела не найдена.")
        return

    ok, text = remove_user_from_strel(int(strel_id), message.from_id)
    if ok:
        await update_strel_message(int(strel_id))

    try:
        await bot.api.messages.delete(
            peer_id=message.peer_id,
            cmids=[message.conversation_message_id],
            delete_for_all=True,
        )
    except Exception:
        pass

    await message.answer(text)


@bot.on.message(text=["!activ", "/activ"])
async def activ_handler(message: Message):
    if message.from_id is None:
        return
    if not is_moderator(message.from_id):
        await message.answer("У тебя нет прав на эту команду.")
        return

    await send_activity_report()
    await message.answer("Отчёт отправлен модераторам в ЛС.")


@bot.on.message(text=["/help", "!help"])
async def help_handler(message: Message):
    await message.answer(
        "Команды:\n"
        "!strela - забить стрелу. Пример: !strela 4x4 Mirage 10.03 17:00 Дигл шот\n"
        "!bizwar - показать стрелы на сегодня\n"
        "!add ID @user main|reserve - добавить человека в состав\n"
        "!remove ID @user - удалить человека из состава\n"
        "!all текст - срочный вызов всех\n"
        "!mute @user 10|30|60 - выдать мут\n"
        "!топ 7 - топ стрелков за неделю\n"
        "!топ 30 - топ стрелков за месяц\n"
        "!activ - отправить отчет модераторам в ЛС\n"
        "+ ID - записаться в стрелу\n"
        "- ID - выйти из стрелы\n"
    )


@bot.on.message(text=["!strela <raw>", "/strela <raw>"])
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

    parsed = parse_strela_command(f"!strela {raw}")
    if not parsed:
        await message.answer("Формат: !strela 4x4 Mirage 10.03 17:00 Дигл шот")
        return

    strel_id = create_strel(chat_id, message.peer_id, message.from_id, parsed)
    text = f"@all\n\n{await build_strel_text(strel_id)}"

    result = await bot.api.messages.send(
        peer_id=message.peer_id,
        random_id=int(time.time() * 1000),
        message=text,
        keyboard=build_strel_keyboard(strel_id),
    )

    print("DEBUG SEND RESULT:", result)
    set_strel_message_id(strel_id, int(result))

    server_num = None
    parsed_server_lower = parsed.server_name.lower()
    for num, name in SERVER_MAP.items():
        if name.lower() == parsed_server_lower:
            server_num = num
            break

    if server_num is not None:
        add_bizwar(chat_id, parsed.event_time, "strela", server_num, parsed.count_slots, parsed.event_date)


@bot.on.message(text=["!bizwar", "/bizwar", "!strels", "/strels"])
async def bizwar_list_handler(message: Message):
    if message.peer_id is None or message.peer_id < 2_000_000_000:
        return

    cleanup_old_bizwars()
    chat_id = message.peer_id - 2_000_000_000
    rows = list_today_bizwars(chat_id)

    if not rows:
        await message.answer("Стрел на сегодня пока не запланировано.")
        return

    lines = [f"{today_str()}:"]
    for row in rows:
        server_name = SERVER_MAP.get(row["server_num"], row["server_num"])
        enemy = row["enemy"]
        if enemy == "strela":
            lines.append(f"{row['war_time']} ({server_name}) [{row['player_count']}x{row['player_count']}]")
        else:
            lines.append(f"{row['war_time']} vs {enemy} ({server_name}) [{row['player_count']}x{row['player_count']}]")

    await message.answer("\n".join(lines))


@bot.on.message(text=["!add <strel_id> <target> <slot_type>", "/add <strel_id> <target> <slot_type>"])
async def add_handler(message: Message, strel_id: str, target: str, slot_type: str):
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

    slot_type = slot_type.lower()
    if slot_type in {"основа", "main"}:
        slot_type = "main"
    elif slot_type in {"резерв", "reserve"}:
        slot_type = "reserve"
    else:
        await message.answer("Укажи slot_type: main/reserve или основа/резерв")
        return

    strel = fetch_strel(int(strel_id))
    if not strel:
        await message.answer("Стрела не найдена.")
        return

    if fetch_player_entry(int(strel_id), user_id):
        await message.answer("Пользователь уже записан.")
        return

    pos = get_next_free_position(int(strel_id), slot_type, strel["count_slots"])
    if pos is None:
        if slot_type == "main":
            await message.answer("В основе мест нет.")
        else:
            await message.answer("В резерве мест нет.")
        return

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO strel_players (strel_id, user_id, slot_type, position) VALUES (?, ?, ?, ?)",
        (int(strel_id), user_id, slot_type, pos),
    )
    conn.commit()

    await update_strel_message(int(strel_id))

    if slot_type == "main":
        await message.answer("Игрок добавлен в основу.")
    else:
        await message.answer("Игрок добавлен в резерв.")


@bot.on.message(text=["!remove <strel_id> <target>", "/remove <strel_id> <target>"])
async def remove_handler(message: Message, strel_id: str, target: str):
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


@bot.on.message(text=["!all <text>", "/all <text>"])
async def call_handler(message: Message, text: str):
    if message.from_id is None or not is_moderator(message.from_id):
        await message.answer("У тебя нет прав на эту команду.")
        return

    await message.answer(
        f"🚨 ВНИМАНИЕ ВСЕМ 🚨\n\n"
        f"@all\n\n"
        f"{text}"
    )


@bot.on.message(text=["!mute <target> <minutes>", "/mute <target> <minutes>"])
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


@bot.on.message(text=["!топ <days>", "/топ <days>"])
async def top_handler(message: Message, days: str):
    if message.peer_id is None or message.peer_id < 2_000_000_000:
        return
    if days not in {"7", "30"}:
        await message.answer("Используй !топ 7 или !топ 30")
        return

    rows = get_top(message.peer_id - 2_000_000_000, int(days))
    if not rows:
        await message.answer("За этот период активности нет.")
        return

    user_ids = [row["user_id"] for row in rows]
    user_names = await get_user_names(user_ids)

    lines = [f"Топ за {days} дней:"]
    for idx, row in enumerate(rows, start=1):
        uid = row["user_id"]
        name = user_names.get(uid, f"id{uid}")
        lines.append(f"{idx}) [id{uid}|{name}] — {row['cnt']}")

    await message.answer("\n".join(lines))


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

    try:
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
    except Exception:
        await bot.api.messages.send_message_event_answer(
            event_id=event.object.event_id,
            user_id=user_id,
            peer_id=peer_id,
            event_data={"type": "show_snackbar", "text": "Ошибка обновления"},
        )
        raise


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
bot.loop_wrapper.add_task(scheduler_loop())
print("Bot started")
bot.run_forever()
