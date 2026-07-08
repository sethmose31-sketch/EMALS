"""
app.py
------
An elaborate Streamlit dashboard for browsing the CV database produced by
build_database.py. Run with:

    streamlit run app.py
"""

import json
import os
import time
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

# ----------------------------------------------------------------------------
# Design tokens — "hiring dossier" aesthetic
# ----------------------------------------------------------------------------
COLOR_INK = "#1B2430"
COLOR_PAPER = "#F7F4EE"
COLOR_PAPER_CARD = "#FFFFFF"
COLOR_ACCENT = "#3D5A80"
COLOR_STAMP = "#C1793A"
COLOR_SAGE = "#5E7A6B"
COLOR_MUTED = "#8B8577"
COLOR_DANGER = "#B4543A"

SENIORITY_COLORS = {
    "Entry": "#8AA399",
    "Junior": "#5E7A6B",
    "Mid": "#3D5A80",
    "Senior": "#C1793A",
    "Lead": "#B4543A",
    "Executive": "#1B2430",
    "Unknown": "#8B8577",
}

st.set_page_config(page_title="Candidate Dossier", page_icon="📋", layout="wide")

# Custom CSS
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {{
    font-family: 'Inter', sans-serif;
}}
.stApp {{
    background-color: {COLOR_PAPER};
}}
h1, h2, h3 {{
    font-family: 'Fraunces', serif !important;
    color: {COLOR_INK} !important;
}}
section[data-testid="stSidebar"] {{
    background-color: {COLOR_INK};
}}
section[data-testid="stSidebar"] * {{
    color: #E8E4DA !important;
}}
.dossier-card {{
    background-color: {COLOR_PAPER_CARD};
    border: 1px solid #E5E0D5;
    border-left: 6px solid var(--stamp-color, {COLOR_ACCENT});
    border-radius: 6px;
    padding: 18px 20px;
    margin-bottom: 14px;
    position: relative;
}}
.stamp {{
    display: inline-block;
    font-family: 'Fraunces', serif;
    font-weight: 700;
    font-size: 0.7rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: white;
    padding: 3px 10px;
    border-radius: 3px;
    transform: rotate(-2deg);
}}
.candidate-name {{
    font-family: 'Fraunces', serif;
    font-size: 1.3rem;
    font-weight: 600;
    color: {COLOR_INK};
    margin-bottom: 2px;
}}
.candidate-title {{
    color: {COLOR_MUTED};
    font-size: 0.92rem;
    margin-bottom: 8px;
}}
.skill-chip {{
    display: inline-block;
    background-color: #EFEBE1;
    color: {COLOR_INK};
    padding: 2px 9px;
    border-radius: 12px;
    font-size: 0.78rem;
    margin: 2px 4px 2px 0;
}}
.metric-eyebrow {{
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.72rem;
    color: {COLOR_MUTED};
}}
.section-label {{
    font-family: 'Fraunces', serif;
    font-weight: 600;
    color: {COLOR_ACCENT};
    border-bottom: 2px solid {COLOR_ACCENT};
    display: inline-block;
    padding-bottom: 2px;
    margin-top: 18px;
    margin-bottom: 10px;
}}
.red-flag {{
    color: {COLOR_DANGER};
    font-size: 0.85rem;
}}
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------
@st.cache_data
def load_candidates(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        db = json.load(f)
    return db


def flatten_for_table(candidates):
    rows = []
    for c in candidates:
        contact = c.get("contact", {})
        rows.append({
            "Name": c.get("full_name") or "(Unknown)",
            "Title": c.get("current_title") or "—",
            "Seniority": c.get("seniority_level") or "Unknown",
            "Experience (yrs)": c.get("total_experience_years") or 0,
            "Email": contact.get("email") or "—",
            "Phone": contact.get("phone") or "—",
            "Location": contact.get("location") or "—",
            "Source File": c.get("source_file", "").split("/")[-1]  # Just show filename
        })
    return pd.DataFrame(rows)

# ----------------------------------------------------------------------------
# Main app
# ----------------------------------------------------------------------------
DATA_PATH = os.path.join("data", "candidates.json")
db = load_candidates(DATA_PATH)

st.markdown("<h1>📋 Candidate Dossier</h1>", unsafe_allow_html=True)

if db is None or not db.get("candidates"):
    st.warning(
        "No candidate database found yet. Run this first:\n\n"
        "```bash\n"
        "export GEMINI_API_KEY=your-key\n"
        "python build_database.py --input cvs_folder --output data/candidates.json\n"
        "```\n\n"
        "**Supported formats:** PDF, DOCX, DOC, JPG, PNG, TIFF, BMP, TXT"
    )
    st.stop()

candidates = db["candidates"]
generated_at = db.get("generated_at", "")
total_files = db.get("total_files_processed", 0)
successful = db.get("successful_extractions", 0)

st.caption(f"{len(candidates)} candidates · database built {generated_at[:19].replace('T', ' ')}")
if total_files > 0:
    st.caption(f"Processed {successful}/{total_files} files successfully")

df = flatten_for_table(candidates)

# ----------------------------------------------------------------------------
# Sidebar filters
# ----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Filter the roster")
    search = st.text_input("Search name, title, or skill")

    seniority_options = sorted(df["Seniority"].unique().tolist())
    seniority_filter = st.multiselect("Seniority", seniority_options, default=seniority_options)

    max_exp = max(int(df["Experience (yrs)"].max()), 1)
    exp_range = st.slider("Years of experience", 0, max_exp, (0, max_exp))

    st.markdown("---")
    sort_by = st.selectbox("Sort by", ["Experience (yrs)", "Name", "Seniority"])
    sort_desc = st.checkbox("Descending", value=True)

    st.markdown("---")
    st.markdown("### Compare candidates")
    compare_names = st.multiselect("Pick up to 3", df["Name"].tolist(), max_selections=3)


def matches_search(candidate, query):
    if not query:
        return True
    query = query.lower()
    haystack = " ".join([
        candidate.get("full_name") or "",
        candidate.get("current_title") or "",
        " ".join((candidate.get("skills") or {}).get("technical", [])),
        " ".join((candidate.get("skills") or {}).get("tools_and_platforms", [])),
    ]).lower()
    return query in haystack


filtered = [
    c for c in candidates
    if matches_search(c, search)
    and (c.get("seniority_level") or "Unknown") in seniority_filter
    and exp_range[0] <= (c.get("total_experience_years") or 0) <= exp_range[1]
]

filtered_df = flatten_for_table(filtered)
if not filtered_df.empty:
    filtered_df = filtered_df.sort_values(by=sort_by, ascending=not sort_desc)

# ----------------------------------------------------------------------------
# Overview metrics
# ----------------------------------------------------------------------------
m1, m2, m3, m4 = st.columns(4)
m1.metric("Candidates shown", len(filtered))
avg_exp = round(filtered_df["Experience (yrs)"].mean(), 1) if not filtered_df.empty else 0
m2.metric("Avg. experience", f"{avg_exp} yrs")
senior_count = sum(1 for c in filtered if (c.get("seniority_level") or "") in ("Senior", "Lead", "Executive"))
m3.metric("Senior+ candidates", senior_count)
flagged = sum(1 for c in filtered if c.get("red_flags_or_gaps"))
m4.metric("With flagged gaps", flagged)

# ----------------------------------------------------------------------------
# Charts
# ----------------------------------------------------------------------------
st.markdown('<div class="section-label">Roster analytics</div>', unsafe_allow_html=True)
chart_col1, chart_col2, chart_col3 = st.columns(3)

with chart_col1:
    if not filtered_df.empty:
        fig = px.histogram(filtered_df, x="Experience (yrs)", nbins=10,
                            color_discrete_sequence=[COLOR_ACCENT])
        fig.update_layout(title="Experience distribution", plot_bgcolor="white",
                           paper_bgcolor="white", font_color=COLOR_INK, height=280,
                           margin=dict(t=40, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)

with chart_col2:
    if not filtered_df.empty:
        seniority_counts = filtered_df["Seniority"].value_counts().reset_index()
        seniority_counts.columns = ["Seniority", "Count"]
        colors = [SENIORITY_COLORS.get(s, COLOR_MUTED) for s in seniority_counts["Seniority"]]
        fig2 = go.Figure(data=[go.Pie(labels=seniority_counts["Seniority"],
                                       values=seniority_counts["Count"],
                                       hole=0.5, marker=dict(colors=colors))])
        fig2.update_layout(title="Seniority mix", height=280, paper_bgcolor="white",
                            font_color=COLOR_INK, margin=dict(t=40, b=10, l=10, r=10))
        st.plotly_chart(fig2, use_container_width=True)

with chart_col3:
    all_skills = []
    for c in filtered:
        all_skills.extend((c.get("skills") or {}).get("technical", []))
    if all_skills:
        skill_counts = pd.Series(all_skills).value_counts().head(8).reset_index()
        skill_counts.columns = ["Skill", "Count"]
        fig3 = px.bar(skill_counts, x="Count", y="Skill", orientation="h",
                       color_discrete_sequence=[COLOR_STAMP])
        fig3.update_layout(title="Top technical skills", plot_bgcolor="white",
                            paper_bgcolor="white", font_color=COLOR_INK, height=280,
                            yaxis=dict(autorange="reversed"), margin=dict(t=40, b=10, l=10, r=10))
        st.plotly_chart(fig3, use_container_width=True)

# ----------------------------------------------------------------------------
# Candidate roster
# ----------------------------------------------------------------------------
st.markdown('<div class="section-label">Candidate roster</div>', unsafe_allow_html=True)

if filtered_df.empty:
    st.info("No candidates match the current filters.")
else:
    name_to_candidate = {c.get("full_name") or "(Unknown)": c for c in filtered}

    for _, row in filtered_df.iterrows():
        c = name_to_candidate.get(row["Name"])
        if c is None:
            continue
        stamp_color = SENIORITY_COLORS.get(c.get("seniority_level"), COLOR_MUTED)
        techs = (c.get("skills") or {}).get("technical", [])[:8]

        with st.container():
            st.markdown(f"""
            <div class="dossier-card" style="--stamp-color:{stamp_color}">
                <span class="stamp" style="background-color:{stamp_color}; float:right;">{c.get('seniority_level', 'Unknown')}</span>
                <div class="candidate-name">{c.get('full_name') or 'Unknown'}</div>
                <div class="candidate-title">{c.get('current_title') or '—'} · {c.get('total_experience_years') or '?'} yrs experience</div>
                <p style="color:{COLOR_INK}; font-size:0.92rem;">{c.get('summary', '')}</p>
                {''.join(f'<span class="skill-chip">{s}</span>' for s in techs)}
            </div>
            """, unsafe_allow_html=True)

            with st.expander(f"Open full dossier — {c.get('full_name') or 'Unknown'}"):
                contact = c.get("contact") or {}
                cc1, cc2 = st.columns(2)
                with cc1:
                    st.markdown(f"""
                    **Email:** {contact.get('email') or '—'}
                    **Phone:** {contact.get('phone') or '—'}
                    **Location:** {contact.get('location') or '—'}
                    """)
                with cc2:
                    st.markdown(f"""
                    **LinkedIn:** {contact.get('linkedin') or '—'}
                    **Portfolio/GitHub:** {contact.get('portfolio_or_github') or '—'}
                    **Source file:** {c.get('source_file')}
                    """)

                st.markdown("**Work experience**")
                for job in c.get("work_experience", []):
                    st.markdown(f"- **{job.get('role', '—')}** at *{job.get('company', '—')}* "
                                f"({job.get('start_date', '?')} → {job.get('end_date', '?')})")
                    for r in job.get("responsibilities", [])[:5]:
                        st.markdown(f"   - {r}")

                edu_col, skill_col = st.columns(2)
                with edu_col:
                    st.markdown("**Education**")
                    for edu in c.get("education", []):
                        st.markdown(f"- {edu.get('degree', '—')} in {edu.get('field_of_study', '—')}, "
                                    f"{edu.get('institution', '—')} ({edu.get('graduation_year', '—')})")
                    if c.get("certifications"):
                        st.markdown("**Certifications**")
                        for cert in c["certifications"]:
                            st.markdown(f"- {cert}")

                with skill_col:
                    st.markdown("**All skills**")
                    skills = c.get("skills") or {}
                    for group_label, key in [("Technical", "technical"), ("Tools/Platforms", "tools_and_platforms"), ("Soft skills", "soft")]:
                        vals = skills.get(key, [])
                        if vals:
                            st.markdown(f"*{group_label}:* " + ", ".join(vals))
                    if c.get("languages"):
                        st.markdown(f"*Languages:* {', '.join(c['languages'])}")

                if c.get("red_flags_or_gaps"):
                    st.markdown("**⚠️ Flags to review**")
                    for flag in c["red_flags_or_gaps"]:
                        st.markdown(f'<span class="red-flag">- {flag}</span>', unsafe_allow_html=True)

                if c.get("data_quality_notes"):
                    with st.container():
                        st.caption("Data quality notes: " + "; ".join(c["data_quality_notes"]))

# ----------------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------------
st.markdown('<div class="section-label">Export</div>', unsafe_allow_html=True)
st.download_button(
    "Download filtered roster as CSV",
    data=filtered_df.to_csv(index=False).encode("utf-8"),
    file_name=f"candidates_filtered_{datetime.now().strftime('%Y%m%d')}.csv",
    mime="text/csv",
)