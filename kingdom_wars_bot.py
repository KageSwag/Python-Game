"""
⚜️ Kingdom Wars — Telegram strategy bot (v1)
pip install aiogram
export BOT_TOKEN=123:ABC
export GAME_SPEED=1      # برای تست: 60 (همه‌ی زمان‌ها ۶۰ برابر سریع‌تر)
python kingdom_wars_bot.py
"""
import asyncio, html, json, logging, math, os, random, sqlite3, time
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

TOKEN = os.getenv("BOT_TOKEN", "")
SPEED = float(os.getenv("GAME_SPEED", "1"))
r = Router()
esc = html.escape

# ───────────────────────── دیتابیس ─────────────────────────
db = sqlite3.connect(os.getenv("DB_PATH", "kingdom.db"), check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS players(id INTEGER PRIMARY KEY,name TEXT,x REAL,y REAL,terrain TEXT,
 gold REAL,food REAL,iron REAL,wood REAL,t REAL,b TEXT,a TEXT,q TEXT,tq TEXT,morale REAL,shield REAL,
 daily REAL,streak INTEGER,tut INTEGER,dplan TEXT,wins INTEGER,losses INTEGER,loot REAL);
CREATE TABLE IF NOT EXISTS marches(id INTEGER PRIMARY KEY AUTOINCREMENT,att INTEGER,dfn INTEGER,
 units TEXT,plan TEXT,arrive REAL,dur REAL,status INTEGER,loot TEXT);
CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY AUTOINCREMENT,pid INTEGER,ts REAL,brief TEXT);
CREATE TABLE IF NOT EXISTS hits(att INTEGER,dfn INTEGER,ts REAL);
""")
COLS = ["id","name","x","y","terrain","gold","food","iron","wood","t","b","a","q","tq","morale",
        "shield","daily","streak","tut","dplan","wins","losses","loot"]
JC = ("b", "a", "q", "tq")

# ───────────────────────── داده‌های بازی ─────────────────────────
RES = [("gold","🪙","طلا"),("food","🌾","غذا"),("iron","⛓","آهن"),("wood","🪵","چوب")]
BUILD = {  # emoji, name, resource, base/h per level
    "mine":("⛏","معدن طلا","gold",120), "farm":("🌾","مزرعه","food",150),
    "forge":("🔥","آهنگری","iron",60), "lumber":("🪓","چوب‌بری","wood",90),
    "barracks":("🏯","پادگان",None,0), "wall":("🧱","دیوار",None,0),
    "tower":("🗼","برج دیده‌بانی",None,0), "storage":("📦","انبار",None,0)}
UN = {
    "inf":  dict(e="🗡",n="پیاده‌نظام",atk=6, df=10,up=1,carry=30,time=15,cost=dict(gold=30,food=20,iron=10)),
    "arc":  dict(e="🏹",n="کماندار",   atk=9, df=5, up=1,carry=20,time=20,cost=dict(gold=40,food=20,wood=15)),
    "cav":  dict(e="🐎",n="سواره‌نظام",atk=16,df=8, up=2,carry=60,time=40,cost=dict(gold=90,food=40,iron=40)),
    "siege":dict(e="💣",n="منجنیق",    atk=20,df=2, up=3,carry=0, time=90,cost=dict(gold=150,food=60,iron=60,wood=80))}
TERRAIN = {  # emoji, name, ضریب دفاع، ضریب واحدهای مهاجم
    "plains":("🌾","جلگه",1.0,{"cav":1.2}), "mountain":("⛰","کوهستان",1.25,{"cav":0.8}),
    "forest":("🌲","جنگل",1.1,{"arc":0.8}), "river":("🌊","رود",1.15,{})}
ST = {"assault":("⚔️","یورش",1.25,1.3), "hold":("🛡","سنگر",0.9,0.7), "flank":("🌀","دور زدن",1.0,1.0)}
MX = {"assault":{"assault":1.0,"hold":0.85,"flank":1.2},
      "hold":{"assault":1.15,"hold":1.0,"flank":0.8},
      "flank":{"assault":0.85,"hold":1.3,"flank":1.0}}
DPLAN = {"fort":("🏰","سنگر محکم",["hold","hold","hold"]),
         "bal":("⚖️","متعادل",["hold","assault","hold"]),
         "counter":("🗡","ضدحمله",["assault","flank","assault"])}
PHASE = ["برخورد اول","نبرد اصلی","ضربه‌ی نهایی"]

# ───────────────────────── ابزارها ─────────────────────────
def fa(n): return f"{int(n):,}".translate(str.maketrans("0123456789,","۰۱۲۳۴۵۶۷۸۹،"))
def fmt(s):
    s = int(max(0, s)); h, rem = divmod(s, 3600); m, sec = divmod(rem, 60)
    return f"{fa(h)}س {fa(m)}د" if h else (f"{fa(m)}د {fa(sec)}ث" if m else f"{fa(sec)}ث")
def bar(v, mx, n=8):
    k = int(n * min(1, v / max(mx, 1))); return "▰" * k + "▱" * (n - k)
def kb(rows): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows])
def cap(p): return 2000 + 1500 * p["b"]["storage"]
def power(a, b): return int(sum(n * (UN[u]["atk"] + UN[u]["df"]) for u, n in a.items()) + sum(b.values()) * 20)
def upkeep(p): return sum(n * UN[u]["up"] for u, n in p["a"].items())
def prod(p, res): return sum(v[3] * p["b"][k] for k, v in BUILD.items() if v[2] == res)
def bcost(L): m = 1.7 ** L; return {"gold": int(120 * m), "wood": int(90 * m), "iron": int(25 * m) if L else 0}
def btime(L): return 120 * 1.8 ** L / SPEED
def can(p, c): return all(p[k] >= v for k, v in c.items())
def pay(p, c):
    for k, v in c.items(): p[k] -= v
def costs(c): return " ".join(f"{fa(v)}{e}" for (k, e, _), v in ((x, c.get(x[0], 0)) for x in RES) if v)
def travel(a, b):
    d = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
    return (5 + d * 0.45) * 60 * (1.2 if b["terrain"] == "river" else 1) / SPEED

# ───────────────────────── بازیکن ─────────────────────────
def save(p):
    vals = [json.dumps(p[c]) if c in JC else p[c] for c in COLS]
    db.execute(f"INSERT OR REPLACE INTO players({','.join(COLS)}) VALUES({','.join('?'*len(COLS))})", vals); db.commit()

def tick(p):
    now = time.time()
    if p["q"] and p["q"]["done"] <= now: p["b"][p["q"]["k"]] += 1; p["q"] = None
    rest = []
    for j in p["tq"]:
        if j["done"] <= now: p["a"][j["u"]] += j["n"]
        else: rest.append(j)
    p["tq"] = rest
    dt = (now - p["t"]) / 3600; p["t"] = now; c = cap(p)
    for k, _, _ in RES:
        if k != "food" or True: p[k] = max(p[k], min(c, p[k] + prod(p, k) * dt))
    p["food"] -= upkeep(p) * dt
    if p["food"] < 0:  # قحطی
        p["food"] = 0; p["morale"] = max(5, p["morale"] - 10 * dt)
        for u in p["a"]: p["a"][u] = int(p["a"][u] * (1 - min(0.5, 0.05 * dt)))
    else: p["morale"] = min(100, p["morale"] + 3 * dt)

def load(uid):
    row = db.execute("SELECT * FROM players WHERE id=?", (uid,)).fetchone()
    if not row: return None
    p = dict(row)
    for c in JC: p[c] = json.loads(p[c])
    tick(p); save(p); return p

def create(u):
    b = {k: 1 for k in BUILD}; b["wall"] = 0; b["tower"] = 0
    p = dict(id=u.id, name=(u.first_name or "فرمانده")[:18], x=random.uniform(0, 100), y=random.uniform(0, 100),
             terrain=random.choice(list(TERRAIN)), gold=500, food=600, iron=150, wood=400, t=time.time(), b=b,
             a={"inf": 10, "arc": 5, "cav": 0, "siege": 0}, q=None, tq=[], morale=80, shield=time.time() + 48 * 3600,
             daily=0, streak=0, tut=0, dplan="bal", wins=0, losses=0, loot=0)
    save(p); return p

async def notify(bot, pid, text, brief):
    db.execute("INSERT INTO reports(pid,ts,brief) VALUES(?,?,?)", (pid, time.time(), brief)); db.commit()
    try: await bot.send_message(pid, text, reply_markup=kb([[("🏰 منو", "m:home")]]))
    except Exception: pass

# ───────────────────────── صفحه‌ها ─────────────────────────
def home_text(p):
    now = time.time(); T = TERRAIN[p["terrain"]]; cp = cap(p)
    sh = f"🛡 سپر نوآموز: <b>{fmt(p['shield']-now)}</b>" if p["shield"] > now else "⚔️ سپر ندارید؛ قلمرو در معرض حمله است"
    L = ["┏━━━━━━━━━━━━━━━━━┓", "    ⚜️ <b>K I N G D O M   W A R S</b> ⚜️", "┗━━━━━━━━━━━━━━━━━┛",
         f"👑 <b>{esc(p['name'])}</b>   {T[0]} {T[1]}",
         f"⚡ قدرت <b>{fa(power(p['a'], p['b']))}</b>   🎖 روحیه <b>{fa(p['morale'])}٪</b>", sh, "", "<b>— خزانه —</b>"]
    for k, e, n in RES: L.append(f"{e} {n}  <code>{fa(p[k])}</code>  {bar(p[k], cp)}")
    net = prod(p, "food") - upkeep(p)
    L.append(f"🍞 جیره‌ی ارتش: <b>{fa(net)}</b> در ساعت" + ("  ⚠️ قحطی در راهه!" if net < 0 else ""))
    ev = []
    if p["q"]: ev.append(f"🔨 {BUILD[p['q']['k']][1]} ← {fmt(p['q']['done']-now)}")
    for j in p["tq"][:2]: ev.append(f"🏋 {fa(j['n'])} {UN[j['u']]['n']} ← {fmt(j['done']-now)}")
    for m in db.execute("SELECT * FROM marches WHERE att=?", (p["id"],)):
        ev.append(("🐎 لشکر در حال بازگشت ← " if m["status"] else "⚔️ لشکر در راه نبرد ← ") + fmt(m["arrive"] - now))
    if p["b"]["tower"] >= 1:
        for m in db.execute("SELECT * FROM marches WHERE dfn=? AND status=0", (p["id"],)):
            ev.append(f"🚨 <b>هشدار دیده‌بان:</b> لشکر دشمن ← {fmt(m['arrive']-now)}")
    if ev: L += ["", "<b>— جبهه —</b>"] + ev
    L += ["", "<i>فرمانده، دستور بدید.</i>"]
    return "\n".join(L)

def home_kb(p):
    n = db.execute("SELECT COUNT(*) FROM marches WHERE att=?", (p["id"],)).fetchone()[0]
    war = f"⚔️ لشکرکشی ({fa(n)})" if n else "⚔️ لشکرکشی"
    return kb([[("🏰 قلمرو من", "m:kingdom"), (war, "m:war")],
               [("🛡 ارتش و پادگان", "m:army"), ("💰 اقتصاد", "m:econ")],
               [("🗺 نقشه‌ی جهان", "m:map"), ("🏆 رتبه‌بندی", "m:rank")],
               [("🤝 اتحادها 🔒", "lock:ally"), ("🕵️ جاسوسی 🔒", "lock:spy")],
               [("📜 گزارش‌ها", "m:reports"), ("🎁 پاداش روزانه", "daily")],
               [("📖 آموزش" + (" 🆕" if not p["tut"] else ""), "tut:0"), ("🔄 بروزرسانی", "m:home")]])

BACK = [("🏰 منوی اصلی", "m:home")]

def kingdom_screen(p):
    L = ["🏰 <b>قلمرو من</b>", ""]; rows = []; row = []
    for k, (e, n, res, rate) in BUILD.items():
        lv = p["b"][k]
        eff = {"mine":f"+{fa(rate*lv)}/س","farm":f"+{fa(rate*lv)}/س","forge":f"+{fa(rate*lv)}/س","lumber":f"+{fa(rate*lv)}/س",
               "barracks":f"صف آموزش {fa(30*lv)}","wall":f"دفاع +{fa(12*lv)}٪","tower":f"دید و هشدار سطح {fa(lv)}",
               "storage":f"ظرفیت {fa(2000+1500*lv)}"}[k]
        L.append(f"{e} <b>{n}</b> · سطح {fa(lv)} · {eff}")
        if lv < 10: L.append(f"     ⬆️ {costs(bcost(lv))} · {fmt(btime(lv))}")
        row.append((f"{e} {n} ⬆️", f"b:{k}"))
        if len(row) == 2: rows.append(row); row = []
    if p["q"]: L += ["", f"🔨 در حال ساخت: {BUILD[p['q']['k']][1]} ← {fmt(p['q']['done']-time.time())}"]
    return "\n".join(L), kb(rows + [BACK])

def army_screen(p):
    L = ["🛡 <b>ارتش و پادگان</b>", ""]; rows = []
    for u, d in UN.items():
        L.append(f"{d['e']} <b>{d['n']}</b>: <code>{fa(p['a'][u])}</code> · ⚔️{d['atk']} 🛡{d['df']} · جیره {d['up']} · بار {d['carry']}")
        L.append(f"     {costs(d['cost'])} · {fa(d['time'])}ث/نفر")
        rows.append([(f"{d['e']} ×{n}", f"t:{u}:{n}") for n in (5, 10, 25)])
    q = sum(j["n"] for j in p["tq"])
    L += ["", f"📋 صف آموزش: {fa(q)}/{fa(30*p['b']['barracks'])}", f"🍞 مصرف: {fa(upkeep(p))} غذا در ساعت", "",
          "<b>فرمان دفاعی</b> (وقتی حمله‌ای برسه):"]
    for k, (e, n, plan) in DPLAN.items():
        L.append(("✅ " if p["dplan"] == k else "▫️ ") + f"{e} {n}: " + " ← ".join(ST[s][0] for s in plan))
    rows.append([((("✅" if p["dplan"] == k else "") + f"{e} {n}"), f"dp:{k}") for k, (e, n, _) in DPLAN.items()])
    L += ["", "<i>واحدها نقش دارن: پیاده‌نظام ستون دفاعه، سواره ضربه‌زن اصلی، کماندار ارزان و پرقدرت، منجنیق دیوار رو می‌شکنه.</i>"]
    return "\n".join(L), kb(rows + [BACK])

def econ_screen(p):
    L = ["💰 <b>اقتصاد</b>", "", "<b>تولید در ساعت:</b>"]
    for k, e, n in RES: L.append(f"{e} {n}: +{fa(prod(p, k))}" + (f"  − جیره {fa(upkeep(p))}" if k == "food" else ""))
    L += ["", f"📦 ظرفیت انبار: {fa(cap(p))}", "<i>منابع وقتی آفلاینی هم جمع می‌شن، تا سقف انبار. انبار پر = تولید هدر می‌ره.</i>"]
    return "\n".join(L), kb([[("🎁 پاداش روزانه", "daily")], BACK])

def map_screen(p):
    W, H = 10, 7; g = [["⬛"] * W for _ in range(H)]; rows = db.execute("SELECT id,name,x,y,terrain,shield FROM players").fetchall()
    for q in rows:
        cx, cy = min(W-1, int(q["x"]/100*W)), min(H-1, int(q["y"]/100*H)); cur = g[cy][cx]
        g[cy][cx] = "📍" if q["id"] == p["id"] else (cur if cur == "📍" else (TERRAIN[q["terrain"]][0] if cur == "⬛" else "🏰"))
    near = sorted((q for q in rows if q["id"] != p["id"]), key=lambda q: math.hypot(q["x"]-p["x"], q["y"]-p["y"]))[:5]
    L = ["🗺 <b>نقشه‌ی جهان</b>", "", "\n".join("".join(r_) for r_ in g), "", "📍 شما   🌾 جلگه  ⛰ کوه  🌲 جنگل  🌊 رود  🏰 چند قلمرو", "", "<b>نزدیک‌ترین قلمروها:</b>"]
    for q in near:
        L.append(f"{TERRAIN[q['terrain']][0]} {esc(q['name'])} · {fmt(travel(p, dict(q)))} راه" + (" 🛡" if q["shield"] > time.time() else ""))
    if not near: L.append("هنوز کسی نیست؛ دوستانتون رو دعوت کنید!")
    return "\n".join(L), kb([BACK])

def rank_screen(p):
    rows = sorted(db.execute("SELECT id,name,a,b,wins,loot FROM players"), key=lambda q: -power(json.loads(q["a"]), json.loads(q["b"])))
    L = ["🏆 <b>تالار قدرت</b>", ""]; med = ["🥇", "🥈", "🥉"]
    for i, q in enumerate(rows[:10]):
        L.append(f"{med[i] if i < 3 else fa(i+1)+'.'} {esc(q['name'])} · ⚡{fa(power(json.loads(q['a']), json.loads(q['b'])))} · 🏆{fa(q['wins'])}")
    me = next((i for i, q in enumerate(rows) if q["id"] == p["id"]), 0)
    L += ["", f"جایگاه شما: <b>{fa(me+1)}</b> از {fa(len(rows))}"]
    return "\n".join(L), kb([BACK])

def reports_screen(p):
    rows = db.execute("SELECT * FROM reports WHERE pid=? ORDER BY id DESC LIMIT 8", (p["id"],)).fetchall()
    L = ["📜 <b>گزارش‌ها</b>", ""] + ([f"• {esc(q['brief'])}" for q in rows] or ["هنوز گزارشی نیست."])
    return "\n".join(L), kb([BACK])

# ───────────────────────── آموزش ─────────────────────────
TUT = [
 ("👑 خوش آمدی فرمانده", "سرزمین‌ها در آشوبن و هر پیمان، یه خنجر پشت پرده‌ست. تو یه قلمرو کوچیک داری؛ هدف: <b>رشد کن، ارتش بساز، دشمنات رو نابود کن و اسمت رو توی تالار قدرت بنویس.</b>\n\nهر وقت بنویسی «<b>منو</b>» پنل فرماندهی باز می‌شه."),
 ("💰 منابع", "چهار منبع داری: 🪙طلا 🌾غذا ⛓آهن 🪵چوب.\n\n• منابع <b>حتی وقتی آفلاینی</b> جمع می‌شن\n• انبار ظرفیت داره؛ پر شد تولید هدر می‌ره\n• معدن، مزرعه، آهنگری و چوب‌بری تولید رو بالا می‌برن\n• هر روز «🎁 پاداش روزانه» بگیر؛ پشت‌سرهم بیشتر می‌شه"),
 ("🏰 ساختمان‌ها", "از «قلمرو من» ساختمان‌ها رو ارتقا بده. ساخت <b>زمان‌بره</b> و هر بار فقط یکی در جریانه.\n\n• 🏯 پادگان: ظرفیت و سرعت آموزش\n• 🧱 دیوار: دفاع بالاتر (منجنیق ضد دیوار)\n• 🗼 دیده‌بان: لشکر دشمن رو <b>قبل از رسیدن</b> می‌بینی\n• 📦 انبار: ظرفیت منابع"),
 ("🛡 ارتش و جیره", "سرباز می‌گیری، اما <b>ارتش غذا می‌خوره</b>.\n\n• 🗡 پیاده: ستون دفاع\n• 🏹 کماندار: ارزان، ضربه‌ی خوب\n• 🐎 سواره: ضربه‌ی سنگین، گرون\n• 💣 منجنیق: دیوار رو می‌شکنه، ضعیف در دفاع\n\n⚠️ غذا تموم شه: روحیه می‌ریزه و سربازها فرار می‌کنن!"),
 ("⚔️ جنگ", "از «لشکرکشی» هدف رو انتخاب کن، درصد نیرو بفرست و <b>برای ۳ مرحله‌ی نبرد فرمان مخفی بده</b>:\n\n⚔️ یورش: قوی ولی پرتلفات\n🛡 سنگر: کم‌تلفات ولی ضعیف‌تر\n🌀 دور زدن: روی سنگر دشمن برنده‌ست، روی یورش بازنده\n\nدشمن هم فرمان دفاعی داره. <b>حدس بزن چی می‌چینه.</b> لشکرها زمان سفر دارن (۵ تا ~۶۰ دقیقه)."),
 ("🗺 زمین و اطلاعات ناقص", "هر قلمرو یه زمین داره:\n🌾 جلگه: سواره‌ی مهاجم قوی‌تره\n⛰ کوه: دفاع بالا، سواره ضعیف\n🌲 جنگل: کماندار مهاجم ضعیف\n🌊 رود: سفر سخت‌تر، دفاع خوب\n\n🌫 قدرت دشمن رو <b>دقیق نمی‌بینی</b>، فقط یه بازه‌ی تخمینی. برج دیده‌بان بازه رو دقیق‌تر می‌کنه."),
 ("🔥 قوانین بقا", "• ۴۸ ساعت اول 🛡 سپر داری. حمله کنی، سپرت می‌شکنه\n• یه نفر رو پشت‌سرهم نمی‌شه زد (کولداون یک‌ساعته)\n• بعد از باخت سنگین، فرمانده ممکنه اسیر شه و باج بدی\n• رویدادهای تصادفی: راهزن، قحطی، طاعون، برداشت خوب\n• 🤝 اتحاد و 🕵️ جاسوسی در آپدیت بعدی میان\n\nآماده‌ای؟ جایزه‌ی آموزش رو بگیر و برو سراغ فتح!")]

def tut_screen(p, i):
    i = max(0, min(len(TUT) - 1, i)); t, body = TUT[i]
    dots = " ".join("●" if j == i else "○" for j in range(len(TUT)))
    nav = []
    if i: nav.append(("⬅️ قبلی", f"tut:{i-1}"))
    if i < len(TUT) - 1: nav.append(("بعدی ➡️", f"tut:{i+1}"))
    rows = [nav] if nav else []
    if i == len(TUT) - 1: rows.append([("🎁 دریافت جایزه‌ی آموزش" if not p["tut"] else "✅ جایزه گرفته شد", "tutdone")])
    rows.append(BACK)
    return f"📖 <b>آموزش · {t}</b>\n\n{body}\n\n{dots}", kb(rows)

# ───────────────────────── عملیات ─────────────────────────
def upgrade(p, k):
    L = p["b"][k]
    if p["q"]: return "⏳ یک ساخت‌وساز در جریانه"
    if L >= 10: return "به حداکثر سطح رسیده"
    c = bcost(L)
    if not can(p, c): return "❌ منابع کافی نیست"
    pay(p, c); p["q"] = {"k": k, "done": time.time() + btime(L)}; save(p); return "🔨 ساخت شروع شد"

def train(p, u, n):
    c = {k: v * n for k, v in UN[u]["cost"].items()}
    if sum(j["n"] for j in p["tq"]) + n > 30 * p["b"]["barracks"]: return "❌ صف پادگان پره"
    if not can(p, c): return "❌ منابع کافی نیست"
    pay(p, c); start = max([time.time()] + [j["done"] for j in p["tq"]])
    p["tq"].append({"u": u, "n": n, "done": start + n * UN[u]["time"] / p["b"]["barracks"] / SPEED}); save(p)
    return f"🏋 آموزش {fa(n)} {UN[u]['n']} شروع شد"

def daily(p):
    now = time.time()
    if now - p["daily"] < 20 * 3600: return f"⏳ {fmt(20*3600-(now-p['daily']))} دیگه"
    p["streak"] = p["streak"] + 1 if now - p["daily"] < 48 * 3600 else 1; s = min(p["streak"], 7)
    p["gold"] += 150 + 50 * s; p["food"] += 100; p["iron"] += 30 + 10 * s; p["daily"] = now; save(p)
    return f"🎁 روز {fa(p['streak'])} پشت‌سرهم! +{fa(150+50*s)} طلا"

def estimate(power_, tower):
    n = max(0.05, 0.30 - 0.04 * tower); return int(power_ * random.uniform(1 - n, 1)), int(power_ * random.uniform(1, 1 + n))

# ───────────────────────── لشکرکشی ─────────────────────────
drafts = {}

def targets(p):
    rows = [q for q in db.execute("SELECT * FROM players WHERE id!=?", (p["id"],))]
    return sorted(rows, key=lambda q: math.hypot(q["x"] - p["x"], q["y"] - p["y"]))[:6]

def war_view(p):
    d = drafts.get(p["id"]); now = time.time()
    if not d:
        L = ["⚔️ <b>لشکرکشی</b>", "", "هدف رو انتخاب کن (نزدیک‌ترین‌ها):"]; rows = []
        for q in targets(p):
            sh = q["shield"] > now
            rows.append([((("🛡 " if sh else TERRAIN[q["terrain"]][0] + " ") + f"{q['name']} · {fmt(travel(p, dict(q)))}"), "w:x" if sh else f"w:t:{q['id']}")])
        if not rows: L.append("هنوز دشمنی نیست.")
        return "\n".join(L), kb(rows + [BACK])
    q = db.execute("SELECT * FROM players WHERE id=?", (d["t"],)).fetchone()
    T = TERRAIN[q["terrain"]]; lo, hi = estimate(power(json.loads(q["a"]), json.loads(q["b"])), p["b"]["tower"])
    head = f"🎯 <b>{esc(q['name'])}</b> · {T[0]} {T[1]}\n🌫 قدرت تخمینی: <b>{fa(lo)} تا {fa(hi)}</b>\n⏱ زمان سفر: {fmt(travel(p, dict(q)))}\n"
    if d["pct"] is None:
        return head + f"\nچند درصد نیرو بفرستی؟ (قدرت فعلی ارتش: {fa(power(p['a'], {}))})", kb([[(f"{x}٪", f"w:p:{x}") for x in (25, 50, 75, 100)], [("↩️ تغییر هدف", "w:c")]])
    if len(d["plan"]) < 3:
        i = len(d["plan"]); done = " ← ".join(ST[s][0] for s in d["plan"])
        return head + f"\n<b>مرحله {fa(i+1)}: {PHASE[i]}</b>\nفرمان مخفی بده" + (f"\nتا اینجا: {done}" if done else ""), kb(
            [[(f"{v[0]} {v[1]}", f"w:s:{k}") for k, v in ST.items()], [("↩️ لغو", "w:c")]])
    units = {u: int(p["a"][u] * d["pct"] / 100) for u in UN}
    army = "  ".join(f"{UN[u]['e']}{fa(n)}" for u, n in units.items() if n)
    return head + f"\n📋 نیرو: {army or '—'}\n🧭 نقشه: " + " ← ".join(f"{ST[s][0]}{ST[s][1]}" for s in d["plan"]) + "\n\n<b>تأیید می‌کنی؟</b>", kb(
        [[("✅ حمله!", "w:go")], [("🔁 فرمان‌ها از اول", "w:u"), ("↩️ لغو", "w:c")]])

def war_step(p, a):
    uid = p["id"]; d = drafts.get(uid); note = None; act = a[1] if len(a) > 1 else ""
    if act == "c": drafts.pop(uid, None)
    elif act == "x": note = "🛡 این قلمرو سپر داره"
    elif act == "t":
        tid = int(a[2])
        if db.execute("SELECT 1 FROM hits WHERE att=? AND dfn=? AND ts>?", (uid, tid, time.time() - 3600 / SPEED)).fetchone():
            note = "⏳ کولداون: تازه بهش حمله کردی"
        else: drafts[uid] = {"t": tid, "pct": None, "plan": []}
    elif act == "p" and d: d["pct"] = int(a[2])
    elif act == "s" and d and len(d["plan"]) < 3: d["plan"].append(a[2])
    elif act == "u" and d: d["plan"] = []
    elif act == "go" and d: note = launch(p, d)
    t, k = war_view(p); return t, k, note

def launch(p, d):
    units = {u: int(p["a"][u] * d["pct"] / 100) for u in UN}
    if sum(units.values()) < 1: return "❌ نیرویی برای ارسال نیست"
    q = load(d["t"])
    if not q: return "❌ هدف پیدا نشد"
    for u, n in units.items(): p["a"][u] -= n
    if p["shield"] > time.time(): p["shield"] = 0
    dur = travel(p, q); db.execute("INSERT INTO marches(att,dfn,units,plan,arrive,dur,status,loot) VALUES(?,?,?,?,?,?,0,'{}')",
                                  (p["id"], q["id"], json.dumps(units), json.dumps(d["plan"]), time.time() + dur, dur))
    db.execute("INSERT INTO hits VALUES(?,?,?)", (p["id"], q["id"], time.time())); db.commit(); save(p)
    asyncio.create_task(warn(q, p, units, dur)); drafts.pop(p["id"], None); return "⚔️ لشکر حرکت کرد!"

async def warn(q, att, units, dur):
    if q["b"]["tower"] < 1: return
    lo, hi = estimate(power(units, {}), q["b"]["tower"]); who = esc(att["name"]) if q["b"]["tower"] >= 3 else "ناشناس"
    await notify(BOT, q["id"], f"🚨 <b>هشدار دیده‌بان!</b>\nلشکر «{who}» با قدرت تقریبی {fa(lo)}–{fa(hi)} به سمت شماست.\n⏱ رسیدن: {fmt(dur)}\nفرمان دفاعی‌تون رو چک کنید!", f"هشدار: لشکر {who} در راه")

# ───────────────────────── نبرد ─────────────────────────
def resolve(att, dfn, units, plan):
    T = TERRAIN[dfn["terrain"]]; mor = lambda x: 0.75 + 0.35 * x["morale"] / 100
    A = sum(n * UN[u]["atk"] * T[3].get(u, 1) for u, n in units.items()) * mor(att)
    wb = max(0, 0.12 * dfn["b"]["wall"] - 0.004 * units.get("siege", 0))
    D = max(1, sum(n * UN[u]["df"] for u, n in dfn["a"].items()) * T[2] * (1 + wb + 0.04 * dfn["b"]["tower"]) * mor(dfn))
    dplan = DPLAN[dfn["dplan"]][2]; ar = dr = 1.0; wins = 0; lines = []
    for i in range(3):
        sa, sd = plan[i], dplan[i]
        pa = A / 3 * ar * ST[sa][2] * MX[sa][sd] * random.uniform(.9, 1.1)
        pd = D / 3 * dr * ST[sd][2] * MX[sd][sa] * random.uniform(.9, 1.1); rt = pa / max(pd, 1)
        if rt >= 1: la, ld = 0.07, 0.18 + 0.08 * min(rt - 1, 1.5); wins += 1
        else: la, ld = 0.18 + 0.08 * min(1 / rt - 1, 1.5), 0.07
        ar *= 1 - la * ST[sa][3]; dr *= 1 - ld * ST[sd][3]
        lines.append(f"{fa(i+1)}) {PHASE[i]}: {ST[sa][0]}{ST[sa][1]} ⟷ {ST[sd][0]}{ST[sd][1]} — " + ("✅ مهاجم پیش رفت" if rt >= 1 else "🛑 دفاع ایستاد"))
    win = wins >= 2; surv = {u: int(n * ar) for u, n in units.items()}
    dfn["a"] = {u: int(n * dr) for u, n in dfn["a"].items()}; loot = {}
    if win:
        avail = {k: dfn[k] * 0.30 for k, _, _ in RES}; carry = sum(surv[u] * UN[u]["carry"] for u in surv)
        sc = min(1, carry / max(sum(avail.values()), 1)); loot = {k: int(v * sc) for k, v in avail.items()}
        for k, v in loot.items(): dfn[k] -= v
    return win, surv, loot, lines, 1 - ar, 1 - dr

async def arrive(bot, m):
    units = json.loads(m["units"]); att = load(m["att"])
    if m["status"] == 1:
        for u, n in units.items(): att["a"][u] += n
        for k, v in json.loads(m["loot"]).items(): att[k] += v
        save(att); lt = json.loads(m["loot"])
        return await notify(bot, att["id"], f"🏳️ <b>لشکر برگشت</b>\nنیرو: " + " ".join(f"{UN[u]['e']}{fa(n)}" for u, n in units.items() if n) + (f"\nغنیمت: {costs(lt)}" if lt else ""), "لشکر به قلمرو بازگشت")
    dfn = load(m["dfn"])
    if not dfn: return
    win, surv, loot, lines, ca, cd = resolve(att, dfn, units, json.loads(m["plan"]))
    now = time.time(); ransom = 0
    if win:
        att["wins"] += 1; att["morale"] = min(100, att["morale"] + 8); dfn["morale"] = max(10, dfn["morale"] - 12); dfn["losses"] += 1
        att["loot"] += sum(loot.values()); dfn["shield"] = max(dfn["shield"], now + 3 * 3600 / SPEED)
    else:
        att["losses"] += 1; att["morale"] = max(10, att["morale"] - 15); dfn["wins"] += 1; dfn["morale"] = min(100, dfn["morale"] + 8)
        if ca > 0.6 and random.random() < 0.35: ransom = int(att["gold"] * 0.1); att["gold"] -= ransom
    save(att); save(dfn)
    body = "\n".join(lines) + f"\n━━━━━━━━\nتلفات مهاجم: {fa(ca*100)}٪ · تلفات مدافع: {fa(cd*100)}٪"
    if loot: body += f"\n💰 غنیمت: {costs(loot)}"
    if ransom: body += f"\n⛓ فرمانده اسیر شد! باج: {fa(ransom)} 🪙"
    await notify(bot, att["id"], f"⚔️ <b>گزارش نبرد: {'🏆 پیروزی' if win else '💀 شکست'}</b>\nهدف: {esc(dfn['name'])} ({TERRAIN[dfn['terrain']][1]})\n{body}", f"حمله به {dfn['name']}: {'پیروزی' if win else 'شکست'}")
    await notify(bot, dfn["id"], f"🏰 <b>قلمروت مورد حمله قرار گرفت: {'💀 شکست' if win else '🏆 دفاع موفق'}</b>\nمهاجم: {esc(att['name'])}\n{body}", f"حمله‌ی {att['name']}: {'شکست خوردید' if win else 'دفاع موفق'}")
    if sum(surv.values()):
        db.execute("INSERT INTO marches(att,dfn,units,plan,arrive,dur,status,loot) VALUES(?,?,?,?,?,?,1,?)",
                   (att["id"], dfn["id"], json.dumps(surv), "[]", now + m["dur"], m["dur"], json.dumps(loot))); db.commit()

# ───────────────────────── رویدادهای تصادفی ─────────────────────────
async def event_loop(bot):
    while True:
        await asyncio.sleep(600 / SPEED)
        for row in db.execute("SELECT id FROM players").fetchall():
            if random.random() > 0.06: continue
            p = load(row["id"])
            if p["shield"] > time.time(): continue
            ev = random.choice(["bandit", "famine", "harvest", "plague"])
            if ev == "bandit":
                g = min(0.6, 0.1 * p["b"]["wall"] + 0.08 * p["b"]["tower"]); f = 0.12 * (1 - g); p["gold"] *= 1 - f; p["iron"] *= 1 - f
                msg = f"🗡 <b>حمله‌ی راهزنان!</b> {fa(f*100)}٪ طلا و آهن غارت شد. (دیوار و برج جلوشون رو می‌گیره)"
            elif ev == "famine": p["food"] *= 0.85; msg = "🥀 <b>قحطی!</b> ۱۵٪ غذا فاسد شد."
            elif ev == "harvest": p["food"] += 300 * p["b"]["farm"]; msg = "🌾 <b>برداشت پربار!</b> غذای اضافه رسید."
            else: p["a"]["inf"] = int(p["a"]["inf"] * 0.94); p["a"]["arc"] = int(p["a"]["arc"] * 0.94); msg = "☠️ <b>طاعون!</b> ۶٪ از پیاده و کماندار مردن."
            save(p); await notify(bot, p["id"], msg, msg.split("!")[0].replace("<b>", "").replace("</b>", ""))

async def march_loop(bot):
    while True:
        await asyncio.sleep(3)
        for m in db.execute("SELECT * FROM marches WHERE arrive<=?", (time.time(),)).fetchall():
            db.execute("DELETE FROM marches WHERE id=?", (m["id"],)); db.commit()
            try: await arrive(bot, m)
            except Exception: logging.exception("march")

# ───────────────────────── هندلرها ─────────────────────────
MENU_WORDS = {"منو", "/menu", "menu", "منو."}

@r.message(CommandStart())
@r.message(F.text.func(lambda t: t.strip().lower() in MENU_WORDS))
async def open_menu(m: Message):
    p = load(m.from_user.id)
    if not p:
        p = create(m.from_user)
        T = TERRAIN[p["terrain"]]
        return await m.answer(f"⚜️ <b>KINGDOM WARS</b> ⚜️\n\nدود از افق بلنده، {esc(p['name'])}.\nپادشاه قبلی مرده و تاج بی‌صاحب مونده. قلمروی کوچیک در {T[0]} <b>{T[1]}</b> سهم توئه، با ۴۸ ساعت سپر.\n\nقبل از اولین نبرد، آموزش کوتاه رو بخون؛ جایزه هم داره.",
                              reply_markup=kb([[("📖 شروع آموزش", "tut:0")], [("🏰 مستقیم برو منو", "m:home")]]))
    await m.answer(home_text(p), reply_markup=home_kb(p))

async def show(c, text, k):
    try: await c.message.edit_text(text, reply_markup=k)
    except TelegramBadRequest: pass

@r.callback_query()
async def cb(c: CallbackQuery):
    p = load(c.from_user.id)
    if not p: return await c.answer("اول /start بزنید", show_alert=True)
    a = c.data.split(":"); k = a[0]; note = None; scr = None
    if k == "lock": return await c.answer("🔒 این بخش در آپدیت بعدی میاد: " + ("اتحاد، پیمان، خیانت و جنگ پیمان‌ها" if a[1] == "ally" else "جاسوسی، خرابکاری و دیپلماسی"), show_alert=True)
    if k == "w": text, kbd, note = war_step(p, a); await show(c, text, kbd); return await c.answer(note or "")
    if k == "m": scr = a[1]
    elif k == "b": note = upgrade(p, a[1]); scr = "kingdom"
    elif k == "t": note = train(p, a[1], int(a[2])); scr = "army"
    elif k == "dp": p["dplan"] = a[1]; save(p); note = "✅ فرمان دفاعی ثبت شد"; scr = "army"
    elif k == "daily": note = daily(p); scr = "home"
    elif k == "tut": text, kbd = tut_screen(p, int(a[1])); await show(c, text, kbd); return await c.answer()
    elif k == "tutdone":
        if p["tut"]: note = "قبلاً گرفتی"
        else:
            p["tut"] = 1; p["gold"] += 300; p["food"] += 200; p["iron"] += 100; p["wood"] += 150; save(p); note = "🎁 جایزه: ۳۰۰🪙 ۲۰۰🌾 ۱۰۰⛓ ۱۵۰🪵"
        text, kbd = tut_screen(p, len(TUT) - 1); await show(c, text, kbd); return await c.answer(note, show_alert=True)
    p = load(p["id"])
    if scr == "home": text, kbd = home_text(p), home_kb(p)
    elif scr == "war": drafts.pop(p["id"], None); text, kbd = war_view(p)
    else: text, kbd = {"kingdom": kingdom_screen, "army": army_screen, "econ": econ_screen, "map": map_screen,
                       "rank": rank_screen, "reports": reports_screen}[scr](p)
    await show(c, text, kbd); await c.answer(note or "")

BOT = None
async def main():
    global BOT
    logging.basicConfig(level=logging.INFO)
    BOT = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(); dp.include_router(r)
    asyncio.create_task(march_loop(BOT)); asyncio.create_task(event_loop(BOT))
    await dp.start_polling(BOT)

if __name__ == "__main__":
    asyncio.run(main())
