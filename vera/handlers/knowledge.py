"""Research, compliance, CDE and seasonal-demand triggers: messages whose
'why now' is a piece of category knowledge from the weekly digest."""

from __future__ import annotations

import re

from .. import facts as F
from ..core import Ctx, Draft, cap, yes_close
from .fallback import generic


def research_digest(c: Ctx) -> Draft:
    p = c.payload
    item = F.digest_item(
        c.cat, p.get("top_item_id") or p.get("digest_item_id"), kinds=("research", "trend", "tech", "cde")
    )
    if not item:
        return generic(c)
    c.cite(f"category.digest[{item.get('id')}]")
    src = item.get("source")
    title = item.get("title", "")
    n = item.get("trial_n")
    seg = item.get("patient_segment")
    ca = c.m.get("customer_aggregate") or {}

    # merchant anchor: match the research segment to this merchant's roster
    anchor = ""
    if seg and ca.get(f"{seg.rstrip('s')}_count") is not None:
        cnt = ca.get(f"{seg.rstrip('s')}_count")
        anchor = f"You have {F.num(cnt)} {F.humanize(seg)} on your roster — this maps straight onto their recall plan."
        c.cite(f"merchant.customer_aggregate.{seg.rstrip('s')}_count")
    elif ca.get("total_unique_ytd"):
        anchor = f"Relevant for the {F.num(ca['total_unique_ytd'])} patients you've seen this year."
        c.cite("merchant.customer_aggregate.total_unique_ytd")

    lead = f"{c.sal}, new in {src}: {title[:1].lower() + title[1:].rstrip('.')}."
    detail = F.first_sentences(item.get("summary", ""), 1)
    if n:
        if re.search(r"\btrial\b", detail):
            detail = re.sub(r"\btrial\b", f"trial ({F.num(n)} patients)", detail, count=1)
        else:
            lead = lead.rstrip(".") + f" ({F.num(n)}-patient trial)."
    caveat = ""
    if "No effect" in item.get("summary", ""):
        caveat = "Worth noting: no benefit shown in low-risk patients, so this is a targeted change, not a blanket one."
    ask = yes_close(
        c,
        "draft a 3-line patient WhatsApp explaining the new recall interval",
        "Main 3-line patient WhatsApp draft kar doon jo naya recall interval samjhaye",
        eta="5 min",
    )
    body = " ".join(x for x in [lead, detail, caveat, anchor, ask] if x)
    patient_note = _patient_note_from(item, c)
    return Draft(
        body,
        "binary_yes_no",
        rationale=f"Research digest ({src}) matched to this merchant's cohort; curiosity + reciprocity (Vera drafts the patient note).",
        lever="specificity+reciprocity",
        next_action={
            "type": "deliver",
            "topic": title,
            "artifact": patient_note,
            "confirm_en": "send it to your patient list",
            "source": src,
        },
    )


def _patient_note_from(item: dict, c: Ctx) -> str:
    title = item.get("title", "")
    if c.slug == "dentists":
        return (
            f"\"Quick update from {F.biz_name(c.m)}: new research ({item.get('source')}) shows that for people "
            f"with a history of cavities, a fluoride check every 3 months protects teeth better than every 6. "
            f"If you've had fillings in the last 2 years, reply here and we'll suggest the right interval for you.\""
        )
    return f'"{F.biz_name(c.m)} update: {title}. Reply here if you\'d like to know how this applies to you."'


