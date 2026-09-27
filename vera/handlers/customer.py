"""
Customer-facing handlers (send_as = "merchant_on_behalf").

Rules enforced here:
- Only the merchant's *live* offers are quoted to customers. Catalog templates
  are never promised to a customer (that would be a fake offer).
- Language follows customer.identity.language_pref (hi / hi-en mix / english /
  regional-mix gets a regional greeting + English).
- Slots come from the trigger payload; otherwise we name the customer's
  preferred window and ask them to pick a time — never invent a slot.
- Tone: no guilt, no medical claims, one reply action.
"""

from __future__ import annotations

import re

from .. import facts as F
from ..core import Ctx, Draft, cap


def possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


SERVICE_WORDS = {
    "dentists": ("check-up", "cleaning", "checkup"),
    "salons": ("appointment", "hair spa", "haircut"),
    "gyms": ("session", "class", "trial"),
    "restaurants": ("table", "thali", "order"),
    "pharmacies": ("refill", "delivery", "check"),
}


class CV:
    """Customer voice helper."""

    def __init__(self, c: Ctx):
        self.c = c
        self.lang = F.customer_lang(c.cust)
        self.name, self.to = F.cust_names(c.cust)
        self.child = self.name != self.to

    def t(self, en: str, hi: str | None = None, mix: str | None = None) -> str:
        if self.lang == "hi":
            return hi or mix or en
        if self.lang == "hinglish":
            return mix or en
        return en

    def greet(self) -> str:
        who = self.to or ""
        if self.lang == "hi":
            return "Namaste" + (f" {who} ji" if who else "") + "!"
        if self.lang in F.REGIONAL_GREETING:
            return F.REGIONAL_GREETING[self.lang] + (f" {who}" if who else "") + "!"
        return f"Hi {who}!" if who else "Hi!"

    def sender(self, emoji: str = "") -> str:
        c = self.c
        biz, loc = F.biz_name(c.m), F.locality(c.m)
        first = F.owner_first(c.m)
        if c.slug in ("salons", "gyms") and first:
            base = f"{first} from {biz}"
        else:
            base = biz
        if loc:
            base += f", {loc}"
        e = f" {emoji}" if emoji else ""
        return self.t(f"{base} here{e}.", f"{base} se{e}.", f"{base} se{e}.")

    def pref_window(self) -> str | None:
        p = F.g(self.c.cust, "preferences", "preferred_slots")
        return F.humanize(p) if p else None


NEXT_STEP = {
    "dentists": ("your 6-month check-up", "6-mahine wala check-up", "6-month check-up"),
    "salons": ("your next appointment", "agla appointment", "next appointment"),
    "gyms": ("your next session", "agla session", "next session"),
    "restaurants": ("your next meal with us", "agla khana hamare saath", "next meal"),
    "pharmacies": ("your next refill", "agla refill", "next refill"),
}

SOFT_VALUE = {  # category-safe, claim-free value lines for thin-data sends
    "dentists": (
        "A quick check every 6 months keeps small issues small.",
        "Har 6 mahine ka check-up chhoti dikkat ko chhota hi rakhta hai.",
        "Har 6 months ka check-up chhoti problems ko chhota hi rakhta hai.",
    ),
    "gyms": (
        "Even two sessions a week keeps the momentum going.",
        "Hafte mein do session bhi momentum banaye rakhte hain.",
        "Week mein 2 sessions bhi momentum banaye rakhte hain.",
    ),
    "pharmacies": (
        "We can keep your regular medicines packed and ready, or deliver them.",
        "Aapki regular dawaiyan hum pack karke rakh sakte hain, ya ghar bhej sakte hain.",
        "Aapki regular medicines hum ready rakh sakte hain, ya deliver kar sakte hain.",
    ),
}


