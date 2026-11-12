#!/usr/bin/env python3
"""Rule checks for owner-alert-cutoff. Mirrors the n8n Code node logic in Python.
These are not a live n8n run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
STOP = ["stop", "stopall", "unsubscribe", "cancel", "end", "quit", "revoke", "opt out", "optout"]
START = ["start", "unstop", "yes"]


def normalize_phone(raw: str) -> str:
    s = str(raw or "").strip()
    if not s or any(w in s.lower() for w in ("anonymous", "restricted", "unknown", "blocked")):
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if s.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    return ""


def keyword(text: str) -> str:
    t = " ".join(str(text or "").strip().lower().split())
    if not t:
        return ""
    first = "".join(ch for ch in t.split()[0] if ch.isalpha())
    compact = "".join(ch if ch.isalpha() or ch.isspace() else "" for ch in t).strip()
    if t in STOP or first in STOP or compact in STOP:
        return "stop"
    if t in START or compact in START:
        return "start"
    return ""


@dataclass
class Lead:
    status: str
    enrolled_at: datetime
    next_followup_at: datetime | None
    followups_sent: int = 0
    source: str = ""
    last_inbound: str = ""
    last_inbound_at: datetime | None = None
    last_skip_reason: str = ""


@dataclass
class Settings:
    business_name: str = "Sample Barber Shop"
    booking_link: str = "https://example.com/book"
    timezone: str = "America/New_York"
    text_from_hour: int = 8
    text_until_hour: int = 21
    followup_delay_minutes: int = 120
    max_followups: int = 1
    owner_webhook_url: str = ""
    owner_phone: str = ""
    alert_on_stop: bool = True
    from_number: str = "+15555550100"
    sms_api_url: str = "https://api.twilio.com/2010-04-01/Accounts/YOUR_ACCOUNT_SID/Messages.json"


@dataclass
class Store:
    leads: dict[str, Lead] = field(default_factory=dict)
    seen_sids: dict[str, datetime] = field(default_factory=dict)


def enroll(store: Store, settings: Settings, raw_phone: str, now: datetime, source: str = "test"):
    caller = normalize_phone(raw_phone)
    if not caller:
        return {"action": "skipped", "skip_reason": "no valid caller number (maybe blocked or private)", "caller_phone": ""}
    lead = store.leads.get(caller)
    if lead and lead.status == "opted_out":
        return {"action": "skipped_opted_out", "skip_reason": "caller opted out", "caller_phone": caller, "status": lead.status}
    if lead and lead.status == "waiting":
        return {
            "action": "already_waiting",
            "skip_reason": "already enrolled and waiting; left the existing follow-up time in place",
            "caller_phone": caller,
            "status": lead.status,
            "next_followup_at": lead.next_followup_at,
        }
    nxt = now + timedelta(minutes=settings.followup_delay_minutes)
    lead = Lead(status="waiting", enrolled_at=now, next_followup_at=nxt, source=source)
    store.leads[caller] = lead
    return {"action": "enrolled", "skip_reason": "", "caller_phone": caller, "status": "waiting", "next_followup_at": nxt}


def classify(store: Store, settings: Settings, raw_phone: str, text: str, now: datetime, message_sid: str = ""):
    caller = normalize_phone(raw_phone)
    if not caller:
        return {"action": "skipped", "skip_reason": "no valid caller number", "notify_owner": False, "caller_phone": ""}
    if message_sid and message_sid in store.seen_sids:
        return {"action": "duplicate", "skip_reason": "message_sid already processed", "notify_owner": False, "caller_phone": caller}
    if message_sid:
        store.seen_sids[message_sid] = now
    lead = store.leads.get(caller) or Lead(
        status="waiting", enrolled_at=now, next_followup_at=None, source="inbound-only"
    )
    kind = keyword(text)
    if kind == "stop":
        lead.status = "opted_out"
        lead.next_followup_at = None
        lead.last_inbound = text
        lead.last_inbound_at = now
        store.leads[caller] = lead
        return {"action": "opted_out", "status": "opted_out", "notify_owner": bool(settings.alert_on_stop), "caller_phone": caller}
    if kind == "start":
        if lead.status == "opted_out":
            lead.status = "completed"
        lead.last_inbound = text
        lead.last_inbound_at = now
        store.leads[caller] = lead
        return {"action": "opted_in", "status": lead.status, "notify_owner": False, "caller_phone": caller}
    if not str(text or "").strip():
        return {"action": "skipped", "skip_reason": "empty message body", "notify_owner": False, "caller_phone": caller, "status": lead.status}
    lead.status = "replied"
    lead.next_followup_at = None
    lead.last_inbound = text
    lead.last_inbound_at = now
    store.leads[caller] = lead
    return {"action": "replied", "status": "replied", "notify_owner": True, "caller_phone": caller, "text": text}


def next_open_window(settings: Settings, dt: datetime) -> datetime:
    start, end = settings.text_from_hour, settings.text_until_hour
    cursor = dt
    for _ in range(48):
        if start <= cursor.hour < end:
            return cursor
        if cursor.hour >= end:
            nxt = cursor + timedelta(days=1)
            cursor = nxt.replace(hour=start, minute=0, second=0, microsecond=0)
        else:
            cursor = cursor.replace(hour=start, minute=0, second=0, microsecond=0)
    return (dt + timedelta(days=1)).replace(hour=start, minute=0, second=0, microsecond=0)


def pick_due(store: Store, settings: Settings, now: datetime, sms_ready: bool = False):
    due = []
    for phone, lead in list(store.leads.items()):
        if lead.status != "waiting" or not lead.next_followup_at:
            continue
        if lead.next_followup_at > now:
            continue
        if lead.followups_sent >= settings.max_followups:
            lead.status = "completed"
            lead.next_followup_at = None
            continue
        in_quiet = now.hour < settings.text_from_hour or now.hour >= settings.text_until_hour
        if in_quiet:
            open_at = next_open_window(settings, now)
            lead.next_followup_at = open_at
            lead.last_skip_reason = f"quiet hours; moved to {open_at.isoformat()}"
            continue
        due.append({"caller_phone": phone, "action": "due", "ok_to_send": sms_ready})
    if not due:
        return [{"action": "none_due", "ok_to_send": False}]
    return due


def record_send(store: Store, settings: Settings, phone: str, now: datetime):
    lead = store.leads.get(phone)
    if not lead:
        return {"action": "missing_lead"}
    if lead.status != "waiting":
        return {"action": "status_changed_before_send", "status": lead.status}
    lead.followups_sent += 1
    if lead.followups_sent >= settings.max_followups:
        lead.status = "completed"
        lead.next_followup_at = None
        return {"action": "completed", "status": "completed", "followups_sent": lead.followups_sent}
    lead.next_followup_at = now + timedelta(minutes=settings.followup_delay_minutes)
    return {"action": "scheduled_next", "status": "waiting", "followups_sent": lead.followups_sent}


def run():
    s = Settings()
    failures = []

    def check(name, cond, detail=""):
        if not cond:
            failures.append(f"{name}: {detail}")
            print("FAIL", name, detail)
        else:
            print("ok  ", name)

    store = Store()
    now = datetime(2026, 11, 11, 12, 0, tzinfo=TZ)

    r = enroll(store, s, "", now)
    check("empty phone skipped", r["action"] == "skipped")

    r = enroll(store, s, "Restricted", now)
    check("hidden number skipped", r["action"] == "skipped")

    r = enroll(store, s, "5550109001", now)
    check("enroll 10-digit US", r["action"] == "enrolled" and r["caller_phone"] == "+15550109001")
    first_due = r["next_followup_at"]
    check("follow-up is 2 hours later", first_due == now + timedelta(hours=2))

    r = enroll(store, s, "+1 555 010 9001", now + timedelta(minutes=5))
    check("second enroll stays waiting", r["action"] == "already_waiting" and r["next_followup_at"] == first_due)

    r = classify(store, s, "+15550109001", "Can you do Friday at 3?", now + timedelta(minutes=10), "SM1")
    check("real reply", r["action"] == "replied" and r["notify_owner"] is True)
    check("status replied", store.leads["+15550109001"].status == "replied")
    check("cutoff clears next time", store.leads["+15550109001"].next_followup_at is None)

    r = classify(store, s, "+15550109001", "Can you do Friday at 3?", now + timedelta(minutes=11), "SM1")
    check("duplicate sid", r["action"] == "duplicate" and r["notify_owner"] is False)

    due = pick_due(store, s, now + timedelta(hours=3), sms_ready=True)
    check("replied lead is not due", due[0]["action"] == "none_due")

    r = enroll(store, s, "+15550109002", now)
    due = pick_due(store, s, now + timedelta(hours=1), sms_ready=True)
    check("not due yet", due[0]["action"] == "none_due")
    due = pick_due(store, s, now + timedelta(hours=3), sms_ready=True)
    check("second caller is due", due[0]["action"] == "due" and due[0]["caller_phone"] == "+15550109002")
    rec = record_send(store, s, "+15550109002", now + timedelta(hours=3))
    check("single nudge completes", rec["action"] == "completed" and store.leads["+15550109002"].status == "completed")

    r = enroll(store, s, "+15550109003", now)
    r = classify(store, s, "+15550109003", "STOP", now + timedelta(minutes=1), "SM2")
    check("STOP opts out", r["action"] == "opted_out" and r["notify_owner"] is True)
    r = enroll(store, s, "+15550109003", now + timedelta(minutes=2))
    check("cannot enroll opted out", r["action"] == "skipped_opted_out")
    due = pick_due(store, s, now + timedelta(hours=3), sms_ready=True)
    check("opted out is not due", all(d.get("caller_phone") != "+15550109003" for d in due))
    r = classify(store, s, "+15550109003", "START", now + timedelta(minutes=3), "SM3")
    check("START clears opt-out", r["action"] == "opted_in")
    check("START does not re-enroll waiting", store.leads["+15550109003"].status == "completed")

    r = classify(store, s, "+15550109004", "stop.", now, "SM4")
    check("STOP with period", r["action"] == "opted_out")
    r = classify(store, s, "+15550109005", "Yes, Friday works", now, "SM5")
    check("Yes comma is a real reply", r["action"] == "replied")
    r = classify(store, s, "+15550109006", "yes", now, "SM6")
    check("standalone yes is opt-in", r["action"] == "opted_in")
    r = classify(store, s, "+15550109007", "", now, "SM7")
    check("empty body skipped", r["action"] == "skipped")

    store2 = Store()
    enroll(store2, s, "+15550109008", now)
    due = pick_due(store2, s, datetime(2026, 11, 11, 22, 30, tzinfo=TZ), sms_ready=True)
    check("quiet hours send nothing", due[0]["action"] == "none_due")
    nxt = store2.leads["+15550109008"].next_followup_at
    check("quiet hours move to 8am", nxt.hour == 8 and nxt.day == 12)

    store3 = Store()
    s2 = Settings(max_followups=2)
    enroll(store3, s2, "+15550109009", now)
    due = pick_due(store3, s2, now + timedelta(hours=2), sms_ready=True)
    rec = record_send(store3, s2, "+15550109009", now + timedelta(hours=2))
    check("max 2 schedules next", rec["action"] == "scheduled_next" and store3.leads["+15550109009"].status == "waiting")
    rec = record_send(store3, s2, "+15550109009", now + timedelta(hours=4))
    check("second send completes", rec["action"] == "completed")

    if failures:
        raise SystemExit(f"{len(failures)} failed:\n" + "\n".join(failures))
    print("\nAll rule checks passed.")


if __name__ == "__main__":
    run()