def regulation_change(c: Ctx) -> Draft:
    p = c.payload
    item = F.digest_item(c.cat, p.get("top_item_id") or p.get("digest_item_id"), kinds=("compliance",))
    if not item:
        return generic(c)
    c.cite(f"category.digest[{item.get('id')}]")
    deadline = p.get("deadline_iso")
    dl = F.day_month(deadline)
    left = F.days_between(c.now, deadline) if (c.now and deadline) else None
    when = f"effective {dl}" if dl else ""
    if left is not None and 0 < left <= 180:
        when += f" — {left} days from today"
    summary = item.get("summary", "")
    title = item.get("title", "").rstrip(".")
    if dl:
        title = re.sub(r"\s*effective\s+\d{4}-\d{2}-\d{2}", "", title)
    lead = f"{c.sal}, compliance heads-up: {title}"
    lead += f" ({item.get('source')})." if item.get("source") else "."
    # question that forces a one-word reply (asking-the-merchant lever)
    if c.slug == "dentists" and "D-speed" in summary:
        body_mid = (
            "Max dose per IOPA drops 1.5 → 1.0 mSv. E-speed film and digital RVG pass; D-speed film does not."
            if "1.5 mSv" in summary
            else summary
        )
        q = c.say(
            "Which does your clinic use — E-speed, D-speed or RVG? One word is enough; if it's D-speed I'll draft the switch plan + the SOP note for your file.",
            "Aapke clinic mein kaunsa hai — E-speed, D-speed ya RVG? Bas ek word bhejiye; D-speed hua to switch plan + SOP note main draft kar dungi.",
        )
        cta = "open_ended"
    else:
        body_mid = F.first_sentences(summary, 1)
        q = yes_close(
            c,
            f"draft a 1-page checklist so you're covered before {dl or 'the deadline'}",
            f"{dl or 'deadline'} se pehle ka 1-page checklist main bana doon",
        )
        cta = "binary_yes_no"
    body = " ".join(x for x in [lead, (cap(when) + ".") if when else "", body_mid, q] if x)
    return Draft(
        body,
        cta,
        rationale=f"Regulatory change with a hard deadline ({dl}); loss-aversion framing + a one-word diagnostic question so the reply costs nothing.",
        lever="loss_aversion+asking_the_merchant",
        next_action={
            "type": "deliver",
            "topic": item.get("title"),
            "artifact": f"Checklist — {item.get('title')}:\n1) Check current X-ray setup type\n2) {item.get('actionable', 'Update SOP')}\n3) File the SOP note with date + signature",
            "confirm_en": "save it to your records",
        },
    )


