import os
import json
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from openai import OpenAI
from datetime import datetime
import pytz
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

CALENDAR_ID = 'nomanahmadzai33@gmail.com'
SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_calendar_service():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    if creds_json:
        creds_info = json.loads(creds_json)
    else:
        with open('google-credentials.json') as f:
            creds_info = json.load(f)
    credentials = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=credentials)

def create_reservation(name, date, time, guests, phone):
    try:
        service = get_calendar_service()
        houston_tz = pytz.timezone('America/Chicago')
        dt_str = f"{date} {time}"
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        except:
            try:
                dt = datetime.strptime(dt_str, "%m/%d/%Y %I:%M %p")
            except:
                dt = datetime.strptime(dt_str, "%B %d %Y %I:%M %p")
        
        dt_houston = houston_tz.localize(dt)
        dt_end = dt_houston.replace(hour=dt_houston.hour + 1)
        
        event = {
            'summary': f'Reservation - {name} ({guests} guests)',
            'description': f'Name: {name}\nGuests: {guests}\nPhone: {phone}\nBooked via WhatsApp',
            'start': {'dateTime': dt_houston.isoformat(), 'timeZone': 'America/Chicago'},
            'end': {'dateTime': dt_end.isoformat(), 'timeZone': 'America/Chicago'},
        }
        service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return True
    except Exception as e:
        print(f"Calendar error: {e}")
        return False

def get_houston_greeting():
    houston_tz = pytz.timezone('America/Chicago')
    hour = datetime.now(houston_tz).hour
    if 5 <= hour < 12:
        return "Good morning"
    elif 12 <= hour < 17:
        return "Good afternoon"
    elif 17 <= hour < 21:
        return "Good evening"
    else:
        return "Good evening"

SYSTEM_PROMPT = """You are a friendly AI assistant for Tolo Kabab House, an authentic Afghan restaurant in Houston, TX.

LANGUAGE RULE: Always respond in the EXACT language the customer is writing in. English → English. Spanish → Spanish. Dari → Dari. Pashto → Pashto. Urdu → Urdu. Switch immediately if they switch. Never mix languages.

WELCOME MESSAGE RULE: When a customer messages for the FIRST time, always start with a warm greeting using the time-of-day greeting provided, then introduce Tolo Kabab House.

RESERVATION RULE: When a customer wants to make a reservation, collect these ONE AT A TIME:
1. Name
2. Date (ask in natural language)
3. Time
4. Number of guests
5. Phone number
Once you have ALL 5, say: "SAVE_RESERVATION:name={name}|date={date}|time={time}|guests={guests}|phone={phone}"

RESTAURANT INFO:
- Name: Tolo Kabab House
- Address: 7555 Bellaire Blvd, Suite 300, Houston, TX 77036
- Phone: (281) 888-7398
- Cuisine: Authentic Afghan/Pakistani Halal food. All meat 100% halal.
- Dine-in, Takeout, Curbside Pickup, Delivery (DoorDash, Uber Eats)

HOURS:
- Monday: 11:00 AM - 9:30 PM
- Tuesday-Thursday: 11:30 AM - 9:30 PM
- Friday-Saturday: 11:30 AM - 10:00 PM
- Sunday: 11:00 AM - 10:00 PM

APPETIZERS:
- Chicken Soup $4.00 — Chicken soup with boiled egg
- Bulani $8.00 — Smashed potatoes or chive with masala, grilled, served with yogurt or green sauce
- Samosa $4.00 — 4 pieces potatoes, chicken or ground beef samosa with green sauce

ENTREE:
- Afghani Tikka Kabob $16.99 — 12 pieces grilled lamb boneless, naan, salad and green sauce
- Seekh Kabob $13.99 — 2 seekhs grilled ground beef, naan, salad and green sauce
- Beef Kabob $14.99 — 12 pieces grilled beef boneless, naan, salad and green sauce
- Chapli Kabob $13.99 — 2 pieces grilled ground beef, fries, naan, salad and green sauce
- Chicken Kabob $13.99 — 12 pieces grilled chicken boneless, naan, salad and green sauce
- Combo Kabob $15.99 — 4 pieces chicken + 4 pieces beef/lamb, naan, salad and green sauce
- Afghani Burger $7.99 — Halal sausage, fries, boiled eggs, onion, naan, salad and green sauce
- Chicken Roll $7.99 — Chicken wrapped in naan with sauce
- Beef Roll $7.99 — Beef wrapped in naan with sauce
- Qabuli Palau $15.99 — Lamb shank, carrot, raisins with rice, salad and green sauce (most popular)
- Mantoo $12.99 — Dumplings with ground beef, masala, lentils or red beans, yogurt, salad and green sauce
- Bolani $8.99 — Smashed potatoes or chive with masala, grilled, yogurt or green sauce
- French Fries and Ketchup $4.99

PLATTERS:
- Mix Platter 2 Serving $31.99
- Mix Platter 3 Serving $44.99
- Family Platt
