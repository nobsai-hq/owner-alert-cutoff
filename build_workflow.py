#!/usr/bin/env python3
"""Build an importable n8n workflow.json for the owner-alert + follow-up cutoff add-on."""

import json
import uuid
from pathlib import Path

OUT = Path(__file__).resolve().parent / "workflow.json"

SETTINGS_JS = r"""// EDIT THESE. Sample values only. Do not put API tokens here.
const settings = {
  business_name: 'Sample Barber Shop',
  booking_link: 'https://example.com/book',
  // Your texting number, in +1 format. This is a fake sample number.
  from_number: '+15555550100',
  // Twilio send-message address. Put YOUR Account SID in place of YOUR_ACCOUNT_SID.
  sms_api_url: 'https://api.twilio.com/2010-04-01/Accounts/YOUR_ACCOUNT_SID/Messages.json',
  timezone: 'America/New_York',
  // Only send follow-up texts between these hours (24 hour clock, local time).
  text_from_hour: 8,
  text_until_hour: 21,
  // How long to wait after enrollment before the first (and by default only) follow-up.
  followup_delay_minutes: 120,
  // How many automated follow-ups to send after the first missed-call text. 1 is a single nudge.
  max_followups: 1,
  // Optional. Slack, Make, or email inbound webhook. Leave blank to skip.
  owner_webhook_url: '',
  // Optional. If set, and SMS is configured, the owner also gets a text.
  owner_phone: '',
  // Also tell the owner when someone replies STOP.
  alert_on_stop: true,
};

return $input.all().map(item => ({ json: { ...item.json, settings } }));
"""

ENROLL_JS = r"""// Enroll a caller after the first missed-call text went out.
// Status lives in this workflow's static data. It only saves when the workflow is Active.
const item = $input.first().json;
const settings = item.settings;
const body = item.body !== undefined ? item.body : item;

function normalizePhone(raw) {
  const s = String(raw || '').trim();
  if (!s || /anonymous|restricted|unknown|blocked/i.test(s)) return '';
  const digits = s.replace(/\D/g, '');
  if (digits.length === 10) return '+1' + digits;
  if (digits.length === 11 && digits.startsWith('1')) return '+' + digits;
  if (s.startsWith('+') && digits.length >= 8 && digits.length <= 15) return '+' + digits;
  if (/^\+[1-9]\d{7,14}$/.test(s)) return s;
  return '';
}

const caller = normalizePhone(body.caller_phone || body.From || body.from || body.phone || '');
const source = String(body.source || body.Source || 'enroll').slice(0, 80);
const store = $getWorkflowStaticData('global');
store.leads = store.leads || {};

const now = DateTime.now().setZone(settings.timezone);
let action = 'skipped';
let skip_reason = '';
let lead = null;

if (!caller) {
  skip_reason = 'no valid caller number (maybe blocked or private)';
} else if (store.leads[caller] && store.leads[caller].status === 'opted_out') {
  skip_reason = 'caller opted out';
  lead = store.leads[caller];
  action = 'skipped_opted_out';
} else if (store.leads[caller] && store.leads[caller].status === 'waiting') {
  lead = store.leads[caller];
  action = 'already_waiting';
  skip_reason = 'already enrolled and waiting; left the existing follow-up time in place';
} else {
  const next = now.plus({ minutes: Number(settings.followup_delay_minutes) || 120 });
  lead = {
    status: 'waiting',
    enrolled_at: now.toISO(),
    next_followup_at: next.toMillis(),
    followups_sent: 0,
    source,
    last_inbound: '',
    last_inbound_at: '',
  };
  store.leads[caller] = lead;
  action = 'enrolled';
}

return [{
  json: {
    settings,
    caller_phone: caller,
    action,
    skip_reason,
    status: lead ? lead.status : '',
    next_followup_at: lead ? lead.next_followup_at : null,
    followups_sent: lead ? lead.followups_sent : 0,
  }
}];
"""

