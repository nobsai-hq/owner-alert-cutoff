# Owner Alert and Follow-up Cutoff

A free n8n add-on for local businesses and the agencies that serve them. When a missed-call lead replies, the owner is told right away, and automated follow-up stops. A STOP reply also cuts off follow-up.

Built by Bobby Ve, No BS AI.

This is an add-on to the missed-call text-back template, not a replacement: https://github.com/nobsai-hq/missed-call-text-back

Live walkthrough of the idea (browser sandbox, no login): https://owner-alert-cutoff.news-ce2.workers.dev

## The problem

A lot of missed-call automations keep nudging after the person has already written back. That looks sloppy, and it is the question people keep asking.

On the n8n community forum, @Autionix3 asked (Aug 20, 2025):

> I made email follow up automation but don't know how to stop if recipient replied to the email and not send follow ups.

Source: https://community.n8n.io/t/i-want-to-make-email-follow-up-automation-but-dont-know-when-if-recipient-replied-stop-and-not-send-follow-ups/172121

The same cutoff shows up in later threads, including one that also covers SMS. @jesse.crawfordlang (Dec 4, 2025) wanted a sequence that "if it sees a response it will take them out of the sequence at any point," with Outlook for email and Dialpad for texts: https://community.n8n.io/t/need-help-building-email-sequencer/230827

The usual mistake is trying to cancel a Wait node. That is the wrong tool. Keep a status per phone. When they reply, mark them replied and tell the owner. Before every later send, read that status. If they replied or sent STOP, skip.

## What it does

1. After your first missed-call text goes out, you enroll the caller. The workflow stores a status of `waiting` and a time for the next follow-up (default: 2 hours later).
2. When a text comes in, it classifies the message:
   - A real reply (for example, "Can you do Friday at 3?") marks the lead `replied`, alerts the owner, and cancels later automated follow-up.
   - STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, QUIT, REVOKE, or OPT OUT marks the lead `opted_out` and cuts off follow-up. The owner can be told about the opt-out too.
   - START, UNSTOP, or a standalone YES clears the opt-out. It does not start a new follow-up on its own.
3. Every 15 minutes (or when you call the test webhook) the workflow looks for waiting leads whose follow-up time has passed. If they already replied or opted out, they are not sent anything. Quiet hours (default 8 AM to 9 PM, your timezone) push the send to the next open window.
4. By default it sends one polite nudge, then marks the lead `completed`. You can raise `max_followups` in Settings if you want another one.

It accepts Twilio-shaped inbound SMS fields (`From`, `Body`, `MessageSid`) and a simple test body: `{ "caller_phone", "text" }`. The same caller's Twilio retries are ignored when the `MessageSid` was already seen.

## What you need

- An n8n account (cloud or self-hosted).
- The missed-call text-back template, or any flow that already sends the first text.
- A phone number that can send and receive texts. This template is set up for Twilio, but any service with a "send a text" web address can work with small changes.
- Your texting number registered for business texting (in the US this is called A2P 10DLC). Your phone provider walks you through it.
- Optional: a Slack incoming webhook, email inbound webhook, or the owner's mobile number, so the alert has somewhere to go.

You can import the workflow and test enrollment, classification, and the cutoff without any SMS credentials. Leave `sms_api_url` with `YOUR_ACCOUNT_SID` and the follow-up send is skipped.

## Setup steps

1. In n8n, click **Import from File** and pick `workflow.json`.
2. Open the three Settings nodes (**Settings**, **Settings Reply**, **Settings Due**) and put the same values in each: business name, booking link, texting number, Twilio Account SID inside the send address, timezone, and optionally the owner webhook or owner phone.
3. On **Send Follow-up SMS** and **Owner SMS**, add a **Basic Auth** credential. The user is your Twilio Account SID. The password is your Twilio Auth Token. Keep these private and never paste them in the workflow itself.
4. Turn the workflow on (Active) so the lead list can save. Copy the three **Production URLs**:
   - **Enroll Lead**: call this after the first missed-call text goes out, with `{ "caller_phone": "+1..." }`. In the missed-call text-back workflow, add an HTTP Request node after **Send Text** and POST to this URL.
   - **Inbound Reply**: paste this in your phone number's "a message comes in" setting. Twilio only has one of these, so this workflow should be the one that receives replies (it also handles STOP and START).
   - **Check Due Now**: optional. Use it for tests, including a fake clock (see `TESTING.md`).
5. Test with the cases in `TESTING.md`. Enroll a number, send a reply, then run the due check. The follow-up should be skipped. Enroll a second number, do not reply, run the due check with a later `now` time. That one should be marked due.

## Good to know

- The lead list lives in the workflow's own memory. It only saves when the workflow is on (Active), not during a one-off manual test run. If you delete the workflow, the list is gone too.
- This template has not yet been run against a live Twilio number, a live owner SMS, or a live owner webhook. The JSON is written to import into n8n and to match the public Twilio field names above.
- The public demo page is a browser sandbox. It does not send texts, it does not write to a CRM, and it does not collect personal data.
- A standalone YES is treated as opt back in, same as the missed-call text-back template. A longer message that starts with "Yes," (for example "Yes, Friday works") is treated as a real reply, not an opt-in.
- The 15 minute schedule only runs while the workflow is Active. It will not send anything while `sms_api_url` still contains `YOUR_ACCOUNT_SID`.
- Texting rules differ by place. Check the rules where you do business before you turn this on.

## Files

- `workflow.json`: the n8n workflow to import.
- `build_workflow.py`: the script that makes `workflow.json`, if you want to change it in code.
- `TESTING.md`: sample payloads and how to test.
- `src/index.js`: the Cloudflare Worker that serves the public demo page.
- `LICENSE`: MIT. Free to use and change.

## Built by

Built by Bobby Ve, No BS AI. I bridge the gap between enterprise AI and entrepreneurs.

---

Want it set up on your number? Text SETUP to [(470) 779-2869](sms:+14707792869)
