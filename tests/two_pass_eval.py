"""Two-pass LLM pipeline validation test.

Measures Pass 1 (screening) false negative rate on known-relevant papers,
and end-to-end recall/precision of the combined Pass 1 + Pass 2 system.
Supports hybrid configs: local model for Pass 1, online model for Pass 2, etc.

Usage:
    # List available configs
    python3 tests/two_pass_eval.py --list

    # Run one config at a time (recommended — avoids RAM overheating)
    python3 tests/two_pass_eval.py --config local-llama3.2_both
    python3 tests/two_pass_eval.py --config local-llama3.2_online-Ring
    python3 tests/two_pass_eval.py --config local-llama3.2_online-OCRFast

    # Pass 1 only — screen papers, save pass1_<tag>.csv, skip Pass 2
    python3 tests/two_pass_eval.py --config local-qwen3.5_p1only --pass1-only
    python3 tests/two_pass_eval.py --config local-mistral7b_p1only --pass1-only
    python3 tests/two_pass_eval.py --config local-llama3.1_p1only --pass1-only

    # Pass 2 only — reuse an existing pass1 CSV, run only Pass 2
    python3 tests/two_pass_eval.py --config local-llama3.2_both --reuse-pass1 tests/pass1_local-llama3.2_both.csv

    # Run all configs in sequence (not recommended — RAM thermal risk)
    python3 tests/two_pass_eval.py --all

Requires:
    - OPENROUTER_KEY env var for any config with online models
    - LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY env vars for Langfuse tracing (optional)
    - labeled_papers.csv in tests/ (auto-generated from EndNote XML if not present)

Output per run:
    tests/pass1_<tag>.csv          (skipped when --reuse-pass1 is used)
    tests/pass2_<tag>.csv          (skipped when --pass1-only is used)
    tests/comparison_summary.csv   (appended after each run, includes total_time_s)
"""

import os
import csv
import json
import time
import re
import uuid
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ── Config ────────────────────────────────────────────────────────────────────

OPENROUTER_KEY  = os.environ.get("OPENROUTER_KEY", "")
OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"
OLLAMA_BASE     = "http://localhost:11434/v1/chat/completions"

SCORE_THRESHOLD = 6

# Early-stop thresholds for Pass 1
SMOKE_N           = 10    # papers for Pass 1 --smoke mode
UNCLEAR_BAIL      = 3     # consecutive UNCLEAR responses → bail (model is looping)
SLOW_THRESHOLD_S  = 15.0  # seconds per call to count as "slow"
SLOW_BAIL         = 3     # consecutive slow calls → bail (model is stuck)

# Pass 2 smoke — balanced label selection
SMOKE_P2 = {"relevant": 2, "borderline": 1, "irrelevant": 2}  # 5 papers total

# Thermal monitoring (Apple Silicon via powermetrics)
THERMAL_CHECK_EVERY = 10   # check every N papers
THERMAL_WARN_W      = 25.0  # warn above this CPU power draw (watts)
THERMAL_PAUSE_W     = 35.0  # auto-pause above this (throttling likely imminent)

# Langfuse (optional) — set env vars or leave blank to disable
LANGFUSE_HOST       = os.environ.get("LANGFUSE_HOST", "http://localhost:3030")
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_ENABLED    = bool(LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY)