CLASSIFY_JS = r"""// Classify an inbound text. STOP cuts off follow-up. A real reply alerts the owner and cuts off follow-up.
// START clears the opt-out but does not start a new follow-up on its own.
const item = $input.first().json;
const settings = item.settings;
const body = item.body !== undefined ? item.body : item;

const STOP = ['stop', 'stopall', 'unsubscribe', 'cancel', 'end', 'quit', 'revoke', 'opt out', 'optout'];
const START = ['start', 'unstop', 'yes'];

function normalizePhone(raw) {
  const s = String(raw || '').trim();
  if (!s || /anonymous|restricted|unknown|blocked/i.test(s)) return '';
  const digits = s.replace(/\D/g, '');
  if (digits.length === 10) return '+1' + digits;
  if (digits.length === 11 && digits.startsWith('1')) return '+' + digits;
  if (s.startsWith('+') && digits.length >= 8 && digits.length <= 15) return '+' + digits;
  if (/^\+[1-9]\d{7,14}$/.test(s)) return s;
  return '';
}

function keyword(text) {
  const t = String(text || '').trim().toLowerCase().replace(/\s+/g, ' ');
  if (!t) return '';
  const first = t.split(' ')[0].replace(/[^a-z]/g, '');
  const compact = t.replace(/[^a-z\s]/g, '').trim();
  // STOP is matched on the first word so "STOP texting me" still opts out.
  if (STOP.includes(t) || STOP.includes(first) || STOP.includes(compact)) return 'stop';
  // START and YES only count when the whole message is that word, so "Yes, Friday works" stays a real reply.
  if (START.includes(t) || START.includes(compact)) return 'start';
  return '';
}

const caller = normalizePhone(body.From || body.from || body.caller_phone || body.phone || '');
const text = String(body.Body || body.body || body.text || '').trim();
const message_sid = String(body.MessageSid || body.message_sid || body.SmsSid || body.sid || '').trim();
const store = $getWorkflowStaticData('global');
store.leads = store.leads || {};
store.seen_sids = store.seen_sids || {};

const now = DateTime.now().setZone(settings.timezone);
let action = 'ignored';
let skip_reason = '';
let notify_owner = false;
let lead = caller ? (store.leads[caller] || null) : null;

if (!caller) {
  skip_reason = 'no valid caller number';
  action = 'skipped';
} else if (message_sid && store.seen_sids[message_sid]) {
  skip_reason = 'message_sid already processed';
  action = 'duplicate';
} else {
  if (message_sid) store.seen_sids[message_sid] = now.toMillis();
  // Cap remembered SIDs so the list cannot grow forever.
  const sids = Object.keys(store.seen_sids);
  if (sids.length > 500) {
    sids.sort((a, b) => store.seen_sids[a] - store.seen_sids[b]);
    for (const id of sids.slice(0, sids.length - 500)) delete store.seen_sids[id];
  }

  const kind = keyword(text);
  if (!lead) {
    lead = {
      status: 'waiting',
      enrolled_at: now.toISO(),
      next_followup_at: null,
      followups_sent: 0,
      source: 'inbound-only',
      last_inbound: '',
      last_inbound_at: '',
    };
  }

  if (kind === 'stop') {
    lead.status = 'opted_out';
    lead.next_followup_at = null;
    lead.last_inbound = text;
    lead.last_inbound_at = now.toISO();
    store.leads[caller] = lead;
    action = 'opted_out';
    notify_owner = !!settings.alert_on_stop;
  } else if (kind === 'start') {
    if (lead.status === 'opted_out') lead.status = 'completed';
    lead.last_inbound = text;
    lead.last_inbound_at = now.toISO();
    store.leads[caller] = lead;
    action = 'opted_in';
    notify_owner = false;
  } else if (!text) {
    action = 'skipped';
    skip_reason = 'empty message body';
  } else {
    lead.status = 'replied';
    lead.next_followup_at = null;
    lead.last_inbound = text;
    lead.last_inbound_at = now.toISO();
    store.leads[caller] = lead;
    action = 'replied';
    notify_owner = true;
  }
}

const owner_text = action === 'replied'
  ? ('Reply for ' + settings.business_name + ' from ' + caller + ': ' + text)
  : (action === 'opted_out'
    ? (caller + ' replied STOP. Automated follow-up is off.')
    : '');

return [{
  json: {
    settings,
    caller_phone: caller,
    text,
    message_sid,
    action,
    skip_reason,
    status: lead ? lead.status : '',
    notify_owner,
    notify_owner_sms: notify_owner && !!(settings.owner_phone),
    notify_owner_webhook: notify_owner && !!(settings.owner_webhook_url),
    owner_text,
  }
}];
"""

