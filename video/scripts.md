# Video scripts (≤ 60 s each)

Voice-over budget: ~145 words per minute. Each script below is 125–140 words so there is room to breathe.
Numbers marked ⟨…⟩ are refreshed from the final build before recording. Organizer guidance: show our own system output and validation (not score.py); there is no hour-16 ordinance in this edition.

---

## 1 · Team intro (on camera, 60 s)

| Time | Picture | Voice-over |
|---|---|---|
| 0–6 | Title card: *Rental Housing Law Navigator* · "compile · diff · prove" | — (music sting) |
| 6–20 | Kaini on camera, at a desk / window | "I'm Kaini, a graduate student in applied statistics at NYU. I live in Jersey City, one of the cities in this challenge, so the rent rules here are my rules too." |
| 20–36 | Cut: map of the 10 cities lighting up, then a stack of statute pages | "A property manager with buildings in ten cities faces hundreds of overlapping laws that change every few months. Searching for them isn't enough. You need an answer you can check." |
| 36–52 | Back on camera | "So I built this solo in 24 hours, as a compiler rather than a chatbot. The model reads the law. Plain code decides what applies. And every answer comes with the exact sentence it rests on." |
| 52–60 | End card: live demo URL + GitHub | "We don't search housing law. We compile it, diff it, and prove it." |

Words: ~130.

---

## 2 · Product demo (screen recording, 60 s)

Recorded automatically from the live site at 1080p; Kaini records the voice-over only.

| Time | Screen | Voice-over |
|---|---|---|
| 0–5 | Lookup tab, Hoboken address A0002 already open | "Here's an apartment building in Hoboken, as of October 1st, 2026." |
| 5–17 | Summary pills; hover the facts row (observed vs. inferred vs. not in data) | "Six categories of law, state and city. Each building fact is labeled: observed, inferred, or simply not in the data." |
| 17–32 | Click New Jersey's FAIR Act (algorithmic pricing) → proof chain opens; walk steps: jurisdiction, in-force check, coverage tests, conflict with Hoboken, quoted statute | "Open any rule and you get the proof: which law reaches this address, whether it's in force, each coverage test against this building, and the exact sentence from the statute. Today it's enacted but not yet in force." |
| 32–43 | Drag the as-of slider to 2027-07-01; FAIR Act flips to Applies, effective date shown as computed from the quoted text; Hoboken's own ban carries a Conflict flag | "Move the date. On July 1st, 2027, a date the system computed from the bill's own text, it starts applying, and the overlap with Hoboken's ordinance is flagged for a human instead of guessed." |
| 43–53 | Uncertainty tab: unknown-reason bars, then the "517 results change" card | "When the data can't answer, we say unknown and say why. Trusting guessed unit counts would flip 517 results, so we don't." |
| 53–60 | Coverage map, slow pan; end card | "Every jurisdiction, every category, every answer, proven." |

Words: ~135.

---

## 3 · Technical walkthrough (screen + diagram, 60 s)

| Time | Picture | Voice-over |
|---|---|---|
| 0–14 | Animated pipeline: route → extract → ground → normalize → review → relations → roles → evaluate → diff → prove | "The pipeline splits the corpus into jurisdiction-by-category cells. GPT-5.4 fills a fixed schema per cell through a forced tool call, and every quote must match the source verbatim or it's dropped." |
| 14–26 | Close-up: a condition with its role chip (scope / expansion / variant) and date provenance | "Dates carry provenance. A three-vote review removes duplicates, and each condition is typed, so an 'also includes' clause can never shrink coverage." |
| 26–38 | Code view of `evaluate.py` three-valued logic | "Applicability is deterministic three-valued logic. A missing fact gives unknown, never a guess." |
| 38–50 | Terminal: `python -m navigator.check` scrolling to **19/19 passed**; table T1 250 · T2 90 · T3 140 (90 conflicts) · T4 110 · T5 0 | "Nineteen regression checks gate every build. All five change tests match the expected sets." |
| 50–60 | Terminal: `navigator.hour16` on our synthetic ordinance, "addresses changed outside touched jurisdictions: 0" | "Adding a law is one command: it re-extracts only the city it touches, and proves nothing else moved." |

Words: ~125.

---

## Recording notes

- Record the voice in a quiet room, phone 20 cm away, one take per row; the timing is cut to the voice, not the reverse.
- Burned-in captions on all three videos (many judges watch muted).
- Same title-card style, colors and fonts as the live site.
- Export 1920×1080, H.264, under 60.0 s, with a 0.5 s silent tail.
