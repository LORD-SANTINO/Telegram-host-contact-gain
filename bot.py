import os
import asyncio
import json
import vobject
import random
from telethon.tl.functions.contacts import ImportContactsRequest
from telethon.tl.types import InputPhoneContact
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession

# Globals for user states and clients
user_states = {}    # e.g. {chat_id: {"step": "...", ...}}
user_clients = {}   # e.g. {chat_id: TelegramClient instance}
user_sessions = {}  # e.g. {chat_id: string_session}
SESSIONS_DB = 'user_sessions.json'

# Load bot API credentials from environment
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

client = TelegramClient('bot_session', API_ID, API_HASH)

def save_all_sessions():
    # Save all StringSessions to file for persistence
    data = {}
    for chat_id, client in user_clients.items():
        try:
            data[str(chat_id)] = client.session.save()
        except Exception as e:
            print(f"Error saving session {chat_id}: {e}")
    with open(SESSIONS_DB, "w") as f:
        json.dump(data, f)

def load_all_sessions():
    # Load all sessions from file and recreate clients
    global user_sessions, user_clients
    try:
        with open(SESSIONS_DB, "r") as f:
            user_sessions = json.load(f)
    except:
        user_sessions = {}

    for chat_id_str, sess_str in user_sessions.items():
        chat_id = int(chat_id_str)
        client = TelegramClient(StringSession(sess_str), BOT_API_ID, BOT_API_HASH)
        user_clients[chat_id] = client

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
            await event.reply("Now please send your **API_HASH** (32 characters)")

        elif state["step"] == "awaiting_api_hash":
            if len(text) != 32:
                await event.reply("API_HASH should be exactly 32 characters. Try again.")
                return
            state["api_hash"] = text
            state["step"] = "awaiting_phone"
            await event.reply("Send your phone number with country code (e.g., +123456789)")

        elif state["step"] == "awaiting_phone":
            if not text.startswith('+') or not text[1:].isdigit():
                await event.reply("Phone number should start with '+' and contain digits only. Try again.")
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
                # Save session for good measure
                save_all_sessions()
                return

            try:
                await client.send_code_request(state["phone"])
            except errors.PhoneNumberInvalidError:
                await event.reply("Invalid phone number provided. Please send it again.")
                state["step"] = "awaiting_phone"
                return
            except Exception as e:
                await event.reply(f"Failed to send code: {e}")
                user_states.pop(chat_id, None)
                user_clients.pop(chat_id, None)
                return

            state["step"] = "awaiting_code"
            await event.reply("Login code sent! Please enter the code you received via Telegram.")

        elif state["step"] == "awaiting_code":
            client = user_clients.get(chat_id)
            if not client:
                await event.reply("Session lost or expired. Please /start again.")
                user_states.pop(chat_id, None)
                return

            try:
                # Try signing in using phone and code
                await client.sign_in(state["phone"], text)
                await event.reply("Login successful! You are now logged in.")
                state["step"] = "logged_in"
                save_all_sessions()
            except errors.SessionPasswordNeededError:
                # 2FA password required
                state["step"] = "awaiting_2fa_password"
                await event.reply("Two-factor authentication enabled. Please enter your password.")
            except errors.CodeInvalidError:
                await event.reply("Invalid code entered. Please try again.")
            except Exception as e:
                await event.reply(f"Login failed: {e}")
                user_states.pop(chat_id, None)
                user_clients.pop(chat_id, None)

        elif state["step"] == "awaiting_2fa_password":
            client = user_clients.get(chat_id)
            if not client:
                await event.reply("Session lost or expired. Please /start again.")
                user_states.pop(chat_id, None)
                return

            try:
                # Sign in with 2FA password
                await client.sign_in(password=text)
                await event.reply("Two-factor password accepted! Login complete.")
                state["step"] = "logged_in"
                save_all_sessions()
            except errors.PasswordHashInvalidError:
                await event.reply("Incorrect password. Try again.")
            except Exception as e:
                await event.reply(f"Two-factor login failed: {e}")
                user_states.pop(chat_id, None)
                user_clients.pop(chat_id, None)

        elif state["step"] == "logged_in":
            await event.reply(
                "You're already logged in.\n"
                "You can now upload your VCF or use other commands (not implemented yet)."
            )

    except Exception as e:
        await event.reply(f"Unexpected error: {e}")
        user_states.pop(chat_id, None)
        user_clients.pop(chat_id, None)

