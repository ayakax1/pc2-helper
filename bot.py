"""
Телеграм-бот: пересылка сообщений пользователей админу + система банов.

Установка:
    pip install -r requirements.txt

Настройка:
    1. Получи токен бота у @BotFather.
    2. Узнай свой Telegram ID у @userinfobot.
    3. Впиши их ниже в BOT_TOKEN и ADMIN_ID.

Запуск:
    python bot.py

Про постоянную работу 24/7 — см. DEPLOY.md.
"""

import json
import os
from datetime import datetime, timedelta

import telebot

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "8975257839:AAE2wzFeReaMpaB2m1syAeyHRFO-4hX-OQo"      # токен от @BotFather
ADMIN_ID = 8847038707               # твой Telegram ID (число, без @)

WELCOME_TEXT = (
    "Здравствуй! Я — бот-помощник, который свяжет тебя с администраторами "
    "канала @PC2_Analytics для решения любых вопросов. Пожалуйста, соблюдайте "
    "правила этикета при общении с модератором во избежание блокировок. "
    "Для вопроса напиши мне любое сообщение — оно придёт модераторам."
)
# ================================

bot = telebot.TeleBot(BOT_TOKEN)

BANS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bans.json")

# Карта: id сообщения в чате админа -> id пользователя, который его прислал.
message_map = {}


# ---------- Работа с файлом банов ----------
# Формат записи: {"normal": {"until": iso|None, "reason": str} | None,
#                 "ip":     {"until": iso|None, "reason": str} | None}