# Available configs: (pass1_model, pass1_local, pass2_model, pass2_local, tag)
# Sleep is applied only between online calls — local calls have no sleep.
CONFIGS = {
    # ── Original benchmark configs ────────────────────────────────────────────
    "local-llama3.2_both": (
        "llama3.2:latest", True,
        "llama3.2:latest", True,
    ),
    "local-llama3.2_online-Ring": (
        "llama3.2:latest",              True,
        "inclusionai/ring-2.6-1t:free", False,
    ),
    "local-llama3.2_online-OCRFast": (
        "llama3.2:latest",             True,
        "baidu/Qianfan-OCR-Fast:free", False,
    ),
    # ── Pass 1 candidates — use --pass1-only ─────────────────────────────────
    # Already installed — test these first, no downloads needed
    "p1-qwen3.5-0.8b": (          # 1.0 GB — fastest, test first
        "qwen3.5:0.8b", True,
        "llama3.2:latest", True,
    ),
    "p1-qwen3.5-4b": (            # 3.4 GB — mid-size Qwen
        "qwen3.5:4b", True,
        "llama3.2:latest", True,
    ),
    "p1-gemma3-4b": (             # 3.3 GB — strong instruction following
        "gemma3:4b", True,
        "llama3.2:latest", True,
    ),
    "p1-phi4-mini-reasoning": (   # 3.2 GB — reasoning variant already installed
        "phi4-mini-reasoning:3.8b", True,
        "llama3.2:latest", True,
    ),
    "p1-nemotron-nano-4b": (      # 2.8 GB — NVIDIA nano
        "nemotron-3-nano:4b", True,
        "llama3.2:latest", True,
    ),
    "p1-mistral7b": (             # 4.4 GB — ⚠ caused overheating in previous run; take breaks
        "mistral:7b", True,
        "llama3.2:latest", True,
    ),
    "p1-llama3.1-8b": (           # 4.9 GB — largest local P1 candidate
        "llama3.1:8b", True,
        "llama3.2:latest", True,
    ),
    # Needs download — pull with: ollama pull granite3.3:2b
    "p1-granite3.3-2b": (         # 1.5 GB — IBM, explicitly strong at classification
        "granite3.3:2b", True,
        "llama3.2:latest", True,
    ),
    # ── Pass 2 candidates — use --reuse-pass1 ────────────────────────────────
    # Already installed
    "p2-qwen3.5-4b": (            # 3.4 GB — best local P2 bet (strong reasoning)
        "llama3.2:latest", True,
        "qwen3.5:4b", True,
    ),
    "p2-mistral7b": (             # 4.4 GB
        "llama3.2:latest", True,
        "mistral:7b", True,
    ),
    "p2-llama3.1-8b": (           # 4.9 GB — biggest installed
        "llama3.2:latest", True,
        "llama3.1:8b", True,
    ),
    "p2-gemma3-4b": (             # 3.3 GB
        "llama3.2:latest", True,
        "gemma3:4b", True,
    ),
    # Needs download — pull with: ollama pull granite3.3:8b
    "p2-granite3.3-8b": (         # 4.9 GB — IBM, strong at classification/summarization
        "llama3.2:latest", True,
        "granite3.3:8b", True,
    ),
    # Needs download — pull with: ollama pull qwen3.5:9b  (~6.6 GB, needs ~10 GB free RAM)
    "p2-qwen3.5-9b": (
        "llama3.2:latest", True,
        "qwen3.5:9b", True,
    ),
}

RESEARCH_PROFILE = """The researcher is a PhD student investigating sex differences in
neuropathic pain and neuroinflammation, with focus on peripheral nerve injury mechanisms
in animal models and humans. Core topics: sex differences, microglia, neuropathic pain,
peripheral nerve injury, neuroinflammation, immune cells, pain hypersensitivity, spinal
cord, dorsal root ganglion."""

TESTS_DIR   = Path(__file__).parent
XML_PATH    = TESTS_DIR.parent / "My EndNote Library-Converted.xml"
LABELED_CSV = TESTS_DIR / "labeled_papers.csv"
SUMMARY_CSV = TESTS_DIR / "comparison_summary.csv"

# ── Langfuse HTTP tracing ─────────────────────────────────────────────────────

def _lf_post(batch: list):
    """Fire-and-forget POST to Langfuse ingestion API."""
    if not LANGFUSE_ENABLED:
        return
    import base64
    token = base64.b64encode(f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()).decode()
    payload = json.dumps({"batch": batch}).encode()
    req = Request(
        f"{LANGFUSE_HOST}/api/public/ingestion",
        data=payload,
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type":  "application/json",
        },
    )
    try:
        with urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"    [langfuse] warning: {e}")


def lf_log_generation(trace_id: str, name: str, model: str,
                       system: str, user: str, output: str,
                       start_iso: str, end_iso: str,
                       metadata: dict | None = None):
    """Log a single LLM generation to Langfuse."""
    if not LANGFUSE_ENABLED:
        return
    _lf_post([{
        "type": "generation",
        "id":   str(uuid.uuid4()),
        "body": {
            "traceId":   trace_id,
            "name":      name,
            "model":     model,
            "input":     [{"role": "system", "content": system},
                          {"role": "user",   "content": user}],
            "output":    output,
            "startTime": start_iso,
            "endTime":   end_iso,
            "metadata":  metadata or {},
        },
    }])


def lf_create_trace(trace_id: str, name: str, metadata: dict | None = None):
    if not LANGFUSE_ENABLED:
        return
    _lf_post([{
        "type": "trace",
        "id":   str(uuid.uuid4()),
        "body": {
            "id":       trace_id,
            "name":     name,
            "metadata": metadata or {},
        },
    }])


# ── XML → labeled CSV ─────────────────────────────────────────────────────────

def get_text(el):
    if el is None:
        return ""
    return "".join(s.text or "" for s in el.findall(".//style")).strip()

def get_tags(rec):
    return set(get_text(kw) for kw in rec.findall(".//keywords/keyword") if get_text(kw))

