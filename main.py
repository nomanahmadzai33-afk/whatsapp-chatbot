import base64
import hmac
import json
import logging
import os
import re
from functools import wraps

import gspread
from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from google.oauth2 import service_account
from openai import OpenAI
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse

from dashboard_ui import DASHBOARD_HTML
from platform_store import Store, now_iso


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins=["https://purocuento.es", "https://www.purocuento.es", r"https://.*\.railway\.app"])

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY") or "test-key-not-for-production")
twilio_client = Client(
    os.environ.get("TWILIO_ACCOUNT_SID") or ("AC" + "0" * 32),
    os.environ.get("TWILIO_AUTH_TOKEN") or "test-token-not-for-production",
)
TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
OWNER_WHATSAPP_NUMBER = os.environ.get("OWNER_WHATSAPP_NUMBER", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
DB_PATH = os.environ.get("DB_PATH", "/tmp/purocuento_demo.db")

AI_ACTIVE = "AI_ACTIVE"
WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
HUMAN_ACTIVE = "HUMAN_ACTIVE"
CLOSED = "CLOSED"
VALID_STATES = {AI_ACTIVE, WAITING_FOR_HUMAN, HUMAN_ACTIVE, CLOSED}


def get_sheets_client():
    """Return the configured service-account client without logging secrets."""
    try:
        raw = os.environ.get("GOOGLE_CREDENTIALS")
        if not raw:
            return None
        try:
            info = json.loads(base64.b64decode(raw).decode())
        except Exception:
            info = json.loads(raw)
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"],
        )
        return gspread.authorize(credentials)
    except Exception as exc:
        logger.error("Google persistence client unavailable: %s", exc)
        return None


store = Store(DB_PATH, get_sheets_client)
store.init()
try:
    store.restore()
except Exception as exc:
    # The bot remains operational and the dashboard exposes persistence status.
    logger.error("Persistence restore did not complete: %s", exc)


# Compatibility helpers retained for tests and the existing deployment surface.
def db():
    return store.connect()


def ensure_conversation(phone, language="es"):
    return store.ensure_conversation(phone, language)


def get_state(phone):
    row = store.conversation(phone)
    return row["state"] if row else AI_ACTIVE


def set_state(phone, state, actor="system", reason=""):
    if state not in VALID_STATES:
        raise ValueError("Invalid state")
    return store.set_state(phone, state, actor, reason)


def log_message(phone, direction, sender, body, **kwargs):
    return store.add_message(phone, direction, sender, body, **kwargs)


def mark_processed(sid):
    return store.mark_processed(sid)


def get_history(sender):
    return store.history(sender, 50)


def save_history(sender, history):
    """Compatibility import for earlier tests; operational history lives in SQL."""
    with store.connect() as conn:
        conn.execute("DELETE FROM messages WHERE phone=? AND sender IN ('customer','ai')", (sender,))
    for index, item in enumerate(history[-50:]):
        role = item.get("role")
        sender_name = "customer" if role == "user" else "ai"
        direction = "inbound" if role == "user" else "outbound"
        store.add_message(sender, direction, sender_name, item.get("content", ""),
                          external_id=f"compat-{sender}-{index}")
        if role == "user" and item.get("content"):
            store.update_project_from_message(sender, item["content"])


def save_purocuento_lead(name, company, phone_email, project_type, services, dates,
                         location, people, logistics, description):
    """Compatibility adapter: leads now live in the structured project room."""
    phone = phone_email if str(phone_email).startswith("whatsapp:") else str(phone_email or "legacy-lead")
    return bool(store.save_project({
        "phone": phone, "name": name, "company": company, "contact": phone_email,
        "project_type": project_type,
        "services": [x.strip() for x in str(services).split(",") if x.strip()],
        "dates": dates, "location": location,
        "people": int(people) if str(people).isdigit() else None,
        "logistics": logistics, "description": description,
        "status": "HUMAN_REVIEW",
    }))


