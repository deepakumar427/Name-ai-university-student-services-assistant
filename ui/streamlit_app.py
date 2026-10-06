"""Streamlit page for students. It only displays; every decision is made by the API.

Flow: login screen -> the API checks the password and returns a token -> chat page.
The token is kept in this browser session only (st.session_state) and sent as
`Authorization: Bearer <token>`. The password is never stored, and no student ID is sent with questions:
the API takes the identity from the token.

Run: streamlit run ui/streamlit_app.py   (API_URL defaults to http://localhost:8000)
"""

import html
import os
from datetime import date

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")

# answer_type -> (label, text colour, background colour, icon)
TYPE_STYLE = {
    "retrieved_fact": ("From university documents", "#0F6B3A", "#E3F5EA", "📄"),
    "calculated": ("Calculated from your records", "#1E4D8C", "#E4EDFA", "🧮"),
    "not_found": ("Not found in the sources", "#5B6472", "#EEF0F3", "🔍"),
    "clarification_needed": ("Needs more detail", "#8A5A00", "#FFF3D6", "❓"),
    "refused": ("Not allowed", "#A12B2B", "#FBE7E7", "🔒"),
    "conflict_flagged": ("Sources disagree", "#6B3FA0", "#F0E8FA", "⚖️"),
}
QUICK_QUESTIONS = [
    ("📋", "What is the minimum attendance required for exams?"),
    ("📊", "What is my attendance in CS201?"),
    ("✅", "Am I eligible for the CS201 exam?"),
    ("🎯", "Am I eligible for campus placement?"),
    ("📝", "How do I apply for the supplementary exam?"),
    ("💰", "What is the fee for the supplementary exam?"),
]

CSS = """
<style>
  #MainMenu, footer, [data-testid="stDecoration"] {visibility: hidden;}
  .block-container {padding-top: 2rem; padding-bottom: 6rem; max-width: 860px;}
  h1, h2, h3 {letter-spacing: -0.01em;}

  .brand {display: flex; align-items: center; gap: 14px; margin-bottom: 0.25rem;}
  .brand-logo {width: 46px; height: 46px; border-radius: 12px; background: linear-gradient(135deg,#1E4D8C,#3A7BD5);
               display: flex; align-items: center; justify-content: center; font-size: 24px; color: #fff;}
  .brand-title {font-size: 1.45rem; font-weight: 700; color: #1B2430; line-height: 1.2;}
  .brand-sub {font-size: 0.88rem; color: #5B6472;}

  .login-hero {text-align: center; margin: 1.5rem 0 1.25rem;}
  .login-hero .brand-logo {margin: 0 auto 0.9rem; width: 64px; height: 64px; font-size: 32px; border-radius: 16px;}
  .login-hero h1 {font-size: 1.7rem; margin: 0; padding: 0;}
  .login-hero p {color: #5B6472; margin-top: 0.35rem;}
  .trust-row {display: flex; justify-content: center; gap: 18px; flex-wrap: wrap; margin-top: 1rem;
              font-size: 0.82rem; color: #5B6472;}

  .profile {background: linear-gradient(135deg,#1E4D8C,#3A7BD5); color: #fff; border-radius: 14px;
            padding: 16px 16px 14px; margin-bottom: 0.75rem;}
  .profile .avatar {width: 42px; height: 42px; border-radius: 50%; background: rgba(255,255,255,0.2);
                    display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 1rem;
                    margin-bottom: 8px;}
  .profile .name {font-weight: 700; font-size: 1.02rem;}
  .profile .meta {font-size: 0.82rem; opacity: 0.9; margin-top: 2px;}
  .profile .sid {display: inline-block; margin-top: 8px; font-size: 0.75rem; background: rgba(255,255,255,0.18);
                 padding: 2px 10px; border-radius: 999px;}

  .badge {display: inline-flex; align-items: center; gap: 6px; font-size: 0.78rem; font-weight: 600;
          padding: 3px 11px; border-radius: 999px; margin-bottom: 6px;}
  .src {background: #fff; border: 1px solid #E3E8EF; border-left: 3px solid #1E4D8C; border-radius: 10px;
        padding: 9px 12px; margin: 6px 0; font-size: 0.86rem;}
  .src .t {font-weight: 600; color: #1B2430;}
  .src .m {color: #5B6472; font-size: 0.78rem; margin-top: 2px;}
  .src .n {color: #8A5A00; font-size: 0.76rem; margin-top: 2px;}
  .meta-line {font-size: 0.74rem; color: #8A93A1; margin-top: 6px;}
  .status-dot {display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px;}
  .empty {text-align: center; color: #5B6472; padding: 1.5rem 0 0.5rem;}
  .empty .big {font-size: 2.2rem;}
</style>
"""

st.set_page_config(page_title="Student Services Assistant", page_icon="🎓", layout="centered")
st.markdown(CSS, unsafe_allow_html=True)


# ---------- API helpers ----------