def load_bans():
    if not os.path.exists(BANS_FILE):
        return {}
    with open(BANS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    migrated = {}
    for uid, entry in data.items():
        if "normal" in entry or "ip" in entry:
            migrated[uid] = {"normal": entry.get("normal"), "ip": entry.get("ip")}
        else:
            # старый формат файла (до введения раздельных полей) — конвертируем
            field = "ip" if entry.get("is_ip") else "normal"
            fresh = {"normal": None, "ip": None}
            fresh[field] = {"until": entry.get("until"), "reason": entry.get("reason")}
            migrated[uid] = fresh
    return migrated


def save_bans(data):
    with open(BANS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


bans = load_bans()


def is_banned(user_id):
    """Проверяет бан, попутно вычищая истёкшие записи."""
    key = str(user_id)
    entry = bans.get(key)
    if not entry:
        return False

    now = datetime.now()
    changed = False
    for field in ("normal", "ip"):
        sub = entry.get(field)
        if sub and sub["until"] is not None and now >= datetime.fromisoformat(sub["until"]):
            entry[field] = None
            changed = True

    active = bool(entry.get("normal")) or bool(entry.get("ip"))

    if changed:
        if not entry["normal"] and not entry["ip"]:
            del bans[key]
        save_bans(bans)

    return active


# ---------- Разбор длительности ----------

import re


def parse_duration(text):
    """
    '30d' -> 30 дней, '12h' -> 12 часов, '45m' -> 45 минут,
    'perm'/'permanent'/'навсегда' -> None (бессрочно).
    Возвращает "invalid", если формат не распознан.
    """
    text = text.lower().strip()
    if text in ("perm", "permanent", "навсегда"):
        return None
    match = re.fullmatch(r"(\d+)([dhm])", text)
    if not match:
        return "invalid"
    value, unit = match.groups()
    value = int(value)
    if unit == "d":
        return timedelta(days=value)
    if unit == "h":
        return timedelta(hours=value)
    return timedelta(minutes=value)


def duration_to_words(delta: timedelta) -> str:
    days = delta.days
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    parts = []
    if days:
        parts.append(f"{days} дн.")
    if hours:
        parts.append(f"{hours} ч.")
    if minutes:
        parts.append(f"{minutes} мин.")
    return " ".join(parts) if parts else "0 мин."


def parse_ban_line(line, extra_reason_lines=None):
    """Разбирает строку вида '/ban [*ip] <срок> <причина>'."""
    tokens = line.strip().split(maxsplit=1)
    if len(tokens) < 2:
        return {"error": "Формат: /ban [*ip] <срок> <причина>\nПример: /ban 30d спам"}

    rest = tokens[1].strip()
    is_ip = False
    if rest.lower().startswith("*ip"):
        is_ip = True
        rest = rest[3:].strip()

    parts = rest.split(maxsplit=1)
    if not parts:
        return {"error": "Не указан срок бана."}

    duration_raw = parts[0]
    reason = parts[1] if len(parts) > 1 else "не указана"
    if extra_reason_lines:
        extra = " ".join(l.strip() for l in extra_reason_lines if l.strip())
        if extra:
            reason = f"{reason} {extra}"

    delta = parse_duration(duration_raw)
    if delta == "invalid":
        return {"error": "Неверный формат срока. Примеры: 30d, 12h, 45m, perm"}

    return {"is_ip": is_ip, "delta": delta, "reason": reason}


# ---------- /start ----------

@bot.message_handler(commands=["start"])
def handle_start(message):
    bot.send_message(message.chat.id, WELCOME_TEXT)


# ---------- /unban (без ответа, отдельной командой) ----------

@bot.message_handler(commands=["unban"])
def handle_unban(message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Формат: /unban [*ip|all|-] <user_id>")
        return

    if len(args) == 2:
        flag, target_id = "-", args[1]
    else:
        flag, target_id = args[1].lower(), args[2]

    if flag not in ("*ip", "all", "-"):
        bot.reply_to(message, "Флаг должен быть одним из: *ip, all, -\nФормат: /unban [*ip|all|-] <user_id>")
        return

    if not target_id.isdigit():
        bot.reply_to(message, "user_id должен быть числом.")
        return

    entry = bans.get(target_id)
    if not entry or (not entry.get("normal") and not entry.get("ip")):
        bot.reply_to(message, "Этот пользователь не забанен.")
        return

    if flag == "*ip":
        entry["ip"] = None
        result = "снята IP-пометка (обычный бан по аккаунту, если был, сохранён)"
    elif flag == "all":
        entry["ip"] = None
        entry["normal"] = None
        result = "сняты все баны (аккаунт + IP-пометка)"
    else:  # "-"
        entry["normal"] = None
        result = "снят обычный бан по аккаунту (IP-пометка, если была, сохранена)"

    if not entry["normal"] and not entry["ip"]:
        del bans[target_id]
    save_bans(bans)

    bot.reply_to(message, f"✅ Пользователь {target_id}: {result}.")
    try:
        bot.send_message(int(target_id), "Ваша блокировка в боте была снята (частично или полностью).")
    except Exception:
        pass


# ---------- Ответ админа (обычный текст и/или /ban строкой) ----------

@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID and m.reply_to_message is not None,
    content_types=["text"],
)
def handle_admin_reply(message):
    target_id = message_map.get(message.reply_to_message.message_id)
    if target_id is None:
        bot.reply_to(
            message,
            "Не удалось определить пользователя (сообщение слишком старое, "
            "либо бот перезапускался и карта сообщений очистилась).",
        )
        return

    lines = message.text.splitlines()
    ban_line_index = None
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("/ban"):
            ban_line_index = i
            break

    # Нет строки с /ban — это просто ответ пользователю
    if ban_line_index is None:
        answer_text = message.text.strip()
        if not answer_text:
            return
        try:
            bot.send_message(target_id, answer_text)
            bot.reply_to(message, "✅ Ответ отправлен пользователю.")
        except Exception:
            bot.reply_to(message, "⚠️ Не удалось отправить ответ (возможно, пользователь заблокировал бота).")
        return

    # Текст перед строкой /ban (если есть) — отправляем как обычный ответ
    answer_lines = [l for l in lines[:ban_line_index] if l.strip()]
    if answer_lines:
        try:
            bot.send_message(target_id, "\n".join(answer_lines))
        except Exception:
            pass

    ban_line = lines[ban_line_index]
    extra_lines = lines[ban_line_index + 1:]
    parsed = parse_ban_line(ban_line, extra_reason_lines=extra_lines)
    if "error" in parsed:
        bot.reply_to(message, parsed["error"])
        return

    is_ip, delta, reason = parsed["is_ip"], parsed["delta"], parsed["reason"]

    if delta is None:
        until_iso, until_text = None, "бессрочно"
    else:
        until_dt = datetime.now() + delta
        until_iso = until_dt.isoformat()
        until_text = until_dt.strftime("%d.%m.%Y %H:%M")

    user_entry = bans.setdefault(str(target_id), {"normal": None, "ip": None})
    user_entry["ip" if is_ip else "normal"] = {"until": until_iso, "reason": reason}
    save_bans(bans)

    ip_suffix = " по IP." if is_ip else "."
    if delta is None:
        ban_text = f"Вы были заблокированы в боте навсегда{ip_suffix}"
    else:
        ban_text = (
            f"Вы были заблокированы в боте на {duration_to_words(delta)}{ip_suffix}\n"
            f"Дата разблокировки: {until_text}"
        )
    try:
        bot.send_message(target_id, ban_text)
    except Exception:
        pass

    admin_text = (
        f"✅ Пользователь {target_id} забанен ({'IP-пометка' if is_ip else 'аккаунт'}) "
        f"до: {until_text}\nПричина: {reason}"
    )
    if is_ip:
        admin_text += (
            "\n\n⚠️ Напоминание: реальный бан по IP через Telegram технически невозможен "
            "(Bot API не передаёт IP-адреса) — это лишь пометка, не блокирующая другие аккаунты."
        )
    bot.reply_to(message, admin_text)


# ---------- Приём сообщений от пользователей ----------

@bot.message_handler(
    func=lambda m: m.chat.id != ADMIN_ID,
    content_types=["text", "photo", "video", "document", "voice", "sticker", "audio"],
)
def handle_user_message(message):
    user = message.from_user

    if is_banned(user.id):
        bot.reply_to(message, "Извините, ваше сообщение не было отправлено из-за блокировки.")
        return

    username = f"@{user.username}" if user.username else "none"
    header = f"Username: {username}\nID: {user.id}"

    if message.content_type == "text":
        sent = bot.send_message(ADMIN_ID, f"{header}\n\n{message.text}")
        message_map[sent.message_id] = user.id
    else:
        forwarded = bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
        info_msg = bot.send_message(ADMIN_ID, f"⬆️ {header}")
        message_map[forwarded.message_id] = user.id
        message_map[info_msg.message_id] = user.id

    bot.reply_to(message, "✅ Ваше сообщение отправлено администраторам. Ожидайте ответа.")


if __name__ == "__main__":
    print("Бот запущен...")
    bot.infinity_polling()