HANDOFF_KEYWORDS_ES = [
    r"\bprecio", r"\bpresupuesto", r"\bcosto", r"\btarifa", r"\bdisponibilidad",
    r"\bdisponible", r"\bstock", r"\breserva", r"\burgent", r"\bqueja", r"\bcontrato",
    r"\bdescuento", r"\bfactura", r"\bpago", r"\bentrega", r"hablar con (?:una persona|alguien|el equipo)",
]
HANDOFF_KEYWORDS_EN = [
    r"\bprice", r"\bquote", r"\bcost", r"\brate", r"\bavailability", r"\bavailable",
    r"\bstock", r"\breservation", r"\burgent", r"\bcomplaint", r"\bcontract", r"\bdiscount",
    r"\binvoice", r"\bpayment", r"\bdelivery", r"speak (?:to|with) (?:a person|someone|the team)",
]

HANDOFF_MESSAGE_ES = (
    "Gracias. Para precios, disponibilidad, presupuesto o cualquier compromiso comercial, "
    "la revisión del equipo de PuroCuento es obligatoria. He dejado tu solicitud en su bandeja "
    "con el briefing disponible para que continúen contigo personalmente."
)
HANDOFF_MESSAGE_EN = (
    "Thank you. Pricing, availability, quotations and any commercial commitment require review "
    "by the PuroCuento team. I have placed your request in their inbox with the available briefing "
    "so they can continue with you personally."
)
GREETING_RESPONSE_ES = (
    "Hola, soy el asistente virtual de PuroCuento. Puedo orientarte sobre nuestros servicios y "
    "preparar el briefing de tu producción. Los precios, la disponibilidad y la solución final "
    "siempre los valida personalmente nuestro equipo. ¿Qué necesitas para tu proyecto?"
)
GREETING_RESPONSE_EN = (
    "Hello, I'm PuroCuento's virtual assistant. I can explain our services and prepare the briefing "
    "for your production. Pricing, availability and the final solution are always personally approved "
    "by our team. What do you need for your project?"
)


def detect_language(text):
    english = {"hello", "what", "which", "how", "need", "project", "event", "price", "available", "where"}
    words = set(re.findall(r"[a-z]+", text.lower()))
    return "en" if len(words & english) >= 2 else "es"


