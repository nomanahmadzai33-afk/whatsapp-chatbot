import json
import logging
import os
import re
import sqlite3
import threading
import unicodedata
import uuid
from datetime import datetime

import pytz

from knowledge_seed import SEED_KNOWLEDGE

logger = logging.getLogger(__name__)
MADRID = pytz.timezone("Europe/Madrid")


def now_iso():
    return datetime.now(MADRID).isoformat(timespec="seconds")


def _json(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


class EventMirror:
    """Append-only durable backup in a dedicated Google spreadsheet.

    Railway's local SQLite file is the fast operational cache.  Every mutation
    is also written as an idempotent event.  A fresh container replays those
    events, so conversations and knowledge survive deploys without touching any
    legacy Du Liban worksheets.
    """

    def __init__(self, client_factory):
        self.client_factory = client_factory
        self.enabled = os.environ.get("ENABLE_SHEETS_PERSISTENCE", "false").lower() == "true"
        self.book_name = os.environ.get("PUROCUENTO_DATA_SHEET", "PuroCuento Support Platform")
        self._worksheet = None
        self._lock = threading.Lock()

    def worksheet(self):
        if not self.enabled:
            return None
        if self._worksheet is not None:
            return self._worksheet
        client = self.client_factory() if self.client_factory else None
        if not client:
            return None
        try:
            book = client.open(self.book_name)
        except Exception:
            book = client.create(self.book_name)
        try:
            sheet = book.worksheet("EventLog")
        except Exception:
            sheet = book.add_worksheet(title="EventLog", rows=5000, cols=5)
            sheet.append_row(["EventID", "Timestamp", "Kind", "EntityKey", "PayloadJSON"])
        self._worksheet = sheet
        return sheet

    def append(self, event_id, kind, key, payload):
        sheet = self.worksheet()
        if not sheet:
            return False
        try:
            with self._lock:
                sheet.append_row([
                    event_id,
                    now_iso(),
                    kind,
                    key,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ])
            return True
        except Exception as exc:
            logger.error("Persistent event mirror failed: %s", exc)
            return False

    def events(self):
        sheet = self.worksheet()
        if not sheet:
            return []
        try:
            records = sheet.get_all_records()
            output = []
            for row in records:
                payload = _json(row.get("PayloadJSON"), {})
                output.append((str(row.get("EventID", "")), str(row.get("Kind", "")), payload))
            return output
        except Exception as exc:
            logger.error("Persistent event restore failed: %s", exc)
            return []


class Store:
    def __init__(self, path, client_factory=None):
        self.path = path
        self.mirror = EventMirror(client_factory)
        self._write_lock = threading.RLock()

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _columns(self, conn, table):
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    def _add_column(self, conn, table, definition):
        name = definition.split()[0]
        if name not in self._columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")

    def init(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
              CREATE TABLE IF NOT EXISTS conversations (
                phone TEXT PRIMARY KEY,
                state TEXT NOT NULL DEFAULT 'AI_ACTIVE',
                language TEXT DEFAULT 'es',
                assigned_to TEXT DEFAULT '',
                priority TEXT DEFAULT 'NORMAL',
                unread INTEGER DEFAULT 0,
                handoff_reason TEXT DEFAULT '',
                notified_at TEXT DEFAULT '',
                updated_at TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id TEXT UNIQUE,
                phone TEXT NOT NULL,
                direction TEXT NOT NULL,
                sender TEXT NOT NULL,
                body TEXT NOT NULL,
                twilio_sid TEXT DEFAULT '',
                delivery_status TEXT DEFAULT '',
                created_at TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS processed_messages (
                sid TEXT PRIMARY KEY, created_at TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS projects (
                phone TEXT PRIMARY KEY,
                name TEXT DEFAULT '', company TEXT DEFAULT '', contact TEXT DEFAULT '',
                project_type TEXT DEFAULT '', services_json TEXT DEFAULT '[]', dates TEXT DEFAULT '',
                location TEXT DEFAULT '', people INTEGER, logistics TEXT DEFAULT '',
                description TEXT DEFAULT '', missing_json TEXT DEFAULT '[]',
                high_value INTEGER DEFAULT 0, risk_reasons_json TEXT DEFAULT '[]',
                status TEXT DEFAULT 'QUALIFYING', updated_at TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS knowledge (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
                content TEXT NOT NULL, source_url TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'draft',
                version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS audit_events (
                id TEXT PRIMARY KEY, phone TEXT DEFAULT '', actor TEXT NOT NULL,
                action TEXT NOT NULL, details TEXT DEFAULT '', created_at TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS notifications (
                sid TEXT PRIMARY KEY, phone TEXT NOT NULL, recipient TEXT NOT NULL,
                status TEXT NOT NULL, error_code TEXT DEFAULT '', created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS restored_events (
                event_id TEXT PRIMARY KEY, restored_at TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS archived_projects (
                project_id TEXT PRIMARY KEY,
                phone TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                archived_at TEXT NOT NULL
              );
            """)
            # Safe upgrades from the earlier demo schema.
            for definition in (
                "assigned_to TEXT DEFAULT ''", "priority TEXT DEFAULT 'NORMAL'",
                "unread INTEGER DEFAULT 0", "handoff_reason TEXT DEFAULT ''",
                "notified_at TEXT DEFAULT ''", "active_project_id TEXT DEFAULT ''",
            ):
                self._add_column(conn, "conversations", definition)
            for definition in (
                "external_id TEXT", "twilio_sid TEXT DEFAULT ''", "delivery_status TEXT DEFAULT ''",
                "project_id TEXT DEFAULT ''",
            ):
                self._add_column(conn, "messages", definition)
            self._add_column(conn, "projects", "project_id TEXT DEFAULT ''")
            for row in conn.execute("SELECT phone,project_id FROM projects").fetchall():
                project_id = row["project_id"] or str(uuid.uuid4())
                if not row["project_id"]:
                    conn.execute("UPDATE projects SET project_id=? WHERE phone=?", (project_id, row["phone"]))
                conn.execute("UPDATE conversations SET active_project_id=? WHERE phone=? AND (active_project_id='' OR active_project_id IS NULL)",
                             (project_id, row["phone"]))
                conn.execute("UPDATE messages SET project_id=? WHERE phone=? AND (project_id='' OR project_id IS NULL)",
                             (project_id, row["phone"]))
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_external_id ON messages(external_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_phone_id ON messages(phone,id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at DESC)")
            for item in SEED_KNOWLEDGE:
                conn.execute(
                    """INSERT OR IGNORE INTO knowledge(id,title,category,content,source_url,status,version,updated_at)
                    VALUES(?,?,?,?,?,'approved',1,?)""",
                    (item["id"], item["title"], item["category"], item["content"], item["source_url"], now_iso()),
                )

    def _event(self, kind, key, payload, event_id=None):
        event_id = event_id or str(uuid.uuid4())
        self.mirror.append(event_id, kind, key, payload)
        return event_id

    def restore(self):
        restored = 0
        for event_id, kind, payload in self.mirror.events():
            if not event_id:
                continue
            with self.connect() as conn:
                if conn.execute("SELECT 1 FROM restored_events WHERE event_id=?", (event_id,)).fetchone():
                    continue
            try:
                self._apply_event(kind, payload)
                with self.connect() as conn:
                    conn.execute("INSERT OR IGNORE INTO restored_events VALUES(?,?)", (event_id, now_iso()))
                restored += 1
            except Exception as exc:
                logger.error("Could not restore event %s (%s): %s", event_id, kind, exc)
        logger.info("Persistent store restored %s events", restored)
        return restored

    def _apply_event(self, kind, p):
        if kind == "conversation":
            self.upsert_conversation(mirror=False, **p)
        elif kind == "message":
            self.add_message(mirror=False, **p)
        elif kind == "project":
            self.save_project(p, mirror=False)
        elif kind == "knowledge":
            self.save_knowledge(p, mirror=False)
        elif kind == "audit":
            self.audit(mirror=False, **p)
        elif kind == "notification":
            self.save_notification(mirror=False, **p)
        elif kind == "processed":
            with self.connect() as conn:
                conn.execute("INSERT OR IGNORE INTO processed_messages(sid,created_at) VALUES(?,?)", (p["sid"], p["created_at"]))

    def upsert_conversation(self, phone, state="AI_ACTIVE", language="es", assigned_to="",
                            priority="NORMAL", unread=0, handoff_reason="", notified_at="",
                            active_project_id="", updated_at=None, mirror=True):
        updated_at = updated_at or now_iso()
        payload = dict(phone=phone, state=state, language=language, assigned_to=assigned_to,
                       priority=priority, unread=unread, handoff_reason=handoff_reason,
                       notified_at=notified_at, active_project_id=active_project_id or "",
                       updated_at=updated_at)
        with self.connect() as conn:
            conn.execute("""INSERT INTO conversations(phone,state,language,assigned_to,priority,unread,
              handoff_reason,notified_at,active_project_id,updated_at) VALUES(:phone,:state,:language,:assigned_to,:priority,
              :unread,:handoff_reason,:notified_at,:active_project_id,:updated_at)
              ON CONFLICT(phone) DO UPDATE SET state=excluded.state,language=excluded.language,
              assigned_to=excluded.assigned_to,priority=excluded.priority,unread=excluded.unread,
              handoff_reason=excluded.handoff_reason,notified_at=excluded.notified_at,
              active_project_id=excluded.active_project_id,
              updated_at=excluded.updated_at""", payload)
        if mirror:
            self._event("conversation", phone, payload)

    def ensure_conversation(self, phone, language="es"):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE phone=?", (phone,)).fetchone()
        if row:
            if language and row["language"] != language:
                data = dict(row)
                data["language"] = language
                data["updated_at"] = now_iso()
                self.upsert_conversation(**data)
            return
        self.upsert_conversation(phone=phone, language=language)

    def conversation(self, phone):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE phone=?", (phone,)).fetchone()
        return dict(row) if row else None

    def set_state(self, phone, state, actor="system", reason=""):
        self.ensure_conversation(phone)
        data = self.conversation(phone)
        old = data["state"]
        data["state"] = state
        data["updated_at"] = now_iso()
        if state == "HUMAN_ACTIVE":
            data["assigned_to"] = actor if actor not in {"system", "ai"} else "Equipo PuroCuento"
        elif state == "AI_ACTIVE":
            data["assigned_to"] = ""
            data["notified_at"] = ""
            data["handoff_reason"] = ""
        if reason:
            data["handoff_reason"] = reason
        self.upsert_conversation(**data)
        self.audit(phone, actor, "STATE_CHANGED", f"{old} → {state}" + (f" · {reason}" if reason else ""))

    def set_priority(self, phone, priority):
        self.ensure_conversation(phone)
        data = self.conversation(phone)
        data["priority"] = priority
        data["updated_at"] = now_iso()
        self.upsert_conversation(**data)

    def add_message(self, phone, direction, sender, body, external_id=None, twilio_sid="",
                    delivery_status="", project_id=None, created_at=None, mirror=True):
        self.ensure_conversation(phone)
        conversation = self.conversation(phone) or {}
        payload = dict(phone=phone, direction=direction, sender=sender, body=body,
                       external_id=external_id or str(uuid.uuid4()), twilio_sid=twilio_sid or "",
                       delivery_status=delivery_status or "",
                       project_id=project_id if project_id is not None else conversation.get("active_project_id", ""),
                       created_at=created_at or now_iso())
        with self.connect() as conn:
            conn.execute("""INSERT OR IGNORE INTO messages(external_id,phone,direction,sender,body,twilio_sid,
              delivery_status,project_id,created_at) VALUES(:external_id,:phone,:direction,:sender,:body,:twilio_sid,
              :delivery_status,:project_id,:created_at)""", payload)
            conv = dict(conn.execute("SELECT * FROM conversations WHERE phone=?", (phone,)).fetchone())
        conv["updated_at"] = payload["created_at"]
        if direction == "inbound":
            conv["unread"] = 1
        self.upsert_conversation(mirror=mirror, **conv)
        if mirror:
            self._event("message", payload["external_id"], payload)
        return payload["external_id"]

    def history(self, phone, limit=30):
        conversation = self.conversation(phone) or {}
        project_id = conversation.get("active_project_id", "")
        with self.connect() as conn:
            if project_id:
                rows = conn.execute("""SELECT sender,body FROM messages WHERE phone=? AND project_id=?
                  AND sender IN ('customer','ai') ORDER BY id DESC LIMIT ?""",
                                    (phone, project_id, limit)).fetchall()
            else:
                rows = conn.execute("""SELECT sender,body FROM messages WHERE phone=? AND sender IN ('customer','ai')
                  ORDER BY id DESC LIMIT ?""", (phone, limit)).fetchall()
        rows = list(reversed(rows))
        return [{"role": "user" if r["sender"] == "customer" else "assistant", "content": r["body"]} for r in rows]

    def mark_processed(self, sid):
        if not sid:
            return True
        created = now_iso()
        try:
            with self.connect() as conn:
                conn.execute("INSERT INTO processed_messages(sid,created_at) VALUES(?,?)", (sid, created))
            self._event("processed", sid, {"sid": sid, "created_at": created})
            return True
        except sqlite3.IntegrityError:
            return False

    def list_conversations(self):
        with self.connect() as conn:
            rows = conn.execute("""SELECT c.*,
              (SELECT body FROM messages m WHERE m.phone=c.phone ORDER BY id DESC LIMIT 1) last_message,
              (SELECT created_at FROM messages m WHERE m.phone=c.phone ORDER BY id DESC LIMIT 1) last_message_at,
              p.name,p.company,p.project_type,p.services_json,p.high_value,p.status project_status
              FROM conversations c LEFT JOIN projects p ON p.phone=c.phone ORDER BY c.updated_at DESC""").fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["services"] = _json(item.pop("services_json", "[]"), [])
            output.append(item)
        return output

    def thread(self, phone):
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM messages WHERE phone=? ORDER BY id", (phone,)).fetchall()
            conn.execute("UPDATE conversations SET unread=0 WHERE phone=?", (phone,))
        return [dict(row) for row in rows]

    def save_project(self, project, mirror=True):
        defaults = dict(name="", company="", contact="", project_type="", services=[], dates="",
                        location="", people=None, logistics="", description="", missing=[], high_value=0,
                        risk_reasons=[], status="QUALIFYING", updated_at=now_iso())
        defaults.update(project)
        p = defaults
        payload = dict(phone=p["phone"], name=p["name"], company=p["company"], contact=p["contact"],
                       project_type=p["project_type"], services=p.get("services", []), dates=p["dates"],
                       location=p["location"], people=p["people"], logistics=p["logistics"],
                       description=p["description"], missing=p.get("missing", []),
                       high_value=int(bool(p["high_value"])), risk_reasons=p.get("risk_reasons", []),
                       status=p["status"], project_id=p.get("project_id") or str(uuid.uuid4()),
                       updated_at=p.get("updated_at") or now_iso())
        dbp = dict(payload)
        dbp["services_json"] = json.dumps(payload["services"], ensure_ascii=False)
        dbp["missing_json"] = json.dumps(payload["missing"], ensure_ascii=False)
        dbp["risk_reasons_json"] = json.dumps(payload["risk_reasons"], ensure_ascii=False)
        with self.connect() as conn:
            conn.execute("""INSERT INTO projects(phone,name,company,contact,project_type,services_json,dates,
              location,people,logistics,description,missing_json,high_value,risk_reasons_json,status,project_id,updated_at)
              VALUES(:phone,:name,:company,:contact,:project_type,:services_json,:dates,:location,:people,
              :logistics,:description,:missing_json,:high_value,:risk_reasons_json,:status,:project_id,:updated_at)
              ON CONFLICT(phone) DO UPDATE SET name=excluded.name,company=excluded.company,
              contact=excluded.contact,project_type=excluded.project_type,services_json=excluded.services_json,
              dates=excluded.dates,location=excluded.location,people=excluded.people,
              logistics=excluded.logistics,description=excluded.description,missing_json=excluded.missing_json,
              high_value=excluded.high_value,risk_reasons_json=excluded.risk_reasons_json,
              status=excluded.status,project_id=excluded.project_id,updated_at=excluded.updated_at""", dbp)
            conn.execute("UPDATE conversations SET active_project_id=? WHERE phone=?",
                         (payload["project_id"], payload["phone"]))
            conn.execute("""UPDATE messages SET project_id=? WHERE phone=?
              AND (project_id='' OR project_id IS NULL)""",
                         (payload["project_id"], payload["phone"]))
        if mirror:
            self._event("project", payload["phone"], payload)
        return payload

    def project(self, phone):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE phone=?", (phone,)).fetchone()
        if not row:
            return None
        p = dict(row)
        p["services"] = _json(p.pop("services_json"), [])
        p["missing"] = _json(p.pop("missing_json"), [])
        p["risk_reasons"] = _json(p.pop("risk_reasons_json"), [])
        return p

    def list_projects(self):
        with self.connect() as conn:
            phones = [r[0] for r in conn.execute("SELECT phone FROM projects ORDER BY high_value DESC,updated_at DESC")]
            archived = [dict(r) for r in conn.execute(
                "SELECT payload_json,archived_at FROM archived_projects ORDER BY archived_at DESC"
            ).fetchall()]
        active = [self.project(phone) for phone in phones]
        historical = []
        for row in archived:
            item = _json(row["payload_json"], {})
            item["archived_at"] = row["archived_at"]
            item["active"] = False
            historical.append(item)
        for item in active:
            item["active"] = True
        return active + historical

    def start_new_project(self, phone, actor="system", reason="Nueva consulta detectada"):
        """Archive the current project and start an isolated project context."""
        self.ensure_conversation(phone)
        current = self.project(phone)
        if current:
            archived = dict(current)
            archived["status"] = "ARCHIVED"
            with self.connect() as conn:
                conn.execute("""INSERT OR REPLACE INTO archived_projects(project_id,phone,payload_json,archived_at)
                  VALUES(?,?,?,?)""", (current["project_id"], phone,
                                        json.dumps(archived, ensure_ascii=False), now_iso()))
                conn.execute("DELETE FROM projects WHERE phone=?", (phone,))
        conversation = self.conversation(phone)
        conversation.update(state="AI_ACTIVE", assigned_to="", priority="NORMAL", unread=0,
                            handoff_reason="", notified_at="", active_project_id="", updated_at=now_iso())
        self.upsert_conversation(**conversation)
        self.audit(phone, actor, "NEW_PROJECT_STARTED", reason)
        return True

    @staticmethod
    def infer_services(text):
        norm = Store._normalize(text)
        service_map = {
            "Green Room / Camerinos": ["green room", "camerino", "vestuario", "maquillaje"],
            "Video village / Video Van": ["video village", "video van", "production van", "monitor"],
            "Mobiliario y decoración": ["mobiliario", "sofa", "silla", "mesa", "decoracion"],
            "Espacios temporales": ["carpa", "blackwall", "pipe", "drape", "espacio temporal"],
            "Climatización / Energía": ["climatizacion", "ventilacion", "electricidad", "energia", "carga"],
            "Transporte / Montaje": ["transporte", "montaje", "desmontaje", "carga y descarga"],
            "Hospitality / VIP": ["hospitality", "vip", "craft", "talento", "futbolista"],
            "Sonido / Audiovisual": ["altavoz", "sonido", "audio", "microfono", "jbl", "speaker"],
            "Set Support": ["set support", "unit manager", "soporte en set"],
            "Oficina de producción": ["oficina", "check-in", "coordinacion"],
        }
        return {label for label, tokens in service_map.items() if any(token in norm for token in tokens)}

    def should_start_new_project(self, phone, text):
        current = self.project(phone)
        if not current:
            return False
        norm = self._normalize(text).strip()
        explicit = (
            r"\b(?:nuevo|otro) proyecto\b", r"\bnueva consulta\b", r"\bempezar de (?:nuevo|cero)\b",
            r"\bnew project\b", r"\banother project\b", r"\bstart over\b",
        )
        if any(re.search(pattern, norm) for pattern in explicit):
            return True
        new_services = self.infer_services(text)
        old_services = set(current.get("services", []))
        greeting = re.match(r"^(?:hola|buenos dias|buenas tardes|hello|hi|hey)\b", norm)
        substantial = len(norm) >= 70
        return bool(greeting and substantial and new_services and old_services and new_services.isdisjoint(old_services))

    def update_project_from_message(self, phone, text):
        p = self.project(phone) or {"phone": phone}
        combined = f"{p.get('description','')}\n{text}".strip()[-5000:]
        norm = self._normalize(combined)

        email = re.search(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b", combined)
        explicit_phone = re.search(r"(?<!\d)(?:\+?34[\s-]?)?[6-9](?:[\s-]?\d){8}(?!\d)", combined)
        if email or explicit_phone:
            p["contact"] = " · ".join(x for x in [email.group(0) if email else "", explicit_phone.group(0) if explicit_phone else ""] if x)
        name = re.search(r"(?:me llamo|soy)\s+([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÜÑáéíóúüñ-]{1,30}(?:\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÜÑáéíóúüñ-]{1,30})?)", combined)
        if name:
            p["name"] = name.group(1).strip()
        company = re.search(r"(?:empresa|productora|agencia)\s+(?:es|se llama)?\s*([\wÁÉÍÓÚÜÑáéíóúüñ& .-]{2,50})", combined, re.I)
        if company:
            p["company"] = company.group(1).strip(" .")

        types = [("rodaje", "Rodaje"), ("produccion", "Producción audiovisual"),
                 ("evento", "Evento"), ("publicidad", "Publicidad"), ("sesion", "Sesión")]
        for token, label in types:
            if token in norm:
                p["project_type"] = label
                break

        services = set(p.get("services", []))
        services.update(self.infer_services(combined))
        p["services"] = sorted(services)

        people = [int(m.group(1)) for m in re.finditer(r"\b(\d{1,4})\s*(?:personas|pax|futbolistas|talentos|people|guests?)\b", norm)]
        if people:
            p["people"] = max(people)
        date = re.search(r"\b(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{1,2}\s+de\s+(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre))\b", norm)
        if date:
            p["dates"] = date.group(0)
        locations = ["madrid", "alcobendas", "la moraleja", "barcelona", "valencia", "sevilla", "malaga", "bilbao"]
        place = next((x for x in locations if x in norm), "")
        if place:
            p["location"] = place.title()
        logistics = [x for x in ["interior" if "interior" in norm else "", "exterior" if "exterior" in norm else "",
                                  "montaje" if "montaje" in norm else "", "desmontaje" if "desmontaje" in norm else "",
                                  "transporte" if "transporte" in norm else ""] if x]
        if logistics:
            p["logistics"] = ", ".join(dict.fromkeys(logistics))
        p["description"] = combined

        reasons = []
        if (p.get("people") or 0) >= 10:
            reasons.append("10+ personas")
        if len(p["services"]) >= 2:
            reasons.append("múltiples servicios")
        if any(x in norm for x in ["vip", "futbolista", "talento", "agencia", "cliente"]):
            reasons.append("talento / cliente / VIP")
        if any(x in norm for x in ["dos zonas", "2 zonas", "varias zonas", "green room", "backstage"]):
            reasons.append("implantación compleja")
        p["risk_reasons"] = list(dict.fromkeys(reasons))
        p["high_value"] = int(bool(reasons and (len(reasons) >= 2 or "10+ personas" in reasons)))
        missing = []
        for key, label in (("name", "nombre"), ("company", "empresa/productora"), ("project_type", "tipo de proyecto"),
                           ("services", "servicios"), ("dates", "fecha/duración"), ("location", "localización")):
            if not p.get(key):
                missing.append(label)
        p["missing"] = missing
        p["status"] = "HUMAN_REVIEW" if p["high_value"] and len(missing) <= 3 else p.get("status", "QUALIFYING")
        p["updated_at"] = now_iso()
        saved = self.save_project(p)
        if saved["high_value"]:
            self.set_priority(phone, "HIGH")
        return saved

    @staticmethod
    def _normalize(text):
        text = unicodedata.normalize("NFKD", text.lower())
        return "".join(ch for ch in text if not unicodedata.combining(ch))

    def list_knowledge(self, include_archived=True):
        query = "SELECT * FROM knowledge" + ("" if include_archived else " WHERE status!='archived'") + " ORDER BY category,title"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(query).fetchall()]

    def save_knowledge(self, item, mirror=True):
        item = dict(item)
        item.setdefault("id", str(uuid.uuid4()))
        item.setdefault("title", "Sin título")
        item.setdefault("category", "General")
        item.setdefault("content", "")
        item.setdefault("source_url", "")
        item.setdefault("status", "draft")
        item.setdefault("version", 1)
        item.setdefault("updated_at", now_iso())
        with self.connect() as conn:
            old = conn.execute("SELECT version FROM knowledge WHERE id=?", (item["id"],)).fetchone()
            if old and int(item.get("version", 1)) <= old["version"]:
                item["version"] = old["version"] + 1
            item["updated_at"] = now_iso()
            conn.execute("""INSERT INTO knowledge(id,title,category,content,source_url,status,version,updated_at)
              VALUES(:id,:title,:category,:content,:source_url,:status,:version,:updated_at)
              ON CONFLICT(id) DO UPDATE SET title=excluded.title,category=excluded.category,
              content=excluded.content,source_url=excluded.source_url,status=excluded.status,
              version=excluded.version,updated_at=excluded.updated_at""", item)
        if mirror:
            self._event("knowledge", item["id"], item)
        return item

    def search_knowledge(self, query, limit=5):
        stop = {"para", "como", "con", "que", "una", "unos", "las", "los", "del", "por", "and", "the", "for", "what", "need", "quiero", "necesito"}
        terms = {t for t in re.findall(r"[a-z0-9]+", self._normalize(query)) if len(t) > 2 and t not in stop}
        scored = []
        for item in self.list_knowledge(include_archived=False):
            if item["status"] != "approved":
                continue
            title = self._normalize(item["title"] + " " + item["category"])
            content = self._normalize(item["content"])
            score = sum(4 for t in terms if t in title) + sum(1 for t in terms if t in content)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["title"]))
        return [dict(item, relevance=score) for score, item in scored[:limit]]

    def audit(self, phone, actor, action, details="", event_id=None, created_at=None, mirror=True):
        payload = dict(event_id=event_id or str(uuid.uuid4()), phone=phone or "", actor=actor,
                       action=action, details=details or "", created_at=created_at or now_iso())
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO audit_events(id,phone,actor,action,details,created_at) VALUES(?,?,?,?,?,?)",
                         (payload["event_id"], payload["phone"], payload["actor"], payload["action"], payload["details"], payload["created_at"]))
        if mirror:
            self._event("audit", payload["event_id"], payload)
        return payload["event_id"]

    def list_audit(self, limit=200):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]

    def save_notification(self, sid, phone, recipient, status, error_code="", created_at=None,
                          updated_at=None, mirror=True):
        payload = dict(sid=sid, phone=phone, recipient=recipient, status=status,
                       error_code=error_code or "", created_at=created_at or now_iso(),
                       updated_at=updated_at or now_iso())
        with self.connect() as conn:
            old = conn.execute("SELECT created_at FROM notifications WHERE sid=?", (sid,)).fetchone()
            if old:
                payload["created_at"] = old["created_at"]
            conn.execute("""INSERT INTO notifications(sid,phone,recipient,status,error_code,created_at,updated_at)
              VALUES(:sid,:phone,:recipient,:status,:error_code,:created_at,:updated_at)
              ON CONFLICT(sid) DO UPDATE SET status=excluded.status,error_code=excluded.error_code,
              updated_at=excluded.updated_at""", payload)
        if mirror:
            self._event("notification", sid, payload)
        return payload

    def summary(self):
        with self.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            waiting = conn.execute("SELECT COUNT(*) FROM conversations WHERE state='WAITING_FOR_HUMAN'").fetchone()[0]
            human = conn.execute("SELECT COUNT(*) FROM conversations WHERE state='HUMAN_ACTIVE'").fetchone()[0]
            high = conn.execute("SELECT COUNT(*) FROM projects WHERE high_value=1").fetchone()[0]
            approved = conn.execute("SELECT COUNT(*) FROM knowledge WHERE status='approved'").fetchone()[0]
            failed = conn.execute("SELECT COUNT(*) FROM notifications WHERE status IN ('failed','undelivered')").fetchone()[0]
        storage = "persistent-volume" if not os.path.abspath(self.path).startswith("/tmp/") else "temporary"
        return {"conversations": total, "waiting": waiting, "human_active": human,
                "high_value": high, "approved_knowledge": approved, "notification_failures": failed,
                "persistent_mirror": self.mirror.enabled, "storage": storage}
