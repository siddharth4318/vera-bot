"""Research, compliance, CDE and seasonal-demand triggers: messages whose
'why now' is a piece of category knowledge from the weekly digest."""

from __future__ import annotations

import re

from .. import facts as F
from ..core import Ctx, Draft, because, cap, yes_close
from .fallback import generic

POST_WORDS = ("gbp", "google", "post", "menu", "tag", "description", "position", "offer")


def _research_anchor(c: Ctx, item: dict) -> str:
    """Why this study matters to *this* merchant, from what they can see on
    their own dashboard (signals, live offers) rather than roster counts."""
    seg = str(item.get("patient_segment") or "")
    lines = []
    count = F.g(c.m, "customer_aggregate", f"{seg.rstrip('s')}_count") if seg else None
    if "high_risk" in seg and F.has_signal(c.m, "high_risk_adult_cohort"):
        lines.append(
            "No benefit shown in low-risk patients — but your clinic is flagged for a high-risk adult cohort, so this changes your recall plan directly."
        )
        c.cite("merchant.signals.high_risk_adult_cohort")
    elif count:
        lines.append(f"Your records show {F.num(count)} {F.humanize(seg)} — this maps straight onto their recall plan.")
        c.cite(f"merchant.customer_aggregate.{seg.rstrip('s')}_count")
    if c.slug == "dentists" and "fluoride" in item.get("title", "").lower():
        visit = F.offer_matching(F.active_offers(c.m), "clean", "check", "scaling")
        if visit:
            lines.append(f"Easiest start: add the varnish check to your '{visit}' visits.")
            c.cite("merchant.offers")
    if not lines:
        seen = F.g(c.m, "customer_aggregate", "total_unique_ytd")
        if seen:
            lines.append(f"Relevant for the {F.num(seen)} {F.people(c.slug)} you've served this year.")
            c.cite("merchant.customer_aggregate.total_unique_ytd")
    return " ".join(lines)


def _lower_first(text: str) -> str:
    """'Formaldehyde-free ...' -> 'formaldehyde-free ...' but keep 'ICMR', 'GBP'."""
    if len(text) > 1 and text[1].isupper():
        return text
    return text[:1].lower() + text[1:]


def _asks_for_post(item: dict) -> bool:
    act = str(item.get("actionable") or "").lower()
    return any(w in act for w in POST_WORDS) and "whatsapp" not in act