def is_greeting(text):
    normalized = re.sub(r"[^a-záéíóúüñ\s]", " ", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized in {"hola", "buenos dias", "buenos días", "buenas tardes", "buenas noches", "hello", "hi", "hey", "greetings"}


def check_handoff(text, language):
    patterns = HANDOFF_KEYWORDS_EN if language == "en" else HANDOFF_KEYWORDS_ES
    if any(re.search(pattern, text.lower()) for pattern in patterns):
        return HANDOFF_MESSAGE_EN if language == "en" else HANDOFF_MESSAGE_ES
    return None


def is_qualified_lead(sender, incoming_msg):
    project = store.project(sender)
    if not project:
        return False
    history = store.history(sender, 50)
    user_turns = [x for x in history if x["role"] == "user" and len(x["content"].strip()) >= 10]
    has_contact = bool(project.get("contact")) or sender.startswith("whatsapp:+")
    useful_brief = bool(project.get("services")) and (project.get("location") or project.get("dates") or project.get("people"))
    return has_contact and useful_brief and len(user_turns) >= 2


def reply_promises_handoff(reply):
    text = reply.lower()
    phrases = (
        "equipo se pondrá en contacto", "equipo se pondra en contacto", "enviado al equipo",
        "pasar tu solicitud al equipo", "equipo continuará", "equipo continuara",
        "team will contact", "team will get in touch", "sent it to the", "connect you with our team",
        "continue with you personally",
    )
    return any(phrase in text for phrase in phrases)


def unsafe_reply(reply):
    text = reply.lower()
    forbidden = [
        r"(?:€|eur|euros?|\$)\s*\d|\d[\d.,]*\s*(?:€|eur|euros?|\$)",
        r"(?:está|esta|tenemos) disponible", r"disponibilidad confirmada", r"reserva confirmada",
        r"te garantizamos", r"confirmamos (?:la|el|tu)", r"we confirm", r"is available",
    ]
    return any(re.search(pattern, text) for pattern in forbidden)


def get_system_prompt(query="", documents=None, project=None):
    documents = documents or []
    source_context = "\n\n".join(
        f"FUENTE APROBADA: {d['title']} [{d['category']}]\n{d['content']}\nURL: {d.get('source_url','')}"
        for d in documents
    ) or "No se recuperó una fuente específica para esta consulta. No inventes datos; formula una pregunta o deriva al equipo."
    project_context = json.dumps(project or {}, ensure_ascii=False)
    return f"""Eres el asistente virtual supervisado de PuroCuento, empresa madrileña de servicios para
producción audiovisual, rodajes y eventos. Respondes en el idioma del cliente con criterio profesional,
claridad y concisión. No finges certeza: cada afirmación específica sobre PuroCuento debe estar respaldada
por una FUENTE APROBADA incluida abajo.

REGLAS DE AUTORIDAD HUMANA (NO NEGOCIABLES)
1. Nunca das precios, rangos, presupuestos, descuentos, stock o disponibilidad.
2. Nunca confirmas reservas, entregas, contratos, pagos, fechas, cantidades ni soluciones técnicas finales.
3. El equipo humano debe aprobar toda propuesta comercial y técnica, y todo proyecto de alto valor.
4. Si el cliente pide algo de los puntos anteriores, indicas que la revisión humana es obligatoria.
5. Si falta conocimiento aprobado o hay incertidumbre, no completas con conocimiento general ni inventas.
6. Máximo dos preguntas de alto valor por turno. Normalmente 2–4 frases; hasta 6 para briefings complejos.

MODO CONSULTOR PARA PROYECTOS COMPLEJOS
- Considera complejo: varias zonas, talento/VIP, más de 10 personas, montaje temporal o varios servicios.
- Resume primero las zonas, usuarios y objetivos para demostrar comprensión.
- Da solo un planteamiento preliminar integral: distribución, privacidad, funcionalidad, confort,
  climatización, energía, comunicaciones, accesos, carga/descarga, montaje y desmontaje.
- No empieces recomendando un producto aislado. No introduzcas altavoces en una consulta general de Green Room.
- Pregunta primero fecha y localización; después dimensiones/plano, interior/exterior, accesos, horarios y rider.
- Cuando el briefing sea útil, explica que el equipo humano lo revisará y continuará la conversación.
- Para implantaciones complejas, una visita técnica permite validar medidas, accesos, energía y logística.

PROCESO DE CUALIFICACIÓN
Recoge gradualmente: nombre, empresa/productora, contacto, tipo de proyecto, servicios, fecha/duración,
localización, número de personas, logística y descripción. WhatsApp ya aporta un canal de contacto válido.

DATOS GENERALES
Web: https://purocuento.es · Email: operativa@purocuento.es · Teléfono: +34 657 654 417.
Horario: lunes a viernes 09:00–14:00 y 16:00–18:00 (Madrid); fin de semana cerrado.

BRIEFING ESTRUCTURADO ACTUAL
{project_context}

CONOCIMIENTO RECUPERADO Y APROBADO
{source_context}

Consulta actual: {query}
"""


def _notification_target():
    if not OWNER_WHATSAPP_NUMBER:
        return ""
    return OWNER_WHATSAPP_NUMBER if OWNER_WHATSAPP_NUMBER.startswith("whatsapp:") else f"whatsapp:{OWNER_WHATSAPP_NUMBER}"


def notify_owner(phone, message, reason="Revisión humana requerida", force=False):
    target = _notification_target()
    if not target:
        store.audit(phone, "system", "NOTIFICATION_SKIPPED", "OWNER_WHATSAPP_NUMBER no configurado")
        return False
    conversation = store.conversation(phone) or {}
    if conversation.get("notified_at") and not force:
        return True
    dashboard_url = request.url_root.rstrip("/") + "/dashboard"
    project = store.project(phone) or {}
    flags = " · ".join(project.get("risk_reasons", []))
    body = (
        "🔔 PuroCuento · revisión humana\n"
        f"Cliente: {phone.replace('whatsapp:', '')}\n"
        f"Motivo: {reason}\n"
        f"Proyecto: {project.get('project_type') or 'Por definir'}"
        + (f" · {flags}" if flags else "")
        + f"\nÚltimo mensaje: {message[:420]}\nAbrir bandeja: {dashboard_url}"
    )
    try:
        status_url = request.url_root.rstrip("/") + "/twilio/message-status"
        sent = twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER, to=target, body=body, status_callback=status_url
        )
        store.save_notification(sent.sid, phone, target, sent.status or "queued")
        data = store.conversation(phone)
        data["notified_at"] = now_iso()
        store.upsert_conversation(**data)
        store.audit(phone, "system", "OWNER_NOTIFIED", f"{target} · SID {sent.sid} · {reason}")
        logger.info("Owner notification accepted: sid=%s status=%s", sent.sid, sent.status)
        return True
    except Exception as exc:
        store.audit(phone, "system", "NOTIFICATION_FAILED", str(exc)[:300])
        logger.error("Owner notification failed: %s", exc)
        return False


