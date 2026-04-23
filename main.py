import os
import json
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from openai import OpenAI
from datetime import datetime
import pytz

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

CALENDAR_ID = 'nomanahmadzai33@gmail.com'

def get_calendar_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds_json = os.environ.get('GOOGLE_CREDENTIALS')
        if not creds_json:
            return None
        try:
            import base64
            creds_info = json.loads(base64.b64decode(creds_json).decode())
        except Exception:
            creds_info = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=['https://www.googleapis.com/auth/calendar']
        )
        return build('calendar', 'v3', credentials=credentials)
    except Exception as e:
        print(f"Calendar service error: {e}")
        return None

def create_reservation(name, date, time_str, guests, phone):
    try:
        service = get_calendar_service()
        if not service:
            print("No calendar service available")
            return False
        houston_tz = pytz.timezone('America/Chicago')
        formats = [
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %I:%M %p",
            "%m/%d/%Y %H:%M",
            "%m/%d/%Y %I:%M %p",
            "%d/%m/%Y %H:%M",
            "%B %d %Y %H:%M",
            "%B %d, %Y %H:%M",
        ]
        dt = None
        combined = f"{date} {time_str}".strip()
        for fmt in formats:
            try:
                dt = datetime.strptime(combined, fmt)
                break
            except:
                continue

        if not dt:
            from datetime import date as ddate, timedelta
            tomorrow = ddate.today() + timedelta(days=1)
            dt = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 19, 0)

        dt_houston = houston_tz.localize(dt)
        dt_end = dt_houston.replace(hour=min(dt_houston.hour + 1, 23))

        event = {
            'summary': f'Reservation - {name} ({guests} guests)',
            'description': f'Name: {name}\nGuests: {guests}\nPhone: {phone}\nDate: {date}\nTime: {time_str}\nBooked via WhatsApp',
            'start': {'dateTime': dt_houston.isoformat(), 'timeZone': 'UTC'},
            'end': {'dateTime': dt_end.isoformat(), 'timeZone': 'UTC'},
        }
        result = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        print(f"Event created: {result.get('htmlLink')}")
        return True
    except Exception as e:
        print(f"Calendar error: {e}")
        return False

def get_houston_time():
    houston_tz = pytz.timezone('America/Chicago')
    now = datetime.now(houston_tz)
    return now

def get_houston_greeting():
    try:
        now = get_houston_time()
        hour = now.hour
        if 5 <= hour < 12:
            return "Good morning"
        elif 12 <= hour < 17:
            return "Good afternoon"
        else:
            return "Good evening"
    except:
        return "Hello"

