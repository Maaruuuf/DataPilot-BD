"""Reporting Agent: AI-written executive summary + charts -> printable HTML report (Feature 8)."""
import html
from datetime import datetime
from typing import Dict, List, Optional

from ..core.llm import LLM, LANG_NAME
from ..core.localization import to_bn_digits

T = {
    "en": {"title": "Data Quality & Insights Report", "generated": "Generated", "score": "Data Quality Score",
           "summary": "Executive Summary", "insights": "Key Insights", "anomalies": "Anomalies & Risks",
           "recs": "Recommendations", "dims": "Quality Dimensions", "issues_by_rule": "Issues by Check",
           "top_cols": "Columns with Most Issues", "dataset": "Dataset", "rows": "Rows", "cols": "Columns",
           "missing": "Missing cells", "dups": "Duplicate rows", "completeness": "Completeness",
           "validity": "Validity", "uniqueness": "Uniqueness", "consistency": "Consistency",
           "modules": "Operational Highlights", "print": "Print / Save as PDF", "none": "No major issues found."},
    "bn": {"title": "ডেটা মান ও বিশ্লেষণ প্রতিবেদন", "generated": "তৈরির সময়", "score": "ডেটা কোয়ালিটি স্কোর",
           "summary": "নির্বাহী সারসংক্ষেপ", "insights": "প্রধান পর্যবেক্ষণ", "anomalies": "অসঙ্গতি ও ঝুঁকি",
           "recs": "সুপারিশ", "dims": "মানের মাত্রা", "issues_by_rule": "যাচাই অনুযায়ী ত্রুটি",
           "top_cols": "সবচেয়ে বেশি ত্রুটিযুক্ত কলাম", "dataset": "ডেটাসেট", "rows": "সারি", "cols": "কলাম",
           "missing": "খালি ঘর", "dups": "ডুপ্লিকেট সারি", "completeness": "সম্পূর্ণতা",
           "validity": "বৈধতা", "uniqueness": "অনন্যতা", "consistency": "সামঞ্জস্য",
           "modules": "কার্যক্রমের হাইলাইট", "print": "প্রিন্ট / পিডিএফ", "none": "উল্লেখযোগ্য কোনো সমস্যা পাওয়া যায়নি।"},
}


def svg_bars(items: List[tuple], width: int = 560, color: str = "#1d4ed8", maxv: Optional[float] = None) -> str:
    """Horizontal bar chart as inline SVG (no external dependencies, prints cleanly)."""
    if not items:
        return ""
    maxv = maxv or max(v for _, v in items) or 1
    bar_h, gap, label_w = 22, 8, 190
    h = len(items) * (bar_h + gap) + 4
    parts = [f'<svg viewBox="0 0 {width} {h}" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">']
    for i, (label, v) in enumerate(items):
        y = i * (bar_h + gap) + 2
        w = max((width - label_w - 60) * v / maxv, 1)
        parts.append(f'<text x="0" y="{y + 16}" font-size="12" fill="#334155">{html.escape(str(label))[:30]}</text>')
        parts.append(f'<rect x="{label_w}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="3" fill="{color}"/>')
        parts.append(f'<text x="{label_w + w + 6:.1f}" y="{y + 16}" font-size="12" fill="#0f172a">{v:g}</text>')
    parts.append("</svg>")
    return "".join(parts)


def fallback_narrative(profile: Dict, validation: Dict, lang: str) -> Dict:
    s, sc = validation["summary"], validation["score"]
    worst = sorted(s["by_column"].items(), key=lambda x: -x[1])[:3]
    rules = sorted(s["by_rule"].items(), key=lambda x: -x[1])[:3]
    if lang == "bn":
        return {
            "headline": f"ডেটা কোয়ালিটি স্কোর {to_bn_digits(sc['overall'])}/১০০ (গ্রেড {sc['grade']})",
            "summary": (f"{to_bn_digits(profile['rows'])}টি রেকর্ডে {to_bn_digits(s['total'])}টি সমস্যা পাওয়া গেছে, "
                        f"যার মধ্যে {to_bn_digits(s['fixable'])}টি স্বয়ংক্রিয়ভাবে সংশোধনযোগ্য।"),
            "key_insights": [f"{c}: {to_bn_digits(n)}টি সমস্যা" for c, n in worst],
            "anomalies": [f"{r}: {to_bn_digits(n)}টি" for r, n in rules],
            "recommendations": ["অনুমোদন সাপেক্ষে স্বয়ংক্রিয় সংশোধনগুলো প্রয়োগ করুন।",
                                "ডেটা এন্ট্রির সময় ফর্ম্যাট যাচাই চালু করুন।"]}
    return {
        "headline": f"Data quality score {sc['overall']}/100 (grade {sc['grade']})",
        "summary": (f"The dataset has {profile['rows']} records. {s['total']} issues were found across "
                    f"{s['rows_affected']} records; {s['fixable']} can be corrected automatically after approval."),
        "key_insights": [f"{c} has the most problems ({n})." for c, n in worst] or [],
        "anomalies": [f"{r.replace('_', ' ')}: {n} case(s)" for r, n in rules] or [],
        "recommendations": ["Review and approve the suggested corrections, then re-validate.",
                            "Enforce format checks (mobile, NID, dates) at the point of data entry.",
                            "Resolve duplicate and missing-value records before the data is used for payments or compliance."],
    }