def _order_cta(v: CV) -> str:
    return v.t(
        "Need anything this week? Reply with your list and we'll keep it ready.",
        "Is hafte kuch chahiye? Apni list bhej dijiye, hum taiyaar rakhenge.",
        "Is week kuch chahiye? Apni list reply kijiye, hum ready rakhenge.",
    )


def _live_offer(c: Ctx, *kw: str) -> str | None:
    offers = F.active_offers(c.m)
    if kw:
        return F.offer_matching(offers, *kw)
    return offers[0] if offers else None


def _slots(c: Ctx) -> list[str]:
    s = c.payload.get("available_slots") or c.payload.get("next_session_options") or []
    return [x.get("label") for x in s if isinstance(x, dict) and x.get("label")]


def _slot_cta(v: CV, slots: list[str]) -> tuple[str, str]:
    slug = v.c.slug
    if not slots and slug == "restaurants":
        return (
            v.t(
                "Reply YES and we'll keep a table for you this week — or order on WhatsApp anytime.",
                "HAAN likhiye, is hafte aapke liye table rakh denge — ya kabhi bhi WhatsApp par order kijiye.",
                "YES reply kijiye, is week aapke liye table rakh denge — ya WhatsApp par order kijiye.",
            ),
            "binary_yes_no",
        )
    if not slots and slug == "pharmacies":
        return _order_cta(v), "open_ended"
    if len(slots) >= 2:
        return (
            v.t(
                f"Reply 1 for {slots[0]}, 2 for {slots[1]} — or send a time that suits you.",
                f"{slots[0]} ke liye 1, {slots[1]} ke liye 2 likhiye — ya apna samay bata dijiye.",
                f"{slots[0]} ke liye 1, {slots[1]} ke liye 2 reply kijiye — ya jo time suit kare woh bata dijiye.",
            ),
            "multi_choice_slot",
        )
    if len(slots) == 1:
        return (
            v.t(
                f"Reply YES to lock {slots[0]}.",
                f"{slots[0]} pakka karne ke liye HAAN likhiye.",
                f"{slots[0]} lock karne ke liye YES reply kijiye.",
            ),
            "binary_yes_no",
        )
    w = v.pref_window()
    return (
        v.t(
            f"Reply YES and we'll hold a {w} slot for you." if w else "Reply YES and we'll share this week's slots.",
            (
                f"HAAN likhiye, hum aapke liye {w} ka slot rakh denge."
                if w
                else "HAAN likhiye, hum is hafte ke slots bhej denge."
            ),
            (
                f"YES reply kijiye, hum aapke liye {w} slot hold kar lenge."
                if w
                else "YES reply kijiye, hum is hafte ke slots bhej denge."
            ),
        ),
        "binary_yes_no",
    )


def _months_ago(c: Ctx, iso: str | None) -> int | None:
    if not iso:
        return None
    ref = c.now
    if not ref:
        return None
    d = F.days_between(iso, ref)
    return round(d / 30) if d and d > 20 else None


# --------------------------------------------------------------------------- #


