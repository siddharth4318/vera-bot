"""
Multi-turn reply engine for /v1/reply.

Priority ladder (first match wins) — ordered so the cheapest, most certain
signals are checked first and the bot never burns turns:

    1. opt-out ("stop", "not interested", "mat bhejo")      -> end, suppress merchant
    2. auto-reply (canned WA-Business text, or same text 2+) -> 1st: one owner-flag line,
                                                                2nd: wait 24h, 3rd+: end
    3. hostile w/o opt-out (abuse, "useless")               -> one de-escalation line
    4. slot pick ("1"/"2") on a booking thread              -> confirm booking
    5. commitment ("yes", "ok let's do it", "go ahead")     -> ACTION MODE: deliver the
                                                                artifact now, no more questions
    6. deferral ("later", "busy", "kal")                    -> wait N seconds
    7. plain decline ("no", "nahi")                         -> end
    8. out-of-scope ask (GST, loans, legal)                 -> polite decline + redirect
    9. question                                             -> answer from context + same CTA
   10. information / answer to our question                 -> use it, deliver artifact
Language is re-detected every turn (merchant may switch Hindi <-> English).
"""

from __future__ import annotations

import re
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import facts as F
from .composer import compose

# --------------------------------------------------------------------------- #
# Lexicons
# --------------------------------------------------------------------------- #

OPT_OUT = re.compile(
    r"\b(stop|unsubscribe|opt[\s-]?out|not interested|no interest|don'?t (message|msg|text|contact|send)|do not (message|contact|send|text)"
    r"|leave me alone|remove me|block(ing)? you|band karo|mat bhejo|mat karo|message mat|nahi chahiye|interest nahi|mujhe mat)\b",
    re.I,
)

HOSTILE = re.compile(
    r"\b(useless|nonsense|bakwas|bakwaas|idiot|stupid|shut up|bloody|damn|pagal|irritat\w*|annoy\w*|harass\w*|bothering|fraud|scam|spam|waste of time|faltu|bekar)\b",
    re.I,
)

AUTO_REPLY = re.compile(
    r"(thank(s| you) for (contacting|reaching|your message|messaging)|we will (get back|respond|reply)|will (respond|reply|get back) (shortly|soon|to you)"
    r"|our team will|currently (unavailable|away|closed)|out of office|business hours|auto(mated)?[\s-]?(reply|response|message)|i am an automated"
    r"|this is an automated|we are closed|aapki (madad|jaankari) ke liye (bahut[\s-]*)?(bahut )?shukriya|team tak pahuncha|jald hi (sampark|jawab)"
    r"|hamari team|for (urgent|immediate) (queries|help)|please (leave|drop) (a|your) message|visit us at|welcome to [\w\s']+! )",
    re.I,
)

ACCEPT = re.compile(
    r"^\s*(yes|yess+|yeah|yep|ya|haan|han|ha|hanji|haanji|ji|ok(ay)?|okk+|sure|done|confirm(ed)?|go ahead|go for it|proceed|chalo|chalega|theek hai|thik hai|"
    r"sounds good|perfect|great|please do|pls do|do it|kar do|kardo|kar dijiye|bhej do|bhejo|send( it)?|let'?s do( it)?|lets do( it)?|start|interested|👍|✅)\b",
    re.I,
)
ACCEPT_ANY = re.compile(
    r"\b(let'?s do it|lets do it|go ahead|please (send|share|do|draft|proceed)|send (me|it|the)|i want to (join|start|do)|mujhe (judna|join|chahiye)|judna hai|"
    r"what'?s next|whats next|next step|how do (i|we) (start|proceed)|sign me up|book (it|me)|kar do|bhej do|haan ji|yes please|ok let'?s)\b",
    re.I,
)

