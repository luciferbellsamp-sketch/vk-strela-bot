from vkbottle import Bot
from vkbottle.bot import Message
import os

TOKEN = os.getenv("TOKEN")

bot = Bot(token=TOKEN)

@bot.on.message(text=["/ping", "ping"])
async def ping_handler(message: Message):
    await message.answer("Бот работает ✅")

print("Bot started")
bot.run_forever()
