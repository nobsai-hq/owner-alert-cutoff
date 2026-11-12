# How to test this template

The workflow JSON is written to import into n8n. It has not yet been run against a live Twilio number, a live owner SMS, or a live owner webhook. Use these payloads in a test n8n instance first.

All phone numbers below are fake 555 numbers. Turn the workflow on (Active) so the lead list can save. Leave `sms_api_url` with `YOUR_ACCOUNT_SID` unless you intentionally want to send a real text.

Copy the Production URLs from **Enroll Lead**, **Inbound Reply**, and **Check Due Now**.

## 1. Import

In n8n, Import from File, pick `workflow.json`. The canvas should show 18 nodes, including a sticky note. Confirm the three Settings nodes share the same sample values.

## 2. Enroll a caller

```
POST <enroll-url>
Content-Type: application/json

{
  "caller_phone": "+15550109001",
  "source": "test"
}
```

Expected: HTTP 200, `action` is `enrolled`, `status` is `waiting`, and `next_followup_at` is about 2 hours ahead.

Send the same body again. Expected: `action` is `already_waiting`. The original follow-up time is left in place.

## 3. A real reply cuts off follow-up and would alert the owner

```
POST <reply-url>
Content-Type: application/json

{
  "From": "+15550109001",
  "Body": "Can you do Friday at 3?",
  "MessageSid": "SMaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

Expected: `action` is `replied`, `status` is `replied`, `notify_owner` is true, and `owner_text` includes the caller's number and the message. If `owner_webhook_url` and `owner_phone` are still blank, no alert is sent, which is expected.

Send the same body with the same `MessageSid` again. Expected: `action` is `duplicate`, no second owner alert.

## 4. Due check after a reply is skipped

```
POST <check-due-url>
Content-Type: application/json

{
  "now": "2026-11-12T16:00:00-05:00"
}
```

Use a `now` value at least two hours after you enrolled the caller, and inside 8 AM to 9 PM Eastern. Expected: this caller is not due. `action` is `none_due` (or another waiting lead, but not +15550109001). No follow-up text is sent.

## 5. A second caller who does not reply is due

Enroll `+15550109002`. Do not send a reply. Then call Check Due Now with a `now` at least two hours later, inside texting hours.

Expected: one item with `caller_phone` `+15550109002`, `action` is `due`. If SMS is not configured, `ok_to_send` is false and the skip reason says so. If SMS is configured, a follow-up would go out and **Record Send** would mark the lead `completed` after the default single nudge.

## 6. STOP cuts off follow-up

Enroll `+15550109003`, then:

```
POST <reply-url>
Content-Type: application/json

{
  "From": "+15550109003",
  "Body": "STOP",
  "MessageSid": "SMbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
}
```

Expected: `action` is `opted_out`, `status` is `opted_out`. A later due check does not text this number. Trying to enroll the same number again is skipped with `skipped_opted_out`.

`START` on that number clears the opt-out (`action` is `opted_in`) but does not enroll a new follow-up.

## 7. Quiet hours

Enroll a new number, then call Check Due Now with `"now": "2026-11-12T22:30:00-05:00"` (after 9 PM Eastern). Expected: the lead stays `waiting`, no send, and the next follow-up time moves into the next open window (8 AM).

## 8. Hidden or empty numbers

Enroll `{ "caller_phone": "" }` or `{ "From": "Restricted" }`. Expected: `action` is `skipped`, no lead is stored.

## Cases checked in this repo (Nov 11, 2026)

`test_logic.py` covers enrollment, already-waiting, opt-out block, real reply cutoff, MessageSid dedupe, STOP, START, empty body, hidden numbers, due vs not due, quiet hours, and max follow-ups. Those tests run the same rules in Python. They are not a live n8n run.

## Not tested yet

- A real Twilio inbound SMS to the reply webhook
- A real owner SMS or owner webhook delivery
- A real follow-up SMS from Twilio
- The 15 minute schedule on an Active n8n instance
- Two due checks running at the exact same moment (the static-data write is not a database transaction)
- n8n Cloud, or n8n versions other than the JSON import itself

If you run those tests, add the results here.
