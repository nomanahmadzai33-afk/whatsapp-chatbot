import os
import json
import logging
from flask_cors import CORS
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from openai import OpenAI
from datetime import datetime
import pytz
import gspread
from google.oauth2 import service_account
import base64

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins=[
    "https://talkserveai.net",
    "https://www.talkserveai.net",
    "https://*.railway.app"
])

openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
twilio_client  = Client(os.environ.get('TWILIO_ACCOUNT_SID'), os.environ.get('TWILIO_AUTH_TOKEN'))

# ── Google Sheets helper ──────────────────────────────────────────────────────
SHEET_NAME = "Du Liban Reservations"   # <-- UPDATE to your actual Google Sheet name

def get_sheets_client():
    try:
        creds_json = os.environ.get('GOOGLE_CREDENTIALS')
        if not creds_json:
            logger.warning("GOOGLE_CREDENTIALS env var not set")
            return None
        try:
            creds_info = json.loads(base64.b64decode(creds_json).decode())
        except Exception:
            creds_info = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=[
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'
            ]
        )
        return gspread.authorize(credentials)
    except Exception as e:
        logger.error(f"Sheets client error: {e}")
        return None

def save_reservation(name, date, time_str, guests, phone):
    try:
        gc = get_sheets_client()
        if not gc:
            return False
        sheet = gc.open(SHEET_NAME).sheet1
        madrid_tz = pytz.timezone('Europe/Madrid')
        now = datetime.now(madrid_tz).strftime("%Y-%m-%d %H:%M")
        sheet.append_row([now, name, guests, date, time_str, phone, "CONFIRMED", ""])
        logger.info(f"Reservation saved: {name} — {date} {time_str} ({guests} guests)")
        return True
    except Exception as e:
        logger.error(f"save_reservation error: {e}")
        return False

def save_order(name, phone, items, pickup_time, pickup_date, total):
    try:
        gc = get_sheets_client()
        if not gc:
            return False
        try:
            sheet = gc.open(SHEET_NAME).worksheet("Pedidos")
        except Exception:
            spreadsheet = gc.open(SHEET_NAME)
            sheet = spreadsheet.add_worksheet(title="Pedidos", rows=1000, cols=10)
            sheet.append_row(["Timestamp","Name","Phone","Items","PickupDate","PickupTime","Total","Status","Notes"])
        madrid_tz = pytz.timezone('Europe/Madrid')
        now = datetime.now(madrid_tz).strftime("%Y-%m-%d %H:%M")
        sheet.append_row([now, name, phone, items, pickup_date, pickup_time, total, "PENDING", ""])
        logger.info(f"Order saved: {name}")
        return True
    except Exception as e:
        logger.error(f"save_order error: {e}")
        return False

def send_whatsapp_confirmation(phone, name, guests, date, time_str):
    """Send a WhatsApp confirmation message to the guest."""
    try:
        twilio_number = os.environ.get('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')
        if guests:
            body = (
                f"✅ Hola {name}! Tu reserva en Du Liban está confirmada: "
                f"{guests} persona(s) el {date} a las {time_str}. "
                f"Te llamaremos el día anterior para confirmar. ¡Hasta pronto! 📞 +34 911 677 963"
            )
        else:
            body = (
                f"✅ Hola {name}! Tu reserva en Du Liban está anotada para el {date} a las {time_str}. "
                f"Te confirmaremos en breve. ¡Hasta pronto! 📞 +34 911 677 963"
            )
        twilio_client.messages.create(
            body=body,
            from_=twilio_number,
            to=f"whatsapp:{phone}"
        )
        logger.info(f"WhatsApp confirmation sent to {phone}")
        return True
    except Exception as e:
        logger.error(f"send_whatsapp_confirmation error: {e}")
        return False

# ── Time helpers ──────────────────────────────────────────────────────────────
def get_madrid_time():
    madrid_tz = pytz.timezone('Europe/Madrid')
    return datetime.now(madrid_tz)