def build_labeled_csv():
    print(f"Building labeled_papers.csv from {XML_PATH} ...")
    tree  = ET.parse(str(XML_PATH))
    recs  = tree.getroot().findall(".//record")

    sex_tags = {
        "Sex differences", "sex differences", "Sex Factors", "Sex Characteristics",
        "*Sex Characteristics", "sex difference", "Sex difference", "Sex-difference",
        "Gender differences", "Sexual Dimorphism", "sex dimorphism", "Sex dimorphism",
        "Microglial sex differences",
    }
    epi_tags = {
        "Prevalence", "Incidence", "Cross-Sectional Studies", "Cohort Studies",
        "Case-Control Studies", "Surveys and Questionnaires", "United States/epidemiology",
        "Risk Factors", "Prospective Studies",
    }
    core_tags = {
        "Neuropathic pain", "neuropathic pain", "microglia", "peripheral nerve injury",
        "nerve injury", "Nerve injury", "neuroinflammation", "spinal cord",
        "nerve regeneration", "Pain", "pain", "Chronic pain", "chronic pain",
        "Pain Measurement", "Inflammation", "inflammation", "Cytokines", "cytokines",
        "macrophages", "fibromyalgia",
    }
    offtopic_tags = {
        "Cell Line, Tumor", "Neoplasms", "Cancer", "Diabetes Mellitus",
        "Cardiovascular Diseases",
    }

    rows = []
    for rec in recs:
        abstract = get_text(rec.find(".//abstract"))
        if not abstract:
            continue
        title = get_text(rec.find(".//title"))
        tags  = get_tags(rec)

        if tags & sex_tags:
            label = "relevant"
        elif (tags & epi_tags) and not (tags & core_tags):
            label = "borderline"
        elif (tags & offtopic_tags) and not (tags & core_tags):
            label = "irrelevant"
        else:
            continue
        rows.append({"title": title, "abstract": abstract, "label": label})

    with open(LABELED_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["title", "abstract", "label"])
        w.writeheader()
        w.writerows(rows)

    counts = {l: sum(1 for r in rows if r["label"] == l)
              for l in ("relevant", "borderline", "irrelevant")}
    print(f"  Written {len(rows)} papers: {counts}")
    return rows

# ── LLM calls ─────────────────────────────────────────────────────────────────

def call_llm(model: str, system: str, user: str, max_tokens: int,
             use_local: bool, retries: int = 4, base_delay: float = 8.0,
             trace_id: str = "", lf_name: str = "") -> tuple[str, str, str]:
    """Returns (content, start_iso, end_iso). Logs to Langfuse if enabled."""
    base_url = OLLAMA_BASE if use_local else OPENROUTER_BASE
    key      = "ollama"   if use_local else OPENROUTER_KEY

    if not use_local and not key:
        raise ValueError("OPENROUTER_KEY not set")

    payload = json.dumps({
        "model":       model,
        "messages":    [{"role": "system", "content": system},
                        {"role": "user",   "content": user}],
        "max_tokens":  max_tokens,
        "temperature": 0.1,
    }).encode()
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://github.com/samzhou108/PaperMatcher",
    }

    for attempt in range(retries):
        start_dt  = datetime.now(timezone.utc)
        start_iso = start_dt.isoformat()
        req = Request(base_url, data=payload, headers=headers)
        try:
            with urlopen(req, timeout=120) as resp:
                data    = json.loads(resp.read())
                content = data["choices"][0]["message"]["content"].strip()
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                end_iso = datetime.now(timezone.utc).isoformat()
                if trace_id and lf_name:
                    lf_log_generation(trace_id, lf_name, model,
                                      system, user, content,
                                      start_iso, end_iso,
                                      {"attempt": attempt, "use_local": use_local})
                return content, start_iso, end_iso
        except Exception as e:
            err = str(e)
            if "429" in err and attempt < retries - 1:
                delay = base_delay * (2 ** attempt)
                print(f"    [rate limit] waiting {delay:.0f}s ... (retry {attempt+1})")
                time.sleep(delay)
            else:
                end_iso = datetime.now(timezone.utc).isoformat()
                return f"ERROR: {e}", start_iso, end_iso

    return "ERROR: max retries exceeded", "", ""


PASS1_SYSTEM = f"""You are screening research papers for relevance to a researcher's profile.

{RESEARCH_PROFILE}

Your only job is to decide if this paper could be relevant. Be PERMISSIVE — when in doubt, answer MAYBE.
Respond with exactly one word: YES, MAYBE, or NO."""

PASS2_SYSTEM = f"""You are a research assistant scoring papers for relevance to a researcher.

{RESEARCH_PROFILE}

Respond in this exact format:
Score: [1-10]
Reason: [one sentence]
Summary: [two sentences]

Scoring guide: 9-10 = directly addresses core topics; 7-8 = relevant with partial overlap;
5-6 = broad relevance; 3-4 = tangential; 1-2 = unrelated."""


