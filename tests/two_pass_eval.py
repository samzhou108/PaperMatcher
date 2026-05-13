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

    # Run all configs in sequence (not recommended — RAM thermal risk)
    python3 tests/two_pass_eval.py --all

Requires:
    - OPENROUTER_KEY env var for any config with online models
    - LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY env vars for Langfuse tracing (optional)
    - labeled_papers.csv in tests/ (auto-generated from EndNote XML if not present)

Output per run:
    tests/pass1_<tag>.csv
    tests/pass2_<tag>.csv
    tests/comparison_summary.csv  (appended after each run, includes total_time_s)
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

# Langfuse (optional) — set env vars or leave blank to disable
LANGFUSE_HOST       = os.environ.get("LANGFUSE_HOST", "http://localhost:3030")
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_ENABLED    = bool(LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY)

# Available configs: (pass1_model, pass1_local, pass2_model, pass2_local, tag)
# Sleep is applied only between online calls — local calls have no sleep.
CONFIGS = {
    "local-llama3.2_both": (
        "llama3.2:latest", True,
        "llama3.2:latest", True,
    ),
    "local-llama3.2_online-Ring": (
        "llama3.2:latest",             True,
        "inclusionai/ring-2.6-1t:free", False,
    ),
    "local-llama3.2_online-OCRFast": (
        "llama3.2:latest",              True,
        "baidu/Qianfan-OCR-Fast:free",  False,
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
        "HTTP-Referer":  "https://github.com/pubmedpaperpilot",
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


# ── Single config eval ────────────────────────────────────────────────────────

def run_single_eval(papers: list, tag: str,
                     p1_model: str, p1_local: bool,
                     p2_model: str, p2_local: bool) -> dict:
    """
    Run full 2-pass eval for one config.
    Sleep is only applied after online (non-local) calls to respect rate limits.
    Returns summary dict.
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
    print(f"\nPass 1 — screening {len(papers)} papers ...")
    pass1_rows   = []
    p1_total_time = 0.0

    for i, p in enumerate(papers):
        decision, elapsed = pass1_screen(p["title"], p["abstract"],
                                          p1_model, p1_local, trace_id)
        p1_total_time += elapsed
        passes = decision in ("YES", "MAYBE")
        pass1_rows.append({
            "title":          p["title"][:80],
            "label":          p["label"],
            "pass1_decision": decision,
            "passes":         passes,
            "elapsed_s":      round(elapsed, 2),
        })
        status = "✓" if passes else "✗"
        print(f"  [{i+1:3d}/{len(papers)}] {status} {decision:8s}  [{p['label']:10s}]  "
              f"{elapsed:5.1f}s  {p['title'][:50]}")
        if not p1_local:
            time.sleep(ONLINE_SLEEP)

    with open(p1_out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["title", "label", "pass1_decision", "passes", "elapsed_s"])
        w.writeheader()
        w.writerows(pass1_rows)

    def p1_group(lbl):
        grp    = [r for r in pass1_rows if r["label"] == lbl]
        passed = [r for r in grp if r["passes"]]
        missed = [r for r in grp if not r["passes"]]
        return len(passed), len(grp), missed

    rel_p, rel_t, rel_missed = p1_group("relevant")
    brd_p, brd_t, _           = p1_group("borderline")
    irr_p, irr_t, _           = p1_group("irrelevant")

    print(f"\n── Pass 1 results (LLM time: {p1_total_time:.1f}s) ──")
    print(f"  Recall (relevant):         {rel_p}/{rel_t} = {rel_p/rel_t*100:.1f}%  [target ≥95%]")
    print(f"  Pass-through (borderline): {brd_p}/{brd_t} = {brd_p/brd_t*100:.1f}%")
    print(f"  FP rate (irrelevant):      {irr_p}/{irr_t} = {irr_p/irr_t*100:.1f}%")
    if rel_missed:
        print(f"  False negatives:")
        for r in rel_missed:
            print(f"    ✗ {r['title'][:75]}")

    # ── Pass 2 ────────────────────────────────────────────────────────────────
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
    args = parser.parse_args()

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
        if not p1l or not p2l:
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
        result = run_single_eval(papers, tag, p1m, p1l, p2m, p2l)
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