def get_kitchen_status(now):
    """
    Returns a string describing kitchen availability at the given Madrid datetime.
    
    Du Liban hours:
      - Tuesday–Sunday: 13:00–01:00
      - Monday: CLOSED
      - Kitchen closed: 16:00–20:00 (only shisha, cocktails, drinks)
      - Kitchen closes again at approximately 23:30
    """
    weekday = now.weekday()   # 0=Monday … 6=Sunday
    hour    = now.hour
    minute  = now.minute
    time_mins = hour * 60 + minute   # minutes since midnight

    # Monday = closed entirely
    if weekday == 0:
        return "RESTAURANTE_CERRADO_LUNES"

    # Outside opening hours (01:00–13:00)
    if time_mins < 13 * 60 or time_mins >= 25 * 60:   # past 01:00 next day
        return "RESTAURANTE_CERRADO_HORARIO"

    # Kitchen closed afternoon break
    if 16 * 60 <= time_mins < 20 * 60:
        return "COCINA_CERRADA_TARDE"   # Only shisha/drinks/cocktails

    # Kitchen closing late night
    if time_mins >= 23 * 60 + 30:
        return "COCINA_CERRADA_NOCHE"   # Only drinks available

    # Full service
    return "SERVICIO_COMPLETO"

# ── System prompt ─────────────────────────────────────────────────────────────
def get_system_prompt():
    now          = get_madrid_time()
    current_date = now.strftime("%A, %d de %B de %Y")
    current_time = now.strftime("%H:%M")
    kitchen_status = get_kitchen_status(now)

    # Build kitchen notice block
    if kitchen_status == "RESTAURANTE_CERRADO_LUNES":
        kitchen_notice = """
⚠️ ESTADO ACTUAL: El restaurante está CERRADO hoy (lunes). Los lunes Du Liban no abre.
No hagas ninguna reserva para hoy. Si el cliente pregunta, explícale amablemente que el restaurante cierra los lunes y sugiérele que vuelva de martes a domingo."""

    elif kitchen_status == "RESTAURANTE_CERRADO_HORARIO":
        kitchen_notice = """
⚠️ ESTADO ACTUAL: El restaurante está CERRADO ahora mismo.
Horario: martes a domingo de 13:00 a 01:00. Los lunes cierra todo el día."""

    elif kitchen_status == "COCINA_CERRADA_TARDE":
        kitchen_notice = """
⚠️ ESTADO ACTUAL (16:00–20:00): La cocina está CERRADA en este momento.
Actualmente sólo se sirven: shisha (narguile), cócteles y bebidas.
NO prometas ni confirmes pedidos de comida para ahora mismo.
Si alguien quiere reservar una mesa para cenar (después de las 20:00), puedes ayudarle."""

    elif kitchen_status == "COCINA_CERRADA_NOCHE":
        kitchen_notice = """
⚠️ ESTADO ACTUAL (después de las 23:30): La cocina ya ha cerrado.
Sólo se sirven bebidas y shisha.
NO prometas ni confirmes pedidos de comida para ahora mismo."""

    else:  # SERVICIO_COMPLETO
        kitchen_notice = "✅ ESTADO ACTUAL: Servicio completo activo. Cocina y carta disponibles."

    return f"""Eres el asistente virtual de WhatsApp del restaurante Du Liban de la Moraleja en Alcobendas, Madrid. No tienes nombre propio. Eres cálido, profesional y completamente humano. Nunca suenes como robot o IA. Nunca digas que eres IA.

FECHA Y HORA ACTUAL (Madrid): {current_date}, {current_time}

{kitchen_notice}

────────────────────────────────────
IDIOMA
────────────────────────────────────
Detecta el idioma del cliente al instante y mantén ese mismo idioma durante toda la conversación.
- Español → español nativo de España
- Inglés → inglés nativo
- Árabe → árabe nativo
- Francés → francés nativo
- Por defecto: español

TONO: Cálido, cercano y profesional. Máximo 2–3 frases por respuesta. Natural, nunca robótico. No uses emojis en exceso.

────────────────────────────────────
INFORMACIÓN DEL RESTAURANTE
────────────────────────────────────
Nombre: Du Liban de la Moraleja
Tipo: Restaurante libanés
Dirección: Alcobendas (La Moraleja), Madrid, España
Teléfono: +34 911 677 963
Web: talkserveai.net (gestor del sistema, no del restaurante)

HORARIO:
- Martes a domingo: 13:00 – 01:00
- LUNES: CERRADO

COCINA:
- Abierta: 13:00–16:00 y 20:00–23:30
- CERRADA: 16:00–20:00 (sólo shisha, cócteles y bebidas)
- CERRADA: después de las 23:30 (sólo bebidas)

────────────────────────────────────
CARTA (resumen)
────────────────────────────────────
MEZZE FRÍOS: Hummus, Moutabal, Tabbouleh, Fattoush, Warak Enab, Muhammara, Labneh
MEZZE CALIENTES: Falafel, Kafta, Kebbeh, Hallumi, Soujok, Batata Harrah, Rakakat Jebneh
PRINCIPALES: Taouk (pollo), Kafta Meshwiyye, Lahmeh Mshwieh (ternera), Samak Mashwi (pescado), Farrouj (pollo entero), Mawzet Kharouf (codillo de cordero)
MARISCOS: Okhtobut (pulpo), Salata Al Rubyan (langostinos)
POSTRES: Knefeh, Baklava, Mhalabieh, Aish El Saraya, Em Ali
SHISHA / NARGUILE: Disponible (30€). Recambio 15€. Sólo cuando la cocina está cerrada (16–20h) o como complemento
BEBIDAS: Cócteles, Mojito (13€), Margarita (13€), Aperol Spritz (14€), zumos, agua, té moruno

Precio medio por persona: 35–55€
No hay menú del día estándar. Carta completa disponible en el restaurante.

────────────────────────────────────
RESERVAS — MUY IMPORTANTE
────────────────────────────────────
Recoge los datos UNO A UNO de forma natural:
1. Nombre completo del cliente
2. Día y fecha completa (ej: "este viernes 21 de agosto")
3. Hora (entre 13:00 y 00:00, no durante el cierre de cocina si quieren cenar)
4. Número de personas
5. Teléfono de contacto

Cuando tengas los 5 datos, confirma EXACTAMENTE así:
"Perfecto [nombre], he apuntado tu reserva para [N] personas el [día fecha completa] a las [hora]. Recibirás confirmación por este mismo mensaje en breve. ¡Te esperamos!"

Si la reserva es para más de 10 personas: "Para grupos de más de 10 personas, por favor contáctanos directamente al +34 911 677 963 para que podamos organizarlo mejor."

Después escribe SÓLO en una nueva línea (sin añadir más texto):
SAVE_RESERVATION:name=NOMBRE|date=FECHA|time=HORA|guests=NUMERO|phone=TELEFONO

IMPORTANTE: NO confirmes reservas para LUNES. Di amablemente que el restaurante cierra los lunes.
IMPORTANTE: Si piden mesa para ahora y es entre 16:00–20:00, avísales de que la cocina está cerrada en ese momento y ofréceles mesa para las 20:00 en adelante.

────────────────────────────────────
CAMBIOS Y CANCELACIONES
────────────────────────────────────
Pide: nombre del cliente, fecha original de la reserva, y qué desea cambiar.
Confirma con día + fecha + mes completo.
Si cancela: "Entendido [nombre], hemos cancelado tu reserva del [día fecha]. Si quieres volver a reservar, aquí estamos."

────────────────────────────────────
PREGUNTAS FRECUENTES
────────────────────────────────────
- ¿Tienen parking? → "Hay parking disponible en los alrededores de La Moraleja."
- ¿Admiten niños? → "Sí, son bienvenidos en familia."
- ¿Tienen terraza? → "Consulta disponibilidad llamando al +34 911 677 963."
- ¿Sirven alcohol? → "Sí, tenemos carta de vinos, cócteles y bebidas."
- ¿Hay opciones vegetarianas? → "Sí, tenemos una amplia selección de mezze vegetarianos como hummus, tabbouleh, moutabal, falafel y más."
- ¿Tienen opciones sin gluten? → "Varios platos de nuestra carta son naturalmente sin gluten. Para alergias específicas, por favor indícalo al reservar."
- ¿Qué es la shisha? → "La shisha (narguile) es una pipa de agua con tabaco aromatizado, disponible en varios sabores. Precio: 30€."

────────────────────────────────────
TRANSFERENCIA A HUMANO
────────────────────────────────────
Si el cliente pregunta algo que no puedes responder con seguridad, o si la situación es compleja:
"Ahora mismo te pongo en contacto con nuestro equipo. Puedes llamarnos directamente al +34 911 677 963."

────────────────────────────────────
REGLAS ESTRICTAS
────────────────────────────────────
1. NUNCA prometas comida cuando la cocina está cerrada (16:00–20:00 o después de 23:30)
2. NUNCA confirmes reservas para lunes
3. NUNCA inventes información sobre el menú, precios o disponibilidad
4. NUNCA digas que eres una IA o un bot
5. SIEMPRE confirma fechas con día de la semana + número + mes completo
6. Respuestas cortas y naturales siempre (máximo 3 frases)
7. El equipo humano confirma definitivamente — tú sólo anotas la intención
8. NUNCA hagas promesas sobre precios exactos si no estás seguro
"""