def ai_narrative(profile: Dict, validation: Dict, extras: Dict, llm: LLM, lang: str) -> Dict:
    base = fallback_narrative(profile, validation, lang)
    if not llm.available:
        return base
    facts = {"rows": profile["rows"], "columns": profile["columns"], "missing_pct": profile["missing_pct"],
             "score": validation["score"], "issues": validation["summary"],
             "columns_profile": [{k: c[k] for k in ("name", "kind", "null_pct", "unique")} for c in profile["column_info"]],
             "operational_extras": extras}
    system = ("You are a senior MIS analyst writing for the Head of an airline engineering department in Bangladesh. "
              "Use ONLY the facts given - do not invent numbers. Return JSON: {\"headline\": str, \"summary\": str "
              "(max 70 words), \"key_insights\": [3-5 str], \"anomalies\": [up to 5 str], "
              f"\"recommendations\": [3-5 concrete actions]}}. Write in {LANG_NAME.get(lang, 'English')}.")
    data = llm.json(system, str(facts))
    if isinstance(data, dict) and data.get("summary"):
        for k in base:
            if k in data and data[k]:
                base[k] = data[k]
    return base


def build_html(name: str, profile: Dict, validation: Dict, narrative: Dict, lang: str, extras: Dict) -> str:
    t = T.get(lang, T["en"])
    sc, s = validation["score"], validation["summary"]
    fmt = (lambda x: to_bn_digits(x)) if lang == "bn" else str
    dims = [(t["completeness"], sc["completeness"]), (t["validity"], sc["validity"]),
            (t["uniqueness"], sc["uniqueness"]), (t["consistency"], sc["consistency"])]
    rules = sorted(s["by_rule"].items(), key=lambda x: -x[1])[:8]
    cols = sorted(s["by_column"].items(), key=lambda x: -x[1])[:8]

    def ul(items):
        return "<ul>" + "".join(f"<li>{html.escape(str(i))}</li>" for i in items) + "</ul>" if items else f"<p>{t['none']}</p>"

    extra_html = ""
    if extras:
        rows = "".join(f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>" for k, v in extras.items())
        extra_html = f"<h2>{t['modules']}</h2><table>{rows}</table>"
    color = "#15803d" if sc["overall"] >= 85 else "#b45309" if sc["overall"] >= 70 else "#b91c1c"
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{html.escape(name)} - {t['title']}</title>
<style>
body{{font-family:'Noto Sans Bengali','Segoe UI',Arial,sans-serif;max-width:860px;margin:30px auto;padding:0 20px;color:#0f172a;line-height:1.55}}
h1{{margin:0 0 4px}} h2{{border-bottom:2px solid #e2e8f0;padding-bottom:4px;margin-top:28px;font-size:18px}}
.meta{{color:#64748b;font-size:13px}} .score{{display:flex;gap:24px;align-items:center;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:16px;margin-top:16px}}
.big{{font-size:52px;font-weight:700;color:{color}}} .grade{{font-size:22px;font-weight:600;color:{color}}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:14px}} .kpi{{border:1px solid #e2e8f0;border-radius:8px;padding:10px;text-align:center}}
.kpi b{{display:block;font-size:20px}} table{{border-collapse:collapse;width:100%}} td{{border:1px solid #e2e8f0;padding:6px 10px;font-size:14px}}
button{{margin-top:20px;padding:8px 14px}} @media print{{button{{display:none}}}}
</style></head><body>
<h1>{t['title']}</h1><div class="meta">{t['dataset']}: {html.escape(name)} &middot; {t['generated']}: {datetime.now():%Y-%m-%d %H:%M} &middot; DataPilot BD</div>
<div class="score"><div><div class="big">{fmt(sc['overall'])}</div><div class="grade">{t['score']} &middot; {sc['grade']}</div></div>
<div style="flex:1"><b>{html.escape(str(narrative.get('headline','')))}</b><p>{html.escape(str(narrative.get('summary','')))}</p></div></div>
<div class="kpis"><div class="kpi"><b>{fmt(profile['rows'])}</b>{t['rows']}</div><div class="kpi"><b>{fmt(profile['columns'])}</b>{t['cols']}</div>
<div class="kpi"><b>{fmt(profile['missing_cells'])}</b>{t['missing']}</div><div class="kpi"><b>{fmt(profile['duplicate_rows'])}</b>{t['dups']}</div></div>
<h2>{t['dims']}</h2>{svg_bars(dims, color='#0f766e', maxv=100)}
<h2>{t['insights']}</h2>{ul(narrative.get('key_insights'))}
<h2>{t['anomalies']}</h2>{ul(narrative.get('anomalies'))}
<h2>{t['issues_by_rule']}</h2>{svg_bars([(r.replace('_',' '), n) for r, n in rules])}
<h2>{t['top_cols']}</h2>{svg_bars(cols, color='#b45309')}
{extra_html}
<h2>{t['recs']}</h2>{ul(narrative.get('recommendations'))}
<button onclick="window.print()">{t['print']}</button></body></html>"""