@events.register(events.NewMessage(pattern='/start'))
async def start(event):
    await start_login_flow(event)

@events.register(events.NewMessage)
async def all_messages(event):
    if event.raw_text.startswith('/'):
        # Ignore commands here to avoid conflicts
        return
    await handle_message(event)


@client.on(events.NewMessage(pattern='/upload_vcf'))
async def upload_vcf_handler(event):
    chat_id = event.chat_id
    if chat_id not in user_clients:
        await event.reply("You need to be logged in first via /start.")
        return
    await event.reply("Upload your VCF file now (send as a file).")

@client.on(events.NewMessage)
async def receive_vcf(event):
    chat_id = event.chat_id
    if chat_id not in user_clients or not event.document:
        return

    if event.document.mime_type == 'text/vcard' or (event.document.attributes and event.document.attributes[0].file_name.endswith('.vcf')):
        vcf_path = await event.message.download_media(file='temp.vcf')
        await process_and_store_vcf(event, vcf_path)
    else:
        await event.reply("Please upload a valid .vcf file.")

async def process_and_store_vcf(event, vcf_path):
    chat_id = event.chat_id
    client = user_clients[chat_id]

    contacts = []
    try:
        with open(vcf_path, 'r', encoding='utf-8') as f:
            for vcard in vobject.readComponents(f):
                name = getattr(vcard, 'fn', None)
                if name:
                    name = name.value
                else:
                    name = 'Unknown'
                if hasattr(vcard, 'tel_list'):
                    for tel in vcard.tel_list:
                        phone_num = tel.value.strip().replace(' ', '').replace('-', '')
                        if phone_num.startswith('+'):
                            contacts.append({'phone': phone_num, 'name': name})
    except Exception as e:
        await event.reply(f"Failed to parse VCF file: {e}")
        os.remove(vcf_path)
        return

    if not contacts:
        await event.reply("No valid contacts found in the VCF file.")
        os.remove(vcf_path)
        return

    imported_users = []
    failed_contacts = []
    batch_size = 30
    pause_between = 10

    for i in range(0, len(contacts), batch_size):
        batch = contacts[i:i + batch_size]
        phone_contacts = [
            InputPhoneContact(
                client_id=random.randint(0, 999999),
                phone=c['phone'],
                first_name=c['name'],
                last_name=''
            ) for c in batch
        ]

        try:
            result = await client(ImportContactsRequest(contacts=phone_contacts))
            for user in result.users:
                imported_users.append({
                    'id': user.id,
                    'access_hash': user.access_hash,
                    'first_name': user.first_name or 'Unknown',
                    'phone': user.phone
                })
            await event.reply(f"Imported batch {i//batch_size+1}")
            await asyncio.sleep(pause_between)
        except Exception as e:
            failed_contacts.extend(batch)
            await event.reply(f"Error importing batch {i//batch_size+1}: {str(e)}")

    # Save imported users to a user-specific file or memory
    # This part you can customize depending on your app design

    await event.reply(f"VCF processing complete. Imported {len(imported_users)} contacts.")

    os.remove(vcf_path)

async def main():
    global client

    load_all_sessions()  # Load all saved user sessions and initialize clients

    # Create the bot client to listen for
    await client.start()
    print("Bot is running...")

    # Start all user TelegramClients loaded from sessions
    for user_client in user_clients.values():
        await user_client.start()

    await client.run_until_disconnected()

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
            