THANKS = re.compile(
    r"^\s*(thanks|thank you|thx|ty|shukriya|dhanyavaad|dhanyawad|great thanks|ok thanks|okay thanks|👍|🙏)\b[\w\s!.]*$",
    re.I,
)
SHOW_ME = re.compile(
    r"\b(what would|show me|send me|example|sample|kaisa dikhega|dikhao|let me see|what will it)\b", re.I
)
FILLER = re.compile(
    r"\b(actually|i think|mostly|probably|maybe|it'?s|its|it is|definitely|for sure|hai|tha|thi|sabse zyada|most|this week|is hafte|bhai|ji)\b",
    re.I,
)

DEFER = re.compile(
    r"\b(later|not now|busy|baad mein|baad me|abhi nahi|in a meeting|call (me )?later|tomorrow|kal|next week|evening|shaam|after \d+|thodi der|give me time)\b",
    re.I,
)
DECLINE = re.compile(
    r"^\s*(no|nope|nah|nahi|nahin|na|no thanks|no thank you|not needed|not required|zaroorat nahi)\b[.! ]*$", re.I
)

OUT_OF_SCOPE = re.compile(
    r"\b(gst|income tax|itr|tax filing|loan|emi|legal|lawyer|court|visa|passport|insurance claim|accountant|ca\b|payroll|website develop\w*|app develop\w*|crypto|stock tip)\b",
    re.I,
)
QUESTION = re.compile(
    r"\?|^\s*(what|how|why|when|where|which|who|can you|could you|will you|is it|are you|do you|kya|kaise|kitna|kitne|kab|kyun|kaun|kahan)\b",
    re.I,
)
PRICE_Q = re.compile(r"\b(price|cost|charges?|fees?|kitna|kitne|paisa|paise|how much|rate)\b", re.I)
WHO_Q = re.compile(
    r"\b(who (are|is) (you|this)|kaun (ho|hai)|what is (this|vera)|is this (a )?bot|are you (a )?(bot|human|real))\b",
    re.I,
)

TIME_HINT = re.compile(
    r"\b(\d{1,2}(:\d{2})?\s*(am|pm|baje)|morning|evening|afternoon|subah|shaam|monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun|tomorrow|kal|weekend)\b",
    re.I,
)

HINDI_TOKENS = {
    "hai",
    "hain",
    "nahi",
    "nahin",
    "kya",
    "karo",
    "kar",
    "kijiye",
    "chahiye",
    "aap",
    "aapka",
    "aapki",
    "mujhe",
    "haan",
    "theek",
    "thik",
    "acha",
    "accha",
    "bhai",
    "ji",
    "hoon",
    "main",
    "mein",
    "ko",
    "ka",
    "ki",
    "se",
    "par",
    "kal",
    "abhi",
    "bhejo",
    "batao",
    "kaise",
    "kitna",
    "wala",
    "wali",
    "sab",
    "bahut",
    "shukriya",
    "dhanyavaad",
    "karna",
    "hoga",
}

QUALIFYING = ("would you", "do you", "can you tell", "what if", "how about")


def detect_lang(msg: str, default: str) -> str:
    if re.search(r"[ऀ-ॿ]", msg):
        return "hinglish"
    toks = re.findall(r"[a-zA-Z]+", msg.lower())
    if not toks:
        return default
    hits = sum(1 for t in toks if t in HINDI_TOKENS)
    if hits >= 2 or (hits >= 1 and len(toks) <= 3):
        return "hinglish"
    if len(toks) >= 3 and hits == 0:
        return "en"
    return default