# ── Conversation history (file-based persistence) ────────────────────────────
# History is stored in /tmp/conversations/ as JSON files, one per sender.
# This survives between requests on the same deployment.  On Railway a redeploy
# wipes /tmp — for true persistence across deploys, swap for Redis or Postgres.
MAX_HISTORY = 50
CONV_DIR = "/tmp/conversations"
os.makedirs(CONV_DIR, exist_ok=True)

def _conv_path(sender: str) -> str:
    """Return a safe file path for a sender's conversation history."""
    safe = sender.replace("+", "plus").replace(":", "_").replace("/", "_")
    return os.path.join(CONV_DIR, f"{safe}.json")

def get_history(sender: str) -> list:
    path = _conv_path(sender)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_history(sender: str, history: list):
    """Persist conversation history to disk, trimmed to MAX_HISTORY messages."""
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    path = _conv_path(sender)
    with open(path, "w") as f:
        json.dump(history, f)

def trim_history(sender: str):
    """Legacy shim — kept so call sites don't break; save_history now trims."""
    pass

# ── WhatsApp webhook ──────────────────────────────────────────────────────────
@app.route('/whatsapp', methods=['POST'])
def whatsapp():
    try:
        incoming_msg = request.values.get('Body', '').strip()
        sender       = request.values.get('From', '')

        logger.info(f"Message from {sender}: {incoming_msg[:80]}")

        history = get_history(sender)
        history.append({'role': 'user', 'content': incoming_msg})

        response = openai_client.chat.completions.create(
            model='gpt-4o',
            messages=[{'role': 'system', 'content': get_system_prompt()}] + history,
            max_tokens=450,
            temperature=0.7
        )
        reply = response.choices[0].message.content.strip()
        history.append({'role': 'assistant', 'content': reply})
        save_history(sender, history)  # persist to disk

        # ── Handle SAVE_RESERVATION ──
        if 'SAVE_RESERVATION:' in reply:
            try:
                raw  = reply.split('SAVE_RESERVATION:')[1].split('\n')[0].strip()
                parts = dict(p.split('=', 1) for p in raw.split('|'))
                save_reservation(
                    parts.get('name', ''),
                    parts.get('date', ''),
                    parts.get('time', ''),
                    parts.get('guests', ''),
                    parts.get('phone', '')
                )
                # Send WhatsApp confirmation to guest
                guest_phone = sender.replace('whatsapp:', '')
                send_whatsapp_confirmation(
                    guest_phone,
                    parts.get('name', ''),
                    parts.get('guests', ''),
                    parts.get('date', ''),
                    parts.get('time', '')
                )
                # Strip the command from the visible reply
                reply = reply.replace(f"SAVE_RESERVATION:{raw}", '').strip()
            except Exception as e:
                logger.error(f"SAVE_RESERVATION parse error: {e}")

        # ── Handle SAVE_ORDER ──
        if 'SAVE_ORDER:' in reply:
            try:
                raw   = reply.split('SAVE_ORDER:')[1].split('\n')[0].strip()
                oparts = dict(p.split('=', 1) for p in raw.split('|'))
                save_order(
                    oparts.get('name', ''),
                    oparts.get('phone', ''),
                    oparts.get('items', ''),
                    oparts.get('time', ''),
                    oparts.get('date', ''),
                    oparts.get('total', '')
                )
                reply = reply.replace(f"SAVE_ORDER:{raw}", '').strip()
            except Exception as e:
                logger.error(f"SAVE_ORDER parse error: {e}")

        logger.info(f"Reply to {sender}: {reply[:80]}")

    except Exception as e:
        logger.error(f"whatsapp() error: {e}")
        reply = 'Lo sentimos, ha habido un problema. Por favor inténtalo de nuevo o llámanos al +34 911 677 963.'

    resp = MessagingResponse()
    resp.message(reply)
    return str(resp)