def recall_due(c: Ctx) -> Draft:
    v = CV(c)
    p = c.payload
    rel = (c.cust or {}).get("relationship") or {}
    last = p.get("last_service_date") or rel.get("last_visit")
    due = p.get("due_date")
    svc = F.humanize(p.get("service_due") or "").replace("6 month", "6-month")
    slots = _slots(c)
    c.cite("trigger.payload recall + customer.relationship")
    offer = _live_offer(c, "clean", "check", "spa", "trial") if c.slug != "restaurants" else None
    emoji = {"dentists": " 🦷", "salons": " ✨", "gyms": " 💪"}.get(c.slug, "")
    parts = [v.greet(), v.sender(emoji.strip())]
    if svc and last:
        parts.append(
            v.t(
                f"Your last visit was on {F.day_month(last)}, so your {svc} is due"
                + (f" by {F.day_month(due)}." if due else " now."),
                f"Aapki pichhli visit {F.day_month(last)} ko thi, isliye aapka {svc}"
                + (f" {F.day_month(due)} tak due hai." if due else " ab due hai."),
                f"Aapki last visit {F.day_month(last)} ko thi — aapka {svc}"
                + (f" {F.day_month(due)} tak due hai." if due else " ab due hai."),
            )
        )
    elif last:
        nxt = NEXT_STEP.get(c.slug, ("your next visit", "agli visit", "next visit"))
        parts.append(
            v.t(
                f"Your last visit was on {F.day_month(last)} — it's a good time to plan {nxt[0]}.",
                f"Aapki pichhli visit {F.day_month(last)} ko thi — ab {nxt[1]} plan karne ka sahi samay hai.",
                f"Last visit {F.day_month(last)} ko thi — ab {nxt[2]} plan karne ka sahi time hai.",
            )
        )
        tip = SOFT_VALUE.get(c.slug)
        if tip and not slots:
            parts.append(v.t(*tip))
    if slots:
        w = v.pref_window()
        parts.append(
            v.t(
                f"We've kept {len(slots)} {w + ' ' if w else ''}slot{'s' if len(slots) > 1 else ''} for you.",
                f"Aapke liye {len(slots)} slot rakhe hain.",
                f"Aapke liye {len(slots)} {w + ' ' if w else ''}slot{'s' if len(slots) > 1 else ''} ready hain.",
            )
        )
    if offer:
        parts.append(v.t(f"Current offer: {offer}.", f"Abhi ka offer: {offer}.", f"Current offer: {offer}."))
        c.cite("merchant.offers (active)")
    cta, kind = _slot_cta(v, slots) if c.slug != "pharmacies" else (_order_cta(v), "binary_yes_no")
    parts.append(cta)
    return Draft(
        " ".join(parts),
        kind,
        rationale="Customer recall: names the real last-visit date and due date, offers only the merchant's live offer and real slots, honours language + time preference; single reply action.",
        lever="personal_specificity+low_friction",
        next_action={"type": "book", "slots": slots, "topic": "recall"},
    )


def appointment_tomorrow(c: Ctx) -> Draft:
    v = CV(c)
    p = c.payload
    when = p.get("slot_label") or F.weekday_day_month(p.get("appointment_iso") or p.get("slot_iso"))
    svc = p.get("service")
    c.cite("trigger.kind appointment_tomorrow")
    parts = [v.greet(), v.sender()]
    what = F.humanize(svc) if svc else ("table reservation" if c.slug == "restaurants" else "appointment")
    parts.append(
        v.t(
            f"Just a reminder of your {what} tomorrow" + (f" ({when})." if when else "."),
            f"Kal aapka {what} hai" + (f" ({when})." if when else ", yaad dila rahe hain."),
            f"Reminder — kal aapka {what} hai" + (f" ({when})." if when else "."),
        )
    )
    visits = F.g(c.cust, "relationship", "visits_total")
    if visits and visits >= 3:
        parts.append(v.t("Always good to see you.", "Aapka intezaar rahega.", "Aapka wait rahega."))
    parts.append(
        v.t(
            "Reply YES to confirm, or tell us a better time and we'll move it.",
            "Pakka karne ke liye HAAN likhiye, ya naya samay bata dijiye.",
            "Confirm karne ke liye YES reply kijiye, ya better time bata dijiye — hum shift kar denge.",
        )
    )
    return Draft(
        " ".join(parts),
        "binary_confirm_cancel",
        rationale="Appointment reminder: confirm-or-reschedule in one reply; no invented time when the trigger has none.",
        lever="commitment+low_friction",
        next_action={"type": "confirm", "topic": "appointment"},
    )