def norm(msg: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", msg.lower())).strip()


def defer_seconds(msg: str) -> int:
    m = msg.lower()
    if re.search(r"\b(next week|agle hafte)\b", m):
        return 7 * 86400
    if re.search(r"\b(tomorrow|kal)\b", m):
        return 86400
    if re.search(r"\b(evening|shaam|tonight|raat)\b", m):
        return 4 * 3600
    mt = re.search(r"(\d+)\s*(min|minute)", m)
    if mt:
        return max(300, int(mt.group(1)) * 60)
    mt = re.search(r"(\d+)\s*(hour|hr|ghante|ghanta)", m)
    if mt:
        return int(mt.group(1)) * 3600
    return 2 * 3600


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #


@dataclass
class Conv:
    conversation_id: str
    merchant_id: str | None = None
    customer_id: str | None = None
    trigger_id: str | None = None
    kind: str = ""
    send_as: str = "vera"
    next: dict = field(default_factory=dict)
    stage: str = "pitched"  # pitched -> delivered -> confirmed -> closed
    bot_bodies: list = field(default_factory=list)
    turns_in: int = 0
    auto_count: int = 0
    hostile_count: int = 0
    lang: str = "en"
    closed: bool = False


class ConversationEngine:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.convs: dict[str, Conv] = {}
        self.merchant_msgs: dict[str, Counter] = defaultdict(Counter)  # repeat-text detector across convs
        self.merchant_auto: dict[str, int] = defaultdict(int)
        self.opted_out: set[str] = set()

    # ---------------------------------------------------------------- setup
    def open(self, conv_id: str, action: dict, next_action: dict, lang: str):
        with self.lock:
            cv = Conv(
                conversation_id=conv_id,
                merchant_id=action.get("merchant_id"),
                customer_id=action.get("customer_id"),
                trigger_id=action.get("trigger_id"),
                kind=action.get("_kind", ""),
                send_as=action.get("send_as", "vera"),
                next=next_action or {},
                lang=lang,
            )
            cv.bot_bodies.append(action.get("body", ""))
            self.convs[conv_id] = cv

    def reset(self):
        with self.lock:
            self.convs.clear()
            self.merchant_msgs.clear()
            self.merchant_auto.clear()
            self.opted_out.clear()

    # ---------------------------------------------------------------- helpers
    def _ensure(self, body: dict) -> Conv:
        cid = body.get("conversation_id") or "conv_unknown"
        cv = self.convs.get(cid)
        if cv:
            return cv
        # Conversation we didn't start (or lost): rebuild a sensible pending action
        mid, cust_id = body.get("merchant_id"), body.get("customer_id")
        m = self.store.get("merchant", mid) or {}
        cat = self.store.get("category", m.get("category_slug")) or {}
        lang = F.merchant_lang(cat, m) if m else "en"
        nxt = {}
        if cust_id:

            cust = self.store.get("customer", cust_id)
            for _tid, trg in sorted(self.store.all("trigger").items()):
                if trg.get("customer_id") == cust_id and cust:
                    nxt = compose(cat, m, trg, cust).get("_next") or {}
                    break
            if not nxt:
                nxt = {"type": "book", "topic": "booking"}
            if cust:
                lang = "hinglish" if F.customer_lang(cust) in ("hi", "hinglish") else "en"
        elif m:

            r = compose(cat, m, {"id": f"reply_{cid}", "kind": "reply_context", "payload": {}}, None)
            nxt = r.get("_next") or {}
        cv = Conv(
            conversation_id=cid,
            merchant_id=mid,
            customer_id=cust_id,
            next=nxt,
            lang=lang,
            send_as="merchant_on_behalf" if body.get("from_role") == "customer" else "vera",
        )
        self.convs[cid] = cv
        return cv

    def _names(self, cv: Conv) -> tuple[str, dict, dict]:
        m = self.store.get("merchant", cv.merchant_id) or {}
        cat = self.store.get("category", m.get("category_slug")) or {}
        sal = F.salutation(cat.get("slug", ""), m) if m else ""
        return sal, m, cat

    def _send(self, cv: Conv, body: str, cta: str, rationale: str) -> dict:
        body = body.strip()
        if body in cv.bot_bodies:  # anti-repetition guard
            return self._end(cv, "Next reply would repeat an earlier message verbatim; closing instead of looping.")
        cv.bot_bodies.append(body)
        return {"action": "send", "body": body, "cta": cta, "rationale": rationale}

    def _end(self, cv: Conv, rationale: str) -> dict:
        cv.closed = True
        cv.stage = "closed"
        return {"action": "end", "rationale": rationale}

    def _wait(self, cv: Conv, secs: int, rationale: str) -> dict:
        return {"action": "wait", "wait_seconds": int(secs), "rationale": rationale}

    def _t(self, cv: Conv, en: str, hi: str) -> str:
        return hi if cv.lang == "hinglish" else en

    # ---------------------------------------------------------------- main
    def reply(self, body: dict) -> dict:
        with self.lock:
            msg = str(body.get("message") or "").strip()
            known = (body.get("conversation_id") in self.convs) and bool(
                self.convs[body.get("conversation_id")].bot_bodies
            )
            cv = self._ensure(body)
            cv.turns_in += 1
            if cv.closed:
                return self._end(cv, "Conversation already closed; not re-engaging.")
            if not msg:
                return self._wait(cv, 1800, "Empty message; waiting for a real reply.")
            cv.lang = detect_lang(msg, cv.lang)
            is_customer = body.get("from_role") == "customer" or cv.send_as == "merchant_on_behalf"
            n = norm(msg)
            mkey = cv.merchant_id or "_"
            self.merchant_msgs[mkey][n] += 1
            seen = self.merchant_msgs[mkey][n]

            # 1. opt-out
            if OPT_OUT.search(msg):
                if cv.merchant_id and not is_customer and known:
                    self.opted_out.add(cv.merchant_id)  # only for threads Vera actually started
                return self._end(
                    cv, "Explicit opt-out; closing and suppressing further proactive sends to this contact."
                )

            # 2. auto-reply
            if AUTO_REPLY.search(msg) or (seen >= 2 and len(n) > 25):
                self.merchant_auto[mkey] += 1
                cv.auto_count += 1
                k = max(cv.auto_count, self.merchant_auto[mkey])
                if k == 1:
                    sal, m, cat = self._names(cv)
                    ask = cv.next.get("confirm_en") or "get this started"
                    b = self._t(
                        cv,
                        f"Looks like an auto-reply 🙂 {('For ' + sal + ': ') if sal else ''}when you see this, just reply YES and I'll {ask}.",
                        f"Lagta hai yeh auto-reply hai 🙂 {(sal + ', ') if sal else ''}jab aap dekhein, bas YES bhej dijiye — main {ask} kar dungi.",
                    )
                    return self._send(
                        cv,
                        b,
                        "binary_yes_no",
                        "Detected WhatsApp Business auto-reply (canned phrasing); one short line flagged for the owner, no further pitch.",
                    )
                if k == 2:
                    return self._wait(
                        cv,
                        86400,
                        "Same auto-reply again — owner not at the phone. Backing off 24h instead of burning turns.",
                    )
                return self._end(cv, f"Auto-reply {k}x with no human signal; closing to avoid wasting turns.")

            # 3. hostile without explicit opt-out
            if HOSTILE.search(msg) and not ACCEPT.search(msg):
                cv.hostile_count += 1
                if cv.hostile_count >= 2:
                    if cv.merchant_id and not is_customer and known:
                        self.opted_out.add(cv.merchant_id)
                    return self._end(cv, "Repeated frustration; exiting gracefully and suppressing this merchant.")
                if OUT_OF_SCOPE.search(msg):
                    pass  # fall through to scope handler with apology prefix
                else:
                    b = self._t(
                        cv,
                        "Sorry for the bother — I'll keep it short. Reply STOP and I won't message again; otherwise I'm here if you want help with your listing.",
                        "Maafi chahti hoon pareshani ke liye. STOP likhiye to main dobara message nahi karungi; warna listing mein madad chahiye to main yahin hoon.",
                    )
                    return self._send(
                        cv,
                        b,
                        "none",
                        "Frustration without explicit opt-out: one de-escalation line with a clear exit path; no pitch.",
                    )

            # 3b. thanks after work is done -> close gracefully
            if THANKS.search(msg) and cv.stage in ("delivered", "confirmed"):
                return self._end(
                    cv, "Merchant said thanks after delivery/confirmation; nothing pending — closing gracefully."
                )

            # 4. slot pick for booking threads
            slots = cv.next.get("slots") or []
            mslot = re.fullmatch(r"\s*(?:option\s*)?([1-9])\s*[.!]?\s*", msg)
            if is_customer and mslot and slots:
                i = int(mslot.group(1)) - 1
                if 0 <= i < len(slots):
                    cv.stage = "confirmed"
                    b = self._t(
                        cv,
                        f"Booked ✅ {slots[i]}. See you then! Reply here if anything changes.",
                        f"Ho gaya ✅ {slots[i]} book hai. Milte hain! Kuch badle to yahin bata dijiye.",
                    )
                    return self._send(
                        cv, b, "none", f"Customer picked option {i+1}; confirming the exact slot, no upsell."
                    )

            if is_customer and mslot and not slots:
                return self._act(cv, True, msg)

            # 5. commitment -> action mode
            if ACCEPT.search(msg) or ACCEPT_ANY.search(msg):
                return self._act(cv, is_customer, msg)

            # 6. deferral
            if DEFER.search(msg):
                secs = defer_seconds(msg)
                return self._wait(
                    cv, secs, f"Merchant asked for time; backing off {secs // 60} min rather than pushing."
                )

            # 7. plain decline
            if DECLINE.search(msg):
                return self._end(cv, "Clear 'no' to this specific offer; exiting without a counter-pitch.")

            # 8. out of scope
            if OUT_OF_SCOPE.search(msg):
                topic = cv.next.get("topic") or "your listing"
                sorry = (
                    self._t(cv, "Sorry about the earlier bother. ", "Pehle ki pareshani ke liye maafi. ")
                    if cv.hostile_count
                    else ""
                )
                b = sorry + self._t(
                    cv,
                    f"That one's outside what I can do — a CA or specialist is the right person for it. What I can do today: {F.humanize(topic)}. Reply YES and I'll finish it for you.",
                    f"Yeh mere daayre se bahar hai — iske liye CA ya specialist sahi rahenge. Main aaj yeh kar sakti hoon: {F.humanize(topic)}. YES bhejiye, main poora kar dungi.",
                )
                return self._send(
                    cv,
                    b,
                    "binary_yes_no",
                    "Out-of-scope request politely declined (no pretend expertise); redirected to the original thread with one CTA.",
                )

            # 8b. customer proposing their own time ("can we do Sat 11am?")
            if is_customer and TIME_HINT.search(msg):
                cv.stage = "confirmed"
                b = self._t(
                    cv,
                    f"Sure — noted your request ({msg.strip().rstrip('?')}). The team will confirm that slot here shortly.",
                    f"Bilkul — aapka request note kar liya ({msg.strip().rstrip('?')}). Team yeh slot yahin confirm karegi.",
                )
                return self._send(
                    cv,
                    b,
                    "none",
                    "Customer proposed their own time; acknowledged without falsely confirming availability.",
                )

            # 9. questions ("show me what it'd look like" == ready to act)
            if SHOW_ME.search(msg) and not is_customer:
                return self._act(cv, False, msg)
            if QUESTION.search(msg):
                return self._answer(cv, msg, is_customer)

            # 10. information / answer to our open question
            return self._info(cv, msg, is_customer)

    # ---------------------------------------------------------------- action mode
    def _act(self, cv: Conv, is_customer: bool, msg: str) -> dict:
        nx = cv.next or {}
        if is_customer:
            slots = nx.get("slots") or []
            if cv.stage == "confirmed":
                return self._end(cv, "Customer acknowledged after confirmation; nothing more to add.")
            cv.stage = "confirmed"
            if nx.get("topic") == "refill" or nx.get("type") == "confirm" and "refill" in str(nx.get("topic")):
                b = self._t(
                    cv,
                    "Done ✅ Packing it now — delivery confirmation with the bill will follow shortly. Reply here if any dose has changed.",
                    "Ho gaya ✅ Abhi pack kar rahe hain — bill ke saath delivery confirmation thodi der mein. Dose badla ho to yahin bata dijiye.",
                )
            elif len(slots) == 1:
                b = self._t(
                    cv,
                    f"Done ✅ {slots[0]} is booked for you. See you then!",
                    f"Ho gaya ✅ {slots[0]} aapke liye book hai. Milte hain!",
                )
            elif nx.get("type") == "confirm":
                b = self._t(
                    cv,
                    "Confirmed ✅ Thank you — see you tomorrow! Reply here if anything changes.",
                    "Confirm ✅ Dhanyavaad — kal milte hain! Kuch badle to yahin bata dijiye.",
                )
            else:
                b = self._t(
                    cv,
                    "Done ✅ We're holding a spot for you — our team will message the exact time shortly. Reply with a preferred time if you have one.",
                    "Ho gaya ✅ Aapke liye spot hold hai — team thodi der mein exact time bhejegi. Koi pasand ka time ho to bata dijiye.",
                )
            return self._send(
                cv, b, "none", "Customer said yes: confirmed immediately in their language, no further questions."
            )

        sal, m, cat = self._names(cv)
        if cv.stage in ("pitched",):
            cv.stage = "delivered"
            art = nx.get("artifact") or "The plan is ready on my side."
            conf = nx.get("confirm_en") or "go live"
            b = self._t(
                cv,
                f"Done — here's the draft:\n\n{art}\n\nReply CONFIRM and I'll {conf}. Want changes? Just type them.",
                f"Ho gaya — yeh raha draft:\n\n{art}\n\nCONFIRM bhejiye, main aage ka kaam kar dungi ({conf}). Kuch badalna ho to bas likh dijiye.",
            )
            return self._send(
                cv,
                b,
                "binary_confirm_cancel",
                "Merchant committed — switched straight to action mode: delivered the artifact in the same turn, one CONFIRM to execute. No qualifying questions.",
            )
        if cv.stage == "delivered":
            cv.stage = "confirmed"
            conf = nx.get("confirm_en")
            b = self._t(
                cv,
                (f"Confirmed ✅ I'll {conf} now" if conf else "Confirmed ✅ Executing it now")
                + " and send you the result here within 24 hours.",
                "Confirm ✅ Abhi kar rahi hoon — 24 ghante mein result yahin bhej dungi.",
            )
            return self._send(cv, b, "none", "Second commitment = execute; report-back promise, conversation can rest.")
        return self._end(cv, "Merchant acknowledged after execution; closing politely with nothing left to ask.")

    # ---------------------------------------------------------------- questions
    def _answer(self, cv: Conv, msg: str, is_customer: bool) -> dict:
        sal, m, cat = self._names(cv)
        nx = cv.next or {}
        if WHO_Q.search(msg):
            biz = F.biz_name(m) if m else "your business"
            b = self._t(
                cv,
                f"I'm Vera, magicpin's assistant — I help {biz} get more customers from Google and magicpin (posts, offers, reviews). No cost to reply. Want me to go ahead with the step I mentioned? Reply YES.",
                f"Main Vera hoon, magicpin ki assistant — {biz} ko Google aur magicpin se zyada customers dilane mein madad karti hoon. Jo step bataya tha, woh kar doon? YES bhejiye.",
            )
            if is_customer:
                b = self._t(
                    cv,
                    f"This is {F.biz_name(m)}'s WhatsApp assistant — messages here reach the team directly. Reply YES to confirm, or tell us what you need.",
                    f"Yeh {F.biz_name(m)} ka WhatsApp assistant hai — yahan ka message seedha team tak jaata hai. Confirm ke liye YES bhejiye.",
                )
            return self._send(
                cv, b, "binary_yes_no", "Identity question answered plainly, then back to the single CTA."
            )
        if PRICE_Q.search(msg):
            offers = F.active_offers(m) if m else []
            sub = (m or {}).get("subscription") or {}
            if is_customer and offers:
                b = self._t(
                    cv,
                    f"Current offer: {offers[0]}. Reply YES and we'll hold a slot for you.",
                    f"Abhi ka offer: {offers[0]}. YES bhejiye, hum slot rakh denge.",
                )
            elif not is_customer and nx.get("topic") == "renewal" and sub.get("plan"):
                b = self._t(
                    cv,
                    f"Your current plan is {sub.get('plan')}; I'll share the exact renewal amount with the payment step so there are no surprises. Reply YES to get it.",
                    f"Aapka plan {sub.get('plan')} hai; exact renewal amount payment step ke saath bhejungi. YES bhejiye.",
                )
            else:
                b = self._t(
                    cv,
                    "The draft I prepare costs you nothing to review — you only approve what goes live. Reply YES and I'll send it over.",
                    "Draft dekhne ka koi charge nahi — jo live jaana hai woh sirf aap approve karenge. YES bhejiye, main bhej deti hoon.",
                )
            return self._send(
                cv,
                b,
                "binary_yes_no",
                "Price question answered only with facts in context (live offer / plan name); no invented numbers.",
            )
        if re.search(r"\b(how|kaise|what do i|kya karna|next|process|steps?)\b", msg, re.I):
            return self._act(cv, is_customer, msg)  # 'how does it work' == ready to act: show, don't explain
        if is_customer:
            b = self._t(
                cv,
                "Thanks for asking — the team will reply to that here shortly. Meanwhile, reply YES if you'd like us to hold a slot.",
                "Poochhne ke liye dhanyavaad — team yahin jawab degi. Tab tak slot hold karna ho to YES bhejiye.",
            )
            return self._send(
                cv,
                b,
                "binary_yes_no",
                "Customer question outside known facts: routed to the team honestly, single CTA kept.",
            )
        topic = F.humanize(nx.get("topic") or "this")
        b = self._t(
            cv,
            f"Good question — I'll check that and include it. Meanwhile, the {topic} draft is ready on my side. Reply YES and I'll send it.",
            f"Achha sawaal — main check karke bata dungi. Tab tak {topic} ka draft ready hai. YES bhejiye, bhej deti hoon.",
        )
        return self._send(
            cv,
            b,
            "binary_yes_no",
            "Question outside known facts: no guessing, acknowledged honestly, kept the single CTA.",
        )

    def _service_from(self, cv: Conv, msg: str) -> str:
        sal, m, cat = self._names(cv)
        low = msg.lower()
        vocab = [v.lower() for v in (F.g(cat, "voice", "vocab_allowed") or [])]
        for o in cat.get("offer_catalog") or []:
            vocab.append(o.get("title", "").split(" @")[0].split(" (")[0].lower())
        for v in sorted(vocab, key=len, reverse=True):
            if v and re.search(r"\b" + re.escape(v) + r"\b", low):
                return v.title() if len(v) > 4 else v.upper()
        s = FILLER.sub(" ", re.sub(r"[^\w\s+&/-]", " ", msg))
        s = re.sub(r"\s+", " ", s).strip()
        return s[:40].title() if s else ""

    # ---------------------------------------------------------------- info
    def _info(self, cv: Conv, msg: str, is_customer: bool) -> dict:
        nx = cv.next or {}
        low = msg.lower()
        if is_customer:
            if re.search(
                r"\b(\d{1,2}(:\d{2})?\s*(am|pm)|morning|evening|afternoon|subah|shaam|mon|tue|wed|thu|fri|sat|sun)", low
            ):
                cv.stage = "confirmed"
                b = self._t(
                    cv,
                    f'Noted — "{msg}". We\'ll check that slot and confirm it here shortly.',
                    f'Note kar liya — "{msg}". Hum yeh slot check karke yahin confirm karenge.',
                )
                return self._send(
                    cv,
                    b,
                    "none",
                    "Customer proposed their own time; acknowledged without falsely confirming availability.",
                )
            b = self._t(
                cv,
                "Thanks for letting us know! Reply YES whenever you'd like us to book, or tell us a time that suits you.",
                "Batane ke liye dhanyavaad! Jab book karna ho YES bhejiye, ya apna time bata dijiye.",
            )
            return self._send(
                cv, b, "binary_yes_no", "Customer info message; acknowledged with the same single booking CTA."
            )
        if nx.get("type") == "collect":
            service = self._service_from(cv, msg) or nx.get("guess") or "that service"
            cv.stage = "delivered"
            sal, m, cat = self._names(cv)
            b = self._t(
                cv,
                f'Got it — {service}. Here\'s the draft:\n\nGoogle post: "{service.title()} at {F.biz_name(m)} — our most-asked service this week. Walk in or book on WhatsApp."\n'
                f"WhatsApp price reply: \"Thanks for asking! {service.title()} is available this week — reply with a day and we'll hold a slot.\"\n\nReply CONFIRM and I'll publish the post.",
                f'Samajh gayi — {service}. Yeh raha draft:\n\nGoogle post: "{service.title()} at {F.biz_name(m)} — is hafte ki sabse popular service. Walk in karein ya WhatsApp par book karein."\n'
                f'WhatsApp reply: "Poochhne ke liye shukriya! {service.title()} is hafte available hai — din bataiye, slot hold kar denge."\n\nCONFIRM bhejiye, post publish kar dungi.',
            )
            return self._send(
                cv,
                b,
                "binary_confirm_cancel",
                "Merchant answered the curious-ask; turned their exact words into the promised artifacts immediately.",
            )
        if re.search(r"\b(d[\s-]?speed|e[\s-]?speed|rvg)\b", low):
            if re.search(r"\bd[\s-]?speed\b", low):
                cv.next = {
                    "type": "deliver",
                    "topic": "X-ray compliance switch",
                    "confirm_en": "file the SOP note",
                    "artifact": "Switch plan (before 15 Dec):\n1) Replace D-speed with E-speed film (lowest cost) — or get an RVG sensor quote\n"
                    "2) Run one test exposure and log the dose (target ≤ 1.0 mSv per IOPA)\n"
                    '3) SOP note: "From <date>, all IOPA exposures use E-speed film/RVG per DCI circular 2026-11-04." — sign and file',
                }
                b = self._t(
                    cv,
                    "Then it's worth acting before the deadline: D-speed film won't meet the new 1.0 mSv limit. Switch options are E-speed film (lowest cost) or an RVG sensor. Reply YES and I'll draft the switch plan + SOP note.",
                    "Tab deadline se pehle badalna zaroori hai: D-speed film naye 1.0 mSv limit ko pass nahi karegi. Options: E-speed film (sabse sasta) ya RVG sensor. YES bhejiye, switch plan + SOP note draft kar dungi.",
                )
            else:
                b = self._t(
                    cv,
                    "Good news — that setup already passes the new limit. Reply YES and I'll draft a one-page SOP note documenting it for your file.",
                    "Achhi khabar — aapka setup naye limit ko pass karta hai. YES bhejiye, file ke liye 1-page SOP note bana dungi.",
                )
            return self._send(
                cv,
                b,
                "binary_yes_no",
                "Merchant answered the diagnostic question; routed to the matching compliance action.",
            )
        if cv.hostile_count:
            return self._end(cv, "Merchant was frustrated earlier and is lukewarm now; not pushing — exiting politely.")
        # generic statement: treat as engaged, move forward
        return (
            self._act(cv, False, msg)
            if cv.stage == "pitched" and len(msg.split()) <= 12 and not re.search(r"\b(no|nahi|not)\b", low)
            else self._send(
                cv,
                self._t(
                    cv,
                    "Understood, thanks. Reply YES whenever you'd like me to take the next step — I'll handle the work.",
                    "Samajh gayi, dhanyavaad. Jab aage badhna ho YES bhejiye — kaam main sambhal lungi.",
                ),
                "binary_yes_no",
                "Neutral info message; acknowledged and kept a single low-effort CTA.",
            )
        )