def trigger_handoff(phone, incoming, language, reason):
    set_state(phone, WAITING_FOR_HUMAN, "ai", reason)
    project = store.project(phone) or {"phone": phone, "description": incoming}
    project["status"] = "HUMAN_REVIEW"
    project["updated_at"] = now_iso()
    store.save_project(project)
    notify_owner(phone, incoming, reason)
    return HANDOFF_MESSAGE_EN if language == "en" else HANDOFF_MESSAGE_ES


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    sender = request.values.get("From", "").strip()
    incoming = request.values.get("Body", "").strip()
    sid = request.values.get("MessageSid", "").strip()
    if not sender or not incoming or not mark_processed(sid):
        return str(MessagingResponse())
    language = detect_language(incoming)
    try:
        ensure_conversation(sender, language)
        if get_state(sender) == AI_ACTIVE and store.should_start_new_project(sender, incoming):
            store.start_new_project(sender, "ai", "Cambio claro de proyecto detectado en WhatsApp")
        log_message(sender, "inbound", "customer", incoming, external_id=sid or None, twilio_sid=sid, delivery_status="received")
        state = get_state(sender)
        if state in {WAITING_FOR_HUMAN, HUMAN_ACTIVE, CLOSED}:
            store.audit(sender, "system", "AI_SUPPRESSED", f"Estado {state}")
            if state in {WAITING_FOR_HUMAN, HUMAN_ACTIVE}:
                notify_owner(sender, incoming, "Nuevo mensaje en conversación derivada", force=True)
            return str(MessagingResponse())

        if is_greeting(incoming):
            reply = GREETING_RESPONSE_EN if language == "en" else GREETING_RESPONSE_ES
        else:
            project = store.update_project_from_message(sender, incoming)
            mandatory = check_handoff(incoming, language)
            if mandatory:
                reply = trigger_handoff(sender, incoming, language, "Precio, disponibilidad o compromiso")
            elif project.get("high_value") and is_qualified_lead(sender, incoming):
                reply = trigger_handoff(sender, incoming, language, "Proyecto de alto valor cualificado")
            elif is_qualified_lead(sender, incoming):
                reply = trigger_handoff(sender, incoming, language, "Briefing cualificado")
            else:
                docs = store.search_knowledge(incoming, 5)
                # Commercial policy and contact details are always relevant guardrails.
                anchors = {"quotes-policy", "company-contact"}
                for item in store.list_knowledge(False):
                    if item["id"] in anchors and item["status"] == "approved" and all(d["id"] != item["id"] for d in docs):
                        docs.append(item)
                history = store.history(sender, 24)
                response = openai_client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "system", "content": get_system_prompt(incoming, docs, project)}] + history,
                    max_tokens=500,
                    temperature=0.2,
                )
                reply = response.choices[0].message.content.strip()
                if unsafe_reply(reply):
                    store.audit(sender, "safety", "AI_REPLY_BLOCKED", reply[:500])
                    reply = trigger_handoff(sender, incoming, language, "Control de seguridad: respuesta requiere validación")
                elif reply_promises_handoff(reply):
                    set_state(sender, WAITING_FOR_HUMAN, "ai", "La respuesta prometió seguimiento humano")
                    notify_owner(sender, incoming, "Seguimiento humano prometido")

        log_message(sender, "outbound", "ai", reply)
        logger.info("Reply to %s: %s", sender, reply[:100])
    except Exception as exc:
        logger.exception("WhatsApp processing failed: %s", exc)
        reply = (
            "Lo siento, no puedo validar esta solicitud con seguridad en este momento. "
            "La he transferido al equipo de PuroCuento para revisión humana."
        )
        try:
            set_state(sender, WAITING_FOR_HUMAN, "system", "Error del asistente")
            log_message(sender, "outbound", "system", reply)
            notify_owner(sender, incoming, "Error del asistente")
        except Exception:
            logger.exception("Emergency handoff also failed")
    twiml = MessagingResponse()
    twiml.message(reply)
    return str(twiml)