PICK_DUE_JS = r"""// Find waiting leads whose follow-up time has passed. Do not send if they replied or opted out.
// Optional test payload: { "now": "2026-11-12T16:00:00-05:00" } to pretend it is that time.
const item = $input.first().json;
const settings = item.settings;
const body = item.body !== undefined ? item.body : item;
const store = $getWorkflowStaticData('global');
store.leads = store.leads || {};

const zone = settings.timezone || 'America/New_York';
const now = body && body.now ? DateTime.fromISO(String(body.now), { zone }) : DateTime.now().setZone(zone);
const smsReady = settings.from_number && settings.sms_api_url && !String(settings.sms_api_url).includes('YOUR_ACCOUNT_SID');

function nextOpenWindow(dt) {
  const start = Number(settings.text_from_hour);
  const end = Number(settings.text_until_hour);
  let cursor = dt;
  for (let i = 0; i < 48; i++) {
    if (cursor.hour >= start && cursor.hour < end) return cursor;
    if (cursor.hour >= end) {
      cursor = cursor.plus({ days: 1 }).set({ hour: start, minute: 0, second: 0, millisecond: 0 });
    } else {
      cursor = cursor.set({ hour: start, minute: 0, second: 0, millisecond: 0 });
    }
  }
  return dt.plus({ days: 1 }).set({ hour: start, minute: 0, second: 0, millisecond: 0 });
}

const due = [];
for (const [phone, lead] of Object.entries(store.leads)) {
  if (!lead || lead.status !== 'waiting') continue;
  if (!lead.next_followup_at) continue;
  if (lead.next_followup_at > now.toMillis()) continue;
  if ((lead.followups_sent || 0) >= (Number(settings.max_followups) || 1)) {
    lead.status = 'completed';
    lead.next_followup_at = null;
    continue;
  }

  const inQuiet = now.hour < Number(settings.text_from_hour) || now.hour >= Number(settings.text_until_hour);
  if (inQuiet) {
    const open = nextOpenWindow(now);
    lead.next_followup_at = open.toMillis();
    lead.last_skip_reason = 'quiet hours; moved to ' + open.toISO();
    continue;
  }

  const message = 'Hi, it is ' + settings.business_name +
    '. Just checking in if you still need a time. Book here: ' + settings.booking_link +
    ' or reply to this text and we will get back to you. Reply STOP to opt out.';

  due.push({
    json: {
      settings,
      caller_phone: phone,
      action: 'due',
      status: lead.status,
      followups_sent: lead.followups_sent || 0,
      ok_to_send: !!smsReady,
      skip_reason: smsReady ? '' : 'SMS is not configured yet (sms_api_url still has YOUR_ACCOUNT_SID, or from_number is empty)',
      message,
      now: now.toISO(),
    }
  });
}

if (!due.length) {
  return [{
    json: {
      settings,
      caller_phone: '',
      action: 'none_due',
      status: '',
      followups_sent: 0,
      ok_to_send: false,
      skip_reason: 'no waiting leads are due',
      message: '',
      now: now.toISO(),
    }
  }];
}

return due;
"""