# ── Voice receptionist reservation endpoint ───────────────────────────────────
@app.route('/voice-reservation', methods=['POST'])
def voice_reservation():
    try:
        data     = request.get_json()
        name     = data.get('name', '')
        date     = data.get('date', '')
        time_str = data.get('time', '')
        guests   = data.get('guests', '')
        phone    = data.get('phone', '')
        if phone and not phone.startswith('+'):
            phone = '+34' + phone
        save_reservation(name, date, time_str, guests, phone)
        send_whatsapp_confirmation(phone, name, guests, date, time_str)
        logger.info(f"Voice reservation saved: {name}")
        return {'status': 'success', 'message': f'Reservation saved for {name}'}, 200
    except Exception as e:
        logger.error(f"voice_reservation error: {e}")
        return {'status': 'error', 'message': str(e)}, 500


# ── IVR reservation confirmation (Twilio voice callback) ─────────────────────
@app.route('/confirm-reservation', methods=['POST'])
def confirm_reservation():
    digit = request.values.get('Digits', '')
    phone = (
        request.values.get('phone', '')
        or request.values.get('To', '')
        or request.values.get('Called', '')
    )
    try:
        gc    = get_sheets_client()
        sheet = gc.open(SHEET_NAME).sheet1
        records = sheet.get_all_records()
        msg = "No encontramos su reserva. Llámenos al 911 677 963."
        for i, r in enumerate(records):
            r_phone = ''.join(ch for ch in str(r.get('Phone', '')) if ch.isdigit())
            c_phone = ''.join(ch for ch in str(phone) if ch.isdigit())
            if len(r_phone) < 9 or len(c_phone) < 9:
                continue
            if r_phone[-9:] == c_phone[-9:]:
                row_num = i + 2
                if digit == '1':
                    sheet.update_cell(row_num, 7, 'VERIFIED')
                    msg = "Gracias, su reserva en Du Liban ha sido verificada. Le esperamos."
                elif digit == '2':
                    sheet.update_cell(row_num, 7, 'CANCELLED')
                    msg = "Su reserva ha sido cancelada. Si necesita ayuda llámenos al 911 677 963."
                else:
                    msg = "No hemos recibido su respuesta. Por favor llámenos al 911 677 963."
                break
    except Exception as e:
        logger.error(f"confirm_reservation error: {e}")
        msg = "Ha ocurrido un error. Por favor llámenos al 911 677 963."

    twiml = f'''<?xml version="1.0" encoding="UTF-8"?>
<Response><Say language="es-ES" voice="Polly.Conchita">{msg}</Say></Response>'''
    return twiml, 200, {'Content-Type': 'text/xml'}