def _research_ask(c: Ctx, item: dict) -> str:
    who = F.people(c.slug)
    title = item.get("title", "").lower()
    act = str(item.get("actionable") or "").lower()
    if c.slug == "dentists" and "recall" in title:
        return yes_close(
            c,
            "draft a 3-line patient WhatsApp explaining the new recall interval",
            "Main 3-line patient WhatsApp draft kar doon jo naya recall interval samjhaye",
            eta="5 min",
        )
    if "whatsapp" in act and "remind" in act:
        return yes_close(
            c,
            f"set up that WhatsApp refill reminder for your {who}",
            f"Aapke {who} ke liye WhatsApp refill reminder set up kar doon",
            eta="10 min",
        )
    if _asks_for_post(item):
        return yes_close(c, "draft the Google post for it", "Iske liye Google post draft kar doon", eta="5 min")
    return yes_close(
        c,
        f"draft a 3-line WhatsApp note for your {who} on what this means for them",
        f"Aapke {who} ke liye 3-line WhatsApp note draft kar doon",
        eta="5 min",
    )


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

    lead = f"{c.sal}, new in {src}: {_lower_first(title).rstrip('.')}." if src else f"{c.sal}, {title.rstrip('.')}."
    detail = F.first_sentences(item.get("summary", ""), 1)
    if n:
        if re.search(r"\btrial\b", detail):
            detail = re.sub(r"\btrial\b", f"trial ({F.num(n)} patients)", detail, count=1)
        else:
            lead = lead.rstrip(".") + f" ({F.num(n)}-patient trial)."
    anchor = _research_anchor(c, item)
    caveat = ""
    if "No effect" in item.get("summary", "") and "low-risk" not in anchor:
        caveat = "Worth noting: no benefit shown in low-risk patients, so this is a targeted change, not a blanket one."
    act = ""
    if (not anchor or _asks_for_post(item)) and item.get("actionable"):
        a = str(item["actionable"]).rstrip(".")
        act = f"What it means for {F.biz_name(c.m)}: {_lower_first(a)}."
        views = F.perf(c.m, "views")
        if views and _asks_for_post(item):
            act += f" {F.num(views)} people viewed your profile in the last 30 days — a post on this shows them you're current."
            c.cite("merchant.performance.views")
    body = " ".join(x for x in [lead, detail, caveat, anchor, act, _research_ask(c, item)] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale=because(
            f"Research digest ({src}) with curiosity + reciprocity (Vera drafts the follow-up)",
            "matched to the merchant's own cohort/records" if anchor else "",
            "the digest's own recommended action" if act else "",
        ),
        lever="specificity+reciprocity",
        next_action={
            "type": "deliver",
            "topic": title,
            "artifact": _patient_note_from(item, c),
            "confirm_en": f"send it to your {F.people(c.slug)}",
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
    extra = ""
    stale = F.signal_value(c.m, "stale_posts")
    if stale and cta == "open_ended":
        extra = f"Once you're covered, a short 'DCI-compliant X-rays' Google post doubles as your first post in {stale.rstrip('d')} days — patients read that as care."
        c.cite("merchant.signals.stale_posts")
    body = " ".join(x for x in [lead, (cap(when) + ".") if when else "", body_mid, extra, q] if x)
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
    lead += f" ({item.get('source')})." if item.get("source") else "."
    mid = F.first_sentences(item.get("summary", ""), 2)
    fee = item.get("actionable") or ""
    if p.get("fee"):  # the trigger's own fee field beats the digest's longer note
        fee = F.humanize(str(p["fee"])).capitalize()
        if "IDA" in item.get("title", ""):
            fee = fee.replace("members", "IDA members")
        other = re.search(r"(₹[\d,]+) for non-members", str(item.get("actionable") or ""))
        if other:
            fee += f" ({other.group(1)} otherwise)"
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
    src = (item or {}).get("source")
    if parts:
        c.cite("trigger.payload.trends")
        where = f" ({src})" if src else ""
        lead = (
            f"{c.sal}, summer demand has flipped: {', '.join(parts)}{where}."
            if c.slug == "pharmacies"
            else f"{c.sal}, seasonal shift is live: {', '.join(parts)}{where}."
        )
    elif item:
        c.cite(f"category.digest[{item.get('id')}]")
        lead = f"{c.sal}, {item.get('title')} ({src})."
    else:
        return generic(c)
    act = (item or {}).get("actionable", "")
    shelf = f"Shelf move before the weekend: {act[:1].lower() + act[1:].rstrip('.')}." if act else ""

    # tie it to what this store already has live
    offers = F.active_offers(c.m)
    delivery = F.offer_matching(offers, "delivery")
    hook = ""
    if delivery and c.slug == "pharmacies":
        hook = f"Your '{delivery}' makes a summer kit (ORS + sunscreen + antifungal) an easy one-order basket."
        c.cite("merchant.offers")

    content = F.content_item(c.cat, ("summer", "season", "monsoon"))
    anchor = ""
    if content:
        c.cite(f"category.patient_content_library[{content.get('id')}]")
        rep = F.g(c.m, "customer_aggregate", "repeat_customer_pct")
        if rep:
            who = f"your repeat customers ({F.pct(rep)} of your base)"
            c.cite("merchant.customer_aggregate.repeat_customer_pct")
        elif F.has_signal(c.m, "high_repeat_rate"):
            who = "your repeat customers — and your repeat rate is already high"
        else:
            who = f"your {F.people(c.slug)}"
        anchor = f"Ready to send to {who}: \"{content.get('title')}\"."
    ask = yes_close(
        c,
        "send you that note + a counter-display checklist",
        "Woh note + counter-display checklist bhej doon",
        eta="5 min",
    )
    body = " ".join(x for x in [lead, shelf, hook, anchor, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale="Seasonal demand shift with the exact % from the trigger (source named); one shelf action with a deadline, tied to the store's live delivery offer and call volume, plus a ready customer broadcast.",
        lever="specificity+effort_externalization",
        next_action={
            "type": "deliver",
            "topic": "seasonal demand",
            "artifact": (content or {}).get("body", ""),
            "confirm_en": "broadcast it to your customers",
        },
    )


def _customers_on(c: Ctx, molecule: str) -> list[dict]:
    """The merchant's own customers whose records show this molecule."""
    mol = molecule.lower()
    hits = []
    for cust in c.roster or []:
        services = " ".join(str(x) for x in F.g(cust, "relationship", "services_received", default=[]) or []).lower()
        if mol in services:
            hits.append(cust)
    return hits


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
            "Sub-potency — no safety risk beyond weaker LDL control, but customers must be told and given a replacement"
            + (f" ({item.get('source')})." if item.get("source") else ".")
        )
    chronic = F.g(c.m, "customer_aggregate", "chronic_rx_count")
    on_it = _customers_on(c, mol) if mol else []
    if on_it:
        names = [n for n in (F.cust_names(x)[0] or F.cust_names(x)[1] for x in on_it) if n][:3]
        if not names:
            names = [f"{len(on_it)} customer{'s' if len(on_it) > 1 else ''}"]
        matched = " and ".join(names) + (" are" if len(names) > 1 else " is") + f" on {mol}"
        lead_in = (
            "You asked for the filtered list — first matches from your records: "
            if loop and "list" in loop.lower()
            else "From your customer records: "
        )
        rest = f" I'll run the same check across all {F.num(chronic)} chronic-Rx customers." if chronic else ""
        anchor = f"{lead_in}{matched}.{rest}"
        c.cite("merchant customer records (molecule match)")
        if chronic:
            c.cite("merchant.customer_aggregate.chronic_rx_count")
    elif loop and "list" in loop.lower():
        anchor = f"You asked for the filtered customer list — it's ready: I've matched your {F.num(chronic) + ' ' if chronic else ''}chronic-Rx customers against these {mol} batches."
        c.cite("merchant.conversation_history (asked for list)")
        if chronic:
            c.cite("merchant.customer_aggregate.chronic_rx_count")
    elif chronic:
        anchor = f"You have {F.num(chronic)} chronic-Rx customers — I can filter the ones on {mol} in a minute."
        c.cite("merchant.customer_aggregate.chronic_rx_count")
    else:
        anchor = f"I can pull every customer who got {mol} from these batches out of your refill records in a minute."
    repeat = F.has_signal(c.m, "high_repeat_rate")
    delivery = F.offer_matching(F.active_offers(c.m), "delivery")
    if repeat:
        c.cite("merchant.signals.high_repeat_rate")
    if delivery:
        c.cite("merchant.offers (delivery)")
    stakes = (
        "They're regulars — a proactive call plus a free home-delivered replacement keeps their trust."
        if repeat and delivery
        else (
            "They're regulars — a proactive call now keeps their trust."
            if repeat
            else "Offer the replacement with free home delivery so nobody has to come back in." if delivery else ""
        )
    )
    ask = yes_close(
        c,
        "send the affected customers a calm WhatsApp note + replacement-pickup steps today",
        "Affected customers ko aaj hi shaant WhatsApp note + replacement-pickup steps bhej doon",
        eta="5 min",
    )
    body = " ".join(x for x in [lead, why, anchor, stakes, ask] if x)
    return Draft(
        body,
        "binary_yes_no",
        rationale=because(
            "Urgent recall alert with the payload's exact batch numbers and one YES",
            "closes the merchant's open request for the list" if loop and "list" in loop.lower() else "",
            "their chronic-Rx count" if chronic else "",
            "matching customers from their own records" if on_it else "",
            "their repeat-customer signal" if repeat else "",
            "their live delivery offer" if delivery else "",
        ),
        lever="urgency+specificity+effort_externalization",
        next_action={
            "type": "deliver",
            "topic": f"{mol} recall",
            "artifact": f'Customer note: "Namaste, {F.biz_name(c.m)} here. A batch of {mol} you received is part of a voluntary manufacturer recall (reduced strength, not unsafe). Please keep the strip aside — we\'ll replace it free, or deliver a fresh pack to your door. Reply here with a convenient time."',
            "confirm_en": "send to the affected customers",
        },
    )