def customer_lapsed(c: Ctx) -> Draft:
    v = CV(c)
    p = c.payload
    rel = (c.cust or {}).get("relationship") or {}
    days = p.get("days_since_last_visit")  # only trust an explicit number
    focus = p.get("previous_focus") or F.g(c.cust, "preferences", "training_focus")
    months = p.get("previous_membership_months")
    offer = _live_offer(c)
    parts = [v.greet(), v.sender()]
    c.cite("trigger.payload lapse + customer.relationship")
    if days and days >= 14:
        weeks = round(days / 7)
        span = f"about {weeks} weeks" if days < 90 else f"about {round(days / 30)} months"
        span_hi = f"lagbhag {weeks} hafte" if days < 90 else f"lagbhag {round(days / 30)} mahine"
        parts.append(
            v.t(
                f"It's been {span} since we last saw you — happens to everyone, no judgment.",
                f"Aapko dekhe hue {span_hi} ho gaye — aisa sabke saath hota hai.",
                f"Aapko dekhe {span_hi} ho gaye — hota hai, koi baat nahi.",
            )
        )
    elif rel.get("last_visit"):
        parts.append(
            v.t(
                f"We haven't seen you since {F.day_month(rel['last_visit'])} — hope all is well.",
                f"{F.day_month(rel['last_visit'])} ke baad aap nahi aaye — ummeed hai sab theek hai.",
                f"{F.day_month(rel['last_visit'])} ke baad aapse mulaqat nahi hui — hope all good!",
            )
        )
    if focus:
        parts.append(
            v.t(
                f"Your {F.humanize(focus)} plan is right where you left it"
                + (f" after {months} months of work." if months else "."),
                f"Aapka {F.humanize(focus)} plan wahin se shuru ho sakta hai"
                + (f" — {months} mahine ki mehnat bekaar nahi jaane denge." if months else "."),
                f"Aapka {F.humanize(focus)} plan wahin se continue ho sakta hai"
                + (f" — {months} months ki progress hai." if months else "."),
            )
        )
    w = v.pref_window()
    if offer:
        parts.append(
            v.t(
                f"To ease back in: {offer}" + (f", {w} slots open." if w else "."),
                f"Wapas shuru karne ke liye: {offer}" + (f", {w} ke slots khaali hain." if w else "."),
                f"Easy restart ke liye: {offer}" + (f", {w} slots available." if w else "."),
            )
        )
        c.cite("merchant.offers (active)")
    if not focus and not offer:
        tip = SOFT_VALUE.get(c.slug)
        if tip:
            parts.append(v.t(*tip))
    if c.slug == "pharmacies":
        parts.append(_order_cta(v))
    elif c.slug == "restaurants":
        parts.append(
            v.t(
                "Want us to keep a table for you this week? Reply YES.",
                "Is hafte aapke liye table rakh dein? HAAN likhiye.",
                "Is week aapke liye table rakh dein? YES reply kijiye.",
            )
        )
    else:
        parts.append(
            v.t(
                "Want us to hold a spot this week? Reply YES — no commitment.",
                "Is hafte ek slot rakh dein? HAAN likhiye — koi zabardasti nahi.",
                "Is week ek slot hold kar dein? YES reply kijiye — no commitment.",
            )
        )
    return Draft(
        " ".join(parts),
        "binary_yes_no",
        rationale="Lapsed customer win-back: no-guilt framing, references their own goal/history, only the merchant's live offer, single no-commitment YES.",
        lever="no_shame+personal_goal+low_friction",
        next_action={"type": "book", "topic": "winback"},
    )