# ── Data read endpoints (for dashboard) ───────────────────────────────────────
@app.route('/get-reservations', methods=['GET'])
def get_reservations():
    try:
        gc      = get_sheets_client()
        sheet   = gc.open(SHEET_NAME).sheet1
        records = sheet.get_all_records()
        return {'reservations': records}, 200
    except Exception as e:
        logger.error(f"get_reservations error: {e}")
        return {'error': str(e)}, 500

@app.route('/get-orders', methods=['GET'])
def get_orders():
    try:
        gc = get_sheets_client()
        try:
            sheet   = gc.open(SHEET_NAME).worksheet("Pedidos")
            records = sheet.get_all_records()
        except Exception:
            records = []
        return {'orders': records}, 200
    except Exception as e:
        logger.error(f"get_orders error: {e}")
        return {'error': str(e)}, 500

@app.route('/get-customers', methods=['GET'])
def get_customers():
    try:
        gc = get_sheets_client()
        try:
            sheet   = gc.open(SHEET_NAME).worksheet("Customers")
            records = sheet.get_all_records()
        except Exception:
            records = []
        return {'customers': records}, 200
    except Exception as e:
        logger.error(f"get_customers error: {e}")
        return {'error': str(e)}, 500

@app.route('/get-tables', methods=['GET'])
def get_tables():
    try:
        gc = get_sheets_client()
        try:
            sheet   = gc.open(SHEET_NAME).worksheet("Tables")
            records = sheet.get_all_records()
        except Exception:
            records = []
        return {'tables': records}, 200
    except Exception as e:
        logger.error(f"get_tables error: {e}")
        return {'error': str(e)}, 500