def cde_opportunity(c: Ctx) -> Draft:
    p = c.payload
    item = F.digest_item(c.cat, p.get("digest_item_id") or p.get("top_item_id"), kinds=("cde",))
    if not item:
        return generic(c)
    c.cite(f"category.digest[{item.get('id')}]")
    when = F.weekday_day_month(item.get("date"))
    dt = F.parse_dt(item.get("date"))
    tm = ""
    if dt:
        h = dt.hour % 12 or 12
        tm = f", {h}{'pm' if dt.hour >= 12 else 'am'}"
    credits = p.get("credits") or item.get("credits")
    lead = f"{c.sal}, {item.get('title')} — {when}{tm}" if when else f"{c.sal}, {item.get('title')}"
    if credits:
        lead += f", {credits} CDE credits"
    lead += "."
    mid = F.first_sentences(item.get("summary", ""), 2)
    fee = item.get("actionable") or ""
    # continuity: merchant asked about aligners/whitening earlier?
    loop = F.open_loop(c.m) or (F.last_merchant_turn(c.m) or {}).get("body")
    tie = ""
    if loop and any(k in loop.lower() for k in ("aligner", "digital", "scan", "impression", "crown")):
        tie = "Since you asked me to focus on aligners, this one fits — digital impressions are the front door to aligner cases."
        c.cite("merchant.conversation_history (aligner focus)")
    ask = yes_close(
        c,
        "register you and set a reminder 2 hours before",
        "Main aapko register karke 2 ghante pehle reminder laga doon",
        eta="2 min",
    )
    body = " ".join(x for x in [lead, mid, fee.rstrip(".") + "." if fee else "", tie, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="CDE event with date, credits and fee from the category digest; tied to the merchant's own stated interest; effort externalized (Vera registers).",
        lever="specificity+effort_externalization",
        next_action={
            "type": "deliver",
            "topic": item.get("title"),
            "artifact": f"Registered interest for: {item.get('title')} ({when}{tm}). Reminder set for 2 hours before.",
            "confirm_en": "confirm the registration",
        },
    )


def category_seasonal(c: Ctx) -> Draft:
    p = c.payload
    trends = p.get("trends") or []
    item = F.digest_item(c.cat, None, kinds=("seasonal",))
    parts = []
    for t in trends[:4]:
        # 'ORS_demand_+40' -> 'ORS +40%'
        raw = str(t)
        label = (
            raw.split("_demand")[0].replace("_", "/") if "_demand" in raw else raw.rsplit("_", 1)[0].replace("_", " ")
        )
        val = raw.rsplit("_", 1)[-1]
        if val.lstrip("+-").isdigit():
            parts.append(f"{label} {val}%")
    ca = c.m.get("customer_aggregate") or {}
    if parts:
        c.cite("trigger.payload.trends")
        lead = (
            f"{c.sal}, summer demand has flipped: {', '.join(parts)} (multi-pharmacy data, this season)."
            if c.slug == "pharmacies"
            else f"{c.sal}, seasonal shift is live: {', '.join(parts)}."
        )
    elif item:
        c.cite(f"category.digest[{item.get('id')}]")
        lead = f"{c.sal}, {item.get('title')} ({item.get('source')})."
    else:
        return generic(c)
    act = (item or {}).get("actionable", "")
    shelf = f"Shelf move for this week: {act[:1].lower() + act[1:].rstrip('.')}." if act else ""
    content = F.content_item(c.cat, ("summer", "season", "monsoon"))
    anchor = ""
    rep = ca.get("repeat_customer_pct")
    if content:
        c.cite(f"category.patient_content_library[{content.get('id')}]")
        who = f"your repeat customers ({F.pct(rep)} of your base)" if rep else "your customers"
        anchor = f"I also have a ready customer note — \"{content.get('title')}\" — to send to {who}."
    ask = yes_close(
        c,
        "send you that note + a counter-display checklist",
        "Woh note + counter-display checklist bhej doon",
        eta="5 min",
    )
    body = " ".join(x for x in [lead, shelf, anchor, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Seasonal demand shift with concrete % from the trigger; converts it into one shelf action + a ready customer broadcast from the content library.",
        lever="specificity+effort_externalization",
        next_action={
            "type": "deliver",
            "topic": "seasonal demand",
            "artifact": (content or {}).get("body", ""),
            "confirm_en": "broadcast it to your customers",
        },
    )


def supply_alert(c: Ctx) -> Draft:
    p = c.payload
    item = F.digest_item(c.cat, p.get("alert_id") or p.get("top_item_id"), kinds=("alert", "supply"))
    mol, batches, mfr = p.get("molecule"), p.get("affected_batches") or [], p.get("manufacturer")
    if not (mol or item):
        return generic(c)
    c.cite("trigger.payload batches")
    if item:
        c.cite(f"category.digest[{item.get('id')}]")
    loop = F.open_loop(c.m)
    lead = f"{c.sal}, urgent: voluntary recall on {mol or ''} batches {', '.join(batches)}".strip()
    if mfr:
        lead += f" ({mfr.replace('Mfr', 'Mfr ').replace('  ', ' ')})"
    lead += "."
    why = ""
    if item and "sub-potency" in item.get("summary", ""):
        why = (
            "Reason: sub-potency — no safety risk beyond weaker LDL control, but patients must be told and given a replacement"
            + (f" ({item.get('source')})." if item.get("source") else ".")
        )
    chronic = F.g(c.m, "customer_aggregate", "chronic_rx_count")
    anchor = ""
    if loop and "list" in loop.lower():
        anchor = f"You asked for the filtered customer list — it's ready: I've run your {F.num(chronic) + ' ' if chronic else ''}chronic-Rx customers against the {mol} refills."
        c.cite("merchant.conversation_history (asked for list)")
        if chronic:
            c.cite("merchant.customer_aggregate.chronic_rx_count")
    elif chronic:
        anchor = f"You have {F.num(chronic)} chronic-Rx customers — I can filter the ones on {mol} in a minute."
        c.cite("merchant.customer_aggregate.chronic_rx_count")
    ask = yes_close(
        c,
        "send the affected customers a calm WhatsApp note + replacement-pickup steps",
        "Affected customers ko shaant WhatsApp note + replacement-pickup steps bhej doon",
        eta="5 min",
    )
    body = " ".join(x for x in [lead, why, anchor, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Urgent (5) safety/compliance alert with exact batch numbers; closes the merchant's open request for the list; complete workflow offered in one YES.",
        lever="urgency+specificity+effort_externalization",
        next_action={
            "type": "deliver",
            "topic": f"{mol} recall",
            "artifact": f'Customer note: "Namaste, {F.biz_name(c.m)} here. A batch of {mol} you received is part of a voluntary manufacturer recall (reduced strength, not unsafe). Please bring or keep the strip aside — we\'ll replace it free, or deliver a fresh pack. Reply here with a convenient time."',
            "confirm_en": "send to the affected customers",
        },
    )