def pass1_screen(title: str, abstract: str, model: str,
                  use_local: bool, trace_id: str = "") -> tuple[str, float]:
    """Returns (decision, elapsed_s)."""
    user    = f"Title: {title}\n\nAbstract: {abstract[:1500]}"
    max_tok = 512 if use_local else 20
    t0      = time.time()
    content, _, _ = call_llm(model, PASS1_SYSTEM, user, max_tok,
                              use_local, trace_id=trace_id, lf_name="pass1_screen")
    elapsed = time.time() - t0
    upper = content.upper()
    for word in ("YES", "MAYBE", "NO"):
        if word in upper:
            return word, elapsed
    return f"UNCLEAR:{content[:40]}", elapsed


def pass2_score(title: str, abstract: str, model: str,
                 use_local: bool, trace_id: str = "") -> tuple[dict, float]:
    """Returns (result_dict, elapsed_s)."""
    user = f"Title: {title}\n\nAbstract: {abstract[:2000]}"
    t0   = time.time()
    resp, _, _ = call_llm(model, PASS2_SYSTEM, user, 350,
                           use_local, trace_id=trace_id, lf_name="pass2_score")
    elapsed = time.time() - t0
    result  = {"score": None, "reason": "", "summary": "", "raw": resp}
    for line in resp.splitlines():
        line = line.strip()
        if line.lower().startswith("score:"):
            try:
                result["score"] = int("".join(c for c in line.split(":", 1)[1] if c.isdigit())[:2])
            except ValueError:
                pass
        elif line.lower().startswith("reason:"):
            result["reason"] = line.split(":", 1)[1].strip()
        elif line.lower().startswith("summary:"):
            result["summary"] = line.split(":", 1)[1].strip()
    return result, elapsed


def mean_sd(vals):
    if not vals:
        return 0.0, 0.0
    m  = sum(vals) / len(vals)
    sd = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5
    return m, sd


def _p1_group(rows, lbl):
    grp    = [r for r in rows if r["label"] == lbl]
    passed = [r for r in grp if r["passes"]]
    missed = [r for r in grp if not r["passes"]]
    return len(passed), len(grp), missed


def _print_p1_summary(rows, total_time, relevant_total):
    rel_p, rel_t, rel_missed = _p1_group(rows, "relevant")
    brd_p, brd_t, _           = _p1_group(rows, "borderline")
    irr_p, irr_t, _           = _p1_group(rows, "irrelevant")
    unclear_n = sum(1 for r in rows if r["pass1_decision"].startswith("UNCLEAR"))
    print(f"\n── Pass 1 results ({len(rows)} papers, LLM time: {total_time:.1f}s) ──")
    if rel_t:
        print(f"  Recall (relevant):         {rel_p}/{rel_t} = {rel_p/rel_t*100:.1f}%  [target ≥95%]")
    if brd_t:
        print(f"  Pass-through (borderline): {brd_p}/{brd_t} = {brd_p/brd_t*100:.1f}%")
    if irr_t:
        print(f"  FP rate (irrelevant):      {irr_p}/{irr_t} = {irr_p/irr_t*100:.1f}%")
    if unclear_n:
        print(f"  ⚠ UNCLEAR responses:       {unclear_n} (model did not follow format)")
    if rel_missed:
        print(f"  False negatives:")
        for r in rel_missed:
            print(f"    ✗ {r['title'][:75]}")


def _p1_only_summary(tag, p1_display, p1_total_time, rows, relevant_total,
                     trace_id, stopped=""):
    rel_p, rel_t, rel_missed = _p1_group(rows, "relevant")
    brd_p, brd_t, _           = _p1_group(rows, "borderline")
    irr_p, irr_t, _           = _p1_group(rows, "irrelevant")
    return {
        "tag": tag, "p1_model": p1_display, "p2_model": "(skipped)",
        "p1_recall":        round(rel_p / rel_t, 4) if rel_t else 0,
        "p1_fns":           len(rel_missed),
        "p1_borderline_pt": round(brd_p / brd_t, 4) if brd_t else 0,
        "p1_irr_fp":        round(irr_p / irr_t, 4) if irr_t else 0,
        "p2_rel_mean": 0, "p2_rel_sd": 0, "p2_brd_mean": 0, "p2_irr_mean": 0,
        "e2e_recall": 0,
        "p1_time_s":    round(p1_total_time, 1),
        "p2_time_s":    0,
        "total_time_s": round(p1_total_time, 1),
        "stopped":      stopped,
        "langfuse_trace": trace_id if LANGFUSE_ENABLED else "",
    }


# ── Thermal monitoring ───────────────────────────────────────────────────────

def _read_thermal() -> tuple[float, bool]:
    """
    Sample CPU power draw and throttle state via powermetrics.
    Returns (cpu_watts, is_throttling).
    Requires sudo — silently returns (0.0, False) if unavailable.
    """
    try:
        import subprocess
        result = subprocess.run(
            ["sudo", "-n", "powermetrics", "--samplers", "cpu_power", "-n", "1"],
            capture_output=True, text=True, timeout=8,
        )
        watts      = 0.0
        throttling = False
        for line in result.stdout.splitlines():
            line_l = line.lower()
            if "cpu power" in line_l and "w" in line_l:
                # e.g. "CPU Power: 5.13W"
                for token in line.split():
                    token = token.rstrip("W").rstrip("w")
                    try:
                        watts = float(token)
                        break
                    except ValueError:
                        continue
            if "throttle: yes" in line_l:
                throttling = True
        return watts, throttling
    except Exception:
        return 0.0, False