@app.route('/get-pending-actions', methods=['GET'])
def get_pending_actions():
    try:
        gc = get_sheets_client()
        try:
            sheet   = gc.open(SHEET_NAME).worksheet("PendingActions")
            records = sheet.get_all_records()
        except Exception:
            records = []
        return {'actions': records}, 200
    except Exception as e:
        logger.error(f"get_pending_actions error: {e}")
        return {'error': str(e)}, 500

@app.route('/update-table', methods=['POST'])
def update_table():
    try:
        data     = request.get_json()
        table_id = data.get('table_id')
        status   = data.get('status')
        guest    = data.get('guest_name', '')
        gc       = get_sheets_client()
        try:
            sheet   = gc.open(SHEET_NAME).worksheet("Tables")
            records = sheet.get_all_records()
            for i, r in enumerate(records):
                if str(r.get('TableID', '')) == str(table_id):
                    sheet.update_cell(i + 2, 2, status)
                    sheet.update_cell(i + 2, 3, guest)
                    return {'status': 'success'}, 200
            # Table not found — add it
            sheet.append_row([table_id, status, guest])
        except Exception:
            pass
        return {'status': 'ok', 'note': 'Tables sheet not found; skipped'}, 200
    except Exception as e:
        logger.error(f"update_table error: {e}")
        return {'error': str(e)}, 500

@app.route('/resolve-pending-action', methods=['POST'])
def resolve_pending_action():
    try:
        data    = request.get_json()
        row_num = data.get('row')
        gc      = get_sheets_client()
        sheet   = gc.open(SHEET_NAME).worksheet("PendingActions")
        headers = sheet.row_values(1)
        status_col = headers.index('Estado') + 1
        sheet.update_cell(row_num, status_col, 'HECHO')
        return {'status': 'success'}, 200
    except Exception as e:
        logger.error(f"resolve_pending_action error: {e}")
        return {'error': str(e)}, 500

@app.route('/update-reservation', methods=['POST'])
def update_reservation():
    try:
        data    = request.get_json()
        row_num = data.get('row')
        field   = data.get('field')
        value   = data.get('value')
        gc      = get_sheets_client()
        sheet   = gc.open(SHEET_NAME).sheet1
        headers = sheet.row_values(1)
        col     = headers.index(field) + 1
        sheet.update_cell(row_num, col, value)
        return {'status': 'success'}, 200
    except Exception as e:
        logger.error(f"update_reservation error: {e}")
        return {'error': str(e)}, 500

@app.route('/update-order', methods=['POST'])
def update_order():
    try:
        data    = request.get_json()
        row_num = data.get('row')
        field   = data.get('field')
        value   = data.get('value')
        gc      = get_sheets_client()
        sheet   = gc.open(SHEET_NAME).worksheet("Pedidos")
        headers = sheet.row_values(1)
        col     = headers.index(field) + 1
        sheet.update_cell(row_num, col, value)
        return {'status': 'success'}, 200
    except Exception as e:
        logger.error(f"update_order error: {e}")
        return {'error': str(e)}, 500


# ── Health check ──────────────────────────────────────────────────────────────
@app.route('/', methods=['GET'])
def home():
    now    = get_madrid_time()
    status = get_kitchen_status(now)
    return {
        'service': 'Du Liban WhatsApp Bot',
        'status': 'running',
        'madrid_time': now.strftime("%Y-%m-%d %H:%M"),
        'kitchen_status': status
    }, 200

@app.route('/health', methods=['GET'])
def health():
    return {'status': 'ok'}, 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