@st.cache_data(ttl=30)
def health() -> dict | None:
    try:
        return requests.get(f"{API_URL}/health", timeout=5).json()
    except requests.RequestException:
        return None


def auth_header() -> dict:
    return {"Authorization": f"Bearer {st.session_state['token']}"}  # identity = the token, nothing else


def clear_session(message: str = "") -> None:
    for key in ("token", "student_id", "profile", "history"):
        st.session_state.pop(key, None)
    if message:
        st.session_state["notice"] = message


def logout() -> None:
    if st.session_state.get("token"):
        try:  # revoke the token on the server too, so it can't be reused
            requests.post(f"{API_URL}/auth/logout", headers=auth_header(), timeout=10)
        except requests.RequestException:
            pass
    clear_session("You have been signed out.")


def load_profile() -> dict:
    if "profile" not in st.session_state:
        try:
            resp = requests.get(f"{API_URL}/auth/me", headers=auth_header(), timeout=10)
            st.session_state["profile"] = resp.json() if resp.status_code == 200 else {}
        except requests.RequestException:
            st.session_state["profile"] = {}
    return st.session_state["profile"]


# ---------- login screen ----------

def login_screen() -> None:
    _, mid, _ = st.columns([1, 2.2, 1])
    with mid:
        st.markdown(
            '<div class="login-hero"><div class="brand-logo">🎓</div>'
            "<h1>Student Services Assistant</h1>"
            "<p>Sign in to ask about university rules, your attendance, results and eligibility.</p></div>",
            unsafe_allow_html=True,
        )
        if st.session_state.get("notice"):
            st.info(st.session_state.pop("notice"), icon="👋")
        with st.container(border=True):
            with st.form("login", clear_on_submit=False, border=False):
                sid = st.text_input("Student ID", placeholder="e.g. S1001")
                pwd = st.text_input("Password", type="password", placeholder="Your password")
                submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
            if submitted:
                attempt_login(sid, pwd)
        with st.expander("Demo accounts"):
            st.markdown(
                "Password for every demo student: `Student@2026`\n\n"
                "| Student | Try |\n|---|---|\n"
                "| `S1001` | attendance exactly at the limit |\n"
                "| `S1002` | attendance one class short |\n"
                "| `S1008` | “If I pass the supplementary…” |"
            )
        st.markdown(
            '<div class="trust-row"><span>🔒 Secure sign-in</span><span>📄 Answers with sources</span>'
            "<span>🧮 Exact calculations</span></div>",
            unsafe_allow_html=True,
        )


def attempt_login(sid: str, pwd: str) -> None:
    if not sid.strip() or not pwd:
        st.warning("Please enter your student ID and password.")
        return
    try:
        resp = requests.post(f"{API_URL}/auth/login", json={"student_id": sid.strip(), "password": pwd}, timeout=30)
    except requests.RequestException:
        st.error("The service is not reachable right now. Please try again shortly.")
        return
    if resp.status_code != 200:
        st.error("Invalid student ID or password.")
        return
    data = resp.json()
    st.session_state["token"] = data["access_token"]  # the password is not kept anywhere
    st.session_state["student_id"] = data["student_id"]
    st.session_state["history"] = []
    st.rerun()


# ---------- answer rendering ----------

def badge(answer_type: str) -> str:
    label, fg, bg, icon = TYPE_STYLE.get(answer_type, (answer_type, "#5B6472", "#EEF0F3", "•"))
    return f'<span class="badge" style="color:{fg};background:{bg}">{icon} {label}</span>'


def source_card(i: int, c: dict) -> str:
    e = html.escape
    note = f'<div class="n">⚠ {e(c["note"])}</div>' if c["note"] else ""
    return (f'<div class="src"><div class="t">[{i}] {e(c["title"])}</div>'
            f'<div class="m">{e(c["doc_id"])} · Section {e(str(c["section"]))} · Page {e(str(c["page"]))} · '
            f'Version {e(c["version"])} · Effective {e(c["effective_from"])}</div>{note}</div>')


def show_answer(data: dict) -> None:
    st.markdown(badge(data["answer_type"]), unsafe_allow_html=True)
    st.markdown(data["answer"])
    if data["citations"]:
        st.markdown("".join(source_card(i, c) for i, c in enumerate(data["citations"], start=1)),
                    unsafe_allow_html=True)
    details = []
    if data["explanation"]:
        details.append(("💡 Why this answer", "why"))
    if data["conflicts_detected"]:
        details.append((f"⚖️ {len(data['conflicts_detected'])} conflicting source(s) set aside", "conflicts"))
    if data["tools_invoked"]:
        details.append(("🧮 How it was calculated", "tools"))
    for title, kind in details:
        with st.expander(title):
            if kind == "why":
                st.write(data["explanation"])
            elif kind == "conflicts":
                st.dataframe(
                    [{"Used": c["winner"], "Set aside": c["loser"], "Rule": c["step"].title(), "Reason": c["reason"]}
                     for c in data["conflicts_detected"]],
                    hide_index=True, width="stretch")
            else:
                st.caption("Computed by code from your records and the official rule, not by the AI.")
                for t in data["tools_invoked"]:
                    st.markdown(f"**`{t['tool']}`** · input `{t['input']}` · status `{t['status']}`")
                    st.json(t["output"], expanded=False)
    st.markdown(f'<div class="meta-line">Rules as of {data["as_of_date"]} · Reference {data["trace_id"][:8]}</div>',
                unsafe_allow_html=True)


