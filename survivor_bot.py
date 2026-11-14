"""
🏕 بازمانده — Survivor RP (Telegram bot)
pip install aiogram
export BOT_TOKEN="123:ABC..."   # توکن از @BotFather
python survivor_bot.py
"""
import asyncio, html, logging, os, random, sqlite3, time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "survivor.db")

# ───────────────────────── دیتابیس ─────────────────────────
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS players(
  uid INTEGER PRIMARY KEY, name TEXT, alive INTEGER DEFAULT 1,
  hp REAL DEFAULT 100, hunger REAL DEFAULT 80, thirst REAL DEFAULT 80, energy REAL DEFAULT 100,
  level INTEGER DEFAULT 1, xp INTEGER DEFAULT 0, coins INTEGER DEFAULT 0,
  kills INTEGER DEFAULT 0, deaths INTEGER DEFAULT 0, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
  shelter INTEGER DEFAULT 0, born REAL, best REAL DEFAULT 0, tick REAL, shield REAL DEFAULT 0,
  exp_zone TEXT, exp_end REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS inv(uid INTEGER, item TEXT, qty INTEGER, PRIMARY KEY(uid,item));
""")


def q(sql, args=(), one=False):
    cur = db.execute(sql, args)
    db.commit()
    return cur.fetchone() if one else cur.fetchall()


def get(uid):
    return q("SELECT * FROM players WHERE uid=?", (uid,), True)


# ───────────────────────── داده‌های بازی ─────────────────────────
ITEMS = {
    "wood": "🪵 چوب", "stone": "🪨 سنگ", "fiber": "🧵 الیاف", "hide": "🦌 چرم", "iron": "⛓ آهن",
    "berries": "🫐 توت", "raw_meat": "🥩 گوشت خام", "cooked_meat": "🍗 گوشت پخته",
    "water": "💧 آب", "bandage": "🩹 باند",
    "spear": "🔱 نیزه", "iron_sword": "🗡 شمشیر آهنی",
    "hide_armor": "🦺 زره چرمی", "iron_armor": "🛡 زره آهنی",
}
EQ_ATK = {"spear": 8, "iron_sword": 18}
EQ_DEF = {"hide_armor": 8, "iron_armor": 16}
EQUIP = set(EQ_ATK) | set(EQ_DEF)
USE = {
    "berries": {"hunger": 12}, "raw_meat": {"hunger": 18, "sick": 0.3},
    "cooked_meat": {"hunger": 40, "hp": 5}, "water": {"thirst": 30}, "bandage": {"hp": 25},
}
RECIPES = {
    "spear": {"wood": 3, "stone": 2}, "iron_sword": {"iron": 3, "wood": 2},
    "hide_armor": {"hide": 3, "fiber": 2}, "iron_armor": {"iron": 4, "hide": 2},
    "bandage": {"fiber": 2}, "cooked_meat": {"raw_meat": 1, "wood": 1},
}
RECIPE_NOTE = {
    "spear": "حمله +8", "iron_sword": "حمله +18", "hide_armor": "دفاع +8",
    "iron_armor": "دفاع +16", "bandage": "+25 سلامتی", "cooked_meat": "+40 گرسنگی",
}
BUY = {"water": 12, "berries": 10, "bandage": 30, "cooked_meat": 25}
SELL = {"wood": 2, "stone": 2, "fiber": 2, "hide": 5, "iron": 8}

ZONES = {
    "forest": dict(name="🌲 جنگل", dur=90, en=10, danger=0.25, dmg=(6, 16), xp=10,
                   events=["گرگ گرسنه", "مار سمی"],
                   loot={"wood": (2, 5, 1.0), "fiber": (1, 3, .7), "berries": (1, 3, .6),
                         "raw_meat": (1, 2, .35), "hide": (1, 2, .25)}),
    "river": dict(name="🌊 رودخانه", dur=120, en=12, danger=0.25, dmg=(5, 14), xp=12,
                  events=["تمساح", "لغزیدن روی صخره‌های لیز"],
                  loot={"water": (2, 4, 1.0), "fiber": (1, 2, .5), "stone": (1, 3, .5),
                        "raw_meat": (1, 2, .4)}),
    "mountain": dict(name="⛰ کوهستان", dur=240, en=20, danger=0.4, dmg=(10, 24), xp=25,
                     events=["ریزش سنگ", "خرس قهوه‌ای"],
                     loot={"stone": (3, 6, 1.0), "iron": (1, 3, .5), "hide": (1, 2, .3),
                           "wood": (1, 2, .4)}),
    "ruins": dict(name="🏚 شهر متروکه", dur=420, en=30, danger=0.6, dmg=(15, 35), xp=45,
                  events=["غارتگر مسلح", "دام مخفی"],
                  loot={"iron": (2, 4, .7), "bandage": (1, 2, .5), "water": (1, 2, .4),
                        "cooked_meat": (1, 1, .2), "coins": (20, 60, 1.0)}),
}


def maxhp(p): return 100 + 10 * (p["level"] - 1)
def cap(p): return 40 + 10 * p["shelter"]
def inv(uid): return {r["item"]: r["qty"] for r in q("SELECT item,qty FROM inv WHERE uid=? AND qty>0", (uid,))}
def atk(p, bag): return 15 + 3 * p["level"] + max([EQ_ATK[k] for k in bag if k in EQ_ATK], default=0)
def dfn(p, bag): return max([EQ_DEF[k] for k in bag if k in EQ_DEF], default=0)


def add(uid, item, n):
    p = get(uid)
    if item not in EQUIP:
        room = cap(p) - sum(v for k, v in inv(uid).items() if k not in EQUIP)
        n = min(n, room)
    if n <= 0:
        return 0
    q("INSERT INTO inv VALUES(?,?,?) ON CONFLICT(uid,item) DO UPDATE SET qty=qty+?", (uid, item, n, n))
    return n


def take(uid, item, n):
    q("UPDATE inv SET qty=qty-? WHERE uid=? AND item=?", (n, uid, item))
    q("DELETE FROM inv WHERE qty<=0")


def fmt(sec):
    sec = max(0, int(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h: return f"{h} ساعت و {m} دقیقه"
    if m: return f"{m} دقیقه و {s} ثانیه"
    return f"{s} ثانیه"


def bar(v, mx=100, n=8):
    f = max(0, min(n, round(v / mx * n)))
    return "▰" * f + "▱" * (n - f)


def esc(s): return html.escape(s or "؟")


# ───────────────────────── منطق اصلی ─────────────────────────
def create(uid, name):
    now = time.time()
    q("INSERT INTO players(uid,name,born,tick,shield) VALUES(?,?,?,?,?)", (uid, name, now, now, now + 3600))
    add(uid, "water", 2); add(uid, "berries", 3)


def new_life(uid):
    now = time.time()
    q("""UPDATE players SET alive=1,hp=100,hunger=80,thirst=80,energy=100,level=1,xp=0,coins=0,
         shelter=0,born=?,tick=?,shield=?,exp_zone=NULL,exp_end=0 WHERE uid=?""", (now, now, now + 3600, uid))
    q("DELETE FROM inv WHERE uid=?", (uid,))
    add(uid, "water", 2); add(uid, "berries", 3)


def die(uid):
    p = get(uid)
    surv = time.time() - p["born"]
    q("UPDATE players SET alive=0,hp=0,best=?,deaths=deaths+1,exp_zone=NULL,exp_end=0 WHERE uid=?",
      (max(p["best"], surv), uid))
    q("DELETE FROM inv WHERE uid=?", (uid,))


def sync(uid):
    """محاسبه‌ی نیازها بر اساس زمان واقعی (بدون نیاز به تایمر پس‌زمینه)."""
    p = get(uid)
    if not p or not p["alive"]:
        return p
    now = time.time()
    el = (now - p["tick"]) / 60
    if el < 0.05:
        return p
    red = 1 - 0.1 * p["shelter"]
    hr, tr = 0.35 * red, 0.5 * red
    hunger = max(0, p["hunger"] - el * hr)
    thirst = max(0, p["thirst"] - el * tr)
    starve = max(0, el - p["hunger"] / hr) + max(0, el - p["thirst"] / tr)
    hp = p["hp"]
    if starve > 0:
        hp -= starve * 0.8
    elif hunger > 30 and thirst > 30:
        hp = min(maxhp(p), hp + el * 0.25)
    energy = min(100, p["energy"] + el)
    dead = hp <= 0 and el <= 30          # اگه خیلی آفلاین بودی نمی‌میری، فقط ضعیف می‌شی
    q("UPDATE players SET hp=?,hunger=?,thirst=?,energy=?,tick=? WHERE uid=?",
      (max(1, hp) if not dead else 0, hunger, thirst, energy, now, uid))
    if dead:
        die(uid)
    return get(uid)


def give_xp(uid, n):
    p = get(uid)
    xp, lvl, up = p["xp"] + n, p["level"], 0
    while xp >= 50 * lvl:
        xp -= 50 * lvl; lvl += 1; up += 1
    q("UPDATE players SET xp=?,level=? WHERE uid=?", (xp, lvl, uid))
    if up:
        q("UPDATE players SET hp=? WHERE uid=?", (maxhp(get(uid)), uid))
    return up


def use_item(uid, item):
    p = sync(uid)
    if inv(uid).get(item, 0) < 1:
        return "این رو نداری!"
    e = USE[item]
    take(uid, item, 1)
    hp = min(maxhp(p), p["hp"] + e.get("hp", 0))
    msg = f"{ITEMS[item]} استفاده شد ✅"
    if e.get("sick") and random.random() < e["sick"]:
        hp = max(1, hp - 10); msg += " 🤢 مسموم شدی (-10)"
    q("UPDATE players SET hp=?,hunger=?,thirst=? WHERE uid=?",
      (hp, min(100, p["hunger"] + e.get("hunger", 0)), min(100, p["thirst"] + e.get("thirst", 0)), uid))
    return msg


def craft(uid, key):
    bag = inv(uid)
    if key in EQUIP and bag.get(key):
        return "قبلاً داری!"
    need = RECIPES[key]
    if any(bag.get(k, 0) < v for k, v in need.items()):
        return "مواد کافی نداری ❌"
    for k, v in need.items():
        take(uid, k, v)
    add(uid, key, 1)
    return f"{ITEMS[key]} ساخته شد 🔨"


def resolve_exp(uid):
    p = sync(uid)
    z = ZONES[p["exp_zone"]]
    bag = inv(uid)
    hp, lines, got = p["hp"], [], {}
    for item, (lo, hi, ch) in z["loot"].items():
        if random.random() < ch:
            n = random.randint(lo, hi)
            if item == "raw_meat" and any(k in bag for k in EQ_ATK):
                n += 1
            got[item] = n
    if random.random() < z["danger"]:
        ev = random.choice(z["events"])
        dmg = max(2, random.randint(*z["dmg"]) - dfn(p, bag) // 2)
        if any(k in bag for k in EQ_ATK) and random.random() < 0.5:
            dmg //= 3
            got["hide"] = got.get("hide", 0) + 1
            lines.append(f"⚠️ با {ev} درگیر شدی و با سلاحت شکستش دادی! (-{dmg}❤️)")
        else:
            lines.append(f"⚠️ {ev}! (-{dmg}❤️)")
        hp -= dmg
    q("UPDATE players SET exp_zone=NULL,exp_end=0 WHERE uid=?", (uid,))
    if hp <= 0:
        q("UPDATE players SET hp=0 WHERE uid=?", (uid,))
        die(uid)
        return "\n".join(lines) + "\n\n💀 <b>تو در اکتشاف جونت رو از دست دادی...</b>"
    q("UPDATE players SET hp=? WHERE uid=?", (hp, uid))
    coins = got.pop("coins", 0)
    if coins:
        q("UPDATE players SET coins=coins+? WHERE uid=?", (coins, uid))
    full = False
    for it, n in got.items():
        a = add(uid, it, n)
        lines.append(f"+{a} {ITEMS[it]}")
        full |= a < n
    if coins: lines.append(f"+{coins} 🪙")
    if full: lines.append("🎒 کوله پر شد، بعضی چیزها جا موند!")
    up = give_xp(uid, z["xp"])
    lines.append(f"✨ +{z['xp']} XP" + ("  🎉 <b>سطح بالا رفت!</b>" if up else ""))
    return f"<b>{z['name']} — اکتشاف تموم شد</b>\n━━━━━━━━━━━━━━\n" + "\n".join(lines)


# ───────────────────────── PvP ─────────────────────────
def duel(a, b):
    ba, bb = inv(a["uid"]), inv(b["uid"])
    aa, ad, ab, bd = atk(a, ba), dfn(a, ba), atk(b, bb), dfn(b, bb)
    ha, hb, ma, mb = a["hp"], b["hp"], maxhp(a), maxhp(b)
    for _ in range(10):
        hb -= max(2, aa * random.uniform(.5, 1) - bd * .4)
        if hb <= 0: break
        ha -= max(2, ab * random.uniform(.5, 1) - ad * .4)
        if ha <= 0: break
    win = "a" if hb <= 0 else "b" if ha <= 0 else ("a" if ha / ma >= hb / mb else "b")
    return win, max(0, ha), max(0, hb)


def loot_from(loser, winner):
    frac = max(0.1, 0.35 - 0.05 * loser["shelter"])
    cfrac = max(0.05, 0.25 - 0.04 * loser["shelter"])
    coins = int(loser["coins"] * cfrac)
    q("UPDATE players SET coins=coins-? WHERE uid=?", (coins, loser["uid"]))
    q("UPDATE players SET coins=coins+? WHERE uid=?", (coins, winner["uid"]))
    got = {}
    for it, n in inv(loser["uid"]).items():
        k = int(n * frac)
        if it not in EQUIP and k > 0:
            a = add(winner["uid"], it, k)
            if a:
                take(loser["uid"], it, a); got[it] = a
    return coins, got


def run_attack(uid, tid):
    """برمی‌گردونه: (پیام برای مهاجم, پیام برای مدافع)"""
    att, dfd = sync(uid), sync(tid)
    if not dfd or not dfd["alive"]: return "این بازیکن دیگه زنده نیست.", None
    if dfd["exp_zone"] or dfd["shield"] > time.time(): return "این بازیکن الان قابل حمله نیست 🛡", None
    if att["energy"] < 20: return "انرژی کافی نداری (⚡20 لازمه).", None
    if att["hp"] < 20: return "با این زخم‌ها نمی‌تونی بجنگی! اول خودت رو درمان کن 🩹", None
    q("UPDATE players SET energy=energy-20,shield=0 WHERE uid=?", (uid,))
    win, ha, hb = duel(att, dfd)
    winner, loser = (att, dfd) if win == "a" else (dfd, att)
    hl = hb if win == "a" else ha
    hw = ha if win == "a" else hb
    q("UPDATE players SET hp=? WHERE uid=?", (max(1, hw), winner["uid"]))
    coins, got = loot_from(loser, winner)
    killed = hl <= 0
    xp = 40 + 6 * loser["level"]
    q("UPDATE players SET wins=wins+1,kills=kills+? WHERE uid=?", (1 if killed else 0, winner["uid"]))
    give_xp(winner["uid"], xp)
    q("UPDATE players SET losses=losses+1 WHERE uid=?", (loser["uid"],))
    if killed:
        die(loser["uid"])
    else:
        q("UPDATE players SET hp=?,shield=? WHERE uid=?", (max(1, hl), time.time() + 1200, loser["uid"]))
    loot_txt = ", ".join([f"{n} {ITEMS[i]}" for i, n in got.items()] + ([f"{coins} 🪙"] if coins else [])) or "چیزی نبود"
    a_name, d_name = esc(att["name"]), esc(dfd["name"])
    if winner["uid"] == uid:
        m1 = (f"⚔️ <b>پیروز شدی!</b>\nدر برابر {d_name} (سطح {dfd['level']})\n"
              f"{'💀 حریف کشته شد!' if killed else '🏃 حریف فرار کرد.'}\n🎁 غنیمت: {loot_txt}\n✨ +{xp} XP")
        m2 = f"🚨 <b>{a_name}</b> بهت حمله کرد و برنده شد!\n" + ("💀 تو کشته شدی..." if killed else f"🩸 غارت شدی: {loot_txt}\n🛡 ۲۰ دقیقه محافظت داری.")
    else:
        m1 = (f"💥 <b>شکست خوردی!</b>\n{d_name} قوی‌تر بود.\n" + ("💀 تو کشته شدی..." if killed else f"🩸 غارت شدی: {loot_txt}\n🛡 ۲۰ دقیقه محافظت داری."))
        m2 = f"🛡 <b>{a_name}</b> بهت حمله کرد ولی دفاع کردی و شکستش دادی!\n🎁 غنیمت: {loot_txt}\n✨ +{xp} XP"
    return m1, m2


# ───────────────────────── صفحه‌ها ─────────────────────────
def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in r] for r in rows])


BACK = [("🏠 منوی اصلی", "m:main")]
LINE = "━━━━━━━━━━━━━━"


def status_text(p):
    bag = inv(p["uid"])
    mh = maxhp(p)
    t = (f"<b>🧍 {esc(p['name'])}</b>  •  سطح {p['level']}  ({p['xp']}/{50 * p['level']} XP)\n{LINE}\n"
         f"❤️ {bar(p['hp'], mh)} {int(p['hp'])}/{mh}\n"
         f"🍖 {bar(p['hunger'])} {int(p['hunger'])}\n"
         f"💧 {bar(p['thirst'])} {int(p['thirst'])}\n"
         f"⚡ {bar(p['energy'])} {int(p['energy'])}\n{LINE}\n"
         f"⚔️ حمله {atk(p, bag)}   🛡 دفاع {dfn(p, bag)}   🪙 {p['coins']}\n"
         f"⏳ زنده‌مانده: {fmt(time.time() - p['born'])}")
    if p["shield"] > time.time():
        t += f"\n🛡 محافظت در برابر حمله: {fmt(p['shield'] - time.time())}"
    if p["exp_zone"]:
        left = p["exp_end"] - time.time()
        t += f"\n🧭 در {ZONES[p['exp_zone']]['name']} — " + (f"{fmt(left)} مونده" if left > 0 else "برگشتی! نتیجه آماده‌ست")
    return t


def screen_dead(p):
    return (f"💀 <b>مُردی...</b>\n{LINE}\n⏳ این زندگی: {fmt(time.time() - p['born'])}\n"
            f"🏅 بهترین رکورد: {fmt(p['best'])}\n⚔️ کشته‌ها: {p['kills']}   ☠️ مرگ‌ها: {p['deaths']}\n\n"
            "همه‌چیزت از بین رفت. آماده‌ای دوباره شروع کنی؟"), kb([("🔁 زندگی جدید", "m:new")])


def screen_main(uid):
    p = sync(uid)
    if not p["alive"]:
        return screen_dead(p)
    top = [("🧭 نتیجه‌ی اکتشاف", "x:collect")] if p["exp_zone"] else [("🗺 اکتشاف", "m:explore")]
    return status_text(p), kb(
        top + [("🎒 کوله‌پشتی", "m:bag")],
        [("🔨 ساخت", "m:craft"), ("🏕 پایگاه", "m:base")],
        [("⚔️ نبرد با بازیکنان", "m:pvp")],
        [("🏪 بازار", "m:shop"), ("🏆 رتبه‌بندی", "m:top")],
        [("🔄 بروزرسانی", "m:main")])


def screen_bag(uid):
    p = sync(uid)
    bag = inv(uid)
    used = sum(v for k, v in bag.items() if k not in EQUIP)
    body = "\n".join(f"{ITEMS[k]} × {v}" for k, v in bag.items()) or "خالیه!"
    rows = [[(f"{ITEMS[k]} ({v})", f"u:{k}")] for k, v in bag.items() if k in USE]
    return (f"<b>🎒 کوله‌پشتی</b>  ({used}/{cap(p)})\n{LINE}\n{body}\n{LINE}\nروی غذا/دارو بزن تا استفاده بشه 👇"), kb(*rows, BACK)


def screen_explore(uid):
    p = sync(uid)
    if p["exp_zone"]:
        return screen_main(uid)
    t = f"<b>🗺 اکتشاف</b>\n{LINE}\n"
    rows = []
    for k, z in ZONES.items():
        risk = "🟢" if z["danger"] < .3 else "🟡" if z["danger"] < .5 else "🔴"
        t += f"{z['name']} — {z['dur'] // 60 if z['dur'] >= 120 else z['dur']}{'د' if z['dur'] >= 120 else 'ث'} • ⚡{z['en']} • خطر {risk}\n"
        rows.append([(f"{z['name']}  ⚡{z['en']}", f"x:{k}")])
    return t + "\nهرچی دورتر، غنیمت بهتر و خطر بیشتر!", kb(*rows, BACK)


def screen_craft(uid):
    bag = inv(uid)
    t = f"<b>🔨 ساخت</b>\n{LINE}\n"
    rows = []
    for k, need in RECIPES.items():
        ok = all(bag.get(i, 0) >= n for i, n in need.items())
        cost = " + ".join(f"{n}{ITEMS[i].split()[0]}" for i, n in need.items())
        t += f"{ITEMS[k]} ← {cost}  <i>({RECIPE_NOTE[k]})</i>\n"
        rows.append([(("✅ " if ok else "🔒 ") + ITEMS[k], f"c:{k}")])
    return t, kb(*rows, BACK)


def base_cost(lvl): return {"wood": 8 * (lvl + 1), "stone": 5 * (lvl + 1)}


def screen_base(uid):
    p = sync(uid)
    lvl = p["shelter"]
    t = (f"<b>🏕 پایگاه — سطح {lvl}/5</b>\n{LINE}\n"
         f"• کاهش مصرف غذا و آب: {lvl * 10}%\n• ظرفیت کوله: {cap(p)}\n"
         f"• کاهش غارت در PvP: {lvl * 5}%\n")
    if lvl >= 5:
        return t + "\n✅ پایگاهت کاملاً ارتقا یافته!", kb(BACK)
    c = base_cost(lvl)
    return t + f"\nارتقا: {c['wood']} {ITEMS['wood']} + {c['stone']} {ITEMS['stone']}", kb([("⬆️ ارتقای پایگاه", "b:up")], BACK)


def screen_shop(uid):
    p = sync(uid)
    t = f"<b>🏪 بازار</b>   🪙 {p['coins']}\n{LINE}\nخرید:\n" + "\n".join(f"{ITEMS[k]} — {v}🪙" for k, v in BUY.items())
    t += "\n\nفروش (همه‌ی موجودی): " + "، ".join(f"{ITEMS[k]} {v}🪙" for k, v in SELL.items())
    return t, kb([(f"🛒 {ITEMS[k]}", f"s:{k}") for k in list(BUY)[:2]],
                 [(f"🛒 {ITEMS[k]}", f"s:{k}") for k in list(BUY)[2:]],
                 [(f"💰 {ITEMS[k]}", f"sl:{k}") for k in list(SELL)[:3]],
                 [(f"💰 {ITEMS[k]}", f"sl:{k}") for k in list(SELL)[3:]], BACK)


def screen_pvp(uid):
    p = sync(uid)
    rows_db = q("""SELECT * FROM players WHERE alive=1 AND uid!=? AND shield<? AND exp_zone IS NULL
                   AND ABS(level-?)<=5 ORDER BY RANDOM() LIMIT 6""", (uid, time.time(), p["level"]))
    t = (f"<b>⚔️ نبرد</b>   ⚡{int(p['energy'])}\n{LINE}\nهر حمله ⚡20 انرژی می‌خواد.\n"
         "برنده غنیمت می‌گیره، بازنده غارت می‌شه (و ممکنه بمیره!).\n")
    if not rows_db:
        return t + "\nالان کسی قابل حمله نیست 😴", kb([("🔄 جستجوی دوباره", "m:pvp")], BACK)
    rows = [[(f"🎯 {r['name'][:14]} • سطح {r['level']} • ❤️{int(r['hp'])}", f"p:{r['uid']}")] for r in rows_db]
    return t + "\nیه هدف انتخاب کن:", kb(*rows, [("🔄 لیست جدید", "m:pvp")], BACK)


def screen_top(kind="surv"):
    rows = q("SELECT * FROM players")
    now = time.time()
    if kind == "surv":
        rows.sort(key=lambda r: max(r["best"], (now - r["born"]) if r["alive"] else 0), reverse=True)
        lines = [f"{esc(r['name'])} — {fmt(max(r['best'], (now - r['born']) if r['alive'] else 0))}" for r in rows[:10]]
        title = "⏳ طولانی‌ترین بقا"
    else:
        rows.sort(key=lambda r: (r["kills"], r["wins"]), reverse=True)
        lines = [f"{esc(r['name'])} — ☠️ {r['kills']} کشته • 🏆 {r['wins']} برد" for r in rows[:10]]
        title = "⚔️ برترین جنگجوها"
    medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
    body = "\n".join(f"{medals[i]} {l}" for i, l in enumerate(lines)) or "هنوز کسی نیست"
    return f"<b>🏆 {title}</b>\n{LINE}\n{body}", kb([("⏳ بقا", "t:surv"), ("⚔️ جنگجو", "t:kills")], BACK)


# ───────────────────────── ربات ─────────────────────────
dp = Dispatcher()
INTRO = ("🏕 <b>به دنیای «بازمانده» خوش اومدی!</b>\n\n"
         "تو تو یه جزیره‌ی وحشی گیر افتادی. گرسنگی و تشنگی با زمان واقعی بالا می‌ره، "
         "باید اکتشاف کنی، وسیله بسازی، پایگاه بسازی و از بقیه‌ی بازمانده‌ها جون سالم به در ببری.\n"
         "ساعت اول، محافظت از حمله داری. موفق باشی! 🔥")


async def show(cb: CallbackQuery, text, markup, toast=None):
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            raise
    await cb.answer(toast, show_alert=False)


@dp.message(Command("start", "menu"))
async def start(m: Message):
    uid = m.from_user.id
    if not get(uid):
        create(uid, m.from_user.first_name)
        await m.answer(INTRO)
    t, k = screen_main(uid)
    await m.answer(t, reply_markup=k)


@dp.callback_query()
async def on_cb(cb: CallbackQuery):
    uid, d, toast = cb.from_user.id, cb.data, None
    if not get(uid):
        create(uid, cb.from_user.first_name)
    p = sync(uid)
    if not p["alive"] and d != "m:new":
        d = "m:main"

    if d == "m:new": new_life(uid); screen = screen_main(uid)
    elif d == "m:main": screen = screen_main(uid)
    elif d == "m:bag": screen = screen_bag(uid)
    elif d.startswith("u:"): toast = use_item(uid, d[2:]); screen = screen_bag(uid)
    elif d == "m:explore": screen = screen_explore(uid)
    elif d == "x:collect":
        p = get(uid)
        if not p["exp_zone"]:
            screen = screen_main(uid)
        elif p["exp_end"] > time.time():
            toast = f"هنوز {fmt(p['exp_end'] - time.time())} مونده ⏳"; screen = screen_main(uid)
        else:
            screen = (resolve_exp(uid), kb(BACK))
    elif d.startswith("x:"):
        z = ZONES[d[2:]]; p = get(uid)
        if p["exp_zone"]: toast = "الان تو اکتشافی!"
        elif p["energy"] < z["en"]: toast = f"انرژی کافی نیست (⚡{z['en']})"
        elif p["hp"] < 15: toast = "خیلی زخمی هستی! اول درمان کن 🩹"
        else:
            q("UPDATE players SET energy=energy-?,exp_zone=?,exp_end=? WHERE uid=?", (z["en"], d[2:], time.time() + z["dur"], uid))
            toast = f"رفتی {z['name']} 🧭"
        screen = screen_main(uid)
    elif d == "m:craft": screen = screen_craft(uid)
    elif d.startswith("c:"): toast = craft(uid, d[2:]); screen = screen_craft(uid)
    elif d == "m:base": screen = screen_base(uid)
    elif d == "b:up":
        p = get(uid); c = base_cost(p["shelter"]); bag = inv(uid)
        if p["shelter"] >= 5: toast = "به آخرین سطح رسیدی!"
        elif any(bag.get(k, 0) < v for k, v in c.items()): toast = "مواد کافی نداری ❌"
        else:
            for k, v in c.items(): take(uid, k, v)
            q("UPDATE players SET shelter=shelter+1 WHERE uid=?", (uid,)); toast = "پایگاه ارتقا یافت 🏕⬆️"
        screen = screen_base(uid)
    elif d == "m:shop": screen = screen_shop(uid)
    elif d.startswith("sl:"):
        it = d[3:]; n = inv(uid).get(it, 0)
        if n: take(uid, it, n); q("UPDATE players SET coins=coins+? WHERE uid=?", (n * SELL[it], uid)); toast = f"فروختی: +{n * SELL[it]} 🪙"
        else: toast = "نداری!"
        screen = screen_shop(uid)
    elif d.startswith("s:"):
        it = d[2:]; p = get(uid)
        if p["coins"] < BUY[it]: toast = "سکه کافی نیست 🪙"
        elif add(uid, it, 1) == 0: toast = "کوله پره 🎒"
        else: q("UPDATE players SET coins=coins-? WHERE uid=?", (BUY[it], uid)); toast = f"{ITEMS[it]} خریدی ✅"
        screen = screen_shop(uid)
    elif d == "m:pvp": screen = screen_pvp(uid)
    elif d.startswith("p:"):
        t = sync(int(d[2:]))
        if not t or not t["alive"]: toast = "این بازیکن نیست"; screen = screen_pvp(uid)
        else:
            screen = (f"🎯 <b>{esc(t['name'])}</b>  •  سطح {t['level']}\n❤️ {bar(t['hp'], maxhp(t))} {int(t['hp'])}\n"
                      f"☠️ کشته‌ها: {t['kills']}   🏆 برد: {t['wins']}\n\nمطمئنی حمله کنی؟ ⚡20",
                      kb([("💥 حمله!", f"pa:{t['uid']}")], [("⬅️ برگشت", "m:pvp")]))
    elif d.startswith("pa:"):
        tid = int(d[3:]); m1, m2 = run_attack(uid, tid)
        if m2:
            try: await cb.bot.send_message(tid, m2, reply_markup=kb([("🏠 منوی اصلی", "m:main")]))
            except Exception: pass
        screen = (m1, kb(BACK))
    elif d == "m:top": screen = screen_top()
    elif d.startswith("t:"): screen = screen_top(d[2:])
    else: screen = screen_main(uid)

    await show(cb, screen[0], screen[1], toast)


async def main():
    logging.basicConfig(level=logging.INFO)
    if not TOKEN:
        raise SystemExit("BOT_TOKEN تنظیم نشده!")
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