def thermal_check(paper_idx: int) -> None:
    """Print a thermal warning if CPU power is high. Pause if very hot."""
    if paper_idx % THERMAL_CHECK_EVERY != 0:
        return
    watts, throttling = _read_thermal()
    if watts == 0.0:
        return  # powermetrics unavailable (no sudo -n access) — skip silently
    if throttling:
        print(f"\n  🌡 THROTTLING DETECTED ({watts:.1f}W) — pausing 60s to cool down ...")
        time.sleep(60)
    elif watts >= THERMAL_PAUSE_W:
        print(f"\n  🌡 Very hot ({watts:.1f}W) — pausing 30s ...")
        time.sleep(30)
    elif watts >= THERMAL_WARN_W:
        print(f"\n  🌡 Warm ({watts:.1f}W) — consider a break after this paper")


def _select_smoke_p2_papers(papers: list) -> list:
    """Pick a balanced subset for a Pass 2 smoke test.

    Picks directly from the full labeled set regardless of P1 decisions,
    so label diversity is guaranteed even if irrelevant papers were filtered.
    Returns list of (paper_dict, fake_pass1_row) tuples ready for P2 scoring.
    """
    by_label: dict[str, list] = {"relevant": [], "borderline": [], "irrelevant": []}
    for p in papers:
        lbl = p.get("label", "")
        if lbl in by_label:
            by_label[lbl].append(p)

    selected = []
    for lbl, count in SMOKE_P2.items():
        pool = by_label[lbl]
        chosen = pool[:count]
        for p in chosen:
            fake_r1 = {
                "title": p["title"][:80], "label": lbl,
                "pass1_decision": "SMOKE", "passes": True, "elapsed_s": 0.0,
            }
            selected.append((p, fake_r1))

    total = sum(SMOKE_P2.values())
    labels = ", ".join(f"{c}×{l}" for l, c in SMOKE_P2.items() if c)
    print(f"\n  [P2 smoke] Selected {len(selected)}/{total} papers ({labels})")
    if len(selected) < total:
        print(f"  [P2 smoke] ⚠ Some labels had fewer papers than requested")
    return selected


# ── Single config eval ────────────────────────────────────────────────────────

