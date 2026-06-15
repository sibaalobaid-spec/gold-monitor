"""
Gold Price Monitor - ÖGUSSA
GitHub Actions version — runs once per invocation (no loop)
"""

import asyncio
import json
import os
import smtplib
import sys
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText

import requests
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding="utf-8")

# ================================================================
#  SETTINGS  (credentials come from GitHub Secrets / env vars)
# ================================================================
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
EMAIL_TO          = "Basharalkhateb6@gmail.com"
EMAIL_FROM        = "Basharalkhateb6@gmail.com"
EMAIL_PASSWORD    = os.environ["EMAIL_PASSWORD"]

OUNCE_ALERT_BELOW = 3600.0
MORNING_HOUR      = 9
EVENING_HOUR      = 18
WATCH_WEIGHTS     = ["Barren 100 Gramm", "Barren 50 Gramm", "Barren 31,1 Gramm", "Barren 10 Gramm"]
PRICES_FILE       = "gold_prices.json"
URL               = "https://www.oegussa.at/de/shop/goldbarren"
BERLIN            = timezone(timedelta(hours=2))
# ================================================================


def now_berlin() -> datetime:
    return datetime.now(BERLIN)


def ts_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def send_telegram(message: str) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=15,
        )
        ok = r.status_code == 200
        print(f"  Telegram: {'OK' if ok else 'FAIL - ' + r.text}")
        return ok
    except Exception as e:
        print(f"  Telegram error: {e}")
        return False


def send_email(subject: str, body: str) -> bool:
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as smtp:
            smtp.login(EMAIL_FROM, EMAIL_PASSWORD)
            smtp.send_message(msg)
        print("  Email: OK")
        return True
    except Exception as e:
        print(f"  Email error: {e}")
        return False


def parse_price(price_str: str) -> float:
    return float(price_str.replace("€", "").strip().replace(".", "").replace(",", "."))


def load_prices() -> dict:
    if os.path.exists(PRICES_FILE):
        with open(PRICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_prices(prices: dict):
    with open(PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(prices, f, ensure_ascii=False, indent=2)


async def scrape_prices() -> dict:
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir="/tmp/.gold_monitor_browser",
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="de-AT",
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await ctx.new_page()
        await page.goto(URL, wait_until="networkidle", timeout=60000)

        data = await page.evaluate("""() => {
            const lists = document.querySelectorAll('ul');
            const goldList = Array.from(lists).find(
                ul => ul.querySelectorAll('.shopPriceValue').length > 3
            );
            if (!goldList) return [];
            return Array.from(goldList.querySelectorAll('li')).map(li => {
                const img     = li.querySelector('img[title]');
                const priceEl = li.querySelector('.shopPriceValue');
                return { name: img?.getAttribute('title') ?? null,
                         price: priceEl?.textContent.trim() ?? null };
            }).filter(p => p.name && p.price);
        }""")
        await ctx.close()

    all_prices = {item["name"]: item["price"] for item in data}
    return {n: p for n, p in all_prices.items() if any(w in n for w in WATCH_WEIGHTS)}


def check_ounce_email(prices: dict, ts: str):
    ounce = next((n for n in prices if "31,1 Gramm" in n), None)
    if not ounce:
        return
    val = parse_price(prices[ounce])
    if val < OUNCE_ALERT_BELOW:
        print(f"  ALERT: Ounce {val}€ < {OUNCE_ALERT_BELOW}€ - sending email!")
        send_email(
            subject=f"تنبيه ذهب: الأونصة نزلت إلى {prices[ounce]}",
            body=(
                f"تنبيه سعر الذهب - ÖGUSSA\n\n"
                f"الأونصة (31.1g) نزل سعرها إلى: {prices[ounce]}\n"
                f"الحد المطلوب: أقل من {OUNCE_ALERT_BELOW}€\n\n"
                f"الوقت: {ts}\n"
                f"الموقع: {URL}"
            ),
        )


def build_report(prices: dict, label: str, ts: str) -> str:
    lines = [f"• {name}: <b>{price}</b>" for name, price in prices.items()]
    return (
        f"📊 <b>تقرير الذهب - {label}</b>\n\n"
        + "\n".join(lines)
        + f"\n\n🕐 {ts}"
    )


async def main():
    now     = now_berlin()
    ts      = ts_str(now)
    weekday = now.weekday()   # 0=Mon ... 4=Fri ... 5=Sat, 6=Sun
    hour    = now.hour

    print(f"[{ts}]  weekday={weekday}  hour={hour}")

    try:
        prices = await scrape_prices()
    except Exception as e:
        print(f"  Scrape error: {e}")
        return

    if not prices:
        print("  No products found.")
        return

    print(f"  Found {len(prices)} products: {list(prices.keys())}")

    check_ounce_email(prices, ts)

    if weekday >= 5:
        # Saturday / Sunday: only scheduled reports
        if hour == MORNING_HOUR:
            send_telegram(build_report(prices, "الصباحية", ts))
        elif hour == EVENING_HOUR:
            send_telegram(build_report(prices, "المسائية", ts))
        else:
            print("  Weekend, not report time - no Telegram sent.")
    else:
        # Monday – Friday: alert on any price change
        old     = load_prices()
        changes = []
        for name, new_price in prices.items():
            old_price = old.get(name)
            if old_price and old_price != new_price:
                try:
                    diff  = parse_price(new_price) - parse_price(old_price)
                    pct   = (diff / parse_price(old_price)) * 100
                    arrow = "📈" if diff > 0 else "📉"
                    changes.append(
                        f"{arrow} <b>{name}</b>\n"
                        f"   قبل: {old_price}  ←  الآن: {new_price}\n"
                        f"   ({diff:+.2f}€ / {pct:+.2f}%)"
                    )
                except ValueError:
                    changes.append(f"⚠️ {name}: {old_price} → {new_price}")

        if changes:
            send_telegram(
                "🔔 <b>تنبيه تغيير سعر الذهب</b>\n\n"
                + "\n\n".join(changes)
                + f"\n\n🕐 {ts}"
            )
        else:
            print("  No price changes.")

    save_prices(prices)
    print("  Done.")


if __name__ == "__main__":
    asyncio.run(main())
