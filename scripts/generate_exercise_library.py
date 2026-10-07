"""Regenerates frontend/src/data/exerciseLibrary.json -- a browsable reference for every
exercise the hiit/strength_training picker supports (see ExerciseLibraryPage.tsx), with a photo,
description, muscle groups, and a link to the exercise's own Garmin Connect page, wherever that
data actually exists.

Three real, honestly-labeled tiers, not one blended guess (an earlier draft that
repeated one category photo across dozens of exercises was rejected):

1. **Garmin's own "detailed" exercises** (~10% of the catalog, confirmed live 2026-09 --
   `GET https://connect.garmin.com/web-data/exercises/en-US/<CATEGORY>/<EXERCISE>.json`, no auth
   needed) carry a real `heroImage` and a real `description` written by Garmin. These are the
   only entries with an actual Garmin-produced photo, and the only ones that get a `garmin_url`
   at all -- see below.
2. **A free-exercise-db match** (github.com/yuhonas/free-exercise-db, public domain, ~870
   exercises): matched by name (stripping equipment words like "Barbell"/"Banded", folding
   singular/plural -- deliberately NOT stripping anything that changes body position, tempo, or
   laterality, e.g. "decline"/"seated"/"single-arm" stay) with real step-by-step instructions and
   muscle tags from that dataset. Reuse of one fed photo across several Garmin exercises is fine
   when they're genuinely the same base exercise (name-equivalent once equipment words are
   stripped, e.g. "Barbell Curl" and "Banded Curl" sharing a curl photo -- see `_MODIFIERS`'s
   own comment for exactly which words qualify). The looser fuzzy-similarity tier (a jaccard
   overlap on the stripped name, not an exact match) is a real but weaker signal, so it's capped
   to a handful of claimants per photo rather than left unbounded -- unbounded reuse there is
   what produced the original complaint (one photo standing in for 50+ only loosely-related
   variants).
3. **Muscle-group data only**: Garmin's own master exercise list
   (`GET https://connect.garmin.com/web-data/exercises/Exercises.json`) has primary/secondary
   muscles for every exercise in the catalog even when it has no photo or description -- real,
   sourced data, just not a photo.

Tier 2's `steps` come straight from free-exercise-db's own `instructions` array, kept as a real
list (previously flattened into one `description` paragraph) -- `description` stays `None` for
tier 2 to avoid showing the same content twice.

A small, deliberately bounded set of tier 3 exercises get a hand-written `steps` list too (plus
an optional `reference_url`/`reference_label`), via `HAND_CURATED_STEPS` below -- see that
constant's own docstring for why this exists and how it was chosen. This is NOT a scraper: no
content or images are copied from any third-party exercise site. exrx.net's own published terms
(https://exrx.net/Notes/LinkGuidelines, checked live 2026-09) reserve their exercise directory
content and cap even bare links to under 75% of any one subdirectory; darebee.com licenses its
content CC BY-NC-ND, which forbids derivatives -- reformatting either into this catalog's own
JSON shape isn't something either site's terms permit at any real scale. What IS safe and useful:
originally-written steps (my own words, general exercise-science knowledge, not any one site's
phrasing) for the handful of tier-3 exercises that are genuinely a single describable technique
-- not a scrape of the other ~1,140 tier-3 exercises, most of which are one of many
visually-different named variants within a family (e.g. dozens of differently-positioned plank/
push-up/crunch variants) that a single hand-written description would misrepresent, the exact
"one description standing in for 50+ variants" mistake this file's tier system was already
built to avoid.

`garmin_url` is set **only for tier 1**, not every exercise -- confirmed live that Garmin's own
exercise pages sit entirely behind a sign-in wall (an unauthenticated visitor hits
sso.garmin.com regardless of tier, even for a detailed exercise), and there is no way to confirm
from here that a non-detailed exercise has *any* real content once past that wall. Rather than
link to a page that plausibly 404s or renders empty, only the exercises this script has directly
confirmed have real Garmin content get a Garmin link at all.

This hits Garmin's public exercise-data endpoint ~1,527 times (a few seconds, no auth, same
public asset the GarminExercisesCollector project and this project's own SportType lookup use)
plus a single fetch of free-exercise-db's combined exercises.json. Re-run whenever the
garminconnect dependency's own exercise list changes.

Usage: uv run python scripts/generate_exercise_library.py
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TypedDict

from garminconnect.exercises import _RAW

OUT_PATH = (
    Path(__file__).resolve().parent.parent / "frontend" / "src" / "data" / "exerciseLibrary.json"
)

MASTER_URL = "https://connect.garmin.com/web-data/exercises/Exercises.json"
DETAIL_URL = "https://connect.garmin.com/web-data/exercises/en-US/{category}/{exercise}.json"
GARMIN_PAGE_URL = "https://connect.garmin.com/modern/exercises/{category}/{exercise}"
FED_DB_URL = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/dist/exercises.json"
FED_IMAGE_BASE = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/exercises/"

_UA = {"User-Agent": "Mozilla/5.0 (compatible; PerseverExerciseLibraryBot/1.0)"}

# Words stripped before comparing two exercise names for "close enough to share a photo" --
# deliberately only equipment/apparatus nouns and plain connector words, so "Barbell Bench
# Press"/"Bench Press" compare equal (same visible movement, different thing in your hands).
# Anything that changes body position, orientation, tempo, or laterality stays IN the comparison
# on purpose (a decline push-up, a seated row, a single-arm row, an isometric hold, a wide-grip
# pull-up all look visibly different from their plain counterpart in a photo) -- stripping those
# too is exactly what caused the original complaint (one photo standing in for dozens of
# visually-different variants). See _match_fed_exercises' own docstring for the reuse policy
# this feeds into.
_MODIFIERS = {
    "barbell",
    "dumbbell",
    "dumbbells",
    "cable",
    "banded",
    "band",
    "machine",
    "smith",
    "kettlebell",
    "ez",
    "bar",
    "plate",
    "resistance",
    "suspension",
    "trx",
    "sling",
    "swiss",
    "ball",
    "bosu",
    "mat",
    "strap",
    "with",
    "the",
    "a",
    "an",
    "and",
    "of",
    "to",
    "on",
    "at",
    "in",
    "for",
    "against",
    "each",
}


def _category_label(category: str) -> str:
    return category.replace("_", " ").title()


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _stem(t: str) -> str:
    if len(t) > 4 and t.endswith("es"):
        return t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def _core_tokens(s: str) -> list[str]:
    return [_stem(t) for t in _norm(s).split(" ") if t and t not in _MODIFIERS]


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def _fetch_detail(category: str, exercise: str) -> tuple[str, dict | None]:
    url = DETAIL_URL.format(category=category, exercise=exercise)
    try:
        return f"{category}/{exercise}", _fetch_json(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return f"{category}/{exercise}", None
        raise


def _fetch_all_details(pairs: list[tuple[str, str]]) -> dict[str, dict]:
    results: dict[str, dict] = {}
    start = time.time()
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(_fetch_detail, c, e): (c, e) for c, e in pairs}
        for i, fut in enumerate(as_completed(futures), 1):
            key, data = fut.result()
            if data:
                results[key] = data
            if i % 300 == 0:
                print(f"  {i}/{len(pairs)} exercise pages checked ({time.time() - start:.0f}s)")
    print(
        f"  {len(pairs)} exercise pages checked in {time.time() - start:.0f}s, "
        f"{len(results)} have Garmin's own photo/description"
    )
    return results


def _match_fed_exercises(garmin_entries: list[dict], fed_list: list[dict]) -> dict[str, dict]:
    fed_list = [e for e in fed_list if e.get("images")]
    fed_by_norm: dict[str, list[dict]] = {}
    fed_by_core: dict[tuple[str, ...], list[dict]] = {}
    for e in fed_list:
        fed_by_norm.setdefault(_norm(e["name"]), []).append(e)
        fed_by_core.setdefault(tuple(sorted(_core_tokens(e["name"]))), []).append(e)

    candidates: list[tuple[int, float, str, dict]] = []
    for g in garmin_entries:
        gname = g["name"]
        gnorm = _norm(gname)
        gcore = _core_tokens(gname)
        gcore_set = set(gcore)
        g_last = gcore[-1] if gcore else None

        if gnorm in fed_by_norm:
            candidates.append((0, 1.0, gname, fed_by_norm[gnorm][0]))
            continue
        key = tuple(sorted(gcore))
        if gcore and key in fed_by_core:
            candidates.append((1, 1.0, gname, fed_by_core[key][0]))
            continue
        best, best_score = None, 0.0
        for e in fed_list:
            fcore = _core_tokens(e["name"])
            if not fcore or not gcore or not g_last or fcore[-1] != g_last:
                continue
            sc = _jaccard(gcore_set, set(fcore))
            if sc > best_score:
                best, best_score = e, sc
        if best is not None and best_score >= 0.7:
            candidates.append((2, best_score, gname, best))

    # Highest-confidence match first (rank, then score desc). "Close enough to reuse" means
    # rank 0/1 (name-equivalent once equipment/positional words like "Barbell"/"Incline" are
    # stripped -- genuinely the same base exercise, e.g. "Barbell Bench Press" and "Bench
    # Press"): those may reuse a photo freely, no cap. Rank 2 (fuzzy jaccard overlap, a real but
    # looser similarity) gets a modest cap instead -- unbounded reuse there is what produced the
    # original complaint (one photo standing in for 50+ loosely-related variants); a cap keeps
    # only the closest few claimants.
    candidates.sort(key=lambda c: (c[0], -c[1]))
    JACCARD_RANK = 2
    # Checked live against the real ~1,527-exercise catalog before touching this (never adjust
    # this kind of heuristic on a few examples alone): the *threshold* above (0.7) is already
    # tightly calibrated -- lowering it to 0.6 or 0.5 reintroduces genuinely bad matches (e.g.
    # "One-arm Push-up" fuzzy-matching "One Arm Chin-Up" at 0.6, a materially different exercise),
    # so it stays untouched. The *cap* had real headroom, though: at 0.7 only 39 exercises total
    # ever clear the bar for a fuzzy/rank-2 match, so raising the cap from 3 to 5 lets 3 more of
    # those already-qualifying, already-inspected matches through -- not a broadened heuristic,
    # just less of the previously-legitimate matches getting crowded out by the cap itself.
    JACCARD_REUSE_CAP = 5
    image_use_count: dict[str, int] = {}
    matched: dict[str, dict] = {}
    for rank, _score, gname, fed in candidates:
        image = fed["images"][0]
        if rank == JACCARD_RANK and image_use_count.get(image, 0) >= JACCARD_REUSE_CAP:
            continue
        image_use_count[image] = image_use_count.get(image, 0) + 1
        matched[gname] = fed
    return matched


class _Curated(TypedDict):
    steps: list[str]
    reference_url: str
    reference_label: str


# Hand-written steps for the small set of tier-3 exercises that are genuinely a single,
# describable technique -- specifically, Garmin's own generic "family" entry for a whole
# category (name == categoryLabel, e.g. "Row", "Lateral Raise", "Battle Rope"), never one of the
# many differently-named, visually-different variants inside that category (a decline push-up, a
# banded row, a single-leg hip raise, etc.), which stay honest muscle-group-only entries -- this
# is the exact discipline the tier system itself already established, just applied to which
# entries are worth writing original instructions for at all. Written from general
# exercise-science knowledge/technique cues gathered across several sources, in original wording
# -- never a copy of any one site's phrasing -- with one link per entry to a page for further
# reading/video, since a plain hyperlink (unlike copying content) is not something any site's
# terms restrict at this small a scale. Keyed by (category, exercise), matching `_RAW`'s own
# identifiers.
HAND_CURATED_STEPS: dict[tuple[str, str], _Curated] = {
    ("BATTLE_ROPE", "BATTLE_ROPE"): {
        "steps": [
            "Anchor a battle rope at its midpoint and stand facing the anchor, feet "
            "shoulder-width apart and knees soft, holding one end of the rope in each hand.",
            "Brace your core and hinge slightly at the hips, keeping your chest up and your "
            "spine long.",
            "Drive both arms up and down together as hard as you can, generating each wave "
            "from your shoulders and upper back rather than flicking with your wrists.",
            "Let your hips and legs add power to the movement, almost bouncing with the "
            "rhythm, without letting your lower back round.",
            "Keep the waves reaching the anchor consistently for the set duration, then rest "
            "and repeat.",
        ],
        "reference_url": "https://blog.nasm.org/battle-ropes-workouts",
        "reference_label": "NASM",
    },
    ("CARRY", "CARRY"): {
        "steps": [
            "Stand between two heavy dumbbells, kettlebells, or a loaded carry implement, "
            "feet hip-width apart.",
            "Hinge at the hips with a flat back, grip the weights firmly, and drive through "
            "your heels to stand up tall.",
            "Pull your shoulder blades back and down, brace your core, and keep your chest "
            "lifted -- don't let the weight pull you forward or to one side.",
            "Walk forward with short, controlled steps, keeping your ribs stacked over your "
            "hips rather than leaning.",
            "Set the weights down under control at the end of the distance or time, resetting "
            "your hip hinge rather than rounding your back.",
        ],
        "reference_url": "https://barbend.com/farmers-carry/",
        "reference_label": "BarBend",
    },
    ("CHOP", "CHOP"): {
        "steps": [
            "Set a cable or band at chest-to-shoulder height and stand side-on to the "
            "anchor, feet shoulder-width apart.",
            "Grasp the handle with both hands, arms extended, and stand tall with a slight "
            "bend in the knees.",
            "Brace your core and pull the handle down and across your body toward your "
            "opposite hip, rotating through your torso and hips together.",
            "Let your back foot pivot naturally as you rotate, keeping your arms mostly "
            "straight throughout the movement.",
            "Pause briefly, then reverse the movement under control back to the starting "
            "position, and complete all reps on one side before switching.",
        ],
        "reference_url": "https://barbend.com/cable-chop/",
        "reference_label": "BarBend",
    },
    ("HIP_SWING", "HIP_SWING"): {
        "steps": [
            "Stand with feet shoulder-width apart, a kettlebell (or similar weight) a short "
            "distance in front of you.",
            "Hinge at the hips to grip the weight, keeping your spine neutral and your shins "
            "close to vertical.",
            "Hike the weight back between your legs, then drive your hips forward explosively "
            "to swing it up.",
            "Let the power come from your hips and glutes extending, not from lifting with "
            "your arms or rounding your lower back.",
            "Allow the weight to float up to roughly chest height, then let it swing back down "
            "under control into the next hip hinge.",
            "Keep your core braced throughout so your lower back stays neutral at both ends of "
            "the swing.",
        ],
        "reference_url": "https://en.wikipedia.org/wiki/Kettlebell_swing",
        "reference_label": "Wikipedia",
    },
    ("HYPEREXTENSION", "HYPEREXTENSION"): {
        "steps": [
            "Position yourself face-down on a hyperextension bench with your ankles secured "
            "under the footpads and your hip crease at the top edge of the pad.",
            "Cross your arms over your chest or place your hands lightly behind your head, "
            "keeping your neck neutral.",
            "Lower your torso forward by hinging at the hips, keeping your back flat, until "
            "you feel a stretch through your hamstrings or your torso is roughly parallel to "
            "the floor.",
            "Extend your hips and back together to raise your torso back up in a straight "
            "line, without arching past neutral at the top.",
            "Move slowly and under control in both directions -- this is not a movement to rush.",
        ],
        "reference_url": "https://en.wikipedia.org/wiki/Hyperextension_(exercise)",
        "reference_label": "Wikipedia",
    },
    ("LATERAL_RAISE", "LATERAL_RAISE"): {
        "steps": [
            "Stand tall with feet shoulder-width apart, a dumbbell in each hand at your "
            "sides, palms facing your body.",
            "Keep a slight, fixed bend in your elbows and brace your core.",
            "Raise both arms out to the sides, leading with your elbows, until they reach "
            "roughly shoulder height.",
            "Keep your shoulders down and away from your ears throughout -- avoid shrugging "
            "or swinging the weight up with momentum.",
            "Pause briefly at the top, then lower the dumbbells back down under control.",
        ],
        "reference_url": "https://www.webmd.com/fitness-exercise/how-to-do-lateral-raises",
        "reference_label": "WebMD",
    },
    ("LEG_RAISE", "LEG_RAISE"): {
        "steps": [
            "Lie flat on your back with your legs extended and together, arms at your "
            "sides, palms down.",
            "Press your lower back into the floor and brace your core before you move.",
            "Keeping your legs straight, raise them together toward the ceiling until "
            "they're roughly perpendicular to the floor.",
            "Lower them back down slowly under control, stopping just short of the floor to "
            "keep tension on your abs.",
            "If your lower back arches off the floor at any point, raise the starting height "
            "or bend your knees slightly to keep the movement comfortable.",
        ],
        "reference_url": "https://en.wikipedia.org/wiki/Leg_raise",
        "reference_label": "Wikipedia",
    },
    ("ROW", "ROW"): {
        "steps": [
            "Stand with feet roughly shoulder-width apart, holding a barbell or a pair of "
            "dumbbells with an overhand grip.",
            "Hinge forward at the hips with a slight bend in the knees until your torso is "
            "close to parallel with the floor, keeping your back flat.",
            "Let the weight hang directly under your shoulders with your arms extended.",
            "Pull the weight up toward your lower ribs by driving your elbows back and "
            "squeezing your shoulder blades together, keeping your elbows close to your body.",
            "Lower the weight back down under control to a full stretch before starting the "
            "next rep.",
        ],
        "reference_url": "https://www.acefitness.org/resources/everyone/exercise-library/12/bent-over-row/",
        "reference_label": "ACE Fitness",
    },
    ("SLEDGE_HAMMER", "SLEDGE_HAMMER"): {
        "steps": [
            "Stand facing a large tire with feet roughly shoulder-width apart, holding a "
            "sledgehammer with one hand near the head and the other at the base of the handle.",
            "Raise the hammer overhead, sliding your top hand down to meet your bottom hand "
            "as it rises.",
            "Brace your core and hinge slightly at the hips as you begin the downswing.",
            "Drive the hammer down explosively, letting the power come from your hips and "
            "core rather than your arms alone, striking the tire slightly above its center.",
            "Alternate which hand leads the swing from rep to rep, and start with a lighter "
            "hammer until your form and timing feel controlled.",
        ],
        "reference_url": "https://barbend.com/tire-sledgehammer-workout/",
        "reference_label": "BarBend",
    },
    ("SLED", "SLED"): {
        "steps": [
            "To push: load the sled, position yourself behind it, and grip the handles with "
            "your arms extended or elbows bent, whichever the handle height allows.",
            "Hinge forward at the hips with a slight knee bend, keeping your spine neutral "
            "and your chest up rather than dropping your head.",
            "Drive through your legs and take strong, deliberate steps to push the sled "
            "forward, maintaining that same forward lean throughout.",
            "To pull: face the sled, hinge into a partial squat, and grip the rope or straps "
            "with your arms extended.",
            "Take controlled steps backward, pulling the sled toward you, keeping your spine "
            "neutral and avoiding any rounding of your lower back.",
        ],
        "reference_url": "https://www.thegymgroup.com/exercises/sled-push-and-pull/",
        "reference_label": "The Gym Group",
    },
    ("STAIR_STEPPER", "STAIR_STEPPER"): {
        "steps": [
            "Step onto the machine and set a light resistance to start, keeping your body "
            "weight over your legs rather than the handrails.",
            "Stand tall with your core engaged, shoulders back, and eyes forward -- avoid "
            "hunching forward over the console.",
            "Press each pedal down through your whole foot, completing a full range of "
            "motion on each step rather than short, shallow presses.",
            "Rest your hands lightly on the handrails for balance only, not to take weight "
            "off your legs.",
            "Increase resistance or add short, faster intervals once your form feels steady, "
            "rather than starting at a high resistance.",
        ],
        "reference_url": "https://www.anytimefitness.com/blog/stair-climber-101-everything-you-need-to-know-to-make-the-most-out-of-your-climb",
        "reference_label": "Anytime Fitness",
    },
    ("RUN", "SPRINT"): {
        "steps": [
            "Start from a standing or light jogging position and accelerate to your fastest "
            "controllable pace over the first few strides.",
            "Drive your knees up and forward rather than just moving your legs faster.",
            "Pump your arms forward and back in rhythm with your legs, elbows bent around 90 "
            "degrees.",
            "Land toward the middle or front of your foot rather than your heel, and keep "
            "your torso tall with a slight forward lean.",
            "Hold your top speed for the planned distance or time, then slow gradually rather "
            "than stopping abruptly.",
        ],
        "reference_url": "https://en.wikipedia.org/wiki/Sprint_(running)",
        "reference_label": "Wikipedia",
    },
    ("RUN", "WALK"): {
        "steps": [
            "Stand tall with your shoulders back and core lightly engaged.",
            "Step forward, landing on your heel and rolling smoothly through to push off your "
            "toes.",
            "Swing your arms naturally at your sides in rhythm with your opposite leg.",
            "Keep a steady, comfortable pace you can sustain for the planned distance or time.",
            "Breathe steadily throughout rather than holding your breath.",
        ],
        "reference_url": "https://en.wikipedia.org/wiki/Walking",
        "reference_label": "Wikipedia",
    },
    ("RUN", "RUN_OR_WALK"): {
        "steps": [
            "Alternate between running and walking segments for the planned duration or "
            "distance, e.g. run for a set time or distance, then walk to recover before "
            "running again.",
            "Keep your running segments at a controlled, sustainable pace rather than sprinting "
            "-- this drill is about total time on your feet, not top speed.",
            "Use the walking segments to actively recover: relax your shoulders and let your "
            "breathing settle before your next running segment.",
            "Maintain good posture (tall, slight forward lean) in both the running and walking "
            "portions.",
        ],
        "reference_url": "https://www.jeffgalloway.com/training/run-walk/",
        "reference_label": "Jeff Galloway",
    },
    ("LADDER", "LADDER"): {
        "steps": [
            "Lay an agility ladder flat on the ground and stand at one end, facing down its "
            "length.",
            "For the simplest drill, run through the ladder placing one foot in each square, "
            "driving your knees up and keeping your steps quick and light.",
            "Keep your arms pumping naturally as you would when running, and look ahead "
            "rather than down at your feet.",
            "For a lateral variation, turn sideways to the ladder and step in and out of each "
            "square with a quick side-to-side shuffle.",
            "Focus on clean, accurate foot placement before trying to move faster -- speed "
            "should come after the pattern feels controlled.",
        ],
        "reference_url": "https://marathonhandbook.com/agility-ladder-drills/",
        "reference_label": "Marathon Handbook",
    },
}


# Originally-written step lists for the handful of tier-1 base movements ("Push-up", "Squat",
# etc.) that Garmin's own data covers only as one prose `description`, never a step list --
# these are extremely standard, well-established exercises (my own general exercise-science
# knowledge, not copied from any site), and having a real base step list for them is what makes
# the compositional generator below possible at all. Applied to the tier-1 entry itself too (as
# `steps`, alongside its existing real Garmin `description`/photo -- nothing there changes).
BASE_STEPS_OVERRIDE: dict[str, list[str]] = {
    "PUSH_UP": [
        "Start in a high plank with hands slightly wider than shoulder-width, arms straight, "
        "body in a straight line from head to heels.",
        "Brace your core and glutes so your hips don't sag or pike up.",
        "Bend your elbows to lower your chest toward the floor, keeping them at roughly a "
        "45-degree angle from your torso rather than flared straight out.",
        "Lower until your chest is just above the floor, then press back up to full arm extension.",
        "Keep your neck neutral throughout, looking slightly ahead rather than straight down.",
    ],
    "SQUAT": [
        "Stand with feet roughly shoulder-width apart, toes pointed slightly outward.",
        "Brace your core and keep your chest up as you begin the descent.",
        "Push your hips back and bend your knees to lower down, as if sitting into a chair, "
        "keeping your weight through your heels and mid-foot.",
        "Lower until your thighs are at least parallel to the floor, keeping your knees "
        "tracking in line with your toes.",
        "Drive through your heels to stand back up to full hip and knee extension.",
    ],
    "PLANK": [
        "Lie face down, then prop yourself up on your forearms, elbows directly under your "
        "shoulders.",
        "Extend your legs behind you, balancing on your toes, so your body forms a straight "
        "line from head to heels.",
        "Brace your core and squeeze your glutes to keep your hips level -- don't let them "
        "sag or pike up.",
        "Keep your neck neutral, looking at the floor just ahead of your hands.",
        "Hold the position, breathing steadily, for the target duration.",
    ],
    "CRUNCH": [
        "Lie on your back with your knees bent and feet flat on the floor, hip-width apart.",
        "Place your hands lightly behind your head or crossed over your chest, without "
        "pulling on your neck.",
        "Engage your core and curl your shoulders and upper back off the floor, exhaling as "
        "you rise.",
        "Lift only until your shoulder blades clear the floor -- this is a short range of "
        "motion, not a full sit-up.",
        "Lower back down under control, inhaling, without fully relaxing your abs at the bottom.",
    ],
    "LUNGE": [
        "Stand tall with feet hip-width apart, hands on your hips or holding weights at your "
        "sides.",
        # Deliberately direction-neutral ("into a lunge position," not "forward") -- this same
        # step list is reused for every named lunge variant, including reverse/walking/side
        # lunges, where "step forward" would be flatly wrong.
        "Step into a lunge position with one leg, lowering your hips until both knees are "
        "bent to roughly 90 degrees.",
        "Keep your front knee tracking over your front foot and your torso upright throughout.",
        "Push through your front foot to return to the starting position.",
        "Alternate legs, or complete all reps on one side before switching, depending on your "
        "program.",
    ],
    "HIP_RAISE": [
        "Lie on your back with your knees bent and feet flat on the floor, hip-width apart, "
        "arms at your sides.",
        "Brace your core and squeeze your glutes as you drive through your heels to lift "
        "your hips off the floor.",
        "Raise your hips until your body forms a straight line from shoulders to knees, "
        "without overarching your lower back.",
        "Hold briefly at the top, keeping your glutes contracted.",
        "Lower your hips back down under control, without letting them fully rest between reps.",
    ],
    "PULL_UP": [
        "Grip a pull-up bar with an overhand grip, hands slightly wider than shoulder-width apart.",
        "Hang with your arms fully extended and your core braced, avoiding excessive swinging.",
        "Pull your body up by driving your elbows down and back, leading with your chest "
        "toward the bar.",
        "Continue until your chin clears the bar.",
        "Lower back down under control to a full arm extension before starting the next rep.",
    ],
    "SIT_UP": [
        "Lie on your back with your knees bent and feet flat on the floor, anchored if needed.",
        "Cross your arms over your chest or place your hands lightly behind your head.",
        "Engage your core and curl your torso up off the floor, leading with your chest "
        "rather than your neck.",
        "Continue until your torso is upright, or close to it.",
        "Lower back down under control to the starting position.",
    ],
}

# Phrases matched against the WORDS a tier-3 variant's name adds beyond its category's own base
# exercise name (e.g. "Weighted Single-leg Elevated-feet Push-up" adds "weighted", "single leg",
# "elevated feet" beyond "Push-up") -- checked longest-phrase-first so "single leg" matches
# before a lone "single"/"leg" would. Each note describes, honestly and specifically, how that
# one real difference changes the base movement -- never a generic "this is a variant" filler.
# Original wording throughout, general exercise-science knowledge.
MODIFIER_NOTES: list[tuple[tuple[str, ...], str]] = [
    (
        ("single", "leg"),
        "Performing this on one leg at a time removes the help the other leg "
        "normally provides, so expect it to demand more balance.",
    ),
    (
        ("single", "arm"),
        "Working one arm at a time removes the help the other arm normally "
        "provides -- keep your torso square rather than twisting toward the working side.",
    ),
    (
        ("one", "arm"),
        "Working one arm at a time removes the help the other arm normally "
        "provides -- keep your torso square rather than twisting toward the working side.",
    ),
    (
        ("one", "leg"),
        "Performing this on one leg at a time removes the help the other leg "
        "normally provides, so expect it to demand more balance.",
    ),
    (
        ("elevated", "feet"),
        "Elevating your feet shifts more of your body weight onto your "
        "upper body, increasing the difficulty.",
    ),
    (
        ("neutral", "grip"),
        "A neutral grip (palms facing each other) is often easier on the "
        "wrists and shoulders than a full overhand or underhand grip.",
    ),
    (
        ("mixed", "grip"),
        "A mixed grip (one palm forward, one back) lets you hold more weight "
        "than a double-overhand grip -- alternate which hand faces which way between sets.",
    ),
    (
        ("reverse", "grip"),
        "A reverse (underhand/supinated) grip changes which muscles assist "
        "the main movers compared to a standard grip.",
    ),
    (
        ("underhand", "grip"),
        "An underhand (supinated) grip brings more biceps into a pulling "
        "movement than an overhand grip.",
    ),
    (
        ("overhand", "grip"),
        "An overhand (pronated) grip is the standard grip for this "
        "movement, emphasizing the back and shoulders over the biceps.",
    ),
    (
        ("wide", "grip"),
        "A wider hand placement than standard shifts emphasis to different "
        "muscles and shortens the range of motion slightly.",
    ),
    (
        ("close", "grip"),
        "A closer hand placement than standard lengthens the range of motion "
        "and shifts more emphasis onto the arms.",
    ),
    (
        ("ez", "bar"),
        "An EZ-bar's angled grips are easier on the wrists than a straight bar -- "
        "keep your hands on the angled sections.",
    ),
    (
        ("swiss", "ball"),
        "A Swiss ball adds an unstable base -- brace your core to avoid "
        "rolling, and use a lighter load than the stable version.",
    ),
    (
        ("medicine", "ball"),
        "Holding a medicine ball adds load and something to grip -- keep a "
        "secure hold on it throughout.",
    ),
    (
        ("foam", "roller"),
        "A foam roller adds instability similar to a Swiss ball -- move "
        "slowly and keep your core braced.",
    ),
    (
        ("bosu", "balance"),
        "A BOSU trainer's unstable dome demands extra core and ankle "
        "stabilization -- move slower and use less load than the stable version.",
    ),
    (
        ("get", "up"),
        "This sequences several positions into one continuous movement -- move "
        "through each step deliberately rather than rushing to the next.",
    ),
    (
        ("towel",),
        "Gripping a towel instead of a bar challenges your grip strength more than "
        "usual -- expect your forearms to fatigue faster.",
    ),
    (
        ("hollow",),
        "A hollow body position presses your lower back into the floor and holds "
        "your limbs slightly off it -- keep that contact with the floor throughout.",
    ),
    (
        ("suspended",),
        "Suspension straps add instability and let you adjust difficulty by "
        "changing your body angle -- the more horizontal your body, the harder it gets.",
    ),
    (
        ("trx",),
        "Suspension straps add instability and let you adjust difficulty by changing "
        "your body angle -- the more horizontal your body, the harder it gets.",
    ),
    (
        ("ring",),
        "Gymnastic rings add instability a fixed bar/handle doesn't have -- control "
        "the movement rather than letting the rings swing.",
    ),
    (
        ("rings",),
        "Gymnastic rings add instability a fixed bar/handle doesn't have -- control "
        "the movement rather than letting the rings swing.",
    ),
    (
        ("barbell",),
        "A barbell gives continuous, fixed-path resistance -- keep an even grip "
        "across the whole range.",
    ),
    (
        ("dumbbell",),
        "Dumbbells let each side move independently -- keep both sides moving at "
        "the same pace so one side doesn't do more of the work.",
    ),
    (
        ("dumbbells",),
        "Dumbbells let each side move independently -- keep both sides moving at "
        "the same pace so one side doesn't do more of the work.",
    ),
    (
        ("kettlebell",),
        "A kettlebell's handle sits above the weight -- keep your wrist neutral "
        "rather than letting the bell roll around in your grip.",
    ),
    (
        ("cable",),
        "A cable keeps tension on the muscle through the full range, including the "
        "stretched position, unlike a free weight.",
    ),
    (
        ("machine",),
        "A machine guides the movement path for you -- set the seat/pad height so "
        "the pivot point lines up with your own joint.",
    ),
    (
        ("smith",),
        "A Smith machine fixes the bar to a vertical (or angled) track, so your feet "
        "can sit slightly forward of the bar since the path is already fixed.",
    ),
    (
        ("band",),
        "A resistance band gets harder to stretch as you move through the range -- "
        "expect the hardest part to be near full extension, not the start.",
    ),
    (
        ("banded",),
        "A resistance band gets harder to stretch as you move through the range -- "
        "expect the hardest part to be near full extension, not the start.",
    ),
    (
        ("decline",),
        "A decline angle (head lower than hips/feet) shifts more load onto your "
        "upper body -- expect it to feel harder than the flat version.",
    ),
    (
        ("incline",),
        "An incline angle shifts more load onto your lower chest and front "
        "shoulders -- keep your core braced so you don't slide down.",
    ),
    (
        ("kneeling",),
        "Performing this from a kneeling position removes your legs from the base "
        "of support, isolating the working muscles more and reducing momentum.",
    ),
    (
        ("seated",),
        "Sitting removes your legs from the movement, isolating the target muscles "
        "and reducing the ability to use momentum from your lower body.",
    ),
    (
        ("standing",),
        "Standing brings your core and legs into play for stability, unlike a "
        "seated or lying version of the same movement.",
    ),
    (
        ("lying",),
        "Lying down removes your legs from the base of support and can let you "
        "isolate the target muscle more directly.",
    ),
    (
        ("inverted",),
        "An inverted position reverses which way gravity loads the movement -- "
        "keep your core braced throughout.",
    ),
    (
        ("crossover",),
        "Crossing past the midline of your body adds a rotational component -- "
        "move under control rather than swinging across.",
    ),
    (
        ("diagonal",),
        "Moving on a diagonal rather than straight up-and-down or side-to-side "
        "brings a rotational element into the exercise -- keep it smooth rather than jerky.",
    ),
    (
        ("rotational",),
        "This adds a rotational (twisting) component through your torso -- "
        "initiate the rotation from your core, not just your arms.",
    ),
    (
        ("rotation",),
        "This adds a rotational (twisting) component through your torso -- "
        "initiate the rotation from your core, not just your arms.",
    ),
    (
        ("alternating",),
        "Alternate sides each rep rather than completing a full set on one side before switching.",
    ),
    (
        ("staggered",),
        "A staggered stance (one foot ahead of the other) shifts more load onto "
        "the forward leg/arm -- keep most of your weight there.",
    ),
    (
        ("split",),
        "A split stance (one foot forward, one back) narrows your base -- keep your "
        "torso upright and your weight balanced between both legs.",
    ),
    (
        ("sumo",),
        "A sumo stance (feet wider than shoulder-width, toes turned out) shifts more "
        "emphasis onto your inner thighs and glutes than a standard stance.",
    ),
    (
        ("curtsy",),
        "A curtsy step (crossing one leg diagonally behind the other) adds a "
        "lateral, rotational component compared to a straight lunge.",
    ),
    (
        ("weighted",),
        "Adding external weight increases the resistance beyond your body weight "
        "alone -- start lighter than you think you need until your form is solid.",
    ),
    (
        ("plyometric",),
        "This is an explosive (plyometric) version -- move as fast as you can "
        "on the way up while still landing/finishing softly and under control.",
    ),
    (
        ("explosive",),
        "This is an explosive version -- move as fast as you can through the "
        "power phase while still finishing under control.",
    ),
    (
        ("jump",),
        "This is an explosive, jumping version -- move as fast as you can on the way "
        "up while still landing softly and under control.",
    ),
    (
        ("jumps",),
        "This is an explosive, jumping version -- move as fast as you can on the way "
        "up while still landing softly and under control.",
    ),
    (
        ("isometric",),
        "This is a held (isometric) version -- rather than performing reps, hold "
        "the working position steady for the target duration.",
    ),
    (
        ("static",),
        "This is a held (static) version -- rather than performing reps, hold the "
        "working position steady for the target duration.",
    ),
    (
        ("handstand",),
        "An inverted handstand position shifts the entire load onto your "
        "shoulders and arms -- build up against a wall for support before attempting it "
        "freestanding.",
    ),
    (
        ("hanging",),
        "Hanging from a bar removes your legs from the base of support entirely -- "
        "keep swinging to a minimum by controlling the movement.",
    ),
    (
        ("chin",),
        "A chin-up-style underhand grip brings more biceps into the movement than a "
        "standard overhand pull-up grip.",
    ),
    (
        ("modified",),
        "This is an easier, modified version of the standard movement -- use it "
        "to build the strength and control needed for the full version.",
    ),
    (
        ("sliding",),
        "Sliding discs (or a towel on a smooth floor) remove friction, so the "
        "working limb has to control the movement actively rather than just lifting and setting "
        "down -- move slowly.",
    ),
    (
        ("pilates",),
        "This follows Pilates' own emphasis on slow, controlled movement driven "
        "from your deep core -- prioritize control over speed or range.",
    ),
    (
        ("crossed",),
        "Crossing your limbs changes your base of support and can add a "
        "rotational element -- keep the movement controlled.",
    ),
    (
        ("pike",),
        "A pike position hinges sharply at the hips with straight legs -- keep your "
        "core braced so the movement comes from your hips, not your lower back rounding.",
    ),
    (
        ("v",),
        "A V-shaped body position works both your upper and lower abs together -- keep "
        "your lower back from arching off the floor.",
    ),
    (
        ("mountain", "climber"),
        "Rather than holding the position static, drive your knees in "
        "toward your chest one at a time at a controlled pace, keeping your hips low throughout.",
    ),
    (
        ("pistol",),
        "A pistol squat is performed on one leg, with the other leg extended "
        "straight out in front of you for balance -- it demands far more balance, ankle "
        "mobility, and single-leg strength than a two-legged squat.",
    ),
]

# A few modifier words mean genuinely different things in different exercise families ("reverse"
# alone means backward-stepping for a lunge, but a wholly different leg-driven movement for a
# hip raise/crunch -- see SUB_BASE_OVERRIDES below for those) -- rather than one global note that
# would be right in one category and wrong in another, these are scoped to the one category
# where the meaning is unambiguous.
CATEGORY_SCOPED_NOTES: dict[str, list[tuple[tuple[str, ...], str]]] = {
    "LUNGE": [
        (
            ("reverse",),
            "A reverse lunge steps backward instead of forward -- it's often "
            "gentler on the front knee, since there's less forward momentum to control.",
        ),
        (
            ("walking",),
            "A walking lunge continues forward into the next step rather than "
            "returning to the start position each rep.",
        ),
        (
            ("side",),
            "A side (lateral) lunge steps out to the side rather than forward or "
            "back, bending the stepping knee while keeping the other leg straight.",
        ),
    ],
}

# Exercises that share a category with a real base movement but are, on inspection, a
# genuinely different exercise Garmin's own categorization happens to file alongside it (e.g.
# "Weight-plate Front Raise" under Shoulder Press, or several Olympic-lift-derived barbell
# movements under Squat) -- composing these from the category's base steps would produce
# actively wrong instructions (a front raise is not a press; a barbell snatch is not "just a
# squat"), so they're deliberately left with only the honest muscle-group-only description
# instead, the same as any other tier-3 exercise with no real base. Found by reading every
# compositional category's full exercise list, not assumed from the name pattern alone.
EXCLUDE_FROM_COMPOSITION: set[tuple[str, str]] = {
    ("CALF_RAISE", "SINGLE_LEG_DECLINE_PUSH_UP"),  # a push-up, not a calf raise
    ("CALF_RAISE", "SINGLE_LEG_HIP_RAISE_WITH_KNEE_HOLD"),  # a hip raise, not a calf raise
    ("CRUNCH", "TOES_TO_BAR"),  # a hanging exercise, not a lying crunch
    ("CRUNCH", "WEIGHTED_TOES_TO_BAR"),
    ("HYPEREXTENSION", "LAT_PULL_DOWN_WITH_ROW"),  # a pull-down/row combo, not a back extension
    ("HYPEREXTENSION", "OVERHEAD_LUNGE_WITH_MEDICINE_BALL"),  # a lunge
    ("HYPEREXTENSION", "PLANK_KNEE_TUCKS"),  # a plank exercise
    ("HYPEREXTENSION", "WEIGHTED_PLANK_KNEE_TUCKS"),
    ("HYPEREXTENSION", "SIDE_STEP"),  # a lateral stepping drill
    ("HYPEREXTENSION", "WEIGHTED_SIDE_STEP"),
    ("LATERAL_RAISE", "MUSCLE_UP"),  # a pull-up/dip combo, not a raise
    ("LATERAL_RAISE", "RING_MUSCLE_UP"),
    ("LATERAL_RAISE", "WEIGHTED_RING_MUSCLE_UP"),
    ("LATERAL_RAISE", "RING_DIP_KIPPING"),  # a dip
    ("LATERAL_RAISE", "WEIGHTED_RING_DIP"),
    ("LATERAL_RAISE", "WEIGHTED_ROPE_CLIMB"),  # a rope climb
    ("LATERAL_RAISE", "WALL_SLIDE"),  # a shoulder-mobility drill
    ("LATERAL_RAISE", "WALL_WALK"),  # a bear-crawl-up-the-wall drill
    ("LATERAL_RAISE", "WEIGHTED_WALL_SLIDE"),
    ("LATERAL_RAISE", "CALORIE_ROW"),  # a rowing-machine test, not a raise
    ("LATERAL_RAISE", "_45_DEGREE_CABLE_EXTERNAL_ROTATION"),  # a rotator-cuff exercise
    ("PLANK", "BEAR_CRAWL"),  # a crawling movement, not a static hold
    ("PLANK", "WEIGHTED_BEAR_CRAWL"),
    ("PULL_UP", "EZ_BAR_PULLOVER"),  # a pullover, not a pull-up
    ("PULL_UP", "SWISS_BALL_EZ_BAR_PULLOVER"),
    ("SHOULDER_PRESS", "WEIGHT_PLATE_FRONT_RAISE"),  # a front raise, not a press
    ("SHRUG", "SCAPTION_AND_SHRUG"),  # a scapular-plane raise, not a shrug
    ("SHRUG", "SCAPULAR_RETRACTION"),  # the opposite motion of a shrug
    ("SHRUG", "SERRATUS_CHAIR_SHRUG"),  # scapular protraction, a different plane of motion
    ("SHRUG", "SERRATUS_SHRUG"),
    ("SHRUG", "WEIGHTED_SERRATUS_CHAIR_SHRUG"),
    ("SHRUG", "WEIGHTED_SERRATUS_SHRUG"),
    ("SQUAT", "SQUAT_AMERICAN_SWING"),  # a ballistic swing, not a squat
    ("SQUAT", "BARBELL_HANG_SQUAT_SNATCH"),  # an Olympic lift -- "just squat" would mislead
    ("SQUAT", "BARBELL_SQUAT_SNATCH"),
    ("SQUAT", "DUMBBELL_SQUAT_CLEAN"),
    ("SQUAT", "DUMBBELL_SQUAT_SNATCH"),
    ("SQUAT", "KETTLEBELL_SWING_OVERHEAD"),  # a swing, not a squat
    ("SQUAT", "KETTLEBELL_SWING_WITH_FLIP_TO_SQUAT"),
}

# A handful of exercises are a genuinely different (but common enough to be worth doing
# properly) movement from their category's own base -- e.g. a "reverse hip raise" is done prone
# on a bench lifting your legs, nothing like the supine glute-bridge "Hip Raise" base. Each gets
# its own real step list (still original, not copied) rather than being forced through the
# category's unrelated base, or excluded outright. Keyed by exact (category, exercise); the
# stored "base name" is only used to compute which of that exercise's OTHER words (weighted,
# swiss ball, single-leg, ...) still deserve a MODIFIER_NOTES clause on top.
_REVERSE_CRUNCH_STEPS = [
    "Lie on your back with your knees bent and your hands at your sides or under your lower "
    "back for support.",
    "Lift your feet off the floor so your knees are bent toward your chest, shins roughly "
    "parallel to the floor.",
    "Curl your hips and knees up and in toward your chest by contracting your lower abs, "
    "keeping your upper back and head still on the floor.",
    "Lower your legs back down under control until your feet are just above the floor, "
    "without letting your lower back arch.",
    "Keep the movement coming from your hips and abs, not from swinging your legs.",
]
_REVERSE_HIP_RAISE_STEPS = [
    "Lie face down on a bench or sturdy raised surface with your hips at the edge and your "
    "legs hanging off, holding onto the bench or a fixed point for support.",
    "Keep your legs straight or with a slight bend in the knees, together.",
    "Squeeze your glutes and lower back to raise your legs up until they're in line with your "
    "torso, without arching your lower back past neutral.",
    "Hold briefly at the top, then lower your legs back down under control.",
    "Keep the movement slow and controlled rather than swinging your legs up.",
]
_LAT_PULLDOWN_STEPS = [
    "Sit at a lat pulldown machine (or kneel in front of a high cable/band anchor) and grip "
    "the bar or handle wider than shoulder-width.",
    "Sit tall with your torso slightly leaned back, chest up, and brace your core.",
    "Pull the bar down toward your upper chest by driving your elbows down and back, "
    "squeezing your shoulder blades together.",
    "Avoid leaning back excessively or using your bodyweight to yank the bar down.",
    "Let the bar rise back up under control to a full stretch before starting the next rep.",
]
_GOOD_MORNING_STEPS = [
    "Stand with feet hip- to shoulder-width apart, a barbell resting across your upper back "
    "(or hands behind your head for a bodyweight version).",
    "Keep a soft bend in your knees and brace your core.",
    "Hinge forward at the hips, pushing your hips back while keeping your back flat, until "
    "your torso is close to parallel with the floor.",
    "Feel a stretch through your hamstrings at the bottom, without rounding your lower back.",
    "Drive your hips forward to return to standing, squeezing your glutes at the top.",
]
_DIP_STEPS = [
    "Grip parallel bars (or the edge of a sturdy bench/chair for a bodyweight version) and "
    "support your body with your arms straight.",
    "Keep your shoulders down and away from your ears, and lean your torso slightly forward "
    "to emphasize your chest and triceps.",
    "Bend your elbows to lower your body until your upper arms are roughly parallel to the "
    "floor, or as far as feels comfortable on your shoulders.",
    "Keep your elbows tracking back rather than flaring straight out to the sides.",
    "Press back up through your palms to full arm extension, without shrugging your shoulders up.",
]

_SubBaseEntry = dict[tuple[str, str], tuple[str, list[str]]]


def _sub_base(
    category: str, base_name: str, steps: list[str], exercises: list[str]
) -> _SubBaseEntry:
    return {(category, exercise): (base_name, steps) for exercise in exercises}


SUB_BASE_OVERRIDES: dict[tuple[str, str], tuple[str, list[str]]] = {
    **_sub_base(
        "CRUNCH",
        "Reverse Crunch",
        _REVERSE_CRUNCH_STEPS,
        [
            "CROSS_LEG_REVERSE_CRUNCH",
            "FOAM_ROLLER_REVERSE_CRUNCH_ON_BENCH",
            "FOAM_ROLLER_REVERSE_CRUNCH_WITH_DUMBBELL",
            "FOAM_ROLLER_REVERSE_CRUNCH_WITH_MEDICINE_BALL",
            "INCLINE_REVERSE_CRUNCH",
            "REVERSE_CURL_AND_LIFT",
            "SEATED_ALTERNATING_REVERSE_CRUNCH",
            "SINGLE_LEG_REVERSE_CRUNCH",
            "WEIGHTED_CROSS_LEG_REVERSE_CRUNCH",
            "WEIGHTED_FOAM_ROLLER_REVERSE_CRUNCH_ON_BENCH",
            "WEIGHTED_INCLINE_REVERSE_CRUNCH",
            "WEIGHTED_REVERSE_CRUNCH",
            "WEIGHTED_REVERSE_CRUNCH_ON_A_BENCH",
            "WEIGHTED_REVERSE_CURL_AND_LIFT",
            "WEIGHTED_SEATED_ALTERNATING_REVERSE_CRUNCH",
            "WEIGHTED_SINGLE_LEG_REVERSE_CRUNCH",
        ],
    ),
    **_sub_base(
        "SIT_UP",
        "Reverse Curl-up",
        _REVERSE_CRUNCH_STEPS,
        [
            "REVERSE_CURL_UP",
            "WEIGHTED_REVERSE_CURL_UP",
        ],
    ),
    **_sub_base(
        "HIP_RAISE",
        "Reverse Hip Raise",
        _REVERSE_HIP_RAISE_STEPS,
        [
            "REVERSE_HIP_RAISE",
            "WEIGHTED_REVERSE_HIP_RAISE",
            "BENT_KNEE_SWISS_BALL_REVERSE_HIP_RAISE",
            "WEIGHTED_BENT_KNEE_SWISS_BALL_REVERSE_HIP_RAISE",
        ],
    ),
    **_sub_base(
        "HIP_RAISE",
        "Incline Rear-leg Extension",
        _REVERSE_HIP_RAISE_STEPS,
        [
            "INCLINE_REAR_LEG_EXTENSION",
            "WEIGHTED_INCLINE_REAR_LEG_EXTENSION",
        ],
    ),
    **_sub_base(
        "PULL_UP",
        "Lat Pulldown",
        _LAT_PULLDOWN_STEPS,
        [
            "_30_DEGREE_LAT_PULLDOWN",
            "CLOSE_GRIP_LAT_PULLDOWN",
            "KNEELING_LAT_PULLDOWN",
            "KNEELING_UNDERHAND_GRIP_LAT_PULLDOWN",
            "REVERSE_GRIP_PULLDOWN",
            "STRAIGHT_ARM_PULLDOWN",
        ],
    ),
    **_sub_base(
        "LEG_CURL",
        "Good Morning",
        _GOOD_MORNING_STEPS,
        [
            "SINGLE_LEG_BARBELL_GOOD_MORNING",
            "SPLIT_BARBELL_GOOD_MORNING",
            "STAGGERED_STANCE_GOOD_MORNING",
            "ZERCHER_GOOD_MORNING",
        ],
    ),
    **_sub_base(
        "TRICEPS_EXTENSION",
        "Dip",
        _DIP_STEPS,
        [
            "BODY_WEIGHT_DIP",
            "SUSPENDED_DIP",
            "TABLETOP_DIP",
            "WEIGHTED_DIP",
            "WEIGHTED_INCLINE_DIP",
            "WEIGHTED_SUSPENDED_DIP",
            "WEIGHTED_TABLETOP_DIP",
            "SINGLE_LEG_BENCH_DIP_AND_KICK",
            "WEIGHTED_SINGLE_LEG_BENCH_DIP_AND_KICK",
        ],
    ),
}


def _extra_words(name: str, base_name: str) -> list[str]:
    base_words = set(_norm(base_name).split())
    return [w for w in _norm(name).split() if w not in base_words]


def _composed_notes(name: str, base_name: str, category: str) -> list[str]:
    words = _extra_words(name, base_name)
    notes: list[str] = []
    for phrase, note in [*MODIFIER_NOTES, *CATEGORY_SCOPED_NOTES.get(category, [])]:
        n = len(phrase)
        matched = any(tuple(words[i : i + n]) == phrase for i in range(len(words) - n + 1))
        if matched and note not in notes:
            notes.append(note)
    return notes


def _muscle_sentence(primary: list[str], secondary: list[str]) -> str | None:
    if not primary and not secondary:
        return None
    parts = []
    if primary:
        parts.append(f"Primarily targets the {', '.join(primary).lower()}")
    if secondary:
        lead = "also works" if primary else "Works"
        parts.append(f"{lead} the {', '.join(secondary).lower()}")
    sentence = "; ".join(parts) + "."
    return sentence[0].upper() + sentence[1:]


def main() -> None:
    print("Fetching Garmin's master exercise/muscle list...")
    master = _fetch_json(MASTER_URL)

    garmin_entries = [
        {
            "name": name,
            "category": category,
            "categoryLabel": _category_label(category),
            "exercise": exercise,
        }
        for name, category, exercise in _RAW
    ]
    garmin_entries.sort(key=lambda e: (e["categoryLabel"], e["name"]))

    print(f"Checking {len(garmin_entries)} exercises for Garmin's own photo/description...")
    pairs = sorted({(e["category"], e["exercise"]) for e in garmin_entries})
    details = _fetch_all_details(pairs)

    print("Fetching free-exercise-db (public domain, github.com/yuhonas/free-exercise-db)...")
    fed_list = _fetch_json(FED_DB_URL)
    fed_matches = _match_fed_exercises(garmin_entries, fed_list)

    tier_counts = {1: 0, 2: 0, 3: 0}
    out_entries = []
    for g in garmin_entries:
        category, exercise, name = g["category"], g["exercise"], g["name"]
        cat_data = master.get("categories", {}).get(category, {}).get("exercises", {})
        ex_data = cat_data.get(exercise, {})
        primary = [m.replace("_", " ").title() for m in ex_data.get("primaryMuscles", [])]
        secondary = [m.replace("_", " ").title() for m in ex_data.get("secondaryMuscles", [])]

        entry = {
            "name": name,
            "category": category,
            "categoryLabel": g["categoryLabel"],
            "exercise": exercise,
            # Only set for tier 1 -- see module docstring on why. Garmin's exercise pages sit
            # entirely behind a sign-in wall (confirmed live: even a detailed exercise like
            # PUSH_UP bounces an unauthenticated visitor to sso.garmin.com), and there is no way
            # to confirm from here that a non-detailed exercise has any real content once past
            # it, so this app only links to the ones it knows for certain have something to show.
            "garmin_url": None,
            "primary_muscles": primary,
            "secondary_muscles": secondary,
        }

        detail = details.get(f"{category}/{exercise}")
        fed = fed_matches.get(name)

        if detail and detail.get("heroImage"):
            entry.update(
                tier=1,
                garmin_url=GARMIN_PAGE_URL.format(category=category, exercise=exercise),
                image_url=f"https://connect.garmin.com{detail['heroImage']}",
                image_source="garmin",
                description=detail.get("description"),
                # Only set for the literal base entry of a category BASE_STEPS_OVERRIDE covers
                # (name == categoryLabel) -- e.g. "Push-up" itself, never another real,
                # independently-photographed tier-1 exercise that happens to share its category
                # ("Mountain Climber" under Plank, "Kettlebell Swing" under Hip Raise), which
                # already has its own correct Garmin description and needs no generic steps
                # stapled on that could contradict it.
                steps=(
                    BASE_STEPS_OVERRIDE.get(category)
                    if _norm(name) == _norm(g["categoryLabel"])
                    else None
                ),
                difficulty=detail.get("difficulty"),
            )
            tier_counts[1] += 1
        elif fed:
            if not primary and not secondary:
                entry["primary_muscles"] = [m.title() for m in fed.get("primaryMuscles", [])]
                entry["secondary_muscles"] = [m.title() for m in fed.get("secondaryMuscles", [])]
            instructions = fed.get("instructions") or []
            entry.update(
                tier=2,
                image_url=FED_IMAGE_BASE + fed["images"][0],
                image_source="free-exercise-db",
                # Kept as a real step list now (previously flattened into one paragraph) --
                # `description` stays None for tier 2 so the same content isn't shown twice.
                description=None,
                steps=instructions or None,
                difficulty=fed.get("level"),
                matched_exercise_name=fed["name"],
            )
            tier_counts[2] += 1
        else:
            curated = HAND_CURATED_STEPS.get((category, exercise))
            entry.update(
                tier=3,
                image_url=None,
                image_source=None,
                description=_muscle_sentence(primary, secondary),
                difficulty=None,
                steps=curated["steps"] if curated else None,
                reference_url=curated["reference_url"] if curated else None,
                reference_label=curated["reference_label"] if curated else None,
            )
            tier_counts[3] += 1

        out_entries.append(entry)

    hand_curated_used = sum(
        1
        for e in out_entries
        if e.get("steps")
        and e["tier"] == 3
        and (e["category"], e["exercise"]) in HAND_CURATED_STEPS
    )
    unused_curated = set(HAND_CURATED_STEPS) - {
        (e["category"], e["exercise"]) for e in out_entries if e.get("steps") and e["tier"] == 3
    }
    if unused_curated:
        print(f"WARNING: HAND_CURATED_STEPS keys never matched a real exercise: {unused_curated}")

    # Second pass: a category's own base exercise (tier 1 via BASE_STEPS_OVERRIDE, tier 2 via
    # its real free-exercise-db steps, or tier 3 via HAND_CURATED_STEPS) is now known for every
    # category regardless of iteration order, so every OTHER tier-3 exercise in that same
    # category -- a genuinely named variant of that one base movement, not an unrelated exercise
    # -- can be composed from the base's own real steps plus honest, specific notes on exactly
    # what its name adds beyond the base (see MODIFIER_NOTES). This is the bulk of tier 3: of
    # the ~1,150 tier-3 exercises, roughly 800 are named variants inside a category that has a
    # real base this way.
    category_base: dict[str, tuple[str, list[str], str | None, str | None]] = {}
    for e in out_entries:
        if _norm(e["name"]) == _norm(e["categoryLabel"]) and e.get("steps"):
            curated = HAND_CURATED_STEPS.get((e["category"], e["exercise"]))
            category_base[e["category"]] = (
                e["name"],
                e["steps"],
                curated["reference_url"] if curated else None,
                curated["reference_label"] if curated else None,
            )

    composed_count = 0
    for e in out_entries:
        if e["tier"] != 3 or e.get("steps"):
            continue
        key = (e["category"], e["exercise"])
        if key in EXCLUDE_FROM_COMPOSITION:
            continue

        sub_base = SUB_BASE_OVERRIDES.get(key)
        if sub_base:
            base_name, base_steps = sub_base
            ref_url = ref_label = None
        else:
            base = category_base.get(e["category"])
            if not base:
                continue
            base_name, base_steps, ref_url, ref_label = base

        notes = _composed_notes(e["name"], base_name, e["category"])
        e["steps"] = [*base_steps, *notes] if notes else list(base_steps)
        if notes and ref_url:
            e["reference_url"] = ref_url
            e["reference_label"] = ref_label
        composed_count += 1

    OUT_PATH.write_text(json.dumps(out_entries, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {len(out_entries)} exercises to {OUT_PATH}\n"
        f"  tier 1 (Garmin's own photo + description): {tier_counts[1]}\n"
        f"  tier 2 (free-exercise-db photo + step-by-step instructions): {tier_counts[2]}\n"
        f"  tier 3 (muscle groups only, no photo): {tier_counts[3]}"
        f" ({hand_curated_used} hand-curated + {composed_count} composed from a base + "
        f"{tier_counts[3] - hand_curated_used - composed_count} muscle-group-only)"
    )


if __name__ == "__main__":
    main()