def get_system_prompt():
    now = get_houston_time()
    current_date = now.strftime("%A, %B %d, %Y")
    current_time = now.strftime("%I:%M %p")
    day_of_week = now.strftime("%A")
    
    return f"""You are a friendly AI assistant for Tolo Kabab House, an authentic Afghan restaurant in Houston, TX.

CURRENT DATE AND TIME (Houston, Texas):
- Today is: {current_date}
- Current time: {current_time}
- Day of week: {day_of_week}

Use this information to understand relative dates like "tomorrow", "next Friday", "this weekend", "mañana", "el viernes", etc. Always calculate the correct date based on TODAY's date above.

LANGUAGE RULE: CRITICAL - Always respond in the EXACT same language the customer is writing in. If they write in Spanish, respond 100% in Spanish. If English, respond in English. If Dari, respond in Dari. If Pashto, respond in Pashto. If Urdu, respond in Urdu. NEVER switch languages.

WELCOME MESSAGE RULE: When a customer messages for the FIRST time, always start with a warm greeting, then introduce Tolo Kabab House.

RESERVATION RULE: When a customer wants to make a reservation, collect these ONE AT A TIME:
1. Name
2. Date - understand natural language like "tomorrow", "next Friday", "mañana", "el viernes que viene" and convert to YYYY-MM-DD
3. Time - convert to HH:MM (24hr format)
4. Number of guests
5. Phone number
Once you have ALL 5, on a NEW LINE write ONLY:
SAVE_RESERVATION:name=NAME|date=YYYY-MM-DD|time=HH:MM|guests=NUMBER|phone=PHONE

Then in the SAME language as the customer, confirm the reservation warmly.

RESTAURANT INFO:
- Name: Tolo Kabab House
- Address: 7555 Bellaire Blvd, Suite 300, Houston, TX 77036
- Phone: (281) 888-7398
- All meat 100% halal. Dine-in, Takeout, Delivery (DoorDash, Uber Eats)

HOURS:
- Monday: 11:00 AM - 9:30 PM
- Tuesday-Thursday: 11:30 AM - 9:30 PM
- Friday-Saturday: 11:30 AM - 10:00 PM
- Sunday: 11:00 AM - 10:00 PM

APPETIZERS: Chicken Soup $4.00, Bulani $8.00, Samosa $4.00
ENTREE: Afghani Tikka Kabob $16.99, Seekh Kabob $13.99, Beef Kabob $14.99, Chapli Kabob $13.99, Chicken Kabob $13.99, Combo Kabob $15.99, Afghani Burger $7.99, Chicken Roll $7.99, Beef Roll $7.99, Qabuli Palau $15.99 (most popular), Mantoo $12.99, Bolani $8.99, French Fries $4.99
PLATTERS: Mix Platter 2 Serving $31.99, Mix Platter 3 Serving $44.99, Family Platter 4 Serving $63.99
KARAHI: Chicken $11.99, Beef $12.99, Goat $14.99, Lamb $14.99
VEGGIE: Red Beans $11.00, Spinach $11.00
KIDS: Chicken Nuggets $6.99, Mango Juice $3.99, Ice Cream Qulfi $2.99
SIDES: Naan $1.49, Red Beans $3.99, Goat Karahi $5.99, Qabuli Rice $4.99
DRINKS: Dough $1.00, Cold Drink $1.00, Mango/Banana Juice $3.00, Ice Cream $2.00, Saffron Tea free

Complimentary with dine-in: soup, salad, saffron tea, firni dessert.
Keep responses short and warm. No markdown. Max 2-3 sentences. Never say you are an AI unless asked."""

conversation_history = {}

@app.route('/whatsapp', methods=['POST'])
def whatsapp():
    try:
        incoming_msg = request.values.get('Body', '').strip()
        sender = request.values.get('From', '')

        is_first_message = sender not in conversation_history
        if is_first_message:
            conversation_history[sender] = []
            greeting = get_houston_greeting()
            system_prompt = get_system_prompt() + f"\n\nCURRENT GREETING: It is {greeting} in Houston TX. Start with this greeting."
        else:
            system_prompt = get_system_prompt()

        conversation_history[sender].append({'role': 'user', 'content': incoming_msg})

        response = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[{'role': 'system', 'content': system_prompt}] + conversation_history[sender],
            max_tokens=300
        )
        reply = response.choices[0].message.content
        conversation_history[sender].append({'role': 'assistant', 'content': reply})

        if 'SAVE_RESERVATION:' in reply:
            try:
                data = reply.split('SAVE_RESERVATION:')[1].split('\n')[0].strip()
                parts = dict(p.split('=', 1) for p in data.split('|'))
                success = create_reservation(
                    parts.get('name', ''),
                    parts.get('date', ''),
                    parts.get('time', ''),
                    parts.get('guests', ''),
                    parts.get('phone', '')
                )
                reply = reply.replace(f"SAVE_RESERVATION:{data}", '').strip()
                if not reply:
                    if success:
                        reply = "¡Tu reserva está confirmada y guardada! ¡Te esperamos en Tolo Kabab House! 🎉"
                    else:
                        reply = "¡Tu reserva ha sido anotada! Por favor llámanos al (281) 888-7398 para confirmar."
            except Exception as e:
                print(f"Reservation error: {e}")

    except Exception as e:
        print(f"Main error: {e}")
        reply = 'Sorry, please try again!'

    resp = MessagingResponse()
    resp.message(reply)
    return str(resp)

@app.route('/', methods=['GET'])
def home():
    return 'WhatsApp Chatbot running!'

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