def trial_followup(c: Ctx) -> Draft:
    v = CV(c)
    p = c.payload
    trial = p.get("trial_date")
    slots = _slots(c)
    who = v.name if v.child else None
    parts = [v.greet(), v.sender("🙏")]
    c.cite("trigger.payload trial + customer.identity")
    if trial:
        parts.append(
            v.t(
                f"{possessive(who) if who else 'Your'} first trial class with us was on {F.day_month(trial)} — thank you for coming!",
                f"{who + ' ne' if who else 'Aapne'} {F.day_month(trial)} ko hamare saath trial class ki thi — dhanyavaad!",
                f"{who + ' ne' if who else 'Aapne'} {F.day_month(trial)} ko trial class ki thi — thank you!",
            )
        )
    rel = (c.cust or {}).get("relationship") or {}
    if not trial and rel.get("last_visit"):
        nxt = NEXT_STEP.get(c.slug, ("your next visit", "agli visit", "next visit"))
        parts.append(
            v.t(
                f"Thanks for trying us out — your last visit was on {F.day_month(rel['last_visit'])}. It's a good time to plan {nxt[0]}.",
                f"Humein try karne ke liye dhanyavaad — aapki pichhli visit {F.day_month(rel['last_visit'])} ko thi. Ab {nxt[1]} plan karne ka sahi samay hai.",
                f"Try karne ke liye thank you — last visit {F.day_month(rel['last_visit'])} ko thi. Ab {nxt[2]} plan karne ka sahi time hai.",
            )
        )
        tip = SOFT_VALUE.get(c.slug)
        if tip:
            parts.append(v.t(*tip))
    if slots:
        pref = v.pref_window()
        parts.append(
            v.t(
                f"Next session: {slots[0]}" + (f" ({pref}, as you prefer)." if pref else "."),
                f"Agla session: {slots[0]}.",
                f"Next session: {slots[0]}" + (f" — {pref}, jaisa aapko pasand hai." if pref else "."),
            )
        )
    offer = None if v.child else _live_offer(c)  # adult offers aren't pitched for a child's program
    if offer:
        parts.append(v.t(f"To continue: {offer}.", f"Aage ke liye: {offer}.", f"Continue karne ke liye: {offer}."))
    if c.slug in ("pharmacies", "restaurants") and not slots:
        cta, k = _slot_cta(v, [])
        parts.append(cta)
        return Draft(
            " ".join(parts),
            k,
            rationale="Trial follow-up with thin data: relationship date + category-appropriate next step; single reply.",
            lever="momentum+low_friction",
            next_action={"type": "book", "topic": "trial"},
        )
    parts.append(
        v.t(
            f"Shall we save {possessive(who) if who else 'your'} spot? Reply YES.",
            "Jagah pakki kar dein? HAAN likhiye.",
            f"{who + ' ka' if who else 'Aapka'} spot save kar dein? YES reply kijiye.",
        )
    )
    return Draft(
        " ".join(parts),
        "binary_yes_no",
        rationale="Trial follow-up while intent is fresh; addresses the parent for a child, uses the real next session and preference.",
        lever="momentum+low_friction",
        next_action={"type": "book", "slots": slots, "topic": "trial"},
    )


def _recalled_molecule(cat: dict, molecules: list[str]) -> tuple[str, dict] | tuple[None, None]:
    """Is any of the customer's molecules named in a recall alert in this week's digest?"""
    for item in cat.get("digest") or []:
        if item.get("kind") not in ("alert", "recall"):
            continue
        text = (item.get("title", "") + " " + item.get("summary", "")).lower()
        for mol in molecules:
            if mol.lower() in text:
                return mol, item
    return None, None


def _refill_perks(v: CV, offers: list[str], senior: bool, address_saved: bool) -> list[str]:
    """Only the pharmacy's live offers, phrased for the customer's language."""
    perks = []
    senior_offer = F.offer_matching(offers, "senior")
    if senior and senior_offer:
        pct = re.search(r"\d+%", senior_offer)
        pct = pct.group(0) if pct else ""
        perks.append(
            v.t(
                f"your senior-citizen {pct} discount applies",
                f"senior citizen {pct} chhoot lagegi",
                f"senior {pct} discount lagega",
            )
        )
    delivery = F.offer_matching(offers, "delivery")
    if delivery:
        threshold = F.price_in(delivery)
        to_home = " to the saved address" if address_saved else ""
        to_home_hi = " saved address par" if address_saved else ""
        if threshold:
            perks.append(
                v.t(
                    f"free home delivery{to_home} on orders above {threshold}",
                    f"{threshold} se upar free home delivery{to_home_hi}",
                    f"{threshold} se upar free home delivery{to_home_hi}",
                )
            )
        else:
            perks.append(
                v.t(
                    f"free home delivery{to_home}", f"free home delivery{to_home_hi}", f"free home delivery{to_home_hi}"
                )
            )
    return perks