# ---------- chat page ----------

def sidebar(profile: dict) -> date:
    student_id = st.session_state["student_id"]
    with st.sidebar:
        name = profile.get("full_name") or f"Student {student_id}"
        initials = "".join(part[0] for part in name.split()[:2]).upper()
        meta = " · ".join(str(x) for x in (
            profile.get("programme"),
            f"Batch {profile['batch_year']}" if profile.get("batch_year") else None,
            f"Semester {profile['current_semester']}" if profile.get("current_semester") else None,
        ) if x)
        st.markdown(
            f'<div class="profile"><div class="avatar">{html.escape(initials)}</div>'
            f'<div class="name">{html.escape(name)}</div><div class="meta">{html.escape(meta)}</div>'
            f'<span class="sid">Logged in as: {html.escape(student_id)}</span></div>',
            unsafe_allow_html=True,
        )
        st.button("Sign out", on_click=logout, width="stretch")
        st.markdown("#### Settings")
        as_of = st.date_input("Apply rules as of", value=date.today(),
                              help="Rules change over time. Pick a date to see which rules applied then.")
        if st.session_state.get("history"):
            if st.button("Clear conversation", width="stretch"):
                st.session_state["history"] = []
                st.rerun()
        st.markdown("#### System")
        status = health()
        if status:
            ok = status["status"] == "ok"
            colour = "#1E9E5A" if ok else "#D08A00"
            st.markdown(f'<span class="status-dot" style="background:{colour}"></span>'
                        f'{"All systems operational" if ok else "Running with reduced features"}',
                        unsafe_allow_html=True)
            st.caption(f"Model: {status['model']} · {status.get('documents', '?')} documents indexed")
        else:
            st.markdown('<span class="status-dot" style="background:#C53030"></span>Service unreachable',
                        unsafe_allow_html=True)
    return as_of


def ask(question: str, as_of: date) -> dict | None:
    try:
        resp = requests.post(f"{API_URL}/ask", json={"question": question, "as_of_date": as_of.isoformat()},
                             headers=auth_header(), timeout=180)
    except requests.RequestException:
        st.error("The service is not reachable right now. Please try again shortly.")
        return None
    if resp.status_code == 401:  # expired or revoked token
        clear_session("Your session has expired. Please sign in again.")
        st.rerun()
    if resp.status_code != 200:
        st.error(f"Something went wrong (error {resp.status_code}). Please try again.")
        return None
    return resp.json()


def chat_screen() -> None:
    profile = load_profile()
    as_of = sidebar(profile)
    first = (profile.get("full_name") or "").split(" ")[0] or st.session_state["student_id"]
    st.markdown(
        '<div class="brand"><div class="brand-logo">🎓</div><div>'
        '<div class="brand-title">Student Services Assistant</div>'
        f'<div class="brand-sub">Welcome, {html.escape(first)}. Answers come only from official university '
        "documents and your own records.</div></div></div>",
        unsafe_allow_html=True,
    )
    st.divider()

    history = st.session_state.setdefault("history", [])
    # Read new input first. A question joins the conversation BEFORE it is answered, so if the page reruns
    # mid-answer (e.g. the student sends another question), the unanswered one is picked up and finished.
    typed = st.chat_input("Ask a question, e.g. “Am I eligible for the CS201 exam?”", max_chars=1000)
    new_question = (typed or st.session_state.pop("pending_question", None) or "").strip()
    if new_question:
        history.append({"question": new_question, "response": None})

    if not history:
        st.markdown('<div class="empty"><div class="big">💬</div>Ask anything about university rules, '
                    "or about your own attendance, results and eligibility.</div>", unsafe_allow_html=True)
        cols = st.columns(2)
        for i, (icon, q) in enumerate(QUICK_QUESTIONS):
            if cols[i % 2].button(f"{icon}  {q}", key=f"quick-{i}", width="stretch"):
                st.session_state["pending_question"] = q
                st.rerun()
        return

    for turn in list(history):
        with st.chat_message("user", avatar="🧑‍🎓"):
            st.markdown(turn["question"])
        with st.chat_message("assistant", avatar="🎓"):
            if turn["response"] is None:
                with st.spinner("Checking the university documents and your records…"):
                    turn["response"] = ask(turn["question"], as_of)
                if turn["response"] is None:  # failed: the error is shown, drop the turn so it can be re-asked
                    history.remove(turn)
                    continue
            show_answer(turn["response"])

if st.session_state.get("token"):
    chat_screen()
else:
    login_screen()