RECORD_JS = r"""// Record that a follow-up went out. Schedule another one only if max_followups is greater than 1.
const item = $input.first().json;
const settings = item.settings;
const store = $getWorkflowStaticData('global');
store.leads = store.leads || {};
const lead = store.leads[item.caller_phone];
const now = item.now ? DateTime.fromISO(String(item.now), { zone: settings.timezone }) : DateTime.now().setZone(settings.timezone);

let action = 'recorded';
if (!lead) {
  action = 'missing_lead';
} else if (lead.status !== 'waiting') {
  action = 'status_changed_before_send';
} else {
  lead.followups_sent = (lead.followups_sent || 0) + 1;
  lead.last_followup_at = now.toISO();
  if (lead.followups_sent >= (Number(settings.max_followups) || 1)) {
    lead.status = 'completed';
    lead.next_followup_at = null;
    action = 'completed';
  } else {
    const next = now.plus({ minutes: Number(settings.followup_delay_minutes) || 120 });
    lead.next_followup_at = next.toMillis();
    action = 'scheduled_next';
  }
  store.leads[item.caller_phone] = lead;
}

return [{
  json: {
    ...item,
    action,
    status: lead ? lead.status : '',
    followups_sent: lead ? lead.followups_sent : item.followups_sent,
  }
}];
"""

STICKY = """## Owner alert and follow-up cutoff
Built by Bobby Ve, No BS AI.

This is an add-on to the missed-call text-back template. It does not replace it.

1. Edit the Settings nodes (there are three, with the same values).
2. After your first missed-call text goes out, POST the caller to the Enroll Lead production URL.
3. Point your number's "a message comes in" webhook at Inbound Reply.
4. Add a Basic Auth credential to Send Follow-up SMS and Owner SMS (Twilio Account SID + Auth Token).
5. Turn the workflow on so the lead list can save. The 15 minute check only runs while it is Active.

When a caller replies, the owner is alerted and later automated follow-up is skipped. STOP also cuts off follow-up.

Do not try to cancel a Wait node. This workflow keeps a status per phone and reads it before every send.
"""


def nid():
    return str(uuid.uuid4())


def node(name, typ, typver, x, y, parameters, **extra):
    n = {
        "id": nid(),
        "name": name,
        "type": typ,
        "typeVersion": typver,
        "position": [x, y],
        "parameters": parameters,
    }
    n.update(extra)
    return n


def iff(name, x, y, left_expr):
    return node(
        name,
        "n8n-nodes-base.if",
        2,
        x,
        y,
        {
            "conditions": {
                "options": {
                    "caseSensitive": True,
                    "leftValue": "",
                    "typeValidation": "strict",
                },
                "conditions": [
                    {
                        "id": nid(),
                        "leftValue": left_expr,
                        "rightValue": True,
                        "operator": {
                            "type": "boolean",
                            "operation": "true",
                            "singleValue": True,
                        },
                    }
                ],
                "combinator": "and",
            },
            "options": {},
        },
    )


def webhook(name, path, x, y):
    return node(
        name,
        "n8n-nodes-base.webhook",
        2,
        x,
        y,
        {
            "httpMethod": "POST",
            "path": path,
            "responseMode": "onReceived",
            "options": {},
        },
        webhookId=nid(),
    )


def settings_node(name, x, y):
    return node(name, "n8n-nodes-base.code", 2, x, y, {"jsCode": SETTINGS_JS})


