import os
import asyncio
import json
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession

user_states = {}    # Tracks user login steps by chat_id
user_clients = {}   # Stores TelegramClient instances by chat_id
SESSIONS_FILE = "sessions.json"
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

client = TelegramClient('bot_session', API_ID, API_HASH)

def save_sessions():
    data = {}
    for chat_id, client in user_clients.items():
        try:
            data[str(chat_id)] = client.session.save()
        except Exception as e:
            print(f"Failed saving session for {chat_id}: {e}")
    with open(SESSIONS_FILE, "w") as f:
        json.dump(data, f)

def load_sessions():
    try:
        with open(SESSIONS_FILE, "r") as f:
            data = json.load(f)
        return {int(k): v for k, v in data.items()}
    except Exception:
        return {}

async def start_login_flow(event):
    chat_id = event.chat_id
    user_states[chat_id] = {"step": "awaiting_api_id"}
    await event.reply("Welcome! Please send your **API_ID** (number) from https://my.telegram.org/apps")

async def handle_message(event):
    chat_id = event.chat_id
    text = event.raw_text.strip()

    if chat_id not in user_states:
        return

    state = user_states[chat_id]

    try:
        if state["step"] == "awaiting_api_id":
            if not text.isdigit():
                await event.reply("API_ID must be a number. Try again.")
                return
            state["api_id"] = int(text)
            state["step"] = "awaiting_api_hash"
            await event.reply("Now please send your **API_HASH** (32 chars)")

        elif state["step"] == "awaiting_api_hash":
            if len(text) != 32:
                await event.reply("API_HASH should be 32 characters. Try again.")
                return
            state["api_hash"] = text
            state["step"] = "awaiting_phone"
            await event.reply("Send your phone number with country code (e.g., +123456789)")

        elif state["step"] == "awaiting_phone":
            if not text.startswith('+') or not text[1:].isdigit():
                await event.reply("Phone number should start with '+' and digits. Try again.")
                return
            state["phone"] = text
            state["step"] = "sending_code"
            await event.reply("Connecting and sending login code...")

            client = TelegramClient(StringSession(), state["api_id"], state["api_hash"])
            user_clients[chat_id] = client
            await client.connect()

            if await client.is_user_authorized():
                await event.reply("You are already logged in!")
                state["step"] = "logged_in"
                return

            try:
                await client.send_code_request(state["phone"])
            except errors.PhoneNumberInvalidError:
                await event.reply("Invalid phone number. Send it again.")
                state["step"] = "awaiting_phone"
                return
            except Exception as e:
                await event.reply(f"Failed to send code: {e}")
                user_states.pop(chat_id, None)
                user_clients.pop(chat_id, None)
                return

            state["step"] = "awaiting_code"
            await event.reply("Code sent! Please enter the login code.")

        elif state["step"] == "awaiting_code":
            client = user_clients.get(chat_id)
            if not client:
                await event.reply("Session lost. Please /start again.")
                user_states.pop(chat_id, None)
                return

            try:
                # Normal sign-in attempt with code
                await client.sign_in(state["phone"], text)
                await event.reply("Logged in successfully!")
                state["step"] = "logged_in"
                save_sessions()
            except errors.SessionPasswordNeededError:
                # 2FA password required, ask user for it
                state["step"] = "awaiting_2fa_password"
                await event.reply("Two-step verification enabled. Please enter your password.")
            except errors.CodeInvalidError:
                await event.reply("Invalid code. Try again.")
            except Exception as e:
                await event.reply(f"Login failed: {e}")
                user_states.pop(chat_id, None)
                user_clients.pop(chat_id, None)

        elif state["step"] == "awaiting_2fa_password":
            client = user_clients.get(chat_id)
            if not client:
                await event.reply("Session lost. Please /start again.")
                user_states.pop(chat_id, None)
                return

            try:
                # Use sign_in with password to complete 2FA
                await client.sign_in(password=text)
                await event.reply("Password accepted! Logged in.")
                state["step"] = "logged_in"
                save_sessions()
            except errors.PasswordHashInvalidError:
                await event.reply("Incorrect password. Try again.")
            except Exception as e:
                await event.reply(f"Password error: {e}")
                user_states.pop(chat_id, None)
                user_clients.pop(chat_id, None)

        elif state["step"] == "logged_in":
            await event.reply("You're logged in! You can proceed with your commands (e.g., upload VCF).")

    except Exception as e:
        await event.reply(f"Unexpected error: {e}")
        user_states.pop(chat_id, None)
        user_clients.pop(chat_id, None)

@client.on(events.NewMessage(pattern='/start'))
async def start(event):
    await start_login_flow(event)

@client.on(events.NewMessage)
async def all_messages(event):
    # Ignore commands here to avoid conflicts
    if event.raw_text.startswith('/'):
        return
    await handle_message(event)

async def main():
    global client
    await client.start()
    print("Bot is running...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
