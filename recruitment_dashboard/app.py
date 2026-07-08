from flask import Flask, render_template, jsonify, abort
import json
import os
from collections import Counter

app = Flask(__name__, template_folder='templates', static_folder='static')


def load_candidates():
    base = os.path.dirname(__file__)
    data_path = os.path.join(base, '..', 'data', 'candidates.json')
    data_path = os.path.abspath(data_path)
    with open(data_path, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    return payload.get('candidates', [])


def compute_stats(candidates):
    total = len(candidates)
    seniority_counts = Counter()
    exp_list = []
    skill_counts = Counter()

    for c in candidates:
        seniority_counts[c.get('seniority_level') or 'Unknown'] += 1
        exp = c.get('total_experience_years')
        if isinstance(exp, (int, float)):
            exp_list.append(exp)
        # collect top-level technical skills
        skills = c.get('skills', {}).get('technical') or []
        for s in skills:
            skill_counts[s.lower()] += 1

    avg_exp = sum(exp_list) / len(exp_list) if exp_list else 0

    top_skills = skill_counts.most_common(10)

    return {
        'total': total,
        'seniority_counts': dict(seniority_counts),
        'avg_experience': round(avg_exp, 2),
        'top_skills': top_skills,
    }


@app.route('/')
def index():
    candidates = load_candidates()
    stats = compute_stats(candidates)
    return render_template('index.html', stats=stats)


@app.route('/api/applicants')
def api_applicants():
    candidates = load_candidates()
    # convert to lightweight records for table
    out = []
    for i, c in enumerate(candidates):
        out.append({
            'id': i,
            'name': c.get('full_name'),
            'title': c.get('current_title'),
            'experience': c.get('total_experience_years'),
            'location': c.get('contact', {}).get('location'),
            'education': (c.get('education') or [])[:1],
            'source_file': c.get('source_file')
        })
    return jsonify({ 'data': out })


@app.route('/applicant/<int:applicant_id>')
def applicant(applicant_id):
    candidates = load_candidates()
    if applicant_id < 0 or applicant_id >= len(candidates):
        abort(404)
    c = candidates[applicant_id]
    return render_template('applicant.html', cand=c, id=applicant_id)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=7000)