@app.route("/twilio/message-status", methods=["POST"])
def twilio_message_status():
    sid = request.values.get("MessageSid", "")
    status = request.values.get("MessageStatus", "")
    error = request.values.get("ErrorCode", "")
    with store.connect() as conn:
        notification = conn.execute("SELECT * FROM notifications WHERE sid=?", (sid,)).fetchone()
        conn.execute("UPDATE messages SET delivery_status=? WHERE twilio_sid=?", (status, sid))
    if notification:
        n = dict(notification)
        store.save_notification(sid, n["phone"], n["recipient"], status, error)
        if status in {"failed", "undelivered"}:
            conversation = store.conversation(n["phone"])
            if conversation:
                conversation["notified_at"] = ""
                store.upsert_conversation(**conversation)
        store.audit(n["phone"], "twilio", "NOTIFICATION_STATUS", f"{status}" + (f" · error {error}" if error else ""))
    logger.info("Twilio status: sid=%s status=%s error=%s", sid, status, error)
    return "", 204


def dashboard_auth(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        expected_user = os.environ.get("DASHBOARD_USERNAME", "admin")
        expected_password = os.environ.get("DASHBOARD_PASSWORD", "")
        auth = request.authorization
        valid = bool(
            expected_password and auth
            and hmac.compare_digest(auth.username or "", expected_user)
            and hmac.compare_digest(auth.password or "", expected_password)
        )
        if not valid:
            return Response("Authentication required", 401, {"WWW-Authenticate": 'Basic realm="PuroCuento"'})
        return fn(*args, **kwargs)
    return wrapped


@app.route("/dashboard")
@dashboard_auth
def dashboard():
    return Response(DASHBOARD_HTML, mimetype="text/html")


@app.route("/dashboard/api/summary")
@dashboard_auth
def dashboard_summary():
    return jsonify(store.summary())


@app.route("/dashboard/api/conversations")
@dashboard_auth
def dashboard_conversations():
    return jsonify(store.list_conversations())


@app.route("/dashboard/api/thread")
@dashboard_auth
def dashboard_thread():
    phone = request.args.get("phone", "")
    if not phone:
        return jsonify(error="Falta el contacto"), 400
    messages = store.thread(phone)
    return jsonify(conversation=store.conversation(phone), project=store.project(phone), messages=messages)


@app.route("/dashboard/api/state", methods=["POST"])
@dashboard_auth
def dashboard_state():
    data = request.get_json(silent=True) or {}
    phone, state = data.get("phone", ""), data.get("state", "")
    if not phone or state not in VALID_STATES:
        return jsonify(error="Contacto o estado no válido"), 400
    set_state(phone, state, "Equipo PuroCuento", "Cambio manual desde la bandeja")
    return jsonify(ok=True, state=state)


@app.route("/dashboard/api/new-project", methods=["POST"])
@dashboard_auth
def dashboard_new_project():
    data = request.get_json(silent=True) or {}
    phone = data.get("phone", "").strip()
    if not phone:
        return jsonify(error="Falta el contacto"), 400
    store.start_new_project(phone, "Equipo PuroCuento", "Nuevo proyecto creado manualmente")
    return jsonify(ok=True, state=AI_ACTIVE)


@app.route("/dashboard/api/send", methods=["POST"])
@dashboard_auth
def dashboard_send():
    data = request.get_json(silent=True) or {}
    phone, body = data.get("phone", "").strip(), data.get("body", "").strip()
    if not phone or not body:
        return jsonify(error="Falta contacto o mensaje"), 400
    if get_state(phone) != HUMAN_ACTIVE:
        return jsonify(error="Toma el control de la conversación antes de responder"), 409
    try:
        status_url = request.url_root.rstrip("/") + "/twilio/message-status"
        sent = twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER, to=phone, body=body, status_callback=status_url
        )
        log_message(phone, "outbound", "human", body, twilio_sid=sent.sid,
                    delivery_status=sent.status or "queued")
        store.audit(phone, "Equipo PuroCuento", "HUMAN_MESSAGE_SENT", f"SID {sent.sid}")
        return jsonify(ok=True, sid=sent.sid, status=sent.status)
    except Exception as exc:
        store.audit(phone, "Equipo PuroCuento", "HUMAN_MESSAGE_FAILED", str(exc)[:300])
        logger.error("Human reply failed: %s", exc)
        return jsonify(error="Twilio no pudo enviar el mensaje; revisa la ventana de WhatsApp"), 502


