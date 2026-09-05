"""Regenerates frontend/src/data/exerciseLibrary.json -- a browsable reference for every
exercise the hiit/strength_training picker supports (see ExerciseLibraryPage.tsx), with a photo,
description, muscle groups, and a link to the exercise's own Garmin Connect page, wherever that
data actually exists.

Three real, honestly-labeled tiers, not one blended guess -- see docs/adr/0015-scheduled-
workouts.md's own addendum on why this replaced an earlier draft that repeated one category
photo across dozens of exercises:

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
    JACCARD_REUSE_CAP = 3
    image_use_count: dict[str, int] = {}
    matched: dict[str, dict] = {}
    for rank, _score, gname, fed in candidates:
        image = fed["images"][0]
        if rank == JACCARD_RANK and image_use_count.get(image, 0) >= JACCARD_REUSE_CAP:
            continue
        image_use_count[image] = image_use_count.get(image, 0) + 1
        matched[gname] = fed
    return matched


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
                description=" ".join(instructions) if instructions else None,
                difficulty=fed.get("level"),
                matched_exercise_name=fed["name"],
            )
            tier_counts[2] += 1
        else:
            entry.update(
                tier=3,
                image_url=None,
                image_source=None,
                description=_muscle_sentence(primary, secondary),
                difficulty=None,
            )
            tier_counts[3] += 1

        out_entries.append(entry)

    OUT_PATH.write_text(json.dumps(out_entries, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {len(out_entries)} exercises to {OUT_PATH}\n"
        f"  tier 1 (Garmin's own photo + description): {tier_counts[1]}\n"
        f"  tier 2 (free-exercise-db photo + instructions): {tier_counts[2]}\n"
        f"  tier 3 (muscle groups only, no photo): {tier_counts[3]}"
    )


if __name__ == "__main__":
    main()