def run_single_eval(papers: list, tag: str,
                     p1_model: str, p1_local: bool,
                     p2_model: str, p2_local: bool,
                     pass1_only: bool = False,
                     reuse_pass1_csv: str = "",
                     smoke: bool = False) -> dict:
    """
    Run full 2-pass eval for one config.
    Sleep is only applied after online (non-local) calls to respect rate limits.
    pass1_only: run Pass 1 only, skip Pass 2.
    reuse_pass1_csv: path to an existing pass1_<tag>.csv — skip Pass 1, run Pass 2 only.
    smoke: run only the first SMOKE_N papers as a quick sanity check, then exit.
    Returns summary dict (None fields if stopped early / smoke mode).
    """
    ONLINE_SLEEP = 2.0   # seconds between online calls
    relevant_total = sum(1 for p in papers if p["label"] == "relevant")

    safe_tag = tag.replace(":", "_").replace("/", "_")
    p1_out   = TESTS_DIR / f"pass1_{safe_tag}.csv"
    p2_out   = TESTS_DIR / f"pass2_{safe_tag}.csv"

    p1_display = f"{p1_model} (local)" if p1_local else p1_model
    p2_display = f"{p2_model} (local)" if p2_local else p2_model

    # One Langfuse trace per config run
    trace_id = str(uuid.uuid4())
    lf_create_trace(trace_id, f"two_pass_eval/{tag}", {
        "p1_model": p1_display,
        "p2_model": p2_display,
        "n_papers": len(papers),
        "tag":      tag,
    })

    print(f"\n{'#'*65}")
    print(f"CONFIG : {tag}")
    print(f"Pass 1 : {p1_display}")
    print(f"Pass 2 : {p2_display}")
    if LANGFUSE_ENABLED:
        print(f"Langfuse trace: {LANGFUSE_HOST}/traces/{trace_id}")
    print(f"{'#'*65}")

    # ── Pass 1 ────────────────────────────────────────────────────────────────
    pass1_rows    = []
    p1_total_time = 0.0

    if reuse_pass1_csv:
        # Load pre-computed Pass 1 decisions — skip running Pass 1
        print(f"\nPass 1 — loading saved decisions from {reuse_pass1_csv} ...")
        with open(reuse_pass1_csv, newline="", encoding="utf-8") as f:
            saved = list(csv.DictReader(f))
        saved_by_title = {r["title"]: r for r in saved}
        for p in papers:
            short = p["title"][:80]
            r = saved_by_title.get(short, {})
            decision = r.get("pass1_decision", "YES")   # default pass if not in CSV
            passes   = decision in ("YES", "MAYBE")
            p1_total_time += float(r.get("elapsed_s", 0))
            pass1_rows.append({
                "title":          short,
                "label":          p["label"],
                "pass1_decision": decision,
                "passes":         passes,
                "elapsed_s":      float(r.get("elapsed_s", 0)),
            })
        print(f"  Loaded {len(pass1_rows)} decisions ({sum(1 for r in pass1_rows if r['passes'])} pass).")
    else:
        run_papers = papers[:SMOKE_N] if smoke else papers
        mode_label = f"SMOKE ({SMOKE_N} papers)" if smoke else f"{len(papers)} papers"
        print(f"\nPass 1 — screening {mode_label} with {p1_model} ...")
        if not smoke:
            print(f"  Auto-stop triggers: {UNCLEAR_BAIL} consecutive UNCLEAR  |  "
                  f"{SLOW_BAIL} consecutive calls >{SLOW_THRESHOLD_S:.0f}s")

        consecutive_unclear = 0
        consecutive_slow    = 0
        bail_reason         = ""

        for i, p in enumerate(run_papers):
            decision, elapsed = pass1_screen(p["title"], p["abstract"],
                                              p1_model, p1_local, trace_id)
            p1_total_time += elapsed
            passes = decision in ("YES", "MAYBE")
            is_unclear = decision.startswith("UNCLEAR")
            is_slow    = p1_local and elapsed > SLOW_THRESHOLD_S

            # Update streak counters
            consecutive_unclear = consecutive_unclear + 1 if is_unclear else 0
            consecutive_slow    = consecutive_slow    + 1 if is_slow    else 0

            pass1_rows.append({
                "title":          p["title"][:80],
                "label":          p["label"],
                "pass1_decision": decision,
                "passes":         passes,
                "elapsed_s":      round(elapsed, 2),
            })

            status = "✓" if passes else ("⚠" if is_unclear else "✗")
            slow_flag = "  ⏱SLOW" if is_slow else ""
            print(f"  [{i+1:3d}/{len(run_papers)}] {status} {decision:14s}  [{p['label']:10s}]  "
                  f"{elapsed:5.1f}s{slow_flag}  {p['title'][:45]}")

            if not p1_local:
                time.sleep(ONLINE_SLEEP)

            thermal_check(i + 1)

            # Bail checks (not in smoke mode — smoke is just observational)
            if not smoke:
                if consecutive_unclear >= UNCLEAR_BAIL:
                    bail_reason = (f"UNCLEAR×{UNCLEAR_BAIL} in a row — model is looping or "
                                   f"ignoring the prompt")
                    break
                if consecutive_slow >= SLOW_BAIL:
                    bail_reason = (f">{SLOW_THRESHOLD_S:.0f}s for {SLOW_BAIL} consecutive calls "
                                   f"— model too slow for screening")
                    break

        # Save whatever we have (partial or full)
        with open(p1_out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["title", "label", "pass1_decision", "passes", "elapsed_s"])
            w.writeheader()
            w.writerows(pass1_rows)

        if bail_reason:
            print(f"\n  ⛔ AUTO-STOPPED after {len(pass1_rows)} papers: {bail_reason}")
            print(f"  Partial results saved to {p1_out.name}")
            # Print what we saw before bailing
            _print_p1_summary(pass1_rows, p1_total_time, relevant_total)
            return _p1_only_summary(tag, p1_display, p1_total_time, pass1_rows,
                                    relevant_total, trace_id, stopped=bail_reason)

        if smoke:
            print(f"\n  ── Smoke test complete ({len(pass1_rows)} papers) ──")
            _print_p1_summary(pass1_rows, p1_total_time, relevant_total)
            print(f"\n  Results saved to {p1_out.name}")
            print(f"  If results look good, re-run without --smoke for the full test.")
            return _p1_only_summary(tag, p1_display, p1_total_time, pass1_rows,
                                    relevant_total, trace_id, stopped="smoke")

    _print_p1_summary(pass1_rows, p1_total_time, relevant_total)

    if pass1_only:
        print(f"\n  [--pass1-only] Skipping Pass 2. Results saved to {p1_out.name}")
        return _p1_only_summary(tag, p1_display, p1_total_time, pass1_rows,
                                relevant_total, trace_id)

    # ── Pass 2 ────────────────────────────────────────────────────────────────
    if smoke:
        to_score = _select_smoke_p2_papers(papers)
        print(f"\nPass 2 — SMOKE scoring {len(to_score)} papers with {p2_model} ...")
    else:
        to_score = [(p, r) for p, r in zip(papers, pass1_rows) if r["passes"]]
        print(f"\nPass 2 — scoring {len(to_score)} papers that passed Pass 1 ...")
    pass2_rows    = []
    p2_total_time = 0.0

    for i, (p, r1) in enumerate(to_score):
        result, elapsed = pass2_score(p["title"], p["abstract"],
                                       p2_model, p2_local, trace_id)
        p2_total_time += elapsed
        pass2_rows.append({
            "title":           p["title"][:80],
            "label":           p["label"],
            "pass1_decision":  r1["pass1_decision"],
            "score":           result["score"],
            "above_threshold": result["score"] is not None and result["score"] >= SCORE_THRESHOLD,
            "reason":          result["reason"][:120],
            "summary":         result["summary"][:200],
            "elapsed_s":       round(elapsed, 2),
        })
        score_str = str(result["score"]) if result["score"] is not None else "?"
        print(f"  [{i+1:3d}/{len(to_score)}] score={score_str:>2s}  [{p['label']:10s}]  "
              f"{elapsed:5.1f}s  {p['title'][:50]}")
        if not p2_local:
            time.sleep(ONLINE_SLEEP)

    with open(p2_out, "w", newline="", encoding="utf-8") as f:
        fn = ["title", "label", "pass1_decision", "score", "above_threshold",
              "reason", "summary", "elapsed_s"]
        w  = csv.DictWriter(f, fieldnames=fn)
        w.writeheader()
        w.writerows(pass2_rows)

    def scores_for(lbl):
        return [r["score"] for r in pass2_rows if r["label"] == lbl and r["score"] is not None]

    rel_m, rel_sd = mean_sd(scores_for("relevant"))
    brd_m, brd_sd = mean_sd(scores_for("borderline"))
    irr_m, irr_sd = mean_sd(scores_for("irrelevant"))

    rel_above = [r for r in pass2_rows if r["label"] == "relevant" and r["above_threshold"]]
    e2e        = len(rel_above) / relevant_total if relevant_total else 0
    total_time = p1_total_time + p2_total_time

    if smoke:
        print(f"\n── Pass 2 SMOKE results ({len(pass2_rows)} papers, {p2_total_time:.1f}s) ──")
        for r in pass2_rows:
            score_str = str(r["score"]) if r["score"] is not None else "?"
            above = "✓" if r["above_threshold"] else "✗"
            print(f"  {above} score={score_str:>2s}  [{r['label']:10s}]  {r['title'][:55]}")
            if r["reason"]:
                print(f"       {r['reason'][:80]}")
        print(f"\n  Saved to {p2_out.name}")
        print(f"  If scores look reasonable, re-run without --smoke for the full test.")
        return _p1_only_summary(tag, p1_display, p1_total_time, pass1_rows,
                                relevant_total, trace_id, stopped="smoke")

    print(f"\n── Pass 2 results (LLM time: {p2_total_time:.1f}s) ──")
    print(f"  Score (relevant):   {rel_m:.1f} ± {rel_sd:.1f}  (n={len(scores_for('relevant'))})")
    print(f"  Score (borderline): {brd_m:.1f} ± {brd_sd:.1f}  (n={len(scores_for('borderline'))})")
    print(f"  Score (irrelevant): {irr_m:.1f} ± {irr_sd:.1f}  (n={len(scores_for('irrelevant'))})")
    print(f"  E2E recall (≥{SCORE_THRESHOLD}): {len(rel_above)}/{relevant_total} = {e2e*100:.1f}%")
    print(f"  TOTAL LLM TIME: {total_time:.1f}s  ({total_time/60:.1f} min)")

    rel_below = [r for r in pass2_rows
                 if r["label"] == "relevant" and r["score"] is not None and not r["above_threshold"]]
    if rel_below:
        print(f"  Relevant below threshold:")
        for r in rel_below:
            print(f"    score={r['score']}  {r['title'][:65]}")

    rel_p, rel_t, rel_missed = _p1_group(pass1_rows, "relevant")
    brd_p, brd_t, _           = _p1_group(pass1_rows, "borderline")
    irr_p, irr_t, _           = _p1_group(pass1_rows, "irrelevant")
    summary = {
        "tag":             tag,
        "p1_model":        p1_display,
        "p2_model":        p2_display,
        "p1_recall":       round(rel_p / rel_t, 4) if rel_t else 0,
        "p1_fns":          len(rel_missed),
        "p1_borderline_pt":round(brd_p / brd_t, 4) if brd_t else 0,
        "p1_irr_fp":       round(irr_p / irr_t, 4) if irr_t else 0,
        "p2_rel_mean":     round(rel_m, 2),
        "p2_rel_sd":       round(rel_sd, 2),
        "p2_brd_mean":     round(brd_m, 2),
        "p2_irr_mean":     round(irr_m, 2),
        "e2e_recall":      round(e2e, 4),
        "p1_time_s":       round(p1_total_time, 1),
        "p2_time_s":       round(p2_total_time, 1),
        "total_time_s":    round(total_time, 1),
        "stopped":         "",
        "langfuse_trace":  trace_id if LANGFUSE_ENABLED else "",
    }

    # Append (or create) comparison_summary.csv
    fieldnames = list(summary.keys())
    write_header = not SUMMARY_CSV.exists()
    with open(SUMMARY_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()
        w.writerow(summary)
    print(f"\n  Saved: {p1_out.name}, {p2_out.name}  (summary appended to {SUMMARY_CSV.name})")

    return summary


# ── Main ──────────────────────────────────────────────────────────────────────

def load_papers():
    if not LABELED_CSV.exists():
        return build_labeled_csv()
    with open(LABELED_CSV, newline="", encoding="utf-8") as f:
        papers = list(csv.DictReader(f))
    counts = {l: sum(1 for p in papers if p["label"] == l)
              for l in ("relevant", "borderline", "irrelevant")}
    print(f"Loaded {len(papers)} papers  (relevant={counts['relevant']}, "
          f"borderline={counts['borderline']}, irrelevant={counts['irrelevant']})")
    return papers


def main():
    parser = argparse.ArgumentParser(description="Two-pass LLM eval")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--config", choices=list(CONFIGS.keys()),
                       help="Run a single named config")
    group.add_argument("--all",    action="store_true",
                       help="Run all configs in sequence")
    group.add_argument("--list",   action="store_true",
                       help="List available configs and exit")
    parser.add_argument("--pass1-only", action="store_true",
                        help="Run Pass 1 only; skip Pass 2 entirely")
    parser.add_argument("--reuse-pass1", metavar="CSV",
                        help="Load Pass 1 decisions from a saved CSV and run Pass 2 only")
    parser.add_argument("--smoke", action="store_true",
                        help=f"Quick sanity check: run only first {SMOKE_N} papers, "
                             f"auto-stop if model loops or is too slow")
    args = parser.parse_args()

    if args.reuse_pass1 and args.pass1_only:
        print("ERROR: --pass1-only and --reuse-pass1 are mutually exclusive.")
        raise SystemExit(1)

    if args.list:
        print("Available configs:")
        for tag, (p1m, p1l, p2m, p2l) in CONFIGS.items():
            print(f"  {tag}")
            print(f"    Pass 1: {p1m} ({'local' if p1l else 'online'})")
            print(f"    Pass 2: {p2m} ({'local' if p2l else 'online'})")
        return

    # Validate keys before loading papers
    needs_online = False
    run_list = list(CONFIGS.items()) if args.all else [(args.config, CONFIGS[args.config])]
    for _, (_, p1l, _, p2l) in run_list:
        if (not p1l and not args.reuse_pass1) or (not p2l and not args.pass1_only):
            needs_online = True
    if needs_online and not OPENROUTER_KEY:
        print("ERROR: OPENROUTER_KEY env var required for online models.")
        print("  export OPENROUTER_KEY=sk-or-...")
        raise SystemExit(1)

    if LANGFUSE_ENABLED:
        print(f"Langfuse tracing enabled → {LANGFUSE_HOST}")
    else:
        print("Langfuse tracing disabled (set LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY to enable)")

    papers = load_papers()

    summaries = []
    for tag, (p1m, p1l, p2m, p2l) in run_list:
        # smoke+reuse-pass1 → run P2 smoke (don't skip P2)
        # smoke alone        → run P1 smoke only (skip P2)
        p1_only = args.pass1_only or (args.smoke and not args.reuse_pass1)
        result = run_single_eval(papers, tag, p1m, p1l, p2m, p2l,
                                 pass1_only=p1_only,
                                 reuse_pass1_csv=args.reuse_pass1 or "",
                                 smoke=args.smoke)
        # Don't write smoke or bailed runs to comparison_summary.csv
        stopped = result.get("stopped", "")
        if stopped in ("smoke",) or (stopped and not args.pass1_only):
            continue
        summaries.append(result)

    if len(summaries) > 1:
        print(f"\n{'='*95}")
        print("COMPARISON SUMMARY")
        print(f"{'='*95}")
        print(f"{'Config':<35} {'P1 Recall':>10} {'FNs':>4} {'P2 Rel↑':>8} "
              f"{'P2 Brd':>7} {'E2E':>8} {'Time(min)':>10}")
        print(f"{'-'*95}")
        for s in summaries:
            print(f"{s['tag']:<35} {s['p1_recall']*100:>9.1f}% {s['p1_fns']:>4d} "
                  f"{s['p2_rel_mean']:>7.1f}  {s['p2_brd_mean']:>6.1f}  "
                  f"{s['e2e_recall']*100:>7.1f}%  {s['total_time_s']/60:>8.1f}")


if __name__ == "__main__":
    main()