@app.route("/dashboard/api/projects")
@dashboard_auth
def dashboard_projects():
    return jsonify(store.list_projects())


@app.route("/dashboard/api/knowledge", methods=["GET", "POST"])
@dashboard_auth
def dashboard_knowledge():
    if request.method == "GET":
        return jsonify(store.list_knowledge(True))
    data = request.get_json(silent=True) or {}
    if data.get("status") not in {"draft", "approved", "archived"}:
        return jsonify(error="Estado de conocimiento no válido"), 400
    if len(data.get("title", "").strip()) < 3 or len(data.get("content", "").strip()) < 30:
        return jsonify(error="Añade un título y contenido suficientemente detallado"), 400
    if data.get("status") == "approved" and not data.get("source_url", "").startswith("https://"):
        return jsonify(error="El contenido aprobado necesita una fuente HTTPS verificable"), 400
    item = store.save_knowledge(data)
    store.audit("", "Equipo PuroCuento", "KNOWLEDGE_UPDATED",
                f"{item['title']} · {item['status']} · v{item['version']}")
    return jsonify(item)


@app.route("/dashboard/api/audit")
@dashboard_auth
def dashboard_audit():
    return jsonify(store.list_audit(250))


@app.route("/healthz")
def healthz():
    try:
        summary = store.summary()
        return jsonify(status="ok", service="purocuento-support", database="ready",
                       storage=summary["storage"]), 200
    except Exception:
        return jsonify(status="error", service="purocuento-support", database="unavailable"), 503


# Old restaurant endpoints are intentionally closed.  They must never touch the
# Du Liban spreadsheet from the PuroCuento service.
@app.route("/voice-reservation", methods=["POST"])
@app.route("/confirm-reservation", methods=["POST"])
@app.route("/get-reservations", methods=["GET"])
@app.route("/get-orders", methods=["GET"])
@app.route("/get-customers", methods=["GET"])
@app.route("/get-tables", methods=["GET"])
@app.route("/get-pending-actions", methods=["GET"])
@app.route("/update-table", methods=["POST"])
@app.route("/resolve-pending-action", methods=["POST"])
@app.route("/update-reservation", methods=["POST"])
def legacy_disabled():
    return jsonify(status="disabled", message="Legacy restaurant endpoint is not part of PuroCuento."), 410


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