def main():
    enroll = webhook("Enroll Lead", "enroll", 0, 0)
    settings_enroll = settings_node("Settings", 260, 0)
    enroll_code = node("Enroll or Skip", "n8n-nodes-base.code", 2, 520, 0, {"jsCode": ENROLL_JS})

    reply = webhook("Inbound Reply", "reply", 0, 320)
    settings_reply = settings_node("Settings Reply", 260, 320)
    classify = node("Classify Reply", "n8n-nodes-base.code", 2, 520, 320, {"jsCode": CLASSIFY_JS})
    notify_wh = iff("Notify Owner Webhook?", 780, 220, "={{ $json.notify_owner_webhook }}")
    owner_wh = node(
        "Owner Webhook",
        "n8n-nodes-base.httpRequest",
        4.2,
        1060,
        160,
        {
            "method": "POST",
            "url": "={{ $json.settings.owner_webhook_url }}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [{"name": "Content-Type", "value": "application/json"}]
            },
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ text: $json.owner_text, caller_phone: $json.caller_phone, action: $json.action, business: $json.settings.business_name }) }}",
            "options": {},
        },
    )
    notify_sms = iff("Notify Owner SMS?", 780, 420, "={{ $json.notify_owner_sms }}")
    owner_sms = node(
        "Owner SMS",
        "n8n-nodes-base.httpRequest",
        4.2,
        1060,
        420,
        {
            "method": "POST",
            "url": "={{ $json.settings.sms_api_url }}",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpBasicAuth",
            "sendBody": True,
            "contentType": "form-urlencoded",
            "bodyParameters": {
                "parameters": [
                    {"name": "To", "value": "={{ $json.settings.owner_phone }}"},
                    {"name": "From", "value": "={{ $json.settings.from_number }}"},
                    {"name": "Body", "value": "={{ $json.owner_text }}"},
                ]
            },
            "options": {},
        },
    )

    schedule = node(
        "Every 15 Minutes",
        "n8n-nodes-base.scheduleTrigger",
        1.2,
        0,
        680,
        {"rule": {"interval": [{"field": "minutes", "minutesInterval": 15}]}},
    )
    check = webhook("Check Due Now", "check-due", 0, 860)
    settings_due = settings_node("Settings Due", 260, 760)
    pick = node("Pick Due Leads", "n8n-nodes-base.code", 2, 520, 760, {"jsCode": PICK_DUE_JS})
    send_if = iff("Send Follow-up?", 780, 760, "={{ $json.ok_to_send }}")
    send_sms = node(
        "Send Follow-up SMS",
        "n8n-nodes-base.httpRequest",
        4.2,
        1060,
        680,
        {
            "method": "POST",
            "url": "={{ $json.settings.sms_api_url }}",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpBasicAuth",
            "sendBody": True,
            "contentType": "form-urlencoded",
            "bodyParameters": {
                "parameters": [
                    {"name": "To", "value": "={{ $json.caller_phone }}"},
                    {"name": "From", "value": "={{ $json.settings.from_number }}"},
                    {"name": "Body", "value": "={{ $json.message }}"},
                ]
            },
            "options": {},
        },
    )
    record = node("Record Send", "n8n-nodes-base.code", 2, 1320, 680, {"jsCode": RECORD_JS})

    sticky = node(
        "Read Me",
        "n8n-nodes-base.stickyNote",
        1,
        -40,
        -420,
        {"content": STICKY, "height": 420, "width": 520},
    )

    nodes = [
        enroll,
        settings_enroll,
        enroll_code,
        reply,
        settings_reply,
        classify,
        notify_wh,
        owner_wh,
        notify_sms,
        owner_sms,
        schedule,
        check,
        settings_due,
        pick,
        send_if,
        send_sms,
        record,
        sticky,
    ]

    def link(a, b):
        return (a["name"], b["name"])

    pairs = [
        link(enroll, settings_enroll),
        link(settings_enroll, enroll_code),
        link(reply, settings_reply),
        link(settings_reply, classify),
        link(classify, notify_wh),
        link(classify, notify_sms),
        link(notify_wh, owner_wh),
        link(notify_sms, owner_sms),
        link(schedule, settings_due),
        link(check, settings_due),
        link(settings_due, pick),
        link(pick, send_if),
        link(send_if, send_sms),
        link(send_sms, record),
    ]

    connections = {}
    for src, dst in pairs:
        connections.setdefault(src, {"main": [[]]})
        # If we already added a second branch from the same node, append to first output
        # except IF nodes need only the true branch (output 0).
        connections[src]["main"][0].append({"node": dst, "type": "main", "index": 0})

    # Classify fans out to two IF nodes on the same output. The dict logic above
    # already appends both to main[0], which is correct.
    # IF true branch is also main[0]. Good.

    workflow = {
        "name": "Owner Alert and Follow-up Cutoff (No BS AI template)",
        "nodes": nodes,
        "connections": connections,
        "active": False,
        "settings": {"executionOrder": "v1"},
        "meta": {"templateCredsSetupCompleted": False, "builtBy": "Bobby Ve, No BS AI"},
        "tags": [],
    }

    OUT.write_text(json.dumps(workflow, indent=2) + "\n")
    print(f"Wrote {OUT} with {len(nodes)} nodes")


if __name__ == "__main__":
    main()