def chronic_refill_due(c: Ctx) -> Draft:
    if c.slug != "pharmacies":
        return recall_due(c)  # a "refill" at a clinic/salon is really a follow-up visit

    v = CV(c)
    p = c.payload
    molecules = p.get("molecule_list") or []
    runs_out = F.day_month(p.get("stock_runs_out_iso"))
    c.cite("trigger.payload molecules + runout date")

    # messages to a senior often go through a son/daughter's phone: refer to the patient as "<name> ji"
    via_family = "via" in str(F.g(c.cust, "preferences", "channel", default=""))
    patient = (v.name or "").replace("Mr. ", "")
    ref = f"{patient} ji" if patient and via_family else patient

    parts = ["Namaste!", v.sender()]
    if molecules:
        mol_list, n = ", ".join(molecules), len(molecules)
        whose, whose_hi = (possessive(ref), f"{ref} ki") if ref else ("Your", "Aapki")
        if runs_out:
            parts.append(
                v.t(
                    f"{whose} {n} monthly medicines ({mol_list}) will run out on {runs_out}.",
                    f"{whose_hi} {n} mahine ki dawaiyan ({mol_list}) {runs_out} tak khatam ho jayengi.",
                    f"{whose_hi} {n} monthly medicines ({mol_list}) {runs_out} ko khatam hongi.",
                )
            )
        else:
            parts.append(
                v.t(
                    f"{whose} monthly medicines ({mol_list}) are due for refill.",
                    f"{whose_hi} dawaiyan ({mol_list}) refill ke liye due hain.",
                    f"{whose_hi} medicines ({mol_list}) refill due hain.",
                )
            )
        parts.append(
            v.t(
                "Same dose, same brand — ready to pack.",
                "Wahi dose, wahi brand — pack karne ke liye taiyaar.",
                "Same dose, same brand — ready hai.",
            )
        )

    recalled, alert = _recalled_molecule(c.cat, molecules)
    if recalled:
        parts.append(
            v.t(
                f"Note: some {recalled} batches are under a voluntary recall this month — we'll dispense only from an unaffected batch.",
                f"Dhyan dein: is mahine {recalled} ke kuch batch recall hue hain — hum sirf safe batch se hi denge.",
                f"Note: is mahine {recalled} ke kuch batches recall mein hain — hum sirf unaffected batch hi denge.",
            )
        )
        c.cite(f"category.digest[{alert.get('id')}] cross-check")

    perks = _refill_perks(
        v, F.active_offers(c.m), bool(F.g(c.cust, "identity", "senior_citizen")), bool(p.get("delivery_address_saved"))
    )
    if perks:
        parts.append(cap(v.t(" and ".join(perks), ", aur ".join(perks), ", aur ".join(perks))) + ".")
        c.cite("merchant.offers (senior, delivery)")

    parts.append(
        v.t(
            "Reply YES to dispatch, or tell us if the dose has changed.",
            "Bhejne ke liye HAAN likhiye, ya dose mein koi badlav ho to bata dijiye.",
            "Dispatch ke liye YES reply kijiye, ya dose change hua ho to bata dijiye.",
        )
    )
    return Draft(
        " ".join(parts),
        "binary_yes_no",
        rationale=(
            "Chronic refill: exact molecules and run-out date, only the pharmacy's live perks, a recall cross-check "
            "against this week's digest, and a respectful family-channel address in the customer's language."
        ),
        lever="precision+trust+low_friction",
        next_action={"type": "confirm", "topic": "refill"},
    )


def wedding_followup(c: Ctx) -> Draft:
    v = CV(c)
    p = c.payload
    wd = p.get("wedding_date") or F.g(c.cust, "preferences", "wedding_date")
    days = p.get("days_to_wedding")
    if days is None and c.now and wd:
        days = F.days_between(c.now, wd)
    trial = p.get("trial_completed")
    step = F.humanize(p.get("next_step_window_open") or "").replace("30day", "30-day")
    c.cite("trigger.payload wedding + customer.preferences")
    parts = [v.greet() + " 💍", v.sender()]
    if days is not None and wd:
        parts.append(
            v.t(
                f"{days} days to your wedding on {F.day_month(wd)}!",
                f"Aapki shaadi {F.day_month(wd)} ko — {days} din baaki!",
                f"{F.day_month(wd)} ki wedding mein {days} din baaki!",
            )
        )
    if trial:
        parts.append(
            v.t(
                f"After your bridal trial on {F.day_month(trial)}, the next step is the {step or 'skin-prep program'}.",
                f"{F.day_month(trial)} ke bridal trial ke baad agla step hai {step or 'skin-prep program'}.",
                f"{F.day_month(trial)} ke bridal trial ke baad next step hai {step or 'skin-prep program'}.",
            )
        )
    beat = None
    for b in c.cat.get("seasonal_beats") or []:
        if "bridal" in b.get("note", "") and ("4x" in b.get("note", "") or "primary" in b.get("note", "")):
            beat = b
    if beat and wd and F.month_in_range(F.parse_dt(wd).month, beat["month_range"]):
        parts.append(
            v.t(
                f"{beat['month_range']} is peak wedding season (bookings run ~4x normal), so we'd suggest locking your dates early.",
                f"{beat['month_range']} peak shaadi season hai — dates jaldi pakki kar lein.",
                f"{beat['month_range']} peak wedding season hai (bookings ~4x) — dates early lock kar lijiye.",
            )
        )
        c.cite("category.seasonal_beats (bridal 4x)")
    w = v.pref_window()
    parts.append(
        v.t(
            (
                f"Want us to hold a {w} slot to plan your schedule? Reply YES."
                if w
                else "Want us to hold a slot to plan your schedule? Reply YES."
            ),
            (
                f"Schedule plan karne ke liye {w} ka slot rakh dein? HAAN likhiye."
                if w
                else "Schedule ke liye slot rakh dein? HAAN likhiye."
            ),
            (
                f"Schedule plan karne ke liye {w} slot hold kar dein? YES reply kijiye."
                if w
                else "Slot hold kar dein? YES reply kijiye."
            ),
        )
    )
    return Draft(
        " ".join(parts),
        "binary_yes_no",
        rationale="Bridal follow-up: countdown + trial continuity + peak-season scarcity from category data; no invented package price; honours Saturday preference.",
        lever="countdown+scarcity+low_friction",
        next_action={"type": "book", "topic": "bridal"},
    )


def generic_customer(c: Ctx) -> Draft:
    v = CV(c)
    kind = F.humanize(c.trg.get("kind", ""))
    rel = (c.cust or {}).get("relationship") or {}
    offer = _live_offer(c)
    parts = [v.greet(), v.sender()]
    if rel.get("last_visit"):
        parts.append(
            v.t(
                f"Thanks for visiting us — last on {F.day_month(rel['last_visit'])}.",
                f"Aapki pichhli visit {F.day_month(rel['last_visit'])} ko thi — dhanyavaad.",
                f"Last visit {F.day_month(rel['last_visit'])} ko thi — thank you!",
            )
        )
    if offer:
        parts.append(v.t(f"This week: {offer}.", f"Is hafte: {offer}.", f"Is week: {offer}."))
    cta, k = _slot_cta(v, _slots(c))
    parts.append(cta)
    return Draft(
        " ".join(parts),
        k,
        rationale=f"Customer trigger '{kind}' with thin data: relationship fact + live offer only; single reply.",
        lever="personal+low_friction",
        next_action={"type": "book", "topic": kind},
    )
